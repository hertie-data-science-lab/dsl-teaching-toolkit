"""Org and team membership: creating a team, reconciling one team's roster against
what a config file declares, and the guards that stop a sweep pruning the wrong
person."""

from __future__ import annotations

from dsl_course import gh_teams


def test_get_org_owners_distinguishes_no_owners_from_an_unreadable_list(monkeypatch):
    # An empty frozenset disables the prune guard in reconcile_team_members, so a failed
    # read must NOT produce one - it produces None, which skips pruning altogether.
    gh_teams.get_org_owners.cache_clear()
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    assert gh_teams.get_org_owners("Org") is None
    gh_teams.get_org_owners.cache_clear()
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (0, "[]"))
    assert gh_teams.get_org_owners("Org") == frozenset()
    gh_teams.get_org_owners.cache_clear()


def test_reconcile_team_members_adds_missing_and_removes_extra(monkeypatch):
    monkeypatch.setattr(
        gh_teams, "get_team_members", lambda org, team: {"alice", "bob"}
    )
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: None)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    added, removed = [], []
    monkeypatch.setattr(
        gh_teams,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "instructors", {"alice", "carol"})
    assert errors == 0
    assert added == ["carol"]
    assert removed == ["bob"]


def test_reconcile_team_members_never_prunes_a_renamed_account(monkeypatch):
    # A GitHub login is renameable and its id is not. `ada-new` is not in `wanted` (the
    # roster still spells the old name), but it carries the id of a roster row - so it is
    # the same student, and pruning it was the second half of an unrecoverable break: the
    # add of `ada-old` 404s and the eviction repeats every night. `stranger` has no roster
    # id and still goes.
    monkeypatch.setattr(
        gh_teams, "get_team_members", lambda org, team: {"ada-new", "stranger"}
    )
    monkeypatch.setattr(
        gh_teams,
        "get_team_member_ids",
        lambda org, team: {"ada-new": "42", "stranger": "99"},
    )
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: None)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    removed = []
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members(
        "org", "students", {"ada-old"}, keep_ids={"42"}
    )
    assert errors == 0
    assert removed == ["stranger"]


def test_reconcile_team_members_skips_the_prune_when_ids_are_unreadable(monkeypatch):
    # Pruning without the ids is exactly how a renamed student is evicted, so an
    # unreadable id listing skips the prune whole - the same rule the owner list follows.
    monkeypatch.setattr(
        gh_teams, "get_team_members", lambda org, team: {"ada-new", "stranger"}
    )
    monkeypatch.setattr(gh_teams, "get_team_member_ids", lambda org, team: None)
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: None)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    removed = []
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    assert (
        gh_teams.reconcile_team_members("org", "students", {"ada-old"}, keep_ids={"42"})
        == 0
    )
    assert removed == []


def test_reconcile_team_members_never_prunes_the_acting_login(monkeypatch):
    monkeypatch.setattr(
        gh_teams, "get_team_members", lambda org, team: {"alice", "hertie-dsl-bot"}
    )
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: "hertie-dsl-bot")
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    removed = []
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "course-admin", wanted=set())
    assert errors == 0
    assert removed == ["alice"]


def test_reconcile_team_members_never_prunes_any_org_owner(monkeypatch):
    # The robust fix: exclude ALL owners, not just whoever's currently running the
    # sync - so a human running this locally doesn't evict the bot (or vice versa).
    monkeypatch.setattr(
        gh_teams,
        "get_team_members",
        lambda org, team: {"alice", "hertie-dsl-bot", "henrycgbaker"},
    )
    monkeypatch.setattr(
        gh_teams, "_acting_login", lambda: "henrycgbaker"
    )  # a human, running locally
    monkeypatch.setattr(
        gh_teams,
        "get_org_owners",
        lambda org: frozenset({"hertie-dsl-bot", "henrycgbaker"}),
    )
    removed = []
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "course-admin", wanted=set())
    assert errors == 0
    assert removed == ["alice"]  # neither owner touched, despite neither being declared


def test_reconcile_team_members_compares_case_insensitively(monkeypatch, capsys):
    # GitHub logins are case-insensitive: a hand-typed `Anna-Adams` and the API's
    # `anna-adams` are the same account. Comparing raw casing added-then-pruned it every
    # run, oscillating that person's access nightly.
    monkeypatch.setattr(gh_teams, "get_team_members", lambda org, team: {"anna-adams"})
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: None)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    added, removed = [], []
    monkeypatch.setattr(
        gh_teams,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "instructors", {"Anna-Adams"})
    assert errors == 0
    assert added == []  # already present (case-folded) - not re-added
    assert removed == []  # ...and therefore not pruned as "unwanted"


