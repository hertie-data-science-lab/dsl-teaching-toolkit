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
from conftest import CREATED_ISSUE_URL, issue_row, source_fault

from dsl_course import schedule
from dsl_course import source_digest as sd

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=BERLIN)
COURSE = sd.Context("Course")
COHORT = sd.Context("Course", "Cohort")


def _f(where, offset, field="course_source_path", lineno=None, repo="cm"):
    return source_fault(
        where,
        f"Course/{repo}/x does not exist",
        NOW + offset if offset else None,
        field=field,
        lineno=lineno,
        repo=repo,
        path="x",
    )


@pytest.fixture(autouse=True)
def _the_tier_is_not_what_this_is_about(monkeypatch):
    """The body's field-reference link is pinned to the tier the course org runs; that
    read is not what any of these tests is about."""
    monkeypatch.setattr(sd, "central_ref_for", lambda org: "release")


# ------------------------------------------------------------------ state round-trip


def _state(body: str) -> dict:
    return sd._read_marker(body, sd._STATE, {})


def test_the_body_carries_its_own_previous_state():
    # No committed state file and no database - the issue IS the record, so the digest
    # can tell "still broken" from "just got worse" with nothing but what it last wrote.
    body = sd.render_body([_f("releases.a", timedelta(hours=2))], NOW, COURSE)
    assert _state(body) == {"releases.a[x].course_source_path": "critical"}


def test_a_body_this_module_did_not_write_reads_as_no_state():
    assert _state("someone typed this by hand") == {}
    assert _state("") == {}
    assert _state("<!-- dsl-source-state: not json -->") == {}
    # A marker holding the wrong SHAPE is a hand-edit too, and reading a list as a state
    # map would raise inside a release tick.
    assert _state("<!-- dsl-source-state: [1, 2] -->") == {}


def test_the_state_markers_are_invisible_in_the_rendered_issue():
    body = sd.render_body([_f("releases.a", timedelta(days=30))], NOW, COURSE)
    assert body.count("<!-- dsl-source-state:") == 1
    assert body.count("<!-- dsl-source-mention:") == 1
    assert body.strip().endswith("-->")  # last lines, out of the reader's way


def test_the_body_names_the_field_to_edit_not_just_the_entry():
    body = sd.render_body(
        [_f("assignments.a1", None, field="course_source_repo")], NOW, COURSE
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
        COURSE,
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
        COURSE,
    )
    assert "### MISSED\n" in body
    assert "### CRITICAL (6h)\n" in body
    assert "### URGENT (12h)\n" in body
    assert "### WARNING (24h)\n" in body
    assert "### advisory\n" in body
    # The hours are the DEADLINE, not a count of the rows under the heading - those are
    # right there, and a number that has to agree with them can disagree with them.
    assert "(2)" not in body
    # A fault that has already fired did not "fire" in the future tense.
    assert "_fired " in body and "_fires " in body


def test_the_body_carries_the_one_sentence_that_would_fix_each_fault():
    # The issue and the mail say the SAME remedy, because both ask the fault - an issue
    # and an email disagreeing about the fix is worse than either on its own.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3), lineno=36)], NOW, COHORT
    )
    assert "|  fix: push the materials to that folder in Course/cm," in body


def test_the_body_addresses_the_people_git_named():
    # A team mention reaches everybody and is therefore what nobody reads. The planner of
    # the line and the committer of the repo are the two people who can act.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))],
        NOW,
        COHORT._replace(mention=("JanG", "cpj97")),
    )
    assert "cc @JanG @cpj97" in body
    assert "Cohort/instructors" not in body


def test_with_nobody_named_the_body_falls_back_to_the_team():
    body = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, COHORT)
    assert "cc @Cohort/instructors" in body


def test_the_body_tells_the_reader_not_to_close_it_by_hand():
    # Closing it fixes nothing in the file and the next tick re-opens it. Saying so is
    # cheaper than the state adoption that has to cope with it.
    body = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, COHORT)
    assert "**Do not close or edit this issue by hand.**" in body


