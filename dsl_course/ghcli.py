"""The two subprocess wrappers everything GitHub-facing runs through - `gh` and `git` -
with their timeouts, the rate-limit retry ladder, and the shared 404 test.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from itertools import pairwise
from pathlib import Path
from typing import Any

from .log import log_err

RATE_LIMIT_MARKERS = (
    "secondary rate limit",
    "api rate limit exceeded",
    "abuse detection",
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

    Retries on GitHub secondary rate limits, and on a subprocess timeout, with exponential
    backoff."""
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
        is_rate_limited = any(m in lower for m in RATE_LIMIT_MARKERS)
        if not is_rate_limited or attempt == retries:
            return result.returncode, result.stdout, result.stderr
        print(
            f"  [wait] rate-limited, retry {attempt + 1}/{retries} in {delay}s",
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


def _is_mutating(args: tuple[str, ...]) -> bool:
    """Whether this argv is a write - `gh api --method`/`-X` naming a mutating verb."""
    return any(
        flag in ("--method", "-X") and value.upper() in _MUTATING_METHODS
        for flag, value in pairwise(args)
    )


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


def gh(*args: str, stdin: str | None = None, retries: int = 3) -> tuple[int, str]:
    """Run a gh CLI command. Returns (returncode, stdout+stderr).

    Retries on GitHub secondary rate limits - and on a subprocess timeout - with
    exponential backoff, and paces writes to stay under the secondary limit in the first
    place (see _pace_writes).
    """
    _pace_writes(args)
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
