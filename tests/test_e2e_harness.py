"""The pure parts of the live end-to-end harness (`tests/e2e`).

The harness itself only runs with `DSL_E2E=1` against the demo orgs, so its safety
reasoning would otherwise never be exercised by CI - and the safety reasoning is the half
that must not be wrong: which orgs are in scope, and whether the estate came back the way
it was found. Those are pure functions, and they are tested here, in the ordinary suite.
"""

from __future__ import annotations

import base64
import importlib
import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from dsl_course import course, ghcli, grades, repos, roster, scaffold, schedule
from tests.e2e import (
    allowlist,
    cleanup,
    drive,
    estate,
    schedule_edit,
    shapes,
    student,
)

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
    assert estate.fingerprint(COURSE) == {
        "repos": {},
        estate.CONFIG_REPO: {},
        estate.WORKFLOWS_DIR: {},
    }


def test_the_fingerprint_photographs_the_org_level_workflows(monkeypatch):
    # The run's own template repopulates four of the buttons' dropdowns, and a teardown
    # that deleted the template without re-rendering them left the org in a state no
    # refresh produces - invisible to a fingerprint of repos and classroom-config alone.
    monkeypatch.setattr(
        estate.discovery,
        "list_org_repos",
        lambda org: [
            {
                "name": ".github",
                "visibility": "public",
                "topics": [course.COURSE_HUB_TOPIC],
                "archived": False,
            }
        ],
    )
    monkeypatch.setattr(
        estate.ghcli, "gh_json", lambda *args: _tree({"release-assignment.yml": "abc"})
    )
    fp = estate.fingerprint(COURSE)
    assert fp[estate.WORKFLOWS_DIR] == {"release-assignment.yml": "abc"}
    assert estate.diff(fp, {**fp, estate.WORKFLOWS_DIR: {}}) == {
        ".github/workflows/release-assignment.yml": ("abc", None)
    }


def test_a_cohort_org_is_not_asked_for_org_level_workflows(monkeypatch):
    # It holds none - its own workflows live in `welcome` and `classroom-config` - and a
    # tree fetch of a directory that is not there raises rather than coming back empty.
    monkeypatch.setattr(
        estate.discovery,
        "list_org_repos",
        lambda org: [
            {
                "name": ".github",
                "visibility": "public",
                "topics": [course.COHORT_TOPIC],
                "archived": False,
            }
        ],
    )

    def refuse(*args):
        raise AssertionError("a cohort org has no org-level workflows to read")

    monkeypatch.setattr(estate.ghcli, "gh_json", refuse)
    assert estate.fingerprint(COHORT)[estate.WORKFLOWS_DIR] == {}


# ------------------------------------------------------- are the org's workflows current

# The preflight's real question. The heartbeat cannot answer it: its content is the date,
# so it moves at most once a day and a promotion an hour later leaves it untouched while
# the workflow files themselves have been rewritten.

RENDERED = {
    ".github/workflows/refresh-actions.yml": b"on: schedule\n",
    ".github/workflows/send-codes.yml": b"on: workflow_dispatch\n",
}


def _tree(shas: dict[str, str]) -> dict:
    return {
        "tree": [
            {"path": name, "type": "blob", "sha": sha} for name, sha in shas.items()
        ]
    }


def _org_holds(monkeypatch, shas: dict[str, str]) -> None:
    monkeypatch.setattr(estate.ghcli, "gh_json", lambda *args: _tree(shas))


def _sha(path: str) -> str:
    return estate.gh_contents.blob_sha(RENDERED[path])


def test_an_org_running_what_this_tip_renders_has_no_drift(monkeypatch):
    _org_holds(
        monkeypatch,
        {
            "refresh-actions.yml": _sha(".github/workflows/refresh-actions.yml"),
            "send-codes.yml": _sha(".github/workflows/send-codes.yml"),
        },
    )
    assert estate.workflow_drift(COURSE, RENDERED) == []


def test_one_stale_workflow_is_named(monkeypatch):
    _org_holds(
        monkeypatch,
        {
            "refresh-actions.yml": _sha(".github/workflows/refresh-actions.yml"),
            "send-codes.yml": "0" * 40,
        },
    )
    assert estate.workflow_drift(COURSE, RENDERED) == ["send-codes.yml"]


def test_a_workflow_the_org_never_got_is_named(monkeypatch):
    _org_holds(
        monkeypatch,
        {"refresh-actions.yml": _sha(".github/workflows/refresh-actions.yml")},
    )
    assert estate.workflow_drift(COURSE, RENDERED) == ["send-codes.yml"]


def test_a_retired_workflow_the_org_still_holds_is_named(monkeypatch):
    # Refresh deletes retired workflows in the same commit it writes the current set, so a
    # leftover is the same signal as a stale file: this org has not been refreshed.
    _org_holds(
        monkeypatch,
        {
            "refresh-actions.yml": _sha(".github/workflows/refresh-actions.yml"),
            "send-codes.yml": _sha(".github/workflows/send-codes.yml"),
            "render-grades.yml": "1" * 40,
        },
    )
    assert estate.workflow_drift(COURSE, RENDERED) == ["render-grades.yml"]


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


