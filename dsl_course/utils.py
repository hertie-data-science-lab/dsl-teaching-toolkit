"""Shared utilities for dsl_course tools."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import date, datetime
from fnmatch import fnmatch
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml

RATE_LIMIT_MARKERS = (
    "secondary rate limit",
    "api rate limit exceeded",
    "abuse detection",
)

# Per-call ceiling for a single `gh` subprocess. A hung TLS connection would otherwise
# block the whole Actions job until GitHub's 6-hour limit; a timeout is treated as a
# retryable failure within the retry ladder below.
GH_TIMEOUT_SECONDS = 120


def gh(*args: str, stdin: str | None = None, retries: int = 3) -> tuple[int, str]:
    """Run a gh CLI command. Returns (returncode, stdout+stderr).

    Retries on GitHub secondary rate limits - and on a subprocess timeout - with
    exponential backoff.
    """
    import time

    delay = 30
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["gh"] + list(args),
                capture_output=True,
                check=False,
                text=True,
                input=stdin,
                timeout=GH_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            out = f"gh: timed out after {GH_TIMEOUT_SECONDS}s"
            if attempt == retries:
                return 1, out
            print(
                f"  [wait] {out}, retry {attempt + 1}/{retries} in {delay}s",
                flush=True,
            )
            time.sleep(delay)
            delay *= 2
            continue
        out = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return result.returncode, out
        lower = out.lower()
        is_rate_limited = any(m in lower for m in RATE_LIMIT_MARKERS)
        if not is_rate_limited or attempt == retries:
            return result.returncode, out
        print(
            f"  [wait] rate-limited, retry {attempt + 1}/{retries} in {delay}s",
            flush=True,
        )
        time.sleep(delay)
        delay *= 2
    # Only reachable with a negative `retries` (the loop never runs); callers unpack a
    # pair, so hand back a failure rather than None.
    return 1, "gh: not run (retries < 0)"


def gh_json(*args: str) -> Any:
    """Run a gh CLI command and parse JSON stdout. Raises on failure."""
    result = subprocess.run(
        ["gh"] + list(args),
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh {' '.join(args)}` failed (exit {result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
    return json.loads(result.stdout)


# GitHub usernames: 1-39 chars, ASCII alphanumerics or single hyphens, no leading/
# trailing hyphen and no consecutive hyphens. Used to reject a typo'd faculty handle
# before it is invited as a stranger.
_GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def is_valid_github_username(handle: str) -> bool:
    """Whether `handle` is a syntactically valid GitHub username (charset/length only -
    not whether the account exists)."""
    return bool(_GITHUB_USERNAME_RE.match(handle))


def require_csv_header(
    fieldnames: list[str] | None, required: tuple[str, ...], what: str
) -> None:
    """Refuse a CSV whose header lacks a column the caller cannot do without.

    The failure this exists for: a German-locale Excel saves `;`-delimited CSV. DictReader
    then sees ONE header column, every field reads "", nothing raises, and the caller
    proceeds on an empty roster / empty marks - `enrol_codes` even wrote such a file back
    mangled, exit 0. A header that cannot name the required columns is a hard error."""
    have = {f.strip() for f in (fieldnames or [])}
    missing = [f for f in required if f not in have]
    if missing:
        raise RuntimeError(
            f"{what}: header lacks {', '.join(missing)} (got {list(fieldnames or [])}). "
            f"A semicolon-delimited export looks like this - save the file as "
            f"comma-separated UTF-8 CSV and try again."
        )


def strip_bom(text: str) -> str:
    """Drop a leading UTF-8 BOM. Excel exports CSVs with one, and left in place
    `csv.DictReader` reads it into the first header name so every lookup on that column
    misses and rows are silently dropped."""
    return text.lstrip("﻿")


# Per-call ceiling for a single `git` subprocess, the sibling of GH_TIMEOUT_SECONDS.
# Larger, because these are clones and pushes of whole materials repos rather than one API
# call - but bounded, because an unauthenticated remote that decides to prompt, or a hung
# TLS connection, otherwise blocks the job until GitHub's 6-hour limit kills it with no
# message anyone can act on.
GIT_TIMEOUT_SECONDS = 600


def git(*args: str, cwd: str | None = None) -> tuple[int, str]:
    """Run a git command. A timeout comes back as a normal failure pair, so every caller's
    existing `!= 0` check reports it rather than seeing an exception."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True,
            check=False,
            text=True,
            cwd=cwd,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 1, f"git: timed out after {GIT_TIMEOUT_SECONDS}s"
    return result.returncode, (result.stdout + result.stderr).strip()


# The faculty-only heading in the materials README that `scaffold` seeds. `deploy` refuses
# to release a README still containing it, so the sentinel is declared ONCE here - the
# writer and the guard both import it, and neither can lapse when the wording is edited.
# Here rather than in `scaffold` so that `deploy`, which the scheduler calls, does not pull
# the whole scaffold/seed chain in for one string.
FACULTY_ONLY_HEADING = "delete this section before releasing the README"

# Bot identity + disabled hooks for engine-made commits. Spread into git() calls in the
# clone/commit/push paths of release/site/scaffold/assign: git("-C", wd, *GIT_ENV, ...).
GIT_ENV = [
    "-c",
    "user.email=bot@dsl.local",
    "-c",
    "user.name=dsl-bot",
    "-c",
    "core.hooksPath=/dev/null",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def log_step(msg: str) -> None:
    print(f"\n-> {msg}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  [ok] {msg}", flush=True)


def log_skip(msg: str) -> None:
    print(f"  [skip] {msg} (already exists)", flush=True)


def log_err(msg: str) -> None:
    print(f"  [err] {msg}", file=sys.stderr, flush=True)


def log_verbose(msg: str) -> None:
    """Print `msg` only when `DSL_VERBOSE` is set in the environment.

    Every faculty workflow runs in the course org's PUBLIC `.github`, so its Actions log is
    world-readable - and a line naming one student's handle, their `<slug>-<handle>` repo,
    or a team's roster publishes who is in the cohort and who is grouped with whom. Those
    lines are INFORMATIONAL; what a faculty member actually reads is the aggregate
    `Done - {...}` summary, which stays. So they are routed through here: printed when
    someone runs the CLI locally with `DSL_VERBOSE=1`, absent from every workflow, because
    no rendered workflow sets the variable (a test enforces that).

    An ERROR a faculty member must act on keeps its handle and stays on `log_err` - those
    are rare, and unactionable without saying who."""
    if os.environ.get("DSL_VERBOSE"):
        print(msg, flush=True)


def repo_missing(org: str, name: str) -> bool:
    """Whether GitHub positively says the repo is NOT there (a 404). The shape for a
    caller about to record something permanent on the strength of absence: a 5xx or a
    rate limit is neither present nor absent, and must read as "could not tell"."""
    code, out = gh("api", f"repos/{org}/{name}")
    return code != 0 and is_missing_resource(out)


def repo_exists(org: str, name: str) -> bool:
    """Whether the repo is there. OPTIMISTIC: any read failure reads as absent, because
    this answers a create-if-missing question where guessing wrong costs a retry.

    Its neighbour `org_exists` is deliberately the opposite shape - it raises rather than
    call an unreadable org deleted - because its callers act destructively on a False.
    Reach for that one whenever absence is going to remove something."""
    code, _ = gh("api", f"repos/{org}/{name}")
    return code == 0


def org_exists(org: str) -> bool:
    """Whether `org` is still a live GitHub org.

    The liveness half of discovery, and the ONLY evidence an org is gone that anything
    here may act on. The topic search behind the inventory is eventually consistent, and
    generously so: a deleted org kept coming back from `gh search repos topic:dsl-cohort`
    for ten days after the org itself was gone. The search says what is INDEXED; this says
    what is THERE.

    Fails CLOSED, which is the whole point - only an unambiguous 404 is absence. A 403, a
    5xx, a rate limit or a timeout all mean "could not tell", and both callers act
    destructively on a False (a row dropped from a generated page, a cohort unregistered
    from every nightly sync). Reading "could not tell" as "deleted" would do that on any
    transient failure, so it raises instead. `repo_exists` above is deliberately the
    opposite shape: it answers a cheap should-I-create question where a wrong guess costs
    a retry, not a deletion.

    Even the 404 is weaker evidence than it looks: GitHub answers 404, not 403, for an org
    the TOKEN cannot see, so a bot removed from one org - or running on a rotated token
    that was never re-invited - reads identically to a deleted org. False therefore means
    "not visible to this token", and a caller that acts destructively on it needs more
    than one look (see seed._live_cohorts, which requires two consecutive misses)."""
    code, out = gh("api", f"orgs/{org}", "--jq", ".login")
    if code == 0:
        return True
    if is_missing_resource(out):
        return False
    raise RuntimeError(
        f"could not determine whether the org `{org}` still exists: {out[:200]}"
    )


def repo_is_private(org: str, name: str) -> bool:
    """Return True if the repo is private (assume private if the check fails)."""
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".private")
    return out.strip() != "false" if code == 0 else True


