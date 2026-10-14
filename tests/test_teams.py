"""teams: each assignment's teams, read off the `team:<number>` columns of students.csv
joined with the engine's roster file (decision 0039 rule 4)."""

from __future__ import annotations

from dsl_course import roster, teams
from dsl_course.schedule import AssignmentEntry, Schedule

STUDENTS = (
    "email,name,role,team:4,team:6\n"
    "ana@x.org,Ana,,geneva,\n"
    "ben@x.org,Ben,,Geneva,oslo\n"
    "cara@x.org,Cara,auditor,,oslo\n"
    "dan@x.org,Dan,,geneva,\n"
)
SYSTEM = (
    "email,github_handle,github_id,enrol_code,code_sent_at\n"
    "ana@x.org,ana-l,1,,\n"
    "BEN@x.org,Ben-O,2,,\n"
    "cara@x.org,cara-n,3,,\n"
)
KEYS = {"assignment-4": "4", "Project": "6"}


def _parse(students=STUDENTS, system=SYSTEM, keys=KEYS, faults=None):
    return teams.parse(roster.parse(students, system), keys, faults)


def test_a_team_is_the_rows_sharing_a_cell_per_assignment():
    assert _parse() == {
        "assignment-4": {"geneva": ["ana-l", "ben-o"]},
        "project": {"oslo": ["ben-o", "cara-n"]},
    }


def test_a_student_not_yet_onboarded_waits_for_their_handle():
    # Dan's cell was typed before he joined: no handle, so not yet a member.
    assert "dan" not in str(_parse())


def test_columns_are_the_assignment_numbers_else_the_key():
    sched = Schedule(
        assignments={
            "assignment-4": AssignmentEntry(None, "t4", number=4),
            "bonus": AssignmentEntry(None, "tb"),
        }
    )
    assert teams.columns(sched) == {"assignment-4": "4", "bonus": "bonus"}


def test_teams_for_is_casefolded_and_empty_for_an_unknown_key():
    per = _parse()
    assert teams.teams_for(per, "Assignment-4") == {"geneva": ["ana-l", "ben-o"]}
    assert teams.teams_for(per, "nope") == {}


def test_a_cell_naming_a_faculty_team_is_a_fault_and_ignored():
    faults = []
    students = "email,name,role,team:4\nana@x.org,Ana,,admin\n"
    keys = {"course": "4"}
    assert _parse(students, SYSTEM, keys, faults) == {}
    assert len(faults) == 1 and "FACULTY team" in faults[0].what


def test_a_column_naming_no_assignment_is_a_fault():
    faults = []
    _parse(keys={"assignment-4": "4"}, faults=faults)
    assert [f.field for f in faults] == ["team:6"]


def test_a_teams_fault_never_carries_a_handle_or_a_team_name():
    faults = []
    _parse(
        "email,name,role,team:9\nana@x.org,Ana,,secret-team\n",
        faults=faults,
    )
    text = " ".join(f.what + f.where for f in faults)
    assert "secret-team" not in text and "ana" not in text


def test_a_clean_roster_has_no_team_faults():
    faults = []
    _parse(faults=faults)
    assert faults == []
