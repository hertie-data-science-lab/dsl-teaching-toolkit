"""The late cutoff SSOT: the autograder's pin is computed, never an input - the due date
plus the effective `late_window_days` (assignments.yml, then the course, then the
institution's 10 days). Every value is a timezone-aware datetime; a bare due date closes at
end of day (23:59:59)."""

from __future__ import annotations

from datetime import date

import pytest

from dsl_course import settings
from dsl_course.schedule import Schedule, grading_cutoff_datetime, parse


def grading_datetime_iso(sched: Schedule, slug: str) -> str | None:
    at = grading_cutoff_datetime(sched, slug)
    return at.isoformat() if at is not None else None


def _one(due) -> Schedule:
    return parse(
        {
            "assignments": {
                "assignment-1": {"course_source_repo": "a-f2026", "due_datetime": due}
            }
        }
    )


@pytest.fixture
def window(monkeypatch):
    """Give the semester's assignments.yml a window for assignment-1."""

    def set_days(days: int) -> None:
        settings.semester_blocks.cache_clear()
        monkeypatch.setattr(
            settings,
            "_assignments_text",
            lambda org: (
                f"assignments:\n  assignment-1:\n    late_window_days: {days}\n"
            ),
        )

    return set_days


def _in(sched: Schedule) -> Schedule:
    sched.org = "Sem"
    return sched


def test_due_datetime_closes_end_of_day_plus_the_institution_window():
    assert grading_datetime_iso(_one("2026-10-13"), "assignment-1").startswith(
        "2026-10-23T23:59:59"
    )


def test_due_as_yaml_date_object():
    assert grading_datetime_iso(_one(date(2026, 10, 13)), "assignment-1").startswith(
        "2026-10-23T23:59:59"
    )


def test_due_with_explicit_time_is_honoured(window):
    window(0)
    sched = _in(_one("2026-10-13T18:00"))
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T18:00")


def test_unscheduled_assignment_is_none():
    assert grading_datetime_iso(_one("2026-10-13"), "assignment-2") is None
    assert grading_datetime_iso(Schedule(), "assignment-1") is None


def test_the_semester_window_moves_the_cutoff_not_the_due_date(window):
    window(2)
    sched = _in(_one("2026-10-13"))
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-15T23:59:59")
    # the due date students see is untouched by it
    assert (
        sched.assignments["assignment-1"]
        .due_datetime.isoformat()
        .startswith("2026-10-13T23:59:59")
    )


def test_a_zero_window_is_the_due_date(window):
    window(0)
    sched = _in(_one("2026-10-13"))
    assert grading_datetime_iso(sched, "assignment-1").startswith("2026-10-13T23:59:59")


def test_a_cutoff_without_due_is_still_dropped():
    # `due_datetime` remains the required field - it is what students are told.
    assert (
        parse(
            {"assignments": {"assignment-1": {"course_source_repo": "a"}}}
        ).assignments
        == {}
    )
