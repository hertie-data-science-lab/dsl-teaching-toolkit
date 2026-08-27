"""site.py schedule wiring: the cohort website's rows take their dates and their types
from schedule.yml (not a synthesised weekly guess), joined to the released folders by
ordinal AND section - a week's lecture and its lab are separate rows. A wrong mapping here
silently mis-dates the whole schedule page, or hides a lab inside a lecture row."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import yaml

from dsl_course import site, utils
from dsl_course.schedule import AssignmentEntry, Deploy, Event, Release, Schedule

UTC = ZoneInfo("UTC")

BERLIN = ZoneInfo("Europe/Berlin")
END_OF_TERM = date(2026, 12, 18)


@pytest.fixture(autouse=True)
def _no_acting_login(monkeypatch):
    """_team_people asks who the sync is authenticated as, and the real lookup shells out
    to `gh` (green on an authenticated dev box, red in tokenless CI). None excludes
    nobody, which is what every test here but the bot-card one wants."""
    monkeypatch.setattr(site, "_acting_login", lambda: None)


def _sched(releases: list[Release]) -> Schedule:
    return Schedule(releases=releases)


def _row_dates(sched: Schedule) -> dict[tuple[str, str], datetime]:
    """The dating half of `_planned_sessions` - which row happens when."""
    return {key: row.when for key, row in site._planned_sessions(sched).items()}


def test_session_dates_maps_folder_ordinal_and_section_to_release_when():
    s = _sched(
        [
            Release(
                "week-2",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                deploy=[
                    Deploy("cm", "lectures/02_intro", "lectures", None),
                    Deploy("cm", "labs/02_x", "labs", None),
                ],
            ),
            Release(
                "week-1",
                datetime(2026, 9, 8, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/01_a", "lectures", "01_a")],
            ),
        ]
    )
    sw = _row_dates(s)
    assert sw[("2", "lecture")] == datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)
    assert sw[("2", "lab")] == datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)
    # keyed off the cohort_dest_path ordinal; a bare dest folder takes its section from
    # cohort_dest_repo
    assert sw[("1", "lecture")] == datetime(2026, 9, 8, 14, 0, tzinfo=BERLIN)


def test_session_dates_date_a_lab_row_from_its_own_release():
    # Monday's lecture and Wednesday's lab are two entries; the lab row must carry its own
    # time rather than inheriting the (earlier) lecture's.
    s = _sched(
        [
            Release(
                "lecture-3",
                datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/03_week-3", "materials", None)],
            ),
            Release(
                "lab-3",
                datetime(2026, 9, 17, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "labs/03_week-3", "materials", None)],
            ),
        ]
    )
    sw = _row_dates(s)
    assert sw[("3", "lecture")] == datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN)
    assert sw[("3", "lab")] == datetime(2026, 9, 17, 14, 0, tzinfo=BERLIN)


def test_session_dates_earliest_release_wins_for_a_row():
    s = _sched(
        [
            Release(
                "late",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "lectures", None)],
            ),
            Release(
                "early",
                datetime(2026, 9, 10, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "readings/02_y", "materials", None)],
            ),
        ]
    )
    # readings are lecture material, so both land on the same row - earliest wins
    assert _row_dates(s)[("2", "lecture")] == datetime(2026, 9, 10, 9, 0, tzinfo=BERLIN)


def test_session_dates_ignores_non_ordinal_deploys():
    s = _sched(
        [
            Release(
                "ds",
                datetime(2026, 10, 20, 9, 30, tzinfo=BERLIN),
                deploy=[
                    Deploy(
                        "data", "week7/housing.csv", "materials", "datasets/housing.csv"
                    )
                ],
            ),
        ]
    )
    assert _row_dates(s) == {}  # not a numbered session folder


# A RELEASED row - non-empty sources, so these pin the released branch rather than the
# placeholder one (they read `[]` before the placeholder branch existed, which silently
# moved their subject).
RELEASED = [("materials", "lectures", "02_week-2")]


def _row(when, **kw):
    """The plan's view of a row - what `_lecture_entry` renders from. Built here so a test
    states only the plan fields it is actually about."""
    return site._PlannedRow(when=when, **kw)


def test_lecture_entry_shows_real_time_from_a_datetime(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry(
        "Cohort", "2", _row(datetime(2026, 9, 15, 14, 30, tzinfo=BERLIN)), RELEASED
    )
    assert "date: 2026-09-15T14:30:00" in md
    assert "not released yet" not in md


def test_lecture_entry_falls_back_to_0900_for_a_bare_date(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry("Cohort", "2", _row(date(2026, 9, 15)), RELEASED)
    assert "date: 2026-09-15T09:00:00" in md


def test_lecture_entry_renders_a_lab_row_as_its_own_type(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    md = site._lecture_entry("Cohort", "3", _row(date(2026, 9, 17)), RELEASED, "lab")
    assert "type: lab" in md
    assert 'title: "Lab 3"' in md
    assert "Session 3" not in md
    lec = site._lecture_entry("Cohort", "3", _row(date(2026, 9, 15)), RELEASED)
    assert "type: lecture" in lec and 'title: "Session 3"' in lec


def test_only_the_unreleased_row_carries_the_theme_flag(monkeypatch):
    # The prose says it, but a flag is what lets the theme badge or grey the row - and
    # what tells a placeholder apart from a released folder that holds no files.
    monkeypatch.setattr(site, "_session_files", lambda *a: [])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    assert "unreleased: true" not in site._lecture_entry(
        "Cohort", "2", _row(date(2026, 9, 15)), RELEASED
    )
    assert "unreleased: true" in site._lecture_entry(
        "Cohort", "2", _row(date(2026, 9, 15)), []
    )


def test_session_dates_use_the_event_datetime_not_the_deploy_datetime():
    # The site announces the class; the copies may ship on their own clocks.
    s = Schedule(
        releases=[
            Release(
                "week-2",
                datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
                deploy=[
                    Deploy(
                        "cm",
                        "lectures/02_intro",
                        "materials",
                        None,
                        deploy_datetime=datetime(2026, 9, 15, 9, 0, tzinfo=BERLIN),
                    )
                ],
            )
        ]
    )
    assert _row_dates(s)[("2", "lecture")] == datetime(
        2026, 9, 15, 10, 0, tzinfo=BERLIN
    )


def test_event_entry_renders_a_display_only_schedule_row():
    e = Event("project-clinic", "", datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN))
    out = site._event_entry(e, END_OF_TERM)
    assert "type: special_event" in out
    assert 'name: "Project Clinic"' in out  # prettified from the label
    assert "date: 2026-11-17T10:00:00" in out
    assert 'description: ""' in out
    titled = Event(
        "project-clinic",
        "Bring your data",
        datetime(2026, 11, 17, 10, 0, tzinfo=BERLIN),
    )
    assert 'name: "Bring your data"' in site._event_entry(titled, END_OF_TERM)


def test_event_entry_renders_an_exam_as_an_exam_row():
    e = Event("mid-term", "MidTerm Exam", date(2026, 11, 3), type="exam")
    out = site._event_entry(e, END_OF_TERM)
    assert "type: exam" in out
    assert 'description: "MidTerm Exam"' in out
    assert "date: 2026-11-03T09:00:00" in out  # whole day -> the placeholder time
    assert "name:" not in out  # the exam row reads `description`, not `name`


def test_event_entry_title_falls_back_to_the_prettified_label():
    e = Event("resit_exam", "", date(2026, 12, 20), type="exam")
    assert 'description: "Resit Exam"' in site._event_entry(e, END_OF_TERM)


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


def test_term_date_entry_hides_the_placeholder_time():
    out = site._term_date_entry("Term starts", date(2026, 9, 7))
    assert "type: term_date" in out
    assert "date: 2026-09-07T09:00:00" in out
    assert "hide_time: true" in out  # a term boundary is a whole day, not a 09:00 slot
    assert 'name: "Term starts"' in out  # the name is the row's only text
    assert 'description: ""' in out


def test_assignment_entry_dates_the_released_row_from_the_handout(monkeypatch):
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nBrief."
    )
    out = site._assignment_entry(
        "Course",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 23, tzinfo=BERLIN),
    )
    # the entry's own row is the "released!" row; the due row lives in due_event
    assert "date: 2026-09-22T09:00:00" in out.split("due_event:")[0]
    assert "    date: 2026-10-13T23:59:59" in out
    # the theme's due row is already labelled "due", so the description just names it
    assert '    description: "Assignment 1"' in out


def test_assignment_entry_falls_back_to_the_due_date_without_a_handout(monkeypatch):
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    out = site._assignment_entry(
        "Course",
        "assignment-2-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-2"}),
    )
    assert out.count("date: 2026-11-10T23:59:00") == 2  # both rows on the due date


def test_an_unhanded_out_assignment_is_absent_from_the_site(monkeypatch):
    # The template repo exists from the day faculty write the assignment; publishing its
    # README on sight put the whole brief on the PUBLIC cohort site weeks before hand-out,
    # while the scheduler was still correctly holding the student repos back. Withholding
    # only the brief still announced the assignment, so a pending one gets no entry at all
    # - no page, no released row, no due row. The plan stays in schedule.yml.
    def _no_reads(*a, **k):
        raise AssertionError("the embargoed README must not be read at all")

    monkeypatch.setattr(site, "get_file_content", _no_reads)
    assert (
        site._assignment_entry(
            "Course",
            "assignment-1-f2026",
            datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
            datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
            now=datetime(2026, 9, 21, tzinfo=BERLIN),
        )
        is None
    )


def test_a_passed_handout_inlines_the_brief(monkeypatch):
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
        now=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),  # the moment itself is released
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_a_manual_handout_releases_the_brief_with_no_date_pinned(monkeypatch):
    # The manual button's documented mode pins no handout_datetime at all, so the plan
    # cannot say this went out - the frozen cohort template repo it creates is what says
    # so. Gating on the plan alone published these briefs from the day the template
    # existed, which is the whole bug.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 2\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "assignment-2-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-2"}),
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_an_assignment_with_no_handout_on_record_is_withheld(monkeypatch):
    # Neither signal fires: no cohort template repo, no pin. Withholding is the safe
    # direction - the assignment appears the moment either says it went out.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 2\nThe brief."
    )
    assert (
        site._assignment_entry("Course", "assignment-2-f2026", date(2026, 11, 10))
        is None
    )


def test_an_early_manual_release_beats_a_pin_still_in_the_future(monkeypatch):
    # Faculty pinned a later date, then released early. The repos exist, so the brief is
    # already with the students; the site must not go on claiming it is embargoed.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    out = site._assignment_entry(
        "Course",
        "assignment-1-f2026",
        datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        datetime(2026, 10, 20, 14, 0, tzinfo=BERLIN),
        handed_out=frozenset({"assignment-1"}),
        now=datetime(2026, 10, 10, tzinfo=BERLIN),
    )
    assert "The brief." in out
    assert "unreleased: true" not in out


def test_handed_out_keys_on_the_cohort_dest_repo_not_the_slug(monkeypatch):
    # assign.py freezes the cohort template under `cohort_dest_repo` when an entry renames
    # it, so the gate must look the assignment up under the same name it was created with.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# Assignment 1\nThe brief."
    )
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                cohort_dest_repo="homework-1",
            )
        }
    )
    args = ("Course", "assignment-1-f2026", date(2026, 10, 13))
    assert "The brief." in site._assignment_entry(
        *args, sched=sched, handed_out=frozenset({"homework-1"})
    )
    # the slug is NOT the name it was frozen under, so it must not open the gate
    assert (
        site._assignment_entry(
            *args, sched=sched, handed_out=frozenset({"assignment-1"})
        )
        is None
    )


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
    due, handout = site._assignment_dates(sched, "assignment-1-f2026", date(2026, 1, 1))
    assert due == datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN)
    assert handout == datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN)
    # unscheduled: the synthesised fallback, and no handout row
    assert site._assignment_dates(sched, "assignment-9-f2026", date(2026, 1, 1)) == (
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
        "_sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site.seed, "discover_cohort_repos", lambda orgs: [])
    monkeypatch.setattr(
        site.seed, "discover_release_sources", lambda org, repos: list(sources)
    )
    monkeypatch.setattr(
        site.seed, "discover_assignments", lambda org: list(assignments)
    )
    monkeypatch.setattr(
        site, "discover_handed_out_assignments", lambda org: frozenset(handed_out)
    )
    monkeypatch.setattr(site, "_yaml_file", lambda *a: {})
    monkeypatch.setattr(site.schedule, "load", lambda org: sched)
    monkeypatch.setattr(site, "_people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(
        site, "_session_files", files or (lambda org, repo, subpath, folder: [])
    )
    # The memoised tree, which `_session_links` reads the default branch from to build its
    # folder-link URLs. Stubbed even where `_session_files` is faked: without it the fake
    # covers the file list but the branch lookup still reaches GitHub, which passes on an
    # authenticated dev box and fails in CI.
    monkeypatch.setattr(site, "_repo_tree", lambda org, repo: ("main", ()))
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    assert site.sync_site("Course-Org", "Cohort-f2026") == 0
    return captured["plan"]


def test_cohort_site_links_back_to_the_cohort_org(monkeypatch, tmp_path):
    # The footer's GitHub link (site.github_org) is the cohort site's only click-back; it
    # must point at THIS cohort org, not the template default or the course org.
    plan = _plan(monkeypatch, tmp_path, Schedule())
    assert plan.config["github_org"] == "Cohort-f2026"


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
    # every cohort site. Undeclared, it must not be written at all - the site repo keeps
    # whatever blurb it has.
    captured = {}
    monkeypatch.setattr(
        site,
        "_sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site.seed, "discover_cohort_repos", lambda orgs: [])
    monkeypatch.setattr(site.seed, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site.seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(
        site, "discover_handed_out_assignments", lambda org: frozenset()
    )
    monkeypatch.setattr(site.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(site, "_people_yaml", lambda *a, **k: "people: []\n")

    monkeypatch.setattr(site, "_yaml_file", lambda *a: {})
    assert site.sync_site("Course-Org", "Cohort-f2026") == 0
    assert "course_description" not in captured["plan"].config

    monkeypatch.setattr(
        site, "_yaml_file", lambda *a: {"course_description": "Nets, from 0."}
    )
    assert site.sync_site("Course-Org", "Cohort-f2026") == 0
    cfg = site._set_config(
        'course_name: "x"\ncourse_description: "old"\ncourse_code: "y"\n',
        "course_description",
        captured["plan"].config["course_description"],
    )
    assert yaml.safe_load(cfg)["course_description"] == "Nets, from 0."
    assert yaml.safe_load(cfg)["course_code"] == "y"  # neighbours untouched


def test_set_config_writes_one_line_over_a_block_scalar():
    # A faculty `>` block in dsl-course.yml, and/or one already in _config.yml: either way
    # the result must stay valid YAML on one line, its body not stranded as loose text.
    cfg = site._set_config(
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
        "_sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site.seed, "discover_cohort_repos", lambda orgs: [])
    monkeypatch.setattr(site.seed, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site.seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(
        site, "discover_handed_out_assignments", lambda org: frozenset()
    )
    monkeypatch.setattr(site, "_yaml_file", lambda *a: {"course_name": "Deep Learning"})
    monkeypatch.setattr(site, "_people_yaml", lambda *a, **k: "people: []\n")
    # the REAL schedule.load, fed the malformed file
    monkeypatch.setattr(
        site.schedule, "get_file_content", lambda org, repo, path: MALFORMED_SCHEDULE
    )

    assert site.sync_site("Course-Org", "Cohort-f2026") == 0

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
    assert "lab 2 are not released yet" in lectures["lab-02.md"]
    assert "session 2 are not released yet" in lectures["session-02.md"]


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
    # The row says what is coming and where, and stops there - naming the cohort org as
    # well made the schedule table's cell two clauses long for no reader's benefit.
    assert "`Cohort-f2026`" not in body


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
        files=lambda org, repo, subpath, folder: [("lab.pdf", "https://x/lab.pdf")],
    )
    body = plan.collections["_lectures"]["lab-02.md"]
    assert 'name: "lab - lab.pdf"' in body
    assert "not released yet" not in body


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
    assert "not released yet" in plan.collections["_lectures"]["session-02.md"]
    assert "not released yet" not in plan.collections["_lectures"]["lab-05.md"]


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
        files=lambda org, repo, subpath, folder: [
            (f"{subpath}.pdf", f"https://x/{subpath}")
        ],
    )
    session = plan.collections["_lectures"]["session-02.md"]
    assert 'name: "lecture - lectures.pdf"' in session
    assert "lab - " not in session
    assert 'name: "lab - labs.pdf"' in plan.collections["_lectures"]["lab-02.md"]


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
    assert 'description: "MidTerm Exam"' in events["midterm.md"]
    assert 'description: "Final Exam"' in events["final.md"]
    assert "type: special_event" in events["01-project-clinic.md"]


def test_term_date_rows_only_when_the_schedule_pins_the_bounds(monkeypatch, tmp_path):
    plan = _plan(
        monkeypatch,
        tmp_path,
        Schedule(semester_start=date(2026, 9, 7), semester_end=date(2026, 12, 18)),
    )
    events = plan.collections["_events"]
    assert 'name: "Term starts"' in events["term-start.md"]
    assert "date: 2026-09-07T09:00:00" in events["term-start.md"]
    assert 'name: "Term ends"' in events["term-end.md"]
    assert "date: 2026-12-18T09:00:00" in events["term-end.md"]

    unbounded = _plan(monkeypatch, tmp_path, Schedule())
    assert "term-start.md" not in unbounded.collections["_events"]
    assert "term-end.md" not in unbounded.collections["_events"]


# -------------------------------------------------------- dest_repo mismatch (fix 4)


def test_assignment_entry_names_the_cohort_dest_repo_not_the_course_repo(monkeypatch):
    # assign.py provisions `<cohort_dest_repo or slug>-<handle>`; the site must name the
    # same repo (and title the page from it), not the course repo minus its tag.
    monkeypatch.setattr(site, "get_file_content", lambda *a, **k: "")
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
                cohort_dest_repo="homework-1",
            )
        }
    )
    out = site._assignment_entry(
        "Course",
        "assignment-1-f2026",
        date(2026, 10, 13),
        sched=sched,
        handed_out=frozenset({"homework-1"}),
    )
    assert "`homework-1-<your-handle>`" in out
    assert 'title: "Homework 1"' in out


def test_the_site_build_gates_a_brief_on_what_the_cohort_actually_holds(
    monkeypatch, tmp_path
):
    # End-to-end through sync_site, not just the renderer: the gate is worthless if the
    # build forgets to pass what the cohort org holds. (`_plan` blanks every file read, so
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
    # No page, so no released row and no due row either - the whole collection is empty.
    assert withheld == {}

    out = _plan(monkeypatch, tmp_path, **args, handed_out=["assignment-1"]).collections[
        "_assignments"
    ]
    assert "01-assignment-1-f2026.md" in out


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
    assert list(out) == ["02-assignment-2-f2026.md"]


# ---------------------------------------------- fail-loud reads (fixes 5 and 6)


def test_session_files_missing_tree_is_empty(monkeypatch):
    monkeypatch.setattr(site, "get_default_branch", lambda org, repo: "main")
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "HTTP 404: Not Found"))
    assert site._session_files("Cohort-f2026", "materials", "lectures", "03_x") == []


def test_session_files_fetch_failure_raises_rather_than_stripping_the_site(monkeypatch):
    # A swallowed failure returned (), the site republished with every material link gone.
    monkeypatch.setattr(site, "get_default_branch", lambda org, repo: "main")
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "HTTP 502: bad gateway"))
    with pytest.raises(RuntimeError):
        site._session_files("Cohort-f2026", "materials", "lectures", "03_x")


def test_team_people_missing_team_is_empty(monkeypatch):
    monkeypatch.setattr(site, "gh", lambda *a, **k: (1, "HTTP 404: Not Found"))
    assert site._team_people("Course", "instructors") == []


def test_team_people_read_failure_raises_rather_than_wiping_the_team(monkeypatch):
    monkeypatch.setattr(site, "gh", lambda *a, **k: (1, "HTTP 500: boom"))
    with pytest.raises(RuntimeError):
        site._team_people("Course", "instructors")


def _member_gh(profile: tuple[int, str]):
    """Fake gh: the team lists one member, whose profile lookup returns `profile`."""

    def fake(*args, **kwargs):
        return (0, "jane\n") if any(a.endswith("/members") for a in args) else profile

    return fake


def test_team_people_skips_a_deleted_account_but_says_so(monkeypatch, capsys):
    monkeypatch.setattr(site, "gh", _member_gh((1, "gh: Not Found (HTTP 404)")))
    assert site._team_people("Course", "instructors") == []
    assert "jane" in capsys.readouterr().out  # one card fewer, not silently


def test_team_people_per_member_failure_raises_rather_than_dropping_one_card(
    monkeypatch,
):
    # The team read is fail-loud; the per-MEMBER read used to swallow everything, so a
    # transient error republished the site one instructor short with nothing to show for it.
    monkeypatch.setattr(site, "gh", _member_gh((1, "gh: HTTP 502 Bad Gateway")))
    with pytest.raises(RuntimeError, match="could not read the GitHub profile of jane"):
        site._team_people("Course", "instructors")


def test_team_people_never_renders_the_syncs_own_bot_account(monkeypatch, capsys):
    # The bot is in `instructors` for the access it needs, and it rendered on the public
    # site as a member of the teaching team. Its own profile lookup must not even happen.
    def fake(*args, **kwargs):
        if any(a.endswith("/members") for a in args):
            return (0, "hertie-dsl-bot\njane\n")
        assert "users/hertie-dsl-bot" not in args
        return (0, "Jane\thttps://a/j.png\thttps://gh/jane")

    monkeypatch.setattr(site, "gh", fake)
    monkeypatch.setattr(
        site, "_acting_login", lambda: "Hertie-DSL-Bot"
    )  # logins fold case
    assert site._team_people("Course", "instructors") == [
        ("Jane", "https://a/j.png", "https://gh/jane")
    ]
    assert "hertie-dsl-bot" in capsys.readouterr().out  # skipped out loud


def _bad_indent_error() -> yaml.YAMLError:
    """The REAL exception a bad indent in people.yml produces. load_yaml_config re-raises
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
    # A cohort's people.yml with one bad indent used to parse to `{}` - "nothing declared" -
    # and republish the site with every teaching-team card gone, green.
    err = _bad_indent_error()
    monkeypatch.setattr(site, "load_yaml_config", lambda *a: (_ for _ in ()).throw(err))
    with pytest.raises(yaml.YAMLError):
        site._yaml_file("Cohort-f2026", "classroom-config", "people.yml")


