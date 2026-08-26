"""assign -- the slug transform, and WHO gets a repo. Auditors are read-only: handing one
an assignment repo (and, downstream, a grade) is the failure this guards. Exercised through
the dry-run path, which is pure: it reads a local roster and prints the planned units
without touching gh/git.
"""

from __future__ import annotations

import pytest
import yaml

from dsl_course import assign
from dsl_course.schedule import Schedule

HEADER = "hertie_email,name,github_handle,github_id,enrol_code,role"


def _roster_file(tmp_path, *rows: str):
    path = tmp_path / "students.csv"
    path.write_text("\n".join((HEADER, *rows)) + "\n")
    return str(path)


@pytest.fixture(autouse=True)
def _no_cohort_schedule(monkeypatch):
    """provision_all resolves the cohort-side name from schedule.yml; these tests exercise
    the provisioning mechanics, not the lookup, and must never reach for the network."""
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())


def test_assignment_slug_drops_the_cohort_suffix():
    assert assign.assignment_slug("assignment-1-f2026") == "assignment-1"
    assert assign.assignment_slug("assignment-4-project") == "assignment-4-project"


def test_provisioning_skips_auditors(tmp_path, capsys):
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
        "bob@uni.edu,Bob,bob-b,44,dsl-def,",  # blank role -> enrolled
    )
    rc = assign.provision_all(
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


def test_provisioning_still_works_for_a_roster_without_a_role_column(tmp_path, capsys):
    path = tmp_path / "students.csv"
    path.write_text(
        "student_id,hertie_email,name,github_handle,github_id,section\n"
        "1,ada@uni.edu,Ada,ada-l,42,A\n"
    )
    rc = assign.provision_all(
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
    monkeypatch.setattr(
        "dsl_course.collect.assignment_is_group", lambda org, cohort, template: True
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
    rc = assign.provision_all(
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
    monkeypatch.setattr(
        "dsl_course.collect.assignment_is_group",
        lambda org, cohort, template: (_ for _ in ()).throw(
            AssertionError("must not be read")
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    rc = assign.provision_all(
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
    """An existing repo, so provision_one only exercises the access half."""
    monkeypatch.setattr(assign, "repo_exists", lambda org, repo: True)


def test_a_repo_no_student_can_open_is_a_failed_handout(_provisioned, monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so a repo
    # nobody can see never reached provision_all's exit predicate: the release went green
    # while the student had nothing to submit into.
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
    assert status == "skipped"


# ------------------------------------- group provisioning honours the roster allowlist


def test_group_provisioning_filters_teams_csv_through_the_roster_allowlist(
    tmp_path, capsys, monkeypatch
):
    # teams.csv is student-writable (the welcome "Join team" issue appends rows). A handle
    # not on the roster - a typo, or a stranger's login - must be excluded, never invited
    # into the private org with maintain on a repo. An auditor's handle is excluded too.
    monkeypatch.setattr(
        "dsl_course.collect.assignment_is_group", lambda org, cohort, template: True
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
    rc = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "@ada-l" in captured.out  # the one valid, enrolled, onboarded handle
    assert "stranger-x" not in captured.out  # never provisioned
    assert "eve-e" not in captured.out  # the auditor's handle is not a team member
    assert "stranger-x" in captured.err and "Eve-E" in captured.err  # both warned


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
    rc = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, group=False
    )
    assert captured["key"] == "project"  # the schedule key, not "group-project"
    assert rc == 1  # the site failure was counted, not raised as a traceback
