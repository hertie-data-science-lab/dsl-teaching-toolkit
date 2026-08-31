"""dsl-course sync-roster -- materialise org + team access from students.csv.

The enrolment "access" half: a single idempotent reconcile that ensures every onboarded
row in the cohort's students.csv is (a) a member of the cohort org and (b) in the role
team its `role` column names - `students` for enrolled rows, `auditors` for auditors.
Both teams carry cohort-private read on released materials; only `students` rows get
assignment repos and gradebooks (see dsl_course.assign / dsl_course.grades).

Students normally grant themselves on Join (templates/welcome/onboard.yml); this is the
faculty & instructors true-up - edit students.csv, then re-run to reconcile the whole team to the roster.

With --prune, handles no longer wanted in a team are removed from it (off-boarding, and
the second half of a role change: the handle joins its new role team and is pruned from
the old one); off by default here so a standalone/manual run never silently revokes
access. The seeded **Sync membership** workflow (dsl_course.sync_membership) always calls
this with prune=True - config is meant to be the live truth there; this module's own
off-by-default is only for ad-hoc/CLI use outside that workflow.

Usage:
    python3 -m dsl_course.sync_roster --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_roster --cohort-org hertie-dsl-demo-f2026 --prune
"""

from __future__ import annotations

import argparse
import sys

from . import roster, teams
from .course import AUDITORS_TEAM, STUDENTS_TEAM, submission_repo, submission_suffix
from .discovery import classify_repos, list_org_repos
from .gh_teams import reconcile_team_members, set_org_membership
from .log import log_err, log_ok, log_person, log_step
from .repos import (
    cancel_invitation,
    is_collaborator,
    pending_invitations,
    remove_collaborator,
)

TEAM = STUDENTS_TEAM  # enrolled rows
AUDITOR_TEAM = AUDITORS_TEAM  # read-only rows


def desired_members(students: list[roster.Student]) -> dict[str, list[roster.Student]]:
    """`{role team: the onboarded rows that belong in it}` - the ONE partition of a roster
    into the two cohort role teams.

    A not-yet-onboarded row has no handle to add, so it isn't wanted anywhere yet. Both
    keys are always present, so a pruning sync empties a team that should be empty rather
    than leaving yesterday's members in it.

    ROWS, not handles, because the reconcile needs both halves of each: the login to add,
    and the immutable GitHub id it is handed as `keep_ids`. A login is renameable and an
    id is not, so a student who renames their account leaves the roster's `github_handle`
    cell stale while still being on the roster - the add of the old login 404s and the
    prune evicts the new one, every night, until somebody hand-edits the CSV."""
    onboarded = [s for s in students if s.onboarded]
    return {
        TEAM: [s for s in onboarded if s.is_enrolled],
        AUDITOR_TEAM: [s for s in onboarded if s.is_auditor],
    }


def submission_repo_suffixes(repos: list[dict]) -> list[tuple[str, str]]:
    """`(repo, suffix)` for every submission repo in a cohort org's listing.

    `discovery.classify_repos` names the template each one derives from; the suffix is
    what is left. That suffix is a student's HANDLE for an individual assignment and a
    TEAM name for a group one - which is exactly why nothing downstream acts on one
    without first asking GitHub whether it is really a collaborator."""
    return [
        (repo, submission_suffix(repo, template))
        for repo, template in sorted(classify_repos(repos).items())
        if template
    ]


