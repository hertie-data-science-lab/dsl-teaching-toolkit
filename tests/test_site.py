"""site.py schedule wiring: the semester website's rows take their dates and their types
from schedule.yml (not a synthesised weekly guess), joined to the released folders by
ordinal AND section - a week's lecture and its lab are separate rows. A wrong mapping here
silently mis-dates the whole schedule page, or hides a lab inside a lecture row."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import pytest
import yaml

from dsl_course import (
    course,
    discovery,
    gh_contents,
    ghcli,
    grades,
    schedule_plan,
    site,
    site_repo,
)
from dsl_course import schedule as schedule_mod
from dsl_course.schedule import (
    ArchiveRow,
    AssignmentEntry,
    Deploy,
    Event,
    Release,
    Schedule,
)
from dsl_course.site_repo import Link
from tests.conftest import BareOrigins, entry_links

UTC = ZoneInfo("UTC")

BERLIN = ZoneInfo("Europe/Berlin")
END_OF_TERM = date(2026, 12, 18)


@pytest.fixture(autouse=True)
def _individual_by_default(monkeypatch):
    """An assignment row names the repo shape a student looks for, and that now comes
    from the template's `grading_config.yml` - a course-org read the guard in conftest
    refuses. Individual is what an unanswered read gives anyway; the one test about the
    group shape sets its own."""
    monkeypatch.setattr(
        site, "load_grading_spec", lambda *a: grades.parse_grading_spec("")
    )


@pytest.fixture(autouse=True)
def _noacting_login(monkeypatch):
    """_team_people asks who the sync is authenticated as, and the real lookup shells out
    to `gh` (green on an authenticated dev box, red in tokenless CI). None excludes
    nobody, which is what every test here but the bot-card one wants."""
    monkeypatch.setattr(site_repo, "acting_login", lambda: None)


def _sched(releases: list[Release]) -> Schedule:
    return Schedule(releases=releases)


# A RELEASED row - non-empty sources, so these pin the released branch rather than the
# placeholder one (they read `[]` before the placeholder branch existed, which silently
# moved their subject).
RELEASED = [("materials", "lectures", "02_week-2")]


def _row(when, **kw):
    """The plan's view of a row - what `_lecture_entry` renders from. Built here so a test
    states only the plan fields it is actually about."""
    return schedule_plan.PlannedRow(when=when, **kw)


def test_lecture_entry_shows_real_time_from_a_datetime(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry(
        "Semester",
        "2",
        _row(datetime(2026, 9, 15, 14, 30, tzinfo=BERLIN)),
        RELEASED,
        hosted={},
    )
    assert "date: 2026-09-15T14:30:00" in md
    assert "not yet released" not in md


def test_lecture_entry_falls_back_to_0900_for_a_bare_date(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry(
        "Semester", "2", _row(date(2026, 9, 15)), RELEASED, hosted={}
    )
    assert "date: 2026-09-15T09:00:00" in md


def test_lecture_entry_renders_a_lab_row_as_its_own_type(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry(
        "Semester", "3", _row(date(2026, 9, 17)), RELEASED, "lab", hosted={}
    )
    assert "type: lab" in md
    assert 'title: "Lab 3"' in md
    assert "Session 3" not in md
    lec = site._lecture_entry(
        "Semester", "3", _row(date(2026, 9, 15)), RELEASED, hosted={}
    )
    assert "type: lecture" in lec and 'title: "Session 3"' in lec


def test_only_the_unreleased_row_carries_the_theme_flag(monkeypatch):
    # The prose says it, but a flag is what lets the theme badge or grey the row - and
    # what tells a placeholder apart from a released folder that holds no files.
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    assert "unreleased: true" not in site._lecture_entry(
        "Semester", "2", _row(date(2026, 9, 15)), RELEASED, hosted={}
    )
    assert "unreleased: true" in site._lecture_entry(
        "Semester", "2", _row(date(2026, 9, 15)), [], hosted={}
    )


def test_a_provisional_session_date_is_marked_on_both_kinds_of_row(monkeypatch):
    # `tbc:` was parsed on `releases:` and rendered nowhere: the plan carried it, the row
    # never wrote it and the lecture template never read it, so a faculty member marking a
    # class date provisional got a schedule that looked settled. Display-only, as it is on
    # every other block - and on the released and unreleased row alike, since it says
    # something about the DATE rather than about the materials.
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    for kind, sources in (("lecture", RELEASED), ("lab", RELEASED), ("lecture", [])):
        marked = site._lecture_entry(
            "Semester", "2", _row(date(2026, 9, 15), tbc=True), sources, kind, hosted={}
        )
        plain = site._lecture_entry(
            "Semester", "2", _row(date(2026, 9, 15)), sources, kind, hosted={}
        )
        assert "tbc: true" in marked
        assert "tbc" not in plain
        # Display only: the date the row shows - and the deploys fire on - is unchanged.
        assert "date: 2026-09-15T09:00:00" in marked


def test_event_entry_renders_a_display_only_schedule_row():
    e = Event("project-clinic", "", datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN))
    out = site._event_entry(e, END_OF_TERM)
    assert "type: special_event" in out
    # `title`, which the theme renders in the TITLE column - the EVENT column is where
    # every row type prints its KIND, and this row's kind is "Event"
    assert 'title: "Project Clinic"' in out  # prettified from the label
    assert "date: 2026-11-17T10:00:00" in out
    assert "name:" not in out
    titled = Event(
        "project-clinic",
        "Bring your data",
        datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN),
    )
    assert 'title: "Bring your data"' in site._event_entry(titled, END_OF_TERM)


def test_event_entry_renders_an_exam_as_an_exam_row():
    e = Event("mid-term", "MidTerm Exam", date(2026, 11, 3), type="exam")
    out = site._event_entry(e, END_OF_TERM)
    assert "type: exam" in out
    assert 'title: "MidTerm Exam"' in out
    assert "date: 2026-11-03T09:00:00" in out  # whole day -> the placeholder time
    assert "name:" not in out  # the exam row reads `title`, not `name`
    # And no invented body: the row used to carry "Details to be confirmed." whether or
    # not anything was.
    assert "to be confirmed" not in out


def test_event_entry_title_falls_back_to_the_prettified_label():
    e = Event("resit_exam", "", date(2026, 12, 20), type="exam")
    assert 'title: "Resit Exam"' in site._event_entry(e, END_OF_TERM)


def test_tbc_rows_render_with_theme_flags():
    # Undated (event_datetime: tbc): sortable end-of-term placeholder + dateless flag,
    # so the theme prints "TBC" instead of the placeholder date.
    undated = Event("guest-lecture", "Guest lecture", None, tbc=True)
    out = site._event_entry(undated, END_OF_TERM)
    assert "tbc: true" in out and "dateless: true" in out
    assert "date: 2026-12-18T09:00:00" in out
    # Provisionally dated (tbc: true): real date kept, marker only.
    dated = Event(
        "project-clinic", "", datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN), tbc=True
    )
    out = site._event_entry(dated, END_OF_TERM)
    assert "tbc: true" in out and "dateless" not in out
    assert "date: 2026-11-17T10:00:00" in out
    # Exams: same two shapes.
    out = site._event_entry(
        Event("resit", "Resit Exam", None, "exam", True), END_OF_TERM
    )
    assert "type: exam" in out and "dateless: true" in out
    out = site._event_entry(
        Event("mid-term", "MidTerm Exam", date(2026, 11, 3), "exam", True), END_OF_TERM
    )
    assert "tbc: true" in out and "dateless" not in out


def _archive_row(details: str | None = None, when: date = date(2027, 2, 16)):
    """The parsed `archive:` block these renderer tests hand in - the row whole, which is
    what the renderer takes."""
    return ArchiveRow(when=when, details=details)


def _said(out: str) -> object:
    """The archive row's `details:` as YAML loads it - the sentence, in the same front
    matter key every other row's prose goes into."""
    _, _, rest = out.partition("---\n")
    # rsplit, not partition: a `---` line INSIDE the block scalar is indented under its
    # key, and only the last one is the front matter's own terminator.
    return yaml.safe_load(rest.rsplit("---\n", 1)[0]).get("details")


def test_the_archive_row_is_a_special_event_that_says_what_freezes():
    said = "Everything here goes read-only. You keep read access."
    out = site._archive_entry(_archive_row(said), date(2026, 12, 20))
    assert "type: special_event" in out
    assert 'title: "Semester archived"' in out
    assert "date: 2027-02-16T09:00:00" in out
    assert "hide_time: true" in out  # a whole day, not a 09:00 appointment
    # What it SAYS is the semester's own sentence, in the key every row says things in.
    assert _said(out) == said
    # And nothing else: no body, so nothing renders twice.
    assert out.endswith("---\n")


def test_the_semesters_own_sentence_is_the_rows_details_and_not_its_title():
    # This is the sentence students read, and it goes where every other row's prose goes -
    # the Details column - rather than into the page body, which is what forced it onto one
    # line and made it the one `details:` that could not run to a paragraph.
    said = "We freeze on the 16th - your repos stay readable for ever."
    out = site._archive_entry(_archive_row(said), date(2027, 2, 1))
    assert _said(out) == said
    assert 'title: "Semester archived"' in out
    assert "date: 2027-02-16T09:00:00" in out


def test_a_sentence_that_looks_like_liquid_is_not_liquid():
    # Front matter is data, not a template, so the fence the body route needed is gone and
    # the braces survive exactly as typed. Unrendered either way - which is the point: a
    # malformed tag would otherwise fail the WHOLE site build.
    said = "Frozen {% raw-looking %} - ask {{ site.title }}."
    out = site._archive_entry(_archive_row(said), date(2027, 2, 1))
    assert _said(out) == said
    assert "{% raw %}" not in out


def test_the_sentence_can_ask_for_the_archive_date_by_name():
    # A date typed into the sentence as a literal goes stale the moment `archive.when`
    # moves or is left to its default; `{date}` cannot. Every occurrence is filled.
    out = site._archive_entry(
        _archive_row("Archived on {date}. Read-only from {date}."), date(2027, 2, 1)
    )
    assert _said(out) == "Archived on 2027-02-16. Read-only from 2027-02-16."


def test_a_sentence_without_the_token_is_left_alone():
    # Including its braces: this is faculty prose, not a format string, so anything but
    # the exact token survives verbatim.
    said = "We freeze in {other} words - nothing here is a placeholder."
    out = site._archive_entry(_archive_row(said), date(2027, 2, 1))
    assert _said(out) == said


def test_a_multi_paragraph_sentence_stays_multi_paragraph():
    # The whole point of moving it into `details:`: it is a block scalar like every other
    # row's, so a freeze notice may run to two paragraphs - and a line of its own that
    # reads `---` is indented inside the block rather than cutting the page in half.
    out = site._archive_entry(_archive_row("Frozen.\n\n---\n\nGone."), date(2027, 2, 1))
    assert _said(out) == "Frozen.\n\n---\n\nGone.\n"


def test_without_a_sentence_the_row_carries_none():
    # There is no default: the toolkit does not know what a freeze means for a given
    # semester's students, and a wrong reassurance is worse than none. The row still
    # renders - its title and its date - and the Updates box skips an empty bullet.
    out = site._archive_entry(_archive_row(), date(2027, 2, 1))
    assert out.endswith('title: "Semester archived"\n---\n')
    assert "read-only" not in out


def test_the_archive_row_only_reaches_the_updates_box_inside_its_window():
    # The box is where a student would actually notice it, and two weeks is long enough
    # to act on. Outside the window the row is still on the schedule, silently.
    when = date(2027, 2, 16)
    edge = when - schedule_mod.ARCHIVE_NOTICE
    said = "Everything here goes read-only on {date}."
    assert "announce: true" in site._archive_entry(_archive_row(said, when), edge)
    assert "announce" not in site._archive_entry(
        _archive_row(said, when), edge - timedelta(days=1)
    )


def test_the_window_closes_once_the_freeze_has_happened():
    # The upper bound alone left every past archive date announced for ever - and a date
    # in the past sorts nowhere near the top of a box ordered by date, so the bullet
    # would sit in the Updates box saying a freeze was coming that already came.
    when = date(2027, 2, 16)
    said = "Everything here goes read-only on {date}."
    assert "announce: true" in site._archive_entry(_archive_row(said, when), when)
    assert "announce" not in site._archive_entry(
        _archive_row(said, when), when + timedelta(days=1)
    )


def test_a_row_with_nothing_to_say_is_not_announced():
    # The Updates box captures each bullet inside its `limit: 7` loop and drops an empty
    # one afterwards, so an announced row with nothing to say did not simply render as
    # nothing: it spent a slot - the newest, this row sorting by its own future date - on
    # nothing, for the whole fortnight.
    when = date(2027, 2, 16)
    inside = when - timedelta(days=1)
    assert "announce" not in site._archive_entry(_archive_row(when=when), inside)
    assert "announce: true" in site._archive_entry(
        _archive_row("We freeze on {date}.", when), inside
    )