def test_yaml_file_reads_an_absent_file_as_nothing_declared(monkeypatch):
    monkeypatch.setattr(site, "load_yaml_config", lambda *a: None)
    assert site._yaml_file("Cohort-f2026", "classroom-config", "people.yml") == {}


def test_main_reports_a_malformed_config_as_one_line_not_a_traceback(
    monkeypatch, capsys
):
    # people.yml is web-editable, so faculty author bad indents directly. yaml.YAMLError
    # is not a RuntimeError, so it used to walk straight through main()'s guard and out as
    # a traceback in the Actions log.
    from dsl_course import seed

    err = _bad_indent_error()
    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-f2026"])
    monkeypatch.setattr(site, "sync_site", lambda *a: (_ for _ in ()).throw(err))
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--cohort-org", "Cohort-f2026"],
    )
    assert site.main() == 1
    assert "Traceback" not in capsys.readouterr().err


def test_main_refuses_a_cohort_this_course_org_never_registered(monkeypatch, capsys):
    # --cohort-org reaches main straight from a repository_dispatch's client_payload,
    # written by whoever holds a cohort's DSL_BOT_TOKEN - a lower trust tier than the
    # course org. Naming SOMEONE ELSE'S cohort would rebuild that cohort's site from this
    # dispatch, so the registry gets the last word.
    from dsl_course import seed

    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-f2026"])
    synced: list = []
    monkeypatch.setattr(site, "sync_site", lambda *a: synced.append(a) or 0)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--cohort-org", "Other-f2026"],
    )
    assert site.main() == 1
    assert synced == []
    assert "not registered under Course" in capsys.readouterr().err