def test_the_body_links_at_the_line_to_edit():
    # `releases.lecture_02` still leaves faculty scrolling a file they wrote in August.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3), lineno=36)], NOW, COHORT
    )
    assert (
        "at [`schedule.yml:36`](https://github.com/Cohort/classroom-config/blob/main/"
        "schedule.yml#L36)"
    ) in body


def test_a_fault_whose_line_is_unknown_is_listed_without_one():
    # The parser has no line for a plan a caller built itself, and a broken link is worse
    # than no link - the fault itself still has to be reported.
    body = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, COHORT)
    assert "**releases.a -> course_source_path** at `schedule.yml`" in body
    assert "schedule.yml#L" not in body


def test_the_field_reference_points_at_the_tier_the_org_runs():
    # The runbook describes the engine the org actually runs; a trunk org sent to release's
    # docs reads a schema for code it does not have.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=2))],
        NOW,
        COHORT._replace(central_ref="main"),
    )
    assert "/blob/main/docs/07-schedule-releases.md" in body


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


# ------------------------------------------------- state written by an older version


@pytest.mark.parametrize("offset", [timedelta(hours=3), -timedelta(hours=1)])
def test_a_rung_name_this_ladder_no_longer_has_is_not_an_escalation(gh, offset):
    # Live bodies record `error`, the rung that became `urgent` and `critical`. Read as
    # the quietest rung it made every standing fault an escalation on the first tick after
    # the deploy, whatever it had actually been reported at.
    fault = _f("releases.a", offset)
    fake = gh(_open(fault, state={fault.key: "error"}))
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.mail == {}
    assert fake.did("issue", "comment") == []
    # ...and the marker is rewritten in this ladder's own vocabulary, so it happens once.
    assert _state(fake.body_of("issue", "edit")) == {
        fault.key: str(fault.severity(NOW))
    }


def test_an_unreadable_previous_rung_still_clears(gh):
    # "It is fixed" is decided by the key being gone, not by the rung it left from.
    fault = _f("releases.a", timedelta(hours=3))
    fake = gh(_open(state={fault.key: "error"}))
    sd.sync("Cohort", "Course", [], NOW)
    (closed,) = fake.did("issue", "close")
    assert "7" in closed


def test_an_old_shaped_key_is_matched_to_the_fault_it_belongs_to():
    # `key` gained `[path]` after these issues were opened, so every one of them carries
    # `<where>.<field>`. Renamed, not read as-is: as-is it is a fault that cleared and a
    # fault that appeared, in the same tick.
    fault = _f("releases.a", timedelta(hours=20))
    assert sd.migrated({"releases.a.course_source_path": "warning"}, [fault]) == (
        {fault.key: "warning"}
    )


def test_an_old_key_two_deploys_now_share_is_dropped_rather_than_guessed():
    # These are the two faults the path was added to tell apart, and the recorded rung
    # belonged to one of them. Dropping it makes the loudest appear again; keeping it
    # would silence whichever one it was not.
    both = [
        source_fault("releases.a", fires=NOW + timedelta(hours=3), path=p, repo="cm")
        for p in ("x", "y")
    ]
    assert sd.migrated({"releases.a.course_source_path": "warning"}, both) == {}


def test_a_key_matching_nothing_today_is_left_alone_so_it_can_clear():
    assert sd.migrated({"releases.gone.course_source_path": "urgent"}, []) == (
        {"releases.gone.course_source_path": "urgent"}
    )


def test_a_body_carrying_both_shapes_keeps_the_rung_it_reported_at():
    # A tick that ran mid-migration. The current-shaped key is the one the last comment
    # was written from.
    fault = _f("releases.a", timedelta(hours=20))
    assert sd.migrated(
        {"releases.a.course_source_path": "warning", fault.key: "critical"}, [fault]
    ) == {fault.key: "critical"}


# ------------------------------------------------------------------------- sync + IO


def _open(*faults, state=None, mention=()):
    """An OPEN digest whose body is what a previous tick would have written."""
    body = sd.render_body(
        list(faults), NOW, COHORT._replace(mention=tuple(mention)), state
    )
    return [issue_row(7, sd.TITLE, body)]


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
    assert out.issue_url == CREATED_ISSUE_URL


