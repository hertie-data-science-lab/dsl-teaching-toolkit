"""A release's actual time written back to schedule.yml when it beat the plan, and a
hand-out's when it ran early (decision 0043 rule 3).

The reads and the write are stubbed at `schedule`'s imported `get_file_with_sha` and at
`gh_contents.put_file` (what `put_file_as_read` calls); the record's are
`test_release_record`'s `Store`.
"""

from __future__ import annotations

import dataclasses
import functools
import re
import sys
from datetime import UTC, datetime

import pytest
import yaml
from test_release_record import EARLIER, FILES, Store, _autosync, _entry, _shipping

from dsl_course import deploy, gh_contents, release_record, schedule, scheduler
from dsl_course.deploy import RELEASED, Batch, Copied
from dsl_course.gh_contents import load_yaml_lines

FUTURE = "2099-01-05T10:00"
PAST = "2020-01-05T10:00"
STAMP = re.compile(
    r"release_datetime: \d{4}-\d\d-\d\dT\d\d:\d\d   # set automatically: when it went out"
)


def _plan(event: str, release_datetime: str | None = None) -> str:
    """A labs entry with two copies; the first may state its own `release_datetime`."""
    own = f"\n        release_datetime: {release_datetime}" if release_datetime else ""
    return (
        "labs:\n"
        "  lab-08:\n"
        "    session: 8\n"
        f"    event_datetime: {event}\n"
        "    files:\n"
        "      # the lab itself\n"
        "      - from_course_repo: cm\n"
        f"        from_path: labs/08{own}\n"
        "      - from_course_repo: cm\n"
        "        from_path: solutions/labs/08\n"
        "        to_path: labs/08/solutions\n"
        "  lab-09:\n"
        "    session: 9\n"
        "    event_datetime: tbc\n"
    )


def _parsed(text: str) -> schedule.Schedule:
    return schedule.parse(load_yaml_lines(text))


class File:
    """semester-config's schedule.yml as `schedule` reads and writes it."""

    def __init__(self, text: str):
        self.text = text
        self.reads = 0
        self.writes: list[tuple[str, str]] = []  # (text, commit message)

    def install(self, monkeypatch):
        def read(org, repo, path, **_k):
            assert (repo, path) == ("semester-config", "schedule.yml")
            self.reads += 1
            return self.text, f"sha{len(self.writes)}"

        def put(org, repo, path, content, msg, expected_sha=None):
            self.writes.append((content.decode(), msg))
            self.text = content.decode()
            return True

        monkeypatch.setattr(schedule, "get_file_with_sha", read)
        monkeypatch.setattr(gh_contents, "get_file_with_sha", read)
        monkeypatch.setattr(gh_contents, "put_file", put)
        return self


# ------------------------------------------------------------------------ the rule


AT = datetime(2026, 10, 27, 12, 49, 3, tzinfo=UTC)  # 13:49 in Berlin


def _copies(text: str):
    (lab,) = [r for r in _parsed(text).releases if r.label == "lab-08"]
    return lab, lab.deploy[0]


@pytest.mark.parametrize(
    ("event", "own", "writes"),
    [
        ("2026-10-27T14:49", None, False),  # on time: default 13:49 Berlin == AT
        ("2026-10-28T10:00", None, True),  # early against the default
        ("2026-10-27T10:00", "2026-10-30T09:00", True),  # early against an explicit
        ("2026-10-27T10:00", None, False),  # late: the default was 09:00
        ("tbc", None, True),  # no planned moment
    ],
)
def test_a_copy_writes_its_ship_time_only_when_it_beat_the_plan(event, own, writes):
    lab, d = _copies(_plan(event, own))
    assert schedule.beat_the_plan(lab, d, AT) is writes


def test_an_explicit_time_is_told_from_the_default_by_its_line():
    explicit = _copies(_plan("2026-10-28T10:00", "2026-10-30T09:00"))[1]
    default = _copies(_plan("2026-10-28T10:00"))[1]
    assert "release_datetime" in explicit.lines
    assert "release_datetime" not in default.lines
    assert default.deploy_datetime is not None  # the parse filled the default in


# ------------------------------------------------------------------- the text surgery


def _want(text: str, i: int = 0, stamp: str = "2026-10-27T13:49"):
    (lab,) = [r for r in _parsed(text).releases if r.label == "lab-08"]
    return schedule.ReleaseTime(lab, lab.deploy[i], stamp)


