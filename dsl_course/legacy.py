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

from . import records
from .course import CONFIG_REPO, JOIN_REPO, SEMESTER_FILE
from .course import COURSE_CONFIG as COURSE_FILE
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


# The hand-edited files decision 0039 folded into one per level, by their old names: read
# by `migrate` (and a 0.10 `assignments.yml` by `settings.parse_legacy_assignments`).
OLD_COURSE_FILE = "dsl-course.yml"
OLD_OPENCOURSE_FILE = "opencourse.yml"
OLD_INSTRUCTORS_FILE = "instructors.yml"
OLD_ASSIGNMENTS_FILE = "assignments.yml"
OLD_POLICY_FILE = "policy.yml"
OLD_MATERIALS_FILE = "materials.yml"

# The surfaces old names were spelt in.
SCHEDULE_TOP = "schedule.yml top level"
SCHEDULE_ENTRY = "schedule.yml <block>.<key>"
SCHEDULE_FILE = "schedule.yml <block>.<key>.files[]"
SCHEDULE_ASSIGNMENT = "schedule.yml assignments.<key>"
ASSIGNMENTS_YML = "assignments.yml top level"
GRADING_CONFIG = "grading_config.yml"
SETTINGS = "assignment settings"
COURSE_CONFIG = "dsl-course.yml"
# Keys of a 0.10 `dsl-course.yml` that moved into a block of `course.yml`.
COURSE_FILE_KEYS = "dsl-course.yml -> course.yml"
DEFAULTS_KEYS = "a defaults: block"
COURSE_GITHUB = "course .github"
SEMESTER_CONFIG = "semester-config"
TOOLKIT_ROOT = "toolkit root"
MATERIALS_ROOT = "materials repo root"
SEMESTER_ORG = "semester org"
SEMESTER_TOPICS = "semester .github topics"
COURSE_REPOS = "course org repos"
MEMBERSHIP_DISPATCH = "sync-membership dispatch payload"
STATUS_TODO_CHECK = "status.json to-do check"
ROSTER_FILES = "semester-config students.csv / teams.csv"

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
    # The term's own dates before decision 0038 amendment 2: three top-level keys, now
    # entries of the one `semester:` block.
    Old(
        "semester_start",
        "a `semester:` entry of `type: start`",
        SCHEDULE_TOP,
        "0.11.0",
        NOT_MIGRATED,
        "the semester has no start date",
    ),
    Old(
        "semester_end",
        "a `semester:` entry of `type: end`",
        SCHEDULE_TOP,
        "0.11.0",
        NOT_MIGRATED,
        "the semester has no end date",
    ),
    Old(
        "archive",
        "a `semester:` entry of `type: archive`",
        SCHEDULE_TOP,
        "0.11.0",
        NOT_MIGRATED,
        "nothing archives this semester",
    ),
    Old(
        "reading_list",
        "reading_guide",
        SCHEDULE_ENTRY,
        "0.11.0",
        NOT_MIGRATED,
        "the row shows no reading guide",
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
    # ds01's hourly membership timer (an EXTERNAL system, ds01-infra) still sends it: read
    # as `all_semesters` with a log line and no fault until ds01-infra switches.
    Old("all_cohorts", "all_semesters", MEMBERSHIP_DISPATCH, "0.10.0"),
    # Read by `migrate` only: the names it rewrites.
    Old("format", "formats", SETTINGS, "0.10.0"),
    Old("github", "assignment_repo", GRADING_CONFIG, "0.9.0"),
    Old("classroom-config", CONFIG_REPO, SEMESTER_ORG, "0.10.0"),
    Old("welcome", JOIN_REPO, SEMESTER_ORG, "0.10.0"),
    Old("people.yml", OLD_INSTRUCTORS_FILE, SEMESTER_ORG, "0.10.0"),
    Old("dsl-cohort", "dsl-semester", SEMESTER_TOPICS, "0.10.0"),
    Old("cohort-courses-pages.yml", "semesters.yml", COURSE_GITHUB, "0.10.0"),
    Old("org", "", COURSE_CONFIG, "0.10.0"),
    Old("org_name", "", COURSE_CONFIG, "0.10.0"),
    Old("cohort_defaults", "", COURSE_CONFIG, "0.10.0"),
    Old("semester_defaults", "", COURSE_CONFIG, "0.10.0"),
    # An assignment template told by its name before the `dsl-assignment` topic (0014).
    Old("assignment-", "dsl-assignment", COURSE_REPOS, "0.10.0"),
    # Decision 0039: one hand-edited file per level. Read by `migrate.config_layers`,
    # which folds each old file into its new home and deletes it; `policy.read` refuses a
    # toolkit checkout that still carries the old institution file.
    Old(OLD_COURSE_FILE, COURSE_FILE, COURSE_GITHUB, "0.11.0"),
    Old(
        OLD_OPENCOURSE_FILE, f"{COURSE_FILE} open_course_site", COURSE_GITHUB, "0.11.0"
    ),
    Old(OLD_INSTRUCTORS_FILE, f"{SEMESTER_FILE} people", SEMESTER_CONFIG, "0.11.0"),
    Old(OLD_ASSIGNMENTS_FILE, f"{SEMESTER_FILE} defaults", SEMESTER_CONFIG, "0.11.0"),
    Old(
        f"{records.SYSTEM_DIR}/{OLD_COURSE_FILE}",
        records.path("pointer"),
        SEMESTER_CONFIG,
        "0.11.0",
    ),
    Old(OLD_POLICY_FILE, "institution.yml", TOOLKIT_ROOT, "0.11.0"),
    # Not read by the engine since 0.11.0 (ruling of 14 Oct 2026): its `syllabus:` folds
    # into each semester's `student_site.syllabus`, its `kinds:` give way to the
    # institution's kinds. `migrate` reads it once, for both.
    Old(
        OLD_MATERIALS_FILE,
        f"{SEMESTER_FILE} student_site.syllabus",
        MATERIALS_ROOT,
        "0.11.0",
        cost="it is not read: the syllabus is the semester's `student_site.syllabus` "
        "and a folder's kind is the institution's",
    ),
    # A 0.10 `dsl-course.yml`'s top-level keys -> their `course.yml` place ("" = dropped).
    Old("course_name", "course.name", COURSE_FILE_KEYS, "0.11.0"),
    Old("course_code", "course.code", COURSE_FILE_KEYS, "0.11.0"),
    Old("course_description", "course.description", COURSE_FILE_KEYS, "0.11.0"),
    Old("central_ref", "course.toolkit_version_ref", COURSE_FILE_KEYS, "0.11.0"),
    Old("licence", "course.licence", COURSE_FILE_KEYS, "0.11.0"),
    Old("student_console", "course.student_console", COURSE_FILE_KEYS, "0.11.0"),
    Old("contact", "open_course_site.contact", COURSE_FILE_KEYS, "0.11.0"),
    Old("assignment_defaults", "defaults", COURSE_FILE_KEYS, "0.11.0"),
    Old("people", "people", COURSE_FILE_KEYS, "0.11.0"),
    Old("site_link_extensions", "", COURSE_FILE_KEYS, "0.11.0"),
    # A 0.10 defaults block's keys (`assignment_defaults:`, `assignments.yml defaults:`).
    Old("visibility", "assignment_repo_visibility", DEFAULTS_KEYS, "0.11.0"),
    Old("formats", "", DEFAULTS_KEYS, "0.11.0"),
    # The roster before decision 0039 rule 4: one students.csv carrying the engine's
    # columns too, and teams.csv beside it. Read by `roster.split_legacy`, which the 0.11.0
    # migration folds them with.
    Old("hertie_email", "email", ROSTER_FILES, "0.11.0"),
    Old("github_handle", ".system/roster.csv", ROSTER_FILES, "0.11.0"),
    Old("github_id", ".system/roster.csv", ROSTER_FILES, "0.11.0"),
    Old("enrol_code", ".system/roster.csv", ROSTER_FILES, "0.11.0"),
    Old("code_sent_at", ".system/roster.csv", ROSTER_FILES, "0.11.0"),
    Old("teams.csv", "students.csv `team:<number>` columns", ROSTER_FILES, "0.11.0"),
    # A status.json an engine before 0.11.0 wrote still carries it until the semester's next
    # status refresh rewrites the file; the final sweep drops the row once every live org
    # has migrated and refreshed (decision 0038 retired the check).
    Old(
        "kind_folder",
        "none: folders carry no kind (the schedule block decides it)",
        STATUS_TODO_CHECK,
        "0.11.0",
    ),
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
