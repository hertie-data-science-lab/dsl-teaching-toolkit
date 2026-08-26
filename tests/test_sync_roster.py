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