@pytest.mark.parametrize(
    "schedule",
    [
        SCHEDULE,  # one trailing newline, the ordinary case
        SCHEDULE.rstrip("\n"),  # none - a file somebody saved without one
        SCHEDULE + "\n",  # two, which the edit may not quietly make one
    ],
)
def test_the_round_trip_leaves_the_file_byte_for_byte_as_it_was(schedule):
    # The whole point of fencing the edit rather than re-emitting the YAML: the cohort
    # gets its own file back. The edit used to end every write with exactly one newline,
    # so a schedule.yml that had none came back one byte longer - and the teardown's
    # fidelity check, which compares blob shas, cannot tell that from a real edit. It was
    # invisible until `gh_contents.get_file_content` stopped stripping what it reads.
    with_block = schedule_edit.insert_block(schedule, "e2eab12cd", BLOCK)
    assert schedule_edit.remove_block(with_block, "e2eab12cd") == schedule


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
        # One assignment per shape, so every artefact this run writes is named after the
        # NAMESPACE plus a shape - `-` is as much a boundary here as `/` and `.`.
        ("snapshots/assignment-90-e2eab12cd-shared.csv", True),
        ("grading_sheets/assignment-90-e2eab12cd-student-choice.yml", True),
        ("autograde/assignment-90-e2eab12cd-private/_graded.json", True),
        ("snapshots/assignment-1.csv", False),
        ("schedule.yml", False),
        ("autograde/assignment-90-e2effffff/_graded.json", False),
        # A longer run id, not a shape of ours.
        ("snapshots/assignment-90-e2eab12cd2-private.csv", False),
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


def _one_repo_of_this_run(monkeypatch, answer: tuple[int, str]) -> None:
    """One org holding exactly one of this run's repos, and a `gh` that answers `answer`
    to the delete."""
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", f"{COURSE},{COHORT}")
    monkeypatch.setenv("DSL_E2E_ORGS", COHORT)
    monkeypatch.setattr(
        cleanup.discovery, "list_org_repos", lambda org: [{"name": cleanup.slug(RUN)}]
    )
    monkeypatch.setattr(cleanup, "_clean_config", lambda *args: 0)
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: answer)


FORBIDDEN = (1, "HTTP 403: Must have admin rights to Repository")


def test_a_delete_that_403s_is_counted_undone_not_deleted(monkeypatch, capsys):
    # The first live run read `[ok] 3 repo(s) deleted` while all three 403'd, because the
    # count was of attempts. The two numbers must describe the same repos.
    _one_repo_of_this_run(monkeypatch, FORBIDDEN)
    left: list[str] = []
    assert cleanup.cleanup(RUN, left=left) == 1
    out, err = capsys.readouterr()
    assert f"{COHORT}: 0 repo(s) deleted" in out
    assert "left 1 thing(s) undone" in err
    assert left == [f"{COHORT}/{cleanup.slug(RUN)}"]


def test_a_delete_that_works_is_counted_deleted(monkeypatch, capsys):
    _one_repo_of_this_run(monkeypatch, (0, ""))
    left: list[str] = []
    assert cleanup.cleanup(RUN, left=left) == 0
    assert f"{COHORT}: 1 repo(s) deleted" in capsys.readouterr().out
    assert left == []


def test_what_is_left_behind_comes_with_the_command_to_delete_it(monkeypatch, capsys):
    # The run is already over by the time anyone reads this; a re-run with the same token
    # would 403 again, so the way out is the delete spelt out.
    _one_repo_of_this_run(monkeypatch, FORBIDDEN)
    assert cleanup.main(["--run-id", RUN]) == 1
    assert "gh api --method DELETE repos/<org>/<repo>" in capsys.readouterr().out


def test_the_repo_names_in_those_commands_are_verbose_only(monkeypatch, capsys):
    # `<slug>-<handle>` names a student; the template is safe to print, the filled-in
    # command is not.
    filled = f"DELETE repos/{COHORT}/{cleanup.slug(RUN)}"
    _one_repo_of_this_run(monkeypatch, FORBIDDEN)
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    cleanup.main(["--run-id", RUN])
    assert filled not in capsys.readouterr().out
    monkeypatch.setenv("DSL_VERBOSE", "1")
    cleanup.main(["--run-id", RUN])
    assert filled in capsys.readouterr().out


# ------------------------------------- and puts the buttons back the way it found them


def _a_course_org(
    monkeypatch, *, holds_run_repo: bool = False, drift=()
) -> list[tuple]:
    """One course org in scope, with the refresh's own writer recorded rather than run."""
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", COURSE)
    monkeypatch.setenv("DSL_E2E_ORGS", COURSE)
    listing = [
        {"name": ".github", "visibility": "public", "topics": [course.COURSE_HUB_TOPIC]}
    ]
    if holds_run_repo:
        listing.append({"name": cleanup.slug(RUN), "visibility": "private"})
    monkeypatch.setattr(cleanup.discovery, "list_org_repos", lambda org: listing)
    monkeypatch.setattr(cleanup.discovery, "central_ref_for", lambda org: "main")
    monkeypatch.setattr(cleanup, "_clean_config", lambda *args: 0)
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(
        cleanup.seed, "github_workflow_files", lambda org, ref: {"a.yml": b"x"}
    )
    written: list[tuple] = []

    def write(org, ref):
        written.append((org, ref))
        return 0

    monkeypatch.setattr(cleanup.seed, "seed_github_workflows", write)
    monkeypatch.setattr(
        cleanup.estate, "workflow_drift", lambda org, rendered: list(drift)
    )
    return written


def test_the_teardown_re_renders_the_course_orgs_buttons(monkeypatch):
    # Deleting the template is not enough: four of the buttons list it in a dropdown
    # rendered from the org's repo listing, and the next run's preflight refuses on
    # exactly that drift. So cleanup runs the refresh's writer at the org's own ref.
    written = _a_course_org(monkeypatch)
    assert cleanup.cleanup(RUN) == 0
    assert written == [(COURSE, "main")]


def test_a_cohort_org_has_no_buttons_to_re_render(monkeypatch):
    written = _a_course_org(monkeypatch)
    monkeypatch.setattr(
        cleanup.discovery,
        "list_org_repos",
        lambda org: [{"name": "welcome", "visibility": "public"}],
    )
    assert cleanup.cleanup(RUN) == 0
    assert written == []


def test_a_re_render_that_left_the_org_stale_is_counted_undone(monkeypatch, capsys):
    # The write went out and the org still runs something else: silence here is the next
    # run refusing to start, hours later, with nothing to connect it to this teardown.
    _a_course_org(monkeypatch, drift=("release-assignment.yml",))
    assert cleanup.cleanup(RUN) == 1
    assert "release-assignment.yml" in capsys.readouterr().err


