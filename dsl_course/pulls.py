"""One idempotent pull request per head branch - the find/create/edit primitive a caller
needs when it wants ONE standing PR about a branch rather than a new one per tick.

The twin of `issues`, and for the same reason: the callers are unattended crons that
re-derive "should this be open?" from the world every quarter of an hour, so the question
they ask is "is there already a PR from this branch?" and the answer has to be exact. A
PR is addressed by its HEAD BRANCH, not by its title: the branch is what the caller
controls, a title is prose someone may edit, and two PRs proposing the same branch is the
one outcome that turns a release conflict into a wall of notifications. The BASE is asked
for too, but only to refuse: a PR from this head onto some other branch is a decision
somebody took, not this caller's PR to edit.

`gh pr list --head` filters server-side, and the two client-side tests here are what make
the answer trustworthy anyway: `headRefName` must match exactly, and the PR must not be
CROSS-REPOSITORY. `headRefName` carries no owner, so a fork's branch of the same name is
in that listing too - and this toolkit tells every student to fork the materials repo,
which is where such a branch would come from. Adopting one would edit a student's PR and
leave the release's own question unasked.

Failures are logged and returned as a count, never raised at the caller: this runs inside
a release cron where an unopened PR must not stop the release. `find_pr` is the exception,
like `find_issue` - a listing that could not be read is not "there is no PR", and
inventing that answer opens a duplicate every tick.
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

from .ghcli import gh, gh_json
from .log import log, log_err

# `gh pr list` defaults to 30, and the exact match below is client-side.
_LIST_LIMIT = "100"

# What `gh pr create` prints on success, and the only place a caller can learn the number
# of a PR it has just opened. Matched rather than read off a line position: `gh` hands
# stdout and stderr back joined, so an advisory can arrive above or below it.
_PR_URL = re.compile(r"https://\S+/pull/(\d+)")


class PullRequest(NamedTuple):
    """One open pull request found by its head branch: its number, its web URL and the
    branch it proposes to merge INTO - which the caller has to see, because a head branch
    answers "is there a pull request?" and only the base answers "is it the one I would
    have opened?"."""

    number: int
    url: str
    base: str


class Upserted(NamedTuple):
    """What one `upsert_pr` did: the error count its callers fold into their own, and the
    PR's URL where there is one - the link the caller puts in its log line, since a
    release that held its merge back has nowhere else to point a human."""

    errors: int
    url: str | None = None


def find_pr(repo: str, head: str) -> PullRequest | None:
    """The open PR in `repo` proposing branch `head` FROM `repo` ITSELF, or None. Lowest
    number first, so a duplicate opened during an outage does not change which one gets
    adopted.

    A cross-repository PR is never it, however its branch is named: the caller owns a
    branch in this repo, not in somebody's fork of it (see the module docstring).

    Raises when the listing could not be read: absence has to be a real answer.

    Read through `gh_json`, which parses stdout ALONE - `gh` joins stdout and stderr, so
    one advisory beside a perfectly good listing would otherwise raise a JSONDecodeError
    the caller never catches."""
    try:
        rows = gh_json(
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            head,
            "--state",
            "open",
            "--limit",
            _LIST_LIMIT,
            "--json",
            "number,url,baseRefName,headRefName,isCrossRepository",
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not list pull requests in {repo}: {exc}") from exc
    found = [
        PullRequest(r["number"], r.get("url") or "", r.get("baseRefName") or "")
        for r in sorted(rows, key=lambda r: r["number"])
        if r.get("headRefName") == head and not r.get("isCrossRepository")
    ]
    return found[0] if found else None


def upsert_pr(
    repo: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    reviewer: str | None = None,
    refresh_body: bool = False,
) -> Upserted:
    """Make sure `repo` has one open PR proposing `head` into `base`, opening it if it has
    none. Reports the error count and the PR's URL (see `Upserted`).

    The body of an EXISTING PR is left alone unless `refresh_body`: a caller whose body is
    a running summary of what the branch now holds wants it rewritten every run, and one
    whose body is an explanation written once does not - and rewriting that would talk
    over whatever a human added to it.

    An open PR from `head` onto ANOTHER base is not this call's: a human retargeted it,
    and it is named in the log and left exactly as it is - no body refresh, and nothing new
    opened, which is also what GitHub would allow, a second open PR from one head being
    refused.

    `reviewer` (`<org>/<team>`) is requested only on the PR this call OPENED, in a separate
    edit that is allowed to fail: whether a team can be requested at all depends on the
    org's plan, and a release must not go red because a review request did not stick. A
    re-run does not re-request - the reviewers a human has since dismissed are their
    decision, not this function's."""
    try:
        existing = find_pr(repo, head)
    except RuntimeError as exc:
        log_err(str(exc))
        return Upserted(1)
    if existing and existing.base != base:
        # Somebody retargeted it. Not this call's pull request any more - editing its body
        # would talk over that decision, and opening a second one from the same head is
        # what GitHub refuses anyway. Named, so the run says where the head branch went.
        log(
            f"  [warn] {repo}#{existing.number} already proposes `{head}` into "
            f"`{existing.base}`, not `{base}` - leaving it alone"
        )
        return Upserted(0, existing.url)
    if existing:
        if refresh_body:
            code, out = gh(
                "pr", "edit", str(existing.number), "--repo", repo, "--body", body
            )
            if code != 0:
                log_err(f"could not update the pull request in {repo}: {out[:200]}")
                return Upserted(1, existing.url)
        return Upserted(0, existing.url)

    code, out = gh(
        "pr",
        "create",
        "--repo",
        repo,
        "--head",
        head,
        "--base",
        base,
        "--title",
        title,
        "--body",
        body,
    )
    found = _PR_URL.search(out or "")
    url = found.group(0) if found else None
    if code != 0:
        log_err(f"could not open a pull request in {repo}: {out[:200]}")
        return Upserted(1, url)
    if reviewer and found:
        code, out = gh(
            "pr",
            "edit",
            found.group(1),
            "--repo",
            repo,
            "--add-reviewer",
            reviewer,
        )
        if code != 0:
            log(f"  [warn] could not request {reviewer}'s review on {url}: {out[:120]}")
    return Upserted(0, url)