def test_term_date_entry_hides_the_placeholder_time():
    out = site._term_date_entry("Semester starts", date(2026, 9, 7))
    assert "type: term_date" in out
    assert "date: 2026-09-07T09:00:00" in out
    assert "hide_time: true" in out  # a term boundary is a whole day, not a 09:00 slot
    # The name is the row's TITLE. It used to be `name:`, which the theme prints in the
    # Event column, beside an always-empty Title cell - the one row that named itself in a
    # different column from every other.
    assert 'title: "Semester starts"' in out
    assert "name:" not in out and "description:" not in out


def test_assignment_entry_dates_the_released_row_from_the_handout(monkeypatch):
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nBrief."
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 23, tzinfo=BERLIN),
    )
    # the entry's own row is the "released!" row; the due row lives in due_event
    assert "date: 2026-09-22T09:00:00" in out.split("due_event:")[0]
    assert "    date: 2026-10-13T23:59:59" in out
    # the theme's due row is already labelled "due", so its title just names the
    # assignment - the same identifier the out-row above carries
    assert '    title: "Assignment 1"' in out


def test_assignment_entry_falls_back_to_the_due_date_without_a_handout(monkeypatch):
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-2-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-2"}),
    )
    assert out.count("date: 2026-11-10T23:59:00") == 2  # both rows on the due date


def test_an_unhanded_out_assignment_is_a_placeholder(monkeypatch):
    # The template repo exists from the day faculty write the assignment; publishing its
    # README on sight put the whole brief on the PUBLIC semester site weeks before hand-out,
    # while the scheduler was still correctly holding the student repos back. So the
    # CONTENT is embargoed - the README is not read at all - but the entry still exists,
    # and with it the two schedule rows. Withholding those left an assignment students
    # could read about in schedule.yml missing from the schedule that publishes it.
    def _no_reads(*a, **k):
        raise AssertionError("the embargoed README must not be read at all")

    monkeypatch.setattr(site, "get_file_content", _no_reads)
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert "handout_pending: true" in out
    # both rows, on their real dates - the hand-out row and the due row
    assert "date: 2026-09-22T09:00:00" in out.split("due_event:")[0]
    assert "    date: 2026-10-13T23:59:59" in out
    # the plan-side name only, never the README's own title
    assert 'title: "Assignment 1"' in out
    assert "**Assignment 1 is not yet released**" in out


def test_a_passed_handout_inlines_the_brief(monkeypatch):
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),  # the moment itself is released
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_a_manual_handout_releases_the_brief_with_no_date_pinned(monkeypatch):
    # The manual button's documented mode pins no handout_datetime at all, so the plan
    # cannot say this went out - the frozen semester template repo it creates is what says
    # so. Gating on the plan alone published these briefs from the day the template
    # existed, which is the whole bug.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 2\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-2-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-2"}),
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_an_assignment_with_no_handout_on_record_withholds_its_brief(monkeypatch):
    # Neither signal fires: no semester template repo, no pin. Withholding the CONTENT is
    # the safe direction - the brief appears the moment either says it went out - but the
    # row is still the plan's, and the plan is already public.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 2\nThe brief."
    )
    out = site._assignment_entry(
        "Course", "Semester-f2026", "assignment-2-f2026", date(2026, 11, 10)
    )
    assert "handout_pending: true" in out
    assert "The brief." not in out
    assert 'title: "Assignment 2"' in out


def test_a_released_assignment_links_the_semester_repo_not_the_course_org(monkeypatch):
    # The two halves of an assignment live in different orgs, and this took only the
    # course one - so the page told students their repo was "in `<course-org>`'s semester
    # org": the org they cannot open, and not the one they can.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        date(2026, 10, 13),
        handed_out=frozenset({"assignment-1"}),
    )
    # both levels: the theme reaches the due row via `map: "due_event"`, which cannot
    # see the parent entry's fields
    assert out.count("Semester-f2026/repositories?q=assignment-1-") == 2
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2
    assert "Course" not in out.split("---")[1]  # the course org names no student repo


def test_the_plans_title_is_the_assignments_name_and_beats_the_readme(monkeypatch):
    # Declared in schedule.yml, so it can appear BEFORE hand-out - the README it otherwise
    # comes from is embargoed until then.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1 - something else"
    )
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                title="Fraud detection",
            )
        }
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        date(2026, 10, 13),
        found=("assignment-1", sched.assignments["assignment-1"]),
        handed_out=frozenset({"assignment-1"}),
    )
    assert 'title: "Assignment 1"' in out  # the identifier is always the slug's
    assert out.count('subtitle: "Fraud detection"') == 2  # entry + due row


def test_a_declared_name_that_repeats_the_identifier_is_trimmed(monkeypatch):
    # Faculty repeat the identifier in a README heading and in a `releases:` title alike,
    # so printing either whole under its identifier read "Assignment 1 / Assignment 1 -
    # linear regression..." and "Lab 1 / Lab 1". Both dash characters in live sources are
    # handled.
    assert (
        site.row_name("Assignment 1 - linear regression", "Assignment 1")
        == "linear regression"
    )
    assert (
        site.row_name("Assignment 1 \u2014 Introduce Yourself", "Assignment 1")
        == "Introduce Yourself"
    )
    # a heading that is the name already survives whole
    assert (
        site.row_name("Group project - a report", "Assignment 3 Project")
        == "Group project - a report"
    )
    # and `Assignment 10` is not `Assignment 1` plus a name of "0"
    assert site.row_name("Assignment 10 revisited", "Assignment 1") == (
        "Assignment 10 revisited"
    )
    # a session's declared title gets the same trim
    assert site.row_name("Lab 1", "Lab 1") == ""
    assert site.row_name("Session 3 - Probability", "Session 3") == "Probability"


def test_a_group_assignment_names_the_team_repo_shape(monkeypatch):
    # A group assignment fans out one repo per team, so `<your-handle>` is the wrong thing
    # to go looking for.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Group project\nThe brief."
    )
    monkeypatch.setattr(
        site, "load_grading_spec", lambda *a: grades.parse_grading_spec("type: group\n")
    )
    sched = Schedule(
        assignments={
            "assignment-3": AssignmentEntry(
                course_source_repo="assignment-3-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
            )
        }
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-3-f2026",
        date(2026, 10, 13),
        found=("assignment-3", sched.assignments["assignment-3"]),
        handed_out=frozenset({"assignment-3"}),
    )
    assert 'repo_name: "assignment-3-<your-team>"' in out


# A group assignment whose teams the students pick themselves - the one shape whose
# handout provisions nothing at all until a team exists.
SELF_SELECT_GROUP = "type: group\nteam_formation: self_select\nmax_team_size: 4\n"
# Inside the plan below's window (handout 22 Sep, grading pin 20 Oct), and outside it.
FORMING = datetime(2026, 9, 30, 12, 0, tzinfo=BERLIN)
SHUT = datetime(2026, 10, 21, 12, 0, tzinfo=BERLIN)


def _team_entry(monkeypatch, config: str, *, now: datetime, teams_csv="", **kw) -> str:
    """One assignment's page, off the `grading_config.yml` text `config` and a plan that
    hands it out on 22 September and freezes it on 20 October - so `now` alone decides
    which side of the team-formation window the page is rendered on.

    `teams_csv` is the semester's private teams.csv, as text, so the table of teams the page
    prints goes through the real parser the rest of the toolkit reads that file with - or a
    reader of its own, for the page rendered against a file that could not be read."""
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Group project\nThe brief."
    )
    monkeypatch.setattr(
        site, "load_grading_spec", lambda *a: grades.parse_grading_spec(config)
    )
    monkeypatch.setattr(
        site.teams,
        "_teams_text",
        teams_csv if callable(teams_csv) else lambda org: teams_csv or None,
    )
    sched = Schedule(
        assignments={
            "assignment-3": AssignmentEntry(
                course_source_repo="assignment-3-f2026",
                handout_datetime=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                grading_datetime=datetime(2026, 10, 20, 9, 0, tzinfo=BERLIN),
            )
        }
    )
    entry = sched.assignments["assignment-3"]
    return site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-3-f2026",
        entry.due_datetime,
        handout=entry.handout_datetime,
        found=("assignment-3", entry),
        now=now,
        sched=sched,
        **kw,
    )


def test_an_assignment_waiting_on_its_teams_asks_for_one_instead(monkeypatch):
    # The handout of a self-select group assignment parks until a team exists, so the pin
    # published the brief - correctly, the assignment IS out - and beside it a `repo_url`
    # into the org's repo list filtered to an assignment that had created no repos. A
    # student pressed "Open the submission repo", got an empty list, and the page said
    # nothing at all about the one thing they could do about it.
    out = _team_entry(monkeypatch, SELF_SELECT_GROUP, now=FORMING)
    # The brief stays out. It is what a team would be formed OVER.
    assert "The brief." in out
    assert "handout_pending" not in out
    # The address STAYS, at both levels. Hiding it for the length of the window would
    # punish the students who acted first: a team formed on day one owns its repo, and
    # GitHub filters that listing by what the reader can actually see - so it is right for
    # them and merely empty for everyone else, which is what the invitation explains.
    assert out.count("repo_url:") == 2
    assert out.count('repo_name: "assignment-3-<your-team>"') == 2
    assert (
        'team_join_url: "https://github.com/Semester-f2026/welcome/issues/new/choose"'
        in out
    )
    # The cap the Join-team form enforces, and the day it stops accepting - the same date
    # the lock file gives that form's refusal to name, SPOKEN as the mail and the refusal
    # speak it (`grades.spoken_day`).
    assert 'team_join_cap: "4"' in out
    assert 'team_join_closes: "20th Oct"' in out


def test_the_teams_that_exist_are_listed_beside_the_invitation(monkeypatch):
    # The decision the callout asks for - start a team, or join one - cannot be taken
    # without knowing what is already there, and teams.csv is private. Names and counts, so
    # the page answers it without publishing who is in which team.
    out = _team_entry(
        monkeypatch,
        SELF_SELECT_GROUP,
        now=FORMING,
        teams_csv=(
            "assignment,team,github_handle\n"
            "assignment-3,team-alpha,ada-l\n"
            "assignment-3,team-alpha,bo-b\n"
            "assignment-3,team-bravo,cy-c\n"
        ),
    )
    assert "teams:\n" in out
    assert '  - name: "team-alpha"\n    members: 2\n    cap: 4\n' in out
    assert '  - name: "team-bravo"\n    members: 1\n    cap: 4\n' in out


def test_no_handle_from_teams_csv_reaches_the_public_page(monkeypatch):
    # The semester site is PUBLIC. A team name is student-chosen and public by construction;
    # who is in it is not, and neither is the `<slug>-<handle>` repo it would name.
    out = _team_entry(
        monkeypatch,
        SELF_SELECT_GROUP,
        now=FORMING,
        teams_csv="assignment,team,github_handle\nassignment-3,team-alpha,ada-l\n",
    )
    assert "ada-l" not in out


def test_a_window_with_no_teams_yet_prints_no_table(monkeypatch):
    # Day one, which is most of what this page is for: the invitation goes out and there is
    # nothing to list. `teams:` is its own presence test, so the layout renders no empty
    # table rather than a heading over nothing.
    out = _team_entry(monkeypatch, SELF_SELECT_GROUP, now=FORMING)
    assert "team_join_url" in out and "teams:" not in out


def test_a_teams_csv_that_cannot_be_read_still_renders_the_page(monkeypatch, capsys):
    # teams.csv is student-written and lives behind an API. Neither a broken header nor a
    # rate limit may take down the render of a semester's whole website - the callout is the
    # part that matters.
    def boom(org):
        raise RuntimeError("API rate limit exceeded")

    out = _team_entry(monkeypatch, SELF_SELECT_GROUP, now=FORMING, teams_csv=boom)
    assert "team_join_url" in out and "teams:" not in out
    assert "rate limit" in capsys.readouterr().err


def test_the_invitation_goes_when_the_window_does(monkeypatch):
    # Past the grading pin there is nothing left to form a team for - the snapshot has
    # frozen - so the page is exactly the page it always was.
    out = _team_entry(monkeypatch, SELF_SELECT_GROUP, now=SHUT)
    assert "team_join" not in out
    assert out.count("Semester-f2026/repositories?q=assignment-3-") == 2


def test_a_group_assignment_not_yet_handed_out_is_still_only_pending(monkeypatch):
    # Before the hand-out the window has not opened, and the student could not act on an
    # invitation if it were there: the brief they would be teaming up over is embargoed.
    # So the page keeps the one sentence it has always shown.
    out = _team_entry(
        monkeypatch, SELF_SELECT_GROUP, now=datetime(2026, 9, 21, tzinfo=BERLIN)
    )
    assert "handout_pending: true" in out
    assert "team_join" not in out
    assert "The brief." not in out


