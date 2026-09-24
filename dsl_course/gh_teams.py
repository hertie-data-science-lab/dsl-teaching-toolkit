"""The org itself and the teams in it: converging an org's settings, creating a team,
inviting a person into the org, and reconciling one team's roster against what a config
file says it should be.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from functools import cache, lru_cache

from .ghcli import gh, is_already_exists, is_missing_resource
from .log import log, log_err, log_ok, log_person, log_skip

# GitHub usernames: 1-39 chars, ASCII alphanumerics or single hyphens, no leading/
# trailing hyphen and no consecutive hyphens. Used to reject a typo'd faculty handle
# before it is invited as a stranger.
_GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def is_valid_github_username(handle: str) -> bool:
    """Whether `handle` is a syntactically valid GitHub username (charset/length only -
    not whether the account exists)."""
    return bool(_GITHUB_USERNAME_RE.match(handle))


# What `create_team_outcome` found: it made the team (201), or one of that name was already
# there (the duplicate-name 422).
CREATED = "created"
EXISTED = "existed"


def create_team(
    org: str, name: str, description: str = "", privacy: str | None = None
) -> bool:
    """Create a team. Idempotent - treats a duplicate-name 422 as success.
    Returns True if a team with this name now exists.

    `privacy` is what the team must have; None asks only that it exist, and leaves an
    existing team's privacy as it is.
    """
    return create_team_outcome(org, name, description, privacy) is not None


def create_team_outcome(
    org: str, name: str, description: str = "", privacy: str | None = None
) -> str | None:
    """`create_team`, saying which way it succeeded: CREATED when this call made the team,
    EXISTED when it was already there, None when neither.

    The difference matters to a caller about to reconcile the team: GitHub's REST reads
    can 404 a team for minutes after it is made, and a team this call made has a known
    membership anyway - see `reconcile_team_members(just_created=True)`."""
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
        f"privacy={privacy or 'closed'}",
    )
    if code == 0:
        log_ok(f"team created: {name}")
        return CREATED
    if is_already_exists(out):
        _converge_team_privacy(org, name, privacy)
        return EXISTED
    log_err(f"failed to create team {name}: {out[:200]}")
    return None


def _converge_team_privacy(org: str, name: str, privacy: str | None) -> None:
    """Correct an existing team's privacy, but only when it is actually wrong.

    A team keeps the privacy it was made with, and nothing else revisits it - `students`
    and `auditors` are `secret` so a student cannot read the class list off the team page,
    and every semester created before that decision still has them `closed`. Converged here,
    at the one place a duplicate is seen. The read comes first because create_team runs
    once per team per sync, every hour: an unconditional PATCH spent that whole allowance
    of the write governor re-setting privacy that was already right."""
    if privacy is None:
        log_skip(f"team {name}")
        return
    code, current = gh("api", f"orgs/{org}/teams/{name}", "--jq", ".privacy")
    if code == 0 and current.strip() == privacy:
        log_skip(f"team {name}")
        return
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"orgs/{org}/teams/{name}",
        "--field",
        f"privacy={privacy}",
    )
    if code == 0:
        log_skip(f"team {name}")
    else:
        # The team exists either way, which is what create_team returns; only its privacy
        # could not be corrected.
        log_err(f"team {name}: privacy not set to {privacy}: {out[:120]}")


def create_role_teams(org: str, teams: Iterable[tuple[str, str, str]]) -> int:
    """Create (or converge the privacy of) each `(slug, description, privacy)` in
    `teams`, returning how many could NOT be created.

    create_team already treats a duplicate-name 422 as success, so a non-zero count here
    is a genuine failure - and every one of them is load-bearing: membership sync, the
    faculty grants and the workflow buttons all address teams by slug, so a bootstrap
    that lost one leaves an org nobody but its owner can work in. The count used to be
    dropped on the floor and the run reported success."""
    return sum(
        0 if create_team(org, slug, desc, privacy=privacy) else 1
        for slug, desc, privacy in teams
    )


def members_without_2fa(org: str) -> int | None:
    """How many of `org`'s members have two-factor auth off. None if it could not be read."""
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{org}/members?filter=2fa_disabled",
        "--jq",
        ".[].login",
    )
    return len(out.split()) if code == 0 else None


# What it looks like when GitHub ANSWERED and the answer was no: a policy above the org,
# or a plan without the setting. Only these two, and only from GitHub's own wording - a
# 5xx, a connection that never got there and an expired token are all failures to report,
# not settings somebody has to go and change.
_REFUSED = ("http 403", "http 422")


def _is_refusal(out: str) -> bool:
    """Whether a failed `gh api` output is GitHub declining the change rather than the
    call not getting through."""
    lower = out.lower()
    return any(marker in lower for marker in _REFUSED)


