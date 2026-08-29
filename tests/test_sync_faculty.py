"""sync_faculty parses a `people:` block (course org's or a cohort's) and flattens it
into desired GitHub team membership per role. The gh wiring (the reconcile/grant
calls) is not tested here - only the pure parsing, role->team flattening, and the
cohort-scoping/tag-matching helpers, which decide what gets reconciled.
"""

from __future__ import annotations

import yaml

from dsl_course import sync_faculty


def _parse(raw: str) -> dict:
    """Parse a people.yml/dsl-course.yml text through the production entry point."""
    return sync_faculty.parse_faculty_from_meta(yaml.safe_load(raw) or {})


def test_desired_team_members_skips_an_invalid_github_handle(capsys):
    # A typo'd handle would otherwise be invited to the org as a stranger with push on
    # `.github`; there is no faculty roster to intersect against, so charset-validation is
    # the minimum guard - an invalid handle is skipped and reported, never granted.
    faculty = {
        "instructors": [
            {"github_handle": "janedoe"},
            {"github_handle": "not a handle"},  # space -> invalid
            {"github_handle": "-leading-hyphen"},
        ],
        "course_admins": [
            {"github_handle": "admin_underscore"}
        ],  # underscore -> invalid
    }
    desired = sync_faculty.desired_team_members(faculty, today="2026-10-01")
    assert desired == {"instructors": {"janedoe"}, "course-admin": set()}
    err = capsys.readouterr().err
    assert "not a valid GitHub username" in err


def test_sync_course_admins_refuses_to_prune_when_the_config_is_absent(monkeypatch):
    # The mass-de-admin bug: an absent dsl-course.yml must NOT be read as an empty desired
    # set, or a pruning reconcile strips course-admin from every org. Absent -> skip + error.
    monkeypatch.setattr(sync_faculty, "load_faculty", lambda org: None)
    calls = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: calls.append(a) or 0,
    )
    errors = sync_faculty.sync_course_admins("Course", ["Course-f2026"])
    assert errors == 1
    assert calls == []  # nothing reconciled - no blind prune


def test_sync_course_admins_still_prunes_a_present_but_empty_people_block(monkeypatch):
    # A present-but-empty people block ({}, not None) is a legitimate "empty the team" and
    # must still reconcile (with prune) - only ABSENT is skipped.
    monkeypatch.setattr(sync_faculty, "load_faculty", lambda org: {})
    calls = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda org, team, wanted, **k: calls.append((org, team, wanted)) or 0,
    )
    errors = sync_faculty.sync_course_admins("Course", ["Course-f2026"])
    assert errors == 0
    # reconciled the course org + the one cohort, each to an empty desired set
    assert [c[0] for c in calls] == ["Course", "Course-f2026"]
    assert all(c[2] == set() for c in calls)


def test_sync_cohort_instructors_refuses_to_prune_when_people_yml_is_absent(
    monkeypatch,
):
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: None)
    calls = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: calls.append(a) or 0,
    )
    errors = sync_faculty.sync_cohort_instructors("Course", "Course-f2026", [], [])
    assert errors == 1
    assert calls == []


def test_sync_cohort_instructors_counts_failed_grants(monkeypatch):
    # create_team / grant_team_repo_access returns used to be discarded, so a failed grant
    # was invisible to the exit code. Now each failure is counted.
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: {})
    monkeypatch.setattr(sync_faculty, "reconcile_team_members", lambda *a, **k: 0)
    monkeypatch.setattr(sync_faculty, "term_tag", lambda org: "f2026")
    monkeypatch.setattr(sync_faculty, "create_team", lambda *a, **k: True)
    monkeypatch.setattr(
        sync_faculty, "grant_team_repo_access", lambda *a, **k: False
    )  # every grant fails
    errors = sync_faculty.sync_cohort_instructors(
        "Course", "Course-f2026", ["course-materials-f2026"], []
    )
    # _tag_repos always includes .github + the one matching content repo -> 2 failed grants
    assert errors == 2


def test_sync_cohort_instructors_skips_wiring_when_team_creation_fails(monkeypatch):
    # A failed create_team must not then grant access + reconcile against a nonexistent
    # team (which would triple-count the one failure and fire doomed API calls).
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: {})
    monkeypatch.setattr(sync_faculty, "term_tag", lambda org: "f2026")
    monkeypatch.setattr(sync_faculty, "create_team", lambda *a, **k: False)
    grants = []
    monkeypatch.setattr(
        sync_faculty, "grant_team_repo_access", lambda *a, **k: grants.append(a) or True
    )
    reconciles = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: reconciles.append(a) or 0,
    )
    errors = sync_faculty.sync_cohort_instructors(
        "Course", "Course-f2026", ["course-materials-f2026"], []
    )
    assert errors == 1  # the single create_team failure, counted once
    assert grants == []  # no doomed grants against a team that does not exist
    assert (
        len(reconciles) == 1
    )  # only the cohort's own instructors team, not the tag team