def test_an_individual_assignment_is_never_asked_to_form_a_team(monkeypatch):
    # The window is a fact about the schedule alone, so every assignment in the plan has
    # one. What decides whether it MEANS anything is the shape, off the template's own
    # grading_config.yml - and an individual assignment has no teams to form.
    out = _team_entry(monkeypatch, "type: individual\n", now=FORMING)
    assert "team_join" not in out
    assert out.count("Semester-f2026/repositories?q=assignment-3-") == 2


def test_an_allocated_group_assignment_asks_nobody_to_form_a_team(monkeypatch):
    # `team_formation: assigned` means the teaching team writes teams.csv and the
    # Join-team form refuses every request. Pointing a semester at a form that will refuse
    # them is worse than the missing button this replaces.
    out = _team_entry(
        monkeypatch, "type: group\nteam_formation: assigned\n", now=FORMING
    )
    assert "team_join" not in out
    assert out.count("Semester-f2026/repositories?q=assignment-3-") == 2


def test_the_invitation_does_not_wait_for_the_first_team(monkeypatch):
    # Whether a team exists is ONE answer for the whole semester - `handed_out` carries this
    # assignment's name only once the handout has actually provisioned, which for a group
    # assignment means a team formed. Keying the invitation on that would take it away
    # from every student still looking for a team the moment the first one was agreed.
    alone = _team_entry(monkeypatch, SELF_SELECT_GROUP, now=FORMING)
    formed = _team_entry(
        monkeypatch,
        SELF_SELECT_GROUP,
        now=FORMING,
        handed_out=frozenset({"assignment-3"}),
    )
    assert alone == formed
    assert "team_join_url" in formed


def _entry_for(monkeypatch, config: str, **kw) -> str:
    """One assignment's page, off the `grading_config.yml` text `config` and a README the
    read never leaves the process for."""
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Moodle essay\nThe brief."
    )
    monkeypatch.setattr(
        site, "load_grading_spec", lambda *a: grades.parse_grading_spec(config)
    )
    return site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        **kw,
    )


def test_an_assignment_handed_in_off_github_names_no_repo_at_all(monkeypatch):
    # `submit_via: external` means Moodle, Kaggle or in class, and the handout creates no
    # repo for it. The page and the due row said "push to `main`" for these too, because
    # the theme printed that off `repo_url` alone, and then named a repo that does not
    # exist. At BOTH levels, since the due row is a sub-hash that cannot see its parent's.
    out = _entry_for(
        monkeypatch, "submit_via: external\n", handed_out=frozenset({"assignment-1"})
    )
    assert out.count('submit_shape: "external"') == 2
    assert "repo_name" not in out and "repo_url" not in out


def test_an_external_assignment_carries_the_address_it_is_handed_in_at(monkeypatch):
    # `submit_url` is the one thing the toolkit is ever told about a handover it does not
    # see. The host rides along so the button can say where it goes before it is pressed.
    out = _entry_for(
        monkeypatch,
        "submit_via: external\nsubmit_url: https://moodle.example.edu/x?id=7\n",
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count('submit_url: "https://moodle.example.edu/x?id=7"') == 2
    assert out.count('submit_host: "moodle.example.edu"') == 2


def test_a_pending_external_assignment_offers_nowhere_to_submit_yet(monkeypatch):
    # The address is a place to go NOW, so - like `repo_url` - it waits until the brief
    # that explains what to take there is out. The shape itself is the plan's and is
    # written either way.
    out = _entry_for(
        monkeypatch,
        "submit_via: external\nsubmit_url: https://moodle.example.edu/x?id=7\n",
        handout=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert out.count('submit_shape: "external"') == 2
    assert "submit_url" not in out
    assert "is not yet released** - the brief appears here when it is." in out


def test_a_public_assignment_says_so_at_both_levels(monkeypatch):
    # Who may READ the repo is part of its shape, and the shape is ONE word at both levels
    # - the due row is a sub-hash that cannot see its parent's fields. The theme `case`s on
    # it: a student has to know the repo is world-readable BEFORE their first push, not
    # from the brief afterwards.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A1\nThe brief.")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("visibility: public\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count('submit_shape: "assignment-repo-public"') == 2
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2


def test_a_pending_public_assignment_names_the_repo_it_will_make(monkeypatch):
    # The shape is the plan's and is known before anything ships, so it is written while
    # the assignment is pending too - and the placeholder line says `public` where it says
    # `private` for every other assignment, rather than promising the wrong thing.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("visibility: public\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handout=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert out.count('submit_shape: "assignment-repo-public"') == 2
    assert "your public `assignment-1-<your-handle>` repo appears when it is." in out


def test_a_student_choice_assignment_says_so_at_both_levels(monkeypatch):
    # The shape word is what the theme `case`s on, and the due row is a sub-hash that
    # cannot see its parent's fields - so both levels carry it, kebab-cased like every
    # other shape however the config spells the value.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A1\nThe brief.")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("visibility: student_choice\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count('submit_shape: "assignment-repo-student-choice"') == 2
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2


def test_a_pending_student_choice_assignment_promises_a_private_repo(monkeypatch):
    # The handout creates a PRIVATE repo; `student_choice` is a rule about who may change
    # that afterwards. The placeholder line would otherwise promise a semester their
    # "student_choice repo".
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("visibility: student_choice\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handout=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert out.count('submit_shape: "assignment-repo-student-choice"') == 2
    assert "your private `assignment-1-<your-handle>` repo appears when it is." in out


def test_an_external_assignments_shape_names_no_visibility(monkeypatch):
    # A visibility describes a repo and this shape creates none, so its shape is the bare
    # word: a page that carried a visibility would describe something nobody made.
    out = _entry_for(
        monkeypatch, "submit_via: external\n", handed_out=frozenset({"assignment-1"})
    )
    assert out.count('submit_shape: "external"') == 2
    assert "visibility" not in out


@pytest.mark.parametrize(
    ("config", "rule"),
    [
        (
            "late_window_days: 7\nlate_penalty_per_day: 10%\n",
            "10% per day, up to 7 days",
        ),
        ("late_window_days: 7\n", "accepted up to 7 days late"),
        ("late_window_days: 0\n", "not accepted after the deadline"),
        ("", "10% per day, up to 10 days"),
    ],
)
def test_the_page_carries_the_late_rule_the_assignment_declares(
    monkeypatch, config, rule
):
    # The rule is the assignment's own (`grading_config.yml`), so the page prints what
    # this assignment's own cutoff will actually do - `course.late_rule`, the same
    # sentence wherever the toolkit spells the rule rather than the date. A file that
    # declares neither setting is graded by the Hertie standard and the page says so;
    # `late_window_days: 0` is the assignment that takes nothing after the deadline.
    out = _entry_for(monkeypatch, config, handed_out=frozenset({"assignment-1"}))
    assert f'late_rule: "{rule}"' in out


def test_an_assignment_handed_in_off_github_carries_no_late_rule(monkeypatch):
    # Nothing is TIMED there: no repo is created, so no commit is pinned, no day is
    # counted and no penalty is ever applied (`course.collects_commits`). The key is the
    # theme's gate, so writing one would put a rule about a deadline this toolkit does not
    # hold on the page - and the brief is what says what the hand-in service does.
    out = _entry_for(
        monkeypatch,
        "submit_via: external\nlate_window_days: 7\nlate_penalty_per_day: 10%\n",
        handed_out=frozenset({"assignment-1"}),
    )
    assert "late_rule" not in out


def test_the_late_rule_is_the_pages_alone_and_not_the_due_rows(monkeypatch):
    # The due row is a glance at WHEN and WHERE; the rule qualifies an answer the row does
    # not give, and the page's callout is where that answer is.
    out = _entry_for(
        monkeypatch,
        "late_window_days: 7\nlate_penalty_per_day: 10%\n",
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count("late_rule:") == 1
    assert "late_rule" not in out.split("due_event:")[1]


def test_every_shape_that_hands_out_a_repo_carries_its_note_on_the_page_alone(
    monkeypatch,
):
    # The aside the layout prints under the brief - one text per shape, off
    # `course.SHAPE_NOTES`, so the page and the repo's own About line say the same words.
    # The PAGE alone, like the late rule: the due row is a glance at when and where.
    for config, shape in (
        ("", "assignment-repo-private"),
        ("visibility: public\n", "assignment-repo-public"),
        ("visibility: student_choice\n", "assignment-repo-student-choice"),
        ("submit_via: shared_dropbox_repo\n", "shared-dropbox-repo"),
    ):
        out = _entry_for(monkeypatch, config, handed_out=frozenset({"assignment-1"}))
        assert f'shape_note: "{course.shape_note(shape)}"\n' in out
        assert out.count("shape_note:") == 1
        assert "shape_note" not in out.split("due_event:")[1]


def test_a_pending_assignment_carries_no_shape_note_yet(monkeypatch):
    # The box would otherwise sit above the "not handed out yet" line, warning about a repo
    # that does not exist.
    out = _entry_for(monkeypatch, "visibility: public\n", handed_out=frozenset())
    assert "shape_note" not in out


def test_a_shape_that_hands_out_no_repo_carries_no_note(monkeypatch):
    # `external` is the one shape left without a note: the work is handed in somewhere
    # else, so there is no repo for a sentence about who can read it to be about.
    out = _entry_for(
        monkeypatch, "submit_via: external\n", handed_out=frozenset({"assignment-1"})
    )
    assert "shape_note" not in out


def test_the_cutoff_sentence_is_the_pages_alone_and_never_the_external_ones(
    monkeypatch,
):
    # What is marked, in the words the repo's own About line uses (`course.CUTOFF_SENTENCE`
    # - one text, because a student reads the two minutes apart). Gated like the late rule:
    # `external` pins no commit, so there is no `main` for a cutoff to be read off.
    out = _entry_for(monkeypatch, "", handed_out=frozenset({"assignment-1"}))
    assert f'cutoff_sentence: "{course.CUTOFF_SENTENCE}"\n' in out
    assert out.count("cutoff_sentence:") == 1
    assert "cutoff_sentence" not in out.split("due_event:")[1]
    off_github = _entry_for(
        monkeypatch, "submit_via: external\n", handed_out=frozenset({"assignment-1"})
    )
    assert "cutoff_sentence" not in off_github


def test_the_page_carries_the_total_the_questions_add_up_to(monkeypatch):
    # What the assignment is out of is the assignment's own (`questions:` in
    # grading_config.yml), summed by the one helper the gradebook sums it with
    # (`grades.total_points`) - so the page and a student's gradebook cannot print two
    # different totals. The page alone, like the late rule: the due row is a glance at
    # WHEN and WHERE.
    out = _entry_for(
        monkeypatch,
        "questions:\n  Q1: 15\n  Q2: 10\n",
        handed_out=frozenset({"assignment-1"}),
    )
    assert 'max_points: "25"' in out
    assert out.count("max_points:") == 1
    assert "max_points" not in out.split("due_event:")[1]


@pytest.mark.parametrize(
    "config",
    [
        # No `questions:` at all: the assignment declares no maxima, so there is no total.
        "",
        # Declared, but not as numbers - a course may mark `Q1` against a rubric. Nothing
        # adds up, and a page that printed "Worth points" would be worse than no line.
        "questions:\n  Q1: see rubric\n  Q2: 10\n",
    ],
)
def test_an_assignment_that_declares_no_total_carries_no_points_line(
    monkeypatch, config
):
    # The key's presence is the theme's gate, so an absent key is an absent line.
    out = _entry_for(monkeypatch, config, handed_out=frozenset({"assignment-1"}))
    assert "max_points" not in out


def test_an_assignment_handed_in_on_github_carries_no_such_flag(monkeypatch):
    # The default, and the wording the theme has always printed: the repo IS the
    # submission, so nothing about the page changes.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A1\nThe brief.")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("submit_via: github\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count('submit_shape: "assignment-repo-private"') == 2
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2


def test_a_definition_that_cannot_be_read_leaves_the_github_wording(monkeypatch):
    # The read goes to the course org over the network and the whole site sync sits under
    # a cron, so a template with no `solution` branch must leave the page exactly as it
    # was rather than flip it to "handed in outside GitHub".
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A1\nThe brief.")
    monkeypatch.setattr(site, "load_grading_spec", grades.load_grading_spec)
    monkeypatch.setattr(
        grades, "_grading_text", lambda *a: (_ for _ in ()).throw(RuntimeError("404"))
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
    )
    assert out.count('submit_shape: "assignment-repo-private"') == 2
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2


def test_a_pending_assignment_links_no_repo(monkeypatch):
    # Nothing exists at the other end of the link yet, so the placeholder names the shape
    # to expect and stops there.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert "repo_url" not in out
    # the SHAPE is still named - it is the plan's, and known before anything ships
    assert out.count('repo_name: "assignment-1-<your-handle>"') == 2
    assert "`assignment-1-<your-handle>` repo appears when it is" in out


def test_an_early_manual_release_beats_a_pin_still_in_the_future(monkeypatch):
    # Faculty pinned a later date, then released early. The repos exist, so the brief is
    # already with the students; the site must not go on claiming it is embargoed.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 10, 20, 14, 0, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
        now=datetime(2026, 10, 10, tzinfo=BERLIN),
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_handed_out_keys_on_the_semester_dest_repo_not_the_slug(monkeypatch):
    # assign.py freezes the semester template under `semester_dest_repo` when an entry renames
    # it, so the gate must look the assignment up under the same name it was created with.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                semester_dest_repo="homework-1",
            )
        }
    )
    args = ("Course", "Semester-f2026", "assignment-1-f2026", date(2026, 10, 13))
    found = ("assignment-1", sched.assignments["assignment-1"])
    assert "The brief." in site._assignment_entry(
        *args, found=found, handed_out=frozenset({"homework-1"})
    )
    # the slug is NOT the name it was frozen under, so it must not open the gate
    withheld = site._assignment_entry(
        *args, found=found, handed_out=frozenset({"assignment-1"})
    )
    assert "handout_pending: true" in withheld
    assert "The brief." not in withheld


def test_assignment_dates_read_the_schedule():
    from dsl_course.schedule import AssignmentEntry

    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
            )
        }
    )
    found = schedule_mod.entry_for_repo(sched, "assignment-1-f2026")
    due, handout = site._assignment_dates(found, date(2026, 1, 1))
    assert due == datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN)
    assert handout == datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN)
    # unscheduled: the synthesised fallback, and no handout row
    assert site._assignment_dates(None, date(2026, 1, 1)) == (
        date(2026, 1, 1),
        None,
    )


