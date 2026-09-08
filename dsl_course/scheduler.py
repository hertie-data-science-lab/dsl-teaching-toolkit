"""dsl-course scheduler -- datetime-driven auto-release.

The same idempotent release functions as the manual workflows, fired automatically from the
cohort's own `classroom-config/schedule.yml` `releases:` plan (see
`dsl_course.schedule`). Each labelled release carries a `when` datetime and a mix of
actions - `deploy` (copy a source path from a COURSE-org repo into a COHORT-org repo) and
`assignment` (provision one student repo per enrolled student from a template). Grading is
NOT one of them: it is driven off each assignment's own deadline, below, not off a
`releases:` entry. A tick fires every release whose `when` has arrived. Because every
release is idempotent, re-runs are no-ops and there is no "already released" state to
track. Grading is the exception - see AUTOGRADE below.

TWO DRIVERS deliver those ticks (see `workflows_render.render_scheduler`): GitHub's own
cron, which is best-effort and in practice delivers a small fraction of its fires, and an
external dispatcher that fires the same workflow by `repository_dispatch`. Nothing here
needs to know which arrived: every action is dated and either idempotent or fire-once, so
an extra tick costs a few reads and a missed one is picked up by the next. What DOES watch
the drivers is `dsl_course.cadence`, called from the release phase below: it reads this
workflow's own run history and files two issues off it - the course org's "driver health"
when the external dispatcher has gone quiet, and a cohort's "late delivery" when a dated
moment shipped well after its datetime. Only a real, whole-course pass touches it (never a
dry-run preview and never a single-cohort invocation), and it is disarmed until the external
dispatcher has been seen at least once.

TWO PHASES, which the workflow runs as separate jobs so that neither waits on the other
(`--skip-autograde` runs the first, `--autograde-only` the second; passing neither runs
both, which is what a local invocation wants):

1. RELEASE - snapshot every passed grading deadline, pre-flight the plan's sources, then
   fire every due release, for every cohort in one pass. Minutes at most.
2. AUTOGRADE - grade every passed deadline once, for ONE cohort. This is the slow half (it
   clones and runs every submission, on a 120-minute budget), which is why it is per cohort
   and out of the release path: a cohort mid-grading must not delay a release due meanwhile.

The two phases talk to each other only through the snapshot file, never in memory - so the
autograde phase is correct a tick later, in another process, or after a release pass that
failed.

Assignment handouts are declared with the rest of the assignment's lifecycle -
`assignments.<slug>.handout_datetime` - and synthesised into releases here
(_handout_releases), so they fire through the exact machinery a deploy does. The model
solution rides on that same release once `assignments.<slug>.solution_datetime` has
passed - Release assignment's `include_solution` tick, on a clock.

Every tick also drives each assignment's grading deadline (`grading_datetime`, else
`due_datetime`), whether or not the cohort uses `releases` at all:

1. FREEZE (release phase). For every assignment whose grading deadline has gone by and that
   has no snapshot yet, record the commit each submission repo is graded at into
   `classroom-config/snapshots/<slug>.csv` (see `dsl_course.collect`). That timestamp is the
   server's, not the student's, which is the only reason the pin can be trusted.
2. AUTOGRADE, ONCE (autograde phase). Run the autograder for every frozen assignment -
   template `<slug>-<tag>` in the course org. The fire-once marker is the
   `autograde/<slug>/_graded.json` sentinel (or the `_skipped.json` record): present means
   already graded, so never again.

Sources are always read from the course org and destinations always written to the cohort
org - the two orgs come from the invocation (`--course-org` / `--cohort-org`), never from
the schedule, which names repos only.

Usage (the workflow's two jobs are the first two lines; --now is for testing):
    python3 -m dsl_course.scheduler --course-org COURSE --all-cohorts --skip-autograde
    python3 -m dsl_course.scheduler --course-org COURSE --cohort-org COHORT --autograde-only
    python3 -m dsl_course.scheduler --course-org COURSE --list-cohorts
    python3 -m dsl_course.scheduler --course-org COURSE --check-course-config
    python3 -m dsl_course.scheduler --course-org COURSE --all-cohorts
    python3 -m dsl_course.scheduler --course-org COURSE --cohort-org COHORT --dry-run
    python3 -m dsl_course.scheduler --course-org COURSE --cohort-org COHORT --now 2026-09-15T14:00
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone

from . import (
    cadence,
    config_digest,
    discovery,
    notify,
    roster,
    schedule,
    site,
    source_digest,
    sync_faculty,
    sync_teams,
    teams,
)
from .assign import provision_all, solution_released
from .collect import (
    SnapshotResult,
    collect,
    has_autograde_results,
    load_snapshots,
    snapshot_assignment,
    snapshot_path,
    sync_sheet,
)
from .course import COURSE_ADMIN_TEAM
from .deploy import deploy_many
from .grades import (
    cohort_sheet_faults,
    cutoff_at,
    grading_config_faults,
    load_grading_spec,
    sheet_path,
)
from .log import log, log_err, log_ok, log_step
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


def due_snapshots(
    course_org: str, sched: schedule.Schedule, now: datetime
) -> list[tuple[str, str]]:
    """(slug, cutoff ISO) for every scheduled assignment whose CUTOFF has passed at `now` -
    the assignments whose submissions are ready to be frozen and then graded.
    Deadline-ordered, so the run log is deterministic.

    The cutoff is `grades.cutoff_at`: an explicit `grading_datetime`, else the due date plus
    the template's `late_window_days`. Reading the template is what puts the freeze at the
    END of the late window rather than at the deadline the window is measured from - the
    sheet's header and every receipt promise work is accepted until then, and a snapshot
    taken at the due date silently refused all of it.

    Not pure, therefore: it reads each template's `grading_config.yml`. That read is
    memoised per process (`grades._grading_text`), and both passes below share this answer.
    Whether each assignment has already been snapshotted or graded is still a separate
    question (see `_snapshot_passed_deadlines` / `_autograde_passed_deadlines`)."""
    passed = []
    for slug, entry in sched.assignments.items():
        gspec = load_grading_spec(course_org, entry.course_source_repo)
        at = cutoff_at(sched, slug, gspec)
        if at is not None and at <= now:
            passed.append((slug, at))
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
        # cohort schedule / the template's grading_config.yml - so a scheduled group handout
        # provisions per TEAM, not one repo per student.
        failed, changed = provision_all(
            course_org,
            release.assignment,
            cohort_org,
            solution=release.assignment_solution,
            # Hourly: leave existing repos alone (the manual button still repairs access).
            touch_existing=False,
            scheduled=True,
            # WHICH entry this release was synthesised from. Two may hand out from one
            # template, and the tick knows which it is firing - so it says, rather than
            # letting the far end pick the first and hand out the other one's repos.
            slug=release.assignment_slug,
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
) -> int:
    """Freeze every passed-deadline assignment that has no snapshot yet. Write-once: an
    assignment already frozen is skipped silently, so this is a no-op on every tick after
    the first. Returns the error count.

    What was frozen is NOT returned: the snapshot file itself is the handoff to the
    autograde phase, which runs in another job (and so another process) entirely."""
    errors = 0
    for slug, deadline in due_snapshots(course_org, sched, now):
        entry = sched.assignments[slug]
        # every cohort-side artefact keys on the assignment's cohort NAME, not its slug
        name = schedule.cohort_name(slug, entry)
        if load_snapshots(cohort_org, name) is not None:
            # already frozen - never re-snapshot, a late push must not move it
            continue
        if dry_run:
            log(f"    DRY-RUN  snapshot {snapshot_path(name)} (deadline {deadline})")
            continue
        log_step(f"  snapshot {name} (deadline {deadline})")
        # Resolve group-ness the SAME way grading does, off the template's own
        # grading_config.yml - so the snapshot freezes the exact repos grading scores.
        # A template that cannot be found leaves it individual, which is the parse's
        # default anyway.
        template = _assignment_template(course_org, slug, entry)
        is_group = bool(template) and load_grading_spec(course_org, template).is_group
        # `name` names the repos, `slug` (the schedule key) is what teams.csv is keyed on.
        # A FAILED freeze counts; NOTHING_TO_FREEZE (nobody handed out yet) does not, and
        # neither writes a snapshot file - which is what keeps the autograde phase off an
        # assignment that would otherwise score write-once zeros for the whole cohort.
        result = snapshot_assignment(
            cohort_org,
            name,
            deadline,
            is_group=is_group,
            teams_key=slug,
            tz=sched.timezone,
        )
        if result is SnapshotResult.FAILED:
            errors += 1
    return errors


def _assignment_template(
    course_org: str, slug: str, entry: schedule.AssignmentEntry
) -> str | None:
    """The course-org repo `slug` hands out from: its `course_source_repo`, if that repo
    is there with something in it. None otherwise, and loudly - the name is required and
    written by hand, so a name that resolves to nothing can only be a typo, and its one
    other symptom is an assignment that never hands out and never grades.

    `schedule.source_repo_paths`, and not the optimistic `repo_exists` this used to ask:
    `source_faults` puts the same question to the same repo in the same run and calls a
    repo with no commits missing, so the two used to disagree about a template nobody had
    pushed to - one mailing faculty about a missing source while the other handed out from
    it. `None` there is "could not tell", and stays optimistic: refusing to hand out on a
    403 or a rate limit is worse than trying and failing."""
    paths = schedule.source_repo_paths(course_org, entry.course_source_repo)
    if paths is None or paths:
        return entry.course_source_repo
    log_err(
        f"assignments.{slug}.course_source_repo names `{entry.course_source_repo}`, which "
        f"does not exist in {course_org} (or it is empty) - nothing can be handed out or "
        f"autograded for it"
    )
    return None


def _autograde_passed_deadlines(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Autograde every passed-deadline assignment exactly once - zero config. Returns the
    error count.

    Fire-once: the `autograde/<slug>/_graded.json` sentinel (or the `_skipped.json` record) in
    classroom-config is the marker. Absent means never machine-graded, so grade now; present
    means graded already, so never again - which is what stops an hourly re-run from recomputing
    scores a marker has since hand-edited. A deliberate re-grade = delete `autograde/<slug>/`
    (delete `autograde/<slug>/` to let a later tick regrade).

    A missing template repo, a template with no `solution` branch, and `autograde: false`
    are all skips, not failures: plenty of assignments are hand-marked. Group vs individual
    is not guessed here - `collect` resolves it from the cohort schedule / grading_config.yml."""
    errors = 0
    for slug, deadline in due_snapshots(course_org, sched, now):
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
        # graded - on a green run. A snapshot that failed, or was skipped because nothing
        # was handed out yet, simply means: not now. The next tick looks again. The FILE is
        # the gate, re-read here rather than inherited: this phase is its own job.
        if load_snapshots(cohort_org, name) is None:
            log(f"  [wait] autograde {slug} - no completed snapshot yet, not grading")
            continue
        if dry_run:
            log(f"    DRY-RUN  autograde {slug} via {template} (deadline {deadline})")
            continue
        log_step(f"  autograde {slug} via {template} (deadline {deadline})")
        # `slug` here is the schedule KEY (`due_snapshots` yields keys), which is exactly
        # what `collect` needs to tell two entries on one template apart.
        if (
            collect(
                course_org, template, cohort_org, deadline, scheduled=True, slug=slug
            )
            != 0
        ):
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
    lifecycle (handout_datetime/due_datetime/grading_datetime) is declared in ONE block,
    and the handout still fires through the exact machinery a `releases` entry would:
    due at its datetime, re-checked every tick
    (idempotent - a late onboarder gets their repo on the next one), per-team when the
    template's grading_config.yml says so. An assignment with no `<slug>-<tag>` template repo is
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
                label=f"{slug}{schedule.HANDOUT_SUFFIX}",
                when=entry.handout_datetime,
                assignment=template,
                assignment_slug=slug,
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
    in step. Always returns 0.

    Nothing here fails the run, at any rung. A source nobody has staged is a CONTENT
    fault, and it is delivered where the people who can fix it are looking: the cohort's
    digest issue, which @mentions the instructors and links the line to edit, plus the
    mail `notify` sends the same people off the same transitions. The exit code belongs to
    the run itself - it broke, or it did not - and a missing source used to spend it on
    every tick, up to eight red runs an hour mailing a bot account about a folder only
    faculty can write. The signature keeps its int so the caller's `errors +=` reads the
    same as every other phase."""
    try:
        sources = schedule.source_faults(sched, course_org)
    except Exception as exc:
        log_err(f"could not check {cohort_org}'s sources ({type(exc).__name__}): {exc}")
        sources = []
    # ONE issue for schedule.yml, carrying both of the ways it goes wrong: a source that
    # is not staged yet (counting down to its release) and an entry the parser could not
    # read at all (already out of the plan). A reader asked to fix this file finds
    # everything wrong with it in one place, and each fault is still filed - and notified -
    # on its own clock (`ConfigFault.fires`).
    # NOT short-circuited when empty: an empty list is what CLOSES the issue, and the
    # tick after the last fault is fixed is the one that has to say so.
    faults = sources + list(sched.faults)
    if sources:
        log_step(
            f"{len(sources)} source(s) in {cohort_org}'s plan not staged in "
            f"{course_org} (worst: {schedule.worst_severity(sources, now)})"
        )
    if sched.faults:
        log_step(
            f"{len(sched.faults)} entr(y/ies) in {cohort_org}'s "
            f"{schedule.SCHEDULE_PATH} the scheduler cannot read"
        )
    # Local, because everything downstream speaks about time to a human: the deadline
    # faculty wrote, and the overnight window where a notification is held rather than
    # sent.
    local = schedule.in_cohort_zone(sched, now)
    # Who to tell, asked ONCE and only if asked at all: the digest @mentions them and the
    # mail is addressed to them, so asking git twice would be two reads and two chances to
    # disagree - and `sync` calls this only on a tick with something to say, because the
    # answer costs a blame query, a people.yml read and a commit lookup per repo.
    routing = notify.Routing()

    def whom() -> list[str]:
        nonlocal routing
        try:
            routing = notify.route(cohort_org, course_org, faults, local)
        except Exception as exc:
            log_err(
                f"could not work out who to tell about {cohort_org}'s sources: {exc}"
            )
        return routing.logins

    try:
        digest = source_digest.sync(
            cohort_org,
            course_org,
            faults,
            local,
            dry_run=dry_run,
            resolve_mention=whom,
        )
    except Exception as exc:
        log_err(f"could not update {cohort_org}'s source digest: {exc}")
        return 0
    if digest.errors:
        # `sync` has already said what went wrong, line by line. Recorded here and NOT
        # returned: an undelivered notification must not stop a release.
        log_step(
            f"{cohort_org}'s source digest: {digest.errors} error(s) - not delivered"
        )
    try:
        # The mail beside the @mention, for the transitions the digest just recorded. It
        # counts its own failures and never raises; this catch is for the one it did not
        # foresee, on the same terms as the digest above - a release is not worth a
        # notification.
        unsent = notify.notify_source_transitions(
            cohort_org, course_org, digest, local, routing, dry_run=dry_run
        )
        # The same issue's other half, in its own letter: an entry nobody can read names
        # the file and what it costs, not a deadline it does not have.
        unreadable = notify.notify_config_faults(
            source_digest.SCHEDULE,
            cohort_org,
            course_org,
            digest,
            local,
            routing,
            dry_run=dry_run,
        )
        unsent = notify.Unsent(
            unsent.addressees + unreadable.addressees, unsent.keys + unreadable.keys
        )
        # A mail that did not go out is un-RECORDED rather than lost. The digest has
        # already written the new rung, so without this the notification was owed once,
        # failed once and was never owed again - and the log line saying so was the only
        # trace. Putting the previous rung back makes the next tick recompute the very
        # same crossing and say it once (`source_digest.hold`).
        if unsent.keys and not dry_run:
            source_digest.hold(
                cohort_org,
                {k: digest.was.get(k) for k in unsent.keys},
                digest.reminder_was,
            )
    except Exception as exc:
        log_err(f"could not mail {cohort_org}'s source faults: {exc}")
    return 0


def _config_faults(course_org: str, cohort_org: str, sched: schedule.Schedule) -> dict:
    """Every hand-edited file in this cohort's classroom-config EXCEPT schedule.yml, and
    what is wrong with each. `{digest: faults}`, and a file left OUT of it is one this tick
    could not read.

    schedule.yml is not here because it is already parsed - the plan this tick is running
    IS the parse - and because everything wrong with it belongs in the one issue the
    source pre-flight keeps (see `_preflight_sources`).

    Absent from the map is not the same as no faults: syncing a digest with an empty list
    closes its issue and tells the cohort the file is fine, and "we could not look" is not
    that. A rate limit on students.csv must not report the roster as repaired - so a read
    that failed drops that file from this tick and leaves its issue exactly as it was.

    A content fault is not a read failure, though, and the roster and teams readers RAISE
    on an unreadable header (every consumer depends on that). Recording the fault first is
    what lets this tell the two apart: faults in hand means the file was read and is
    broken."""
    out: dict = {}

    def collect(spec, load) -> None:
        found: list = []
        try:
            load(found)
        except Exception as exc:
            if not found:
                log_err(
                    f"could not read {cohort_org}'s {spec.file} "
                    f"({type(exc).__name__}): {exc}"
                )
                return
        out[spec] = found

    collect(
        config_digest.PEOPLE,
        lambda found: sync_faculty.read_cohort_people(cohort_org, found),
    )
    students: list[roster.Student] | None = None

    def read_roster(found: list) -> None:
        nonlocal students
        students = roster.load(cohort_org, found)

    collect(config_digest.ROSTER, read_roster)
    # The roster is the allowlist teams.csv is vetted against, and only where it was
    # actually read: an empty one would report every member as a stranger. `None` is a
    # roster that is ABSENT as well as one whose read failed - a cohort with no
    # students.csv already has that fault in its own digest, and vetting against the
    # empty set it implies would file one more teams.csv fault per row on top of it.
    known = sync_teams.known_handles(students) if students is not None else None
    collect(config_digest.TEAMS, lambda found: teams.load(cohort_org, found, known))
    # The grading sheets, on this tick and no other: they have no push fast path, because a
    # sheet is edited all day while somebody marks and a mail per save would be a mail
    # about a file still being typed into.
    collect(
        config_digest.GRADING_SHEETS,
        lambda found: cohort_sheet_faults(course_org, cohort_org, sched, found),
    )
    # The one file here that is not in this cohort's classroom-config at all: the
    # assignment's own definition, in the course org. Its faults keep the SOURCE clock -
    # they bite when the assignment is graded - so the engine files them under the rungs
    # and holds them overnight without knowing anything about this file in particular.
    collect(
        config_digest.GRADING_CONFIG,
        lambda found: grading_config_faults(course_org, cohort_org, sched, found),
    )
    return out


def _sync_config_digest(
    spec: config_digest.Digest,
    course_org: str,
    cohort_org: str,
    faults: list,
    local: datetime,
    dry_run: bool,
    route: Callable[[], notify.Routing] | None = None,
) -> None:
    """One file's digest issue and the mail beside it. Swallows everything.

    The same shape as `_preflight_sources`, and for the same reason: a notification that
    could not be delivered must not take a release cron down with it, and one file's
    unreadable digest must not stop the next file's from being written.

    `route` is who to tell, for a digest that answers that differently: the COURSE-level
    one is addressed to the course admins out of an org secret rather than to a cohort's
    teaching team out of its people.yml. Everything else about the two is identical, which
    is why it is one function and one parameter."""
    routing = notify.Routing()
    ask = route or (lambda: notify.route(cohort_org, course_org, faults, local))

    def whom() -> list[str]:
        nonlocal routing
        try:
            routing = ask()
        except Exception as exc:
            log_err(f"could not work out who to tell about {spec.file}: {exc}")
        return routing.logins

    try:
        digest = config_digest.sync(
            spec,
            cohort_org,
            course_org,
            faults,
            local,
            dry_run=dry_run,
            resolve_mention=whom,
        )
    except Exception as exc:
        log_err(f"could not update {cohort_org}'s {spec.file} digest: {exc}")
        return
    if digest.errors:
        log_step(f"{cohort_org}'s {spec.file} digest: {digest.errors} error(s)")
    try:
        unsent = notify.notify_config_faults(
            spec, cohort_org, course_org, digest, local, routing, dry_run=dry_run
        )
        # A mail that did not go out is un-RECORDED rather than lost - see
        # `config_digest.hold`.
        if unsent.keys and not dry_run:
            config_digest.hold(
                spec,
                cohort_org,
                {k: digest.was.get(k) for k in unsent.keys},
                digest.reminder_was,
            )
    except Exception as exc:
        log_err(f"could not mail {cohort_org}'s {spec.file} faults: {exc}")


def _preflight_configs(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Check every hand-edited file in this cohort's classroom-config and keep one digest
    issue per file in step. Always returns 0.

    The hourly floor under the push fast path. An edit that leaves students.csv unreadable
    fires the dispatcher and is mailed within the minute; this is what catches the one
    that was pushed before any of this existed, the one whose dispatch failed, and the
    people.yml entry whose `end:` date lapsed while nobody was pushing anything.

    Nothing here fails the run, at any rung, for the reason `_preflight_sources` does not:
    a file faculty have to fix is a CONTENT fault, and the exit code belongs to the run
    itself. The signature keeps its int so the caller's `errors +=` reads the same as
    every other phase."""
    local = schedule.in_cohort_zone(sched, now)
    for spec, faults in _config_faults(course_org, cohort_org, sched).items():
        if faults:
            log_step(
                f"{len(faults)} entr(y/ies) in {cohort_org}/{spec.file} the toolkit "
                f"cannot use"
            )
        _sync_config_digest(spec, course_org, cohort_org, faults, local, dry_run)
    return 0


