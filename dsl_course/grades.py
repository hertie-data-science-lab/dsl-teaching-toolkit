"""dsl-course grades -- private per-student gradebook repos (the single home for grades).

Every grade, individual or group, is delivered into a PRIVATE per-student repo
`grades-<handle>` (student = read). Team project repos may be public (showcase /
open-courseware), so grades NEVER touch them: a group result is split into the shared
team score (duplicated into each member's gradebook) and that member's private
adjustment + final grade, all delivered individually.

Three idempotent stages, each a faculty & instructors workflow:

    sync       cohort/grades-<handle>            (private; student = read) per onboarded student
                     ^
    render     classroom-config/grades/<assignment>.csv   (faculty & instructors' table, was Excel)
                     |  build per-student YAML
                     v
               classroom-config/gradebook/<handle>.yml  -- opened as ONE PR (the preview)
                     |  distribute (after the PR merges)
                     v
               cohort/grades-<handle>/grades.yml + an email to the student's hertie email address

`classroom-config` keeps the full grade archive (private source of truth); the PR diff is
the all-students-at-once preview that the Power Automate flow never gave.

Usage:
    python3 -m dsl_course.grades sync       --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.grades render     --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.grades distribute --cohort-org hertie-dsl-demo-f2026
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import mailer, roster
from .access import FACULTY_READ_ACCESS, grant_faculty
from .course import CONFIG_REPO, GRADEBOOK_PREFIX
from .discovery import course_name_for_cohort, list_org_repos
from .gh_contents import (
    blob_sha,
    dump_csv,
    get_file_content,
    get_file_with_sha,
    put_file,
    read_csv,
)
from .ghcli import GIT_ENV, clone, gh, git, is_already_exists
from .log import log, log_err, log_ok, log_person, log_step
from .repos import (
    add_collaborator,
    create_repo,
    default_branch,
    repo_exists,
    set_repo_topics,
)

GRADES_DIR = "grades"  # faculty-edited source tables, one CSV per assignment
GRADEBOOK_DIR = "gradebook"  # rendered per-student YAML staged for the preview PR
# Which student has been told about which version of their gradebook. SYSTEM-owned, in the
# private classroom-config. Without it the notify set came from the PUSH outcome, which is
# not durable: a notification that failed could never be retried, because the re-run saw
# every gradebook as `unchanged` and emailed nobody, in green.
NOTIFIED_PATH = f"{GRADEBOOK_DIR}/notified.csv"
NOTIFIED_HEADER = ("github_handle", "grades_sha", "notified_at")
RENDER_BRANCH = "grades-update"
COHORT_CSV_NAME = "cohort-gradebook.csv"  # generated wide faculty-only glance view

# One assignment CSV row. `autograde_score` is the machine's passing-test count on both
# individual and group assignments; `manual_score` is the faculty & instructors' hand-marked
# part of an individual one. Group rows additionally carry the shared `team_score`, that
# member's private `individual_adjustment`, and the shared `team_comments`. `final_grade` is
# authoritative (stored explicitly so faculty & instructors own any rounding/combination).
# `autograde_score`/`manual_score` are faculty-internal working columns - they never appear in
# the student's gradebook. Values stay strings - a grade may be a letter, a percentage, or
# "+4" - we never coerce.
#
# Every name says its scope and its role outright: a marker opening the CSV in Excel reads the
# header, not this file, and the old `auto`/`manual`/`final` said neither which part of the
# mark they held nor who owned them.
GRADE_FIELDS = (
    "github_handle",
    "team",
    "autograde_score",
    "manual_score",
    "team_score",
    "individual_adjustment",
    "final_grade",
    "individual_comments",
    "team_comments",
)

# The columns the autograder writes. They are WRITE-ONCE (see merge_auto): once one holds a
# value - a machine score, or a marker's correction of one - no later run may replace it.
#
# `team_score` is deliberately NOT here. It is the marker's shared team mark, and while the
# group autograder wrote its passing-test count into it the two shared one write-once cell:
# whichever landed first won, so a team marked before the grading deadline silently lost its
# autograde and a team marked after it found the cell already taken. The count now goes to
# `autograde_score`, exactly as it does for an individual assignment, and `team_score` is
# faculty-owned like `manual_score` beside it.
MACHINE_FIELDS = ("autograde_score", "team")

# The legend for `grades.yml`. This README is the ONLY place a student is told what the
# keys in that file mean - there is no other documentation on their side - so every key
# gradebook_entry can emit is defined here, and the two must be kept in step.
_STARTER_README = (
    "# Your gradebook\n\n"
    "This private repository is accessible only to you. Grades and feedback for each "
    "piece of assessment appear in `grades.yml` as the course progresses.\n\n"
    "## What each field means\n\n"
    "| Field | Meaning |\n"
    "| --- | --- |\n"
    "| `final_grade` | Your mark for that assignment. This is the authoritative one. |\n"
    "| `individual_comments` | Your marker's feedback on your own work. |\n"
    "| `team` | Group assignments only: the team you submitted with. |\n"
    "| `team_score` | Group assignments only: the mark the whole team received. |\n"
    "| `individual_adjustment` | Group assignments only: your own adjustment to the team "
    "score, up or down. Nobody else on your team sees yours. |\n"
    "| `team_comments` | Group assignments only: feedback shared with the whole team. |\n"
)


@dataclass
class GradeRow:
    github_handle: str = ""
    team: str = ""
    autograde_score: str = ""
    manual_score: str = ""
    team_score: str = ""
    individual_adjustment: str = ""
    final_grade: str = ""
    individual_comments: str = ""
    team_comments: str = ""


# --------------------------------------------------------------------------- pure core


# The pre-rename column names. A CSV still carrying them must never be parsed, because the
# rename left `github_handle`, `team` and `team_comments` untouched: an old row would parse
# PARTIALLY, keeping its handle while every renamed cell read blank. Nothing downstream can
# tell that apart from a legitimately sparse row, so `render` would publish a gradebook with
# the marks missing and `merge_auto` would write the file back having discarded them - a
# green run that destroys a marker's work. Refusing to read the file is the only safe answer.
_RETIRED_GRADE_FIELDS = {
    "auto": "autograde_score",
    "manual": "manual_score",
    "team_grade": "team_score",
    "adjustment": "individual_adjustment",
    "final": "final_grade",
    "comments": "individual_comments",
}


class RetiredGradeHeader(Exception):
    """A grades CSV written against the pre-rename column names."""


def parse_grades(text: str) -> list[GradeRow]:
    """Parse one `grades/<assignment>.csv` into rows (blank/extra columns tolerated).

    Raises RetiredGradeHeader if the header uses the pre-rename names - see
    `_RETIRED_GRADE_FIELDS` for why that cannot be tolerated the way an unknown column is."""
    reader = read_csv(text, ("github_handle",), "grades CSV")
    stale = [f for f in (reader.fieldnames or []) if f.strip() in _RETIRED_GRADE_FIELDS]
    if stale:
        renames = ", ".join(f"{f} -> {_RETIRED_GRADE_FIELDS[f.strip()]}" for f in stale)
        raise RetiredGradeHeader(
            f"grades CSV uses retired column name(s): {renames}. Rename the header row and "
            f"re-run; reading it as-is would drop every mark in those columns."
        )
    return [
        GradeRow(**{f: (row.get(f) or "").strip() for f in GRADE_FIELDS})
        for row in reader
    ]


def gradebook_entry(row: GradeRow) -> dict:
    """One assignment's entry for a student. Group fields appear only for group rows; the
    faculty-internal `autograde_score`/`manual_score` columns are never surfaced (the student
    sees the authoritative `final_grade`, not the machine/manual split); empty fields are
    dropped so an individual assignment reads as just final_grade + individual_comments.

    The keys carry the CSV's own names, so the file a student opens and the file their marker
    fills in use one vocabulary - and `_STARTER_README` beside it defines each one, since this
    file is the whole of what a student is given."""
    entry: dict[str, str] = {}
    if row.team:
        entry["team"] = row.team
        if row.team_score:
            entry["team_score"] = row.team_score
        if row.individual_adjustment:
            entry["individual_adjustment"] = row.individual_adjustment
        if row.team_comments:
            entry["team_comments"] = row.team_comments
    if row.final_grade:
        entry["final_grade"] = row.final_grade
    if row.individual_comments:
        entry["individual_comments"] = row.individual_comments
    return entry


def build_gradebooks(per_assignment: dict[str, list[GradeRow]]) -> dict[str, dict]:
    """Pivot {assignment: [rows]} into {handle: {student, assignments: {assignment: ...}}}.

    Deterministic: assignments are folded in sorted order so the rendered YAML (and thus
    the preview diff) is stable across runs."""
    books: dict[str, dict] = {}
    canonical: dict[str, str] = {}  # fold key -> the first spelling seen for it
    for assignment in sorted(per_assignment):
        for row in per_assignment[assignment]:
            if not row.github_handle:
                continue
            handle = canonical.setdefault(
                row.github_handle.casefold(), row.github_handle
            )
            book = books.setdefault(handle, {"student": handle, "assignments": {}})
            book["assignments"][assignment] = gradebook_entry(row)
    return books


def render_yaml(book: dict) -> str:
    """Serialise one student's gradebook to YAML text (insertion order preserved)."""
    return yaml.safe_dump(book, sort_keys=False, allow_unicode=True)


