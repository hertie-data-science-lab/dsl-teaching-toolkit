"""source_digest: one self-updating issue per cohort, whose BODY is state and whose
COMMENTS are events. The whole point is notification volume - a term written up front has
dozens of missing sources, all normal, so anything that emails per fault or per tick buries
the one that matters. These tests pin the three moments a human is meant to hear about
(appears, escalates, clears) and the silence in between.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dsl_course import issues
from dsl_course import source_digest as sd
from dsl_course.schedule import SourceFault

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=BERLIN)
# What `gh issue create` prints, and the only place the number of a just-opened issue
# comes from.
CREATED_URL = "https://github.com/Cohort/classroom-config/issues/12"


def _f(
    where: str,
    offset: timedelta | None,
    field: str = "course_source_path",
    lineno: int | None = None,
    repo: str = "cm",
):
    return SourceFault(
        where,
        f"Course/{repo}/x does not exist",
        NOW + offset if offset else None,
        field=field,
        lineno=lineno,
        repo=repo,
        path="x",
    )


class _Gh:
    """A recording fake for ghcli.gh - every call captured, replies queued by subcommand.

    The issue LISTING goes through `gh_json` (it parses stdout alone, so a gh advisory on
    stderr cannot spoil it), so that one is served by `json` below."""

    def __init__(
        self, rows: list[dict] | None = None, closed: list[dict] | None = None
    ):
        self.rows = rows or []
        self.closed = closed or []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return 0, f"{CREATED_URL}\n" if args[:2] == ("issue", "create") else ""

    def json(self, *args, **kwargs):
        self.calls.append(args)
        state = args[args.index("--state") + 1] if "--state" in args else "open"
        return self.closed if state == "closed" else self.rows

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def body_of(self, *prefix) -> str:
        (call,) = self.did(*prefix)
        return call[call.index("--body") + 1]


@pytest.fixture
def gh(monkeypatch):
    def _make(rows=None, closed=None):
        fake = _Gh(rows, closed)
        # The issue plumbing lives in `dsl_course.issues` now (`find_issue`/`upsert_issue`/
        # `close_issues_titled`); the digest's own logic is unchanged, so this is the same
        # recording fake one module further down.
        monkeypatch.setattr(issues, "gh", fake)
        monkeypatch.setattr(issues, "gh_json", fake.json)
        # The body's field-reference link is pinned to the tier the course org runs; that
        # read is not what any of these tests is about.
        monkeypatch.setattr(sd, "central_ref_for", lambda org: "release")
        return fake

    return _make


# ------------------------------------------------------------------ state round-trip


def test_the_body_carries_its_own_previous_state():
    # No committed state file and no database - the issue IS the record, so the digest
    # can tell "still broken" from "just got worse" with nothing but what it last wrote.
    body = sd.render_body([_f("releases.a", timedelta(hours=2))], NOW, "Course")
    assert sd.read_state(body) == {"releases.a.course_source_path": "critical"}


def test_a_body_this_module_did_not_write_reads_as_no_state():
    assert sd.read_state("someone typed this by hand") == {}
    assert sd.read_state("") == {}
    assert sd.read_state("<!-- dsl-source-state: not json -->") == {}


def test_the_state_marker_is_invisible_in_the_rendered_issue():
    body = sd.render_body([_f("releases.a", timedelta(days=30))], NOW, "Course")
    assert body.count("<!-- dsl-source-state:") == 1
    assert body.strip().endswith("-->")  # last line, out of the reader's way


def test_the_body_names_the_field_to_edit_not_just_the_entry():
    body = sd.render_body(
        [_f("assignments.a1", None, field="course_source_repo")], NOW, "Course"
    )
    assert "**assignments.a1 -> course_source_repo**" in body
    assert "no date (tbc)" in body


def test_rungs_are_rendered_loudest_first():
    body = sd.render_body(
        [
            _f("releases.far", timedelta(days=40)),
            _f("releases.fired", -timedelta(hours=1)),
            _f("releases.tomorrow", timedelta(hours=3)),
            _f("releases.near", timedelta(hours=10)),
            _f("releases.soon", timedelta(hours=20)),
        ],
        NOW,
        "Course",
    )
    assert (
        body.index("### MISSED")
        < body.index("### CRITICAL")
        < body.index("### URGENT")
        < body.index("### WARNING")
        < body.index("### advisory")
    )


def test_every_rung_heading_carries_its_own_deadline():
    # The rung is the only thing that tells a reader how much of their day this deserves,
    # and the hours are in the heading so "URGENT" is a number rather than a mood. The
    # top rung is not "deploys soon" - it has already failed to ship.
    body = sd.render_body(
        [
            _f("releases.fired", -timedelta(hours=1)),
            _f("releases.tomorrow", timedelta(hours=3)),
            _f("releases.near", timedelta(hours=10)),
            _f("releases.soon", timedelta(hours=20)),
            _f("releases.far", timedelta(days=40)),
        ],
        NOW,
        "Course",
    )
    assert "### MISSED (1)" in body
    assert "### CRITICAL (6h) (1)" in body
    assert "### URGENT (12h) (1)" in body
    assert "### WARNING (24h) (1)" in body
    assert "### advisory (1)" in body
    # A fault that has already fired did not "fire" in the future tense.
    assert "_fired " in body and "_fires " in body


def test_the_body_carries_the_one_sentence_that_would_fix_each_fault():
    # The issue and the mail say the SAME remedy, because both ask the fault - an issue
    # and an email disagreeing about the fix is worse than either on its own.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3), lineno=36)], NOW, "Course", "Cohort"
    )
    assert "|  fix: push the materials to that folder in Course/cm," in body


def test_the_body_addresses_the_people_git_named():
    # A team mention reaches everybody and is therefore what nobody reads. The planner of
    # the line and the committer of the repo are the two people who can act.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))],
        NOW,
        "Course",
        "Cohort",
        mention=["JanG", "cpj97"],
    )
    assert "cc @JanG @cpj97" in body
    assert "Cohort/instructors" not in body


def test_with_nobody_named_the_body_falls_back_to_the_team():
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))], NOW, "Course", "Cohort"
    )
    assert "cc @Cohort/instructors" in body


def test_the_body_tells_the_reader_not_to_close_it_by_hand():
    # Closing it fixes nothing in the file and the next tick re-opens it. Saying so is
    # cheaper than the state adoption that has to cope with it (see adopted_state).
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))], NOW, "Course", "Cohort"
    )
    assert "**Do not close or edit this issue by hand.**" in body


def test_the_body_links_at_the_line_to_edit():
    # `releases.lecture_02` still leaves faculty scrolling a file they wrote in August.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3), lineno=36)], NOW, "Course", "Cohort"
    )
    assert (
        "at [`schedule.yml:36`](https://github.com/Cohort/classroom-config/blob/main/"
        "schedule.yml#L36)"
    ) in body


def test_a_fault_whose_line_is_unknown_is_listed_without_one():
    # The scan returns None for a line it cannot find, and a broken link is worse than no
    # link - the fault itself still has to be reported.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))], NOW, "Course", "Cohort"
    )
    assert "**releases.a -> course_source_path** at `schedule.yml`" in body
    assert "schedule.yml#L" not in body


# ------------------------------------------------------------------------ transitions


def test_a_new_advisory_is_not_news_but_a_new_warning_is():
    current = {"a.f": "advisory", "b.f": "warning", "c.f": "critical"}
    t = sd.transitions({}, current)
    assert t.appeared == ["b.f", "c.f"]  # the advisory stays quiet
    assert (t.escalated, t.cleared) == ([], [])


def test_transitions_carry_the_rung_each_fault_landed_on():
    # Who is told depends on the rung, not on the fact that something changed - so the
    # notifier reads it from here rather than re-deriving it from the faults.
    t = sd.transitions({"a.f": "warning"}, {"a.f": "missed", "b.f": "advisory"})
    assert t.escalated == ["a.f"]
    assert t.rung == {"a.f": sd.Severity.MISSED, "b.f": sd.Severity.ADVISORY}


def test_no_transition_at_all_is_falsey():
    assert not sd.transitions({"a.f": "warning"}, {"a.f": "warning"})
    assert sd.transitions({}, {"a.f": "warning"})


def test_escalation_is_reported_but_standing_still_is_not():
    previous = {"a.f": "warning", "b.f": "warning"}
    current = {"a.f": "urgent", "b.f": "warning"}
    t = sd.transitions(previous, current)
    assert t.escalated == ["a.f"]
    assert t.appeared == []  # `b` is unchanged - an hourly tick must not re-announce it


def test_clearing_is_always_news_however_quietly_it_arrived():
    # It left from `advisory`, which never earned an email going in - but "it is fixed"
    # is the message that lets someone stop worrying, so it is always reported.
    assert sd.transitions({"a.f": "advisory"}, {}).cleared == ["a.f"]


def test_de_escalation_is_not_reported_as_a_change():
    # A date pushed back makes a fault less urgent. Nothing broke, so nobody is emailed.
    t = sd.transitions({"a.f": "critical"}, {"a.f": "warning"})
    assert (t.appeared, t.escalated, t.cleared) == ([], [], [])


# ------------------------------------------------------------------------- sync + IO


def test_an_advisory_only_plan_opens_no_issue_at_all(gh):
    # Jan writes his whole term in August: 21 sources that do not exist yet, all of them
    # normal. Opening a ticket for that is the cry-wolf failure in a different channel.
    fake = gh([])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=60))], NOW)
    assert out.errors == 0
    assert fake.did("issue", "create") == []
    assert fake.did("issue", "comment") == []


def test_the_first_warning_opens_the_issue(gh):
    fake = gh([])
    assert (
        sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=20))], NOW).errors
        == 0
    )
    created = fake.did("issue", "create")
    assert len(created) == 1
    assert sd.TITLE in created[0]
    # A brand-new issue notifies by being created; a comment on top would double up.
    assert fake.did("issue", "comment") == []


def test_the_tick_that_opens_the_issue_still_reports_its_url(gh):
    # The first-appeared mail goes out on exactly this tick, and the number of an issue
    # just opened is printed by `gh issue create` and nowhere else - so without it the
    # mail could not link the record it was summarising.
    gh([])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=20))], NOW)
    assert out.issue_url == CREATED_URL


def test_a_quiet_tick_edits_the_body_and_says_nothing(gh):
    # The hourly cron re-runs with nothing changed. The body is refreshed (GitHub does not
    # email on a body edit) and NOT commented on - this is the noise control.
    body = sd.render_body([_f("releases.a", timedelta(hours=20))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": body}])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=20))], NOW)
    assert out.errors == 0 and not out.transitions
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


def test_an_escalation_comments_and_mentions_the_instructors(gh):
    was = sd.render_body([_f("releases.a", timedelta(hours=20))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    out = sd.sync(
        "Cohort", "Course", [_f("releases.a", timedelta(hours=3), lineno=36)], NOW
    )
    assert out.errors == 0
    comments = fake.did("issue", "comment")
    assert len(comments) == 1
    text = comments[0][comments[0].index("--body") + 1]
    assert "Escalated" in text and "now **CRITICAL**" in text
    # The fix is one click from the notification, not a scroll through the file.
    assert "schedule.yml#L36" in text
    # An issue only emails people it mentions - without this the comment is as silent as
    # the run summary it exists to improve on.
    assert "cc @Cohort/instructors" in text


def test_sync_reports_the_transitions_and_the_issue_to_link_to(gh):
    # What the notifier mails on top of the @mention: the rung each key crossed, the
    # issue that holds the detail, and the faults themselves.
    was = sd.render_body([_f("releases.a", timedelta(hours=20))], NOW, "Course")
    gh([{"number": 7, "title": sd.TITLE, "body": was}])
    fault = _f("releases.a", timedelta(hours=3), lineno=36)
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.transitions.escalated == ["releases.a.course_source_path"]
    assert out.transitions.rung == {
        "releases.a.course_source_path": sd.Severity.CRITICAL
    }
    assert out.issue_url == "https://github.com/Cohort/classroom-config/issues/7"
    assert out.faults_by_key == {"releases.a.course_source_path": fault}


def test_the_last_fault_clearing_closes_the_issue(gh):
    was = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0
    closed = fake.did("issue", "close")
    assert len(closed) == 1 and "7" in closed[0]
    assert fake.did("issue", "edit") == []
    # "It is fixed" is a transition too - the notifier says so on the same channel.
    assert out.transitions.cleared == ["releases.a.course_source_path"]


def test_nothing_missing_and_no_issue_is_a_complete_no_op(gh):
    fake = gh([])
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0 and out.transitions is None
    assert fake.did("issue", "create") == fake.did("issue", "close") == []


def test_an_issue_a_human_filed_is_never_adopted_and_rewritten(gh):
    # `--search` is full-text, so someone quoting the title in their own issue would come
    # back in the results. Rewriting their issue out from under them would be worse than
    # opening a second one.
    fake = gh([{"number": 3, "title": "re: " + sd.TITLE, "body": "my notes"}])
    sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=20))], NOW)
    assert fake.did("issue", "edit") == []
    assert len(fake.did("issue", "create")) == 1


def test_dry_run_touches_nothing(gh):
    fake = gh([])
    assert (
        sd.sync(
            "Cohort",
            "Course",
            [_f("releases.a", timedelta(hours=3))],
            NOW,
            dry_run=True,
        ).errors
        == 0
    )
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


def test_the_field_reference_points_at_the_tier_the_org_runs(monkeypatch):
    # The runbook describes the engine the org actually runs; a trunk org sent to release's
    # docs reads a schema for code it does not have.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=2))], NOW, "Course", "Cohort", None, "main"
    )
    assert "/blob/main/docs/07-schedule-releases.md" in body


# ------------------------------------------------------------------- quiet hours

# 02:00 and 07:05 in the cohort's zone. The window is LOCAL on purpose: the scheduler
# ticks in UTC, and 02:00 in a datacentre is nobody's night.
NIGHT = datetime(2026, 8, 17, 2, 0, tzinfo=BERLIN)
MORNING = datetime(2026, 8, 17, 7, 5, tzinfo=BERLIN)


def _prior(state: dict[str, str], held: dict[str, str] | None = None) -> list[dict]:
    """An OPEN digest whose body carries exactly this previous state and this ledger.

    Written out rather than derived from a fault list at an earlier tick: what these tests
    are about is the transition, and spelling the previous rung out is the difference
    between a fixture that says so and one that happens to compute it."""
    body = sd.render_body([], NOW, "Course", "Cohort", state, held=held)
    return [{"number": 7, "title": sd.TITLE, "body": body}]


# 05:00 and 06:00 on the day of NOW: one still to fire when the night tick runs, one that
# has fired by the time the morning tick does.
_KEY = "releases.a.course_source_path"
_FIRES_AT_FIVE = -timedelta(hours=7)
_FIRED_AT_SIX = -timedelta(hours=6)


def test_the_quiet_window_is_the_night_in_the_cohorts_own_zone():
    assert sd.in_quiet_hours(NIGHT)
    assert sd.in_quiet_hours(datetime(2026, 8, 17, 22, 0, tzinfo=BERLIN))
    assert sd.in_quiet_hours(datetime(2026, 8, 17, 6, 59, tzinfo=BERLIN))
    assert not sd.in_quiet_hours(MORNING)
    assert not sd.in_quiet_hours(NOW)


def test_a_rung_crossed_at_two_in_the_morning_updates_the_issue_but_holds_the_mail(gh):
    # Waking somebody at 02:00 about a folder they cannot push to until they are at a
    # keyboard is how a notification channel gets muted. The ISSUE still says so at 02:00.
    fake = gh(_prior({_KEY: "warning"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRES_AT_FIVE)], NIGHT)
    assert out.mail == {}
    assert len(fake.did("issue", "edit")) == 1  # the body was still refreshed
    assert fake.did("issue", "comment")  # and the escalation still commented
    # ...and the debt is recorded in the body, which is the only state this has.
    assert sd.read_pending(fake.body_of("issue", "edit")) == {_KEY: "critical"}


def test_the_first_tick_after_seven_sends_one_mail_at_the_loudest_rung_held(gh):
    # Two rungs crossed overnight owe ONE mail, not two: what matters in the morning is
    # how bad it is now, not the order it got there.
    fake = gh(_prior({_KEY: "critical"}, held={_KEY: "urgent"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRED_AT_SIX)], MORNING)
    assert out.mail == {_KEY: sd.Severity.MISSED}
    # The ledger is spent, so a clock that jumps - a DST change, a cron catching up after
    # an outage - cannot deliver the same mail twice.
    assert sd.read_pending(fake.body_of("issue", "edit")) == {}


def test_a_fault_that_cleared_overnight_owes_nobody_a_morning_mail(gh):
    # It shipped. An email about it arriving after the fact is worse than silence.
    gh(_prior({_KEY: "critical"}, held={_KEY: "critical"}))
    out = sd.sync("Cohort", "Course", [], MORNING)
    assert out.mail == {}
    assert out.transitions.cleared == [_KEY]


def test_a_rung_crossed_in_the_working_day_mails_at_once(gh):
    gh(_prior({_KEY: "warning"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.mail == {_KEY: sd.Severity.CRITICAL}


def test_a_standing_fault_that_crossed_nothing_owes_no_mail(gh):
    # The hourly case. Every tick sees the same fault at the same rung, and mailing on
    # that is what "notify on transitions only" exists to prevent.
    gh(_prior({_KEY: "missed"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRED_AT_SIX)], MORNING)
    assert out.mail == {}


def test_a_body_written_before_quiet_hours_existed_owes_nothing():
    # Every live cohort has one. Read as "everything is pending" it would mail the lot.
    assert sd.read_pending("no marker here") == {}
    assert sd.read_pending("<!-- dsl-source-pending: not json -->") == {}


# ------------------------------------------------------- a digest closed by hand


def test_re_opening_adopts_the_state_of_the_issue_somebody_closed(gh):
    # Closing it staged nothing. Without adopting the state it left behind, the next tick
    # reads every standing fault as newly appeared and mails the cohort about all of it.
    gh([], closed=_prior({_KEY: "critical"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.transitions.appeared == []
    assert out.mail == {}


def test_a_closed_issue_with_no_state_is_no_reason_not_to_report(gh):
    gh([], closed=[{"number": 7, "title": sd.TITLE, "body": "hand-written"}])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.transitions.appeared == [_KEY]
    assert out.mail == {_KEY: sd.Severity.CRITICAL}