def repo_is_archived(org: str, name: str) -> bool:
    """Return True if the repo is archived (assume LIVE if the check fails).

    Archived repos are read-only - every write 403s. The optimistic default is deliberate:
    a transient API failure must not silently skip a live cohort's refresh. Guess wrong
    that way and the write itself fails loudly, which is the outcome we want.
    """
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".archived")
    return out.strip() == "true" if code == 0 else False


def get_default_branch(org: str, name: str) -> str:
    """Return the default branch of a repo. Falls back to 'main'."""
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".default_branch")
    if code == 0 and out:
        return out
    return "main"


@cache
def default_branch(org: str, name: str) -> str:
    """The default branch, RAISING if it cannot be read - and cached per repo.

    The fail-loud twin of get_default_branch, for writers. Guessing "main" is the right
    default for a reader that would otherwise just find nothing; for a write it aims a
    commit at a branch that may not be the one that exists, so put_files would rather fail
    than land work somewhere nobody is looking.

    Cached because a repo's default branch cannot change mid-run, and one run commits to
    the same repo more than once (a cohort's classroom-config takes both a contract and a
    samples commit; an org's `.github` takes both a workflows and a READMEs commit).
    functools.cache does not memoise a raised exception, so a transient failure is retried
    rather than pinned for the life of the process."""
    code, out = gh("api", f"repos/{org}/{name}", "--jq", ".default_branch")
    if code != 0 or not out.strip():
        raise RuntimeError(f"could not read {org}/{name}'s default branch: {out[:200]}")
    return out.strip()


def create_team(
    org: str, name: str, description: str = "", privacy: str = "closed"
) -> bool:
    """Create a team. Idempotent - treats a duplicate-name 422 as success.
    Returns True if a team with this name now exists.
    """
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"orgs/{org}/teams",
        "--field",
        f"name={name}",
        "--field",
        f"description={description}",
        "--field",
        f"privacy={privacy}",
    )
    if code == 0:
        log_ok(f"team created: {name}")
        return True
    # Only a genuine duplicate-name 422 is success. A bare `"422" in out` also swallowed
    # an invalid-name or policy/plan 422 as success, so a caller would then write into a
    # team that was never created. Key on the message text, and on all three spellings
    # GitHub uses: "already exists", the JSON `already_exists` error code, and - what the
    # teams endpoint actually returns - "Name must be unique for this org". Missing that
    # last one hard-failed every membership sync after a team's first creation.
    lower = out.lower()
    if any(
        phrase in lower
        for phrase in ("already exists", "already_exists", "must be unique")
    ):
        log_skip(f"team {name}")
        return True
    log_err(f"failed to create team {name}: {out[:200]}")
    return False


def org_membership_state(org: str, login: str) -> str | None:
    """Return '<state> (<role>)' for a current/pending member, else None."""
    code, out = gh(
        "api", f"orgs/{org}/memberships/{login}", "--jq", '"\\(.state) (\\(.role))"'
    )
    return out if code == 0 and out else None


def set_org_membership(org: str, login: str, role: str = "member") -> bool:
    """Ensure `login` belongs to `org` (invites if needed). Idempotent.

    If already a member/owner, leaves them as-is (never demotes an owner - that 403s).
    Returns True on success or graceful skip (e.g. a non-existent demo handle).
    """
    current = org_membership_state(org, login)
    if current:
        log_verbose(f"  [skip] org membership {login} ({current})")
        return True
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"orgs/{org}/memberships/{login}",
        "--field",
        f"role={role}",
    )
    if code == 0:
        log_verbose(f"  [ok] invited {login} to {org}")
        return True
    log_err(f"could not invite {login} (not a real account?): {out[:120]}")
    return False


def add_team_member(org: str, team_slug: str, login: str, role: str = "member") -> bool:
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"orgs/{org}/teams/{team_slug}/memberships/{login}",
        "--field",
        f"role={role}",
    )
    if code == 0:
        return True
    log_err(f"failed to add {login} to {team_slug}: {out[:100]}")
    return False


def get_team_members(org: str, team_slug: str) -> set[str] | None:
    """Current members of a team, or None if the listing could not be read.

    None (non-zero exit OR unparseable JSON) means "couldn't read" and must never be
    conflated with an empty team: reconciling against an unreadable team would add or
    prune blind. Mirrors get_org_owners."""
    code, out = gh(
        "api", f"orgs/{org}/teams/{team_slug}/members?per_page=100", "--paginate"
    )
    if code != 0:
        log_err(f"could not read the members of {org}/{team_slug}: {out[:200]}")
        return None
    try:
        return {m["login"] for m in json.loads(out)}
    except (json.JSONDecodeError, KeyError, TypeError):
        log_err(f"unparseable member listing for {org}/{team_slug}: {out[:200]}")
        return None


def remove_team_member(org: str, team_slug: str, login: str) -> bool:
    code, _ = gh(
        "api", "--method", "DELETE", f"orgs/{org}/teams/{team_slug}/memberships/{login}"
    )
    return code == 0


@lru_cache(maxsize=1)
def _acting_login() -> str | None:
    """Login of the token `gh` is currently authenticated as (the bot, in CI)."""
    code, out = gh("api", "user", "--jq", ".login")
    return out.strip() if code == 0 and out.strip() else None


@cache
def get_org_owners(org: str) -> frozenset[str] | None:
    """Active Owners of `org` - see reconcile_team_members for why these are never
    pruned from any team.

    None means the list could not be read (an empty frozenset means the org genuinely
    has no owners). The distinction matters: an unreadable list silently disabled the
    owner-protection guard, so a prune could evict an Owner."""
    code, out = gh("api", f"orgs/{org}/members?role=admin&per_page=100", "--paginate")
    if code != 0:
        log_err(f"could not read the owners of {org}: {out[:200]}")
        return None
    try:
        return frozenset(m["login"] for m in json.loads(out))
    except (json.JSONDecodeError, KeyError, TypeError):
        log_err(f"unparseable owner listing for {org}: {out[:200]}")
        return None


def _fold_diff(a: dict[str, str], b: dict[str, str]) -> list[str]:
    """Original-cased values of `a` whose casefold key is absent from `b`."""
    return [a[f] for f in a.keys() - b.keys()]


