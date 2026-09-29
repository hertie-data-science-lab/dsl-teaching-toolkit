"""The bot accepts its own org invitations.

Setting up a course or a semester needs the bot as an ACTIVE owner of the new org
(`bootstrap_course.preflight`), and only a person can invite it. Accepting used to be a
second person's job - signing in as the bot - so the scheduler's quarter-hourly release pass
does it instead: every pending invitation of the token's own account is accepted, whichever
course org's run gets there first. The bot is one account, so any course org's tick serves
every org being onboarded, and a second tick finds nothing pending.

Org names are not student data: the one line per org is safe in a public run log.
"""

from __future__ import annotations

from .ghcli import gh
from .log import log_err, log_ok


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


def accept_pending() -> list[str]:
    """Accept every pending org invitation, and return the orgs accepted.

    Idempotent: an accepted invitation is no longer pending, so the next run skips it. One
    org that cannot be accepted (the invitation withdrawn meanwhile) does not stop the rest."""
    accepted = []
    for org in pending_orgs():
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
