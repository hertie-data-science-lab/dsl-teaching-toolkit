"""The two subprocess wrappers everything GitHub-facing runs through - `gh` and `git` -
with their timeouts, the retry ladder (rate limits, and a transient GitHub fault on a
read), and the shared 404 test.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections import deque
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

from .log import log_err

RATE_LIMIT_MARKERS = (
    "secondary rate limit",
    "api rate limit exceeded",
    "abuse detection",
)

# A GitHub-side fault on ONE call, in gh's own words: the 5xx family (`gh: Internal Server
# Error (HTTP 500)`, `HTTP 502: Bad Gateway`) and the body that ended mid-JSON. The same
# call a moment later almost always succeeds - so without this a single bad read reddens a
# tick, files a public "is failing" issue and mails course-admin. The scheduler ticks 96
# times a day, which is far too often for that to stay noise anyone reads.
TRANSIENT_MARKERS = (
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "unexpected end of json input",
)

# Per-call ceiling for a single `gh` subprocess. A hung TLS connection would otherwise
# block the whole Actions job until GitHub's 6-hour limit; a timeout is treated as a
# retryable failure within the retry ladder below.
GH_TIMEOUT_SECONDS = 120


def _run_gh(
    args: tuple[str, ...], stdin: str | None, retries: int
) -> tuple[int, str, str]:
    """One `gh` invocation with the timeout and the retry ladder: (code, stdout, stderr).

    The streams are kept APART here and joined by `gh` below, because gh_json has to parse
    stdout on its own - gh writes advisories (a token nearing expiry, an update notice) to
    stderr, and a joined pair would feed them to the JSON parser.

    THE choke point: this is the one `subprocess.run(["gh", ...])` in the package, so the
    org fence and the write governor sit here rather than on `gh` - `gh_json` goes straight
    through this and would otherwise be the one caller running unfenced and unpaced.

    Retries on GitHub secondary rate limits, on a subprocess timeout, and - for a
    NON-mutating call only - on a transient GitHub fault (see TRANSIENT_MARKERS), with
    exponential backoff."""
    _check_gh_allowlist(args)
    _pace_writes(args)
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
            err = f"gh: timed out after {GH_TIMEOUT_SECONDS}s"
            if attempt == retries:
                return 1, "", err
            print(
                f"  [wait] {err}, retry {attempt + 1}/{retries} in {delay}s",
                flush=True,
            )
            time.sleep(delay)
            delay *= 2
            continue
        if result.returncode == 0:
            return result.returncode, result.stdout, result.stderr
        lower = (result.stdout + result.stderr).lower()
        why = None
        if any(m in lower for m in RATE_LIMIT_MARKERS):
            why = "rate-limited"
        elif not _is_mutating(args):
            # Only a READ repeats a 5xx: a mutating call that came back with one may still
            # have applied the write, and repeating it would apply it twice.
            marker = next((m for m in TRANSIENT_MARKERS if m in lower), None)
            if marker:
                why = f"transient {marker}"
        if why is None or attempt == retries:
            return result.returncode, result.stdout, result.stderr
        print(
            f"  [wait] {why}, retry {attempt + 1}/{retries} in {delay}s",
            flush=True,
        )
        time.sleep(delay)
        delay *= 2
    # Only reachable with a negative `retries` (the loop never runs); callers unpack a
    # pair, so hand back a failure rather than None.
    return 1, "", "gh: not run (retries < 0)"


# GitHub caps CONTENT-CREATING requests at roughly 80 a minute per token, separately from
# the 5,000/hour budget. A first handout tick issues several writes per student in one
# process, so past ~60 students the burst trips the secondary limit and the retry ladder
# (30 + 60 + 120 s) is spent before it clears - and the rest of the cohort fails in turn.
# Pacing the writes to stay under the cap is cheaper than retrying through it.
WRITES_PER_MINUTE = 70
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_write_times: deque[float] = deque()

# Named at module level so a test can drive the governor with a fake clock rather than
# spending a real minute.
_now = time.monotonic
_sleep = time.sleep


# `gh api` sends POST as soon as a field is passed, so an argv with no `--method` at all
# can still be a write. Every call in the package now spells its method out, which is the
# habit to keep - this is the backstop for the one that forgets, and the reason a READ that
# passes `-f` must still say `-X GET` (as `collect._snapshot_sha` does) or be paced as a
# write.
_FIELD_FLAGS = frozenset({"-f", "-F", "--field", "--raw-field", "--input"})

# The porcelain verbs that write. The toolkit reaches for these wherever the REST shape
# would be worse (an issue comment, a workflow dispatch, a secret), and they carry no
# `--method` for the check above to see. Everything absent from this table - `list`,
# `view`, `download`, `clone` - is a read.
_WRITE_VERBS = {
    "issue": frozenset({"create", "comment", "close", "reopen", "edit", "delete"}),
    "repo": frozenset({"create", "delete", "edit", "fork", "rename"}),
    "workflow": frozenset({"run", "enable", "disable"}),
    "secret": frozenset({"set", "delete"}),
    "variable": frozenset({"set", "delete"}),
    "release": frozenset({"create", "delete"}),
    "pr": frozenset({"create", "merge", "close", "edit"}),
    "label": frozenset({"create", "edit", "delete"}),
}

# Two nouns where every verb is treated as a write: membership and team shape are the
# permissions of the estate, and a read misfiled here costs a pause, not a repo.
_ALWAYS_WRITE = frozenset({"team", "org"})


def _split_flags(args: tuple[str, ...]) -> tuple[str, ...]:
    """`--method=PUT` as two tokens, so one pass reads either spelling.

    Only FLAGS are split: `--field name=x` leaves `name=x` alone, which is what the owner
    patterns and the mutating-method test both need to see whole."""
    out: list[str] = []
    for arg in args:
        head, sep, value = arg.partition("=")
        out.extend([head, value] if sep and head.startswith("-") else [arg])
    return tuple(out)


def _api_is_mutating(flat: tuple[str, ...], words: list[str]) -> bool:
    """Whether a `gh api` argv writes."""
    declared = [v.upper() for f, v in pairwise(flat) if f in ("--method", "-X")]
    if declared:
        return any(method in _MUTATING_METHODS for method in declared)
    if words[1:2] == ["graphql"]:
        # One endpoint, both directions: what a GraphQL call does is in the document, not
        # in the verb. A read whose text happens to say `mutation` is refused, which is
        # the safe way round.
        return any("mutation" in arg for arg in flat)
    return any(arg in _FIELD_FLAGS for arg in flat)


def _is_mutating(args: tuple[str, ...]) -> bool:
    """Whether this argv is a write - the one test the pacer and the org fence share.

    Three shapes, because `gh` has three ways of writing: an `api` call with a mutating
    `--method` (or with fields and no method at all, which gh sends as POST), a porcelain
    subcommand whose verb writes, and a `graphql` document containing a mutation. It used
    to ask only about `--method`, so a `gh issue comment` was neither paced nor fenced.

    Pure: argv in, verdict out, no environment read."""
    flat = _split_flags(args)
    words = [arg for arg in flat if not arg.startswith("-")]
    if not words:
        return False
    command = words[0]
    if command == "api":
        return _api_is_mutating(flat, words)
    if command in _ALWAYS_WRITE:
        return True
    verb = words[1] if len(words) > 1 else ""
    return verb in _WRITE_VERBS.get(command, frozenset())


def _pace_writes(args: tuple[str, ...]) -> None:
    """Hold a mutating call until fewer than WRITES_PER_MINUTE were issued in the last 60
    seconds, then record it. Reads pass straight through."""
    if not _is_mutating(args):
        return
    while True:
        at = _now()
        while _write_times and at - _write_times[0] >= 60:
            _write_times.popleft()
        if len(_write_times) < WRITES_PER_MINUTE:
            _write_times.append(at)
            return
        _sleep(60 - (at - _write_times[0]))


# --------------------------------------------------------- the opt-in org allowlist

# An OPT-IN blast-radius fence for the live end-to-end run (tests/e2e), which drives real
# workflows against real orgs with a maintainer token that can delete repositories.
# `DSL_ORG_ALLOWLIST=org-a,org-b` means: this process may WRITE to those orgs and nowhere
# else. UNSET - which is every workflow run, every CLI run and every unit test - is no
# behaviour change at all; nothing in the toolkit sets it.
#
# A refusal RAISES rather than coming back as gh's usual failure pair. A pair is exactly
# what "this repo is not there" looks like to `utils.repo_exists`, which is optimistic by
# design, so a refusal handed back that way would be read as absence and the caller would
# go on to CREATE the thing in the org we just refused to write to. An exception is the
# one answer nothing can swallow.
_ORG_ALLOWLIST_ENV = "DSL_ORG_ALLOWLIST"

# The three shapes an owner appears in inside a gh argv: an api path (`repos/<owner>/...`,
# `orgs/<owner>/...`), a bare `<owner>/<repo>` (which is also what `-R`/`--repo` takes),
# and the `owner=` field of a create-from-template call, whose PATH names the template's
# org while the field names the org the new repo lands in.
#
# Shape-matched rather than positional, because a `--field content=<base64>` value carries
# slashes too: no owner has an `=`, a space or a colon in it, and flags are skipped.
_API_OWNER = re.compile(r"^(?:repos|orgs)/([A-Za-z0-9._-]+)/")
_NAME_WITH_OWNER = re.compile(r"^([A-Za-z0-9._-]+)/[A-Za-z0-9._-]+$")
_OWNER_FIELD = re.compile(r"^owner=([A-Za-z0-9._-]+)$")
_OWNER_NAME = re.compile(r"[A-Za-z0-9._-]+")

# `https://github.com/<owner>/<repo>`, `git@github.com:<owner>/<repo>`, and the
# `https://x-access-token:***@github.com/<owner>/<repo>` form gh's credential helper writes.
_REMOTE_OWNER = re.compile(r"github\.com[:/]([A-Za-z0-9._-]+)/")


def org_allowlist() -> frozenset[str] | None:
    """The orgs this process may write to, or None when the fence is off (the default)."""
    raw = os.environ.get(_ORG_ALLOWLIST_ENV, "")
    names = {n.strip() for n in raw.split(",") if n.strip()}
    return frozenset(names) if names else None


def _argv_owners(args: tuple[str, ...]) -> set[str]:
    """Every org this argv names as a target."""
    owners = set()
    flat = _split_flags(args)
    for flag, value in pairwise(flat):
        # `-R <owner>/<repo>` is already read positionally by the loop below; it is spelt
        # out here so the intent survives a change to the patterns. `--org <name>` is the
        # one that is NOT, having no `/` in it - `gh secret set --org` is a real write.
        if flag in ("-R", "--repo") and (match := _NAME_WITH_OWNER.match(value)):
            owners.add(match.group(1))
        elif flag == "--org" and _OWNER_NAME.fullmatch(value):
            owners.add(value)
    for arg in flat:
        if arg.startswith("-"):
            continue
        for pattern in (_API_OWNER, _NAME_WITH_OWNER, _OWNER_FIELD):
            if match := pattern.match(arg):
                owners.add(match.group(1))
                break
    return owners


def _check_gh_allowlist(args: tuple[str, ...]) -> None:
    """Refuse a write to an org outside the allowlist. Reads always pass.

    A write naming NO org is refused too. Every mutating call in the package names one in
    its path, so an argv we cannot attribute is a shape this guard has never seen - and a
    fence that waves through what it does not understand is not a fence."""
    allowed = org_allowlist()
    if allowed is None or not _is_mutating(args):
        return
    owners = _argv_owners(args)
    outside = sorted(owners - allowed)
    if not owners or outside:
        named = ", ".join(outside) if outside else "no org at all"
        raise RuntimeError(
            f"{_ORG_ALLOWLIST_ENV} allows writes to {', '.join(sorted(allowed))} - "
            f"refusing `gh {' '.join(args[:3])}`, which writes to {named}."
        )


def _git_subcommand(args: tuple[str, ...]) -> str:
    """`push` out of `-C <wd> -c user.name=x push -q origin HEAD` - the first token that is
    neither a flag nor the value of git's two pre-command flags."""
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("-C", "-c"):
            skip = True
        elif not arg.startswith("-"):
            return arg
    return ""