def test_a_quiet_tick_edits_the_body_and_says_nothing(gh):
    # The hourly cron re-runs with nothing changed. The body is refreshed (GitHub does not
    # email on a body edit) and NOT commented on - this is the noise control.
    fault = _f("releases.a", timedelta(hours=20))
    fake = gh(_open(fault))
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.errors == 0 and out.mail == {}
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


def test_an_escalation_comments_and_mentions_the_instructors(gh):
    fake = gh(_open(_f("releases.a", timedelta(hours=20))))
    out = sd.sync(
        "Cohort", "Course", [_f("releases.a", timedelta(hours=3), lineno=36)], NOW
    )
    assert out.errors == 0
    (comment,) = fake.did("issue", "comment")
    text = comment[comment.index("--body") + 1]
    assert "Escalated" in text and "now **CRITICAL**" in text
    # The fix is one click from the notification, not a scroll through the file.
    assert "schedule.yml#L36" in text
    # An issue only emails people it mentions - without this the comment is as silent as
    # the run summary it exists to improve on.
    assert "cc @Cohort/instructors" in text


def test_sync_reports_what_is_owed_a_mail_and_the_issue_to_link_to(gh):
    # What the notifier mails on top of the @mention: the rung each key crossed, the
    # issue that holds the detail, and the faults themselves.
    gh(_open(_f("releases.a", timedelta(hours=20))))
    fault = _f("releases.a", timedelta(hours=3), lineno=36)
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.mail == {"releases.a[x].course_source_path": sd.Severity.CRITICAL}
    assert out.issue_url == "https://github.com/Cohort/classroom-config/issues/7"
    assert out.faults_by_key == {"releases.a[x].course_source_path": fault}


def test_a_digest_opened_before_the_key_changed_says_nothing_new(gh):
    # The first tick after the deploy, against a live issue's own body. Neither a Cleared
    # comment nor a fresh mail: nothing about this fault has changed.
    fault = _f("releases.a", timedelta(hours=20))
    fake = gh(_open(fault, state={"releases.a.course_source_path": "warning"}))
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.mail == {}
    assert fake.did("issue", "comment") == []
    # ...and the marker is rewritten in the current shape, so this runs once.
    assert _state(fake.body_of("issue", "edit")) == {fault.key: "warning"}


def test_an_old_shaped_key_still_escalates_from_the_rung_it_recorded(gh):
    fault = _f("releases.a", timedelta(hours=3), lineno=36)
    fake = gh(_open(state={"releases.a.course_source_path": "warning"}))
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.mail == {fault.key: sd.Severity.CRITICAL}
    (comment,) = fake.did("issue", "comment")
    assert "Escalated" in comment[comment.index("--body") + 1]


def test_a_withheld_source_is_listed_and_commented_on_but_never_mailed(gh):
    # It caps at WARNING, which is the rung the mail starts at, so without a rule of its
    # own it mails on appearance - about files that ARE in the org, held back by a pattern
    # faculty wrote deliberately. The issue can be read and revisited; an inbox cannot.
    withheld = source_fault(
        "releases.a",
        "the files exist but cm/.releaseignore keeps them back",
        NOW + timedelta(hours=3),
        kind=schedule.FaultKind.WITHHELD,
        ceiling=sd.Severity.WARNING,
        path="x",
    )
    fake = gh([])
    out = sd.sync("Cohort", "Course", [withheld], NOW)
    assert out.mail == {}
    (created,) = fake.did("issue", "create")
    assert "keeps them back" in created[created.index("--body") + 1]


def test_the_last_fault_clearing_closes_the_issue(gh):
    fake = gh(_open(_f("releases.a", timedelta(hours=3))))
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0 and out.mail == {}
    (closed,) = fake.did("issue", "close")
    assert "7" in closed
    assert fake.did("issue", "edit") == []
    # It closes with the one sentence that says so; nobody is emailed that a problem
    # stopped existing.
    assert "is now usable" in closed[closed.index("--comment") + 1]


def test_nothing_missing_and_no_issue_is_a_complete_no_op(gh):
    fake = gh([])
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0 and out.issue_url is None
    assert fake.did("issue", "create") == fake.did("issue", "close") == []


