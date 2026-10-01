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


def test_numbers_come_from_the_entry_then_the_label_and_never_the_position():
    # Decision 0020: an entry with neither has no number - never its position.
    rows = _rows(
        [
            Release("intro", _at(1), [Deploy("cm", "lectures/a")]),
            Release("lecture-09", _at(2), [Deploy("cm", "lectures/b")]),
            Release("guest", _at(3), [Deploy("cm", "lectures/c")], number=20),
            Release("wrap-up", _at(4), [Deploy("cm", "lectures/d")]),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("intro", None, []),
        ("lecture-09", 9, []),
        ("guest", 20, []),
        ("wrap-up", None, []),
    ]


def test_re_dating_an_entry_moves_nobody():
    before = [
        Release("lecture-1", _at(1), [Deploy("cm", "lectures/a")]),
        Release("b", _at(2), [Deploy("cm", "lectures/b")], number=2),
        Release("c", _at(3), [Deploy("cm", "lectures/c")], number=3),
    ]
    after = [
        Release("lecture-1", _at(5), [Deploy("cm", "lectures/a")]),
        *before[1:],
    ]
    numbers = lambda rel: {
        sr.row.key: sr.number for sr in schedule_plan.site_rows(_rows(rel))
    }
    assert numbers(before) == numbers(after) == {"lecture-1": 1, "b": 2, "c": 3}


def test_a_silent_entry_is_no_row_and_renumbers_nothing():
    # Maths: a silent setup copy and a silent quiz between the lectures.
    rows = _rows(
        [
            Release("lecture-1", _at(1), [Deploy("cm", "lectures/a")]),
            Release("setup", _at(2), [Deploy("cm", "m4ds")], show_on_site=False),
            Release("lecture-2", _at(3), [Deploy("cm", "lectures/b")]),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("lecture-1", 1, []),
        ("lecture-2", 2, []),
    ]


def test_numbered_readings_join_the_lecture_with_that_number_whatever_the_date():
    rows = _rows(
        [
            Release("lecture-1", _at(3), [Deploy("cm", "lectures/01")]),
            Release("intro", _at(10), [Deploy("cm", "lectures/02")], number=2),
            # After its lecture, by label; before its lecture, silent, by `number:`.
            Release("readings_01", _at(5), [Deploy("cm", "readings/01")]),
            Release(
                "pack",
                _at(1),
                [Deploy("cm", "readings/02")],
                number=2,
                show_on_site=False,
            ),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("lecture-1", 1, ["readings_01"]),
        ("intro", 2, ["pack"]),
    ]


def test_unnumbered_untitled_readings_are_their_own_row():
    # Dated a day before a lecture: no date inference, it stays its own row.
    rows = _rows(
        [
            Release("week-reading", _at(2), [Deploy("cm", "readings/a")]),
            Release("lecture-1", _at(3), [Deploy("cm", "lectures/01")]),
            Release("extra", _at(4), [Deploy("cm", "readings/b")], show_on_site=False),
        ]
    )
    # Shown or silent, a readings row with no number of its own is unnumbered.
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("week-reading", None, []),
        ("lecture-1", 1, []),
        ("extra", None, []),
    ]


def test_titled_readings_are_their_own_row():
    rows = _rows(
        [
            Release("lecture-1", _at(3), [Deploy("cm", "lectures/01")]),
            Release(
                "further",
                _at(3, 12),
                [Deploy("cm", "readings/x")],
                title="Attention, further",
            ),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("lecture-1", 1, []),
        ("further", None, []),
    ]


def test_readings_numbered_for_a_lecture_that_does_not_exist_are_their_own_row():
    # No lecture 7, and a silent lecture carries no number to join.
    rows = _rows(
        [
            Release("lecture-1", _at(3), [Deploy("cm", "lectures/01")]),
            Release(
                "lecture-2", _at(4), [Deploy("cm", "lectures/02")], show_on_site=False
            ),
            Release("readings-07", _at(5), [Deploy("cm", "readings/07")]),
            Release(
                "readings-02",
                _at(6),
                [Deploy("cm", "readings/02")],
                show_on_site=False,
            ),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("lecture-1", 1, []),
        ("readings-07", 7, []),
        ("readings-02", None, []),
    ]


def test_offplan_folders_are_the_kind_sections_no_copy_covers():
    deploys = [
        Deploy("cm", "lectures/01_intro"),  # the folder itself
        Deploy("cm", "labs", "materials"),  # a whole section covers its folders
        Deploy("code", "pkg", "materials", "lectures/02_x/code"),  # inside a folder
    ]
    trees = {
        "materials": [
            "lectures/01_intro/a.pdf",
            "lectures/02_x/code/m.py",
            "lectures/03_off/slides.html",
            "labs/01_lab/nb.ipynb",
            "readings/01_week/r.pdf",
            "quiz/q.pdf",  # not a kind section: All Materials only
            "lectures/root.pdf",  # a file of the section, no folder
            "lectures/04_sol/solution/key.py",  # never material
        ],
        "labs": ["05_extra/nb.ipynb"],  # a repo that IS one section
    }
    assert schedule_plan.offplan_folders(deploys, trees) == [
        ("labs", "05_extra", "lab"),
        ("materials", "lectures/03_off", "lecture"),
        ("materials", "readings/01_week", "readings"),
    ]


def test_the_console_s_key_for_a_new_readings_pack_joins_no_lecture():
    # The console keys a new unnumbered readings entry `readings`, `readings-<title>`,
    # then `readings-b`: no digit, so no number, so it joins no lecture (0013 rule 3).
    rows = _rows(
        [
            Release("lecture-1", _at(3), [Deploy("cm", "lectures/01")]),
            Release("lecture-2", _at(4), [Deploy("cm", "lectures/02")]),
            Release("readings", _at(5), [Deploy("cm", "readings/a")]),
            Release("readings-week", _at(6), [Deploy("cm", "readings/b")]),
            Release("readings-b", _at(7), [Deploy("cm", "readings/c")]),
        ]
    )
    assert _shown(schedule_plan.site_rows(rows)) == [
        ("lecture-1", 1, []),
        ("lecture-2", 2, []),
        ("readings", None, []),
        ("readings-week", None, []),
        ("readings-b", None, []),
    ]


def test_a_supporting_files_entry_is_no_row():
    # Decision 0026 rule 3: released with the rest, never a row (nor a weekly-plan line).
    rows = _rows(
        [
            Release("lecture-1", _at(1), [Deploy("cm", "lectures/a")]),
            Release("data", _at(1), [Deploy("cm", "data/week1")]),
            Release("figs", _at(2), [Deploy("cm", "lectures/a")], kind="assets"),
        ]
    )
    assert [r.kind for r in rows] == ["lecture", "assets", "assets"]
    assert _shown(schedule_plan.site_rows(rows)) == [("lecture-1", 1, [])]