def test_main_matches_a_registered_cohort_case_insensitively(monkeypatch):
    # GitHub org names are case-insensitive; a case difference must not read as a
    # cross-cohort dispatch.
    from dsl_course import seed

    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-F2026"])
    synced: list = []
    monkeypatch.setattr(site, "sync_site", lambda *a: synced.append(a) or 0)
    monkeypatch.setattr(
        "sys.argv",
        ["site", "sync", "--course-org", "Course", "--cohort-org", "cohort-f2026"],
    )
    assert site.main() == 0
    assert synced == [("Course", "cohort-f2026")]


def test_all_cohorts_loop_survives_one_cohorts_raised_failure(monkeypatch, capsys):
    # The lesson PR #151/#146 applied to the nightly refresh: the single try used to wrap
    # the whole loop, so one cohort's raise skipped every LATER cohort's site on the 06:00
    # cron. main() imports discover_cohorts from .seed at call time - patch it at source.
    from dsl_course import seed

    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-A", "Cohort-B"])
    seen: list[str] = []

    def fake_sync(course, cohort):
        seen.append(cohort)
        if cohort == "Cohort-A":
            raise _bad_indent_error()
        return 0

    monkeypatch.setattr(site, "sync_site", fake_sync)
    monkeypatch.setattr(
        "sys.argv", ["site", "sync", "--course-org", "Course", "--all-cohorts"]
    )
    assert site.main() == 1
    assert seen == ["Cohort-A", "Cohort-B"]
    assert "Cohort-A" in capsys.readouterr().err