def dump_grades(rows: list[GradeRow]) -> str:
    """Serialise grade rows back to CSV text (header + one row per GradeRow)."""
    return dump_csv(GRADE_FIELDS, ([getattr(r, f) for f in GRADE_FIELDS] for r in rows))


def render_cohort_csv(per: dict[str, list[GradeRow]]) -> str:
    """Pivot every assignment's raw grade rows into one wide CSV - one row per student,
    one column-group per assignment (sorted) - a faculty-only glance view. Generated,
    never hand-edited; the per-assignment CSVs in GRADES_DIR remain the source of
    truth. Unlike gradebook_entry (student-facing, redacted), this keeps
    autograde_score/manual_score/team_score/individual_adjustment too - it never leaves
    classroom-config."""
    fields = tuple(f for f in GRADE_FIELDS if f != "github_handle")
    assignments = sorted(per)
    by_assignment: dict[str, dict[str, GradeRow]] = {}
    handle_set: set[str] = set()
    for a, rows in per.items():
        by_assignment[a] = {r.github_handle: r for r in rows if r.github_handle}
        handle_set.update(by_assignment[a])
    handles = sorted(handle_set)

    def wide_row(handle: str) -> list[str]:
        row = [handle]
        for a in assignments:
            r = by_assignment[a].get(handle)
            row.extend(getattr(r, f) if r else "" for f in fields)
        return row

    return dump_csv(
        ["github_handle"] + [f"{a}_{f}" for a in assignments for f in fields],
        (wide_row(h) for h in handles),
    )


