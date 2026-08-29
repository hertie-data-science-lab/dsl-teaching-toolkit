"""teams.parse is the pure core consumed by group provisioning - a wrong pivot puts a
student on the wrong team's repo. No network.
"""

from __future__ import annotations

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