# --------------------------------------------- front-matter escaping (fix 7)


def test_front_matter_survives_a_backslash_in_a_title(monkeypatch):
    # `# \sigma review` is an invalid YAML escape unquoted - the whole site build fails
    # (ScannerError) unless every scalar is routed through _q.
    monkeypatch.setattr(
        site, "get_file_content", lambda *a, **k: "# \\sigma review\nBody"
    )
    out = site._assignment_entry(
        "Course",
        "assignment-1-f2026",
        date(2026, 11, 10),
        handed_out=frozenset(
            {"assignment-1"}
        ),  # the README title is only read once out
    )
    front = yaml.safe_load(out.split("---")[1])  # must parse, no ScannerError
    assert "sigma" in front["title"]


def test_links_block_survives_a_backslash_in_a_filename():
    block = site._links_block([("lectures", [("notes \\x.pdf", "https://x/1")])])
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
        "assignment-1-f2026",
        date(2026, 11, 10),
        handed_out=frozenset({"assignment-1"}),  # the README is only inlined once out
    )
    assert "{% raw %}" in out and "{% endraw %}" in out


# --------------------------------------------- tz-aware display (fix 8)


def test_iso_when_prints_the_datetime_it_is_given_offset_free():
    # The cohort-tz conversion happens ONCE, in schedule's parser (below), so every
    # datetime reaching the renderers is already cohort wall-clock: printing it is just
    # dropping the offset, with no zone for a renderer to forget to pass.
    assert site._iso_when(datetime(2026, 9, 15, 12, 0, tzinfo=BERLIN)) == (
        "2026-09-15T12:00:00"
    )
    assert site._iso_when(datetime(2026, 9, 15, 10, 0, tzinfo=UTC)) == (
        "2026-09-15T10:00:00"
    )