def merge_auto(text: str, updates: list[tuple[str, dict[str, str]]]) -> str:
    """Upsert machine-graded fields into a grades CSV, returning new CSV text.

    Each update is (github_handle, {field: value}); the handle's row is updated in place
    (leaving every column the update does not name exactly as it was) or created and
    appended if absent. Only MACHINE_FIELDS are write-once - a key outside that set is
    written unconditionally, so callers must not pass a faculty-owned column such as
    `team_score` or `manual_score` here. Used by the collector to record `autograde_score` (plus `team` on a
    group assignment) without disturbing hand-marked scores, comments, or the final grade.

    WRITE-ONCE. A machine-written cell (MACHINE_FIELDS) that already holds a value is NEVER
    overwritten - this fills EMPTY cells only, on scheduled and manual runs alike. That is
    what makes a hand-edited `autograde_score` safe: no re-run can silently replace a
    marker's correction with a recomputed score. To get a fresh machine score, clear those
    cells (or delete the CSV) first, then re-grade."""
    rows = parse_grades(text) if text.strip() else []
    order: list[str] = []
    by_handle: dict[str, GradeRow] = {}
    # Fold-keyed: GitHub logins are case-insensitive, so `Ada-L` in a hand-typed CSV and
    # `ada-l` from the API are one student. Keyed raw, the autograder appended a SECOND row
    # for the same person and every write-once guard on the first one read as an empty
    # cell. The row's own spelling - the marker's - is left exactly as written.
    for r in rows:
        key = r.github_handle.casefold()
        if key not in by_handle:
            by_handle[key] = r
            order.append(key)
    preserved = 0
    for handle, fields in updates:
        key = handle.casefold()
        row = by_handle.get(key)
        if row is None:
            row = GradeRow(github_handle=handle)
            by_handle[key] = row
            order.append(key)
        kept = 0
        for field, value in fields.items():
            if field in MACHINE_FIELDS and getattr(row, field, ""):
                kept += 1  # already filled (hand-edited or graded before) - leave it
                continue
            setattr(row, field, value)
        if kept:
            preserved += kept
            log_person(f"  [keep] {handle}: {kept} existing cell(s) left as they are")
    if preserved:
        log_ok(
            f"{preserved} existing machine-written cell(s) preserved - "
            f"{'/'.join(MACHINE_FIELDS)} are write-once; clear a cell to have it recomputed"
        )
    return dump_grades([by_handle[h] for h in order])


