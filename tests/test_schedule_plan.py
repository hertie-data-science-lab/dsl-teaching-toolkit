"""schedule_plan: the rows a semester's release plan declares.

One row per dated `releases:` entry, in date order, with its kind declared or inferred once
from where its first copy lands. Nothing is read off a folder name beyond that section.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from dsl_course import schedule_plan
from dsl_course.schedule import Deploy, Release, Schedule

BERLIN = ZoneInfo("Europe/Berlin")


def _at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=BERLIN)


def _rows(releases, aliases=schedule_plan._no_aliases, start=None):
    sched = Schedule(releases=releases, semester_start=start)
    return schedule_plan.planned_rows(sched, aliases)


def _kind(*deploys: Deploy, kind: str = "", aliases=schedule_plan._no_aliases):
    return schedule_plan.entry_kind(
        Release("x", _at(1), list(deploys), kind=kind), aliases
    )


def test_one_row_per_dated_entry_in_plan_order():
    rows = _rows(
        [
            Release("lecture-1", _at(7), [Deploy("cm", "lectures/01_a")]),
            Release("lab-1", _at(9, 14), [Deploy("cm", "labs/01_a")]),
            Release("drop-in", None, [], tbc=True),  # undated: no place on a dated list
        ]
    )
    assert [(r.key, r.kind, r.when) for r in rows] == [
        ("lecture-1", "lecture", _at(7)),
        ("lab-1", "lab", _at(9, 14)),
    ]


def test_a_row_is_dated_by_its_event_not_by_when_its_files_ship():
    (row,) = _rows(
        [Release("l", _at(15), [Deploy("cm", "lectures/02", deploy_datetime=_at(14))])]
    )
    assert row.when == _at(15)


def test_folders_without_an_ordinal_are_rows_like_any_other():
    rows = _rows(
        [
            Release("week-1", _at(7), [Deploy("cm", "week1")]),
            Release("intro", _at(8), [Deploy("cm", "lectures/part-a/intro")]),
            Release(
                "files", _at(9), [Deploy("cm", "dldemo/a.py", "materials", "code/a.py")]
            ),
        ]
    )
    assert [r.key for r in rows] == ["week-1", "intro", "files"]
    assert rows[2].dests == ["materials/code/a.py"]


def test_a_row_carries_its_name_details_date_marker_and_silence():
    (row,) = _rows(
        [
            Release(
                "readings-2",
                _at(7),
                [Deploy("cm", "readings/02_x")],
                title="Week 2",
                details="Two papers.",
                tbc=True,
                show_on_site=False,
            )
        ]
    )
    assert (row.subtitle, row.details, row.tbc, row.shown) == (
        "Week 2",
        "Two papers.",
        True,
        False,
    )


def test_a_rows_destinations_are_deduped_in_plan_order():
    (row,) = _rows(
        [
            Release(
                "l",
                _at(7),
                [
                    Deploy("cm", "lectures/01"),
                    Deploy("code", "a.py", "materials", "lectures/01"),
                    Deploy("cm", "labs/01"),
                ],
            )
        ]
    )
    assert row.dests == ["materials/lectures/01", "materials/labs/01"]


def test_a_declared_kind_wins_over_any_folder():
    assert _kind(Deploy("cm", "labs/01"), kind="drop-in") == ("drop-in", False)


def test_the_built_in_aliases_and_the_lecture_default():
    for section, kind in {
        "labs": "lab",
        "lab": "lab",
        "tutorials": "lab",
        "readings": "readings",
        "reading": "readings",
        "literature": "readings",
        "Readings": "readings",
        "lectures": "lecture",
        "quiz": "lecture",
        "code": "lecture",
    }.items():
        assert _kind(Deploy("cm", f"{section}/01")) == (kind, True), section


def test_a_repo_alias_wins_over_the_built_in_one():
    aliases = {"cm": {"quiz": "other", "labs": "drop-in"}}.get
    assert _kind(Deploy("cm", "quiz/q1.pdf"), aliases=lambda r: aliases(r, {})) == (
        "other",
        True,
    )
    assert _kind(Deploy("cm", "labs/01"), aliases=lambda r: aliases(r, {})) == (
        "drop-in",
        True,
    )
    # Another repo's aliases are not this one's.
    assert _kind(Deploy("x", "quiz/q1.pdf"), aliases=lambda r: aliases(r, {})) == (
        "lecture",
        True,
    )


def test_a_repo_per_kind_destination_is_its_own_section():
    # `01_x` at the root of the semester's `labs` repo: the repo is the section.
    assert _kind(Deploy("labs-f2026", "01_x", "labs")) == ("lab", True)
    assert _kind(Deploy("cm", "01_x", "readings")) == ("readings", True)


def test_the_first_copy_decides_a_mixed_entry():
    assert _kind(Deploy("cm", "labs/03"), Deploy("cm", "lectures/03")) == ("lab", True)


def test_an_entry_that_copies_nothing_yet_is_a_lecture_until_it_says_otherwise():
    assert _kind() == ("lecture", True)
    assert _kind(kind="lab") == ("lab", False)


def test_a_label_carries_its_number_at_either_end():
    for label, n in {
        "lecture_03": 3,
        "lab-09": 9,
        "01_lab": 1,
        "readings 12": 12,
        "course-intro": None,
        "week3": 3,
    }.items():
        assert schedule_plan.label_number(label) == n, label


def _shown(rows):
    return [(sr.row.key, sr.number, [r.key for r in sr.readings]) for sr in rows]


def test_numbers_come_from_the_entry_then_the_label_then_the_position():
    rows = _rows(
        [
            Release("intro", _at(1), [Deploy("cm", "lectures/a")]),
            Release("lecture-09", _at(2), [Deploy("cm", "lectures/b")]),
            Release("guest", _at(3), [Deploy("cm", "lectures/c")], number=20),
            Release("wrap-up", _at(4), [Deploy("cm", "lectures/d")]),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("intro", 1, []),
        ("lecture-09", 9, []),
        ("guest", 20, []),
        ("wrap-up", 4, []),
    ]


def test_a_silent_entry_is_no_row_and_renumbers_nothing():
    # Maths: a silent setup copy and a silent quiz between the lectures.
    rows = _rows(
        [
            Release("a", _at(1), [Deploy("cm", "lectures/a")]),
            Release("setup", _at(2), [Deploy("cm", "m4ds")], show_on_site=False),
            Release("b", _at(3), [Deploy("cm", "lectures/b")]),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [("a", 1, []), ("b", 2, [])]


def test_untitled_readings_attach_to_the_next_shown_lecture():
    rows = _rows(
        [
            Release("r1", _at(1), [Deploy("cm", "readings/01")], show_on_site=False),
            Release("l1", _at(3), [Deploy("cm", "lectures/01")]),
            Release(
                "r2",
                _at(4),
                [Deploy("cm", "readings/02")],
                title="Attention, further",
                show_on_site=False,
            ),
            Release("r3", _at(9), [Deploy("cm", "readings/03")]),
        ]
    )
    # A titled one is its own (unnumbered, off-schedule) row; one no lecture follows is
    # its own numbered row.
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("l1", 1, ["r1"]),
        ("r2", None, []),
        ("r3", 3, []),
    ]
