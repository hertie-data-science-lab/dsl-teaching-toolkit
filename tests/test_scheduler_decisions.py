"""The scheduler's dry run names what is due and will NOT be released, one
`Decision: <ref> not released: <CODE> <sentence>` line each, for the console to lift into
an outcome's `reasons`."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from dsl_course import gh_contents, grades, roster, schedule, scheduler
from dsl_course.faults import ConfigFault, FaultKind
from dsl_course.schedule import Deploy, Release

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
LINE = re.compile(r"Decision: (\S+) not released: ([A-Z_]+) (.+)")
CODES = {
    "SOURCE_MISSING",
    "SOURCE_UNWRITTEN",
    "WITHHELD",
    "SEMESTER_ARCHIVED",
    "TEAMS_INCOMPLETE",
    "ALREADY_DONE",
}


def _fault(where, kind, fires, what="cm/lectures/05_trees does not exist"):
    return ConfigFault(where, what, fires, kind=kind, field="course_source_path")


def _parsed(line: str) -> tuple[str, str, str]:
    match = LINE.fullmatch(line)
    assert match, line
    assert match.group(2) in CODES
    return match.groups()


def test_a_due_source_that_is_not_there_is_a_decision():
    decisions = scheduler.source_decisions(
        [
            _fault("releases.s5", FaultKind.MISSING_PATH, PAST),
            _fault("assignments.a2", FaultKind.MISSING_REPO, PAST, "no repo cm/a2"),
            _fault("releases.s6", FaultKind.WITHHELD, PAST, "the files are held back"),
            # Not due yet: the digest counts it down, the dry run says nothing.
            _fault("releases.s9", FaultKind.MISSING_PATH, LATER),
        ],
        NOW,
    )
    assert [_parsed(d.line())[:2] for d in decisions] == [
        ("s5", "SOURCE_MISSING"),
        ("a2", "SOURCE_MISSING"),
        ("s6", "WITHHELD"),
    ]


def test_an_archived_semester_releases_nothing_that_is_due():
    sched = schedule.parse(
        {
            "releases": {
                "s1": {
                    "event_datetime": "2026-09-01T10:00",
                    "deploy": [
                        {"course_source_repo": "cm", "course_source_path": "l1"}
                    ],
                },
                "s9": {
                    "event_datetime": "2026-12-01T10:00",
                    "deploy": [
                        {"course_source_repo": "cm", "course_source_path": "l9"}
                    ],
                },
            },
            "assignments": {
                "a1": {
                    "course_source_repo": "a1-f2026",
                    "handout_datetime": "2026-09-10T10:00",
                    "due_datetime": "2026-09-20T23:59",
                }
            },
        }
    )
    lines = [d.line() for d in scheduler.archived_decisions(sched, NOW)]
    assert [_parsed(line)[:2] for line in lines] == [
        ("s1", "SEMESTER_ARCHIVED"),
        ("a1", "SEMESTER_ARCHIVED"),
    ]


def test_an_unwritten_syllabus_is_named(monkeypatch):
    stub = f"# Syllabus\n\n{gh_contents.STUB_MARK}\n"
    monkeypatch.setattr(scheduler, "get_file_content", lambda org, repo, path: stub)
    release = Release(
        label="seed-syllabus",
        when=PAST,
        deploy=[Deploy(course_source_repo="cm", course_source_path="SYLLABUS.md")],
    )
    (decision,) = scheduler._stub_decisions("Course", [release], NOW)
    assert _parsed(decision.line())[:2] == ("seed-syllabus", "SOURCE_UNWRITTEN")


def _handout(monkeypatch, *, group: bool, teams_csv: dict, listing: dict | None):
    sched = schedule.parse(
        {
            "assignments": {
                "a1": {
                    "course_source_repo": "a1-f2026",
                    "handout_datetime": "2026-09-10T10:00",
                    "due_datetime": "2026-10-20T23:59",
                }
            }
        }
    )
    spec = grades.GradingSpec(type="group" if group else "individual")
    monkeypatch.setattr(scheduler, "load_grading_spec", lambda org, template, **_: spec)
    monkeypatch.setattr(scheduler.teams, "load", lambda org: teams_csv)
    monkeypatch.setattr(
        scheduler.roster,
        "load",
        lambda org: [
            roster.Student("ada@x.edu", "Ada", "ada", "1"),
            roster.Student("bo@x.edu", "Bo", "", ""),
        ],
    )
    release = Release(
        label="a1-handout", when=PAST, assignment="a1-f2026", assignment_slug="a1"
    )
    return scheduler._handout_decisions("Course", "Semester", sched, [release], listing)


def test_a_group_handout_with_no_teams_waits_for_them(monkeypatch):
    (decision,) = _handout(monkeypatch, group=True, teams_csv={}, listing={})
    assert _parsed(decision.line())[:2] == ("a1", "TEAMS_INCOMPLETE")


def test_a_handout_every_student_already_has_is_already_done(monkeypatch):
    (decision,) = _handout(
        monkeypatch, group=False, teams_csv={}, listing={"a1-ada": {}}
    )
    assert _parsed(decision.line())[:2] == ("a1", "ALREADY_DONE")
    # The public log never names a student or their repo.
    assert "ada" not in decision.line()


def test_a_handout_still_owed_to_someone_is_not_a_decision(monkeypatch):
    assert _handout(monkeypatch, group=False, teams_csv={}, listing={}) == []
    # A listing nobody could read decides nothing either.
    assert _handout(monkeypatch, group=False, teams_csv={}, listing=None) == []


def test_a_read_that_fails_costs_its_decisions_not_the_preview(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(scheduler.schedule, "source_faults", boom)
    sched = schedule.parse({})
    assert scheduler.dry_run_decisions("Course", "Semester", sched, [], NOW, {}) == []
    assert "could not work out every decision" in capsys.readouterr().err