# ---------------------------------------------------------------------- gh/git wiring


RENDER_BOT_NAME = "dsl-bot"  # the author name GIT_ENV stamps on engine-made commits


def _human_commit_authors(
    log_output: str, bot_name: str = RENDER_BOT_NAME
) -> list[str]:
    """Author names on the render branch (from `git log --format=%an base..branch`) that are
    NOT the bot - i.e. a reviewer's own commit on the open preview PR. `render` refuses to
    force-overwrite the branch when this is non-empty, so a reviewer's correction is never
    silently discarded by a re-render."""
    return sorted(
        {
            a.strip()
            for a in log_output.splitlines()
            if a.strip() and a.strip() != bot_name
        }
    )


def load_grade_sources(cohort_org: str) -> dict[str, list[GradeRow]]:
    """Read every `grades/<assignment>.csv` from the cohort's classroom-config repo."""
    code, out = gh(
        "api",
        f"repos/{cohort_org}/{CONFIG_REPO}/contents/{GRADES_DIR}",
        "--jq",
        ".[].name",
    )
    if code != 0:
        log_err(
            f"no {GRADES_DIR}/ in {cohort_org}/{CONFIG_REPO} - add a grade CSV first "
            f"(e.g. {GRADES_DIR}/assignment-1.csv)"
        )
        return {}
    per: dict[str, list[GradeRow]] = {}
    stale = []
    for name in sorted(out.splitlines()):
        if not name.endswith(".csv"):
            continue
        content = get_file_content(cohort_org, CONFIG_REPO, f"{GRADES_DIR}/{name}")
        if content is None:
            continue
        try:
            per[name[:-4]] = parse_grades(content)
        except RetiredGradeHeader as exc:
            # Name the file and carry on reading the others, so one un-migrated CSV
            # reports itself by name rather than aborting on the first one it meets.
            log_err(f"{GRADES_DIR}/{name}: {exc}")
            stale.append(name)
    if stale:
        # Returning a partial set would render gradebooks for the cohort MINUS these
        # assignments, which reads as "those students have no marks" rather than as an
        # error. Nothing is rendered until every CSV can be read.
        log_err(
            f"{len(stale)} grade CSV(s) still use retired column names - rename their "
            f"header rows before rendering: {', '.join(stale)}"
        )
        return {}
    return per


def _existing_repos(cohort_org: str) -> frozenset[str] | None:
    """The cohort's repo names off ONE paginated listing, or None when it could not be read.

    Asking `repo_exists` per student cost a GET per student on every nightly sync, for a
    question one listing answers for the whole cohort. None falls the caller back to that
    probe: the listing is an optimisation, not a new way for a sync to fail."""
    try:
        return frozenset(r["name"] for r in list_org_repos(cohort_org))
    except RuntimeError as exc:
        log_err(
            f"could not list {cohort_org}'s repos - falling back to a probe per repo: {exc}"
        )
        return None