# The two org settings the toolkit READS and never writes. Both are web-only: they are
# reported by `GET /orgs/{org}` and absent from `PATCH /orgs/{org}`, so the maintainer sets
# them once per semester org by hand (docs/DEPLOYMENT-CHECKLIST.md) and the digest says so
# while either is wrong. Named here, beside the settings this module DOES converge, so it
# is one list of "what an org has to be" rather than two.
MEMBERS_CAN_DELETE = "members_can_delete_repositories"
MEMBERS_CAN_PUBLISH = "members_can_change_repo_visibility"


def org_settings(org: str) -> dict | None:
    """`GET /orgs/{org}` - everything GitHub reports about one org, or None if it could
    not be read.

    The read half of `converge_org_settings`, and the only way to see the two switches
    above at all: they are in this payload and in no PATCH body. None is "we could not
    look", which every caller has to tell from "the setting is wrong" - a rate limit must
    not report a correctly configured org as broken."""
    code, out = gh("api", f"orgs/{org}")
    if code != 0:
        log_err(f"could not read {org}'s settings: {out[:160]}")
        return None
    try:
        settings = json.loads(out)
    except ValueError:
        log_err(f"could not parse {org}'s settings")
        return None
    return settings if isinstance(settings, dict) else None


def converge_org_settings(org: str, *, private_forks: bool = False) -> int:
    """Tighten one org: base permissions, member repo creation, and 2FA where possible.

    Idempotent, and run on every nightly refresh as well as at bootstrap. These were set
    once, at bootstrap, and never revisited, so every org bootstrapped before the
    tightening still handed each member `read` on the unreleased materials, the model
    solutions and the `solution` branches.

    `private_forks` is the third setting, and the odd one out: it LOOSENS - so it is
    asked for, not assumed, sent in a PATCH of its own (see below), and only a SEMESTER
    asks. A semester's materials repo is
    private, and GitHub refuses a fork of a private repo unless its org allows it, so
    the Fork button students are told to press was simply absent; there it grants
    nothing, because a fork carries the reader's own access and a student who can fork
    the materials could already read them. A COURSE org is the opposite case: it holds
    the unreleased materials, the model solutions and the hidden tests, its members are
    faculty who already have push where they need it, and a private fork there is an
    uncontrolled copy of the solutions in somebody's personal account, gaining nobody
    anything.

    Base permissions matter in BOTH org kinds. A semester holds students; a COURSE org holds
    the materials students must not see, and at GitHub's default of `read` every member of
    it (every TA, every visiting instructor, anyone ever added for one semester) could read
    all of it. Faculty access comes from the team grants (access.converge_faculty_access),
    not from being a member, so nobody who should have access loses it.

    Returns the number of PATCHes that FAILED - the two GitHub can REFUSE excluded, each
    named in the log instead. It refuses `two_factor_requirement_enabled` while any member
    still has 2FA off, and `members_can_fork_private_repositories` wherever an enterprise
    policy above the org forbids private forks or the plan does not carry the setting.
    Both are facts about the account rather than broken convergences: counting them would
    red this cron in every such org every night for something no re-run can fix. A fork
    PATCH that failed any OTHER way - the request never got there, the token lost its
    scope - is still a failure."""
    failures = 0
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"orgs/{org}",
        "--field",
        "default_repository_permission=none",
        "--field",
        "members_can_create_repositories=false",
    )
    if code == 0:
        log_ok(f"{org} tightened (base permission none, no member repo creation)")
    else:
        failures += 1
        log_err(f"could not tighten {org}: {out[:120]}")

    # Its OWN patch, not a third field on the one above. GitHub validates a PATCH body as
    # a unit, so a refused fork field - an enterprise policy above the org forbids private
    # forks, a plan does not carry the setting - would take the two tightening fields down
    # with it, and the org would sit at GitHub's default of `read` for every member on
    # every repo behind a log line about forking.
    if private_forks:
        code, out = gh(
            "api",
            "--method",
            "PATCH",
            f"orgs/{org}",
            "--field",
            "members_can_fork_private_repositories=true",
        )
        if code == 0:
            log_ok(f"{org}: private repos forkable")
        elif _is_refusal(out):
            # Like 2FA below: GitHub answered, and the answer is no. Nothing in this org
            # can change that, so counting it would leave every nightly refresh red for
            # ever - and the button students are told to press is missing either way,
            # which is what the line has to say.
            log(
                f"  [warn] {org}: private repos not forkable - GitHub refused it "
                f"({out[:80]}). Students cannot fork the materials until an owner "
                f"allows private forks."
            )
        else:
            failures += 1
            log_err(f"could not let {org} fork its private repos: {out[:120]}")

    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"orgs/{org}",
        "--field",
        "two_factor_requirement_enabled=true",
    )
    if code == 0:
        log_ok(f"{org}: 2FA required of every member")
    else:
        without = members_without_2fa(org)
        log(
            f"  [warn] {org}: 2FA not enforced: "
            + (
                f"{without} members without 2FA"
                if without is not None
                else f"could not count the members without it ({out[:80]})"
            )
        )
    return failures


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


