"""dsl-course scheduler -- datetime-driven auto-release.

The same idempotent release functions as the manual workflows, fired automatically from the
cohort's own `classroom-config/schedule.yml` `releases:` plan (see
`dsl_course.schedule`). Each labelled release carries a `when` datetime and a mix of
actions - `deploy` (copy a source path from a COURSE-org repo into a COHORT-org repo) and
`assignment` (provision one student repo per enrolled student from a template). Grading is
NOT one of them: it is driven off each assignment's own deadline, below, not off a
`releases:` entry. An hourly cron fires every release whose `when` has arrived. Because every release is idempotent, re-runs are no-ops and there is
no "already released" state to track. Grading is the exception - see AUTOGRADE below.

Assignment handouts are declared with the rest of the assignment's lifecycle -
`assignments.<slug>.handout_datetime` - and synthesised into releases here
(_handout_releases), so they fire through the exact machinery a deploy does. The model
solution rides on that same release once `assignments.<slug>.solution_datetime` has
passed - Release assignment's `include_solution` tick, on a clock.

The same hourly run also drives each assignment's grading deadline (`grading_datetime`,
else `due_datetime`), whether or not the cohort uses `releases` at all:

1. FREEZE. For every assignment whose grading deadline has gone by and that has no snapshot
   yet, record the commit each submission repo is graded at into
   `classroom-config/snapshots/<slug>.csv` (see `dsl_course.collect`). That timestamp is the
   server's, not the student's, which is the only reason the pin can be trusted.
2. AUTOGRADE, ONCE. Then run the autograder for those same assignments - template
   `<slug>-<tag>` in the course org. The fire-once marker is the `autograde/<slug>/_graded.json`
   sentinel (or the `_skipped.json` record): present means already graded, so never again.

Sources are always read from the course org and destinations always written to the cohort
org - the two orgs come from the invocation (`--course-org` / `--cohort-org`), never from
the schedule, which names repos only.

Usage (the cron passes the course org and iterates its cohorts; --now is for testing):
    python3 -m dsl_course.scheduler --course-org COURSE --all-cohorts
    python3 -m dsl_course.scheduler --course-org COURSE --cohort-org COHORT --dry-run
    python3 -m dsl_course.scheduler --course-org COURSE --cohort-org COHORT --now 2026-09-15T14:00
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import schedule, site, source_digest
from .assign import provision_all, solution_released
from .collect import (
    SnapshotResult,
    collect,
    has_autograde_results,
    load_snapshots,
    resolve_is_group,
    snapshot_assignment,
    snapshot_path,
    template_is_group,
)
from .deploy import deploy_many
from .log import log, log_err, log_ok, log_step
from .repos import repo_exists
from .schedule import Release
from .schedule_plan import deploy_dest
from .seed import discover_cohorts

# --------------------------------------------------------------------------- pure core

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def due_releases(releases: list[Release], now: datetime) -> list[Release]:
    """Entries with something to DO at `now`, in event_datetime order. An assignment
    handout fires at the entry's event_datetime; each deploy at its own deploy_datetime
    (else the event_datetime) - so an entry is due as soon as any one of its actions is.
    Display-only entries (no actions) never fire and are never due. `releases` is already
    sorted (schedule._parse_releases, and `run` re-sorts once the synthesised handouts are
    merged in), and every datetime is tz-aware, so the comparisons are correct across
    timezones."""
    return [
        r
        for r in releases
        if r.due_deploys(now) or (r.assignment and r.when is not None and r.when <= now)
    ]


def release_order(release: Release) -> tuple[bool, datetime]:
    """The plan's ordering key, the same one `schedule._parse_releases` sorts on: by
    event_datetime, with undated (TBC) entries last. Synthesised handout releases are
    merged into that already-sorted list, so the merged list has to be re-sorted through
    this or `due_releases` stops being event_datetime-ordered."""
    return (release.when is None, release.when or _EPOCH)


def due_snapshots(sched: schedule.Schedule, now: datetime) -> list[tuple[str, str]]:
    """(slug, grading-deadline ISO) for every scheduled assignment whose grading deadline
    (`grading_datetime`, else `due_datetime`) has passed at `now` - the assignments whose
    submissions are ready to be frozen and then graded. Deadline-ordered, so the run log is
    deterministic. Whether each one has already been snapshotted or graded is a separate,
    I/O question (see `_snapshot_passed_deadlines` / `_autograde_passed_deadlines`)."""
    passed = [
        (slug, at)
        for slug in sched.assignments
        if (at := schedule.grading_datetime_at(sched, slug)) is not None and at <= now
    ]
    return [(slug, at.isoformat()) for slug, at in sorted(passed, key=lambda p: p[1])]


def describe(release: Release, now: datetime | None = None) -> list[str]:
    """Human one-liners for a release's actions (for dry-run / 'what opens when'). With
    `now`, deploys not yet due (a deploy_datetime after the entry's event_datetime) are
    marked rather than listed as firing."""
    if release.is_event_only:
        return ["no actions - nothing to release"]
    lines: list[str] = []
    for d in release.deploy:
        fire_at = d.deploy_datetime or release.when
        pending = now is not None and (fire_at is None or fire_at > now)
        suffix = (
            f"  (not yet due - deploys {d.deploy_datetime.isoformat()})"
            if pending and d.deploy_datetime
            else ""
        )
        lines.append(
            f"deploy {d.course_source_repo}/{d.course_source_path} -> "
            f"{d.cohort_dest_repo}/{deploy_dest(d)}{suffix}"
        )
    actions_pending = now is not None and (release.when is None or release.when > now)
    actions_suffix = (
        f"  (not yet due - fires {release.when.isoformat() if release.when else 'TBC'})"
        if actions_pending
        else ""
    )
    if release.assignment:
        solution = " + model solution" if release.assignment_solution else ""
        lines.append(f"assignment {release.assignment}{solution}{actions_suffix}")
    return lines


# ---------------------------------------------------------------------- gh/git wiring


def _execute_nondeploy(
    course_org: str, cohort_org: str, release: Release
) -> tuple[int, bool]:
    """Run one release's non-deploy action (an assignment handout, and once its
    `solution_datetime` has passed, the model solution with it). Deploys are batched
    across the whole run (see `run`) so their source/dest repos clone once. Returns
    `(error count, whether anything was actually provisioned)` - the same shape
    `deploy_many` answers in, and for the same reason: `due_releases` is cumulative, so
    a handed-out release re-fires on every tick and almost all of them change nothing."""
    errors = 0
    changed = False
    if release.assignment:
        # provision_all's default (group=None) resolves group-vs-individual from the
        # cohort schedule / the template's grading.yml - so a scheduled group handout
        # provisions per TEAM, not one repo per student.
        failed, changed = provision_all(
            course_org,
            release.assignment,
            cohort_org,
            solution=release.assignment_solution,
            # Hourly: leave existing repos alone (the manual button still repairs access).
            touch_existing=False,
            scheduled=True,
        )
        if failed != 0:
            errors += 1
    return errors, changed


def _snapshot_passed_deadlines(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> tuple[int, frozenset[str]]:
    """Freeze every passed-deadline assignment that has no snapshot yet. Write-once: an
    assignment already frozen is skipped silently, so this is a no-op on every tick after
    the first.

    Returns `(error count, the cohort names that HAVE a snapshot afterwards)` - already
    frozen or frozen by this pass. Autograding refuses to grade what was never frozen, and
    asking again there was a second read of the same file for every passed deadline on
    every tick; this pass has just established the answer."""
    errors = 0
    frozen: set[str] = set()
    for slug, deadline in due_snapshots(sched, now):
        entry = sched.assignments[slug]
        # every cohort-side artefact keys on the assignment's cohort NAME, not its slug
        name = schedule.cohort_name(slug, entry)
        if load_snapshots(cohort_org, name) is not None:
            # already frozen - never re-snapshot, a late push must not move it
            frozen.add(name)
            continue
        if dry_run:
            log(f"    DRY-RUN  snapshot {snapshot_path(name)} (deadline {deadline})")
            continue
        log_step(f"  snapshot {name} (deadline {deadline})")
        # Resolve group-ness the SAME way grading does, through the one `resolve_is_group`
        # precedence - cohort schedule `type:` wins, else the template's grading.yml - so the
        # snapshot freezes the exact repos grading scores. Deriving it from the schedule alone
        # would miss a group assignment declared ONLY in grading.yml, freezing individual repos
        # while grading targets group repos (every team then reads as "absent from the snapshot"
        # and scores zero). grading.yml is read only when the schedule leaves type unset.
        if entry.type is not None:
            template_group = None
        else:
            template = _assignment_template(course_org, slug, entry)
            template_group = (
                template_is_group(course_org, template) if template else None
            )
        is_group = resolve_is_group(
            force=False, schedule_type=entry.type, template_group=template_group
        )
        # `name` names the repos, `slug` (the schedule key) is what teams.csv is keyed on.
        result = snapshot_assignment(
            cohort_org, name, deadline, is_group=is_group, teams_key=slug
        )
        if result is SnapshotResult.FAILED:
            errors += 1
        elif result is not SnapshotResult.NOTHING_TO_FREEZE:
            # NOTHING_TO_FREEZE wrote no snapshot, so the assignment is NOT frozen and must
            # not be graded this tick - autograding it would write write-once zeros for a
            # cohort that has not been handed out yet.
            frozen.add(name)
    return errors, frozenset(frozen)


def _assignment_template(
    course_org: str, slug: str, entry: schedule.AssignmentEntry
) -> str | None:
    """The course-org repo `slug` hands out from: its `course_source_repo`, if that repo
    exists. None otherwise, and loudly - the name is required and written by hand, so a
    name that resolves to nothing can only be a typo, and its one other symptom is an
    assignment that never hands out and never grades."""
    if repo_exists(course_org, entry.course_source_repo):
        return entry.course_source_repo
    log_err(
        f"assignments.{slug}.course_source_repo names `{entry.course_source_repo}`, which "
        f"does not exist in {course_org} - nothing can be handed out or autograded for it"
    )
    return None


def _autograde_passed_deadlines(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    frozen: frozenset[str],
) -> int:
    """Autograde every passed-deadline assignment exactly once - zero config. Returns the
    error count.

    `frozen` is `_snapshot_passed_deadlines`' answer for this same tick: the cohort names
    that have a snapshot. It is read rather than re-fetched, because the snapshot pass ran
    immediately before over the same deadlines and is the only thing that could have
    changed it.

    Fire-once: the `autograde/<slug>/_graded.json` sentinel (or the `_skipped.json` record) in
    classroom-config is the marker. Absent means never machine-graded, so grade now; present
    means graded already, so never again - which is what stops an hourly re-run from recomputing
    scores a marker has since hand-edited. A deliberate re-grade = delete `autograde/<slug>/`
    (or use the Grade assignment workflow).

    A missing template repo, a template with no `solution` branch, and `autograde: false`
    are all skips, not failures: plenty of assignments are hand-marked. Group vs individual
    is not guessed here - `collect` resolves it from the cohort schedule / grading.yml."""
    errors = 0
    for slug, deadline in due_snapshots(sched, now):
        # the fire-once marker is keyed on the cohort NAME - it must agree with what
        # collect writes, or a passed deadline re-grades every tick
        name = schedule.cohort_name(slug, sched.assignments[slug])
        if has_autograde_results(cohort_org, name):
            continue  # already machine-graded - re-grading is a deliberate act
        template = _assignment_template(course_org, slug, sched.assignments[slug])
        if template is None:
            log(f"  [skip] autograde {slug} - no template repo for it in {course_org}")
            continue
        # Never grade what was never frozen. Without a snapshot `collect` pins on committer
        # dates (student-controlled), and when no submission repo exists at all it would
        # record a permanent write-once ZERO for every student and mark the assignment
        # graded - on a green run. A snapshot that failed this tick, or was skipped because
        # nothing was handed out yet, simply means: not now. The next tick looks again.
        if name not in frozen:
            log(f"  [wait] autograde {slug} - no completed snapshot yet, not grading")
            continue
        if dry_run:
            log(f"    DRY-RUN  autograde {slug} via {template} (deadline {deadline})")
            continue
        log_step(f"  autograde {slug} via {template} (deadline {deadline})")
        if collect(course_org, template, cohort_org, deadline, scheduled=True) != 0:
            errors += 1
    return errors


def _run_releases(
    course_org: str, cohort_org: str, due: list[Release], now: datetime
) -> int:
    """Fire every due release's due actions, then sync the site once. Returns the error
    count. `now` gates each action individually: a deploy with its own deploy_datetime
    fires on its own clock, an entry's handout at its event_datetime - an entry can be
    due for one and not (yet) the other."""
    errors = 0
    # Batch EVERY due release's due deploys through one deploy_many: each unique source
    # and dest repo is cloned once for the whole run, not once per copy.
    all_deploys = [d for release in due for d in release.due_deploys(now)]
    deploy_errors, changed = 0, False
    if all_deploys:
        deploy_errors, changed = deploy_many(
            course_org, cohort_org, all_deploys, sync=False
        )
        errors += deploy_errors

    # Assignment handouts run per release (they aren't file copies).
    did_assign = False
    for release in due:
        if release.assignment and release.when is not None and release.when <= now:
            log_step(
                f"  [{release.label}] assignment handout"
                + (" + solution" if release.assignment_solution else "")
            )
            handout_errors, handout_changed = _execute_nondeploy(
                course_org, cohort_org, release
            )
            errors += handout_errors
            # Only a handout that PROVISIONED something has anything new to show the site.
            # `due_releases` is cumulative - every handed-out assignment is due again on
            # every tick - so setting this unconditionally re-rendered the whole cohort
            # website once an hour, for the rest of the term, off a pass that had skipped
            # every repo.
            did_assign = did_assign or handout_changed

    # One website sync at the end, only if something actually changed.
    if changed or did_assign:
        # site.sync_site RAISES on a genuine tree/team read failure (post-PR2). This
        # cohort's site-sync failure must be logged and counted, not an unhandled traceback
        # that aborts the run - and, under --all-cohorts, every cohort scheduled after it.
        try:
            if site.sync_site(course_org, cohort_org) != 0:
                log_err("site sync incomplete after scheduled release")
                errors += 1
        except Exception as exc:
            log_err(f"site sync failed after scheduled release: {exc}")
            errors += 1
    return errors


def _solution_due(
    cohort_org: str, slug: str, entry: schedule.AssignmentEntry, now: datetime
) -> bool:
    """Whether this tick should push the model solution for `slug`.

    The datetime check is cheap and comes first, so the fire-once read is paid only by an
    assignment whose solution moment has actually arrived - not by every assignment on
    every tick."""
    if entry.solution_datetime is None or entry.solution_datetime > now:
        return False
    return not solution_released(cohort_org, schedule.cohort_name(slug, entry))


def _handout_releases(
    course_org: str, cohort_org: str, sched: schedule.Schedule, now: datetime
) -> list[Release]:
    """Synthetic releases for `assignments.<slug>.handout_datetime` - the whole assignment
    lifecycle (handout_datetime/due_datetime/grading_datetime/max_team_size) is declared in
    ONE block, and the handout still fires through the exact machinery a
    `releases` entry would: due at its datetime, re-checked every tick
    (idempotent - a late onboarder gets their repo on the next one), per-team when the
    template's grading.yml says so. An assignment with no `<slug>-<tag>` template repo is
    skipped - it may be pinned for its website date alone.

    The model solution rides on THIS release once `solution_datetime` has passed, rather
    than being a second synthesised one - one release, one provisioning pass, both jobs;
    between the two datetimes it simply fires without the solution.

    It is ALSO gated on assign.solution_released, and that gate is what makes it safe.
    `due_releases` is cumulative by design - the handout re-fires every tick so a student
    who onboards late still gets their repo - and re-probing a repo is cheap, but
    push_solution CLONES every student repo. Ungated, a passed `solution_datetime` would
    mean a clone per student per hour for the rest of the term. Folding the two releases
    into one removed a duplicate pass; only the marker removes the recurrence.

    It also makes "a scheduled solution needs a scheduled handout" structural - there is no
    release to carry the solution unless `handout_datetime` is set - so the scheduler no
    longer has to notice and skip. `_parse_assignments` flags that combination instead, at
    commit time, where faculty will actually see it."""
    out = []
    for slug, entry in sched.assignments.items():
        if entry.handout_datetime is None:
            continue
        template = _assignment_template(course_org, slug, entry)
        if template is None:
            log(f"  [skip] handout {slug} - no template repo for it in {course_org}")
            continue
        out.append(
            Release(
                label=f"{slug}-handout",
                when=entry.handout_datetime,
                assignment=template,
                assignment_solution=_solution_due(cohort_org, slug, entry, now),
            )
        )
    return out


def _preflight_sources(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Check the plan's sources against the course org and keep the cohort's digest issue
    in step. Returns the error count.

    Exactly one thing here fails the run: a source at the ERROR rung, which is a deploy
    about to ship nothing. Everything else - a check that could not run, a digest that
    could not be written - is logged and swallowed. The distinction is the point: a
    RELEASE problem is worth a red X on the cron, a NOTIFICATION problem is not worth
    stopping a release for."""
    try:
        faults = schedule.source_faults(sched, course_org)
    except Exception as exc:
        log_err(f"could not check {cohort_org}'s sources ({type(exc).__name__}): {exc}")
        return 0
    worst = schedule.worst_severity(faults, now)
    if faults:
        log_step(
            f"{len(faults)} source(s) in {cohort_org}'s plan not staged in "
            f"{course_org} (worst: {worst})"
        )
    try:
        source_digest.sync(cohort_org, course_org, faults, now, dry_run=dry_run)
    except Exception as exc:
        log_err(f"could not update {cohort_org}'s source digest: {exc}")
    if worst == schedule.Severity.ERROR:
        log_err(
            f"{cohort_org}: a source due within "
            f"{int(schedule.SOURCE_ERROR_WINDOW.total_seconds() // 3600)}h is not staged "
            f"in {course_org} - that deploy will ship nothing"
        )
        return 1
    return 0