def test_the_buttons_are_not_re_rendered_while_the_template_is_still_listed(
    monkeypatch, capsys
):
    # Rendering from a listing that still holds this run's template would write the
    # assignment straight back into the dropdowns - the opposite of a teardown.
    written = _a_course_org(monkeypatch, holds_run_repo=True)
    assert cleanup.cleanup(RUN) == 1
    assert written == []
    assert "NOT re-rendering" in capsys.readouterr().err


def test_a_dry_run_re_renders_nothing(monkeypatch):
    written = _a_course_org(monkeypatch)
    assert cleanup.cleanup(RUN, dry_run=True) == 0
    assert written == []


def test_cleanup_refuses_without_the_transport_fence(monkeypatch):
    monkeypatch.delenv("DSL_ORG_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="DSL_ORG_ALLOWLIST is not set"):
        cleanup.cleanup(RUN, dry_run=True)
    # and as a command it says so and exits 1 rather than traceback-ing - having reached
    # no `gh` at all, which `conftest._no_live_gh` is what proves
    assert cleanup.main(["--run-id", RUN, "--dry-run"]) == 1


# ------------------------------------------------- putting the shared files back exactly

# `csv.writer` writes CRLF, so this is the shape `cohort-gradebook.csv` really has on
# disk - trailing newline and all. Every byte of it has to survive the round trip.
CRLF_CSV = b"hertie_email,name\r\nada@x,Ada\r\n"


def _reads(monkeypatch, answers: dict[str, tuple[int, str]]):
    """Stand `cleanup`'s `gh` up on canned answers keyed by the contents path."""
    seen: list[tuple[str, ...]] = []

    def fake(*args: str, **kw):
        seen.append(args)
        for path, answer in answers.items():
            if any(a.endswith(f"/contents/{path}") for a in args):
                return answer
        return 1, "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(cleanup.ghcli, "gh", fake)
    return seen


def test_a_recorded_file_keeps_every_byte_it_had(monkeypatch):
    # `gh_contents.get_file_content` reads the subprocess in text mode and strips it, so a
    # CRLF file came back with every \r gone and no trailing newline. Written back that is
    # a different blob, and the estate check at teardown - which compares blob shas -
    # called it drift on every single run.
    encoded = base64.b64encode(CRLF_CSV).decode()
    _reads(monkeypatch, {"cohort-gradebook.csv": (0, encoded)})
    assert cleanup.file_bytes(COHORT, "classroom-config", "cohort-gradebook.csv") == (
        CRLF_CSV
    )


def test_a_file_that_is_not_there_is_recorded_as_absent(monkeypatch):
    _reads(monkeypatch, {})
    assert (
        cleanup.file_bytes(COHORT, "classroom-config", "gradebook/nothing.csv") is None
    )


def test_a_read_that_failed_is_not_read_as_absent(monkeypatch):
    # An absent file is DELETED by the restore. A rate limit read as absence would take a
    # real file out of a real org.
    _reads(monkeypatch, {"cohort-gradebook.csv": (1, "gh: API rate limit exceeded")})
    with pytest.raises(RuntimeError, match="could not read"):
        cleanup.file_bytes(COHORT, "classroom-config", "cohort-gradebook.csv")


def test_the_restore_writes_back_exactly_what_was_recorded(monkeypatch):
    written: list[tuple[str, str, dict, tuple]] = []
    monkeypatch.setattr(
        cleanup.gh_contents,
        "put_files",
        lambda org, repo, files, message, delete=(), **kw: (
            written.append((org, repo, files, tuple(delete))) or True
        ),
    )
    assert (
        cleanup.restore_files(
            COHORT,
            "classroom-config",
            {"cohort-gradebook.csv": CRLF_CSV, "gradebook/distributed.csv": None},
        )
        == 0
    )
    ((org, repo, files, delete),) = written
    assert (org, repo) == (COHORT, "classroom-config")
    assert files == {"cohort-gradebook.csv": CRLF_CSV}
    assert delete == ("gradebook/distributed.csv",)


def test_what_was_read_is_what_is_written_back(monkeypatch):
    # The whole point of the pair, in one line: record a file, hand it back, and the bytes
    # are the bytes. `put_files` skips a path whose blob already matches, so an unchanged
    # file makes no commit at all.
    _reads(
        monkeypatch,
        {"cohort-gradebook.csv": (0, base64.b64encode(CRLF_CSV).decode())},
    )
    recorded = cleanup.file_bytes(COHORT, "classroom-config", "cohort-gradebook.csv")
    written: list[dict] = []
    monkeypatch.setattr(
        cleanup.gh_contents,
        "put_files",
        lambda org, repo, files, message, delete=(), **kw: (
            written.append(files) or True
        ),
    )
    cleanup.restore_files(
        COHORT, "classroom-config", {"cohort-gradebook.csv": recorded}
    )
    assert written == [{"cohort-gradebook.csv": CRLF_CSV}]


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


def test_the_lock_file_goes_back_even_when_the_walk_died_before_distribute(monkeypatch):
    # The two recordings the teardown puts back are taken at different points - the lock
    # before the walk, because the HANDOUT is what moves it, and the distribute pair at
    # step 11. A run that died in between has only the first, and the lock still has to go
    # back: cleanup sweeps by run id, and this file carries none.
    module = _pipeline_module(monkeypatch)
    assert module._config_restore(b"assignments: {}\n", None) == {
        grades.TEAM_LOCK_PATH: b"assignments: {}\n"
    }


def test_a_lock_file_that_was_not_there_is_restored_by_deleting_it(monkeypatch):
    # A cohort bootstrapped before the lock existed records None, which `restore_files`
    # spells as a delete - so the handout's write comes out rather than being left as a
    # file that org never had.
    module = _pipeline_module(monkeypatch)
    recorded = module.Stage(
        "shared_before",
        detail={
            "registrar": CRLF_CSV,
            "distributed": b"target,channel\n",
            "grades_yml": b"",
            "readme": b"",
        },
    )
    assert module._config_restore(None, recorded) == {
        grades.TEAM_LOCK_PATH: None,
        grades.COHORT_CSV_NAME: CRLF_CSV,
        grades.DISTRIBUTED_PATH: b"target,channel\n",
    }


def test_a_central_ref_that_no_longer_resolves_is_reported_not_raised(monkeypatch):
    # `staging` stopped being a tier on 2026-09-07. A MissingCentralRef out of the tier
    # read would abort the preflight with a traceback that says neither what the org runs
    # nor that it is the wrong thing; the preflight's own assertion says both.
    module = _pipeline_module(monkeypatch)
    monkeypatch.setattr(
        module.gh_contents,
        "get_file_content",
        lambda org, repo, path: "central_ref: staging\n",
    )
    assert module._declared_tier() == "staging"


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


def test_the_privacy_scan_keeps_every_line_the_toolkit_printed(monkeypatch):
    # The gate step names the person who PRESSED the button, which a public log is
    # entitled to do. Only that step's own ACTOR/REPO lines come out of the scan - a
    # handle anywhere else, including on another `check-team` line, is still a leak.
    module = _pipeline_module(monkeypatch)
    gate = "check-team\tVerify the user\t2026-09-06T11:03:38Z   "
    log = (
        f"{gate}ACTOR: ada-l\n"
        f"{gate}REPO: org/.github\n"
        f"{gate}ada-l may run\n"
        "collect-submissions\tCollect\t2026-09-06T11:03:56Z   [ok] 1 of 2\n"
    )
    kept = module._toolkit_lines(log)
    assert "ACTOR: ada-l" not in kept
    assert "REPO: org/.github" not in kept
    assert "ada-l may run" in kept
    assert "[ok] 1 of 2" in kept


def test_the_status_line_is_what_a_rewritten_sheet_is_quoted_by(monkeypatch):
    module = _pipeline_module(monkeypatch)
    sheet = "# GRADING SHEET\n# Status: OPEN - 1 of 2 students\nsubmissions:\n"
    assert module._status(sheet) == "# Status: OPEN - 1 of 2 students"
    assert module._status("submissions:\n") == ""


def test_every_live_test_module_carries_the_gate():
    """The gate is per-module rather than in the e2e conftest (which says why), so a new
    module that forgot it would drive real orgs from CI. Text, not behaviour, because the
    whole point is that the line must be there BEFORE anything imports the module."""
    modules = sorted((Path(__file__).parent / "e2e").glob("test_*.py"))
    assert modules, "the live harness has no test modules"
    for path in modules:
        assert GATE in path.read_text(), f"{path.name} is not gated on DSL_E2E"


# --------------------------------------------- the student pushes the way a student does


def test_the_students_git_calls_all_run_with_hooks_off(monkeypatch, tmp_path):
    """The maintainer's own `pre-push` fired inside the harness's throwaway clone and
    failed the first live run on their house lint rules. A student's machine has no such
    hook, so the harness must not have one either."""
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        student.ghcli, "git", lambda *args: seen.append(args) or (0, "")
    )
    monkeypatch.setenv(student.HANDLE_ENV, "e2e-student")
    monkeypatch.setenv(student.TOKEN_ENV, "ghp_notatoken")
    student.push_file(
        "org/repo", tmp_path / "clone", "submission.py", 'print("x")\n', "e2e: submit"
    )

    clone = next(a for a in seen if "clone" in a)
    assert ("--config", student.HOOKS_SETTING) == clone[1:3]
    for args in (a for a in seen if a is not clone):
        assert student.HOOKS_OFF[1] in args, f"hooks are live for `{' '.join(args)}`"

    push = next(a for a in seen if "push" in a)
    assert push[2:4] == student.HOOKS_OFF and "--no-verify" in push
    assert "--no-verify" in next(a for a in seen if "commit" in a)