# GitHub's REST reads can answer 404 for a team for several minutes after it was created,
# while GraphQL already sees it - measured at 11s to about 5 minutes. A team created at 10:16
# could not have its members listed by Sync membership at 10:21, so the reconcile aborted
# and the second joiner waited for a manual re-run.
#
# The run that CREATES a team never reads it (see `reconcile_team_members(just_created=)`).
# The waiting is for the run that finds it already there - a later run, or the other of two
# workflows fired by the same teams.csv push - and is bounded by a PER-PROCESS budget. Once
# one ladder's worth of waiting is spent, every later 404 in the same run answers at once -
# so a sync over many teams, or one that meets a team that really is gone, adds at most
# `_LAG_BUDGET` seconds in total, never that much per team. Five minutes sits well inside the
# 30 and 60 minute ceilings of the jobs that reconcile teams (Sync membership, Scheduled
# release).
_LAG_DELAYS = (15, 30, 60, 90, 105)
_LAG_BUDGET = sum(_LAG_DELAYS)
_lag_spent = 0
# At module level so a test can spend the budget without spending the time.
_sleep = time.sleep


def _gh_riding_team_lag(*args: str) -> tuple[int, str]:
    """`gh(*args)` for a READ on a team, repeating a 404 while this run's lag budget lasts.

    Only a 404 is repeated: every other failure is already `gh`'s retry ladder's business,
    and comes straight back. If every attempt 404s the last answer is returned unchanged,
    so the caller's "could not be read" path is exactly what it was.

    Reads only. A membership PUT is not routed here: GitHub accepted one straight after
    creating the team in the incident above (the first joiner was added by the creating
    run), and a 404 on it is also GitHub's answer for a login that no longer exists - a
    renamed student, every night - which would spend the budget on something no wait can
    fix."""
    global _lag_spent
    code, out = gh(*args)
    for delay in _LAG_DELAYS:
        if code == 0 or not is_missing_resource(out):
            break
        if _lag_spent + delay > _LAG_BUDGET:
            break
        log(f"  [wait] team not visible to the API yet (404), retrying in {delay}s")
        _lag_spent += delay
        _sleep(delay)
        code, out = gh(*args)
    return code, out


def _team_member_rows(org: str, team_slug: str) -> dict[str, str] | None:
    """`{login: GitHub id}` for a team's current members - the ONE listing behind both
    public readers - or None if it could not be READ.

    None (a non-zero exit OR unparseable JSON) must never be conflated with an empty team:
    reconciling against an unreadable team would add or prune blind. Mirrors
    get_org_owners."""
    code, out = _gh_riding_team_lag(
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


def list_teams(org: str) -> dict[str, str] | None:
    """`{slug: description}` for every team in `org`, or None if it could not be READ -
    never an empty dict, which would read as "no teams" to a caller deciding what to
    reconcile."""
    code, out = gh("api", f"orgs/{org}/teams?per_page=100", "--paginate")
    if code != 0:
        log_err(f"could not list the teams of {org}: {out[:200]}")
        return None
    try:
        return {t["slug"]: t.get("description") or "" for t in json.loads(out)}
    except (json.JSONDecodeError, KeyError, TypeError):
        log_err(f"unparseable team listing for {org}: {out[:200]}")
        return None


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


# Every membership change `reconcile_team_members` has made in this process - or, on a
# dry run, would have made. A tally rather than a return value because every caller sums
# the function's ERROR count, and changes are what Check staff access reports: the
# console reads it off `membership_changes()` around one run (`sync_membership.main`).
_CHANGES = {"added": 0, "removed": 0}


def membership_changes() -> dict[str, int]:
    """`{"added": n, "removed": m}` since the last `reset_membership_changes()`."""
    return dict(_CHANGES)


def reset_membership_changes() -> None:
    _CHANGES.update(added=0, removed=0)


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
    just_created: bool = False,
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

    `just_created` says the caller's own `create_team_outcome` made this team a moment ago.
    Its membership is then known without asking - at most the acting login, which GitHub
    auto-adds as the creator - and asking would be worse than useless: GitHub's REST reads
    404 a new team for up to minutes, which would abort the reconcile. Adds go ahead from
    there; the acting login is never pruned, so the prune has nothing it could remove.
    """
    if just_created:
        acting = acting_login()
        current: set[str] | None = {acting} if acting else set()
    else:
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
            log_person(f"    PREVIEW add {handle} -> {org}/{team}")
            _CHANGES["added"] += 1
        elif add_team_member(org, team, handle):
            log_person(f"  [ok] {handle} -> {org}/{team}")
            _CHANGES["added"] += 1
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
                log_person(f"    PREVIEW remove {handle} <- {org}/{team}")
                _CHANGES["removed"] += 1
            elif remove_team_member(org, team, handle):
                log_person(f"  [ok] removed {handle} from {org}/{team}")
                _CHANGES["removed"] += 1
            else:
                errors += 1
    return errors
