"""scaffold create-if-absent + failure propagation.

"New materials repo" / "New assignment" re-run against the SAME tag lands on a repo
`create_repo` reports as already-existing - so the starter files (README.md, SYLLABUS.md,
starter.py/.ipynb, the section .gitkeep scaffolds) must be create-only, never overwriting
faculty content or resurrecting a deleted starter directory. A run that failed to seed the
Release buttons (or the solution branch) must report non-zero, not a green "ready".
"""

from __future__ import annotations

import pytest

from dsl_course import gh_contents, ghcli, releaseignore, scaffold


class FakeRepo:
    """The file contents a scaffold writes into, plus the skips it logs."""

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

    def put_files(self, org, repo, files, message, *, delete=(), create_only=False):
        """One commit, several files - recorded per file, so the assertions stay about
        WHICH paths a scaffold touches rather than how they were batched. create_only is
        honoured here because that is now put_files' job, not the caller's."""
        for path, content in files.items():
            if create_only and (repo, path) in self.files:
                self.skips.append(f"{repo}/{path}")
                continue
            self.put_file(org, repo, path, content, message)
        for path in delete:
            self.files.pop((repo, path), None)
            self.deletes.append((repo, path))
        return True

    def written(self, repo):
        return {path for r, path in self.writes if r == repo}