def test_the_submission_is_clean_python(monkeypatch):
    # It is pushed to a repo a maintainer may clone next; nothing lints it any more.
    module = _pipeline_module(monkeypatch)
    assert module.SUBMISSION_BODY == 'print("e2e submission")\n'


# ------------------------------------------------- can this token take the run away again

HEADERS = (
    "HTTP/2.0 200 OK\r\nX-Oauth-Scopes: gist, read:org, repo\r\nDate: now\r\n\r\n{}"
)


def test_a_classic_token_without_delete_repo_is_refused(monkeypatch):
    module = _pipeline_module(monkeypatch)
    assert module.oauth_scopes(HEADERS) == frozenset({"gist", "read:org", "repo"})
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: (0, HEADERS))
    with pytest.raises(AssertionError, match="cannot delete repos"):
        module._assert_can_delete_repos()


def test_a_classic_token_with_delete_repo_passes(monkeypatch):
    module = _pipeline_module(monkeypatch)
    full = HEADERS.replace("read:org, repo", "delete_repo, read:org, repo")
    assert "delete_repo" in module.oauth_scopes(full)
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: (0, full))
    module._assert_can_delete_repos()


def test_a_fine_grained_token_is_probed_instead(monkeypatch, capsys):
    """It sends no `X-OAuth-Scopes` at all, so the scope check has nothing to read and
    `admin` on a repo the token can see is the question that can be answered."""
    module = _pipeline_module(monkeypatch)
    assert module.oauth_scopes("HTTP/2.0 200 OK\r\nDate: now\r\n\r\n{}") is None

    answers = iter([(0, "HTTP/2.0 200 OK\r\n\r\n{}"), (0, "true\n")])
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: next(answers))
    module._assert_can_delete_repos()
    assert "no X-OAuth-Scopes" in capsys.readouterr().out

    denied = iter([(0, "HTTP/2.0 200 OK\r\n\r\n{}"), (0, "false\n")])
    monkeypatch.setattr(ghcli, "gh", lambda *args, **kwargs: next(denied))
    with pytest.raises(AssertionError, match="cannot delete repos"):
        module._assert_can_delete_repos()


