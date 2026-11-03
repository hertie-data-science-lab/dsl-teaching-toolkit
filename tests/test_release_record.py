"""Fire-once releases with a record (decision 0043 rules 1-2).

The record (`release_record`), what the scheduler ships and seeds off it, what Release
materials records, the hand-release matcher, and the preview's `ALREADY_DONE`. The reads
and the write are the record module's imported `gh_contents` names, stubbed here; what
deploy_many does with git is `test_release_merge`'s.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import pytest

from dsl_course import deploy, release_record, schedule_plan, scheduler
from dsl_course.deploy import HELD, RELEASED, UNCHANGED, Batch, Copied
from dsl_course.schedule import Deploy, Release, Schedule

NOW = datetime(2026, 10, 27, 13, 49, 3, tzinfo=UTC)
EARLIER = "2026-10-20T09:00:00Z"
LAB = Deploy("cm", "labs/08_session-8", "materials", None)
LECTURE = Deploy("cm", "lectures/08", "materials", None)
FILES = {"slides.pdf": "a1", "requirements.txt": "b2"}


def _entry(label="lab-08", d=LAB, files=FILES, first=EARLIER, last=EARLIER) -> dict:
    return {
        "entry": label,
        "source": {
            "repo": d.course_source_repo,
            "path": d.course_source_path,
            "commit": "c0",
        },
        "dest": {"repo": d.semester_dest_repo, "path": d.course_source_path},
        "first_shipped_at": first,
        "last_shipped_at": last,
        "files": files,
    }


class Store:
    """semester-config's record file, and the destination trees, as the record reads them."""

    def __init__(self, copies=None, trees=None):
        self.text = None if copies is None else json.dumps({"copies": copies})
        self.trees = trees or {}
        self.writes: list[str] = []
        self.tree_reads: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(
            release_record,
            "get_file_with_sha",
            lambda org, repo, path, **k: (
                None if self.text is None else (self.text, "sha0")
            ),
        )

        def tree(org, repo, branch):
            self.tree_reads.append(repo)
            return self.trees.get(repo, {})

        monkeypatch.setattr(scheduler, "repo_blob_shas", tree)

        def write(org, repo, path, body, sha, rebuild, message):
            assert (repo, path) == ("semester-config", ".system/releases.json")
            self.writes.append(body)
            self.text = body
            return body

        monkeypatch.setattr(release_record, "put_file_as_read", write)
        return self

    @property
    def copies(self) -> list[dict]:
        return json.loads(self.text)["copies"]


def _shipping(monkeypatch, outcome=RELEASED, files=FILES):
    """deploy_many as seen from the scheduler: every copy applied, its dest `outcome`."""
    calls: list[list[Deploy]] = []

    def fake(course_org, semester_org, deploys, sync=True, remove=None):
        calls.append(list(deploys))
        copies = [Copied(d, "c1", files, outcome) for d in deploys]
        return Batch(0, outcome == RELEASED, copies)

    monkeypatch.setattr(scheduler, "deploy_many", fake)
    return calls


def _autosync(monkeypatch, on: bool):
    monkeypatch.setattr(
        scheduler.settings,
        "effective",
        lambda org, key, **k: (
            (on, "semester")
            if key == "autosync_released_edits"
            else (None, "institution")
        ),
    )


def _release(*copies: Deploy, label="lab-08") -> Release:
    return Release(label=label, when=NOW, deploy=list(copies))


def _tick(release: Release) -> tuple[int, bool]:
    return scheduler._run_releases("Course-Org", "Semester-Org", [release], NOW)


# ------------------------------------------------------------------- the record itself


def test_a_copy_is_keyed_by_both_ends_and_a_root_path_is_empty():
    assert release_record.key_of(Deploy("cm", "/labs/08/", "materials", None)) == (
        "cm",
        "labs/08",
        "materials",
        "labs/08",
    )
    assert release_record.key_of(Deploy("cm", "/", "site", "week1")) == (
        "cm",
        "",
        "site",
        "week1",
    )


def test_inside_keys_a_folder_relative_to_it_and_a_file_by_its_name():
    tree = {"labs/08/a.pdf": "1", "labs/08/x/b.py": "2", "labs/09/c": "3", "R.md": "4"}
    assert release_record.inside(tree, "labs/08") == {"a.pdf": "1", "x/b.py": "2"}
    assert release_record.inside(tree, "R.md") == {"R.md": "4"}
    assert release_record.inside(tree, "labs/08/x/b.py") == {"b.py": "2"}
    assert release_record.inside(tree, "") == tree
    assert release_record.inside(tree, "labs/10") == {}