def test_a_default_time_is_inserted_after_the_items_last_key():
    text = _plan("2026-10-28T10:00")
    new, done, declined = schedule._stamp_release_times(text, [_want(text, 1)])
    assert declined == [] and [old for _w, old in done] == ["(default)"]
    lines = new.splitlines()
    at = lines.index("        to_path: labs/08/solutions")
    assert STAMP.fullmatch(lines[at + 1].strip())
    assert lines[at + 1].startswith("        release_datetime: 2026-10-27T13:49")
    # Everything else, the comment included, is where it was.
    assert [ln for i, ln in enumerate(lines) if i != at + 1] == text.splitlines()
    (lab,) = [r for r in _parsed(new).releases if r.label == "lab-08"]
    assert lab.deploy[1].deploy_datetime.strftime("%Y-%m-%dT%H:%M") == (
        "2026-10-27T13:49"
    )


def test_an_explicit_time_has_its_own_line_rewritten():
    text = _plan("2026-10-27T10:00", "2026-10-30T09:00")
    new, done, _ = schedule._stamp_release_times(text, [_want(text)])
    assert [old for _w, old in done] == ["2026-10-30T09:00"]
    assert "2026-10-30T09:00" not in new
    assert len(new.splitlines()) == len(text.splitlines())
    (lab,) = [r for r in _parsed(new).releases if r.label == "lab-08"]
    assert lab.deploy[0].deploy_datetime.strftime("%H:%M") == "13:49"


@pytest.mark.parametrize(
    "files",
    [
        "    files:\n      - {from_course_repo: cm, from_path: labs/08}\n",
        "    files: [{from_course_repo: cm, from_path: labs/08}]\n",
        "    files: {from_course_repo: cm, from_path: labs/08}\n",
    ],
)
def test_a_flow_mapping_is_declined(files):
    text = "labs:\n  lab-08:\n    session: 8\n    event_datetime: tbc\n" + files
    new, done, declined = schedule._stamp_release_times(text, [_want(text)])
    assert new == text and done == [] and len(declined) == 1


def test_a_single_mapping_files_block_takes_the_line_at_its_indent():
    text = (
        "labs:\n  lab-08:\n    session: 8\n    event_datetime: tbc\n"
        "    files:\n      from_course_repo: cm\n      from_path: labs/08\n"
    )
    new, done, _ = schedule._stamp_release_times(text, [_want(text)])
    assert done and new.splitlines()[-1].startswith("      release_datetime: ")


# --------------------------------------------------------------- the write, one commit


def test_every_write_back_of_a_run_is_one_commit(monkeypatch, capsys):
    text = _plan("2099-01-05T10:00")
    file = File(text).install(monkeypatch)
    lab, _ = _copies(text)
    schedule.record_release_times(
        "Semester-Org", "Europe/Berlin", [(lab, d, AT) for d in lab.deploy]
    )
    assert file.reads == 1
    ((new, message),) = file.writes
    assert message.splitlines() == [
        "schedule: record release times",
        "",
        "lab-08 labs/08 release_datetime  (default) -> 2026-10-27T13:49",
        "lab-08 solutions/labs/08 release_datetime  (default) -> 2026-10-27T13:49",
    ]
    assert new.count("release_datetime: 2026-10-27T13:49") == 2
    assert "lab-08 labs/08 release_datetime  (default) -> 2026-10-27T13:49" in (
        capsys.readouterr().out
    )


def test_nothing_that_beat_its_plan_reads_nothing(monkeypatch):
    text = _plan("2026-10-27T10:00")
    file = File(text).install(monkeypatch)
    lab, d = _copies(text)
    schedule.record_release_times("Semester-Org", "Europe/Berlin", [(lab, d, AT)])
    assert file.reads == 0 and file.writes == []


def test_a_declined_item_names_the_line_to_add_by_hand(monkeypatch, capsys):
    text = (
        "labs:\n  lab-08:\n    session: 8\n    event_datetime: tbc\n"
        "    files: [{from_course_repo: cm, from_path: labs/08}]\n"
    )
    file = File(text).install(monkeypatch)
    lab, d = _copies(text)
    schedule.record_release_times("Semester-Org", "Europe/Berlin", [(lab, d, AT)])
    assert file.writes == []
    err = capsys.readouterr().err
    assert "add `release_datetime: 2026-10-27T13:49` to labs.lab-08" in err


