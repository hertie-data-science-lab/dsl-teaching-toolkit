"""config_digest, on the half `source_digest` does not exercise: a file the toolkit cannot
read at all.

An immediate fault has no deadline to escalate towards, so everything the source digest
gets from the ladder this one has to get from the CLOCK - one per issue, counted from the
oldest fault still in it. What is pinned here is that the clock says something twice and
then stops, that the body reads as a file somebody has to fix rather than as a countdown,
and that a notification is never held overnight: the value of saying it within the minute
is that whoever pushed it is still at the keyboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import issue_row, source_fault

from dsl_course import config_digest as cd
from dsl_course import source_digest
from dsl_course.faults import ConfigFault

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=BERLIN)
NIGHT = NOW.replace(hour=2)
COHORT = cd.Context("Course", "Cohort")
ROSTER = cd.ROSTER


@pytest.fixture(autouse=True)
def _the_tier_is_not_what_this_is_about(monkeypatch):
    monkeypatch.setattr(cd, "central_ref_for", lambda org: "release")


def _fault(where: str = "row 4", field: str = "role") -> ConfigFault:
    return ConfigFault(
        where,
        "unrecognised role - the row is treated as enrolled",
        file="students.csv",
        field=field,
        lineno=4,
        fix_text="fix row 4 of students.csv",
    )


def _open(*faults, state=None, since=None, sent=None):
    """An OPEN digest whose body is what a previous tick would have written."""
    body = cd.render_body(ROSTER, list(faults), NOW, COHORT, state, since)
    if sent is not None:
        body += "\n" + cd._write_marker(cd._CLOCK, {"sent": sent}, ROSTER.state_key)
    return [issue_row(7, ROSTER.title, body)]


def _state(body: str) -> dict:
    return cd._read_marker(body, cd._STATE, {}, ROSTER.state_key)


# ------------------------------------------------------------------------- the body


def test_the_body_names_the_file_and_its_own_docs():
    body = cd.render_body(ROSTER, [_fault()], NOW, COHORT)
    assert "`classroom-config/students.csv` has broken entries" in body
    assert "/docs/06-enrol-students-to-cohort.md" in body
    assert "**Do not close or edit this issue by hand.**" in body


def test_an_immediate_fault_is_filed_under_how_long_it_has_stood():
    fault = _fault()
    seen = {fault.key: (NOW - timedelta(days=3)).isoformat()}
    body = cd.render_body(ROSTER, [fault], NOW, COHORT, since=seen)
    assert "### unfixed for 3 days" in body
    assert "_first seen Sat 05 Sep 2026_" in body
    # No rung heading: WARNING (24h) would promise a deadline this fault does not have.
    assert "WARNING" not in body


def test_a_fault_that_has_just_turned_up_says_so_rather_than_a_number_of_days():
    body = cd.render_body(ROSTER, [_fault()], NOW, COHORT)
    assert "### needs fixing" in body
    assert "first seen just now" in body


def test_the_body_carries_the_rung_and_the_moment_it_was_first_seen():
    fault = _fault()
    seen = {fault.key: NOW.isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    body = cd.render_body(ROSTER, [fault], NOW, COHORT, state, seen)
    assert _state(body) == {fault.key: {"rung": "warning", "since": NOW.isoformat()}}
    assert cd._rungs(_state(body)) == {fault.key: "warning"}


def test_a_marker_written_before_the_moment_was_recorded_still_reads():
    # Every digest issue open when this shipped carries `{key: "warning"}`. Read as junk,
    # each of its faults would appear afresh - a comment and a mail about nothing.
    assert cd._rungs({"a.f": "urgent"}) == {"a.f": "urgent"}
    assert cd._first_seen({"a.f": "urgent"}) == {}


# ------------------------------------------------------------------------- the clock


def test_a_fault_that_appears_is_mailed_and_commented_once(gh):
    fault = _fault()
    fake = gh([])
    out = cd.sync(ROSTER, "Cohort", "Course", [fault], NOW)
    assert out.mail == {fault.key: cd.Severity.WARNING}
    assert out.reminder is None
    # The issue is CREATED, which notifies on its own - so no comment beside it.
    assert fake.did("issue", "create")


def test_a_standing_fault_says_nothing_at_all_on_the_next_tick(gh):
    fault = _fault()
    seen = {fault.key: NOW.isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    fake = gh(_open(fault, state=state, since=seen))
    out = cd.sync(ROSTER, "Cohort", "Course", [fault], NOW + timedelta(hours=1))
    assert out.mail == {} and out.reminder is None
    assert not fake.did("issue", "comment")


@pytest.mark.parametrize(
    ("age", "sent", "expected"),
    [
        (timedelta(hours=47), 0, None),
        (timedelta(hours=49), 0, "2 days"),
        (timedelta(hours=49), 1, None),  # already said
        (timedelta(days=8), 1, "7 days"),
        (timedelta(days=30), 2, None),  # past the last rung: silence
    ],
)
def test_the_reminder_ladder_says_it_twice_and_then_stops(gh, age, sent, expected):
    fault = _fault()
    seen = {fault.key: (NOW - age).isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    gh(_open(fault, state=state, since=seen, sent=sent))
    out = cd.sync(ROSTER, "Cohort", "Course", [fault], NOW)
    assert out.reminder == expected
    # A reminder mails everything still open, not just what changed - nothing changed.
    assert bool(out.mail) is bool(expected)


def test_a_reminder_comments_that_nothing_has_been_fixed(gh):
    fault = _fault()
    seen = {fault.key: (NOW - timedelta(days=3)).isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    fake = gh(_open(fault, state=state, since=seen, sent=0))
    cd.sync(ROSTER, "Cohort", "Course", [fault], NOW)
    comment = fake.body_of("issue", "comment")
    assert "**Escalated** (unfixed for 2 days)" in comment
    assert f"- `{fault.key}`" in comment


def test_a_reminder_names_only_the_faults_on_its_own_clock(gh):
    # schedule.yml is the ONE issue carrying both clocks. A source that fires in November
    # is counting down its own ladder; listed under "unfixed for 2 days" the comment
    # promises a deadline it does not have and disagrees with the mail beside it, which
    # counts the immediate faults alone.
    schedule_digest = source_digest.SCHEDULE
    dropped = ConfigFault(
        "releases.week_02",
        "not a mapping - the entry is dropped",
        file="schedule.yml",
        lineno=12,
    )
    later = source_fault("releases.week_09", fires=NOW + timedelta(days=60))
    seen = {
        dropped.key: (NOW - timedelta(days=3)).isoformat(),
        later.key: NOW.isoformat(),
    }
    state = cd._state_marker(cd.current_state([dropped, later], NOW), seen)
    body = (
        cd.render_body(schedule_digest, [dropped, later], NOW, COHORT, state, seen)
        + "\n"
        + cd._write_marker(cd._CLOCK, {"sent": 0}, schedule_digest.state_key)
    )
    fake = gh([issue_row(7, schedule_digest.title, body)])
    out = cd.sync(schedule_digest, "Cohort", "Course", [dropped, later], NOW)
    assert out.reminder == "2 days"
    assert set(out.mail) == {dropped.key}
    comment = fake.body_of("issue", "comment")
    assert f"- `{dropped.key}`" in comment
    assert later.key not in comment


def test_one_clock_per_issue_counted_from_the_oldest_fault(gh):
    old, new = _fault("row 4"), _fault("row 9", field="github_handle")
    seen = {
        old.key: (NOW - timedelta(days=3)).isoformat(),
        new.key: NOW.isoformat(),
    }
    state = cd._state_marker(cd.current_state([old, new], NOW), seen)
    gh(_open(old, new, state=state, since=seen, sent=0))
    out = cd.sync(ROSTER, "Cohort", "Course", [old, new], NOW)
    assert out.reminder == "2 days"
    assert set(out.mail) == {old.key, new.key}


def test_the_clock_resets_when_the_issue_closes_itself(gh):
    fake = gh(_open(_fault(), sent=2))
    cd.sync(ROSTER, "Cohort", "Course", [], NOW)
    body = fake.body_of("issue", "edit")
    assert cd._read_marker(body, cd._CLOCK, {}, ROSTER.state_key) == {}
    assert "Every entry in `classroom-config/students.csv` was usable" in body
    (close,) = fake.did("issue", "close")
    assert (
        "Every entry in students.csv is now usable"
        in close[close.index("--comment") + 1]
    )


# ------------------------------------------------------------------- the quiet window


def test_an_immediate_fault_is_never_held_overnight(gh):
    # A push at 02:00 leaves a file nobody can enrol from. Holding it until 07:00 trades
    # the one moment it is cheap to fix for a quieter night.
    fault = _fault()
    gh([])
    out = cd.sync(ROSTER, "Cohort", "Course", [fault], NIGHT)
    assert out.mail == {fault.key: cd.Severity.WARNING}


def test_a_scheduled_digest_still_holds(gh):
    fault = source_fault(fires=NIGHT + timedelta(hours=3))
    gh([])
    out = source_digest.sync("Cohort", "Course", [fault], NIGHT)
    assert out.mail == {}


def test_a_reminder_whose_mail_failed_is_owed_again_next_tick(gh):
    # The counter is written with the body, BEFORE the mail is attempted. A send that
    # failed has to put it back, or the 48-hour letter is attempted once, fails once and
    # is never owed again - the one hole in "un-recorded, not lost".
    fault = _fault()
    seen = {fault.key: (NOW - timedelta(days=3)).isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    gh(_open(fault, state=state, since=seen, sent=0))
    out = cd.sync(ROSTER, "Cohort", "Course", [fault], NOW)
    assert (out.reminder, out.reminder_was) == ("2 days", 0)

    # The mail did not go out. `hold` puts the counter back where it was.
    body = (
        cd.render_body(ROSTER, [fault], NOW, COHORT, state, seen)
        + "\n"
        + (cd._write_marker(cd._CLOCK, {"sent": 1}, ROSTER.state_key))
    )
    fake = gh([issue_row(7, ROSTER.title, body)])
    assert cd.hold(ROSTER, "Cohort", {fault.key: "warning"}, out.reminder_was) == 0
    put_back = fake.body_of("issue", "edit")
    assert cd._read_marker(put_back, cd._CLOCK, {}, ROSTER.state_key) == {"sent": 0}


# ------------------------------------------------------- one issue, several files in it


def _sheet(slug: str, lineno: int) -> ConfigFault:
    return ConfigFault(
        f"{slug} line {lineno}",
        "a mark on this line is not a number, so nothing for this unit is sent",
        file=f"grading_sheets/{slug}.yml",
        field="score_individual",
        lineno=lineno,
    )


def test_every_grading_sheet_is_one_issue_and_each_line_links_its_own_sheet():
    # A cohort marks half a dozen assignments at once, so an issue per sheet would be six
    # threads about one grader's afternoon - but the digest's label for the folder must
    # not become the link, or every line would point at the wrong file.
    body = cd.render_body(
        cd.GRADING_SHEETS, [_sheet("a1", 5), _sheet("a2", 9)], NOW, COHORT
    )
    assert "`classroom-config/grading_sheets/` has broken entries" in body
    assert "/grading_sheets/a1.yml#L5" in body
    assert "/grading_sheets/a2.yml#L9" in body
    assert "### needs fixing" in body  # no deadline: it is a line nobody can read


# ------------------------------------------- a hand-edited file that keeps the OTHER clock


def _spec_fault(fires) -> ConfigFault:
    """A `grading_config.yml` value that will not grade as written: a hand-edited file's
    fault with a MOMENT - the one it is used to grade at."""
    return ConfigFault(
        "assignments.a3",
        "`submit_via: emial` is not one of github/external - using `github`",
        fires=fires,
        field="submit_via",
        lineno=3,
        file="grading_config.yml",
        in_repo="assignment-3-f2026",
        in_org="Course-Org",
        ref="solution",
        fix_text="correct the value on the line above (allowed: github/external)",
    )


def test_the_grading_config_issue_names_the_file_without_claiming_it_is_here():
    # The issue is the COHORT's and the file is in the course org, on a template's
    # solution branch: `classroom-config/grading_config.yml` is a path that does not exist.
    body = cd.render_body(cd.GRADING_CONFIG, [_spec_fault(NOW)], NOW, COHORT)
    assert "`<assignment template>/grading_config.yml` has broken entries" in body


def test_a_fault_with_a_moment_is_filed_under_its_rung_whatever_file_it_is_in():
    # The clock is the FAULT's answer, never the issue's. Filed by age it would sit under
    # "unfixed for N days", which promises no deadline and hides the one it has.
    body = cd.render_body(
        cd.GRADING_CONFIG, [_spec_fault(NOW + timedelta(hours=8))], NOW, COHORT
    )
    assert "### URGENT (12h)" in body
    assert "unfixed for" not in body and "needs fixing" not in body
    assert "/assignment-3-f2026/blob/solution/grading_config.yml#L3" in body


def test_a_fault_with_a_moment_is_held_overnight_like_every_other_deadline(gh):
    # 02:00, and it can wait until 07:00 - unlike a line nobody can read, whose whole
    # value is being said while the person who pushed it is still at the keyboard.
    gh([])
    out = cd.sync(
        cd.GRADING_CONFIG,
        "Cohort",
        "Course",
        [_spec_fault(NIGHT + timedelta(hours=8))],
        NIGHT,
    )
    assert out.mail == {}


def test_a_fault_with_a_moment_earns_no_age_reminder(gh):
    # One clock per fault. Counting its days as well would be a second, contradictory
    # schedule for one thing to fix.
    fault = _spec_fault(NOW + timedelta(hours=8))
    seen = {fault.key: (NOW - timedelta(days=9)).isoformat()}
    state = cd._state_marker(cd.current_state([fault], NOW), seen)
    body = cd.render_body(cd.GRADING_CONFIG, [fault], NOW, COHORT, state, seen)
    gh([issue_row(7, cd.GRADING_CONFIG.title, body)])
    out = cd.sync(cd.GRADING_CONFIG, "Cohort", "Course", [fault], NOW)
    assert out.reminder is None


# ------------------------------------------------- the one digest that is not a cohort's


COURSE = cd.COURSE
COURSE_CTX = cd.Context("Course", "Course")


def _course_fault(file: str = "dsl-course.yml", field: str = "central_ref"):
    return ConfigFault(
        file,
        "this file is not valid YAML, so none of it is read",
        file=file,
        field=field,
        in_repo=".github",
    )


def test_the_course_digest_is_written_in_the_course_orgs_own_github(gh):
    # The repo is the DIGEST's answer, not the engine's: hard-wired to classroom-config
    # this issue would open in a repo the course org does not have.
    fake = gh([])
    cd.sync(COURSE, "Course", "Course", [_course_fault()], NOW)
    (create,) = fake.did("issue", "create")
    assert create[create.index("--repo") + 1] == "Course/.github"
    assert COURSE.title in create


def test_the_course_digest_falls_back_to_the_course_admin_team(gh):
    body = cd.render_body(COURSE, [_course_fault()], NOW, COURSE_CTX)
    assert "cc @Course/course-admin" in body
    assert "`.github/dsl-course.yml` has broken entries" in body
    assert "/docs/01-new-course-org.md" in body


def test_both_course_files_sit_in_the_one_issue(gh):
    # dsl-course.yml and the registry are one failure - either unreadable and the sync
    # walks past the whole course - so they are one issue, each fault citing its own file.
    faults = [_course_fault(), _course_fault("cohort-courses-pages.yml", "cohorts")]
    body = cd.render_body(COURSE, faults, NOW, COURSE_CTX)
    assert "`dsl-course.yml`" in body and "`cohort-courses-pages.yml`" in body


def test_the_course_digest_survives_the_very_file_it_reports_on(gh, monkeypatch):
    # The docs link asks the course org which tier it runs, which reads the dsl-course.yml
    # this issue is being written ABOUT. A malformed one raises out of the YAML loader, and
    # letting that through would silence the digest reporting exactly that.
    def unparseable(org):
        raise yaml.YAMLError("bad")

    monkeypatch.setattr(cd, "central_ref_for", unparseable)
    fake = gh([])
    out = cd.sync(COURSE, "Course", "Course", [_course_fault()], NOW)
    assert out.errors == 0 and fake.did("issue", "create")
