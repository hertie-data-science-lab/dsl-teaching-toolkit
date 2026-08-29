"""sync-roster pure core -- which handle belongs in which role team. The reconcile decides
org + team access for a whole cohort, so the split has to be exact: an auditor landing in
`students` would be handed assignment repos, an enrolled student landing in `auditors`
would silently lose them. The gh calls around it are wiring, not tested.
"""

from __future__ import annotations

from dsl_course import roster, sync_roster

HEADER = "hertie_email,name,github_handle,github_id,enrol_code,role"


def _roster(*rows: str) -> list[roster.Student]:
    return roster.parse("\n".join((HEADER, *rows)) + "\n")


def test_desired_members_splits_enrolled_from_auditors():
    students = _roster(
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
        "bob@uni.edu,Bob,bob-b,44,dsl-def,",  # blank role -> enrolled
    )
    assert sync_roster.desired_members(students) == {
        "students": {"ada-l", "bob-b"},
        "auditors": {"eve-e"},
    }


def test_desired_members_ignores_not_yet_onboarded_rows():
    # no handle yet -> nothing to add to either team (the org invite happens on onboard)
    students = _roster(
        "ada@uni.edu,Ada,,,dsl-abc,enrolled",
        "eve@uni.edu,Eve,,,dsl-xyz,auditor",
    )
    assert sync_roster.desired_members(students) == {
        "students": set(),
        "auditors": set(),
    }


def test_desired_members_covers_both_teams_even_when_one_is_empty():
    # both keys always present, so a pruning sync empties the team it should empty
    # rather than skipping it (a demoted-to-nobody team must still be reconciled).
    students = _roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    wanted = sync_roster.desired_members(students)
    assert set(wanted) == {sync_roster.TEAM, sync_roster.AUDITOR_TEAM}
    assert wanted[sync_roster.AUDITOR_TEAM] == set()


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
    monkeypatch.setattr(roster, "get_file_content", lambda *a, **kw: None)
    assert roster.load("some-cohort") is None

    monkeypatch.setattr(roster, "get_file_content", lambda *a, **kw: HEADER + "\n")
    assert roster.load("some-cohort") == []


def test_a_role_change_moves_the_handle_between_teams():
    before = sync_roster.desired_members(
        _roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,auditor")
    )
    after = sync_roster.desired_members(
        _roster("ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
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


def _offboard_stubs(monkeypatch, *, collaborators):
    repos = _repos(
        "assignment-1-ada-l",
        "assignment-1-zoe-z",
        "assignment-4-project-team-x",
        templates=("assignment-1", "assignment-4-project"),
    )
    monkeypatch.setattr(sync_roster, "list_org_repos", lambda org: repos)
    monkeypatch.setattr(
        sync_roster,
        "is_collaborator",
        lambda org, repo, login: login in collaborators,
    )
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


def test_the_revoke_only_runs_behind_the_prune_flag(monkeypatch):
    monkeypatch.setattr(sync_roster, "reconcile_team_members", lambda *a, **kw: 0)
    monkeypatch.setattr(roster, "load", lambda org: [])
    monkeypatch.setattr(
        sync_roster,
        "list_org_repos",
        lambda org: (_ for _ in ()).throw(AssertionError("must not enumerate repos")),
    )
    assert sync_roster.sync("COHORT", prune=False) == 0