# ------------------------------------------------ one assignment per submission shape


def _seeded(shape: shapes.Shape) -> str:
    """`grading_config.yml` exactly as New assignment writes it for this shape - the real
    scaffold, so the harness's edits are exercised against the file they will actually
    meet and not against a hand-written sample of it."""
    return scaffold._grading_config(
        title="E2E",
        kind="individual",
        team_formation="self_select",
        submit_via=shape.submit_via,
        visibility=shape.visibility or "private",
        formats=["py"],
        autograde=shape.autograde,
        defaults={},
    )


def test_the_run_drives_every_shape_the_engine_can_make():
    # The table is the run's plan and the vocabulary is the engine's. A shape the toolkit
    # cannot make would be a stage that hangs; a shape it can make and nothing here drives
    # is a shape no live run has ever proved.
    assert {s.submit_via for s in shapes.SHAPES} == set(course.SUBMIT_VIA)
    assert {s.visibility for s in shapes.SHAPES if s.visibility} == set(
        course.VISIBILITIES
    )
    assert len(shapes.BY_NAME) == len(shapes.SHAPES)


def test_the_shape_words_are_the_ones_the_page_carries():
    # The live run asserts these strings in a page's front matter, and the theme `case`s
    # on them. Pinned here so a rename in `course.submit_shape` breaks in CI rather than
    # an hour into a live run.
    assert [s.key for s in shapes.SHAPES] == [
        "assignment-repo-private",
        "assignment-repo-public",
        "assignment-repo-student-choice",
        "external",
        "shared-dropbox-repo",
    ]


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_everything_a_shape_creates_is_inside_the_runs_namespace(shape):
    # Five assignments in one run, and one sweep for all of them: every name any shape
    # hands out has to be one `cleanup` deletes rather than one it reports as somebody
    # else's leavings.
    slug = shapes.slug(RUN, shape)
    assert cleanup.is_run_repo(slug, RUN)
    assert not cleanup.is_drift(slug, RUN)
    repo = shape.repo(RUN, "henrycgbaker")
    if repo:
        assert cleanup.is_run_repo(repo, RUN)
        assert not cleanup.is_drift(repo, RUN)


def test_an_external_assignment_has_no_repo_to_name():
    # The shape's whole point: the handout creates nothing, so there is no name for the
    # harness to look for and none for the log to leak.
    assert shapes.BY_NAME["external"].repo(RUN, "henrycgbaker") == ""
    assert shapes.BY_NAME["external"].folder("henrycgbaker") == ""


def test_the_drop_box_is_one_repo_and_the_work_is_a_folder_in_it():
    shape = shapes.BY_NAME["shared"]
    slug = shapes.slug(RUN, shape)
    assert shape.repo(RUN, "henrycgbaker") == f"{slug}-submissions"
    # Never the bare slug: that is the frozen cohort template the brief lives in.
    assert shape.repo(RUN, "henrycgbaker") != slug
    # The name carries no handle, which is what makes it the one submission repo a public
    # workflow log may print in full.
    assert "henrycgbaker" not in shape.repo(RUN, "henrycgbaker")
    assert shape.folder("henrycgbaker") == "henrycgbaker/"


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_the_config_the_harness_writes_parses_to_the_shape_it_meant(shape):
    # The whole run rests on this file: it is the only place a shape is declared, and a
    # value the parser drops hands out the DEFAULT shape under another name.
    spec = grades.parse_grading_spec(shapes.configure(_seeded(shape), shape))
    assert spec.submit_via == shape.submit_via
    assert spec.submit_shape == shape.key
    assert spec.submit_url == shape.submit_url
    assert spec.has_feedback_issue is shape.has_feedback_issue
    assert spec.creates_unit_repos is shape.creates_unit_repos


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_declaring_the_same_shape_twice_changes_nothing(shape):
    # Idempotent, so a config the New assignment form already got right is left alone and
    # the run commits nothing to the template it just created.
    once = shapes.configure(_seeded(shape), shape)
    assert shapes.configure(once, shape) == once


def test_a_commented_setting_is_uncommented_and_keeps_its_explanation():
    # `submit_url` is seeded commented out, with the sentence that says what it is for.
    # An instructor uncommenting it keeps that sentence; so does this.
    external = shapes.BY_NAME["external"]
    line = next(
        ln
        for ln in shapes.configure(_seeded(external), external).splitlines()
        if ln.startswith("submit_url:")
    )
    assert shapes.SUBMIT_URL in line
    assert "external only" in line


def test_the_scaffolds_placeholder_never_reaches_a_live_setting():
    # `grades._submit_url` refuses a line still carrying `CHANGE-ME`, so a `configure`
    # that merely uncommented the seeded line would ship a cohort a button pointing at a
    # page that does not exist - and the reader would drop the value on the way.
    for shape in shapes.SHAPES:
        for line in shapes.configure(_seeded(shape), shape).splitlines():
            if not line.startswith("#"):
                assert course.SETTING_PLACEHOLDER not in line


def test_a_setting_the_scaffold_never_writes_is_refused():
    with pytest.raises(ValueError, match="appears 0 time"):
        shapes.set_setting("title: x\n", "submit_shape", "assignment-repo-public")


def test_a_setting_that_appears_twice_is_refused():
    with pytest.raises(ValueError, match="appears 2 time"):
        shapes.set_setting("visibility: a\nvisibility: b\n", "visibility", "public")


def test_only_the_top_level_setting_is_edited():
    # `questions:` seeds an indented block, and a key nested inside one is a grader's
    # data rather than a setting: rewriting it would be editing somebody's question.
    text = "visibility: private\nquestions:\n  visibility: no\n"
    assert shapes.set_setting(text, "visibility", "public") == (
        "visibility: public\nquestions:\n  visibility: no\n"
    )


