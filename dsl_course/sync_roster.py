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

from . import roster
from .discovery import list_org_repos
from .gh_teams import reconcile_team_members, set_org_membership
from .log import log_err, log_ok, log_step, log_verbose
from .repos import is_collaborator, remove_collaborator

TEAM = "students"  # enrolled rows
AUDITOR_TEAM = "auditors"  # read-only rows


def desired_members(students: list[roster.Student]) -> dict[str, set[str]]:
    """{role team: handles} for the two cohort role teams, onboarded rows only.

    A not-yet-onboarded row has no handle to add, so it isn't wanted anywhere yet. Both
    keys are always present, so a pruning sync empties a team that should be empty rather
    than leaving yesterday's members in it."""
    onboarded = [s for s in students if s.onboarded]
    return {
        TEAM: {s.github_handle for s in onboarded if s.is_enrolled},
        AUDITOR_TEAM: {s.github_handle for s in onboarded if s.is_auditor},
    }


def desired_ids(students: list[roster.Student]) -> dict[str, set[str]]:
    """`{role team: GitHub ids}` for exactly the rows `desired_members` names.

    Handed to the prune as `keep_ids`. A GitHub login is renameable and an id is not, so a
    student who renames their account leaves the roster's `github_handle` cell stale while
    still being on the roster: the add of the old login 404s and the prune evicts the new
    one, every night, until somebody hand-edits the CSV. Keyed per TEAM, not cohort-wide -
    a role change is meant to prune the handle out of the team it left."""
    onboarded = [s for s in students if s.onboarded and s.github_id]
    return {
        TEAM: {s.github_id for s in onboarded if s.is_enrolled},
        AUDITOR_TEAM: {s.github_id for s in onboarded if s.is_auditor},
    }


def submission_repo_suffixes(repos: list[dict]) -> list[tuple[str, str]]:
    """`(repo, suffix)` for every `<template>-<suffix>` repo in a cohort org's listing.

    The same rule `discovery.is_student_repo` uses: a submission repo is generated from one
    of the org's cohort assignment templates, so its name is that template's name plus a
    suffix. The suffix is a student's HANDLE for an individual assignment and a TEAM name
    for a group one - which is exactly why nothing downstream acts on a suffix without
    asking GitHub whether it is really a collaborator."""
    templates = sorted(
        (r["name"] for r in repos if r.get("isTemplate")), key=len, reverse=True
    )
    out = []
    # Templates themselves are excluded: `assignment-4-project` is a repo in this listing
    # AND starts with `assignment-4-`, so a cohort with both templates would otherwise
    # read one of its own templates as a submission repo belonging to `project`.
    for repo in sorted(r["name"] for r in repos if not r.get("isTemplate")):
        for template in templates:  # longest first, so a nested slug wins
            if repo.startswith(f"{template}-"):
                out.append((repo, repo[len(template) + 1 :]))
                break
    return out


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

    Deliberately narrow. Only the login the repo is NAMED after is ever revoked, and only
    once GitHub confirms it is a direct collaborator - a group repo's suffix is a team
    name, faculty and the bot hold their access through teams, and a repo name is not a
    reason to take anyone's access away. `on_roster` is casefolded, because GitHub logins
    are case-insensitive and a case-only difference is the same account."""
    stale = [
        (repo, suffix)
        for repo, suffix in submission_repo_suffixes(list_org_repos(cohort_org))
        if suffix.casefold() not in on_roster
    ]
    errors = 0
    revoked = 0
    for repo, suffix in stale:
        present = is_collaborator(cohort_org, repo, suffix)
        if present is None:  # unreadable - never guess, in either direction
            errors += 1
            continue
        if not present:
            continue  # a team name, or already revoked
        if dry_run:
            log_verbose(f"    DRY-RUN revoke {suffix} <- {cohort_org}/{repo}")
            revoked += 1
        elif remove_collaborator(cohort_org, repo, suffix):
            log_verbose(f"  [ok] revoked {suffix} from {cohort_org}/{repo}")
            revoked += 1
        else:
            errors += 1
    if revoked:
        # Only DIRECT collaborator grants are counted, because only those were removed:
        # `is_collaborator` reads the affiliation=direct listing, so a repo whose suffix
        # merely matches somebody with team or owner access is never one of these.
        log_ok(
            f"{revoked} direct submission-repo grant(s) revoked for handle(s) no longer "
            f"on the roster{' (dry run)' if dry_run else ''}"
        )
    return errors


def sync(cohort_org: str, prune: bool = False, dry_run: bool = False) -> int:
    students = roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1
    # An empty roster (header only - a freshly bootstrapped cohort) is a valid state,
    # not an error: reconcile both role teams to empty like any other edit.
    wanted = desired_members(students)
    keep_ids = desired_ids(students)
    log_step(
        f"Materialising access for {len(wanted[TEAM])} onboarded student(s) + "
        f"{len(wanted[AUDITOR_TEAM])} auditor(s) in {cohort_org}"
    )

    errors = 0
    for team, handles in wanted.items():
        for handle in sorted(handles):
            if dry_run:
                log_verbose(f"    DRY-RUN enroll: {handle} -> org member")
            elif not set_org_membership(cohort_org, handle, role="member"):
                errors += 1
        # Team membership via the shared reconcile so pruning inherits its guard:
        # an org Owner (or the acting bot) on the roster is never evicted.
        errors += reconcile_team_members(
            cohort_org,
            team,
            handles,
            prune=prune,
            dry_run=dry_run,
            keep_ids=keep_ids[team],
        )
    if prune:
        # Behind the same flag as the team prune, and for the same reason: this is the
        # other half of off-boarding, and an ad-hoc run must not silently revoke anything.
        errors += revoke_offboarded_access(
            cohort_org,
            {h.casefold() for handles in wanted.values() for h in handles},
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
