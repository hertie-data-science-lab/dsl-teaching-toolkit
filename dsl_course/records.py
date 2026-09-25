"""Where the engine keeps what it writes: ONE folder, `.system/`, in every repo it writes
into beside faculty (decision 0010).

A repo's root holds only what an instructor edits; everything the engine writes - its
status, the outcomes of console runs, the fire-once markers, the records of what was sent -
lives under `.system/`. `path(kind, ...)` is the one spelling of each: the writers and the
readers ask it, the console reads the same table from `console/schemas/names.json`, and
`migrate` is the only module that knows where a record used to be.

Stdlib only, no I/O: this is a vocabulary, like `course`.
"""

from __future__ import annotations

SYSTEM_DIR = ".system"

# kind -> its path under SYSTEM_DIR. A directory kind takes further parts in `path`.
RECORDS = {
    # semester-config and the course org's `.github` (counts only there: it is public)
    "status": "status.json",
    "outcomes": "outcomes",
    # semester-config: the pointer to the course org, and the semester's records
    "pointer": "dsl-course.yml",
    "lock": "assignments.lock.yml",
    "snapshots": "snapshots",
    "autograde": "autograde",
    "solutions": "solutions",
    # the last preview of a hand out WITH the solution: who, and which template - the
    # real run of it goes ahead only straight after (`assign.preview_first`)
    "solution_preview": "solution-preview.json",
    # one marker per assignment whose marks automation returned at its
    # `marks_return_datetime` (`grades.marks_due`)
    "marks_returned": "marks-returned",
    "gradebook": "gradebook",
    "distributed": "gradebook/distributed.csv",
    "team_formation": "team-formation",
    "archive": "archive.md",
    "semester_gradebook": "semester-gradebook.csv",
    # the course org's `.github`: the refresh's heartbeat and its miss ledger
    "heartbeat": "last-refresh",
    "missing_semesters": "missing-semesters",
    # the org's `.github`, only while `migrate` has its Actions paused: what each repo was
    # set to before, so the unpause restores exactly that
    "migration_pause": "migration-pause.json",
    # the course org's `.github`: the run settings `migrate` took out of each template,
    # for its semesters' `assignments.yml`
    "migration_run_keys": "migration-run-keys.json",
    # materials repos: the toolkit describing itself, never released
    "maintaining": "MAINTAINING.md",
    "syllabus_sample": "SYLLABUS.md.sample",
    "syllabus_sessions": "SYLLABUS.sessions.md",
}


def path(kind: str, *parts: str) -> str:
    """The repo-relative path of a record: `path("autograde", slug, "_graded.json")`."""
    return "/".join((SYSTEM_DIR, RECORDS[kind], *parts))
