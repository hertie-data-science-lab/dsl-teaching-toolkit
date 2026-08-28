"""bootstrap_course metadata builders: instructors/TAs/course-admins live on the
persistent course org (the SSOT, mirrored into every cohort by sync_faculty). A cohort
org's .github/dsl-course.yml is only a pointer back to it - its schedule lives in
classroom-config/schedule.yml (seeded from templates/classroom-config/schedule.yml).

The seeded content itself lives in real files under templates/, read at runtime by
welcome.template - so these also pin what a fresh cohort's config repo actually receives."""

from __future__ import annotations

import re
from pathlib import Path

from dsl_course import bootstrap_course as bc
from dsl_course import roster, welcome


def test_course_metadata_carries_faculty_block():
    md = bc._course_metadata("My-Course-E1", "My Course", "Deep Learning", "E1")
    assert "org: My-Course-E1" in md
    assert "course_name: Deep Learning" in md
    assert "course_code: E1" in md
    # the (commented) faculty block faculty fill in - schedule stays cohort-side
    assert "# people:" in md
    assert "github_handle" in md
    assert "schedule:" not in md
    # instructors get an OPTIONAL open-courseware card scaffold; TAs never do - they
    # change every cohort, so they're declared per cohort, not course-level.
    assert "# instructors:" in md
    assert "teaching_assistants" not in md


def test_course_metadata_seeds_admins_live_when_given():
    # --admins at bootstrap must land in the SSOT itself (uncommented), not just get a
    # one-time direct team invite (add_course_admins) - otherwise the next sync_faculty
    # run sees them as undeclared and prunes them right back out.
    md = bc._course_metadata(
        "My-Course-E1", "My Course", "Deep Learning", "E1", admins=["alice", "bob"]
    )
    assert "# people:" not in md  # live, not commented out
    assert "people:" in md
    assert '- github_handle: "alice"' in md
    assert '- github_handle: "bob"' in md


def test_parse_handles_splits_comma_and_space():
    assert bc._parse_handles("alice, bob   carol") == ["alice", "bob", "carol"]
    assert bc._parse_handles("") == []
    assert bc._parse_handles("   ") == []


def test_schedule_yml_seed_is_commented_and_covers_every_field():
    # Mostly-commented, like the old cohort dsl-course.yml schedule block - faculty
    # uncomment what they want to pin.
    schedule = welcome.template("classroom-config/schedule.yml")
    assert all(
        line.startswith("#") or not line.strip() for line in schedule.splitlines()
    )
    for key in (
        "timezone",
        "releases",
        "event_datetime",
        "deploy",
        "course_source_repo",
        "course_source_path",
        "cohort_dest_repo",
        "cohort_dest_path",
        "semester_start",
        "semester_end",
        "assignments",
        "handout_datetime",
        "grading_datetime",
        "events",
    ):
        assert key in schedule


def test_classroom_readme_points_to_course_org_for_people():
    # There is no cohort dsl-course.yml any more - the README is the one place that
    # still tells faculty where people/instructors are actually managed.
    readme = welcome.template("classroom-config/README.md")
    assert "course org" in readme
    assert "schedule.yml" in readme
    assert "schedule.csv" not in readme


def test_starter_roster_seeds_the_full_column_set():
    # A cohort discovers the roster schema from its own config repo, so the seeded header
    # must be exactly roster.FIELDS - a short header (no `enrol_code`/`role`) sends faculty
    # looking for code-based onboarding and auditors that the columns don't offer. The
    # live file is header-only; the worked example rows live in students.csv.sample
    # (covered by the seeding tests, which validate every shipped sample).
    starter = welcome.template("classroom-config/students.csv")
    assert tuple(starter.splitlines()[0].split(",")) == roster.FIELDS
    assert roster.parse(starter) == []  # header-only: nobody to enrol by accident


def test_classroom_readme_documents_every_roster_column():
    # The README's roster table is what faculty read instead of the schema doc; a column
    # missing from it is a column nobody fills in.
    readme = welcome.template("classroom-config/README.md")
    documented = set(re.findall(r"^\| `?(\w+)`? \|", readme, re.MULTILINE))
    assert set(roster.FIELDS) <= documented