def test_a_write_refused_for_a_newer_file_reapplies_onto_it(monkeypatch):
    text = _plan("tbc")
    edited = text + "  lab-10:\n    session: 10\n    event_datetime: tbc\n"
    state = {"text": text, "sha": "s0"}

    def read(org, repo, path, **_k):
        out = (state["text"], state["sha"])
        state["text"], state["sha"] = edited, "s1"  # faculty pushed meanwhile
        return out

    writes = []

    def put(org, repo, path, content, msg, expected_sha=None):
        if expected_sha != state["sha"]:
            return False
        writes.append(content.decode())
        return True

    monkeypatch.setattr(schedule, "get_file_with_sha", read)
    monkeypatch.setattr(gh_contents, "get_file_with_sha", read)
    monkeypatch.setattr(gh_contents, "put_file", put)
    lab, d = _copies(text)
    schedule.record_release_times("Semester-Org", "Europe/Berlin", [(lab, d, AT)])
    (new,) = writes
    assert "lab-10" in new and "release_datetime: 2026-10-27T13:49" in new


# ------------------------------------------------------- the callers: Release materials


def _cli(monkeypatch, text, argv):
    monkeypatch.setattr(deploy.schedule, "load", lambda org: _parsed(text))
    monkeypatch.setattr(
        deploy,
        "deploy_many",
        lambda c, s, deploys, sync=True, remove=None, replace=False: Batch(
            0, True, [Copied(d, "c9", FILES, RELEASED) for d in deploys]
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["deploy", "--course-org", "C", "--semester-org", "S", *argv, "--no-preview"],
    )
    return deploy.main()


@pytest.mark.parametrize(("event", "writes"), [(FUTURE, 2), ("tbc", 2), (PAST, 0)])
def test_release_now_writes_back_an_early_or_tbc_copy(monkeypatch, event, writes):
    Store([]).install(monkeypatch)
    file = File(_plan(event)).install(monkeypatch)
    assert _cli(monkeypatch, file.text, ["--entry", "lab-08"]) == 0
    assert file.text.count("set automatically: when it went out") == writes
    assert len(file.writes) == (1 if writes else 0)


def test_release_again_of_a_recorded_copy_writes_nothing(monkeypatch):
    lab = _copies(_plan(FUTURE))[1]
    Store([_entry(d=lab)]).install(monkeypatch)
    file = File(_plan(FUTURE)).install(monkeypatch)
    assert _cli(monkeypatch, file.text, ["--entry", "lab-08"]) == 0
    # The solutions copy was not in the record: it alone is written.
    assert file.text.count("set automatically: when it went out") == 1
    assert "        to_path: labs/08/solutions\n        release_datetime:" in file.text


def test_a_hand_release_of_a_tbc_folder_writes_its_time(monkeypatch):
    Store([]).install(monkeypatch)
    file = File(_plan("tbc")).install(monkeypatch)
    argv = ["--course-source-repo", "cm", "--course-source-path", "labs/08"]
    assert _cli(monkeypatch, file.text, argv) == 0
    assert "        from_path: labs/08\n        release_datetime:" in file.text


# ---------------------------------------------- the scheduler: seeds and autosync write none


@pytest.mark.parametrize("autosync", [True, False])
def test_a_scheduled_seed_or_reship_never_touches_the_schedule(monkeypatch, autosync):
    # Seeded: the solutions copy, unrecorded, already at its destination. Re-shipped:
    # the same copy recorded, under autosync. Either way schedule.yml is not read.
    lab, _d = _copies(_plan("2026-10-27T10:00"))
    solutions = dataclasses.replace(lab, deploy=[lab.deploy[1]])
    recorded = [_entry(d=lab.deploy[1])] if autosync else []
    for c in recorded:
        c["dest"]["path"] = "labs/08/solutions"
    store = Store(recorded, {"materials": {"labs/08/solutions/s.py": "s"}})
    store.install(monkeypatch)
    _autosync(monkeypatch, autosync)
    shipped = _shipping(monkeypatch)
    monkeypatch.setattr(
        schedule, "get_file_with_sha", lambda *a, **k: pytest.fail("schedule read")
    )
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert scheduler._run_releases("C", "S", [solutions], now)[0] == 0
    assert bool(shipped) is autosync
    assert store.copies[0]["first_shipped_at"] == (EARLIER if autosync else None)


def test_the_tick_reads_the_record_once_for_lateness_and_releases(monkeypatch):
    Store([_entry(d=_copies(_plan(PAST))[1])]).install(monkeypatch)
    loads = []
    real = release_record.load
    monkeypatch.setattr(
        release_record, "load", lambda org: loads.append(org) or real(org)
    )
    _autosync(monkeypatch, False)
    _shipping(monkeypatch)
    lab, d = _copies(_plan(PAST))
    read = functools.cache(functools.partial(release_record.load, "S"))
    assert scheduler._first_shipped(read)(d) == datetime(2026, 10, 20, 9, tzinfo=UTC)
    scheduler._run_releases("C", "S", [lab], datetime(2026, 10, 27, tzinfo=UTC), read)
    assert loads == ["S"]


# --------------------------------------------------------------- hand-out, earlier once


def _handout(planned: str) -> str:
    return (
        "assignments:\n  assignment-1:\n"
        f"    handout_datetime: {planned}\n    due_datetime: 2026-10-13\n"
    )


def test_a_handout_earlier_than_planned_moves_the_plan_once(monkeypatch):
    file = File(_handout("2026-10-01T09:00")).install(monkeypatch)
    schedule.record_handout(
        "S", "assignment-1", "2026-09-22T14:05", tz_name="Europe/Berlin"
    )
    ((new, _msg),) = file.writes
    entry = yaml.safe_load(new)["assignments"]["assignment-1"]
    assert entry["handout_datetime"] == "2026-09-22T14:05"
    assert entry["due_datetime"].isoformat() == "2026-10-13"
    # The same moment again, or a later one, never moves it.
    schedule.record_handout(
        "S", "assignment-1", "2026-09-22T14:05", tz_name="Europe/Berlin"
    )
    schedule.record_handout(
        "S", "assignment-1", "2026-09-23T09:00", tz_name="Europe/Berlin"
    )
    assert len(file.writes) == 1


@pytest.mark.parametrize("planned", ["2026-09-22T14:05", "2026-09-01T09:00"])
def test_a_handout_at_or_after_its_plan_leaves_it(monkeypatch, planned):
    file = File(_handout(planned)).install(monkeypatch)
    schedule.record_handout(
        "S", "assignment-1", "2026-09-22T14:05", tz_name="Europe/Berlin"
    )
    assert file.writes == []


def test_a_whole_day_handout_counts_from_the_days_start(monkeypatch):
    file = File(_handout("2026-09-23")).install(monkeypatch)
    schedule.record_handout(
        "S", "assignment-1", "2026-09-23T08:00", tz_name="Europe/Berlin"
    )
    assert file.writes == []
    schedule.record_handout(
        "S", "assignment-1", "2026-09-22T23:00", tz_name="Europe/Berlin"
    )
    assert "handout_datetime: 2026-09-22T23:00" in file.text


def test_there_is_no_solution_write_back(monkeypatch):
    text = (
        "assignments:\n  assignment-1:\n    handout_datetime: 2026-09-01T09:00\n"
        "    due_datetime: 2026-10-13\n    solution_datetime: 2026-10-30T09:00\n"
    )
    file = File(text).install(monkeypatch)
    schedule.record_handout(
        "S", "assignment-1", "2026-09-22T14:05", tz_name="Europe/Berlin"
    )
    assert file.writes == [] and "solution_datetime: 2026-10-30T09:00" in file.text


def test_faculty_comment_on_a_rewritten_line_is_kept():
    text = _plan("2026-10-27T10:00", "2026-10-30T09:00  # after reading week")
    new, _done, _ = schedule._stamp_release_times(text, [_want(text)])
    assert "release_datetime: 2026-10-27T13:49  # after reading week\n" in new


def test_a_schedule_that_cannot_be_read_says_so_not_flow_mapping(monkeypatch, capsys):
    def broken(*a, **k):
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(schedule, "get_file_with_sha", broken)
    lab, d = _copies(_plan("tbc"))
    schedule.record_release_times("Semester-Org", "Europe/Berlin", [(lab, d, AT)])
    err = capsys.readouterr().err
    assert "could not be read (HTTP 502)" in err and "flow mapping" not in err
    assert "add `release_datetime: 2026-10-27T13:49` to labs.lab-08" in err
