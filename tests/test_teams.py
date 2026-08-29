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