def test_the_file_is_one_blob_per_state():
    record = release_record.Record()
    record.ship("lab-08", LAB, "c1", FILES, NOW, refresh=False)
    record.ship("lecture-8", LECTURE, "c1", {"n.md": "z"}, NOW, refresh=False)
    text = release_record.dump(record.copies)
    assert release_record.dump(release_record.parse(text)) == text
    (lab, lecture) = json.loads(text)["copies"]
    assert lab == {
        "entry": "lab-08",
        "source": {"repo": "cm", "path": "labs/08_session-8", "commit": "c1"},
        "dest": {"repo": "materials", "path": "labs/08_session-8"},
        "first_shipped_at": "2026-10-27T13:49:03Z",
        "last_shipped_at": "2026-10-27T13:49:03Z",
        "files": {"requirements.txt": "b2", "slides.pdf": "a1"},
    }
    assert lecture["entry"] == "lecture-8"


def test_a_file_that_is_not_the_record_refuses_to_parse():
    for text in ("[]", '{"copies": {}}', '{"copies": [{"entry": "x"}]}'):
        with pytest.raises(ValueError):
            release_record.parse(text)


def test_a_refused_write_reapplies_this_runs_change_on_the_fresh_file(monkeypatch):
    # Another run recorded the same copy first, and seeded another: this run's ship keeps
    # their first_shipped_at and adds its own, and their seed stays.
    other = Deploy("cm", "labs/09", "materials", None)
    fresh = json.dumps(
        {"copies": [_entry(), _entry("lab-09", other, {"n": "1"}, None, None)]}
    )
    written = []

    def refuse_once(org, repo, path, body, sha, rebuild, message):
        written.append(rebuild(fresh))
        return written[-1]

    monkeypatch.setattr(release_record, "put_file_as_read", refuse_once)
    record = release_record.Record()
    record.ship("lab-08", LAB, "c1", {"new.pdf": "9"}, NOW, refresh=False)
    assert record.save("Semester-Org")
    copies = {c["entry"]: c for c in json.loads(written[0])["copies"]}
    assert copies["lab-08"]["first_shipped_at"] == EARLIER
    assert copies["lab-08"]["files"] == {"new.pdf": "9"}
    assert copies["lab-09"]["first_shipped_at"] is None


def test_nothing_changed_writes_nothing(monkeypatch):
    store = Store([_entry()]).install(monkeypatch)
    record = release_record.load("Semester-Org")
    record.ship("lab-08", LAB, "c2", FILES, NOW, refresh=False)
    assert record.save("Semester-Org")
    assert store.writes == []


# ------------------------------------------------------------------ the scheduled tick


def test_a_recorded_copy_is_not_shipped_again(monkeypatch, capsys):
    store = Store([_entry()]).install(monkeypatch)
    _autosync(monkeypatch, False)
    calls = _shipping(monkeypatch)
    assert _tick(_release(LAB)) == (0, False)
    assert calls == []
    assert store.writes == []
    assert "1 due copy already released" in capsys.readouterr().out


def test_an_unrecorded_copy_ships_and_is_recorded(monkeypatch):
    store = Store([]).install(monkeypatch)
    calls = _shipping(monkeypatch)
    assert _tick(_release(LAB)) == (0, True)
    assert calls == [[LAB]]
    (copy,) = store.copies
    assert copy["entry"] == "lab-08"
    assert copy["source"]["commit"] == "c1"
    assert copy["first_shipped_at"] == copy["last_shipped_at"] == "2026-10-27T13:49:03Z"
    assert copy["files"] == dict(sorted(FILES.items()))
    # The destination was read once, to see whether there was anything to seed from.
    assert store.tree_reads == ["materials"]


def test_with_autosync_an_edit_ships_and_refreshes_the_record(monkeypatch):
    store = Store([_entry()]).install(monkeypatch)
    _autosync(monkeypatch, True)
    calls = _shipping(monkeypatch, files={"slides.pdf": "edited"})
    assert _tick(_release(LAB)) == (0, True)
    assert calls == [[LAB]]
    (copy,) = store.copies
    # A copy only adds: requirements.txt, not in this copy, is still at the destination.
    assert copy["files"] == {"requirements.txt": "b2", "slides.pdf": "edited"}
    assert copy["first_shipped_at"] == EARLIER
    assert copy["last_shipped_at"] == "2026-10-27T13:49:03Z"