def test_an_issue_a_human_filed_is_never_adopted_and_rewritten(gh):
    # `--search` is full-text, so someone quoting the title in their own issue would come
    # back in the results. Rewriting their issue out from under them would be worse than
    # opening a second one.
    fake = gh([issue_row(3, "re: " + sd.TITLE, "my notes")])
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


def test_the_body_is_written_from_one_listing_not_two(gh):
    # The state to compare against, the create-or-edit decision and the state a hand-close
    # left behind are all in that listing - each asked separately was three searches a
    # tick.
    fake = gh(_open(_f("releases.a", timedelta(hours=20))))
    sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert len(fake.did("issue", "list")) == 1


# ------------------------------------------------------------------- quiet hours

# 02:00 and 07:05 in the cohort's zone. The window is LOCAL on purpose: the scheduler
# ticks in UTC, and 02:00 in a datacentre is nobody's night.
NIGHT = datetime(2026, 8, 17, 2, 0, tzinfo=BERLIN)
MORNING = datetime(2026, 8, 17, 7, 5, tzinfo=BERLIN)

# 05:00 and 06:00 on the day of NOW: one still to fire when the night tick runs, one that
# has fired by the time the morning tick does.
_KEY = "releases.a[x].course_source_path"
_FIRES_AT_FIVE = -timedelta(hours=7)
_FIRED_AT_SIX = -timedelta(hours=6)


def test_the_quiet_window_is_the_night_in_the_cohorts_own_zone():
    assert sd.in_quiet_hours(NIGHT)
    assert sd.in_quiet_hours(datetime(2026, 8, 17, 22, 0, tzinfo=BERLIN))
    assert sd.in_quiet_hours(datetime(2026, 8, 17, 6, 59, tzinfo=BERLIN))
    assert not sd.in_quiet_hours(MORNING)
    assert not sd.in_quiet_hours(NOW)


def test_a_rung_crossed_at_two_in_the_morning_updates_the_body_and_says_nothing(gh):
    # Waking somebody at 02:00 about a folder they cannot push to until they are at a
    # keyboard is how a notification channel gets muted. The body still LISTS it at 02:00;
    # the comment and the mail wait for the morning.
    fake = gh(_open(state={_KEY: "warning"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRES_AT_FIVE)], NIGHT)
    assert out.mail == {}
    assert fake.did("issue", "comment") == []
    body = fake.body_of("issue", "edit")
    assert "### CRITICAL" in body  # the current rung is what the body shows
    # ...and the rung RECORDED is the one already reported, so the next tick finds the
    # very same crossing rather than a debt to remember.
    assert _state(body) == {_KEY: "warning"}


def test_the_first_tick_after_seven_says_it_once_at_the_rung_it_reached(gh):
    # Two rungs crossed overnight are ONE notification: what matters in the morning is how
    # bad it is now, not the order it got there.
    fake = gh(_open(state={_KEY: "warning"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRED_AT_SIX)], MORNING)
    assert out.mail == {_KEY: sd.Severity.MISSED}
    assert len(fake.did("issue", "comment")) == 1
    # Recorded at last, so the tick after this one has nothing to say.
    assert _state(fake.body_of("issue", "edit")) == {_KEY: "missed"}


def test_a_fault_that_appeared_overnight_is_not_recorded_at_all(gh):
    # An appearance held as "reported at WARNING" would never be announced: the morning
    # tick would see no change. Left out of the marker, it appears again in the morning.
    fake = gh(_open(state={}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRES_AT_FIVE)], NIGHT)
    assert out.mail == {}
    assert _state(fake.body_of("issue", "edit")) == {}


def test_a_fault_that_cleared_overnight_closes_the_issue_at_once(gh):
    # It shipped. There is no notification to hold - the issue closes itself and nobody is
    # emailed about it.
    fake = gh(_open(_f("releases.a", timedelta(hours=3))))
    out = sd.sync("Cohort", "Course", [], NIGHT)
    assert out.mail == {}
    assert len(fake.did("issue", "close")) == 1