def reconcile_team_members(
    org: str, team: str, wanted: set[str], prune: bool = True, dry_run: bool = False
) -> int:
    """Full add(+remove) reconcile of one team's membership to exactly `wanted`.

    Never prunes an org Owner, or the acting token's own login. Owners already have
    full access regardless of team membership (GitHub auto-adds whoever creates a
    team as a member, so e.g. the bot ends up in `current` without ever being a
    deliberate grant), so pruning either doesn't change actual access - it just
    churns team membership on every reconcile. Excluding ALL owners (not just
    whoever happens to be running this particular sync) means the same protection
    holds no matter who triggers it - a human running this locally under their own
    account no longer evicts the bot, and vice versa.

    If the owner list can't be read at all, the whole prune pass is skipped: pruning
    blind is how an Owner gets evicted, and adds are still applied. If the team's OWN
    current membership can't be read, the reconcile aborts entirely (returns an error):
    adding or pruning blind against an unreadable team is unsafe either way.

    Membership is compared case-insensitively (`.casefold()`): GitHub logins are
    case-insensitive, so a hand-typed `Anna-Adams` and the API's `anna-adams` are the same
    account - comparing raw casing would add-then-prune it on every run, oscillating access.
    """
    current = get_team_members(org, team)
    if current is None:
        log_err(
            f"reconcile aborted for {org}/{team}: the team's current membership could "
            f"not be read, so adding or pruning against it would act blind"
        )
        return 1
    errors = 0
    # Fold-keyed maps of both sides: adds use `wanted`'s casing, removes use `current`'s.
    wanted_by_fold = {h.casefold(): h for h in wanted}
    current_by_fold = {h.casefold(): h for h in current}
    for handle in sorted(_fold_diff(wanted_by_fold, current_by_fold)):
        if dry_run:
            log_verbose(f"    DRY-RUN add {handle} -> {org}/{team}")
        elif add_team_member(org, team, handle):
            log_verbose(f"  [ok] {handle} -> {org}/{team}")
        else:
            errors += 1
    if prune:
        owners = get_org_owners(org)
        if owners is None:
            log_err(
                f"pruning skipped for {org}/{team}: the org owner list could not be "
                f"read, and pruning without it risks evicting an Owner"
            )
            return errors
        acting = _acting_login()
        for handle in sorted(_fold_diff(current_by_fold, wanted_by_fold)):
            if handle == acting or handle in owners:
                continue
            if dry_run:
                log_verbose(f"    DRY-RUN remove {handle} <- {org}/{team}")
            elif remove_team_member(org, team, handle):
                log_verbose(f"  [ok] removed {handle} from {org}/{team}")
            else:
                errors += 1
    return errors


def coerce_date(value: object) -> date | None:
    """A YAML date/datetime or an ISO `YYYY-MM-DD` string -> a `date` (None if unparseable).
    Date-level only (whole-day). The single canonical date coercion: `active_today` here and
    `schedule._coerce_date` both use it, so the two can never drift. An unquoted
    `start: 2026-09-01` in YAML parses to a `datetime.date` (or `datetime`), not a string;
    a quoted one is a string - both land on the same `date`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):  # date and its datetime subclass both land here
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def active_today(start: str | date | None, end: str | date | None, today: str) -> bool:
    """Whether `today` (ISO date string) falls within [start, end], either bound optional
    (open-ended if omitted). Bounds may be ISO strings or `datetime.date` objects (an
    unquoted YAML date); an unparseable bound is treated as absent (open-ended on that side)."""
    today_d = coerce_date(today)
    start_d = coerce_date(start)
    end_d = coerce_date(end)
    if start_d and today_d and today_d < start_d:
        return False
    if end_d and today_d and today_d > end_d:  # noqa: SIM103 - guards mirror the docstring
        return False
    return True


# Session directories are named "<ordinal>_<free text>" (e.g. "00_intro",
# "07_finals-review") - only the leading, zero-padding-tolerant ordinal is meaningful;
# the rest is whatever the course calls it. No "week"/"session" literal is required.
_SESSION_PREFIX_RE = re.compile(r"^0*(\d+)_")


def session_number(name: str) -> int | None:
    """Extract the ordinal prefix from a directory name ('00_intro' -> 0, '07_x' -> 7),
    or None if it doesn't start with digits followed by an underscore."""
    m = _SESSION_PREFIX_RE.match(name)
    return int(m.group(1)) if m else None


def session_dirs(dir_paths: Iterable[str]) -> list[tuple[str, str, int]]:
    """THE session-folder rule, over a flat list of relative directory paths.

    `(parent, folder_name, session_number)` for every ordinal-prefixed directory found
    at depth 1 (`NN_.../` - the repo itself is one section, so parent is "") or depth 2
    (`section/NN_.../` - a named section). Anything deeper, and anything without an
    ordinal prefix, is not a session folder. A `parent` is therefore exactly a
    releasable section.

    One rule, two transports: the local filesystem (discover_sections here, used by
    the public-site builder) and the GitHub trees API (dsl_course.discovery) both feed their
    directory listing through this, so "ordinal-prefixed directory = session folder"
    is defined once.
    """
    found = []
    for path in dir_paths:
        parts = path.split("/")
        if len(parts) > 2:
            continue
        n = session_number(parts[-1])
        if n is None:
            continue
        found.append((parts[0] if len(parts) == 2 else "", parts[-1], n))
    return found


def _local_dir_paths(repo_root: Path) -> list[str]:
    """The relative paths of every directory in `repo_root` down to depth 2 - the
    filesystem transport for session_dirs (the API side fetches a git tree instead)."""
    if not repo_root.is_dir():
        return []
    paths = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir():
            continue
        paths.append(child.name)
        paths += [
            f"{child.name}/{grandchild.name}"
            for grandchild in sorted(child.iterdir())
            if grandchild.is_dir()
        ]
    return paths


def find_session_dir(section_dir: Path, session: str) -> Path | None:
    """Find the child of `section_dir` whose ordinal prefix matches `session` exactly
    (session='3' matches '3_x'/'03_x'/'003_x', but not '13_x' or '30_x')."""
    if not section_dir.is_dir() or not session.isdigit():
        return None
    target = int(session)
    for child in sorted(section_dir.iterdir()):
        if child.is_dir() and session_number(child.name) == target:
            return child
    return None


def discover_sections(repo_root: Path) -> list[str]:
    """Any top-level directory containing at least one ordinal-prefixed subdirectory is
    a releasable section - no declared config, the directory structure is the only
    source of truth. Sorted for a deterministic order.

    The local-checkout transport of the session_dirs rule; dsl_course.discovery is the
    API-side one."""
    return sorted(
        {parent for parent, _, _ in session_dirs(_local_dir_paths(repo_root)) if parent}
    )


def grant_team_repo_access(
    org: str, team: str, repo: str, permission: str, *, missing_is_note: bool = False
) -> bool:
    """Grant a team a permission level on one repo (idempotent).

    `missing_is_note`: a team that does not exist yet is logged as a note, not an error -
    an org can be released into before its teams exist, and the next release or sync
    fixes it. Any OTHER failure (a 5xx, a rate limit) stays an error either way; it used
    to read as "team not found" on the read-teams path, which hid real outages."""
    code, out = gh(
        "api",
        "-X",
        "PUT",
        f"orgs/{org}/teams/{team}/repos/{org}/{repo}",
        "-f",
        f"permission={permission}",
    )
    if code == 0:
        return True
    if missing_is_note and is_missing_resource(out):
        log(f"  ({team} team not found - create it first)")
        return False
    log_err(f"  ! could not grant {team} {permission} on {org}/{repo}: {out[:120]}")
    return False


# The course-org faculty teams that get standing access to course repos: instructors run
# releases day-to-day (write), course-admin manage (admin). Applied to `.github` at bootstrap
# and to every scaffolded materials/assignment repo, so faculty & instructors can push content without an
# owner hand-granting each new repo.
COURSE_TEAM_ACCESS = {"instructors": "push", "course-admin": "admin"}

# Faculty access to a repo they should READ but not edit: the RELEASED copy of materials,
# and a student's gradebook. Both have a source of truth elsewhere, so a hand edit here is
# not durable and looks like one that stuck:
#   - a re-release copies over the released copy (`copytree(dirs_exist_ok=True)`), so a
#     correction belongs in the course org's materials repo, then re-release
#   - `distribute` rewrites a gradebook's grades.yml from
#     `classroom-config/grades/<slug>.csv`, so a mark belongs in that CSV
# A submission repo is read for the same reason: marking happens in
# `classroom-config/grades/<slug>.csv`, and by then the deadline snapshot has frozen its
# HEAD and the autograder has run off that snapshot, so a commit there would reach no
# gradebook and form no part of the record.
#
# What keeps WRITE is where faculty actually author: `classroom-config` (the grading CSVs,
# schedule.yml, people.yml, the roster), `welcome/README.md` (the students' front door,
# seeded create-only so faculty may reword it), and `.github` - that one because GitHub
# requires write on a repo to trigger a workflow_dispatch at all, which is what every
# faculty button is.
#
# `course-admin` stays admin throughout - it is the cohort's owner of last resort, and read
# access cannot fix a broken repo.
FACULTY_READ_ACCESS = {"instructors": "pull", "course-admin": "admin"}

