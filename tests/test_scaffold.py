"""scaffold create-if-absent + failure propagation.

"New materials repo" / "New assignment" re-run against the SAME tag lands on a repo
`create_repo` reports as already-existing - so the starter files (README.md, SYLLABUS.md,
starter.py/.ipynb, the section .gitkeep scaffolds) must be create-only, never overwriting
faculty content or resurrecting a deleted starter directory. A run that failed to seed the
Release buttons (or the solution branch) must report non-zero, not a green "ready".
"""

from __future__ import annotations

import pytest

from dsl_course import scaffold, seed, utils


class FakeRepo:
    """The file contents a scaffold writes into, plus the skips it logs."""

    def __init__(self, existing: dict[tuple[str, str], str] | None = None):
        self.files: dict[tuple[str, str], str] = dict(existing or {})
        self.writes: list[tuple[str, str]] = []
        self.skips: list[str] = []

    def get_file_content(self, org, repo, path):
        return self.files.get((repo, path))

    def put_file(self, org, repo, path, content, message):
        self.files[(repo, path)] = content.decode()
        self.writes.append((repo, path))
        return True

    def put_files(self, org, repo, files, message, *, delete=(), create_only=False):
        """One commit, several files - recorded per file, so the assertions stay about
        WHICH paths a scaffold touches rather than how they were batched. create_only is
        honoured here because that is now put_files' job, not the caller's."""
        for path, content in files.items():
            if create_only and (repo, path) in self.files:
                self.skips.append(f"{repo}/{path}")
                continue
            self.put_file(org, repo, path, content, message)
        return True

    def written(self, repo):
        return {path for r, path in self.writes if r == repo}


