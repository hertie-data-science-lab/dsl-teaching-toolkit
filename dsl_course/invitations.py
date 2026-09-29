"""The bot accepts its own org invitations - from registered course orgs only.

Setting up a course needs the bot as an ACTIVE owner of the new org
(`bootstrap_course.preflight`), and only a person can invite it. Accepting used to be a
second person's job - signing in as the bot - so the scheduler's quarter-hourly release pass
does it instead, whichever course org's run gets there first: the bot is one account, so any
course org's tick serves every org being set up.

Only an org named in the toolkit's `orgs.yml` (`org_registry`) is accepted, or a semester
org listed in the `semesters.yml` of the registered course whose pass is running: a course's
admins vouch for its semesters, and the New semester wizard lists the org there before
inviting the bot. Anyone can invite the bot, and an org it has joined and that carries the
course topic would be refreshed and handed the bot token. Every other invitation is left
pending, never declined, so registering the org later is all it takes: the next tick
accepts it.

Org names are not student data: the one line per org is safe in a public run log.
"""

from __future__ import annotations

from collections.abc import Iterable

from . import org_registry
from .ghcli import gh
from .log import log, log_err, log_ok


def pending_orgs() -> list[str]:
    """Every org that has invited this token's account and is still waiting, all pages.
    Raises when the listing cannot be read: "none pending" is an answer, a failure is not."""
    code, out = gh(
        "api",
        "--paginate",
        "user/memberships/orgs?state=pending&per_page=100",
        "--jq",
        ".[] | .organization.login",
    )
    if code != 0:
        raise RuntimeError(f"could not list the bot's pending invitations: {out[:200]}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def accept_pending(course_org: str = "", semesters: Iterable[str] = ()) -> list[str]:
    """Accept every pending invitation from a registered course org, or from one of
    `semesters` (the registry of `course_org`, counted only when that course is itself
    registered), and return the orgs accepted. Raises when the registry cannot be read,
    before anything is accepted.

    Idempotent: an accepted invitation is no longer pending, so the next run skips it. One
    org that cannot be accepted (the invitation withdrawn meanwhile) does not stop the rest."""
    accepted = []
    registered = set(org_registry.course_orgs())
    if course_org.casefold() in registered:
        registered |= {s.casefold() for s in semesters}
    for org in pending_orgs():
        if org.casefold() not in registered:
            log(f"  [skip] invitation from {org} left pending: not registered")
            continue
        code, out = gh(
            "api",
            "--method",
            "PATCH",
            f"user/memberships/orgs/{org}",
            "-f",
            "state=active",
        )
        if code == 0:
            log_ok(f"accepted the invitation to {org}")
            accepted.append(org)
        else:
            log_err(f"could not accept the invitation to {org}: {out[:200]}")
    return accepted