def test_desired_team_members_coerces_a_nonstring_handle():
    # An unquoted YAML handle can parse to int/bool; it must be stringified so the
    # downstream casefold() in reconcile can't crash the unguarded course-admin path.
    faculty = {"course_admins": [{"github_handle": 12345}]}
    desired = sync_faculty.desired_team_members(faculty, today="2026-10-01")
    assert desired["course-admin"] == {"12345"}
    assert all(isinstance(h, str) for h in desired["course-admin"])


def test_parse_faculty_skips_entries_without_github_handle():
    raw = """
people:
  instructors:
    - github_handle: janedoe
      name: "Prof. Jane Doe"
    - name: "No Handle"
  teaching_assistants:
    - github_handle: anOther
  course_admins:
    - github_handle: adminhandle
"""
    faculty = _parse(raw)
    assert [p["github_handle"] for p in faculty["instructors"]] == ["janedoe"]
    assert [p["github_handle"] for p in faculty["teaching_assistants"]] == ["anOther"]
    assert [p["github_handle"] for p in faculty["course_admins"]] == ["adminhandle"]


def test_parse_faculty_with_no_people_block_is_empty():
    assert _parse("org: My-Course-E1\n") == {}


def test_desired_team_members_maps_roles_and_filters_by_date():
    faculty = {
        "instructors": [{"github_handle": "janedoe"}],
        "teaching_assistants": [
            {"github_handle": "active-ta", "start": "2026-09-01", "end": "2027-01-31"},
            {"github_handle": "lapsed-ta", "start": "2025-09-01", "end": "2026-01-31"},
        ],
        "course_admins": [{"github_handle": "adminhandle"}],
    }
    desired = sync_faculty.desired_team_members(faculty, today="2026-10-01")
    assert desired == {
        "instructors": {"janedoe", "active-ta"},
        "course-admin": {"adminhandle"},
    }


def test_cohort_roles_only_drops_course_admins():
    faculty = {
        "instructors": [{"github_handle": "janedoe"}],
        "teaching_assistants": [{"github_handle": "anOther"}],
        "course_admins": [{"github_handle": "adminhandle"}],
    }
    cohort_faculty = sync_faculty._cohort_roles_only(faculty)
    assert "course_admins" not in cohort_faculty
    assert cohort_faculty["instructors"] == faculty["instructors"]
    assert cohort_faculty["teaching_assistants"] == faculty["teaching_assistants"]


def test_cohort_roles_only_is_safe_without_course_admins():
    faculty = {"instructors": [{"github_handle": "janedoe"}]}
    assert sync_faculty._cohort_roles_only(faculty) == faculty


def test_cohort_people_yml_declaring_course_admins_grants_nothing():
    # a stray course_admins: entry in a cohort's people.yml must not grant admin -
    # that role is exclusively course-level.
    raw = """
people:
  course_admins:
    - github_handle: sneaky
"""
    faculty = sync_faculty._cohort_roles_only(_parse(raw))
    desired = sync_faculty.desired_team_members(faculty, today="2026-10-01")
    assert desired == {"instructors": set(), "course-admin": set()}


def test_matches_tag_requires_exact_suffix_with_hyphen():
    assert sync_faculty._matches_tag("course-materials-f2026", "f2026") is True
    assert sync_faculty._matches_tag("assignment-1-s2026", "s2026") is True
    assert sync_faculty._matches_tag("course-materials-f2025", "f2026") is False
    # no hyphen before the tag-like substring - must not false-positive
    assert sync_faculty._matches_tag("course-materials-sf2026", "f2026") is False
    assert sync_faculty._matches_tag("welcome", "f2026") is False


def test_tag_repos_filters_and_always_includes_dotgithub():
    content_repos = ["course-materials-f2026", "course-materials-f2025", "welcome"]
    assignments = ["assignment-1-f2026", "assignment-2-s2026"]
    repos = sync_faculty._tag_repos(content_repos, assignments, "f2026")
    assert repos == [".github", "course-materials-f2026", "assignment-1-f2026"]


def test_tag_repos_empty_lists_still_includes_dotgithub():
    assert sync_faculty._tag_repos([], [], "f2026") == [".github"]


def test_desired_for_filters_to_one_team():
    faculty = {
        "instructors": [{"github_handle": "janedoe"}],
        "course_admins": [{"github_handle": "adminhandle"}],
    }
    assert sync_faculty._desired_for(faculty, "instructors", "2026-10-01") == {
        "janedoe"
    }
    assert sync_faculty._desired_for(faculty, "course-admin", "2026-10-01") == {
        "adminhandle"
    }
