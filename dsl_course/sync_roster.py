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
from .utils import (
    log_err,
    log_ok,
    log_step,
    log_verbose,
    reconcile_team_members,
    set_org_membership,
)

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
    for team, handles in wanted.items():
        for handle in sorted(handles):
            if dry_run:
                log_verbose(f"    DRY-RUN enroll: {handle} -> org member")
            elif not set_org_membership(cohort_org, handle, role="member"):
                errors += 1
        # Team membership via the shared reconcile so pruning inherits its guard:
        # an org Owner (or the acting bot) on the roster is never evicted.
        errors += reconcile_team_members(
            cohort_org, team, handles, prune=prune, dry_run=dry_run
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
