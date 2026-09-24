"""Grading-deadline SSOT: the autograder's pin comes from the semester schedule, not a separate
Grade-button input. Precedence is an explicit `assignments[slug].grading_datetime`, else
`due_datetime`. Every value is a timezone-aware datetime; a bare date closes at end of day
(23:59:59)."""

from __future__ import annotations

from datetime import date

from dsl_course.schedule import Schedule, grading_datetime_iso, parse


def test_due_datetime_closes_end_of_day():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T23:59:59")


def test_due_as_yaml_date_object():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": date(2026, 10, 13),
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T23:59:59")


def test_due_with_explicit_time_is_honoured():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13T18:00",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T18:00")


def test_unscheduled_assignment_is_none():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-2") is None
    assert grading_datetime_iso(Schedule(), "assignment-1") is None


# ------------------------------------------------------------ explicit grading_datetime
# The pin students never see. It exists so the snapshot freeze and the autograde run share
# one instant that is stated outright, rather than inferred from due_datetime alone.


def test_explicit_grading_datetime_wins_over_due_datetime():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": "2026-10-15",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-15T23:59:59")
    # the due date students see is untouched by it
    assert (
        sched.assignments["assignment-1"]
        .due_datetime.isoformat()
        .startswith("2026-10-13T23:59:59")
    )


def test_grading_datetime_bare_date_closes_end_of_day():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": "2026-10-15",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-15T23:59:59")
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": date(2026, 10, 15),
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-15T23:59:59")


def test_grading_datetime_with_an_explicit_time_is_honoured():
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": "2026-10-15T09:00",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-15T09:00")


def test_grading_datetime_is_not_clamped_relative_to_due_datetime():
    # Nothing clamps it - the pin is whatever the semester states, even earlier than due.
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-20",
                    "grading_datetime": "2026-10-14",
                }
            }
        }
    )
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-14T23:59:59")


def test_malformed_grading_datetime_falls_back_to_due_datetime():
    # Silently ignored, like every other unparseable field here - not an error, and not a
    # drop of the whole entry.
    sched = parse(
        {
            "assignments": {
                "assignment-1": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "grading_datetime": "soon-ish",
                }
            }
        }
    )
    assert sched.assignments["assignment-1"].grading_datetime is None
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T23:59:59")


def test_grading_datetime_without_due_is_still_dropped():
    # `due_datetime` remains the required field - it is what students are told.
    assert (
        parse(
            {"assignments": {"assignment-1": {"grading_datetime": "2026-10-15"}}}
        ).assignments
        == {}
    )