# The cohort repos faculty AUTHOR in - the only cohort repos that get write. Everything else
# in a cohort org has its source of truth elsewhere and takes FACULTY_READ_ACCESS. `.github`
# is here because GitHub requires write on a repo to trigger a workflow_dispatch at all.
COHORT_WRITE_REPOS = frozenset({".github", "welcome", "classroom-config"})

# GitHub's repo permissions, weakest first, in the vocabulary a PUT takes (`permission=`).
# A team-repos LISTING answers in a different one (`role_name`: read/write/...) - which is
# why the sweep reads the listing's `permissions` booleans instead; their keys are these.
_PERM_RANK = {"pull": 1, "triage": 2, "push": 3, "maintain": 4, "admin": 5}


def faculty_floor(repo: str, cohort: bool) -> dict[str, str]:
    """The faculty teams' MINIMUM grant on `repo`: write where faculty author (every repo of
    a course org, the COHORT_WRITE_REPOS of a cohort), read everywhere else in a cohort."""
    if not cohort or repo in COHORT_WRITE_REPOS:
        return COURSE_TEAM_ACCESS
    return FACULTY_READ_ACCESS


def grant_course_team_access(org: str, repo: str) -> None:
    """Give the course-org faculty teams their standing access to `repo` (COURSE_TEAM_ACCESS)."""
    for team, perm in COURSE_TEAM_ACCESS.items():
        grant_team_repo_access(org, team, repo, perm)


def grant_faculty_read_access(org: str, repo: str) -> None:
    """Give the faculty teams read on `repo` (FACULTY_READ_ACCESS) - for a repo whose source
    of truth is elsewhere, so an edit made here would be overwritten."""
    for team, perm in FACULTY_READ_ACCESS.items():
        # Per-student hot path (every gradebook, every submission repo): a cohort whose
        # faculty teams are not there yet must not print two errors per student.
        grant_team_repo_access(org, team, repo, perm, missing_is_note=True)


def grant_tagged_team_access(course_org: str, repo: str, tag: str) -> None:
    """Give this tag's cohort-declared instructors team (`instructors-<tag>`) push
    access on `repo` - scoped to just that tag's own content, unlike the standing
    COURSE_TEAM_ACCESS grant every repo gets. No course-admin-<tag> variant: admin
    access stays on the single, course-wide `course-admin` team.

    Ensures the team exists first (idempotent) - callable in either order, whether
    a tag's content repo is scaffolded before or after its cohort first declares
    instructors."""
    team = f"instructors-{tag}"
    create_team(course_org, team, f"Instructors for {tag} (cohort-declared)")
    grant_team_repo_access(course_org, team, repo, "push")


# The cohort-org role teams that get read on released content.
READ_TEAMS = ("students", "auditors")


def grant_read_teams(cohort_org: str, repo: str) -> None:
    """Give both cohort role teams read on a released repo.

    Auditors see exactly what enrolled students see once it's released - the split is
    assignments and grades, not content - so every release grant covers both teams. A
    missing team is a note, not an error: an org can be released into before its teams
    exist, and the next release (or Sync membership) fixes it."""
    for team in READ_TEAMS:
        if grant_team_repo_access(cohort_org, team, repo, "pull", missing_is_note=True):
            log_ok(f"{team} team -> read")


# Descriptions this toolkit wrote in a wording it has since REPLACED, mapped to the
# current one. A repo carrying an old string is carrying OUR text, so it is ours to
# update; anything else a human typed, and is left alone.
#
# There is deliberately no entry for a CURRENT wording - a repo already carrying it needs
# no change - so this is exactly the rename log, and rewording a description means adding
# a line here or convergence silently stops. That forcing function is why this is a
# mapping rather than the set of "everything we ever wrote": the set had to be edited in
# lockstep with a literal in another file, and forgetting would have frozen the old
# wording on every existing org while classifying it as faculty's.
SUPERSEDED_DESCRIPTIONS = {
    # Claimed "enrolled students only", but grant_read_teams gives the `auditors` team read
    # on every released repo too - so the repo table students land on carried a false claim
    # about who can see the materials.
    "Released course materials (enrolled students only)": (
        "Released lectures, labs, readings, & other materials"
    ),
    # The wording that replaced the one above, superseded in its turn. A chain, not a
    # rewrite: an org still on the oldest string has to reach the newest in one pass, so
    # every link keeps pointing at the CURRENT text rather than at its immediate successor.
    "Released lectures, labs, readings, and other materials": (
        "Released lectures, labs, readings, & other materials"
    ),
    "Course materials (lectures/readings by session)": (
        "Course materials (lectures/labs/readings/datasets/other) by session"
    ),
    # The site repo is generated and rewritten on every sync (site.py stamps that inside the
    # repo itself), so its description says so where faculty see it: on the org's landing
    # page, beside the repos they SHOULD open. "on push" went with it - true but about the
    # mechanism, and the reader wants to know whether to touch it.
    "Course website (auto-deployed on push)": (
        "[do not touch]: Course website (auto-deployed)"
    ),
    # The wording before that one. Found on a cohort scaffolded early enough to predate the
    # rename, which is the whole reason this table is a mapping and not a single pair: a
    # description set at creation stays until something converges it, so every wording we
    # have ever written needs a row here or that org keeps it forever.
    "Cohort course website (auto-deployed on push)": (
        "[do not touch]: Course website (auto-deployed)"
    ),
}

# Per TIER, because one old wording wants two different new ones. A cohort org's `.github`
# is machine-owned scaffolding faculty never open; a COURSE org's is where they actually
# work - it holds dsl-course.yml and every workflow they run. A flat old -> new mapping
# cannot tell those apart, so the tier picks the table. Same forcing function as above: a
# reworded literal must be added here or convergence silently stops.
SUPERSEDED_COHORT_DESCRIPTIONS = {
    "Org profile and configuration": "[do not touch]: Org profile and configuration",
    # Every org still carries the wording on the LEFT, so this is a single hop rather than
    # a chain: the interim text this replaced never reached one.
    "PRIVATE cohort config - roster (students.csv). No PII leaves here.": (
        "[visible to instructors only]: Everything you configure for this cohort is "
        "here - student roster, teams, term schedule, and marking. Students never see "
        "it, and no PII leaves this repo."
    ),
}
SUPERSEDED_COURSE_DESCRIPTIONS = {
    "Org profile and configuration": "[control panel]: Org profile & configuration",
}


def _strongest_permission(permissions: dict) -> str | None:
    """The strongest TRUE flag of a listing's cumulative `permissions` object, in the PUT
    vocabulary. None when no flag we rank is set - the caller leaves that repo alone."""
    held = [p for p, on in permissions.items() if on and p in _PERM_RANK]
    return max(held, key=_PERM_RANK.__getitem__) if held else None


def team_repo_access(org: str, team: str) -> dict[str, str | None] | None:
    """`{repo: permission}` for every repo `team` holds - ONE paginated read, PUT vocabulary.

    None when the team does not exist (a 404): an org can be swept before its teams are
    created, and the next sweep picks it up. Not `{}` - that reads as "holds nothing", and
    the caller would then PUT on every repo in the org and 404 on each. Any other failure
    RAISES, on the same rule as every other listing here.

    Read from the `permissions` booleans, never `.role_name`: the listing's role names
    (`read`/`write`) are not the PUT vocabulary (`pull`/`push`), and ranking one in the
    other's table once read every instructor's write as "below read" and demoted them. A
    repo maps to None when its object sets no flag we rank; the caller must skip it."""
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{org}/teams/{team}/repos?per_page=100",
        "--jq",
        ".[] | {name, permissions: (.permissions // {})}",
    )
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{team}'s repos: {out[:200]}")
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"unparseable repo listing for {org}/{team}: {out[:200]}"
        ) from exc
    return {r["name"]: _strongest_permission(r["permissions"]) for r in rows}