def test_with_autosync_an_unchanged_copy_writes_nothing(monkeypatch):
    store = Store([_entry()]).install(monkeypatch)
    _autosync(monkeypatch, True)
    _shipping(monkeypatch, outcome=UNCHANGED)
    assert _tick(_release(LAB)) == (0, False)
    assert store.writes == []


def test_a_copy_already_at_its_destination_is_seeded_not_shipped(monkeypatch, capsys):
    # The upgrade path: a semester released into before the record existed. Nothing is
    # copied; the record is written from what students already have.
    tree = {"labs/08_session-8/slides.pdf": "s1", "README.md": "r"}
    store = Store(None, {"materials": tree}).install(monkeypatch)
    calls = _shipping(monkeypatch)
    assert _tick(_release(LAB)) == (0, False)
    assert calls == []
    (copy,) = store.copies
    assert copy["first_shipped_at"] is None
    assert copy["last_shipped_at"] is None
    assert copy["source"]["commit"] is None
    assert copy["files"] == {"slides.pdf": "s1"}
    assert "[seed] lab-08" in capsys.readouterr().out


def test_a_copy_held_at_a_conflict_is_not_recorded(monkeypatch):
    store = Store([]).install(monkeypatch)
    _shipping(monkeypatch, outcome=HELD)
    _tick(_release(LAB))
    assert store.writes == []


def test_a_byte_identical_copy_is_recorded(monkeypatch):
    # The no-op merge: students already have exactly this. Unrecorded, it would be cloned
    # again on every tick for ever.
    store = Store([]).install(monkeypatch)
    _shipping(monkeypatch, outcome=UNCHANGED)
    _tick(_release(LAB))
    assert [c["entry"] for c in store.copies] == ["lab-08"]


def test_two_entries_citing_one_copy_ship_it_once(monkeypatch):
    Store([]).install(monkeypatch)
    calls = _shipping(monkeypatch)
    scheduler._run_releases(
        "Course-Org",
        "Semester-Org",
        [_release(LAB), _release(LAB, label="lab-08-again")],
        NOW,
    )
    assert calls == [[LAB]]


def test_a_record_that_cannot_be_read_ships_nothing_and_says_so(monkeypatch, capsys):
    def unreadable(*a, **k):
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(release_record, "get_file_with_sha", unreadable)
    calls = _shipping(monkeypatch)
    assert _tick(_release(LAB)) == (1, False)
    assert calls == []
    assert "no copy is released this tick" in capsys.readouterr().err


def test_a_record_that_cannot_be_written_is_an_error(monkeypatch):
    Store([]).install(monkeypatch)
    monkeypatch.setattr(release_record, "put_file_as_read", lambda *a, **k: None)
    _shipping(monkeypatch)
    assert _tick(_release(LAB)) == (1, True)


def test_the_institution_default_is_fire_once():
    # WP-1 adds the key; until a layer states it, nothing resolves true.
    assert scheduler._autosync("Course-Org", "Semester-Org") is False


# -------------------------------------------------------------------------- the preview


def test_the_preview_says_already_done_for_an_entry_whose_copies_are_all_out(
    monkeypatch,
):
    seeded = Deploy("cm", "lectures/08", "materials", None)
    tree = {"lectures/08/notes.md": "n"}
    store = Store([_entry()], {"materials": tree}).install(monkeypatch)
    due = [
        _release(LAB),
        _release(seeded, label="lecture-8"),
        _release(Deploy("cm", "labs/09", "materials", None), label="lab-09"),
    ]
    decisions = scheduler._release_decisions("Course-Org", "Semester-Org", due, NOW)
    assert [(d.ref, d.code) for d in decisions] == [
        ("lab-08", "ALREADY_DONE"),
        ("lecture-8", "ALREADY_DONE"),
    ]
    assert store.writes == []  # a preview writes nothing, a seed included


# ---------------------------------------------------------------- Release materials (CLI)


def _sched() -> Schedule:
    return Schedule(
        releases=[
            Release("lab-08", NOW, deploy=[LAB]),
            Release("lecture-8", NOW, deploy=[LECTURE]),
            Release(
                "syllabus", NOW, deploy=[Deploy("cm", "SYLLABUS.md", "materials", None)]
            ),
        ]
    )