def test_a_written_offset_reaches_the_site_as_the_cohort_wall_clock_time():
    # End to end: 10:00 UTC in a Berlin cohort (CEST, +2 in September) is shown as 12:00 -
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
def test_a_row_carries_the_title_and_description_the_plan_declared(monkeypatch):
    monkeypatch.setattr(site, "_session_files", lambda *a: [("s.pdf", "https://x/1")])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    out = site._lecture_entry(
        "Cohort-f2026",
        "1",
        _row(
            datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN),
            subtitle="Probability Theory",
            description="Sample spaces and Bayes' rule.",
        ),
        RELEASED,
    )
    # `title` stays the ordinal - what the theme has always assumed it is - and the
    # declared name rides `subtitle` beside it.
    assert 'title: "Session 1"' in out
    assert 'subtitle: "Probability Theory"' in out
    assert 'description: "Sample spaces and Bayes\' rule."' in out


def test_a_row_omits_the_declared_fields_it_was_not_given(monkeypatch):
    # Omitted, not written blank: the theme tests for them, so an empty string would
    # render an empty line where there should be nothing at all.
    monkeypatch.setattr(site, "_session_files", lambda *a: [("s.pdf", "https://x/1")])
    monkeypatch.setattr(site, "_repo_tree", lambda o, r: ("main", ()))
    out = site._lecture_entry(
        "Cohort-f2026", "1", _row(datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN)), RELEASED
    )
    assert "subtitle:" not in out and "description:" not in out