def converge_faculty_access(
    org: str,
    repos: list[dict],
    cohort: bool,
    protected: frozenset[str] = frozenset(),
) -> int:
    """Raise the faculty teams to their floor (`faculty_floor`) on every live repo of `org`.

    A team grant is set when a repo is created and never revisited, so a repo kind that
    predates its grant, or an org bootstrapped before one existed, keeps whatever it
    started with. Both org kinds run at default_repository_permission=none, so that grant
    is the WHOLE of a non-owner's access; every live faculty member being an org owner is
    the only reason it went unnoticed. This is the convergence path.

    A FLOOR, never a level: a repo already granted higher is left alone. Fail closed: a
    grant this sweep cannot rank is skipped, never read as "nothing" and overwritten.
    `protected` names the per-student repos (discovery.student_repo_names): they take the
    READ floor whatever `cohort` says, so a mis-told tier can under-grant a course org but
    can never hand instructors push on a student's submission or gradebook. Archived repos
    are skipped (GitHub refuses the PUT).

    Cost: `2 * ceil(N/100)` GETs for a converged org; the FIRST sweep of an unconverged
    org is one PUT per missing grant (a 300-repo cohort: ~600 sequential PUTs, which may
    trip the secondary rate limit and crawl through gh()'s backoff - it self-heals, the
    next night finishes). Never fatal: a failed PUT is a line, not a red refresh."""
    changed = 0
    live = [r["name"] for r in repos if not r.get("archived")]
    for team in COURSE_TEAM_ACCESS:
        try:
            have = team_repo_access(org, team)
        except RuntimeError as exc:
            log(f"  ({exc})")
            continue
        if have is None:
            log(f"  (no {team} team in {org} yet - faculty access not converged)")
            continue
        for name in live:
            floor = (
                FACULTY_READ_ACCESS
                if name in protected
                else faculty_floor(name, cohort)
            )[team]
            if name in have:
                current = have[name]
                if current is None:
                    log(
                        f"  ({team} holds {name} at a level this sweep cannot rank - left)"
                    )
                    continue
                if _PERM_RANK[current] >= _PERM_RANK[floor]:
                    continue
            if grant_team_repo_access(org, team, name, floor):
                log_ok(f"{team} -> {floor} on {name}")
                changed += 1
    return changed


def converge_topics(org: str, repos: list[dict], cohort: bool) -> int:
    """Stamp the machinery topics missing from a COHORT org's per-student repos.

    `submission` (plus the template's own name) on `<template>-<handle>`, `gradebook` on
    `grades-<handle>` - exactly what assign.py and grades.py stamp at creation. That stamp
    is a separate PATCH after the create, so any repo whose stamp failed, or that predates
    the topic, is permanently untagged; nothing ever revisited it. Untagged matters: the
    topics are what keep a student's submission repo and a private gradebook off the org
    landing page, out of the release targets, and on the READ floor of the faculty sweep.
    Both readers have a name rule as a backstop for exactly that reason, but a backstop is
    not a reason to leave the record wrong.

    ADDITIVE, and only where something is missing: the PUT replaces the whole topic list,
    so whatever else a repo carries is read off the listing and written back with it, and
    a repo already carrying its topics costs no call at all. Course orgs are skipped -
    they have neither repo kind.

    Costs no reads (the caller's listing carries `topics` and `isTemplate`) and is never
    fatal: set_repo_topics logs its own failure, and this returns the count so a caller
    that reports failures can include it."""
    if not cohort:
        return 0
    # Local: discovery imports utils, so the names it owns come in at call time.
    from .discovery import GRADEBOOK_PREFIX

    templates = sorted(r["name"] for r in repos if r.get("isTemplate"))
    failures = 0
    for repo in repos:
        if repo.get("archived"):
            continue
        name = repo["name"]
        template = next((t for t in templates if name.startswith(f"{t}-")), None)
        if template is not None:
            wanted = {template, "submission"}
        elif name.startswith(GRADEBOOK_PREFIX):
            wanted = {"gradebook"}
        else:
            continue
        have = set(repo.get("topics") or [])
        if wanted <= have:
            continue
        if set_repo_topics(org, name, sorted(have | wanted)):
            log_ok(f"topics converged on {name}")
        else:
            failures += 1
    return failures


def converge_descriptions(org: str, repos: list[dict], cohort: bool = False) -> int:
    """Update every repo in `repos` whose description we have since reworded.

    `cohort` selects the tier-specific table on top of the shared one: the same old
    `.github` wording becomes "[do not touch]" on a cohort org and "[control panel]" on a
    course org, because they are opposite instructions to the same reader.

    A GitHub description is only ever set at repo CREATION, so a wording fix otherwise
    never reaches a repo that already exists - while being the "What it's for" column on
    the org's landing page. This is the convergence path for it.

    Costs no reads: `repos` is the listing the caller already holds (list_org_repos asks
    for `description` in the same paginated call), so the only requests made are a PATCH
    per genuinely-drifted repo. The dicts are updated in place as well, so a caller that
    renders the listing straight afterwards shows the new wording in the same run rather
    than one run late.

    Never fatal - a description is documentation, and a failed PATCH is worth a line, not
    a failed refresh. Returns the number changed.
    """
    superseded = SUPERSEDED_DESCRIPTIONS | (
        SUPERSEDED_COHORT_DESCRIPTIONS if cohort else SUPERSEDED_COURSE_DESCRIPTIONS
    )
    changed = 0
    for repo in repos:
        if repo.get("archived"):
            continue  # GitHub refuses the PATCH; a frozen cohort logged one failure a night
        want = superseded.get((repo.get("description") or "").strip())
        if not want:
            continue
        code, _ = gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{org}/{repo['name']}",
            "--field",
            f"description={want}",
        )
        if code == 0:
            repo["description"] = want
            log_ok(f"{repo['name']} description -> current wording")
            changed += 1
        else:
            log(f"  ({repo['name']}: could not update the description)")
    return changed


def create_repo(
    org: str,
    name: str,
    private: bool = True,
    description: str = "",
    is_template: bool = False,
) -> bool:
    """Create a repo. Idempotent - treats existing repo as success.

    Sets `description` only on creation. Bringing an EXISTING repo's description up to a
    reworded one is converge_descriptions' job, off the listing the refresh already
    holds - not this function's, which would have to pay a read per call to find out."""
    args = [
        "api",
        "--method",
        "POST",
        f"orgs/{org}/repos",
        "--field",
        f"name={name}",
        "--field",
        f"private={str(private).lower()}",
        "--field",
        f"is_template={str(is_template).lower()}",
    ]
    if description:
        args += ["--field", f"description={description}"]
    code, out = gh(*args)
    if code == 0:
        log_ok(f"repo created: {org}/{name}")
        return True
    # Only a genuine name-clash 422 is success. A bare `"422" in out` also swallowed an
    # invalid-name or policy/plan 422 as success, so a caller would then write into a repo
    # that was never created. Key on GitHub's specific message text instead.
    if "name already exists" in out.lower():
        log_skip(f"repo {org}/{name}")
        return True
    log_err(f"failed to create repo {org}/{name}: {out[:200]}")
    return False


def blob_sha(content: bytes) -> str:
    """Git's blob hash of `content` - what the Contents API reports as a file's `.sha`.

    Computing it locally is what lets every writer here decide, with no extra API call,
    whether a write would change anything."""
    return hashlib.sha1(
        b"blob " + str(len(content)).encode() + b"\0" + content
    ).hexdigest()


def put_file(
    org: str,
    repo: str,
    path: str,
    content: bytes,
    message: str,
    expected_sha: str | None = None,
) -> bool:
    """Create or update a file via the Contents API.

    Updates require the existing file's SHA. By default it is fetched here, immediately
    before the write. That SHA is git's blob sha, so comparing it with the blob sha of
    `content` computed locally tells us - with no extra API call - whether the write would
    change anything: an identical file is left alone. Callers may therefore run on a
    schedule without filling repos with no-op commits.

    `expected_sha` is for a read-modify-write: pass the sha the content was READ at (see
    get_file_with_sha) and that sha is sent as-is, no fresh read. GitHub then REFUSES the
    write if the file has moved on since - which is the whole point. Re-reading the sha at
    write time makes the API call succeed however stale the content is, so a commit that
    landed between the read and the write is silently reverted; that is how Send codes
    could wipe a Join binding out of students.csv. A caller passing it must be ready to
    re-read, re-apply its change, and retry.

    One file, one commit. Use put_files when several files belong in the SAME commit.
    """
    b64 = base64.b64encode(content).decode()
    args = [
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/contents/{path}",
        "--field",
        f"message={message}",
        "--field",
        f"content={b64}",
    ]
    if expected_sha is not None:
        if expected_sha == blob_sha(content):
            return True  # the file already holds exactly this
        if expected_sha:
            args += ["--field", f"sha={expected_sha}"]
    else:
        # If the file already exists, fetch its SHA (required for update)
        code, sha = gh(
            "api",
            f"repos/{org}/{repo}/contents/{path}",
            "--jq",
            ".sha",
        )
        if code == 0 and sha:
            if sha == blob_sha(content):
                return True
            args += ["--field", f"sha={sha}"]
    code, out = gh(*args)
    if code == 0:
        return True
    log_err(f"failed to put {path}: {out[:200]}")
    return False


