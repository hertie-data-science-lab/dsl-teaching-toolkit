"""sync_teams flattens teams.csv into the GitHub Teams it should materialise.

The gh wiring (create/add/remove team) is not tested - only the pure mapping from the
parsed roster of project teams to {team_slug: members}, which decides what gets created,
plus ensure_team's prune guard (the membership primitives stubbed, no live calls).
"""

from __future__ import annotations

import pytest

from dsl_course import gh_teams, roster, sync_teams
from tests.conftest import ROSTER_HEADER


def test_team_slug_is_assignment_prefixed_and_lowercased():
    # Assignment-prefixed so a name reused across assignments stays org-unique; lower-cased
    # to match the slug GitHub derives from the team name.
    assert (
        sync_teams.team_slug("assignment-4-project", "Wizards")
        == "assignment-4-project-wizards"
    )


def test_desired_teams_flattens_per_assignment_without_collision():
    per = {
        "assignment-4-project": {
            "wizards": ["anna-adams", "ben-baker"],
            "hackers": ["carla-cohen"],
        },
        "assignment-6-capstone": {"wizards": ["dan-davies"]},
    }
    assert sync_teams.desired_teams(per) == {
        "assignment-4-project-wizards": {"anna-adams", "ben-baker"},
        "assignment-4-project-hackers": {"carla-cohen"},
        "assignment-6-capstone-wizards": {"dan-davies"},
    }


def test_desired_teams_unions_case_colliding_team_names():
    # team_slug lower-cases, so `Team-X` and `team-x` collapse to one slug. Overwriting
    # dropped one row's members; unioning keeps both.
    per = {
        "assignment-4-project": {
            "Team-X": ["anna-adams"],
            "team-x": ["ben-baker"],
        }
    }
    assert sync_teams.desired_teams(per) == {
        "assignment-4-project-team-x": {"anna-adams", "ben-baker"}
    }


@pytest.fixture
def stub_team(monkeypatch):
    """Stub the gh primitives ensure_team drives; return the recorded add/remove calls."""
    calls = {"added": [], "removed": []}
    monkeypatch.setattr(sync_teams, "create_team", lambda *a, **k: True)
    monkeypatch.setattr(
        gh_teams,
        "get_team_members",
        lambda org, team: {"anna-adams", "hertie-dsl-bot", "henrycgbaker", "zoe-zed"},
    )
    monkeypatch.setattr(gh_teams, "acting_login", lambda: "hertie-dsl-bot")
    monkeypatch.setattr(
        gh_teams, "get_org_owners", lambda org: frozenset({"henrycgbaker"})
    )
    monkeypatch.setattr(
        gh_teams,
        "add_team_member",
        lambda org, team, h, role="member": calls["added"].append(h) or True,
    )
    monkeypatch.setattr(
        gh_teams,
        "remove_team_member",
        lambda org, team, h: calls["removed"].append(h) or True,
    )
    return calls


def test_ensure_team_prunes_stray_members_but_never_owners_or_the_bot(stub_team):
    # GitHub auto-adds whoever creates a team, so the bot lands in a project team without
    # ever being a deliberate grant; pruning it (or an org Owner, who has full access
    # regardless) would churn membership - or evict a maintainer - on every sync.
    ok = sync_teams.ensure_team(
        "org", "assignment-4-project-wizards", {"anna-adams", "ben-baker"}, prune=True
    )
    assert ok
    assert stub_team["added"] == ["ben-baker"]
    assert stub_team["removed"] == ["zoe-zed"]


def test_ensure_team_without_prune_only_adds(stub_team):
    ok = sync_teams.ensure_team(
        "org", "assignment-4-project-wizards", {"anna-adams", "ben-baker"}, prune=False
    )
    assert ok
    assert stub_team["added"] == ["ben-baker"]
    assert stub_team["removed"] == []


HEADER = ROSTER_HEADER


def _students(*rows: str) -> list[roster.Student]:
    return roster.parse("\n".join((HEADER, *rows)) + "\n")


def test_vet_handles_canonicalises_accepts_and_rejects():
    allowed = {
        "ada-l": "Ada-L",
        "ben-b": "Ben-B",
    }  # fold-key -> roster canonical casing
    accepted, rejected = sync_teams.vet_handles(
        ["ADA-L", "ben-b", "m-stranger"], allowed
    )
    assert accepted == ["Ada-L", "Ben-B"]  # case-normalised to the roster's casing
    assert rejected == [
        "m-stranger"
    ]  # not on the roster -> excluded, raw handle returned