def _plan(
    monkeypatch,
    tmp_path,
    sched: Schedule,
    sources=(),
    assignments=(),
    files=None,
    handed_out=(),
):
    """Run sync_site against a faked org and return the _SitePlan it built. `files` fakes
    the per-source file listing (default: every source is empty)."""
    captured: dict = {}
    monkeypatch.setattr(
        site,
        "sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    # ONE semester listing answers both of the build's questions of the org. Everything in
    # it carries the handed-out topic, so it names the templates and no content repos.
    monkeypatch.setattr(
        site,
        "list_org_repos",
        lambda org: [
            {"name": n, "topics": ["assignment-template"]} for n in handed_out
        ],
    )
    monkeypatch.setattr(
        site, "discover_release_sources", lambda org, repos: list(sources)
    )
    monkeypatch.setattr(site, "discover_assignments", lambda org: list(assignments))
    monkeypatch.setattr(site, "yaml_file", lambda *a: {})
    monkeypatch.setattr(site.schedule, "load", lambda org: sched)
    monkeypatch.setattr(site, "people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(
        site,
        "_session_files",
        files or (lambda org, repo, subpath, folder, hosted: []),
    )
    # The memoised tree, which `_session_links` reads the default branch from to build its
    # folder-link URLs. Stubbed even where `_session_files` is faked: without it the fake
    # covers the file list but the branch lookup still reaches GitHub, which passes on an
    # authenticated dev box and fails in CI.
    monkeypatch.setattr(site, "_repo_tree", cache(lambda org, repo: ("main", ())))
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    assert site.sync_site("Course-Org", "Semester-f2026") == 0
    return captured["plan"]


def test_the_build_lists_the_semester_once_for_both_of_its_questions(
    monkeypatch, tmp_path
):
    # "Which repos hold released content" and "which assignments have gone out" are two
    # questions about one listing; each paid for its own full paginated walk of the org.
    listed: list[str] = []
    captured: dict = {}
    monkeypatch.setattr(
        site,
        "sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(
        site,
        "list_org_repos",
        lambda org: (
            listed.append(org)
            or [
                {"name": "materials", "topics": []},
                {"name": "assignment-1", "topics": ["assignment-template"]},
            ]
        ),
    )
    seen_content: list[list[str]] = []
    monkeypatch.setattr(
        site,
        "discover_release_sources",
        lambda org, repos: seen_content.append(repos) or [],
    )
    monkeypatch.setattr(site, "discover_assignments", lambda org: [])
    monkeypatch.setattr(site, "yaml_file", lambda *a: {})
    monkeypatch.setattr(site.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(site, "people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")

    assert site.sync_site("Course-Org", "Semester-f2026") == 0
    assert listed == ["Semester-f2026"], "the semester was listed twice for one build"
    # The same listing, read two ways: the templated repo is a hand-out, not content.
    assert seen_content == [["materials"]]


def test_semester_site_links_back_to_the_semester_org(monkeypatch, tmp_path):
    # The footer's GitHub link (site.github_org) is the semester site's only click-back; it
    # must point at THIS semester org, not the template default or the course org.
    plan = _plan(monkeypatch, tmp_path, Schedule())
    assert plan.config["github_org"] == "Semester-f2026"


def test_a_mixed_week_becomes_a_lecture_row_and_a_lab_row(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "lecture-2",
                    datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "lectures/02_week-2", "materials", None)],
                ),
                Release(
                    "lab-2",
                    datetime(2026, 9, 10, 14, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "labs/02_week-2", "materials", None)],
                ),
            ]
        ),
        sources=[
            ("materials", "lectures", "02_week-2", 2),
            ("materials", "readings", "02_week-2", 2),
            ("materials", "labs", "02_week-2", 2),
        ],
    )
    lectures = plan.collections["_lectures"]
    assert sorted(lectures) == ["lab-02.md", "session-02.md"]
    assert "type: lecture" in lectures["session-02.md"]
    assert "date: 2026-09-08T10:00:00" in lectures["session-02.md"]
    assert "type: lab" in lectures["lab-02.md"]
    assert "date: 2026-09-10T14:00:00" in lectures["lab-02.md"]  # its OWN release time


def test_course_description_flows_from_course_metadata_into_config(
    monkeypatch, tmp_path
):
    # course_description is declared once in the course org's dsl-course.yml and pushed to
    # every semester site. Undeclared, it must not be written at all - the site repo keeps
    # whatever blurb it has.
    captured = {}
    monkeypatch.setattr(
        site,
        "sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site, "list_org_repos", lambda org: [])
    monkeypatch.setattr(site, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site, "discover_assignments", lambda org: [])
    monkeypatch.setattr(site.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(site, "people_yaml", lambda *a, **k: "people: []\n")

    monkeypatch.setattr(site, "yaml_file", lambda *a: {})
    assert site.sync_site("Course-Org", "Semester-f2026") == 0
    assert "course_description" not in captured["plan"].config

    monkeypatch.setattr(
        site, "yaml_file", lambda *a: {"course_description": "Nets, from 0."}
    )
    assert site.sync_site("Course-Org", "Semester-f2026") == 0
    cfg = site_repo._replace_config_scalar(
        'course_name: "x"\ncourse_description: "old"\ncourse_code: "y"\n',
        "course_description",
        captured["plan"].config["course_description"],
    )
    assert yaml.safe_load(cfg)["course_description"] == "Nets, from 0."
    assert yaml.safe_load(cfg)["course_code"] == "y"  # neighbours untouched


def test_replacing_a_config_scalar_writes_one_line_over_a_block_scalar():
    # A faculty `>` block in dsl-course.yml, and/or one already in _config.yml: either way
    # the result must stay valid YAML on one line, its body not stranded as loose text.
    cfg = site_repo._replace_config_scalar(
        "course_description: >\n  an old\n  folded blurb\ncourse_code: 'y'\n",
        "course_description",
        "line one\nline two\n",
    )
    assert yaml.safe_load(cfg) == {
        "course_description": "line one line two",
        "course_code": "y",
    }


def test_site_still_builds_when_schedule_yml_does_not_parse(
    monkeypatch, tmp_path, capsys
):
    # The incident: unparseable schedule.yml crashed schedule.load, which crashed BOTH the
    # hourly Scheduled release AND Sync site - so the site kept the template's "Fall 2025"
    # placeholders. schedule.load now degrades to an empty Schedule, and the sync must
    # complete: course identity + inferred semester land, dates are synthesised.
    from tests.test_schedule import MALFORMED_SCHEDULE

    captured = {}
    monkeypatch.setattr(
        site,
        "sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site, "list_org_repos", lambda org: [])
    monkeypatch.setattr(site, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site, "discover_assignments", lambda org: [])
    monkeypatch.setattr(site, "yaml_file", lambda *a: {"course_name": "Deep Learning"})
    monkeypatch.setattr(site, "people_yaml", lambda *a, **k: "people: []\n")
    # the REAL schedule.load, fed the malformed file
    monkeypatch.setattr(
        site.schedule, "get_file_content", lambda org, repo, path: MALFORMED_SCHEDULE
    )

    assert site.sync_site("Course-Org", "Semester-f2026") == 0

    plan = captured["plan"]
    assert plan.config["course_name"] == "Deep Learning"
    assert plan.config["course_semester"] == "Fall 2026"
    # no schedule data: the exam rows fall back to the synthesised mid/end-term stubs
    assert {"midterm.md", "final.md"} <= set(plan.collections["_events"])
    assert "is NOT valid YAML" in capsys.readouterr().err


def test_a_week_with_only_one_kind_gets_only_that_row(monkeypatch, tmp_path):
    lab_only = _plan(
        monkeypatch,
        tmp_path,
        Schedule(),
        sources=[("materials", "labs", "03_week-3", 3)],
    )
    assert sorted(lab_only.collections["_lectures"]) == ["lab-03.md"]
    lecture_only = _plan(
        monkeypatch,
        tmp_path,
        Schedule(),
        sources=[("materials", "lectures", "04_week-4", 4)],
    )
    assert sorted(lecture_only.collections["_lectures"]) == ["session-04.md"]


def test_the_whole_planned_term_gets_rows_before_anything_is_released(
    monkeypatch, tmp_path
):
    # The plan IS the schedule: a session faculty have written down shows on the site from
    # that moment, not from the day its materials happen to ship.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "lecture-2",
                    datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "lectures/02_week-2", "materials", None)],
                ),
                Release(
                    "lab-2",
                    datetime(2026, 9, 10, 14, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "labs/02_week-2", "materials", None)],
                ),
            ]
        ),
        sources=[],  # nothing released yet
    )
    lectures = plan.collections["_lectures"]
    assert sorted(lectures) == ["lab-02.md", "session-02.md"]
    # Dated from the plan, and openly marked as having nothing to open yet.
    assert "date: 2026-09-10T14:00:00" in lectures["lab-02.md"]
    assert "links: []" in lectures["lab-02.md"]
    assert "lab 2 are not yet released" in lectures["lab-02.md"]
    assert "session 2 are not yet released" in lectures["session-02.md"]


def test_an_unreleased_row_names_where_its_materials_will_land(monkeypatch, tmp_path):
    # Mirrors the assignment row's placeholder: say what is coming and where, rather than
    # leaving an empty cell that reads as a mistake.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "lecture-3",
                    datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
                    deploy=[
                        Deploy("cm", "lectures/03_week-3", "lecture-materials", None),
                        Deploy("cm", "readings/03_week-3", "lecture-materials", None),
                    ],
                )
            ]
        ),
    )
    body = plan.collections["_lectures"]["session-03.md"]
    assert "`lecture-materials/lectures/03_week-3`" in body
    assert "`lecture-materials/readings/03_week-3`" in body
    # The row says what is coming and where, and stops there - naming the semester org as
    # well made the schedule table's cell two clauses long for no reader's benefit.
    assert "`Semester-f2026`" not in body


def test_a_released_row_replaces_its_placeholder_with_links(monkeypatch, tmp_path):
    sched = Schedule(
        releases=[
            Release(
                "lab-2",
                datetime(2026, 9, 10, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "labs/02_week-2", "materials", None)],
            )
        ]
    )
    plan = _plan(
        monkeypatch,
        tmp_path,
        sched,
        sources=[("materials", "labs", "02_week-2", 2)],
        files=lambda org, repo, subpath, folder, hosted: [
            Link("lab.pdf", "https://x/lab.pdf")
        ],
    )
    body = plan.collections["_lectures"]["lab-02.md"]
    assert ("lab", "lab.pdf") in entry_links(body)
    assert "not yet released" not in body


