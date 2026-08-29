"""sync-roster pure core -- which handle belongs in which role team. The reconcile decides
org + team access for a whole cohort, so the split has to be exact: an auditor landing in
`students` would be handed assignment repos, an enrolled student landing in `auditors`
would silently lose them. The gh calls around it are wiring, not tested.
"""

from __future__ import annotations

import pytest

from dsl_course import roster, sync_roster

HEADER = "hertie_email,name,github_handle,github_id,enrol_code,role"


@pytest.fixture(autouse=True)
def _no_teams_csv(monkeypatch):
    """The revoke reads teams.csv to know which submission repos belong to a TEAM. A
    cohort with no group assignments is the uninteresting answer here; the tests about
    that read set their own."""
    monkeypatch.setattr(sync_roster.teams, "load", lambda org: {})


def _roster(*rows: str) -> list[roster.Student]:
    return roster.parse("\n".join((HEADER, *rows)) + "\n")


def _handles(wanted: dict) -> dict[str, set[str]]:
    """The partition as bare logins - what these tests are about; `sync` reads the ids off
    the same rows."""
    return {team: {s.github_handle for s in rows} for team, rows in wanted.items()}


def test_desired_members_splits_enrolled_from_auditors():
    students = _roster(
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
        "bob@uni.edu,Bob,bob-b,44,dsl-def,",  # blank role -> enrolled
    )
    assert _handles(sync_roster.desired_members(students)) == {
        "students": {"ada-l", "bob-b"},
        "auditors": {"eve-e"},
    }


def test_desired_members_ignores_not_yet_onboarded_rows():
    # no handle yet -> nothing to add to either team (the org invite happens on onboard)
    students = _roster(
        "ada@uni.edu,Ada,,,dsl-abc,enrolled",
        "eve@uni.edu,Eve,,,dsl-xyz,auditor",
    )
    assert _handles(sync_roster.desired_members(students)) == {
        "students": set(),
        "auditors": set(),
    }


def test_desired_members_covers_both_teams_even_when_one_is_empty():
    # both keys always present, so a pruning sync empties the team it should empty
    # rather than skipping it (a demoted-to-nobody team must still be reconciled).
    students = _roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    wanted = sync_roster.desired_members(students)
    assert set(wanted) == {sync_roster.TEAM, sync_roster.AUDITOR_TEAM}
    assert wanted[sync_roster.AUDITOR_TEAM] == []


def test_sync_hands_the_prune_each_teams_own_github_ids(monkeypatch):
    # The prune needs the ids to tell a renamed student from a stranger - but PER TEAM, or
    # a role change (students -> auditors) would be protected out of the prune that is
    # meant to carry it out.
    students = _roster(
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "bo@uni.edu,Bo,bo-b,99,dsl-def,auditor",
        "cy@uni.edu,Cy,,,dsl-ghi,enrolled",  # not onboarded - no id yet
    )
    monkeypatch.setattr(roster, "load", lambda org: students)
    monkeypatch.setattr(sync_roster, "set_org_membership", lambda *a, **kw: True)
    monkeypatch.setattr(sync_roster, "list_org_repos", lambda org: [])
    seen: dict[str, set[str]] = {}
    monkeypatch.setattr(
        sync_roster,
        "reconcile_team_members",
        lambda org, team, handles, prune=True, dry_run=False, keep_ids=frozenset(): (
            seen.update({team: set(keep_ids)}) or 0
        ),
    )
    assert sync_roster.sync("COHORT", prune=True) == 0
    assert seen == {sync_roster.TEAM: {"42"}, sync_roster.AUDITOR_TEAM: {"99"}}


def test_sync_fails_on_a_missing_roster_but_not_an_empty_one(monkeypatch):
    # Missing/unreadable students.csv (load -> None) is an error; a roster that
    # exists but has no rows yet (a freshly bootstrapped cohort) is a valid state
    # and must reconcile cleanly instead of failing every daily cron.
    monkeypatch.setattr(sync_roster, "reconcile_team_members", lambda *a, **kw: 0)
    monkeypatch.setattr(sync_roster, "list_org_repos", lambda org: [])

    monkeypatch.setattr(roster, "load", lambda org: None)
    assert sync_roster.sync("some-cohort", prune=True) == 1

    monkeypatch.setattr(roster, "load", lambda org: [])
    assert sync_roster.sync("some-cohort", prune=True) == 0


