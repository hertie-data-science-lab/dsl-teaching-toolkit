"""dsl-course collect -- faculty-side autograding (hidden tests, after the deadline).

Runs entirely in a faculty-controlled job (course-org Actions, bot token). For each
submission repo it checks out the commit that repo was frozen at (see SNAPSHOTS below),
overlays the assignment's HIDDEN tests (kept on the course template's `solution` branch,
never shipped to students), runs them, and records a machine score into the PRIVATE grades
CSV. Faculty & instructors then add manual marks and the existing grades pipeline emails
the result - so a student never sees a score in their own repo.

  course/<template> @ solution branch  ->  grading.yml + hidden tests
                |
  cohort/<slug>-<handle>  (individual)   clone @ snapshot, overlay tests, run
  cohort/<slug>-<team>    (group)              |
                v
  classroom-config/autograde/<slug>/<key>.json   (per-test detail, private archive)
  classroom-config/grades/<slug>.csv             (autograde_score + team filled)

Student code is run in a subprocess with the GitHub token stripped from the environment.

SNAPSHOTS (a server-timed FREEZE, not a server-timed deadline).  A git committer date is
entirely client-supplied (`GIT_COMMITTER_DATE`), so late work backdated to before the
deadline passes a `rev-list --before` pin. The hourly scheduler therefore freezes each
assignment shortly after its grading deadline, writing one row per submission repo into

    classroom-config/snapshots/<slug>.csv
        repo,sha,recorded_at,submitted_at,submitted_source

and never rewriting it. `submitted_at` is WHEN that pinned commit was made and
`submitted_source` says where that time came from - `commit` for the committer date the
API reports, which the student supplies and can backdate. Grading pins to the recorded sha; a blank sha means "nothing had
been pushed by the deadline" and scores zero. Only with no snapshot at all does grading
fall back to the date-based pin, loudly.

Only the MOMENT is server-timed, and it is easy to over-read what that buys. WHICH commit
the freeze chooses is still the last one whose committer date is on or before the deadline,
and that date is the student's to set - so a commit pushed after the deadline but before
the next hourly tick, backdated, is still picked up by that first snapshot. What the
snapshot closes is the UNBOUNDED window, not the hour before it. A chosen commit dated
after `recorded_at` needs a skewed or doctored clock, so `_snapshot_sha` says so. To
re-freeze deliberately, delete the snapshot CSV and let the next tick rebuild it.

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
longer be mistaken for a completed grade. Machine-written grade cells are write-once too (see
`grades.merge_auto`), so a marker's hand-edit is never clobbered. To re-grade deliberately,
delete `autograde/<slug>/` (the next tick regrades) or run the Grade assignment workflow -
and clear the `autograde_score` cells you want recomputed.

grading.yml (on the template's solution branch):
    type: individual        # or group
    autograde: true         # false -> skip (all-manual)
    tests: tests            # path on the solution branch holding the hidden tests

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
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import cache
from pathlib import Path

import yaml

from . import course, grades, roster, schedule, sync_teams, teams
from .course import (
    CONFIG_REPO,
    SOLUTION_BRANCH,
    assignment_slug,
    resolve_is_group,
    submission_repo,
)
from .fs import copy_tree
from .gh_contents import (
    blob_sha,
    dump_csv,
    file_exists,
    get_file_content,
    get_file_with_sha,
    is_untouched_stub,
    put_file,
)
from .ghcli import GIT_ENV, clone, gh, git, is_missing_resource
from .log import log, log_err, log_ok, log_person, log_skip, log_step
from .repos import repo_missing

AUTOGRADE_DIR = "autograde"  # classroom-config/autograde/<slug>/<key>.json
GRADED_RECORD = "_graded.json"  # fire-once sentinel: a successful run's LAST write
SKIP_RECORD = "_skipped.json"  # the same marker, for an assignment nothing grades
SNAPSHOT_DIR = "snapshots"  # classroom-config/snapshots/<slug>.csv
SNAPSHOT_FIELDS = ("repo", "sha", "recorded_at", "submitted_at", "submitted_source")
# Where a row's `submitted_at` came from. Only `commit` is written today: the committer
# date the commits API reports, which is the STUDENT's to set (`GIT_COMMITTER_DATE`) and
# can therefore be backdated. A server-timed rung (`push`, from the repository-activity
# API) comes later; recording the provenance now is what lets a marker - and the code -
# tell a client-supplied time from a server-observed one in a file that is never rewritten.
SUBMITTED_SOURCE_COMMIT = "commit"
GRADING_FILE = "grading.yml"  # on the template's solution branch
RUN_TIMEOUT = 300  # wall-clock seconds per graded subprocess
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

# The assignment's own definition. `type`/`autograde`/`tests` drive the autograder;
# everything below them drives the grading sheet - its shape, its maxima, its header - so a
# course states each fact once, in the file that already holds the others.
_DEFAULT_SPEC = {
    "type": "individual",
    "autograde": True,
    "tests": "tests",
    "title": "",
    "submit_via": "github",
    "questions": None,
    "late_window_days": None,
    "late_penalty_per_day": None,
}

SUBMIT_VIA = (
    "github",
    "external",
)  # `external` = handed in off GitHub (Moodle, Kaggle)


# --------------------------------------------------------------------------- pure core


def _one_of(value: object, allowed: tuple[str, ...], field: str, default: str) -> str:
    """A closed vocabulary, or the default with a warning. Never the raw value: an
    unrecognised `submit_via` would silently turn late arithmetic off for a cohort."""
    text = str(value or "").strip().lower()
    if text in allowed:
        return text
    log_err(
        f"  ! {GRADING_FILE}: `{field}: {value}` is not one of "
        f"{'/'.join(allowed)} - using `{default}`"
    )
    return default


def _questions(value: object) -> dict[str, str] | None:
    """`questions:` as {name: maximum AS TEXT}.

    Text, because the maxima are only ever DISPLAYED - beside each blank in the sheet, and
    in its header - and a course that writes `1.5` must read back what it wrote. Anything
    that is not a mapping is dropped with a warning rather than half-read."""
    if not isinstance(value, dict):
        log_err(
            f"  ! {GRADING_FILE}: `questions:` must be a mapping of name -> points - ignored"
        )
        return None
    questions = {
        str(name).strip(): ("" if points is None else str(points).strip())
        for name, points in value.items()
        if str(name).strip()
    }
    return questions or None


def _whole_days(value: object) -> int | None:
    """`late_window_days` as a whole number of days, or None with a warning."""
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        log_err(
            f"  ! {GRADING_FILE}: `late_window_days: {value}` is not a whole number of "
            f"days - ignored"
        )
        return None


def parse_grading_spec(text: str) -> dict:
    """Parse a grading.yml (missing keys fall back to defaults; extras ignored).

    A malformed VALUE is logged and dropped, never raised and never passed through: this
    file is hand-edited by faculty and read by an hourly cron, so one bad line costs the
    field it sits on and nothing else."""
    data = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        data = {}
    spec = dict(_DEFAULT_SPEC)
    spec.update({k: data[k] for k in ("type", "autograde", "tests") if k in data})
    if "title" in data:
        spec["title"] = str(data["title"] or "").strip()
    if "submit_via" in data:
        spec["submit_via"] = _one_of(
            data["submit_via"], SUBMIT_VIA, "submit_via", "github"
        )
    if "questions" in data:
        spec["questions"] = _questions(data["questions"])
    if "late_window_days" in data:
        spec["late_window_days"] = _whole_days(data["late_window_days"])
    if "late_penalty_per_day" in data:
        spec["late_penalty_per_day"] = (
            str(data["late_penalty_per_day"] or "").strip() or None
        )
    return spec


def template_is_group(master_org: str, template: str) -> bool:
    """Whether an assignment template declares itself group-provisioned: `type: group` in
    the grading.yml on its solution branch (written by the New assignment scaffold). No
    solution branch / no grading.yml means individual (the parse's default)."""
    text = get_file_content(master_org, template, GRADING_FILE, ref=SOLUTION_BRANCH)
    return parse_grading_spec(text or "")["type"] == "group"


def assignment_is_group(master_org: str, cohort_org: str, template: str) -> bool:
    """The one resolution of group-vs-individual every consumer (handout, grading) uses.

    Precedence (via `resolve_is_group`): the COHORT's own declaration -
    `assignments.<slug>.type` in classroom-config/schedule.yml - wins; else the template's
    design-time grading.yml `type:` (solution branch, written by the New assignment scaffold);
    else individual. Read-side only: the cohort setting never writes back into the course org's
    grading.yml - sources are read course-ward, state written cohort-ward. The template's
    grading.yml is read only when the cohort leaves `type` unset."""
    found = schedule.entry_for_repo(schedule.load(cohort_org), template)
    entry = found[1] if found else None
    schedule_type = entry.type if entry else None
    template_group = (
        None if schedule_type is not None else template_is_group(master_org, template)
    )
    return resolve_is_group(
        force=False, schedule_type=schedule_type, template_group=template_group
    )


def score_from_junit(xml_text: str) -> dict:
    """Turn a pytest junit XML report into the result.json contract {score, max, tests}.

    A case passes only if it has neither failure, error, nor skipped child element."""
    root = ET.fromstring(xml_text)
    if root.tag == "testsuite":
        suite = root
    else:
        nested = root.find("testsuite")
        suite = nested if nested is not None else root
    cases = [
        {
            "name": tc.get("name"),
            "passed": tc.find("failure") is None
            and tc.find("error") is None
            and tc.find("skipped") is None,
        }
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


def _sanitised_env() -> dict:
    """A copy of the environment with every GitHub token stripped - student code must
    never run with the bot token in scope."""
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "GH_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    # Caps glibc arena proliferation, which would otherwise reserve a heap arena per core and
    # push a legitimate multi-threaded run past RLIMIT_DATA_BYTES.
    env["MALLOC_ARENA_MAX"] = "2"
    return env


def submission_targets(
    cohort_org: str, slug: str, is_group: bool, teams_key: str | None = None
) -> list[tuple[str, str, list[str]]]:
    """The submission units for `slug` as (repo, key, members): one per team for a group
    assignment, one per onboarded student otherwise. Empty - with the reason logged - when
    there is nothing to grade.

    `slug` is the cohort-side NAME (`schedule.cohort_name`), which is what every repo here
    is named after. `teams_key` is the SCHEDULE KEY, which is what teams.csv is keyed on -
    the Join-team form validates the assignment against `assignments:` in schedule.yml and
    writes that key. The two differ whenever `cohort_dest_repo` is set, and looking teams
    up by the name then found none, so a group assignment silently had no targets at all.
    Defaults to `slug` for the (usual) case where they are the same.

    `is_group` is decided upstream by `resolve_is_group` (force -> schedule -> grading.yml)
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
        # Unvetted, a typo'd or invented handle earned a row of its OWN in the grades CSV -
        # the file faculty mark from and `render` fans out into per-student gradebooks -
        # for an account with no place in the cohort at all.
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
        return out
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
    return targets


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
        '(.[0].sha // "") + " " + (.[0].commit.committer.date // "")',
    )
    if code == 0:
        sha, _, committed = out.strip().partition(" ")
        if not sha:
            _warn_if_late_commits_only(cohort_org, repo, deadline)
            return Pin()  # the repo is reachable; no commit on/before the deadline
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


def _committed_after(committed: str, recorded_at: str) -> bool:
    """Whether `committed` (a commit's committer date) is later than `recorded_at` (the
    moment the freeze was taken). Both ISO; either missing or unparseable means no.

    GitHub answers `...Z` and `datetime.fromisoformat` only learnt to read that in 3.11,
    so the suffix is spelt out rather than left to the runner's Python version - the
    difference between a warning that fires and one that never does."""
    if not (committed and recorded_at):
        return False
    try:
        when = datetime.fromisoformat(committed.replace("Z", "+00:00"))
        taken = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when.tzinfo is not None and taken.tzinfo is not None and when > taken


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
    Grade assignment workflow."""
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


@cache
def _grading_text(course_org: str, template: str) -> str | None:
    """The template's grading.yml, read ONCE per template per process.

    An hourly tick asks the same template the same question from the scheduler, the sheet
    refresh and the collection that follows them. Memoising the TEXT (like
    `schedule._schedule_text`) means every caller still parses its own dict - nothing
    shared to mutate - and still sees its own warnings. tests/conftest.py clears it."""
    return get_file_content(course_org, template, GRADING_FILE, ref=SOLUTION_BRANCH)


def load_grading_spec(course_org: str, template: str) -> dict:
    """The assignment's definition from the course template's `solution` branch.

    NEVER raises: it sits under the hourly cron, and a template with no solution branch, no
    grading.yml, or one that does not parse must leave the rest of the tick running on the
    defaults rather than take the cohort down with it."""
    try:
        text = _grading_text(course_org, template)
    except RuntimeError as exc:
        log_err(f"  ! could not read {template}/{GRADING_FILE}: {exc}")
        return dict(_DEFAULT_SPEC)
    try:
        return parse_grading_spec(text or "")
    except yaml.YAMLError as exc:
        log_err(
            f"  ! {template}/{GRADING_FILE} is not valid YAML - using defaults: {exc}"
        )
        return dict(_DEFAULT_SPEC)


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
            # The pinned commit's committer date, recorded with WHERE it came from - the
            # student supplied it, so it is a claim, not an observation (see the module
            # docstring). The snapshot is write-once, so it is recorded now or never.
            rows.append(
                (repo, pin.sha, recorded_at, pin.committed, SUBMITTED_SOURCE_COMMIT)
            )
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


def _display_moment(at: datetime | None) -> str:
    """`Sun 4 Oct 2026 23:59` - a date a grader reads, not one a machine parses. Built by
    hand rather than with `%-d`, which is a glibc/BSD extension."""
    if at is None:
        return ""
    return f"{at:%a} {at.day} {at:%b} {at.year} {at:%H:%M}"


def _display_long(at: datetime | None, tz_name: str = "") -> str:
    """`Sunday 4 October 2026, 23:59 (Europe/Berlin)` - the form a STUDENT reads, once, in
    an issue they have to act on. The sheet's header uses the short form beside it: a
    grader scans that file rather than reading it."""
    if at is None:
        return ""
    zone = f" ({tz_name})" if tz_name else ""
    return f"{at:%A} {at.day} {at:%B %Y}, {at:%H:%M}{zone}"


def _cutoff_at(sched: schedule.Schedule, key: str, gspec: dict) -> datetime | None:
    """When this assignment stops accepting work: an explicit `grading_datetime`, else the
    due date plus the template's late window."""
    entry = sched.assignments.get(key)
    if entry is None:
        return None
    if entry.grading_datetime is not None:
        return entry.grading_datetime
    days = gspec.get("late_window_days")
    return entry.due_datetime + timedelta(days=days) if days else entry.due_datetime


def sheet_spec(
    sched: schedule.Schedule, key: str, slug: str, gspec: dict, is_group: bool
) -> grades.SheetSpec:
    """What the sheet needs to know about this assignment, gathered from the two files
    that own it: `grading.yml` on the template's solution branch, and the cohort's
    `schedule.yml`. Nothing here is written into the sheet as data - it reaches the grader
    as the comment header, which is regenerated on every write."""
    entry = sched.assignments.get(key)
    return grades.SheetSpec(
        slug=slug,
        title=gspec["title"] or (entry.title if entry else "") or slug,
        is_group=is_group,
        submit_external=gspec["submit_via"] == "external",
        questions=gspec["questions"],
        late_window_days=gspec["late_window_days"],
        late_penalty_per_day=gspec["late_penalty_per_day"],
        autograde=bool(gspec["autograde"]),
        due_display=_display_moment(entry.due_datetime if entry else None),
        cutoff_display=_display_moment(_cutoff_at(sched, key, gspec)),
        due_long=_display_long(entry.due_datetime if entry else None, sched.timezone),
        cutoff_long=_display_long(_cutoff_at(sched, key, gspec), sched.timezone),
    )


def _parse_iso(stamp: str) -> datetime | None:
    """An API timestamp as an aware datetime. GitHub answers `...Z`, which
    `datetime.fromisoformat` only learnt to read in 3.11."""
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def submitted_display(submitted_at: str, tz: str | None) -> str:
    """The pinned commit's time in the COHORT's own clock, to the minute -
    `2026-10-03T22:14+02:00`.

    To the MINUTE deliberately: with seconds it matches YAML 1.1's timestamp pattern, and
    `safe_load` would hand the next writer a `datetime` where the grader's file has a
    string - so the sheet would re-type its own field and churn a commit."""
    at = _parse_iso(submitted_at)
    if at is None:
        return ""
    return at.astimezone(schedule._tz(tz)).isoformat(timespec="minutes")


def days_late(submitted: datetime, due: datetime) -> int:
    """Whole days started past the due date, floored at 0. A minute late is one day late -
    the Hertie wording is "per day started", and rounding the other way would make the
    penalty a function of the hour a student pushed at."""
    seconds = (submitted - due).total_seconds()
    return int(-(-seconds // 86400)) if seconds > 0 else 0


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
) -> dict[str, dict]:
    """The toolkit's facts, per submission unit: when the pinned commit was made, how late
    that is, and (for a team) what CONTRIBUTIONS.md said at that commit."""
    out: dict[str, dict] = {}
    for repo, unit, _members in targets:
        sha, submitted_at = pins.get(repo, ("", ""))
        info: dict = {"submitted": None, "days_late": None}
        when = _parse_iso(submitted_at) if (sha and submitted_at) else None
        if when is not None:
            info["submitted"] = submitted_display(submitted_at, tz)
            info["days_late"] = days_late(when, due) if due is not None else None
        if is_group:
            info["contributions"] = _contributions(cohort_org, repo, sha)
        out[unit] = info
    return out


def _provisional_pins(
    cohort_org: str, targets: list[tuple[str, str, list[str]]], deadline: str
) -> dict[str, tuple[str, str]] | None:
    """Each repo's last commit on or before the cutoff, read WITHOUT writing a snapshot.

    The snapshot file stays write-once and stays the cutoff's job: these pins move with
    every push through the late window, which is the whole point of refreshing the sheet.
    None if any lookup failed - a half-read cohort must not rewrite the file."""
    pins: dict[str, tuple[str, str]] = {}
    for repo, _unit, _members in targets:
        pin = _snapshot_sha(cohort_org, repo, deadline)
        if pin is None:
            return None
        pins[repo] = (pin.sha, pin.committed)
    return pins


def _receipt_event(
    phase: SheetPhase, sha: str, was: object, now_shown: str
) -> str | None:
    """Which receipt this unit has earned since the last write, if any.

    The comparison is against what the SHEET last recorded, not against a marker we would
    have to store: `info.submitted` is already there, it is ours, and it moves exactly when
    a new commit is pinned. A push in the same minute as the last one is the one case it
    cannot see, and a duplicate receipt is worse than a missed one for a student who
    pushed twice in sixty seconds."""
    if phase is SheetPhase.FREEZING:
        return course.RECEIPT_FROZEN if sha else None
    if not was:
        return course.RECEIPT_DUE  # first time we have looked; says so either way
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
    for repo, unit, _members in targets:
        sha, submitted_at = pins.get(repo, ("", ""))
        when = _parse_iso(submitted_at) if (sha and submitted_at) else None
        if when is not None:
            # In the COHORT's clock, like everything else a student is shown: the API
            # answers UTC, and "pushed 20:14" for a 22:14 push reads as a bug.
            when = when.astimezone(schedule._tz(tz))
        shown = submitted_display(submitted_at, tz) if when is not None else ""
        was = ((previous.get(unit) or {}).get(grades.INFO_KEY) or {}).get("submitted")
        event = _receipt_event(phase, sha, was, shown)
        if event is None or (not changed and event == course.RECEIPT_UPDATED):
            continue
        body = grades.receipt(
            spec,
            event,
            sha=sha,
            pushed_display=_display_long(when),
            days=days_late(when, due) if (when is not None and due) else 0,
        )
        issue = grades.ensure_feedback_issue(
            cohort_org, repo, grades.feedback_body(spec), dry_run
        )
        if issue is None:
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
    line = f"OPEN - {submitted} of {total} submitted"
    if derived:
        line += "; late pushes still update `info:` until the cutoff."
    return line


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
    phase: SheetPhase = SheetPhase.OPEN,
    units: list[tuple[str, list[str]]] | None = None,
    autograde: dict[str, str] | None = None,
    receipts: bool = True,
    dry_run: bool = False,
) -> bool:
    """Write `grading_sheets/<slug>.yml` for this assignment, creating it if it is not
    there and leaving it exactly as it is when nothing has changed.

    ONE function for all three moments - handout, refresh, freeze - because they differ
    only in what `info:` may say. A separate creator would be a second definition of the
    sheet's shape, and the two would drift the first time a field was added.

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
    previous = (grades.parse_sheet(old_text) if old_text else {}).get(
        spec.container_key
    ) or {}

    derive = (
        bool(targets)
        and not spec.submit_external
        and (
            phase is SheetPhase.FREEZING
            or (phase is SheetPhase.OPEN and due is not None and now >= due)
        )
    )
    info_updates: dict[str, dict] = {}
    if derive:
        if phase is SheetPhase.FREEZING:
            rows = load_snapshot_rows(cohort_org, slug) or {}
            pins = {r: (row.sha, row.submitted_at) for r, row in rows.items()}
        else:
            pins = _provisional_pins(
                cohort_org, targets, (_cutoff_at(sched, key, gspec) or now).isoformat()
            )
            if pins is None:
                log_err(
                    f"  ! could not read every submission for {path} - not rewriting"
                )
                return False
        info_updates = _sheet_info(
            cohort_org, targets, pins, due, sched.timezone, is_group=is_group
        )
    for unit, count in (autograde or {}).items():
        info_updates.setdefault(unit, {})["autograde"] = count

    submitted = sum(1 for info in info_updates.values() if info.get("submitted"))
    status = _status_line(spec, phase, len(units), submitted, derive)
    sheet = grades.merge_sheet(
        {spec.container_key: previous} if previous else None,
        spec,
        units,
        info_updates,
        frozen=phase is SheetPhase.FROZEN,
    )
    content = grades.dump_sheet(sheet, spec, status).encode()
    changed = not (old_sha and blob_sha(content) == old_sha)
    written = True
    if not changed:
        log_skip(f"{path} (unchanged)")
    elif dry_run:
        log(f"    DRY-RUN  {path} ({status})")
    else:
        # The message carries counts, never a handle or a team name: classroom-config is
        # private, but its commit messages are quoted back in public run logs.
        written = put_file(
            cohort_org, CONFIG_REPO, path, content, f"grading sheet: {slug} - {status}"
        )
        if written:
            log_ok(f"{path}: {status}")
    # AFTER the record: a receipt promises something the sheet is supposed to hold, so the
    # sheet lands first. An unchanged tick posts nothing new to say - only the once-only
    # `due` and `frozen` events, which have to fire whether or not a pin moved (a student
    # who never submitted has an unchanging sheet and is owed both).
    if derive and receipts:
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


def _run_limited(argv: list[str], *, cwd: str, env: dict, timeout: int) -> bool:
    """Run `argv` in its OWN session/process group under `_apply_rlimits`. Returns True if it
    exited on its own (ANY exit code - a non-zero pytest run is still a valid grading result),
    False if it blew the wall-clock `timeout` and the whole group was killed.

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
    proc = subprocess.Popen(
        argv,
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
            # leaving the original group's workers (the fork bomb) alive.
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()  # group already gone / kill not permitted - fall back to the child
        proc.wait()  # reap the (killed) group leader so it isn't left a zombie
        return False


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
    env = _sanitised_env()
    # Keep cwd/'' off sys.path so a committed `pytest.py`/`sitecustomize.py` can't shadow real
    # modules. The submission is NOT put on PYTHONPATH: every PYTHONPATH entry precedes the
    # stdlib, so a student `json.py`/`operator.py` there would shadow a module the hidden tests
    # import and let them force assertions. Instead the trusted hidden-tests conftest appends
    # the submission to sys.path AFTER the stdlib (see the injection below), so a real module
    # always wins the import while the submission's own uniquely-named module still resolves.
    env["PYTHONSAFEPATH"] = "1"
    # The clone persists the bot credential in `.git/config`; env-stripping doesn't reach it,
    # and student code runs next and can read the workspace - so drop `.git` first (fix 10).
    shutil.rmtree(workdir / ".git", ignore_errors=True)
    _strip_student_test_rigging(workdir)
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
        if _grader_dep_missing("pytest"):
            return None
        report = Path(run) / "report.xml"
        completed = _run_limited(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--confcutdir={tests_dir}",
                str(tests_dir),
                f"--junitxml={report}",
            ],
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
        )
        if not completed:
            log_err(
                f"  ! grading timed out after {RUN_TIMEOUT}s (process group killed)"
            )
            return None
        if not report.exists():
            return None
        return score_from_junit(report.read_text())


def _grade_target(
    cohort_org: str,
    repo: str,
    spec: dict,
    tests_src: Path,
    deadline: str,
    snapshot: str | None = None,
) -> dict | None:
    """Clone one submission, pin it to its snapshot (else the deadline), run the hidden
    tests. Always returns a result dict (a zero with a note for non-submissions /
    failures), or None if unclonable."""
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
                return _zero_result("submission repo does not exist")
            log_err(f"  ! could not clone {target_ref(repo)} (transient - will retry)")
            return None
        sha = _pin_commit(wd, deadline, snapshot)
        if sha is None:
            return _zero_result(f"no submission on/before {deadline}")
        result = _run_tests(wd, tests_src)
        if result is None:
            return _zero_result(GRADE_FAILED_NOTE)
        result["commit"] = sha
        return result


def refresh_assignment_sheet(
    master_org: str,
    template: str,
    cohort_org: str,
    now: datetime | None = None,
    group: bool = False,
    dry_run: bool = False,
) -> int:
    """Bring one assignment's grading sheet up to date, without freezing anything.

    What the **Collect submissions** button does, and what the hourly refresh does for
    every assignment past its due date. The snapshot stays the cutoff's job: this only ever
    re-derives `info:`, so a grader who presses the button an hour after a late push sees
    it immediately instead of waiting for the tick.

    A sheet whose snapshot already exists is past its cutoff and is written in the FROZEN
    phase - the facts it holds are the ones the freeze recorded, and no later press may
    move them."""
    sched = schedule.load(cohort_org)
    found = schedule.entry_for_repo(sched, template)
    key = found[0] if found else assignment_slug(template)
    slug = schedule.cohort_name(*found) if found else key
    entry = found[1] if found else None
    gspec = load_grading_spec(master_org, template)
    is_group = resolve_is_group(
        force=group,
        schedule_type=entry.type if entry else None,
        template_group=gspec["type"] == "group",
    )
    ok = sync_sheet(
        master_org,
        cohort_org,
        sched,
        key,
        slug,
        template,
        is_group=is_group,
        now=now or datetime.now(schedule._tz(sched.timezone)),
        phase=(
            SheetPhase.FROZEN
            if load_snapshots(cohort_org, slug) is not None
            else SheetPhase.OPEN
        ),
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
) -> int:
    """Autograde every submission for `template` as of `deadline`, archiving result.json and
    recording the machine score into the cohort's private grades CSV. Idempotent.

    `scheduled` marks the hourly cron: an assignment with no submission targets is then a
    "not yet", never the permanent not-machine-graded record a button press writes."""
    if master_org == cohort_org:
        log_err("master-org and cohort-org must differ.")
        return 1
    # The cohort-side identity is the SCHEDULE key when the assignment is scheduled
    # (the slug is a free label since course_source_repo), else the repo name minus its
    # tag. Everything cohort-side keys on it - snapshots, autograde markers, grades - and
    # the scheduler's fire-once marker uses the schedule key, so the two must agree or a
    # passed deadline re-grades every tick.
    sched = schedule.load(cohort_org)
    found = schedule.entry_for_repo(sched, template)
    key = found[0] if found else assignment_slug(template)
    slug = schedule.cohort_name(*found) if found else key
    # SSOT: default the grading pin to the cohort schedule's grading deadline (explicit
    # `grading_datetime`, else `due_datetime`); an explicit `deadline` (CLI override)
    # wins; fall back to today - in the cohort's own timezone, like every other date here -
    # only if unscheduled.
    deadline = (
        deadline
        or schedule.grading_datetime_iso(sched, key)
        or _today_in_cohort_tz(sched)
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

    # The assignment's definition, read from the API (memoised) rather than from the clone
    # below, because the grading SHEET must be frozen at the cutoff on every path - and two
    # of them never reach a clone: a template with no solution branch, and an all-manual
    # assignment. Both are ordinary states, not failures, and both still have a deadline.
    gspec = load_grading_spec(master_org, template)
    entry = found[1] if found else None
    # group-vs-individual via the single `resolve_is_group` precedence (force -> cohort
    # schedule `type:` -> template grading.yml -> individual). The entry is the one found
    # above by course_source_repo - `slug` is the cohort-side NAME, which is
    # `cohort_dest_repo` when that is set and so is not a key into `sched.assignments`.
    is_group = resolve_is_group(
        force=group,
        schedule_type=entry.type if entry else None,
        template_group=gspec["type"] == "group",
    )
    cutoff = local_deadline(deadline, sched.timezone)

    def freeze_sheet(counts: dict[str, str] | None = None) -> bool:
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
            phase=SheetPhase.FREEZING,
            autograde=counts,
            dry_run=dry_run,
        )

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
            freeze_sheet()
            return _record_skip(
                cohort_org,
                slug,
                f"no `{SOLUTION_BRANCH}` branch on {master_org}/{template}",
                dry_run,
            )
        spec_path = soldir / GRADING_FILE
        spec = parse_grading_spec(spec_path.read_text() if spec_path.is_file() else "")
        if not spec["autograde"]:
            log_ok(
                f"{slug}: autograde disabled in {GRADING_FILE} - all-manual, nothing to collect."
            )
            freeze_sheet()
            return _record_skip(
                cohort_org, slug, f"`autograde: false` in {GRADING_FILE}", dry_run
            )
        tests_src = soldir / str(spec["tests"])
        if not tests_src.is_dir():
            log_err(
                f"{slug}: tests path `{spec['tests']}` not found on the solution branch."
            )
            return 1

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
        # The per-target result archives are held here and written only AFTER the grades CSV
        # is durable (see below), with the `_graded.json` sentinel written last of all. Writing
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
                result = _zero_result(f"absent from {snapshot_path(slug)}")
            else:
                result = _grade_target(
                    cohort_org,
                    repo,
                    spec,
                    tests_src,
                    deadline,
                    snapshot=None if snapshots is None else snapshots[repo],
                )
            if result is None:
                unreachable.append(repo)
                continue
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
            # A count, shown to the grader for information - never a mark by itself, and
            # never a field a student sees. It reaches them, if at all, through whatever
            # the grader then types into `score_*`.
            #
            # Only a unit with someone to give the mark to counts as graded: a team whose
            # every handle was rejected by the roster allowlist has nobody, and recording
            # its count would make the assignment look graded when no student can receive
            # it. The per-target archive above is still written - the run DID examine it.
            if members:
                scores[target_key] = f"{result['score']}/{result['max']}"

        if dry_run:
            return 0
        if not scores and unreachable:
            # Nothing graded because nothing could be READ - a repo that is not there yet,
            # or an API having a bad afternoon. The run is genuinely unfinished, so it goes
            # red and no record is written: the next tick must be free to try again, and
            # one outage must never mark an assignment as permanently not-machine-graded.
            log_err(
                f"{slug}: none of the {len(unreachable)} submission repo(s) could be read "
                f"(tagged above) - nothing graded, and nothing recorded; the next run retries"
            )
            return 1
        if not scores:
            # Every target WAS examined and none of them yielded a grade. Not a failure: the
            # snapshot is frozen, so an hourly retry would see exactly what this run saw and
            # go red for ever. Record the skip and stay green - a deliberate re-grade is
            # still a delete of autograde/<slug>/ away.
            log_ok(
                f"{slug}: nothing gradable across {len(targets)} target(s) - recording "
                f"the skip rather than retrying every hour."
            )
            return _record_skip(
                cohort_org,
                slug,
                f"nothing gradable across {len(targets)} target(s) as of {deadline}",
                dry_run,
            )
        if failed_to_run and len(failed_to_run) == len(archives):
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
                f"{slug}: graded {len(scores)} target(s), but {len(unreachable)} "
                f"submission repo(s) could not be read (tagged above) - NOT marking {slug} "
                f"machine-graded; the next run retries the missing one(s)"
            )
            return 1
        # A failed archive write must red the run and WITHHOLD the sentinel: the marker is now
        # a single explicit file, not the first archive's side effect, so partial detail can
        # never be mistaken for a completed grade. The next tick regrades (merge_auto leaves the
        # recorded scores untouched) and rewrites the missing archive(s) + the sentinel.
        archive_ok = True
        for apath, acontent, amsg in archives:
            if not put_file(cohort_org, CONFIG_REPO, apath, acontent, amsg):
                log_err(f"could not write {apath}")
                archive_ok = False
        # The grading sheet is the record, and freezing it is the LAST write before the
        # fire-once sentinel. Order matters both ways round: a sentinel written over an
        # unfrozen sheet would leave the cutoff's facts unrecorded for ever (nothing
        # re-derives them), and a frozen sheet with no sentinel is simply re-frozen next
        # tick to identical bytes, which writes nothing.
        if not (archive_ok and freeze_sheet(scores) and mark_graded(cohort_org, slug)):
            log_err(
                f"{slug}: graded {len(scores)} target(s) but a result archive, the grading "
                f"sheet or the fire-once sentinel failed to write - NOT marking "
                f"machine-graded; retrying"
            )
            return 1
    log_ok(
        f"graded {len(scores)} target(s) into {grades.sheet_path(slug)}; "
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.refresh_only:
        return refresh_assignment_sheet(
            args.master_org,
            args.template,
            args.cohort_org,
            group=args.group,
            dry_run=args.dry_run,
        )
    return collect(
        args.master_org,
        args.template,
        args.cohort_org,
        args.deadline,
        group=args.group,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
