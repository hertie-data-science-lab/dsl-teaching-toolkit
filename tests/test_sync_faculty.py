"""sync_faculty parses a `people:` block (course org's or a cohort's) and flattens it
into desired GitHub team membership per role. The gh wiring (the reconcile/grant
calls) is not tested here - only the pure parsing, role->team flattening, and the
cohort-scoping/tag-matching helpers, which decide what gets reconciled.
"""

from __future__ import annotations

import yaml

from dsl_course import gh_contents, sync_faculty


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
    # Nothing is reconciled - an absent people.yml with prune=True would strip the
    # cohort's whole instructors team - and the run stays GREEN: a file faculty have to
    # write reaches them on the people.yml digest issue, while this run's red X reaches
    # only a maintainer who cannot write another org's teaching team.
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: None)
    calls = []
    monkeypatch.setattr(
        sync_faculty,
        "reconcile_team_members",
        lambda *a, **k: calls.append(a) or 0,
    )
    errors = sync_faculty.sync_cohort_instructors("Course", "Course-f2026", [], [])
    assert errors == 0
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


# --------------------------------------------------------------- `email:`, the one field
# an instructor/TA entry needs beyond the handle. It is what a notification is sent to, so
# an entry missing it is reported - and never fixed by withholding access, which would
# take the cohort's team away over a field nobody has filled in yet.


def test_valid_email_accepts_an_address_and_rejects_everything_else():
    assert sync_faculty.valid_email("  jane@example.org  ") == "jane@example.org"
    for junk in (None, "", "jane", "@example.org", "jane@", "@", 12345):
        assert sync_faculty.valid_email(junk) is None, junk


def test_parse_faculty_reports_a_teaching_entry_that_cannot_be_notified(capsys):
    # Missing and malformed are one fault with one fix, and both keep the grant: the
    # entry is still parsed (and therefore still reconciled into the team).
    raw = """
people:
  instructors:
    - github_handle: janedoe
      name: "Prof. Jane Doe"
  teaching_assistants:
    - github_handle: anOther
      email: "not-an-address"
  course_admins:
    - github_handle: adminhandle
"""
    faculty = _parse(raw)
    assert [p["github_handle"] for p in faculty["instructors"]] == ["janedoe"]
    assert [p["github_handle"] for p in faculty["teaching_assistants"]] == ["anOther"]
    err = capsys.readouterr().err
    assert "instructors entry janedoe" in err
    assert "teaching_assistants entry anOther" in err
    assert "`email:`" in err
    # course_admins is notified through the course org, not a cohort's people.yml
    assert "adminhandle" not in err
    # the declared value is personal data: the report names the handle, never the address
    assert "not-an-address" not in err


def test_teaching_contacts_carries_the_handle_the_address_and_the_role():
    # All three, from one pass over people.yml: a notification is ADDRESSED by email,
    # ATTRIBUTED by handle (git blame speaks handles) and COPIED by role. Reading the role
    # back off the entry afterwards would mean iterating the file a second way.
    faculty = sync_faculty.parse_faculty_from_meta(
        yaml.safe_load("""
people:
  instructors:
    - github_handle: janedoe
      email: "jane@example.org"
    - github_handle: nomail
      name: "No Address"
    - github_handle: future-hire
      email: "later@example.org"
      start: "2999-09-01"
  teaching_assistants:
    - github_handle: alex
      email: "alex@example.org"
    - github_handle: lapsed-ta
      email: "gone@example.org"
      end: "2000-01-31"
""")
    )
    # Declaration order, instructors before TAs; an entry with no usable address is left
    # out (`without_email` names those), and `start`/`end` bound who is active today.
    assert sync_faculty.teaching_contacts(faculty, "2026-10-01") == [
        sync_faculty.Contact("janedoe", "jane@example.org", "instructors"),
        sync_faculty.Contact("alex", "alex@example.org", "teaching_assistants"),
    ]
    # `is_ta` is what decides who is copied, so it is asserted rather than assumed.
    assert [c.is_ta for c in sync_faculty.teaching_contacts(faculty, "2026-10-01")] == [
        False,
        True,
    ]


