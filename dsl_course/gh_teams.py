"""Org membership and team membership: creating a team, inviting a person into the org,
and reconciling one team's roster against what a config file says it should be.
"""

from __future__ import annotations

import json
import re
from functools import cache, lru_cache

from .ghcli import gh, is_already_exists
from .log import log_err, log_ok, log_person, log_skip

# GitHub usernames: 1-39 chars, ASCII alphanumerics or single hyphens, no leading/
# trailing hyphen and no consecutive hyphens. Used to reject a typo'd faculty handle
# before it is invited as a stranger.
_GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def is_valid_github_username(handle: str) -> bool:
    """Whether `handle` is a syntactically valid GitHub username (charset/length only -
    not whether the account exists)."""
    return bool(_GITHUB_USERNAME_RE.match(handle))


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
    if is_already_exists(out):
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
        log_person(f"  [skip] org membership {login} ({current})")
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
        log_person(f"  [ok] invited {login} to {org}")
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


def _team_member_rows(org: str, team_slug: str) -> dict[str, str] | None:
    """`{login: GitHub id}` for a team's current members - the ONE listing behind both
    public readers - or None if it could not be READ.

    None (a non-zero exit OR unparseable JSON) must never be conflated with an empty team:
    reconciling against an unreadable team would add or prune blind. Mirrors
    get_org_owners."""
    code, out = gh(
        "api", f"orgs/{org}/teams/{team_slug}/members?per_page=100", "--paginate"
    )
    if code != 0:
        log_err(f"could not read the members of {org}/{team_slug}: {out[:200]}")
        return None
    try:
        return {m["login"]: str(m["id"]) for m in json.loads(out)}
    except (json.JSONDecodeError, KeyError, TypeError):
        log_err(f"unparseable member listing for {org}/{team_slug}: {out[:200]}")
        return None


def get_team_members(org: str, team_slug: str) -> set[str] | None:
    """The logins currently in a team, as GitHub spells them. None if unreadable."""
    rows = _team_member_rows(org, team_slug)
    return None if rows is None else set(rows)


def get_team_member_ids(org: str, team_slug: str) -> dict[str, str] | None:
    """`{login.casefold(): GitHub id}` for a team's current members. None if unreadable.

    The IMMUTABLE half of the same listing. A login is renameable; an id is not, so this
    is the only way a reconcile can tell "somebody who does not belong here" from "the
    same person under a new name" - see the `keep_ids` guard in
    `reconcile_team_members`."""
    rows = _team_member_rows(org, team_slug)
    return None if rows is None else {log.casefold(): gid for log, gid in rows.items()}


def remove_team_member(org: str, team_slug: str, login: str) -> bool:
    code, _ = gh(
        "api", "--method", "DELETE", f"orgs/{org}/teams/{team_slug}/memberships/{login}"
    )
    return code == 0


@lru_cache(maxsize=1)
def acting_login() -> str | None:
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
    org: str,
    team: str,
    wanted: set[str],
    prune: bool = True,
    dry_run: bool = False,
    keep_ids: set[str] = frozenset(),
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

    `keep_ids` are GitHub ids that belong in this team however they are currently spelt: a
    member holding one is never pruned. A login is renameable and an id is not, so a
    student who renames their account is otherwise indistinguishable from a stranger - the
    config still names the OLD login, the add 404s and the prune evicts the new one, every
    night, until someone hand-edits the CSV. The ids cost one extra listing, paid only when
    a caller supplies some AND there is something to prune; if they cannot be read the
    prune is skipped whole, on the same rule as the owner list above.
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
            log_person(f"    DRY-RUN add {handle} -> {org}/{team}")
        elif add_team_member(org, team, handle):
            log_person(f"  [ok] {handle} -> {org}/{team}")
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
        acting = acting_login()
        stale = sorted(_fold_diff(current_by_fold, wanted_by_fold))
        protected: set[str] = set()
        if stale and keep_ids:
            by_fold = get_team_member_ids(org, team)
            if by_fold is None:
                log_err(
                    f"pruning skipped for {org}/{team}: the member ids could not be read, "
                    f"and pruning without them evicts anyone who has renamed their account"
                )
                return errors
            protected = {f for f, gid in by_fold.items() if gid in keep_ids}
        for handle in stale:
            if handle == acting or handle in owners:
                continue
            if handle.casefold() in protected:
                # Same person, new login: the config still names the old one. Leave them
                # in; the roster's handle cell is re-linked when they next open a Join
                # issue (templates/welcome/onboard.yml matches on the id too).
                log_person(
                    f"  [keep] {handle} in {org}/{team} - renamed, same GitHub id"
                )
                continue
            if dry_run:
                log_person(f"    DRY-RUN remove {handle} <- {org}/{team}")
            elif remove_team_member(org, team, handle):
                log_person(f"  [ok] removed {handle} from {org}/{team}")
            else:
                errors += 1
    return errors
