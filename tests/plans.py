"""A semester plan that cites templates, for tests about what happens once one is in the
schedule: a template the plan does not cite is handed out, collected and patched by
nothing (decision 0014)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from dsl_course.schedule import AssignmentEntry, Schedule

# Well past, so a collection reads as after the late cutoff.
DUE = datetime(2026, 1, 5, 23, 59, tzinfo=UTC)


def citing(*templates: str, due: datetime = DUE) -> Schedule:
    """Each template under the key its name gives without the semester suffix
    (`assignment-1-f2026` -> `assignment-1`), due on `due`."""
    return Schedule(
        assignments={
            re.sub(r"-[fs]\d{4}$", "", t): AssignmentEntry(
                course_source_repo=t, due_datetime=due
            )
            for t in templates
        }
    )