def test_a_rung_crossed_in_the_working_day_notifies_at_once(gh):
    gh(_open(state={_KEY: "warning"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.mail == {_KEY: sd.Severity.CRITICAL}


def test_a_standing_fault_that_crossed_nothing_owes_no_mail(gh):
    # The hourly case. Every tick sees the same fault at the same rung, and mailing on
    # that is what "notify on transitions only" exists to prevent.
    gh(_open(state={_KEY: "missed"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRED_AT_SIX)], MORNING)
    assert out.mail == {}


def test_a_clock_that_jumps_cannot_deliver_the_same_notification_twice(gh):
    # A DST change, or a cron catching up after an outage. Nothing is owed and nothing is
    # spent: the state marker already says MISSED, so there is no transition to find.
    fake = gh(_open(state={_KEY: "missed"}))
    for when in (MORNING, MORNING + timedelta(hours=1)):
        out = sd.sync("Cohort", "Course", [_f("releases.a", _FIRED_AT_SIX)], when)
        assert out.mail == {}
    assert fake.did("issue", "comment") == []


# ------------------------------------------------------- who is @mentioned, and when


def _never():
    raise AssertionError("nothing to say - who to tell must not be asked")


def test_a_tick_with_nothing_to_say_reuses_the_logins_the_body_recorded(gh):
    # The answer costs a blame query, a people.yml read and a commit lookup per repo,
    # every fifteen minutes for as long as the fault stands.
    fault = _f("releases.a", timedelta(hours=20))
    fake = gh(_open(fault, mention=["JanG"]))
    sd.sync("Cohort", "Course", [fault], NOW, resolve_mention=_never)
    assert "cc @JanG" in fake.body_of("issue", "edit")


def test_a_tick_with_a_comment_to_post_asks_who_to_tell(gh):
    fake = gh(_open(_f("releases.a", timedelta(hours=20)), mention=["Stale"]))
    sd.sync(
        "Cohort",
        "Course",
        [_f("releases.a", timedelta(hours=3))],
        NOW,
        resolve_mention=lambda: ["JanG", "cpj97"],
    )
    (comment,) = fake.did("issue", "comment")
    assert "cc @JanG @cpj97" in comment[comment.index("--body") + 1]
    assert "cc @JanG @cpj97" in fake.body_of("issue", "edit")


def test_the_tick_that_opens_the_issue_asks_too(gh):
    # There is no previous body to read a mention off, and the creation is the
    # notification.
    gh([])
    sd.sync(
        "Cohort",
        "Course",
        [_f("releases.a", timedelta(hours=20))],
        NOW,
        resolve_mention=lambda: ["JanG"],
    )


def test_a_held_notification_does_not_spend_the_lookup_either(gh):
    # Nothing is said until the morning, so there is nobody to name until then.
    fault = _f("releases.a", _FIRES_AT_FIVE)
    fake = gh(_open(fault, state={_KEY: "warning"}, mention=["JanG"]))
    sd.sync("Cohort", "Course", [fault], NIGHT, resolve_mention=_never)
    assert "cc @JanG" in fake.body_of("issue", "edit")


# ------------------------------------------------------- a digest closed by hand


def test_re_opening_adopts_the_state_of_the_issue_somebody_closed(gh):
    # Closing it staged nothing. Without adopting the state it left behind, the next tick
    # reads every standing fault as newly appeared and mails the cohort about all of it.
    gh([], closed=_open(state={_KEY: "critical"}))
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.mail == {}


def test_a_closed_issue_with_no_state_is_no_reason_not_to_report(gh):
    gh([], closed=[issue_row(7, sd.TITLE, "hand-written")])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.mail == {_KEY: sd.Severity.CRITICAL}


# --------------------------------------------------------------- when a read fails


def test_a_listing_that_could_not_be_read_is_an_error_not_an_empty_digest(gh):
    # Reported as "no issue", a rate-limited listing would open a duplicate every tick.
    fake = gh([], list_code=1)
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.errors == 1 and out.mail == {}
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


def test_a_write_that_failed_owes_nobody_a_mail(gh):
    # The state was never written, so the next tick finds the same transition and says it
    # then. Mailing now would be a mail whose issue does not say the same thing.
    gh(_open(state={_KEY: "warning"}), write_code=1)
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW)
    assert out.errors == 1 and out.mail == {}