def test_reconcile_team_members_aborts_when_current_membership_is_unreadable(
    monkeypatch, capsys
):
    # get_team_members returns None when the team's membership can't be read. Adding or
    # pruning blind against it is unsafe, so the whole reconcile aborts with an error -
    # it must not treat the team as empty (which would re-add everyone, or prune nobody).
    monkeypatch.setattr(gh_teams, "get_team_members", lambda org, team: None)
    added, removed = [], []
    monkeypatch.setattr(
        gh_teams,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "instructors", {"alice"})
    assert errors == 1
    assert added == [] and removed == []
    assert "reconcile aborted" in capsys.readouterr().err


def test_get_team_members_returns_none_on_failure_not_an_empty_set(monkeypatch):
    # None (unreadable) must never be conflated with an empty team, or a reconcile acts
    # blind. Mirrors get_org_owners.
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (1, "gh: HTTP 502"))
    assert gh_teams.get_team_members("Org", "students") is None
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (0, "not json"))
    assert gh_teams.get_team_members("Org", "students") is None
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (0, '[{"login": "alice"}]'))
    assert gh_teams.get_team_members("Org", "students") == {"alice"}


def test_create_team_only_treats_an_already_exists_422_as_success(monkeypatch):
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (0, ""))
    assert gh_teams.create_team("Org", "students") is True
    monkeypatch.setattr(
        gh_teams, "gh", lambda *a, **k: (1, "HTTP 422: name already_exists")
    )
    assert gh_teams.create_team("Org", "students") is True
    # The body GitHub's teams endpoint ACTUALLY returns for a duplicate team, verbatim.
    # It says neither "already exists" nor `already_exists`, so it read as a hard failure
    # and every membership sync after a team's first creation died on it.
    duplicate_team_422 = (
        '{"message":"Validation Failed","errors":[{"resource":"Team",'
        '"code":"unprocessable","field":"data",'
        '"message":"Name must be unique for this org"}],'
        '"documentation_url":"https://docs.github.com/rest/teams..."}'
    )
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: (1, duplicate_team_422))
    assert gh_teams.create_team("Org", "students") is True
    monkeypatch.setattr(
        gh_teams,
        "gh",
        lambda *a, **k: (1, "HTTP 422: Validation Failed - name too long"),
    )
    assert gh_teams.create_team("Org", "x" * 200) is False


def test_is_valid_github_username_charset_and_hyphen_rules():
    assert gh_teams.is_valid_github_username("anna-adams")
    assert gh_teams.is_valid_github_username("Anna-Adams")
    assert gh_teams.is_valid_github_username("a" * 39)
    assert not gh_teams.is_valid_github_username("a" * 40)  # too long
    assert not gh_teams.is_valid_github_username("-anna")  # leading hyphen
    assert not gh_teams.is_valid_github_username("anna-")  # trailing hyphen
    assert not gh_teams.is_valid_github_username("an--na")  # double hyphen
    assert not gh_teams.is_valid_github_username("a_b")  # underscore not allowed
    assert not gh_teams.is_valid_github_username("")


def test_reconcile_team_members_skips_the_prune_when_the_owners_are_unreadable(
    monkeypatch, capsys
):
    # Without the owner list there is no way to tell an Owner from a stray member, and a
    # blind prune could evict one. Adds still happen; the prune pass is skipped, loudly.
    monkeypatch.setattr(gh_teams, "get_team_members", lambda org, team: {"alice"})
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: None)
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: None)
    added, removed = [], []
    monkeypatch.setattr(
        gh_teams,
        "add_team_member",
        lambda org, team, h, role="member": added.append(h) or True,
    )
    monkeypatch.setattr(
        gh_teams, "remove_team_member", lambda org, team, h: removed.append(h) or True
    )
    errors = gh_teams.reconcile_team_members("org", "course-admin", {"carol"})
    assert errors == 0
    assert added == ["carol"]
    assert removed == []
    assert "pruning skipped" in capsys.readouterr().err


def test_reconcile_keeps_the_handles_it_adds_and_removes_out_of_a_public_log(
    capsys, monkeypatch
):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(gh_teams, "get_team_members", lambda org, team: {"zoe-zed"})
    monkeypatch.setattr(gh_teams, "_acting_login", lambda: "bot")
    monkeypatch.setattr(gh_teams, "get_org_owners", lambda org: frozenset())
    monkeypatch.setattr(gh_teams, "add_team_member", lambda *a, **k: True)
    monkeypatch.setattr(gh_teams, "remove_team_member", lambda *a, **k: True)
    assert (
        gh_teams.reconcile_team_members("org", "students", {"ada-l"}, prune=True) == 0
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out and "zoe-zed" not in captured.out