def test_load_distinguishes_missing_from_empty(monkeypatch):
    # Two cohort names, not one: students.csv is read once per cohort per process, so
    # re-answering for the same org would be served from that memo.
    monkeypatch.setattr(roster, "get_file_content", lambda *a, **kw: None)
    assert roster.load("cohort-without-a-roster") is None

    monkeypatch.setattr(roster, "get_file_content", lambda *a, **kw: HEADER + "\n")
    assert roster.load("cohort-with-an-empty-roster") == []


def test_students_csv_is_read_once_per_cohort_but_each_caller_parses_its_own(
    monkeypatch,
):
    # One run asks for the roster several times over (the handout, the collection, the
    # gradebook sync). The TEXT is memoised, not the rows - so no caller can hand another
    # a list it has since edited.
    reads: list[str] = []
    monkeypatch.setattr(
        roster,
        "get_file_content",
        lambda org, repo, path: (
            reads.append(org)
            or (HEADER + "\nada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n")
        ),
    )
    first = roster.load("COHORT")
    second = roster.load("COHORT")
    assert reads == ["COHORT"]
    assert first == second and first is not second
    first.clear()
    assert roster.load("COHORT") == second


def test_a_role_change_moves_the_handle_between_teams():
    before = _handles(
        sync_roster.desired_members(_roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,auditor"))
    )
    after = _handles(
        sync_roster.desired_members(
            _roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
        )
    )
    # the pruning pass sees ada-l as unwanted in `auditors` and wanted in `students`
    assert before["auditors"] == {"ada-l"} and before["students"] == set()
    assert after["students"] == {"ada-l"} and after["auditors"] == set()


# ------------------- off-boarding also revokes the repos the student was granted directly


def _repos(*names, templates=()):
    return [
        {"name": n, "isTemplate": n in templates, "topics": []}
        for n in (*names, *templates)
    ]


def test_submission_repo_suffixes_splits_on_the_cohort_template_name():
    repos = _repos(
        "assignment-1-ada-l",
        "assignment-1-bob-b",
        "assignment-4-project-team-x",
        "welcome",
        "grades-ada-l",
        templates=("assignment-1", "assignment-4-project"),
    )
    assert sync_roster.submission_repo_suffixes(repos) == [
        ("assignment-1-ada-l", "ada-l"),
        ("assignment-1-bob-b", "bob-b"),
        ("assignment-4-project-team-x", "team-x"),
    ]


def test_a_nested_template_name_splits_on_the_longest_match():
    # `assignment-4` and `assignment-4-project` both prefix the repo; only the longer one
    # leaves a suffix that is a handle rather than "project-ada-l".
    repos = _repos(
        "assignment-4-project-ada-l", templates=("assignment-4", "assignment-4-project")
    )
    assert sync_roster.submission_repo_suffixes(repos) == [
        ("assignment-4-project-ada-l", "ada-l")
    ]


def _offboard_stubs(monkeypatch, *, collaborators, declared=None, probed=None):
    repos = _repos(
        "assignment-1-ada-l",
        "assignment-1-zoe-z",
        "assignment-4-project-team-x",
        templates=("assignment-1", "assignment-4-project"),
    )
    monkeypatch.setattr(sync_roster, "list_org_repos", lambda org: repos)
    monkeypatch.setattr(sync_roster.teams, "load", lambda org: declared or {})

    def collaborator(org, repo, login):
        if probed is not None:
            probed.append(repo)
        return login in collaborators

    monkeypatch.setattr(sync_roster, "is_collaborator", collaborator)
    removed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sync_roster,
        "remove_collaborator",
        lambda org, repo, login: removed.append((repo, login)) or True,
    )
    return removed


def test_a_handle_off_the_roster_loses_its_submission_repos(monkeypatch):
    # Pruning the team was never the whole of it: an individual assignment grants the
    # student DIRECTLY as a maintain collaborator, so a deleted roster row kept full write
    # on every repo they had ever been handed.
    removed = _offboard_stubs(monkeypatch, collaborators={"ada-l", "zoe-z"})
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}) == 0
    assert removed == [("assignment-1-zoe-z", "zoe-z")]


