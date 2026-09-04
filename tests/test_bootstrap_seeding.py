"""Bootstrap seeding is create-only for USER-owned files.

"Bootstrap cohort" is the documented idempotent-repair path (re-run to apply new team
grants, refresh workflows), so it runs against LIVE cohorts. `repos.create_repo` reports
an already-existing repo as success, so the `if create_repo(...)` blocks are no
first-run guard - the guard has to be per file. These tests pin the split:

- USER-owned (classroom-config roster/teams/schedule/people/grades, welcome's
  student-facing README, and the course org's dsl-course.yml SSOT): seeded once, NEVER
  rewritten - a rewrite destroyed a live roster (enrol codes + onboarded handles) in
  hertie-dsl-demo-f2026.
- SYSTEM-owned (welcome's onboard/team-formation workflows + the issue forms they parse,
  classroom-config's dispatch-*.yml, its README contract and `*.sample` worked
  examples, the cohort's generated dsl-course.yml pointer): re-pushed on every run so
  fixes reach running cohorts.

Every user-editable classroom-config file is a scaffold/sample PAIR - `<file>` seeded once,
`<file>.sample` always converged - and the samples are injected from
example-course/cohort-org/ rather than authored twice.

The COURSE tier of example-course/ is validated here too. Only its SYLLABUS.md is a seeded
pair (SYLLABUS.md.sample is derived from it); the rest is documentation, linked from docs/
and never pushed anywhere. Both halves are parsed by the engine's own readers all the same,
because the docs call that tree the live example and an unvalidated example goes
schema-stale in silence - which is how a cohort's schedule.yml once parsed as zero releases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from dsl_course import (
    assign,
    collect,
    course,
    gh_contents,
    gh_teams,
    grades,
    readings,
    roster,
    scaffold,
    schedule,
    seed,
    site_repo,
    sync_faculty,
    teams,
    welcome,
)
from dsl_course import bootstrap_course as bc
from dsl_course.central import CENTRAL
from dsl_course.repos import Converged
from tests.conftest import repo_row

# Derived from the seeding tables, so a sixth config file cannot silently miss the set
# these tests police - which is the whole point of the tables existing.
USER_OWNED = {*welcome.CLASSROOM_SCAFFOLDS, "grades/.gitkeep"}
SYSTEM_OWNED = {
    ".github/workflows/dispatch-sync.yml",
    ".github/workflows/dispatch-sync-site.yml",
    ".github/workflows/dispatch-scheduled-release.yml",
    ".github/workflows/dispatch-send-codes.yml",
    ".github/workflows/validate-schedule.yml",
    "README.md",
    *welcome.CLASSROOM_SAMPLES,
}
WELCOME_SYSTEM_OWNED = {
    ".github/workflows/onboard.yml",
    ".github/workflows/team-formation.yml",
    ".github/ISSUE_TEMPLATE/01-join-course.yml",
    ".github/ISSUE_TEMPLATE/02-join-team.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
}


class FakeOrg:
    """The repo contents bootstrap writes into, plus the log lines it emits."""

    def __init__(self, existing: dict[tuple[str, str], str] | None = None):
        self.files: dict[tuple[str, str], str] = dict(existing or {})
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.skips: list[str] = []
        self.labels: list[tuple[str, str]] = []

    def get_file_content(self, org, repo, path):
        return self.files.get((repo, path))

    def put_file(self, org, repo, path, content, message):
        self.files[(repo, path)] = content.decode()
        self.writes.append((repo, path))
        return True

    def delete_file(self, org, repo, path, message):
        self.files.pop((repo, path), None)
        self.deletes.append((repo, path))
        return True

    def put_files(self, org, repo, files, message, *, delete=(), create_only=False):
        """One commit, several files - recorded per file, so the assertions below stay
        about WHICH paths a seed touches rather than how they were batched. create_only is
        honoured here because that is now put_files' job, not the caller's."""
        for path, content in files.items():
            if create_only and (repo, path) in self.files:
                self.skips.append(f"{repo}/{path}")
                continue
            self.put_file(org, repo, path, content, message)
        for path in delete:
            self.delete_file(org, repo, path, message)
        return True

    def written(self, repo):
        return {path for r, path in self.writes if r == repo}


@pytest.fixture
def fake(monkeypatch):
    f = FakeOrg()
    # USER-owned files go through gh_contents.seed_if_absent / put_files(create_only=True),
    # which resolve get_file_content / put_file / put_files / log_skip
    # in the gh_contents namespace; SYSTEM-owned files are written by bc.put_file directly. Fake
    # every layer to the same recorder.
    monkeypatch.setattr(gh_contents, "get_file_content", f.get_file_content)
    monkeypatch.setattr(gh_contents, "put_file", f.put_file)
    monkeypatch.setattr(gh_contents, "put_files", f.put_files)
    monkeypatch.setattr(gh_contents, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(bc, "put_file", f.put_file)
    monkeypatch.setattr(bc, "put_files", f.put_files)
    # The welcome repo's SYSTEM-owned files are written by dsl_course.welcome (so that
    # seed.refresh can re-push them without importing bootstrap_course), in one commit per
    # set - so its put_files has to be faked too.
    monkeypatch.setattr(welcome, "put_files", f.put_files)
    # ...and the routing labels it seeds beside them, recorded rather than created.
    monkeypatch.setattr(
        welcome,
        "ensure_label",
        lambda org, repo, name, **k: f.labels.append((repo, name)) or True,
    )
    # everything else setup_cohort_extras does is repo-level and safe to re-run; it is
    # stubbed out so these tests stay pure (no gh calls).
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(bc, "create_cohort_teams", lambda org: 0)
    monkeypatch.setattr(bc, "grant_cohort_faculty_access", lambda org: None)
    monkeypatch.setattr(bc, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(bc.scaffold, "scaffold_site", lambda org: 0)
    return f


def test_every_seeded_doc_link_names_the_orgs_own_tier(fake):
    # The scaffolds link the runbooks by absolute URL. They named `main` whatever the org
    # ran, so a release cohort read the schema of code nobody had promoted yet.
    bc.setup_cohort_extras("Cohort-f2026", "staging")
    for path in ("schedule.yml", "people.yml"):
        body = fake.files[("classroom-config", path)]
        assert f"{CENTRAL}/blob/staging/docs/" in body, path
        assert f"{CENTRAL}/blob/main/docs/" not in body, path


def test_fresh_cohort_seeds_every_file(fake):
    bc.setup_cohort_extras("Cohort-f2026", "release")
    assert USER_OWNED | SYSTEM_OWNED == fake.written("classroom-config")
    assert fake.written("welcome") == WELCOME_SYSTEM_OWNED | {"README.md"}
    assert fake.skips == []


def test_welcome_readme_links_to_this_orgs_issue_chooser(fake):
    # The "open a Join issue" link is org-specific, so `{org}` must be substituted - an
    # unrendered placeholder would send every cohort's students to a dead link.
    bc.setup_cohort_extras("Cohort-f2026", "release")
    readme = fake.files[("welcome", "README.md")]
    assert "https://github.com/Cohort-f2026/welcome/issues/new/choose" in readme, readme
    assert "{org}" not in readme


def test_rerun_preserves_a_faculty_edited_welcome_readme(fake):
    # A repo-root README is content faculty may reword for their course; a repair re-run
    # must leave it alone while the .github/ machinery underneath it still refreshes.
    edited = "# Welcome to Deep Learning\n\nOur own wording.\n"
    fake.files[("welcome", "README.md")] = edited

    bc.setup_cohort_extras("Cohort-f2026", "release")

    assert fake.files[("welcome", "README.md")] == edited
    assert fake.written("welcome") == WELCOME_SYSTEM_OWNED
    assert "welcome/README.md" in fake.skips


def test_rerun_preserves_user_config_and_refreshes_workflows(fake):
    # A live mid-semester cohort: real roster + faculty-edited schedule/people, plus a
    # stale README/sample from an older engine version.
    live = {
        "students.csv": "email,github_handle,enrol_code\na@x.edu,ahandle,AB12CD\n",
        "teams.csv": "assignment,team,github_handle\na1,team-1,ahandle\n",
        "schedule.yml": "timezone: Europe/Berlin\nassignments:\n  - id: a1\n",
        "people.yml": "people:\n  instructors:\n    - github_handle: profx\n",
        "teams.csv.sample": "team,members\nstale,sample\n",
        "README.md": "# stale contract from an older engine\n",
        "grades/.gitkeep": "",
        ".github/workflows/dispatch-sync.yml": "name: stale dispatcher\n",
    }
    fake.files.update({("classroom-config", p): c for p, c in live.items()})
    fake.files[("welcome", ".github/workflows/onboard.yml")] = "name: stale onboard\n"

    bc.setup_cohort_extras("Cohort-f2026", "release")

    # USER-owned files: untouched, byte for byte.
    for path in USER_OWNED:
        assert ("classroom-config", path) not in fake.written("classroom-config"), path
        assert fake.files[("classroom-config", path)] == live[path], path
    assert fake.written("classroom-config") == SYSTEM_OWNED

    # SYSTEM-owned files: re-pushed, so the stale copies are replaced by the templates.
    assert fake.files[("classroom-config", ".github/workflows/dispatch-sync.yml")] == (
        welcome.template("classroom-config/dispatch-sync.yml")
    )
    assert fake.files[("classroom-config", "README.md")] == (
        welcome.template("classroom-config/README.md")
    )
    assert fake.files[("classroom-config", "teams.csv.sample")] == (
        welcome.example_cohort_file("teams.csv")
    )
    assert fake.files[("welcome", ".github/workflows/onboard.yml")] == (
        welcome.welcome_workflow("welcome/onboard.yml")
    )
    assert fake.written("welcome") == WELCOME_SYSTEM_OWNED | {"README.md"}


def test_rerun_logs_one_skip_per_preserved_file(fake):
    fake.files.update(
        {
            ("classroom-config", "students.csv"): "email\na@x.edu\n",
            ("classroom-config", "schedule.yml"): "timezone: Europe/Berlin\n",
        }
    )
    bc.setup_cohort_extras("Cohort-f2026", "release")
    assert fake.skips == [
        "classroom-config/students.csv",
        "classroom-config/schedule.yml",
    ]


def test_the_scaffold_set_lands_as_one_commit_but_stays_create_only_per_file(
    monkeypatch,
):
    # Seeding a cohort's config is one act, so the scaffolds share a commit rather than
    # opening a repo faculty then work in by hand with a burst of `init:`/`docs: seed`
    # lines. What must NOT change is the per-file create-only rule: a repair re-run against
    # a live cohort has to write only what is genuinely missing, and leave the roster (enrol
    # codes, onboarded handles) untouched.
    live = {"students.csv": "live-roster-sha"}
    monkeypatch.setattr(gh_contents, "log_skip", lambda msg: None)
    monkeypatch.setattr(gh_contents, "default_branch", lambda org, repo, **k: "main")
    monkeypatch.setattr(gh_contents, "repo_blob_shas", lambda org, repo, branch: live)
    committed = []
    monkeypatch.setattr(
        gh_contents,
        "_commit_tree",
        lambda org, repo, branch, tree, message: committed.append(tree) or True,
    )

    assert gh_contents.put_files(
        "Cohort-f2026",
        "classroom-config",
        {"students.csv": b"header only\n", "teams.csv": b"t\n", "people.yml": b"p\n"},
        "init: scaffolds",
        create_only=True,
    )
    assert len(committed) == 1
    assert {entry["path"] for entry in committed[0]} == {"teams.csv", "people.yml"}, (
        "a file already present must be left exactly as faculty left it"
    )


def test_a_create_only_write_commits_nothing_when_every_file_is_already_there(
    monkeypatch,
):
    # The whole set present is the ordinary repair-re-run case, and it must cost no commit.
    monkeypatch.setattr(gh_contents, "log_skip", lambda msg: None)
    monkeypatch.setattr(gh_contents, "default_branch", lambda org, repo, **k: "main")
    monkeypatch.setattr(
        gh_contents, "repo_blob_shas", lambda org, repo, branch: {"students.csv": "sha"}
    )
    monkeypatch.setattr(
        gh_contents, "_commit_tree", lambda *a, **k: pytest.fail("wrote a no-op commit")
    )
    assert gh_contents.put_files(
        "Cohort-f2026",
        "classroom-config",
        {"students.csv": b"x\n"},
        "init: scaffolds",
        create_only=True,
    )


def test_seed_if_absent_skips_an_empty_existing_file(fake):
    # New contract: a skip means the file IS present as intended, so seed_if_absent returns
    # True (a success, not a failure) and attempts no write. get_file_content returns "" for
    # an existing empty file (grades/.gitkeep) - falsy but present, so it still counts.
    fake.files[("classroom-config", "grades/.gitkeep")] = ""
    assert gh_contents.seed_if_absent(
        "Cohort-f2026", "classroom-config", "grades/.gitkeep", b"x", "msg"
    )
    assert fake.writes == []
    assert "classroom-config/grades/.gitkeep" in fake.skips


def test_seed_if_absent_returns_false_only_when_the_write_fails(monkeypatch):
    # The write-failed case must be distinguishable from a skip: an ABSENT file whose
    # put_file fails returns False, so `if not seed_if_absent(...): failures += 1` counts
    # exactly the real failures (never a skip of a live file).
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(gh_contents, "put_file", lambda *a, **k: False)
    assert not gh_contents.seed_if_absent("Org", "repo", "path", b"x", "msg")


def test_seeded_scaffolds_render_this_cohorts_tag(fake):
    # people.yml's commented example carries THIS cohort's dates, so the window a faculty
    # member uncomments is already the right one. The schedule.yml scaffold deliberately
    # ships key-only (no example values to render) - `schedule.yml.sample` is where a
    # filled, tag-correct term lives instead.
    #
    # The invariant that matters for every scaffold: no format placeholder may survive
    # into a seeded file. A `{tag}` reaching a cohort repo is a broken example, and it
    # would only be noticed by the faculty member who copy-pasted it.
    bc.setup_cohort_extras("Deep-Learning-f2027", "release")
    people = fake.files[("classroom-config", "people.yml")]
    assert '"2027-09-01"' in people and '"2028-01-31"' in people
    for (repo, path), content in fake.files.items():
        assert "{tag}" not in content and "{year" not in content, f"{repo}/{path}"


def test_cohort_tag_derivation():
    assert bc._tag_and_year("Deep-Learning-f2027") == ("f2027", 2027)
    assert bc._tag_and_year("Stats-S2030") == ("s2030", 2030)
    # No recognisable suffix -> the fallback keeps the examples plausible.
    assert bc._tag_and_year("Some-Odd-Name") == ("f2026", 2026)


def test_the_sample_set_is_the_whole_worked_example_cohort():
    # The set is DERIVED from example-course/cohort-org/, not enumerated - that is what
    # makes "every file in cohort-org/ ships as a sample" true rather than aspirational
    # (an enumeration once silently dropped the team-graded grades table).
    assert set(welcome.CLASSROOM_SAMPLES) == {
        "students.csv.sample",
        "teams.csv.sample",
        "schedule.yml.sample",
        "people.yml.sample",
        "grades/assignment-1.csv.sample",
        "grades/assignment-4-project.csv.sample",
    }
    for path, source in welcome.CLASSROOM_SAMPLES.items():
        assert (welcome.EXAMPLE_COHORT / source).is_file(), f"{path} <- {source}"


def test_every_shipped_sample_parses_with_the_real_parser():
    # A sample IS the schema documentation faculty copy from, so it is validated by the
    # very code that will read their copy - never by a second, driftable checker.
    students = roster.parse(welcome.example_cohort_file("students.csv"))
    assert len(students) >= 3
    assert any(s.is_auditor for s in students), (
        "the roster sample must exercise `role: auditor`"
    )

    per_assignment = teams.parse(welcome.example_cohort_file("teams.csv"))
    assert sorted(per_assignment["assignment-4-project"]) == [
        "team-alpha",
        "team-beta",
        "team-gamma",
    ]

    sched, error = schedule.load_file(str(welcome.EXAMPLE_COHORT / "schedule.yml"))
    assert error is None, error
    assert sched.dropped == [], "\n".join(sched.dropped)
    # the current three-block schema, every block exercised
    assert sched.releases and sched.assignments and sched.events

    faculty = sync_faculty.parse_faculty_from_meta(
        yaml.safe_load(welcome.example_cohort_file("people.yml")) or {}
    )
    assert faculty["instructors"] and faculty["teaching_assistants"]

    # both grade tables: the individual case, and the team-graded one the derived set
    # restored (a group assignment fills team/team_grade/team_comments instead)
    individual = grades.parse_grades(
        welcome.example_cohort_file("grades/assignment-1.csv")
    )
    project = grades.parse_grades(
        welcome.example_cohort_file("grades/assignment-4-project.csv")
    )
    assert individual and not any(r.team for r in individual)
    assert project and all(r.team and r.team_score for r in project)


def test_scaffold_and_sample_carry_the_engines_current_column_sets():
    # A drifted header teaches faculty a schema the engine no longer reads. The scaffolds
    # are the file they fill in; the samples are what they copy rows from.
    def header(text: str) -> tuple[str, ...]:
        return tuple(text.splitlines()[0].split(","))

    for name, fields in (
        ("students.csv", roster.FIELDS),
        ("teams.csv", teams.FIELDS),
    ):
        assert header(welcome.template(f"classroom-config/{name}")) == fields
        assert header(welcome.example_cohort_file(name)) == fields
    # header-only scaffolds: nobody to enrol, and no team to provision, by accident
    assert roster.parse(welcome.template("classroom-config/students.csv")) == []
    assert teams.parse(welcome.template("classroom-config/teams.csv")) == {}
    for path, source in welcome.CLASSROOM_SAMPLES.items():
        if source.startswith("grades/"):
            assert header(welcome.example_cohort_file(source)) == grades.GRADE_FIELDS, (
                path
            )


def test_samples_carry_nothing_that_only_makes_sense_inside_this_repo():
    # example-course/cohort-org/ is SHIPPING reference material: each file is pushed into
    # every cohort's private config repo, where a repo-relative `docs/...` link resolves to
    # nothing. Full URLs only, as the seeded README already does.
    for path, source in welcome.CLASSROOM_SAMPLES.items():
        for line in welcome.example_cohort_file(source).splitlines():
            assert "docs/" not in line or "https://" in line, f"{path}: {line}"


def test_the_people_sample_names_nobody_real():
    # The staff cards it demonstrates land in six live cohort orgs, so a real handle would
    # be an unasked-for mention (and would resolve to a real avatar). Fictional people
    # only: either no handle at all (valid - the card is display-only) or the
    # demo-*-placeholder convention.
    faculty = sync_faculty.parse_faculty_from_meta(
        yaml.safe_load(welcome.example_cohort_file("people.yml")) or {}
    )
    for role, people in faculty.items():
        for person in people:
            handle = (person.get("github_handle") or "").strip()
            assert not handle or (
                handle.startswith("demo-") and handle.endswith("-placeholder")
            ), f"{role}: {handle}"


# ------------------------------- the COURSE tier of the worked example
#
# example-course/course-org/ is what docs/02, docs/03, docs/README and DEPLOYMENT-CHECKLIST
# all send faculty to as the canonical live example. Only SYLLABUS.md is seeded (as the
# `.sample` half of the materials repo's syllabus pair); the rest is read by humans. Either
# way it is parsed by the ENGINE'S readers below - never a second checker written for tests
# - so a schema move that leaves this tree behind fails here rather than in a faculty repo.


def _shipped_syllabus_sample() -> str:
    return scaffold.materials_system_files("Course-E1", "course-materials-f2026")[
        course.SYLLABUS_SAMPLE_FILE
    ].decode()


def test_the_syllabus_sample_is_read_from_the_example_course_not_authored_twice(
    monkeypatch,
):
    # The course tier's one scaffold/sample pair follows the cohort rule: DERIVED. Asserting
    # that the shipped sample contains the example's text would be tautological (it is read
    # from it), so feed the reader a sentinel instead - that fails the moment anyone
    # reintroduces a hand-authored literal, which is how the two copies drifted to a filled
    # syllabus and a three-line stub in the first place.
    monkeypatch.setattr(
        scaffold,
        "example_course_file",
        lambda rel: "# Sentinel\n\nbody of the sentinel\n",
    )
    shipped = _shipped_syllabus_sample()
    assert shipped.startswith("# Sentinel\n")
    assert "body of the sentinel" in shipped


def test_the_syllabus_samples_ownership_notice_is_added_at_the_write_site():
    # The notice is NOT carried in the example: in its own org that file is a course team's
    # own INSTRUCTOR-OWNED syllabus and must not claim the toolkit overwrites it. It is
    # stamped on the way out instead, under the title (an H1 on line 1 is what the
    # derivation splits on, and what every renderer of this file assumes).
    example = welcome.example_course_file(scaffold.EXAMPLE_SYLLABUS)
    shipped = _shipped_syllabus_sample()

    assert example.startswith("# "), "the example syllabus must open with its H1 title"
    assert "SYSTEM-OWNED" not in example
    lines = shipped.splitlines()
    assert lines[0] == example.splitlines()[0]
    assert "SYSTEM-OWNED" in "\n".join(lines[1:8])


def test_the_example_course_declares_every_key_the_generator_writes():
    # If `_course_metadata` gains an identity key, the worked example must teach it. The
    # expectation is DERIVED from the generator's own template, never a hand-kept list
    # that could drift the same way.
    generated = yaml.safe_load(
        welcome.template("course/dsl-course.yml").format(
            org="Course-E1",
            org_name="Course",
            course_name="Deep Learning",
            course_code="E1",
        )
    )
    example = yaml.safe_load(welcome.example_course_file("dsl-course.yml"))
    assert set(generated) <= set(example), (
        f"the worked example omits {sorted(set(generated) - set(example))}"
    )


def test_the_example_courses_people_block_feeds_both_of_its_readers():
    # One block, two consumers: sync_faculty grants GitHub access from it, site_repo.py renders
    # website cards from it. A card key the theme cannot read is invisible until a real
    # site is built, so check the mapping here.
    meta = yaml.safe_load(welcome.example_course_file("dsl-course.yml"))
    faculty = sync_faculty.parse_faculty_from_meta(meta)
    assert faculty["course_admins"], "course_admins is the SSOT for course-wide admin"
    assert faculty["instructors"], (
        "instructor cards are what the public course site shows"
    )

    for entry in faculty["instructors"]:
        # the example must teach OUR spelling - `photo`/`url` are what a course declares,
        # and an example already written in the theme's names would pass the mapping below
        # while teaching faculty a key the docs never document
        assert "photo" in entry and "url" in entry, sorted(entry)
        card = site_repo._card(entry)
        # ...and the theme's on the way out
        assert "photo" not in card and "url" not in card
        assert card.get("profile_pic") and card.get("webpage")
        # access-only keys never reach a public page
        assert not set(card) & set(site_repo.ACCESS_ONLY)


def test_every_example_assignment_parses_with_the_real_grading_reader():
    # grading.yml is design-time faculty input, and `parse_grading_spec` defaults every
    # missing key - so a retired spelling in the example reads as a silent default rather
    # than an error. Assert the VALUES, not just that it parses.
    kinds = {}
    for a in sorted(welcome.EXAMPLE_COURSE.glob("assignment-*")):
        spec_file = a / "solution" / collect.GRADING_FILE
        assert spec_file.is_file(), f"{a.name}: no solution/{collect.GRADING_FILE}"
        spec = collect.parse_grading_spec(spec_file.read_text())
        kinds[a.name] = spec["type"]
        # the hidden tests the Grade assignment workflow runs live where the file says
        assert (a / "solution" / spec["tests"]).is_dir(), (
            f"{a.name}: `tests: {spec['tests']}` names no directory"
        )
        assert spec["autograde"] is True
    # both kinds are demonstrated - `type: group` is what drives team provisioning, and an
    # example that only ever showed individual assignments taught half the schema
    assert "group" in kinds.values() and "individual" in kinds.values(), kinds


def test_every_example_assignment_has_the_layout_the_engine_pushes():
    # `assign` pushes `main/` to the student repo's main branch and `solution/` to the
    # solution branch. A worked example missing either half is not a copyable one.
    for a in sorted(welcome.EXAMPLE_COURSE.glob("assignment-*")):
        assert (a / "main").is_dir(), f"{a.name}: no main/ - students would get nothing"
        assert (a / assign.SOLUTION_DIR).is_dir(), f"{a.name}: no solution/"
        assert any((a / "main").iterdir()), f"{a.name}: main/ is empty"


def test_the_example_materials_tree_is_a_releasable_one():
    # A section is any top-level dir with an ordinal-prefixed subdir - the structure IS
    # the config, so an example that renamed a folder out of that shape would document a
    # tree the Release actions cannot see.
    materials = welcome.EXAMPLE_COURSE / "course-materials-f2026"
    sections = course.discover_sections(materials)
    assert {"lectures", "labs", "readings"} <= set(sections), sections
    # the readings redesign: the overlay is named by FILENAME, not extension
    overlays = sorted(materials.glob(f"readings/*/{readings.READING_OVERLAY_FILE}"))
    assert overlays, (
        f"no {readings.READING_OVERLAY_FILE} in the example's readings sessions - the "
        f"example must show the optional prose overlay, not just uploaded files"
    )


def test_the_example_course_names_nobody_real():
    # Same rule as the cohort samples, for the same reason: faculty copy this file, and a
    # real handle would send an unasked-for org invitation (the file says so itself).
    meta = yaml.safe_load(welcome.example_course_file("dsl-course.yml"))
    for role, people in sync_faculty.parse_faculty_from_meta(meta).items():
        for person in people:
            handle = (person.get("github_handle") or "").strip()
            assert not handle or (
                handle.startswith("demo-") and handle.endswith("-placeholder")
            ), f"{role}: {handle}"


def test_course_dsl_course_yml_is_never_rewritten(fake, monkeypatch):
    # The course org's dsl-course.yml is the faculty SSOT (people.course_admins, instructor
    # cards): a repair re-run of "Bootstrap course" must not reset it to the template.
    monkeypatch.setattr(bc, "set_repo_topics", lambda *a, **k: True)
    edited = (
        "org: My-Course-E1\npeople:\n  course_admins:\n    - github_handle: alice\n"
    )
    fake.files[(".github", "dsl-course.yml")] = edited

    bc.create_profile_repo("My-Course-E1", "My Course", "Deep Learning", "E1")

    assert fake.files[(".github", "dsl-course.yml")] == edited
    assert fake.writes == []
    assert fake.skips == [".github/dsl-course.yml"]


def test_rerun_retires_the_pre_rename_issue_forms(fake):
    # The forms moved to 01-/02- prefixed names (chooser ordering); a live cohort still
    # carrying the old files would show both generations in the issue chooser.
    fake.files[("welcome", ".github/ISSUE_TEMPLATE/join.yml")] = "name: old\n"
    bc.setup_cohort_extras("Cohort-f2026", "release")
    assert ("welcome", ".github/ISSUE_TEMPLATE/join.yml") in fake.deletes
    assert ("welcome", ".github/ISSUE_TEMPLATE/join.yml") not in fake.files


# --------------------------- setup_cohort_extras closes the partial-provisioning holes


def test_cohort_extras_reds_when_a_repo_cannot_be_created(fake, monkeypatch):
    # A failed create_repo (post-PR1, a genuine failure - not the idempotent 422) leaves the
    # cohort with no student-facing repo; its False must be counted and the seeding skipped,
    # not silently dropped by a bare `if create_repo(...)` that reports a green cohort.
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: False)
    assert (
        bc.setup_cohort_extras("Cohort-f2026", "release") == 2
    )  # welcome + classroom-config
    # both seeding blocks skipped - nothing was written into either repo
    assert fake.writes == []


def test_cohort_extras_no_longer_repeat_the_org_tighten(fake, monkeypatch):
    # One home for the PATCH (gh_teams.converge_org_settings), so the two org kinds
    # cannot drift.
    patched: list[tuple[str, ...]] = []
    monkeypatch.setattr(bc, "gh", lambda *a, **k: patched.append(a) or (0, ""))
    monkeypatch.setattr(gh_teams, "gh", lambda *a, **k: patched.append(a) or (0, ""))
    bc.setup_cohort_extras("Cohort-f2026", "release")
    fields = [f for call in patched for f in call]
    assert "default_repository_permission=none" not in fields


def test_cohort_extras_reds_when_a_dispatcher_write_fails(fake, monkeypatch):
    # A failed SYSTEM-owned write (the classroom-config README contract, or a dispatch-sync
    # workflow) means membership/site sync never triggers, yet the create_repo blocks stay
    # green - so the previously-discarded write return is now counted. The SYSTEM-owned
    # writes go through dsl_course.welcome (shared with the nightly refresh), so that is
    # the put_files to break.
    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: False)
    assert bc.setup_cohort_extras("Cohort-f2026", "release") >= 1


def test_cohort_extras_reds_when_a_user_file_seed_fails(fake, monkeypatch):
    # A USER-owned scaffold that is absent and whose write FAILS must red the bootstrap -
    # seed_if_absent's False (a real write failure, not a skip of a live file) is now folded
    # into the count.
    monkeypatch.setattr(gh_contents, "put_file", lambda *a, **k: False)
    assert bc.setup_cohort_extras("Cohort-f2026", "release") >= 1


# ------------------------------------------ the one initial site sync a bootstrap does


def _stub_bootstrap(monkeypatch) -> None:
    """Neutralise everything a cohort bootstrap does EXCEPT the site sync - the org-level
    gh/git layer, the repo seeding (covered above) and the summary output."""
    # Every configuration step reports a failure count that _run threads into its exit
    # code and into the closing summary - a clean stub reports zero failures.
    for name in (
        "converge_org_settings",
        "create_default_teams",
        "grant_button_access",
        "setup_cohort_extras",
        "seed_workflows",
        "create_profile_repo",
    ):
        monkeypatch.setattr(bc, name, lambda *a, **k: 0)
    monkeypatch.setattr(bc, "preflight", lambda org: True)
    monkeypatch.setattr(bc, "add_course_admins", lambda org, handles: 0)
    monkeypatch.setattr(bc, "validate_secret_presence", lambda org, secret: True)
    monkeypatch.setattr(bc, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bc, "register_cohort", lambda course, cohort: True)
    monkeypatch.setattr(bc, "update_profile_readme", lambda *a, **k: 0)
    monkeypatch.setattr(bc.sync_faculty, "sync", lambda course, cohorts=None: 0)
    # The org's tier is read off its (not yet written) dsl-course.yml; every test here
    # is about what bootstrap seeds, not which ref it seeds at.
    monkeypatch.setattr(bc, "central_ref_for", lambda org: "release")


def test_cohort_bootstrap_runs_one_initial_site_sync(monkeypatch):
    # Without it a fresh cohort site keeps the website template's placeholders ("Fall
    # 2025", "Course Name (Code)") until the first successful "Sync site" - which in the
    # live incident never came, because the cohort's schedule.yml stopped parsing.
    synced: list[tuple[str, str]] = []
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc.site, "sync_site", lambda c, o: synced.append((c, o)) or 0)
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_course",
            "--org",
            "Cohort-f2026",
            "--cohort",
            "--course",
            "Course-Org",
        ],
    )

    assert bc.main() == 0
    assert synced == [("Course-Org", "Cohort-f2026")]


def _raises(c, o):
    raise RuntimeError("pages 404")


@pytest.mark.parametrize("outcome", [lambda c, o: 1, _raises], ids=["rc=1", "raises"])
def test_bootstrap_survives_a_failing_initial_site_sync(monkeypatch, capsys, outcome):
    # Best effort: Pages provisioning can lag right behind repo creation, and the org is
    # already configured by this point - a hiccup must not fail the bootstrap.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc.site, "sync_site", outcome)
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_course",
            "--org",
            "Cohort-f2026",
            "--cohort",
            "--course",
            "Course-Org",
        ],
    )

    assert bc.main() == 0
    assert "Sync site" in capsys.readouterr().err


def test_bootstrap_reports_an_unreachable_api_instead_of_a_traceback(
    monkeypatch, capsys
):
    # Every read on the way through (the create-only file check, the cohort registry, the
    # repo listing behind the profile README) now raises rather than reporting an absent
    # file or an empty org. Bootstrap runs from a button, so that has to land as an [err]
    # line and a red run, not a Python traceback halfway down the log.
    _stub_bootstrap(monkeypatch)

    def boom(org, org_name=None, course_name=None, **kwargs):
        raise RuntimeError("could not list repos in Course-Org: gh: HTTP 502")

    monkeypatch.setattr(bc, "update_profile_readme", boom)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 1
    assert "HTTP 502" in capsys.readouterr().err


# ----------------------------------------- the nightly refresh converges live cohorts


def _stub_refresh(
    monkeypatch,
    welcome_failures=lambda org: 0,
    sample_failures=lambda org: 0,
    system_failures=lambda org, ref: 0,
    pointer_failures=lambda org, course: 0,
    seed_failures=0,
    heartbeat_failures=0,
    prior_misses=(),
) -> dict[str, str]:
    """Neutralise every network call seed.refresh makes; the write paths report a
    failure count, which is what refresh's exit code is built from.

    Returns the in-memory `.github` file store, seeded with `prior_misses` as the
    previous night's miss ledger (MISSES_PATH) - so a test can drive the two-misses rule
    across runs without stubbing the rule itself. Each entry is a `<cohort> <first missed
    at>` line; `_missed_at` builds one at a chosen age."""
    monkeypatch.setattr(seed, "central_ref_for", lambda org: "release")
    monkeypatch.setattr(
        seed, "discover_cohorts", lambda org: ["Cohort-f2026", "Cohort-s2027"]
    )
    monkeypatch.setattr(seed, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(seed, "_propagate_repo_secret", lambda org, repos: 0)
    monkeypatch.setattr(seed, "list_org_repos", lambda org: [])
    monkeypatch.setattr(seed, "converge_org_settings", lambda org: 0)
    monkeypatch.setattr(seed, "create_role_teams", lambda org, teams: 0)
    monkeypatch.setattr(seed, "_converge_org_metadata", lambda org, repos: 0)
    monkeypatch.setattr(seed, "seed_github_workflows", lambda org, ref: seed_failures)
    monkeypatch.setattr(seed, "_write_heartbeat", lambda org: heartbeat_failures)
    monkeypatch.setattr(seed, "update_profile_readme", lambda org, **k: 0)
    monkeypatch.setattr(seed, "refresh_welcome_workflows", welcome_failures)
    monkeypatch.setattr(seed, "refresh_classroom_samples", sample_failures)
    monkeypatch.setattr(seed, "refresh_classroom_system_files", system_failures)
    monkeypatch.setattr(seed, "refresh_cohort_pointer", pointer_failures)
    # The per-cohort loop probes the cohort ORG once: gone = unregister + skip. A live org
    # then reads the archived flag off its own listing (empty above = nothing archived),
    # so org_exists True + an unarchived classroom-config = present and live, proceed.
    monkeypatch.setattr(seed, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(seed, "org_exists", lambda org: True)
    monkeypatch.setattr(seed, "unregister_cohort", lambda course, cohort: True)
    store = {seed.MISSES_PATH: "".join(f"{m}\n" for m in prior_misses)}
    monkeypatch.setattr(
        seed, "get_file_content", lambda org, repo, path: store.get(path)
    )
    monkeypatch.setattr(
        seed,
        "put_file",
        lambda org, repo, path, content, msg: (
            store.__setitem__(path, content.decode()) or True
        ),
    )
    return store


@pytest.mark.parametrize(
    "per_cohort_job",
    ["welcome_failures", "sample_failures", "system_failures"],
    ids=["welcome-workflows", "config-samples", "classroom-system-files"],
)
def test_refresh_reaches_every_registered_cohort(monkeypatch, per_cohort_job):
    # Every per-cohort job is seeded at Bootstrap cohort, and then left behind by an
    # engine (and a set of schemas) that keep moving on central main. The nightly Refresh
    # is what closes that gap, so each has to reach EVERY registered cohort, not just the
    # course org. The classroom-config dispatchers/README used to refresh ONLY inside
    # Bootstrap cohort, so three live cohorts drifted a semester behind the templates.
    refreshed: list[str] = []
    _stub_refresh(
        monkeypatch, **{per_cohort_job: lambda org, *a: refreshed.append(org) or 0}
    )

    assert seed.refresh("Course-Org") == 0
    assert refreshed == ["Cohort-f2026", "Cohort-s2027"]


def test_refresh_repushes_every_cohorts_course_pointer(monkeypatch):
    # `.github/dsl-course.yml` is what a cohort's classroom-config dispatchers read to
    # find their course org. SYSTEM-owned, but written only by Bootstrap cohort's own
    # wiring until now, so every live cohort's copy froze the day it was created - same
    # bug class as the landing pages below.
    pointed: list[tuple[str, str]] = []
    _stub_refresh(
        monkeypatch,
        pointer_failures=lambda cohort, course: pointed.append((cohort, course)) or 0,
    )

    assert seed.refresh("Course-Org") == 0
    assert pointed == [
        ("Cohort-f2026", "Course-Org"),
        ("Cohort-s2027", "Course-Org"),
    ]


def test_refresh_rebuilds_every_cohorts_own_landing_pages(monkeypatch):
    # Both org READMEs are SYSTEM-owned and documented as rewritten on every nightly
    # refresh, but only the COURSE org's pair ever was: a cohort's were written once at
    # Bootstrap and then frozen, so every wording fix since reached the course org and no
    # cohort (a live cohort's .github README sat untouched for months).
    rendered: list[str] = []
    _stub_refresh(monkeypatch)
    monkeypatch.setattr(
        seed, "update_profile_readme", lambda org, **k: rendered.append(org) or 0
    )

    assert seed.refresh("Course-Org") == 0
    assert rendered == ["Course-Org", "Cohort-f2026", "Cohort-s2027"]


def test_refresh_reasserts_every_cohort_role_teams_privacy(monkeypatch):
    # A team's privacy was asserted only by the create call at bootstrap, and nothing
    # revisited it: every cohort made before students/auditors were declared `secret`
    # still has them `closed`, with the class list browsable by the class. The nightly
    # sweep is what closes that, from the SAME table bootstrap creates them with.
    asked: list[tuple[str, str, str]] = []

    def record(org, teams):
        asked.extend((org, slug, privacy) for slug, _, privacy in teams)
        return 0

    _stub_refresh(monkeypatch)
    monkeypatch.setattr(seed, "create_role_teams", record)

    assert seed.refresh("Course-Org") == 0
    # Cohorts only - the course org holds unreleased materials and never gets the
    # student teams (course.COHORT_TEAMS).
    assert asked == [
        (cohort, slug, privacy)
        for cohort in ("Cohort-f2026", "Cohort-s2027")
        for slug, _, privacy in (*course.FACULTY_TEAMS, *course.COHORT_TEAMS)
    ]
    # ...and the table itself still says what the decision was, not just consistently
    # whatever it happens to hold.
    assert {slug: privacy for _, slug, privacy in asked} == {
        "instructors": "closed",
        "course-admin": "closed",
        "students": "secret",
        "auditors": "secret",
    }


def test_refresh_reds_when_a_role_team_privacy_cannot_be_converged(monkeypatch):
    # A team whose privacy would not converge leaves a cohort's class list readable by
    # the class, which is the kind of thing a green nightly cron must not hide.
    _stub_refresh(monkeypatch)
    monkeypatch.setattr(
        seed, "create_role_teams", lambda org, teams: 1 if "f2026" in org else 0
    )

    assert seed.refresh("Course-Org") == 1


def test_refresh_leaves_an_archived_cohort_frozen(monkeypatch, capsys):
    # A finished semester's repos are archived, so every write 403s - and the config
    # samples are NEW files, which put_file's identical-sha no-op cannot absorb. The
    # nightly cron therefore went red every night in any org with a past cohort. Skipping
    # the archived cohort whole is the fix; the live cohort beside it still converges.
    refreshed: list[str] = []

    def refresh_one(org: str, *a) -> int:
        refreshed.append(org)
        return 9 if org == "Cohort-f2026" else 0  # what the 403s would have counted as

    _stub_refresh(
        monkeypatch,
        welcome_failures=refresh_one,
        sample_failures=refresh_one,
        system_failures=refresh_one,
    )
    # Both orgs live (org probe healthy); Cohort-f2026 is a finished, archived semester.
    # Read off the cohort's own listing, which the convergence sweep below needs anyway.
    monkeypatch.setattr(seed, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(
        seed,
        "list_org_repos",
        lambda org: [
            {
                "name": seed.CONFIG_REPO,
                "topics": [],
                "archived": org == "Cohort-f2026",
            }
        ],
    )

    rendered: list[str] = []
    pointed: list[str] = []
    monkeypatch.setattr(
        seed, "update_profile_readme", lambda org, **k: rendered.append(org) or 0
    )
    monkeypatch.setattr(
        seed, "refresh_cohort_pointer", lambda org, course: pointed.append(org) or 0
    )

    assert seed.refresh("Course-Org") == 0
    # every job, live cohort only
    assert refreshed == ["Cohort-s2027"] * 3
    # The pointer write sits in the same loop and must honour the skip too.
    assert pointed == ["Cohort-s2027"]
    # The landing pages are written inside the same loop, so they have to honour the skip
    # too - an archived repo is read-only and the write would 403 the whole cron.
    assert rendered == ["Course-Org", "Cohort-s2027"]
    out = capsys.readouterr()
    assert "[skip] Cohort-f2026 (archived cohort - left frozen)" in out.out
    assert "refresh incomplete" not in out.err


# Read ONCE, not per call: a ledger line is stamped to the second, and a test that builds
# the expected line separately from the one it fed in flaked whenever the two calls
# straddled a second boundary. Only the relative ages matter to the rule under test.
_RUN_START = datetime.now(timezone.utc)


def _missed_at(cohort: str, hours_ago: float) -> str:
    """A miss-ledger line for `cohort`, first missed `hours_ago` hours ago."""
    when = _RUN_START - timedelta(hours=hours_ago)
    return f"{cohort.casefold()} {when.isoformat(timespec='seconds')}"


def _missing_cohort_run(monkeypatch, prior_misses=()):
    """One refresh in which Cohort-f2026 does not answer; returns what it did."""
    refreshed: list[str] = []
    store = _stub_refresh(
        monkeypatch,
        welcome_failures=lambda org, *a: refreshed.append(org) or 0,
        sample_failures=lambda org, *a: refreshed.append(org) or 0,
        system_failures=lambda org, *a: refreshed.append(org) or 0,
        prior_misses=prior_misses,
    )
    monkeypatch.setattr(seed, "org_exists", lambda org: org != "Cohort-f2026")
    pruned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        seed,
        "unregister_cohort",
        lambda course, cohort: pruned.append((course, cohort)),
    )
    return seed.refresh("Course-Org"), refreshed, pruned, store


def test_refresh_prunes_a_cohort_missing_since_the_day_before(monkeypatch, capsys):
    # A cohort org DELETED after it was registered 404s on every write - which reds the
    # nightly cron forever (distinct from an archived cohort, which still exists). It is
    # skipped AND dropped from the registry: logging "prune it by hand" left the dead org
    # registered, so every nightly sync in every tool went on trying it.
    code, refreshed, pruned, store = _missing_cohort_run(
        monkeypatch, prior_misses=[_missed_at("Cohort-f2026", 25)]
    )

    assert refreshed == ["Cohort-s2027"] * 3  # deleted cohort skipped whole
    assert pruned == [
        ("Course-Org", "Cohort-f2026")
    ]  # and unregistered, not just noted
    assert store[seed.MISSES_PATH] == ""  # the ledger is cleared once it has acted
    # Unregistering is never a silent success: nothing re-adds a cohort, so the run that
    # did it has to be a run somebody looks at.
    assert code == 1
    out = capsys.readouterr()
    assert "[skip] Cohort-f2026" in out.out
    assert "refresh incomplete" in out.err


def test_a_second_miss_hours_after_the_first_does_not_unregister(monkeypatch):
    # "Two consecutive refreshes" was nominally a night apart, but two manual runs minutes
    # apart are also two consecutive refreshes - which unregistered a live cohort inside
    # one bad afternoon, off a token blip that was over by the morning.
    code, _refreshed, pruned, store = _missing_cohort_run(
        monkeypatch, prior_misses=[_missed_at("Cohort-f2026", 2)]
    )

    assert code == 0
    assert pruned == []
    # ... and the ORIGINAL timestamp survives, or the grace period would restart nightly
    # and nothing would ever be unregistered.
    assert store[seed.MISSES_PATH].strip() == _missed_at("Cohort-f2026", 2)


def test_a_ledger_written_before_timestamps_costs_one_more_grace_period(monkeypatch):
    # The live orgs carry a bare `<cohort>` line. Reading that as "missed at the epoch"
    # would unregister every one of them on the first run of this code; it reads as "too
    # recent to act on" instead, and the next run has a real timestamp to measure from.
    code, _refreshed, pruned, store = _missing_cohort_run(
        monkeypatch, prior_misses=["Cohort-f2026"]
    )

    assert (code, pruned) == (0, [])
    assert store[seed.MISSES_PATH].startswith("cohort-f2026 20")


def test_a_first_miss_never_unregisters_a_cohort(monkeypatch, capsys):
    # GitHub answers 404, not 403, for an org the TOKEN cannot see, so a bot dropped from
    # one org reads exactly like a deleted org. Acting on one look silently removed a LIVE
    # cohort from every nightly sync, and nothing re-adds it.
    code, refreshed, pruned, store = _missing_cohort_run(monkeypatch)

    assert code == 0
    assert pruned == []
    assert refreshed == ["Cohort-s2027"] * 3  # skipped for tonight, not unregistered
    # remembered for the next run, WITH the moment it was first missed
    assert store[seed.MISSES_PATH].startswith("cohort-f2026 20")
    assert "Cohort-f2026 did not answer" in capsys.readouterr().err


def test_a_cohort_that_answers_again_clears_its_miss(monkeypatch):
    store = _stub_refresh(monkeypatch, prior_misses=[_missed_at("Cohort-f2026", 25)])
    pruned: list = []
    monkeypatch.setattr(
        seed, "unregister_cohort", lambda course, cohort: pruned.append(cohort)
    )

    assert seed.refresh("Course-Org") == 0
    assert pruned == []
    assert store[seed.MISSES_PATH] == ""


def test_refresh_does_not_prune_on_a_transient_read_failure(monkeypatch):
    # A non-404 read error is NOT proof the cohort is gone - it must still be refreshed
    # (and fail loud there), never silently skipped on a rate-limit or 502, and above all
    # never UNREGISTERED, which would remove it from every nightly sync silently.
    refreshed: list[str] = []
    _stub_refresh(
        monkeypatch,
        welcome_failures=lambda org, *a: refreshed.append(org) or 0,
        sample_failures=lambda org, *a: refreshed.append(org) or 0,
        system_failures=lambda org, *a: refreshed.append(org) or 0,
    )

    def cannot_tell(org: str) -> bool:
        raise RuntimeError("gh: HTTP 502")

    monkeypatch.setattr(seed, "org_exists", cannot_tell)
    pruned: list = []
    monkeypatch.setattr(
        seed, "unregister_cohort", lambda course, cohort: pruned.append(cohort)
    )

    assert seed.refresh("Course-Org") == 0
    assert refreshed == ["Cohort-f2026"] * 3 + ["Cohort-s2027"] * 3
    assert pruned == []


# ------------------------------------------------- the 60-day auto-disable heartbeat
# GitHub disables a repo's schedules after 60 days without repository activity. A refresh
# with nothing to change writes nothing (put_file skips identical blobs), so a quiet org's
# crons - Refresh actions among them - are all switched off and cannot restart themselves.


def test_the_heartbeat_stamps_todays_date_into_the_dotgithub_repo(monkeypatch):
    written: list[tuple] = []
    monkeypatch.setattr(seed, "put_file", lambda *a: written.append(a) or True)

    assert seed._write_heartbeat("Course-Org") == 0
    ((org, repo, path, content, message),) = written
    assert (org, repo, path) == ("Course-Org", ".github", seed.HEARTBEAT_PATH)
    today = datetime.now(timezone.utc).date().isoformat()
    # The DATE alone: a second run the same day writes an identical blob, which put_file
    # skips - so at most one commit a day, never per-run churn.
    assert content.decode() == f"{today}\n"
    assert today in message


def test_a_failed_heartbeat_reds_the_refresh(monkeypatch, capsys):
    # A heartbeat that isn't landing is an org drifting towards having every cron disabled,
    # which is precisely the silent failure this exists to prevent - so it counts into
    # refresh's exit code rather than passing green.
    monkeypatch.setattr(seed, "put_file", lambda *a: False)
    assert seed._write_heartbeat("Course-Org") == 1
    assert "scheduled workflows are disabled after 60 quiet days" in (
        capsys.readouterr().err
    )

    _stub_refresh(monkeypatch, heartbeat_failures=1)
    assert seed.refresh("Course-Org") == 1


# ----------------------------------------------- _propagate_repo_secret (Free-plan gap)


def test_propagate_repo_secret_refuses_a_personal_gh_token(monkeypatch, capsys):
    # A maintainer running `seed refresh` by hand usually has their PERSONAL GH_TOKEN
    # exported; publishing it as the shared repo secret would leak their PAT into every
    # content repo. With only GH_TOKEN set we refuse - nothing is published.
    #
    # The refusal counts EVERY repo as unpropagated: an org that has not yet had the
    # nightly refresh land still runs the pre-fix new-assignment.yml (no DSL_BOT_TOKEN in
    # env), so a green refusal reports a healthy org whose live buttons have no auth.
    monkeypatch.delenv("DSL_BOT_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_personal")
    called: list = []
    monkeypatch.setattr(seed, "gh", lambda *a, **k: called.append((a, k)) or (0, ""))

    assert seed._propagate_repo_secret("Course-Org", ["cm-f2026", "cm-s2027"]) == 2
    assert called == []
    assert "refusing to publish a personal token" in capsys.readouterr().err


def test_propagate_repo_secret_reds_when_no_token_at_all_is_set(monkeypatch, capsys):
    # Neither token in env: nothing can be published, so every repo is left unpropagated
    # and the refresh must go red rather than report a converged org.
    monkeypatch.delenv("DSL_BOT_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(seed, "gh", lambda *a, **k: (0, ""))

    assert seed._propagate_repo_secret("Course-Org", ["cm-f2026"]) == 1
    assert "cannot set the DSL_BOT_TOKEN repo secret" in capsys.readouterr().err


def test_propagate_repo_secret_uses_stdin_and_counts_failures(monkeypatch, capsys):
    # The token goes over stdin - `gh secret set` reads it from there when --body is
    # omitted - never an argv --body (which `ps` exposes). `gh secret set` has no
    # --body-file flag at all, so passing one makes every call fail with "unknown flag".
    # A repo the secret could not be set on counts into the refresh exit code.
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    calls: list = []

    def fake_gh(*a, **k):
        calls.append((a, k))
        return (1, "gh: HTTP 403") if a[4].endswith("/two") else (0, "")

    monkeypatch.setattr(seed, "gh", fake_gh)

    assert seed._propagate_repo_secret("Course-Org", ["one", "two"]) == 1
    for a, k in calls:
        assert not any(x.startswith("--body") for x in a)
        assert "s3cret" not in a
        assert k.get("stdin") == "s3cret"
    assert "could not set DSL_BOT_TOKEN on Course-Org/two" in capsys.readouterr().err


# ------------------------------------------------- --propagate-secret (org-level secret)


def test_propagate_secret_refuses_a_personal_gh_token(monkeypatch, capsys):
    # The ORG secret has a WIDER blast radius than the repo secret _propagate_repo_secret
    # already guards: publishing a maintainer's personal PAT here hands it to every
    # workflow in .github/welcome/classroom-config. Refuse, and red the bootstrap - a
    # silent skip leaves an org whose buttons all fail weeks later with no auth.
    _stub_bootstrap(monkeypatch)
    monkeypatch.delenv("DSL_BOT_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_personal")
    published: list = []
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: published.append(a) or True)
    monkeypatch.setattr(
        "sys.argv", ["bootstrap_course", "--org", "Course-Org", "--propagate-secret"]
    )

    assert bc.main() == 1
    assert published == []
    err = capsys.readouterr().err
    assert "refusing to publish a personal token" in err
    assert "bootstrap incomplete" in err


def test_propagate_secret_reds_when_the_org_secret_write_fails(monkeypatch, capsys):
    # set_org_secret returns False on a failed write; that used to be dropped, reporting a
    # green bootstrap for an org whose workflows have no token.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: False)
    monkeypatch.setattr(
        "sys.argv", ["bootstrap_course", "--org", "Course-Org", "--propagate-secret"]
    )

    assert bc.main() == 1
    assert "bootstrap incomplete" in capsys.readouterr().err


def test_set_org_secret_sends_the_value_over_stdin(monkeypatch):
    # Never an argv --body: the org bootstrap runs on a shared runner, where `ps` would
    # expose the bot token to anything else on the box. Both the org secret and the
    # private-infra-repo mirror go over stdin.
    calls: list = []
    monkeypatch.setattr(bc, "repo_exists", lambda org, r: r in (".github", "welcome"))
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: r == "welcome")
    monkeypatch.setattr(bc, "gh", lambda *a, **k: calls.append((a, k)) or (0, ""))

    assert bc.set_org_secret("Course-Org", "DSL_BOT_TOKEN", "s3cret") is True
    assert len(calls) == 2  # org secret + the private `welcome` mirror
    for a, k in calls:
        assert not any(x.startswith("--body") for x in a)
        assert "s3cret" not in a
        assert k.get("stdin") == "s3cret"


def test_set_org_secret_reds_when_a_private_infra_mirror_fails(monkeypatch, capsys):
    # The org-secret write succeeding is not success on its own: on GitHub Free an org
    # secret is never delivered to a PRIVATE repo, so a failed classroom-config mirror
    # re-arms exactly the delivery gap the mirror exists to close - its dispatch
    # workflows read an empty DSL_BOT_TOKEN while the bootstrap reports green.
    monkeypatch.setattr(bc, "repo_exists", lambda org, r: True)
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: r == "classroom-config")
    monkeypatch.setattr(
        bc, "gh", lambda *a, **k: (1, "gh: HTTP 403") if "--repo" in a else (0, "")
    )

    assert bc.set_org_secret("Course-Org", "DSL_BOT_TOKEN", "s3cret") is False
    assert "failed to set repo secret on Course-Org/classroom-config" in (
        capsys.readouterr().err
    )


def test_set_secret_refuses_an_empty_secret_file(monkeypatch, capsys, tmp_path):
    # An empty/whitespace file used to write an EMPTY org secret and report success -
    # every seeded workflow then fails with "set the GH_TOKEN environment variable"
    # weeks later, with a green bootstrap behind it.
    _stub_bootstrap(monkeypatch)
    published: list = []
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: published.append(a) or True)
    empty = tmp_path / "token.txt"
    empty.write_text("   \n")
    monkeypatch.setattr(
        "sys.argv",
        ["bootstrap_course", "--org", "Course-Org", "--set-secret", str(empty)],
    )

    assert bc.main() == 1
    assert published == []
    err = capsys.readouterr().err
    assert "secret file is empty" in err
    assert "bootstrap incomplete" in err


# ------------------------------------- bootstrap threads sub-step failures into its exit


def test_course_bootstrap_reds_when_workflow_seeding_fails(monkeypatch, capsys):
    # All 17 org workflows failing to write (e.g. the token lost `workflow` scope) used to
    # exit 0 - a half-configured org that reports success. seed_workflows' failure count is
    # now threaded into the bootstrap exit code.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc, "seed_workflows", lambda org, ref: 17)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 1
    assert "bootstrap incomplete" in capsys.readouterr().err


def _cohort_argv() -> list[str]:
    return [
        "bootstrap_course",
        "--org",
        "Cohort-f2026",
        "--cohort",
        "--course",
        "Course-Org",
    ]


def test_cohort_bootstrap_reds_when_registration_fails(monkeypatch, capsys):
    # register_cohort returns False on a failed registry write: a cohort invisible to
    # discover_cohorts is invisible to every nightly sync, so it must red the bootstrap.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc, "register_cohort", lambda course, cohort: False)
    monkeypatch.setattr(bc.site, "sync_site", lambda c, o: 0)
    monkeypatch.setattr("sys.argv", _cohort_argv())

    assert bc.main() == 1
    err = capsys.readouterr().err
    assert "could not register Cohort-f2026" in err
    assert "bootstrap incomplete" in err


def test_cohort_bootstrap_reds_when_faculty_sync_reports_errors(monkeypatch, capsys):
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc.sync_faculty, "sync", lambda course, cohorts=None: 2)
    monkeypatch.setattr(bc.site, "sync_site", lambda c, o: 0)
    monkeypatch.setattr("sys.argv", _cohort_argv())

    assert bc.main() == 1
    assert "bootstrap incomplete" in capsys.readouterr().err


def test_cohort_bootstrap_reds_when_student_repos_half_seeded(monkeypatch, capsys):
    # setup_cohort_extras returns the count of welcome/config-sample writes that failed.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc, "setup_cohort_extras", lambda org, ref: 4)
    monkeypatch.setattr(bc.site, "sync_site", lambda c, o: 0)
    monkeypatch.setattr("sys.argv", _cohort_argv())

    assert bc.main() == 1
    assert "bootstrap incomplete" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("failing_job", "count"),
    [
        ("seed_failures", 2),
        ("welcome_failures", 1),
        ("sample_failures", 1),
        ("system_failures", 1),
    ],
    ids=[
        "org-workflows",
        "welcome-workflows",
        "config-samples",
        "classroom-system-files",
    ],
)
def test_refresh_goes_red_when_it_could_not_converge(
    monkeypatch, capsys, failing_job, count
):
    # The nightly cron is how an org keeps up with central. A run that failed to write
    # the buttons but reported success leaves faculty with a stale (or absent) button and
    # nothing in the Actions list to say so.
    failure = count if failing_job == "seed_failures" else (lambda org, *a: count)
    _stub_refresh(monkeypatch, **{failing_job: failure})

    assert seed.refresh("Course-Org") == 1
    assert "refresh incomplete" in capsys.readouterr().err


def test_refresh_cli_logs_an_unreachable_api_instead_of_a_traceback(
    monkeypatch, capsys
):
    # Discovery now raises rather than reporting an empty org; the CLI is where that
    # becomes an [err] line + exit 1, so the Actions log stays readable.
    def boom(org: str) -> list[str]:
        raise RuntimeError("could not list repos in Course-Org: gh: HTTP 502")

    monkeypatch.setattr(seed, "central_ref_for", lambda org: "release")
    monkeypatch.setattr(seed, "discover_cohorts", boom)
    monkeypatch.setattr("sys.argv", ["seed", "refresh", "--course-org", "Course-Org"])

    assert seed.main() == 1
    assert "HTTP 502" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("job", "extra_args", "message"),
    [
        ("refresh_welcome_workflows", (), "welcome-repo files not written"),
        ("refresh_classroom_samples", (), "classroom-config samples not written"),
        (
            "refresh_classroom_system_files",
            ("release",),
            "classroom-config system files not written",
        ),
    ],
    ids=["welcome-workflows", "config-samples", "classroom-system-files"],
)
def test_a_per_cohort_refresh_reds_on_a_failed_write_and_claims_nothing(
    monkeypatch, capsys, job, extra_args, message
):
    # "[ok] ... up to date" used to print unconditionally, so a cohort whose onboarding
    # workflow never landed still read as fully seeded. Each per-cohort job owes the caller
    # a non-zero instead, since that is what makes the nightly Refresh go red. Now that each
    # job lands as ONE commit the answer is 1, not a per-file tally: nothing partial can
    # land, so there is no count to take.
    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: False)
    monkeypatch.setattr(welcome, "ensure_label", lambda *a, **k: True)

    assert getattr(welcome, job)("Cohort-f2026", *extra_args) == 1
    out = capsys.readouterr()
    assert "up to date" not in out.out
    assert message in out.err


def test_the_nightly_classroom_refresh_touches_only_system_owned_files(monkeypatch):
    # THE no-clobber invariant. refresh_classroom_system_files runs nightly against LIVE
    # cohorts, so every path it writes is a path overwritten from a template every night.
    # The cohort's own config - students.csv (enrol codes + onboarded handles), teams.csv,
    # schedule.yml, people.yml, grades/ - is seeded create-if-missing at bootstrap and must
    # stay that way; adding one of them to the refresh set would destroy a live roster
    # (which is exactly what happened once, in hertie-dsl-demo-f2026).
    #
    # Hard-coded on purpose: deriving the expectation from welcome.CLASSROOM_SYSTEM_FILES
    # would make the test agree with any change to it.
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        welcome,
        "put_files",
        lambda org, repo, files, message, **k: (
            written.extend((repo, path) for path in files) or True
        ),
    )

    assert welcome.refresh_classroom_system_files("Cohort-f2026", "release") == 0
    assert {path for _, path in written} == {
        "README.md",
        ".github/workflows/dispatch-sync.yml",
        ".github/workflows/dispatch-sync-site.yml",
        ".github/workflows/dispatch-scheduled-release.yml",
        ".github/workflows/dispatch-send-codes.yml",
        ".github/workflows/validate-schedule.yml",
    }, (
        "the nightly refresh may only re-push SYSTEM-owned classroom-config files; a "
        "USER-owned file here (students.csv, teams.csv, schedule.yml, people.yml, "
        "grades/) would be overwritten from the template every night"
    )
    assert {repo for repo, _ in written} == {roster.CONFIG_REPO}
    # No path is written twice, so the count callers add up is one per file.
    assert len(written) == len(set(written))


# ------------------------------------ every claimed step is counted, and the summary
# ------------------------------------ is rendered from what actually happened


def test_a_team_that_could_not_be_created_is_counted(monkeypatch):
    # create_team already absorbs the idempotent duplicate-name 422, so a False here is a
    # real failure - and an org missing `instructors` is one nobody but its owner can use.
    monkeypatch.setattr(gh_teams, "create_team", lambda *a, **k: False)
    assert bc.create_default_teams("Course-Org") == len(course.FACULTY_TEAMS)
    assert bc.create_cohort_teams("Cohort-f2026") == len(course.COHORT_TEAMS)
    monkeypatch.setattr(gh_teams, "create_team", lambda *a, **k: True)
    assert bc.create_default_teams("Course-Org") == 0


def _profile_repo_run(monkeypatch, *, seeded=True, topics=True):
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(bc, "seed_if_absent", lambda *a, **k: seeded)
    monkeypatch.setattr(bc, "set_repo_topics", lambda *a, **k: topics)
    return bc.create_profile_repo("Course-Org", "Org", "Course", "C1", is_cohort=False)


def test_an_unseeded_course_ssot_reds_the_bootstrap(monkeypatch, capsys):
    # No dsl-course.yml means no faculty SSOT and no course identity for the site, but
    # its write used to be unchecked under an unconditional "initialised" line.
    assert _profile_repo_run(monkeypatch, seeded=False) == 1
    out = capsys.readouterr()
    assert "profile repo initialised" not in out.out
    assert "dsl-course.yml" in out.err


def test_an_untagged_github_repo_reds_the_bootstrap(monkeypatch):
    # Without `dsl-course-hub` the org is invisible to list_orgs.
    assert _profile_repo_run(monkeypatch, topics=False) == 1
    assert _profile_repo_run(monkeypatch) == 0


def test_a_missing_bot_token_reds_the_bootstrap(monkeypatch, capsys):
    # It used to print a WARNING and exit 0, so an org could be handed over with no
    # token at all - every seeded workflow in it fails on its first run, weeks later.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc, "validate_secret_presence", lambda org, secret: False)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 1
    assert "DSL_BOT_TOKEN not set" in capsys.readouterr().err


def test_the_summary_names_the_step_that_failed(monkeypatch, capsys):
    # The closing block used to assert every line whatever happened, so an operator read
    # a configured org off a run that configured nothing.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr(bc, "converge_org_settings", lambda org: 1)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 1
    out = capsys.readouterr().out
    assert "- [FAILED] Org settings: base permission none" in out
    assert "- Faculty teams:" in out
    assert "bootstrap INCOMPLETE" in out


def test_a_clean_run_still_reads_as_complete(monkeypatch, capsys):
    _stub_bootstrap(monkeypatch)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 0
    out = capsys.readouterr().out
    assert "[FAILED]" not in out
    assert "bootstrap complete" in out


def test_the_student_facing_teams_are_secret(monkeypatch):
    # A closed team's membership is browsable by every org member, so any student could
    # read the `auditors` list and learn a classmate's academic status.
    created: list[tuple[str, str]] = []
    monkeypatch.setattr(
        gh_teams,
        "create_team",
        lambda org, slug, desc, privacy: created.append((slug, privacy)) or True,
    )
    bc.create_cohort_teams("Cohort-f2026")
    assert created == [("students", "secret"), ("auditors", "secret")]


# ---------------------------------------------------- the nightly convergence sweep
# Descriptions, faculty-team access and machinery topics are each set once at repo
# creation and never revisited. Converging them is the nightly refresh's job; it used to
# ride inside update_profile_readme, which made a README renderer the only thing granting
# repo permissions in the estate.


_r = repo_row  # one row of a real list_org_repos listing


def _spy_sweep(monkeypatch, repos):
    """Run the sweep over `repos` and return what converge_faculty_access was told."""
    seen: dict = {}

    def spy(org, repos, tier, protected):
        seen.update(tier=tier, protected=set(protected))
        return Converged()

    monkeypatch.setattr(seed, "converge_faculty_access", spy)
    monkeypatch.setattr(seed, "converge_descriptions", lambda *a, **k: Converged())
    monkeypatch.setattr(seed, "converge_topics", lambda *a, **k: Converged())
    seed._converge_org_metadata("Org", repos)
    return seen


def test_the_sweep_is_told_the_tier_and_the_student_repos(monkeypatch):
    # Deleting the call, or passing cohort=False for a cohort, would otherwise be
    # invisible: every other test stubs the sweep to a no-op. This pins what the one call
    # site passes.
    cohort = [_r(".github", topics=["dsl-cohort"]), _r("welcome"), _r("grades-ada")]
    assert _spy_sweep(monkeypatch, cohort) == {
        "tier": "cohort",
        "protected": {"grades-ada"},
    }

    course = [_r(".github", topics=["dsl-course-hub"]), _r("course-materials-f2026")]
    assert _spy_sweep(monkeypatch, course) == {"tier": "course", "protected": set()}


def test_an_org_of_unknown_tier_gets_the_read_floor(monkeypatch):
    # A legacy cohort: `.github` without topics, student repos, no `welcome`. The landing
    # page renders it as a course org, but the sweep must NOT hand instructors push on
    # every submission repo - so it is told the tier is UNKNOWN, which faculty_floor reads
    # as the read floor, and the student repos are protected by name as well.
    legacy = [
        _r(".github"),
        _r("assignment-1", isTemplate=True),
        _r("assignment-1-ada"),
        _r("grades-ada"),
    ]
    assert _spy_sweep(monkeypatch, legacy) == {
        "tier": None,
        "protected": {"assignment-1-ada", "grades-ada"},
    }


def test_a_failed_topic_stamp_reds_the_refresh(monkeypatch):
    # A missing `submission`/`gradebook` topic is what puts a student's repo on the public
    # landing page and into the release targets, so it counts; a reworded description or
    # a retryable access PUT does not.
    monkeypatch.setattr(seed, "converge_descriptions", lambda *a, **k: Converged())
    monkeypatch.setattr(seed, "converge_faculty_access", lambda *a, **k: Converged())
    monkeypatch.setattr(seed, "converge_topics", lambda *a, **k: Converged(0, 2))
    assert seed._converge_org_metadata("Org", [_r(".github")]) == 2


def test_refresh_sweeps_every_org_off_one_listing(monkeypatch):
    # The course org AND every live cohort - a cohort's grants are the whole of a
    # non-owner instructor's access there. One list_org_repos per org, shared with the
    # landing page so the page renders the descriptions this run just corrected.
    listings: list[str] = []
    swept: list[str] = []
    tightened: list[str] = []
    rendered: list[tuple[str, int]] = []
    _stub_refresh(monkeypatch)
    monkeypatch.setattr(
        seed, "converge_org_settings", lambda org: tightened.append(org) or 0
    )
    monkeypatch.setattr(
        seed, "list_org_repos", lambda org: listings.append(org) or [_r(".github")]
    )
    monkeypatch.setattr(
        seed, "_converge_org_metadata", lambda org, repos: swept.append(org) or 0
    )
    monkeypatch.setattr(
        seed,
        "update_profile_readme",
        lambda org, **k: rendered.append((org, len(k["repos"]))) or 0,
    )

    assert seed.refresh("Course-Org") == 0
    assert swept == ["Course-Org", "Cohort-f2026", "Cohort-s2027"]
    # The org's own settings converge on the same sweep. They were written only at
    # bootstrap, so every org tightened after its own bootstrap kept GitHub's default of
    # `read` for every member on every repo.
    assert tightened == swept
    assert listings == swept
    assert rendered == [("Course-Org", 1), ("Cohort-f2026", 1), ("Cohort-s2027", 1)]