@pytest.fixture
def fake(monkeypatch):
    f = FakeRepo()
    # USER-owned scaffolds go through utils.seed_if_absent / seed_files_if_absent
    # (create-if-absent), which resolve get_file_content / put_file / put_files / log_skip in
    # the utils namespace; SYSTEM-owned MAINTAINING.md is written by scaffold.put_file
    # directly. Fake every layer to the same recorder.
    monkeypatch.setattr(utils, "get_file_content", f.get_file_content)
    monkeypatch.setattr(utils, "put_file", f.put_file)
    monkeypatch.setattr(utils, "put_files", f.put_files)
    monkeypatch.setattr(scaffold, "put_file", f.put_file)
    monkeypatch.setattr(utils, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(scaffold, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(scaffold, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "grant_course_team_access", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "grant_tagged_team_access", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "set_repo_topics", lambda *a, **k: None)
    monkeypatch.setattr(seed, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: [])
    monkeypatch.setattr(seed, "_push_workflows", lambda *a, **k: 0)
    return f


# --------------------------------------------------------------- materials scaffold


def test_fresh_materials_repo_gets_the_full_skeleton(fake):
    assert scaffold.scaffold_materials("Org", "f2026") == 0
    assert fake.written("course-materials-f2026") == {
        "README.md",
        "MAINTAINING.md",
        "SYLLABUS.md",
        # The filled example beside the stub, on the repo's `<file>.sample` convention.
        # SYSTEM-owned like MAINTAINING.md, so a repo scaffolded before it existed picks it
        # up on the next Refresh.
        "SYLLABUS.md.sample",
        "lectures/01_session-1/.gitkeep",
        # Readings get a stub rather than a .gitkeep: a text file in here IS the reading
        # list published on the cohort site, and an empty folder gave no sign of that.
        "readings/01_session-1/reading.md",
        "labs/01_session-1/.gitkeep",
    }
    assert fake.skips == []


def test_rerun_never_overwrites_a_faculty_authored_readme(fake):
    # The live hazard: Release materials copies README.md to students, so a re-run reverting
    # it to the stub silently republishes the placeholder over the faculty's real overview.
    edited = "# Real course overview\n\nWritten by faculty for students.\n"
    fake.files[("course-materials-f2026", "README.md")] = edited

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    assert fake.files[("course-materials-f2026", "README.md")] == edited
    assert "README.md" not in fake.written("course-materials-f2026")
    assert "course-materials-f2026/README.md" in fake.skips


def test_rerun_does_not_resurrect_a_deleted_section_directory(fake):
    # Faculty deleted labs/ (no labs this year). A re-run must not re-create its .gitkeep,
    # which would resurrect the directory. The other absent scaffolds are still seeded.
    fake.files[("course-materials-f2026", "labs/01_session-1/.gitkeep")] = ""

    scaffold.scaffold_materials("Org", "f2026")
    assert "labs/01_session-1/.gitkeep" not in fake.written("course-materials-f2026")
    assert "lectures/01_session-1/.gitkeep" in fake.written("course-materials-f2026")


def test_maintaining_refreshes_on_rerun_while_readme_stays_create_only(fake):
    # MAINTAINING.md is SYSTEM-owned generated docs (built from the actions table): a re-run
    # must refresh it so a toolkit change reaches the repo. README.md beside it is USER-owned
    # and stays create-only, so a faculty-authored README is never clobbered.
    stale = "# stale maintainer guide\n"
    overview = "# faculty overview\n"
    fake.files[("course-materials-f2026", "MAINTAINING.md")] = stale
    fake.files[("course-materials-f2026", "README.md")] = overview

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    # MAINTAINING.md re-written (refreshed from the template), README.md left as faculty had it.
    assert "MAINTAINING.md" in fake.written("course-materials-f2026")
    assert fake.files[("course-materials-f2026", "MAINTAINING.md")] != stale
    assert "README.md" not in fake.written("course-materials-f2026")
    assert fake.files[("course-materials-f2026", "README.md")] == overview
    assert "course-materials-f2026/README.md" in fake.skips


def test_materials_repo_reports_non_zero_when_release_buttons_do_not_seed(
    fake, monkeypatch
):
    # A materials repo with no Release buttons (workflow writes failed) must not report a
    # green "ready" - _push_workflows' failure count is the exit code.
    monkeypatch.setattr(seed, "_push_workflows", lambda *a, **k: 2)
    assert scaffold.scaffold_materials("Org", "f2026") == 1


def test_materials_repo_reds_when_a_user_file_seed_fails(fake, monkeypatch):
    # A USER-owned skeleton whose write FAILS must red the scaffold: the seed returns False
    # only on a real write failure, and that folds into the exit code (a mere skip of a
    # present file is a success, not a failure).
    monkeypatch.setattr(utils, "put_files", lambda *a, **k: False)  # USER seeds fail
    monkeypatch.setattr(scaffold, "put_file", lambda *a, **k: True)  # MAINTAINING ok
    assert scaffold.scaffold_materials("Org", "f2026") == 1


# -------------------------------------------------------------- assignment scaffold


def _clone_ok(monkeypatch, git_fake):
    """gh clone materialises an empty work dir; git behaviour is the caller's fake."""
    import pathlib

    def fake_gh(*args, **k):
        if args[:2] == ("repo", "clone"):
            pathlib.Path(args[3]).mkdir(parents=True, exist_ok=True)
            return (0, "")
        return (0, "")

    monkeypatch.setattr(scaffold, "gh", fake_gh)
    monkeypatch.setattr(scaffold, "git", git_fake)


def _git_ok(*args):
    """Every git call succeeds, and `ls-remote --exit-code` reports NO remote solution
    branch (exit 2) - the fresh-assignment case."""
    return (2, "") if "ls-remote" in args else (0, "")


def test_fresh_assignment_seeds_the_starter(fake, monkeypatch):
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0
    assert {"README.md", "starter.py"} <= fake.written("assignment-1-f2026")


def test_rerun_never_overwrites_an_authored_assignment_starter(fake, monkeypatch):
    _clone_ok(monkeypatch, _git_ok)
    authored = '"""Assignment 1."""\n\n\ndef solve():\n    return real_work()\n'
    fake.files[("assignment-1-f2026", "starter.py")] = authored

    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0
    assert fake.files[("assignment-1-f2026", "starter.py")] == authored
    assert "starter.py" not in fake.written("assignment-1-f2026")
    assert "assignment-1-f2026/starter.py" in fake.skips


def test_assignment_reds_when_a_starter_seed_fails(fake, monkeypatch):
    # A failed create-only write of the starter/README (not a skip of a live file) must red
    # the assignment scaffold, matching scaffold_materials - a half-written template is not
    # a green "ready".
    _clone_ok(monkeypatch, _git_ok)
    monkeypatch.setattr(utils, "put_file", lambda *a, **k: False)  # USER seeds fail
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 1


def test_assignment_reports_a_failed_solution_branch_checkout(
    fake, monkeypatch, capsys
):
    # A failed local `git checkout -b solution` must be reported, not swallowed and then
    # misreported as a push failure further down.
    def git_fake(*args):
        if "ls-remote" in args:
            return (2, "")
        return (1, "") if "checkout" in args else (0, "")

    _clone_ok(monkeypatch, git_fake)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 1
    assert "solution branch" in capsys.readouterr().err


def test_assignment_refuses_to_rebuild_an_existing_solution_branch(
    fake, monkeypatch, capsys
):
    # The clone is FRESH, so no LOCAL solution branch exists and `checkout -b` would
    # succeed even when the remote already carries a faculty-authored solution branch -
    # the run then died at the push with a misleading error. Probe the remote first:
    # ls-remote --exit-code exits 0 when the branch is there, and we refuse outright.
    pushed: list[tuple] = []

    def git_fake(*args):
        if "ls-remote" in args:
            return (0, "abc123\trefs/heads/solution")
        if "push" in args:
            pushed.append(args)
        return (0, "")

    _clone_ok(monkeypatch, git_fake)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 1
    assert "already exists" in capsys.readouterr().err
    assert pushed == []


def test_an_unrelated_feature_solution_branch_does_not_block_the_scaffold(
    fake, monkeypatch
):
    # ls-remote patterns TAIL-match: a bare `solution` pattern matches
    # refs/heads/feature/solution too, so a faculty working branch used to make the
    # scaffold refuse an assignment that has no model solution at all. Probe the FULL ref.
    heads = ["refs/heads/main", "refs/heads/feature/solution"]

    def git_fake(*args):
        if "ls-remote" in args:
            pattern = args[-1]  # git's own tail-matching, reproduced
            hits = [h for h in heads if h == pattern or h.endswith(f"/{pattern}")]
            return (0, "\n".join(hits)) if hits else (2, "")
        return (0, "")

    _clone_ok(monkeypatch, git_fake)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0


def test_the_seeded_readme_would_be_withheld_from_a_release(fake):
    # The end-to-end coupling: the file scaffold actually writes must trip deploy's guard,
    # so an unedited placeholder cannot reach students as their course overview. If the
    # stub's wording is ever edited without the sentinel, this fails here rather than
    # silently on a live release.
    from dsl_course import deploy

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    seeded = fake.files[("course-materials-f2026", "README.md")]
    assert deploy._is_unedited_readme("README.md", seeded)


def test_the_syllabus_stub_is_faculty_owned_and_the_sample_is_refreshed(fake):
    # The stub is the faculty's own document, so a re-run must not revert it; the filled
    # example beside it is ours, so a re-run MUST refresh it - that is how a course
    # scaffolded before it existed gets one.
    written = "# Real syllabus\n\nBy faculty.\n"
    fake.files[("course-materials-f2026", "SYLLABUS.md")] = written
    fake.files[("course-materials-f2026", "SYLLABUS.md.sample")] = "# stale example\n"

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    assert fake.files[("course-materials-f2026", "SYLLABUS.md")] == written
    assert "SYLLABUS.md" not in fake.written("course-materials-f2026")
    assert "SYLLABUS.md.sample" in fake.written("course-materials-f2026")
    assert (
        fake.files[("course-materials-f2026", "SYLLABUS.md.sample")]
        != "# stale example\n"
    )


def test_the_syllabus_stub_carries_the_standard_sections(fake):
    assert scaffold.scaffold_materials("Org", "f2026") == 0
    stub = fake.files[("course-materials-f2026", "SYLLABUS.md")]
    for heading in (
        "## 1. General information",
        "## 2. Course contents and learning objectives",
        "### Prerequisites",
        "## 3. Grading and assignments",
        "## 4. General readings",
        "## 5. Course sessions and readings",
    ):
        assert heading in stub
    # It must say that the name, and its capitalisation, is what releases it - the trap the
    # sample schedule used to set - and that a PDF releases just as readily, which is what
    # ITDS actually uses.
    assert "capitalisation" in stub and "SYLLABUS.pdf" in stub


def test_the_syllabus_sample_is_never_released_to_students():
    # A whole-repo release must not ship our example syllabus into a cohort.
    from dsl_course import deploy

    assert "SYLLABUS.md.sample" in deploy.ROOT_RELEASE_EXCLUDED
