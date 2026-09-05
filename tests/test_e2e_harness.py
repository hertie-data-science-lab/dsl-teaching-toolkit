"""The pure parts of the live end-to-end harness (`tests/e2e`).

The harness itself only runs with `DSL_E2E=1` against the demo orgs, so its safety
reasoning would otherwise never be exercised by CI - and the safety reasoning is the half
that must not be wrong: which orgs are in scope, and whether the estate came back the way
it was found. Those are pure functions, and they are tested here, in the ordinary suite.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from dsl_course import schedule
from tests.e2e import allowlist, cleanup, drive, estate, schedule_edit

GATE = 'pytest.skip("live e2e - set DSL_E2E=1", allow_module_level=True)'
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
    # leave the harness waiting for a queue nothing is ever put in. The group is on the
    # release JOB and its value is a dry-run expression, so the literal is what is pulled
    # out - a real pass is the only kind this harness dispatches.
    assert drive.SCHEDULED_RELEASE_GROUP == "scheduled-release"


def test_a_schedule_push_that_drives_no_tick_is_not_an_error(monkeypatch):
    # `dispatch-scheduled-release.yml` is only in cohorts that have refreshed since it
    # shipped. Where it is not, the edit starts nothing and the harness carries on to its
    # own dispatch rather than timing out.
    monkeypatch.setattr(drive, "_runs", lambda repo, workflow, limit=30: [{"id": 1}])
    monkeypatch.setattr(drive, "_sleep", lambda seconds: None)
    clock = iter([0, 1, 999])
    monkeypatch.setattr(drive, "_now", lambda: next(clock))
    assert drive.wait_for_push_driven_tick("org/.github", "w.yml", {1}) is None


def test_the_tick_a_schedule_push_drives_is_waited_out(monkeypatch):
    # The push is a driver now, so the run it starts is waited out rather than raced: the
    # pass dispatched next must be the one whose artefacts the next stage reads.
    monkeypatch.setattr(
        drive, "_runs", lambda repo, workflow, limit=30: [{"id": 1}, {"id": 7}]
    )
    monkeypatch.setattr(drive, "_now", lambda: 0)
    waited = []
    monkeypatch.setattr(
        drive, "wait_for_run", lambda repo, run_id, timeout: waited.append(run_id)
    )
    assert drive.wait_for_push_driven_tick("org/.github", "w.yml", {1}) == 7
    assert waited == [7]


def test_only_unfinished_runs_count_as_busy():
    runs = [
        {"id": 3, "status": "completed"},
        {"id": 2, "status": "in_progress"},
        {"id": 1, "status": "queued"},
    ]
    assert [r["id"] for r in drive.busy(runs)] == [2, 1]
    assert drive.busy([{"id": 9, "status": "completed"}]) == []


# ----------------------------------------------------------- the fenced schedule edit

SCHEDULE = """\
timezone: Europe/Berlin

assignments:
  assignment-1:
    course_source_repo: assignment-1-f2026
    due_datetime: 2026-10-13

events:
  final-exam:
    event_datetime: 2026-12-01
"""

BLOCK = """\
  assignment-90-e2eab12cd:
    course_source_repo: assignment-90-e2eab12cd
    due_datetime: 2026-09-04T23:59