def test_a_declared_lab_row_keeps_one_row_once_its_files_ship(monkeypatch, tmp_path):
    # The case `type:` exists for: lab material that does not land under `labs/`. The plan
    # placed it as a lab and discovery placed the same folder as a lecture, so the day the
    # files shipped the schedule grew a SECOND row - a lab stuck on "not yet released" for
    # the rest of term, beside a lecture row holding that lab's links. Both sides go
    # through `dest_row_kind` now, so there is one row and it is the declared kind.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "clinic-3",
                    datetime(2026, 9, 24, 14, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "clinics/03_week-3", "materials", None)],
                    type="lab",
                )
            ]
        ),
        sources=[("materials", "clinics", "03_week-3", 3)],
        files=lambda org, repo, subpath, folder, hosted: [
            Link("clinic.pdf", "https://x/clinic.pdf")
        ],
    )
    lectures = plan.collections["_lectures"]
    assert sorted(lectures) == ["lab-03.md"]
    body = lectures["lab-03.md"]
    assert "type: lab" in body
    assert ("clinic", "clinic.pdf") in entry_links(body)
    assert "unreleased: true" not in body
    assert "not yet released" not in body


def test_a_row_released_off_plan_survives_the_planned_rows(monkeypatch, tmp_path):
    # Discovery still leads: the manual Release button ships folders the plan never named,
    # and those rows must not be dropped just because they are absent from schedule.yml.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "lecture-2",
                    datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                    deploy=[Deploy("cm", "lectures/02_week-2", "materials", None)],
                )
            ],
            semester_start=date(2026, 9, 1),
        ),
        sources=[("materials", "labs", "05_bonus", 5)],
    )
    assert sorted(plan.collections["_lectures"]) == ["lab-05.md", "session-02.md"]
    assert "not yet released" in plan.collections["_lectures"]["session-02.md"]
    assert "not yet released" not in plan.collections["_lectures"]["lab-05.md"]


def test_an_undated_release_raises_no_placeholder_row(monkeypatch, tmp_path):
    # `event_datetime: tbc` cannot place a session on a dated table, so it stays off the
    # schedule until faculty give it a date - same rule _planned_sessions applies to dating.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release(
                    "lecture-9",
                    None,
                    deploy=[Deploy("cm", "lectures/09_week-9", "materials", None)],
                )
            ]
        ),
    )
    assert plan.collections["_lectures"] == {}


def test_the_lecture_row_never_carries_the_weeks_lab_links(monkeypatch, tmp_path):
    # Labs are their own entries; a lab file linked from the lecture row too would show
    # the lab twice (schedule + the theme's labs page).
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(),
        sources=[
            ("materials", "lectures", "02_week-2", 2),
            ("materials", "labs", "02_week-2", 2),
        ],
        files=lambda org, repo, subpath, folder, hosted: [
            Link(f"{subpath}.pdf", f"https://x/{subpath}")
        ],
    )
    session = plan.collections["_lectures"]["session-02.md"]
    assert ("lecture", "lectures.pdf") in entry_links(session)
    assert not [s for s, _n in entry_links(session) if s == "lab"]
    assert ("lab", "labs.pdf") in entry_links(
        plan.collections["_lectures"]["lab-02.md"]
    )


def test_events_render_as_their_declared_types(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            events=[
                Event("mid-term", "MidTerm Exam", date(2026, 11, 3), "exam"),
                Event("project-clinic", "Project clinic", date(2026, 11, 10)),
            ]
        ),
    )
    events = plan.collections["_events"]
    assert "type: exam" in events["01-mid-term.md"]
    assert "type: special_event" in events["02-project-clinic.md"]
    # a schedule that names its own exams gets no synthesised stubs
    assert "midterm.md" not in events and "final.md" not in events


def test_synthesised_exams_appear_when_the_schedule_names_none(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            events=[Event("project-clinic", "Project clinic", date(2026, 11, 10))]
        ),
    )
    events = plan.collections["_events"]
    assert 'title: "MidTerm Exam"' in events["midterm.md"]
    assert 'title: "Final Exam"' in events["final.md"]
    assert "type: special_event" in events["01-project-clinic.md"]


def test_the_archive_row_ships_with_the_rest_of_the_schedule(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            semester_end=date(2026, 12, 18),
            archive=ArchiveRow(when=date(2027, 2, 16)),
        ),
    )
    assert (
        'title: "Semester archived"'
        in plan.collections["_events"]["semester-archived.md"]
    )


def test_a_semester_can_keep_its_archive_date_off_the_site(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            semester_end=date(2026, 12, 18),
            archive=ArchiveRow(when=date(2027, 2, 16), show_on_site=False),
        ),
    )
    assert "semester-archived.md" not in plan.collections["_events"]


def test_a_semester_with_no_archive_date_gets_no_row(monkeypatch, tmp_path):
    plan = _plan(monkeypatch, tmp_path, Schedule(semester_start=date(2026, 9, 7)))
    assert "semester-archived.md" not in plan.collections["_events"]


def test_term_date_rows_only_when_the_schedule_pins_the_bounds(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(semester_start=date(2026, 9, 7), semester_end=date(2026, 12, 18)),
    )
    events = plan.collections["_events"]
    assert 'title: "Semester starts"' in events["term-start.md"]
    assert "date: 2026-09-07T09:00:00" in events["term-start.md"]
    assert 'title: "Semester ends"' in events["term-end.md"]
    assert "date: 2026-12-18T09:00:00" in events["term-end.md"]

    unbounded = _plan(monkeypatch, tmp_path, Schedule())
    assert "term-start.md" not in unbounded.collections["_events"]
    assert "term-end.md" not in unbounded.collections["_events"]


# -------------------------------------------------------- dest_repo mismatch (fix 4)


def test_assignment_entry_names_the_semester_dest_repo_not_the_course_repo(monkeypatch):
    # assign.py provisions `<semester_dest_repo or slug>-<handle>`; the site must name the
    # same repo (and title the page from it), not the course repo minus its tag.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                semester_dest_repo="homework-1",
            )
        }
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        date(2026, 10, 13),
        found=("assignment-1", sched.assignments["assignment-1"]),
        handed_out=frozenset({"homework-1"}),
    )
    assert 'repo_name: "homework-1-<your-handle>"' in out
    assert 'title: "Homework 1"' in out


def test_the_site_build_gates_a_brief_on_what_the_semester_actually_holds(
    monkeypatch, tmp_path
):
    # End-to-end through sync_site, not just the renderer: the gate is worthless if the
    # build forgets to pass what the semester org holds. (`_plan` blanks every file read, so
    # the entry's presence - not the brief text - is what this can pin.)
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
            )
        }
    )
    args = {"sched": sched, "assignments": ["assignment-1-f2026"]}
    withheld = _plan(monkeypatch, tmp_path, **args).collections["_assignments"]
    # The entry - and so both schedule rows - is there; only the brief is held back.
    assert "handout_pending: true" in withheld["01-assignment-1.md"]

    out = _plan(monkeypatch, tmp_path, **args, handed_out=["assignment-1"]).collections[
        "_assignments"
    ]
    assert "handout_pending: true" not in out["01-assignment-1.md"]


def test_a_pending_assignment_does_not_shift_a_later_ones_ordinal(
    monkeypatch, tmp_path
):
    # The ordinal is in the URL, so numbering from the position in the FULL list keeps
    # assignment 2's page at the same address whether or not 1 has gone out yet.
    out = _plan(
        monkeypatch,
        tmp_path,
        sched=Schedule(),
        assignments=["assignment-1-f2026", "assignment-2-f2026"],
        handed_out=["assignment-2"],
    ).collections["_assignments"]
    # named by the SEMESTER-side name, which is what students see
    assert list(out) == ["01-assignment-1.md", "02-assignment-2.md"]
    assert "handout_pending: true" in out["01-assignment-1.md"]
    assert "handout_pending: true" not in out["02-assignment-2.md"]


def test_an_assignment_in_the_plan_gets_rows_before_its_template_is_staged(
    monkeypatch, tmp_path
):
    # A term written in August names template repos nobody has created yet. Discovery finds
    # none of them, and the site used to render one row for a semester that had written four
    # - dates published in schedule.yml, nothing on the schedule that publishes them.
    # Dates derived from TODAY, not pinned to a literal. `_assignment_entry` treats a
    # handout_datetime that has already fired as handed out, so a hardcoded September 2026
    # stopped being "a term written in August" the moment the clock reached it - and
    # `_plan` goes through `sync_site`, which takes no `now` to pin.
    base = datetime.now(BERLIN).replace(hour=7, minute=0, second=0, microsecond=0)
    handout = {n: base + timedelta(days=30 + n) for n in (1, 2, 3, 4)}
    sched = Schedule(
        assignments={
            f"assignment-{n}": AssignmentEntry(
                course_source_repo=f"assignment-{n}-f2026",
                due_datetime=handout[n] + timedelta(days=9, hours=16, minutes=59),
                handout_datetime=handout[n],
            )
            for n in (1, 2, 3, 4)
        }
    )
    out = _plan(
        monkeypatch,
        tmp_path,
        sched=sched,
        assignments=["assignment-1-f2026"],  # only the first is staged
    ).collections["_assignments"]
    assert list(out) == [
        "01-assignment-1.md",
        "02-assignment-2.md",
        "03-assignment-3.md",
        "04-assignment-4.md",
    ]
    # each on its own dates, and none of them claiming to be released
    assert f"date: {handout[2]:%Y-%m-%dT%H:%M:%S}" in out["02-assignment-2.md"]
    assert all("handout_pending: true" in e for e in out.values())


def test_two_plan_entries_citing_one_template_stay_two_assignments(
    monkeypatch, tmp_path
):
    # `schedule.entry_for_repo` maps a repo to the FIRST entry citing it, so resolving the
    # entry from the repo gave both of these the same slug, the same dates and one
    # collection file - the second assignment simply vanished.
    sched = Schedule(
        assignments={
            "assignment-3": AssignmentEntry(
                course_source_repo="shared-f2026",
                due_datetime=datetime(2026, 10, 18, 23, 59, tzinfo=BERLIN),
            ),
            "assignment-4": AssignmentEntry(
                course_source_repo="shared-f2026",
                due_datetime=datetime(2026, 11, 8, 23, 59, tzinfo=BERLIN),
            ),
        }
    )
    out = _plan(monkeypatch, tmp_path, sched=sched).collections["_assignments"]
    assert list(out) == ["01-assignment-3.md", "02-assignment-4.md"]
    assert "    date: 2026-10-18T23:59:00" in out["01-assignment-3.md"]
    assert "    date: 2026-11-08T23:59:00" in out["02-assignment-4.md"]


# ---------------------------------------------- fail-loud reads (fixes 5 and 6)


def test_session_files_missing_tree_is_empty(monkeypatch):
    monkeypatch.setattr(site, "default_branch", lambda org, repo, **k: "main")
    monkeypatch.setattr(gh_contents, "gh", lambda *a, **k: (1, "HTTP 404: Not Found"))
    assert (
        site._session_files("Semester-f2026", "materials", "lectures", "03_x", {}) == []
    )


def test_session_files_fetch_failure_raises_rather_than_stripping_the_site(monkeypatch):
    # A swallowed failure returned (), the site republished with every material link gone.
    monkeypatch.setattr(site, "default_branch", lambda org, repo, **k: "main")
    monkeypatch.setattr(gh_contents, "gh", lambda *a, **k: (1, "HTTP 502: bad gateway"))
    with pytest.raises(RuntimeError):
        site._session_files("Semester-f2026", "materials", "lectures", "03_x", {})


def test_team_people_missing_team_is_empty(monkeypatch):
    monkeypatch.setattr(site_repo, "gh", lambda *a, **k: (1, "HTTP 404: Not Found"))
    assert site_repo._team_people("Course", "instructors") == []


def test_team_people_read_failure_raises_rather_than_wiping_the_team(monkeypatch):
    monkeypatch.setattr(site_repo, "gh", lambda *a, **k: (1, "HTTP 500: boom"))
    with pytest.raises(RuntimeError):
        site_repo._team_people("Course", "instructors")


def _member_gh(profile: tuple[int, str]):
    """Fake gh: the team lists one member, whose profile lookup returns `profile`."""

    def fake(*args, **kwargs):
        return (0, "jane\n") if any(a.endswith("/members") for a in args) else profile

    return fake


def test_team_people_skips_a_deleted_account_but_says_so(monkeypatch, capsys):
    monkeypatch.setattr(site_repo, "gh", _member_gh((1, "gh: Not Found (HTTP 404)")))
    assert site_repo._team_people("Course", "instructors") == []
    assert "jane" in capsys.readouterr().out  # one card fewer, not silently


def test_team_people_per_member_failure_raises_rather_than_dropping_one_card(
    monkeypatch,
):
    # The team read is fail-loud; the per-MEMBER read used to swallow everything, so a
    # transient error republished the site one instructor short with nothing to show for it.
    monkeypatch.setattr(site_repo, "gh", _member_gh((1, "gh: HTTP 502 Bad Gateway")))
    with pytest.raises(RuntimeError, match="could not read the GitHub profile of jane"):
        site_repo._team_people("Course", "instructors")


