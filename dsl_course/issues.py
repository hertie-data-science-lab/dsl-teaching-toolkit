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
import re
from typing import NamedTuple

from .ghcli import ALL, ISSUES, forget_written, gh, gh_json, on_write
from .log import log_err, on_cli_start


def issue_url(repo: str, number: int) -> str:
    """The web URL of one issue. Spelled once, because a caller that had to OPEN the issue
    and one that found it already open both need it, and two format strings for one URL is
    one rename away from a mail linking nowhere."""
    return f"https://github.com/{repo}/issues/{number}"


class Issue(NamedTuple):
    """One issue found by its exact title: its number, the body it carries, and whether it
    is closed.

    The BODY travels with it because the callers keep their previous state in it (an HTML
    comment, invisible when rendered) - so one listing answers both "is it open?" and
    "what did we last say?" without a second read."""

    number: int
    body: str
    closed: bool = False


# ONE listing of a repo's OPEN issues answers every title a process asks about: a tick asks
# about eight fault titles in one `semester-config`, and one search per title was eight
# listings (2026-09-26). The LIST endpoint, not `--search`: the search index lags by
# minutes, so an issue opened a moment ago read as absent and was opened again. Open only,
# because open issues stay few while closed ones pile up all term, and a listing is one
# GraphQL page per hundred - so the closed half is searched by title, and only by the one
# caller that needs it (`find_closed`). A repo with `_LISTING_LIMIT` open issues or more
# cannot be listed whole and falls back to the per-title search.
_LISTING_LIMIT = 1000
_SEARCH_LIMIT = "100"

# Per CLI process, like `gh_contents`' reads, and for the same reason OFF until a CLI command
# line has parsed. This module's own writes update the held listing in place; any other
# issue write to a repo drops it. Keyed by the casefolded `org/repo`.
_listings: dict[str, list[dict] | None] | None = None


def list_once(on: bool) -> None:
    """Turn the per-process issue-listing memo on (empty) or off. `tests/conftest.py`
    turns it off between tests."""
    global _listings
    _listings = {} if on else None


def _forget(kind: str, targets: frozenset[str]) -> None:
    if _listings is not None and kind in (ISSUES, ALL):
        forget_written(_listings, targets)


on_write(_forget)
on_cli_start(lambda parser: list_once(parser.read_once))