def _push_owner(args: tuple[str, ...], cwd: str | None) -> str:
    """The org a `git push` would land in, or "" if it cannot be told.

    The remote is nearly always the name `origin`, so the URL has to be resolved out of the
    working copy - through `git` itself, which recurses no further because `remote get-url`
    is not a push."""
    after = args[args.index("push") + 1 :]
    named = [a for a in after if not a.startswith("-")]
    remote = named[0] if named else "origin"
    if "/" not in remote and ":" not in remote:
        where = cwd
        if where is None and "-C" in args:
            where = args[args.index("-C") + 1]
        code, remote = git("remote", "get-url", remote, cwd=where)
        if code != 0:
            return ""
    match = _REMOTE_OWNER.search(remote)
    return match.group(1) if match else ""


def _check_push_allowlist(args: tuple[str, ...], cwd: str | None) -> None:
    """Refuse a `git push` to an org outside the allowlist - the other half of the fence.

    Most of what this toolkit writes goes through the Contents API, but assignment
    handout, the site build and solution pushes all end in a `git push`, so a gh-only
    guard would fence off the small writes and leave the large ones open."""
    allowed = org_allowlist()
    if allowed is None or _git_subcommand(args) != "push":
        return
    owner = _push_owner(args, cwd)
    if owner not in allowed:
        raise RuntimeError(
            f"{_ORG_ALLOWLIST_ENV} allows writes to {', '.join(sorted(allowed))} - "
            f"refusing `git push` to {owner or 'an unreadable remote'}."
        )