def test_an_unreleased_row_still_says_what_the_session_is_about():
    out = site._lecture_entry(
        "Cohort-f2026",
        "3",
        _row(
            datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
            dests={"materials/lectures/03_week-3": None},
            subtitle="Expectation",
            description="Linearity of expectation.",
        ),
        [],
    )
    # What the session covers is known the day the plan is written, so it is published
    # then - the term reads as a syllabus from day one. Only the FILES wait for release,
    # which is what the body says.
    assert 'subtitle: "Expectation"' in out
    assert 'description: "Linearity of expectation."' in out
    assert "will appear in `materials/lectures/03_week-3` when released." in out


def test_the_plan_carries_a_rows_title_description_and_readings(monkeypatch):
    s = _sched(
        [
            Release(
                "lecture-1",
                datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN),
                deploy=[
                    Deploy("cm", "lectures/01_a", "materials", None),
                    Deploy("cm", "readings/01_a", "materials", None),
                ],
                title="Probability Theory",
                description="Sample spaces.",
            )
        ]
    )
    row = site._planned_sessions(s)[("1", "lecture")]
    assert row.subtitle == "Probability Theory"
    assert row.description == "Sample spaces."
    # Readings are lecture material, so they land on this row - and the row knows a
    # reading list is coming even before it ships.
    assert row.readings_planned is True


