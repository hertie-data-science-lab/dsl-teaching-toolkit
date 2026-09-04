"""dsl-course cadence -- is the scheduler actually being driven, and did anything ship late?

Every dated action in the toolkit - materials deploys, assignment handouts, submission
freezes, model solutions - rides on `Scheduled release` ticks. GitHub delivers its `schedule`
best-effort and, measured across two orgs, delivers 2-7% of the fires it is asked for; the
worst observed gap was 13h20m and five consecutive failures went unnoticed for two days. So
a second, external driver fires the same workflow by `repository_dispatch`, and this module
is what notices when a driver stops - or when something shipped late anyway.

STATELESS, because there is nowhere to keep state that a lost tick cannot also lose. Both
answers are computed from the workflow's OWN run history (`created_at` is the moment GitHub
accepted the fire, on the server's clock), and both are published as an issue whose body IS
the record - the same body-as-state, comments-as-events shape as `source_digest`, and for the
same reason: an alarm that emails on every tick is an alarm nobody reads.

Two alarms, in two places, because they have two audiences:

- DRIVER HEALTH, in the course org's public `.github`, for whoever runs the infrastructure:
  no external dispatch in DS01_DEAD means the dispatcher is down and only GitHub's unreliable
  cron is left. This is the dead-man's switch.
- LATE DELIVERY, in each cohort's private `classroom-config`, for that cohort's instructors:
  these named moments passed more than GAP_SLO before the tick that shipped them. This is
  the SLO, and it is per cohort because the plan that was late is theirs.

DISARMED until an external dispatch run has ever been seen. Nothing opens and nothing closes
before then - the whole point of the alarm is that a dispatch every 15 minutes is expected,
and until the dispatcher exists a course org would be told, four times an hour, that a driver
it never had is missing.

Every line either module publishes carries workflow names, timestamps, minutes, schedule
labels and counts - never a handle, a student repo, or a `describe()` line (which names
course-org template repos). The driver-health issue lives in a WORLD-READABLE repo.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from .ghcli import gh_json
from .issues import close_issues_titled, find_issue, upsert_issue
from .log import log, log_err, log_step, log_withheld
from .schedule import CONFIG_REPO, Release, Schedule, grading_datetime_at

# How late one dated moment may ship before its cohort is told. A tick arrives every 15
# minutes and a release's `when` is usually a class time, so the first hour is ordinary
# jitter; past it, faculty stood in front of a class whose materials were not up.
GAP_SLO = timedelta(minutes=60)
# No external dispatch for this long = the dispatcher is dead. It fires every 15 minutes, so
# this is eight consecutive misses - long enough that a reboot or a VPN blip stays quiet.
DS01_DEAD = timedelta(hours=2)
# GitHub's own cron is best-effort by design (6 of 24 delivered on `0 * * * *`, gaps to 13h),
# so only a whole day of silence says anything at all - and it is informational: this
# threshold never opens an issue on its own.
GH_CRON_DEAD = timedelta(hours=24)
# How many consecutive healthy gaps close a late-delivery issue. Eight quarter-hourly ticks
# is two hours of proven cadence, so a recovery has to HOLD rather than merely happen once -
# otherwise the issue closes on the first good tick after an outage and reopens on the next.
HEALTHY_GAPS = 8
# What "healthy" means for one gap: a quarter-hourly driver plus a few minutes of runner
# queue. Anything under this is the system working as designed.
HEALTHY_GAP = timedelta(minutes=20)

# The scheduler's own workflow file, exactly as `seed.seed_github_workflows` writes it into
# every course org's `.github`. Renaming it there and not here leaves a course whose cadence
# check reads a 404 - loudly (the read raises), but only in the logs.
WORKFLOW_FILE = "scheduled-release.yml"
# ~5 hours of quarter-hourly ticks: enough history for HEALTHY_GAPS and for both dead-man
# thresholds, in ONE request. Everything this module says is bounded by this window, and the
# bodies say so rather than implying knowledge of anything older.
RUNS_PAGE = 20

# The two drivers, as GitHub names them in a run's `event`.
DISPATCH_EVENT = "repository_dispatch"
CRON_EVENT = "schedule"
_DRIVERS = frozenset({CRON_EVENT, DISPATCH_EVENT})
# A run that actually did the work. `cancelled` and `null` (still queued) prove a driver
# FIRED but not that anything shipped, so they date the drivers and never the cadence.
_EXECUTED = frozenset({"success", "failure"})

# Stable titles, because each issue is found by searching for this exact string - a title
# that varied with the fault would never match and every tick would open a new issue.
DRIVER_TITLE = "Scheduled release: driver health"
LATE_TITLE = "Scheduled release: late delivery"

_STATE_RE = re.compile(r"<!-- dsl-cadence-state: (\{.*?\}) -->", re.DOTALL)

# How `scheduler._handout_releases` labels the releases it synthesises from
# `assignments.<slug>.handout_datetime`. They arrive here mixed into the `releases:` plan, and
# a late handout must be reported against the field faculty would edit to fix it - the
# assignment's block, not a `releases.<slug>-handout` entry that exists in no file.
_HANDOUT_SUFFIX = "-handout"


# --------------------------------------------------------------------------- pure core


@dataclass(frozen=True)
class Verdict:
    """What the run history says about the two drivers, as of `now`.

    Every field is bounded by the RUNS_PAGE window: `None` means "not in the last
    RUNS_PAGE runs", which is a weaker statement than "never", and the bodies word it that
    way."""

    # False until an external dispatch run has EVER been seen. Nothing is opened or closed
    # while this is False (see the module docstring).
    armed: bool
    last_dispatch_at: datetime | None
    last_schedule_at: datetime | None
    # No external dispatch for DS01_DEAD. The dead-man's switch, and the only thing that
    # opens the driver-health issue.
    ds01_dead: bool
    # No GitHub cron fire for GH_CRON_DEAD. Informational: reported in the body, never a
    # reason to open on its own (GitHub's cron missing fires is its normal state).
    gh_cron_stale: bool
    # The last run that actually executed - what "the previous tick" means for lateness.
    prev_executed_at: datetime | None
    # Gaps between consecutive executed runs, newest first. Hysteresis for the close rule.
    recent_gaps: list[timedelta]
    now: datetime

    @property
    def gap(self) -> timedelta | None:
        """How long since the previous executed tick. None when the window holds none."""
        if self.prev_executed_at is None:
            return None
        return self.now - self.prev_executed_at

    @property
    def healthy(self) -> bool:
        """Whether the last HEALTHY_GAPS gaps are ALL within HEALTHY_GAP - the close rule
        for a late-delivery issue. Requiring a full run of them is what stops the issue
        closing on the first good tick after an outage and reopening on the next."""
        return len(self.recent_gaps) >= HEALTHY_GAPS and all(
            g <= HEALTHY_GAP for g in self.recent_gaps[:HEALTHY_GAPS]
        )


@dataclass(frozen=True)
class LateItem:
    """One dated moment that had already passed by more than GAP_SLO when the tick carrying
    it arrived. `label` names the FIELD faculty would edit, never a repo or a person."""

    label: str
    due: datetime
    late: timedelta


def _moment(raw: object) -> datetime | None:
    """A GitHub API timestamp (`2026-09-04T09:12:00Z`) as a tz-aware datetime, or None.

    Unparseable is None rather than an exception: one malformed row in a run listing must
    not take the whole check out, and the fields it would have dated are all optional."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_state(body: str) -> dict:
    """The state a cadence issue's body carries, or `{}` for a body this module did not
    write. The marker is an HTML comment, so it is invisible in the rendered issue - the
    issue IS the record, and there is no state file to keep in step with it."""
    m = _STATE_RE.search(body or "")
    if not m:
        return {}
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def evaluate(now: datetime, runs: list[dict], own_run_id: str | None) -> Verdict:
    """Read the drivers' health off the scheduler workflow's own run listing. Pure.

    `own_run_id` is THIS run, excluded: it is in the listing (the API returns it the moment
    it starts) and counting it would prove every driver alive from inside the run that was
    checking - a dead-man's switch that can never fire.

    The drivers are dated by ANY conclusion, because a cancelled or still-queued run proves
    the fire arrived; the cadence is measured only over runs that executed, because a
    cancelled one shipped nothing."""
    fired: dict[str, list[datetime]] = {}
    executed: list[datetime] = []
    for run in runs:
        if str(run.get("id")) == str(own_run_id):
            continue
        at = _moment(run.get("created_at"))
        if at is None:
            continue
        event = str(run.get("event") or "")
        fired.setdefault(event, []).append(at)
        if event in _DRIVERS and run.get("conclusion") in _EXECUTED:
            executed.append(at)

    last_dispatch = max(fired.get(DISPATCH_EVENT) or [], default=None)
    last_schedule = max(fired.get(CRON_EVENT) or [], default=None)
    executed.sort()
    gaps = [later - earlier for earlier, later in pairwise(executed)]
    return Verdict(
        armed=last_dispatch is not None,
        last_dispatch_at=last_dispatch,
        last_schedule_at=last_schedule,
        ds01_dead=last_dispatch is not None and now - last_dispatch > DS01_DEAD,
        # A window this short cannot see a schedule run 24h old, so "none in the window" is
        # ignorance, not staleness - and reading it as staleness would hold the
        # driver-health issue open for ever once the dispatcher (4 fires an hour) crowds
        # GitHub's occasional one out of the last RUNS_PAGE runs.
        gh_cron_stale=last_schedule is not None and now - last_schedule > GH_CRON_DEAD,
        prev_executed_at=executed[-1] if executed else None,
        recent_gaps=list(reversed(gaps)),
        now=now,
    )


