"""Bootstrap seeding is create-only for USER-owned files.

"Bootstrap cohort" is the documented idempotent-repair path (re-run to apply new team
grants, refresh workflows), so it runs against LIVE cohorts. `utils.create_repo` reports
an already-existing repo as success, so the `if create_repo(...)` blocks are no
first-run guard - the guard has to be per file. These tests pin the split:

- USER-owned (classroom-config roster/teams/schedule/people/grades, welcome's
  student-facing README, and the course org's dsl-course.yml SSOT): seeded once, NEVER
  rewritten - a rewrite destroyed a live roster (enrol codes + onboarded handles) in
  hertie-dsl-demo-f2026.
- SYSTEM-owned (welcome's onboard/team-formation workflows + the issue forms they parse,
  classroom-config's dispatch-sync*.yml, its README contract and `*.sample` worked
  examples, the cohort's generated dsl-course.yml pointer): re-pushed on every run so
  fixes reach running cohorts.

Every user-editable classroom-config file is a scaffold/sample PAIR - `<file>` seeded once,
`<file>.sample` always converged - and the samples are injected from
example-course/cohort-org/ rather than authored twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import yaml

from dsl_course import bootstrap_course as bc
from dsl_course import (
    grades,
    roster,
    schedule,
    seed,
    sync_faculty,
    teams,
    utils,
    welcome,
)

# Derived from the seeding tables, so a sixth config file cannot silently miss the set
# these tests police - which is the whole point of the tables existing.
USER_OWNED = {*welcome.CLASSROOM_SCAFFOLDS, "grades/.gitkeep"}
SYSTEM_OWNED = {
    ".github/workflows/dispatch-sync.yml",
    ".github/workflows/dispatch-sync-site.yml",
    ".github/workflows/validate-schedule.yml",
    "README.md",
    *welcome.CLASSROOM_SAMPLES,
}
WELCOME_SYSTEM_OWNED = {
    ".github/workflows/onboard.yml",
    ".github/workflows/team-formation.yml",
    ".github/ISSUE_TEMPLATE/01-join-course.yml",
    ".github/ISSUE_TEMPLATE/02-join-team.yml",
}


class FakeOrg:
    """The repo contents bootstrap writes into, plus the log lines it emits."""

    def __init__(self, existing: dict[tuple[str, str], str] | None = None):
        self.files: dict[tuple[str, str], str] = dict(existing or {})
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[tuple[str, str]] = []
        self.skips: list[str] = []

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
    # USER-owned files go through utils.seed_if_absent / seed_files_if_absent
    # (create-if-absent), which resolve get_file_content / put_file / put_files / log_skip
    # in the utils namespace; SYSTEM-owned files are written by bc.put_file directly. Fake
    # every layer to the same recorder.
    monkeypatch.setattr(utils, "get_file_content", f.get_file_content)
    monkeypatch.setattr(utils, "put_file", f.put_file)
    monkeypatch.setattr(utils, "put_files", f.put_files)
    monkeypatch.setattr(utils, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(bc, "put_file", f.put_file)
    # The welcome repo's SYSTEM-owned files are written by dsl_course.welcome (so that
    # seed.refresh can re-push them without importing bootstrap_course), in one commit per
    # set - so its put_files has to be faked too.
    monkeypatch.setattr(welcome, "put_files", f.put_files)
    # everything else setup_cohort_extras does is repo-level and safe to re-run; it is
    # stubbed out so these tests stay pure (no gh calls).
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(bc, "create_cohort_teams", lambda org: None)
    monkeypatch.setattr(bc, "grant_cohort_faculty_access", lambda org: None)
    monkeypatch.setattr(bc, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(bc.scaffold, "scaffold_site", lambda org: 0)
    return f


def test_fresh_cohort_seeds_every_file(fake):
    bc.setup_cohort_extras("Cohort-f2026")
    assert USER_OWNED | SYSTEM_OWNED == fake.written("classroom-config")
    assert fake.written("welcome") == WELCOME_SYSTEM_OWNED | {"README.md"}
    assert fake.skips == []


def test_welcome_readme_links_to_this_orgs_issue_chooser(fake):
    # The "open a Join issue" link is org-specific, so `{org}` must be substituted - an
    # unrendered placeholder would send every cohort's students to a dead link.
    bc.setup_cohort_extras("Cohort-f2026")
    readme = fake.files[("welcome", "README.md")]
    assert "https://github.com/Cohort-f2026/welcome/issues/new/choose" in readme, readme
    assert "{org}" not in readme


def test_rerun_preserves_a_faculty_edited_welcome_readme(fake):
    # A repo-root README is content faculty may reword for their course; a repair re-run
    # must leave it alone while the .github/ machinery underneath it still refreshes.
    edited = "# Welcome to Deep Learning\n\nOur own wording.\n"
    fake.files[("welcome", "README.md")] = edited

    bc.setup_cohort_extras("Cohort-f2026")

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

    bc.setup_cohort_extras("Cohort-f2026")

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
        welcome.template("welcome/onboard.yml")
    )
    assert fake.written("welcome") == WELCOME_SYSTEM_OWNED | {"README.md"}


def test_rerun_logs_one_skip_per_preserved_file(fake):
    fake.files.update(
        {
            ("classroom-config", "students.csv"): "email\na@x.edu\n",
            ("classroom-config", "schedule.yml"): "timezone: Europe/Berlin\n",
        }
    )
    bc.setup_cohort_extras("Cohort-f2026")
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
    monkeypatch.setattr(utils, "log_skip", lambda msg: None)
    monkeypatch.setattr(utils, "default_branch", lambda org, repo: "main")
    monkeypatch.setattr(utils, "repo_blob_shas", lambda org, repo, branch: live)
    committed = []
    monkeypatch.setattr(
        utils,
        "_commit_tree",
        lambda org, repo, branch, tree, message: committed.append(tree) or True,
    )

    assert utils.seed_files_if_absent(
        "Cohort-f2026",
        "classroom-config",
        {"students.csv": b"header only\n", "teams.csv": b"t\n", "people.yml": b"p\n"},
        "init: scaffolds",
    )
    assert len(committed) == 1
    assert {entry["path"] for entry in committed[0]} == {"teams.csv", "people.yml"}, (
        "a file already present must be left exactly as faculty left it"
    )


def test_seed_files_if_absent_commits_nothing_when_every_file_is_already_there(
    monkeypatch,
):
    # The whole set present is the ordinary repair-re-run case, and it must cost no commit.
    monkeypatch.setattr(utils, "log_skip", lambda msg: None)
    monkeypatch.setattr(utils, "default_branch", lambda org, repo: "main")
    monkeypatch.setattr(
        utils, "repo_blob_shas", lambda org, repo, branch: {"students.csv": "sha"}
    )
    monkeypatch.setattr(
        utils, "_commit_tree", lambda *a, **k: pytest.fail("wrote a no-op commit")
    )
    assert utils.seed_files_if_absent(
        "Cohort-f2026", "classroom-config", {"students.csv": b"x\n"}, "init: scaffolds"
    )


def test_seed_if_absent_skips_an_empty_existing_file(fake):
    # New contract: a skip means the file IS present as intended, so seed_if_absent returns
    # True (a success, not a failure) and attempts no write. get_file_content returns "" for
    # an existing empty file (grades/.gitkeep) - falsy but present, so it still counts.
    fake.files[("classroom-config", "grades/.gitkeep")] = ""
    assert utils.seed_if_absent(
        "Cohort-f2026", "classroom-config", "grades/.gitkeep", b"x", "msg"
    )
    assert fake.writes == []
    assert "classroom-config/grades/.gitkeep" in fake.skips


def test_seed_if_absent_returns_false_only_when_the_write_fails(monkeypatch):
    # The write-failed case must be distinguishable from a skip: an ABSENT file whose
    # put_file fails returns False, so `if not seed_if_absent(...): failures += 1` counts
    # exactly the real failures (never a skip of a live file).
    monkeypatch.setattr(utils, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(utils, "put_file", lambda *a, **k: False)
    assert not utils.seed_if_absent("Org", "repo", "path", b"x", "msg")


def test_seeded_scaffolds_render_this_cohorts_tag(fake):
    # people.yml's commented example carries THIS cohort's dates, so the window a faculty
    # member uncomments is already the right one. The schedule.yml scaffold deliberately
    # ships key-only (no example values to render) - `schedule.yml.sample` is where a
    # filled, tag-correct term lives instead.
    #
    # The invariant that matters for every scaffold: no format placeholder may survive
    # into a seeded file. A `{tag}` reaching a cohort repo is a broken example, and it
    # would only be noticed by the faculty member who copy-pasted it.
    bc.setup_cohort_extras("Deep-Learning-f2027")
    people = fake.files[("classroom-config", "people.yml")]
    assert '"2027-09-01"' in people and '"2028-01-31"' in people
    for (repo, path), content in fake.files.items():
        assert "{tag}" not in content and "{year" not in content, f"{repo}/{path}"


def test_cohort_tag_derivation():
    assert bc._cohort_tag("Deep-Learning-f2027") == ("f2027", 2027)
    assert bc._cohort_tag("Stats-S2030") == ("s2030", 2030)
    # No recognisable suffix -> the fallback keeps the examples plausible.
    assert bc._cohort_tag("Some-Odd-Name") == ("f2026", 2026)


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
    # the current three-block schema, all three blocks exercised
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

    for scaffold, fields in (
        ("students.csv", roster.FIELDS),
        ("teams.csv", teams.FIELDS),
    ):
        assert header(welcome.template(f"classroom-config/{scaffold}")) == fields
        assert header(welcome.example_cohort_file(scaffold)) == fields
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
    bc.setup_cohort_extras("Cohort-f2026")
    assert ("welcome", ".github/ISSUE_TEMPLATE/join.yml") in fake.deletes
    assert ("welcome", ".github/ISSUE_TEMPLATE/join.yml") not in fake.files


# --------------------------- setup_cohort_extras closes the partial-provisioning holes


def test_cohort_extras_reds_when_a_repo_cannot_be_created(fake, monkeypatch):
    # A failed create_repo (post-PR1, a genuine failure - not the idempotent 422) leaves the
    # cohort with no student-facing repo; its False must be counted and the seeding skipped,
    # not silently dropped by a bare `if create_repo(...)` that reports a green cohort.
    monkeypatch.setattr(bc, "create_repo", lambda *a, **k: False)
    assert bc.setup_cohort_extras("Cohort-f2026") == 2  # welcome + classroom-config
    # both seeding blocks skipped - nothing was written into either repo
    assert fake.writes == []


def test_cohort_extras_reds_when_the_org_tighten_fails(fake, monkeypatch):
    # The org-tighten PATCH leaving the cohort open (members keep default repo access) is a
    # real misconfiguration - a non-zero there must red the bootstrap, not just log and pass.
    monkeypatch.setattr(bc, "gh", lambda *a, **k: (1, "gh: HTTP 403"))
    assert bc.setup_cohort_extras("Cohort-f2026") == 1


def test_cohort_extras_reds_when_a_dispatcher_write_fails(fake, monkeypatch):
    # A failed SYSTEM-owned write (the classroom-config README contract, or a dispatch-sync
    # workflow) means membership/site sync never triggers, yet the create_repo blocks stay
    # green - so the previously-discarded write return is now counted. The SYSTEM-owned
    # writes go through dsl_course.welcome (shared with the nightly refresh), so that is
    # the put_files to break.
    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: False)
    assert bc.setup_cohort_extras("Cohort-f2026") >= 1


def test_cohort_extras_reds_when_a_user_file_seed_fails(fake, monkeypatch):
    # A USER-owned scaffold that is absent and whose write FAILS must red the bootstrap -
    # seed_if_absent's False (a real write failure, not a skip of a live file) is now folded
    # into the count.
    monkeypatch.setattr(utils, "put_file", lambda *a, **k: False)
    assert bc.setup_cohort_extras("Cohort-f2026") >= 1


# ------------------------------------------ the one initial site sync a bootstrap does


def _stub_bootstrap(monkeypatch) -> None:
    """Neutralise everything a cohort bootstrap does EXCEPT the site sync - the org-level
    gh/git layer, the repo seeding (covered above) and the summary output."""
    for name in ("set_org_settings", "create_default_teams", "grant_button_access"):
        monkeypatch.setattr(bc, name, lambda *a, **k: None)
    # setup_cohort_extras / seed_workflows report a failure count that _run threads into
    # its exit code - a clean stub reports zero failures.
    for name in ("setup_cohort_extras", "seed_workflows"):
        monkeypatch.setattr(bc, name, lambda *a, **k: 0)
    monkeypatch.setattr(bc, "preflight", lambda org: True)
    monkeypatch.setattr(bc, "create_profile_repo", lambda *a, **k: None)
    monkeypatch.setattr(bc, "add_course_admins", lambda org, handles: None)
    monkeypatch.setattr(bc, "validate_secret_presence", lambda org, secret: True)
    monkeypatch.setattr(bc, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bc.seed, "register_cohort", lambda course, cohort: True)
    monkeypatch.setattr(bc.seed, "update_profile_readme", lambda *a, **k: None)
    monkeypatch.setattr(bc.sync_faculty, "sync", lambda course, cohorts=None: 0)


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

    def boom(org, org_name=None, course_name=None):
        raise RuntimeError("could not list repos in Course-Org: gh: HTTP 502")

    monkeypatch.setattr(bc.seed, "update_profile_readme", boom)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 1
    assert "HTTP 502" in capsys.readouterr().err


# ----------------------------------------- the nightly refresh converges live cohorts


def _stub_refresh(
    monkeypatch,
    welcome_failures=lambda org: 0,
    sample_failures=lambda org: 0,
    system_failures=lambda org: 0,
    pointer_failures=lambda org, course: 0,
    seed_failures=0,
    heartbeat_failures=0,
) -> None:
    """Neutralise every network call seed.refresh makes; the write paths report a
    failure count, which is what refresh's exit code is built from."""
    monkeypatch.setattr(
        seed, "discover_cohorts", lambda org: ["Cohort-f2026", "Cohort-s2027"]
    )
    monkeypatch.setattr(seed, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(seed, "_propagate_repo_secret", lambda org, repos: 0)
    monkeypatch.setattr(seed, "seed_github_workflows", lambda org: seed_failures)
    monkeypatch.setattr(seed, "_write_heartbeat", lambda org: heartbeat_failures)
    monkeypatch.setattr(seed, "update_profile_readme", lambda org: None)
    monkeypatch.setattr(seed, "refresh_welcome_workflows", welcome_failures)
    monkeypatch.setattr(seed, "refresh_classroom_samples", sample_failures)
    monkeypatch.setattr(seed, "refresh_classroom_system_files", system_failures)
    monkeypatch.setattr(seed, "refresh_cohort_pointer", pointer_failures)
    # The per-cohort loop probes the cohort ORG once: gone = unregister + skip. A live org
    # then checks repo_is_archived (archived = skip frozen). org_exists True +
    # repo_is_archived False = present and live, proceed.
    monkeypatch.setattr(seed, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(seed, "org_exists", lambda org: True)
    monkeypatch.setattr(seed, "unregister_cohort", lambda course, cohort: True)
    monkeypatch.setattr(seed, "repo_is_archived", lambda org, repo: False)


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
        monkeypatch, **{per_cohort_job: lambda org: refreshed.append(org) or 0}
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
    monkeypatch.setattr(seed, "update_profile_readme", lambda org: rendered.append(org))

    assert seed.refresh("Course-Org") == 0
    assert rendered == ["Course-Org", "Cohort-f2026", "Cohort-s2027"]


def test_refresh_leaves_an_archived_cohort_frozen(monkeypatch, capsys):
    # A finished semester's repos are archived, so every write 403s - and the config
    # samples are NEW files, which put_file's identical-sha no-op cannot absorb. The
    # nightly cron therefore went red every night in any org with a past cohort. Skipping
    # the archived cohort whole is the fix; the live cohort beside it still converges.
    refreshed: list[str] = []

    def refresh_one(org: str) -> int:
        refreshed.append(org)
        return 9 if org == "Cohort-f2026" else 0  # what the 403s would have counted as

    _stub_refresh(
        monkeypatch,
        welcome_failures=refresh_one,
        sample_failures=refresh_one,
        system_failures=refresh_one,
    )
    # Both orgs live (org probe healthy); Cohort-f2026 is a finished, archived semester.
    monkeypatch.setattr(seed, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(
        seed, "repo_is_archived", lambda org, repo: org == "Cohort-f2026"
    )

    rendered: list[str] = []
    pointed: list[str] = []
    monkeypatch.setattr(seed, "update_profile_readme", lambda org: rendered.append(org))
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


def test_refresh_prunes_a_deleted_cohort_org(monkeypatch, capsys):
    # A cohort org DELETED after it was registered 404s on every write - which reds the
    # nightly cron forever (distinct from an archived cohort, which still exists). It is
    # skipped AND dropped from the registry: logging "prune it by hand" left the dead org
    # registered, so every nightly sync in every tool went on trying it.
    refreshed: list[str] = []
    _stub_refresh(
        monkeypatch,
        welcome_failures=lambda org: refreshed.append(org) or 0,
        sample_failures=lambda org: refreshed.append(org) or 0,
        system_failures=lambda org: refreshed.append(org) or 0,
    )
    monkeypatch.setattr(seed, "org_exists", lambda org: org != "Cohort-f2026")
    pruned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        seed,
        "unregister_cohort",
        lambda course, cohort: pruned.append((course, cohort)),
    )

    assert seed.refresh("Course-Org") == 0
    assert refreshed == ["Cohort-s2027"] * 3  # deleted cohort skipped whole
    assert pruned == [
        ("Course-Org", "Cohort-f2026")
    ]  # and unregistered, not just noted
    out = capsys.readouterr()
    assert "[skip] Cohort-f2026" in out.out
    assert "refresh incomplete" not in out.err


def test_refresh_does_not_prune_on_a_transient_read_failure(monkeypatch):
    # A non-404 read error is NOT proof the cohort is gone - it must still be refreshed
    # (and fail loud there), never silently skipped on a rate-limit or 502, and above all
    # never UNREGISTERED, which would remove it from every nightly sync silently.
    refreshed: list[str] = []
    _stub_refresh(
        monkeypatch,
        welcome_failures=lambda org: refreshed.append(org) or 0,
        sample_failures=lambda org: refreshed.append(org) or 0,
        system_failures=lambda org: refreshed.append(org) or 0,
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
    assert "cannot propagate the repo secret" in capsys.readouterr().err


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


def test_mail_secrets_propagate_to_a_course_org_but_not_a_cohort(monkeypatch):
    # Both workflows that send mail ("Send enrolment codes", "Distribute grades") run in
    # the COURSE org, so propagating to a cohort would only spread the mailbox credentials
    # into an org that never uses them.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.setattr(bc, "set_org_secret", lambda *a, **k: True)
    monkeypatch.setattr(bc.site, "sync_site", lambda c, o: 0)
    propagated: list[str] = []
    monkeypatch.setattr(
        bc, "propagate_mail_secrets", lambda org: propagated.append(org) or 0
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_course",
            "--org",
            "Cohort-f2026",
            "--cohort",
            "--course",
            "Course-Org",
            "--propagate-secret",
        ],
    )
    assert bc.main() == 0
    assert propagated == []

    monkeypatch.setattr(
        "sys.argv", ["bootstrap_course", "--org", "Course-Org", "--propagate-secret"]
    )
    assert bc.main() == 0
    assert propagated == ["Course-Org"]


def test_bootstrap_reds_when_a_mail_secret_write_fails(monkeypatch, capsys):
    # The propagation count used to be discarded, reporting a green bootstrap for a course
    # org that cannot send a single enrolment code or grade email.
    _stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.setattr(bc, "set_org_secret", lambda *a, **k: True)
    monkeypatch.setattr(bc, "propagate_mail_secrets", lambda org: 2)
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
    monkeypatch.setattr(bc, "seed_workflows", lambda org: 17)
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
    monkeypatch.setattr(bc.seed, "register_cohort", lambda course, cohort: False)
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
    monkeypatch.setattr(bc, "setup_cohort_extras", lambda org: 4)
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
    failure = count if failing_job == "seed_failures" else (lambda org: count)
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

    monkeypatch.setattr(seed, "discover_cohorts", boom)
    monkeypatch.setattr("sys.argv", ["seed", "refresh", "--course-org", "Course-Org"])

    assert seed.main() == 1
    assert "HTTP 502" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("job", "message"),
    [
        ("refresh_welcome_workflows", "welcome-repo files not written"),
        ("refresh_classroom_samples", "classroom-config samples not written"),
        (
            "refresh_classroom_system_files",
            "classroom-config system files not written",
        ),
    ],
    ids=["welcome-workflows", "config-samples", "classroom-system-files"],
)
def test_a_per_cohort_refresh_reds_on_a_failed_write_and_claims_nothing(
    monkeypatch, capsys, job, message
):
    # "[ok] ... up to date" used to print unconditionally, so a cohort whose onboarding
    # workflow never landed still read as fully seeded. Each per-cohort job owes the caller
    # a non-zero instead, since that is what makes the nightly Refresh go red. Now that each
    # job lands as ONE commit the answer is 1, not a per-file tally: nothing partial can
    # land, so there is no count to take.
    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: False)

    assert getattr(welcome, job)("Cohort-f2026") == 1
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

    assert welcome.refresh_classroom_system_files("Cohort-f2026") == 0
    assert {path for _, path in written} == {
        "README.md",
        ".github/workflows/dispatch-sync.yml",
        ".github/workflows/dispatch-sync-site.yml",
        ".github/workflows/validate-schedule.yml",
    }, (
        "the nightly refresh may only re-push SYSTEM-owned classroom-config files; a "
        "USER-owned file here (students.csv, teams.csv, schedule.yml, people.yml, "
        "grades/) would be overwritten from the template every night"
    )
    assert {repo for repo, _ in written} == {roster.CONFIG_REPO}
    # No path is written twice, so the count callers add up is one per file.
    assert len(written) == len(set(written))


def test_org_settings_ok_line_only_prints_when_2fa_was_set(monkeypatch, capsys):
    # The summary line claims "(2FA enforced)" - it must not print when the PATCH failed.
    monkeypatch.setattr(bc, "gh", lambda *a, **k: (1, "gh: HTTP 403"))
    bc.set_org_settings("Course-Org")
    out = capsys.readouterr()
    assert "2FA enforced" not in out.out
    assert "could not enable 2FA" in out.err

    monkeypatch.setattr(bc, "gh", lambda *a, **k: (0, ""))
    bc.set_org_settings("Course-Org")
    assert "2FA enforced" in capsys.readouterr().out
