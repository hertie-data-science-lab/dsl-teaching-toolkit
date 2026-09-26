"""dsl-course scheduler -- datetime-driven auto-release.

The same idempotent release functions as the manual workflows, fired automatically from the
semester's own `semester-config/schedule.yml` `releases:` plan (see
`dsl_course.schedule`). Each labelled release carries a `when` datetime and a mix of
actions - `deploy` (copy a source path from a COURSE-org repo into a SEMESTER-org repo) and
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
when the external dispatcher has gone quiet, and a semester's "late delivery" when a dated
moment shipped well after its datetime. Only a real, whole-course pass touches it (never a
dry-run preview and never a single-semester invocation), and it is disarmed until the external
dispatcher has been seen at least once.

TWO PHASES, which the workflow runs as separate jobs so that neither waits on the other
(`--skip-autograde` runs the first, `--autograde-only` the second; passing neither runs
both, which is what a local invocation wants):

1. RELEASE - snapshot every passed grading deadline, pre-flight the plan's sources, then
   fire every due release, for every semester in one pass. Minutes at most.
2. AUTOGRADE - grade every passed deadline once, for ONE semester. This is the slow half (it
   clones and runs every submission, on a 120-minute budget), which is why it is per semester
   and out of the release path: a semester mid-grading must not delay a release due meanwhile.

The two phases talk to each other only through the snapshot file, never in memory - so the
autograde phase is correct a tick later, in another process, or after a release pass that
failed.

Assignment handouts are declared with the rest of the assignment's lifecycle -
`assignments.<slug>.handout_datetime` - and synthesised into releases here
(_handout_releases), so they fire through the exact machinery a deploy does. The model
solution rides on that same release once `assignments.<slug>.solution_datetime` has
passed - Release assignment's `solution_datetime: now`, on a clock.

Every tick also drives each assignment's late cutoff (`schedule.grading_cutoff_datetime`:
due + `late_window_days`), whether or not the semester uses `releases` at all:

1. FREEZE (release phase). For every assignment whose grading deadline has gone by and that
   has no snapshot yet, record the commit each submission repo is graded at into
   `semester-config/.system/snapshots/<slug>.csv` (see `dsl_course.collect`). That timestamp is the
   server's, not the student's, which is the only reason the pin can be trusted.
2. AUTOGRADE, ONCE (autograde phase). Run the autograder for every frozen assignment -
   template `<slug>-<tag>` in the course org. The fire-once marker is the
   `.system/autograde/<slug>/_graded.json` sentinel (or the `_skipped.json` record): present means
   already graded, so never again.

Sources are always read from the course org and destinations always written to the semester
org - the two orgs come from the invocation (`--course-org` / `--semester-org`), never from
the schedule, which names repos only.

Usage (the workflow's two jobs are the first two lines; --now is for testing). Bare, it
PREVIEWS - prints what would fire and writes nothing - and acts only with --no-preview:
    python3 -m dsl_course.scheduler --course-org COURSE --all-semesters --skip-autograde --no-preview
    python3 -m dsl_course.scheduler --course-org COURSE --semester-org SEMESTER --autograde-only --no-preview
    python3 -m dsl_course.scheduler --course-org COURSE --list-semesters
    python3 -m dsl_course.scheduler --course-org COURSE --check-course-config --no-preview
    python3 -m dsl_course.scheduler --course-org COURSE --all-semesters --no-preview
    python3 -m dsl_course.scheduler --course-org COURSE --semester-org SEMESTER
    python3 -m dsl_course.scheduler --course-org COURSE --semester-org SEMESTER --now 2026-09-15T14:00
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import NamedTuple

from . import (
    cadence,
    config_digest,
    discovery,
    issues,
    notify,
    roster,
    schedule,
    site,
    source_digest,
    status,
    sync_faculty,
    sync_teams,
    team_formation,
    teams,
    teardown,
    welcome,
)
from .assign import provision_all, solution_released
from .central import CENTRAL, CENTRAL_REF
from .collect import (
    SnapshotResult,
    collect,
    has_autograde_results,
    load_snapshots,
    snapshot_assignment,
    snapshot_path,
    sync_sheet,
)
from .course import COURSE_ADMIN_TEAM, shared_repo, submission_repo
from .deploy import WITHHELD_ROOT_STUBS, deploy_many, is_withheld_stub
from .faults import ConfigFault, FaultKind, Severity, Unusable
from .gh_contents import get_file_content
from .ghcli import gh, start_budget
from .grades import (
    grading_config_faults,
    load_grading_spec,
    marks_due,
    semester_sheet_faults,
    sheet_path,
    sync_team_lock,
)
from .log import (
    CLIParser,
    Summary,
    add_preview_flag,
    log,
    log_err,
    log_ok,
    log_person,
    log_step,
    plural,
)
from .repos import listed_is_private, set_visibility
from .schedule import Release, grading_cutoff_datetime
from .schedule_plan import deploy_dest

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

    The cutoff is `schedule.grading_cutoff_datetime`: the due date plus the effective
    `late_window_days`, so the freeze is at the END of the late window - the sheet's
    header and every receipt promise work is accepted until then.
    Whether each assignment has already been snapshotted or graded is still a separate
    question (see `_snapshot_passed_deadlines` / `_autograde_passed_deadlines`), and so is
    whether there is anything to collect from it at all: this answers "the cutoff has
    passed", which is also what `_refresh_open_sheets` reads it for, and an assignment
    handed in off GitHub has a cutoff like any other. The COLLECTION gate belongs to the
    two passes that collect, and is applied there."""
    passed = []
    for slug in sched.assignments:
        at = grading_cutoff_datetime(sched, slug)
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
            f"{d.semester_dest_repo}/{deploy_dest(d)}{suffix}"
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


class Decision(NamedTuple):
    """Why something due will not be released - what the dry run says after its preview,
    one line each, for the console to lift into an outcome's `reasons`. `ref` is the
    schedule entry (a release label, or an assignment slug for its handout); `text` never
    names a student, a handle or a `<slug>-<handle>` repo: the log is public."""

    ref: str
    code: str
    text: str

    def line(self) -> str:
        return f"Decision: {self.ref} not released: {self.code} {self.text}"

    def reason(self) -> dict:
        """This decision as one of an outcome's `reasons`."""
        return {"code": self.code, "text": f"{self.ref} not released: {self.text}"}


def preview_summary(due: list[Release], decisions: list[Decision]) -> Summary:
    """What the dry run tells the console: how much of what is due would go out now, and
    every decision about what would not, as `reasons`. Entry names and counts only."""
    held = {d.ref for d in decisions}
    going = [
        r for r in due if r.label not in held and (r.assignment_slug or "") not in held
    ]
    reasons = [d.reason() for d in decisions]
    counts = {"due": len(due), "would_release": len(going), "held": len(decisions)}
    if not due and not decisions:
        return Summary("Automation has nothing due in this semester right now.", counts)
    text = (
        f"Automation would release {len(going)} of "
        f"{plural(len(due), 'due entry', 'due entries')} now"
    )
    if decisions:
        text += f"; {plural(len(decisions), 'reason')} why something would not go out"
    return Summary(f"{text}.", counts, reasons)


def _entry_ref(where: str) -> str:
    """`releases.lecture-2` / `assignments.a1` -> the entry's own name."""
    return where.split(".", 1)[1] if "." in where else where


def source_decisions(faults: list[ConfigFault], now: datetime) -> list[Decision]:
    """A `SOURCE_MISSING` or `WITHHELD` decision for every source fault whose moment has
    arrived - what `schedule.source_faults` found, said about what is due now."""
    out = []
    for f in faults:
        if f.fires is None or f.fires > now or f.kind is None:
            continue
        code = "WITHHELD" if f.kind is FaultKind.WITHHELD else "SOURCE_MISSING"
        out.append(Decision(_entry_ref(f.where), code, f"{f.what}."))
    return out


def archived_decisions(sched: schedule.Schedule, now: datetime) -> list[Decision]:
    """One `SEMESTER_ARCHIVED` decision per entry that is due in a semester already archived:
    nothing is ever released into a frozen org."""
    refs = [r.label for r in due_releases(sched.releases, now)] + [
        slug
        for slug, entry in sched.assignments.items()
        if entry.handout_datetime is not None and entry.handout_datetime <= now
    ]
    return [
        Decision(
            ref,
            "SEMESTER_ARCHIVED",
            "This semester is archived, so nothing is released.",
        )
        for ref in refs
    ]