def test_a_group_repos_team_suffix_is_never_revoked(monkeypatch):
    # `assignment-4-project-team-x` is named after a TEAM, not a person. Nothing is
    # revoked because nothing by that name is a collaborator - the name alone is not a
    # reason to take access away.
    removed = _offboard_stubs(monkeypatch, collaborators={"ada-l"})
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}) == 0
    assert removed == []


def test_a_repo_teams_csv_declares_as_a_teams_is_not_even_probed(monkeypatch):
    # teams.csv already says `assignment-4-project-team-x` belongs to a team, so asking
    # GitHub whether "team-x" collaborates on it is a paginated read per team repo per
    # night for an answer that is always "no".
    probed: list[str] = []
    removed = _offboard_stubs(
        monkeypatch,
        collaborators={"ada-l", "zoe-z"},
        declared={"assignment-4-project": {"team-x": ["ada-l"]}},
        probed=probed,
    )
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}) == 0
    assert removed == [("assignment-1-zoe-z", "zoe-z")]
    assert probed == ["assignment-1-zoe-z"], "the team repo was probed anyway"


def test_a_team_name_that_matches_another_assignment_still_probes(monkeypatch):
    # Matched on the whole repo NAME, not the bare suffix: team names and student handles
    # share a namespace, so a team called `zoe-z` under one assignment must not stop an
    # off-boarded @zoe-z losing her INDIVIDUAL repo for another.
    probed: list[str] = []
    removed = _offboard_stubs(
        monkeypatch,
        collaborators={"ada-l", "zoe-z"},
        declared={"assignment-4-project": {"zoe-z": ["ada-l"]}},
        probed=probed,
    )
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}) == 0
    assert removed == [("assignment-1-zoe-z", "zoe-z")]
    assert "assignment-1-zoe-z" in probed


def test_a_case_only_difference_is_the_same_account(monkeypatch):
    removed = _offboard_stubs(monkeypatch, collaborators={"Ada-L", "zoe-z"})
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l", "zoe-z"}) == 0
    assert removed == []


def test_an_unreadable_collaborator_check_is_an_error_not_a_revoke(monkeypatch):
    _repos_only = _offboard_stubs(monkeypatch, collaborators=set())
    monkeypatch.setattr(sync_roster, "is_collaborator", lambda *a: None)
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}) == 2
    assert _repos_only == []  # a rate limit is not evidence of anything


def test_a_dry_run_revokes_nothing(monkeypatch):
    removed = _offboard_stubs(monkeypatch, collaborators={"zoe-z"})
    assert sync_roster.revoke_offboarded_access("COHORT", {"ada-l"}, dry_run=True) == 0
    assert removed == []


def test_a_pruning_sync_revokes_a_vanished_handles_submission_repos(monkeypatch):
    # The wiring, not the unit: `sync(prune=True)` has to CALL the revoke, and hand it
    # exactly the handles still on the roster (casefolded). Deleting the call, or passing
    # the wrong set, left an off-boarded student with maintain on every repo they had ever
    # been handed while every report said they had been removed.
    students = _roster("ada@uni.edu,Ada,Ada-L,42,dsl-abc,enrolled")
    monkeypatch.setattr(roster, "load", lambda org: students)
    monkeypatch.setattr(sync_roster, "set_org_membership", lambda *a, **kw: True)
    monkeypatch.setattr(sync_roster, "reconcile_team_members", lambda *a, **kw: 0)
    removed = _offboard_stubs(monkeypatch, collaborators={"ada-l", "zoe-z"})

    assert sync_roster.sync("COHORT", prune=True) == 0
    assert removed == [("assignment-1-zoe-z", "zoe-z")]  # ada-l is still on the roster


def test_the_revoke_only_runs_behind_the_prune_flag(monkeypatch):
    monkeypatch.setattr(sync_roster, "reconcile_team_members", lambda *a, **kw: 0)
    monkeypatch.setattr(roster, "load", lambda org: [])
    monkeypatch.setattr(
        sync_roster,
        "list_org_repos",
        lambda org: (_ for _ in ()).throw(AssertionError("must not enumerate repos")),
    )
    assert sync_roster.sync("COHORT", prune=False) == 0