def run(course_org: str, cohort_org: str, now: datetime, dry_run: bool = False) -> int:
    sched = schedule.load(cohort_org)
    # A plan that could not be read AS A PLAN is not an empty one. `load` deliberately
    # falls back to an empty Schedule so one cohort's typo cannot freeze the cron - but
    # while it stands, nothing is released, handed out, snapshotted or graded for this
    # cohort, and an hourly GREEN tick is exactly how that goes unnoticed. `load` has
    # already logged what is wrong and where; this is what makes anyone look.
    # (Individually DROPPED entries stay advisory, as before - the rest of the plan runs.)
    # Re-sorted, not just concatenated: the synthesised handouts carry their own datetimes
    # and would otherwise land after every scheduled release whatever their date.
    releases = sorted(
        sched.releases + _handout_releases(course_org, cohort_org, sched, now),
        key=release_order,
    )
    due = due_releases(releases, now)
    log_step(
        f"Scheduler {course_org} -> {cohort_org} as of {now.isoformat()}: "
        f"{len(due)}/{len(releases)} release(s) due"
    )

    # Freeze passed deadlines FIRST: server-timed, and before anything grades against the
    # snapshot. Then autograde those same assignments, once each. Both are independent of
    # the release plan - a cohort can pin due dates without scheduling a single release.
    errors = int(sched.unparseable)
    snapshot_errors, frozen = _snapshot_passed_deadlines(
        course_org, cohort_org, sched, now, dry_run
    )
    errors += snapshot_errors
    errors += _autograde_passed_deadlines(
        course_org, cohort_org, sched, now, dry_run, frozen
    )
    # Look AHEAD as well as at what is due: a deploy whose source was never staged fails
    # at its moment, which is far too late to write the thing. This is the only unattended
    # surface that notices - the commit-time validator only ever runs when someone edits
    # schedule.yml, and a plan written in August and forgotten is exactly the case that
    # needs catching. Never fatal to the run: an undelivered warning must not stop a
    # release (see source_digest.sync).
    errors += _preflight_sources(course_org, cohort_org, sched, now, dry_run)

    if dry_run:
        for release in due:
            for line in describe(release, now):
                log(f"    DRY-RUN  [{release.label}] {line}")
        return int(sched.unparseable)

    if not releases:
        log(
            f"  (no releases or assignment handouts in {cohort_org}/"
            f"{schedule.CONFIG_REPO}/{schedule.SCHEDULE_PATH} - {cohort_org} not using "
            f"scheduled release)"
        )
    elif not due:
        log_ok("nothing due.")
    else:
        errors += _run_releases(course_org, cohort_org, due, now)

    if errors:
        log_err(f"{errors} action(s) failed")
        return 1
    log_ok("scheduler run complete")
    return 0


