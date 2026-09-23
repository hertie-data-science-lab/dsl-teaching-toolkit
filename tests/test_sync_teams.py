"""sync_teams flattens teams.csv into the GitHub Teams it should materialise.

The gh wiring (create/add/remove team) is not tested - only the pure mapping from the
parsed roster of project teams to {team_slug: members}, which decides what gets created,
plus ensure_team's prune guard (the membership primitives stubbed, no live calls).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dsl_course import gh_teams, roster, sync_teams, teams
from tests.conftest import ROSTER_HEADER


def test_team_slug_is_assignment_prefixed_and_lowercased():
    # Assignment-prefixed so a name reused across assignments stays org-unique; lower-cased
    # to match the slug GitHub derives from the team name.
    assert (
        teams.team_slug("assignment-4-project", "Wizards")
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
    # Skipped and logged, but not counted: a handle faculty mistyped is a CONTENT fault
    # of teams.csv, listed by row on that file's digest issue and mailed to whoever
    # pushed the line. Reddening the nightly cron for it as well tells a maintainer only
    # that something is wrong in an org they cannot correct a CSV in.
    assert errors == 0
    assert stub_team["added"] == ["ben-baker"]


def test_sync_refuses_to_reconcile_when_there_is_no_roster(stub_team, monkeypatch):
    # teams.csv present but students.csv absent (None): the allowlist would be empty and
    # a pruning reconcile would evict every project team. Refuse - and stay green, like
    # every other file faculty have to write. A read that FAILED raises out of
    # `roster.load` instead, and still reds the run.
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker"]}},
    )
    monkeypatch.setattr(roster, "load", lambda org: None)  # no roster
    errors = sync_teams.sync("org", prune=True)
    assert errors == 0
    assert stub_team["added"] == [] and stub_team["removed"] == []  # nothing touched


def test_a_read_that_failed_still_reds_the_teams_sync(stub_team, monkeypatch):
    # The other half of the rule above: "we could not look" is not a file faculty can
    # fix, and it must not be reported to them as one - nor quietly pass as green.
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker"]}},
    )

    def rate_limited(org, faults=None):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(roster, "load", rate_limited)
    with pytest.raises(RuntimeError):
        sync_teams.sync("org", prune=True)


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
    # a world-readable log - so the name is verbose-only, while the count faculty act on
    # stays where they can see it. Neither reds the run.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        sync_teams.teams,
        "load",
        lambda org: {"assignment-4-project": {"wizards": ["ben-baker", "m-stranger"]}},
    )
    monkeypatch.setattr(
        roster, "load", lambda org: _students("ben@uni.edu,Ben,,ben-baker,42,")
    )
    assert sync_teams.sync("org", prune=False) == 0
    captured = capsys.readouterr()
    assert "m-stranger" not in captured.out + captured.err
    assert "1 handle(s) in teams.csv are not onboarded roster handles" in captured.err

    monkeypatch.setenv("DSL_VERBOSE", "1")
    assert sync_teams.sync("org", prune=False) == 0
    assert "m-stranger" in capsys.readouterr().out


def _emptied_world(monkeypatch, stub_team, csv, existing):
    """teams.csv, the org's teams and the schedule's keys, on the CONSUMER's names; the
    team whose members `stub_team` reports is recorded per call."""
    monkeypatch.setattr(sync_teams.teams, "load", lambda org: csv)
    monkeypatch.setattr(
        roster,
        "load",
        lambda org: _students(
            "ada@uni.edu,Ada,,ada-l,1,", "ben@uni.edu,Ben,,ben-baker,2,"
        ),
    )
    monkeypatch.setattr(sync_teams, "list_teams", lambda org: existing)
    monkeypatch.setattr(
        sync_teams.schedule,
        "load",
        lambda org: SimpleNamespace(assignments={"assignment-4-project": object()}),
    )
    touched: list[str] = []
    real = gh_teams.get_team_members
    monkeypatch.setattr(
        gh_teams,
        "get_team_members",
        lambda org, team: touched.append(team) or real(org, team),
    )
    return touched


PROJECT = sync_teams.PROJECT_TEAM_DESCRIPTION


def test_moving_out_of_a_one_person_team_revokes_its_access(stub_team, monkeypatch):
    # zoe-zed was alone in `wizards` and moved to `team-x`: teams.csv no longer names
    # `wizards` at all, so the reconcile over the CSV never visited it and she kept push
    # on its repo. The emptied team is reconciled to nobody - but never the org owner or
    # the bot, which `reconcile_team_members` keeps whatever it is asked.
    touched = _emptied_world(
        monkeypatch,
        stub_team,
        {"assignment-4-project": {"team-x": ["ada-l"]}},
        {
            "assignment-4-project-team-x": PROJECT,
            "assignment-4-project-wizards": PROJECT,
        },
    )
    assert sync_teams.sync("org", prune=True) == 0
    assert "assignment-4-project-wizards" in touched
    assert "zoe-zed" in stub_team["removed"]
    assert "henrycgbaker" not in stub_team["removed"]
    assert "hertie-dsl-bot" not in stub_team["removed"]


def test_role_teams_and_teams_it_did_not_make_are_never_emptied(stub_team, monkeypatch):
    # Only a team this module made (its description) for a planned assignment's prefix,
    # and never a role team - even one that happens to carry the project description.
    touched = _emptied_world(
        monkeypatch,
        stub_team,
        {},
        {
            "instructors": PROJECT,
            "students": PROJECT,
            "course-admin": PROJECT,
            "instructors-f2026": PROJECT,
            "assignment-4-project-markers": "Hand-made by the teaching team",
            "assignment-9-other-wizards": PROJECT,
        },
    )
    assert sync_teams.sync("org", prune=True) == 0
    assert touched == [] and stub_team["removed"] == []


def test_an_unpruned_sync_empties_nothing(stub_team, monkeypatch):
    # The ad-hoc CLI never revokes access on its own; neither does this.
    _emptied_world(
        monkeypatch,
        stub_team,
        {"assignment-4-project": {"team-x": ["ada-l"]}},
        {"assignment-4-project-wizards": PROJECT},
    )
    monkeypatch.setattr(
        sync_teams, "list_teams", lambda org: pytest.fail("listed on an unpruned sync")
    )
    assert sync_teams.sync("org", prune=False) == 0
    assert stub_team["removed"] == []


def test_a_team_listing_that_cannot_be_read_empties_nothing_and_reds(
    stub_team, monkeypatch
):
    _emptied_world(
        monkeypatch, stub_team, {"assignment-4-project": {"team-x": ["ada-l"]}}, None
    )
    assert sync_teams.sync("org", prune=True) == 1
    assert "zoe-zed" in stub_team["removed"]  # team-x's own prune still ran
    assert stub_team["removed"].count("zoe-zed") == 1  # and nothing was emptied