def gh(*args: str, stdin: str | None = None, retries: int = 3) -> tuple[int, str]:
    """Run a gh CLI command. Returns (returncode, stdout+stderr).

    Retries on GitHub secondary rate limits, on a subprocess timeout, and - for a read
    only - on a transient GitHub fault, with exponential backoff; and paces writes to stay
    under the secondary limit in the first place (see _pace_writes).

    Raises when `DSL_ORG_ALLOWLIST` is set and this is a write to an org outside it
    (in `_run_gh`, which every path to the `gh` binary goes through).
    """
    code, out, err = _run_gh(args, stdin, retries)
    return code, (out + err).strip()


def clone(org: str, repo: str, dest: str | Path, branch: str | None = None) -> bool:
    """Clone `org/repo` into `dest`, optionally at `branch`. True on success.

    `-- -q` hands git its own quiet flag: a clone's progress output is hundreds of lines
    nobody reads in an Actions log. Every clone in the toolkit goes through here, so the
    argv shape is written once."""
    args = ["repo", "clone", f"{org}/{repo}", str(dest), "--", "-q"]
    if branch is not None:
        args += ["-b", branch]
    code, _ = gh(*args)
    return code == 0


def gh_json(*args: str) -> Any:
    """Run a gh CLI command and parse JSON stdout. Raises on failure.

    Through the same ladder as `gh`: this used to call subprocess directly, so the one
    caller that reads across the whole estate (list_orgs' topic search) was the only
    GitHub call in the toolkit with no timeout and no rate-limit retry - it could hang a
    job until the 6-hour ceiling, or fail the weekly inventory on a secondary limit that
    every other call rides out."""
    code, out, err = _run_gh(args, None, 3)
    if code != 0:
        raise RuntimeError(
            f"`gh {' '.join(args)}` failed (exit {code}): {err.strip()[:200]}"
        )
    return json.loads(out)


