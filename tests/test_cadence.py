"""cadence: is the scheduler being driven, and did anything ship late?

The module is stateless - both answers come out of the workflow's own run listing - so
`evaluate` and `late_items` are pure and get fixed datetimes here. What the tests are really
holding are the anti-cry-wolf rules: disarmed until an external dispatch appears in the
window (and NOT disarmed by one scrolling out of it while the alarm stands), a lateness
window that ignores a term written in the past, hysteresis in both directions before
anything closes, and a comment only on a transition. Plus the PII rule: a public `.github`
issue may carry labels, timestamps and minutes, never a handle or a repo name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dsl_course import cadence
from dsl_course.schedule import AssignmentEntry, Deploy, Release, Schedule

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
OWN = "999"


def _run(
    minutes_ago: float,
    event: str = cadence.DISPATCH_EVENT,
    conclusion: str | None = "success",
    run_id: int | str = 1,
) -> dict:
    """One row of the runs API, timestamped the way GitHub does it (`...Z`)."""
    return {
        "id": run_id,
        "event": event,
        "conclusion": conclusion,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def _evaluate(runs: list[dict], now: datetime = NOW) -> cadence.Verdict:
    return cadence.evaluate(now, runs, OWN)


# ------------------------------------------------------------------------ armed / disarmed


def test_disarmed_while_no_external_dispatch_is_in_the_window():
    # Every org gets this code before the dispatcher exists. Armed on GitHub's cron alone,
    # each would be told four times an hour that a driver it never had is missing.
    # (`armed` is about the WINDOW, not about history - see report_course for the rest.)
    v = _evaluate([_run(5, cadence.CRON_EVENT), _run(20, cadence.CRON_EVENT)])
    assert v.armed is False
    assert v.dispatch_state == cadence.DISPATCH_UNSEEN


def test_one_dispatch_run_of_any_conclusion_arms_it():
    # A cancelled (or still-queued) run proves the FIRE arrived, which is all "armed" means.
    assert _evaluate([_run(5, conclusion="cancelled")]).armed is True
    assert _evaluate([_run(5, conclusion=None)]).armed is True


def test_this_runs_own_row_is_never_evidence_of_anything():
    # The API lists this run the moment it starts, so counting it would let a dead-man's
    # switch prove every driver alive from inside the run that was checking.
    v = _evaluate([_run(0, run_id=OWN), _run(0, run_id=int(OWN))])
    assert v.armed is False
    assert v.last_dispatch_at is None and v.prev_executed_at is None


# ------------------------------------------------------------------------- the threshold


def test_ds01_is_dead_past_two_hours_and_not_at_exactly_two():
    # Pinned at the boundary as well as either side of it, so a `>`/`>=` swap fails here
    # rather than shaving two minutes off the alarm in production.
    assert cadence.DS01_DEAD == timedelta(hours=2)
    assert _evaluate([_run(119)]).ds01_dead is False
    assert _evaluate([_run(120)]).ds01_dead is False
    assert _evaluate([_run(121)]).ds01_dead is True


def test_githubs_own_cron_is_recorded_and_never_measured():
    # There is no cron threshold at all: at four dispatches an hour the 20-run window spans
    # about five hours, so any "quiet for a day" rule could only ever read as false. The
    # timestamp is kept for the body; nothing compares it to anything.
    v = _evaluate([_run(5), _run(20, cadence.CRON_EVENT)])
    assert v.last_schedule_at == NOW - timedelta(minutes=20)
    assert not hasattr(v, "gh_cron_stale")
    # ...and its absence from the window is not a fault either
    assert _evaluate([_run(5)]).last_schedule_at is None


def test_the_dispatcher_is_back_on_cadence_at_exactly_twenty_minutes():
    # The CLOSE rule for driver health, and the other exact boundary.
    assert cadence.HEALTHY_GAP == timedelta(minutes=20)
    assert _evaluate([_run(20)]).dispatch_on_cadence is True
    assert _evaluate([_run(21)]).dispatch_on_cadence is False
    assert _evaluate([]).dispatch_on_cadence is False


# ------------------------------------------------------------------ the previous tick


def test_prev_executed_ignores_a_cancelled_run_and_a_manual_dispatch():
    # A cancelled run shipped nothing, and `workflow_dispatch` is a human pressing the
    # button (usually a dry run) - neither is a tick the cadence can be measured against.
    runs = [
        _run(5, conclusion="cancelled", run_id=1),
        _run(10, "workflow_dispatch", run_id=2),
        _run(90, conclusion="failure", run_id=3),
    ]
    v = _evaluate(runs)
    # the failure DID execute (it ran and reported), so it is the previous tick
    assert v.prev_executed_at == NOW - timedelta(minutes=90)
    assert v.gap == timedelta(minutes=90)


def test_no_executed_run_in_the_window_leaves_the_gap_unknown():
    v = _evaluate([_run(5, conclusion="cancelled")])
    assert v.prev_executed_at is None and v.gap is None


def test_recent_gaps_are_newest_first_and_gate_the_close():
    healthy = [_run(15 * i, run_id=i) for i in range(1, cadence.HEALTHY_GAPS + 2)]
    v = _evaluate(healthy)
    assert v.recent_gaps == [timedelta(minutes=15)] * cadence.HEALTHY_GAPS
    assert v.healthy is True

    # One gap over the line anywhere in the last HEALTHY_GAPS and it is not healthy yet.
    limped = [_run(0), _run(15), _run(200), *[_run(200 + 15 * i) for i in range(1, 8)]]
    late = _evaluate(limped)
    assert late.recent_gaps[0] == timedelta(minutes=15)
    assert late.healthy is False


def test_too_few_gaps_is_never_healthy():
    # A recovery has to HOLD: closing on the first good tick after an outage would reopen
    # the issue on the next late moment, twice an hour.
    v = _evaluate([_run(0), _run(15), _run(30)])
    assert v.healthy is False


def test_a_malformed_row_does_not_take_the_check_out():
    v = _evaluate([{"id": 5, "event": cadence.DISPATCH_EVENT}, _run(30)])
    assert v.armed is True and v.prev_executed_at == NOW - timedelta(minutes=30)


# ---------------------------------------------------------------------------- late items


def _plan() -> tuple[list[Release], Schedule]:
    """A cohort plan whose moments straddle the gap: one deploy at 09:00 Berlin (= 07:00
    UTC, five hours before NOW), a handout, a grading pin and a solution."""
    sched = Schedule(
        releases=[
            Release(
                label="lecture_02",
                when=datetime(2026, 9, 4, 11, 0, tzinfo=BERLIN),
                deploy=[
                    Deploy("cm-f2026", "lectures/02_intro", "materials", None),
                    Deploy(
                        "cm-f2026",
                        "readings/02_intro",
                        "materials",
                        None,
                        deploy_datetime=datetime(2026, 9, 4, 9, 0, tzinfo=BERLIN),
                    ),
                ],
            )
        ],
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 9, 4, 10, 0, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 9, 4, 9, 30, tzinfo=BERLIN),
                solution_datetime=datetime(2026, 9, 4, 10, 30, tzinfo=BERLIN),
            )
        },
    )
    # what `scheduler._release_phase` hands over: the plan plus the synthesised handout
    handout = Release(
        label="assignment-1-handout",
        when=sched.assignments["assignment-1"].handout_datetime,
        assignment="assignment-1-f2026",
    )
    return [*sched.releases, handout], sched


def test_late_items_only_reports_moments_that_fell_in_this_gap():
    releases, sched = _plan()
    # The gap opened at 08:00 Berlin (06:00 UTC), so everything in the plan is inside it.
    prev = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
    labels = [i.label for i in cadence.late_items(releases, sched, prev, NOW)]
    assert labels == [
        "releases.lecture_02 -> deploy[1]",  # 09:00 Berlin, the earliest
        "assignments.assignment-1 handout",  # 09:30
        "assignments.assignment-1 snapshot",  # 10:00 (due_datetime, no grading pin)
        "assignments.assignment-1 solution",  # 10:30
        "releases.lecture_02 -> deploy[0]",  # 11:00, the entry's own event_datetime
    ]


def test_a_term_written_entirely_in_the_past_is_not_late():
    # The post-summer false alarm: a plan whose whole term has gone by is late by months,
    # but nothing in it fell in THIS gap, so nothing here shipped late.
    releases, sched = _plan()
    prev = NOW - timedelta(minutes=20)
    assert cadence.late_items(releases, sched, prev, NOW) == []


def test_a_moment_inside_the_slo_is_not_late():
    releases, sched = _plan()
    # A gap that opened a week ago catches every moment; measured at 11:30 Berlin (09:30
    # UTC) the 11:00 deploy is 30 min old, inside GAP_SLO, and the 10:30 solution is 60 min
    # old - exactly at it, and the rule is strictly greater.
    now = datetime(2026, 9, 4, 9, 30, tzinfo=timezone.utc)
    prev = now - timedelta(days=7)
    labels = [i.label for i in cadence.late_items(releases, sched, prev, now)]
    assert "releases.lecture_02 -> deploy[0]" not in labels
    assert "assignments.assignment-1 solution" not in labels
    assert "assignments.assignment-1 handout" in labels


def test_without_a_previous_tick_there_is_no_gap_to_report():
    releases, sched = _plan()
    assert cadence.late_items(releases, sched, None, NOW) == []


def test_a_display_only_entry_is_never_late():
    # No actions, so nothing was ever going to ship at its datetime.
    sched = Schedule(
        releases=[
            Release(label="clinic", when=datetime(2026, 9, 4, 9, 0, tzinfo=BERLIN))
        ]
    )
    prev = NOW - timedelta(days=1)
    assert cadence.late_items(sched.releases, sched, prev, NOW) == []


def test_a_tbc_entry_is_never_late():
    sched = Schedule(
        releases=[Release(label="tbc", when=None, assignment="assignment-1-f2026")]
    )
    assert cadence.late_items(sched.releases, sched, NOW - timedelta(days=1), NOW) == []


def test_neither_handout_nor_solution_is_reported_without_a_synthesised_release():
    # `_handout_releases` skips an assignment with no template repo in the course org, so no
    # release carries its handout - and the model solution rides on that same release, so
    # without one it could not have shipped either. Something that could not ship is not
    # "late". The SNAPSHOT is different: the freeze needs no template, so it still counts.
    _releases, sched = _plan()
    labels = [
        i.label
        for i in cadence.late_items(
            list(sched.releases), sched, NOW - timedelta(days=1), NOW
        )
    ]
    assert "assignments.assignment-1 handout" not in labels
    assert "assignments.assignment-1 solution" not in labels
    assert "assignments.assignment-1 snapshot" in labels


def test_a_late_label_names_a_yaml_field_and_never_a_repo_or_a_person():
    # The cohort issue is private, but the same labels reach the run log of a PUBLIC repo,
    # and `describe()` output would name the course org's template repos.
    releases, sched = _plan()
    items = cadence.late_items(releases, sched, NOW - timedelta(days=1), NOW)
    assert items
    for item in items:
        assert item.label.startswith(("releases.", "assignments."))
        assert "cm-f2026" not in item.label
        assert "assignment-1-f2026" not in item.label
        assert "-anna" not in item.label


# ------------------------------------------------------------------------- the two reports


class _Issues:
    """Records what cadence asked the issue helpers to do; `existing` is what `find_issue`
    answers with, for every repo and title."""

    def __init__(self, existing: tuple[int, str] | None, rc: int):
        self.existing = existing
        self.rc = rc
        self.upserted: list[tuple[str, str, str, str | None]] = []
        self.closed: list[tuple[str, str, str | None]] = []

    def find(self, repo, title):
        return self.existing

    def upsert(self, repo, title, body, comment=None):
        self.upserted.append((repo, title, body, comment))
        return self.rc

    def close(self, repo, title, comment=None):
        self.closed.append((repo, title, comment))
        return self.rc


@pytest.fixture
def stub(monkeypatch):
    def _make(existing: tuple[int, str] | None = None, rc: int = 0) -> _Issues:
        s = _Issues(existing, rc)
        monkeypatch.setattr(cadence, "find_issue", s.find)
        monkeypatch.setattr(cadence, "upsert_issue", s.upsert)
        monkeypatch.setattr(cadence, "close_issues_titled", s.close)
        return s

    return _make


def _verdict(**kw) -> cadence.Verdict:
    base = {
        "armed": True,
        "last_dispatch_at": NOW - timedelta(minutes=5),
        "last_schedule_at": NOW - timedelta(minutes=40),
        "ds01_dead": False,
        "prev_executed_at": NOW - timedelta(minutes=15),
        "recent_gaps": [timedelta(minutes=15)] * cadence.HEALTHY_GAPS,
        "now": NOW,
    }
    return cadence.Verdict(**{**base, **kw})


def _item(label: str, minutes_late: int = 90) -> cadence.LateItem:
    return cadence.LateItem(
        label, NOW - timedelta(minutes=minutes_late), timedelta(minutes=minutes_late)
    )


# ---- driver health


def test_a_disarmed_verdict_with_no_open_issue_writes_nothing_anywhere(stub, capsys):
    s = stub()
    v = _verdict(armed=False, ds01_dead=False, last_dispatch_at=None)
    assert cadence.report_course("Course-Org", v) == 0
    assert (
        cadence.report_cohort("Course-Org", "Cohort-f2026", v, [_item("releases.a")])
        == 0
    )
    assert (s.upserted, s.closed) == ([], [])
    # ...but it says so, on the run summary, without reddening the run - and it says what
    # it actually knows: nothing in the WINDOW, not "no driver has ever existed".
    err = capsys.readouterr().err
    assert "::warning::" in err
    assert cadence.DISPATCH_EVENT in err
    assert f"last {cadence.RUNS_PAGE} runs" in err
    assert "yet" not in err


def test_a_dispatcher_dead_long_enough_to_leave_the_window_is_still_watched(stub):
    # The trap in reading "armed" as history: the dispatcher dies, the issue opens, and
    # days later its last run scrolls out of the last RUNS_PAGE runs. Read as disarmed, the
    # module would fall silent about a broken org whose own alarm is still standing - so an
    # OPEN issue is the other half of the evidence.
    s = stub(existing=(7, "opened while it was still in the window"))
    v = _verdict(armed=False, ds01_dead=False, last_dispatch_at=None)
    assert cadence.report_course("Course-Org", v) == 0
    assert s.closed == []
    (_repo, _title, body, comment) = s.upserted[0]
    assert f"not once in the last {cadence.RUNS_PAGE} runs" in body
    assert cadence.read_state(body) == {"dispatch": cadence.DISPATCH_UNSEEN}
    # a body we did not write reads as no state, so this counts as a transition
    assert comment is not None and "in the last 20 runs" in comment


def test_a_dead_dispatcher_opens_the_issue_in_the_public_dot_github(stub):
    s = stub()
    v = _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    assert cadence.report_course("Course-Org", v) == 0
    (repo, title, body, comment) = s.upserted[0]
    assert repo == "Course-Org/.github"
    assert title == cadence.DRIVER_TITLE
    # a brand-new issue notifies by being created - `upsert_issue` drops the comment there,
    # so passing one is safe and this is a transition either way
    assert comment is not None
    assert "cc @Course-Org/course-admin" in body
    assert "180 min ago" in body


def _driver_body(verdict: cadence.Verdict) -> str:
    """The rendered driver-health body, exactly as `report_course` renders it."""
    return cadence._driver_body("Course-Org", verdict)


def test_the_driver_body_carries_only_timestamps_and_minutes():
    # This issue lives in a WORLD-READABLE repo, so the body is held to workflow names,
    # timestamps, minutes and thresholds - nothing that says who is in the cohort.
    body = _driver_body(
        _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    )
    assert cadence.read_state(body) == {"dispatch": cadence.DISPATCH_STALE}
    assert body.strip().endswith("-->")  # the marker is last, out of the reader's way
    for leak in ("classroom-config/", "-anna", "grades-"):
        assert leak not in body


def test_the_body_records_githubs_cron_without_alarming_on_it(stub):
    # It drops most of its fires by design, so the line is information and there is no
    # threshold behind it. A silent cron alone opens nothing.
    s = stub()
    quiet_cron = _verdict(last_schedule_at=None)
    assert cadence.report_course("Course-Org", quiet_cron) == 0
    assert s.upserted == []  # the dispatcher is fine, so there is nothing to open
    body = _driver_body(_verdict(ds01_dead=True, last_schedule_at=None))
    assert (
        f"last GitHub cron fire: not once in the last {cadence.RUNS_PAGE} runs" in body
    )
    assert "INFORMATION only" in body


def test_a_standing_alarm_is_not_re_announced_every_tick(stub):
    v = _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    s = stub(existing=(7, _driver_body(v)))
    assert cadence.report_course("Course-Org", v) == 0
    (_repo, _title, _body, comment) = s.upserted[0]
    assert comment is None  # the body is refreshed; nobody is emailed again


def test_the_dispatcher_dropping_out_of_the_window_is_worth_a_second_email(stub):
    # The one transition a standing alarm has: "seen, too long ago" is bad, "not in the
    # last 20 runs at all" is worse, and a human should hear it.
    was = _driver_body(
        _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    )
    s = stub(existing=(7, was))
    worse = _verdict(armed=False, ds01_dead=False, last_dispatch_at=None)
    assert cadence.report_course("Course-Org", worse) == 0
    (_repo, _title, body, comment) = s.upserted[0]
    assert cadence.read_state(body) == {"dispatch": cadence.DISPATCH_UNSEEN}
    assert comment is not None
    assert "no external dispatch at all" in comment
    assert "cc @Course-Org/course-admin" in comment


def test_a_dispatcher_back_on_cadence_closes_the_issue(stub):
    s = stub(existing=(7, "whatever"))
    assert cadence.report_course("Course-Org", _verdict()) == 0
    assert s.upserted == []
    ((repo, title, comment),) = s.closed
    assert (repo, title) == ("Course-Org/.github", cadence.DRIVER_TITLE)
    assert "firing again" in comment


def test_one_dispatch_after_an_outage_does_not_close_the_issue(stub):
    # Closing on "seen inside 2h" would close on the first fire after a three-hour outage
    # and reopen on the next missed one. The close rule is BACK ON CADENCE.
    s = stub(existing=(7, "whatever"))
    limping = _verdict(last_dispatch_at=NOW - timedelta(minutes=45))
    assert limping.ds01_dead is False and limping.dispatch_on_cadence is False
    assert cadence.report_course("Course-Org", limping) == 0
    assert (s.upserted, s.closed) == ([], [])


def test_a_dry_run_never_arms_closes_or_comments(stub):
    # The manual dispatch button defaults to a dry run, so a curious click must not close
    # a live alarm - or open one.
    s = stub(existing=(7, "body"))
    dead = _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    assert cadence.report_course("Course-Org", dead, dry_run=True) == 0
    assert cadence.report_course("Course-Org", _verdict(), dry_run=True) == 0
    assert (s.upserted, s.closed) == ([], [])


def test_an_exception_inside_the_check_is_counted_never_raised(monkeypatch, capsys):
    # It counts (this is the check that watches the drivers, so its own silence matters)
    # but it never raises: `scheduler.main` calls this after the releases, and a
    # notification problem must not escape as a traceback.
    def boom(*a, **k):
        raise RuntimeError("GitHub is having a day")

    monkeypatch.setattr(cadence, "find_issue", boom)
    v = _verdict(ds01_dead=True, last_dispatch_at=NOW - timedelta(minutes=180))
    assert cadence.report_course("Course-Org", v) == 1
    assert "driver health" in capsys.readouterr().err


def test_a_cohort_exception_is_counted_never_raised(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("GitHub is having a day")

    monkeypatch.setattr(cadence, "find_issue", boom)
    v = _verdict(recent_gaps=[])
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", v, [_item("a")]) == 1
    assert "late deliveries" in capsys.readouterr().err


# ---- late delivery


def test_late_items_open_the_issue_in_the_private_classroom_config(stub):
    s = stub()
    items = [
        _item("releases.lecture_02 -> deploy[0]", 132),
        _item("assignments.a1 snapshot", 95),
    ]
    v = _verdict(prev_executed_at=NOW - timedelta(minutes=140), recent_gaps=[])
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", v, items) == 0
    (repo, title, body, comment) = s.upserted[0]
    assert repo == "Cohort-f2026/classroom-config"
    assert title == cadence.LATE_TITLE
    assert "`releases.lecture_02 -> deploy[0]`: due " in body
    assert "(+132 min)" in body
    assert "cc @Cohort-f2026/instructors" in body
    assert comment is not None
    assert cadence.read_state(body) == {
        "items": ["assignments.a1 snapshot", "releases.lecture_02 -> deploy[0]"]
    }


def test_only_an_item_the_body_does_not_already_list_earns_a_comment(stub):
    body = cadence._late_body(
        "Course-Org", "Cohort-f2026", _verdict(), [_item("releases.a")]
    )
    s = stub(existing=(7, body))
    v = _verdict(recent_gaps=[])
    # the same item again: refreshed, and silent
    assert (
        cadence.report_cohort("Course-Org", "Cohort-f2026", v, [_item("releases.a")])
        == 0
    )
    assert s.upserted[0][3] is None
    # a second, different item: one comment, naming only the new one
    s = stub(existing=(7, body))
    assert (
        cadence.report_cohort(
            "Course-Org", "Cohort-f2026", v, [_item("releases.a"), _item("releases.b")]
        )
        == 0
    )
    comment = s.upserted[0][3]
    assert "releases.b" in comment and "releases.a" not in comment


def test_nothing_late_but_no_proven_cadence_leaves_the_issue_open(stub):
    # Half an outage is not a recovery: closing on the first quiet tick would reopen the
    # issue on the next late moment, twice an hour.
    s = stub(existing=(7, "body"))
    v = _verdict(recent_gaps=[timedelta(minutes=15), timedelta(minutes=200)])
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", v, []) == 0
    assert (s.upserted, s.closed) == ([], [])


def test_eight_healthy_gaps_close_the_late_delivery_issue(stub):
    s = stub(existing=(7, "body"))
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", _verdict(), []) == 0
    ((repo, title, comment),) = s.closed
    assert (repo, title) == ("Cohort-f2026/classroom-config", cadence.LATE_TITLE)
    assert str(cadence.HEALTHY_GAPS) in comment


def test_this_ticks_own_gap_counts_towards_the_close(stub):
    # Eight punctual historical gaps and then a three-hour hole, and this tick is the one
    # on the far side of it. Judged on history alone the issue closes, and its closing
    # comment claims eight consecutive on-time ticks - about the very gap that was late.
    s = stub(existing=(7, "body"))
    limped_in = _verdict(prev_executed_at=NOW - timedelta(hours=3))
    assert limped_in.gap == timedelta(hours=3)
    assert limped_in.healthy is False
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", limped_in, []) == 0
    assert (s.upserted, s.closed) == ([], [])


def test_a_gap_of_exactly_twenty_minutes_is_still_healthy():
    # The boundary, so a `<=`/`<` swap fails here rather than reopening the issue on every
    # tick that lands exactly on the cadence.
    on_the_line = _verdict(
        prev_executed_at=NOW - cadence.HEALTHY_GAP,
        recent_gaps=[cadence.HEALTHY_GAP] * cadence.HEALTHY_GAPS,
    )
    assert on_the_line.healthy is True
    assert _verdict(prev_executed_at=NOW - timedelta(minutes=21)).healthy is False


def test_no_previous_tick_at_all_is_not_good_news():
    # Nothing to measure is not "on cadence" - it is no evidence, and a standing issue
    # stays standing.
    assert _verdict(prev_executed_at=None).healthy is False


def test_a_cohort_dry_run_writes_nothing(stub):
    s = stub()
    v = _verdict(recent_gaps=[])
    assert (
        cadence.report_cohort(
            "Course-Org", "Cohort-f2026", v, [_item("releases.a")], dry_run=True
        )
        == 0
    )
    assert (
        cadence.report_cohort(
            "Course-Org", "Cohort-f2026", _verdict(), [], dry_run=True
        )
        == 0
    )
    assert (s.upserted, s.closed) == ([], [])


def test_a_write_that_failed_is_counted(stub):
    s = stub(rc=1)
    v = _verdict(recent_gaps=[])
    assert cadence.report_cohort("Course-Org", "Cohort-f2026", v, [_item("a")]) == 1
    assert len(s.upserted) == 1


# --------------------------------------------------------------------------- the one read


def test_fetch_runs_asks_for_one_page_of_this_workflows_runs(monkeypatch):
    seen: list[tuple[str, ...]] = []

    def fake_json(*args):
        seen.append(args)
        return {"workflow_runs": [_run(5)]}

    monkeypatch.setattr(cadence, "gh_json", fake_json)
    assert len(cadence.fetch_runs("Course-Org")) == 1
    (args,) = seen
    assert args[0] == "api"
    assert args[1] == (
        f"repos/Course-Org/.github/actions/workflows/{cadence.WORKFLOW_FILE}/runs"
        f"?per_page={cadence.RUNS_PAGE}"
    )


def test_an_empty_listing_is_an_empty_list_not_a_crash(monkeypatch):
    monkeypatch.setattr(cadence, "gh_json", lambda *a: {})
    assert cadence.fetch_runs("Course-Org") == []


def test_own_run_id_comes_from_the_actions_environment(monkeypatch):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    assert cadence.own_run_id() is None
    monkeypatch.setenv("GITHUB_RUN_ID", "17")
    assert cadence.own_run_id() == "17"
