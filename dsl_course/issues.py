"""One self-updating issue, addressed by its EXACT title - the find/create/edit/comment/
close primitive every unattended notification in the toolkit needs.

Four places grew their own copy of `gh issue list --search "<title> in:title"` ->
create-or-comment (`TODO.md`), and only one of them matched the title exactly afterwards.
That match is the whole safety property: `--search` is full-text, so an issue a HUMAN filed
quoting the title comes back in the results, and a caller that adopts the first row rewrites
their issue out from under them. It lives here once so no caller can forget it.

The pattern these calls exist to serve is body-as-state, comments-as-events (see
`source_digest`): the body is rewritten on every tick, which GitHub does not email about, and
a comment - which it does - is posted only when the caller says something crossed a
threshold. So `upsert_issue` comments only on an issue that ALREADY existed: a brand-new
issue notifies by being created, and a comment on top of that would double up.

Nothing here raises at its caller: every failure is logged and returned as a count, because
each consumer runs inside an unattended release cron where an undelivered notification must
not stop the release. `find_issue` is the exception - a listing that could not be read is
not "there is no issue", and inventing that answer would open a duplicate every tick.
"""

from __future__ import annotations

import json

from .ghcli import gh, gh_json
from .log import log_err

# `gh issue list` defaults to 30, and the exact-title match below is client-side - so a repo
# whose issue list happened to bury ours past the 30th result would look as though no issue
# existed, and every tick would open a fresh one.
_LIST_LIMIT = "100"


def _titled(repo: str, title: str) -> list[tuple[int, str]]:
    """(number, body) for every OPEN issue in `repo` titled EXACTLY `title`, lowest number
    first. Raises when the listing could not be read: absence has to be a real answer.

    Only open issues. A closed one must not be adopted - the point of closing is that the
    condition cleared, so the next occurrence is a new issue and a new notification.

    Read through `gh_json`, which parses stdout ALONE: `gh` hands back stdout and stderr
    joined, so one advisory on stderr (a token nearing expiry, an update notice) beside a
    perfectly good listing would raise a JSONDecodeError - which the callers, catching
    RuntimeError, would let escape into the release run. Anything unreadable comes back as
    the RuntimeError this contract promises.
    """
    try:
        rows = gh_json(
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--search",
            f"{title} in:title",
            "--limit",
            _LIST_LIMIT,
            "--json",
            "number,body,title",
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not list issues in {repo}: {exc}") from exc
    return [
        (r["number"], r.get("body") or "")
        for r in sorted(rows, key=lambda r: r["number"])
        if r.get("title") == title
    ]


def find_issue(repo: str, title: str) -> tuple[int, str] | None:
    """(number, body) of the open issue in `repo` with this exact title, or None.

    The BODY comes back with it because the callers keep their previous state in it (an
    HTML comment, invisible when rendered) - so one listing answers both "is it open?" and
    "what did we last say?" without a second read."""
    found = _titled(repo, title)
    return found[0] if found else None


def upsert_issue(repo: str, title: str, body: str, comment: str | None = None) -> int:
    """Make `repo`'s issue titled `title` say `body` - editing it if it is open, opening it
    if it is not. Returns the error count.

    `comment` is posted only when the issue ALREADY existed: a new issue emails everyone
    watching by being created, so a comment saying the same thing again is noise. Pass it
    only for a transition the caller wants a human to hear about."""
    try:
        existing = find_issue(repo, title)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    if existing:
        code, out = gh(
            "issue", "edit", str(existing[0]), "--repo", repo, "--body", body
        )
    else:
        code, out = gh(
            "issue", "create", "--repo", repo, "--title", title, "--body", body
        )
    if code != 0:
        log_err(f"could not write `{title}` in {repo}: {out[:200]}")
        return 1
    if comment and existing:
        code, out = gh(
            "issue", "comment", str(existing[0]), "--repo", repo, "--body", comment
        )
        if code != 0:
            log_err(f"could not comment on `{title}` in {repo}: {out[:200]}")
            return 1
    return 0


def close_issues_titled(repo: str, title: str, comment: str | None = None) -> int:
    """Close every open issue in `repo` with this exact title, optionally with a closing
    comment. Returns the error count; closing nothing is a success.

    Plural on purpose: the callers are stateless and re-derive "should this be open?" from
    the world on every tick, so a duplicate opened during an outage has to be cleared too -
    otherwise it stands for the rest of the term with nothing left to close it."""
    try:
        found = _titled(repo, title)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    errors = 0
    for number, _body in found:
        args = ["issue", "close", str(number), "--repo", repo]
        if comment:
            args += ["--comment", comment]
        code, out = gh(*args)
        if code != 0:
            log_err(f"could not close `{title}` in {repo}: {out[:200]}")
            errors += 1
    return errors
