"""dsl-course sync-teams -- materialise per-(assignment, team) GitHub Teams from students.csv.

The group "access" half, mirroring sync_roster for enrolment. The `team:<number>` columns of
`students.csv` (in the semester's private semester-config) are the single source of truth
for who is in which project team for which assignment (`teams`); this reconciles a GitHub
Team `<assignment>-<team>` from them so the team's repo access tracks the file. Idempotent.

The Teams are a DOWNSTREAM PROJECTION of the CSV, never authoritative, so they can't drift -
a re-sync overwrites them to match. Provisioning a group assignment grants the matching team
on the group's repo (so post-sync membership edits propagate to access automatically).

With --prune, members no longer in the file are removed from their team (off-boarding) - never
an org Owner or the acting login (see gh_teams.reconcile_team_members); off by default here so a
standalone/manual run never silently revokes access. A project team whose LAST member has gone
(moved to another team) is reconciled to empty as well - see
`emptied_teams` - and left in place, with its repo: nothing here deletes or archives. The seeded **Sync membership** workflow (dsl_course.sync_membership) always calls this
with prune=True - config is meant to be the live truth there; this module's own off-by-default
is only for ad-hoc/CLI use outside that workflow.

Usage:
    python3 -m dsl_course.sync_teams --semester-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_teams --semester-org hertie-dsl-demo-f2026 --prune
"""

from __future__ import annotations

import sys

from . import roster, schedule, teams
from .gh_teams import (
    CREATED,
    create_team_outcome,
    list_teams,
    reconcile_team_members,
)
from .log import CLIParser, add_preview_flag, log_err, log_ok, log_person, log_step

# The naming rules live with the file's parser, which is the only thing that can refuse a
# row for breaking them. Imported for this module's own reconcile below, not re-exported:
# everything else asks `teams` for them.
from .teams import is_reserved_slug, team_slug


def desired_teams(per: dict[str, dict[str, list[str]]]) -> dict[str, set[str]]:
    """Flatten parsed teams {assignment: {team: [handles]}} to {team_slug: {handles}}.

    team_slug lower-cases, so two team names differing only in case (`Team-X`/`team-x`)
    map to the same slug: UNION their members rather than overwriting, or one team's
    members would vanish from the reconcile. A RESERVED slug is dropped, loudly."""
    wanted: dict[str, set[str]] = {}
    for assignment, groups in per.items():
        for team, members in groups.items():
            slug = team_slug(assignment, team)
            if is_reserved_slug(slug):
                log_err(
                    f"  ! a students.csv team cell ({assignment}, {team}) names the "
                    f"faculty team `{slug}` - refusing to manage it; clear the cell"
                )
                continue
            wanted.setdefault(slug, set()).update(members)
    return wanted


# What `ensure_team` stamps on every team it creates - and so the mark of a team this module
# owns. `emptied_teams` touches nothing without it.
PROJECT_TEAM_DESCRIPTION = "Project team (auto-managed from teams.csv)"
# Kept as written: it is the mark `emptied_teams` recognises on every team made before
# the team columns moved into students.csv.
# `secret`, not `closed`: GitHub shows every org member a "Request to join" button on a
# visible team, which goes around the Join team form, mails the owners with no context, and
# is undone by the next pruning sync if approved. A secret team is visible only to its own
# members and to org owners; students see the teams on each assignment's page.
# A team made `closed` before this converges on its next `ensure_team`.
PROJECT_TEAM_PRIVACY = "secret"