def _stub_decisions(
    course_org: str, due: list[Release], now: datetime
) -> list[Decision]:
    """`SOURCE_UNWRITTEN` for a due copy of a root stub (SYLLABUS.md, README.md) that is
    still the placeholder the toolkit seeded - the release withholds it."""
    out = []
    for release in due:
        for d in release.due_deploys(now):
            path = d.course_source_path.strip("/")
            if path not in WITHHELD_ROOT_STUBS:
                continue
            text = get_file_content(course_org, d.course_source_repo, path)
            if text is not None and is_withheld_stub(path, text):
                out.append(
                    Decision(
                        release.label,
                        "SOURCE_UNWRITTEN",
                        f"{d.course_source_repo}/{path} is still the placeholder, so it "
                        f"is held back.",
                    )
                )
    return out


def _handout_decisions(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    due: list[Release],
    listing: dict[str, dict] | None,
) -> list[Decision]:
    """`TEAMS_INCOMPLETE` for a due group handout with no teams yet, and `ALREADY_DONE`
    for a due handout whose every repo is already in the semester."""
    out = []
    handouts = [r for r in due if r.assignment and r.assignment_slug]
    if not handouts:
        return out
    per_team = teams.load(semester_org)
    students = roster.load(semester_org) or []
    onboarded = [s.github_handle for s in roster.enrolled(students) if s.onboarded]
    for release in handouts:
        key = release.assignment_slug
        entry = sched.assignments.get(key)
        gspec = load_grading_spec(
            course_org, release.assignment, semester_org=semester_org, slug=key
        )
        if entry is None or not gspec.creates_repos:
            continue
        name = schedule.semester_name(key, entry)
        units = list(teams.teams_for(per_team, key)) if gspec.is_group else onboarded
        if gspec.is_group and not units:
            out.append(
                Decision(
                    key,
                    "TEAMS_INCOMPLETE",
                    "No teams have formed yet, so there is nobody to hand it out to.",
                )
            )
            continue
        if listing is None or not units:
            continue
        repos = (
            [submission_repo(name, u) for u in units]
            if gspec.creates_unit_repos
            else [shared_repo(name)]
        )
        have = {name.casefold() for name in listing}
        if all(repo.casefold() in have for repo in repos):
            out.append(
                Decision(key, "ALREADY_DONE", "Every copy has already been handed out.")
            )
    return out


def dry_run_decisions(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    due: list[Release],
    now: datetime,
    listing: dict[str, dict] | None,
) -> list[Decision]:
    """Every decision the dry run can name for this semester. Guarded: a read that fails
    costs its decisions, never the preview."""
    out: list[Decision] = []
    for decide in (
        lambda: source_decisions(schedule.source_faults(sched, course_org), now),
        lambda: _stub_decisions(course_org, due, now),
        lambda: _handout_decisions(course_org, semester_org, sched, due, listing),
    ):
        try:
            out += decide()
        except Exception as exc:
            log_err(
                f"could not work out every decision for {semester_org} "
                f"({type(exc).__name__})"
            )
    return out


# ---------------------------------------------------------------------- gh/git wiring


