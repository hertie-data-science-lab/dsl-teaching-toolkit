"""dsl-course sync-membership -- consolidated roster + teams + faculty sync.

One entrypoint replacing three separate workflows' worth of orchestration:

- course_admins (from the course org's declared `people:` block) ALWAYS reconciles
  everywhere - the course org itself + every cohort registered under it
  (sync_faculty.sync_course_admins) - regardless of which cohort (if any) triggered
  this sync, since admin access is course-wide by design.
- Roster (students.csv), project teams (teams.csv), and each cohort's own
  instructors/TAs (classroom-config/people.yml, via
  sync_faculty.sync_cohort_instructors) additionally reconcile for whichever
  cohort(s) are in scope: one named cohort (--cohort-org, e.g. a push to that
  cohort's classroom-config), or every registered cohort (--all-cohorts, e.g. the
  daily cron - a full resync with no single cohort in context).

Every reconcile here is FULL (add + remove) - there is no --prune flag at this level;
config is the live truth, so a deleted roster row or a lapsed faculty `end` date
revokes access on the very next sync.

Usage:
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --all-cohorts
"""

from __future__ import annotations

import argparse
import sys

from . import seed, sync_faculty, sync_roster, sync_teams
from .utils import _acting_login, log_err, log_ok


def sync(
    course_org: str,
    cohort_org: str | None = None,
    all_cohorts: bool = False,
    dry_run: bool = False,
) -> int:
    # course_admins always reconciles everywhere, independent of which cohort (if
    # any) triggered this sync.
    all_registered = seed.discover_cohorts(course_org)
    if not all_registered:
        # An empty registry can be legitimate for a brand-new course org, so this does not
        # fail the run - but it must be VISIBLE, not a silent green "Sync complete": only
        # the course org's own course-admin gets reconciled, no cohort at all.
        log_err(
            f"no cohorts are registered under {course_org} "
            f"({seed.COHORTS_PATH} is empty or unset) - only course-admin on the course "
            f"org itself will be reconciled. Expected for a brand-new course org; a "
            f"problem if this course has live cohorts."
        )
    # A named cohort reaches here straight from a repository_dispatch's
    # `client_payload.cohort_org`, which is written by whoever holds a cohort's
    # DSL_BOT_TOKEN - a lower trust tier than the course org. Naming SOMEONE ELSE'S cohort
    # would have this run reconcile (and prune) that cohort's roster and teams. The
    # registry is the authority on which cohorts this course org owns, so a name that is
    # not in it is refused rather than acted on. Compared casefold: GitHub org names are
    # case-insensitive, and the registry's spelling need not match the dispatch's.
    if (
        cohort_org
        and all_registered
        and cohort_org.casefold() not in {c.casefold() for c in all_registered}
    ):
        log_err(
            f"{cohort_org} is not registered under {course_org} "
            f"({seed.COHORTS_PATH} lists {', '.join(sorted(all_registered))}) - refusing "
            f"to reconcile it. Register the cohort first if this is genuinely its course org."
        )
        return 1
    errors = sync_faculty.sync_course_admins(
        course_org, all_registered, dry_run=dry_run
    )

    # Roster/teams/instructors reconcile only for whichever cohort(s) are in scope -
    # not fanned out to every other, unrelated cohort.
    targets = (
        list(all_registered) if all_cohorts else ([cohort_org] if cohort_org else [])
    )
    content_repos = seed.discover_content_repos(course_org) if targets else []
    assignments = seed.discover_assignments(course_org) if targets else []
    for org in targets:
        # Per-cohort isolation: the read helpers now raise on non-404, so one cohort's
        # transient failure must not abort the whole batch (the lesson seed.refresh
        # applied). Log it, count it, and carry on to the next cohort.
        try:
            errors += sync_roster.sync(org, prune=True, dry_run=dry_run)
            errors += sync_teams.sync(org, prune=True, dry_run=dry_run)
            errors += sync_faculty.sync_cohort_instructors(
                course_org, org, content_repos, assignments, dry_run=dry_run
            )
        except Exception as exc:
            # Broad by design: this is the batch-isolation boundary, so one cohort's
            # failure (even an unexpected programming error) must not abandon the rest.
            # Naming the exception type keeps a genuine bug distinguishable in the log.
            log_err(
                f"cohort {org} failed to sync (continuing with the rest): "
                f"{type(exc).__name__}: {exc}"
            )
            errors += 1
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--cohort-org", default=None)
    parser.add_argument(
        "--all-cohorts",
        action="store_true",
        help="Also reconcile roster/teams for every registered cohort (not just --cohort-org).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Fail fast on a missing/invalid token. Every gh failure below degrades to an
    # empty list or set (no cohorts, no members, no file content), so an
    # unauthenticated run would otherwise reconcile nothing and still report
    # "[ok] Sync complete" - masking e.g. an org secret that stopped being
    # delivered to this repo.
    if _acting_login() is None:
        log_err(
            "gh is not authenticated (empty or invalid GH_TOKEN?) - "
            "refusing to run: every read would come back empty and the sync "
            "would falsely report success."
        )
        return 1

    errors = sync(
        args.course_org,
        cohort_org=args.cohort_org,
        all_cohorts=args.all_cohorts,
        dry_run=args.dry_run,
    )
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