def emptied_teams(
    existing: dict[str, str], wanted: dict[str, set[str]], keys: list[str]
) -> list[str]:
    """Project teams that exist on GitHub but no longer have a member in students.csv.

    The hole this closes: the reconcile below walks the teams the CSV names, so a team
    whose last member MOVED out (a Join or Create from someone already in a team) was never
    visited again, and its GitHub team - and the push access to its repo - kept the leaver.

    A team qualifies only if ALL of these hold, because emptying the wrong one evicts
    people from something this module does not own:
    - it carries `PROJECT_TEAM_DESCRIPTION`, the mark `ensure_team` stamps on creation;
    - its slug is `<assignment>-...` for an assignment key in the semester's schedule;
    - it is not a role team (`teams.is_reserved_slug`: instructors, students, auditors,
      course-admin, instructors-*), whatever else is true of it;
    - students.csv names no member for it."""
    prefixes = tuple(f"{k.lower()}-" for k in keys)
    if not prefixes:
        return []
    return sorted(
        slug
        for slug, description in existing.items()
        if description == PROJECT_TEAM_DESCRIPTION
        and slug.startswith(prefixes)
        and not is_reserved_slug(slug)
        and slug not in wanted
    )


def ensure_team(
    org: str,
    slug: str,
    members: set[str],
    prune: bool,
    keep_ids: set[str] = frozenset(),
) -> bool:
    """Create the team (idempotent) and reconcile its membership to `members`.

    Reconciliation goes through gh_teams.reconcile_team_members so pruning inherits its
    guard: an org Owner - or the acting login, which GitHub auto-adds as a member of
    whatever team it creates - is never removed. Without it, a maintainer or the bot
    sitting in a project team would be evicted on the next pruning sync. `keep_ids` are
    the members' GitHub ids: a member who renamed their account keeps their place while
    the relink that moves their handle is still pending.

    A team this call made is reconciled without reading it back: GitHub's REST API 404s a
    new team for up to minutes, and its membership is known (see `just_created`)."""
    outcome = create_team_outcome(
        org, slug, description=PROJECT_TEAM_DESCRIPTION, privacy=PROJECT_TEAM_PRIVACY
    )
    if outcome is None:
        return False
    return (
        reconcile_team_members(
            org,
            slug,
            members,
            prune=prune,
            keep_ids=keep_ids,
            just_created=outcome == CREATED,
        )
        == 0
    )


def known_handles(students: list[roster.Student] | None) -> set[str]:
    """The onboarded roster handles - the only accounts a team may add.

    Adding a handle to a GitHub Team also INVITES it to the org if it isn't a member
    yet, so an unvetted handle would invite an arbitrary stranger. Team members are read
    off the roster itself (`teams.parse`), so this holds by construction; the handout and
    the collection still narrow it to their own view of the roster (enrolled only)."""
    return {s.github_handle for s in students or [] if s.onboarded}


def vet_handles(
    members: list[str], allowed_by_fold: dict[str, str]
) -> tuple[list[str], list[str]]:
    """Split `members` against a fold-keyed allowlist (`{handle.casefold(): handle}` built
    from `known_handles`). Returns (accepted, rejected): accepted in the roster's canonical
    casing (a case-only mismatch is the same GitHub account), rejected the raw handles that
    are NOT onboarded roster members. This is the single home of the stranger-invite guard -
    adding a rejected handle to a team would INVITE an arbitrary account into the private org,
    so every path that materialises a team (sync and the assignment release) vets through here
    rather than re-implementing it. Callers log/count rejections in their own words."""
    accepted: list[str] = []
    rejected: list[str] = []
    for m in members:
        canonical = allowed_by_fold.get(m.casefold())
        (accepted if canonical is not None else rejected).append(canonical or m)
    return accepted, rejected


def vet_groups(
    groups: dict[str, list[str]], students: list[roster.Student] | None
) -> list[tuple[str, list[str], list[str]]]:
    """`(group name, accepted, rejected)` for every group, name-sorted.

    The fold-keyed allowlist is built ONCE for the whole map and applied by `vet_handles`,
    so the three readers of the teams - this module's team sync, the assignment handout and
    the collection that grades it - cannot vet a row differently. They used to build the
    same allowlist three times, and a handle good enough to be handed a repo but not good
    enough to earn a grade row is the failure that costs a student marks.

    `students` is the caller's own view of the roster (the team sync allows every onboarded
    row; the handout and the collection allow enrolled rows only), and every caller reports
    its rejections in its own words - all three logs are world-readable, and what each may
    name differs."""
    allowed_by_fold = {h.casefold(): h for h in known_handles(students)}
    return [
        (name, *vet_handles(members, allowed_by_fold))
        for name, members in sorted(groups.items())
    ]


