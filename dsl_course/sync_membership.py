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

A CONTENT fault never reds this run. A students.csv saved as a `;`-delimited export, a
teams.csv with no header, a people.yml that is not YAML: each skips its cohort and is
reported where the person who left it there will see it - the per-file digest issue in
that cohort's classroom-config, and the mail beside it. The COURSE org's own two files
(`dsl-course.yml`, the cohort registry) are the same rule at a wider blast radius: nothing
is reconciled under the course at all, and the course digest issue and the admin mail are
what say so. The exit code is kept for the run's own failures (a `gh` write that was
refused, a token that lost its scope), because this cron's red X opens `Sync membership is
failing` in the course org and emails the maintainer, and neither of them can fix a CSV in
a cohort org.

Usage:
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --all-cohorts
"""

from __future__ import annotations

import argparse
import sys

import yaml

from . import sync_faculty, sync_roster, sync_teams
from .discovery import (
    COHORTS_PATH,
    discover_assignments,
    discover_cohorts,
    discover_content_repos,
)
from .faults import Unusable
from .gh_contents import read_error
from .gh_teams import acting_login
from .grades import write_team_lock
from .log import log_err, log_ok

# What a cohort's hand-edited config can be wrong in a way this sync cannot act on: a CSV
# whose header nobody can read (`faults.Unusable`, raised by `gh_contents.read_csv`) and a
# people.yml that is not YAML at all. Both mean the same thing here - the file says
# nothing this run may reconcile from, and reconciling from what it does say would prune
# a cohort's teams down to whatever survived the parse.
_CONTENT_FAULT = (Unusable, yaml.YAMLError)


def _unreadable_course_config(course_org: str, exc: Exception) -> int:
    """The COURSE org's own config cannot be read - the exit code for that, which is 0.

    `dsl-course.yml` and the cohort registry are hand-edited like every other file here,
    and a course admin is the only person who can fix either. Nothing is reconciled or
    pruned while one of them stands broken, and that is reported where they are looking:
    the course org's digest issue and the mail beside it
    (`scheduler._preflight_course`, which a push to either file runs within the minute).
    Written as a function so both reads answer for it in the same words."""
    log_err(
        f"{course_org} has a course config file the sync cannot read "
        f"({read_error(exc)}) - nothing under this course is reconciled or pruned. The "
        f"digest issue in {course_org}/.github names the line to fix; this run stays "
        f"green."
    )
    return 0


def sync(
    course_org: str,
    cohort_org: str | None = None,
    all_cohorts: bool = False,
    dry_run: bool = False,
) -> int:
    # course_admins always reconciles everywhere, independent of which cohort (if
    # any) triggered this sync.
    try:
        all_registered = discover_cohorts(course_org)
    except _CONTENT_FAULT as exc:
        return _unreadable_course_config(course_org, exc)
    if not all_registered:
        # An empty registry can be legitimate for a brand-new course org, so this does not
        # fail the run - but it must be VISIBLE, not a silent green "Sync complete": only
        # the course org's own course-admin gets reconciled, no cohort at all.
        log_err(
            f"no cohorts are registered under {course_org} "
            f"({COHORTS_PATH} is empty or unset) - only course-admin on the course "
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
    # An EMPTY registry authorises nothing. It used to short-circuit the whole check, so
    # a course org that had never registered a cohort accepted any org name a dispatch
    # cared to name - and then reconciled and PRUNED that org's roster and teams.
    if cohort_org and cohort_org.casefold() not in {
        c.casefold() for c in all_registered
    }:
        listed = ", ".join(sorted(all_registered)) or "nothing"
        log_err(
            f"{cohort_org} is not registered under {course_org} "
            f"({COHORTS_PATH} lists {listed}) - refusing to reconcile it. Register "
            f"the cohort first if this is genuinely its course org."
        )
        return 1
    try:
        errors = sync_faculty.sync_course_admins(
            course_org, all_registered, dry_run=dry_run
        )
    except _CONTENT_FAULT as exc:
        return _unreadable_course_config(course_org, exc)

    # Roster/teams/instructors reconcile only for whichever cohort(s) are in scope -
    # not fanned out to every other, unrelated cohort.
    targets = (
        list(all_registered) if all_cohorts else ([cohort_org] if cohort_org else [])
    )
    content_repos = discover_content_repos(course_org) if targets else []
    assignments = discover_assignments(course_org) if targets else []
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
            # The Join-team form's only source of truth, refreshed here because this is
            # what a push to `schedule.yml` wakes (classroom-config/dispatch-sync.yml) -
            # and a form answering off a stale mirror either refuses a real team or lets
            # one form for an assignment the template says is individual.
            errors += 0 if write_team_lock(course_org, org, dry_run=dry_run) else 1
        except _CONTENT_FAULT as exc:
            # A file faculty have to fix, not a run that broke. This cohort is skipped -
            # a roster nobody can read is not an empty roster, and acting on it would
            # revoke access from everybody the parse dropped - but the run stays GREEN
            # and files no "Sync membership is failing" issue: the fault already reaches
            # the person who left it there, through the digest issue in this cohort's
            # classroom-config and the mail beside it (`scheduler._preflight_configs`).
            # A red X here said only "something is wrong somewhere", every hour, to a
            # course-admin team that cannot fix a CSV in a cohort org.
            log_err(
                f"cohort {org} has a config file the sync cannot read "
                f"({read_error(exc)}) - "
                f"skipping this cohort. The digest issue in {org}/classroom-config "
                f"names the line to fix; this run stays green."
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
    if acting_login() is None:
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
