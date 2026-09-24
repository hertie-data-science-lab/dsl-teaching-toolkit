"""dsl-course sync-membership -- consolidated roster + teams + faculty sync.

One entrypoint replacing three separate workflows' worth of orchestration:

- course_admins (from the course org's declared `people:` block) ALWAYS reconciles
  everywhere - the course org itself + every semester registered under it
  (sync_faculty.sync_course_admins) - regardless of which semester (if any) triggered
  this sync, since admin access is course-wide by design.
- Roster (students.csv), project teams (teams.csv), and each semester's own
  instructors/TAs (classroom-config/instructors.yml, via
  sync_faculty.sync_semester_instructors) additionally reconcile for whichever
  semester(s) are in scope: one named semester (--semester-org, e.g. a push to that
  semester's classroom-config), or every registered semester (--all-semesters, e.g. the
  daily cron - a full resync with no single semester in context).

Every reconcile here is FULL (add + remove) - there is no --prune flag at this level;
config is the live truth, so a deleted roster row or a lapsed faculty `end` date
revokes access on the very next sync.

A CONTENT fault never reds this run. A students.csv saved as a `;`-delimited export, a
teams.csv with no header, a instructors.yml that is not YAML: each skips its semester and is
reported where the person who left it there will see it - the per-file digest issue in
that semester's classroom-config, and the mail beside it. The COURSE org's own two files
(`dsl-course.yml`, the semester registry) are the same rule at a wider blast radius: nothing
is reconciled under the course at all, and the course digest issue and the admin mail are
what say so. The exit code is kept for the run's own failures (a `gh` write that was
refused, a token that lost its scope), because this cron's red X opens `Sync membership is
failing` in the course org and emails the maintainer, and neither of them can fix a CSV in
a semester org.

Usage:
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --semester-org hertie-dsl-demo-f2026
    python3 -m dsl_course.sync_membership --course-org hertie-dsl-demo-course-e1234 --all-semesters
"""

from __future__ import annotations

import argparse
import sys

import yaml

from . import status, sync_faculty, sync_roster, sync_teams
from .discovery import (
    SEMESTERS_PATH,
    discover_assignments,
    discover_content_repos,
    discover_semesters,
    listing_by_name,
    semester_is_live,
)
from .faults import Unusable
from .gh_contents import read_error
from .gh_teams import acting_login, membership_changes, reset_membership_changes
from .grades import ensure_gradebooks, sync_team_lock
from .log import Summary, log_err, log_ok, plural
from .welcome import refresh_join_team_form

# What a semester's hand-edited config can be wrong in a way this sync cannot act on: a CSV
# whose header nobody can read (`faults.Unusable`, raised by `gh_contents.read_csv`) and a
# instructors.yml that is not YAML at all. Both mean the same thing here - the file says
# nothing this run may reconcile from, and reconciling from what it does say would prune
# a semester's teams down to whatever survived the parse.
_CONTENT_FAULT = (Unusable, yaml.YAMLError)

# How long the NIGHTLY SYNC may spend provisioning gradebooks before it stops and leaves
# the rest to the next one - the one caller that passes it, because it is the one with no
# work waiting on the gradebooks it makes. A DEADLINE rather than a count of creations:
# what has to be bounded is the wall clock of a job (Sync membership has a 30-minute one),
# and four API calls per new gradebook take as long as the write pacer and the day's rate
# limit make them - so a count is a guess at that and this is the thing itself. A run that
# stops here has recorded everything it did; repos already there cost almost nothing, so
# the next run starts from where this one left off.
GRADEBOOK_BUDGET_MINUTES = 10