def _entry_label(release: Release, sched: Schedule) -> str:
    """The YAML path faculty would edit to move this entry's moment."""
    slug = (
        release.label[: -len(_HANDOUT_SUFFIX)]
        if release.label.endswith(_HANDOUT_SUFFIX)
        else ""
    )
    if slug and slug in sched.assignments:
        return f"assignments.{slug} handout"
    return f"releases.{release.label}"


def late_items(
    releases: list[Release],
    sched: Schedule,
    prev_at: datetime | None,
    now: datetime,
) -> list[LateItem]:
    """Every dated moment that fell in `(prev_at, now]` and was already more than GAP_SLO
    old when this tick reached it, due-date order. Pure.

    The window is what stops the false alarm that would otherwise fire all summer: a plan
    whose whole term is in the past is late by months, but nothing in it fell in THIS gap,
    so nothing here shipped late. No previous executed tick means no gap to measure, and
    therefore nothing to report.

    `releases` is the merged plan the scheduler is about to fire - `releases:` entries plus
    the handouts synthesised from `assignments.<slug>.handout_datetime` - so an assignment
    whose template repo does not exist (and which therefore synthesised no release, and
    could not have shipped) is not reported as late."""
    if prev_at is None:
        return []
    moments: list[tuple[str, datetime]] = []
    for release in releases:
        label = _entry_label(release, sched)
        for i, dep in enumerate(release.deploy):
            # A deploy ships on its own clock when it carries one, else at the entry's.
            at = dep.deploy_datetime or release.when
            if at is not None:
                moments.append((f"{label} -> deploy[{i}]", at))
        if release.assignment and release.when is not None:
            moments.append((label, release.when))
    for slug, entry in sched.assignments.items():
        # The freeze moment: late here means submissions kept arriving past the deadline the
        # snapshot was supposed to pin.
        at = grading_datetime_at(sched, slug)
        if at is not None:
            moments.append((f"assignments.{slug} snapshot", at))
        if entry.solution_datetime is not None:
            moments.append((f"assignments.{slug} solution", entry.solution_datetime))
    return sorted(
        (
            LateItem(label, at, now - at)
            for label, at in moments
            if prev_at < at <= now and now - at > GAP_SLO
        ),
        key=lambda item: (item.due, item.label),
    )