def test_team_people_never_renders_the_syncs_own_bot_account(monkeypatch, capsys):
    # The bot is in `instructors` for the access it needs, and it rendered on the public
    # site as a member of the teaching team. Its own profile lookup must not even happen.
    def fake(*args, **kwargs):
        if any(a.endswith("/members") for a in args):
            return (0, "hertie-dsl-bot\njane\n")
        assert "users/hertie-dsl-bot" not in args
        return (0, "Jane\thttps://a/j.png\thttps://gh/jane")

    monkeypatch.setattr(site_repo, "gh", fake)
    monkeypatch.setattr(
        site_repo, "acting_login", lambda: "Hertie-DSL-Bot"
    )  # logins fold case
    assert site_repo._team_people("Course", "instructors") == [
        ("Jane", "https://a/j.png", "https://gh/jane")
    ]
    assert "hertie-dsl-bot" in capsys.readouterr().out  # skipped out loud


def _bad_indent_error() -> yaml.YAMLError:
    """The REAL exception a bad indent in instructors.yml produces. load_yaml_config re-raises
    it untouched, and yaml.YAMLError is NOT a RuntimeError - a stub that raised
    RuntimeError instead is exactly why the boundaries below went uncaught."""
    try:
        yaml.safe_load("instructors:\n  - name: Ada\n   github: ada\n")
    except yaml.YAMLError as exc:
        return exc
    raise AssertionError("expected that YAML to be malformed")


def test_yaml_file_raises_on_a_malformed_file_rather_than_wiping_what_it_feeds(
    monkeypatch,
):
    # A semester's instructors.yml with one bad indent used to parse to `{}` - "nothing declared" -
    # and republish the site with every teaching-team card gone, green.
    err = _bad_indent_error()
    monkeypatch.setattr(
        site_repo, "load_yaml_config", lambda *a: (_ for _ in ()).throw(err)
    )
    with pytest.raises(yaml.YAMLError):
        site_repo.yaml_file("Semester-f2026", "classroom-config", "instructors.yml")


def test_yaml_file_reads_an_absent_file_as_nothing_declared(monkeypatch):
    monkeypatch.setattr(site_repo, "load_yaml_config", lambda *a: None)
    assert (
        site_repo.yaml_file("Semester-f2026", "classroom-config", "instructors.yml")
        == {}
    )


def test_main_reports_a_malformed_config_as_one_line_not_a_traceback(
    monkeypatch, capsys
):
    # instructors.yml is web-editable, so faculty author bad indents directly. yaml.YAMLError
    # is not a RuntimeError, so it used to walk straight through main()'s guard and out as
    # a traceback in the Actions log.
    err = _bad_indent_error()
    monkeypatch.setattr(site, "discover_semesters", lambda org: ["Semester-f2026"])
    monkeypatch.setattr(site, "sync_site", lambda *a: (_ for _ in ()).throw(err))
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--semester-org", "Semester-f2026"],
    )
    assert site.main() == 1
    assert "Traceback" not in capsys.readouterr().err


def test_main_refuses_a_semester_this_course_org_never_registered(monkeypatch, capsys):
    # --semester-org reaches main straight from a repository_dispatch's client_payload,
    # written by whoever holds a semester's DSL_BOT_TOKEN - a lower trust tier than the
    # course org. Naming SOMEONE ELSE'S semester would rebuild that semester's site from this
    # dispatch, so the registry gets the last word.

    monkeypatch.setattr(site, "discover_semesters", lambda org: ["Semester-f2026"])
    synced: list = []
    monkeypatch.setattr(site, "sync_site", lambda *a: synced.append(a) or 0)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--semester-org", "Other-f2026"],
    )
    assert site.main() == 1
    assert synced == []
    assert "not registered under Course" in capsys.readouterr().err


def test_main_refuses_every_semester_when_the_registry_is_empty(monkeypatch, capsys):
    # The check used to short-circuit on an empty registry, so a course org that had
    # registered nothing accepted any org a dispatch named. An empty registry authorises
    # nothing.

    monkeypatch.setattr(site, "discover_semesters", lambda org: [])
    synced: list = []
    monkeypatch.setattr(site, "sync_site", lambda *a: synced.append(a) or 0)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--semester-org", "Other-f2026"],
    )
    assert site.main() == 1
    assert synced == []
    assert "lists nothing" in capsys.readouterr().err


def test_main_matches_a_registered_semester_case_insensitively(monkeypatch):
    # GitHub org names are case-insensitive; a case difference must not read as a
    # cross-semester dispatch.

    monkeypatch.setattr(site, "discover_semesters", lambda org: ["Semester-F2026"])
    synced: list = []
    monkeypatch.setattr(site, "sync_site", lambda *a: synced.append(a) or 0)
    refreshed: list = []
    monkeypatch.setattr(site.status, "refresh", lambda *a: refreshed.append(a) or 1)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--semester-org", "semester-f2026"],
    )
    assert site.main() == 0
    assert synced == [("Course", "semester-f2026")]
    # The site's last update is in status.json; a write that failed does not red the sync.
    assert refreshed == [("Course", "semester-f2026")]


def test_all_semesters_loop_survives_one_semesters_raised_failure(monkeypatch, capsys):
    # The lesson PR #151/#146 applied to the nightly refresh: the single try used to wrap
    # the whole loop, so one semester's raise skipped every LATER semester's site on the 06:00
    # cron. The loop iterates the LIVE semesters, which is what a test replaces here.

    monkeypatch.setattr(
        site, "live_semesters", lambda org: ["Semester-A", "Semester-B"]
    )
    seen: list[str] = []

    def fake_sync(course, semester):
        seen.append(semester)
        if semester == "Semester-A":
            raise _bad_indent_error()
        return 0

    monkeypatch.setattr(site, "sync_site", fake_sync)
    monkeypatch.setattr(
        "sys.argv", ["site", "sync", "--course-org", "Course", "--all-semesters"]
    )
    assert site.main() == 1
    assert seen == ["Semester-A", "Semester-B"]
    assert "Semester-A" in capsys.readouterr().err


# --------------------------------------------- front-matter escaping (fix 7)


def test_front_matter_survives_a_backslash_in_a_title(monkeypatch):
    # `# \sigma review` is an invalid YAML escape unquoted - the whole site build fails
    # (ScannerError) unless every scalar is routed through _q.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# \\sigma review\nBody"
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        date(2026, 11, 10),
        handed_out=frozenset(
            {"assignment-1"}
        ),  # the README title is only read once out
    )
    front = yaml.safe_load(out.split("---")[1])  # must parse, no ScannerError
    # the README heading is the assignment's NAME, so it is `subtitle` that carries the
    # faculty prose - and therefore the escape risk
    assert "sigma" in front["subtitle"]
    assert "sigma" in front["due_event"]["subtitle"]
    assert front["title"] == "Assignment 1"  # the identifier is the slug's, always


def test_links_block_survives_a_backslash_in_a_filename():
    block = site_repo.links_block(
        [("lectures", [Link("notes \\x.pdf", "https://x/1")])]
    )
    parsed = yaml.safe_load(block)  # must parse, no ScannerError
    assert "notes" in parsed["links"][0]["name"]


def test_assignment_readme_body_is_fenced_as_liquid_raw(monkeypatch):
    # A `{% ... %}`/`{{ ... }}` in a README would run as Liquid and a malformed tag fails
    # the build; the inlined body is fenced.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# A1\nUse {{ x }} in your code"
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-1"}),  # the README is only inlined once out
    )
    assert "{% raw %}" in out and "{% endraw %}" in out


# --------------------------------------------- tz-aware display (fix 8)


def test_iso_when_prints_the_datetime_it_is_given_offset_free():
    # The semester-tz conversion happens ONCE, in schedule's parser (below), so every
    # datetime reaching the renderers is already semester wall-clock: printing it is just
    # dropping the offset, with no zone for a renderer to forget to pass.
    assert site_repo.iso_when(datetime(2026, 9, 15, 12, 0, tzinfo=BERLIN)) == (
        "2026-09-15T12:00:00"
    )
    assert site_repo.iso_when(datetime(2026, 9, 15, 10, 0, tzinfo=UTC)) == (
        "2026-09-15T10:00:00"
    )


def test_a_written_offset_reaches_the_site_as_the_semester_wall_clock_time():
    # End to end: 10:00 UTC in a Berlin semester (CEST, +2 in September) is shown as 12:00 -
    # the time the class actually happens - not the written offset's 10:00.
    (event,) = site.schedule.parse(
        {
            "timezone": "Europe/Berlin",
            "events": {
                "remote": {
                    "title": "Remote talk",
                    "event_datetime": "2026-09-15T10:00+00:00",
                }
            },
        }
    ).events
    assert "date: 2026-09-15T12:00:00" in site._event_entry(event, END_OF_TERM)


def test_display_only_rows_come_from_events_alone(monkeypatch, tmp_path):
    # `releases:` is the deploy plan; a row with nothing to release belongs in `events:`,
    # and an action-less release entry is NOT a second way to write one.
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(
            releases=[
                Release("guest-lecture", datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN))
            ]
        ),
    )
    assert "Guest Lecture" not in "".join(plan.collections["_events"].values())


# ------------------------------------------------- a session's declared name + blurb
def test_a_row_carries_the_title_and_details_the_plan_declared(monkeypatch):
    monkeypatch.setattr(
        site, "_session_files", lambda *a: [Link("s.pdf", "https://x/1")]
    )
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    out = site._lecture_entry(
        "Semester-f2026",
        "1",
        _row(
            datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN),
            subtitle="Probability Theory",
            details="Sample spaces and Bayes' rule.",
        ),
        RELEASED,
        hosted={},
    )
    # `title` stays the ordinal - what the theme has always assumed it is - and the
    # declared name rides `subtitle` beside it.
    assert 'title: "Session 1"' in out
    assert 'subtitle: "Probability Theory"' in out
    assert 'details: "Sample spaces and Bayes\' rule."' in out


def test_a_row_omits_the_declared_fields_it_was_not_given(monkeypatch):
    # Omitted, not written blank: the theme tests for them, so an empty string would
    # render an empty line where there should be nothing at all.
    monkeypatch.setattr(
        site, "_session_files", lambda *a: [Link("s.pdf", "https://x/1")]
    )
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    out = site._lecture_entry(
        "Semester-f2026",
        "1",
        _row(datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN)),
        RELEASED,
        hosted={},
    )
    assert "subtitle:" not in out and "details:" not in out


def test_an_unreleased_row_still_says_what_the_session_is_about():
    out = site._lecture_entry(
        "Semester-f2026",
        "3",
        _row(
            datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
            dests={"materials/lectures/03_week-3": None},
            subtitle="Expectation",
            details="Linearity of expectation.",
        ),
        [],
        hosted={},
    )
    # What the session covers is known the day the plan is written, so it is published
    # then - the term reads as a syllabus from day one. Only the FILES wait for release,
    # which is what the body says.
    assert 'subtitle: "Expectation"' in out
    assert 'details: "Linearity of expectation."' in out
    assert "will appear in `materials/lectures/03_week-3` when they are." in out


def test_the_site_readme_does_not_promise_the_tab_pages_are_safe():
    # It used to say "pages ... are never rewritten. Change them freely", while every sync
    # overwrites the tab pages - so following it lost the edit AND opened an
    # "edits overwritten" issue. Also: the public sync writes no materials index.
    from dsl_course.site_repo import _site_pages

    semester_readme = site_repo.site_readme("org", semester=True)
    for pg in _site_pages(semester=True):
        assert f"`{pg.file}`" in semester_readme, pg.file
    assert "pages, `Gemfile`" not in semester_readme
    assert "`_data/materials.yml`" in semester_readme
    assert "`_data/materials.yml`" not in site_repo.site_readme("org", semester=False)


def test_readings_pending_reaches_the_rendered_row():
    # End to end: the flag and the "will appear in" sentence are what the whole fold is
    # for, and both were unreachable for the layout the toolkit ships as its example.
    s = _sched(
        [
            Release(
                "readings-2",
                datetime(2026, 9, 1, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "readings/02_x", "materials", None)],
                show_on_site=False,
            ),
            Release(
                "lecture-2",
                datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "materials", None)],
            ),
        ]
    )
    row = schedule_plan.planned_sessions(s)[("2", "lecture")]
    # live_repos empty -> _dest_link renders plain code and makes no tree call
    page = site._lecture_entry(
        "SEMESTER", "2", row, sources=[], live_repos=frozenset(), hosted={}
    )
    assert "readings_pending: true" in page
    assert "materials/readings/02_x" in page
    assert "not yet released" in page