def _unreadable_course_config(course_org: str, exc: Exception) -> int:
    """The COURSE org's own config cannot be read - the exit code for that, which is 0.

    `dsl-course.yml` and the semester registry are hand-edited like every other file here,
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
    semester_org: str | None = None,
    all_semesters: bool = False,
    dry_run: bool = False,
) -> int:
    # course_admins always reconciles everywhere, independent of which semester (if
    # any) triggered this sync.
    try:
        all_registered = discover_semesters(course_org)
    except _CONTENT_FAULT as exc:
        return _unreadable_course_config(course_org, exc)
    if not all_registered:
        # An empty registry can be legitimate for a brand-new course org, so this does not
        # fail the run - but it must be VISIBLE, not a silent green "Sync complete": only
        # the course org's own course-admin gets reconciled, no semester at all.
        log_err(
            f"no semesters are registered under {course_org} "
            f"({SEMESTERS_PATH} is empty or unset) - only course-admin on the course "
            f"org itself will be reconciled. Expected for a brand-new course org; a "
            f"problem if this course has live semesters."
        )
    # A named semester reaches here straight from a repository_dispatch's
    # `client_payload.semester_org`, which is written by whoever holds a semester's
    # DSL_BOT_TOKEN - a lower trust tier than the course org. Naming SOMEONE ELSE'S semester
    # would have this run reconcile (and prune) that semester's roster and teams. The
    # registry is the authority on which semesters this course org owns, so a name that is
    # not in it is refused rather than acted on. Compared casefold: GitHub org names are
    # case-insensitive, and the registry's spelling need not match the dispatch's.
    # An EMPTY registry authorises nothing. It used to short-circuit the whole check, so
    # a course org that had never registered a semester accepted any org name a dispatch
    # cared to name - and then reconciled and PRUNED that org's roster and teams.
    if semester_org and semester_org.casefold() not in {
        c.casefold() for c in all_registered
    }:
        listed = ", ".join(sorted(all_registered)) or "nothing"
        log_err(
            f"{semester_org} is not registered under {course_org} "
            f"({SEMESTERS_PATH} lists {listed}) - refusing to reconcile it. Register "
            f"the semester first if this is genuinely its course org."
        )
        return 1
    # The REGISTRY authorises (above); the LIVE semesters are what this reconciles into.
    # A semester that has been closed out is a read-only org, so every grant, team write and
    # roster push below would 403 on it - nightly, for the rest of the course's life.
    live = [c for c in all_registered if semester_is_live(c)]
    try:
        errors = sync_faculty.sync_course_admins(course_org, live, dry_run=dry_run)
    except _CONTENT_FAULT as exc:
        return _unreadable_course_config(course_org, exc)

    # Roster/teams/instructors reconcile only for whichever semester(s) are in scope -
    # not fanned out to every other, unrelated semester. A named semester is checked against
    # the `live` list rather than probed again: the answer is already taken, and asking a
    # second time prints the "[skip] ... archived semester" line twice for one semester.
    named_is_live = semester_org and semester_org.casefold() in {
        c.casefold() for c in live
    }
    targets = list(live) if all_semesters else ([semester_org] if named_is_live else [])
    content_repos = discover_content_repos(course_org) if targets else []
    assignments = discover_assignments(course_org) if targets else []
    for org in targets:
        # Per-semester isolation: the read helpers now raise on non-404, so one semester's
        # transient failure must not abort the whole batch (the lesson seed.refresh
        # applied). Log it, count it, and carry on to the next semester.
        try:
            # ONE listing of the semester for this pass. Two of the steps below ask the same
            # question of it - which submission repos an off-boarded student still holds a
            # grant on, and which students have no gradebook yet - and each used to take a
            # paginated listing of its own, nightly, per semester.
            #
            # `listing_by_name` and not `list_org_repos`: this is taken before the roster
            # reconcile rather than inside it, and a listing that RAISED here would have
            # skipped the whole semester - enrolment included - over rows only the prune and
            # the gradebooks need. None is "we could not look", and each of them answers
            # it for itself.
            existing = listing_by_name(org)
            errors += sync_roster.sync(
                org, prune=True, dry_run=dry_run, existing=existing
            )
            errors += sync_teams.sync(org, prune=True, dry_run=dry_run)
            errors += sync_faculty.sync_semester_instructors(
                course_org, org, content_repos, assignments, dry_run=dry_run
            )
            # The Join-team form's only source of truth, refreshed here because this is
            # what a push to `schedule.yml` wakes (classroom-config/dispatch-sync.yml) -
            # and a form answering off a stale mirror either refuses a real team or lets
            # one form for an assignment the template says is individual.
            lock = sync_team_lock(course_org, org, dry_run=dry_run)
            errors += 0 if lock.ok else 1
            # And the FORM with it, when the mirror actually moved. Its Assignment field is
            # a REQUIRED dropdown rendered from that same mirror, so an assignment this push
            # has just opened is one nobody can file a Join-team issue for at all until the
            # dropdown offers the slug - which is the difference between "joinable within a
            # minute of the push" and "joinable after tonight's refresh".
            if lock.changed:
                errors += refresh_join_team_form(org)
            # A private gradebook per onboarded student, from the moment they onboard
            # rather than from the first distribute: it is where every shape's marks and
            # feedback go, and the assignment brief points at it from the day it is
            # published. Idempotent, one semester listing.
            # The ONE caller that bounds it: this run has 30 minutes for every semester
            # at once and nothing waiting on the repos it makes, so a semester that would
            # overrun stops and the next night takes the next batch.
            errors += ensure_gradebooks(
                org,
                dry_run=dry_run,
                existing=existing,
                budget_minutes=GRADEBOOK_BUDGET_MINUTES,
            )
        except _CONTENT_FAULT as exc:
            # A file faculty have to fix, not a run that broke. This semester is skipped -
            # a roster nobody can read is not an empty roster, and acting on it would
            # revoke access from everybody the parse dropped - but the run stays GREEN
            # and files no "Sync membership is failing" issue: the fault already reaches
            # the person who left it there, through the digest issue in this semester's
            # classroom-config and the mail beside it (`scheduler._preflight_configs`).
            # A red X here said only "something is wrong somewhere", every hour, to a
            # course-admin team that cannot fix a CSV in a semester org.
            log_err(
                f"semester {org} has a config file the sync cannot read "
                f"({read_error(exc)}) - "
                f"skipping this semester. The digest issue in {org}/classroom-config "
                f"names the line to fix; this run stays green."
            )
        except Exception as exc:
            # Broad by design: this is the batch-isolation boundary, so one semester's
            # failure (even an unexpected programming error) must not abandon the rest.
            # Naming the exception type keeps a genuine bug distinguishable in the log.
            log_err(
                f"semester {org} failed to sync (continuing with the rest): "
                f"{type(exc).__name__}: {exc}"
            )
            errors += 1
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--semester-org", "--cohort-org", default=None)
    parser.add_argument(
        "--all-semesters",
        "--all-cohorts",
        action="store_true",
        help="Also reconcile roster/teams for every registered semester (not just --semester-org).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Fail fast on a missing/invalid token. Every gh failure below degrades to an
    # empty list or set (no semesters, no members, no file content), so an
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

    reset_membership_changes()
    errors = sync(
        args.course_org,
        semester_org=args.semester_org,
        all_semesters=args.all_semesters,
        dry_run=args.dry_run,
    )
    # A push to one semester's roster, teams or instructors.yml dispatches exactly this: the
    # semester's status.json follows the membership it just reconciled. Not counted - the
    # sync's exit code is the sync's - and `status.write` refuses a semester the course's
    # registry does not list, whatever the payload named.
    if args.semester_org and not args.all_semesters and not args.dry_run:
        status.refresh(args.course_org, args.semester_org)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return access_summary(membership_changes(), args.dry_run)


def access_summary(changes: dict[str, int], dry_run: bool) -> Summary:
    """Check staff access's sentence: how many team memberships moved (or would)."""
    n = sum(changes.values())
    if not n:
        return Summary("Access checked: nothing needed changing.", changes)
    verb = "would change" if dry_run else "changed"
    return Summary(f"Access checked: {plural(n, 'team membership')} {verb}.", changes)


if __name__ == "__main__":
    sys.exit(main())
