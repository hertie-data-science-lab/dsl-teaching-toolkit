"""The pure parts of the live end-to-end harness (`tests/e2e`).

The harness itself only runs with `DSL_E2E=1` against the demo orgs, so its safety
reasoning would otherwise never be exercised by CI - and the safety reasoning is the half
that must not be wrong: which orgs are in scope, and whether the estate came back the way
it was found. Those are pure functions, and they are tested here, in the ordinary suite.
"""

from __future__ import annotations

import pytest

from tests.e2e import allowlist, drive, estate

OTHER = "hertie-ml-26-deep"
COURSE, COHORT = sorted(allowlist.DEMO_ORGS)


# ------------------------------------------------------------------ which orgs, exactly


def test_the_default_scope_is_both_demo_orgs(monkeypatch):
    monkeypatch.delenv("DSL_E2E_ORGS", raising=False)
    assert allowlist.orgs() == allowlist.DEMO_ORGS


def test_the_env_var_may_narrow_the_scope(monkeypatch):
    monkeypatch.setenv("DSL_E2E_ORGS", f" {COHORT} ")
    assert allowlist.orgs() == frozenset({COHORT})
    allowlist.assert_allowed(COHORT)
    with pytest.raises(RuntimeError, match="not in scope"):
        allowlist.assert_allowed(COURSE)


def test_the_env_var_may_not_widen_it(monkeypatch):
    # The whole point of a literal frozenset in the source: a typo, or a copied command
    # line from another course, must not be able to aim this harness at a real org.
    monkeypatch.setenv("DSL_E2E_ORGS", f"{COHORT},{OTHER}")
    with pytest.raises(RuntimeError, match="only narrow"):
        allowlist.orgs()


def test_an_org_outside_the_demo_pair_is_never_allowed(monkeypatch):
    monkeypatch.delenv("DSL_E2E_ORGS", raising=False)
    with pytest.raises(RuntimeError, match="not in scope"):
        allowlist.assert_allowed(OTHER)


def test_the_transport_fence_must_be_up(monkeypatch):
    monkeypatch.delenv("DSL_E2E_ORGS", raising=False)
    monkeypatch.delenv("DSL_ORG_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="DSL_ORG_ALLOWLIST is not set"):
        allowlist.assert_fence()
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", f"{COURSE},{OTHER}")
    with pytest.raises(RuntimeError, match="reaches past"):
        allowlist.assert_fence()
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", f"{COURSE},{COHORT}")
    assert allowlist.assert_fence() == allowlist.DEMO_ORGS


# ------------------------------------------------------------------- did it leave a trace


def _fp(repos: dict, config: dict) -> dict:
    return {"repos": repos, estate.CONFIG_REPO: config}


def test_an_untouched_estate_diffs_to_nothing():
    fp = _fp(
        {"welcome": {"private": False, "topics": [], "archived": False}}, {"a": "1"}
    )
    assert estate.diff(fp, fp) == {}


def test_a_repo_left_behind_shows_up():
    before = _fp({}, {})
    after = _fp(
        {"assignment-90-e2eab12": {"private": True, "topics": [], "archived": False}},
        {},
    )
    assert estate.diff(before, after) == {
        "repos/assignment-90-e2eab12": (None, after["repos"]["assignment-90-e2eab12"])
    }


def test_a_changed_topic_and_a_deleted_repo_both_show_up():
    before = _fp(
        {
            "welcome": {"private": False, "topics": ["dsl-welcome"], "archived": False},
            "gone": {"private": True, "topics": [], "archived": False},
        },
        {},
    )
    after = _fp({"welcome": {"private": False, "topics": [], "archived": False}}, {})
    changed = estate.diff(before, after)
    assert set(changed) == {"repos/welcome", "repos/gone"}
    assert changed["repos/gone"][1] is None


def test_a_snapshot_left_in_classroom_config_shows_up():
    before = _fp({}, {"schedule.yml": "aaa"})
    after = _fp(
        {}, {"schedule.yml": "aaa", "snapshots/assignment-90-e2eab12.csv": "bbb"}
    )
    assert estate.diff(before, after) == {
        "classroom-config/snapshots/assignment-90-e2eab12.csv": (None, "bbb")
    }


def test_the_fingerprint_reads_visibility_as_private(monkeypatch):
    monkeypatch.setattr(
        estate.discovery,
        "list_org_repos",
        lambda org: [
            {
                "name": "welcome",
                "visibility": "public",
                "topics": ["x"],
                "archived": False,
            },
            {"name": "classroom-config", "visibility": "private", "archived": True},
        ],
    )
    monkeypatch.setattr(estate.repos, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(
        estate.gh_contents, "repo_blob_shas", lambda *a: {"schedule.yml": "s"}
    )
    fp = estate.fingerprint(COHORT)
    assert fp["repos"]["welcome"] == {
        "private": False,
        "topics": ["x"],
        "archived": False,
    }
    assert fp["repos"]["classroom-config"] == {
        "private": True,
        "topics": [],
        "archived": True,
    }
    assert fp[estate.CONFIG_REPO] == {"schedule.yml": "s"}


def test_a_config_repo_that_is_not_there_is_not_an_error(monkeypatch):
    # The course org has no classroom-config; only cohorts do.
    monkeypatch.setattr(estate.discovery, "list_org_repos", lambda org: [])
    assert estate.fingerprint(COURSE) == {"repos": {}, estate.CONFIG_REPO: {}}


# ---------------------------------------------------------------------- driving a workflow


def test_the_harness_waits_on_the_group_the_renderer_declares():
    # Read from `workflows_render`, not retyped: renaming the group there would otherwise
    # leave the harness waiting for a queue nothing is ever put in.
    assert drive.SCHEDULED_RELEASE_GROUP == "scheduled-release"


def test_only_unfinished_runs_count_as_busy():
    runs = [
        {"id": 3, "status": "completed"},
        {"id": 2, "status": "in_progress"},
        {"id": 1, "status": "queued"},
    ]
    assert [r["id"] for r in drive.busy(runs)] == [2, 1]
    assert drive.busy([{"id": 9, "status": "completed"}]) == []