def test_teaching_contacts_takes_the_same_faculty_and_clock_as_without_email():
    # One parse of people.yml and one clock answer both questions: who a notification can
    # reach, and who it cannot. Two signatures meant two parses and two `date.today()`
    # calls, neither of them the clock a scheduler tick is reasoning about.
    faculty = sync_faculty.parse_faculty_from_meta(
        yaml.safe_load("""
people:
  instructors:
    - github_handle: janedoe
      email: "jane@example.org"
    - github_handle: nomail
""")
    )
    assert [
        c.handle for c in sync_faculty.teaching_contacts(faculty, "2026-10-01")
    ] == ["janedoe"]
    assert sync_faculty.without_email(faculty, "2026-10-01") == ["nomail"]


def test_teaching_contacts_with_no_people_block_is_empty():
    assert sync_faculty.teaching_contacts({}, "2026-10-01") == []


def test_without_email_names_the_active_handles_no_notification_reaches():
    faculty = {
        "instructors": [
            {"github_handle": "janedoe", "email": "jane@example.org"},
            {"github_handle": "nomail"},
        ],
        "teaching_assistants": [
            {"github_handle": "anOther", "email": "not-an-address"},
            {"github_handle": "lapsed-ta", "end": "2026-01-31"},
        ],
        # course-level, and notified through the course org
        "course_admins": [{"github_handle": "adminhandle"}],
    }
    assert sync_faculty.without_email(faculty, today="2026-10-01") == [
        "nomail",
        "anOther",
    ]


# ------------------------------------------------------------- faults a human must fix
#
# people.yml decides who has access and who can be told anything, so an entry the sync
# skips is invisible twice over: no team membership, and no notification about the entry
# either. These are the faults the digest issue and the mail carry.


def _faults(raw: str) -> list:
    """Parse people.yml text WITH line stamps, as the pre-flight reads it."""
    found = []
    sync_faculty.parse_faculty_from_meta(gh_contents.load_yaml_lines(raw), found)
    return found


PEOPLE = """people:
  instructors:
    - github_handle: jan-g
      name: Jan
      email: jan@x.edu
    - github_handle: not a handle
      name: Typo
      email: typo@x.edu
    - name: No Handle At All
      role: guest
  teaching_assistants:
    - github_handle: cpj97
      name: Camilo
"""


def test_every_unusable_people_entry_is_reported_with_its_line():
    # The third instructor is a NAMED card with no handle - display-only, which is a
    # legitimate entry and not a fault. The TA has no `email:` at all, so the citation
    # falls back to the line the entry opens on.
    found = _faults(PEOPLE)
    assert [(f.where, f.field, f.lineno) for f in found] == [
        ("people.instructors[1]", "github_handle", 6),
        ("people.teaching_assistants[0]", "email", 12),
    ]
    assert all(f.file == "people.yml" for f in found)


def test_an_entry_that_is_neither_a_handle_nor_a_card_is_a_fault():
    (fault,) = _faults("people:\n  instructors:\n    - role: guest\n")
    assert fault.where == "people.instructors[0]" and fault.lineno == 3
    assert "no `github_handle:`" in fault.what


def test_a_teaching_entry_with_no_address_still_grants_access():
    faculty = sync_faculty.parse_faculty_from_meta(
        gh_contents.load_yaml_lines(PEOPLE), []
    )
    assert [p["github_handle"] for p in faculty["teaching_assistants"]] == ["cpj97"]


def test_the_line_stamps_never_survive_the_parse():
    """The loader's reserved key would otherwise reach the site's people cards."""
    meta = gh_contents.load_yaml_lines(PEOPLE)
    faculty = sync_faculty.parse_faculty_from_meta(meta, [])
    entries = [p for role in faculty.values() for p in role]
    assert entries and all(gh_contents.LINES not in p for p in entries)


def test_a_clean_people_yml_has_no_faults():
    clean = """people:
  instructors:
    - github_handle: jan-g
      email: jan@x.edu
"""
    assert _faults(clean) == []


