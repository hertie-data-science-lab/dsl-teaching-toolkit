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
  cron is left. This is the dead-man's switch, and the dispatcher is the ONLY thing it is
  about - GitHub's own cron is listed for information and never alarms, because dropping
  most of its fires is its measured normal state.
- LATE DELIVERY, in each cohort's private `classroom-config`, for that cohort's instructors:
  these named moments passed more than GAP_SLO before the tick that shipped them. This is
  the SLO, and it is per cohort because the plan that was late is theirs.

DISARMED until an external dispatch run appears in the window. Nothing opens and nothing
closes before then - the whole point of the alarm is that a dispatch every 15 minutes is
expected, and until the dispatcher exists a course org would be told, four times an hour,
that a driver it never had is missing. Being disarmed is a statement about the WINDOW, not
about history, so an org whose dispatcher died long enough ago for its last run to scroll
out of the window would otherwise fall quiet with its alarm still standing: an OPEN
driver-health issue is the second half of the evidence, and `report_course` keeps watching
whenever it finds one.

Both issues CLOSE on hysteresis, never on a single good sign: the driver has to be back on
cadence (a dispatch inside HEALTHY_GAP), and a cohort's delivery has to have held for
HEALTHY_GAPS consecutive ticks - counting the gap this very tick arrived on. Closing on a
first good tick and reopening on the next late moment is the same cry-wolf failure from the
other direction.

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
from .schedule import (
    CONFIG_REPO,
    HANDOUT_SUFFIX,
    Release,
    Schedule,
    grading_datetime_at,
)

# How late one dated moment may ship before its cohort is told. A tick arrives every 15
# minutes and a release's `when` is usually a class time, so the first hour is ordinary
# jitter; past it, faculty stood in front of a class whose materials were not up.
GAP_SLO = timedelta(minutes=60)
# No external dispatch for this long = the dispatcher is dead. It fires every 15 minutes, so
# this is eight consecutive misses - long enough that a reboot or a VPN blip stays quiet.
DS01_DEAD = timedelta(hours=2)
# How many consecutive healthy gaps close a late-delivery issue - the gap THIS tick arrived
# on plus the ones before it. Eight quarter-hourly ticks is two hours of proven cadence, so
# a recovery has to HOLD rather than merely happen once; otherwise the issue closes on the
# first good tick after an outage and reopens on the next.
HEALTHY_GAPS = 8
# What "healthy" means for one gap: a quarter-hourly driver plus a few minutes of runner
# queue. Anything at or under this is the system working as designed. It is also the
# driver-health close rule - "the dispatcher is back on cadence", not "was seen once".
HEALTHY_GAP = timedelta(minutes=20)

# The scheduler's own workflow file, exactly as `seed.seed_github_workflows` writes it into
# every course org's `.github`. Renaming it there and not here leaves a course whose cadence
# check reads a 404 - loudly (the read raises), but only in the logs.
WORKFLOW_FILE = "scheduled-release.yml"
# ~5 hours of quarter-hourly ticks: enough history for HEALTHY_GAPS and for the dead-man
# threshold, in ONE request. EVERYTHING this module says is bounded by this window - which
# is why there is no "no cron fire in 24h" rule (a 24h-old run cannot be in a 5h window, so
# such a rule could only ever read as false) and why the bodies say "in the last 20 runs"
# rather than implying knowledge of anything older.
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

# The two ways the dispatcher can be missing, as the driver-health body records them. They
# are a real transition - "we have not seen it AT ALL in the window" is worse news than "we
# saw it, too long ago" - and that transition is the one thing that earns a second email on
# a standing alarm.
DISPATCH_STALE = "stale"
DISPATCH_UNSEEN = "none-in-window"


# --------------------------------------------------------------------------- pure core