def _emptied(semester_org: str, wanted: dict[str, set[str]]) -> list[str] | None:
    """`emptied_teams` over the org's teams and the schedule's keys; None when the team
    listing could not be read."""
    existing = list_teams(semester_org)
    if existing is None:
        return None
    return emptied_teams(
        existing, wanted, list(schedule.load(semester_org).assignments)
    )


def _empty_the_emptied(
    semester_org: str, wanted: dict[str, set[str]], dry_run: bool
) -> int:
    """Reconcile every `emptied_teams` team to no members. Returns the error count.

    Through `reconcile_team_members`, so its guard holds here too: an org Owner and the
    acting login are never removed, and an unreadable owner list skips the prune whole.
    A team listing that could not be read empties nothing - absence has to be a real
    answer before anybody loses access on the strength of it."""
    emptied = _emptied(semester_org, wanted)
    if emptied is None:
        return 1
    errors = 0
    for slug in emptied:
        if dry_run:
            log_person(f"    PREVIEW team {slug}: now empty - would remove its members")
            continue
        errors += reconcile_team_members(semester_org, slug, set(), prune=True)
    return errors


def sync(semester_org: str, prune: bool = False, dry_run: bool = False) -> int:
    per = teams.load_or_none(semester_org)
    if per is None:
        # ABSENT students.csv - deleted, or renamed by hand - is not an empty one:
        # reconciling every project team to nobody would take every student's push on
        # their group repo. Hold; blank team cells are how to empty them.
        emptied = _emptied(semester_org, {}) if prune else []
        if emptied is None:
            # The team listing failed: red, as `_empty_the_emptied` is on the same read.
            log_err(f"could not list the teams of {semester_org} - nothing synced")
            return 1
        if emptied:
            log_err(
                f"{roster.STUDENTS_PATH} is absent in {semester_org}/{roster.CONFIG_REPO} but "
                f"project teams exist - nobody is removed from them until it is restored"
            )
        else:
            log_ok(f"no {roster.STUDENTS_PATH} yet - nothing to sync.")
        return 0
    wanted = desired_teams(per)
    if not wanted:
        log_ok("no project teams defined yet - nothing to sync.")
        return _empty_the_emptied(semester_org, wanted, dry_run) if prune else 0
    # Present: `teams.load_or_none` read it above. Members are its own onboarded handles.
    students = roster.load(semester_org) or []
    log_step(f"Materialising {len(wanted)} project team(s) in {semester_org}")
    # Each onboarded member's GitHub id, so a renamed one keeps their place (`ensure_team`).
    ids = {
        s.github_handle: s.github_id for s in students if s.github_id and s.onboarded
    }
    errors = 0
    for slug, accepted, _rejected in vet_groups(
        {s: sorted(members) for s, members in wanted.items()}, students
    ):
        members = set(accepted)
        if dry_run:
            log_person(
                f"    PREVIEW team {slug}: {', '.join('@' + m for m in sorted(members))}"
            )
        elif not ensure_team(
            semester_org,
            slug,
            members,
            prune,
            keep_ids={ids[m] for m in members if m in ids},
        ):
            errors += 1
    if prune:
        errors += _empty_the_emptied(semester_org, wanted, dry_run)
    return errors


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("--semester-org", required=True)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove team members no longer in students.csv.",
    )
    add_preview_flag(parser, "Report the team changes; make none (default).")
    args = parser.parse_args()

    errors = sync(args.semester_org, prune=args.prune, dry_run=args.preview)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