def _cli(monkeypatch, argv, files=FILES, outcome=RELEASED):
    monkeypatch.setattr(deploy.schedule, "load", lambda org: _sched())
    batches = []

    def fake(course_org, semester_org, deploys, sync=True, remove=None):
        batches.append(list(deploys))
        return Batch(0, True, [Copied(d, "c9", files, outcome) for d in deploys])

    monkeypatch.setattr(deploy, "deploy_many", fake)
    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy", "--course-org", "Course-Org", "--semester-org", "Semester-Org"]
        + argv
        + ["--no-preview"],
    )
    return deploy.main(), batches


def test_release_again_re_copies_and_refreshes_the_record(monkeypatch):
    store = Store([_entry()]).install(monkeypatch)
    rc, batches = _cli(monkeypatch, ["--entry", "lab-08"], files={"slides.pdf": "v2"})
    assert rc == 0 and batches == [[LAB]]
    (copy,) = store.copies
    # A copy only adds: requirements.txt, not in this copy, is still at the destination.
    assert copy["files"] == {"requirements.txt": "b2", "slides.pdf": "v2"}
    assert copy["first_shipped_at"] == EARLIER
    assert copy["last_shipped_at"] != EARLIER


def test_release_again_refreshes_even_when_nothing_changed(monkeypatch):
    store = Store([_entry()]).install(monkeypatch)
    _cli(monkeypatch, ["--entry", "lab-08"])
    assert store.copies[0]["last_shipped_at"] != EARLIER


def test_a_hand_release_records_the_schedule_copies_it_made_and_no_other(monkeypatch):
    # `labs` covers lab-08's folder (and lands it where the plan does); `notes` is off the
    # plan and stays unrecorded.
    store = Store([]).install(monkeypatch)
    made = {"labs_08_session-8/x": "?", "08_session-8/a.pdf": "p", "stray.md": "s"}
    rc, _ = _cli(
        monkeypatch,
        ["--course-source-repo", "cm", "--course-source-path", "labs,notes"],
        files=made,
    )
    assert rc == 0
    (copy,) = store.copies
    assert copy["entry"] == "lab-08"
    assert copy["files"] == {"a.pdf": "p"}


def test_a_hand_release_held_at_a_conflict_records_nothing(monkeypatch):
    store = Store([]).install(monkeypatch)
    _cli(monkeypatch, ["--entry", "lab-08"], outcome=HELD)
    assert store.writes == []


# ------------------------------------------------------------------------- the matcher


def test_a_hand_release_covers_a_copy_it_made_where_the_plan_puts_it():
    sched = _sched()

    def covered(src, dest=None, repo="materials"):
        made = Deploy("cm", src, repo, dest)
        return [
            (r.label, sub) for r, _d, sub in schedule_plan.covered_copies(sched, made)
        ]

    assert covered("labs/08_session-8") == [("lab-08", "")]
    assert covered("labs") == [("lab-08", "08_session-8")]
    assert covered("/") == [
        ("lab-08", "labs/08_session-8"),
        ("lecture-8", "lectures/08"),
        ("syllabus", "SYLLABUS.md"),
    ]
    # Released somewhere else, or into another repo: not the plan's copy.
    assert covered("labs", "week8") == []
    assert covered("labs", repo="site") == []
    assert covered("labs/08_session-8/sub") == []
    assert covered("lab") == []


def test_within_is_the_unnumbered_refusals_own_test():
    assert schedule_plan.within("labs", "labs/08") == "08"
    assert schedule_plan.within("labs/08", "labs/08") == ""
    assert schedule_plan.within("", "labs/08") == "labs/08"
    assert schedule_plan.within("labs/0", "labs/08") is None


def test_a_copy_that_copied_nothing_has_not_landed():
    # Everything in it excluded: recorded, it would never ship once files are added.
    assert not Copied(LAB, "c1", {}, RELEASED).landed
    assert Copied(LAB, "c1", FILES, UNCHANGED).landed


