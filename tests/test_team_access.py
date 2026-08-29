"""The course-org faculty-team access policy is single-sourced and applied to every
scaffolded course repo - so a non-owner instructor can push content to a repo they just
scaffolded (previously only `.github` was granted, leaving content repos unwritable).

The same policy covers a cohort org's infra repos (welcome, classroom-config): the cohort
is `default_repository_permission=none`, so before this only org owners could edit the
roster/schedule or triage onboarding issues."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsl_course import access, bootstrap_course, gh_contents, scaffold


def test_course_team_access_policy():
    assert access.COURSE_TEAM_ACCESS == {"instructors": "push", "course-admin": "admin"}


def test_button_teams_is_single_sourced():
    # bootstrap's .github grant and the scaffold grant must not drift apart
    assert bootstrap_course.BUTTON_TEAMS is access.COURSE_TEAM_ACCESS


@pytest.fixture
def scaffold_grants(monkeypatch):
    """Run a scaffold with everything but the ACCESS GRANTS stubbed out; returns the
    `(team, repo, permission)` grants it actually issued."""
    granted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        access,
        "grant_team_repo_access",
        lambda org, team, repo, perm, **k: granted.append((team, repo, perm)) or True,
    )
    monkeypatch.setattr(access, "create_team", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "set_repo_topics", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "refresh_stubs", lambda *a, **k: 0)
    monkeypatch.setattr(scaffold, "put_files", lambda *a, **k: True)
    monkeypatch.setattr(gh_contents, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(gh_contents, "put_files", lambda *a, **k: True)
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "seed_files_if_absent", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "seed_if_absent", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(scaffold, "discover_assignments", lambda org: [])
    monkeypatch.setattr(scaffold, "push_content_workflows", lambda *a, **k: 0)

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            Path(args[3]).mkdir(parents=True, exist_ok=True)
        return 0, ""

    monkeypatch.setattr(scaffold, "gh", fake_gh)
    monkeypatch.setattr(scaffold, "git", lambda *a, **k: (0, ""))
    return granted


def test_a_scaffolded_materials_repo_is_granted_to_the_faculty_teams(scaffold_grants):
    # A non-owner instructor has to be able to push to a repo they just scaffolded. The
    # grant is asserted by DRIVING the scaffold, not by comparing an import binding: the
    # scaffold could grant the wrong repo, or skip the call, and the binding would match.
    scaffold.scaffold_materials("Org", "f2026")
    repo = "course-materials-f2026"
    for team, perm in access.COURSE_TEAM_ACCESS.items():
        assert (team, repo, perm) in scaffold_grants
    # ...and the cohort-declared instructors team for that tag, scoped to its own content.
    assert ("instructors-f2026", repo, "push") in scaffold_grants


def test_a_scaffolded_assignment_repo_is_granted_to_the_faculty_teams(scaffold_grants):
    scaffold.scaffold_assignment("Org", "1", "f2026")
    repo = "assignment-1-f2026"
    for team, perm in access.COURSE_TEAM_ACCESS.items():
        assert (team, repo, perm) in scaffold_grants
    assert ("instructors-f2026", repo, "push") in scaffold_grants


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
    assert set(bootstrap_course.COHORT_FACULTY_REPOS) == {"welcome", "classroom-config"}
    # ...and single-sourced with the nightly sweep's write floor, so a repo cannot be
    # granted push at bootstrap and then read by the sweep (or the reverse).
    assert set(bootstrap_course.COHORT_FACULTY_REPOS) | {".github"} == set(
        access.COHORT_WRITE_REPOS
    )


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
    monkeypatch.setattr(bootstrap_course, "create_cohort_teams", lambda org: 0)
    monkeypatch.setattr(bootstrap_course, "create_repo", lambda *a, **k: False)
    monkeypatch.setattr(bootstrap_course, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bootstrap_course.scaffold, "scaffold_site", lambda org: 0)
    monkeypatch.setattr(bootstrap_course, "grant_cohort_faculty_access", granted.append)
    bootstrap_course.setup_cohort_extras("Course-f2026", "release")
    assert granted == ["Course-f2026"]


def test_the_two_faculty_levels_differ_only_in_the_instructors_grant():
    # course-admin is the cohort's owner of last resort either way: read access cannot fix
    # a broken repo. The distinction is whether an INSTRUCTOR should be editing.
    assert access.COURSE_TEAM_ACCESS == {"instructors": "push", "course-admin": "admin"}
    assert access.FACULTY_READ_ACCESS == {
        "instructors": "pull",
        "course-admin": "admin",
    }
    assert access.COURSE_TEAM_ACCESS.keys() == access.FACULTY_READ_ACCESS.keys()


def test_the_floor_is_write_where_faculty_author_and_read_elsewhere():
    # Every repo of a COURSE org is faculty-authored staging. In a COHORT org only the three
    # repos faculty edit (and `.github`, which workflow_dispatch needs write on) get write;
    # released content, submission repos and gradebooks each have their source of truth
    # elsewhere and are overwritten wholesale, so write there invites an edit that vanishes.
    for repo in (".github", "course-materials-f2026", "assignment-1"):
        assert access.faculty_floor(repo, cohort=False) is access.COURSE_TEAM_ACCESS
    for repo in access.COHORT_WRITE_REPOS:
        assert access.faculty_floor(repo, cohort=True) is access.COURSE_TEAM_ACCESS
    for repo in ("materials", "assignment-1-ada", "grades-ada", "x.github.io"):
        assert access.faculty_floor(repo, cohort=True) is access.FACULTY_READ_ACCESS


# The live shape of one row of GET /orgs/{org}/teams/{team}/repos. `role_name` is in the
# GET vocabulary (read/write), which is NOT what a PUT takes (pull/push) - the sweep must
# never rank it. The `permissions` object is cumulative and its keys ARE the PUT vocabulary.
def _row(name: str, role: str, **flags: bool) -> str:
    base = {
        "admin": False,
        "maintain": False,
        "pull": False,
        "push": False,
        "triage": False,
    }
    base.update(flags)
    return json.dumps({"name": name, "role_name": role, "permissions": base})


def _listing(*rows: str) -> str:
    return "\n".join(rows) + "\n"


def _sweep(
    monkeypatch, listings: dict[str, str], repos, cohort: bool, protected=frozenset()
):
    granted = []

    def fake_gh(*args, **kwargs):
        for team, out in listings.items():
            if any(f"teams/{team}/repos" in a for a in args):
                return 0, out
        return 1, "gh: Not Found (HTTP 404)"

    monkeypatch.setattr(access, "gh", fake_gh)
    monkeypatch.setattr(access, "log", lambda *a, **k: None)
    monkeypatch.setattr(access, "log_ok", lambda *a, **k: None)
    monkeypatch.setattr(
        access,
        "grant_team_repo_access",
        lambda org, team, repo, perm: granted.append((team, repo, perm)) or True,
    )
    changed = access.converge_faculty_access(
        "Org", repos, cohort=cohort, protected=protected
    )
    return changed, granted


def test_the_sweep_reads_the_permission_booleans_and_never_demotes_write(monkeypatch):
    # THE regression. `role_name=write` ranked in the PUT table was 0 < pull, so the sweep
    # PUT pull on every repo an instructor could write - killing every faculty button for
    # exactly the non-owner instructor the sweep exists for, and re-firing nightly after
    # any manual repair. The booleans say push, and push is above the read floor.
    listings = {
        "instructors": _listing(
            _row(".github", "write", pull=True, triage=True, push=True),
            _row("classroom-config", "write", pull=True, triage=True, push=True),
            _row("materials", "read", pull=True),
        ),
        "course-admin": _listing(
            _row(
                ".github",
                "admin",
                pull=True,
                triage=True,
                push=True,
                maintain=True,
                admin=True,
            ),
            _row(
                "classroom-config",
                "admin",
                pull=True,
                triage=True,
                push=True,
                maintain=True,
                admin=True,
            ),
            _row(
                "materials",
                "admin",
                pull=True,
                triage=True,
                push=True,
                maintain=True,
                admin=True,
            ),
        ),
    }
    repos = [{"name": n} for n in (".github", "classroom-config", "materials")]
    changed, granted = _sweep(monkeypatch, listings, repos, cohort=True)
    assert (changed, granted) == (0, [])


def test_the_sweep_grants_the_per_repo_floor_where_a_team_holds_nothing(monkeypatch):
    # Nothing granted anywhere: a cohort's write repos converge at push, the rest at pull,
    # course-admin at admin throughout. A course org converges at push everywhere.
    listings = {"instructors": _listing(), "course-admin": _listing()}
    repos = [{"name": n} for n in ("welcome", "assignment-1-ada", "grades-ada")]
    changed, granted = _sweep(monkeypatch, listings, repos, cohort=True)
    assert changed == 6
    assert set(granted) == {
        ("instructors", "welcome", "push"),
        ("instructors", "assignment-1-ada", "pull"),
        ("instructors", "grades-ada", "pull"),
        ("course-admin", "welcome", "admin"),
        ("course-admin", "assignment-1-ada", "admin"),
        ("course-admin", "grades-ada", "admin"),
    }
    repos = [{"name": n} for n in ("course-materials-f2026", "assignment-1")]
    _, granted = _sweep(monkeypatch, listings, repos, cohort=False)
    assert {(t, p) for t, _, p in granted} == {
        ("instructors", "push"),
        ("course-admin", "admin"),
    }


def _admin_row(name: str) -> str:
    return _row(
        name, "admin", pull=True, triage=True, push=True, maintain=True, admin=True
    )


def test_the_sweep_leaves_a_maintain_or_triage_grant_exactly_as_it_is(monkeypatch):
    # The two levels between the ones the floors name. A `maintain` grant answers the
    # listing with FOUR true flags (admin false), and a `triage` grant with two - so a
    # ranking that read the wrong one, or compared the wrong way round, would nightly
    # re-PUT an instructor down to the floor: `maintain` -> push on the repo faculty
    # trigger every button from, `triage` -> pull on a gradebook. Both are above their
    # floor and both must cost no call at all.
    listings = {
        "instructors": _listing(
            _row(
                ".github", "maintain", pull=True, triage=True, push=True, maintain=True
            ),
            _row("grades-ada", "triage", pull=True, triage=True),
        ),
        "course-admin": _listing(_admin_row(".github"), _admin_row("grades-ada")),
    }
    repos = [{"name": n} for n in (".github", "grades-ada")]
    assert _sweep(monkeypatch, listings, repos, cohort=True) == (0, [])


def test_the_sweep_raises_a_grant_below_its_floor_but_leaves_one_above(monkeypatch):
    # A floor, not a level: `.github` held at read is raised to push (its floor is write);
    # a submission repo held at push is left alone (its floor is read).
    listings = {
        "instructors": _listing(
            _row(".github", "read", pull=True),
            _row("assignment-1-ada", "write", pull=True, triage=True, push=True),
        ),
        "course-admin": _listing(
            _row(
                ".github",
                "admin",
                pull=True,
                triage=True,
                push=True,
                maintain=True,
                admin=True,
            ),
            _row(
                "assignment-1-ada",
                "admin",
                pull=True,
                triage=True,
                push=True,
                maintain=True,
                admin=True,
            ),
        ),
    }
    repos = [{"name": ".github"}, {"name": "assignment-1-ada"}]
    changed, granted = _sweep(monkeypatch, listings, repos, cohort=True)
    assert (changed, granted) == (1, [("instructors", ".github", "push")])


def test_the_sweep_fails_closed_on_a_grant_it_cannot_rank(monkeypatch):
    # `_PERM_RANK.get(x, 0)` was the defect class: anything unrecognised read as "below
    # read" and got overwritten. A permissions object setting no flag we rank must be
    # SKIPPED, never treated as nothing.
    listings = {
        "instructors": _listing(
            json.dumps({"name": "odd", "permissions": {"custom_role": True}})
        ),
        "course-admin": _listing(json.dumps({"name": "odd", "permissions": {}})),
    }
    changed, granted = _sweep(monkeypatch, listings, [{"name": "odd"}], cohort=True)
    assert (changed, granted) == (0, [])


def test_the_sweep_skips_archived_repos(monkeypatch):
    # GitHub refuses a PUT on an archived repo, so a frozen cohort (every repo archived)
    # would otherwise fail 2 writes per repo every night, forever.
    listings = {"instructors": _listing(), "course-admin": _listing()}
    repos = [{"name": "old", "archived": True}, {"name": "live", "archived": False}]
    _, granted = _sweep(monkeypatch, listings, repos, cohort=True)
    assert {r for _, r, _ in granted} == {"live"}


def test_an_absent_team_is_none_and_an_unreadable_one_raises(monkeypatch):
    # An org can be swept before its teams exist (the next sweep picks it up) - but the
    # absence must be None, not {}: {} means "holds nothing", and the sweep would PUT on
    # every repo in the org and 404 on each. An unreadable team must not read as either.
    monkeypatch.setattr(access, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert access.team_repo_access("Org", "instructors") is None
    monkeypatch.setattr(
        access, "gh", lambda *a, **k: (1, "gh: rate limited (HTTP 403)")
    )
    with pytest.raises(RuntimeError):
        access.team_repo_access("Org", "instructors")
    monkeypatch.setattr(access, "gh", lambda *a, **k: (0, "not json"))
    with pytest.raises(RuntimeError):
        access.team_repo_access("Org", "instructors")


def test_an_absent_team_stops_the_sweep_for_that_team_only(monkeypatch):
    listings = {"course-admin": _listing()}  # instructors -> 404 from the fake
    _, granted = _sweep(monkeypatch, listings, [{"name": "welcome"}], cohort=True)
    assert granted == [("course-admin", "welcome", "admin")]


def test_the_listing_is_paginated_in_pages_of_100(monkeypatch):
    # Every other paginated read here asks for 100 a page; a cohort org holds a repo per
    # student per assignment plus a gradebook each.
    seen = []
    monkeypatch.setattr(access, "gh", lambda *a, **k: seen.append(a) or (0, ""))
    assert access.team_repo_access("Org", "instructors") == {}
    assert "--paginate" in seen[0]
    assert "orgs/Org/teams/instructors/repos?per_page=100" in seen[0]


def test_a_protected_repo_takes_the_read_floor_whatever_the_tier_says(monkeypatch):
    # The tier is a heuristic over a listing; the protected set is the backstop. Even when
    # the sweep is told "course" (push everywhere), a student's submission repo or
    # gradebook is never granted push.
    listings = {"instructors": _listing(), "course-admin": _listing()}
    repos = [
        {"name": n} for n in ("assignment-1-ada", "grades-ada", "course-materials")
    ]
    _, granted = _sweep(
        monkeypatch,
        listings,
        repos,
        cohort=False,
        protected=frozenset({"assignment-1-ada", "grades-ada"}),
    )
    assert ("instructors", "assignment-1-ada", "pull") in granted
    assert ("instructors", "grades-ada", "pull") in granted
    assert ("instructors", "course-materials", "push") in granted
    assert not any(p == "push" and r != "course-materials" for _, r, p in granted)


def test_a_missing_team_is_a_note_but_any_other_failure_is_an_error(
    monkeypatch, capsys
):
    # grant_read_teams used to print "team not found" for EVERY failure, so a 5xx or a
    # rate limit read as a cohort that had not made its teams yet.
    monkeypatch.setattr(access, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert not access.grant_team_repo_access(
        "O", "students", "r", "pull", missing_is_note=True
    )
    out = capsys.readouterr()
    assert "not found" in out.out and out.err == ""
    monkeypatch.setattr(access, "gh", lambda *a, **k: (1, "HTTP 502 bad gateway"))
    assert not access.grant_team_repo_access(
        "O", "students", "r", "pull", missing_is_note=True
    )
    assert "could not grant" in capsys.readouterr().err


# ------------------------------------------------------------ converge_topics


def _topic_repos():
    return [
        {"name": ".github", "topics": ["dsl-cohort"]},
        {"name": "assignment-1", "topics": ["assignment-template"], "isTemplate": True},
        {"name": "assignment-1-ada", "topics": []},  # stamp never landed
        {"name": "assignment-1-bob", "topics": ["assignment-1", "submission"]},
        {"name": "grades-ada", "topics": []},
        {"name": "grades-bob", "topics": ["gradebook"]},
        {"name": "welcome", "topics": []},
    ]


def _converge(monkeypatch, repos, ok=True):
    stamped: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        access,
        "set_repo_topics",
        lambda org, repo, topics: stamped.append((repo, topics)) or ok,
    )
    failures = access.converge_topics("Cohort-f2026", repos, cohort=True)
    return failures, dict(stamped)


def test_only_the_repos_missing_a_topic_are_patched(monkeypatch):
    # The stamp is a separate PATCH after the create, so a repo whose stamp failed stayed
    # untagged forever - and the topics are what keep a submission repo and a private
    # gradebook off the org landing page and on the faculty READ floor.
    failures, stamped = _converge(monkeypatch, _topic_repos())
    assert failures == 0
    assert stamped == {
        "assignment-1-ada": ["assignment-1", "submission"],
        "grades-ada": ["gradebook"],
    }


def test_converging_topics_never_removes_one(monkeypatch):
    repos = [
        {"name": "assignment-1", "isTemplate": True, "topics": []},
        {"name": "assignment-1-ada", "topics": ["group-project"]},
    ]
    _, stamped = _converge(monkeypatch, repos)
    assert stamped["assignment-1-ada"] == [
        "assignment-1",
        "group-project",
        "submission",
    ]


def test_a_nested_template_stamps_the_longest_match_and_skips_the_templates(
    monkeypatch,
):
    # One classifier (discovery.classify_repos) for the sweep, the off-boarding revoke and
    # the landing page. Keyed first-alphabetically, this stamped `assignment-4` +
    # `submission` on the `assignment-4-project` TEMPLATE, and named the wrong template on
    # a repo generated from the longer one.
    repos = [
        {"name": "assignment-4", "isTemplate": True, "topics": []},
        {"name": "assignment-4-project", "isTemplate": True, "topics": []},
        {"name": "assignment-4-project-ada", "topics": []},
    ]
    _, stamped = _converge(monkeypatch, repos)
    assert stamped == {
        "assignment-4-project-ada": ["assignment-4-project", "submission"]
    }


def test_an_archived_repo_and_a_course_org_are_left_alone(monkeypatch):
    repos = [{"name": "grades-ada", "topics": [], "archived": True}]
    assert _converge(monkeypatch, repos) == (0, {})
    monkeypatch.setattr(
        access,
        "set_repo_topics",
        lambda *a: pytest.fail("course orgs have no such repo"),
    )
    assert access.converge_topics("Course-Org", _topic_repos(), cohort=False) == 0


def test_a_failed_stamp_is_counted(monkeypatch):
    failures, _ = _converge(monkeypatch, _topic_repos(), ok=False)
    assert failures == 2