class TruncatedTree(RuntimeError):
    """A recursive git-tree listing GitHub had to cut short."""


def _untruncated(out: str, org: str, repo: str) -> list[str]:
    """The tree listing's path lines, having first checked the `truncated` flag it carries
    on its FIRST line.

    The git-tree API caps a recursive listing (100k entries / 7MB) and says so in
    `truncated: true` rather than failing. Read past it, a partial listing looks exactly
    like a smaller repo - so a site sync drops the material links it did not see, and
    put_files rewrites the files it thinks are missing. Both callers here are the fail-loud
    kind (see their docstrings), so this is one more way the answer can be untrustworthy
    and must raise rather than be believed."""
    lines = out.splitlines()
    if lines and lines[0].strip() == "true":
        raise TruncatedTree(
            f"the recursive git tree of {org}/{repo} came back TRUNCATED - GitHub could "
            f"not list every path, and acting on a partial listing would delete or "
            f"rewrite whatever it left out. Split the repo, or read it per directory."
        )
    return lines[1:] if lines else []


def repo_blob_shas(org: str, repo: str, branch: str) -> dict[str, str]:
    """`{path: blob sha}` for every file in `org/repo`'s `branch` - ONE recursive fetch.

    The sha-carrying twin of repo_tree (which answers "which paths exist" for discovery).
    One call answers "what is currently there" for a whole set of paths at once, which is
    what lets put_files decide a no-op night for twenty files without twenty reads.

    `{}` means the tree is genuinely empty: a 404 (no such repo/branch) or a 409 (a repo
    with no commits yet - the state every repo is in between create_repo and its first
    seed). Any OTHER failure RAISES rather than reporting an empty tree, on the same rule
    as repo_tree and get_file_content: swallowed, an unreadable tree reads as "nothing is
    there" and the caller rewrites files it should have left alone."""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/git/trees/{branch}?recursive=1",
        "--jq",
        r'"\(.truncated)", (.tree[] | select(.type=="blob") | [.path, .sha] | @tsv)',
    )
    if code != 0:
        if is_missing_resource(out) or "HTTP 409" in out:
            return {}
        raise RuntimeError(f"could not read the tree of {org}/{repo}: {out[:200]}")
    lines = _untruncated(out, org, repo)
    entries = (line.split("\t") for line in lines if "\t" in line)
    return {path: sha for path, sha in entries}


def put_files(
    org: str,
    repo: str,
    files: dict[str, bytes],
    message: str,
    *,
    delete: Iterable[str] = (),
    create_only: bool = False,
) -> bool:
    """Write `files` and remove `delete` in a SINGLE commit, via the git data API.

    put_file is one commit per file, which is right for a lone write but turns a set of
    files that always change together - the generated workflows - into a burst of
    near-identical commits in a repo faculty read. This makes that one commit.

    Same no-op guarantee as put_file, for ONE read rather than one per path: a single
    recursive tree fetch gives every live blob sha, and a path whose sha already matches
    the content we would write is dropped, as is a `delete` path already absent. When
    nothing survives that filter there is NO commit at all - so the nightly refresh, which
    re-pushes every generated file at every org, stays both silent and cheap. The tree,
    commit and ref calls are paid only on a run that genuinely changes something.

    `create_only` inverts the test for USER-owned files: a path that already exists is left
    exactly as it is (and logged as a skip) instead of being overwritten. See
    seed_files_if_absent, which is this flag with a name.

    `files` values are text (workflow YAML, markdown, CSV headers); they go into the tree
    as strings, which is what the trees API takes.

    Returns False if the tree could not be read, or if any leg of the commit failed - a
    partial write is impossible here, since the ref only moves once the whole tree is
    built."""
    try:
        branch = default_branch(org, repo)
        live = repo_blob_shas(org, repo, branch)
    except RuntimeError as exc:
        log_err(str(exc))
        return False
    tree: list[dict[str, Any]] = []
    for path, content in files.items():
        if create_only and path in live:
            log_skip(f"{repo}/{path}")
            continue
        if live.get(path) != blob_sha(content):
            tree.append(
                {
                    "path": path,
                    "mode": "100644",
                    "type": "blob",
                    "content": content.decode(),
                }
            )
    for path in delete:
        if path in live:
            # A null sha is how the trees API spells "remove this path".
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
    if not tree:
        return True
    return _commit_tree(org, repo, branch, tree, message)


def _head(org: str, repo: str, branch: str) -> tuple[str, str] | None:
    """`(head sha, its tree sha)` for `branch`, or None if the repo has NO commits yet.

    Empty is a state every repo passes through: create_repo does not auto-init, so the
    first seed after it lands into a repo with no commit, no tree and no ref. The Contents
    API hides that - it creates the initial commit itself - and the git data API cannot be
    told: see `_commit_tree`, which routes that one case back through Contents."""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/commits/{branch}",
        "--jq",
        "[.sha, .commit.tree.sha] | @tsv",
    )
    if code == 0 and len(out.split()) == 2:
        head, base_tree = out.split()
        return head, base_tree
    if is_missing_resource(out) or "HTTP 409" in out:
        return None
    raise RuntimeError(f"could not read {org}/{repo}@{branch}: {out[:200]}")


def _seed_first_commit(
    org: str, repo: str, tree: list[dict[str, Any]], message: str
) -> bool:
    """The first commit into a repo that has none, via the Contents API - the only one that
    will create it (see `_commit_tree`).

    One commit per file rather than one for the set, which is the cost of the API that
    works here; it is paid once in a repo's life, on the seed that creates it. Deletions in
    `tree` are skipped: there is nothing in an empty repo to remove."""
    ok = True
    for entry in tree:
        content = entry.get("content")
        if content is None:  # a `sha: None` delete - nothing there to delete
            continue
        if not put_file(org, repo, entry["path"], content.encode(), message):
            ok = False
    return ok


def _commit_tree(
    org: str, repo: str, branch: str, tree: list[dict[str, Any]], message: str
) -> bool:
    """Land `tree` (entries relative to `branch`'s current tree) as one commit.

    The ref update is deliberately NOT forced: if a concurrent run moved the branch since
    we read its head, GitHub rejects the fast-forward and we report a failure the caller
    counts, rather than silently discarding whatever landed in between."""
    try:
        parent = _head(org, repo, branch)
    except RuntimeError as exc:
        log_err(str(exc))
        return False
    if parent is None:
        # A repo with NO commits at all refuses `POST /git/trees` outright - "Git Repository
        # is empty" (409) - whether or not a base_tree is sent. Omitting base_tree is not
        # enough: the git data API needs a commit to hang a tree off, and only the Contents
        # API will create that first one. So the first write into a freshly-created repo
        # goes file by file through Contents, and every later write takes the batched path.
        #
        # This is not hypothetical tidying: batching the classroom-config scaffolds into one
        # commit moved them off Contents, and the first cohort org bootstrapped afterwards
        # could not seed that repo at all - the roster, schedule and people.yml never
        # landed, and every later step that reads them failed in turn.
        return _seed_first_commit(org, repo, tree, message)
    payload: dict[str, Any] = {"tree": tree, "base_tree": parent[1]}
    code, new_tree = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{repo}/git/trees",
        "--input",
        "-",
        "--jq",
        ".sha",
        stdin=json.dumps(payload),
    )
    if code != 0:
        log_err(f"could not build a tree for {org}/{repo}: {new_tree[:200]}")
        return False
    code, commit = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{repo}/git/commits",
        "--input",
        "-",
        "--jq",
        ".sha",
        stdin=json.dumps(
            {
                "message": message,
                "tree": new_tree.strip(),
                "parents": [parent[0]] if parent else [],
            }
        ),
    )
    if code != 0:
        log_err(f"could not commit to {org}/{repo}: {commit[:200]}")
        return False
    # An existing branch is MOVED; a repo whose first commit this is has no ref to move,
    # so the ref is created instead.
    if parent:
        code, out = gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{org}/{repo}/git/refs/heads/{branch}",
            "--raw-field",
            f"sha={commit.strip()}",
        )
    else:
        code, out = gh(
            "api",
            "--method",
            "POST",
            f"repos/{org}/{repo}/git/refs",
            "--raw-field",
            f"ref=refs/heads/{branch}",
            "--raw-field",
            f"sha={commit.strip()}",
        )
    if code != 0:
        log_err(f"could not move {org}/{repo}@{branch}: {out[:200]}")
        return False
    return True