def revoke_offboarded_access(
    cohort_org: str, on_roster: set[str], dry_run: bool = False
) -> int:
    """Revoke the collaborator grant an off-boarded student still holds on the submission
    repos named after them. Returns the error count.

    Pruning the role team was never the whole of off-boarding: an individual assignment
    grants the student DIRECTLY, as a `maintain` collaborator (see `assign.provision_one` -
    the individual path is collaborator-based by design, so that a repo works before the
    org invite is accepted). A handle deleted from students.csv therefore kept full write
    on every assignment repo they had ever been handed, indefinitely, while every report
    said they had been removed.

    A grant made before the org invite was accepted is a pending INVITATION rather than a
    collaborator row, so it is cancelled too - otherwise accepting it later hands the
    access straight back. Org membership itself is not touched here.

    Deliberately narrow. Only the login the repo is NAMED after is ever revoked, and only
    once GitHub confirms it is a direct collaborator or holds an invitation - a group
    repo's suffix is a team name, faculty and the bot hold their access through teams, and
    a repo name is not a reason to take anyone's access away. `on_roster` is casefolded, because GitHub logins
    are case-insensitive and a case-only difference is the same account."""
    # teams.csv already says which repos are the GROUP ones, so asking GitHub whether a
    # team is a collaborator on its own repo is a paginated read per team repo per night,
    # for an answer that is always "no". Matched on the whole repo NAME rather than the
    # bare suffix: a team name and a student's handle live in the same namespace, and only
    # `<assignment>-<team>` says which of the two this repo is. An assignment renamed by
    # `cohort_dest_repo` does not match and simply keeps its probe.
    declared_team_repos = {
        submission_repo(key, team).casefold()
        for key, per_team in teams.load(cohort_org).items()
        for team in per_team
    }
    stale = [
        (repo, suffix)
        for repo, suffix in submission_repo_suffixes(list_org_repos(cohort_org))
        if suffix.casefold() not in on_roster
        and repo.casefold() not in declared_team_repos
    ]
    errors = 0
    revoked = 0
    for repo, suffix in stale:
        present = is_collaborator(cohort_org, repo, suffix)
        if present is None:  # unreadable - never guess, in either direction
            errors += 1
            continue
        if present:
            if dry_run:
                log_person(f"    DRY-RUN revoke {suffix} <- {cohort_org}/{repo}")
                revoked += 1
            elif remove_collaborator(cohort_org, repo, suffix):
                log_person(f"  [ok] revoked {suffix} from {cohort_org}/{repo}")
                revoked += 1
            else:
                errors += 1
        # A grant made before the org invite was accepted is a pending INVITATION, which
        # `is_collaborator` cannot see and `remove_collaborator` does not touch. Left live,
        # accepting it later hands `maintain` back to an off-boarded student.
        invitations = pending_invitations(cohort_org, repo, suffix)
        if invitations is None:
            errors += 1
            continue
        for invitation_id in invitations:
            if dry_run:
                log_person(f"    DRY-RUN cancel invite {suffix} <- {cohort_org}/{repo}")
                revoked += 1
            elif cancel_invitation(cohort_org, repo, invitation_id):
                log_person(f"  [ok] cancelled {suffix}'s invite to {cohort_org}/{repo}")
                revoked += 1
            else:
                errors += 1
    if revoked:
        # Only DIRECT grants and pending invitations are counted, because only those were
        # removed: `is_collaborator` reads the affiliation=direct listing, so a repo whose
        # suffix merely matches somebody with team or owner access is never one of these.
        log_ok(
            f"{revoked} direct submission-repo grant(s)/invite(s) revoked for handle(s) "
            f"no longer on the roster{' (dry run)' if dry_run else ''}"
        )
    return errors


def sync(cohort_org: str, prune: bool = False, dry_run: bool = False) -> int:
    students = roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1
    # An empty roster (header only - a freshly bootstrapped cohort) is a valid state,
    # not an error: reconcile both role teams to empty like any other edit.
    wanted = desired_members(students)
    log_step(
        f"Materialising access for {len(wanted[TEAM])} onboarded student(s) + "
        f"{len(wanted[AUDITOR_TEAM])} auditor(s) in {cohort_org}"
    )

    errors = 0
    for team, rows in wanted.items():
        handles = {s.github_handle for s in rows}
        for handle in sorted(handles):
            if dry_run:
                log_person(f"    DRY-RUN enrol: {handle} -> org member")
            elif not set_org_membership(cohort_org, handle, role="member"):
                errors += 1
        # Team membership via the shared reconcile so pruning inherits its guard:
        # an org Owner (or the acting bot) on the roster is never evicted. `keep_ids` is
        # keyed per TEAM, not cohort-wide - a role change is meant to prune the handle out
        # of the team it left.
        errors += reconcile_team_members(
            cohort_org,
            team,
            handles,
            prune=prune,
            dry_run=dry_run,
            keep_ids={s.github_id for s in rows if s.github_id},
        )
    if prune:
        # Behind the same flag as the team prune, and for the same reason: this is the
        # other half of off-boarding, and an ad-hoc run must not silently revoke anything.
        errors += revoke_offboarded_access(
            cohort_org,
            {s.github_handle.casefold() for rows in wanted.values() for s in rows},
            dry_run=dry_run,
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove team members no longer on the roster.",
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