def test_vet_groups_applies_one_allowlist_across_a_whole_map():
    # The team sync, the handout and the collection each built this allowlist for
    # themselves. A handle good enough to be handed a repo but not good enough to earn a
    # grade row is what a second copy of the rule costs a student.
    students = _students(
        "ada@uni.edu,Ada,enrolled,Ada-L,42,",
        "ben@uni.edu,Ben,enrolled,ben-b,43,",
    )
    assert sync_teams.vet_groups(
        {"wizards": ["ADA-L", "m-stranger"], "alchemists": ["ben-b"]}, students
    ) == [
        ("alchemists", ["ben-b"], []),  # name-sorted, member order preserved
        ("wizards", ["Ada-L"], ["m-stranger"]),
    ]


def test_known_handles_are_the_onboarded_roster_handles():
    students = _students(
        "ada@uni.edu,Ada,enrolled,ada-l,42,",
        "eve@uni.edu,Eve,enrolled,,,",  # not onboarded - no handle to add
    )
    assert sync_teams.known_handles(students) == {"ada-l"}
    assert sync_teams.known_handles(None) == set()  # roster missing/unreadable


def test_sync_never_adds_a_handle_that_is_not_on_the_roster(stub_team, monkeypatch):
    # Adding a handle to a Team also invites it to the org, so a teams.csv handle
    # that isn't an onboarded roster member (a typo, or a placeholder colliding with
    # a real GitHub account) must be skipped and reported, never invited.
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker", "m-stranger"]}},
    )
    monkeypatch.setattr(
        roster, "load", lambda org: _students("ben@uni.edu,Ben,,ben-baker,42,")
    )
    errors = sync_teams.sync("org", prune=False)
    assert errors == 1  # the skipped stranger is surfaced, not silently dropped
    assert stub_team["added"] == ["ben-baker"]


def test_sync_refuses_to_reconcile_when_the_roster_is_unreadable(
    stub_team, monkeypatch
):
    # teams.csv present but students.csv unreadable (None): the allowlist would be empty and
    # a pruning reconcile would evict every project team. Refuse and red, don't mass-evict.
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker"]}},
    )
    monkeypatch.setattr(roster, "load", lambda org: None)  # roster unreadable
    errors = sync_teams.sync("org", prune=True)
    assert errors == 1
    assert stub_team["added"] == [] and stub_team["removed"] == []  # nothing touched


def test_sync_matches_roster_handles_case_insensitively(stub_team, monkeypatch):
    # A teams.csv handle differing only in case from the roster entry is the same
    # GitHub account, so it must be added (in the roster's canonical casing), not
    # dropped as an unknown stranger.
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["Ben-Baker"]}},
    )
    monkeypatch.setattr(
        roster, "load", lambda org: _students("ben@uni.edu,Ben,,ben-baker,42,")
    )
    errors = sync_teams.sync("org", prune=False)
    assert errors == 0
    assert stub_team["added"] == ["ben-baker"]  # roster's canonical casing


def test_a_student_cannot_materialise_a_faculty_team_from_teams_csv(capsys):
    # teams.csv is student-written; (assignment="course", team="admin") slugs to
    # `course-admin`, the team holding admin on every cohort repo. Reconciling it would add
    # the student and prune the real admins.
    per = {
        "course": {"admin": ["mallory"]},
        "instructors": {"f2026": ["mallory"]},
        "assignment-4-project": {"team-x": ["ada-l"]},
    }
    wanted = sync_teams.desired_teams(per)
    assert wanted == {"assignment-4-project-team-x": {"ada-l"}}
    err = capsys.readouterr().err
    assert "course-admin" in err and "instructors-f2026" in err


def test_a_rejected_teams_csv_handle_is_counted_publicly_and_named_only_when_verbose(
    stub_team, monkeypatch, capsys
):
    # The handle came from a STUDENT (the public "Join team" issue), and this sync runs in
    # a world-readable log - so the name is verbose-only, while the count that makes the
    # run red stays where faculty can see it.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker", "m-stranger"]}},
    )
    monkeypatch.setattr(
        roster, "load", lambda org: _students("ben@uni.edu,Ben,,ben-baker,42,")
    )
    assert sync_teams.sync("org", prune=False) == 1
    captured = capsys.readouterr()
    assert "m-stranger" not in captured.out + captured.err
    assert "1 handle(s) in teams.csv are not onboarded roster handles" in captured.err

    monkeypatch.setenv("DSL_VERBOSE", "1")
    assert sync_teams.sync("org", prune=False) == 1
    assert "m-stranger" in capsys.readouterr().out