def test_the_earliest_entry_naming_a_row_is_the_one_that_titles_it():
    # Same rule as the row's DATE: releases are sorted by event_datetime, so the entry the
    # row takes its date from is the entry it takes its name from.
    s = _sched(
        [
            Release(
                "late",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "materials", None)],
                title="Second billing",
            ),
            Release(
                "early",
                datetime(2026, 9, 10, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "readings/02_y", "materials", None)],
                title="Random Variables",
            ),
        ]
    )
    assert site._planned_sessions(s)[("2", "lecture")].subtitle == "Random Variables"


def test_a_silent_release_neither_dates_nor_names_the_row_it_lands_in():
    # The case the flag exists for: readings released a week ahead of the class. They land
    # in session 2's row (readings are lecture material), and without the flag the row
    # would take the EARLIEST entry's date and title - moving "Session 2" to the 10th and
    # renaming it after a reading list.
    s = _sched(
        [
            Release(
                "lecture-2",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "materials", None)],
                title="Random Variables",
            ),
            Release(
                "readings-2",
                datetime(2026, 9, 10, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "readings/02_x", "materials", None)],
                title="Week 2 reading list",
                show_on_site=False,
            ),
        ]
    )
    row = site._planned_sessions(s)[("2", "lecture")]
    assert row.when == datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)
    assert row.subtitle == "Random Variables"
    # The silent entry contributed nothing at all - not even the pending-readings note,
    # which would otherwise promise a reading list on a row that never mentioned one.
    assert row.readings_planned is False
    assert "materials/readings/02_x" not in row.dests