def _list(repo: str, *query: str) -> list[dict]:
    """One `gh issue list`. Raises when the listing could not be read: absence has to be a
    real answer.

    Read through `gh_json`, which parses stdout ALONE: `gh` hands back stdout and stderr
    joined, so one advisory on stderr (a token nearing expiry, an update notice) beside a
    perfectly good listing would raise a JSONDecodeError - which the callers, catching
    RuntimeError, would let escape into the release run. Anything unreadable comes back as
    the RuntimeError this contract promises."""
    try:
        return gh_json(
            "issue", "list", "--repo", repo, *query, "--json", "number,body,title,state"
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not list issues in {repo}: {exc}") from exc


def _listing(repo: str) -> list[dict] | None:
    """Every OPEN issue in `repo`, or None when there are too many to hold."""
    key = repo.casefold()
    if _listings is not None and key in _listings:
        return _listings[key]
    rows: list[dict] | None = _list(
        repo, "--state", "open", "--limit", str(_LISTING_LIMIT)
    )
    if len(rows) >= _LISTING_LIMIT:
        rows = None  # held too, so a full repo is listed once, then searched per title
    if _listings is not None:
        _listings[key] = rows
    return rows


def _exact(rows: list[dict], title: str, closed: bool = False) -> list[Issue]:
    return [
        Issue(r["number"], r.get("body") or "", closed)
        for r in sorted(rows, key=lambda r: r["number"])
        if r.get("title") == title
    ]


def _titled(repo: str, title: str) -> list[Issue]:
    """Every OPEN issue in `repo` titled EXACTLY `title`, lowest number first. Raises
    when the listing could not be read."""
    rows = _listing(repo)
    if rows is None:
        rows = _list(
            repo,
            "--state",
            "open",
            "--search",
            f"{title} in:title",
            "--limit",
            _SEARCH_LIMIT,
        )
    return _exact(rows, title)


def find_issue(repo: str, title: str) -> Issue | None:
    """The open issue in `repo` with this exact title, or None."""
    found = _titled(repo, title)
    return found[0] if found else None


def find_closed(repo: str, title: str) -> Issue | None:
    """The newest CLOSED issue in `repo` with this exact title, or None - one title
    search. Newest by number, which is the order they were opened in.

    A closed one must never be ADOPTED - the point of closing is that the condition
    cleared, so the next occurrence is a new issue and a new notification - only READ, for
    the state it left behind (see `config_digest.sync`)."""
    rows = _list(
        repo,
        "--state",
        "closed",
        "--search",
        f"{title} in:title",
        "--limit",
        _SEARCH_LIMIT,
    )
    found = _exact(rows, title, closed=True)
    return found[-1] if found else None


def open_titles(repo: str) -> set[str]:
    """The exact title of every OPEN issue in `repo`, off the one listing.

    For a caller asking about SEVERAL known titles at once - `status`, which wants to know
    which of a semester's digest issues are standing.

    Raises like `_titled` does, for the same reason: a listing that could not be read is
    not "no issues are open", and a status table that quietly said so would report a
    semester with a broken roster as healthy."""
    rows = _listing(repo)
    if rows is None:
        rows = _list(repo, "--state", "open", "--limit", str(_LISTING_LIMIT))
    return {r.get("title") or "" for r in rows}


def _held(repo: str) -> list[dict] | None:
    """The listing this process holds for `repo`, taken BEFORE a write (which drops it)."""
    return None if _listings is None else _listings.get(repo.casefold())


def _keep(repo: str, rows: list[dict] | None) -> None:
    """Put back the listing a write of this module's own has just brought up to date."""
    if _listings is not None and rows is not None:
        _listings[repo.casefold()] = rows


class Upserted(NamedTuple):
    """What one `upsert_issue` did: the error count its callers fold into their own, and
    the issue's URL where there is one.

    The URL is here for the notification that rides ALONGSIDE the issue. A caller that had
    to OPEN the issue this tick knew its number nowhere else - `gh issue create` prints it
    and the return used to be a bare count - so the mail it sent beside the issue could not
    link the record it was summarising. None when the write failed, or when `gh` printed
    no URL."""

    errors: int
    url: str | None = None


class Titled(NamedTuple):
    """Both halves of one exact title: the issue that is open, and the newest one that is
    closed.

    For a caller whose state lives in the body it last wrote (see `source_digest`). When
    somebody closes that issue by hand nothing has actually been fixed, and re-opening
    from a blank slate reports every standing fault as new and notifies about all of it
    again - so it wants the closed body too."""

    open: Issue | None
    last_closed: Issue | None


def find_issues(repo: str, title: str) -> Titled:
    """The open issue with this exact title, and the newest closed one - which costs a
    search of its own, so a caller that may not need it asks `find_closed` when it does."""
    return Titled(find_issue(repo, title), find_closed(repo, title))


class _Unasked:
    """The `existing=` default: the caller has not looked.

    A type of its own because `None` is already an answer - "I looked, and no issue is
    open" - and a default of None could not tell the two apart. It would then search
    again on exactly the tick that has to CREATE."""


_UNASKED = _Unasked()

# What `gh issue create` prints on success, and the only place a caller can learn the
# number of an issue it has just opened. Matched rather than read off a line position:
# `gh` hands stdout and stderr back joined, so an advisory can arrive above or below it.
_ISSUE_URL = re.compile(r"https://\S+/issues/\d+")


def upsert_issue(
    repo: str,
    title: str,
    body: str,
    comment: str | None = None,
    existing: Issue | None | _Unasked = _UNASKED,
) -> Upserted:
    """Make `repo`'s issue titled `title` say `body` - editing it if it is open, opening it
    if it is not. Reports the error count and the issue's URL (see `Upserted`).

    `comment` is posted only when the issue ALREADY existed: a new issue emails everyone
    watching by being created, so a comment saying the same thing again is noise. Pass it
    only for a transition the caller wants a human to hear about.

    `existing` is a `find_issue` result the caller has already fetched - every consumer
    reads the body for its own previous state before deciding what to write, so without
    this the search runs twice per tick."""
    if existing is _UNASKED:
        try:
            existing = find_issue(repo, title)
        except RuntimeError as exc:
            log_err(str(exc))
            return Upserted(1)
    held = _held(repo)
    if existing:
        url = issue_url(repo, existing.number)
        code, out = gh(
            "issue", "edit", str(existing.number), "--repo", repo, "--body", body
        )
        if code == 0 and held is not None:
            _keep(
                repo,
                [
                    {**r, "body": body} if r["number"] == existing.number else r
                    for r in held
                ],
            )
    else:
        code, out = gh(
            "issue", "create", "--repo", repo, "--title", title, "--body", body
        )
        found = _ISSUE_URL.search(out or "")
        url = found.group(0) if found else None
        if code == 0 and held is not None and url:
            number = int(url.rsplit("/", 1)[1])
            row = {"number": number, "title": title, "body": body, "state": "OPEN"}
            _keep(repo, [*held, row])
    if code != 0:
        log_err(f"could not write `{title}` in {repo}: {out[:200]}")
        return Upserted(1, url)
    if comment and existing:
        held = _held(repo)
        code, out = gh(
            "issue", "comment", str(existing.number), "--repo", repo, "--body", comment
        )
        _keep(repo, held)  # a comment changes nothing the listing holds
        if code != 0:
            log_err(f"could not comment on `{title}` in {repo}: {out[:200]}")
            return Upserted(1, url)
    return Upserted(0, url)


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
    for issue in found:
        held = _held(repo)
        args = ["issue", "close", str(issue.number), "--repo", repo]
        if comment:
            args += ["--comment", comment]
        code, out = gh(*args)
        if code != 0:
            log_err(f"could not close `{title}` in {repo}: {out[:200]}")
            errors += 1
        elif held is not None:
            _keep(repo, [r for r in held if r["number"] != issue.number])
    return errors
