"""The course-org faculty-team access policy is single-sourced and applied to every
scaffolded course repo - so a non-owner instructor can push content to a repo they just
scaffolded (previously only `.github` was granted, leaving content repos unwritable).

The same policy covers a cohort org's infra repos (welcome, classroom-config): the cohort
is `default_repository_permission=none`, so before this only org owners could edit the
roster/schedule or triage onboarding issues."""

from __future__ import annotations

import pytest

from dsl_course import bootstrap_course, scaffold, utils


def test_course_team_access_policy():
    assert utils.COURSE_TEAM_ACCESS == {"instructors": "push", "course-admin": "admin"}


def test_button_teams_is_single_sourced():
    # bootstrap's .github grant and the scaffold grant must not drift apart
    assert bootstrap_course.BUTTON_TEAMS is utils.COURSE_TEAM_ACCESS


def test_scaffolds_use_the_shared_grant_helper():
    # the materials/assignment scaffolds grant the faculty teams via this helper
    assert scaffold.grant_course_team_access is utils.grant_course_team_access


def test_faculty_teams_are_only_instructors_and_admin():
    slugs = {t[0] for t in bootstrap_course.FACULTY_TEAMS}
    assert slugs == {"instructors", "course-admin"}
    # students/auditors must NOT be created on the persistent course org (it holds
    # unreleased materials, model solutions, and hidden tests)
    assert "students" not in slugs and "auditors" not in slugs


def test_cohort_teams_are_students_and_auditors():
    assert {t[0] for t in bootstrap_course.COHORT_TEAMS} == {"students", "auditors"}


def test_faculty_and_cohort_team_sets_are_disjoint():
    faculty = {t[0] for t in bootstrap_course.FACULTY_TEAMS}
    cohort = {t[0] for t in bootstrap_course.COHORT_TEAMS}
    assert not (faculty & cohort)


def test_cohort_infra_repos_get_the_faculty_grant():
    # A cohort org is default_repository_permission=none, so a non-owner instructor could
    # not open classroom-config (schedule.yml/students.csv/teams.csv/people.yml + grades/)
    # or triage welcome's needs-review onboarding issues without these.
    assert bootstrap_course.COHORT_FACULTY_REPOS == ["welcome", "classroom-config"]


def test_cohort_faculty_grant_uses_the_shared_policy(monkeypatch):
    granted = []

    def fake_grant(org, team, repo, perm):
        granted.append((org, team, repo, perm))
        return True

    monkeypatch.setattr(bootstrap_course, "grant_team_repo_access", fake_grant)
    bootstrap_course.grant_cohort_faculty_access("Course-f2026")
    assert set(granted) == {
        ("Course-f2026", "instructors", "welcome", "push"),
        ("Course-f2026", "course-admin", "welcome", "admin"),
        ("Course-f2026", "instructors", "classroom-config", "push"),
        ("Course-f2026", "course-admin", "classroom-config", "admin"),
    }


