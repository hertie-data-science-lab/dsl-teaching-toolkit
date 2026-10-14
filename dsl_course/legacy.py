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

from .course import CONFIG_REPO, INSTRUCTORS_FILE, JOIN_REPO
from .faults import NOT_MIGRATED


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
SCHEDULE_TOP = "schedule.yml top level"
SCHEDULE_ENTRY = "schedule.yml <block>.<key>"
SCHEDULE_FILE = "schedule.yml <block>.<key>.files[]"
SCHEDULE_ASSIGNMENT = "schedule.yml assignments.<key>"
ASSIGNMENTS_YML = "assignments.yml top level"
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

_BLOCKS = "a block per kind (`lectures:`, `labs:`, `readings:`, `exams:`, `events:`, `assets:`)"
_MIGRATE = "run the 0.11.0 migration (`python3 -m dsl_course.migrate`)"

ROWS: tuple[Old, ...] = (
    # schedule.yml before decision 0038: one `releases:` block, the kind and number on the
    # entry or inferred from a folder or a label, `deploy:` copies. Read as NOT_MIGRATED
    # by `schedule.parse`, rewritten by `migrate.schedule_shape`.
    Old(
        "releases",
        _BLOCKS,
        SCHEDULE_TOP,
        "0.11.0",
        NOT_MIGRATED,
        f"nothing in it releases or shows - {_MIGRATE}",
    ),
    Old(
        "kind",
        "the block the entry is written in",
        SCHEDULE_ENTRY,
        "0.11.0",
        NOT_MIGRATED,
        "the block decides the kind",
    ),
    Old(
        "type",
        "the block the entry is written in",
        SCHEDULE_ENTRY,
        "0.11.0",
        NOT_MIGRATED,
        "the block decides the kind",
    ),
    Old(
        "deploy",
        "files",
        SCHEDULE_ENTRY,
        "0.11.0",
        NOT_MIGRATED,
        f"none of its copies ships - {_MIGRATE}",
    ),
    Old(
        "course_source_repo",
        "from_course_repo",
        SCHEDULE_FILE,
        "0.11.0",
        NOT_MIGRATED,
        "this copy never ships",
    ),
    Old(
        "course_source_path",
        "from_path",
        SCHEDULE_FILE,
        "0.11.0",
        NOT_MIGRATED,
        "this copy never ships",
    ),
    Old(
        "semester_dest_repo",
        "to_semester_repo",
        SCHEDULE_FILE,
        "0.11.0",
        NOT_MIGRATED,
        "this copy lands in the default repo",
    ),
    Old(
        "semester_dest_path",
        "to_path",
        SCHEDULE_FILE,
        "0.11.0",
        NOT_MIGRATED,
        "this copy lands at its own path",
    ),
    Old(
        "deploy_datetime",
        "release_datetime",
        SCHEDULE_FILE,
        "0.11.0",
        NOT_MIGRATED,
        "this copy ships at its default time",
    ),
    Old(
        "course_source_repo",
        "template",
        SCHEDULE_ASSIGNMENT,
        "0.11.0",
        NOT_MIGRATED,
        "the assignment has no template",
    ),
    Old(
        "semester_dest_repo",
        "repo_name",
        SCHEDULE_ASSIGNMENT,
        "0.11.0",
        NOT_MIGRATED,
        "its repos take the default name",
    ),
    # An assignment's run settings, per assignment, moved inline onto its schedule.yml
    # entry (decision 0038 rule 9); `defaults:` stays until WP-F.
    Old(
        "assignments",
        "schedule.yml assignments.<key> (the settings written on each entry)",
        ASSIGNMENTS_YML,
        "0.11.0",
        NOT_MIGRATED,
        "none of these settings is read",
    ),
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