def _parse_now(raw: str | None) -> datetime:
    """Parse --now (ISO date or datetime) to a tz-aware moment; default is now (UTC). A
    naive value is treated as UTC - release/due datetimes carry their own zones, so the
    comparison stays correct."""
    if not raw:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course-org", required=True, help="Course org (source of every release)"
    )
    parser.add_argument(
        "--cohort-org", default=None, help="One cohort; omit and use --all-cohorts"
    )
    parser.add_argument(
        "--all-cohorts",
        action="store_true",
        help="Run every cohort registered with the course org (the hourly cron).",
    )
    parser.add_argument(
        "--now", default=None, help="Override 'now' (ISO date/datetime) - for testing."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = _parse_now(args.now)

    if args.all_cohorts:
        cohorts = discover_cohorts(args.course_org)
        if not cohorts:
            # A freshly bootstrapped course org has this cron installed before any
            # cohort is registered - that gap is normal, not an hourly failure.
            log(
                f"  [skip] no cohorts registered with {args.course_org}; "
                "nothing to release."
            )
            return 0
        rc = 0
        for cohort in cohorts:
            # One cohort's raised failure (a read helper that couldn't reach the API, a
            # site sync that blew up) must not abort the remaining cohorts' scheduled
            # releases - log it, mark the batch failed, and carry on. The same per-cohort
            # isolation PR #151/#146 applied to the nightly refresh.
            try:
                rc |= run(args.course_org, cohort, now, dry_run=args.dry_run)
            except Exception as exc:
                log_err(f"scheduler run for {cohort} failed: {exc}")
                rc |= 1  # accumulate, don't clobber prior cohorts' status bits
        return rc

    if not args.cohort_org:
        log_err("pass --cohort-org or --all-cohorts.")
        return 1
    return run(args.course_org, args.cohort_org, now, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