def seed_if_absent(
    org: str, repo: str, path: str, content: bytes, message: str
) -> bool:
    """Write a file create-only, never an overwrite - the single home of the "USER-owned
    file, leave it exactly as faculty left it" rule.

    Returns True whenever the file is now present as intended - whether it was WRITTEN just
    now, OR was already present and left untouched (logged as a skip, so a re-run's output
    shows what was left alone). Returns False ONLY when a write was attempted and FAILED, so
    a caller can `if not seed_if_absent(...): failures += 1` to count real failures without a
    skip of a live file counting as one. Callers keep their own comment on WHY a given file
    is create-only; this owns HOW."""
    if get_file_content(org, repo, path) is not None:
        log_skip(f"{repo}/{path}")
        return True
    return put_file(org, repo, path, content, message)


# The mark that says a seeded stub is STILL the scaffold's - present in every stub we write,
# and the one thing that makes it safe to improve. A stub is refreshed while it carries the
# mark and never touched again once faculty remove it, which they do by writing their own
# (each stub tells them so). Without this a stub was create-only forever: the courses
# already running kept the first version we ever shipped, and every later improvement
# reached new repos only.
#
# The legacy strings are the stubs we shipped before the mark existed, so the repos that
# have those pick the new versions up too. Nothing is ever added here that a faculty member
# might plausibly have typed themselves.
STUB_MARK = "dsl-stub:"
# Every wording we have ever seeded, so a repo carrying an older one is still recognised.
# Nothing goes in here that a faculty member might plausibly have typed themselves.
STUB_MARKS = (
    STUB_MARK,
    "Replace with the real syllabus.",
    "This file is the reading list students see.",
    "This file IS the reading list students see on the site's Readings tab",
)


def term_tag(name: str) -> str | None:
    """The fYYYY / sYYYY term tag in an org or repo name (`course-materials-F2026` ->
    'f2026'), or None. Case-insensitive and lowercased, so the same name cannot yield a tag
    on one code path and nothing on another - which two of the three copies of this regex
    did before they were folded into it."""
    m = re.search(r"[fs]\d{4}", name.lower())
    return m.group(0) if m else None


def is_untouched_stub(text: str) -> bool:
    """Whether `text` is still a stub this toolkit seeded, rather than faculty writing."""
    return any(m in text for m in STUB_MARKS)


# Generated faculty-side files, named where every module that has to know about them can
# see it: `scaffold` writes them, `deploy` refuses to release them, `syllabus` builds one.
# Named rather than re-spelled per module, so the exclusion cannot lapse when one is renamed.
SYLLABUS_SAMPLE_FILE = "SYLLABUS.md.sample"
SYLLABUS_SESSIONS_FILE = "SYLLABUS.sessions.md"
# How `scaffold_materials` names every materials repo (`course-materials-<tag>`) - the New
# materials repo workflow takes only the tag, so this prefix is guaranteed by the toolkit
# rather than a convention faculty could deviate from. Named here because `seed.refresh`
# has to recognise a materials repo among the code and dataset repos that
# `discover_content_repos` returns alongside it, and a rename reaching only one side would
# silently stop the convergence it gates.
MATERIALS_REPO_PREFIX = "course-materials-"
# A session's OPTIONAL prose reading list, the one file in a `readings/NN_.../` folder that
# is inlined as text rather than listed as a download. Named here for the same reason as the
# two above: `scaffold` seeds it and `site`/`syllabus` match on it, so a rename that reached
# only one of them would have the scaffold quietly seeding a file the renderer no longer
# recognises as prose.
#
# Matched by whole filename, never by extension. Deciding by extension made an uploaded
# `lecture-notes.md` or `refs.bib` - a reading in its own right - get swallowed into the page
# as prose instead of listed as a file a student can download.
# Path components a PUBLISHED course page must never carry - matched by NAME, at every
# depth, case-insensitively, and as glob patterns so `.env.local` is caught alongside
# `.env`. The public site copies whole discovered session folders wholesale, so anything a
# faculty member happens to keep beside their teaching material is published with it: a
# `solution/` next to the lab it answers, the `grading.yml` that says how it is marked, the
# hidden `tests/`, a `.env` with a live key. None of those is a release decision anyone
# made; they are what "copy the folder" means.
#
# NOT a release policy for the cohort path - `deploy` deliberately releases what faculty
# name, including a solution, because a cohort repo is private and marking sometimes needs
# one. This is the PUBLIC site, where there is no such case.
PUBLICATION_DENYLIST = (
    "solution",
    "solutions",
    "grading.yml",
    "tests",
    ".env",
    ".env.*",
    ".git",
)


def is_denied_publication(name: str) -> bool:
    """Whether one path COMPONENT is on PUBLICATION_DENYLIST."""
    lowered = name.casefold()
    return any(fnmatch(lowered, pattern) for pattern in PUBLICATION_DENYLIST)


def has_denied_component(path: str) -> bool:
    """Whether any component of `path` is on PUBLICATION_DENYLIST."""
    return any(is_denied_publication(part) for part in path.split("/") if part)


READING_OVERLAY_FILE = "READINGS.md"
READING_OVERLAY_NAMES = frozenset(
    {"readings.md", "readings.markdown", "readings.txt", "readings.bib"}
)


def refresh_stubs(
    org: str,
    repo: str,
    files: dict[str, bytes],
    message: str,
    create: bool = False,
    retire: tuple[str, ...] = (),
) -> int:
    """Bring a SET of seeded stubs up to date in ONE commit, and return the failure count.

    The middle ground between `seed_if_absent` (frozen at whatever we first shipped) and
    `put_file` (clobbers real work): a stub is written while it is absent or still carries a
    stub mark, and left exactly as faculty left it once they have written over it.

    One commit, not one per file, for the same reason `seed_files_if_absent` batches - a set
    of stubs is one act of seeding, and writing them one at a time opens a repo faculty then
    author by hand with a column of identical commit lines.

    `create=False` (the default) refreshes existing stubs but creates none, which is what
    makes this safe to run over EVERY content repo in a nightly convergence: the code and
    dataset repos are in that list too, and seeding a syllabus into `lecture-code-f2026`
    would be nonsense. Creating is the scaffold's job - only it knows what kind of repo it
    just made - so the scaffold passes `create=True`.

    `retire` is the path a stub used to live at, for when one is RENAMED. Keyed by path, a
    rename otherwise orphans the old file: it is no longer in `files`, so it is never
    refreshed and never removed, and it lingers in the repo forever - which, for a stub that
    reads as faculty-facing instructions, means it ships to students as a released "reading"
    the next time that folder goes out. Retired only while it is still an untouched stub;
    once faculty have written over it, it is theirs and is left exactly where it is."""
    write: dict[str, bytes] = {}
    for path, body in files.items():
        current = get_file_content(org, repo, path)
        if current is None:
            if create:
                write[path] = body
            continue
        if is_untouched_stub(current):
            write[path] = body
        else:
            log_skip(f"{repo}/{path}")
    drop = []
    for path in retire:
        current = get_file_content(org, repo, path)
        if current is None:
            continue
        if is_untouched_stub(current):
            drop.append(path)
        else:
            log_skip(f"{repo}/{path} (renamed, but yours - left in place)")
    if not write and not drop:
        return 0
    ok = put_files(org, repo, write, message, delete=tuple(drop))
    return 0 if ok else len(write) + len(drop)