def test_the_preview_seeds_nothing_and_says_nothing_about_seeding(monkeypatch, capsys):
    tree = {"labs/08_session-8/slides.pdf": "s1"}
    Store(None, {"materials": tree}).install(monkeypatch)
    scheduler._release_decisions("Course-Org", "Semester-Org", [_release(LAB)], NOW)
    assert "[seed]" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("due", "recorded"),
    [
        # Two entries copying into one folder: the second's folder is the first's.
        (
            [Deploy("cm", "lec01/slides", "materials", "slides")],
            [Deploy("cm", "lec02/slides", "materials", "slides")],
        ),
        # A parent copy whose child has shipped.
        (
            [Deploy("cm", "lectures", "materials", None)],
            [Deploy("cm", "lectures/02_intro", "materials", None)],
        ),
        # A whole-repo copy into a repo other copies land in.
        ([Deploy("cm", "/", "materials", None)], [LAB]),
        # A copy whose source was re-pointed, landing where the old one did.
        ([Deploy("cm2", "labs/08_session-8", "materials", None)], [LAB]),
    ],
)
def test_a_destination_another_copy_explains_is_not_a_seed(monkeypatch, due, recorded):
    copies = [
        _entry(d=d)
        | {"dest": {"repo": d.semester_dest_repo, "path": release_record.key_of(d)[3]}}
        for d in recorded
    ]
    tree = {
        "slides/a.pdf": "1",
        "lectures/02_intro/n.md": "2",
        "labs/08_session-8/s.pdf": "3",
    }
    Store(copies, {"materials": tree}).install(monkeypatch)
    calls = _shipping(monkeypatch)
    _tick(_release(*due, label="new"))
    assert calls == [due]


def test_copies_due_together_into_one_place_both_ship(monkeypatch):
    parent, child = LAB, Deploy("cm", "labs/08_session-8/solutions", "materials", None)
    Store(None, {"materials": {"labs/08_session-8/solutions/a.py": "1"}}).install(
        monkeypatch
    )
    calls = _shipping(monkeypatch)
    _tick(_release(parent, child))
    assert calls == [[parent, child]]


def test_the_root_spellings_are_one_key():
    keys = {release_record.key_of(Deploy("cm", p, "site", p)) for p in ("", "/", ".")}
    assert keys == {("cm", "", "site", "")}
    assert schedule_plan.within(".", "labs/08") == "labs/08"


def test_a_hand_release_leaves_a_copy_due_later_to_the_schedule(monkeypatch):
    store = Store([]).install(monkeypatch)
    later = datetime(2099, 1, 1, tzinfo=UTC)
    sched = Schedule(releases=[Release("lab-08", later, deploy=[LAB])])
    monkeypatch.setattr(deploy.schedule, "load", lambda org: sched)
    monkeypatch.setattr(
        deploy,
        "deploy_many",
        lambda c, s, deploys, sync=True, remove=None: Batch(
            0,
            True,
            [Copied(d, "c", {"08_session-8/a": "1"}, RELEASED) for d in deploys],
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy",
            "--course-org",
            "C",
            "--semester-org",
            "S",
            "--course-source-repo",
            "cm",
            "--course-source-path",
            "labs",
            "--no-preview",
        ],
    )
    assert deploy.main() == 0
    assert store.writes == []


def test_a_release_that_stops_still_saves_its_seeds(monkeypatch):
    seeded = Deploy("cm", "lectures/08", "materials", None)
    store = Store(None, {"materials": {"lectures/08/n.md": "n"}}).install(monkeypatch)

    def stops(*a, **k):
        raise RuntimeError("could not list the org")

    monkeypatch.setattr(scheduler, "deploy_many", stops)
    assert _tick(_release(seeded, Deploy("cm", "labs/09", "materials", None)))[0] == 1
    assert [c["dest"]["path"] for c in store.copies] == ["lectures/08"]


def test_a_ship_time_written_with_no_offset_is_utc_and_dates_beside_an_aware_one():
    # A hand repair may write `2026-09-01T10:00:00`: read as UTC, never compared naive.
    from datetime import UTC, datetime

    a = ("cm", "labs/01", "materials", "labs/01")
    b = ("cm", "labs/02", "materials", "labs/02")
    copies = {
        a: {"first_shipped_at": "2026-09-01T10:00:00"},
        b: {"first_shipped_at": "2026-09-02T10:00:00Z"},
    }
    assert release_record.first_shipped(copies, a) == datetime(
        2026, 9, 1, 10, tzinfo=UTC
    )
    assert release_record.released_at(
        copies, [a, b], False, lambda k: None
    ) == datetime(2026, 9, 1, 10, tzinfo=UTC)
    # A dated row with a copy the record does not date: unknown, and no history read.
    del copies[b]["first_shipped_at"]
    assert release_record.released_at(copies, [a, b], False, _no_read) is None
    # A TBC row: that copy is dated by its history instead.
    later = datetime(2026, 8, 30, tzinfo=UTC)
    assert release_record.released_at(copies, [a, b], True, lambda k: later) == later


def _no_read(_k):
    raise AssertionError("a dated row reads no history")