def _execute_nondeploy(
    course_org: str,
    semester_org: str,
    release: Release,
    listing: dict[str, dict] | None,
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
        # Individual or group is the template's own grading_config.yml, read by
        # provision_all - so a scheduled group handout provisions per TEAM, not one repo
        # per student, and the cron cannot disagree with the button about it.
        failed, changed = provision_all(
            course_org,
            release.assignment,
            semester_org,
            solution=release.assignment_solution,
            # Hourly: leave existing repos alone (the manual button still repairs access).
            touch_existing=False,
            scheduled=True,
            # WHICH entry this release was synthesised from. Two may hand out from one
            # template, and the tick knows which it is firing - so it says, rather than
            # letting the far end pick the first and hand out the other one's repos.
            slug=release.assignment_slug,
            # The tick's ONE listing of the semester, which this handout both reads
            # and adds every repo it creates to - so the next release in this same tick
            # sees them (see `assign.provision_all`).
            listing=listing,
        )
        if failed != 0:
            errors += 1
    return errors, changed


def _collects(course_org: str, entry: schedule.AssignmentEntry) -> bool:
    """Whether this assignment has commits to freeze and grade at all - false for work
    handed in off GitHub, where the freeze would 404 per student per tick for ever. Off
    the memoised read `due_snapshots` has already paid for."""
    return load_grading_spec(course_org, entry.course_source_repo).collects_commits


def _snapshot_passed_deadlines(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> int:
    """Freeze every passed-deadline assignment that has no snapshot yet. Write-once: an
    assignment already frozen is skipped silently, so this is a no-op on every tick after
    the first. Returns the error count.

    What was frozen is NOT returned: the snapshot file itself is the handoff to the
    autograde phase, which runs in another job (and so another process) entirely.

    `listing` is the tick's own (see `run`), read for `pushed_at`. None is "it could not
    be read", handed down as None rather than as an empty org: every reader has its own
    answer to not knowing, and the freeze's is to take a listing of its own."""
    errors = 0
    for slug, deadline in due_snapshots(course_org, sched, now):
        entry = sched.assignments[slug]
        if not _collects(course_org, entry):
            continue
        # every semester-side artefact keys on the assignment's semester NAME, not its slug
        name = schedule.semester_name(slug, entry)
        if load_snapshots(semester_org, name) is not None:
            # already frozen - never re-snapshot, a late push must not move it
            continue
        if dry_run:
            log(f"    PREVIEW  snapshot {snapshot_path(name)} (deadline {deadline})")
            continue
        log_step(f"  snapshot {name} (deadline {deadline})")
        # Resolve group-ness the SAME way grading does, off the template's own
        # grading_config.yml - so the snapshot freezes the exact repos grading scores.
        # A template that cannot be found leaves it individual, which is the parse's
        # default anyway.
        template = _assignment_template(course_org, slug, entry)
        # The SHAPE, off the same one read: which repos are frozen (one per unit, or one
        # drop box with a folder each) and whether each pin is narrowed to a folder. A
        # template that cannot be found leaves both at the parse's own defaults.
        gspec = load_grading_spec(course_org, template) if template else None
        is_group = gspec is not None and gspec.is_group
        # `name` names the repos, `slug` (the schedule key) is what teams.csv is keyed on.
        # A FAILED freeze counts; NOTHING_TO_FREEZE (nobody handed out yet) does not, and
        # neither writes a snapshot file - which is what keeps the autograde phase off an
        # assignment that would otherwise score write-once zeros for the whole semester.
        result = snapshot_assignment(
            semester_org,
            name,
            deadline,
            is_group=is_group,
            teams_key=slug,
            tz=sched.timezone,
            listing=listing,
            shared=gspec is not None and gspec.submit_shared,
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
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Autograde every passed-deadline assignment exactly once - zero config. Returns the
    error count.

    Fire-once: the `.system/autograde/<slug>/_graded.json` sentinel (or the `_skipped.json` record) in
    semester-config is the marker. Absent means never machine-graded, so grade now; present
    means graded already, so never again - which is what stops an hourly re-run from recomputing
    scores a marker has since hand-edited. A deliberate re-grade = delete `.system/autograde/<slug>/`
    (delete `.system/autograde/<slug>/` to let a later tick regrade).

    A missing template repo, a template with no `solution` branch, and `autograde: false`
    are all skips, not failures: plenty of assignments are hand-marked. Group vs individual
    is not guessed here - `collect` resolves it from the semester schedule / grading_config.yml."""
    errors = 0
    for slug, deadline in due_snapshots(course_org, sched, now):
        if not _collects(course_org, sched.assignments[slug]):
            continue
        # the fire-once marker is keyed on the semester NAME - it must agree with what
        # collect writes, or a passed deadline re-grades every tick
        name = schedule.semester_name(slug, sched.assignments[slug])
        if has_autograde_results(semester_org, name):
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
        if load_snapshots(semester_org, name) is None:
            log(f"  [wait] autograde {slug} - no completed snapshot yet, not grading")
            continue
        if dry_run:
            log(f"    PREVIEW  autograde {slug} via {template} (deadline {deadline})")
            continue
        log_step(f"  autograde {slug} via {template} (deadline {deadline})")
        # `slug` here is the schedule KEY (`due_snapshots` yields keys), which is exactly
        # what `collect` needs to tell two entries on one template apart.
        if (
            collect(
                course_org, template, semester_org, deadline, scheduled=True, slug=slug
            )
            != 0
        ):
            errors += 1
    return errors


def _run_releases(
    course_org: str,
    semester_org: str,
    due: list[Release],
    now: datetime,
    listing: dict[str, dict] | None,
) -> tuple[int, bool]:
    """Fire every due release's due actions. Returns `(errors, site_changed)`. `now` gates
    each action individually: a deploy with its own deploy_datetime fires on its own clock,
    an entry's handout at its event_datetime - an entry can be due for one and not (yet)
    the other.

    `site_changed` is "this pass moved something the semester website shows". The sync itself
    is `_release_phase`'s, so a tick that opens a team-formation window and fires a release
    still renders once - and a tick that moves only the window renders at all."""
    errors = 0
    # Batch EVERY due release's due deploys through one deploy_many: each unique source
    # and dest repo is cloned once for the whole run, not once per copy.
    all_deploys = [d for release in due for d in release.due_deploys(now)]
    deploy_errors, changed = 0, False
    if all_deploys:
        deploy_errors, changed = deploy_many(
            course_org, semester_org, all_deploys, sync=False
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
                course_org, semester_org, release, listing
            )
            errors += handout_errors
            # Only a handout that PROVISIONED something has anything new to show the site.
            # `due_releases` is cumulative - every handed-out assignment is due again on
            # every tick - so setting this unconditionally re-rendered the whole semester
            # website once an hour, for the rest of the term, off a pass that had skipped
            # every repo.
            did_assign = did_assign or handout_changed

    return errors, changed or did_assign


def _solution_due(
    semester_org: str, slug: str, entry: schedule.AssignmentEntry, now: datetime
) -> bool:
    """Whether this tick should push the model solution for `slug`.

    The datetime check is cheap and comes first, so the fire-once read is paid only by an
    assignment whose solution moment has actually arrived - not by every assignment on
    every tick."""
    if entry.solution_datetime is None or entry.solution_datetime > now:
        return False
    return not solution_released(semester_org, schedule.semester_name(slug, entry))


def _handout_releases(
    course_org: str, semester_org: str, sched: schedule.Schedule, now: datetime
) -> list[Release]:
    """Synthetic releases for `assignments.<slug>.handout_datetime` - the whole assignment
    lifecycle (handout_datetime/due_datetime/solution_datetime) is declared in ONE block,
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
                assignment_solution=_solution_due(semester_org, slug, entry, now),
            )
        )
    return out


def _preflight_sources(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    extra: list[ConfigFault] | None = (),
) -> int:
    """Check the plan's sources against the course org and keep the semester's digest issue
    in step. Always returns 0.

    `extra` is everything else about schedule.yml that this tick worked out for itself and
    that the parse alone could not know - today, the assignments whose team-formation
    window is running out with students still unteamed (`team_formation.window_faults`).
    It goes into the SAME list, and therefore the same issue and the same letters: the
    entry a reader would edit is in this file, and one digest per file is what keeps it
    fixable in one place.

    `None` is this tick failing to work those out at all, and it SKIPS the sync entirely,
    on the rule `_config_faults` states: absent from the map is not the same as no faults.
    An empty list is what closes the issue, so syncing the digest without the windows would
    tell a semester whose teams.csv merely hit a rate limit that its standing team-formation
    fault had been fixed - a Cleared comment and a mail on one tick, and the same fault
    filed again as New on the next. Left exactly as it was instead, and the source faults
    wait a tick with it: a missed one is picked up by the next.

    Nothing here fails the run, at any rung. A source nobody has staged is a CONTENT
    fault, and it is delivered where the people who can fix it are looking: the semester's
    digest issue, which @mentions the instructors and links the line to edit, plus the
    mail `notify` sends the same people off the same transitions. The exit code belongs to
    the run itself - it broke, or it did not - and a missing source used to spend it on
    every tick, up to eight red runs an hour mailing a bot account about a folder only
    faculty can write. The signature keeps its int so the caller's `errors +=` reads the
    same as every other phase."""
    if extra is None:
        log(
            f"  [skip] {semester_org}'s {schedule.SCHEDULE_PATH} digest - who is still "
            "without a team could not be read this tick"
        )
        return 0
    try:
        sources = schedule.source_faults(sched, course_org)
    except Exception as exc:
        log_err(
            f"could not check {semester_org}'s sources ({type(exc).__name__}): {exc}"
        )
        sources = []
    # ONE issue for schedule.yml, carrying both of the ways it goes wrong: a source that
    # is not staged yet (counting down to its release) and an entry the parser could not
    # read at all (already out of the plan). A reader asked to fix this file finds
    # everything wrong with it in one place, and each fault is still filed - and notified -
    # on its own clock (`ConfigFault.fires`).
    # NOT short-circuited when empty: an empty list is what CLOSES the issue, and the
    # tick after the last fault is fixed is the one that has to say so.
    faults = sources + list(sched.faults) + _no_archive_date(sched) + list(extra)
    if sources:
        log_step(
            f"{len(sources)} source(s) in {semester_org}'s plan not staged in "
            f"{course_org} (worst: {schedule.worst_severity(sources, now)})"
        )
    if sched.faults:
        log_step(
            f"{len(sched.faults)} entr(y/ies) in {semester_org}'s "
            f"{schedule.SCHEDULE_PATH} the scheduler cannot read"
        )
    # Local, because everything downstream speaks about time to a human: the deadline
    # faculty wrote, and the overnight window where a notification is held rather than
    # sent.
    local = schedule.in_semester_zone(sched, now)
    # Who to tell, asked ONCE and only if asked at all: the digest @mentions them and the
    # mail is addressed to them, so asking git twice would be two reads and two chances to
    # disagree - and `sync` calls this only on a tick with something to say, because the
    # answer costs a blame query, a instructors.yml read and a commit lookup per repo.
    routing = notify.Routing()

    def whom() -> list[str]:
        nonlocal routing
        try:
            routing = notify.route(semester_org, course_org, faults, local)
        except Exception as exc:
            log_err(
                f"could not work out who to tell about {semester_org}'s sources: {exc}"
            )
        return routing.logins

    try:
        digest = source_digest.sync(
            semester_org,
            course_org,
            faults,
            local,
            dry_run=dry_run,
            resolve_mention=whom,
        )
    except Exception as exc:
        log_err(f"could not update {semester_org}'s source digest: {exc}")
        return 0
    if digest.errors:
        # `sync` has already said what went wrong, line by line. Recorded here and NOT
        # returned: an undelivered notification must not stop a release.
        log_step(
            f"{semester_org}'s source digest: {digest.errors} error(s) - not delivered"
        )
    try:
        # The mail beside the @mention, for the transitions the digest just recorded. It
        # counts its own failures and never raises; this catch is for the one it did not
        # foresee, on the same terms as the digest above - a release is not worth a
        # notification.
        unsent = notify.notify_source_transitions(
            semester_org, course_org, digest, local, routing, dry_run=dry_run
        )
        # The same issue's other half, in its own letter: an entry nobody can read names
        # the file and what it costs, not a deadline it does not have.
        unreadable = notify.notify_config_faults(
            source_digest.SCHEDULE,
            semester_org,
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
                semester_org,
                {k: digest.was.get(k) for k in unsent.keys},
                digest.reminder_was,
            )
    except Exception as exc:
        log_err(f"could not mail {semester_org}'s source faults: {exc}")
    return 0


def _config_faults(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    listing: dict[str, dict] | None,
) -> dict:
    """Every hand-edited file in this semester's semester-config EXCEPT schedule.yml, and
    what is wrong with each. `{digest: faults}`, and a file left OUT of it is one this tick
    could not read.

    schedule.yml is not here because it is already parsed - the plan this tick is running
    IS the parse - and because everything wrong with it belongs in the one issue the
    source pre-flight keeps (see `_preflight_sources`).

    Absent from the map is not the same as no faults: syncing a digest with an empty list
    closes its issue and tells the semester the file is fine, and "we could not look" is not
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
                    f"could not read {semester_org}'s {spec.file} "
                    f"({type(exc).__name__}): {exc}"
                )
                return
        out[spec] = found

    collect(
        config_digest.PEOPLE,
        lambda found: sync_faculty.read_semester_people(semester_org, found),
    )
    students: list[roster.Student] | None = None

    def read_roster(found: list) -> None:
        nonlocal students
        students = roster.load(semester_org, found)

    collect(config_digest.ROSTER, read_roster)
    # The roster is the allowlist teams.csv is vetted against, and only where it was
    # actually read: an empty one would report every member as a stranger. `None` is a
    # roster that is ABSENT as well as one whose read failed - a semester with no
    # students.csv already has that fault in its own digest, and vetting against the
    # empty set it implies would file one more teams.csv fault per row on top of it.
    known = sync_teams.known_handles(students) if students is not None else None
    collect(config_digest.TEAMS, lambda found: teams.load(semester_org, found, known))
    # The grading sheets, on this tick and no other: they have no push fast path, because a
    # sheet is edited all day while somebody marks and a mail per save would be a mail
    # about a file still being typed into.
    if schedule.drops_assignments(sched):
        # The checks below walk only the assignments that survived the parse. With one
        # dropped (or a NOT_MIGRATED file) they would find nothing about it and close its
        # issues as fixed: that is "we could not look", so their issues stay as they are.
        log(
            f"  {semester_org}'s schedule.yml leaves out an assignment - the grading "
            f"sheet and assignment digests are left as they are this tick"
        )
        return out
    collect(
        config_digest.GRADING_SHEETS,
        lambda found: semester_sheet_faults(course_org, semester_org, sched, found),
    )
    # The one file here that is not in this semester's semester-config at all: the
    # assignment's own definition, in the course org. Its faults keep the SOURCE clock -
    # they bite when the assignment is graded - so the engine files them under the rungs
    # and holds them overnight without knowing anything about this file in particular.
    collect(
        config_digest.GRADING_CONFIG,
        lambda found: grading_config_faults(
            course_org, semester_org, sched, found, listing
        ),
    )
    # The same pass checks the semester's own run settings against the handed-out repos:
    # those faults are about assignments.yml, and go in its issue.
    if config_digest.GRADING_CONFIG in out:
        (
            out[config_digest.GRADING_CONFIG],
            out[config_digest.ASSIGNMENTS],
        ) = config_digest.split_assignments(out[config_digest.GRADING_CONFIG])
    return out


def _sync_config_digest(
    spec: config_digest.Digest,
    course_org: str,
    semester_org: str,
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
    one is addressed to the course admins out of an org secret rather than to a semester's
    teaching team out of its instructors.yml. Everything else about the two is identical, which
    is why it is one function and one parameter."""
    routing = notify.Routing()
    ask = route or (lambda: notify.route(semester_org, course_org, faults, local))

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
            semester_org,
            course_org,
            faults,
            local,
            dry_run=dry_run,
            resolve_mention=whom,
        )
    except Exception as exc:
        log_err(f"could not update {semester_org}'s {spec.file} digest: {exc}")
        return
    if digest.errors:
        log_step(f"{semester_org}'s {spec.file} digest: {digest.errors} error(s)")
    try:
        unsent = notify.notify_config_faults(
            spec, semester_org, course_org, digest, local, routing, dry_run=dry_run
        )
        # A mail that did not go out is un-RECORDED rather than lost - see
        # `config_digest.hold`.
        if unsent.keys and not dry_run:
            config_digest.hold(
                spec,
                semester_org,
                {k: digest.was.get(k) for k in unsent.keys},
                digest.reminder_was,
            )
    except Exception as exc:
        log_err(f"could not mail {semester_org}'s {spec.file} faults: {exc}")