def test_every_seeded_template_path_resolves():
    # The seeded content is read from disk at bootstrap time, so a typo'd or renamed path
    # would only surface mid-bootstrap against a real org.
    # Both seeding modules read templates: bootstrap_course for the course/classroom-config
    # files, welcome for the onboarding workflows it also re-pushes on every refresh.
    source = Path(bc.__file__).read_text() + Path(welcome.__file__).read_text()
    rels = set(re.findall(r"\btemplate\(\s*[\"']([^\"']+)[\"']\s*\)", source))
    assert len(rels) >= 12
    for rel in sorted(rels):
        assert (welcome.TEMPLATES / rel).is_file(), f"missing template: {rel}"


def test_cohort_metadata_carries_course_pointer():
    # The cohort .github/dsl-course.yml must carry a `course:` line - the classroom-config
    # dispatchers grep it to find where to fire Sync membership / Sync site.
    md = bc._cohort_metadata("My-Cohort-f2026", "My-Course-E1")
    assert "course: My-Course-E1" in md
    assert "org: My-Cohort-f2026" in md
    # the dispatchers do: grep '^course:' | cut -d: -f2- | xargs
    course = next(
        ln.split(":", 1)[1].strip()
        for ln in md.splitlines()
        if ln.startswith("course:")
    )
    assert course == "My-Course-E1"


def _bootstrapped_topics(monkeypatch, **kwargs) -> list[list[str]]:
    """The topics `create_profile_repo` actually stamps on the org's `.github` repo."""
    stamped: list[list[str]] = []
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(bc, "seed_if_absent", lambda *a, **k: True)
    monkeypatch.setattr(
        bc, "set_repo_topics", lambda org, repo, topics: stamped.append(topics) or True
    )
    bc.create_profile_repo("Org", "Org Name", "Course Name", **kwargs)
    return stamped


def test_a_cohort_github_repo_is_stamped_dsl_cohort_and_nothing_else(monkeypatch):
    # list_orgs.py enumerates COURSE orgs by the dsl-course-hub topic; a cohort org
    # stamped with it shows up in the course-org inventory as a phantom course. Asserted
    # through bootstrap itself: computing the right list and never stamping it - which is
    # what the old test allowed - leaves every org untagged and the inventory empty.
    assert _bootstrapped_topics(monkeypatch, course_code="E1234", is_cohort=True) == [
        ["dsl-cohort"]
    ]


def test_a_course_github_repo_is_stamped_the_hub_topic_and_its_course_code(monkeypatch):
    assert _bootstrapped_topics(monkeypatch, course_code="E1234") == [
        ["dsl-course-hub", "course-e1234"]
    ]
    assert _bootstrapped_topics(monkeypatch) == [["dsl-course-hub"]]


def test_a_repo_that_could_not_be_created_is_not_stamped(monkeypatch):
    # create_profile_repo returns early, so nothing downstream must run against a repo
    # that is not there.
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: False)
    stamped: list[list[str]] = []
    monkeypatch.setattr(
        bc, "set_repo_topics", lambda org, repo, topics: stamped.append(topics) or True
    )
    bc.create_profile_repo("Org", "Org Name", "Course Name")
    assert stamped == []


def test_inventory_skips_cohort_pointer_orgs(monkeypatch):
    # Cohorts bootstrapped before the topic split still carry dsl-course-hub, so the
    # inventory must also filter by metadata shape: a cohort's dsl-course.yml is a
    # `course:` pointer, a course org's is not.
    from dsl_course import list_orgs

    monkeypatch.setattr(
        list_orgs,
        "gh_json",
        lambda *a: [
            {"owner": {"login": "Course-Org"}, "name": ".github"},
            {"owner": {"login": "Cohort-Org"}, "name": ".github"},
        ],
    )
    metas = {
        "Course-Org": {
            "org_name": "Course Org",
            "course_name": "ML",
            "course_code": "E1",
        },
        "Cohort-Org": {"course": "Course-Org", "org": "Cohort-Org"},
    }
    monkeypatch.setattr(list_orgs, "_fetch_metadata", lambda org: metas[org])
    monkeypatch.setattr(list_orgs, "org_exists", lambda org: True)
    orgs = list_orgs.discover_course_orgs()
    assert [o["org"] for o in orgs] == ["Course-Org"]
