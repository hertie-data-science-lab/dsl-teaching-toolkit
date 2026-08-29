"""assign -- the slug transform, and WHO gets a repo. Auditors are read-only: handing one
an assignment repo (and, downstream, a grade) is the failure this guards. Exercised through
the dry-run path, which is pure: it reads a local roster and prints the planned units
without touching gh/git.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dsl_course import assign
from dsl_course.schedule import Schedule
from tests.conftest import ROSTER_HEADER

HEADER = ROSTER_HEADER


def _roster_file(tmp_path, *rows: str):
    path = tmp_path / "students.csv"
    path.write_text("\n".join((HEADER, *rows)) + "\n")
    return str(path)


@pytest.fixture(autouse=True)
def _no_cohort_schedule(monkeypatch):
    """provision_all resolves the cohort-side name from schedule.yml; these tests exercise
    the provisioning mechanics, not the lookup, and must never reach for the network."""
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())


@pytest.fixture(autouse=True)
def _empty_cohort_listing(monkeypatch):
    """provision_all takes ONE repo listing of the cohort and answers "does this repo
    exist?" out of it. An empty org is the uninteresting answer for the tests below; the
    ones about the listing itself set their own after this fixture and win."""
    monkeypatch.setattr(assign, "list_org_repos", lambda org: [])


def test_assignment_slug_drops_the_cohort_suffix():
    assert assign.assignment_slug("assignment-1-f2026") == "assignment-1"
    assert assign.assignment_slug("assignment-4-project") == "assignment-4-project"


def test_an_unusable_solution_branch_does_not_block_provisioning(
    tmp_path, monkeypatch, capsys
):
    # A scheduled handout re-runs every tick, so aborting on a bad solution branch meant NO
    # student who onboarded after solution_datetime ever got a repo. The repos must be
    # handed out regardless; an unusable solution only reddens the run.
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    monkeypatch.setattr(assign, "fetch_solution", lambda *a, **k: None)
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    provisioned = []
    monkeypatch.setattr(
        assign,
        "provision_one",
        lambda *a, **k: provisioned.append(a[3]) or "created",
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: None)
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    recorded = []
    monkeypatch.setattr(
        assign, "record_solution_released", lambda *a, **k: recorded.append(a)
    )

    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        solution=True,
        group=False,
    )
    err = capsys.readouterr().err
    assert provisioned == ["assignment-1-ada-l"], (
        "the abort is back - nobody provisioned"
    )
    assert rc == 1, "an unusable solution must still redden the run"
    assert "provisioning continues without it" in err
    assert recorded == [], "a failed solution must not be recorded as released"


def test_a_handout_that_skipped_every_repo_syncs_no_site(tmp_path, monkeypatch):
    # The scheduler re-fires every handed-out release on every hourly tick (that is what
    # gets a late onboarder their repo), so nearly every tick provisions nothing at all.
    # Syncing anyway re-rendered a whole cohort website once an hour for the rest of the
    # term - and the `changed` half of the answer is what lets the SCHEDULER decide the
    # same thing for the tick as a whole.
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: None)
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)
    synced: list[tuple] = []
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a: synced.append(a))

    def run():
        return assign.provision_all(
            "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
        )

    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "skipped")
    assert run() == (0, False)
    assert synced == [], "a pass that changed nothing re-rendered the site"

    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")
    assert run() == (0, True)
    assert synced == [("COURSE", "COHORT")]


def _marker_run(
    tmp_path,
    monkeypatch,
    *,
    status="created",
    units=1,
    site_raises=False,
    record_ok=True,
):
    """provision_all with the network stubbed. Returns (rc, what was recorded)."""
    rows = [f"s{i}@uni.edu,S{i},sh{i},{i},dsl-{i},enrolled" for i in range(units)]
    path = _roster_file(tmp_path, *rows)
    monkeypatch.setattr(assign, "fetch_solution", lambda *a, **k: tmp_path / "sol")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: status)
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: None)
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)

    def sync(*a, **k):
        if site_raises:
            raise RuntimeError("tree read failed")

    monkeypatch.setattr("dsl_course.site.sync_site", sync)
    recorded = []
    monkeypatch.setattr(
        assign,
        "record_solution_released",
        lambda *a, **k: recorded.append(a) or record_ok,
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        solution=True,
        group=False,
    )
    return rc, recorded


def test_the_marker_is_not_written_when_a_solution_push_failed(tmp_path, monkeypatch):
    # The worst failure this feature can have: the marker is fire-once, so recording a
    # release whose pushes failed means the student NEVER receives the solution and no
    # later tick ever retries. provision_one must report the failure, not just log it.
    rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-solution")
    assert recorded == []
    assert rc == 1


def test_the_marker_IS_written_when_the_site_sync_fails(tmp_path, monkeypatch):
    # A site-sync failure says nothing about whether the solution shipped - and it is
    # PERSISTENT (a malformed people.yml raises every run), so withholding the marker for
    # it would re-clone every student repo every hour for the rest of the term.
    rc, recorded = _marker_run(tmp_path, monkeypatch, site_raises=True)
    assert recorded == [("COHORT", "assignment-1", 1)]
    assert rc == 1  # the run still goes red for the site


def test_the_marker_is_not_written_when_a_repo_could_not_be_created(
    tmp_path, monkeypatch
):
    # `failed-create` is returned BEFORE the solution push is even attempted, so the unit
    # never received it - but the marker is fire-once, so writing it here meant that
    # student never got the solution and no later tick retried. Only the failures that
    # happen after a successful push may be written over.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-create")
    assert recorded == []


def test_the_marker_IS_written_when_a_handle_is_dead(tmp_path, monkeypatch):
    # Same reasoning: one unusable student handle is persistent and unrelated to the push.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-no-collaborator")
    assert recorded == [("COHORT", "assignment-1", 1)]


def test_the_marker_is_not_written_when_there_is_nobody_to_push_to(
    tmp_path, monkeypatch
):
    # Nobody onboarded yet -> no repos, so nothing was pushed. Recording it would mean
    # every student who onboards afterwards never receives the solution.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, units=0)
    assert recorded == []


def test_an_unwritten_solution_marker_goes_red(tmp_path, monkeypatch, capsys):
    # The marker is what stops the next tick re-cloning every submission repo to re-push a
    # solution they already have. A write that failed was discarded, so the run went green
    # and the re-clone recurred every hour for the rest of the term.
    rc, recorded = _marker_run(tmp_path, monkeypatch, record_ok=False)
    assert recorded == [("COHORT", "assignment-1", 1)]  # it was attempted
    assert rc == 1
    assert "fire-once record could not be written" in capsys.readouterr().err


def test_a_recorded_solution_release_stays_green(tmp_path, monkeypatch):
    rc, recorded = _marker_run(tmp_path, monkeypatch)
    assert recorded == [("COHORT", "assignment-1", 1)] and rc == 0


def test_a_failed_solution_push_reaches_the_returned_status(tmp_path, monkeypatch):
    # The root of it: provision_one used to log the failure and return "ok" anyway, so
    # provision_all could not tell. Both the group and individual paths must report it.
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    individual = assign.provision_one(
        "C", "t", "COHORT", "r", ["ada"], "assignment-1", sol_dir=tmp_path
    )
    group = assign.provision_one(
        "C", "t", "COHORT", "r", ["ada"], "assignment-1", sol_dir=tmp_path, team="t-a"
    )
    assert individual == "failed-solution"
    assert group == "failed-solution"


def test_provisioning_skips_auditors(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
        "bob@uni.edu,Bob,bob-b,44,dsl-def,",  # blank role -> enrolled
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "assignment-1-ada-l" in out and "assignment-1-bob-b" in out
    assert "eve-e" not in out  # the auditor gets no repo
    assert "1 auditor row(s) skipped" in out
    assert "2 student(s)" in out


def test_provisioning_still_works_for_a_roster_without_a_role_column(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    path = tmp_path / "students.csv"
    path.write_text(
        "student_id,hertie_email,name,github_handle,github_id,section\n"
        "1,ada@uni.edu,Ada,ada-l,42,A\n"
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=str(path),
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "assignment-1-ada-l" in out
    assert "auditor row(s) skipped" not in out


def test_a_dry_run_names_no_student_in_a_public_log(tmp_path, capsys, monkeypatch):
    # The Release assignment workflow runs in the course org's PUBLIC .github, so its log
    # must not publish who is enrolled. The counts a faculty member reads still appear.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "bob@uni.edu,Bob,bob-b,43,dsl-def,enrolled",
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "ada-l" not in out and "bob-b" not in out
    assert "2 student(s)" in out  # the aggregate a faculty member actually reads


def test_not_yet_onboarded_rows_are_still_skipped_separately(tmp_path, capsys):
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "bob@uni.edu,Bob,,,dsl-def,enrolled",  # no handle yet
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
    )
    assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert "1 not-yet-onboarded row(s) skipped" in out
    assert "1 auditor row(s) skipped" in out


def test_group_none_infers_per_team_from_the_templates_grading_yml(
    tmp_path, capsys, monkeypatch
):
    # group=None (the default - scheduler and untick'd button alike) asks the template's
    # own grading.yml: `type: group` provisions per TEAM without anyone force-ticking.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.assignment_is_group", lambda org, cohort, template: True
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "bob-b"], "team-2": ["cid-c"]},
    )
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "bob@uni.edu,Bob,bob-b,43,dsl-def,enrolled",
        "cid@uni.edu,Cid,cid-c,44,dsl-ghi,enrolled",
    )
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "provisioning per team" in out
    assert "assignment-4-project-team-1" in out
    assert "assignment-4-project-team-2" in out
    assert "2 team(s)" in out


def test_group_false_forces_individual_even_for_a_group_template(
    tmp_path, capsys, monkeypatch
):
    # An explicit False never consults grading.yml - the caller decided.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.assignment_is_group",
        lambda org, cohort, template: (_ for _ in ()).throw(
            AssertionError("must not be read")
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-4-project-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    assert rc == 0
    assert "assignment-4-project-ada-l" in capsys.readouterr().out


# ------------------------------------ what counts as a failed handout (the exit code)


@pytest.fixture
def _provisioned(monkeypatch):
    """A repo that creates cleanly, so provision_one exercises the access half. (An
    EXISTING repo with nothing due returns before any access call - see below.)"""
    monkeypatch.setattr(assign, "repo_exists", lambda org, repo: False)
    monkeypatch.setattr(assign, "generate_from_template", lambda **k: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)


def test_a_repo_no_student_can_open_is_a_failed_handout(_provisioned, monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so a repo
    # nobody can see never reached provision_all's exit predicate: the release went green
    # while the student had nothing to submit into.
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: False)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
    )
    assert status.startswith("failed")


def test_a_group_repo_reports_the_teams_own_failures(_provisioned, monkeypatch):
    # ensure_team's result used to be discarded, so a team that couldn't take its members
    # (they see nothing - access is via the team) still reported "ok".
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-wizards",
        ["ada-l", "bob-b"],
        "assignment-1",
        team="assignment-1-wizards",
    )
    assert status.startswith("failed")

    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-wizards",
        ["ada-l", "bob-b"],
        "assignment-1",
        team="assignment-1-wizards",
    )
    assert status == "ok"


# ------------------------------------- group provisioning honours the roster allowlist


def test_group_provisioning_filters_teams_csv_through_the_roster_allowlist(
    tmp_path, capsys, monkeypatch
):
    # teams.csv is student-writable (the welcome "Join team" issue appends rows). A handle
    # not on the roster - a typo, or a stranger's login - must be excluded, never invited
    # into the private org with maintain on a repo. An auditor's handle is excluded too.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.assignment_is_group", lambda org, cohort, template: True
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "stranger-x", "Eve-E"]},
    )
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",  # an auditor, not a team member
    )
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    captured = capsys.readouterr()
    assert rc == 0
    # The DRY-RUN provisioning lines name their members as `@handle`.
    assert "@ada-l" in captured.out  # the one valid, enrolled, onboarded handle
    assert "@stranger-x" not in captured.out  # never provisioned
    assert "@eve-e" not in captured.out  # the auditor's handle is not a team member
    # Both rejections are reported - but the HANDLES a student typed go to the verbose
    # channel (this workflow's log is world-readable) and the actionable error is a count.
    assert "stranger-x" in captured.out and "Eve-E" in captured.out
    assert "2 handle(s) in teams.csv" in captured.err
    assert "stranger-x" not in captured.err and "Eve-E" not in captured.err


def test_a_rejected_teams_csv_handle_is_not_published_in_the_workflow_log(
    tmp_path, capsys, monkeypatch
):
    # No rendered workflow sets DSL_VERBOSE, so this is what a course org's PUBLIC Actions
    # log actually shows: a count a faculty member can act on, and no student's typing.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        "dsl_course.assign.assignment_is_group", lambda org, cohort, template: True
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "stranger-x"]},
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    captured = capsys.readouterr()
    assert "1 handle(s) in teams.csv" in captured.err
    assert "stranger-x" not in captured.err + captured.out


# ------------------------------------------------ half-created cohort template healing


def test_ensure_cohort_template_repairs_a_half_created_template(monkeypatch):
    # A prior run left the repo existing but never set is_template (a _wait_for_content
    # timeout). The exists-path must still verify content and re-PATCH is_template
    # (idempotent), healing it instead of failing every later handout with "not a template".
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(assign, "gh", fake_gh)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        == "assignment-1"
    )
    assert any("PATCH" in a for a in calls) and any(
        "is_template=true" in a for a in calls
    )


def test_ensure_cohort_template_stamps_the_topic_the_site_gates_on(monkeypatch):
    # discovery.discover_handed_out_assignments reads this topic back as the record that
    # the assignment went out, and site._assignment_entry withholds the brief until it
    # does - so dropping the stamp silently blanks every brief on every cohort site.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    stamped: list[tuple] = []
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: stamped.append(a) or True)
    assign.ensure_cohort_template(
        "COURSE", "assignment-1-f2026", "COHORT", "homework-1"
    )
    # stamped on the COHORT-side repo, under the name the site looks it up by
    assert stamped == [("COHORT", "homework-1", ["homework-1", "assignment-template"])]


def test_ensure_cohort_template_says_what_a_failed_topic_stamp_costs(monkeypatch):
    # The hand-out itself succeeded, so this must not fail the run - but a silent drop
    # leaves the site withholding a brief the students already hold.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: False)
    errs: list[str] = []
    monkeypatch.setattr(assign, "log_err", errs.append)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        == "assignment-1"
    )
    assert "assignment-template" in errs[0] and "withheld" in errs[0]


def test_ensure_cohort_template_fails_loudly_when_is_template_patch_fails(monkeypatch):
    # The is_template PATCH result was discarded; now a failed PATCH returns None so the run
    # goes red rather than fanning out from a repo that isn't actually a template.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (1, "403 Forbidden"))
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        is None
    )


# ----------------------------------- handout recorded under the schedule key + site guard


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("tree read failed"),
        # A config file with one bad indent raises yaml.YAMLError, which is NOT a
        # RuntimeError - it used to walk through the guard and misreport the handout.
        yaml.parser.ParserError(None, None, "bad indent", None),
    ],
)
def test_provision_all_records_handout_under_schedule_key_and_survives_site_failure(
    tmp_path, monkeypatch, exc
):
    # record_handout keys on the schedule KEY; with a cohort_dest_repo set, the cohort-side
    # name differs, and passing the name appended a bogus block. And site.sync_site now RAISES
    # on a genuine read failure - one such failure must be logged + counted, never a traceback
    # that misreports the whole handout.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dsl_course.schedule import AssignmentEntry, Schedule

    entry = AssignmentEntry(
        course_source_repo="assignment-4-project-f2026",
        cohort_dest_repo="group-project",
        due_datetime=datetime(2026, 11, 15, tzinfo=ZoneInfo("Europe/Berlin")),
    )
    monkeypatch.setattr(
        "dsl_course.schedule.load",
        lambda org: Schedule(assignments={"project": entry}),
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "dsl_course.schedule.record_handout",
        lambda org, slug, *a: captured.__setitem__("key", slug),
    )
    monkeypatch.setattr(assign, "ensure_cohort_template", lambda *a: "group-project")
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")

    from dsl_course import site

    def boom_site(*a, **k):
        raise exc

    monkeypatch.setattr(site, "sync_site", boom_site)
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, group=False
    )
    assert captured["key"] == "project"  # the schedule key, not "group-project"
    assert rc == 1  # the site failure was counted, not raised as a traceback


def test_both_assignment_arms_grant_faculty_read(_provisioned, monkeypatch):
    # A cohort org is default_repository_permission=none, so a team grant is the WHOLE of
    # a non-owner instructor's access - and submission repos granted only the student. The
    # group arm RETURNS inside itself, so the grant must sit before the split or every team
    # project repo would go on granting nobody but the team. READ, not write: marking
    # happens in classroom-config/grades/<slug>.csv, after the snapshot froze HEAD.
    faculty = []
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: faculty.append(a))
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    assign.provision_one("COURSE", "a1", "COHORT", "a1-ada-l", ["ada-l"], "a1")
    assign.provision_one(
        "COURSE",
        "a1",
        "COHORT",
        "a1-wizards",
        ["ada-l", "bob-b"],
        "a1",
        team="a1-wizards",
    )
    read = assign.FACULTY_READ_ACCESS
    assert faculty == [("COHORT", "a1-ada-l", read), ("COHORT", "a1-wizards", read)]


def test_the_scheduler_leaves_an_existing_repo_alone_but_the_button_repairs_it(
    monkeypatch,
):
    # The scheduler re-runs every handed-out release hourly. Re-granting access to an
    # existing repo on every tick cost 2-4 API calls per student per assignment for the
    # rest of term. The hourly path (touch_existing=False) skips it; the manual Release
    # assignment button keeps re-granting the STUDENT, so re-running it still repairs a
    # student's access. The faculty grant is not re-run on either path - the nightly sweep
    # owns that floor. With a solution to push, the push happens either way.
    calls = []
    monkeypatch.setattr(assign, "repo_exists", lambda org, repo: True)
    for name in (
        "add_collaborator",
        "grant_team_repo_access",
        "grant_faculty",
    ):
        monkeypatch.setattr(
            assign, name, lambda *a, _n=name, **k: calls.append(_n) or True
        )
    monkeypatch.setattr(
        assign.sync_teams, "ensure_team", lambda *a, **k: calls.append("team") or True
    )
    hourly = {"touch_existing": False}
    assert assign.provision_one("C", "t", "K", "a1-ada", ["ada"], "a1", **hourly) == (
        "skipped"
    )
    assert assign.provision_one(
        "C", "t", "K", "a1-w", ["ada"], "a1", team="a1-w", **hourly
    ) == ("skipped")
    assert calls == []
    # the button (default) re-grants the student, and only the student
    assert assign.provision_one("C", "t", "K", "a1-ada", ["ada"], "a1") == "skipped"
    assert calls == ["add_collaborator"]
    pushed = []
    monkeypatch.setattr(assign, "push_solution", lambda *a: pushed.append(a) or True)
    assert assign.provision_one(
        "C", "t", "K", "a1-ada", ["ada"], "a1", sol_dir=Path("s"), **hourly
    ) == ("skipped")
    assert len(pushed) == 1


# ------------- teams.csv is keyed on the SCHEDULE KEY, repos on the cohort-side name


def _scheduled(monkeypatch, key: str, dest: str, source: str):
    """A cohort schedule with ONE assignment whose cohort-side name differs from its key."""
    from datetime import datetime, timezone

    from dsl_course.schedule import AssignmentEntry

    entry = AssignmentEntry(
        due_datetime=datetime(2026, 11, 1, tzinfo=timezone.utc),
        course_source_repo=source,
        cohort_dest_repo=dest,
        type="group",
    )
    monkeypatch.setattr(
        "dsl_course.schedule.load", lambda org: Schedule(assignments={key: entry})
    )


def test_group_handout_looks_teams_up_by_key_and_names_repos_by_dest(
    tmp_path, capsys, monkeypatch
):
    # With `cohort_dest_repo` set the two names diverge. teams.csv is keyed on the SCHEDULE
    # KEY (the Join-team form validates the slug against `assignments:` and writes it), so
    # looking teams up by the cohort-side name found none at all - the handout failed with
    # "no teams" while the CSV was full.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    _scheduled(monkeypatch, "regression", "wk3-regression", "wk3-regression-f2026")
    asked: list[str] = []
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: (
            asked.append(slug)
            or ({"team-1": ["ada-l"]} if slug == "regression" else {})
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    rc, _changed = assign.provision_all(
        "COURSE", "wk3-regression-f2026", "COHORT", roster_path=path, dry_run=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert asked == ["regression"]  # keyed on the schedule key, not the dest repo
    assert "COHORT/wk3-regression-team-1" in out  # the repo keeps the cohort-side name


def test_the_granted_team_slug_matches_the_one_sync_teams_reconciles(
    tmp_path, monkeypatch
):
    # sync_teams.desired_teams derives its slug from the teams.csv key, so a handout that
    # derived its own from the cohort-side name granted `wk3-regression-team-1` while Sync
    # membership kept reconciling `regression-team-1`: two teams, and the members were in
    # the one with no repo.
    from dsl_course import sync_teams

    _scheduled(monkeypatch, "regression", "wk3-regression", "wk3-regression-f2026")
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams, "teams_for", lambda rows, slug: {"team-1": ["ada-l"]}
    )
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "wk3-regression"
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    granted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        assign,
        "provision_one",
        lambda *a, **k: granted.append((a[3], k["team"])) or "ok",
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    assign.provision_all("COURSE", "wk3-regression-f2026", "COHORT", roster_path=path)
    assert granted == [("wk3-regression-team-1", "regression-team-1")]
    # ... which is exactly what the membership sync materialises from the same CSV.
    assert sync_teams.desired_teams({"regression": {"team-1": ["ada-l"]}}) == {
        "regression-team-1": {"ada-l"}
    }


def test_a_group_handout_with_no_teams_yet_waits_on_the_cron_and_fails_on_the_button(
    tmp_path, monkeypatch, capsys
):
    # Teams form when students click 'Join team', days after the handout datetime. The
    # hourly cron counted the empty CSV as a failure and went red every tick until the
    # first team formed; an operator pressing the button still needs to be told.
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {})
    monkeypatch.setattr(assign.teams, "teams_for", lambda rows, slug: {})

    def boom(*a, **k):
        raise AssertionError("nothing may be provisioned without a team")

    monkeypatch.setattr(assign, "ensure_cohort_template", boom)
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")

    def run(**kw):
        return assign.provision_all(
            "COURSE", "project-f2026", "COHORT", roster_path=path, group=True, **kw
        )

    assert run(scheduled=True) == (0, False)
    assert "[wait] no teams" in capsys.readouterr().out
    assert run() == (1, False)
    assert "no teams for" in capsys.readouterr().err


# --------------- a failed solution push outranks every other fault (the marker depends on it)


@pytest.mark.parametrize(
    "broken",
    ["grant_team_repo_access", "add_collaborator"],
)
def test_a_failed_solution_wins_over_a_failed_access_grant(
    tmp_path, monkeypatch, broken
):
    # provision_all writes the FIRE-ONCE solution marker off these statuses, so a repo that
    # reported `failed-no-access` / `failed-no-collaborator` had its missing solution
    # forgotten - and the marker guaranteed no later tick would ever retry it.
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    monkeypatch.setattr(assign, broken, lambda *a, **k: False)
    team = "t-a" if broken == "grant_team_repo_access" else None
    status = assign.provision_one(
        "C", "t", "COHORT", "r", ["ada"], "assignment-1", sol_dir=tmp_path, team=team
    )
    assert status == "failed-solution"


def test_a_failed_solution_wins_over_a_team_missing_members(tmp_path, monkeypatch):
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    status = assign.provision_one(
        "C", "t", "COHORT", "r", ["ada"], "assignment-1", sol_dir=tmp_path, team="t-a"
    )
    assert status == "failed-solution"


def test_a_group_whose_members_were_all_rejected_is_a_failed_unit(monkeypatch, capsys):
    # Every handle in the teams.csv row failed the roster allowlist, so the team is empty
    # and the repo is granted to nobody. Reported "ok", that left a repo no student could
    # open looking like a successful handout.
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: False)
    monkeypatch.setattr(assign, "generate_from_template", lambda **k: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    status = assign.provision_one(
        "C", "t", "COHORT", "a1-team-1", [], "assignment-1", team="assignment-1-team-1"
    )
    assert status == "failed-no-members"
    assert "nobody can open a1-team-1" in capsys.readouterr().err


# ------------------------------------------- ONE listing instead of a probe per repo


def _ready_template(name="assignment-1"):
    return {"name": name, "isTemplate": True, "topics": [name, "assignment-template"]}


def _listing_run(tmp_path, monkeypatch, listing):
    """provision_all over two students, with `listing` standing in for the org listing.
    Returns (the orgs listed, the repos generate_from_template was asked to create)."""
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "bob@uni.edu,Bob,bob-b,43,dsl-def,enrolled",
    )
    listed: list[str] = []

    def fake_listing(org):
        listed.append(org)
        if isinstance(listing, Exception):
            raise listing
        return listing

    created: list[str] = []
    monkeypatch.setattr(assign, "list_org_repos", fake_listing)
    monkeypatch.setattr(
        assign, "generate_from_template", lambda **k: created.append(k["name"]) or True
    )
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )
    return listed, created


def test_provision_all_lists_the_org_once_and_probes_no_repo(tmp_path, monkeypatch):
    # A `repo_exists` per unit cost a GET per student per assignment on EVERY hourly tick
    # (~1,200 an hour for a large cohort) where one paginated listing costs three.
    monkeypatch.setattr(
        assign,
        "repo_exists",
        lambda *a, **k: pytest.fail("a per-repo probe is back in the hot path"),
    )
    listed, created = _listing_run(
        tmp_path,
        monkeypatch,
        [_ready_template(), {"name": "assignment-1-ada-l", "topics": []}],
    )
    assert listed == ["COHORT"], "one listing per run, not one per repo"
    assert created == ["assignment-1-bob-b"], "a listed repo was regenerated"


def test_a_failed_listing_falls_back_to_probing_each_repo(tmp_path, monkeypatch):
    # The listing is an optimisation. A rate limit on it must not stop the students who
    # onboarded this hour from getting their repos.
    probed: list[str] = []
    monkeypatch.setattr(
        assign, "repo_exists", lambda org, name: probed.append(name) or False
    )
    listed, created = _listing_run(
        tmp_path, monkeypatch, RuntimeError("could not list repos in COHORT: 502")
    )
    assert listed == ["COHORT"]
    assert created == ["assignment-1", "assignment-1-ada-l", "assignment-1-bob-b"]
    assert probed == ["assignment-1", "assignment-1-ada-l", "assignment-1-bob-b"]


def test_a_cohort_template_the_listing_shows_ready_is_left_alone(monkeypatch):
    # The repair is three writes per handed-out assignment; running it on a template the
    # listing already shows as frozen, flagged and topiced re-wrote correct state hourly.
    monkeypatch.setattr(
        assign,
        "_wait_for_content",
        lambda *a: pytest.fail("the hourly re-probe is back"),
    )
    monkeypatch.setattr(
        assign,
        "gh",
        lambda *a, **k: pytest.fail("the hourly is_template PATCH is back"),
    )
    monkeypatch.setattr(
        assign,
        "set_repo_topics",
        lambda *a: pytest.fail("the hourly topics PUT is back"),
    )
    assert (
        assign.ensure_cohort_template(
            "COURSE",
            "assignment-1-f2026",
            "COHORT",
            "assignment-1",
            [_ready_template()],
        )
        == "assignment-1"
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "assignment-1", "isTemplate": False, "topics": ["assignment-1"]},
        {"name": "assignment-1", "isTemplate": True, "topics": []},
    ],
)
def test_a_half_created_cohort_template_is_still_repaired_from_the_listing(
    monkeypatch, entry
):
    # A run that timed out in _wait_for_content leaves the repo existing but unflagged or
    # untopiced. The listing must not read that as "ready" - every later handout would
    # fail with a misleading "not a template", or the site would withhold the brief.
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    patched: list[tuple] = []
    monkeypatch.setattr(assign, "gh", lambda *a, **k: patched.append(a) or (0, ""))
    stamped: list[tuple] = []
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: stamped.append(a) or True)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1", [entry]
        )
        == "assignment-1"
    )
    assert any("is_template=true" in a for a in patched)
    assert stamped == [
        ("COHORT", "assignment-1", ["assignment-1", "assignment-template"])
    ]


def test_wait_for_content_does_not_sleep_after_its_last_poll(monkeypatch):
    # The delay is there to space the polls out. Sleeping after the final failed one only
    # adds `delay` to a wait that has already given up.
    slept: list[float] = []
    monkeypatch.setattr(assign.time, "sleep", lambda d: slept.append(d))
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, "0"))
    assert assign._wait_for_content("COHORT", "a1", attempts=3, delay=1.5) is False
    assert slept == [1.5, 1.5]
