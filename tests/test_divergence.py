"""Decision 0043 rule 6: a released copy that differs from what was released.

The pure comparison (`divergence`), the problems the semester status raises from it
(`status_json.divergence_problems`), and Release again's "also remove": the record keeps a
file a copy did not overwrite, and `deploy_many` deletes only files the copy no longer
carries.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from dsl_course import deploy, divergence, release_record, status_json
from dsl_course.ops.registry import REGISTRY, Request
from dsl_course.schedule import Deploy, Release, Schedule
from dsl_course.status_json import SemesterFacts
from tests import test_release_merge

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
LAB = Deploy("cm", "labs/08", "materials", None)
SOLUTIONS = Deploy("cm", "solutions/08", "materials", "labs/08/solutions")
KEY = release_record.key_of(LAB)
# The bare-repo world `deploy_many` runs against (`test_release_merge`).
world = test_release_merge.world
MANIFEST = {"slides.pdf": "s1", "notes.md": "n1", "data/a.csv": "a1"}


# ------------------------------------------------------------------ the comparison


def test_a_course_edit_and_a_new_course_file_are_the_course_kind():
    drifts = divergence.compare(
        MANIFEST,
        {**MANIFEST, "slides.pdf": "s2", "extra.md": "e1"},
        dict(MANIFEST),
    )
    assert set(drifts) == {divergence.COURSE}
    assert drifts[divergence.COURSE].changed == ("slides.pdf",)
    assert drifts[divergence.COURSE].added == ("extra.md",)


def test_a_file_gone_from_the_course_but_still_released_is_deleted_in_course():
    source = {"slides.pdf": "s1", "notes.md": "n1"}
    drifts = divergence.compare(MANIFEST, source, dict(MANIFEST))
    assert set(drifts) == {divergence.DELETED}
    assert drifts[divergence.DELETED].deleted == ("data/a.csv",)
    # Gone from both: nothing left for students to see, nothing to remove.
    dest = {"slides.pdf": "s1", "notes.md": "n1"}
    assert divergence.compare(MANIFEST, source, dest) == {}


def test_an_edit_or_a_new_file_in_the_released_copy_is_the_released_kind():
    dest = {**MANIFEST, "notes.md": "n9", "mine.md": "m1"}
    drifts = divergence.compare(MANIFEST, dict(MANIFEST), dest)
    assert set(drifts) == {divergence.RELEASED}
    assert drifts[divergence.RELEASED].changed == ("notes.md",)
    assert drifts[divergence.RELEASED].added == ("mine.md",)


def test_another_copy_landing_inside_is_left_out_of_every_side():
    dest = {**MANIFEST, "solutions/key.md": "k1"}
    nested = divergence.nested_in(
        ("materials", "labs/08"),
        [
            ("materials", "labs/08"),
            ("materials", "labs/08/solutions"),
            ("other", "labs/08/x"),
        ],
    )
    assert nested == ["solutions"]
    assert divergence.compare(MANIFEST, dict(MANIFEST), dest, nested) == {}


def test_an_unreadable_side_judges_none_of_its_kinds():
    assert divergence.compare(MANIFEST, None, {**MANIFEST, "x": "1"}).keys() == {
        divergence.RELEASED
    }
    assert divergence.compare(MANIFEST, {**MANIFEST, "x": "1"}, None).keys() == {
        divergence.COURSE
    }


def test_the_course_side_is_filtered_as_a_copy_is():
    blobs = {
        "labs/08/slides.pdf": "s1",
        "labs/08/solutions/key.md": "k",  # the denylist, below the copy's own path
        "labs/08/.DS_Store": "c",  # clutter
        "labs/08/drafts/wip.md": "w",  # .releaseignore
        "labs/09/other.md": "o",
        ".github/workflows/x.yml": "g",
        "README.md": "r",
    }
    withheld = {"labs/08/drafts", "labs/08/drafts/wip.md"}
    assert divergence.source_files("labs/08", "labs/08", blobs, withheld, ()) == {
        "slides.pdf": "s1"
    }
    # A whole-repo copy leaves the root plumbing and an unwritten stub out.
    whole = divergence.source_files("/", "", blobs, withheld, {"README.md"})
    assert ".github/workflows/x.yml" not in whole and "README.md" not in whole
    assert whole["labs/09/other.md"] == "o"
    # A single file, keyed by the name it lands under.
    assert divergence.source_files(
        "labs/09/other.md", "readings/week9.md", blobs, (), ()
    ) == {"week9.md": "o"}


def test_blobs_of_drops_the_folders_of_a_full_listing():
    shas = {"labs": "t1", "labs/08": "t2", "labs/08/a.md": "b1", "README.md": "b2"}
    assert divergence.blobs_of(shas) == {"labs/08/a.md": "b1", "README.md": "b2"}


def test_the_fingerprint_moves_only_with_the_changed_side():
    one = divergence.fingerprint([("a", "1"), ("b", "2")])
    assert one == divergence.fingerprint([("b", "2"), ("a", "1")])
    assert len(one) == 8
    assert one != divergence.fingerprint([("a", "1"), ("b", "3")])


# ------------------------------------------------------------------ the problems


def _facts(
    source=None,
    dest=None,
    manifests=None,
    deploys=(LAB,),
) -> SemesterFacts:
    sched = Schedule(releases=[Release("lab-08", NOW, deploy=list(deploys))])
    facts = SemesterFacts(org="Semester-Org", sched=sched)
    facts.manifests = {KEY: dict(MANIFEST)} if manifests is None else manifests
    facts.recorded = frozenset(facts.manifests)
    src = {f"labs/08/{p}": s for p, s in (source or MANIFEST).items()}
    facts.source_blobs = {"cm": src}
    facts.dest_blobs = {
        "materials": {f"labs/08/{p}": s for p, s in (dest or MANIFEST).items()}
    }
    facts.dest_branches = {"materials": "main"}
    return facts


def _kinds(problems):
    return sorted(p["kind"] for p in problems)


def test_each_kind_is_one_later_problem_on_the_release():
    facts = _facts(
        source={"slides.pdf": "s2", "notes.md": "n1"},
        dest={**MANIFEST, "mine.md": "m"},
    )
    problems = status_json.divergence_problems(facts, autosync=False)
    assert _kinds(problems) == [
        "DIVERGED_COURSE",
        "DIVERGED_DELETED",
        "DIVERGED_RELEASED",
    ]
    course = next(p for p in problems if p["kind"] == "DIVERGED_COURSE")
    assert course["id"].startswith("schedule:lab-08:DIVERGED_COURSE:")
    assert (course["release"], course["aside"], course["stage"]) == (
        "lab-08",
        True,
        "K4",
    )
    assert course["fix"]["screen"] == "release"
    (copy,) = course["diverged"]
    assert copy["changed"] == ["slides.pdf"] and copy["dest"]["path"] == "labs/08"
    # A suggestion: never a red problem, whatever the release's own date.
    (tiered,) = status_json._tier([dict(course)], NOW)
    assert tiered["bites"] == status_json.LATER and "when" not in tiered


def test_autosync_suppresses_only_the_course_kind():
    facts = _facts(source={"slides.pdf": "s2", "notes.md": "n1"})
    assert _kinds(status_json.divergence_problems(facts, autosync=True)) == [
        "DIVERGED_DELETED"
    ]


def test_a_set_aside_id_is_dropped_until_that_side_changes_again():
    facts = _facts(dest={**MANIFEST, "notes.md": "n9"})
    (p,) = status_json.divergence_problems(facts, autosync=False)
    assert status_json.divergence_problems(facts, False, frozenset({p["id"]})) == []
    # The course side moving does not bring it back...
    facts.source_blobs["cm"]["labs/08/slides.pdf"] = "s2"
    again = status_json.divergence_problems(facts, False, frozenset({p["id"]}))
    assert _kinds(again) == ["DIVERGED_COURSE"]
    # ...the released side moving does.
    facts.dest_blobs["materials"]["labs/08/notes.md"] = "n10"
    back = status_json.divergence_problems(facts, False, frozenset({p["id"]}))
    assert "DIVERGED_RELEASED" in _kinds(back)


def test_a_nested_copy_is_its_own_and_an_orphan_record_is_not_checked():
    orphan = ("cm", "labs/07", "materials", "labs/07")
    facts = _facts(
        dest={**MANIFEST, "solutions/key.md": "k"},
        manifests={KEY: dict(MANIFEST), orphan: {"x.md": "1"}},
        deploys=(LAB, SOLUTIONS),
    )
    # The orphan's destination drifted too: no plan copy has its key, so nothing is said.
    facts.dest_blobs["materials"]["labs/07/y.md"] = "2"
    assert status_json.divergence_problems(facts, autosync=False) == []


def test_a_shared_destination_leaves_out_the_released_kind():
    other = Deploy("cm2", "labs/08", "materials", "labs/08")
    facts = _facts(dest={**MANIFEST, "theirs.md": "t"}, deploys=(LAB, other))
    assert status_json.divergence_problems(facts, autosync=False) == []


def test_a_course_copy_with_no_files_left_is_its_source_problems_not_this():
    facts = _facts(source={})
    facts.source_blobs = {"cm": {}}
    assert status_json.divergence_problems(facts, autosync=False) == []


def test_the_orphan_record_is_logged_once_by_repo_and_path(monkeypatch, capsys):
    facts = _facts(manifests={("cm", "labs/07", "materials", "labs/07"): {"a": "1"}})
    monkeypatch.setattr(
        status_json.schedule, "source_repo_shas", lambda *a: pytest.fail("no read")
    )
    status_json._gather_sources("Course-Org", facts)
    out = capsys.readouterr().out
    assert out.count("no longer in the plan") == 1
    assert "materials/labs/07" in out


# ------------------------------------------------------------------ the record


def test_a_re_copy_keeps_the_files_it_did_not_carry_unless_removed():
    record = release_record.Record()
    record.ship("lab-08", LAB, "c1", dict(MANIFEST), NOW, refresh=True)
    record.ship("lab-08", LAB, "c2", {"slides.pdf": "s2"}, NOW, refresh=True)
    assert record.copies[KEY]["files"] == {**MANIFEST, "slides.pdf": "s2"}
    removed = ("notes.md", "data/a.csv")
    record.ship("lab-08", LAB, "c3", {"slides.pdf": "s2"}, NOW, True, removed)
    assert record.copies[KEY]["files"] == {"slides.pdf": "s2"}


# ------------------------------------------------------------------ Release again


def test_remove_deletes_only_files_the_copy_no_longer_carries(world):
    world.commit("cm", {"labs/08/a.md": "a", "labs/08/b.md": "b"})
    world.commit("materials", {"README.md": "the semester"})
    first = deploy.deploy_many("Course-Org", "Semester-Org", [LAB], sync=False)
    assert first.copies[0].files.keys() == {"a.md", "b.md"}

    world.commit("cm", {"labs/08/b.md": None})
    batch = deploy.deploy_many(
        "Course-Org",
        "Semester-Org",
        [LAB],
        sync=False,
        # a.md is still in the course: asked for, never removed; ../ never leaves the copy.
        remove={KEY: ["a.md", "b.md", "../../README.md"]},
    )
    assert batch.errors == 0 and batch.copies[0].removed == ("b.md",)
    assert world.files("main") == ["README.md", "labs/08/a.md"]


def test_without_remove_a_deleted_file_stays_released(world):
    world.commit("cm", {"labs/08/a.md": "a", "labs/08/b.md": "b"})
    deploy.deploy_many("Course-Org", "Semester-Org", [LAB], sync=False)
    world.commit("cm", {"labs/08/b.md": None})
    deploy.deploy_many("Course-Org", "Semester-Org", [LAB], sync=False)
    assert world.files("main") == ["labs/08/a.md", "labs/08/b.md"]


def test_the_cli_removes_what_the_record_lists(monkeypatch):
    sched = Schedule(releases=[Release("lab-08", NOW, deploy=[LAB])])
    monkeypatch.setattr(deploy.schedule, "load", lambda org: sched)
    record = release_record.Record({KEY: {"files": dict(MANIFEST)}})
    monkeypatch.setattr(deploy.release_record, "load", lambda org: record)
    seen = {}

    def fake(c, s, deploys, sync=True, remove=None):
        seen["remove"] = remove
        return deploy.Batch(0, True, [])

    monkeypatch.setattr(deploy, "deploy_many", fake)
    argv = ["deploy", "--course-org", "C", "--semester-org", "S", "--entry", "lab-08"]
    monkeypatch.setattr(sys, "argv", [*argv, "--remove-deleted", "--no-preview"])
    deploy.main()
    assert {k: sorted(v) for k, v in seen["remove"].items()} == {KEY: sorted(MANIFEST)}
    # Only with an entry: a release of paths has no record to read the files from.
    paths = ["--course-source-repo", "cm", "--course-source-path", "labs/08"]
    monkeypatch.setattr(sys, "argv", [*argv[:5], *paths, "--remove-deleted"])
    assert deploy.main() == 1


def test_the_console_op_spells_the_flag():
    op = REGISTRY["release.entry"]

    def request(**args) -> Request:
        return Request("release.entry", "prof", "C", "S", args, preview=False)

    assert "--remove-deleted" not in op.argv(request(entry="lab-08"))
    assert (
        op.argv(request(entry="lab-08", remove_deleted=True))[-1] == "--remove-deleted"
    )