"""


def test_the_block_goes_in_under_assignments_and_comes_out_clean():
    with_block = schedule_edit.insert_block(SCHEDULE, "e2eab12cd", BLOCK)
    assert "# dsl-e2e:e2eab12cd begin" in with_block
    # under `assignments:`, not at the end of the file - `releases:` still owns its own item
    assert with_block.index("assignment-90") < with_block.index("events:")
    assert schedule_edit.remove_block(with_block, "e2eab12cd") == SCHEDULE


def test_inserting_twice_replaces_rather_than_stacks():
    once = schedule_edit.insert_block(SCHEDULE, "e2eab12cd", BLOCK)
    twice = schedule_edit.insert_block(once, "e2eab12cd", BLOCK)
    assert twice == once
    assert twice.count("  assignment-90-e2eab12cd:") == 1


def test_removing_a_block_that_is_not_there_changes_nothing():
    # Cleanup is re-runnable, and an interrupted run may never have inserted anything.
    assert schedule_edit.remove_block(SCHEDULE, "e2eab12cd") == SCHEDULE


def test_one_run_does_not_remove_another_runs_block():
    both = schedule_edit.insert_block(
        schedule_edit.insert_block(SCHEDULE, "e2eaaaaaa1", BLOCK), "e2ebbbbbb2", BLOCK
    )
    left = schedule_edit.remove_block(both, "e2eaaaaaa1")
    assert "e2ebbbbbb2 begin" in left and "e2eaaaaaa1" not in left


def test_a_schedule_with_no_assignments_key_is_refused():
    with pytest.raises(ValueError, match="assignments:"):
        schedule_edit.insert_block("timezone: Europe/Berlin\n", "e2eab12cd", BLOCK)


# ------------------------------------------------------------- what cleanup may delete

RUN = "e2eab12cd"


@pytest.mark.parametrize(
    "name,mine",
    [
        ("assignment-90-e2eab12cd", True),
        ("assignment-90-e2eab12cd-template", True),
        ("assignment-90-e2eab12cd-henrycgbaker", True),
        ("assignment-90-e2eab12cd2", False),  # a longer id, not a suffix of ours
        ("assignment-90-e2effffff", False),  # another run
        ("assignment-9-e2eab12cd", False),  # a real assignment that starts the same way
        ("assignment-1-regression-henrycgbaker", False),
        ("classroom-config", False),
    ],
)
def test_only_this_runs_repos_are_deletable(name, mine):
    assert cleanup.is_run_repo(name, RUN) is mine


def test_another_runs_leavings_are_reported_not_deleted():
    assert cleanup.is_drift("assignment-90-e2effffff-jane", RUN)
    assert not cleanup.is_drift("assignment-90-e2eab12cd-jane", RUN)
    assert not cleanup.is_drift("classroom-config", RUN)


@pytest.mark.parametrize(
    "path,mine",
    [
        ("snapshots/assignment-90-e2eab12cd.csv", True),
        ("autograde/assignment-90-e2eab12cd/_graded.json", True),
        ("grading_sheets/assignment-90-e2eab12cd.yml", True),
        ("snapshots/assignment-1.csv", False),
        ("schedule.yml", False),
        ("autograde/assignment-90-e2effffff/_graded.json", False),
    ],
)
def test_only_this_runs_artefacts_are_dropped(path, mine):
    assert cleanup._is_artefact(path, RUN) is mine


def test_a_run_id_that_is_not_one_is_refused():
    # It is interpolated into a delete pattern; `.*` would match every repo in the org.
    for junk in ("", "*", "e2e", "all", "e2eab12cd-extra"):
        with pytest.raises(ValueError, match="not a run id"):
            cleanup.slug(junk)
    assert cleanup.check_run_id(cleanup.new_run_id())


def test_cleanup_refuses_without_the_transport_fence(monkeypatch):
    monkeypatch.delenv("DSL_ORG_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="DSL_ORG_ALLOWLIST is not set"):
        cleanup.cleanup(RUN, dry_run=True)
    # and as a command it says so and exits 1 rather than traceback-ing - having reached
    # no `gh` at all, which `conftest._no_live_gh` is what proves
    assert cleanup.main(["--run-id", RUN, "--dry-run"]) == 1


def _pipeline_module(monkeypatch):
    """The live pipeline module, imported past its own gate.

    Importing it is the point: it never RUNS in CI, so a typo in it would surface only
    mid-run, after the harness had already made repos in a real org. Without the env var
    the import raises `Skipped` and the test that wanted it is quietly skipped too - which
    is exactly the hole this closes."""
    monkeypatch.setenv("DSL_E2E", "1")
    return importlib.import_module("tests.e2e.test_assignment_pipeline")


def test_the_live_pipeline_module_imports(monkeypatch):
    module = _pipeline_module(monkeypatch)
    assert {module.COURSE_ORG, module.COHORT_ORG} == set(allowlist.DEMO_ORGS)


def test_the_block_the_harness_really_inserts_is_valid_yaml(monkeypatch):
    """The fenced text goes into a file the scheduler parses every fifteen minutes: an
    indentation slip here would not fail the harness, it would fail the cohort."""
    module = _pipeline_module(monkeypatch)
    when = datetime(2026, 9, 4, 14, 0)
    later = datetime(2026, 9, 4, 15, 0)
    block = module._schedule_block("assignment-90-e2eab12cd", when, when, later)
    doc = yaml.safe_load(schedule_edit.insert_block(SCHEDULE, "e2eab12cd", block))
    assert set(doc) == {"timezone", "assignments", "events"}
    entry = doc["assignments"]["assignment-90-e2eab12cd"]
    assert entry["course_source_repo"] == "assignment-90-e2eab12cd"
    assert set(entry) <= schedule.KNOWN_ASSIGNMENT | {"title"}
    # The due date and the cutoff are separate instants: collapsing them would skip the
    # refresh pass entirely, which is most of what the live run is there to exercise.
    assert entry["due_datetime"] != entry["grading_datetime"]


def test_every_live_test_module_carries_the_gate():
    """The gate is per-module rather than in the e2e conftest (which says why), so a new
    module that forgot it would drive real orgs from CI. Text, not behaviour, because the
    whole point is that the line must be there BEFORE anything imports the module."""
    modules = sorted((Path(__file__).parent / "e2e").glob("test_*.py"))
    assert modules, "the live harness has no test modules"
    for path in modules:
        assert GATE in path.read_text(), f"{path.name} is not gated on DSL_E2E"