def provision_one(
    cohort_org: str, handle: str, existing: frozenset[str] | None = None
) -> str:
    """Ensure a private grades-<handle> repo exists with the student as read collaborator.

    `existing` is the cohort's repo names off ONE listing (`_existing_repos`); membership
    in it answers "is this gradebook already there?" without a GET per student. None - no
    listing to hand - falls back to probing this one repo."""
    repo = f"{GRADEBOOK_PREFIX}{handle}"
    existed = (
        repo in existing if existing is not None else repo_exists(cohort_org, repo)
    )
    if existed:
        log_person(f"  [skip] gradebook {cohort_org}/{repo}")
    else:
        if not create_repo(
            cohort_org,
            repo,
            private=True,
            description=f"Private gradebook for @{handle}",
            person=True,
        ):
            return "failed-create"
        put_file(
            cohort_org, repo, "README.md", _STARTER_README.encode(), "init gradebook"
        )
        if not set_repo_topics(cohort_org, repo, ["gradebook"]):
            # Not named: this log is public. The nightly sweep converges the topic.
            log_err("  ! a gradebook is untagged - the nightly sweep converges it")

        # At creation only: a team grant does not decay, and the nightly sweep
        # (access.converge_faculty_access) owns the floor for every gradebook that already
        # exists - so re-granting on every sync cost two PUTs per student for nothing.
        #
        # Read, not write: `distribute` rewrites grades.yml from
        # `classroom-config/grades/<slug>.csv`, so a mark corrected here would be
        # overwritten on the next run. The CSV is where a mark belongs.
        grant_faculty(cohort_org, repo, FACULTY_READ_ACCESS, missing_is_note=True)
    if add_collaborator(cohort_org, repo, handle, permission="pull"):
        log_person(f"  [ok]   + @{handle} (read)")
        return "skipped" if existed else "ok"
    # A gradebook the student can't open is a failure, not a partial success - the status
    # starts with "failed" so it reaches the exit code (see sync).
    log_err(f"  ! could not add @{handle} (not a real account?)")
    return "failed-no-collaborator"


def sync(cohort_org: str, dry_run: bool = False) -> int:
    """Provision one private gradebook repo per onboarded enrolled student. Idempotent.

    Auditors are read-only and are never assessed, so they get no gradebook."""
    students = roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - no gradebooks to sync.")
        return 1
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    log_step(f"Syncing {len(onboarded)} gradebook repo(s) in {cohort_org}")
    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, never assessed)")

    # ONE listing of the cohort answers "is it already there?" for every student below.
    # A dry run creates nothing, so it needs no answer.
    existing = None if dry_run else _existing_repos(cohort_org)
    results: dict[str, int] = {}
    for s in onboarded:
        if dry_run:
            log_person(f"    DRY-RUN  {cohort_org}/{GRADEBOOK_PREFIX}{s.github_handle}")
            continue
        status = provision_one(cohort_org, s.github_handle, existing)
        results[status] = results.get(status, 0) + 1
    if dry_run:
        return 0
    log_ok(f"Done - {json.dumps(results)}")
    return 1 if any(k.startswith("failed") for k in results) else 0