def _course_faults(course_org: str) -> tuple[list | None, set[str]]:
    """`(what is wrong with the COURSE org's own two hand-edited files, the admin handles
    it declares)`, or `(None, ...)` when either file could not be READ.

    None is not "no faults", for the reason `_config_faults` gives: syncing the digest with
    an empty list closes its issue and tells the course its config is fine, and a rate
    limit on dsl-course.yml is not that. A file that is MISSING or MALFORMED is a fault and
    comes back in the list - both readers draw that line themselves.

    The handles ride along because the degraded-mode log line counts them (see
    `notify.route_course`), and this tick has already parsed them - a second read to print
    a number would be an API call spent on a log line."""
    faults: list = []
    admins: set[str] = set()
    try:
        faculty = sync_faculty.read_course_config(course_org, faults)
        if faculty:
            admins = sync_faculty.desired_team_members(
                faculty, date.today().isoformat()
            ).get(COURSE_ADMIN_TEAM, set())
        discovery.read_cohort_registry(course_org, faults)
    except Exception as exc:
        log_err(
            f"could not read {course_org}'s course config ({type(exc).__name__}): {exc}"
        )
        return None, admins
    return faults, admins


def _preflight_course(course_org: str, now: datetime, dry_run: bool) -> int:
    """Check the COURSE org's own hand-edited config and keep its digest issue in step.
    Always returns 0.

    ONCE PER RUN, not once per cohort: `dsl-course.yml` and the cohort registry belong to
    the course, and a course admin asked to fix one of them wants one issue, not one per
    cohort saying the same thing.

    Nothing here fails the run, for the reason `_preflight_configs` does not: a file
    faculty have to fix is a CONTENT fault and the exit code belongs to the run itself. A
    registry nobody can parse does still red the tick that follows - the cohort listing
    raises on it and there is genuinely nothing to release - which is why this runs BEFORE
    that listing: reported here or not at all.

    `now` is UTC and stays UTC. The cohort zone that dates a cohort's notifications is a
    cohort's own `schedule.yml` setting, and a course has no single one; every fault here
    is immediate, so nothing is held for the morning and no deadline is being counted
    down."""
    faults, admins = _course_faults(course_org)
    if faults is None:
        return 0
    if faults:
        log_step(
            f"{len(faults)} entr(y/ies) in {course_org}'s own config the toolkit "
            f"cannot use"
        )
    _sync_config_digest(
        config_digest.COURSE,
        course_org,
        course_org,
        faults,
        now,
        dry_run,
        route=lambda: notify.route_course(course_org, faults, now, admins),
    )
    return 0