def test_cohort_setup_grants_faculty_access_even_when_nothing_is_seeded(monkeypatch):
    # The grant must sit OUTSIDE the `if create_repo(...)` seeding blocks: "Bootstrap
    # cohort" re-runs this on an existing org, and that re-run is the repair path for a
    # cohort bootstrapped before the grant existed.
    granted = []
    monkeypatch.setattr(bootstrap_course, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(bootstrap_course, "create_cohort_teams", lambda org: None)
    monkeypatch.setattr(bootstrap_course, "create_repo", lambda *a, **k: False)
    monkeypatch.setattr(bootstrap_course, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bootstrap_course.scaffold, "scaffold_site", lambda org: 0)
    monkeypatch.setattr(bootstrap_course, "grant_cohort_faculty_access", granted.append)
    bootstrap_course.setup_cohort_extras("Course-f2026")
    assert granted == ["Course-f2026"]


def test_every_cohort_repo_kind_grants_faculty_at_the_right_level():
    # A cohort org sets default_repository_permission=none, so a team grant is the WHOLE of
    # a non-owner's access. Released content, submission repos and gradebooks each granted
    # only students - so an instructor who was not an org OWNER could not read the material
    # they released, open the work they had to mark, or see the grades they returned. Every
    # live faculty member happens to be an owner, which is why nothing broke.
    #
    # READ on all three. Each has its source of truth elsewhere - the course org's
    # materials repo, and `grades/<slug>.csv` for both marks and submissions - and each is
    # superseded wholesale by the next release / distribute / snapshot, so write would
    # invite an edit that silently vanishes. Write stays where faculty author:
    # classroom-config, welcome/README.md, and .github (which GitHub requires for
    # workflow_dispatch).
    import inspect

    from dsl_course import assign, deploy, grades

    for mod, call, what in (
        (assign, "grant_faculty_read_access", "the snapshot already froze HEAD"),
        (deploy, "grant_faculty_read_access", "a re-release copies over it"),
        (grades, "grant_faculty_read_access", "distribute rewrites grades.yml"),
    ):
        src = inspect.getsource(mod)
        assert f"{call}(cohort_org, repo)" in src, (
            f"{mod.__name__} must grant faculty via {call} - {what}"
        )


def test_the_two_faculty_levels_differ_only_in_the_instructors_grant():
    # course-admin is the cohort's owner of last resort either way: read access cannot fix
    # a broken repo. The distinction is whether an INSTRUCTOR should be editing.
    assert utils.COURSE_TEAM_ACCESS == {"instructors": "push", "course-admin": "admin"}
    assert utils.FACULTY_READ_ACCESS == {"instructors": "pull", "course-admin": "admin"}


def test_the_faculty_sweep_asserts_a_floor_and_never_demotes(monkeypatch):
    # One read per team, then a PUT only where faculty cannot open the repo at all - so a
    # converged org costs two calls a night, not two per repo. A FLOOR, not a level: a repo
    # deliberately granted higher (a submission repo, at push) must survive a sweep whose
    # job is only to guarantee a minimum. That is what lets this run over every repo in an
    # org without having to decide what kind each one is.
    listings = {
        "instructors": "materials\tpull\nassignment-1-ada\tpush\n",
        "course-admin": "materials\tadmin\nassignment-1-ada\tadmin\n",
    }
    granted = []

    def fake_gh(*args, **kwargs):
        for team, out in listings.items():
            if any(f"teams/{team}/repos" in a for a in args):
                return 0, out
        return 0, ""

    monkeypatch.setattr(utils, "gh", fake_gh)
    monkeypatch.setattr(utils, "log", lambda *a, **k: None)
    monkeypatch.setattr(utils, "log_ok", lambda *a, **k: None)
    monkeypatch.setattr(
        utils,
        "grant_team_repo_access",
        lambda org, team, repo, perm: granted.append((team, repo, perm)) or True,
    )
    repos = [
        {"name": "materials"},
        {"name": "assignment-1-ada"},
        {"name": "grades-ada"},
    ]
    changed = utils.converge_faculty_access("Cohort-f2026", repos)
    assert changed == 2  # the gradebook only, for both teams
    assert ("instructors", "grades-ada", "pull") in granted
    assert ("course-admin", "grades-ada", "admin") in granted
    # already at the floor -> no request
    assert not any(r == "materials" for _, r, _ in granted)
    # already ABOVE the floor -> not demoted to pull
    assert not any(r == "assignment-1-ada" for _, r, _ in granted)


def test_an_absent_team_is_skipped_and_an_unreadable_one_raises(monkeypatch):
    # An org can be swept before its teams exist (the next sweep picks it up), but an
    # unreadable team must NOT read as "holds nothing" - that would re-grant every repo in
    # the org on a rate limit.
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert utils.team_repo_access("Org", "instructors") == {}
    monkeypatch.setattr(utils, "gh", lambda *a, **k: (1, "gh: rate limited (HTTP 403)"))
    with pytest.raises(RuntimeError):
        utils.team_repo_access("Org", "instructors")


def test_the_group_assignment_path_also_grants_faculty():
    # The group arm RETURNS inside itself, so a call placed after the group/individual
    # split reached individual assignments only - every team project repo would have gone
    # on granting nobody but the team.
    import inspect

    from dsl_course import assign

    src = inspect.getsource(assign)
    grant = src.index("grant_faculty_read_access(cohort_org, repo)")
    split = src.index("if team is not None:")
    assert grant < split, (
        "the faculty grant must precede the group/individual split, or group repos miss it"
    )