# ------------------------------------------------------------------------ issue bodies


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _fired(at: datetime | None, now: datetime) -> str:
    if at is None:
        return f"not once in the last {RUNS_PAGE} runs"
    return f"{at.isoformat()} ({_minutes(now - at)} min ago)"


def _marker(state: dict) -> str:
    return f"<!-- dsl-cadence-state: {json.dumps(state, sort_keys=True)} -->"


def _driver_body(course_org: str, verdict: Verdict, state: dict) -> str:
    """The driver-health issue: which driver last fired, when, and against what threshold.

    Timestamps, minutes and workflow names only - this repo is world-readable."""
    return "\n".join(
        [
            (
                f"`Scheduled release` in `{course_org}` is not being driven. Every dated "
                "action in every cohort rides on these ticks, so while this stands nothing "
                "is released, handed out, frozen or graded anywhere."
            ),
            "",
            f"- last external dispatch: {_fired(verdict.last_dispatch_at, verdict.now)}",
            f"- last GitHub cron fire: {_fired(verdict.last_schedule_at, verdict.now)}",
            "",
            (
                f"An external dispatch is expected every 15 min and alarms after "
                f"{_minutes(DS01_DEAD)} min. GitHub's own cron is best-effort - it drops "
                f"most of its fires by design - so it is reported after "
                f"{int(GH_CRON_DEAD.total_seconds() // 3600)}h and never alarms on its own."
            ),
            "",
            (
                f"Read off this workflow's last {RUNS_PAGE} runs, and rewritten on every "
                "tick. It closes itself once both drivers are inside their thresholds."
            ),
            "",
            f"cc @{course_org}/course-admin",
            "",
            _marker(state),
        ]
    )