def render(cohort_org: str) -> int:
    """Build per-student gradebook YAML and open it as ONE preview PR in classroom-config."""
    per = load_grade_sources(cohort_org)
    if not per:
        return 1
    books = build_gradebooks(per)
    if not books:
        log_err("no graded students found across the grade CSVs.")
        return 1
    log_step(
        f"Rendering {len(books)} gradebook(s) from {len(per)} assignment table(s) "
        f"-> preview PR on {cohort_org}/{CONFIG_REPO}"
    )

    base = default_branch(cohort_org, CONFIG_REPO, fallback="main")
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "cfg"
        if not clone(cohort_org, CONFIG_REPO, wd):
            log_err(f"could not clone {cohort_org}/{CONFIG_REPO}")
            return 1
        # A prior render's branch may carry a reviewer's OWN commit (a grade fixed on the open
        # preview PR). Rebuilding from base and force-pushing would silently discard it, so
        # check first: any non-bot commit on `base..RENDER_BRANCH` means human edits are
        # present - refuse rather than clobber. When the branch has only bot render commits
        # (or doesn't exist), the reset + force-push below is safe and idempotent as before.
        if (
            git(
                "-C",
                str(wd),
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                RENDER_BRANCH,
            )[0]
            == 0
        ):
            git(
                "-C",
                str(wd),
                *GIT_ENV,
                "fetch",
                "-q",
                "origin",
                f"{RENDER_BRANCH}:refs/remotes/origin/{RENDER_BRANCH}",
            )
            _code, authors = git(
                "-C",
                str(wd),
                "log",
                "--format=%an",
                f"origin/{base}..origin/{RENDER_BRANCH}",
            )
            human = _human_commit_authors(authors)
            if human:
                log_err(
                    f"the {RENDER_BRANCH} branch carries commit(s) by {', '.join(human)} - a "
                    f"reviewer edited the preview. Refusing to overwrite them: merge or close "
                    f"the open PR (or delete the branch) before re-rendering."
                )
                return 1
        git("-C", str(wd), *GIT_ENV, "checkout", "-q", "-B", RENDER_BRANCH, base)
        gbdir = wd / GRADEBOOK_DIR
        gbdir.mkdir(exist_ok=True)
        for handle in sorted(books):
            (gbdir / f"{handle}.yml").write_text(render_yaml(books[handle]))
            log_person(f"  [ok] + {GRADEBOOK_DIR}/{handle}.yml")
        (wd / COHORT_CSV_NAME).write_text(render_cohort_csv(per))
        log_ok(f"+ {COHORT_CSV_NAME}")

        git("-C", str(wd), *GIT_ENV, "add", "-A")
        # `git commit` exits non-zero BOTH when there is nothing staged and when the commit
        # itself fails (a lock, a full disk, a hook). Reported as "nothing new to render",
        # a real failure looked like the idempotent no-op: green, no preview PR, and the
        # marker's grades never distributed. Ask what is staged, then commit.
        if git("-C", str(wd), "diff", "--cached", "--quiet")[0] == 0:
            log_ok("nothing new to render (gradebooks already match the source).")
            return 0
        code, out = git(
            "-C",
            str(wd),
            *GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            "grades: render gradebooks",
        )
        if code != 0:
            log_err(f"could not commit the rendered gradebooks: {out[:200]}")
            return 1
        if (
            git("-C", str(wd), *GIT_ENV, "push", "-q", "-f", "origin", RENDER_BRANCH)[0]
            != 0
        ):
            log_err("push failed")
            return 1

    # Open the preview PR (or reuse the open one on this branch).
    title = "Grades: review before distribution"
    body = (
        f"Rendered {len(books)} gradebook(s) from `{GRADES_DIR}/`.\n\n"
        f"**This is the preview.** Review every student's grades in the diff below, then "
        f"merge to distribute to each private `grades-<handle>` repo.\n"
    )
    code, out = gh(
        "pr",
        "create",
        "--repo",
        f"{cohort_org}/{CONFIG_REPO}",
        "--base",
        base,
        "--head",
        RENDER_BRANCH,
        "--title",
        title,
        "--body",
        body,
    )
    if code == 0:
        log_ok(f"preview PR opened: {out.strip().splitlines()[-1]}")
    elif is_already_exists(out):
        log_ok("preview PR already open for this branch (updated).")
    else:
        log_err(f"could not open PR: {out[:200]}")
        return 1
    return 0


def distribute(cohort_org: str, notify: bool = True, dry_run: bool = False) -> int:
    """Fan the merged gradebook/<handle>.yml files out into each private grades-<handle>,
    then (unless silenced) email each student a notification to their hertie email address.

    Clone classroom-config once and read the files locally (rather than an API GET per
    student); the only per-student call left is the unavoidable write to each repo.

    dry_run pushes nothing and only previews the email notifications (the grade values
    themselves were already previewed in the render PR)."""
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "cfg"
        if not clone(cohort_org, CONFIG_REPO, wd):
            log_err(f"could not clone {cohort_org}/{CONFIG_REPO}")
            return 1
        gbdir = wd / GRADEBOOK_DIR
        files = sorted(gbdir.glob("*.yml")) if gbdir.is_dir() else []
        if not files:
            log_err(
                f"no {GRADEBOOK_DIR}/ in {cohort_org}/{CONFIG_REPO} - run `render` first."
            )
            return 1
        log_step(f"Distributing {len(files)} gradebook(s) in {cohort_org}")

        notified = _read_notified(wd)
        results: dict[str, int] = {}
        changed: list[str] = []  # pushed a NEW version this run
        live: dict[str, str] = {}  # handle -> the sha their repo now holds
        for f in files:
            content = f.read_text()
            sha = blob_sha(content.encode())
            if dry_run:
                log_person(
                    f"    DRY-RUN  would update {GRADEBOOK_PREFIX}{f.stem}/grades.yml"
                )
                changed.append(f.stem)
                continue
            status = _push_gradebook(cohort_org, f.stem, content)
            results[status] = results.get(status, 0) + 1
            if status == "ok":
                changed.append(f.stem)
            if status in ("ok", "unchanged"):
                live[f.stem] = sha
    if dry_run:
        log_ok(f"DRY-RUN previewed {len(changed)} gradebook update(s) - nothing pushed")
        if notify and changed:
            _email_updates(cohort_org, changed, dry_run=True)
        return 0
    log_ok(f"Done - {json.dumps(results)}")

    # Who still needs telling. A marker entry that does not match the sha now in the repo
    # means either a new version (the `ok` case) or a notification that failed last time
    # (the `unchanged` case the push outcome could never express).
    if notified is None:
        # First run on this cohort: no marker means nothing to catch up on. Notify what
        # changed, and record every current gradebook so the next run has a baseline.
        pending = list(changed)
    else:
        pending = [h for h in live if notified.get(h) != live[h]]

    notifications_failed = 0
    if notify and pending:
        notifications_failed, told = _email_updates(cohort_org, pending, dry_run=False)
    else:
        told = []
    if notify or notified is None:
        record = dict(notified or {})
        record.update({h: live[h] for h in (live if notified is None else told)})
        if record != (notified or {}):
            _write_notified(cohort_org, record)

    pushes_failed = any(k.startswith("failed") for k in results)
    return 1 if pushes_failed or notifications_failed else 0


