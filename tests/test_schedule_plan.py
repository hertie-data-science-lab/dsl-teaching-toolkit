"""schedule_plan: what the release plan says a course's session rows are.

The dating and naming half of the website - which numbered rows exist, when each happens,
what it is called and where its materials land. A wrong mapping here silently mis-dates
the whole schedule page, or hides a lab inside a lecture row.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from dsl_course import schedule_plan
from dsl_course.schedule import Deploy, Release, Schedule

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def _sched(releases: list[Release]) -> Schedule:
    return Schedule(releases=releases)


def _row_dates(sched: Schedule) -> dict[tuple[str, str], datetime]:
    """The dating half of `planned_sessions` - which row happens when."""
    return {key: row.when for key, row in schedule_plan.planned_sessions(sched).items()}


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
    row = schedule_plan.planned_sessions(s)[("1", "lecture")]
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
    assert (
        schedule_plan.planned_sessions(s)[("2", "lecture")].subtitle
        == "Random Variables"
    )


def test_a_silent_release_neither_dates_nor_names_the_row_but_does_fill_its_dests():
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
    row = schedule_plan.planned_sessions(s)[("2", "lecture")]
    assert row.when == datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)
    assert row.subtitle == "Random Variables"
    # What silence withholds is the DATE and the NAME, not the destinations: the row still
    # learns readings are planned for it, so an unreleased session can say where they will
    # appear. Note the silent entry is the EARLIER of the two, so it is reached first - the
    # fold has to happen in a second pass or this is exactly the case that gets lost.
    assert row.readings_planned is True
    assert "materials/readings/02_x" in row.dests


def test_only_known_session_heads_raise_a_row_from_a_label():
    # The label is documented as never shown to students, and rows come from the ordinal
    # session folder a deploy lands in - the label is only a fallback for an entry that has
    # not shipped yet. `anything-N` used to read as a lecture, so a `bonus-1` entry both
    # invented a phantom Session 1 row AND folded into the real one, dragging its date and
    # title back. `readings-N` must not raise a lecture row either.
    assert schedule_plan.row_from_label("lecture-1") == ("1", "lecture")
    assert schedule_plan.row_from_label("lecture_01") == ("1", "lecture")
    assert schedule_plan.row_from_label("lab-01") == ("1", "lab")
    assert schedule_plan.row_from_label("labs-2") == ("2", "lab")
    for no_row in (
        "bonus-1",
        "quiz-2",
        "topic-3",
        "readings-01",
        "course-intro",
        "seed-syllabus",
        "01_lab",
    ):
        assert schedule_plan.row_from_label(no_row) is None, no_row


def test_a_bonus_entry_cannot_drag_a_real_sessions_date_and_title():
    # The concrete damage the whitelist prevents: `row.when = min(...)`, so an August
    # bonus entry folding into lecture-1 would publish Session 1 as August, under the
    # bonus entry's name.
    s = _sched(
        [
            Release(
                "lecture-1",
                datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/01_x", "materials", None)],
                title="Perceptrons",
            ),
            Release(
                "bonus-1",
                datetime(2026, 8, 20, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "extras/bonus", "materials", None)],
                title="Optional extra reading",
            ),
        ]
    )
    rows = schedule_plan.planned_sessions(s)
    row = rows[("1", "lecture")]
    assert row.when == datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)
    assert row.subtitle == "Perceptrons"
    # and the bonus entry raised no row of its own
    assert len(rows) == 1


def test_a_silent_readings_entry_reached_before_its_lecture_still_folds_in():
    # The ordering trap: releases are sorted by datetime and readings ship AHEAD of the
    # session, so the silent entry is always reached BEFORE the entry that raises its row.
    # A single-pass fold loses precisely the common case, which is why there are two.
    s = _sched(
        [
            Release(
                "readings-2",
                datetime(2026, 9, 1, 9, 0, tzinfo=BERLIN),  # a week EARLIER
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
    rows = schedule_plan.planned_sessions(s)
    assert list(rows) == [("2", "lecture")], "the silent entry raised a row of its own"
    row = rows[("2", "lecture")]
    assert row.when == datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN)  # not dragged back
    assert row.readings_planned is True
    assert "materials/readings/02_x" in row.dests


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
    assert schedule_plan.planned_sessions(s) == {}


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
    assert schedule_plan.planned_sessions(s)[("1", "lecture")].readings_planned is False


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
    row = schedule_plan.planned_sessions(s)[("12", "lecture")]
    assert row.when == datetime(2026, 10, 20, 10, 0, tzinfo=BERLIN)
    assert row.subtitle == "Tutorial presentations"
    assert row.description == "Students present their topic."
    # nothing staged, so nothing to name as a destination - the row says only that its
    # materials are not released yet
    assert row.dests == {}


def test_a_deployless_lab_label_raises_a_lab_row_not_a_lecture_row():
    s = _sched([Release("lab-04", datetime(2026, 10, 22, 14, 0, tzinfo=BERLIN))])
    assert set(schedule_plan.planned_sessions(s)) == {("4", "lab")}


def test_a_deployless_entry_whose_label_names_no_session_raises_nothing():
    # `course-intro` opens the course without being a numbered session
    s = _sched([Release("course-intro", datetime(2026, 8, 31, 9, 0, tzinfo=BERLIN))])
    assert schedule_plan.planned_sessions(s) == {}


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
    assert set(schedule_plan.planned_sessions(s)) == {("3", "lecture")}


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
    assert schedule_plan.planned_sessions(s) == {}


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
    assert schedule_plan.planned_sessions(s) == {}


def test_row_kind_splits_labs_from_lectures():
    assert schedule_plan.row_kind("labs") == "lab"
    for section in ("lectures", "readings", "faq", ""):
        assert schedule_plan.row_kind(section) == "lecture"