def seed_files_if_absent(
    org: str, repo: str, files: dict[str, bytes], message: str
) -> bool:
    """seed_if_absent for a SET of files: whatever is genuinely missing, in one commit.

    Same create-only rule, file by file - every path already present is left exactly as
    faculty left it and logged as a skip, so a repair re-run's output still shows what it
    left alone. Only the absent ones are written, and they go together, because a scaffold
    set is one act of seeding rather than six.

    Returns True whenever every path is now present as intended (written just now, or
    already there), and False only when a write was attempted and failed."""
    return put_files(org, repo, files, message, create_only=True)


def is_missing_resource(out: str) -> bool:
    """Whether a failed `gh` output means the resource is genuinely ABSENT (a 404) rather
    than a real error to raise on. The one shared marker test: callers that distinguish
    "not there yet" from "couldn't read it" must agree on what absence looks like, so the
    marker list lives here instead of being re-inlined (and drifting) at each call site.

    Matches gh's own casing (`gh: Not Found (HTTP 404)`) exactly - deliberately case-
    SENSITIVE, so a lowercase `not found` inside some other error's text (a jq key miss,
    say) is NOT misread as a 404 and does not suppress a real failure."""
    return "HTTP 404" in out or "Not Found" in out


def get_file_content(org: str, repo: str, path: str, ref: str = "") -> str | None:
    """Fetch a file's decoded text content (from `ref`, default branch if empty).

    None means the file is genuinely absent (a 404) - nothing else. Any other failure to
    read it (no permission, rate limit, network) raises, because callers treat None as
    "not configured yet" and would otherwise read a transient API failure as an empty
    roster/schedule/registry and cheerfully do nothing. Same rule as repo_blob_shas."""
    url = f"repos/{org}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    code, out = gh(
        "api",
        url,
        "--jq",
        ".content | @base64d",
    )
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{repo}/{path}: {out[:200]}")
    return out


def get_file_with_sha(
    org: str, repo: str, path: str, ref: str = ""
) -> tuple[str, str] | None:
    """`(decoded text, blob sha)` for a file, or None if it is genuinely absent (a 404).

    The read half of a safe read-modify-write: hand the sha back to
    `put_file(..., expected_sha=...)` and the write is refused if anything else committed
    to the file in between. Same fail-loud rule as get_file_content - any failure that is
    NOT a 404 raises, because a caller treating it as "not there" would write over a file
    it never managed to read.

    The sha comes first in the jq output, on its own line, because the content may contain
    newlines and a sha may not."""
    url = f"repos/{org}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    code, out = gh("api", url, "--jq", r'"\(.sha)\n" + (.content | @base64d)')
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{repo}/{path}: {out[:200]}")
    sha, _, text = out.partition("\n")
    return text, sha


def repo_tree(org: str, repo: str, branch: str, kind: str = "") -> tuple[str, ...]:
    """Every path of type `kind` in `org/repo`'s `branch`, sorted - ONE recursive git-tree
    fetch, shared by both transports that need a repo's structure: `kind="tree"` is the
    directories (dsl_course.discovery's session-folder discovery), `kind="blob"` the files
    (site._repo_tree's material links). The two used to fetch the same tree with their own
    error handling, and only one of them was fail-loud. `kind=""` is every path of either
    kind, for a caller that just asks "is this path in the repo" and does not care whether
    the answer is a file or a folder - one fetch rather than two of the same tree.

    A genuinely absent or empty tree is `()`: a 404 (no such repo/branch) or a 409 (a repo
    with no commits at all) really does hold no paths, and the caller correctly finds
    nothing. Any OTHER failure - a rate limit, a network drop - RAISES rather than
    reporting an empty tree: swallowed, it republished a cohort site with every material
    link AND every session row deleted, silently and green. Same rule as get_file_content.
    """
    select = f' | select(.type=="{kind}")' if kind else ""
    code, out = gh(
        "api",
        f"repos/{org}/{repo}/git/trees/{branch}?recursive=1",
        "--jq",
        f'"\\(.truncated)", (.tree[]{select} | .path)',
    )
    if code != 0:
        # 404 = no such tree; 409 = an empty repo (no commits) - a tree-specific signal on
        # top of the shared 404-absence test.
        if is_missing_resource(out) or "HTTP 409" in out:
            return ()
        raise RuntimeError(f"could not read the file tree of {org}/{repo}: {out[:200]}")
    return tuple(sorted(_untruncated(out, org, repo)))


def load_yaml_config(org: str, repo: str, path: str) -> dict | None:
    """Fetch + parse a YAML config file into a mapping, correctly distinguishing the three
    states callers that prune depend on:

    - ABSENT -> None (get_file_content returned None on a genuine 404). Do not prune.
    - present but empty -> {} (the file exists but parses to nothing). A legitimate
      "empty the team" for pruning callers.
    - present with content -> the parsed mapping.

    Any OTHER read failure propagates (get_file_content raises on non-404 - preserved
    here). Malformed YAML, or a non-mapping top level (a list/scalar), is logged (naming
    org/repo/path) and raised - never silently coerced to {}, which is exactly the
    "or '' erases None-vs-content" class of bug this replaces."""
    content = get_file_content(org, repo, path)
    if content is None:
        return None
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        log_err(f"malformed YAML in {org}/{repo}/{path}: {exc}")
        raise
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = (
            f"{org}/{repo}/{path} is not a YAML mapping "
            f"(got {type(data).__name__}) - refusing to use it"
        )
        log_err(msg)
        # RuntimeError (not TypeError) to match the house style for a bad read/config -
        # get_file_content and list_org_repos raise it too, and status.main catches it.
        raise RuntimeError(msg)  # noqa: TRY004
    return data


def set_repo_topics(org: str, repo: str, topics: list[str]) -> bool:
    """Replace the full topic list on a repo (GitHub limit: 20 topics, lowercase kebab)."""
    normalised = sorted({t.lower().replace("_", "-") for t in topics if t})
    args = [
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/topics",
        "-H",
        "Accept: application/vnd.github+json",
    ]
    for t in normalised:
        args += ["--field", f"names[]={t}"]
    code, out = gh(*args)
    if code == 0:
        return True
    log_err(f"failed to set topics on {org}/{repo}: {out[:200]}")
    return False


def add_collaborator(org: str, repo: str, login: str, permission: str = "push") -> bool:
    """Add a collaborator to a repo. permission: pull | triage | push | maintain | admin."""
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{repo}/collaborators/{login}",
        "--field",
        f"permission={permission}",
    )
    if code == 0:
        return True
    log_err(f"failed to add {login} to {org}/{repo}: {out[:200]}")
    return False


def is_collaborator(org: str, repo: str, login: str) -> bool | None:
    """Whether `login` is a DIRECT collaborator on `org/repo`.

    None means the answer could not be read. Kept distinct from False on purpose: the one
    caller is about to REVOKE access, and a rate limit or a network drop must never read as
    "not a collaborator, nothing to do" - nor, worse, be acted on either way."""
    code, out = gh("api", f"repos/{org}/{repo}/collaborators/{login}")
    if code == 0:
        return True
    if is_missing_resource(out):
        return False
    log_err(
        f"could not check whether {login} collaborates on {org}/{repo}: {out[:160]}"
    )
    return None


def remove_collaborator(org: str, repo: str, login: str) -> bool:
    """Revoke a direct collaborator grant. Idempotent - GitHub 204s either way."""
    code, out = gh(
        "api", "--method", "DELETE", f"repos/{org}/{repo}/collaborators/{login}"
    )
    if code == 0:
        return True
    log_err(f"could not remove {login} from {org}/{repo}: {out[:160]}")
    return False


def generate_from_template(
    template_org: str,
    template_name: str,
    owner: str,
    name: str,
    private: bool = True,
    description: str = "",
) -> bool:
    """Create a repo from a template. Idempotent."""
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{template_org}/{template_name}/generate",
        "-H",
        "Accept: application/vnd.github+json",
        "--field",
        f"owner={owner}",
        "--field",
        f"name={name}",
        "--field",
        f"private={str(private).lower()}",
        "--field",
        f"description={description}",
    )
    if code == 0:
        return True
    if "name already exists" in out.lower():
        log_skip(f"repo {owner}/{name}")
        return True
    log_err(f"failed to generate {owner}/{name} from template: {out[:200]}")
    return False
