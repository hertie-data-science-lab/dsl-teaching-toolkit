"""dsl-course collect -- faculty-side autograding (hidden tests, after the deadline).

Runs entirely in a faculty-controlled job (course-org Actions, bot token). For each
submission repo it checks out the commit that repo was frozen at (see SNAPSHOTS below),
overlays the assignment's HIDDEN tests (kept on the course template's `solution` branch,
never shipped to students), runs them, and records how many passed into the PRIVATE grading
sheet, as information for whoever marks it. Faculty & instructors write the marks there and
`grades distribute` sends them - so a student never sees a score in their own repo.

  course/<template> @ solution branch  ->  grading_config.yml + hidden tests
                |
  cohort/<slug>-<handle>  (individual)   clone @ snapshot, overlay tests, run
  cohort/<slug>-<team>    (group)              |
                v
  classroom-config/autograde/<slug>/<key>.json   (per-test detail, private archive)
  classroom-config/autograde/<slug>/<key>.ipynb  (the executed notebook, where the
                                                 completion check ran)
  classroom-config/grading_sheets/<slug>.yml     (`info.autograde`, `info.completion` -
                                                 never a mark)

Student code is run in a subprocess with the GitHub token stripped from the environment.

SNAPSHOTS (a server-timed FREEZE, not a server-timed deadline).  A git committer date is
entirely client-supplied (`GIT_COMMITTER_DATE`), so late work backdated to before the
deadline passes a `rev-list --before` pin. The hourly scheduler therefore freezes each
assignment shortly after its grading deadline, writing one row per submission repo into

    classroom-config/snapshots/<slug>.csv
        repo,sha,recorded_at,submitted_at,submitted_source

and never rewriting it. `submitted_at` is WHEN that submission arrived and
`submitted_source` says who timed it, on a three-rung ladder taken once, at the freeze:

  `push`     GitHub's own record of the push that delivered the pinned commit, read from
             the repository-activity API. Nobody can write this one, so it is what
             `days_late` should rest on.
  `commit`   no push record matched - the committer date the commits API reports, which
             is `GIT_COMMITTER_DATE` and therefore the student's to set.
  `suspect`  the same committer date, contradicted: GitHub's `pushed_at` for the repo is
             LATER than the due moment while the commit claims to predate it.

The last two carry `info.submitted_note` into the sheet, so whoever marks it can see that
the time is a claim rather than an observation. Grading pins to the recorded sha; a blank
sha means "nothing had been pushed by the deadline" and scores zero. Only with no snapshot
at all does grading fall back to the date-based pin, loudly.

WHICH commit the freeze chooses is still the last one whose committer date is on or before
the deadline, and that date is the student's to set - so a commit pushed after the deadline
but before the next tick, backdated, is still the one picked up. What the ladder fixes is
the TIME the row carries: the pin may be a backdated commit, but the moment recorded
against it is the moment GitHub saw it arrive. A chosen commit dated after `recorded_at`
needs a skewed or doctored clock, so `_snapshot_sha` says so. To re-freeze deliberately,
delete the snapshot CSV and let the next tick rebuild it.

FIRE-ONCE.  The hourly scheduler autogrades each assignment exactly once, just after its
grading deadline. The marker is an explicit SENTINEL file this module writes as the very last
action of a successful run - `autograde/<slug>/_graded.json` - NOT the mere existence of the
`autograde/<slug>/` directory: an unchecked archive write used to create that directory
first, so an aborted run left the marker present over unwritten scores and un-graded everyone.
While no sentinel exists the assignment has never been machine-graded, and once one exists it
is never graded again automatically. A DECISION not to grade (no `solution` branch,
`autograde: false`, nothing gradable) writes the sentinel's sibling `<slug>/_skipped.json`
instead, saying why - because a skip that leaves the directory empty is re-decided, at the
cost of a template clone, every hour for ever. `has_autograde_results` tests for either
record, never bare directory existence, so a stray early write into the directory can no
longer be mistaken for a completed grade. To re-grade deliberately, delete
`autograde/<slug>/` and let the next tick regrade.

grading_config.yml (on the template's solution branch):
    type: individual        # or group
    autograde: false        # the default; true -> run the hidden tests at the cutoff
    tests: tests            # path on the solution branch holding the hidden tests
    completion_check: true  # default for `format: ipynb` -> execute the pinned notebook
                            # at the cutoff and record whether it runs top to bottom

The two are INDEPENDENT. A hand-marked notebook assignment (`autograde: false`) still gets
its completion check, which is the common case: the "restart the kernel and run all" rule
is stated by courses that mark by hand, and it is the one rule nobody could verify.

ANOTHER LANGUAGE.  The hidden tests are pytest by default and need not be: a `run.sh` in the
tests directory is run INSTEAD, in the same sandbox and under the same wall clock, and
whatever JUnit XML it leaves at `$DSL_JUNIT_OUT` is the score. That is how an R course grades
with `testthat` (see docs/10) without this module learning a word of R.

Usage:
    python3 -m dsl_course.collect \\
        --master-org COURSE --course-source-repo assignment-1-f2026 \\
        --cohort-org COHORT --deadline 2026-10-15 [--group] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
import resource
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import cache
from pathlib import Path

from . import course, grades, roster, schedule, sync_teams, teams
from .course import (
    CONFIG_REPO,
    SANDBOX_USER,
    SOLUTION_BRANCH,
    resolve_is_group,
    submission_repo,
)
from .derive import DeriveError, Filtered, filter_questions
from .discovery import list_org_repos
from .fs import copy_tree
from .gh_contents import (
    blob_sha,
    dump_csv,
    file_exists,
    get_file_content,
    get_file_with_sha,
    is_untouched_stub,
    put_file,
    repo_blob_shas,
)
from .ghcli import BOT_EMAIL, GIT_ENV, bot_login, clone, gh, git, is_missing_resource

# The assignment's definition and the sheet's spec live in `grades` (see the section
# there): `grades` may not import `collect`, and the sheet is its type. Re-exported
# under their old names so every caller and workflow keeps spelling them here.
from .grades import (
    GRADING_FILE,
    load_grading_spec,
    parse_grading_spec,  # noqa: F401 - re-exported; `collect` no longer parses it itself
    sheet_spec,
)
from .log import log, log_err, log_ok, log_person, log_skip, log_step
from .repos import default_branch, repo_missing

AUTOGRADE_DIR = "autograde"  # classroom-config/autograde/<slug>/<key>.json
GRADED_RECORD = "_graded.json"  # fire-once sentinel: a successful run's LAST write
SKIP_RECORD = "_skipped.json"  # the same marker, for an assignment nothing grades
SNAPSHOT_DIR = "snapshots"  # classroom-config/snapshots/<slug>.csv
SNAPSHOT_FIELDS = ("repo", "sha", "recorded_at", "submitted_at", "submitted_source")
# Where a row's `submitted_at` came from - recorded in a file that is never rewritten, so
# a marker (and the code) can always tell a server-observed time from a claimed one.
#
# `push` is GitHub's own record of when the commit ARRIVED, read from the
# repository-activity API at the freeze. It is the only rung nobody can write: a committer
# date is `GIT_COMMITTER_DATE` and therefore the student's, so late work backdated past
# the deadline used to be pinned with `days_late: 0` and nothing anywhere said otherwise.
SUBMITTED_SOURCE_PUSH = "push"
# The committer date the commits API reports - the fallback, when GitHub has no push
# record that can be matched to this commit. Client-supplied, and the sheet says so.
SUBMITTED_SOURCE_COMMIT = "commit"
# The same committer date, plus a contradiction: GitHub's own `pushed_at` for the repo is
# LATER than the due moment while the commit claims to predate it.
SUBMITTED_SOURCE_SUSPECT = "suspect"
SUSPECT_NOTE = "commit dated before the push that delivered it - check"
COMMIT_ONLY_NOTE = (
    "no push record matched this commit - the time shown is its committer date, "
    "which the student sets"
)
# What `info.submitted_note` says for each source. A `push` row has nothing to note: the
# time on it is the server's. Read at the freeze off the snapshot, which is the record.
SUBMITTED_NOTES = {
    SUBMITTED_SOURCE_SUSPECT: SUSPECT_NOTE,
    SUBMITTED_SOURCE_COMMIT: COMMIT_ONLY_NOTE,
}
RUN_TIMEOUT = 300  # wall-clock seconds per graded subprocess
# The escape hatch out of pytest. A `run.sh` at the top of the hidden tests is run INSTEAD
# of pytest, with the same rlimits, the same token-free environment, the same wall clock and
# the same cwd - so a course whose language is not Python grades through the same sandbox
# rather than through a second one nobody maintains.
#
# THE CONTRACT, and it is short on purpose:
#   * run with `sh`, not executed - the `+x` bit does not survive every checkout, and the
#     name says which interpreter;
#   * `$DSL_JUNIT_OUT` is an absolute path the script MUST write a JUnit XML to. That XML is
#     the result: passed/total out of it become `info.autograde`, exactly as pytest's would;
#   * `$DSL_SUBMISSION_DIR` is the absolute path of the submitted checkout. Python tests do
#     not need it (a startup hook puts the submission on sys.path); everything else does;
#   * the EXIT CODE is ignored. A suite with failures exits non-zero and is still a perfectly
#     good result - "the tests ran and three of them failed" is the answer, not an error.
#     No XML at all is the failure, and it records the usual grading-failed zero;
#   * cwd is the runspace, not the checkout - the tests and the report live outside anything
#     the student wrote, which is what the pytest path buys and this must not give away.
RUN_SCRIPT = "run.sh"
JUNIT_OUT_ENV = "DSL_JUNIT_OUT"
SUBMISSION_ENV = "DSL_SUBMISSION_DIR"
# The note on the one zero that means "the RUNNER broke", not "the student didn't submit".
# `collect` keys its systemic-failure guard on it, so it is a constant, not a loose string.
GRADE_FAILED_NOTE = "grading failed to run"

# POSIX resource caps applied (via `_apply_rlimits`) to every subprocess that runs student
# code, so one hostile submission can't take the whole grading job down with it. Module-level
# and overridable so a test can dial one down to a tiny value. Each is lowered defensively
# (never raised above the inherited hard cap, and a platform that refuses one just skips it),
# so these are ceilings on the grading host, not guarantees on every dev box.
# Heap (data segment): caps an allocate-until-OOM memory bomb (2 GiB, generous for legitimate
# numpy/pandas submissions) before it can OOM-kill the runner. Deliberately NOT RLIMIT_AS:
# virtual address space counts glibc malloc arenas and BLAS thread stacks, so an honest
# small-RSS scientific process on a many-core host blows a 2 GiB VSZ cap and dies before pytest
# can write junit - zeroing the whole cohort behind the sentinel, and unreproducible on macOS
# (which ignores RLIMIT_AS). The heap is what a memory bomb actually allocates.
RLIMIT_DATA_BYTES = 2 * 1024**3
# CPU-seconds (per process, summed across threads): a backstop should the wall-clock
# group-kill be evaded; > wall-clock so it never preempts a legit run.
RLIMIT_CPU_SECONDS = RUN_TIMEOUT * 2
# Processes for the real uid: caps a fork bomb. Counts the WHOLE user's processes, so it sits
# well above a normal run yet far below system exhaustion.
RLIMIT_NPROC_MAX = 2048
# Bytes per file: caps a disk-fill bomb (512 MiB).
RLIMIT_FSIZE_BYTES = 512 * 1024**2

# Belt-and-suspenders removal of files a student could commit to hijack their own grading:
# a pre-baked report, pytest plugin/config-by-name, or an interpreter site-hook. The PRIMARY
# defence is the runspace boundary in `_run_tests` (tests + report live outside the checkout,
# pytest's rootdir/confcutdir are the runspace not the checkout, the checkout is off sys.path
# at startup), which already makes these inert; stripping them too is a second line against a
# boundary regression. Deliberately NOT here: `pyproject.toml`/`setup.cfg`/`tox.ini` - they are
# never read as pytest config from the checkout (the rootdir is the runspace) and a legitimate
# package submission needs them to import. Matched ANYWHERE in the checkout, not just its root.
#
# SCOPE (be honest about it): this closes STATIC rigging - artefacts the student COMMITTED.
# It does NOT stop the student's own code, once imported in-process by the hidden tests, from
# rewriting the junit report pytest wrote (an `atexit` handler, or `os._exit` after a forged
# write) to fake all-pass. That in-process forgery is a KNOWN, ACCEPTED residual, not a hole
# these strips or the boundary claim to cover: it is tolerated because an autograde score is
# never a standalone verdict - faculty add manual marks and review before the grades pipeline
# distributes anything, and a student never sees the machine score in their own repo. If that
# ever changes, the fix is a trusted out-of-band result channel (a streaming pytest plugin
# that reports each outcome to the faculty process as it runs, so the final report can't be
# retroactively rewritten) - it would live alongside the junit read in `_run_tests`.
_STUDENT_TEST_RIGGING = (
    "report.xml",
    "conftest.py",
    "sitecustomize.py",
    "usercustomize.py",
    "pytest.ini",
    "pytest.py",
)

# --------------------------------------------------------------------------- pure core


def score_from_junit(xml_text: str) -> dict:
    """Turn a JUnit XML report into the result.json contract {score, max, tests}.

    A case passes only if it has neither failure, error, nor skipped child element.

    EVERY suite in the file, not the first: pytest writes one, but testthat's
    `JunitReporter` writes one per test file, and taking the first would have scored an R
    course out of however many cases happened to be in its alphabetically-first file."""
    root = ET.fromstring(xml_text)
    suites = (
        [root] if root.tag == "testsuite" else (root.findall("testsuite") or [root])
    )
    cases = [
        {
            "name": tc.get("name"),
            "passed": tc.find("failure") is None
            and tc.find("error") is None
            and tc.find("skipped") is None,
        }
        for suite in suites
        for tc in suite.findall("testcase")
    ]
    return {
        "score": sum(1 for c in cases if c["passed"]),
        "max": len(cases),
        "tests": cases,
    }


# Salted per RUN. Without the salt the tag is sha1("<slug>-<handle>") and both halves are
# public (the slug on the cohort site, the handle in the welcome repo's Join issue titles),
# so anyone could recompute the tag and read the student back off the log.
_REF_SALT = secrets.token_hex(8)


def target_ref(repo: str) -> str:
    """A short tag standing in for a submission repo in the run log - stable within a run,
    unrecoverable from outside it.

    The log is PUBLIC (every workflow runs in the course org's public `.github`) and a
    submission repo is named `<slug>-<handle>`, so naming it beside a score or a
    non-submission publishes a student's result. The private per-target archive under
    autograde/<slug>/ records the tag next to the repo, which is where a marker looks it up."""
    return "#" + hashlib.sha1((_REF_SALT + repo).encode()).hexdigest()[:7]


def _zero_result(note: str) -> dict:
    """A zero score carrying an explanatory note (non-submission / grading failure).

    `max` is 0 rather than the assignment's test count: no test was collected, let alone
    run, so there is no denominator to report and the note is what a marker reads."""
    return {"score": 0, "max": 0, "tests": [], "note": note}


def snapshot_path(slug: str) -> str:
    """Where this assignment's deadline snapshot lives in `classroom-config`."""
    return f"{SNAPSHOT_DIR}/{slug}.csv"


def autograde_path(slug: str) -> str:
    """Where this assignment's per-target result archive lives in `classroom-config`."""
    return f"{AUTOGRADE_DIR}/{slug}"


def dump_snapshots(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Serialise (repo, sha, recorded_at, submitted_at, submitted_source) rows to snapshot
    CSV text, repo-sorted so the file is stable and diffable."""
    return dump_csv(SNAPSHOT_FIELDS, sorted(rows))


@dataclass(frozen=True)
class SnapshotRow:
    """One repo's row in a frozen snapshot. Blank fields are records, not gaps: a blank
    `sha` says "nothing had been pushed by the deadline", and blank submission fields say
    the same, or that this row predates them."""

    repo: str
    sha: str = ""
    recorded_at: str = ""
    submitted_at: str = ""
    submitted_source: str = ""


def parse_snapshot_rows(text: str) -> dict[str, SnapshotRow]:
    """Parse snapshot CSV text into {repo: SnapshotRow} - every recorded column, for the
    callers that want the submission time as well as the pin.

    Keyed by NAME, not position: a snapshot frozen before `submitted_at` and
    `submitted_source` were recorded has three columns, and the file is write-once, so it
    is never backfilled. It must therefore still parse - with those two blank - rather than
    strand the cohort that owns it.

    A bare DictReader, not gh_contents.read_csv: `dump_snapshots` above wrote this file,
    so it has no BOM and no `;` delimiter to guard against."""
    rows: dict[str, SnapshotRow] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if not (repo := (row.get("repo") or "").strip()):
            continue
        rows[repo] = SnapshotRow(
            repo=repo,
            sha=(row.get("sha") or "").strip(),
            recorded_at=(row.get("recorded_at") or "").strip(),
            submitted_at=(row.get("submitted_at") or "").strip(),
            submitted_source=(row.get("submitted_source") or "").strip(),
        )
    return rows


def parse_snapshots(text: str) -> dict[str, str]:
    """Parse snapshot CSV text into {repo: sha} - the pin, which is all grading needs. A
    blank sha is meaningful - it records "nothing had been pushed to this repo by the
    deadline" - so it is kept, not dropped."""
    return {repo: row.sha for repo, row in parse_snapshot_rows(text).items()}


# ---------------------------------------------------------------------- gh/git wiring


# Everything the CI harness put in the environment, gone before student code sees it.
#
# A token in scope is the obvious half (`GH_*`, `GITHUB_TOKEN`, the `DSL_*` the job
# carries). The subtler half is the ACTIONS FILE COMMANDS: the runner hands every step a
# set of writable paths - `$GITHUB_ENV`, `$GITHUB_PATH`, `$GITHUB_OUTPUT`, `$GITHUB_STATE`,
# `$GITHUB_STEP_SUMMARY` - and executes what it finds in them when the step ends. A
# notebook cell appending `BASH_ENV=/tmp/x` to `$GITHUB_ENV` therefore runs in the NEXT
# step of the same job, with that step's secrets in its environment. `RUNNER_*` goes with
# them because the same files live under `$RUNNER_TEMP/_runner_file_commands/`, so leaving
# that one behind hands back the directory the handles pointed into, and `ACTIONS_*`
# carries the runtime token the cache/artifact services authenticate with.
#
# This is the SECOND lock, not the first: what actually closes the escalation is that no
# step carrying a secret ever follows the student-code step in its job (each is the last
# step of its own job - see workflows_render.render_scheduled_release and the renderer
# test that holds it there). Prefixes rather than a name list, so a variable the runner
# adds in a future version is dropped by default rather than by amendment.
_SANDBOX_DROP_PREFIXES = ("ACTIONS_", "DSL_", "GH_", "GITHUB_", "RUNNER_")


def _sanitised_env() -> dict:
    """The environment EVERY graded subprocess runs in - the one place the two rules that
    never vary are written down.

    Nothing of the job it runs inside: no GitHub token, and no handle back into the runner
    (see `_SANDBOX_DROP_PREFIXES`). And `PYTHONSAFEPATH`, because all three of these run
    `python -m` somewhere the student can write, which would otherwise put that directory
    on `sys.path[0]` and let a committed `json.py` / `nbformat.py` be imported before the
    real one. A fourth subprocess site added later inherits both by construction rather
    than by reading a comment.

    What DOES vary is layered on by the caller: the hidden tests add their runspace
    PYTHONPATH and the two `DSL_*` variables `run.sh` reads (after this, deliberately), and
    the completion check takes the network away and gives the notebook's own directory back
    to the kernel (`_completion_env`)."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_SANDBOX_DROP_PREFIXES)
    }
    env["PYTHONSAFEPATH"] = "1"
    # Caps glibc arena proliferation, which would otherwise reserve a heap arena per core and
    # push a legitimate multi-threaded run past RLIMIT_DATA_BYTES.
    env["MALLOC_ARENA_MAX"] = "2"
    return env


def one_per_unit(
    targets: list[tuple[str, str, list[str]]],
) -> list[tuple[str, str, list[str]]]:
    """`targets` with each unit key appearing ONCE, the first row kept.

    A submission unit is a repo and a block on the grading sheet, and both are named after
    the key: two rows carrying the same key are one unit, however they got there. It
    happens - two roster rows sharing a `github_handle` (the same person entered twice, or
    a handle pasted into the wrong row), a team listed twice in teams.csv.

    Unfiltered, the duplicate is not merely wasted work. `grades.merge_sheet` walks the
    units in order, popping each one out of the sheet it read: the first pass merges the
    block, the second finds the key already popped and builds a BRAND NEW one - so a tick
    that derived nothing for that unit (a repo quiet since the last look, which is the
    normal case) replaced a filled-in `info:` with blanks, losing `submitted`, `days_late`
    and `checked` together. Losing `checked` is what made it flip: the next tick saw a unit
    nobody had ever looked at, re-read it, filled `info:` back in - and the tick after that
    blanked it again. The count was wrong with it ("0 of 2 students" for one student), and
    the repo was read and the receipt derived twice on every tick."""
    seen: dict[str, tuple[str, str, list[str]]] = {}
    for repo, unit, members in targets:
        seen.setdefault(unit, (repo, unit, members))
    if len(seen) != len(targets):
        # A count in the public log; the keys themselves only where they are safe. This is
        # a faculty-fixable data error in a hand-edited CSV, not a toolkit failure.
        log_err(
            f"  ! {len(targets) - len(seen)} duplicate submission unit(s) in "
            f"{CONFIG_REPO} - each one is graded once; fix the duplicated row"
        )
        log_person("     duplicated: " + ", ".join(sorted(_repeated(targets))))
    return list(seen.values())


def _repeated(targets: list[tuple[str, str, list[str]]]) -> set[str]:
    """The unit keys that appear more than once in `targets`."""
    counted: dict[str, int] = {}
    for _repo, unit, _members in targets:
        counted[unit] = counted.get(unit, 0) + 1
    return {unit for unit, n in counted.items() if n > 1}


def submission_targets(
    cohort_org: str, slug: str, is_group: bool, teams_key: str | None = None
) -> list[tuple[str, str, list[str]]]:
    """The submission units for `slug` as (repo, key, members): one per team for a group
    assignment, one per onboarded student otherwise, each key ONCE (`one_per_unit`).
    Empty - with the reason logged - when there is nothing to grade.

    `slug` is the cohort-side NAME (`schedule.cohort_name`), which is what every repo here
    is named after. `teams_key` is the SCHEDULE KEY, which is what teams.csv is keyed on -
    the Join-team form validates the assignment against `assignments:` in schedule.yml and
    writes that key. The two differ whenever `cohort_dest_repo` is set, and looking teams
    up by the name then found none, so a group assignment silently had no targets at all.
    Defaults to `slug` for the (usual) case where they are the same.

    `is_group` is decided upstream by `resolve_is_group` (force -> grading_config.yml)
    and passed in; it is NEVER inferred from teams.csv here. teams.csv is student-writable (a
    "Join team" issue can add a row against an individual assignment), so trusting its rows to
    decide the assignment's KIND would let a student turn an individual assignment into a group
    one - it is read only to enumerate a KNOWN-group assignment's teams."""
    if is_group:
        key = teams_key or slug
        groups = teams.teams_for(teams.load(cohort_org), key)
        if not groups:
            log_err(f"no teams for `{key}` in {cohort_org}/{CONFIG_REPO}/teams.csv.")
            return []
        # teams.csv is student-writable (the welcome "Join team" issue appends rows), so its
        # handles pass the SAME roster allowlist `assign.provision_all` vets them through
        # before they are handed out - `sync_teams.vet_groups` is that one allowlist.
        # Unvetted, a typo'd or invented handle earned a block of its OWN in the grading
        # sheet - the file faculty mark in, and `distribute` fans out from - for an account
        # with no place in the cohort at all.
        out = []
        for team, vetted, rejected in sync_teams.vet_groups(
            groups, roster.enrolled(roster.load(cohort_org) or [])
        ):
            if rejected:
                # A count, not the handles: this log is public, and the handles are a
                # student's own typing.
                log_err(
                    f"  ! {len(rejected)} handle(s) in teams.csv for `{key}` are not "
                    f"enrolled, onboarded roster handles - they get no grade row"
                )
            out.append((submission_repo(slug, team), team, vetted))
        return one_per_unit(out)
    # Enrolled participants only, matching assign/grades: an auditor deliberately has no
    # submission repo, so listing one makes it an unclonable phantom target (noise, and a
    # spurious "could not be read"). `roster.enrolled` drops auditors; `onboarded` drops
    # the not-yet-joined.
    targets = [
        (submission_repo(slug, s.github_handle), s.github_handle, [s.github_handle])
        for s in roster.enrolled(roster.load(cohort_org) or [])
        if s.onboarded
    ]
    if not targets:
        log_err(f"no onboarded enrolled students in {cohort_org} to grade.")
    return one_per_unit(targets)


def local_deadline(deadline: str, tz: str | None = None) -> datetime:
    """`deadline` (ISO date or datetime) as an OFFSET-CARRYING datetime in the COHORT's own
    timezone. Raises ValueError on anything that is not ISO.

    A bare date means the END of that day, and a naive datetime is a local time, because
    the deadline a student was given ("submit by the 15th") is a local one - the site shows
    it in the cohort's zone and schedule.yml declares that zone. Read as UTC, as it was,
    "the 15th" ran until 01:59 on the 16th in Berlin summer time: two hours of late work
    graded as on time, and the snapshot froze at the wrong instant to match.

    `tz` is the schedule's `timezone` (`schedule._tz` supplies the default and tolerates an
    unknown zone). A deadline that already carries an offset names an instant and is only
    re-expressed, never moved."""
    raw = deadline if ("T" in deadline or ":" in deadline) else f"{deadline}T23:59:59"
    dt = datetime.fromisoformat(raw)
    zone = schedule._tz(tz)
    return dt.replace(tzinfo=zone) if dt.tzinfo is None else dt


def _until_param(deadline: str, tz: str | None = None) -> str:
    """`deadline` as a UTC `...Z` stamp - the form the commits API's `until=` takes."""
    return (
        local_deadline(deadline, tz)
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


# The commit facts the freeze needs, in one read: the sha and its committer date, plus
# what tells the toolkit's own handout commit from a student's push - how many parents it
# has, and who GitHub says wrote it. Tab-joined because a git author's name and email are
# free text and a space is not a separator in them.
_COMMIT_FIELDS = (
    '[(.[0].sha // ""), (.[0].commit.committer.date // ""), '
    '((.[0].parents // [] | length) | tostring), (.[0].author.login // ""), '
    '(.[0].commit.author.email // "")] | join("\t")'
)


def _commit_facts(out: str) -> tuple[str, str, int | None, str, str]:
    """`_COMMIT_FIELDS` parsed back: (sha, committer date, parent count, login, email).

    The parent count is None when the answer did not carry one - an older shape, or a
    truncated read. None means "cannot tell", and every caller must read it that way."""
    parts = out.strip().split("\t")
    parts += [""] * (5 - len(parts))
    sha, committed, parents, author, email = parts[:5]
    return sha, committed, int(parents) if parents.isdigit() else None, author, email


def _is_handout_commit(parents: int | None, author: str, email: str) -> bool:
    """Whether this commit is the repo's first AND the toolkit's own.

    Both halves are required. A ROOT commit that a student made is a real submission (they
    force-pushed over the handout, which is theirs to do), and a bot commit further along
    the history is not the handout. `bot_login()` answering "" means the identity could not
    be read, so nothing is claimed - a transient must never turn a student's work into "no
    submission recorded"."""
    if parents != 0:
        return False
    if email and email == BOT_EMAIL:
        return True
    return bool(author) and author == bot_login()


@dataclass(frozen=True)
class Pin:
    """What the freeze found for one submission repo: the sha to grade, that commit's
    committer date, and whether the repo was ABSENT (404).

    `absent` is a field rather than another spelling of a blank sha because the two mean
    opposite things to `snapshot_assignment`: a reachable-but-empty repo is a real "nobody
    submitted" and IS frozen, while an absent one may still be provisioned and must not
    be."""

    sha: str = ""
    committed: str = ""
    absent: bool = False


def _snapshot_sha(
    cohort_org: str, repo: str, deadline: str, recorded_at: str = ""
) -> Pin | None:
    """The commit to freeze `repo` at: its last commit on or before `deadline`, read from
    the API (no clone - this runs for every repo of every assignment, hourly).

    "On or before" is judged on the COMMITTER DATE, which the student supplies
    (`GIT_COMMITTER_DATE`). Freezing does not change that - it only stops the pin moving
    afterwards. `recorded_at` is the moment of this freeze: a chosen commit dated after it
    cannot have existed when we looked, so it is a skewed or doctored clock and is logged.

    Returns a bare `Pin()` when there is nothing to grade - no commit that early, or an
    empty repo - and `Pin(absent=True)` when there is no such repo at all (an on-time
    submission cannot live in a repo that does not exist).
    Returns None when the API call itself failed - the caller then abandons the whole
    snapshot so the next cron tick retries, rather than baking a transient error into a
    record that is never rewritten."""
    code, out = gh(
        "api",
        "-X",
        "GET",
        f"repos/{cohort_org}/{repo}/commits",
        "-f",
        f"until={_until_param(deadline)}",
        "-f",
        "per_page=1",
        "--jq",
        _COMMIT_FIELDS,
    )
    if code == 0:
        sha, committed, parents, author, email = _commit_facts(out)
        if not sha:
            _warn_if_late_commits_only(cohort_org, repo, deadline)
            return Pin()  # the repo is reachable; no commit on/before the deadline
        if _is_handout_commit(parents, author, email):
            # The repo's FIRST commit, made by the toolkit: `/generate` copies the template
            # into every student repo, so a student who never pushed still has a commit
            # dated at the handout. Pinning it recorded them as having submitted, on time,
            # and posted them a receipt saying so.
            return Pin()
        if _committed_after(committed, recorded_at):
            # Tag, never the handle: this log is public.
            log(
                f"  [warn] {target_ref(repo)} is pinned to a commit dated after this "
                f"freeze was taken ({committed} > {recorded_at}) - a committer date is "
                f"client-supplied, so check for a skewed clock before marking"
            )
        return Pin(sha, committed)
    # A 409 is an EMPTY repo: it exists but has no commits, so "" is a real recorded
    # non-submission (we freeze it, closing the backdating window for it).
    if "HTTP 409" in out:
        return Pin()
    # A 404 means the repo ISN'T THERE (not generated yet, a handout typo, or a private-repo
    # blip). That is NOT the same as an existing-but-empty repo: an absent repo may still be
    # provisioned, so if EVERY target is absent the caller skips the freeze rather than pinning
    # "nobody submitted" for ever. A precise marker match, not a loose `"empty" in out`.
    if is_missing_resource(out):
        return Pin(absent=True)
    log_err(f"  ! could not read commits for {target_ref(repo)}: {out[:160]}")
    return None


def _parse_iso(stamp: str) -> datetime | None:
    """An API timestamp as an aware datetime, or None if it is not one.

    GitHub answers `...Z` and `datetime.fromisoformat` only learnt to read that in 3.11,
    so the suffix is spelt out rather than left to the runner's Python version - the
    difference between a warning that fires and one that never does. A naive stamp is read
    as UTC, which is what the API means by one."""
    if not stamp:
        return None
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _committed_after(committed: str, recorded_at: str) -> bool:
    """Whether `committed` (a commit's committer date) is later than `recorded_at` (the
    moment the freeze was taken). Both ISO; either missing or unparseable means no."""
    when, taken = _parse_iso(committed), _parse_iso(recorded_at)
    return when is not None and taken is not None and when > taken


def delivery_is_suspect(
    committed: str, pushed_at: str, moment: datetime | None
) -> bool:
    """Whether the SERVER says this commit arrived after `moment` while the commit itself
    claims to predate it.

    The pin is chosen on the committer date, which is `GIT_COMMITTER_DATE` and therefore
    the student's to set: a late push backdated before the due date is pinned with
    `days_late: 0` and, until this, nothing anywhere said otherwise. `pushed_at` from the
    org listing is the cheap server-side second opinion - it is the repo's LAST push, so it
    only ever accuses a repo that really did receive something late.

    Anything missing means no accusation: a listing without the field, an unparseable date,
    or no moment to compare against."""
    if not (committed and pushed_at and moment):
        return False
    made, delivered = _parse_iso(committed), _parse_iso(pushed_at)
    if made is None or delivered is None:
        return False
    return made <= moment < delivered


# The two fields a push record answers with: the HEAD the push left behind, and when
# GitHub observed it. Tab-joined, like `_COMMIT_FIELDS`, and filtered to the activity
# types that actually deliver commits - a branch creation or a repo merge says nothing
# about when a student's work arrived.
_ACTIVITY_FIELDS = (
    '.[] | select(.activity_type == "push" or .activity_type == "force_push") | '
    '[(.after // ""), (.timestamp // "")] | join("\t")'
)


def _push_activity(cohort_org: str, repo: str) -> list[tuple[str, str]] | None:
    """This repo's push records - `(the HEAD after the push, when GitHub saw it)` - or
    None if the question could not be answered.

    The repository-activity API, and not `pushed_at` (which moves with `push_solution` and
    only ever describes the LAST push) and not the events API (90-day retention, and it
    drops old events from a busy repo). Read ONCE per repo, at the write-once freeze, so
    the cost is one call per submission and never a per-tick one."""
    code, out = gh(
        "api",
        "-X",
        "GET",
        f"repos/{cohort_org}/{repo}/activity",
        "-f",
        "per_page=100",
        "--jq",
        _ACTIVITY_FIELDS,
    )
    if code != 0:
        # A repo that is not there has no pushes, and neither has one whose activity GitHub
        # will not serve at all - both are answers. Anything else (a rate limit, a 5xx) is
        # "could not tell", and the caller abandons the freeze over it: the snapshot is
        # never rewritten, so recording the student's own committer date on the strength of
        # a read that failed would fix a wrong submission time - and the late penalty that
        # follows from it - for good. A retry costs a tick.
        return [] if is_missing_resource(out) else None
    rows = []
    for line in out.splitlines():
        after, _, stamp = line.partition("\t")
        if stamp.strip():
            rows.append((after.strip(), stamp.strip()))
    return rows


def push_time_for(activity: list[tuple[str, str]], sha: str, committed: str) -> str:
    """When GitHub says the pinned commit ARRIVED, from this repo's push records, or "".

    Two rungs. The push whose resulting HEAD *is* the pin is the push that delivered it,
    exactly. Failing that - the pin is not the tip of any push, which is what pushing two
    commits at once looks like - the EARLIEST push at or after the moment the commit
    claims to have been made is the earliest one that could have carried it. That second
    rung is deliberately generous to the student: it can only ever name a push at or after
    their own claimed time, so it never invents lateness, and a backdated commit is still
    timed by a real push rather than by the date typed into it.

    "" means neither rung answered, and the caller falls back to the committer date."""
    for after, stamp in activity:
        if after and after == sha:
            return stamp
    made = _parse_iso(committed)
    if made is None:
        return ""
    later = [
        (when, stamp)
        for _after, stamp in activity
        if (when := _parse_iso(stamp)) is not None and when >= made
    ]
    return min(later)[1] if later else ""


def _submitted(
    cohort_org: str,
    repo: str,
    pin: Pin,
    pushed_at: str,
    moment: datetime | None,
) -> tuple[str, str] | None:
    """`(submitted_at, submitted_source)` for one pinned commit - the ladder in full, or
    None when GitHub's push records could not be READ.

    Recorded now or never: the snapshot is write-once, and this is the only pass that
    reads GitHub's push records. Everything downstream - `days_late`, the penalty, the
    receipt a student reads - rests on which rung answered, which is why the row carries
    the rung as well as the time.

    None is kept distinct from "no push record matched" for exactly that reason: falling
    to the committer date is a permanent decision, and taking it on a read that failed
    would time a backdated submission by the date the student typed into it. The caller
    abandons the snapshot instead and the next tick takes it - the same answer
    `_snapshot_sha` gives an unreadable commits read."""
    activity = _push_activity(cohort_org, repo)
    if activity is None:
        return None
    server = push_time_for(activity, pin.sha, pin.committed)
    if server:
        return server, SUBMITTED_SOURCE_PUSH
    # Nothing GitHub timed. The committer date is a CLAIM, so it is warned about here and
    # noted in the sheet - a grader deciding a late penalty has to see which it is.
    if delivery_is_suspect(pin.committed, pushed_at, moment):
        # Tag, never the handle: this log is public.
        log(
            f"  [warn] {target_ref(repo)} is pinned to a commit dated before the push "
            f"that delivered it - a committer date is client-supplied, so check it "
            f"before marking"
        )
        return pin.committed, SUBMITTED_SOURCE_SUSPECT
    log(
        f"  [warn] {target_ref(repo)}: no push record matched the pinned commit - "
        f"recording its committer date, which the student sets"
    )
    return pin.committed, SUBMITTED_SOURCE_COMMIT


def _warn_if_late_commits_only(cohort_org: str, repo: str, deadline: str) -> None:
    """When a reachable repo yielded no commit on/before the deadline, tell an empty repo
    apart from one that HAS commits, all dated after the cutoff. The snapshot filters on the
    committer date (`until=`), so a student whose clock is skewed past the deadline looks
    identical to a non-submitter; log the difference so faculty can spot a skew that would
    otherwise score an on-time student zero. Best-effort: a failed probe just stays quiet."""
    code, out = gh(
        "api",
        "-X",
        "GET",
        f"repos/{cohort_org}/{repo}/commits",
        "-f",
        "per_page=1",
        "--jq",
        '.[0].sha // ""',
    )
    if code == 0 and out.strip():
        log(
            f"    ({target_ref(repo)} has commit(s), but none on/before {deadline} - an empty freeze "
            f"here can also be a client clock skewed past the deadline; check if unexpected)"
        )


def has_autograde_results(cohort_org: str, slug: str) -> bool:
    """Whether `slug` carries the autograder's FIRE-ONCE marker in classroom-config: the
    `_graded.json` sentinel of a completed run, or the `_skipped.json` record of a decision
    not to grade. NOT bare `autograde/<slug>/` existence - an aborted run can leave that
    directory populated with archives but no sentinel, and it must then still regrade.

    The scheduler grades an assignment only while neither record is present, so a machine score
    is written once and never silently refreshed under a marker's hand-edits. A deliberate
    re-grade means deleting `autograde/<slug>/` (the next tick then regrades) or running the
    autograder."""
    return any(
        file_exists(cohort_org, CONFIG_REPO, f"{autograde_path(slug)}/{record}")
        for record in (GRADED_RECORD, SKIP_RECORD)
    )


def mark_not_autograded(cohort_org: str, slug: str, why: str) -> bool:
    """Record that this assignment will never be machine-graded, and why.

    The `_skipped.json` record is one of the two fire-once markers (see
    `has_autograde_results`), so a skip that leaves it absent is not a skip at all: the
    scheduler re-clones the template and re-decides the same skip on every hourly tick, for
    ever. The note is what tells a marker reading the archive that the empty result set was
    deliberate."""
    return put_file(
        cohort_org,
        CONFIG_REPO,
        f"{autograde_path(slug)}/{SKIP_RECORD}",
        json.dumps(
            {
                "skipped": why,
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ).encode(),
        f"autograde: {slug} not machine-graded ({why})",
    )


def _record_skip(cohort_org: str, slug: str, reason: str, dry_run: bool) -> int:
    """Record `slug`'s "not machine-graded" marker, and return the exit code for it.

    A skip DECIDED but not RECORDED is not a skip: `has_autograde_results` reads the
    marker, so without it the next hourly tick re-clones the template and re-decides the
    identical skip, for ever - which ran live in the demo cohort for days. A failed write
    is therefore red; a dry run writes nothing and is green."""
    if dry_run:
        return 0
    if mark_not_autograded(cohort_org, slug, reason):
        return 0
    log_err(f"{slug}: could not record the skip - the next run re-decides it")
    return 1


def mark_graded(cohort_org: str, slug: str) -> bool:
    """Write the fire-once sentinel `autograde/<slug>/_graded.json` - the LAST action of a
    fully successful run, once every per-target archive is durably written.

    Making the marker an EXPLICIT file (rather than the mere existence of `autograde/<slug>/`,
    which the first archive `put_file` created as a side effect) decouples "this assignment is
    graded" from any single archive write: a future early write into the directory can no
    longer be mistaken for a completed grade, and a run that fails part-way through the archives
    withholds this sentinel and so stays eligible for a retry."""
    return put_file(
        cohort_org,
        CONFIG_REPO,
        f"{autograde_path(slug)}/{GRADED_RECORD}",
        json.dumps(
            {
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            indent=2,
        ).encode(),
        f"autograde: {slug} machine-graded",
    )


def load_snapshots(cohort_org: str, slug: str) -> dict[str, str] | None:
    """{repo: sha} from this assignment's snapshot CSV, or None if no snapshot was ever
    taken (the two are different: a recorded blank sha means "no submission", while no
    file at all means grading has to fall back to client-supplied commit dates)."""
    content = get_file_content(cohort_org, CONFIG_REPO, snapshot_path(slug))
    return parse_snapshots(content) if content is not None else None


def load_snapshot_rows(cohort_org: str, slug: str) -> dict[str, SnapshotRow] | None:
    """The frozen snapshot in full - the pin AND when it was submitted - or None if no
    snapshot was ever taken. What the cutoff's grading-sheet write reads."""
    content = get_file_content(cohort_org, CONFIG_REPO, snapshot_path(slug))
    return parse_snapshot_rows(content) if content is not None else None


class SnapshotResult(Enum):
    """What `snapshot_assignment` did. Only WRITTEN and PRESENT mean a snapshot exists, so
    only they make an assignment eligible to be graded."""

    WRITTEN = "written"  # frozen by this call
    PRESENT = "present"  # already frozen by an earlier call
    NOTHING_TO_FREEZE = "nothing"  # no targets, or every target absent - wait and retry
    FAILED = "failed"  # a lookup or the write failed - retry on the next tick


def snapshot_assignment(
    cohort_org: str,
    slug: str,
    deadline: str,
    *,
    is_group: bool,
    teams_key: str | None = None,
    tz: str | None = None,
) -> SnapshotResult:
    """Freeze, at a server-chosen MOMENT, the commit each of `slug`'s submission repos will
    be graded at. Write-once: an existing snapshot is never re-taken or overwritten, so a
    late push can never move the pin. The `SnapshotResult` distinguishes a snapshot that now
    exists (WRITTEN/PRESENT) from one that was deliberately not taken (NOTHING_TO_FREEZE) and
    from a failure (FAILED); the caller must not treat the last two as frozen.

    The moment is ours; the CHOICE of commit is still made on the student-supplied
    committer date (see `_snapshot_sha` and the module docstring). What this closes is the
    unbounded backdating window, not the hour before the freeze.

    An assignment with no submission units yet is a no-op, not a failure: nothing is frozen
    and nothing is written, so a later handout still gets its own snapshot.

    `is_group` is REQUIRED (keyword-only): it decides which repos are frozen, so a silent
    default would let a forgetful future caller pin individual repos for a group assignment.
    The caller resolves it once, upstream, via `resolve_is_group` - it is never guessed here
    from student-writable teams.csv."""
    if load_snapshots(cohort_org, slug) is not None:
        log_skip(f"snapshot {snapshot_path(slug)}")
        return SnapshotResult.PRESENT
    targets = submission_targets(cohort_org, slug, is_group, teams_key)
    if not targets:
        # Nobody onboarded, or no teams for a group assignment - which is also what an
        # assignment not handed out yet looks like from here. The snapshot is write-once,
        # so freezing an empty one would pin the assignment to "nothing submitted" for
        # ever; write nothing and let a later tick take it. Green, because the alternative
        # is a red hourly run for every assignment whose cohort has yet to fill up.
        # `submission_targets` has already logged which of the two it was.
        log(
            f"  [skip] snapshot {snapshot_path(slug)} - nothing to freeze yet; "
            f"a later tick takes it"
        )
        return SnapshotResult.NOTHING_TO_FREEZE
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # One listing for the whole cohort, and the only reason to take it: `pushed_at` is the
    # server's word on when each repo last received anything, and the pin is chosen on a
    # date the student wrote. Best-effort - without it the rows simply carry `commit`, as
    # they always did.
    pushed = _pushed_at(cohort_org)
    moment = local_deadline(deadline, tz)
    rows: list[tuple[str, str, str, str, str]] = []
    any_present = False
    for repo, _key, _members in targets:
        pin = _snapshot_sha(cohort_org, repo, deadline, recorded_at)
        if pin is None:
            log_err(f"  ! abandoning the {slug} snapshot - will retry on the next run")
            return SnapshotResult.FAILED
        if not pin.absent:
            any_present = True  # a sha, or a reachable-but-empty repo that EXISTS
        if pin.sha:
            # WHEN the pinned commit was submitted, and WHERE that time came from: the
            # server's push record if GitHub has one, else the committer date, which the
            # student supplied and can backdate. The snapshot is write-once, so it is
            # recorded now or never.
            submitted = _submitted(cohort_org, repo, pin, pushed.get(repo, ""), moment)
            if submitted is None:
                log_err(
                    f"  ! could not read {target_ref(repo)}'s push records - abandoning "
                    f"the {slug} snapshot, will retry on the next run"
                )
                return SnapshotResult.FAILED
            rows.append((repo, pin.sha, recorded_at, *submitted))
        else:
            # Absent, or reachable with nothing pushed by the deadline. Either way there is
            # no submission, so there is no submission time: both cells stay blank.
            rows.append((repo, "", recorded_at, "", ""))
    if not any_present:
        # EVERY target repo is ABSENT (404): not generated yet, or a handout typo. This is
        # NOT the same as reachable-but-empty repos (a real "nobody submitted", which we DO
        # freeze as zeros to close the backdating window): an absent repo may still be
        # provisioned, and freezing the write-once snapshot now would pin the whole assignment
        # to "nobody submitted" for ever. Write nothing; a later tick, once the repos exist,
        # takes the real snapshot.
        log(
            f"  [skip] snapshot {snapshot_path(slug)} - every target repo is absent "
            f"(not generated yet); a later tick takes it"
        )
        return SnapshotResult.NOTHING_TO_FREEZE
    if not put_file(
        cohort_org,
        CONFIG_REPO,
        snapshot_path(slug),
        dump_snapshots(rows).encode(),
        f"snapshot: {slug} pinned commits as of {deadline}",
    ):
        return SnapshotResult.FAILED
    pinned = sum(1 for row in rows if row[1])
    log_ok(
        f"snapshot {snapshot_path(slug)}: {pinned}/{len(rows)} repo(s) with a commit "
        f"on/before {deadline}"
    )
    return SnapshotResult.WRITTEN


# ------------------------------------------------------------------- the grading sheet

# The sheet is created at handout, filled at the due date, refreshed through the late
# window and frozen at the cutoff. Only the toolkit's own `info:` moves; see
# `grades.merge_sheet` for what that guarantees the grader.
CONTRIBUTIONS_FILE = "CONTRIBUTIONS.md"


class SheetPhase(Enum):
    """Where in the assignment's life this write falls.

    OPEN and FROZEN differ in who owns `info:`; FREEZING is the single write that moves it
    from one to the other, and it still DERIVES (off the frozen snapshot) - it is the last
    derivation there will ever be."""

    OPEN = "open"  # before the cutoff: `info:` is re-derived on every write
    FREEZING = "freezing"  # the cutoff write: derive once more, then stop
    FROZEN = "frozen"  # after it: `info:` is copied verbatim, whatever we now think


def submitted_display(submitted_at: str, tz: str | None) -> str:
    """The pinned commit's time in the COHORT's own clock, to the minute -
    `2026-10-03T22:14+02:00`.

    To the MINUTE: seconds add nothing a grader reads a submission time for, and this is
    also the form the mock-up shows. It used to be load-bearing as well - with seconds the
    value matches YAML 1.1's timestamp pattern - but `grades._SheetLoader` now keeps every
    plain scalar in the sheet a string, so the type can no longer move under it."""
    at = _parse_iso(submitted_at)
    if at is None:
        return ""
    return at.astimezone(schedule._tz(tz)).isoformat(timespec="minutes")


def days_late(submitted: datetime, due: datetime, tz: str | None = None) -> int:
    """Whole days late, counted in the COHORT's own calendar and floored at 0.

    A day starts at local midnight, not 86400 seconds after the last one. The night the
    clocks go back is 25 hours long, so counting fixed blocks made a push at 23:59 the day
    after a late-October deadline read as TWO days late - a 10% deduction nobody could
    explain and the student could not have avoided. Days STARTED is the Hertie wording, and
    a day starts when the date does: the first local midnight past the deadline is day one.

    Measured from the MINUTE-truncated submission time, which is the value the receipt and
    the sheet both show. Without that, a push at 23:59:01 against a 23:59 deadline was
    displayed as `pushed 23:59` and charged a day - the two lines of the same receipt
    disagreeing with each other."""
    zone = schedule._tz(tz)
    local = submitted.astimezone(zone).replace(second=0, microsecond=0)
    deadline = due.astimezone(zone)
    if local <= deadline:
        return 0
    return (local.date() - deadline.date()).days


CONTRIBUTIONS_UNFILLED = "(not filled in)"


def _contributions(cohort_org: str, repo: str, ref: str) -> str | None:
    """CONTRIBUTIONS.md as it stood AT THE PIN - not as it stands now, which is a file the
    team can still edit after the deadline.

    A team that submitted but never wrote the file gets `(not filled in)`, not a blank: the
    blank would read as "the toolkit did not look", and whether the team said who did what
    is exactly the thing a grader is deciding an individual adjustment on. Blank is reserved
    for "there is nothing to have read" - no pin, or a read that failed."""
    if not ref:
        return None
    try:
        text = get_file_content(cohort_org, repo, CONTRIBUTIONS_FILE, ref=ref)
    except RuntimeError:
        return None  # a read failure is not a fact about the team
    if not text or not text.strip() or is_untouched_stub(text):
        return CONTRIBUTIONS_UNFILLED
    return text


def _sheet_info(
    cohort_org: str,
    targets: list[tuple[str, str, list[str]]],
    pins: dict[str, tuple[str, str]],
    due: datetime | None,
    tz: str | None,
    *,
    is_group: bool,
    notes: dict[str, str] | None = None,
) -> dict[str, dict]:
    """The toolkit's facts, per submission unit: when the pinned commit was made, how late
    that is, and (for a team) what CONTRIBUTIONS.md said at that commit.

    Only what was DERIVED, and only for the repos that were READ - a unit with no pin here
    was left alone by `_provisional_pins` because nothing has been pushed to it, and the
    merge keeps the fact the sheet already holds for it.

    The block's shape - which keys exist at all for this assignment - is
    `grades._fresh_info`'s to declare, and the merge unions the two; stating it here as
    well is how a key added in one place goes missing in the other."""
    out: dict[str, dict] = {}
    looked_at = datetime.now(schedule._tz(tz)).isoformat(timespec="minutes")
    for repo, unit, _members in targets:
        if repo not in pins:
            continue
        sha, submitted_at = pins[repo]
        # This pass READ this repo, whatever it found. Recorded so the once-only receipt
        # fires once and so a repo nobody has pushed to is not re-read every tick.
        info: dict = {"checked": looked_at}
        when = _parse_iso(submitted_at) if (sha and submitted_at) else None
        if when is not None:
            info["submitted"] = submitted_display(submitted_at, tz)
            info["days_late"] = days_late(when, due, tz) if due is not None else None
            note = (notes or {}).get(repo)
            if note:
                # The one fact in `info:` that is not about the work: whether the time this
                # row is built from is the SERVER's or the student's. A row GitHub timed
                # carries nothing here; one built from a committer date says so, because a
                # grader acting on `days_late: 0` has to know which they are reading.
                info["submitted_note"] = note
        if is_group:
            info["contributions"] = _contributions(cohort_org, repo, sha)
        out[unit] = info
    return out


def _quiet_since(pushed_at: str, recorded: object, checked: object = None) -> bool:
    """Whether this repo can be left alone: nothing has reached it since we last looked.

    Two facts on the sheet can answer that, and they are compared differently. `recorded`
    is `info.submitted`, a pinned commit's committer date - a push lands seconds AFTER the
    commit it carries, so the minute is compared INCLUSIVELY or every repo reads as busy
    and nothing is saved. `checked` is `info.checked`, the minute this pass last looked at
    the repo, so a push inside that same minute is still unseen and the comparison is
    STRICT. The second fact is what a non-submitter has: without it every repo nobody has
    pushed to was re-read four times an hour for the whole late window.

    False whenever the answer is not certain - an unparseable stamp, a repo missing from
    the listing, a unit the sheet has no fact for yet - because the cost of asking again is
    one API call and the cost of not asking is a submission nobody sees."""
    if not pushed_at:
        return False
    pushed = _parse_iso(str(pushed_at))
    if pushed is None:
        return False
    to_minute = {"second": 0, "microsecond": 0}
    pushed = pushed.replace(**to_minute)
    pinned = _parse_iso(str(recorded)) if recorded else None
    if pinned is not None and pushed <= pinned.replace(**to_minute):
        return True
    looked = _parse_iso(str(checked)) if checked else None
    return looked is not None and pushed < looked.replace(**to_minute)


def _pushed_at(cohort_org: str) -> dict[str, str]:
    """`{repo: pushed_at}` for the whole cohort, from ONE listing.

    The server's word on when each repo last received anything - which is what the pin, a
    date the student wrote, is checked against. Best-effort: without it every repo is
    simply read (see `_provisional_pins`) and nothing is accused."""
    try:
        return {
            row["name"]: row.get("pushed_at") or ""
            for row in list_org_repos(cohort_org)
        }
    except RuntimeError as exc:
        log(f"  (no repo listing this tick - re-reading each submission: {exc})")
        return {}


def _provisional_pins(
    cohort_org: str,
    targets: list[tuple[str, str, list[str]]],
    deadline: str,
    previous: dict,
    due: datetime | None = None,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]] | None:
    """Each repo's last commit on or before the cutoff, read WITHOUT writing a snapshot.

    The snapshot file stays write-once and stays the cutoff's job: these pins move with
    every push through the late window, which is the whole point of refreshing the sheet.

    Only the repos that can have MOVED are read. One org listing carries `pushed_at` for
    the whole cohort, and a repo quiet since the commit the sheet already records cannot
    have gained a later one - so it is not asked, and the fact on the sheet stands (a unit
    absent from these pins is one `grades._merged_block` leaves alone). The refresh runs
    four times an hour for the length of the late window, where it used to cost one commits
    call per submission repo on every one of those ticks.

    Returns `(pins, a note per repo whose pin the server's own push time contradicts)`, or
    None if a lookup we DID make failed - a half-read cohort must not rewrite the file.

    No push records are read here, and so no row is ever sourced `push`: that is one call
    per submission repo and this runs four times an hour for the length of the late
    window. The freeze pays for it once (`_submitted`), which is where the answer is
    written down for good."""
    pushed = _pushed_at(cohort_org)
    pins: dict[str, tuple[str, str]] = {}
    notes: dict[str, str] = {}
    for repo, unit, _members in targets:
        was = (previous.get(unit) or {}).get(grades.INFO_KEY) or {}
        if _quiet_since(pushed.get(repo, ""), was.get("submitted"), was.get("checked")):
            continue
        pin = _snapshot_sha(cohort_org, repo, deadline)
        if pin is None:
            return None
        pins[repo] = (pin.sha, pin.committed)
        if delivery_is_suspect(pin.committed, pushed.get(repo, ""), due):
            notes[repo] = SUSPECT_NOTE
    return pins, notes


def _receipt_event(
    phase: SheetPhase, sha: str, was: object, now_shown: str, checked: object = None
) -> str | None:
    """Which receipt this unit has earned since the last write, if any.

    The comparison is against what the SHEET last recorded, not against a marker we would
    have to store: `info.submitted` is already there, it is ours, and it moves exactly when
    a new commit is pinned. A push in the same minute as the last one is the one case it
    cannot see, and a duplicate receipt is worse than a missed one for a student who
    pushed twice in sixty seconds."""
    if phase is SheetPhase.FREEZING:
        return course.RECEIPT_FROZEN if sha else None
    if not was and not checked:
        # The first time we looked, and it says so either way. `checked` is what tells a
        # student who has not submitted apart from one we have never looked at: without it
        # this fired on every tick, and the receipt marker swallowed all but the first -
        # at the cost of an issue lookup and a comment read per non-submitter per tick.
        return course.RECEIPT_DUE
    if now_shown and now_shown != was:
        return course.RECEIPT_UPDATED
    return None


def _post_receipts(
    cohort_org: str,
    spec: grades.SheetSpec,
    targets: list[tuple[str, str, list[str]]],
    pins: dict[str, tuple[str, str]],
    previous: dict,
    phase: SheetPhase,
    tz: str | None,
    due: datetime | None,
    dry_run: bool,
    changed: bool = True,
) -> None:
    """Tell each student what we recorded for them, in their own repo's Feedback issue.

    Never fatal: a receipt is a courtesy, and a repo whose issue cannot be opened must not
    stop the sheet - which is the record - from being written. Nothing at all for work
    handed in off GitHub: there is no push to acknowledge."""
    if spec.submit_external:
        return
    posted = 0
    for repo, unit, members in targets:
        sha, submitted_at = pins.get(repo, ("", ""))
        when = _parse_iso(submitted_at) if (sha and submitted_at) else None
        if when is not None:
            # In the COHORT's clock, like everything else a student is shown: the API
            # answers UTC, and "pushed 20:14" for a 22:14 push reads as a bug.
            when = when.astimezone(schedule._tz(tz))
        shown = submitted_display(submitted_at, tz) if when is not None else ""
        before = (previous.get(unit) or {}).get(grades.INFO_KEY) or {}
        was = before.get("submitted")
        event = _receipt_event(phase, sha, was, shown, before.get("checked"))
        if event is None or (not changed and event == course.RECEIPT_UPDATED):
            continue
        body = grades.receipt(
            spec,
            event,
            sha=sha,
            pushed_display=grades._display_long(when),
            days=days_late(when, due, tz) if (when is not None and due) else 0,
        )
        issue = grades.ensure_feedback_issue(
            cohort_org, repo, grades.feedback_body(spec, unit, members), dry_run
        )
        if not isinstance(issue, int):
            # No issue, or a lookup that could not be read. A receipt is a courtesy and the
            # sheet is the record, so either way this unit waits for the next tick.
            continue
        if grades.post_receipt(
            cohort_org,
            repo,
            issue,
            body,
            course.receipt_marker(sha, event),
            dry_run,
        ):
            posted += 1
            log_person(f"    receipt ({event}) on {cohort_org}/{repo}#{issue}")
    if posted:
        # A COUNT: this log is public, and a receipt names a submission repo.
        log_ok(f"{posted} submission receipt(s) up to date")


def _status_line(
    spec: grades.SheetSpec, phase: SheetPhase, total: int, submitted: int, derived: bool
) -> str:
    """The one line of the header that changes hour to hour. It says what a grader wants
    to know before opening the file: is it worth marking yet, and can it still move?"""
    if phase is not SheetPhase.OPEN:
        return f"FROZEN {spec.cutoff_display}".strip()
    if spec.submit_external:
        return "OPEN - submitted outside GitHub"
    # Named, because "3 of 5" reads differently for teams than for students and a grader
    # scanning this line wants to know which they are looking at without counting rows.
    unit = "teams" if spec.is_group else "students"
    line = f"OPEN - {submitted} of {total} {unit} have submitted"
    if derived:
        line += "; late pushes still update `info:` until the cutoff."
    return line


def _sheet_phase(
    cohort_org: str,
    slug: str,
    old_text: str,
    now: datetime,
    cutoff: datetime | None,
) -> SheetPhase:
    """Where this write falls in the assignment's life.

    Decided from the file and the clock rather than by the caller. The handout, the
    refresh, the button and the freeze each used to pass their own answer, and the one
    that defaulted to OPEN won whenever it ran last: a late onboarder provisioned after
    the cutoff - or any press of Release assignment - rewrote a sealed sheet's header back
    to `OPEN - late pushes still update info:`, telling a grader the marks could still
    move.

    ALREADY SEALED is what the sheet itself says, so the seal survives a snapshot that
    could not be read. Otherwise the CUTOFF decides, off the schedule and the template -
    no extra call. Only when the schedule cannot say (an assignment collected by hand,
    with no dated entry) is the snapshot file asked."""
    if grades.sheet_is_frozen(old_text):
        return SheetPhase.FROZEN
    sealed = (
        now >= cutoff
        if cutoff is not None
        else load_snapshots(cohort_org, slug) is not None
    )
    return SheetPhase.FREEZING if sealed else SheetPhase.OPEN


def sync_sheet(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    key: str,
    slug: str,
    template: str,
    *,
    is_group: bool,
    now: datetime,
    units: list[tuple[str, list[str]]] | None = None,
    autograde: dict[str, str] | None = None,
    completion: dict[str, str] | None = None,
    dry_run: bool = False,
) -> bool:
    """Write `grading_sheets/<slug>.yml` for this assignment, creating it if it is not
    there and leaving it exactly as it is when nothing has changed.

    ONE function for all three moments - handout, refresh, freeze - because they differ
    only in what `info:` may say. A separate creator would be a second definition of the
    sheet's shape, and the two would drift the first time a field was added. Which moment
    this is, is decided HERE (`_sheet_phase`) and never passed in: three callers choosing
    it from different information is how a re-fired handout un-froze a sealed sheet.

    Nothing is derived before the due date (there is nothing to derive, and a handout must
    not cost an API call per student), nothing at all for an externally submitted
    assignment, and nothing once the sheet is FROZEN. The write itself is skipped when the
    rendered text hashes to what the repo already holds, so the hourly tick is free."""
    gspec = load_grading_spec(course_org, template)
    spec = sheet_spec(sched, key, slug, gspec, is_group)
    path = grades.sheet_path(slug)
    entry = sched.assignments.get(key)
    due = entry.due_datetime if entry else None

    targets: list[tuple[str, str, list[str]]] = []
    if units is None:
        targets = submission_targets(cohort_org, slug, is_group, key)
        units = [(unit, members) for _repo, unit, members in targets]
    else:
        # The handout passes its own list, built from the same roster (`assign.release`),
        # so it can carry the same key twice for the same reasons. One block per unit here
        # too: `one_per_unit` says what a repeated key costs the sheet. The repo name is
        # not needed to tell units apart, hence the blank.
        units = [
            (unit, members)
            for _repo, unit, members in one_per_unit(
                [("", unit, members) for unit, members in units]
            )
        ]
    if not units:
        # Nobody onboarded, or no teams yet. `submission_targets` has said which.
        log(f"  [skip] {path} - no submission units yet; a later tick creates it")
        return True

    try:
        found = get_file_with_sha(cohort_org, CONFIG_REPO, path)
    except RuntimeError as exc:
        log_err(f"  ! could not read {path}: {exc}")
        return False
    old_text, old_sha = found if found else ("", "")
    phase = _sheet_phase(
        cohort_org, slug, old_text, now, grades.cutoff_at(sched, key, gspec)
    )
    try:
        on_disk = grades.parse_sheet(old_text) if old_text else {}
    except grades.SheetUnreadable as exc:
        # A grader mid-edit. The file is theirs and it is the record, so the tick reports
        # and stops; the next one picks it up the moment the YAML parses again.
        log_err(f"  ! {path} cannot be read ({exc}) - leaving it exactly as it is")
        return False
    previous = on_disk.get(spec.container_key) or {}
    if not isinstance(previous, dict):
        log_err(
            f"  ! {path}: `{spec.container_key}:` is not a mapping of units - "
            f"leaving it exactly as it is"
        )
        return False

    derive = (
        bool(targets)
        and not spec.submit_external
        and (
            phase is SheetPhase.FREEZING
            or (phase is SheetPhase.OPEN and due is not None and now >= due)
        )
    )
    info_updates: dict[str, dict] = {}
    pins: dict[str, tuple[str, str]] = {}
    notes: dict[str, str] = {}
    if derive and phase is SheetPhase.FREEZING:
        rows = load_snapshot_rows(cohort_org, slug)
        if rows is None:
            # Sealing against a snapshot that is not there would record "nobody submitted"
            # for the whole cohort, permanently. The facts the sheet already holds are the
            # last ones anybody looked up, so they stand; only the header moves to FROZEN.
            log_err(
                f"  ! no {snapshot_path(slug)} - sealing {path} on the facts it holds"
            )
            derive = False
        else:
            pins = {r: (row.sha, row.submitted_at) for r, row in rows.items()}
            # Which rung timed each row was decided once, at the freeze, and is read back
            # here: the snapshot is the record, and a row GitHub itself timed needs no note.
            notes = {
                r: SUBMITTED_NOTES[row.submitted_source]
                for r, row in rows.items()
                if row.sha and row.submitted_source in SUBMITTED_NOTES
            }
    elif derive:
        found = _provisional_pins(
            cohort_org,
            targets,
            (grades.cutoff_at(sched, key, gspec) or now).isoformat(),
            previous,
            due,
        )
        if found is None:
            log_err(f"  ! could not read every submission for {path} - not rewriting")
            return False
        pins, notes = found
    if derive:
        info_updates = _sheet_info(
            cohort_org,
            targets,
            pins,
            due,
            sched.timezone,
            is_group=is_group,
            notes=notes,
        )
    for unit, count in (autograde or {}).items():
        info_updates.setdefault(unit, {})["autograde"] = count
    # Beside the count, and on the same terms: information for whoever marks it, derived at
    # the cutoff and never typed. `setdefault`, because a unit can carry both.
    for unit, state in (completion or {}).items():
        info_updates.setdefault(unit, {})["completion"] = state

    sheet = grades.merge_sheet(
        on_disk or None,
        spec,
        units,
        info_updates,
        frozen=phase is SheetPhase.FROZEN,
    )
    # Counted off the MERGED sheet rather than off this tick's derivation: a repo nobody
    # has pushed to since the last refresh is not re-read, so its submission is on the file
    # and not in `info_updates`.
    submitted = sum(
        1
        for block in (sheet.get(spec.container_key) or {}).values()
        if isinstance(block, dict)
        and (block.get(grades.INFO_KEY) or {}).get("submitted")
    )
    status = _status_line(spec, phase, len(units), submitted, derive)
    content = grades.dump_sheet(sheet, spec, status).encode()
    # NOT a byte compare. The file is the grader's: their quoting, their indentation, the
    # blank lines they leave and the comments they add ("extension granted by email") are
    # all things a re-dump rewrites, and this runs four times an hour - so a sheet was
    # canonicalised within fifteen minutes of every save, and a grader editing it locally
    # got a non-fast-forward on every push. What the toolkit owns is the DATA and the
    # header; a write happens when one of those two really moved, and not otherwise.
    changed = sheet != on_disk or grades.sheet_header(old_text) != grades.sheet_header(
        content.decode()
    )
    written = True
    if not changed:
        log_skip(f"{path} (unchanged)")
    elif dry_run:
        log(f"    DRY-RUN  {path} ({status})")
    else:
        # The message carries counts, never a handle or a team name: classroom-config is
        # private, but its commit messages are quoted back in public run logs.
        #
        # `expected_sha` is the sha this run READ the file at, so GitHub refuses the write
        # if a grader saved over it in between. Letting `put_file` fetch a fresh sha would
        # make the call succeed however stale our copy was - and this file is typed into by
        # hand, in a browser, while the quarter-hourly tick is running: a save landing in
        # that window would be silently reverted, marks and all. A refusal is counted, and
        # the next tick re-reads, re-merges and writes.
        written = put_file(
            cohort_org,
            CONFIG_REPO,
            path,
            content,
            f"grading sheet: {slug} - {status}",
            expected_sha=old_sha,
        )
        if written:
            log_ok(f"{path}: {status}")
    # AFTER the record: a receipt promises something the sheet is supposed to hold, so the
    # sheet lands first. An unchanged tick posts nothing new to say - only the once-only
    # `due` and `frozen` events, which have to fire whether or not a pin moved (a student
    # who never submitted has an unchanging sheet and is owed both).
    if derive and written:
        # Only when the SHEET landed. Both this pass and the Collect button can derive the
        # same event, and the one whose compare-and-swap write was refused has not recorded
        # what its receipt would promise - it posted a duplicate instead.
        _post_receipts(
            cohort_org,
            spec,
            targets,
            pins,
            previous,
            phase,
            sched.timezone,
            due,
            dry_run,
            changed=changed,
        )
    return written


def _pin_commit(
    repo_dir: Path, deadline: str, snapshot: str | None = None
) -> str | None:
    """Check out the commit this repo is graded at and return its sha (None = nothing to
    grade).

    `snapshot` is this repo's server-timed snapshot entry: a sha to grade, or "" for "no
    commit existed by the deadline". None means no snapshot covers this repo, so we fall
    back to `rev-list --before` - which filters on the COMMITTER date, a value the student
    supplies, so it can be backdated. `deadline` is an ISO date or datetime; a bare date
    (no time) is treated as end-of-day."""

    def _checkout_if_present() -> bool:
        """Check out the frozen commit if it's in the clone; True if it was."""
        if git("-C", str(repo_dir), "cat-file", "-e", f"{snapshot}^{{commit}}")[0] != 0:
            return False
        git("-C", str(repo_dir), *GIT_ENV, "checkout", "-q", snapshot)
        return True

    if snapshot is not None:
        if not snapshot:
            return None  # snapshot recorded no submission on/before the deadline
        if _checkout_if_present():
            return snapshot
        # The pinned commit isn't in the clone: a force-push AFTER the deadline rewrote
        # history. Falling back to the committer-date pin here would grade the rewritten
        # history - turning a detected tamper into a successful one. Try to fetch exactly the
        # frozen commit (a rewrite orphans it, but it survives server-side until GC) and grade
        # THAT; if it can't be recovered, fail the target loudly (score zero) rather than
        # ever pinning on the student-controlled dates of a rewritten history.
        git("-C", str(repo_dir), *GIT_ENV, "fetch", "-q", "origin", snapshot)
        if _checkout_if_present():
            return snapshot
        log_err(
            f"  ! snapshot commit {snapshot[:8]} is not in the clone and could not be "
            f"fetched (history rewritten after the deadline?) - scoring zero rather than "
            f"grading the rewritten history"
        )
        return None
    before = (
        deadline if ("T" in deadline or ":" in deadline) else f"{deadline} 23:59:59"
    )
    code, out = git("-C", str(repo_dir), "rev-list", "-1", f"--before={before}", "HEAD")
    sha = out.strip()
    if code != 0 or not sha:
        return None
    git("-C", str(repo_dir), *GIT_ENV, "checkout", "-q", sha)
    return sha


def _stray_conversion(nb: Path) -> Path | None:
    """The file `jupyter nbconvert --to script` actually wrote for `nb`, when that is not
    the expected `<stem>.py`.

    nbconvert names its output from the notebook's `metadata.language_info.file_extension`,
    so a notebook whose metadata is empty, carries only a `kernelspec`, or omits
    `file_extension` (all common in student submissions, and what a fresh `{}`-metadata
    notebook looks like) converts to `<stem>.txt` - or to a bare `<stem>` if
    `file_extension` is present but empty. The hidden tests then `from starter import ...`
    against a file that does not exist and every submission scores zero, so the output is
    renamed back to `.py` rather than trusted."""
    for candidate in (nb.with_suffix(".txt"), nb.with_suffix("")):
        if candidate.is_file():
            return candidate
    return None


def _walk_files(root: Path) -> Iterator[Path]:
    """Every file under `root`, walked WITHOUT following symlinks - the symlink-safe stand-in
    for `Path.rglob` on a student checkout (see `_strip_student_test_rigging` for the hazard)."""
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in filenames:
            yield base / name


def _strip_student_test_rigging(workdir: Path) -> None:
    """Second-line removal of anything the student could have committed to steer their own
    grading run (see `_STUDENT_TEST_RIGGING`) plus pytest/py caches, before any subprocess
    touches the tree. One walk of the checkout, deleting by name.

    Walked with `followlinks=False` so a committed symlink cycle (a->b, b->a) or a symlink to
    `/` can never loop the walk or drag it out of the checkout. `Path.rglob` FOLLOWS directory
    symlinks, and this runs BEFORE any subprocess timeout could fire, so a followed cycle would
    hang the whole grading job (the same never-completes DoS the sandbox limits close) or walk
    the entire runner filesystem. Symlinks are never traversed, and a name-matching symlink is
    removed by `unlink`, never `rmtree` (which refuses a symlink)."""
    targets = frozenset(_STUDENT_TEST_RIGGING) | {".pytest_cache", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(workdir, followlinks=False):
        base = Path(dirpath)
        for name in filenames:
            if name in targets:
                (base / name).unlink(missing_ok=True)
        for name in list(dirnames):
            if name not in targets:
                continue
            hit = base / name
            if hit.is_symlink():
                hit.unlink(missing_ok=True)
            else:
                shutil.rmtree(hit, ignore_errors=True)
            dirnames.remove(name)  # pruned - don't descend into what we just deleted


def _harden_checkout(workdir: Path) -> None:
    """Make a freshly cloned submission safe to run code out of. Idempotent, and called by
    every path that starts a subprocess in it - the completion check and the hidden tests
    both do, and the one that happens to go first must not be the only one that hardens.

    The clone persists the bot credential in `.git/config`; env-stripping does not reach it
    and student code runs next in the same workspace, so `.git` goes first. The student's
    own grading-rigging files go with it (`_strip_student_test_rigging`)."""
    shutil.rmtree(workdir / ".git", ignore_errors=True)
    _strip_student_test_rigging(workdir)


def _apply_rlimits() -> None:
    """`preexec_fn` for a graded subprocess: runs in the CHILD after fork, before exec, and
    lowers the POSIX resource caps a hostile submission can burn (see the RLIMIT_* constants).

    Defensive by design: each limit is only ever LOWERED (never raised above the inherited hard
    cap), and a platform that refuses one is skipped rather than killing the child before it can
    exec - macOS, for instance, does not enforce RLIMIT_DATA, so a hard cap there is a ceiling
    on the Linux grading host, not a guarantee everywhere the tests run."""
    for name, cap in (
        ("RLIMIT_DATA", RLIMIT_DATA_BYTES),
        ("RLIMIT_CPU", RLIMIT_CPU_SECONDS),
        ("RLIMIT_NPROC", RLIMIT_NPROC_MAX),
        ("RLIMIT_FSIZE", RLIMIT_FSIZE_BYTES),
    ):
        res = getattr(resource, name, None)
        if res is None:
            continue  # a limit this platform doesn't define
        try:
            _soft, hard = resource.getrlimit(res)
            new = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
            resource.setrlimit(res, (new, hard))
        except (ValueError, OSError):
            pass  # a platform that won't take this limit must not abort the run


@cache
def _grader_dep_present(module: str) -> bool:
    """Whether `module` is importable by the interpreter the graded subprocess runs under.

    Checked in-process: the subprocess runs `sys.executable`, and neither PYTHONSAFEPATH
    nor the runspace PYTHONPATH takes site-packages away from it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@cache
def _grader_dep_missing(module: str) -> bool:
    """`_grader_dep_present` inverted, saying so LOUDLY - and, because it is cached too,
    exactly once per run.

    `_run_limited` sends the child's output to DEVNULL and reports ANY exit code as a
    completed run, so a `python -m pytest` that died on "No module named pytest" was
    indistinguishable from a submission that failed its tests: every target came back a
    grading-failed zero, the systemic guard reddened the cron, no sentinel was written, and
    the next hourly tick did it all again. A missing INTERPRETER dependency is a runner
    fault with one fix, so it says so in words rather than through a cohort of zeros."""
    if _grader_dep_present(module):
        return False
    log_err(
        f"  ! `{module}` is not installed in the grading environment - NOTHING can be "
        f"graded until the workflow installs it (it is pinned in requirements.txt, "
        f"which every seeded workflow's preamble installs)"
    )
    return True


# --------------------------------------------------------------- the sandbox uid
#
# The boundary a graded subprocess is on the wrong side of is the UID, not the environment.
# On Linux a process may read `/proc/<pid>/environ` of any process running as its own user,
# and the grading process holds the org-owner PAT for the whole leg - so a notebook cell
# doing `grep -l GH_TOKEN= /proc/*/environ` reads it straight out, whatever we stripped from
# the child's own environment and whatever step ordering the workflow keeps. `_run_limited`
# is the ONE place any of this is spawned from, so uid separation goes here and every
# graded subprocess gets it by construction.
#
# In Actions the sandbox account is created once per job by the rendered preamble (see
# workflows_render), and this fails CLOSED: no account, no `sudo -n`, nothing graded. Off a
# runner - a maintainer's laptop - there is no account to sudo to and no bot token in the
# environment either, so it degrades to the in-process sandbox and says so, once, loudly.
_ACTIONS = "GITHUB_ACTIONS"

SANDBOX_UNAVAILABLE = (
    f"the `{SANDBOX_USER}` sandbox account is not usable on this runner (`sudo -n -u "
    f"{SANDBOX_USER}` failed), and student code must never run as the process holding the "
    f"bot token - nothing was executed"
)
_SANDBOX_LOCAL_WARNING = (
    f"  ! running graded code as THIS user: there is no `{SANDBOX_USER}` account to drop "
    f"to outside GitHub Actions. Anything a submission runs can read this process's "
    f"environment and files. Local runs only - the unattended graders fail closed."
)


def _sudo(*args: str) -> bool:
    """One non-interactive privileged helper command. True when it succeeded.

    `-n` and never a prompt: this runs inside an unattended job, and a `sudo` that waits
    for a password is a job that hangs until the six-hour ceiling. A runner without
    passwordless sudo is a runner that grades nothing (see `sandbox_unusable`)."""
    try:
        return (
            subprocess.run(
                ["sudo", "-n", *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )
    except OSError:
        return False  # no sudo binary at all


@cache
def sandbox_user() -> str:
    """The account graded subprocesses run as, or `""` when they run as this process does.

    Probed once per process, and only under Actions: on a laptop there is no such account,
    and asking `sudo` about it would at best cost a password prompt in an interactive
    terminal."""
    if os.environ.get(_ACTIONS) != "true":
        return ""
    return SANDBOX_USER if _sudo("-u", SANDBOX_USER, "true") else ""


@cache
def sandbox_unusable() -> str:
    """Why no student code may be run right now, or `""` when it may.

    Asked ONCE per assignment, before anything clones a submission, so a runner without the
    sandbox records one skip rather than grading a whole cohort as the token holder."""
    if sandbox_user():
        return ""
    if os.environ.get(_ACTIONS) == "true":
        return SANDBOX_UNAVAILABLE
    log_err(_SANDBOX_LOCAL_WARNING)
    return ""


class SandboxUnavailable(RuntimeError):
    """A graded subprocess was reached for on a runner that cannot isolate it.

    A backstop, not a path anyone should hit: `collect` asks `sandbox_unusable` before it
    reaches a target at all. It exists so a call site added later cannot quietly run
    student code as the token holder."""


def _sandbox_roots(paths: Iterable[str | Path]) -> list[Path]:
    """The directory trees to hand to the sandbox user, one per temporary root.

    Widened from each path to the outermost ancestor still inside the system temp
    directory, because a `mkdtemp` root is mode 0700: chowning `…/tmpXYZ/sub` alone leaves
    `…/tmpXYZ` unreadable to the sandbox user, which cannot then reach the checkout at all.
    `/tmp` itself is world-traversable, so the widening stops there. A path outside the
    temp directory is handed over as it stands."""
    tmp = Path(tempfile.gettempdir()).resolve()
    roots: list[Path] = []
    for path in paths:
        node = Path(path).resolve()
        if tmp in node.parents:
            while node.parent != tmp:
                node = node.parent
        if node not in roots:
            roots.append(node)
    return roots


def _run_limited(
    argv: list[str],
    *,
    cwd: str,
    env: dict,
    timeout: int,
    writable: Iterable[str | Path] = (),
) -> bool:
    """Run `argv` AS THE SANDBOX USER, in its own session/process group under
    `_apply_rlimits`. Returns True if it exited on its own (ANY exit code - a non-zero
    pytest run is still a valid grading result), False if it blew the wall-clock `timeout`.

    The single choke point every graded subprocess goes through, which is why the uid
    separation lives here: the notebook execution, the notebook-to-script conversion, the
    hidden tests / `run.sh`, and the grader's reading-copy export all arrive at this
    function and none of them can opt out. `cwd` and `writable` name the trees the sandbox
    user needs (widened to their temporary roots, see `_sandbox_roots`); they are chowned
    to it before the run and back afterwards, so everything the grader reads next - the
    executed notebook, the JUnit report, the rendered copy - is its own again.

    Every sandbox process is killed by uid when this returns, however it returns. A
    `fork(); setsid(); fork()` daemon escapes the process GROUP by definition, and this
    function is called once per submission per stage: a survivor of student A's run would
    otherwise still be alive while student B's clone sits in a predictable temp path, free
    to read it, tamper with it, or forge the JUnit report B is scored on. `killpg` cannot
    reach those processes at all once they run as another uid - only `pkill -u` can.

    `subprocess.run(timeout=)` SIGKILLs only the direct child, orphaning the grandchildren a
    fork/memory bomb spawns - and the bomb can OOM-kill the whole job before the timeout even
    fires. That is the self-perpetuating DoS this closes: the run aborts, the fire-once sentinel
    never lands, and the next hourly tick regrades the same bomb, so the assignment never
    completes for ANY student. So we start a new session (start_new_session=True) and, on
    timeout, SIGKILL the entire process GROUP, then reap the leader.

    Output goes to DEVNULL, never a pipe: the rlimits cap the CHILD, so a submission printing
    in a loop fills the PARENT's memory with output nothing here reads (measured: 4.2 GB in 4
    seconds) - a memory bomb wearing the runner's own uid. Discarding at the fd means the
    child's writes cost us nothing, and `proc.wait(timeout=)` replaces the `communicate()`
    that only existed to drain those pipes."""
    if unusable := sandbox_unusable():
        raise SandboxUnavailable(unusable)
    user = sandbox_user()
    roots = _sandbox_roots([cwd, *writable]) if user else []
    for root in roots:
        _sudo("chown", "-R", user, str(root))
    # `env -i` and not sudo's own env handling: sudo's policy decides what survives, and
    # what has to reach the child here is EXACTLY `_sanitised_env` - no more (the parent's
    # leftovers) and no less (PATH, the proxy vars that take the network away). HOME and
    # TMPDIR are re-pointed into the tree the sandbox user now owns, because the inherited
    # ones belong to the runner's account: a `~/.jupyter` it cannot write is a stack of
    # warnings on every submission, and a temp file it leaves outside the graded tree is
    # one nothing cleans up.
    spawn = argv
    if user:
        env = {**env, "HOME": str(roots[0]), "TMPDIR": str(roots[0])}
        spawn = [
            "sudo",
            "-n",
            "-u",
            user,
            "env",
            "-i",
            *(f"{name}={value}" for name, value in sorted(env.items())),
            *argv,
        ]
    try:
        proc = subprocess.Popen(
            spawn,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            # The grading process is single-threaded, so the window between fork and exec runs no
            # Python that could deadlock on a lock another thread held - the PLW1509 hazard.
            preexec_fn=_apply_rlimits,  # noqa: PLW1509
        )
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            try:
                # Kill proc.pid AS the group id, not `getpgid(proc.pid)`: start_new_session makes
                # the child its own group leader (pgid == pid) at exec, and re-reading the pgid now
                # would follow a child that has since setsid()'d away - killing its NEW group and
                # leaving the original group's workers (the fork bomb) alive. Under the sandbox
                # this reaches `sudo` and nothing beyond it; the `pkill -u` below is what
                # actually ends the run.
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()  # group already gone / kill not permitted - fall back to the child
            proc.wait()  # reap the (killed) group leader so it isn't left a zombie
            return False
    finally:
        # Whatever happened above - a clean exit, a timeout, an exception on the way in -
        # nothing of this submission's may still be running when the next one is cloned.
        # Kill FIRST, then take the files back: a survivor that outlived the chown would be
        # writing into a tree the grader is about to read as its own.
        if user:
            _sudo("pkill", "-9", "-u", user)
            for root in roots:
                _sudo("chown", "-R", f"{os.getuid()}:{os.getgid()}", str(root))


# ------------------------------------------------------------------ the completion check
#
# "Restart the kernel and run all cells before you hand in" is a rule several syllabuses
# state and nobody could check: a notebook arrives with whatever outputs the student's
# laptop happened to hold, and a grader opening it cannot tell a result that reproduces
# from one that never will. At the cutoff, therefore, the same sandbox that runs the hidden
# tests re-EXECUTES the pinned notebook and records what happened - one word into the
# grading sheet's `info:` block, and the executed copy into the private archive beside the
# result JSON, so the grader can read the notebook as the toolkit saw it run.
#
# It is information, exactly like `info.autograde`: never a mark, never shown to a student,
# and never a reason to fail a run. Opt-in per assignment (`completion_check:` in
# grading_config.yml, defaulted from `format:` - see GradingSpec.runs_completion_check).
COMPLETION_CLEAN = "ran-clean"  # every cell executed, none raised
COMPLETION_ERRORS = "errors:"  # + the number of cells that raised (`errors:3`)
# Byte-identical to the starter the template handed out: the notebook was never opened.
# Distinct from a zero, and distinct from a notebook that runs and does nothing.
COMPLETION_NOT_ATTEMPTED = "not-attempted"
COMPLETION_NO_NOTEBOOK = "no-notebook"  # the submission holds no .ipynb to run
# The student's own notebook never finished - a cell that blocks, an infinite loop. Their
# fact, and one a grader has to see: the wall clock is the same RUN_TIMEOUT everything else
# here gets, and the process GROUP is killed (`_run_limited`).
COMPLETION_TIMED_OUT = "timed-out"
# nbconvert exited but produced no notebook - a file it would not open, a kernel that would
# not start. OUR fault or a corrupt submission, not a verdict on the work, so it is spelt
# differently from a timeout.
COMPLETION_DID_NOT_RUN = "did-not-run"
# What the executed notebook is executed WITH. `nbconvert --execute` drives a kernel
# through `nbclient`, and the kernel itself is `ipykernel` - a separate distribution that
# nbconvert does not pull in, so a runner with only nbconvert fails every notebook with
# "no such kernel" and would report a cohort of `did-not-run`. Both are pinned in
# requirements.txt, which every seeded workflow's preamble installs.
COMPLETION_DEPS = ("nbconvert", "ipykernel")
COMPLETION_DEP_SKIP = (
    "the completion check needs " + " and ".join(COMPLETION_DEPS) + " in the grading "
    "environment, and they are not installed"
)
# The ceiling on ANYTHING this module archives per submission - the executed notebook and
# the grader's reading copy alike. Both are notebooks full of plots, i.e. base64 PNG all
# the way down. Past this the state/verdict is still recorded and the copy is not:
# classroom-config is a git repo somebody has to clone, and a term of 40 MB notebooks per
# student makes it one nobody can.
ARCHIVE_MAX_BYTES = 5 * 1024**2
# The completion check runs OFFLINE. Not a jail - a real one needs a network namespace this
# job does not have - but every well-behaved HTTP client (requests, urllib, pandas.read_csv
# on a URL) honours these, so a notebook that only reproduces because it downloads its data
# reports `errors:N` rather than passing on a resource that may not be there in March. Port
# 9 is discard; nothing listens. Documented in docs/10, because it is a rule the assignment
# has to be written for: commit the data.
COMPLETION_DEAD_PROXY = "http://127.0.0.1:9"
_PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
# Jupyter's own scratch directory inside a notebook's folder; never the submission.
_CHECKPOINTS = ".ipynb_checkpoints"


@cache
def _starter_notebook_shas(course_org: str, template: str) -> frozenset[str]:
    """The git blob shas of every notebook on the template's DEFAULT branch - the starters
    exactly as students received them.

    A submission whose notebook hashes to one of these was never opened, which is
    `not-attempted` and not a run that produced nothing. Shas rather than content: one
    recursive tree call answers for the whole template, and a sha comparison IS byte
    identity - the plan's rule, not an approximation of it.

    An empty set (no notebook on `main`, or a tree we could not read) simply means nothing
    can be recognised as untouched this run; every notebook is then executed, which is the
    safe way round. Memoised per template per process, like the grading spec."""
    branch = default_branch(course_org, template, fallback="main")
    try:
        shas = repo_blob_shas(course_org, template, branch)
    except RuntimeError as exc:
        log_err(
            f"  ! could not read {course_org}/{template}@{branch} ({exc}) - an untouched "
            f"starter cannot be told from a submission this run"
        )
        return frozenset()
    return frozenset(
        sha
        for path, sha in shas.items()
        if path.endswith(".ipynb") and _CHECKPOINTS not in path.split("/")
    )


def _completion_notebooks(workdir: Path) -> list[Path]:
    """Every `.ipynb` in the checkout, shallowest first and ties broken by path.

    ORDERED on purpose. Most submissions hold exactly one notebook, but the state recorded
    against a student must not depend on which file a directory walk happened to see first
    - a re-run that picked the other one would move `info.completion` under a grader who
    had already read it. Walked with `_walk_files` (no symlink following) and
    `.ipynb_checkpoints` skipped: Jupyter's own autosave of the same notebook is not a
    second submission."""
    return sorted(
        (
            path
            for path in _walk_files(workdir)
            if path.suffix == ".ipynb"
            and _CHECKPOINTS not in path.relative_to(workdir).parts
        ),
        key=lambda path: (
            len(path.relative_to(workdir).parts),
            str(path.relative_to(workdir)),
        ),
    )


def _completion_env() -> dict:
    """The graded sandbox environment as the completion check needs it: no network, and the
    notebook's own directory back on the kernel's `sys.path`.

    `PYTHONSAFEPATH` is what the rest of the sandbox uses to keep a student-writable
    directory off `sys.path[0]`, and it is inherited straight through nbconvert into
    `ipykernel_launcher` - where IPython reads it and never adds the notebook's directory
    at all. So `import helpers` in a notebook committed beside `helpers.py` raised
    ModuleNotFoundError, and an ordinary submission was recorded `errors:N` for work that
    runs perfectly on the student's machine.

    Dropping it costs nothing here that is not already spent: the kernel exists to execute
    the student's code, in this same token-free environment, so a module of theirs
    shadowing one of ours changes nothing about what runs. The GRADER-side subprocesses -
    the notebook-to-script conversion, the hidden tests, the reading-copy export - keep it,
    because there a shadowed import is our code being redirected, not theirs."""
    env = _sanitised_env()
    env.pop("PYTHONSAFEPATH", None)
    for var in _PROXY_VARS:
        env[var] = COMPLETION_DEAD_PROXY
    for var in ("no_proxy", "NO_PROXY"):
        env.pop(var, None)
    return env


def _completion_state(executed: bytes) -> str:
    """`ran-clean`, or `errors:N` for the N cells that raised - read off the notebook
    nbconvert wrote.

    Counted per CELL, not per traceback: a cell can emit an error output and nothing else,
    and what a grader wants is how many places the notebook stops working."""
    try:
        nb = json.loads(executed)
    except (ValueError, UnicodeDecodeError):
        return COMPLETION_DID_NOT_RUN
    errors = sum(
        1
        for cell in nb.get("cells", [])
        if any(
            isinstance(out, dict) and out.get("output_type") == "error"
            for out in (cell.get("outputs") or [])
        )
    )
    return COMPLETION_CLEAN if not errors else f"{COMPLETION_ERRORS}{errors}"


# The kernel a Python notebook is executed with, whatever kernel it was SAVED with.
#
# `metadata.kernelspec.name` is whatever the student's own machine called its environment -
# `conda-env-ml-py`, `myenv`, a VS Code interpreter hash - and nbclient resolves that name
# literally against the kernels installed on the grading runner, where the only one is
# `python3`. Without this a submission written anywhere but a bare Jupyter install raised
# NoSuchKernel, wrote no output file, and was recorded `did-not-run`, i.e. "tell the
# maintainer" - for a large share of a real cohort.
COMPLETION_KERNEL = "python3"


def _kernel_argv(raw: bytes) -> list[str]:
    """The kernel override for this notebook, or nothing at all.

    Only for a notebook that says it is PYTHON, or says nothing: forcing `python3` onto an R
    or Julia submission would trade a truthful `did-not-run` for a page of syntax errors
    recorded as the student's. Read off `language_info` (what the notebook last ran as)
    falling back to the kernelspec's own `language`, because a kernelspec NAME is exactly
    the thing that cannot be trusted here."""
    try:
        meta = json.loads(raw).get("metadata") or {}
    except (ValueError, UnicodeDecodeError, AttributeError):
        return []  # not a notebook we can read; nbconvert will say so its own way
    language = str(
        (meta.get("language_info") or {}).get("name")
        or (meta.get("kernelspec") or {}).get("language")
        or ""
    ).lower()
    if language and not language.startswith("python"):
        return []
    return [f"--ExecutePreprocessor.kernel_name={COMPLETION_KERNEL}"]


def _check_completion(
    workdir: Path, starters: frozenset[str], run_root: Path
) -> tuple[str, bytes | None]:
    """Execute this submission's notebook top to bottom and say what happened, plus the
    executed copy to archive (None where there is nothing to archive).

    WHICH notebook: the first one, in `_completion_notebooks` order, that is not still
    byte-identical to a starter the template handed out - so a template shipping
    `00-setup.ipynb` beside `assignment.ipynb` is checked on the one the student worked in.
    `not-attempted` needs EVERY notebook in the checkout to be an untouched starter; on the
    shallowest one alone, a student who did all their work in the second notebook read as
    having done none of it.

    Runs BEFORE `_run_tests`, on the notebook as submitted: `_run_tests` converts every
    `.ipynb` in the checkout to a script, and a script is not what the rule is about. The
    checkout is hardened first by the caller, so no credential is in reach of the code this
    starts, and the run is capped and group-killed exactly like the hidden tests are."""
    notebooks = _completion_notebooks(workdir)
    if not notebooks:
        return COMPLETION_NO_NOTEBOOK, None
    notebook = raw = None
    for candidate in notebooks:
        content = candidate.read_bytes()
        if blob_sha(content) not in starters:
            notebook, raw = candidate, content
            break
    if notebook is None:
        return COMPLETION_NOT_ATTEMPTED, None
    out = run_root / "executed"
    out.mkdir(parents=True, exist_ok=True)
    if not _run_limited(
        [
            sys.executable,
            "-m",
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            *_kernel_argv(raw),
            # Run the WHOLE notebook. Without this nbclient stops at the first traceback,
            # and `errors:1` would mean "at least one" for every submission that has any.
            "--allow-errors",
            "--output-dir",
            str(out),
            "--output",
            "executed.ipynb",
            str(notebook),
        ],
        # The notebook's own directory, which is where the student ran it: a notebook that
        # opens `data/train.csv` beside itself has to find it, or the check reports an
        # error the submission does not have.
        cwd=str(notebook.parent),
        env=_completion_env(),
        timeout=RUN_TIMEOUT,
        # The executed copy lands under `run_root`, which is outside the checkout.
        writable=(run_root,),
    ):
        log_err(
            f"  ! the completion check timed out after {RUN_TIMEOUT}s (process group "
            f"killed) - recording `{COMPLETION_TIMED_OUT}`"
        )
        return COMPLETION_TIMED_OUT, None
    executed = out / "executed.ipynb"
    if not executed.is_file():
        return COMPLETION_DID_NOT_RUN, None
    data = executed.read_bytes()
    return _completion_state(data), data


def _run_tests(workdir: Path, tests_src: Path) -> dict | None:
    """Run the hidden tests against the checked-out submission, token-free and sandboxed.
    Returns the result.json dict, or None if grading could not run (a wall-clock timeout, a
    process-group kill, or a run that wrote no report).

    Integrity: the hidden tests and the scored report live OUTSIDE the checkout, in a fresh
    runspace the student never touched, and pytest runs from there with config/plugin/cache
    discovery cut off at that runspace - so nothing the student COMMITTED can be collected as
    a test, read as configuration, or scored as a pre-baked report. The credential the clone
    stored in `.git` is removed and the student's own rigging files are stripped before any
    subprocess runs, which itself runs in a resource-capped process group (`_run_limited`) so a
    memory/fork/disk bomb is contained rather than taking down the whole grading job.

    KNOWN residual: this is a defence against STATIC rigging. It does NOT stop the student's own
    code, once imported in-process by the hidden tests, from rewriting the junit report we wrote
    (an `atexit`/`os._exit` forge) to fake all-pass. That is an accepted residual - autograde
    scores are faculty-reviewed before the grades pipeline distributes anything and are never
    shown to the student directly. See the `_STUDENT_TEST_RIGGING` note for where the
    trusted-out-of-band-result plugin would go if that ever needs closing."""
    # `_sanitised_env` keeps cwd/'' off sys.path, so a committed `pytest.py` /
    # `sitecustomize.py` can't shadow real modules. The submission is NOT put on PYTHONPATH
    # either: every PYTHONPATH entry precedes the stdlib, so a student `json.py`/`operator.py`
    # there would shadow a module the hidden tests import and let them force assertions.
    # Instead the trusted hidden-tests conftest appends the submission to sys.path AFTER the
    # stdlib (see the injection below), so a real module always wins the import while the
    # submission's own uniquely-named module still resolves.
    env = _sanitised_env()
    _harden_checkout(workdir)
    # Convert every notebook the submission holds to an importable script first (Otter can
    # slot in here). Unconditional, and driven by what is actually in the checkout rather
    # than by a `format:` the template declared: the two disagreed silently whenever a
    # student worked in a notebook on a `py` assignment (or the reverse), and the grader
    # then imported nothing. A submission with no .ipynb walks this loop and converts
    # nothing. Walked with os.walk(followlinks=False), like the strip above, so a symlink
    # cycle can't hang this discovery before the per-convert timeout could fire.
    for nb in _walk_files(workdir):
        if nb.suffix != ".ipynb":
            continue
        if _grader_dep_missing("nbconvert"):
            return None
        # A timed-out convert ABORTS this submission rather than continuing to the next
        # notebook: tolerating one per notebook multiplies the budget (100 hanging .ipynb
        # = 100 x RUN_TIMEOUT), blowing the 6h Actions cap so the job dies before the
        # fire-once sentinel is written and the hourly tick regrades the same bomb for
        # ever. A hanging conversion means this submission cannot be graded, so we bail
        # here and the caller records the usual "grading failed to run" zero.
        if not _run_limited(
            [
                sys.executable,
                "-m",
                "jupyter",
                "nbconvert",
                "--to",
                "script",
                str(nb),
            ],
            cwd=str(workdir),
            env=env,
            timeout=RUN_TIMEOUT,
        ):
            log_err(
                f"  ! converting a notebook timed out after {RUN_TIMEOUT}s "
                f"(process group killed) - abandoning this submission"
            )
            return None
        script = nb.with_suffix(".py")
        if not script.exists() and (stray := _stray_conversion(nb)):
            stray.rename(script)
            log(
                "    (a notebook declares no python file_extension - renamed the stray output)"
            )
    with tempfile.TemporaryDirectory() as run:
        tests_dir = Path(run) / "tests"
        copy_tree(tests_src, tests_dir)
        # Make the submission importable by the hidden tests WITHOUT letting a student
        # module shadow a stdlib/site name (`operator.py`, `json.py`) a hidden test imports.
        # A `sitecustomize` in its own dir - the ONLY thing on PYTHONPATH - runs at
        # interpreter startup (before any conftest) and appends the submission to sys.path
        # AFTER the stdlib, so a real module always wins the import while the submission's
        # own uniquely-named module still resolves. This touches neither the faculty
        # conftest (a `from __future__` first line stays first) nor sys.path[0]
        # (PYTHONSAFEPATH), and the student's own sitecustomize isn't on the path to run.
        startup = Path(run) / "startup"
        startup.mkdir()
        (startup / "sitecustomize.py").write_text(
            f"import sys\nsys.path.append({str(workdir)!r})\n"
        )
        env["PYTHONPATH"] = str(startup)
        report = Path(run) / "report.xml"
        runner = tests_dir / RUN_SCRIPT
        scripted = runner.is_file()
        if scripted:
            # The escape hatch (see RUN_SCRIPT). The script says how to run this course's
            # tests and where the submission is; everything else about the sandbox is
            # unchanged, including that the report lands outside the checkout.
            env[JUNIT_OUT_ENV] = str(report)
            env[SUBMISSION_ENV] = str(workdir)
            argv = ["sh", str(runner)]
        elif _grader_dep_missing("pytest"):
            return None
        else:
            argv = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--confcutdir={tests_dir}",
                str(tests_dir),
                f"--junitxml={report}",
            ]
        completed = _run_limited(
            argv,
            # Run FROM the runspace, NOT the checkout. On Python < 3.11 (no PYTHONSAFEPATH)
            # `python -m` puts cwd on sys.path[0], so a checkout cwd would let a committed
            # `operator.py`/`collections.py` shadow the stdlib during interpreter startup -
            # before any sitecustomize could undo it - crashing or hijacking the run. The
            # runspace holds no student files, so its cwd is inert. (Trade-off: a submission
            # reading a repo-relative data file by bare name isn't supported - a hidden test
            # must pass an absolute path.)
            cwd=run,
            env=env,
            timeout=RUN_TIMEOUT,
            # The runspace is the cwd; the SUBMISSION is a second tree, and the tests
            # import from it.
            writable=(workdir,),
        )
        if not completed:
            log_err(
                f"  ! grading timed out after {RUN_TIMEOUT}s (process group killed)"
            )
            return None
        if not report.exists():
            if scripted:
                log_err(
                    f"  ! the hidden tests' `{RUN_SCRIPT}` wrote no report to "
                    f"${JUNIT_OUT_ENV} - nothing can be scored from this run"
                )
            return None
        try:
            return score_from_junit(report.read_text())
        except (ET.ParseError, UnicodeDecodeError) as exc:
            # A hand-written runner is the likely author of an XML nobody can parse, and an
            # unhandled traceback here would abort the whole cohort's job rather than this
            # one submission. `UnicodeDecodeError` for the same reason: `run.sh` is written
            # by faculty in whatever language their course uses, and a latin-1 report is a
            # report we cannot read, not a run we may abandon a cohort over.
            log_err(f"  ! the test report is not valid XML ({exc}) - nothing scored")
            return None


# ------------------------------------------------- the grader's reading copy (opt-in)

# What a grader can be handed as a page rather than a repo. A notebook exports through
# nbconvert, which every seeded workflow already installs; an Rmd/qmd would need pandoc and
# an R toolchain that the runner has not got, so its FILTERED SOURCE is archived instead -
# still the thing worth reading, and honest about being source.
GRADER_DOCUMENTS = (".ipynb", ".rmd", ".qmd")
# What one export came to, in the words the summary line counts. Not a student-visible
# vocabulary - these appear only in the run log's totals and never beside a name.
GRADER_PDF = "pdf"
GRADER_HTML = "html"  # nbconvert's PDF path needs LaTeX; this is the fallback
# The engines nbconvert's `--to pdf` shells out to. A bare Actions runner has none of
# them, so the PDF leg fails for EVERY submission - and each failure costs a whole
# interpreter start and notebook render before the HTML leg does the work again. Asked
# once per run instead (`_pdf_engine_present`), which is what the exporter would ask.
_PDF_ENGINES = ("xelatex", "pdflatex", "lualatex")
GRADER_SOURCE = "filtered source"  # nothing in the runner can render this format
GRADER_NONE = "no marked questions"  # the assignment does not use the question fences
GRADER_TOO_BIG = "too large to archive"  # past ARCHIVE_MAX_BYTES, for the same reason
GRADER_UNREADABLE = "not readable"  # unclonable, unpinnable, or a malformed document
GRADER_UNWRITTEN = "not archived"  # produced, but the archive write failed


def pick_grader_document(
    workdir: Path,
) -> tuple[Path, Filtered] | None:
    """The one document in a checkout worth exporting for a grader, already filtered.

    The submission with the MOST fenced questions wins, ties broken by path, because a
    repo commonly holds several notebooks - a scratch copy, a provided demo, the answer -
    and the one carrying the questions is by definition the one being marked. A checkout
    where nothing carries a fence returns None, and the caller archives nothing.

    A document that will not parse is skipped rather than raised on: one broken notebook in
    a cohort must not cost the other hundred their grader copy."""
    best: tuple[Path, Filtered] | None = None
    for path in sorted(_walk_files(workdir)):
        if path.suffix.lower() not in GRADER_DOCUMENTS:
            continue
        if _CHECKPOINTS in path.relative_to(workdir).parts:
            continue  # Jupyter's own autosave, as `_completion_notebooks` also skips
        try:
            # Bounded before it is read: these are student-committed files, the read is
            # into the PARENT process (which no rlimit caps), and a document too big to
            # archive cannot produce an archivable export anyway.
            if path.stat().st_size > ARCHIVE_MAX_BYTES:
                continue
            filtered = filter_questions(path.name, path.read_text(errors="replace"))
        except (DeriveError, OSError):
            continue
        if filtered.questions and (
            best is None or filtered.questions > best[1].questions
        ):
            best = (path, filtered)
    return best


@cache
def _pdf_engine_present() -> bool:
    """Whether this runner can make a PDF at all - one `which` per run."""
    return any(shutil.which(engine) for engine in _PDF_ENGINES)


def _export_document(source: Path, env: dict) -> tuple[str, bytes]:
    """Render `source` for a grader: `(what it came to, the bytes to archive)`.

    PDF first, HTML second, the source itself last. nbconvert's PDF path shells out to
    LaTeX, which a bare Actions runner has not got - so the verdict is taken from whether
    the OUTPUT FILE appeared, never from the exit code, and a missing toolchain is a
    quieter answer rather than a red run. Nothing here executes the notebook: these are
    student submissions, and rendering one must not run it."""
    if source.suffix.lower() != ".ipynb" or _grader_dep_missing("nbconvert"):
        return GRADER_SOURCE, source.read_bytes()
    formats = (GRADER_PDF, GRADER_HTML) if _pdf_engine_present() else (GRADER_HTML,)
    for fmt in formats:
        out = source.with_suffix(f".{fmt}")
        out.unlink(missing_ok=True)
        _run_limited(
            [sys.executable, "-m", "jupyter", "nbconvert", "--to", fmt, str(source)],
            cwd=str(source.parent),
            env=env,
            timeout=RUN_TIMEOUT,
        )
        if out.is_file():
            return fmt, out.read_bytes()
    return GRADER_SOURCE, source.read_bytes()


def _grader_document_for(
    cohort_org: str,
    repo: str,
    target_key: str,
    slug: str,
    deadline: str,
    snapshot: str | None,
    env: dict,
) -> str:
    """Archive one submission's grader copy. Returns one of the GRADER_* verdicts."""
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "sub"
        if not clone(cohort_org, repo, wd):
            return GRADER_UNREADABLE
        if _pin_commit(wd, deadline, snapshot) is None:
            return GRADER_UNREADABLE
        # The same hardening the hidden tests and the completion check get, for the same
        # reason: `_export_document` runs a subprocess with this checkout as its cwd, so
        # the clone's stored credential and the student's own rigging files have to go
        # first. Rendering does not execute the notebook - but it does import a stack of
        # Python from a directory the student wrote.
        _harden_checkout(wd)
        picked = pick_grader_document(wd)
        if picked is None:
            return GRADER_NONE
        source, filtered = picked
        source.write_text(filtered.text)
        verdict, content = _export_document(source, env)
        if len(content) > ARCHIVE_MAX_BYTES:
            # The same cap the executed notebook gets, for the same reason: an HTML export
            # of a plot-heavy notebook is base64 PNG all the way down, and one per student
            # makes classroom-config a repo nobody can clone.
            return GRADER_TOO_BIG
        # The verdict for a rendered export IS its extension; only the fallback has to
        # ask the file what it is.
        suffix = (
            verdict
            if verdict in (GRADER_PDF, GRADER_HTML)
            else source.suffix.lstrip(".")
        )
        # `person=True`: the PATH is `autograde/<slug>/<handle>.pdf`, and this log is
        # world-readable even where classroom-config is not.
        if not put_file(
            cohort_org,
            CONFIG_REPO,
            f"{autograde_path(slug)}/{target_key}.{suffix}",
            content,
            f"autograde: {slug}/{target_key} grader copy",
            person=True,
        ):
            return GRADER_UNWRITTEN
        return verdict


def export_grader_documents(
    cohort_org: str,
    slug: str,
    key: str,
    is_group: bool,
    deadline: str,
    dry_run: bool,
) -> None:
    """Archive a reading copy of every submission, filtered to its hand-marked questions.

    Opt-in per assignment (`grader_pdf: true` in the template's `grading_config.yml`) and
    deliberately NOT behind `autograde:`: it is the questions a PERSON marks that the
    Otter `<!-- BEGIN QUESTION -->` fences delimit, so the assignment that wants this most
    is the all-manual one, which never reaches the autograder at all.

    Returns nothing, and that is the contract: a runner with no LaTeX, a submission with no
    fences, one unclonable repo - none of those is a reason to red the cutoff pass and
    re-run the whole freeze on the next tick. Every outcome is counted into one summary
    line and the run carries on. Counts only: the archive PATHS carry handles."""
    targets = submission_targets(cohort_org, slug, is_group, key)
    if not targets:
        return
    if dry_run:
        log(f"    DRY-RUN would archive {len(targets)} grader copy/copies for {slug}")
        return
    log_step(f"Grader copies for {slug}: {len(targets)} target(s)")
    snapshots = load_snapshots(cohort_org, slug)
    # `_export_document` runs `python -m jupyter` FROM the notebook's own directory (an
    # exporter resolves a document's relative assets from there), so it needs the same
    # sandbox environment the hidden tests and the completion check get.
    env = _sanitised_env()
    tally: dict[str, int] = {}
    for repo, target_key, _members in targets:
        verdict = _grader_document_for(
            cohort_org,
            repo,
            target_key,
            slug,
            deadline,
            None if snapshots is None else snapshots.get(repo),
            env,
        )
        tally[verdict] = tally.get(verdict, 0) + 1
    log_ok(
        f"{slug}: grader copies - "
        + ", ".join(f"{n} {what}" for what, n in sorted(tally.items()))
        + f" (in {autograde_path(slug)}/)"
    )


def _grade_target(
    cohort_org: str,
    repo: str,
    tests_src: Path | None,
    deadline: str,
    snapshot: str | None = None,
    *,
    starters: frozenset[str] | None = None,
) -> tuple[dict | None, bytes | None]:
    """Clone one submission, pin it to its snapshot (else the deadline), and examine it:
    the completion check where the assignment asked for one, then the hidden tests where
    there are any.

    Returns `(result, executed notebook)`. `result` is None for the ONE reason the caller
    treats as "never examined" - the repo could not be cloned; everything else comes back
    as a dict (a zero with a note for a non-submission or a failed run). The executed
    notebook is bytes to archive, or None where there is nothing to archive.

    `tests_src` None = this assignment is hand-marked and only the completion check runs;
    `starters` None = no completion check (else the blob shas of the handed-out starters,
    which is how an untouched notebook is recognised)."""
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "sub"
        if not clone(cohort_org, repo, wd):
            if repo_missing(cohort_org, repo):
                # GitHub SAYS the repo does not exist (deleted, or never provisioned) - a
                # recorded zero, NOT a transient failure. Returning None ('unreachable') would
                # hold the fire-once marker and re-clone + re-grade every OTHER repo hourly,
                # for ever, on an assignment that can never complete. Only a 404 counts:
                # `repo_exists` reads ANY failure as absent, and a clone hiccup followed by
                # one 5xx on the probe would write a permanent zero for a student who
                # submitted.
                log_err(
                    f"  ! {target_ref(repo)} does not exist - scoring 0 (no submission)"
                )
                return _zero_result("submission repo does not exist"), None
            log_err(f"  ! could not clone {target_ref(repo)} (transient - will retry)")
            return None, None
        sha = _pin_commit(wd, deadline, snapshot)
        if sha is None:
            zero = _zero_result(f"no submission on/before {deadline}")
            if starters is not None:
                # Nothing was pushed, so what is in the repo is the handout - which is
                # exactly what `not-attempted` says, and saying nothing here would leave
                # the one row a grader most wants to see blank.
                zero["completion"] = COMPLETION_NOT_ATTEMPTED
            return zero, None
        _harden_checkout(wd)
        executed = None
        completion = ""
        if starters is not None:
            # BEFORE the tests: `_run_tests` converts every notebook in the checkout to a
            # script, and the rule this verifies is about the notebook.
            completion, executed = _check_completion(wd, starters, Path(work))
        if tests_src is None:
            result = {}
        else:
            result = _run_tests(wd, tests_src) or _zero_result(GRADE_FAILED_NOTE)
        result["commit"] = sha
        if completion:
            result["completion"] = completion
        return result, executed


def refresh_assignment_sheet(
    master_org: str,
    template: str,
    cohort_org: str,
    *,
    group: bool = False,
    dry_run: bool = False,
    slug: str = "",
) -> int:
    """Bring one assignment's grading sheet up to date, without freezing anything.

    What the **Collect submissions** button does, and what the hourly refresh does for
    every assignment past its due date. The snapshot stays the cutoff's job: this only ever
    re-derives `info:`, so a grader who presses the button an hour after a late push sees
    it immediately instead of waiting for the tick.

    A sheet past its cutoff is sealed rather than refreshed - `sync_sheet` reads that off
    the sheet and the clock - so no press of this button can move a fact the freeze
    recorded, and one pressed over a cutoff that passed while nothing ran does the sealing
    the tick missed."""
    sched = schedule.load(cohort_org)
    # `slug` arrives as the SCHEDULE KEY (which of two entries handing out from this one
    # template) and is consumed here; from the next line on it means the cohort-side name.
    target = schedule.resolve_target(sched, template, slug)
    if isinstance(target, str):
        log_err(target)
        return 1
    key, slug = target
    gspec = load_grading_spec(master_org, template)
    is_group = resolve_is_group(force=group, template_type=gspec.type)
    ok = sync_sheet(
        master_org,
        cohort_org,
        sched,
        key,
        slug,
        template,
        is_group=is_group,
        now=datetime.now(schedule._tz(sched.timezone)),
        dry_run=dry_run,
    )
    return 0 if ok else 1


def _today_in_cohort_tz(sched: schedule.Schedule) -> str:
    """Today's date in the COHORT's timezone (schedule.yml `timezone`, default
    Europe/Berlin) - the last-resort grading pin for an unscheduled assignment. The
    Actions runner is UTC, so its own `date.today()` can be a day behind Berlin
    (00:00-02:00 local) and pin the grading to the wrong day."""
    return datetime.now(schedule._tz(sched.timezone)).date().isoformat()


def collect(
    master_org: str,
    template: str,
    cohort_org: str,
    deadline: str | None = None,
    group: bool = False,
    dry_run: bool = False,
    scheduled: bool = False,
    slug: str = "",
) -> int:
    """Examine every submission for `template` as of `deadline` - the hidden tests where
    the assignment asked to be autograded, the completion check where it asked for one -
    archiving what each run produced and recording the machine facts into the cohort's
    grading sheet (`info.autograde`, `info.completion`). Idempotent.

    `scheduled` marks the hourly cron: an assignment with no submission targets is then a
    "not yet", never the permanent not-machine-graded record a button press writes.

    `slug` names WHICH schedule entry this is, when two of them hand out from this one
    template. Left empty with two in the plan, this refuses: they keep separate snapshots,
    separate grading sheets and separate marks, and the freeze is write-once."""
    if master_org == cohort_org:
        log_err("master-org and cohort-org must differ.")
        return 1
    # The cohort-side identity is the SCHEDULE key when the assignment is scheduled
    # (the slug is a free label since course_source_repo), else the repo name minus its
    # tag. Everything cohort-side keys on it - snapshots, autograde markers, grades - and
    # the scheduler's fire-once marker uses the schedule key, so the two must agree or a
    # passed deadline re-grades every tick.
    sched = schedule.load(cohort_org)
    # As in `provision_all`: the parameter is the SCHEDULE KEY, consumed here, and `slug`
    # then means the cohort-side name for the rest of the run.
    target = schedule.resolve_target(sched, template, slug)
    if isinstance(target, str):
        log_err(target)
        return 1
    key, slug = target
    # The assignment's definition, read from the API (memoised) rather than from the clone
    # below, because the grading SHEET must be frozen at the cutoff on every path - and two
    # of them never reach a clone: a template with no solution branch, and an all-manual
    # assignment. Both are ordinary states, not failures, and both still have a deadline.
    # It is also what the cutoff itself is measured with (`late_window_days`).
    gspec = load_grading_spec(master_org, template)
    # SSOT: default the grading pin to the assignment's CUTOFF - an explicit
    # `grading_datetime`, else the due date plus the template's late window. An explicit
    # `deadline` (CLI override) wins; fall back to today - in the cohort's own timezone,
    # like every other date here - only if unscheduled.
    at = grades.cutoff_at(sched, key, gspec)
    deadline = (
        deadline or (at.isoformat() if at else None) or _today_in_cohort_tz(sched)
    )
    # Pin the deadline to an explicit instant in the COHORT's timezone, once, here: a bare
    # `--deadline 2026-11-15` means the end of the 15th where the students are, and every
    # consumer below (the commits API `until=`, git's `rev-list --before`, the log lines)
    # then reads the same moment instead of each defaulting to the runner's UTC.
    #
    # It also validates (raises on a non-ISO string). git's `rev-list --before` would
    # otherwise take an unparseable `--deadline` as an approxidate that silently matches
    # NOTHING, zeroing every submission in the cohort without a word.
    try:
        deadline = local_deadline(deadline, sched.timezone).isoformat()
    except ValueError:
        log_err(
            f"--deadline '{deadline}' is not an ISO date/datetime - refusing to grade "
            f"(git would silently match no commits and zero the whole cohort)"
        )
        return 1

    # group-vs-individual via the single `resolve_is_group` precedence (force -> the
    # template's grading_config.yml `type:` -> individual).
    is_group = resolve_is_group(force=group, template_type=gspec.type)
    cutoff = local_deadline(deadline, sched.timezone)

    # The grader's reading copy, when the assignment asks for one - BEFORE every autograde
    # exit below, and deliberately so. The fences it filters on delimit the questions a
    # PERSON marks, so an all-manual assignment (no solution branch, `autograde: false`,
    # no tests written) is exactly the one that wants it, and each of those returns from
    # this function a few lines further down. It never changes the exit code: see
    # `export_grader_documents`.
    # Whether student code may be run AT ALL, decided once and before anything clones a
    # submission. Every stage below executes the students' own code, and on this runner
    # that is only safe under a separate uid (see `sandbox_unusable`) - so a runner without
    # one grades nothing rather than grading a cohort as the process holding the PAT. The
    # reading-copy export is inside the fence too: it runs `python -m jupyter` from a
    # directory the student wrote.
    no_sandbox = sandbox_unusable()
    if gspec.grader_pdf and not no_sandbox:
        export_grader_documents(cohort_org, slug, key, is_group, deadline, dry_run)

    def freeze_sheet(
        counts: dict[str, str] | None = None,
        states: dict[str, str] | None = None,
    ) -> bool:
        """Seal the grading sheet: one last derivation, off the write-once snapshot, and
        `info:` is never touched again. Every path out of a passed cutoff runs it, because
        a sheet left OPEN after the deadline tells a grader marks can still move."""
        return sync_sheet(
            master_org,
            cohort_org,
            sched,
            key,
            slug,
            template,
            is_group=is_group,
            now=cutoff,
            autograde=counts,
            completion=states,
            dry_run=dry_run,
        )

    def sealed(
        counts: dict[str, str] | None = None,
        states: dict[str, str] | None = None,
    ) -> bool:
        """Freeze the sheet, and say whether this run may record anything permanent.

        A skip or graded record is FIRE-ONCE. Written over a sheet the freeze could not
        seal - a grader saving in the browser between the read and the compare-and-swap
        write, which is the exact minute graders are told they may type - it retires the
        assignment with its header still OPEN and its `info:` still provisional, and
        nothing ever re-derives them. So the marker waits for the seal, and the next tick
        does both."""
        if freeze_sheet(counts, states):
            return True
        log_err(
            f"{slug}: the grading sheet could not be sealed - recording nothing, so the "
            f"next run seals it and then records"
        )
        return False

    if no_sandbox:
        # FAIL CLOSED. The sheet is still frozen - the deadline passed either way - and the
        # skip is recorded like any other decision not to machine-mark, so the quarter-hour
        # cron does not re-decide it and no target is ever executed. Fix the runner and
        # delete `autograde/<slug>/` to grade it once.
        log_err(f"  ! {no_sandbox}")
        if not sealed():
            return 1
        return _record_skip(cohort_org, slug, no_sandbox, dry_run)

    with tempfile.TemporaryDirectory() as sd:
        soldir = Path(sd) / "sol"
        if not clone(master_org, template, soldir, branch=SOLUTION_BRANCH):
            log_err(
                f"no `{SOLUTION_BRANCH}` branch on {master_org}/{template} - no hidden "
                f"tests to run; nothing to collect."
            )
            # Hand-marked, then: say so once in the archive rather than re-deciding it
            # on every hourly tick (see FIRE-ONCE above). The sheet is still frozen - the
            # deadline passed whether or not anything machine-grades.
            if not sealed():
                return 1
            return _record_skip(
                cohort_org,
                slug,
                f"no `{SOLUTION_BRANCH}` branch on {master_org}/{template}",
                dry_run,
            )
        # WHAT this run does, decided once: hidden tests, a completion check, either,
        # both, or nothing at all. The two are independent - a hand-marked notebook
        # assignment still gets its completion check - so neither can exit early on the
        # other's behalf, and only "neither" is the hand-marked exit that records a skip.
        tests_src: Path | None = soldir / gspec.tests
        no_tests = ""
        if not gspec.autograde:
            no_tests = f"`autograde: false` in {GRADING_FILE}"
        elif not tests_src.is_dir():
            # An assignment that asked to be autograded and whose hidden tests were never
            # written. Reported rather than red: a fault the cron re-decides every quarter
            # of an hour is a fault nobody reads.
            log_err(f"  ! no `{gspec.tests}/` on the solution branch")
            no_tests = f"no `{gspec.tests}/` on the solution branch - hand-marked"
        if no_tests:
            tests_src = None

        starters: frozenset[str] | None = None
        no_completion = ""
        if gspec.runs_completion_check:
            # Every missing one named, not just the first probed: a runner missing both
            # should have to read the log once.
            if [dep for dep in COMPLETION_DEPS if _grader_dep_missing(dep)]:
                # A runner fault with one fix, said in words by `_grader_dep_missing`
                # rather than through a cohort of `did-not-run`. Recorded like any other
                # decision not to machine-mark, so the cron does not re-decide it hourly;
                # deleting `autograde/<slug>/` re-runs it once the runner is fixed.
                no_completion = COMPLETION_DEP_SKIP
            else:
                starters = _starter_notebook_shas(master_org, template)

        if tests_src is None and starters is None:
            log_ok(f"{slug}: hand-marked, nothing to collect.")
            if not sealed():
                return 1
            return _record_skip(
                cohort_org,
                slug,
                "; ".join(reason for reason in (no_tests, no_completion) if reason),
                dry_run,
            )

        # Targets: one per team (group) or one per onboarded student (individual). Repos
        # are named after the cohort-side `slug`; teams.csv is keyed on the schedule `key`.
        targets = submission_targets(cohort_org, slug, is_group, key)
        if not targets:
            # Nothing to grade at a passed deadline: a cohort with nobody onboarded, or a
            # group assignment whose teams.csv has no teams. On the cron path that is a "not
            # yet" - the skip record is fire-once, so writing it would retire the assignment
            # before anyone could submit. On a button press it is the operator's answer, and
            # left unrecorded the cron came back every hour and went red every hour.
            if scheduled:
                log(f"  [wait] {slug} - no submission targets as of {deadline}")
                return 0
            return _record_skip(
                cohort_org, slug, f"no submission targets as of {deadline}", dry_run
            )

        # Which commit each repo is graded at was frozen just after the deadline, at a
        # moment the server chose (see the module docstring). Without that file the pin
        # moves with every later push - say so loudly rather than silently.
        snapshots = load_snapshots(cohort_org, slug)
        if snapshots is None:
            log_err(
                f"  ! no {snapshot_path(slug)} for {slug} - pinning on committer dates, "
                f"which students control; late work backdated before {deadline} will pass"
            )

        log_step(
            f"Collecting {slug} in {cohort_org}: {len(targets)} "
            f"{'team(s)' if is_group else 'student(s)'} as of {deadline}"
        )

        # `{unit key: "passed/total"}` - what `info.autograde` shows a grader at the
        # cutoff. Per UNIT, not per member: a team is graded once, on one commit.
        scores: dict[str, str] = {}
        # `{unit key: "ran-clean" | "errors:N" | ...}` - `info.completion`, on exactly the
        # same terms, and kept apart from `scores` because an assignment can have one
        # without the other.
        completions: dict[str, str] = {}
        # The unit keys this run actually EXAMINED - one entry per target that came back
        # with a result, whether or not it produced a count. Not `len(archives)`: a target
        # can archive two files (its result JSON and its executed notebook).
        examined: list[str] = []
        # The per-target result archives are held here and written only AFTER the grading
        # sheet is durable (see below), with the `_graded.json` sentinel written last. Writing
        # archives mid-loop is what let an aborted run un-grade everyone back when bare
        # `autograde/<slug>/` existence was the marker; the explicit sentinel now decouples the
        # marker from any archive write, but the ordering is kept as defence in depth.
        archives: list[tuple[str, bytes, str]] = []
        # `_grade_target` returns None for one reason only: the submission repo could not be
        # cloned. That is the line between "examined, and there was nothing to grade" (a
        # recorded non-submission still comes back as a zero result) and "never examined" -
        # and the fire-once record below must never be written on the strength of the latter.
        unreachable: list[str] = []
        # Targets whose grading run itself broke (timeout / no report), as opposed to a genuine
        # non-submission. If that is EVERY graded target the fault is the runner, not the
        # cohort - see the systemic-failure guard below.
        failed_to_run: list[str] = []
        for repo, target_key, members in targets:
            log_step(target_ref(repo))
            if dry_run:
                if snapshots is None:
                    pin = f"<= {deadline}"
                elif repo in snapshots:
                    pin = f"snapshot {(snapshots[repo] or 'none')[:8]}"
                else:
                    pin = "no snapshot row -> zero"
                log(f"    DRY-RUN would grade {target_ref(repo)} (pin {pin})")
                continue
            if snapshots is not None and repo not in snapshots:
                # The snapshot file exists but never recorded THIS repo (provisioned after the
                # freeze?). Distinct from a missing file: do NOT silently drop to the
                # student-datable committer-date pin - score zero with a loud per-repo warning.
                log_err(
                    f"  ! {target_ref(repo)} has no row in {snapshot_path(slug)} - it was not part of "
                    f"the deadline freeze; scoring 0 rather than pinning on student-"
                    f"controlled commit dates"
                )
                result, executed = (
                    _zero_result(f"absent from {snapshot_path(slug)}"),
                    None,
                )
            else:
                result, executed = _grade_target(
                    cohort_org,
                    repo,
                    tests_src,
                    deadline,
                    snapshot=None if snapshots is None else snapshots[repo],
                    starters=starters,
                )
            if result is None:
                unreachable.append(repo)
                continue
            examined.append(target_key)
            if result.get("note") == GRADE_FAILED_NOTE:
                failed_to_run.append(repo)
            # The score and per-test detail go ONLY to the private archive: this log is
            # public, and the tag above is the only thing that may stand for the student here.
            result = {"repo": repo, "ref": target_ref(repo), **result}
            archives.append(
                (
                    f"{autograde_path(slug)}/{target_key}.json",
                    json.dumps(result, indent=2).encode(),
                    f"autograde: {slug}/{target_key}",
                )
            )
            if executed is not None:
                # The notebook as the toolkit ran it, beside the result JSON: `errors:3` is
                # a number, and the grader has to be able to see WHICH three. Capped,
                # because a notebook of plots is base64 all the way down and
                # classroom-config is a repo somebody has to clone. No path in this log -
                # it would name the handle.
                if len(executed) <= ARCHIVE_MAX_BYTES:
                    archives.append(
                        (
                            f"{autograde_path(slug)}/{target_key}.ipynb",
                            executed,
                            f"autograde: {slug}/{target_key} (executed notebook)",
                        )
                    )
                else:
                    log(
                        f"    (an executed notebook came to {len(executed) // 1024} KiB, "
                        f"over the {ARCHIVE_MAX_BYTES // 1024} KiB archive cap "
                        f"- the state is recorded, the copy is not)"
                    )
            # A count, shown to the grader for information - never a mark by itself, and
            # never a field a student sees. It reaches them, if at all, through whatever
            # the grader then types into `score_*`.
            #
            # Only a unit with someone to give the mark to counts as graded: a team whose
            # every handle was rejected by the roster allowlist has nobody, and recording
            # its count would make the assignment look graded when no student can receive
            # it. The per-target archive above is still written - the run DID examine it.
            if members:
                if tests_src is not None:
                    scores[target_key] = f"{result['score']}/{result['max']}"
                if result.get("completion"):
                    completions[target_key] = result["completion"]

        if dry_run:
            return 0
        recorded = scores or completions
        if not recorded and unreachable:
            # Nothing graded because nothing could be READ - a repo that is not there yet,
            # or an API having a bad afternoon. The run is genuinely unfinished, so it goes
            # red and no record is written: the next tick must be free to try again, and
            # one outage must never mark an assignment as permanently not-machine-graded.
            log_err(
                f"{slug}: none of the {len(unreachable)} submission repo(s) could be read "
                f"(tagged above) - nothing graded, and nothing recorded; the next run retries"
            )
            return 1
        if not recorded:
            # Every target WAS examined and none of them yielded a grade. Not a failure: the
            # snapshot is frozen, so an hourly retry would see exactly what this run saw and
            # go red for ever. Record the skip and stay green - a deliberate re-grade is
            # still a delete of autograde/<slug>/ away.
            log_ok(
                f"{slug}: nothing gradable across {len(targets)} target(s) - recording "
                f"the skip rather than retrying every hour."
            )
            if not sealed():
                return 1
            return _record_skip(
                cohort_org,
                slug,
                f"nothing gradable across {len(targets)} target(s) as of {deadline}",
                dry_run,
            )
        if failed_to_run and len(failed_to_run) == len(examined):
            # EVERY target that was examined failed to grade for the same class of reason -
            # a broken image, a missing dependency, an rlimit the runner can't satisfy. That
            # is a runner fault, not a cohort of non-submitters, so it is treated like the
            # unreachable case: nothing recorded, no sentinel, red run, next tick retries.
            # Recording it would write a whole cohort of write-once zeros and then lock them
            # in behind the fire-once marker.
            log_err(
                f"{slug}: all {len(failed_to_run)} graded target(s) came back "
                f"'{GRADE_FAILED_NOTE}' - a runner-wide failure, not a cohort of bad "
                f"submissions; nothing recorded, and NOT marking machine-graded"
            )
            return 1

        # Only if EVERY target was reachable does anything permanent get written. The
        # `_graded.json` sentinel below is the fire-once marker, so holding it back on any
        # unreachable repo keeps the assignment eligible for a retry: the next tick regrades
        # and picks up the repo(s) that could not be read.
        if unreachable:
            log_err(
                f"{slug}: examined {len(examined)} target(s), but {len(unreachable)} "
                f"submission repo(s) could not be read (tagged above) - NOT marking {slug} "
                f"machine-graded; the next run retries the missing one(s)"
            )
            return 1
        # A failed archive write must red the run and WITHHOLD the sentinel: the marker is now
        # a single explicit file, not the first archive's side effect, so partial detail can
        # never be mistaken for a completed grade. The next tick regrades - the sheet's
        # `info.autograde` is derived, never typed - and rewrites the missing archive(s)
        # and the sentinel.
        archive_ok = True
        for apath, acontent, amsg in archives:
            # `person=True`: the PATH is `autograde/<slug>/<handle>.json`, and this log is
            # world-readable even when classroom-config is not.
            if not put_file(
                cohort_org, CONFIG_REPO, apath, acontent, amsg, person=True
            ):
                log_person(f"    ! could not write {apath}")
                archive_ok = False
        # The grading sheet is the record, and freezing it is the LAST write before the
        # fire-once sentinel. Order matters both ways round: a sentinel written over an
        # unfrozen sheet would leave the cutoff's facts unrecorded for ever (nothing
        # re-derives them), and a frozen sheet with no sentinel is simply re-frozen next
        # tick to identical bytes, which writes nothing.
        if not (
            archive_ok and sealed(scores, completions) and mark_graded(cohort_org, slug)
        ):
            log_err(
                f"{slug}: examined {len(examined)} target(s) but a result archive, the grading "
                f"sheet or the fire-once sentinel failed to write - NOT marking "
                f"machine-graded; retrying"
            )
            return 1
    log_ok(
        f"examined {len(examined)} target(s) into {grades.sheet_path(slug)}: "
        f"{len(scores)} scored, {len(completions)} completion-checked; "
        f"{len(failed_to_run)} failed to run, {len(unreachable)} unreachable "
        f"(per-target detail in {autograde_path(slug)}/) - faculty mark in the sheet"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master-org", required=True, help="Course org (template source)"
    )
    parser.add_argument(
        "--course-source-repo",
        dest="template",
        required=True,
        help="Assignment template (e.g. assignment-1-f2026)",
    )
    parser.add_argument("--cohort-org", required=True, help="Cohort org (submissions)")
    parser.add_argument(
        "--deadline",
        default=None,
        help="ISO date override; default = the cohort schedule's grading deadline, else today",
    )
    parser.add_argument(
        "--group", action="store_true", help="Group assignment (one repo per team)"
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Refresh the grading sheet now and stop - no snapshot, no grading, no freeze",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="Which assignment in the cohort's schedule.yml this is, when two of them hand out from the same template (each with its own cohort_dest_repo). Leave empty otherwise.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.refresh_only:
        return refresh_assignment_sheet(
            args.master_org,
            args.template,
            args.cohort_org,
            group=args.group,
            dry_run=args.dry_run,
            slug=args.slug,
        )
    return collect(
        args.master_org,
        args.template,
        args.cohort_org,
        args.deadline,
        group=args.group,
        dry_run=args.dry_run,
        slug=args.slug,
    )


if __name__ == "__main__":
    sys.exit(main())