@dataclass(frozen=True)
class Verdict:
    """What the run history says about the two drivers, as of `now`.

    Every field is bounded by the RUNS_PAGE window: `None` means "not in the last
    RUNS_PAGE runs", which is a weaker statement than "never", and the bodies word it that
    way."""

    # Whether an external dispatch run is in the WINDOW - not whether one ever existed.
    # False both before the dispatcher ships and once a long-dead one has scrolled out of
    # the last RUNS_PAGE runs, which is why `report_course` also treats an open issue as
    # evidence that this org was armed (see the module docstring).
    armed: bool
    last_dispatch_at: datetime | None
    # GitHub's own cron, INFORMATIONAL only: it is printed in the body and never compared
    # to a threshold. It drops most of its fires by design, and a window this short cannot
    # tell "quiet for a day" from "crowded out by the dispatcher's four fires an hour".
    last_schedule_at: datetime | None
    # The dispatcher was seen, and longer ago than DS01_DEAD. The dead-man's switch, and
    # the ONLY thing that opens (or holds open) the driver-health issue.
    ds01_dead: bool
    # The last run that actually executed - what "the previous tick" means for lateness.
    prev_executed_at: datetime | None
    # Gaps between consecutive executed runs, newest first. This tick's own gap is NOT in
    # here (it has not executed yet) - `healthy` puts it back.
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
        """Whether the last HEALTHY_GAPS gaps - THIS tick's included - are all within
        HEALTHY_GAP. The close rule for a late-delivery issue.

        This tick's own gap has to count, or a tick arriving three hours after the last one
        closes the issue on the strength of the eight punctual gaps before the outage - and
        says, in the closing comment, that eight consecutive ticks arrived on time. No
        previous tick at all means no evidence, which is not the same as good news."""
        gap = self.gap
        if gap is None:
            return False
        gaps = [gap, *self.recent_gaps]
        return len(gaps) >= HEALTHY_GAPS and all(
            g <= HEALTHY_GAP for g in gaps[:HEALTHY_GAPS]
        )

    @property
    def dispatch_state(self) -> str:
        """How the dispatcher is missing - DISPATCH_UNSEEN or DISPATCH_STALE. Only
        meaningful while the alarm stands."""
        return DISPATCH_STALE if self.last_dispatch_at else DISPATCH_UNSEEN

    @property
    def dispatch_on_cadence(self) -> bool:
        """Whether the dispatcher is back on cadence, which is the driver-health CLOSE rule.
        Deliberately stricter than "not dead": a single dispatch after a three-hour outage
        closes an issue that the next missed fire reopens."""
        return (
            self.last_dispatch_at is not None
            and self.now - self.last_dispatch_at <= HEALTHY_GAP
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
        prev_executed_at=executed[-1] if executed else None,
        recent_gaps=list(reversed(gaps)),
        now=now,
    )


def _entry_label(release: Release, sched: Schedule) -> str:
    """The YAML path faculty would edit to move this entry's moment. A synthesised handout
    is reported against its assignment block, which is the file it actually came from."""
    slug = (
        release.label[: -len(HANDOUT_SUFFIX)]
        if release.label.endswith(HANDOUT_SUFFIX)
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
    could not have shipped) is not reported as late.

    `prev_at` is the previous run's `created_at`: when GitHub accepted the fire, not when
    the run reached the release. A moment that a long-queued run shipped some minutes after
    that timestamp therefore falls inside this tick's window too and can be reported once
    more - the body's state marker is what stops it emailing anyone twice."""
    if prev_at is None:
        return []
    # The handouts the scheduler actually synthesised. An assignment with no template repo
    # in the course org gets none, and its SOLUTION rides on that same release
    # (`_handout_releases`), so without one neither could have shipped.
    synthesised = {r.label for r in releases if r.label.endswith(HANDOUT_SUFFIX)}
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
        # snapshot was supposed to pin. It needs no template repo, so it is always asked.
        at = grading_datetime_at(sched, slug)
        if at is not None:
            moments.append((f"assignments.{slug} snapshot", at))
        if (
            entry.solution_datetime is not None
            and f"{slug}{HANDOUT_SUFFIX}" in synthesised
        ):
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