# --------------------------------------- the definition lives on the solution branch


def test_the_definition_is_read_off_the_solution_branch(monkeypatch):
    seen = []
    monkeypatch.setattr(
        shapes.gh_contents,
        "get_file_with_sha",
        lambda org, repo, path, ref="": (
            seen.append((org, repo, path, ref)) or ("title: x\n", "sha1")
        ),
    )
    assert shapes.read_config("course-org", "assignment-90-x") == ("title: x\n", "sha1")
    assert seen == [
        ("course-org", "assignment-90-x", grades.GRADING_FILE, course.SOLUTION_BRANCH)
    ]


def test_a_template_with_no_definition_says_so(monkeypatch):
    monkeypatch.setattr(shapes.gh_contents, "get_file_with_sha", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="New assignment"):
        shapes.read_config("course-org", "assignment-90-x")


def test_the_definition_is_written_back_on_the_solution_branch(monkeypatch):
    # THE reason this write is not `gh_contents.put_file`: that helper has no `branch`,
    # and the Contents API writes to the default branch when nothing says otherwise. The
    # run would then hand out under the form's config while asserting against its own.
    calls = []
    monkeypatch.setattr(
        shapes.ghcli,
        "gh",
        lambda *args, **kw: calls.append((args, kw)) or (0, ""),
    )
    shapes.write_config("course-org", "assignment-90-x", "title: x\n", "sha1")
    args, kw = calls[0]
    assert f"repos/course-org/assignment-90-x/contents/{grades.GRADING_FILE}" in args
    assert f"branch={course.SOLUTION_BRANCH}" in args
    # The sha the text was READ at, so a commit that landed in between is refused rather
    # than silently reverted.
    assert "sha=sha1" in args
    assert base64.b64decode(kw["stdin"]).decode() == "title: x\n"


def test_a_refused_write_is_never_read_as_a_written_one(monkeypatch):
    monkeypatch.setattr(shapes.ghcli, "gh", lambda *a, **k: (1, "409 conflict"))
    with pytest.raises(RuntimeError, match=course.SOLUTION_BRANCH):
        shapes.write_config("course-org", "assignment-90-x", "title: x\n", "sha1")


# ------------------------------------------- the student publishes their own repo


def _tokens(monkeypatch) -> None:
    monkeypatch.setenv(student.HANDLE_ENV, "ada-l")
    monkeypatch.setenv(student.TOKEN_ENV, "ghp_thestudents")
    monkeypatch.setenv("GH_TOKEN", "ghp_themaintainers")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_the_flip_is_made_with_the_students_own_token(monkeypatch):
    # `student_choice` is the shape where what the STUDENT can do is the thing under
    # test. Flipping it with the maintainer's org-owner token would prove the scheduler's
    # half and none of the student's.
    _tokens(monkeypatch)
    seen: dict[str, str] = {}

    def _flip(org, name, visibility, person=False):
        seen.update(
            org=org,
            name=name,
            visibility=visibility,
            gh=os.environ["GH_TOKEN"],
            github=os.environ["GITHUB_TOKEN"],
        )
        return True

    monkeypatch.setattr(student.repos, "set_visibility", _flip)
    assert student.set_visibility("cohort", "assignment-90-x-ada-l", "public")
    assert seen["gh"] == "ghp_thestudents"
    # Both, because `gh` will fall back to the second on a machine that exports it.
    assert seen["github"] == "ghp_thestudents"
    assert (seen["org"], seen["name"], seen["visibility"]) == (
        "cohort",
        "assignment-90-x-ada-l",
        "public",
    )


def test_the_maintainers_token_is_back_afterwards(monkeypatch):
    _tokens(monkeypatch)
    with student.acting():
        assert os.environ["GH_TOKEN"] == "ghp_thestudents"
    # Restored to what it WAS, including the variable that was never set: everything after
    # the block is the maintainer's run again, and a leaked student token would silently
    # downgrade every write that follows.
    assert os.environ["GH_TOKEN"] == "ghp_themaintainers"
    assert "GITHUB_TOKEN" not in os.environ


def test_the_token_goes_back_even_when_the_call_raises(monkeypatch):
    _tokens(monkeypatch)
    with pytest.raises(RuntimeError, match="boom"), student.acting():
        raise RuntimeError("boom")
    assert os.environ["GH_TOKEN"] == "ghp_themaintainers"


def test_a_refused_flip_is_not_read_as_a_successful_one(monkeypatch):
    _tokens(monkeypatch)
    monkeypatch.setattr(student.repos, "set_visibility", lambda *a, **k: False)
    assert not student.set_visibility("cohort", "assignment-90-x-ada-l", "public")


def test_the_flip_goes_through_the_engines_own_writer():
    # Not a hand-rolled PATCH: `repos.set_visibility` is what the provisioner and the
    # scheduler both use, so the harness exercises the same call the toolkit makes.
    assert student.repos is repos


# -------------------------------------------- five assignments, one fenced block


def test_the_run_puts_one_schedule_entry_per_shape_in_one_fence(monkeypatch):
    """The fenced text goes into a file the scheduler parses every fifteen minutes: an
    indentation slip here would not fail the harness, it would fail the cohort. One
    fence for all five, so the teardown takes the whole run out in one edit whatever it
    got as far as adding."""
    module = _pipeline_module(monkeypatch)
    when = datetime(2026, 9, 4, 14, 0)
    due = datetime(2026, 9, 4, 15, 0)
    cutoff = datetime(2026, 9, 4, 16, 0)
    blocks = module._schedule_blocks("e2eab12cd", when, due, cutoff)
    doc = yaml.safe_load(schedule_edit.insert_block(SCHEDULE, "e2eab12cd", blocks))
    assert set(doc) == {"timezone", "assignments", "events"}
    mine = set(doc["assignments"]) - {"assignment-1"}
    assert mine == {shapes.slug("e2eab12cd", s) for s in shapes.SHAPES}
    for slug in mine:
        entry = doc["assignments"][slug]
        assert entry["course_source_repo"] == slug
        assert set(entry) <= schedule.KNOWN_ASSIGNMENT | {"title"}
        # The due date and the cutoff are separate instants: collapsing them would skip
        # the refresh pass entirely, which is most of what the live run is there to
        # exercise.
        assert entry["due_datetime"] != entry["grading_datetime"]
    # And removing the one fence takes all five out again.
    fenced = schedule_edit.insert_block(SCHEDULE, "e2eab12cd", blocks)
    assert schedule_edit.remove_block(fenced, "e2eab12cd") == SCHEDULE