# --------------------------------------------------------------- rows from a bare label
# docs/07: "a row appears as soon as you write it, not when it ships". An entry that stages
# nothing yet has no deploy destination to key a row off, so its own label places it.


# ------------------------------------------------- ownership notices on generated files


def test_a_generated_page_states_its_ownership_inside_its_front_matter():
    page = site_repo._stamp_front_matter(
        '---\ntype: lecture\ntitle: "Session 1"\n---\n'
    )
    # Jekyll needs `---` on line 1, so the notice cannot go above it
    assert page.startswith("---\n# SYSTEM-OWNED - do not edit.")
    assert "type: lecture" in page and 'title: "Session 1"' in page
    import yaml

    assert yaml.safe_load(page.split("---")[1])["type"] == "lecture"


def test_stamping_a_page_with_no_front_matter_leaves_it_untouched():
    assert site_repo._stamp_front_matter("just text\n") == "just text\n"


def test_the_site_readme_names_what_the_sync_rewrites_and_what_it_does_not():
    r = site_repo.site_readme("hertie-x-f2026", semester=True)
    assert r.startswith("<!-- SYSTEM-OWNED - do not edit.")
    assert "Do not edit this repository." in r
    for owned in ("_lectures/", "_assignments/", "_events/", "_data/people.yml"):
        assert owned in r
    # the theme is explicitly NOT claimed, or faculty cannot restyle their own site
    assert "Everything else is yours" in r


def test_the_config_header_names_the_identity_keys_the_sync_overwrites():
    cfg = site_repo._stamp_config(
        "# Edit the fields below for your course.\ncourse_name: x\n",
        ["course_code", "course_name"],
    )
    assert "`course_code`, `course_name`" in cfg
    assert "dsl-course.yml" in cfg
    assert "course_name: x" in cfg


def test_a_config_without_the_template_header_line_is_left_alone():
    assert (
        site_repo._stamp_config("course_name: x\n", ["course_name"])
        == "course_name: x\n"
    )


def test_a_closed_out_semesters_site_is_left_as_teardown_left_it(monkeypatch):
    # The site repo is frozen with the rest of the semester, and its last sync was the one
    # teardown ran before freezing it. The live semester beside it still rebuilds.
    monkeypatch.setattr(
        discovery, "discover_semesters", lambda org: ["Semester-A", "Semester-B"]
    )
    monkeypatch.setattr(
        discovery, "repo_is_archived", lambda org, name: org == "Semester-A"
    )
    seen: list[str] = []
    monkeypatch.setattr(
        site, "sync_site", lambda course, semester: seen.append(semester) or 0
    )
    monkeypatch.setattr(
        "sys.argv", ["site", "sync", "--course-org", "Course", "--all-semesters"]
    )
    assert site.main() == 0
    assert seen == ["Semester-B"]


def test_a_dispatch_naming_a_closed_out_semester_syncs_nothing(monkeypatch):
    # Still REGISTERED, so it is not the trust-boundary refusal above - just nothing left
    # to do, and a write that would 403.
    monkeypatch.setattr(site, "discover_semesters", lambda org: ["Semester-A"])
    monkeypatch.setattr(site, "semester_is_live", lambda org: False)

    def boom(*a, **k):
        raise AssertionError("a frozen semester's site must not be rebuilt")

    monkeypatch.setattr(site, "sync_site", boom)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--semester-org", "Semester-A"],
    )
    assert site.main() == 0


# ---------------------------------------------------- public copies of published files
# What a semester site hosts itself, so an HTML deck renders in a browser instead of showing
# as source on GitHub. `_mirror_public` is the ONE decision: it says which paths it copied,
# and every renderer links a hosted copy only for a path it names - so a page cannot offer
# a rendered copy that a size cap, a denylist or a failed clone stopped being made.


@pytest.fixture(autouse=True)
def _fresh_publish_policy():
    """The policy memo lives for one sync run, and a test is a run."""
    site._publish_policy.cache_clear()
    yield
    site._publish_policy.cache_clear()


@pytest.fixture
def origins(tmp_path, monkeypatch) -> BareOrigins:
    """Semester repos as bare repos on disk, with `gh repo clone` reaching them."""
    world = BareOrigins(tmp_path / "world")
    monkeypatch.setattr(ghcli, "gh", world.clone_only)
    return world


def _policy(*patterns: str) -> dict[str, tuple]:
    return {"materials": (site.parse_patterns("\n".join(patterns)),)}


def _mirror(monkeypatch, origins, tmp_path, tree: dict[str, str], policies):
    """Mirror a faked semester repo into a site checkout; return (hosted, what it serves).

    Called twice by the tests that are about a SECOND sync: the semester repo is seeded on
    the first call only and the site checkout is reused, which is the state a re-sync
    actually runs against."""
    if tree and "main" not in origins.refs("materials"):
        origins.commit("materials", tree)
    monkeypatch.setattr(
        site, "_repo_tree", lambda org, repo: ("main", tuple(sorted(tree)))
    )
    site_wd = tmp_path / "site"
    hosted = site._mirror_public(site_wd, "Semester-f2026", policies)
    served = site_wd / site.SITE_FILES_DIR
    return hosted, sorted(
        p.relative_to(served).as_posix() for p in served.rglob("*") if p.is_file()
    )


def test_a_published_file_is_linked_to_the_hosted_copy_and_to_its_source(monkeypatch):
    # The row a published deck renders as: the NAME opens the site's own copy, `url` is
    # still the file on GitHub so the template can offer `[source]` beside it.
    monkeypatch.setattr(
        site,
        "_repo_tree",
        cache(lambda org, repo: ("main", ("lectures/01_a/slides.html",))),
    )
    link = site._session_files(
        "Semester-f2026",
        "materials",
        "lectures",
        "01_a",
        {"materials": frozenset({"lectures/01_a/slides.html"})},
    )[0]
    assert link.url == (
        "https://github.com/Semester-f2026/materials/blob/main/lectures/01_a/slides.html"
    )
    assert link.view_url == (
        "https://semester-f2026.github.io/files/materials/lectures/01_a/slides.html"
    )


@pytest.mark.parametrize(
    ("path", "linked"),
    [
        ("lectures/01_a/slides.html", True),
        ("lectures/01_a/slides.pdf", True),
        # Copied, but GitHub already renders it - a second copy would only be a second
        # place for it to go stale, so the row is left exactly as it was.
        ("lectures/01_a/lab.ipynb", False),
    ],
)
def test_only_a_format_a_browser_renders_is_linked_to_its_copy(
    monkeypatch, path, linked
):
    monkeypatch.setattr(site, "_repo_tree", cache(lambda org, repo: ("main", (path,))))
    link = site._session_files(
        "Semester-f2026",
        "materials",
        "lectures",
        "01_a",
        {"materials": frozenset({path})},
    )[0]
    assert bool(link.view_url) is linked


def test_a_file_that_was_not_copied_is_never_linked_to_a_copy(monkeypatch):
    # The whole point of reading what the mirror DID: a deck dropped for its size, or
    # left behind by a clone that failed, must render as today's row rather than as a 404.
    monkeypatch.setattr(
        site,
        "_repo_tree",
        cache(lambda org, repo: ("main", ("lectures/01_a/slides.html",))),
    )
    link = site._session_files("Semester-f2026", "materials", "lectures", "01_a", {})[0]
    assert link.view_url == ""


def test_a_course_that_publishes_nothing_writes_the_front_matter_it_always_did():
    # The golden: every semester site alive today publishes nothing, and its rows must come
    # out byte for byte as they did before any of this existed.
    block = site_repo.links_block(
        [("lectures", [Link("slides.pdf", "https://github.com/o/r/blob/main/s.pdf")])]
    )
    assert block == (
        "links:\n"
        "    - url: https://github.com/o/r/blob/main/s.pdf\n"
        '      name: "slides.pdf"\n'
        '      section: "lecture"'
    )


def test_a_published_decks_bundle_follows_it(monkeypatch, origins, tmp_path):
    # A Quarto deck is one deliverable plus a `<stem>_files/` directory its renderer
    # invented. Copied without it, the deck loads with no figures and no styles - and no
    # faculty member should have to write a pattern for a folder they did not name.
    tree = {
        "lectures/01_a/slides.html": "deck",
        "lectures/01_a/slides_files/figure/plot.svg": "<svg/>",
        "lectures/01_a/notes.md": "notes",
    }
    hosted, served = _mirror(
        monkeypatch, origins, tmp_path, tree, _policy("lectures/**/*.html")
    )
    assert served == [
        "materials/lectures/01_a/slides.html",
        "materials/lectures/01_a/slides_files/figure/plot.svg",
    ]
    assert hosted["materials"] == frozenset(
        {"lectures/01_a/slides.html", "lectures/01_a/slides_files/figure/plot.svg"}
    )


def test_a_denylisted_path_is_never_copied_however_wide_the_pattern(
    monkeypatch, origins, tmp_path
):
    # `everything x all files` is a real answer on the form, and it must not be able to
    # put a solution, a hidden test or a `.env` on a public site.
    tree = {
        "lectures/01_a/slides.html": "deck",
        "lectures/01_a/solution/answers.pdf": "answers",
        "tests/test_hidden.py": "assert True\n",
        ".env": "KEY=example",
        "lectures/01_a/.DS_Store": "junk",
    }
    _hosted, served = _mirror(monkeypatch, origins, tmp_path, tree, _policy("**"))
    assert served == ["materials/lectures/01_a/slides.html"]


def test_removing_a_pattern_removes_the_copy(monkeypatch, origins, tmp_path):
    # Unpublishing is deleting the pattern: the mirror is rebuilt every sync, so what is
    # no longer matched stops being served.
    tree = {"lectures/01_a/slides.html": "deck", "labs/01_a/lab.html": "lab"}
    _hosted, served = _mirror(
        monkeypatch, origins, tmp_path, tree, _policy("**/*.html")
    )
    assert served == [
        "materials/labs/01_a/lab.html",
        "materials/lectures/01_a/slides.html",
    ]
    _hosted, served = _mirror(
        monkeypatch, origins, tmp_path, tree, _policy("lectures/**/*.html")
    )
    assert served == ["materials/lectures/01_a/slides.html"]
    # And declaring nothing public takes the whole mirror with it - without a clone, since
    # nothing has to be read to know it.
    monkeypatch.setattr(site, "clone", _never_cloned)
    hosted, served = _mirror(monkeypatch, origins, tmp_path, tree, {"materials": ()})
    assert (hosted, served) == ({}, [])


def _never_cloned(*_a, **_k):
    raise AssertionError("cloned a repo with nothing declared public")


def test_a_course_with_no_publish_file_is_never_cloned(monkeypatch, origins, tmp_path):
    # The cost of this feature on a course that does not use it has to be zero: no clone,
    # no copy, no directory.
    monkeypatch.setattr(site, "yaml_file", lambda *a: {})
    monkeypatch.setattr(site, "clone", _never_cloned)
    policies = site._publish_policies("Course-Org", _one_deploy(), ["materials"])
    assert policies == {"materials": ()}
    assert _mirror(monkeypatch, origins, tmp_path, {}, policies) == ({}, [])


def _one_deploy() -> Schedule:
    return Schedule(
        releases=[
            Release(
                "s1",
                datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("course-materials-f2026", "lectures/01_a", "materials")],
            )
        ]
    )


def test_a_policy_that_does_not_parse_stops_the_sync(monkeypatch):
    # Syntactically a bad indent is indistinguishable from "nothing public", and reading
    # it that way would unpublish a whole course's decks over a typo - on a green run,
    # since the rest of the site syncs fine. So it fails loudly, like instructors.yml: nothing
    # is republished and nothing is wiped.
    def boom(*_a):
        raise yaml.YAMLError("mapping values are not allowed here")

    monkeypatch.setattr(site, "yaml_file", boom)
    with pytest.raises(yaml.YAMLError):
        site._publish_policies("Course-Org", _one_deploy(), ["materials"])

    monkeypatch.setattr(site, "yaml_file", lambda *a: {"public": "lectures/**"})
    with pytest.raises(ValueError, match="must be a list of patterns"):
        site._publish_policies("Course-Org", _one_deploy(), ["materials"])


def test_a_file_github_would_refuse_is_skipped_rather_than_failing_the_sync(
    monkeypatch, origins, tmp_path, capsys
):
    # One 200 MB recording in a materials repo would otherwise fail the site's own push
    # and take the whole semester site offline - for a file that is still on GitHub.
    monkeypatch.setattr(site, "_MAX_PUBLIC_FILE_BYTES", 8)
    tree = {"lectures/01_a/recording.pdf": "x" * 64, "lectures/01_a/slides.html": "d"}
    hosted, served = _mirror(
        monkeypatch, origins, tmp_path, tree, _policy("lectures/**")
    )
    assert served == ["materials/lectures/01_a/slides.html"]
    assert "recording.pdf" not in hosted["materials"]
    out = capsys.readouterr()
    assert "recording.pdf" in out.err and "::warning::" in out.err