def _driver_body(course_org: str, verdict: Verdict) -> str:
    """The driver-health issue: when each driver last fired, and against what threshold.

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
                f"{_minutes(DS01_DEAD)} min. GitHub's own cron line is INFORMATION only - "
                "it drops most of its fires by design, so it is never what this issue is "
                "about."
            ),
            "",
            (
                f"Read off this workflow's last {RUNS_PAGE} runs, and rewritten on every "
                f"tick. It closes itself once a dispatch has arrived within "
                f"{_minutes(HEALTHY_GAP)} min - back on cadence, not merely seen once."
            ),
            "",
            f"cc @{course_org}/course-admin",
            "",
            _marker({"dispatch": verdict.dispatch_state}),
        ]
    )


def _driver_comment(course_org: str, verdict: Verdict) -> str:
    """The transition line - short, because it is an email subject more than a document."""
    if verdict.dispatch_on_cadence:
        line = (
            f"- the external dispatcher is firing again (last fire "
            f"{_minutes(verdict.now - verdict.last_dispatch_at)} min ago)"
        )
    elif verdict.last_dispatch_at is None:
        line = f"- no external dispatch at all in the last {RUNS_PAGE} runs of this workflow"
    else:
        line = (
            f"- no external dispatch for "
            f"{_minutes(verdict.now - verdict.last_dispatch_at)} min "
            f"(alarm at {_minutes(DS01_DEAD)} min)"
        )
    return f"{line}\n\ncc @{course_org}/course-admin"


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
    the drivers - but it never raises, so it can never abort a release.

    The dispatcher is the only subject. It opens when the dispatcher is late, holds while it
    stays late, and closes only once it is back on cadence."""
    repo = f"{course_org}/.github"
    try:
        # Asked FIRST, and unconditionally, because it is half the evidence about whether
        # this org was ever armed: `verdict.armed` only says whether a dispatch is in the
        # window, so an org whose dispatcher died days ago reads as disarmed - and would
        # stop being watched with its own alarm still standing. An open issue is proof this
        # module armed here once.
        existing = find_issue(repo, DRIVER_TITLE)
        if not verdict.armed and existing is None:
            # A `::warning::` on a green run: true, worth seeing, and not a fault. This is
            # the normal state of every org until the external dispatcher ships.
            log_withheld(
                f"the cadence alarms for {course_org}: no {DISPATCH_EVENT} run in the last "
                f"{RUNS_PAGE} runs of {WORKFLOW_FILE} and no open `{DRIVER_TITLE}` issue, "
                "so there is no external driver to hold to a schedule - nothing opened and "
                "nothing closed"
            )
            return 0
        if verdict.ds01_dead or not verdict.armed:
            state = {"dispatch": verdict.dispatch_state}
            # The body is state, so a comment (the half that emails) is posted only when
            # the alarm is not the one the body already records - which here means the
            # dispatcher went from "seen, too long ago" to "not in the window at all".
            changed = (read_state(existing[1]) if existing else {}) != state
            if dry_run:
                log(
                    f"    DRY-RUN  would {'update' if existing else 'open'} "
                    f"`{DRIVER_TITLE}` in {repo}"
                )
                return 0
            log_step(f"{course_org}: external dispatch {verdict.dispatch_state}")
            return upsert_issue(
                repo,
                DRIVER_TITLE,
                _driver_body(course_org, verdict),
                comment=_driver_comment(course_org, verdict) if changed else None,
            )
        if not verdict.dispatch_on_cadence:
            # Seen inside DS01_DEAD but not yet back on cadence. Nothing to open (the
            # dispatcher is alive) and nothing to close (one fire after an outage is not a
            # recovery) - so leave whatever stands, standing.
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

    Opening needs late items; CLOSING needs a proven cadence (`Verdict.healthy`, which
    counts the gap this tick arrived on), not merely the absence of them. Without that
    asymmetry the issue would close on the first quiet tick of an outage and reopen on the
    next late moment, twice an hour."""
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