def test_a_people_yml_that_is_absent_or_unreadable_is_itself_the_fault(monkeypatch):
    for raise_or_return, expected in (
        (lambda **_: None, "missing"),
        (_raiser(yaml.YAMLError("bad")), "not valid YAML"),
        (_raiser(RuntimeError("not a mapping")), "not a YAML mapping"),
    ):
        monkeypatch.setattr(
            sync_faculty,
            "load_yaml_config",
            lambda *a, _f=raise_or_return, **k: _f(),
        )
        found = []
        assert sync_faculty.read_cohort_people("Cohort-f2026", found) is None
        (fault,) = found
        assert expected in fault.what
        assert fault.file == "people.yml" and fault.lineno is None


def _raiser(exc):
    def raise_it():
        raise exc

    return raise_it


# ---------------------------------------------- the COURSE org's own identity file
#
# dsl-course.yml declares the course admins and the toolkit tier every workflow under
# this course is rendered at. A fault in it is not one cohort's problem: the sync walks
# past the whole course, so it earns a digest of its own in the course org.


COURSE_CONFIG = """org: Course-Org
central_ref: release
people:
  course_admins:
    - github_handle: jan-g
    - github_handle: not a handle
  instructors:
    - github_handle: janedoe
      name: Prof. Jane Doe
"""


def _course(monkeypatch, text: str | None):
    monkeypatch.setattr(sync_faculty, "get_file_content", lambda *a, **k: text)
    found: list = []
    return sync_faculty.read_course_config("Course-Org", found), found


def test_a_course_admin_handle_no_team_can_be_given_is_a_fault(monkeypatch):
    faculty, found = _course(monkeypatch, COURSE_CONFIG)
    assert [p["github_handle"] for p in faculty["course_admins"]] == [
        "jan-g",
        "not a handle",
    ]
    (fault,) = found
    assert (fault.where, fault.field, fault.lineno) == (
        "people.course_admins[1]",
        "github_handle",
        6,
    )
    # The COURSE org's own .github, so the citation and the deep link land on the file a
    # course admin actually edits - not on some cohort's classroom-config.
    assert (fault.file, fault.in_repo) == ("dsl-course.yml", ".github")
    assert fault.link("Course-Org") == (
        "https://github.com/Course-Org/.github/blob/main/dsl-course.yml#L6"
    )


def test_a_display_only_instructor_card_is_not_asked_for_an_address(monkeypatch):
    # The course file's `instructors:` are website cards, and course-admin addresses live
    # in an org secret - so nothing here is notified through its own entry. Requiring
    # `email:` produced a fault, and now a mail, about a card doing exactly its job.
    _faculty, found = _course(monkeypatch, COURSE_CONFIG)
    assert [f.field for f in found] == ["github_handle"]


def test_a_central_ref_nothing_can_be_pinned_to_is_a_fault(monkeypatch):
    _faculty, found = _course(monkeypatch, "org: Course-Org\ncentral_ref: stagign\n")
    (fault,) = found
    assert fault.field == "central_ref" and fault.lineno == 2
    assert "stays at its previous rendering" in fault.what


def test_a_course_config_that_is_absent_or_unreadable_is_itself_the_fault(monkeypatch):
    for text, expected in (
        (None, "missing"),
        ("people: [unclosed\n", "not valid YAML"),
        ("- a list\n", "not a YAML mapping"),
    ):
        faculty, found = _course(monkeypatch, text)
        assert faculty is None
        (fault,) = found
        assert expected in fault.what
        assert fault.file == "dsl-course.yml" and fault.lineno is None
        # One sentence for both of the course org's files, because both cost the course
        # the same thing (`faults.CONSEQUENCE`).
        assert "the sync skips this course" in fault.consequence


def test_a_course_config_that_could_not_be_READ_still_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(sync_faculty, "get_file_content", boom)
    try:
        sync_faculty.read_course_config("Course-Org", [])
    except RuntimeError as exc:
        assert "rate limited" in str(exc)
    else:
        raise AssertionError("a read failure must not read as a broken file")


def test_a_clean_course_config_has_no_faults(monkeypatch):
    faculty, found = _course(
        monkeypatch,
        "org: Course-Org\npeople:\n  course_admins:\n    - github_handle: jan-g\n",
    )
    assert [p["github_handle"] for p in faculty["course_admins"]] == ["jan-g"]
    assert found == []