@pytest.fixture
def fake(monkeypatch):
    f = FakeRepo()
    # USER-owned scaffolds go through gh_contents.seed_if_absent /
    # put_files(create_only=True), which resolve get_file_content / put_file / put_files / log_skip in
    # the gh_contents namespace; the SYSTEM-owned pair goes through scaffold.put_files directly.
    # Fake every layer to the same recorder.
    monkeypatch.setattr(gh_contents, "get_file_content", f.get_file_content)
    monkeypatch.setattr(gh_contents, "put_file", f.put_file)
    monkeypatch.setattr(gh_contents, "put_files", f.put_files)
    monkeypatch.setattr(scaffold, "put_files", f.put_files)
    monkeypatch.setattr(gh_contents, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(scaffold, "log_skip", lambda msg: f.skips.append(msg))
    monkeypatch.setattr(scaffold, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(scaffold, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "grant_tagged_team_access", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "set_repo_topics", lambda *a, **k: None)
    monkeypatch.setattr(scaffold, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(scaffold, "discover_assignments", lambda org: [])
    monkeypatch.setattr(scaffold, "push_content_workflows", lambda *a, **k: 0)
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
        # Readings get a stub rather than a .gitkeep: the folder's files are listed
        # automatically, but an empty folder gave no sign of that, nor that this file is
        # where an online reading goes.
        "readings/01_session-1/READINGS.md",
        "labs/01_session-1/.gitkeep",
        # Seeded inert, purely so faculty find out the withhold list exists.
        ".releaseignore",
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
    # green "ready" - push_content_workflows' failure count is the exit code.
    monkeypatch.setattr(scaffold, "push_content_workflows", lambda *a, **k: 2)
    assert scaffold.scaffold_materials("Org", "f2026") == 1


def test_materials_repo_reds_when_a_user_file_seed_fails(fake, monkeypatch):
    # A USER-owned skeleton whose write FAILS must red the scaffold: the seed returns False
    # only on a real write failure, and that folds into the exit code (a mere skip of a
    # present file is a success, not a failure).
    # Both sets now go through the same create-only writer, so they are told apart by the
    # commit each makes rather than by which namespace resolves `put_files`.
    def failing(org, repo, files, message, *a, **k):
        if message.startswith("init: materials skeleton"):
            return False
        return fake.put_files(org, repo, files, message, *a, **k)

    monkeypatch.setattr(scaffold, "put_files", failing)
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
    monkeypatch.setattr(ghcli, "gh", fake_gh)
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
    monkeypatch.setattr(
        gh_contents, "put_file", lambda *a, **k: False
    )  # USER seeds fail
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
    assert deploy._is_withheld_stub("README.md", seeded)


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
    # The stub points at MAINTAINING.md for how to release it, and that is where the two
    # facts a course team gets wrong have to be found: the release path is case sensitive -
    # the trap the sample schedule used to set - and a PDF releases just as readily, which
    # is what ITDS actually uses.
    assert "MAINTAINING.md" in stub
    guide = scaffold._maintaining("Org", "course-materials-f2026")
    assert "case sensitive" in guide and "SYLLABUS.pdf" in guide


def test_no_system_file_is_ever_released_to_students():
    # A whole-repo release must not ship our example syllabus - or any other file the
    # toolkit wrote about itself - into a cohort. Asserted over the whole manifest because
    # the nightly refresh back-fills these into repos that have been running for months:
    # the exclusion is the precondition that makes creating them there safe.
    from dsl_course import deploy

    for path in scaffold.materials_system_files("Org", "course-materials-f2026"):
        assert path in deploy.ROOT_RELEASE_EXCLUDED, path


def test_a_stub_faculty_have_written_over_is_never_touched_again(fake):
    # Create-only, so a re-run cannot revert their work whatever they left in the file -
    # they do not have to have removed any marker to be safe.
    mine = "# Machine Learning - syllabus\n\nWritten by faculty.\n"
    fake.files[("course-materials-f2026", "SYLLABUS.md")] = mine
    fake.files[("course-materials-f2026", "readings/01_session-1/READINGS.md")] = (
        "- Mine\n"
    )

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    assert fake.files[("course-materials-f2026", "SYLLABUS.md")] == mine
    assert (
        fake.files[("course-materials-f2026", "readings/01_session-1/READINGS.md")]
        == "- Mine\n"
    )
    assert "course-materials-f2026/SYLLABUS.md" in fake.skips


def test_refresh_backfills_the_system_files_into_a_materials_repo(monkeypatch):
    # The gap this closes: both files are SYSTEM-owned - meant to be rewritten whenever the
    # toolkit changes them - but were only ever written by the scaffold, which made that
    # true of new repos and nothing else. This CREATES, because back-filling a file added
    # after the repo was made is the point; hence the name gate, since the nightly sweep
    # also hands us the code and dataset repos.
    f = FakeRepo()
    monkeypatch.setattr(scaffold, "put_files", f.put_files)

    assert scaffold.refresh_materials_system_files("Org", "course-materials-f2026") == 0
    assert scaffold.refresh_materials_system_files("Org", "lecture-code-f2026") == 0

    assert f.written("course-materials-f2026") == {
        "MAINTAINING.md",
        "SYLLABUS.md.sample",
    }
    assert f.written("lecture-code-f2026") == set()


def test_refresh_rewrites_a_stale_system_file(monkeypatch):
    # SYSTEM-owned means the toolkit's copy wins - what the file's own text tells faculty
    # ("kept current by the toolkit - copy from it, do not edit it").
    f = FakeRepo()
    monkeypatch.setattr(scaffold, "put_files", f.put_files)
    f.files[("course-materials-f2026", "MAINTAINING.md")] = "# stale guide\n"

    assert scaffold.refresh_materials_system_files("Org", "course-materials-f2026") == 0
    assert (
        "Reference for faculty & instructors"
        in f.files[("course-materials-f2026", "MAINTAINING.md")]
    )


def test_refresh_reds_when_a_system_file_cannot_be_written(monkeypatch):
    # This runs on a nightly cron, so a write that failed silently would leave an org
    # unconverged with a green run to say otherwise.
    monkeypatch.setattr(scaffold, "put_files", lambda *a, **k: False)
    assert scaffold.refresh_materials_system_files("Org", "course-materials-f2026") == 1


def test_no_cron_path_can_rewrite_an_instructor_owned_file(fake):
    """The whole ownership rule, in one test.

    Every instructor-owned file in the skeleton is CREATE-ONLY: whatever faculty leave in
    one, no nightly path rewrites it. Asserted over the real `seed.refresh` write surface,
    because the bug this replaced was a refresh that looked at a marker and guessed
    wrong - a withhold list edited by APPENDING kept its marker, read as untouched, and
    was overwritten, so patterns vanished and withheld files shipped again."""
    mine = {
        "SYLLABUS.md": "# My syllabus\n",
        "readings/01_session-1/READINGS.md": "- Blitzstein, ch. 1.\n",
        # Appended under the seeded comments, which is how a withhold list is edited.
        releaseignore.RELEASEIGNORE: scaffold._RELEASEIGNORE_STUB
        + "\n**/solutions.ipynb\n",
        "README.md": "# My course\n",
    }
    for path, body in mine.items():
        fake.files[("course-materials-f2026", path)] = body

    assert scaffold.scaffold_materials("Org", "f2026") == 0
    assert scaffold.refresh_materials_system_files("Org", "course-materials-f2026") == 0

    for path, body in mine.items():
        assert fake.files[("course-materials-f2026", path)] == body, path


def test_the_seeded_releaseignore_withholds_nothing(tmp_path):
    """Every line a comment. A stub that shipped one LIVE pattern would silently withhold
    that path from every course scaffolded after it - green runs, missing material, and
    nothing to connect the two. Asserted against the real matcher, not by reading the
    text, so an accidentally-uncommented line fails here."""
    body = scaffold._RELEASEIGNORE_STUB
    (tmp_path / releaseignore.RELEASEIGNORE).write_text(body)
    (tmp_path / "solutions.ipynb").write_text("")
    (tmp_path / "drafts").mkdir()
    deny = releaseignore.deny_for(tmp_path)
    # `.releaseignore` itself is always withheld; nothing else is.
    assert deny(str(tmp_path), ["solutions.ipynb", "drafts"]) == set()


def _site_gh(monkeypatch, pages_post, pages_put, env_put=(0, ""), seeded=None):
    """Drive scaffold_site's three org-level calls; the deploy dispatch never fires."""

    def fake_put_files(org, repo, files, message, **k):
        if seeded is not None:
            seeded.update(files)
        return True

    def fake_gh(*args, **k):
        path = next((a for a in args if a.startswith("repos/")), "")
        if path.endswith("/pages"):
            return pages_post if "POST" in args else pages_put
        if path.endswith("/environments/github-pages"):
            return env_put
        return (1, "")

    monkeypatch.setattr(scaffold, "gh", fake_gh)
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(scaffold, "put_files", fake_put_files)
    monkeypatch.setattr(scaffold, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(scaffold, "_dispatch_deploy", lambda org, site: None)


def test_site_scaffold_reds_when_pages_could_not_be_enabled(monkeypatch, capsys):
    # Both calls failing means the repo serves nothing at all, yet the PUT's return was
    # dropped and the scaffold went on to log "site scaffolded -> https://...".
    _site_gh(monkeypatch, pages_post=(1, "HTTP 422"), pages_put=(1, "HTTP 403"))
    assert scaffold.scaffold_site("Org") == 1
    out = capsys.readouterr()
    assert "could not enable Pages" in out.err
    assert "site scaffolded" not in out.out


def test_site_scaffold_accepts_the_put_fallback(monkeypatch):
    _site_gh(monkeypatch, pages_post=(1, "HTTP 422"), pages_put=(0, ""))
    assert scaffold.scaffold_site("Org") == 0


def test_an_already_enabled_pages_site_is_not_a_failure(monkeypatch):
    _site_gh(monkeypatch, pages_post=(1, "HTTP 409"), pages_put=(1, "never called"))
    assert scaffold.scaffold_site("Org") == 0


def test_a_new_site_repo_is_created_empty_and_seeded_with_its_pages_build(monkeypatch):
    # There is no template repo any more: the site is created empty and the first sync
    # writes it. Only the Pages build has to land first - the two calls after it enable
    # Pages on that workflow and dispatch it, and neither works on a repo with no branch.
    seeded: dict = {}
    created: list = []
    _site_gh(monkeypatch, pages_post=(0, ""), pages_put=(0, ""), seeded=seeded)
    monkeypatch.setattr(scaffold, "repo_exists", lambda org, name: False)
    monkeypatch.setattr(
        scaffold,
        "create_repo",
        lambda org, name, **k: created.append((org, name, k)) or True,
    )

    assert scaffold.scaffold_site("Org") == 0
    assert created[0][:2] == ("Org", "org.github.io")
    assert created[0][2]["private"] is False
    assert list(seeded) == [".github/workflows/deploy.yml"]
    assert b"actions/deploy-pages@" in seeded[".github/workflows/deploy.yml"]


def test_site_scaffold_reds_when_the_pages_build_could_not_be_seeded(
    monkeypatch, capsys
):
    # An empty repo is not a site. Going on to enable Pages on it would report a scaffold
    # that serves nothing and has no workflow for the sync's first push to run.
    _site_gh(monkeypatch, pages_post=(0, ""), pages_put=(0, ""))
    monkeypatch.setattr(scaffold, "put_files", lambda *a, **k: False)

    assert scaffold.scaffold_site("Org") == 1
    assert "could not seed the Pages build" in capsys.readouterr().err


def test_a_failed_branch_policy_clear_is_reported(monkeypatch, capsys):
    # Not fatal - Pages is on - but silently dropping it is what makes a sync-site push
    # from a non-default branch deploy nothing.
    _site_gh(
        monkeypatch, pages_post=(0, ""), pages_put=(0, ""), env_put=(1, "HTTP 404")
    )
    assert scaffold.scaffold_site("Org") == 0
    assert "github-pages branch policy" in capsys.readouterr().err