def test_the_pipeline_names_the_shapes_it_asserts_about(monkeypatch):
    module = _pipeline_module(monkeypatch)
    named = {
        module.PRIVATE,
        module.PUBLIC,
        module.CHOICE,
        module.EXTERNAL,
        module.SHARED,
    }
    assert named == set(shapes.SHAPES)


def test_every_reading_is_filed_under_its_shape(monkeypatch):
    module = _pipeline_module(monkeypatch)
    assert module._per_shape(lambda s: s.key) == {s.name: s.key for s in shapes.SHAPES}


def test_each_shapes_feedback_says_which_shape_it_is(monkeypatch):
    # All five land in ONE gradebook README, so "the feedback arrived" is only an answer
    # if each one says whose it is - and no one's may be a substring of another's, or the
    # assertion passes on the wrong section.
    module = _pipeline_module(monkeypatch)
    texts = [module.feedback_for(s) for s in shapes.SHAPES]
    for text in texts:
        assert sum(text in other for other in texts) == 1


def test_every_comment_the_press_left_is_scanned(monkeypatch):
    module = _pipeline_module(monkeypatch)
    stage = module.Stage(
        "distribute", detail={"comments": {"private": ["a", "b"], "public": []}}
    )
    assert module._every_comment(stage) == "a\nb"


def _student_row(handle: str, role: str = "enrolled"):
    return roster.Student(
        hertie_email=f"{handle}@example.org",
        name=handle,
        github_handle=handle,
        github_id="1",
        role=role,
    )


def test_a_cohort_short_of_a_gradebook_is_counted_not_named(monkeypatch):
    # The handout provisions gradebooks now, and a repo it makes in a STUDENT's namespace
    # is drift the teardown cannot sweep - it owns only its own. So the preflight refuses
    # first, and says how many rather than who.
    module = _pipeline_module(monkeypatch)
    students = [_student_row("ada-l"), _student_row("bob-k")]
    assert module.missing_gradebooks(students, {"grades-ada-l", "grades-bob-k"}) == 0
    assert module.missing_gradebooks(students, {"grades-ada-l"}) == 1


def test_an_auditor_needs_no_gradebook(monkeypatch):
    # Auditors are read-only and are never assessed, so `ensure_gradebooks` makes them
    # none - and a preflight that demanded one would refuse every cohort that has one.
    module = _pipeline_module(monkeypatch)
    students = [_student_row("ada-l"), _student_row("zoe-m", role="auditor")]
    assert module.missing_gradebooks(students, {"grades-ada-l"}) == 0


def test_a_student_who_has_not_onboarded_needs_no_gradebook(monkeypatch):
    module = _pipeline_module(monkeypatch)
    students = [_student_row("ada-l"), _student_row("")]
    assert module.missing_gradebooks(students, {"grades-ada-l"}) == 0


# ----------------------------------------------- the walk itself, against a fake estate

# The walk is ~100 lines of code that only ever runs against two real orgs, and a typo in
# it surfaces an hour into a live run with repos already made. So it is driven HERE, over
# stubs, for its control flow: every stage recorded, every shape reached, and - the part a
# 422 would kill the run over - the exact inputs each button is pressed with.

SHEET_TEXT = f"""\
# GRADING SHEET
# Status: OPEN - 0 of 2 students
submissions:
  ada-l:
    info:
    score_individual:
    feedback_individual:
    {grades.NOTES_KEY}:
"""