def _refresh_sheets(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Keep every open assignment's grading sheet current: one pass, after the freeze.

    Runs from the DUE date, not the cutoff, because that is when a grader starts marking
    and when the first late push lands. An assignment whose cutoff has PASSED is left
    alone: the freeze pass above owns it and `collect` seals its sheet, so re-deriving
    here would move facts the freeze has already settled.

    A sheet that does not exist yet is CREATED - an assignment handed out before this pass
    shipped (or one whose handout ran before the sheet had rows) gets one on the next
    tick rather than never."""
    sealed = {
        schedule.cohort_name(slug, sched.assignments[slug])
        for slug, _ in due_snapshots(course_org, sched, now)
    }
    errors = 0
    for slug, entry in sched.assignments.items():
        if entry.due_datetime is None or entry.due_datetime > now:
            continue
        name = schedule.cohort_name(slug, entry)
        if name in sealed:
            continue
        template = _assignment_template(course_org, slug, entry)
        if not template:
            continue  # no template to read the assignment's definition from
        if dry_run:
            log(f"    DRY-RUN  refresh {sheet_path(name)}")
            continue
        log_step(f"  grading sheet {name}")
        # Spelt exactly as the snapshot pass above spells it, off the one memoised read
        # of the template's grading_config.yml.
        is_group = load_grading_spec(course_org, template).is_group
        if not sync_sheet(
            course_org,
            cohort_org,
            sched,
            slug,
            name,
            template,
            is_group=is_group,
            now=now,
        ):
            errors += 1
    return errors


def _release_phase(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    verdict: cadence.Verdict | None = None,
) -> int:
    """Snapshot every passed deadline, refresh every open grading sheet, pre-flight the
    plan's sources, and fire everything now due. Returns the error count. No grading: see
    `_autograde_passed_deadlines`.

    `verdict` is the cadence reading `main` took once for the whole course; None means this
    invocation does not report lateness at all (see `main`)."""
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

    # Lateness is measured over the WHOLE plan, not over `due`: an entry is reported when
    # its own moment fell in the gap since the last executed tick, and that is a question
    # about the datetimes, not about what happens to be firing. A cadence failure counts
    # (this is the check that watches the drivers) but can never abort the release below -
    # so the whole check, `late_items` included, sits inside the try. Both ends of the
    # window are `created_at` instants (`verdict.now` is this run's own), so the gap is on
    # GitHub's clock rather than a runner clock that also carries this run's queue delay.
    errors = 0
    if verdict is not None and not dry_run:
        try:
            errors += cadence.report_cohort(
                course_org,
                cohort_org,
                verdict,
                cadence.late_items(
                    releases, sched, verdict.prev_executed_at, verdict.now
                ),
                dry_run,
            )
        except Exception as exc:
            log_err(f"could not check {cohort_org}'s plan for late deliveries: {exc}")
            errors += 1

    # Freeze passed deadlines FIRST: server-timed, and before anything grades against the
    # snapshot. Independent of the release plan - a cohort can pin due dates without
    # scheduling a single release.
    errors += _snapshot_passed_deadlines(course_org, cohort_org, sched, now, dry_run)
    # Then the sheets, in the same pass and straight after: the freeze has just settled
    # every assignment past its cutoff, and everything else that is past its DUE date
    # gets its `info:` refreshed here.
    errors += _refresh_sheets(course_org, cohort_org, sched, now, dry_run)
    # Look AHEAD as well as at what is due: a deploy whose source was never staged fails
    # at its moment, which is far too late to write the thing. This is the only unattended
    # surface that notices - the commit-time validator only ever runs when someone edits
    # schedule.yml, and a plan written in August and forgotten is exactly the case that
    # needs catching. Never fatal to the run, at any rung: the fault is faculty's to fix
    # and the digest issue is how they hear about it (see _preflight_sources).
    errors += _preflight_sources(course_org, cohort_org, sched, now, dry_run)
    # The same treatment for every other file faculty edit by hand: a roster nobody can be
    # enrolled from, a people.yml entry that grants nothing, a teams.csv row that will not
    # materialise. Each has its own digest issue and its own mail, and none of them can
    # red this run either.
    errors += _preflight_configs(course_org, cohort_org, sched, now, dry_run)

    if dry_run:
        for release in due:
            for line in describe(release, now):
                log(f"    DRY-RUN  [{release.label}] {line}")
        return errors

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
    return errors


def run(
    course_org: str,
    cohort_org: str,
    now: datetime,
    dry_run: bool = False,
    *,
    release: bool = True,
    autograde: bool = True,
    verdict: cadence.Verdict | None = None,
) -> int:
    """One cohort, one or both phases. The workflow's two jobs each ask for one phase
    (`--skip-autograde` / `--autograde-only`); a local run asks for both.

    `verdict` is the cadence reading of the drivers, taken once per course by `main`. None
    (the default) means this invocation reports no lateness - see `main` for which ones."""
    sched = schedule.load(cohort_org)
    # A plan that could not be read AS A PLAN is not an empty one. `load` deliberately
    # falls back to an empty Schedule so one cohort's typo cannot freeze the cron - but
    # while it stands, nothing is released, handed out, snapshotted or graded for this
    # cohort, and a GREEN tick is exactly how that goes unnoticed. `load` has already
    # logged what is wrong and where; this is what makes anyone look.
    # (Individually DROPPED entries stay advisory, as before - the rest of the plan runs.)
    errors = int(sched.unparseable)
    if release:
        errors += _release_phase(course_org, cohort_org, sched, now, dry_run, verdict)
    if autograde:
        log_step(f"Autograde {course_org} -> {cohort_org} as of {now.isoformat()}")
        errors += _autograde_passed_deadlines(
            course_org, cohort_org, sched, now, dry_run
        )

    if dry_run:
        # A preview reports only what it could not READ: nothing was written, so the
        # errors above are the state of the org, not of this run.
        return int(sched.unparseable)
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


def _registered_cohorts(course_org: str) -> list[str] | None:
    """The course org's registered cohorts, or None once it has said why it could not read
    them. The listing is one API read at the very top of every tick; a fault there must
    end the run with an `[err]` line a faculty member can act on, not a raw traceback."""
    try:
        return discover_cohorts(course_org)
    except Exception as exc:
        log_err(f"could not list cohorts for {course_org}: {exc}")
        return None


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
        help="Run every cohort registered with the course org (the release job).",
    )
    parser.add_argument(
        "--skip-autograde",
        action="store_true",
        help="Release phase only - snapshot, pre-flight and release, never grade.",
    )
    parser.add_argument(
        "--autograde-only",
        action="store_true",
        help="Autograde phase only - grade every frozen passed deadline, release nothing.",
    )
    parser.add_argument(
        "--list-cohorts",
        action="store_true",
        help="Print the course org's registered cohorts as a JSON list, and exit.",
    )
    parser.add_argument(
        "--check-course-config",
        action="store_true",
        help="Pre-flight the COURSE org's own dsl-course.yml and cohort registry, keep "
        "its digest issue in step, and exit 0. What Sync membership runs on a push to "
        "either file - the fast path under this cron's own hourly floor.",
    )
    parser.add_argument(
        "--now", default=None, help="Override 'now' (ISO date/datetime) - for testing."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    now = _parse_now(args.now)

    if args.skip_autograde and args.autograde_only:
        log_err(
            "--skip-autograde and --autograde-only ask for opposite halves of a run."
        )
        return 1

    if args.check_course_config:
        # Always 0: a config faculty have to fix is a CONTENT fault, and Sync membership -
        # which is what runs this - must not go red for one (see FINAL DECISIONS).
        return _preflight_course(args.course_org, now, args.dry_run)

    if args.list_cohorts:
        # The grading job's matrix, and the ONLY thing that may reach stdout: the workflow
        # captures it whole into a step output and hands it to fromJSON. `ghcli.gh` prints
        # its retry notices ("[wait] rate-limited...") to stdout before a successful
        # retry, and a second line there writes a step-output line with no `=` - GitHub
        # rejects the file, the step fails, and because the listing runs first the release
        # step is skipped. So one transient 403 would cost a whole release tick. Every log
        # line the read makes goes to stderr for the duration; only the answer is printed.
        with contextlib.redirect_stdout(sys.stderr):
            cohorts = _registered_cohorts(args.course_org)
        if cohorts is None:
            return 1
        print(json.dumps(cohorts))
        return 0

    phases = {
        "release": not args.autograde_only,
        "autograde": not args.skip_autograde,
    }

    if args.all_cohorts:
        # The COURSE org's own config FIRST, and before the listing below rather than
        # beside the cohorts: a registry nobody can parse is one of the faults this
        # reports, and it is also what makes that listing raise - so reported here, or
        # never. Once per tick, on a real release pass only; the grading matrix's
        # per-cohort legs and a laptop's single-cohort run are not the course's tick.
        if phases["release"]:
            _preflight_course(args.course_org, now, args.dry_run)
        cohorts = _registered_cohorts(args.course_org)
        if cohorts is None:
            # A listing that could not be READ is not "no cohorts": go red so the failure
            # issue files, rather than reporting a quiet no-op tick.
            return 1
        if not cohorts:
            # A freshly bootstrapped course org has this cron installed before any
            # cohort is registered - that gap is normal, not an hourly failure.
            log(
                f"  [skip] no cohorts registered with {args.course_org}; "
                "nothing to release."
            )
            return 0
        rc = 0
        # ONE cadence reading for the whole course, taken before anything ships - the
        # question is how long since the previous tick, and firing the releases first would
        # answer it about this run's own writes. Only on a real, whole-course release pass:
        # the manual button defaults to a dry run, and a single `--cohort-org` invocation
        # is a laptop, so neither may arm an alarm, comment on one, or close one.
        verdict = None
        if phases["release"] and not args.dry_run:
            try:
                verdict = cadence.evaluate(
                    now, cadence.fetch_runs(args.course_org), cadence.own_run_id()
                )
            except Exception as exc:
                # Worth the red X - this is the check that watches the drivers - and worth
                # nothing more: `verdict` stays None and every release below still runs.
                log_err(f"could not read {args.course_org}'s run history: {exc}")
                rc |= 1
        for cohort in cohorts:
            # One cohort's raised failure (a read helper that couldn't reach the API, a
            # site sync that blew up) must not abort the remaining cohorts' scheduled
            # releases - log it, mark the batch failed, and carry on. The same per-cohort
            # isolation PR #151/#146 applied to the nightly refresh.
            try:
                rc |= run(
                    args.course_org,
                    cohort,
                    now,
                    dry_run=args.dry_run,
                    verdict=verdict,
                    **phases,
                )
            except Exception as exc:
                log_err(f"scheduler run for {cohort} failed: {exc}")
                rc |= 1  # accumulate, don't clobber prior cohorts' status bits
        # Last, so a driver-health alarm can never delay a release: the drivers being down
        # is not this run's problem to fix, only to report.
        if verdict is not None:
            rc |= cadence.report_course(args.course_org, verdict, args.dry_run)
        return rc

    if not args.cohort_org:
        log_err("pass --cohort-org or --all-cohorts.")
        return 1
    return run(args.course_org, args.cohort_org, now, dry_run=args.dry_run, **phases)


if __name__ == "__main__":
    sys.exit(main())