def test_a_silent_release_raises_no_row_of_its_own():
    # Nothing else touches session 3, so with the flag honoured the plan declares no row
    # for it at all - the files still reach students, and discovery still links them into
    # whatever row they land in once released.
    s = _sched(
        [
            Release(
                "errata-3",
                datetime(2026, 9, 20, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/03_x", "materials", None)],
                show_on_site=False,
            )
        ]
    )
    assert site._planned_sessions(s) == {}


def test_a_row_with_no_readings_in_the_plan_never_reports_them_pending():
    s = _sched(
        [
            Release(
                "lecture-1",
                datetime(2026, 9, 1, 8, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/01_a", "materials", None)],
            )
        ]
    )
    assert site._planned_sessions(s)[("1", "lecture")].readings_planned is False


# --------------------------------------------------------------- rows from a bare label
# docs/07: "a row appears as soon as you write it, not when it ships". An entry that stages
# nothing yet has no deploy destination to key a row off, so its own label places it.


def test_an_entry_with_no_deploy_still_raises_its_row_from_its_label():
    s = _sched(
        [
            Release(
                "lecture-12",
                datetime(2026, 10, 20, 10, 0, tzinfo=BERLIN),
                title="Tutorial presentations",
                description="Students present their topic.",
            )
        ]
    )
    row = site._planned_sessions(s)[("12", "lecture")]
    assert row.when == datetime(2026, 10, 20, 10, 0, tzinfo=BERLIN)
    assert row.subtitle == "Tutorial presentations"
    assert row.description == "Students present their topic."
    # nothing staged, so nothing to name as a destination - the row says only that its
    # materials are not released yet
    assert row.dests == {}


def test_a_deployless_lab_label_raises_a_lab_row_not_a_lecture_row():
    s = _sched([Release("lab-04", datetime(2026, 10, 22, 14, 0, tzinfo=BERLIN))])
    assert set(site._planned_sessions(s)) == {("4", "lab")}


def test_a_deployless_entry_whose_label_names_no_session_raises_nothing():
    # `course-intro` opens the course without being a numbered session
    s = _sched([Release("course-intro", datetime(2026, 8, 31, 9, 0, tzinfo=BERLIN))])
    assert site._planned_sessions(s) == {}


def test_the_label_fallback_never_fires_when_a_deploy_already_placed_the_row():
    # label says 12, the deploy lands in session 3 - the deploy wins and no phantom
    # session-12 row appears beside it
    s = _sched(
        [
            Release(
                "lecture-12",
                datetime(2026, 10, 20, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/03_x", "materials", None)],
            )
        ]
    )
    assert set(site._planned_sessions(s)) == {("3", "lecture")}


def test_an_assignment_entry_never_becomes_a_session_row_via_its_label():
    # assignment out/due rows are built elsewhere; `assignment-1` must not claim session 1
    s = _sched(
        [
            Release(
                "assignment-1",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                assignment="assignment-1",
            )
        ]
    )
    assert site._planned_sessions(s) == {}


def test_a_silent_deployless_entry_still_raises_nothing():
    s = _sched(
        [
            Release(
                "lecture-9",
                datetime(2026, 9, 29, 10, 0, tzinfo=BERLIN),
                show_on_site=False,
            )
        ]
    )
    assert site._planned_sessions(s) == {}