def _stub_estate(monkeypatch, module) -> list[tuple[str, dict]]:
    """Answer every live call the walk makes. Returns the dispatch log."""
    dispatched: list[tuple[str, dict]] = []

    def content(org, repo, path, ref=""):
        if path.endswith("schedule.yml"):
            return SCHEDULE
        if path.startswith(f"{grades.SHEETS_DIR}/"):
            return SHEET_TEXT
        if path.startswith("_assignments/"):
            return '---\nsubmit_shape: "assignment-repo-private"\n---\n'
        return ""

    monkeypatch.setattr(module.gh_contents, "get_file_content", content)
    monkeypatch.setattr(
        module.gh_contents,
        "get_file_with_sha",
        lambda org, repo, path, ref="": (content(org, repo, path), "sha1"),
    )
    monkeypatch.setattr(module.gh_contents, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(module.schedule_edit, "put_schedule", lambda *a, **k: True)
    monkeypatch.setattr(module.discovery, "list_org_repos", lambda org: [])
    monkeypatch.setattr(module.grades, "find_feedback_issue", lambda org, repo: None)
    monkeypatch.setattr(
        module.repos,
        "direct_collaborators",
        lambda org, repo, **k: frozenset({"ada-l"}),
    )
    monkeypatch.setattr(module.cleanup, "file_bytes", lambda org, repo, path: b"x")
    monkeypatch.setattr(module.ghcli, "gh", lambda *a, **k: (0, "admin"))

    def gh_json(*args):
        url = args[1] if len(args) > 1 else ""
        if "contents/_assignments" in url:
            return [f"01-{shapes.slug(RUN, s)}.md" for s in shapes.SHAPES]
        return []

    monkeypatch.setattr(module.ghcli, "gh_json", gh_json)

    seeded = scaffold._grading_config(
        title="E2E",
        kind="individual",
        team_formation="self_select",
        submit_via="assignment_repo",
        visibility="private",
        formats=["py"],
        autograde=True,
        defaults={},
    )
    monkeypatch.setattr(
        module.shapes, "read_config", lambda org, slug: (seeded, "sha1")
    )
    monkeypatch.setattr(module.shapes, "write_config", lambda *a: None)

    monkeypatch.setattr(module.student, "handle", lambda: "ada-l")
    monkeypatch.setattr(
        module.student, "push_file", lambda repo, dest, path, body, msg: "abc1234"
    )
    monkeypatch.setattr(module.student, "set_visibility", lambda *a: True)

    def dispatch(repo, workflow, inputs, **kwargs):
        dispatched.append((workflow, dict(inputs)))
        return len(dispatched)

    monkeypatch.setattr(module.drive, "dispatch", dispatch)
    monkeypatch.setattr(module.drive, "wait_for_run", lambda *a, **k: "success")
    monkeypatch.setattr(module.drive, "run_log", lambda *a, **k: "")
    monkeypatch.setattr(module.drive, "wait_for_idle", lambda *a, **k: None)
    monkeypatch.setattr(module.drive, "run_ids", lambda *a, **k: set())
    monkeypatch.setattr(module.drive, "wait_for_push_driven_tick", lambda *a, **k: None)
    return dispatched


def test_the_walk_reaches_every_stage_and_every_shape(monkeypatch):
    module = _pipeline_module(monkeypatch)
    _stub_estate(monkeypatch, module)
    stages = module._walk(RUN, {})
    assert set(stages) == {
        "scaffold",
        "configure",
        "schedule",
        "handout",
        "at_handout",
        "submission",
        "published_early",
        "due",
        "refresh",
        "after_due",
        "collect_button",
        "cutoff",
        "published_late",
        "grading",
        "artefacts",
        "marked",
        "shared_before",
        "distribute_dry",
        "distribute",
        "distribute_again",
    }
    names = {s.name for s in shapes.SHAPES}
    assert set(stages["scaffold"].detail["conclusions"]) == names
    assert set(stages["configure"].detail["configs"]) == names
    assert set(stages["marked"].detail["sheets"]) == names
    assert set(stages["at_handout"].detail["pages"]) == names
    # Only the shapes that take a push get one - `external` hands in off GitHub.
    assert set(stages["submission"].detail["pushed"]) == {
        s.name for s in shapes.SHAPES if s.collects_commits
    }


def test_the_walk_presses_new_assignment_once_per_shape_with_its_own_answers(
    monkeypatch,
):
    # A form input this does not know about is a 422 an hour into a live run, and a shape
    # answered with the wrong box is a run that proves the same thing five times.
    module = _pipeline_module(monkeypatch)
    dispatched = _stub_estate(monkeypatch, module)
    module._walk(RUN, {})
    presses = [i for w, i in dispatched if w == module.NEW_ASSIGNMENT]
    assert len(presses) == len(shapes.SHAPES)
    for shape, inputs in zip(shapes.SHAPES, presses, strict=True):
        assert inputs["semester_tag"] == f"{RUN}-{shape.name}"
        assert inputs["submit_via"] == shape.submit_via
        assert inputs["visibility"] == (shape.visibility or "private")
        assert inputs["autograde"] is shape.autograde
        assert inputs["assignment_number"] == cleanup.ASSIGNMENT_NUMBER


def test_the_walk_drives_the_ticks_in_the_order_the_passes_need(monkeypatch):
    # `scheduler.run` freezes what is already past its deadline BEFORE it hands anything
    # out, so the pass that hands out can never be the pass that collects: three ticks,
    # in this order, with a schedule edit before each.
    module = _pipeline_module(monkeypatch)
    dispatched = _stub_estate(monkeypatch, module)
    module._walk(RUN, {})
    workflows = [w for w, _ in dispatched]
    assert workflows.count(module.SCHEDULED_RELEASE) == 3
    assert workflows.count(module.DISTRIBUTE_GRADES) == 3
    assert workflows.count(module.COLLECT_SUBMISSIONS) == 1
    # The collect button is pressed against ONE assignment - the shape whose assertions
    # this harness carried before the others existed.
    pressed = next(i for w, i in dispatched if w == module.COLLECT_SUBMISSIONS)
    assert pressed["course_source_repo"] == shapes.slug(RUN, module.PRIVATE)
    # A live run must never put a real message in a real inbox.
    for workflow, inputs in dispatched:
        if workflow == module.DISTRIBUTE_GRADES:
            assert inputs["silent"] is True
    assert [i["dry_run"] for w, i in dispatched if w == module.DISTRIBUTE_GRADES] == [
        True,
        False,
        False,
    ]


def test_the_student_publishes_before_the_cutoff_and_after_it(monkeypatch):
    # The two halves of the `student_choice` promise, and they have to happen either side
    # of the schedule edit that moves the cutoff into the past - or the run proves neither.
    module = _pipeline_module(monkeypatch)
    order: list[str] = []
    _stub_estate(monkeypatch, module)
    monkeypatch.setattr(
        module.student,
        "set_visibility",
        lambda org, repo, visibility: order.append(f"flip {repo}") or True,
    )
    real_write = module._write_schedule
    monkeypatch.setattr(
        module,
        "_write_schedule",
        lambda run_id, handout, due, cutoff: (
            order.append(f"schedule cutoff={cutoff}")
            or real_write(run_id, handout, due, cutoff)
        ),
    )
    module._walk(RUN, {})
    flips = [i for i, step in enumerate(order) if step.startswith("flip ")]
    edits = [i for i, step in enumerate(order) if step.startswith("schedule ")]
    assert len(flips) == 2 and len(edits) == 3
    # published while the cutoff was still ahead, then again once it had passed
    assert edits[0] < flips[0] < edits[1] < edits[2] < flips[1]
    choice = shapes.slug(RUN, module.CHOICE)
    assert all(order[i] == f"flip {choice}-ada-l" for i in flips)