def _driver_comment(course_org: str, verdict: Verdict) -> str:
    """The transition line - short, because it is an email subject more than a document."""
    lines = []
    if verdict.ds01_dead and verdict.last_dispatch_at is not None:
        lines.append(
            f"- no external dispatch for "
            f"{_minutes(verdict.now - verdict.last_dispatch_at)} min "
            f"(alarm at {_minutes(DS01_DEAD)} min)"
        )
    if verdict.gh_cron_stale and verdict.last_schedule_at is not None:
        lines.append(
            f"- no GitHub cron fire for "
            f"{_minutes(verdict.now - verdict.last_schedule_at)} min (informational)"
        )
    if not lines:
        lines.append("- both drivers are firing inside their thresholds again")
    return "\n".join(lines) + f"\n\ncc @{course_org}/course-admin"


def _item_line(item: LateItem, now: datetime) -> str:
    return (
        f"- `{item.label}`: due {item.due.isoformat()}, shipped {now.isoformat()} "
        f"(+{_minutes(item.late)} min)"
    )


def _late_body(
    course_org: str, cohort_org: str, verdict: Verdict, items: list[LateItem]
) -> str:
    """The late-delivery issue: the moments this gap swallowed, and how late each was.

    Schedule LABELS and datetimes only - never `scheduler.describe()` output, which names
    course-org template repos, and never a handle or a submission repo."""
    gap = (
        f"{_minutes(verdict.gap)} min" if verdict.gap is not None else "an unknown gap"
    )
    return "\n".join(
        [
            (
                f"`Scheduled release` in `{course_org}` last ran {gap} ago, and these "
                f"moments in `{CONFIG_REPO}/schedule.yml` came and went inside that gap. "
                f"Each shipped more than {_minutes(GAP_SLO)} min after the datetime it "
                "was written for."
            ),
            "",
            *[_item_line(item, verdict.now) for item in items],
            "",
            (
                "Nothing above was dropped for being late - this tick is shipping it. "
                "The dates are correct as written; the ticks that carry them were late. "
                "This issue "
                f"rewrites itself every tick and closes once {HEALTHY_GAPS} consecutive "
                f"ticks arrive within {_minutes(HEALTHY_GAP)} min of each other."
            ),
            "",
            f"cc @{cohort_org}/instructors",
            "",
            _marker({"items": sorted(item.label for item in items)}),
        ]
    )


def _late_comment(cohort_org: str, new: list[LateItem], now: datetime) -> str:
    return (
        "**Shipped late**:\n"
        + "\n".join(_item_line(item, now) for item in new)
        + f"\n\ncc @{cohort_org}/instructors"
    )


# ---------------------------------------------------------------------- gh/git wiring


def own_run_id() -> str | None:
    """This Actions run's own id, so the check cannot date a driver off itself. None off a
    runner (a local invocation, which never reports - see `scheduler.main`)."""
    return os.environ.get("GITHUB_RUN_ID")


def fetch_runs(course_org: str) -> list[dict]:
    """The scheduler workflow's last RUNS_PAGE runs in `course_org` - one request, and the
    only input either alarm has. Raises when the read fails: a listing that could not be
    read is not "no runs", and reading it that way would report every driver dead."""
    payload = gh_json(
        "api",
        f"repos/{course_org}/.github/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?per_page={RUNS_PAGE}",
    )
    return list(payload.get("workflow_runs") or [])