# Per-call ceiling for a single `git` subprocess, the sibling of GH_TIMEOUT_SECONDS.
# Larger, because these are clones and pushes of whole materials repos rather than one API
# call - but bounded, because an unauthenticated remote that decides to prompt, or a hung
# TLS connection, otherwise blocks the job until GitHub's 6-hour limit kills it with no
# message anyone can act on.
GIT_TIMEOUT_SECONDS = 600


def git(*args: str, cwd: str | None = None) -> tuple[int, str]:
    """Run a git command. A timeout comes back as a normal failure pair, so every caller's
    existing `!= 0` check reports it rather than seeing an exception.

    A `push` raises when `DSL_ORG_ALLOWLIST` is set and the remote is outside it."""
    _check_push_allowlist(args, cwd)
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


# Bot identity + disabled hooks for engine-made commits. Spread into git() calls in the
# clone/commit/push paths of release/site/scaffold/assign: git("-C", wd, *GIT_ENV, ...).
# Spelt once here because `collect` reads it back off a commit to tell the toolkit's own
# commits from a student's.
BOT_EMAIL = "bot@dsl.local"
BOT_NAME = "dsl-bot"
GIT_ENV = [
    "-c",
    f"user.email={BOT_EMAIL}",
    "-c",
    f"user.name={BOT_NAME}",
    "-c",
    "core.hooksPath=/dev/null",
]


