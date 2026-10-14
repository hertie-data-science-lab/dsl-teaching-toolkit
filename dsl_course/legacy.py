"""The one module that knows old names.

Every name the engine or its migration still recognises after a rename or a retirement is
a row here: what it was (`old`), what replaced it or where the fact lives now (`new`), the
file or surface it was spelt in (`where`), the release that retired it (`since`), and the
code a parser reports it under (`fault_code`; None for a row only `migrate` reads, or one
a parser accepts without a fault).

A parser asks `flag(old, where)` or `rows(where)`; `migrate` asks `renames(where)`. No
other module spells an old name. `names.json` carries the rows for the console
(`as_json`). Retiring an old name is deleting its row and the line that asks for it:
`tests/test_legacy.py` fails on a row nothing reads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .course import ASSIGNMENTS_FILE, CONFIG_REPO, INSTRUCTORS_FILE, JOIN_REPO


@dataclass(frozen=True)
class Old:
    """One old name. See the module docstring."""

    old: str
    new: str
    where: str
    since: str
    fault_code: str | None = None
    # What ignoring it costs, for a parser that says so.
    cost: str = ""


# The surfaces old names were spelt in.
SCHEDULE_ASSIGNMENT = "schedule.yml assignments.<key>"
GRADING_CONFIG = "grading_config.yml"
SETTINGS = "assignment settings"
COURSE_CONFIG = "dsl-course.yml"
COURSE_GITHUB = "course .github"
SEMESTER_ORG = "semester org"
SEMESTER_TOPICS = "semester .github topics"
COURSE_REPOS = "course org repos"
MEMBERSHIP_DISPATCH = "sync-membership dispatch payload"

_GRADING_CONFIG_HOME = (
    "in the assignment's own grading_config.yml, on the course template's "
    "`solution` branch"
)
_RUN_KEYS_HOME = f"in {CONFIG_REPO}/{ASSIGNMENTS_FILE} (`assignments.<key>`)"

ROWS: tuple[Old, ...] = (
    # Settings that USED to live in a schedule.yml `assignments:` entry (decision 0009):
    # flagged by name, so the note says where the declaration went. Live guidance.
    Old(
        "type",
        _GRADING_CONFIG_HOME,
        SCHEDULE_ASSIGNMENT,
        "0.9.0",
        cost=(
            "the assignment is handed out and graded in whatever shape grading_config.yml "
            "declares - individual when it declares none"
        ),
    ),
    Old(
        "max_team_size",
        _RUN_KEYS_HOME,
        SCHEDULE_ASSIGNMENT,
        "0.9.0",
        cost="the 'Join team' flow uses the cap declared there, or the course default",
    ),
    # ds01's hourly membership timer (an EXTERNAL system, ds01-infra) still sends it: read
    # as `all_semesters` with a log line and no fault until ds01-infra switches.
    Old("all_cohorts", "all_semesters", MEMBERSHIP_DISPATCH, "0.10.0"),
    # Read by `migrate` only: the names it rewrites.
    Old("format", "formats", SETTINGS, "0.10.0"),
    Old("github", "assignment_repo", GRADING_CONFIG, "0.9.0"),
    Old("classroom-config", CONFIG_REPO, SEMESTER_ORG, "0.10.0"),
    Old("welcome", JOIN_REPO, SEMESTER_ORG, "0.10.0"),
    Old("people.yml", INSTRUCTORS_FILE, SEMESTER_ORG, "0.10.0"),
    Old("dsl-cohort", "dsl-semester", SEMESTER_TOPICS, "0.10.0"),
    Old("cohort-courses-pages.yml", "semesters.yml", COURSE_GITHUB, "0.10.0"),
    Old("org", "", COURSE_CONFIG, "0.10.0"),
    Old("org_name", "", COURSE_CONFIG, "0.10.0"),
    Old("cohort_defaults", "", COURSE_CONFIG, "0.10.0"),
    Old("semester_defaults", "", COURSE_CONFIG, "0.10.0"),
    # An assignment template told by its name before the `dsl-assignment` topic (0014).
    Old("assignment-", "dsl-assignment", COURSE_REPOS, "0.10.0"),
)


def rows(where: str) -> tuple[Old, ...]:
    """Every old name spelt in `where`."""
    return tuple(r for r in ROWS if r.where == where)


def flag(old: str, where: str) -> Old | None:
    """`old`'s row when it is an old name in `where`, else None."""
    return next((r for r in rows(where) if r.old == old), None)


def renames(where: str) -> dict[str, str]:
    """`where`'s old names, old -> new (`""` for one that was removed outright)."""
    return {r.old: r.new for r in rows(where)}


def as_json() -> list[dict]:
    """The rows, for `names.json`."""
    return [asdict(r) for r in ROWS]


def old_name(new: str, where: str) -> str:
    """The old spelling of `new` in `where` (`migrate` reads the names it rewrites from)."""
    return next(r.old for r in rows(where) if r.new == new)