def _preflight_configs(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> int:
    """Check every hand-edited file in this semester's semester-config and keep one digest
    issue per file in step. Always returns 0.

    The hourly floor under the push fast path. An edit that leaves students.csv unreadable
    fires the dispatcher and is mailed within the minute; this is what catches the one
    that was pushed before any of this existed, the one whose dispatch failed, and the
    instructors.yml entry whose `end:` date lapsed while nobody was pushing anything.

    Nothing here fails the run, at any rung, for the reason `_preflight_sources` does not:
    a file faculty have to fix is a CONTENT fault, and the exit code belongs to the run
    itself. The signature keeps its int so the caller's `errors +=` reads the same as
    every other phase."""
    local = schedule.in_semester_zone(sched, now)
    for spec, faults in _config_faults(
        course_org, semester_org, sched, listing
    ).items():
        if faults:
            log_step(
                f"{len(faults)} entr(y/ies) in {semester_org}'s {spec.cite_file()} the "
                f"toolkit cannot use"
            )
        _sync_config_digest(spec, course_org, semester_org, faults, local, dry_run)
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
        discovery.read_semester_registry(course_org, faults)
    except Exception as exc:
        log_err(
            f"could not read {course_org}'s course config ({type(exc).__name__}): {exc}"
        )
        return None, admins
    return faults, admins


def _preflight_course(course_org: str, now: datetime, dry_run: bool) -> int:
    """Check the COURSE org's own hand-edited config and keep its digest issue in step.
    Always returns 0.

    ONCE PER RUN, not once per semester: `dsl-course.yml` and the semester registry belong to
    the course, and a course admin asked to fix one of them wants one issue, not one per
    semester saying the same thing.

    Nothing here fails the run, for the reason `_preflight_configs` does not: a file
    faculty have to fix is a CONTENT fault and the exit code belongs to the run itself.
    Nor does the semester listing that follows - a registry nobody can parse lists no
    semesters and releases nothing (`_registered_semesters`) - which is why this runs BEFORE
    that listing: reported here or not at all.

    `now` is UTC and stays UTC. The semester zone that dates a semester's notifications is a
    semester's own `schedule.yml` setting, and a course has no single one; every fault here
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
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> int:
    """Keep every open assignment's grading sheet current: one pass, after the freeze.

    Runs from the DUE date, not the cutoff, because that is when a grader starts marking
    and when the first late push lands. An assignment whose cutoff has PASSED is left
    alone: the freeze pass above owns it and `collect` seals its sheet, so re-deriving
    here would move facts the freeze has already settled.

    A sheet that does not exist yet is CREATED - an assignment handed out before this pass
    shipped (or one whose handout ran before the sheet had rows) gets one on the next
    tick rather than never.

    `listing` is the tick's own (see `run`): every sheet refreshed in one pass reads the
    same rows, where each used to take a listing of its own."""
    sealed = {
        schedule.semester_name(slug, sched.assignments[slug])
        for slug, _ in due_snapshots(course_org, sched, now)
    }
    errors = 0
    for slug, entry in sched.assignments.items():
        if entry.due_datetime is None or entry.due_datetime > now:
            continue
        name = schedule.semester_name(slug, entry)
        if name in sealed:
            continue
        template = _assignment_template(course_org, slug, entry)
        if not template:
            continue  # no template to read the assignment's definition from
        if dry_run:
            log(f"    PREVIEW  refresh {sheet_path(name)}")
            continue
        log_step(f"  grading sheet {name}")
        # Spelt exactly as the snapshot pass above spells it, off the one memoised read
        # of the template's grading_config.yml.
        is_group = load_grading_spec(course_org, template).is_group
        if not sync_sheet(
            course_org,
            semester_org,
            sched,
            slug,
            name,
            template,
            is_group=is_group,
            now=now,
            listing=listing,
        ).written:
            errors += 1
    return errors


def _no_archive_date(sched: schedule.Schedule) -> list[ConfigFault]:
    """The advisory a semester earns by having no date on which it is ever closed out.

    Two ways to earn it, and they need different sentences: writing no `archive:` block at
    all, which is a decision - archiving is opt-in - and writing one no date can be
    derived from, which is a mistake. There is no date in either case.

    A fault with no `fires`, because there is no moment it bites at - that is exactly
    what is wrong with it. Raised here rather than by the parser, because a term with no
    dates yet is a perfectly ordinary August and must not fail `Validate schedule`; it is
    the unattended tick, term after term, that has standing to point out that this semester
    will still be live and joinable years from now.

    Capped at ADVISORY, which is what keeps it a line in the digest issue rather than
    email. An undated fault otherwise sits at WARNING - the notify bar itself
    (`faults.NOTIFY_FROM`) - so this would be routed, mailed to the teaching team, and
    then re-mailed by the digest's age ladder every term for ever, about a semester whose
    only sin is that nobody has typed a term end yet. The `.releaseignore` case caps
    itself for the same reason: listed, never anybody's inbox."""
    if sched.archive is not None and sched.archive.when is not None:
        return []
    if sched.archive is not None:
        what = (
            "this semester's `archive:` block names no `event_datetime:` and the semester "
            "no `semester_end`, so its archive date cannot be derived - nothing will "
            "ever archive it"
        )
        fix_text = (
            "give the `archive:` block an `event_datetime:`, or add a `semester_end:`"
        )
    else:
        what = (
            "this semester writes no `archive:` block, so nothing will ever archive it - "
            "it stays live, writable and joinable after the semester ends"
        )
        fix_text = (
            "add an `archive:` block; empty, it archives the semester 60 days after "
            "`semester_end`"
        )
    return [
        ConfigFault(
            "",
            what,
            field="archive",
            file=schedule.SCHEDULE_PATH,
            ceiling=Severity.ADVISORY,
            fix_text=fix_text,
        )
    ]


# What the notice issue's body records once the mail beside it has gone out. Body-as-state,
# like every other self-updating issue here (see `issues`): the tick that sends the mail
# writes this, and every tick after it reads it and says nothing more.
_MAILED_MARK = "<!-- dsl-archive-notice: mailed -->"


# The runbook section a reader of the notice is sent to for what an archive does and how
# to reopen a repo afterwards. An absolute URL into this toolkit, so it resolves from a
# semester's own `semester-config` - see the doc-filenames table in
# docs/reference/maintainers.md.
_ARCHIVE_DOC = "docs/10-grade-and-return-assignments.md#archiving-the-semester"


def _archive_notice_body(
    semester_org: str, when: date, mailed: bool, central_ref: str
) -> str:
    """The notice issue's body. Rewritten on every tick, which GitHub does not email
    about - the one mail is sent beside it, once.

    `central_ref` is the tier this semester's course org runs, so the runbook link lands on
    the docs its own workflows are checked out at rather than on whatever `release`
    happens to hold."""
    # The mark goes in ONLY when a mail actually went. It is the whole of this issue's
    # state, so stamping it on a tick that sent nothing - no address in instructors.yml, no
    # mail transport wired up - would record a mail that never happened and every later
    # tick would read it and stay quiet. Unstamped, the fortnight goes on offering it, and
    # the tick after somebody fixes the transport sends it.
    told = (
        f"The instructors have been emailed this once.\n\n{_MAILED_MARK}\n"
        if mailed
        else "No email went with this notice - see the run log.\n"
    )
    return (
        f"This notice is opened automatically by the scheduler.\n\n"
        f"On **{when}** all the repositories in `{semester_org}` will be archived. Nothing "
        f"is deleted and all read access permissions remain as they are, write accesses "
        f"are revoked and every repository is read-only from then on.\n\n"
        f"If there is anything you would like to make changes to, please make those "
        f"before then. To move this archiving date or remove it altogether, edit "
        f"`{schedule.SCHEDULE_PATH}` in this repo. To reopen a repository after it is "
        f"archived, un-archive it from its own Settings page - see "
        f"[Archiving the semester]"
        f"(https://github.com/{CENTRAL}/blob/{central_ref}/{_ARCHIVE_DOC}).\n\n"
        f"{told}"
    )


# What the comment says when an archive notice is closed because its date is no longer
# the one the schedule names. Closing it silently would read as "this happened".
_CALLED_OFF_COMMENT = (
    "This notice no longer matches `semester-config/schedule.yml`: the archive date has "
    "been moved, or the `archive:` block taken away - and a semester with no block is "
    "never archived automatically. Nothing was archived. If the semester should still be "
    "archived, the **Archive semester** button does it."
)


def _stale_archive_notices(semester_org: str, keep: str, dry_run: bool) -> int:
    """Close every open `Semester archives on <date>` notice in this semester but `keep`
    (`""` keeps none). Returns the error count.

    The notice names its date in the TITLE, so a moved date cannot edit it - a second
    notice is opened instead (`teardown.archive_notice_title` says why) - and nothing
    closed the first while the semester was live. `teardown._close_notices` sweeps every
    dated notice, but only at the seal, and a semester whose `archive:` block was removed
    is never sealed at all: its notice stood open for the rest of the term, naming a date
    on which nothing would happen.

    Stateless like the rest of the tick: it re-derives which notice SHOULD be open from
    the schedule and closes the others, so a duplicate opened during an outage goes with
    them, and a tick with nothing to close costs one issue listing and no writes."""
    repo = f"{semester_org}/{schedule.CONFIG_REPO}"
    try:
        stale = sorted(
            title
            for title in issues.open_titles(repo)
            if teardown.is_archive_notice(title)
            and (not keep or teardown.notice_date(title) != teardown.notice_date(keep))
        )
    except RuntimeError as exc:
        # A listing that could not be read is not "no notice is open".
        log_err(str(exc))
        return 1
    if not stale:
        return 0
    if dry_run:
        log(f"    PREVIEW close {len(stale)} stale archive notice(s) in {repo}")
        return 0
    return sum(
        issues.close_issues_titled(repo, title, _CALLED_OFF_COMMENT) for title in stale
    )


def _archive_notice(
    course_org: str, semester_org: str, when: date, now: datetime, dry_run: bool
) -> int:
    """Keep ONE "Semester archives on <date>" issue open in `semester-config`, and mail the
    teaching team once beside it. Returns the error count.

    The mail is the half that reaches anybody: this fires in the weeks after a term ends,
    when nobody is reading a semester's GitHub notifications. It is sent once and the issue
    body records that it went, so the fortnight of ticks after it says nothing more."""
    repo = f"{semester_org}/{schedule.CONFIG_REPO}"
    title = teardown.archive_notice_title(when)
    if dry_run:
        log(f"    PREVIEW  open `{title}` in {repo} and mail the instructors")
        return 0
    try:
        # The written title first, then every older prefix: a notice opened before the
        # rename is still THE notice, edited in place rather than duplicated.
        found = None
        for candidate in teardown.archive_notice_titles(when):
            found = issues.find_issue(repo, candidate)
            if found is not None:
                title = candidate
                break
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    mailed = found is not None and _MAILED_MARK in found.body
    if not mailed:
        mailed = notify.notify_semester_archiving(semester_org, course_org, when, now)
    try:
        central_ref = discovery.central_ref_for(course_org)
    except Exception:
        # Every exception, not just the RuntimeError a bad ref raises: this reads the
        # course org's `dsl-course.yml`, and a notice that failed over one unreadable
        # line in it would leave a semester with no warning that it freezes in a
        # fortnight. The default tier is the right guess, and the link still resolves.
        central_ref = CENTRAL_REF
    body = _archive_notice_body(semester_org, when, mailed, central_ref)
    if found is not None and found.body == body:
        # `upsert_issue` edits unconditionally, and this body changes exactly once in the
        # fortnight - when the mail goes. Four ticks an hour for fourteen days is about
        # 1,300 identical edits per semester otherwise, each one a write against the API
        # budget and a line in the repo's own activity.
        return 0
    return issues.upsert_issue(repo, title, body, existing=found).errors


def _archive_phase(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
) -> int:
    """Close the semester out on its own archive date, and give a fortnight's notice
    first. Returns the error count.

    AFTER the releases, so a copy due on the archive date still ships before the freeze;
    and only ever for a semester whose `archive:` block asked for it, because freezing a
    whole org nobody asked to freeze is the worst possible use of a default.

    Every path also converges the NOTICE, because an `archive:` block can be taken away
    or its date moved after one is open, and the title names the old date for ever
    otherwise (`_stale_archive_notices`).

    `teardown.close_out` is idempotent and re-entrant, so a run that died half way is
    simply picked up by the next tick - which is why this needs no fire-once marker of its
    own. The tick after a successful one never reaches here at all: the sealed
    `semester-config` takes the semester out of `discovery.live_semesters`."""
    archives = sched.archive.when if sched.archive else None
    if archives is None:
        return _stale_archive_notices(semester_org, "", dry_run)
    today = now.date()
    if today >= archives:
        if dry_run:
            log(f"    PREVIEW  archive {semester_org} (due {archives})")
            return 0
        # `today`, not its own clock: this tick has just decided the semester is due, and
        # a `--now` past the archive date would otherwise fire a close-out that refused.
        return teardown.close_out(course_org, semester_org, dry_run=False, today=today)
    if archives - today > schedule.ARCHIVE_NOTICE:
        return _stale_archive_notices(semester_org, "", dry_run)
    errors = _archive_notice(course_org, semester_org, archives, now, dry_run)
    # The notice for TODAY's date is the one that should stand; any other dated one is a
    # date somebody moved, and two open notices naming two dates tell the teaching team
    # nothing.
    return errors + _stale_archive_notices(
        semester_org, teardown.archive_notice_title(archives), dry_run
    )


def _reprivatise_student_repos(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> int:
    """Put every `visibility: student_choice` repo back to private that has been published
    BEFORE its grading cutoff. Returns the error count.

    The one thing the toolkit still owes a shape whose flag it has given away. The student
    is `admin` of their own repo so that they can put their work in a portfolio once it
    has been marked; until the cutoff, a repo the world can read is a repo the rest of the
    semester can copy from, and no amount of wording in the brief stops that. So it is
    closed again, every quarter of an hour, until the door shuts - and after the cutoff
    this pass never touches a visibility again, which is what makes the promise on the
    assignment page true.

    Off the tick's OWN listing (`run`), which already carries each row's `visibility`: one
    PATCH per offending repo and no read of its own. A listing that could not be taken is
    "we could not look", and nothing is flipped on the strength of that.

    `repos.listed_is_private` is optimistic - an unknown row answers private - so a row
    that does not say `public` is left alone rather than PATCHed on a guess."""
    if not listing:
        return 0
    errors = 0
    for slug, entry in sorted(sched.assignments.items()):
        if entry.handout_datetime is None:
            continue
        gspec = load_grading_spec(
            course_org, entry.course_source_repo, semester_org=semester_org, slug=slug
        )
        if not gspec.visibility_is_students:
            continue
        at = grading_cutoff_datetime(sched, slug)
        if at is None or at <= now:
            continue
        # Sorting the org's repos into the assignments they came out of is not free, and
        # this pass runs on every tick of every semester - almost none of which has a
        # `student_choice` assignment inside its grading window at all. So it happens
        # here, below every gate above, and not before them. `assignment_rows` leaves
        # archived repos out: they are read-only, so the PATCH would 403 on every tick for
        # the rest of the term.
        public = [
            row["name"]
            for row in discovery.assignment_rows(
                listing, schedule.semester_name(slug, entry)
            )
            if not listed_is_private(row)
        ]
        if not public:
            continue
        # A COUNT in the run log and never a name: `<slug>-<handle>` is a student's
        # handle, and this line is written into a PUBLIC workflow log. The names go to
        # `log_person`, which prints only under DSL_VERBOSE on a local run.
        log_step(
            f"{slug}: {len(public)} repo(s) published before the late cutoff - "
            f"{'would be made' if dry_run else 'making them'} private again until "
            f"{at.isoformat()}"
        )
        for repo in public:
            if dry_run:
                log_person(f"    PREVIEW  {semester_org}/{repo} -> private")
                continue
            if set_visibility(semester_org, repo, "private", person=True):
                log_person(f"  [ok] {semester_org}/{repo} is private again")
            else:
                errors += 1
    return errors


def _team_formation_phase(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    windows: list[team_formation.Window] | None,
) -> tuple[int, bool]:
    """Bring the team-formation lock up to this moment. Returns `(errors, lock_changed)`.

    `windows` is this tick's roster x teams.csv diff, already read for the pre-flight's
    fault (`_release_phase`) and handed on rather than read again - it costs students.csv,
    teams.csv and a definition per assignment. None is a semester this tick could not read.

    The window it carries is computed from datetimes, so nothing else in the semester has to
    happen for one to open or shut - and until this ran on the tick, nothing did: the lock
    was written only by the membership sync, the nightly refresh, the bootstrap and a real
    handout, so a window opened whenever one of those next happened to fire rather than at
    its own moment.

    `lock_changed` is `sync_team_lock`'s own blob compare, handed back to `_release_phase`:
    the tick that moves the window is the tick that has to re-render the site showing it.
    True on exactly one tick per transition and NOT a retry flag - see the one site sync in
    `_release_phase` for what a failed render then costs.

    The FORM moves with the lock, and on the same tick: the Join-team form's Assignment
    field is a `required` dropdown rendered from the lock, so a window that opened this
    quarter of an hour is one a student cannot file an issue for until the form offers its
    slug. `refresh_join_workflows` would do it, but only at bootstrap and on the nightly
    cron - up to a day during which the site shows the callout and the mail links a chooser
    that refuses them. `refresh_join_team_form` pushes that one file, and only when the lock
    actually moved.

    THE MAIL CANNOT ABORT THE TICK. `notify_windows` re-raises whatever the transport
    raised - `mailer` turns a failed Graph token request into a RuntimeError, which is what
    an expired GRAPH_CLIENT_CERT, a revoked app or a tenant outage all look like - and
    nothing between here and the semester loop would have caught it: this phase runs BEFORE
    `_run_releases`, so every scheduled hand-out, archive and autograde for the semester would
    stop until somebody rotated the certificate. Contained exactly as the site sync in
    `_release_phase` is: logged, counted, and the tick carries on. The claim is released
    before it reaches here (`notify_windows`), so nothing is recorded as told that was not.

    The mail goes out AFTER the lock, and that order matters: the lock is what the
    Join-team form reads, so a student who acts on the message within the minute must not
    find the form still refusing them. It is also the only thing here that can be held -
    `notify_windows` waits out the semester's quiet hours - and the window opening on time is
    not negotiable, while a message arriving at 07:00 rather than 02:00 is.

    THE TICK ONLY WRITES THE LOCK FOR A SEMESTER WHOSE PLAN HAS ASSIGNMENTS IN IT. A semester
    whose `assignments:` block is empty - most of them, for most of a term's planning - was
    paying a repo probe and a contents read every quarter of an hour, ~192 a day, to write
    `assignments:\n  {}` over itself.

    Gated on the PLAN and not on "does this semester have a self-select assignment", which is
    the tighter question and the wrong one. `team_formation` is declared in the COURSE
    org's `grading_config.yml`, and no semester-side dispatcher watches that file - so a
    course that switches its only self-select assignment to `assigned` would leave the tick
    with nothing to write, the lock still saying `self_select`, and the Join-team form
    still accepting the self-selection faculty had just turned off, until the nightly
    refresh. Removing an assignment from the plan cannot strand the lock the same way,
    because that edit IS a push to schedule.yml and the membership sync fires on it."""
    errors, changed = 0, False
    if sched.assignments:
        write = sync_team_lock(
            course_org, semester_org, sched, now=now, dry_run=dry_run
        )
        # `sync_team_lock` logs its own preview and its own failure (it is written from
        # four other places that each need the same line), so there is nothing to say here.
        errors, changed = (0 if write.ok else 1), write.changed
        if changed:
            errors += welcome.refresh_join_team_form(semester_org)
    try:
        errors += team_formation.notify_windows(
            course_org, semester_org, sched, windows, now, dry_run=dry_run
        )
    except Exception as exc:
        log_err(f"team-formation mail failed in {semester_org}: {exc}")
        errors += 1
    return errors, changed


def _release_phase(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    now: datetime,
    dry_run: bool,
    verdict: cadence.Verdict | None,
    listing: dict[str, dict] | None,
    defer_site_sync: bool = False,
) -> int:
    """Snapshot every passed deadline, refresh every open grading sheet, pre-flight the
    plan's sources, and fire everything now due. Returns the error count. No grading: see
    `_autograde_passed_deadlines`.

    `verdict` is the cadence reading `main` took once for the whole course; None means this
    invocation does not report lateness at all (see `main`).

    `listing` is this tick's one listing of the semester (see `run`), handed to each pass
    below in the order they already run in. None means it could not be read."""
    # Re-sorted, not just concatenated: the synthesised handouts carry their own datetimes
    # and would otherwise land after every scheduled release whatever their date.
    releases = sorted(
        sched.releases + _handout_releases(course_org, semester_org, sched, now),
        key=release_order,
    )
    due = due_releases(releases, now)
    log_step(
        f"Scheduler {course_org} -> {semester_org} as of {now.isoformat()}: "
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
            errors += cadence.report_semester(
                course_org,
                semester_org,
                verdict,
                cadence.late_items(
                    releases, sched, verdict.prev_executed_at, verdict.now
                ),
                dry_run,
            )
        except Exception as exc:
            log_err(f"could not check {semester_org}'s plan for late deliveries: {exc}")
            errors += 1

    # Freeze passed deadlines FIRST: server-timed, and before anything grades against the
    # snapshot. Independent of the release plan - a semester can pin due dates without
    # scheduling a single release.
    errors += _snapshot_passed_deadlines(
        course_org, semester_org, sched, now, dry_run, listing
    )
    # Then the sheets, in the same pass and straight after: the freeze has just settled
    # every assignment past its cutoff, and everything else that is past its DUE date
    # gets its `info:` refreshed here.
    errors += _refresh_sheets(course_org, semester_org, sched, now, dry_run, listing)
    # And then the one shape whose visibility the toolkit does not own: a `student_choice`
    # repo published before its cutoff is closed again here. After the freeze above, so a
    # repo made public on the morning of the deadline is still snapshotted from the work
    # in it; before the releases below, because nothing a handout does depends on it.
    errors += _reprivatise_student_repos(
        course_org, semester_org, sched, now, dry_run, listing
    )
    # WHO is still waiting for a team, read ONCE for the whole tick and handed to both
    # passes that want it. Above the pre-flight because the fault it produces belongs in
    # the schedule.yml digest that pass already syncs - the entry a reader would edit is in
    # that file, and the window's close is the moment the fault counts down to.
    #
    # A parked group handout is otherwise SILENT: `assign.provision_all` logs `[wait] no
    # teams` and returns green, which is right for the tick and tells the teaching team
    # nothing, while the semester may not know it has anything to do.
    windows = team_formation.open_windows(course_org, semester_org, sched, now)
    # Marks whose `marks_return_datetime` has come: the complete sheets go back after the
    # releases below, and each incomplete one is a fault in the same digest.
    marks_faults, marks_ready = marks_due(course_org, semester_org, sched, now)
    window_faults = team_formation.window_faults(sched, windows)
    # Look AHEAD as well as at what is due: a deploy whose source was never staged fails
    # at its moment, which is far too late to write the thing. This is the only unattended
    # surface that notices - the commit-time validator only ever runs when someone edits
    # schedule.yml, and a plan written in August and forgotten is exactly the case that
    # needs catching. Never fatal to the run, at any rung: the fault is faculty's to fix
    # and the digest issue is how they hear about it (see _preflight_sources).
    errors += _preflight_sources(
        course_org,
        semester_org,
        sched,
        now,
        dry_run,
        None if window_faults is None else [*window_faults, *marks_faults],
    )
    # The same treatment for every other file faculty edit by hand: a roster nobody can be
    # enrolled from, a instructors.yml entry that grants nothing, a teams.csv row that will not
    # materialise. Each has its own digest issue and its own mail, and none of them can
    # red this run either.
    errors += _preflight_configs(course_org, semester_org, sched, now, dry_run, listing)
    # Last before the releases, and ABOVE the dry-run return so a preview says what it
    # would write: the team-formation window is pure datetime arithmetic, so this is the
    # pass that makes one open and shut on its own clock rather than whenever something
    # else in the semester happened to sync. Before the releases because the site sync at the
    # end of this phase renders off the lock - a tick that opens a window writes it here
    # and renders the site showing it at the end of the same tick.
    lock_errors, lock_changed = _team_formation_phase(
        course_org, semester_org, sched, now, dry_run, windows
    )
    errors += lock_errors

    if dry_run:
        for release in due:
            for line in describe(release, now):
                log(f"    PREVIEW  [{release.label}] {line}")
        for key in marks_ready:
            log(
                f"    PREVIEW  [{key}] return {key}'s marks (marks_return_datetime reached)"
            )
        decisions = dry_run_decisions(
            course_org, semester_org, sched, due, now, listing
        )
        for decision in decisions:
            log(decision.line())
        preview = preview_summary(due, decisions)
        return Summary(preview.text, preview.counts, preview.reasons, code=errors)

    release_changed = False
    if not releases:
        log(
            f"  (no releases or assignment handouts in {semester_org}/"
            f"{schedule.CONFIG_REPO}/{schedule.SCHEDULE_PATH} - {semester_org} not using "
            f"scheduled release)"
        )
    elif not due:
        log_ok("nothing due.")
    else:
        release_errors, release_changed = _run_releases(
            course_org, semester_org, due, now, listing
        )
        errors += release_errors
    errors += _return_marks(course_org, semester_org, sched, marks_ready)

    # THE one website sync of the tick, and the only place it is decided: a release that
    # provisioned something, or a team-formation window that moved, and nothing else. Both
    # are rare - the lock's content moves on exactly two ticks per assignment, the one that
    # opens the window and the one that shuts it - so an unchanged semester is never
    # re-rendered, and a tick with nothing due still shows a window that has just turned.
    #
    # ONE SHOT, and knowingly so. `lock_changed` is a blob compare, not a debt: by the next
    # tick the lock is current, so it is False again whatever this sync did with it, and a
    # failure below is never retried on the scheduler's clock. What that costs is bounded
    # and small - the students were mailed a link to a callout the page is not yet showing,
    # and the daily **Sync site** cron renders it within ~24h. Making the tick retry would
    # mean carrying "the site owes a render" somewhere durable, which is a second piece of
    # semester state to write, read and get wrong for a page that is a day stale at worst.
    if (release_changed or lock_changed) and defer_site_sync:
        # A run fired by a semester-config push, which (for schedule.yml, instructors.yml or
        # teams.csv) started Sync site too: rendering here as well pushed the site repo
        # alongside it and lost the race. Queued behind Sync site's own concurrency group
        # instead, so the render lands after this release.
        errors += _request_site_sync(course_org, semester_org)
    elif release_changed or lock_changed:
        # site.sync_site RAISES on a genuine tree/team read failure (post-PR2). This
        # semester's site-sync failure must be logged and counted, not an unhandled traceback
        # that aborts the run - and, under --all-semesters, every semester scheduled after it.
        try:
            if site.sync_site(course_org, semester_org) != 0:
                log_err("site sync incomplete after scheduled release")
                errors += 1
        except Exception as exc:
            log_err(f"site sync failed after scheduled release: {exc}")
            errors += 1
    return errors


def _return_marks(
    course_org: str, semester_org: str, sched: schedule.Schedule, ready: list[str]
) -> int:
    """Ask the course org's Distribute grades to return each assignment whose
    `marks_return_datetime` has come with every unit marked (a `return-marks` dispatch,
    scoped to the one assignment). It runs in THAT workflow, so an automatic return and a
    button press share one concurrency group and never overlap; the run writes the
    fire-once marker when it succeeds, and until then each tick asks again (the group
    holds one pending run). Returns the error count."""
    errors = 0
    for key in ready:
        name = schedule.semester_name(key, sched.assignments[key])
        code, out = gh(
            "api",
            "--method",
            "POST",
            f"repos/{course_org}/.github/dispatches",
            "-f",
            "event_type=return-marks",
            "-f",
            f"client_payload[semester_org]={semester_org}",
            "-f",
            f"client_payload[assignment]={name}",
        )
        if code != 0:
            log_err(f"could not ask Distribute grades to return {key}: {out[:200]}")
            errors += 1
        else:
            log_ok(f"asked Distribute grades to return {key} (marks_return_datetime)")
    return errors


def _request_site_sync(course_org: str, semester_org: str) -> int:
    """Ask the course org's Sync site to render `semester_org`, by the same `sync-site`
    dispatch the semester's semester-config sends. Returns the error count."""
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{course_org}/.github/dispatches",
        "-f",
        "event_type=sync-site",
        "-f",
        f"client_payload[semester_org]={semester_org}",
    )
    if code != 0:
        log_err(f"could not ask Sync site to render {semester_org}: {out[:200]}")
        return 1
    log_ok(f"asked Sync site to render {semester_org}")
    return 0


def run(
    course_org: str,
    semester_org: str,
    now: datetime,
    dry_run: bool = False,
    *,
    release: bool = True,
    autograde: bool = True,
    verdict: cadence.Verdict | None = None,
    defer_site_sync: bool = False,
) -> int:
    """One semester, one or both phases. The workflow's two jobs each ask for one phase
    (`--skip-autograde` / `--autograde-only`); a local run asks for both.

    `verdict` is the cadence reading of the drivers, taken once per course by `main`. None
    (the default) means this invocation reports no lateness - see `main` for which ones."""
    sched = schedule.load(semester_org)
    # A plan that could not be read AS A PLAN is not an empty one: while it stands, nothing
    # is released, handed out, snapshotted or graded for this semester. `load` has logged what
    # is wrong and where, and filed it as a fault on schedule.yml - which is what reaches
    # the person who can fix it, through the digest issue and the mail beside it
    # (`_preflight_sources`). It does NOT red this tick: the file is faculty's to fix, and
    # spending the exit code on it meant up to eight red runs an hour, and a `Scheduled
    # release is failing` issue every six, mailing the maintainer about a typo in a semester's
    # plan. (Individually DROPPED entries were always advisory - the rest of the plan runs.)
    errors = 0
    # The release pass's preview, on a preview: what the console shows of it.
    preview: Summary | None = None
    if sched.instance_unparseable:
        # Every run setting is unknown, so nothing here may act on a default the semester
        # did not choose. The tick stays green and the fault goes out on the schedule.yml
        # digest, like an unreadable schedule.yml.
        log_err(
            f"{semester_org}/{schedule.CONFIG_REPO}/{schedule.ASSIGNMENTS_FILE} is not "
            f"valid YAML - nothing is released, handed out or graded until it is fixed"
        )
        if release:
            _preflight_sources(course_org, semester_org, sched, now, dry_run, [])
        return 0
    if release:
        # ONE listing of the semester for the whole tick, taken here at the start of it and
        # handed to every pass that asks a question of the org: the freeze's `pushed_at`,
        # the sheet refresh's receipts, the grading-config digest's "what did this
        # assignment actually hand out?", the student_choice re-privatise, and both arms of
        # every handout. Each used to take one of its own, so the cost grew with the number
        # of ASSIGNMENTS a semester carries rather than with the number of semesters.
        #
        # It is MUTABLE, and the handouts write back into it every repo and gradebook they
        # create (`discovery.listing_row`): a tick fires every handed-out release, so the
        # second one has to see what the first just made or it makes it again and counts
        # GitHub's refusals as failures. That is what keeps this at one listing per tick.
        #
        # None is "we could not look", and it is handed down AS None - never as an empty
        # org - because each pass reads it in its own way: the digest reports nothing, the
        # freeze and the sheets take a listing of their own, the re-privatise flips
        # nothing, and no receipt is posted on a repo nobody could confirm is private.
        # Not taken for the autograde phase, which asks the org nothing.
        listing = discovery.listing_by_name(semester_org)
        phase = _release_phase(
            course_org,
            semester_org,
            sched,
            now,
            dry_run,
            verdict,
            listing,
            defer_site_sync,
        )
        errors += phase
        if isinstance(phase, Summary):
            preview = phase
        # Last of the release pass: the semester's own end. A release due today ships
        # first, and then - on the day - the whole org is frozen behind it.
        errors += _archive_phase(course_org, semester_org, sched, now, dry_run)
    if autograde:
        log_step(f"Autograde {course_org} -> {semester_org} as of {now.isoformat()}")
        errors += _autograde_passed_deadlines(
            course_org, semester_org, sched, now, dry_run
        )

    if dry_run:
        # A preview writes nothing, so it has nothing of its own to report: the errors
        # above are the state of the org, not of this run.
        return Summary(preview.text, preview.counts, preview.reasons) if preview else 0
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


def _registered_semesters(course_org: str) -> list[str] | None:
    """The semesters this tick releases into, or None once it has said why it could not read
    them. The listing is one API read at the very top of every tick; a fault there must
    end the run with an `[err]` line a faculty member can act on, not a raw traceback.

    LIVE semesters (`discovery.live_semesters`): a semester that has been closed out is frozen,
    and every release, snapshot and digest write this tick would make on it 403s - four
    times an hour, for the rest of the course's life.

    A registry that is MALFORMED is not a failed read: it is a hand-edited file a course
    admin has to fix, already reported to them by `_preflight_course` (which runs first,
    for exactly this reason), so it lists no semesters and leaves the tick green. Anything
    else - a rate limit, a token that lost its scope - is a read that failed, and the run
    is owed its red X for it."""
    try:
        return discovery.live_semesters(course_org)
    except Unusable as exc:
        log_err(f"{exc} - nothing to release until it is fixed; this run stays green.")
        return []
    except Exception as exc:
        log_err(f"could not list semesters for {course_org}: {exc}")
        return None


def _one_semester(course_org: str, semester_org: str) -> tuple[list[str], int]:
    """The one-semester path's gate: `([semester], 0)` to run it, spelt as the registry spells
    it; `([], 0)` for a registered semester that has been closed out (frozen - running it
    would only spend a tick on 403s, the loop's answer too); `([], 1)` for a name the
    registry does not list, or a registry that could not be read.

    The name can arrive in a `repository_dispatch` payload, which whoever holds a semester's
    bot token writes, so the course's own registry decides - the check Sync site and Sync
    membership make before they touch a dispatched semester."""
    try:
        registered = discovery.discover_semesters(course_org)
    except Exception as exc:
        log_err(f"could not list semesters for {course_org}: {exc}")
        return [], 1
    match = [c for c in registered if c.casefold() == semester_org.casefold()]
    if not match:
        listed = ", ".join(sorted(registered)) or "nothing"
        log_err(
            f"{semester_org} is not registered under {course_org} ({listed}) - "
            f"refusing to run it."
        )
        return [], 1
    if not discovery.semester_is_live(match[0]):
        return [], 0
    return match[:1], 0


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument(
        "--course-org", required=True, help="Course org (source of every release)"
    )
    parser.add_argument(
        "--semester-org",
        default=None,
        help="One semester; omit and use --all-semesters",
    )
    parser.add_argument(
        "--all-semesters",
        action="store_true",
        help="Run every semester registered with the course org (the release job).",
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
        "--list-semesters",
        action="store_true",
        help="Print the course org's registered semesters as a JSON list, and exit.",
    )
    parser.add_argument(
        "--check-course-config",
        action="store_true",
        help="Pre-flight the COURSE org's own dsl-course.yml and semester registry, keep "
        "its digest issue in step, and exit 0. What Sync membership runs on a push to "
        "either file - the fast path under this cron's own hourly floor.",
    )
    parser.add_argument(
        "--defer-site-sync",
        action="store_true",
        help="Hand the site render to the Sync site workflow (a sync-site dispatch) "
        "instead of pushing it from here - for a run fired by a semester-config push, "
        "which may have started Sync site too.",
    )
    parser.add_argument(
        "--now", default=None, help="Override 'now' (ISO date/datetime) - for testing."
    )
    add_preview_flag(
        parser, "Print what would fire; release, grade and write nothing (default)."
    )
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
        return _preflight_course(args.course_org, now, args.preview)

    if args.list_semesters:
        # The grading job's matrix, and the ONLY thing that may reach stdout: the workflow
        # captures it whole into a step output and hands it to fromJSON. `ghcli.gh` prints
        # its retry notices ("[wait] rate-limited...") to stdout before a successful
        # retry, and a second line there writes a step-output line with no `=` - GitHub
        # rejects the file, the step fails, and because the listing runs first the release
        # step is skipped. So one transient 403 would cost a whole release tick. Every log
        # line the read makes goes to stderr for the duration; only the answer is printed.
        with contextlib.redirect_stdout(sys.stderr):
            if args.semester_org:
                semesters, rc = _one_semester(args.course_org, args.semester_org)
                semesters = None if rc else semesters
            else:
                semesters = _registered_semesters(args.course_org)
        if semesters is None:
            return 1
        print(json.dumps(semesters))
        return 0

    phases = {
        "release": not args.autograde_only,
        "autograde": not args.skip_autograde,
    }

    if args.all_semesters:
        # The COURSE org's own config FIRST, and before the listing below rather than
        # beside the semesters: a registry nobody can parse is one of the faults this
        # reports, and it is also what makes that listing raise - so reported here, or
        # never. Once per tick, on a real release pass only; the grading matrix's
        # per-semester legs and a laptop's single-semester run are not the course's tick.
        if phases["release"]:
            _preflight_course(args.course_org, now, args.preview)
        semesters = _registered_semesters(args.course_org)
        if semesters is None:
            # A listing that could not be READ is not "no semesters": go red so the failure
            # issue files, rather than reporting a quiet no-op tick.
            return 1
        if not semesters:
            # A freshly bootstrapped course org has this cron installed before any
            # semester is registered - that gap is normal, not an hourly failure.
            log(
                f"  [skip] no semesters registered with {args.course_org}; "
                "nothing to release."
            )
            return 0
        rc = 0
        # ONE cadence reading for the whole course, taken before anything ships - the
        # question is how long since the previous tick, and firing the releases first would
        # answer it about this run's own writes. Only on a real, whole-course release pass:
        # the manual button defaults to a dry run, and a single `--semester-org` invocation
        # is a laptop, so neither may arm an alarm, comment on one, or close one.
        verdict = None
        if phases["release"] and not args.preview:
            try:
                verdict = cadence.evaluate(
                    now, cadence.fetch_runs(args.course_org), cadence.own_run_id()
                )
            except Exception as exc:
                # Worth the red X - this is the check that watches the drivers - and worth
                # nothing more: `verdict` stays None and every release below still runs.
                log_err(f"could not read {args.course_org}'s run history: {exc}")
                rc |= 1
        for semester in semesters:
            # One semester's raised failure (a read helper that couldn't reach the API, a
            # site sync that blew up) must not abort the remaining semesters' scheduled
            # releases - log it, mark the batch failed, and carry on. The same per-semester
            # isolation PR #151/#146 applied to the nightly refresh.
            try:
                rc |= run(
                    args.course_org,
                    semester,
                    now,
                    dry_run=args.preview,
                    verdict=verdict,
                    **phases,
                )
            except Exception as exc:
                log_err(f"scheduler run for {semester} failed: {exc}")
                rc |= 1  # accumulate, don't clobber prior semesters' status bits
        # Last, so a driver-health alarm can never delay a release: the drivers being down
        # is not this run's problem to fix, only to report.
        if verdict is not None:
            rc |= cadence.report_course(args.course_org, verdict, args.preview)
        # The budget alarm rides on the same real, whole-course pass: a quarter-hourly
        # reading per course org, and the listing it needs is the one just made.
        if phases["release"] and not args.preview:
            rc |= cadence.report_budget(args.course_org, start_budget())
        return rc

    if not args.semester_org:
        log_err("pass --semester-org or --all-semesters.")
        return 1
    # One semester: a semester-config push's run, or a laptop. The registry authorises it,
    # and takes the same answer as the loop above - a semester that has been closed out is
    # frozen, and running it would only spend a tick on 403s.
    semesters, rc = _one_semester(args.course_org, args.semester_org)
    if not semesters:
        if args.preview and rc == 0:
            # Registered but archived: the preview still says what would have been due.
            decisions = archived_decisions(schedule.load(args.semester_org), now)
            for decision in decisions:
                log(decision.line())
            return Summary(
                "This semester is archived, so automation releases nothing.",
                {"held": len(decisions)},
                [d.reason() for d in decisions],
            )
        return rc
    rc = run(
        args.course_org,
        semesters[0],
        now,
        dry_run=args.preview,
        defer_site_sync=args.defer_site_sync,
        **phases,
    )
    # The scoped run is what a semester-config push fires, and its release pass is where
    # every digest was just brought in line with the files - so the semester's status.json
    # follows here, after it. Never on a dry run (a preview writes nothing), and never
    # counted: the release's exit code is the release's.
    if phases["release"] and not args.preview:
        status.refresh(args.course_org, semesters[0])
    return rc


if __name__ == "__main__":
    sys.exit(main())