def test_a_clone_that_failed_leaves_the_last_syncs_copies_standing(
    monkeypatch, origins, tmp_path, capsys
):
    # Delete-and-rebuild, in that order and only after the clone: a site republished with
    # every rendered deck missing because one clone failed is the worse outage.
    tree = {"lectures/01_a/slides.html": "deck"}
    policy = _policy("lectures/**/*.html")
    _hosted, served = _mirror(monkeypatch, origins, tmp_path, tree, policy)
    assert served == ["materials/lectures/01_a/slides.html"]
    monkeypatch.setattr(site, "clone", lambda *a, **k: False)
    hosted, served = _mirror(monkeypatch, origins, tmp_path, tree, policy)
    # The copies stand, and nothing links them: a stale file nobody points at beats a
    # page full of dead links.
    assert served == ["materials/lectures/01_a/slides.html"] and hosted == {}
    assert "could not clone" in capsys.readouterr().err


def test_the_policy_is_read_from_the_source_repo_the_plan_names(monkeypatch):
    # Course-level, in the repo faculty actually edit - keyed on the semester DESTINATION,
    # which is the repo whose files the site links and whose bytes get copied.
    asked: list[tuple[str, str, str]] = []

    def yaml_file(org, repo, path):
        asked.append((org, repo, path))
        return {"public": ["lectures/**/*.html"]}

    monkeypatch.setattr(site, "yaml_file", yaml_file)
    sched = _one_deploy()
    sched.releases[0].deploy.append(Deploy("course-code-f2026", "src", "code"))
    policies = site._publish_policies("Course-Org", sched, ["materials"])
    # The `code` destination is not one of this semester's content repos, so it is not asked
    # about at all.
    assert asked == [("Course-Org", "course-materials-f2026", "publish.yml")]
    assert policies["materials"][0].check_file("lectures/01_a/slides.html").include


def test_a_shared_assignment_names_the_real_drop_box_and_the_reader_s_folder(
    monkeypatch,
):
    # The ONE shape whose `repo_name` is a real repo: there is a single drop box for the
    # whole semester, so the page can name it exactly - and `repo_url` is that repo rather
    # than the org's filtered list, because there is nothing to filter to.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A3\nThe brief.")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("submit_via: shared_dropbox_repo\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-3-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-3"}),
    )
    assert out.count('submit_shape: "shared-dropbox-repo"') == 2
    assert out.count('repo_name: "assignment-3-submissions"') == 2
    assert (
        out.count(
            'repo_url: "https://github.com/Semester-f2026/assignment-3-submissions"'
        )
        == 2
    )
    # What is the reader's own is a FOLDER, which is the half a repo name cannot carry.
    assert out.count('submit_path: "<your-handle>/"') == 2


def test_a_shared_group_assignment_names_the_team_s_folder(monkeypatch):
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "# A3\nThe brief.")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec(
            "submit_via: shared_dropbox_repo\ntype: group\n"
        ),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-3-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-3"}),
    )
    assert out.count('submit_path: "<your-team>/"') == 2
    # One drop box either way: the repo is the semester's, not the team's.
    assert out.count('repo_name: "assignment-3-submissions"') == 2


def test_a_pending_shared_assignment_promises_a_drop_box_and_not_a_repo(monkeypatch):
    # "your private <repo> repo appears when it is" would promise every student a repo of
    # their own, which is the one thing this shape does not hand out.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    monkeypatch.setattr(
        site,
        "load_grading_spec",
        lambda *a: grades.parse_grading_spec("submit_via: shared_dropbox_repo\n"),
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-3-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        handout=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 21, tzinfo=BERLIN),
    )
    assert "the `assignment-3-submissions` drop box appears when it is." in out
    # And no address, because there is nothing at the other end of it yet.
    assert "repo_url" not in out


# ------------------------------------- `details:`, the Details column on every row type
# One key feeding one column, wherever it is written. Before this, `description` meant the
# session blurb on a lecture row and the row's NAME on an exam, a special event, a term
# boundary and an assignment's due row.


def test_an_events_details_fill_its_row_and_its_title_stays_the_title():
    e = Event(
        "mid-term",
        "MidTerm Exam",
        date(2026, 11, 3),
        type="exam",
        details="Room A1. Two hours, open book.",
    )
    out = site._event_entry(e, END_OF_TERM)
    assert 'title: "MidTerm Exam"' in out
    assert 'details: "Room A1. Two hours, open book."' in out


def test_a_special_events_details_fill_its_row():
    e = Event(
        "clinic",
        "Project clinic",
        date(2026, 11, 10),
        details="Bring a laptop and whatever is not working.",
    )
    out = site._event_entry(e, END_OF_TERM)
    assert 'title: "Project clinic"' in out
    assert 'details: "Bring a laptop and whatever is not working."' in out


def test_an_exam_with_nothing_to_say_says_nothing():
    # It used to carry "Details to be confirmed." in its body - written into every exam of
    # every semester whether or not anything was outstanding, and undeletable from
    # schedule.yml.
    out = site._event_row("exam", "Final Exam", date(2026, 12, 15))
    assert "to be confirmed" not in out.lower()
    assert "details:" not in out
    assert out.endswith('title: "Final Exam"\n---\n')


def test_an_event_kept_off_the_site_gets_no_row(monkeypatch, tmp_path):
    sched = Schedule(
        semester_end=END_OF_TERM,
        events=[
            Event("clinic", "Project clinic", date(2026, 11, 10)),
            Event("reserve", "Reserve slot", date(2026, 11, 17), show_on_site=False),
        ],
    )
    events = _plan(monkeypatch, tmp_path, sched).collections["_events"]
    rendered = "".join(events.values())
    assert "Project clinic" in rendered
    assert "Reserve slot" not in rendered


def test_a_hidden_exam_still_answers_the_synthesised_exam_stubs(monkeypatch, tmp_path):
    # A semester that wrote its exams and then took them off the site HAS said what its
    # exams are; answering that with two invented ones would put back what it removed.
    sched = Schedule(
        semester_end=END_OF_TERM,
        events=[
            Event(
                "mid-term",
                "MidTerm",
                date(2026, 11, 3),
                type="exam",
                show_on_site=False,
            )
        ],
    )
    events = _plan(monkeypatch, tmp_path, sched).collections["_events"]
    assert "midterm.md" not in events and "final.md" not in events
    assert "MidTerm" not in "".join(events.values())


def test_an_assignments_details_ride_both_of_its_rows(monkeypatch):
    # The schedule reaches the due row through `map: "due_event"`, so the sub-hash cannot
    # see its parent's copy and needs its own.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    entry = AssignmentEntry(
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        course_source_repo="assignment-1-f2026",
        details="Closed form first, then gradient descent.",
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        entry.due_datetime,
        found=("assignment-1", entry),
    )
    page = yaml.safe_load(out.split("---\n")[1])
    assert page["details"] == "Closed form first, then gradient descent."
    assert page["due_event"]["details"] == "Closed form first, then gradient descent."


def test_a_multi_paragraph_details_block_survives_the_due_rows_nesting(monkeypatch):
    # A block scalar at column 0 would end the front matter or reparent the key; the due
    # row's copy is indented under `due_event:` and must still be legal YAML.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    entry = AssignmentEntry(
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        course_source_repo="assignment-1-f2026",
        details="First paragraph.\n\nSecond paragraph.",
    )
    out = site._assignment_entry(
        "Course",
        "Semester-f2026",
        "assignment-1-f2026",
        entry.due_datetime,
        found=("assignment-1", entry),
    )
    page = yaml.safe_load(out.split("---\n")[1])
    assert page["due_event"]["details"] == "First paragraph.\n\nSecond paragraph.\n"


def test_a_tbc_assignment_marks_both_rows_and_moves_neither_date(monkeypatch):
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")

    def rendered(tbc: bool) -> dict:
        entry = AssignmentEntry(
            due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
            course_source_repo="assignment-1-f2026",
            grading_datetime=datetime(2026, 10, 15, 23, 59, 59, tzinfo=BERLIN),
            tbc=tbc,
        )
        out = site._assignment_entry(
            "Course",
            "Semester-f2026",
            "assignment-1-f2026",
            entry.due_datetime,
            datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
            found=("assignment-1", entry),
            now=datetime(2026, 9, 23, tzinfo=BERLIN),
        )
        return yaml.safe_load(out.split("---\n")[1])

    marked, plain = rendered(True), rendered(False)
    assert marked["tbc"] is True and marked["due_event"]["tbc"] is True
    assert "tbc" not in plain and "tbc" not in plain["due_event"]
    # Nothing but the mark differs: the deadline the theme prints is the deadline.
    assert marked["date"] == plain["date"]
    assert marked["due_event"]["date"] == plain["due_event"]["date"]


def test_an_assignment_kept_off_the_site_gets_no_page_and_no_rows(
    monkeypatch, tmp_path
):
    # The site is told nothing; everything else about the assignment runs as written. Its
    # due row is a sub-hash of this page, so it goes with it - the theme has no way to
    # render one of an entry's rows and not the other.
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                show_on_site=False,
            ),
            "assignment-2": AssignmentEntry(
                course_source_repo="assignment-2-f2026",
                due_datetime=datetime(2026, 10, 27, 23, 59, 59, tzinfo=BERLIN),
            ),
        }
    )
    pages = _plan(
        monkeypatch,
        tmp_path,
        sched,
        assignments=["assignment-1-f2026", "assignment-2-f2026"],
    ).collections["_assignments"]
    # Hidden even though discovery found its template: the plan is where faculty say what
    # the site shows. `02-`, not `01-`: the hidden one's ordinal stays spent (below).
    assert list(pages) == ["02-assignment-2.md"]


def test_hiding_one_assignment_leaves_the_others_where_they_were(monkeypatch, tmp_path):
    # The ordinal is the position in the FULL list, so hiding one mid-term must not
    # renumber the assignments after it: their pages are published URLs students have
    # bookmarked and the gradebook links, and `03-...` becoming `02-...` breaks every one.
    # The same ordinal also synthesises the fortnightly fallback due date, so renumbering
    # pulls an undated assignment's placeholder deadline two weeks earlier as well.
    def plan(hide_the_middle_one: bool):
        sched = Schedule(
            semester_start=date(2026, 9, 1),
            assignments={
                "assignment-1": AssignmentEntry(
                    course_source_repo="assignment-1-f2026",
                    due_datetime=datetime(2026, 10, 6, 23, 59, 59, tzinfo=BERLIN),
                ),
                "assignment-2": AssignmentEntry(
                    course_source_repo="assignment-2-f2026",
                    due_datetime=datetime(2026, 10, 20, 23, 59, 59, tzinfo=BERLIN),
                    show_on_site=not hide_the_middle_one,
                ),
            },
        )
        # assignment-3 is DISCOVERED and unplanned, so its deadline is the synthesised
        # fortnightly one - counted off the very ordinal this test is about.
        return _plan(
            monkeypatch,
            tmp_path,
            sched,
            assignments=[
                "assignment-1-f2026",
                "assignment-2-f2026",
                "assignment-3-f2026",
            ],
        ).collections["_assignments"]

    shown, hidden = plan(False), plan(True)
    assert list(shown) == [
        "01-assignment-1.md",
        "02-assignment-2.md",
        "03-assignment-3.md",
    ]
    assert list(hidden) == ["01-assignment-1.md", "03-assignment-3.md"]
    # Same page, byte for byte: the hidden neighbour changed nothing about it, including
    # the fallback deadline its front matter carries.
    assert hidden["03-assignment-3.md"] == shown["03-assignment-3.md"]


def test_the_archive_row_can_be_renamed_and_marked_provisional():
    out = site._archive_entry(
        ArchiveRow(
            when=date(2027, 2, 16),
            title="Repositories frozen",
            details="Read-only from {date}.",
            tbc=True,
        ),
        date(2027, 2, 1),
    )
    assert 'title: "Repositories frozen"' in out
    assert "tbc: true" in out
    # Display only - the row still dates the freeze where the plan put it.
    assert "date: 2027-02-16T09:00:00" in out


def test_a_team_member_is_published_as_a_salted_digest_of_their_handle():
    # The page's script hashes its reader's saved handle the same way to recognise their
    # team, so this vector is the contract between the two sides: sha256 of
    # `<semester org>:<handle, lower-cased>`, hex.
    vector = "49565f39eed5ad0a289c5291fa1b3540c44ccd9205c0c78550ca21ab1d328dc8"
    assert site.member_digest("Cohort-f2026", "ada-l") == vector
    assert site.member_digest("Cohort-f2026", "Ada-L") == vector
    # Salted with the org: the same student is a different digest in another semester.
    assert site.member_digest("Semester-s2027", "ada-l") != vector