def _read_notified(wd: Path) -> dict[str, str] | None:
    """`{handle: gradebook sha}` from the marker, or None if the cohort has none yet.

    None is kept distinct from `{}`: a cohort with no marker file has nothing to catch up
    on, and must NOT be told wholesale that their grades have been updated."""
    path = wd / NOTIFIED_PATH
    if not path.is_file():
        return None
    return {
        (row.get("github_handle") or "").strip(): (row.get("grades_sha") or "").strip()
        for row in read_csv(path.read_text(), ("github_handle",), NOTIFIED_PATH)
    }


def _write_notified(cohort_org: str, notified: dict[str, str]) -> None:
    """One PUT for the whole cohort - the read was a local file in the clone."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    body = dump_csv(
        NOTIFIED_HEADER,
        ((handle, sha, stamp) for handle, sha in sorted(notified.items())),
    )
    put_file(
        cohort_org,
        CONFIG_REPO,
        NOTIFIED_PATH,
        body.encode(),
        "grades: record notifications sent",
    )


def _push_gradebook(cohort_org: str, handle: str, content: str) -> str:
    """Write grades.yml into grades-<handle>. One of `ok` (the file changed), `unchanged`
    (it already held exactly this) or `failed-push` (a missing repo - sync not run - or an
    unreadable one).

    The tri-state is what `distribute` notifies off. `put_file` compares blob shas and
    skips an identical write, but it returns True either way, so a re-run of Distribute
    told the WHOLE cohort their grades had been updated whenever a marker had corrected
    one row. Read the sha here instead - the same single GET put_file would have made -
    and hand it back as `expected_sha`, which also makes this a safe read-modify-write:
    a gradebook that moved on between the read and the write is refused, not clobbered."""
    repo = f"{GRADEBOOK_PREFIX}{handle}"
    body = content.encode()
    try:
        current = get_file_with_sha(cohort_org, repo, "grades.yml")
    except RuntimeError as exc:
        log_err(f"could not read {repo}/grades.yml: {exc}")
        return "failed-push"
    if current is not None and current[1] == blob_sha(body):
        log_person(f"  [skip] {repo}/grades.yml unchanged")
        return "unchanged"
    sha = current[1] if current is not None else ""
    if not put_file(
        cohort_org, repo, "grades.yml", body, "grades: update", expected_sha=sha
    ):
        return "failed-push"
    log_person(f"  [ok] + {repo}/grades.yml")
    return "ok"


def update_message(
    student: roster.Student, cohort_org: str, course_name: str = ""
) -> mailer.Message:
    """The 'your grades have been updated' email for one student: (to, subject, body).

    The course goes in the SUBJECT as well as the body: the inbox list is where a student
    taking several of these actually tells them apart, and by the time they have opened it
    the body is redundant. A course that carries no name yet keeps the generic wording
    rather than emailing a blank."""
    url = f"https://github.com/{cohort_org}/{GRADEBOOK_PREFIX}{student.github_handle}"
    course_suffix = f" for {course_name}" if course_name else ""
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"Your grades{course_suffix} have been updated. View them in your private "
        f"gradebook:\n"
        f"  {url}\n"
    )
    subject = (
        f"Your grades for {course_name} have been updated"
        if course_name
        else "Your grades have been updated"
    )
    return (student.hertie_email, subject, body)


def sample_body(cohort_org: str, course_name: str = "") -> str:
    """The notification rendered with PLACEHOLDERS, for the dry-run preview.

    `update_message` with a placeholder in place of a student - see `mailer.sample_of`."""
    return mailer.sample_of(
        lambda student: update_message(student, cohort_org, course_name),
        github_handle="<handle>",
    )


def _email_updates(
    cohort_org: str, handles: list[str], dry_run: bool = False
) -> tuple[int, list[str]]:
    """Email each student a 'grades updated' notification to their hertie email address,
    linking to their private gradebook repo (the grade's source of truth).

    Returns `(how many FAILED, which handles were told)`. `distribute` exits on the first
    and records the second: the grades themselves are already pushed by this point, so a
    mail failure is not a reason to undo anything - but a student who never got the
    notification does not know to look, and a green run told nobody."""
    # Fold-keyed for the same reason merge_auto is: the gradebook filenames come from the
    # grade CSVs (a marker's typing) and the roster's casing is its own, so a case-only
    # difference used to mean a student was silently never told their grades had landed.
    students = roster.load(cohort_org)
    if students is None:
        # `or []` here read an unreadable roster as "nobody to email": the grades went out
        # and the run was green having notified no one. enrol_codes.run reds on the same
        # condition; so must this.
        log_err(
            f"roster in {cohort_org} could not be read - "
            f"{len(handles)} notification(s) not sent."
        )
        return len(handles), []
    by_handle: dict[str, roster.Student] = {}
    for s in students:
        if s.github_handle:
            by_handle.setdefault(s.github_handle.casefold(), s)
    # Name the course in the body - a student taking several of these can't tell one
    # "your grades have been updated" from another. Read live from the course org's
    # dsl-course.yml; a course that carries no name yet keeps the generic wording rather
    # than emailing a blank.
    # The grades are already pushed by the time this runs, so a transient read failure or a
    # malformed dsl-course.yml must not turn a successful distribution into a traceback
    # with zero notifications sent - load_yaml_config deliberately RAISES on both. The
    # course name is a nicety; the email is not.
    try:
        course_name = course_name_for_cohort(cohort_org)
    except Exception as exc:  # a name is never worth losing the notifications over
        log_err(f"could not read the course name ({exc}) - mailing without it")
        course_name = ""
    messages = []
    handle_for: dict[str, str] = {}
    for handle in handles:
        student = by_handle.get(handle.casefold())
        if not student or not student.hertie_email:
            continue
        handle_for[student.hertie_email] = handle
        messages.append(update_message(student, cohort_org, course_name))
    if not messages:
        # A withdrawn student is an ordinary state and must not red every distribution
        # from here on; a count says it happened without naming anyone.
        if handles:
            log_err(f"{len(handles)} gradebook(s) have no roster row with an email")
        return 0, []
    sent = mailer.send_bulk(
        messages, dry_run=dry_run, sample=sample_body(cohort_org, course_name)
    )
    failed = len(messages) - len(sent)
    if failed:
        log_err(f"{failed} of {len(messages)} grade notification(s) not sent")
    return failed, [handle_for[to] for to in sent if to in handle_for]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("sync", "render", "distribute"):
        p = sub.add_parser(name)
        p.add_argument("--cohort-org", required=True)
        if name == "sync":
            p.add_argument("--dry-run", action="store_true")
        if name == "distribute":
            p.add_argument(
                "--no-notify",
                action="store_true",
                help="Skip the email notification (just push the grades).",
            )
            # Default ON, as in enrol_codes: the rendered workflow passes --dry-run /
            # --no-dry-run explicitly, so a bare local invocation cannot send by accident.
            # `sync --dry-run` above keeps store_true - it is not a mail path.
            p.add_argument(
                "--dry-run",
                action=argparse.BooleanOptionalAction,
                default=True,
                help="Preview the grade emails; push nothing, send nothing (default).",
            )
    args = parser.parse_args()

    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        if args.action == "sync":
            return sync(args.cohort_org, dry_run=args.dry_run)
        if args.action == "render":
            return render(args.cohort_org)
        return distribute(
            args.cohort_org, notify=not args.no_notify, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
