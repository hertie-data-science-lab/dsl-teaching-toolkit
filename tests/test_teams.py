"""teams.parse is the pure core consumed by group provisioning - a wrong pivot puts a
student on the wrong team's repo. No network.
"""

from __future__ import annotations

import pytest

from dsl_course import teams


def test_parse_groups_by_assignment_and_team():
    text = (
        "assignment,team,github_handle\n"
        "assignment-4-project,team-x,anna-adams\n"
        "assignment-4-project,team-x,ben-baker\n"
        "assignment-4-project,team-y,carla-cohen\n"
        "assignment-6-project,team-x,anna-adams\n"
    )
    per = teams.parse(text)
    assert per["assignment-4-project"]["team-x"] == ["anna-adams", "ben-baker"]
    assert per["assignment-4-project"]["team-y"] == ["carla-cohen"]
    # per-assignment composition: same team name, different roster next assignment
    assert per["assignment-6-project"]["team-x"] == ["anna-adams"]


def test_a_capitalised_schedule_key_still_finds_its_rows():
    # The Join-team form lower-cases the assignment it writes; schedule.yml's keys are
    # typed by hand. Keyed raw, a `Assignment-4` entry found NO teams - so its group
    # handout, its deadline snapshot and its grading pass each silently had nothing to do,
    # while every repo they should have touched existed.
    per = teams.parse("assignment,team,github_handle\nAssignment-4,Team-X,anna-adams\n")
    assert per == {"assignment-4": {"team-x": ["anna-adams"]}}
    assert teams.teams_for(per, "Assignment-4") == {"team-x": ["anna-adams"]}
    assert teams.teams_for(per, "assignment-4") == {"team-x": ["anna-adams"]}


def test_parse_dedupes_and_skips_blank_rows():
    text = (
        "assignment,team,github_handle\n"
        "a1,t1,anna\n"
        "a1,t1,anna\n"  # duplicate
        "a1,,carla\n"  # blank team -> skipped
        ",t1,ben\n"  # blank assignment -> skipped
    )
    per = teams.parse(text)
    assert per == {"a1": {"t1": ["anna"]}}


def test_parse_tolerates_a_utf8_bom_from_excel():
    # Excel exports a UTF-8 BOM; left in, csv.DictReader reads the first header as
    # "﻿assignment" and every `assignment` lookup misses, dropping every row.
    text = "﻿assignment,team,github_handle\nassignment-4-project,team-x,anna-adams\n"
    per = teams.parse(text)
    assert per == {"assignment-4-project": {"team-x": ["anna-adams"]}}


def test_teams_for_returns_empty_for_unknown_assignment():
    per = teams.parse("assignment,team,github_handle\na1,t1,anna\n")
    assert teams.teams_for(per, "nope") == {}
    assert teams.teams_for(per, "a1") == {"t1": ["anna"]}


def test_a_semicolon_delimited_teams_csv_is_refused():
    import pytest

    with pytest.raises(RuntimeError, match="semicolon"):
        teams.parse("assignment;team;github_handle\na1;t;ada\n")


def test_team_names_are_casefolded():
    # The GitHub team a row materialises into is lower-cased (sync_teams.team_slug) and so
    # is the repo named after it, so `Wizards` and `wizards` were always one team
    # downstream while parsing as two units here.
    text = (
        "assignment,team,github_handle\n"
        "assignment-4-project,Wizards,ada-l\n"
        "assignment-4-project,wizards,ben-b\n"
    )
    assert teams.parse(text) == {
        "assignment-4-project": {"wizards": ["ada-l", "ben-b"]}
    }


def test_one_account_typed_two_ways_is_one_member():
    # GitHub logins are case-insensitive, and teams.csv is student-written. `ALICE` and
    # `alice` are the same account, so they earned two collaborator adds and - once
    # vet_handles folded them back to one canonical handle - two rows in the grades CSV.
    per = teams.parse(
        "assignment,team,github_handle\n"
        "project,team-x,ALICE\n"
        "project,team-x,alice\n"
        "project,team-x,bob\n"
    )
    assert per["project"]["team-x"] == ["alice", "bob"]


# ------------------------------------------------------------- faults a human must fix
#
# teams.csv is written by STUDENTS, through a public issue form, so a fault about it may
# name a row and a column and never a handle or a team name.


def _faults(text: str, known: set[str] | None = None) -> list:
    found = []
    teams.parse(text, found, known)
    return found


def test_an_unreadable_header_is_recorded_as_well_as_raised():
    found = []
    with pytest.raises(RuntimeError, match="semicolon"):
        teams.parse("assignment;team;github_handle\na1;t;ada\n", found)
    assert found[0].file == "teams.csv" and found[0].lineno == 1


def test_a_row_naming_a_faculty_team_is_a_fault():
    (fault,) = _faults("assignment,team,github_handle\ncourse,admin,ada\n")
    assert fault.lineno == 2 and fault.field == "team"
    assert "FACULTY team" in fault.what


def test_one_student_in_two_teams_of_one_assignment_is_a_fault():
    text = "assignment,team,github_handle\na4,team-x,ada\na4,team-y,ada\n"
    (fault,) = _faults(text)
    assert fault.lineno == 3 and "row 2" in fault.what
    assert "two teams cannot claim one student" in fault.what


def test_a_duplicated_row_is_a_fault_rather_than_a_silent_drop():
    text = "assignment,team,github_handle\na4,team-x,ada\na4,team-x,ada\n"
    (fault,) = _faults(text)
    assert fault.lineno == 3 and fault.field == "github_handle"


def test_a_handle_that_is_not_on_the_roster_is_a_fault_only_when_the_roster_is_known():
    text = "assignment,team,github_handle\na4,team-x,stranger\n"
    assert _faults(text) == []  # roster unreadable: not checked, not accused
    (fault,) = _faults(text, known={"ada"})
    assert fault.lineno == 2 and "onboarded roster handle" in fault.what


def test_a_teams_fault_never_carries_a_handle_or_a_team_name():
    text = "assignment,team,github_handle\nproject,wizards,anna-adams\n"
    (fault,) = _faults(text, known=set())
    for private in ("anna-adams", "wizards"):
        assert private not in fault.what and private not in fault.fix()


def test_a_clean_teams_csv_has_no_faults():
    text = "assignment,team,github_handle\na4,team-x,ada\na4,team-x,eve\n"
    assert _faults(text, known={"ada", "eve"}) == []
