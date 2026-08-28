"""dsl-course sync-teams -- materialise per-(assignment, team) GitHub Teams from teams.csv.

The group "access" half, mirroring sync_roster for enrolment. `teams.csv` (in the cohort's
private classroom-config) is the single source of truth for who is in which project team for
which assignment; this reconciles a GitHub Team `<assignment>-<team>` from each row so the
team's repo access + @mentions track the CSV. Idempotent.

The Teams are a DOWNSTREAM PROJECTION of the CSV, never authoritative, so they can't drift -
a re-sync overwrites them to match. Provisioning a group assignment grants the matching team
on the group's repo (so post-sync membership edits propagate to access automatically).

With --prune, members no longer in the CSV are removed from their team (off-boarding) - never
an org Owner or the acting login (see utils.reconcile_team_members); off by default here so a
standalone/manual run never silently revokes access. Emptied teams are left in place. The seeded **Sync membership** workflow (dsl_course.sync_membership) always calls this
with prune=True - config is meant to be the live truth there; this module's own off-by-default
is only for ad-hoc/CLI use outside that workflow.

Usage:
    python3 -m dsl_course.sync_teams --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_teams --cohort-org hertie-dsl-demo-f2026 --prune
"""

from __future__ import annotations

import argparse
import sys

from . import roster, teams
from .log import log_err, log_ok, log_step, log_verbose
from .utils import create_team, reconcile_team_members


def team_slug(assignment: str, team: str) -> str:
    """The GitHub Team name/slug materialised for one (assignment, team) pair.

    Assignment-prefixed so a team name reused across assignments (e.g. `wizards` in two
    projects) maps to distinct org-unique teams. Lower-cased to match the slug GitHub
    derives from the team name."""
    return f"{assignment}-{team}".lower()


# Team slugs students may never materialise. teams.csv is STUDENT-written (the public
# Join-team issue form), and `team_slug("course", "admin")` is `course-admin` - the faculty
# team that holds admin on every repo in the cohort. Reconciling that slug from teams.csv
# would add the student to it and prune the real admins. The workflow refuses these at the
# form; this is the backstop for a row that reached the CSV any other way.
RESERVED_TEAM_SLUGS = frozenset({"course-admin", "instructors", "students", "auditors"})


def is_reserved_slug(slug: str) -> bool:
    return slug in RESERVED_TEAM_SLUGS or slug.startswith("instructors-")


def desired_teams(per: dict[str, dict[str, list[str]]]) -> dict[str, set[str]]:
    """Flatten parsed teams.csv {assignment: {team: [handles]}} to {team_slug: {handles}}.

    team_slug lower-cases, so two team names differing only in case (`Team-X`/`team-x`)
    map to the same slug: UNION their members rather than overwriting, or one team's
    members would vanish from the reconcile. A RESERVED slug is dropped, loudly."""
    wanted: dict[str, set[str]] = {}
    for assignment, groups in per.items():
        for team, members in groups.items():
            slug = team_slug(assignment, team)
            if is_reserved_slug(slug):
                log_err(
                    f"  ! teams.csv row ({assignment}, {team}) names the faculty team "
                    f"`{slug}` - refusing to manage it from teams.csv; remove the row"
                )
                continue
            wanted.setdefault(slug, set()).update(members)
    return wanted


def ensure_team(org: str, slug: str, members: set[str], prune: bool) -> bool:
    """Create the team (idempotent) and reconcile its membership to `members`.

    Reconciliation goes through utils.reconcile_team_members so pruning inherits its
    guard: an org Owner - or the acting login, which GitHub auto-adds as a member of
    whatever team it creates - is never removed. Without it, a maintainer or the bot
    sitting in a project team would be evicted on the next pruning sync."""
    ok = create_team(
        org, slug, description="Project team (auto-managed from teams.csv)"
    )
    if not ok:
        return False
    return reconcile_team_members(org, slug, members, prune=prune) == 0


def known_handles(students: list[roster.Student] | None) -> set[str]:
    """The onboarded roster handles - the only accounts teams.csv may add.

    Adding a handle to a GitHub Team also INVITES it to the org if it isn't a member
    yet, so an unvetted teams.csv handle (a typo, or a placeholder name that happens
    to collide with a real GitHub account) would invite an arbitrary stranger. The
    roster is the SSOT of who belongs to the cohort; teams.csv only groups them."""
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


def sync(cohort_org: str, prune: bool = False, dry_run: bool = False) -> int:
    wanted = desired_teams(teams.load(cohort_org))
    if not wanted:
        log_ok("no project teams defined yet - nothing to sync.")
        return 0
    students = roster.load(cohort_org)
    if students is None:
        # The roster is UNREADABLE (absent, or a transient read failure) - distinct from a
        # present-but-empty one. Building the allowlist from None gives an empty set, so a
        # pruning reconcile would then EVICT every member from every project team. Refuse to
        # touch anything, mirroring sync_roster's abort on the same signal, rather than
        # mass-evicting and reporting red. (roster.load has already logged the cause.)
        log_err(
            f"roster unreadable in {cohort_org} - refusing to reconcile project teams "
            f"(an empty allowlist would evict every team member)"
        )
        return 1
    log_step(f"Materialising {len(wanted)} project team(s) in {cohort_org}")
    # Fold-keyed so a teams.csv handle that differs only in case from its roster entry
    # (same GitHub account) matches; the roster's canonical casing is what gets added.
    allowed_by_fold = {h.casefold(): h for h in known_handles(students)}
    errors = 0
    for slug in sorted(wanted):
        accepted, rejected = vet_handles(sorted(wanted[slug]), allowed_by_fold)
        for member in rejected:
            # Names a handle a STUDENT typed into teams.csv, so the detail is verbose-only
            # (this workflow's log is world-readable). The count below is what a faculty
            # member acts on, and it names nobody.
            log_verbose(
                f"    {member} in teams.csv is not an onboarded roster handle - "
                f"not adding to {slug} (would invite an arbitrary GitHub account)"
            )
        if rejected:
            errors += len(rejected)
            log_err(
                f"{len(rejected)} handle(s) in teams.csv are not onboarded roster handles "
                f"- not added to {slug} (they would invite arbitrary GitHub accounts). "
                f"Re-run the CLI locally with DSL_VERBOSE=1 to see which."
            )
        members = set(accepted)
        if dry_run:
            log_verbose(
                f"    DRY-RUN team {slug}: {', '.join('@' + m for m in sorted(members))}"
            )
        elif not ensure_team(cohort_org, slug, members, prune):
            errors += 1
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove team members no longer in teams.csv.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    errors = sync(args.cohort_org, prune=args.prune, dry_run=args.dry_run)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