@cache
def bot_login() -> str:
    """The GitHub login this token authenticates as - resolved ONCE per process.

    A commit the API made on the toolkit's behalf (the `/generate` that creates every
    submission repo, a Contents PUT) carries this login as its author, and no git identity
    at all - so `BOT_EMAIL` cannot recognise it and the login has to be asked for. One
    call per process, cached, because the question is asked once per submission repo.

    "" when it cannot be read, which every caller must treat as "cannot tell" - never as
    "not the bot", or a transient here would turn a handout commit into a submission."""
    code, out = gh("api", "user", "--jq", ".login")
    return out.strip() if code == 0 else ""


def is_missing_resource(out: str) -> bool:
    """Whether a failed `gh` output means the resource is genuinely ABSENT (a 404) rather
    than a real error to raise on. The one shared marker test: callers that distinguish
    "not there yet" from "couldn't read it" must agree on what absence looks like, so the
    marker list lives here instead of being re-inlined (and drifting) at each call site.

    Matches gh's own casing (`gh: Not Found (HTTP 404)`) exactly - deliberately case-
    SENSITIVE, so a lowercase `not found` inside some other error's text (a jq key miss,
    say) is NOT misread as a 404 and does not suppress a real failure."""
    return "HTTP 404" in out or "Not Found" in out


# What a failed create means when the thing is ALREADY THERE: GitHub's 422 name clash, its
# JSON `already_exists` error code, the teams endpoint's "Name must be unique for this org",
# and a plain 409 Conflict (the Pages endpoint's answer). Deliberately these phrasings and
# not a bare "422"/"already" - an invalid-name or policy 422 read as success would let a
# caller go on writing into a repo or team that was never created.
_ALREADY_EXISTS_MARKERS = (
    "already exists",
    "already_exists",
    "must be unique",
    "http 409",
)


def is_already_exists(out: str) -> bool:
    """Whether a failed `gh` output means the resource is already there - an idempotent
    success, not an error. The twin of `is_missing_resource`, and shared for the same
    reason: every create-if-absent site must agree on what "already there" looks like."""
    lower = out.lower()
    return any(m in lower for m in _ALREADY_EXISTS_MARKERS)


def bot_token(what: str) -> str | None:
    """The bot token to publish as `what`, or None once it has said why.

    ONLY `DSL_BOT_TOKEN` is ever published. A maintainer running a bootstrap or a refresh
    by hand usually has their PERSONAL `GH_TOKEN` exported, and publishing that would hand
    their PAT to every workflow in the org - so GH_TOKEN alone is refused rather than
    quietly used. Both refusals are LOUD and the caller counts them as failures: a green
    skip leaves an org whose seeded workflows fail with no auth, weeks later, with a
    successful run behind them."""
    token = os.environ.get("DSL_BOT_TOKEN")
    if token:
        return token
    if os.environ.get("GH_TOKEN"):
        log_err(
            f"DSL_BOT_TOKEN not set (only GH_TOKEN is) - refusing to publish a personal "
            f"token as {what}; set DSL_BOT_TOKEN to propagate it."
        )
    else:
        log_err(f"DSL_BOT_TOKEN not set - cannot set {what}.")
    return None
