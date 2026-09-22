"""The other half of the team-formation window: who has NOT formed a team, and the fault
that says so while the window is still open.

A parked group handout was silent - `assign.provision_all` logs `[wait] no teams` and
returns green - so the teaching team heard nothing until somebody asked. These pin the
three things it has to get right: which assignments earn a window at all, that the
fault climbs towards the moment formation SHUTS, and that nothing it says names a
person.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import ROSTER_HEADER

from dsl_course import team_formation
from dsl_course.faults import Severity
from dsl_course.grades import GradingSpec
from dsl_course.schedule import AssignmentEntry, Schedule

BERLIN = ZoneInfo("Europe/Berlin")
COURSE, COHORT = "Course-Org", "Cohort-f2026"
OPENS = datetime(2026, 9, 22, 7, 0, tzinfo=BERLIN)
SHUTS = datetime(2026, 10, 4, 23, 59, 59, tzinfo=BERLIN)
INSIDE = datetime(2026, 9, 25, 9, 0, tzinfo=BERLIN)

GROUP = GradingSpec(type="group", team_formation="self_select")

# Four onboarded students and one who has not joined GitHub yet - the row every count
# below has to leave out, because a student with no handle cannot be in a team.
ROSTER = (
    f"{ROSTER_HEADER}\n"
    "anna@x.edu,Anna Adams,enrolled,anna-adams,1,,\n"
    "ben@x.edu,Ben Baker,enrolled,ben-baker,2,,\n"
    "carla@x.edu,Carla Cohen,enrolled,Carla-Cohen,3,,\n"
    "dan@x.edu,Dan Doyle,enrolled,dan-doyle,4,,\n"
    "eve@x.edu,Eve Evans,enrolled,,,,\n"
    "fred@x.edu,Fred Frey,auditor,fred-frey,6,,"
)
TEAMS_HEADER = "assignment,team,github_handle"


def _entry(**kw) -> AssignmentEntry:
    return AssignmentEntry(
        course_source_repo="a2-f2026",
        due_datetime=SHUTS,
        handout_datetime=OPENS,
        lines={"due_datetime": 12, "grading_datetime": 14},
        **kw,
    )


def _sched(**entries: AssignmentEntry) -> Schedule:
    return Schedule(assignments=dict(entries or {"assignment-2": _entry()}))


@pytest.fixture
def cohort(monkeypatch):
    """The cohort's two hand-edited files and each template's definition, as TEXT where
    there is text - so the real parsers run and the fold-casing rules under test are the
    ones the toolkit actually applies."""

    def _wire(roster_csv: str = ROSTER, teams_csv: str = "", spec=GROUP):
        monkeypatch.setattr(
            team_formation.roster, "_roster_text", lambda org: roster_csv
        )
        monkeypatch.setattr(
            team_formation.teams,
            "_teams_text",
            lambda org: teams_csv or None,
        )
        monkeypatch.setattr(
            team_formation.grades,
            "declared_grading_spec",
            lambda org, template: spec,
        )

    return _wire


def _rows(*pairs: tuple[str, str], key: str = "assignment-2") -> str:
    return "\n".join([TEAMS_HEADER, *(f"{key},{team},{h}" for team, h in pairs)])


# ------------------------------------------------------------------ which windows open


def test_an_open_self_select_group_window_is_reported_with_its_boundaries(cohort):
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert (window.key, window.opens, window.closes) == ("assignment-2", OPENS, SHUTS)
    assert (window.teams, window.enrolled) == (0, 4)


def test_the_cohort_side_name_rides_along_beside_the_schedule_key(cohort):
    # teams.csv is keyed on the SCHEDULE key and every repo is named after the cohort-side
    # name, and they differ exactly when `cohort_dest_repo` is set - so a surface that had
    # only one of them would either find no teams or name a repo nobody has.
    cohort(teams_csv=_rows(("team-x", "anna-adams"), key="assignment-2"))
    (window,) = team_formation.open_windows(
        COURSE,
        COHORT,
        _sched(**{"assignment-2": _entry(cohort_dest_repo="a2")}),
        INSIDE,
    )
    assert (window.key, window.name) == ("assignment-2", "a2")
    assert window.teams == 1


def test_an_individual_assignment_never_opens_a_window(cohort):
    cohort(spec=GradingSpec(type="individual"))
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_an_assigned_group_assignment_never_opens_a_window(cohort):
    # The teaching team writes teams.csv for these, and the Join-team form refuses them -
    # so "nobody has self-selected yet" is not a thing that can be true of one.
    cohort(spec=GradingSpec(type="group", team_formation="assigned"))
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_a_template_with_no_definition_opens_no_window(cohort):
    # The lock has already locked such an assignment's form to `none`, so there is no door
    # for a student to be waiting at.
    cohort(spec=None)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


@pytest.mark.parametrize(
    "when", [OPENS - timedelta(minutes=1), SHUTS, SHUTS + timedelta(days=1)]
)
def test_a_window_that_is_not_open_at_now_is_not_reported(cohort, when):
    cohort()
    assert team_formation.open_windows(COURSE, COHORT, _sched(), when) == []


def test_an_assignment_handed_out_by_hand_has_no_window_to_be_waiting_at(cohort):
    # No `handout_datetime` is no hour from which "form your team now" would be true.
    cohort()
    sched = _sched(
        **{
            "assignment-2": AssignmentEntry(
                due_datetime=SHUTS, course_source_repo="a2-f2026"
            )
        }
    )
    assert team_formation.open_windows(COURSE, COHORT, sched, INSIDE) == []


# -------------------------------------------------------------------------- who waits


def test_only_enrolled_onboarded_rows_count_as_waiting(cohort):
    # An auditor gets no assignment repo at all, and a row with no handle cannot be in a
    # team - so neither is anybody to chase.
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert {s.github_handle for s in window.waiting} == {
        "anna-adams",
        "ben-baker",
        "Carla-Cohen",
        "dan-doyle",
    }


def test_a_handle_typed_in_another_casing_is_the_same_student(cohort):
    # teams.csv is parsed casefolded (GitHub logins are case-insensitive); a roster handle
    # compared in its own casing would read as a student who never joined, and would be
    # chased for a team she is already in.
    cohort(teams_csv=_rows(("team-x", "CARLA-COHEN")))
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert "Carla-Cohen" not in {s.github_handle for s in window.waiting}
    assert window.enrolled == 4 and len(window.waiting) == 3


def test_rows_for_another_assignment_do_not_team_anybody_up(cohort):
    cohort(teams_csv=_rows(("team-x", "anna-adams"), key="assignment-1"))
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert window.teams == 0 and len(window.waiting) == 4


# ------------------------------------------------------------------- a read that failed


def test_a_teams_csv_that_could_not_be_read_reports_no_windows_at_all(
    cohort, monkeypatch, capsys
):
    # None, not []: "we could not look" reported as "everybody has a team" is the failure
    # this whole pass exists to stop. The phase then raises no fault at all.
    cohort()

    def boom(org):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(team_formation.teams, "_teams_text", boom)
    windows = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert windows is None
    assert "rate limit" in capsys.readouterr().err
    assert team_formation.window_faults(_sched(), windows) == []


def test_an_absent_roster_reports_no_windows_rather_than_a_teamed_cohort(cohort):
    # The roster is the allowlist. The empty set an absent file implies would report every
    # student as teamed; students.csv already carries its own fault on its own digest.
    cohort(roster_csv=None)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) is None


# ------------------------------------------------------------------------- the fault


def test_no_fault_once_every_enrolled_student_has_a_team(cohort):
    cohort(
        teams_csv=_rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
        )
    )
    sched = _sched()
    windows = team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    assert [w.teams for w in windows] == [2]
    assert team_formation.window_faults(sched, windows) == []


def test_with_no_team_at_all_the_fault_says_so_in_counts(cohort):
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.what == (
        "team formation is open and no team has formed yet - all 4 enrolled student(s) "
        "are still without one"
    )


def test_with_some_teams_formed_the_fault_counts_the_students_left_over(cohort):
    cohort(teams_csv=_rows(("team-x", "anna-adams"), ("team-x", "ben-baker")))
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.what == (
        "team formation is open and 2 of 4 enrolled student(s) are not in a team yet, "
        "across the 1 team(s) formed so far"
    )


def test_nothing_in_the_fault_names_a_student(cohort):
    # This runs in a PUBLIC workflow, and the fault's text reaches a run log, a digest
    # issue and an email. Every handle, name and address on the fixture roster, and the
    # team names students typed, are checked against every string the fault carries.
    cohort(teams_csv=_rows(("wizards", "anna-adams")))
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    text = (
        f"{fault.what} {fault.where} {fault.field} "
        f"{fault.label} {fault.fix()} {fault.line()}"
    ).casefold()
    for secret in (
        "anna",
        "adams",
        "ben-baker",
        "carla",
        "dan-doyle",
        "x.edu",
        "wizards",
    ):
        assert secret not in text, f"{secret!r} reached a public surface"


def test_the_fault_points_at_the_line_that_decides_when_the_window_shuts(cohort):
    # The deep link has to land on the line a reader would EDIT to give the cohort longer,
    # and that is whichever key closed the window.
    cohort()
    plain = _sched()
    (fault,) = team_formation.window_faults(
        plain, team_formation.open_windows(COURSE, COHORT, plain, INSIDE)
    )
    assert (fault.where, fault.field, fault.lineno) == (
        "assignments.assignment-2",
        "due_datetime",
        12,
    )
    assert fault.at == "schedule.yml:12"
    assert fault.in_repo == "classroom-config"

    pinned = _sched(
        **{"assignment-2": _entry(grading_datetime=SHUTS - timedelta(days=1))}
    )
    (fault,) = team_formation.window_faults(
        pinned, team_formation.open_windows(COURSE, COHORT, pinned, INSIDE)
    )
    assert (fault.field, fault.lineno) == ("grading_datetime", 14)


def test_the_fault_carries_its_own_consequence_and_fix(cohort):
    # schedule.yml's own sentence says the entry "is not scheduled" - which is exactly
    # false here: the entry is scheduled, it has fired, and what is missing is the teams.
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.consequence == (
        "no repos are provisioned for that assignment, so the students still without a "
        "team cannot hand anything in"
    )
    assert fault.fix() == (
        "write the missing rows into classroom-config/teams.csv yourself, or move the "
        "date on the line above to keep team formation open for longer."
    )


# ------------------------------------------------------------- the rungs across the close


def test_the_fault_climbs_the_rungs_towards_the_moment_formation_shuts(cohort):
    # An immediate fault that carries a MOMENT: `kind` is None, so the letter is the one
    # for a line somebody has to act on, while `fires` makes it get louder as the window
    # runs out. Both follow from `severity` branching on `fires`, never on `is_source`.
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.is_source is False and fault.fires == SHUTS
    rungs = [
        fault.severity(SHUTS - timedelta(days=5)),
        fault.severity(SHUTS - timedelta(hours=20)),
        fault.severity(SHUTS - timedelta(hours=8)),
        fault.severity(SHUTS - timedelta(hours=2)),
        fault.severity(SHUTS + timedelta(hours=1)),
    ]
    assert rungs == [
        Severity.ADVISORY,
        Severity.WARNING,
        Severity.URGENT,
        Severity.CRITICAL,
        Severity.MISSED,
    ]
