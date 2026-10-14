"""schedule_plan: the rows a semester's plan declares (decision 0038).

One row per slot of each content entry, dated ones in date order then undated (`tbc`) ones;
its kind is the block it is written in, its number and session the entry's. Nothing is read
off a folder name.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dsl_course import schedule_plan
from dsl_course.schedule import Deploy, Release, Schedule, Slot

BERLIN = ZoneInfo("Europe/Berlin")


def _at(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=BERLIN)


def _rows(releases, start=None):
    sched = Schedule(releases=releases, semester_start=start)
    return schedule_plan.planned_rows(sched)


def _lecture(n: int, when, *deploys: Deploy, **kw) -> Release:
    kw.setdefault("session", str(n))
    return Release(f"lecture-{n}", when, list(deploys), kind="lecture", number=n, **kw)


def _lab(n: int, when, *deploys: Deploy, **kw) -> Release:
    kw.setdefault("session", str(n))
    return Release(f"lab-{n}", when, list(deploys), kind="lab", number=n, **kw)


def _readings(key: str, when, *deploys: Deploy, **kw) -> Release:
    return Release(f"readings-{key}", when, list(deploys), kind="readings", **kw)


def test_one_row_per_dated_entry_in_plan_order():
    rows = _rows(
        [
            _lecture(1, _at(7), Deploy("cm", "lectures/01_a")),
            _lab(1, _at(9, 14), Deploy("cm", "labs/01_a")),
        ]
    )
    assert [(r.key, r.kind, r.when, r.undated, r.unit) for r in rows] == [
        ("lecture-1", "lecture", _at(7), False, "1"),
        ("lab-1", "lab", _at(9, 14), False, "1"),
    ]


def test_an_entry_makes_one_row_per_slot_with_one_number_and_one_set_of_files():
    # Decision 0038 rule 3: two lab groups are two rows of one entry; a slot's own room
    # and words beat the entry's.
    lab = _lab(
        2,
        _at(9, 8),
        Deploy("cm", "labs/02"),
        location="Lab 2.30",
        details="Bring a laptop.",
        slots=[
            Slot(_at(9, 8)),
            Slot(_at(10, 18), location="Lab 2.32", details="Group 2"),
        ],
    )
    rows = _rows([lab])
    assert [(r.key, r.slot, r.number, r.when) for r in rows] == [
        ("lab-2", 0, 2, _at(9, 8)),
        ("lab-2", 1, 2, _at(10, 18)),
    ]
    assert [(r.location, r.details) for r in rows] == [
        ("Lab 2.30", "Bring a laptop."),
        ("Lab 2.32", "Group 2"),
    ]
    assert rows[0].deploys == rows[1].deploys == tuple(lab.deploy)


def test_an_undated_entry_sorts_after_the_last_dated_row_of_its_kind():
    # `event_datetime: tbc` is a row, flagged undated, with no `when`, sorted a minute
    # after the last lab - so it follows Lab 2 on the schedule, ahead of the lecture after it.
    minute = timedelta(minutes=1)
    rows = _rows(
        [
            _lab(3, None, Deploy("cm", "labs/03_a")),
            _lab(1, _at(8), Deploy("cm", "labs/01_a")),
            _lab(2, _at(9), Deploy("cm", "labs/02_a")),
            _lab(4, None, Deploy("cm", "labs/04_a")),
            _lecture(1, _at(10), Deploy("cm", "lectures/01_a")),
        ]
    )
    assert [(r.key, r.when, r.sort_at, r.undated) for r in rows] == [
        ("lab-1", _at(8), _at(8), False),
        ("lab-2", _at(9), _at(9), False),
        # No date of its own: `when` is None, and `sort_at` places it - in date order.
        ("lab-3", None, _at(9) + minute, True),
        ("lab-4", None, _at(9) + 2 * minute, True),
        ("lecture-1", _at(10), _at(10), False),
    ]


def test_an_undated_entry_of_a_kind_with_no_dated_row_sorts_at_term_end():
    sched = Schedule(
        releases=[
            _lecture(1, _at(7), Deploy("cm", "lectures/01_a")),
            _lab(1, None, Deploy("cm", "labs/01_a")),
        ],
        semester_end=date(2026, 12, 18),
    )
    lab = schedule_plan.planned_rows(sched)[-1]
    assert (lab.key, lab.undated) == ("lab-1", True)
    assert lab.when is None
    assert lab.sort_at.date() == date(2026, 12, 18)
    assert lab.sort_at > _at(7)


def test_nothing_fires_for_an_undated_entry():
    # The row's `when` is a place on the list, never a release time: the entry's own
    # deploys stay undue however late it is.
    release = _lab(1, None, Deploy("cm", "labs/01_a"))
    _rows([release])
    assert release.due_deploys(datetime(2099, 1, 1, tzinfo=BERLIN)) == []


def test_a_row_is_dated_by_its_event_not_by_when_its_files_ship():
    (row,) = _rows(
        [_lecture(2, _at(15), Deploy("cm", "lectures/02", deploy_datetime=_at(14)))]
    )
    assert row.when == _at(15)


def test_a_rows_kind_is_its_blocks_whatever_the_folder():
    # Decision 0038 rule 2: no folder inference - `readings/` copied by a lecture entry
    # is a lecture's file.
    (row,) = _rows([_lecture(1, _at(7), Deploy("cm", "readings/01_a"))])
    assert row.kind == "lecture"
    assert schedule_plan.dest_section(Deploy("cm", "readings/01_a")) == "readings"
    assert schedule_plan.dest_section(Deploy("cm", "SYLLABUS.md", "docs")) == "docs"


def test_a_row_carries_its_name_details_date_marker_and_silence():
    (row,) = _rows(
        [
            _readings(
                "week-2",
                _at(7),
                Deploy("cm", "readings/02_x"),
                title="Week 2",
                details="Two papers.",
                tbc=True,
                show_on_site=False,
                reading_list="- Bishop, ch. 5.",
            )
        ]
    )
    assert (row.subtitle, row.details, row.tbc, row.shown, row.reading_list) == (
        "Week 2",
        "Two papers.",
        True,
        False,
        "- Bishop, ch. 5.",
    )


def test_a_rows_destinations_are_deduped_in_plan_order():
    (row,) = _rows(
        [
            _lecture(
                1,
                _at(7),
                Deploy("cm", "lectures/01"),
                Deploy("code", "a.py", "materials", "lectures/01"),
                Deploy("cm", "labs/01"),
            )
        ]
    )
    assert row.dests == ["materials/lectures/01", "materials/labs/01"]


def test_a_destination_under_another_of_the_rows_is_not_listed_again():
    (row,) = _rows(
        [
            _lab(
                3,
                _at(7),
                Deploy("cm", "labs/03_lab/solutions"),
                Deploy("cm", "labs/03_lab"),
                Deploy("cm", "labs/03_lab_extra"),
                Deploy("cm", "labs/03_lab", "other"),
                Deploy("cm", "labs/03_lab/solutions", "other"),
            )
        ]
    )
    # Nested in the same repo goes; a sibling sharing a prefix and another repo stay.
    assert row.dests == [
        "materials/labs/03_lab",
        "materials/labs/03_lab_extra",
        "other/labs/03_lab",
    ]


# ----------------------------------------------------------------- site rows


def _shown(rows):
    return [(sr.row.key, sr.number) for sr in schedule_plan.site_rows(rows)]


def test_a_shown_row_is_numbered_by_its_entry_and_never_by_position():
    rows = _rows(
        [
            _lecture(3, _at(7), Deploy("cm", "l/a")),
            _lecture(1, _at(14), Deploy("cm", "l/b")),
        ]
    )
    assert _shown(rows) == [("lecture-3", 3), ("lecture-1", 1)]


def test_a_silent_entry_is_no_row_and_renumbers_nothing():
    rows = _rows(
        [
            _lecture(1, _at(7), Deploy("cm", "l/a"), show_on_site=False),
            _lecture(2, _at(14), Deploy("cm", "l/b")),
        ]
    )
    assert _shown(rows) == [("lecture-2", 2)]


def test_readings_attach_to_their_units_first_row_by_date_whatever_its_kind():
    # Decision 0038 rule 5: `session:` names the unit; the first row by date hosts the
    # readings (here the lab, a day before the lecture), and every row of the unit carries
    # the chip.
    rows = _rows(
        [
            _lab(2, _at(8), Deploy("cm", "labs/02")),
            _lecture(2, _at(9), Deploy("cm", "lectures/02")),
            _readings("week-2", _at(1), Deploy("cm", "readings/02"), session="2"),
            _lecture(3, _at(16), Deploy("cm", "lectures/03")),
        ]
    )
    got = {
        sr.row.key: ([r.key for r in sr.readings], sr.chip)
        for sr in schedule_plan.site_rows(rows)
    }
    assert got == {
        "lab-2": (["readings-week-2"], True),
        "lecture-2": ([], True),
        "lecture-3": ([], False),
    }


def test_readings_attach_whatever_their_date_and_even_silent():
    rows = _rows(
        [
            _readings(
                "w1",
                _at(30),
                Deploy("cm", "r/1"),
                session="1",
                show_on_site=False,
            ),
            _lecture(1, _at(7), Deploy("cm", "l/1")),
        ]
    )
    (sr,) = schedule_plan.site_rows(rows)
    assert sr.row.key == "lecture-1" and [r.key for r in sr.readings] == ["readings-w1"]


def test_readings_with_no_session_are_their_own_row():
    rows = _rows(
        [
            _lecture(1, _at(7), Deploy("cm", "l/1")),
            _readings("background", _at(1), Deploy("cm", "r/bg"), title="Background"),
        ]
    )
    assert _shown(rows) == [("readings-background", None), ("lecture-1", 1)]


def test_readings_for_a_unit_with_no_shown_row_are_their_own_row():
    rows = _rows(
        [
            _lecture(1, _at(7), Deploy("cm", "l/1"), show_on_site=False),
            _readings("w1", _at(1), Deploy("cm", "r/1"), session="1"),
            _readings("w9", _at(2), Deploy("cm", "r/9"), session="9"),
        ]
    )
    assert [k for k, _ in _shown(rows)] == ["readings-w1", "readings-w9"]


def test_a_readings_entry_with_two_slots_is_attached_once():
    rows = _rows(
        [
            _lecture(1, _at(7), Deploy("cm", "l/1")),
            _readings(
                "w1",
                _at(1),
                Deploy("cm", "r/1"),
                session="1",
                slots=[Slot(_at(1)), Slot(_at(2))],
            ),
        ]
    )
    (sr,) = schedule_plan.site_rows(rows)
    assert [r.key for r in sr.readings] == ["readings-w1"]


def test_a_supporting_files_entry_is_no_row():
    rows = _rows(
        [
            Release("assets-data", _at(1), [Deploy("cm", "data")], kind="assets"),
            _lecture(1, _at(7), Deploy("cm", "l/1")),
        ]
    )
    assert _shown(rows) == [("lecture-1", 1)]


# ------------------------------------------------------------------ numbers


def test_only_a_shown_entry_of_a_numbered_kind_needs_a_number():
    # Decision 0020 through the kind model: `numbered:` in the policy says which kinds.
    sched = Schedule(
        releases=[
            Release("assets-data", _at(1), [Deploy("cm", "data/week1")], kind="assets"),
            Release("exam-mid", _at(2), [Deploy("cm", "exam.pdf")], kind="exam"),
            _readings("w", _at(3), Deploy("cm", "r")),
        ]
    )
    assert schedule_plan.unnumbered(sched) == []
    assert schedule_plan.unnumbered_release(sched, "cm", [""]) is None
    sched.releases.append(
        Release("lecture-intro", _at(3), [Deploy("cm", "lectures/b")], kind="lecture")
    )
    assert [m.key for m in schedule_plan.unnumbered(sched)] == ["lecture-intro"]
    assert schedule_plan.unnumbered_release(sched, "cm", [""]) is not None
    (fault,) = schedule_plan.number_faults(schedule_plan.unnumbered(sched))
    assert fault.where == "lecture-intro"


def test_two_entries_of_one_kind_sharing_a_number_are_named():
    sched = Schedule(
        releases=[
            _lecture(2, _at(7), Deploy("cm", "l/a")),
            Release("lecture-again", _at(8), [], kind="lecture", number=2),
            _lab(2, _at(9), Deploy("cm", "b/a")),
        ]
    )
    assert schedule_plan.duplicate_numbers(sched) == [
        ("lecture", 2, ["lecture-2", "lecture-again"])
    ]


def test_a_duplicate_number_names_the_kind_by_its_policy_plural():
    assert schedule_plan.duplicate_text("readings", 2, ["a", "b"]).startswith(
        "Two readings are numbered 2"
    )