def report_course(course_org: str, verdict: Verdict, dry_run: bool = False) -> int:
    """Keep the course org's driver-health issue in line with `verdict`. Returns the error
    count - a cadence failure IS worth a red run, because this is the check that watches
    the drivers - but it never raises, so it can never abort a release."""
    if not verdict.armed:
        # A `::warning::` on a green run: true, worth seeing, and not a fault. This is the
        # normal state of every org until the external dispatcher ships.
        log_withheld(
            f"the cadence alarms for {course_org}: no {DISPATCH_EVENT} run in the last "
            f"{RUNS_PAGE} runs of {WORKFLOW_FILE}, so there is no external driver to hold "
            "to a schedule yet - nothing opened and nothing closed"
        )
        return 0
    repo = f"{course_org}/.github"
    try:
        if verdict.ds01_dead:
            state = {
                "ds01_dead": verdict.ds01_dead,
                "gh_cron_stale": verdict.gh_cron_stale,
            }
            existing = find_issue(repo, DRIVER_TITLE)
            # The body is state, so a comment (the half that emails) is posted only when
            # the set of live alarms is not the one the body already records.
            changed = (read_state(existing[1]) if existing else {}) != state
            if dry_run:
                log(
                    f"    DRY-RUN  would {'update' if existing else 'open'} "
                    f"`{DRIVER_TITLE}` in {repo}"
                )
                return 0
            log_step(
                f"no external dispatch for "
                f"{_minutes(verdict.now - verdict.last_dispatch_at)} min in {course_org}"
            )
            return upsert_issue(
                repo,
                DRIVER_TITLE,
                _driver_body(course_org, verdict, state),
                comment=_driver_comment(course_org, verdict) if changed else None,
            )
        if verdict.gh_cron_stale:
            # Informational, and never a reason to open: GitHub dropping most of its own
            # fires is its documented, measured normal state, and the external dispatcher
            # is the driver that matters. It appears in the body when the dispatcher is
            # down too, and it holds an open issue open until the cron is seen again.
            return 0
        if dry_run:
            log(f"    DRY-RUN  would close `{DRIVER_TITLE}` in {repo} if it is open")
            return 0
        return close_issues_titled(
            repo, DRIVER_TITLE, _driver_comment(course_org, verdict)
        )
    except Exception as exc:
        log_err(f"could not report {course_org}'s driver health: {exc}")
        return 1


def report_cohort(
    course_org: str,
    cohort_org: str,
    verdict: Verdict,
    items: list[LateItem],
    dry_run: bool = False,
) -> int:
    """Keep this cohort's late-delivery issue in line with `items`. Returns the error count,
    and never raises - the same contract as `report_course`.

    Opening needs late items; CLOSING needs a proven cadence, not merely the absence of
    them. Without that asymmetry the issue would close on the first quiet tick of an outage
    and reopen on the next late moment, twice an hour."""
    if not verdict.armed:
        return 0
    repo = f"{cohort_org}/{CONFIG_REPO}"
    try:
        if not items:
            if not verdict.healthy:
                # Nothing late THIS tick, but the cadence has not held long enough to say
                # the problem is over. Leave whatever is open, open.
                return 0
            if dry_run:
                log(f"    DRY-RUN  would close `{LATE_TITLE}` in {repo} if it is open")
                return 0
            return close_issues_titled(
                repo,
                LATE_TITLE,
                f"{HEALTHY_GAPS} consecutive ticks have arrived within "
                f"{_minutes(HEALTHY_GAP)} min of each other - delivery is back on cadence.",
            )
        existing = find_issue(repo, LATE_TITLE)
        known = set(read_state(existing[1]).get("items") or []) if existing else set()
        new = [item for item in items if item.label not in known]
        if dry_run:
            log(
                f"    DRY-RUN  would {'update' if existing else 'open'} `{LATE_TITLE}` in "
                f"{repo} ({len(items)} late item(s), {len(new)} new)"
            )
            return 0
        log_step(
            f"{len(items)} moment(s) in {cohort_org}'s plan shipped more than "
            f"{_minutes(GAP_SLO)} min late ({len(new)} new)"
        )
        return upsert_issue(
            repo,
            LATE_TITLE,
            _late_body(course_org, cohort_org, verdict, items),
            comment=_late_comment(cohort_org, new, verdict.now) if new else None,
        )
    except Exception as exc:
        log_err(f"could not report {cohort_org}'s late deliveries: {exc}")
        return 1
