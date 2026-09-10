"""scaffold create-if-absent + failure propagation.

"New materials repo" / "New assignment" re-run against the SAME tag lands on a repo
`create_repo` reports as already-existing - so the starter files (README.md, SYLLABUS.md,
starter.py/.ipynb, the section .gitkeep scaffolds) must be create-only, never overwriting
faculty content or resurrecting a deleted starter directory. A run that failed to seed the
Release buttons (or the solution branch) must report non-zero, not a green "ready".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dsl_course import derive, gh_contents, ghcli, grades, releaseignore, scaffold


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


def _solution_files(monkeypatch) -> dict[str, str]:
    """What the scaffold writes onto the solution branch, by path relative to the clone.

    The solution branch is built in a real temp checkout and pushed with git, so the only
    seam is the work dir itself - captured here at the moment `git add -A` runs."""
    written: dict[str, str] = {}

    def git_fake(*args):
        if "ls-remote" in args:
            return (2, "")
        if "add" in args:
            wd = Path(args[1])
            written.clear()
            written.update(
                {
                    str(f.relative_to(wd)): f.read_text()
                    for f in wd.rglob("*")
                    if f.is_file()
                }
            )
        return (0, "")

    _clone_ok(monkeypatch, git_fake)
    return written


def test_fresh_assignment_seeds_the_starter(fake, monkeypatch):
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0
    assert {"README.md", "starter.py"} <= fake.written("assignment-1-f2026")


def test_the_markup_starters_are_valid_documents_of_their_own_format(fake, monkeypatch):
    # A `.tex` holding Markdown does not compile, and an .Rmd/.qmd without front matter
    # does not knit - a stub the student has to repair is worse than no stub at all.
    _clone_ok(monkeypatch, _git_ok)
    for number, fmt in (("1", "latex"), ("2", "rmd"), ("3", "qmd")):
        assert (
            scaffold.scaffold_assignment("Org", number, "f2026", fmt, name="Backprop")
            == 0
        )

    tex = fake.files[("assignment-1-f2026", "starter.tex")]
    # Everything before the preamble is a `%` comment, so the file still compiles.
    body = [line for line in tex.splitlines() if not line.startswith("%")]
    assert body[0] == "\\documentclass[11pt,a4paper]{article}"
    assert "\\title{Backprop}" in tex and "\\maketitle" in tex
    assert "\\section{Task}" in tex
    assert tex.rstrip().endswith("\\end{document}")

    for number, fmt, path, output_key, verb in (
        ("2", "rmd", "starter.Rmd", "output", "Knit"),
        ("3", "qmd", "starter.qmd", "format", "Render"),
    ):
        doc = fake.files[(f"assignment-{number}-f2026", path)]
        front = yaml.safe_load(doc.split("---\n")[1])
        assert front["title"] == "Backprop" and output_key in front
        assert "## Task" in doc and "```{r}" in doc
        # The graded artefact, named in the file the student opens - and in the SAME
        # sentence the brief carries, because both read `_hand_in`.
        assert f"{verb} `{path}` and commit **both** it and the `starter.html`" in doc
        assert scaffold._hand_in(fmt) in doc


def test_a_title_that_is_tex_syntax_still_compiles(fake, monkeypatch):
    # `R&D`, `100% coverage`, `train_test_split`: unescaped, an `&` is an alignment tab, a
    # `%` comments out the rest of the line and an `_` is a maths subscript - so the stub
    # whose whole promise is "this compiles unedited" would not.
    _clone_ok(monkeypatch, _git_ok)
    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", "latex", name="R&D: 100% train_test_split"
        )
        == 0
    )
    tex = fake.files[("assignment-1-f2026", "starter.tex")]
    assert "\\title{R\\&D: 100\\% train\\_test\\_split}" in tex


def test_a_title_with_a_colon_or_a_quote_stays_one_yaml_scalar(fake, monkeypatch):
    # Front matter is YAML: an unquoted `Lab 3: k-means` makes that line a nested mapping
    # and knitr reads no title at all, and a bare `"` inside a quoted scalar ends it.
    _clone_ok(monkeypatch, _git_ok)
    title = 'Lab 3: the "hello world" of k-means'
    assert scaffold.scaffold_assignment("Org", "2", "f2026", "rmd", name=title) == 0
    doc = fake.files[("assignment-2-f2026", "starter.Rmd")]
    assert yaml.safe_load(doc.split("---\n")[1])["title"] == title


def test_the_notebook_starter_runs_carries_the_rule_and_names_its_language(
    fake, monkeypatch
):
    # Three things at once, because all three are contracts: it is valid nbformat (it opens),
    # its first cell states the restart-and-run-all rule the completion check verifies, and
    # `language_info.file_extension` is `.py` so the grader's nbconvert names its script
    # starter.py (see collect._stray_conversion).
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", "ipynb", name="MLP") == 0
    nb = json.loads(fake.files[("assignment-1-f2026", "starter.ipynb")])
    assert nb["nbformat"] == 4
    assert nb["metadata"]["language_info"]["file_extension"] == ".py"
    first = "".join(nb["cells"][0]["source"])
    assert first.startswith("# MLP\n")
    assert "Restart the kernel and run all cells" in first
    assert "raise NotImplementedError" in "".join(nb["cells"][1]["source"])


def test_the_python_starter_survives_a_title_a_docstring_could_not_hold(
    fake, monkeypatch
):
    # The title goes into a module docstring, so a `"""` or a trailing quote in it would
    # end the docstring early and seed a file that does not even parse.
    _clone_ok(monkeypatch, _git_ok)
    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", "py", name='The """quoted""" one"'
        )
        == 0
    )
    starter = fake.files[("assignment-1-f2026", "starter.py")]
    compile(starter, "starter.py", "exec")  # raises if the escaping slipped


@pytest.mark.parametrize(
    "fmt, says",
    [
        ("ipynb", "Restart kernel and run all"),
        ("py", "Commit your `.py` files"),
        ("rmd", "Knit `starter.Rmd`"),
        ("qmd", "Render `starter.qmd`"),
        ("latex", "Compile `starter.tex`"),
    ],
)
def test_the_brief_names_the_artefact_this_format_hands_in(
    fake, monkeypatch, fmt, says
):
    # THE convention: the built artefact is committed beside its source, and the built
    # artefact is what a grader reads. Left to the author, a brief collects .Rmd files
    # nobody can mark - so the stub says it, whatever else the author writes.
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", fmt, name="A") == 0
    brief = fake.files[("assignment-1-f2026", "README.md")]
    assert says in brief
    assert "_Say which files you expect back" in brief  # still a stub


def test_a_raw_repo_brief_claims_no_artefact(fake, monkeypatch):
    # `none` seeds no starter, so there is no `starter.*` to name - and a sentence about
    # committing one would be a rule the repo cannot keep.
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", "none") == 0
    brief = fake.files[("assignment-1-f2026", "README.md")]
    assert "starter" not in brief
    assert "## What to submit\n\n_Say which files you expect back" in brief


def test_the_brief_stub_has_the_two_headings_and_no_more(fake, monkeypatch):
    # The page students read. Two headings, both empty: seeding a plausible-looking brief
    # is how a placeholder ships as the assignment.
    _clone_ok(monkeypatch, _git_ok)
    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", name="Neural networks from scratch"
        )
        == 0
    )
    brief = fake.files[("assignment-1-f2026", "README.md")]
    assert brief.startswith("# Neural networks from scratch\n")
    assert "## Task" in brief and "## What to submit" in brief
    assert "**Points:** __" in brief and "**Due:** see the course schedule" in brief


def test_a_format_of_none_seeds_the_brief_and_nothing_else(fake, monkeypatch):
    # The raw-repo option: grading reads whatever is in the repo, so an assignment that
    # wants no starter gets none rather than a stub its students must delete.
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", "none") == 0
    assert fake.written("assignment-1-f2026") == {"README.md"}


def test_a_group_assignment_seeds_contributions_and_an_individual_one_does_not(
    fake, monkeypatch
):
    # CONTRIBUTIONS.md is read at the pin into the grading sheet, and carries the stub
    # mark so a team that never wrote it reads as "(not filled in)" rather than blank.
    _clone_ok(monkeypatch, _git_ok)
    assert scaffold.scaffold_assignment("Org", "4", "f2026", "none", "group") == 0
    seeded = fake.files[("assignment-4-f2026", "CONTRIBUTIONS.md")]
    assert gh_contents.is_untouched_stub(seeded)
    assert scaffold.scaffold_assignment("Org", "5", "f2026", "none") == 0
    assert "CONTRIBUTIONS.md" not in fake.written("assignment-5-f2026")


def test_hidden_tests_are_seeded_only_when_the_assignment_asked_to_be_autograded(
    fake, monkeypatch
):
    # `tests/` beside a hand-marked assignment reads as work the course owes, and
    # `autograde: true` over a directory of placeholders is a machine score nobody meant.
    written = _solution_files(monkeypatch)
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0
    assert not [f for f in written if f.startswith("tests/")]
    assert scaffold.scaffold_assignment("Org", "2", "f2026", autograde=True) == 0
    assert "tests/test_solution.py" in written


def test_the_generated_definition_carries_the_answers_and_the_course_defaults(
    fake, monkeypatch
):
    # The whole point of the eight boxes: what the button was asked lands in the file the
    # handout, the sheet and the Join-team form all read, over the course's own defaults.
    written = _solution_files(monkeypatch)
    monkeypatch.setattr(
        scaffold,
        "course_assignment_defaults",
        lambda org: {
            "max_team_size": 3,
            "late_window_days": 7,
            "late_penalty_per_day": "10%",
        },
    )
    assert (
        scaffold.scaffold_assignment(
            "Org",
            "1",
            "f2026",
            "ipynb",
            "group",
            name="Neural networks: from scratch",
            team_formation="assigned",
            submit_via="external",
            autograde=True,
        )
        == 0
    )
    spec = grades.parse_grading_spec(written["grading_config.yml"])
    assert spec.dropped == ()
    assert (
        spec.title == "Neural networks: from scratch"
    )  # the colon survives the round trip
    assert (spec.type, spec.team_formation, spec.max_team_size) == (
        "group",
        "assigned",
        3,
    )
    assert (spec.submit_via, spec.format, spec.autograde) == ("external", "ipynb", True)
    assert (spec.late_window_days, spec.late_penalty_per_day) == (7, "10%")
    assert written["grading_config.yml"].startswith("# INSTRUCTOR-OWNED")


def test_the_model_answer_is_seeded_where_derive_reads_it(fake, monkeypatch):
    # `derive` writes `solution/X` onto `main` as `X`. A model answer seeded as
    # `solution/solution.ipynb` therefore derived a SECOND notebook beside the untouched
    # `starter.ipynb` - two files, the derived one named "solution", and the hidden tests
    # still importing the stub. One stem on both branches.
    written = _solution_files(monkeypatch)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", "ipynb") == 0
    assert "solution/starter.ipynb" in written
    assert derive.student_path("solution/starter.ipynb") == "starter.ipynb"
    assert scaffold.scaffold_assignment("Org", "2", "f2026", "py") == 0
    assert "solution/starter.py" in written
    # ...and it is FENCED, so pressing the button on a fresh template derives a starter
    # rather than refusing one: an unfenced seed would derive the model answer itself.
    seeded = derive.strip_source("solution/starter.py", written["solution/starter.py"])
    assert seeded.replaced == 1 and "return 42" not in seeded.text


@pytest.mark.parametrize("fmt, name", [("rmd", "starter.Rmd"), ("qmd", "starter.qmd")])
def test_the_model_answer_is_seeded_in_the_format_the_template_uses(
    fake, monkeypatch, fmt, name
):
    # It was always `starter.py`, whatever the assignment's format - so Derive on a fresh
    # Rmd template wrote a `starter.py` onto `main` BESIDE the untouched `starter.Rmd`
    # rather than becoming it. Same stem and same suffix on both branches.
    written = _solution_files(monkeypatch)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", fmt) == 0

    assert f"solution/{name}" in written
    assert "solution/starter.py" not in written
    # ...and it is fenced in the vocabulary this format speaks, so the button derives a
    # starter from it rather than refusing one.
    seeded = derive.strip_source(f"solution/{name}", written[f"solution/{name}"])
    assert seeded.replaced == 1 and "42" not in seeded.text


@pytest.mark.parametrize("fmt", ["latex", "none"])
def test_a_format_derive_cannot_read_is_seeded_no_model_answer(fake, monkeypatch, fmt):
    # `.tex` is not derivable and `none` has no starter to become, so a stub either way
    # could only ever be a file the button refuses.
    written = _solution_files(monkeypatch)
    assert scaffold.scaffold_assignment("Org", "1", "f2026", fmt) == 0

    assert [path for path in written if path.startswith("solution/")] == [
        "solution/README.md"
    ]


def test_the_cutoff_switches_are_written_out_with_their_defaults(fake, monkeypatch):
    # The file teaches the whole vocabulary, so every switch the cutoff reads is on the
    # page with the value it would have had anyway - a faculty member flips a `false`
    # rather than having to learn a key name from the docs. `completion_check` follows
    # `format:`; `grader_pdf` is off for everything until someone fences the questions.
    written = _solution_files(monkeypatch)
    for fmt, notebook in (("ipynb", True), ("py", False)):
        assert scaffold.scaffold_assignment("Org", "1", "f2026", fmt) == 0
        text = written["grading_config.yml"]
        spec = grades.parse_grading_spec(text)
        assert spec.dropped == ()
        assert (spec.completion_check, spec.grader_pdf) == (notebook, False)
        assert f"completion_check: {str(notebook).lower()}" in text
        assert "grader_pdf: false" in text


def test_a_course_with_no_defaults_gets_the_settings_commented_out(fake, monkeypatch):
    # Nothing is asserted on the course's behalf: the file teaches the whole vocabulary,
    # and a late window nobody declared stays a comment rather than becoming a policy.
    written = _solution_files(monkeypatch)
    monkeypatch.setattr(scaffold, "course_assignment_defaults", lambda org: {})
    assert scaffold.scaffold_assignment("Org", "1", "f2026") == 0
    spec = grades.parse_grading_spec(written["grading_config.yml"])
    assert (spec.late_window_days, spec.late_penalty_per_day) == (None, None)
    assert spec.max_team_size is None
    assert "# late_window_days:" in written["grading_config.yml"]


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
    monkeypatch.setattr(scaffold, "put_files", lambda *a, **k: False)  # USER seeds fail
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


# ------------------------------------------------------------------- copy_from

# Against real repositories - a bare origin per repo, cloned exactly as the scaffold
# clones one - because everything copy_from promises is a fact about git: that both
# branches arrive, that the history comes with them, and that an instructor's own file
# lands byte for byte. Only `gh` is faked (into a local `git clone`).

_ID = ghcli.GIT_ENV


def _git(*args: str) -> str:
    code, out = ghcli.git(*args)
    assert code == 0, f"`git {' '.join(args)}` failed: {out}"
    return out


class Origins:
    """A course org's repos, as bare repositories on disk."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "origins").mkdir(parents=True, exist_ok=True)
        self._scratch = 0

    def bare(self, name: str) -> Path:
        path = self.root / "origins" / f"{name}.git"
        if not path.exists():
            _git("init", "-q", "--bare", "-b", "main", str(path))
        return path

    def commit(self, name: str, files: dict[str, str], branch: str = "main") -> None:
        """The FIRST commit on `branch` of a bare repo, through a throwaway clone - the
        only way to write into a repo with no working tree. A branch beyond the first is
        cut from what the clone checked out, which is how a `solution` starts life."""
        self._scratch += 1
        work = self.root / "scratch" / f"{name}{self._scratch}"
        _git("clone", "-q", str(self.bare(name)), str(work))
        if ghcli.git("-C", str(work), "rev-parse", "--verify", "-q", "HEAD")[0] == 0:
            _git("-C", str(work), "checkout", "-q", "-b", branch)
        for rel, text in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git("-C", str(work), "add", "-A")
        _git(
            "-C", str(work), *_ID, "commit", "-q", "--no-verify", "-m", f"add {branch}"
        )
        _git("-C", str(work), *_ID, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

    def branches(self, name: str) -> list[str]:
        listed = _git(
            "--git-dir",
            str(self.bare(name)),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        )
        return sorted(listed.split())

    def files(self, name: str, branch: str = "main") -> list[str]:
        listed = _git(
            "--git-dir", str(self.bare(name)), "ls-tree", "-r", "--name-only", branch
        )
        return sorted(listed.split())

    def read(self, name: str, path: str, branch: str = "main") -> str:
        return _git("--git-dir", str(self.bare(name)), "show", f"{branch}:{path}")

    def subjects(self, name: str, branch: str = "main") -> list[str]:
        listed = _git("--git-dir", str(self.bare(name)), "log", "--format=%s", branch)
        return listed.splitlines()


@pytest.fixture
def origins(fake, tmp_path, monkeypatch) -> Origins:
    """`fake`, with `gh repo clone` answered from local bare repos and `create_repo`
    opening one. The Contents-API writes stay on the recorder, so a test can say both what
    was copied and what was written over the copy."""
    world = Origins(tmp_path)

    def clone_only(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            name = args[2].split("/", 1)[1]
            return ghcli.git("clone", "-q", str(world.bare(name)), str(args[3]))
        return 0, ""

    monkeypatch.setattr(ghcli, "gh", clone_only)
    monkeypatch.setattr(
        scaffold, "create_repo", lambda org, repo, **k: bool(world.bare(repo))
    )
    return world


def test_a_copied_materials_repo_arrives_whole_and_keeps_its_history(origins):
    # What copy_from is for: next year starts as this year, so the instructor edits the
    # lecture rather than re-typing it - and the file arrives byte for byte, with the
    # commit that explains it.
    origins.commit(
        "course-materials-f2025",
        {
            "README.md": "# Data science, 2025\n",
            "lectures/01_session-1/slides.md": "week one\n",
            ".releaseignore": "**/drafts/\n",
        },
    )

    assert scaffold.scaffold_materials("Org", "f2026", "course-materials-f2025") == 0

    assert origins.files("course-materials-f2026") == [
        ".releaseignore",
        "README.md",
        "lectures/01_session-1/slides.md",
    ]
    assert origins.read("course-materials-f2026", "README.md") == "# Data science, 2025"
    assert origins.subjects("course-materials-f2026") == ["add main"]


def test_a_copied_materials_repo_is_re_seeded_with_the_system_files_only(origins, fake):
    # The copy carries LAST year's maintainer guide and syllabus example, which are the
    # toolkit describing itself and may be a year out of date - so those are rewritten.
    # The skeleton is not written at all: every file it would seed is already there,
    # authored, and create-only writes would only log a column of skips.
    origins.commit("course-materials-f2025", {"README.md": "# 2025\n"})

    assert scaffold.scaffold_materials("Org", "f2026", "course-materials-f2025") == 0

    assert fake.written("course-materials-f2026") == {
        "MAINTAINING.md",
        "SYLLABUS.md.sample",
    }


def test_a_materials_copy_lands_before_the_first_api_write(origins, fake, monkeypatch):
    # The ordering IS the guard, so it is pinned rather than left to a comment. The
    # Contents API opens `main` with a root commit of its own on a repo that has none,
    # and the copy's push is --atomic: written first, it would reject the copy whole and
    # leave a green-looking repo holding two toolkit files and none of the year's
    # materials. Nothing else in the suite can see this - the recorder's writes never
    # reach the bare repo the copy pushes into, so the two never collide.
    origins.commit("course-materials-f2025", {"README.md": "# 2025\n"})
    seeded = scaffold.refresh_materials_system_files
    already: list[list[str]] = []

    def spy(org, repo):
        already.append(origins.branches(repo))
        return seeded(org, repo)

    monkeypatch.setattr(scaffold, "refresh_materials_system_files", spy)

    assert scaffold.scaffold_materials("Org", "f2026", "course-materials-f2025") == 0
    assert already == [["main"]]


def test_a_materials_copy_that_could_not_be_cloned_reds_the_scaffold(
    origins, monkeypatch, capsys
):
    # The repo has just been created and is empty; carrying on would seed a skeleton over
    # the copy that never arrived and report it ready.
    monkeypatch.setattr(scaffold, "clone", lambda *a, **k: False)

    assert scaffold.scaffold_materials("Org", "f2026", "course-materials-f2025") == 1
    assert "nothing was copied" in capsys.readouterr().err


def test_a_copied_assignment_brings_both_branches(origins, fake):
    origins.commit(
        "assignment-1-f2025", {"README.md": "# The brief\n", "starter.py": "pass\n"}
    )
    origins.commit(
        "assignment-1-f2025",
        {"grading_config.yml": "type: group\n", "solution/starter.py": "return 1\n"},
        branch="solution",
    )

    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", "ipynb", copy_from="assignment-1-f2025"
        )
        == 0
    )

    assert origins.branches("assignment-1-f2026") == ["main", "solution"]
    assert origins.files("assignment-1-f2026") == ["README.md", "starter.py"]
    # `solution` is cut from `main` and adds to it, exactly as the scaffold builds one.
    assert origins.files("assignment-1-f2026", "solution") == [
        "README.md",
        "grading_config.yml",
        "solution/starter.py",
        "starter.py",
    ]
    # The definition is the copied one, not one written from the button's answers.
    assert (
        origins.read("assignment-1-f2026", "grading_config.yml", "solution")
        == "type: group"
    )
    # And nothing was seeded over it - no brief stub, no starter, no model answer.
    assert fake.written("assignment-1-f2026") == set()


def test_a_copied_assignment_says_which_boxes_it_ignored(origins, capsys):
    # `format`, `type` and the rest describe an assignment this one already is. Saying so
    # once, with the file that does govern it, is the difference between an instructor
    # editing that file and one wondering why `individual` came out `group`. The NAME is
    # in that list too: box 1 is required, so every copy is typed a title that the copied
    # grading_config.yml then overrides in the sheet, the handout and the site.
    origins.commit("assignment-1-f2025", {"README.md": "# The brief\n"})
    origins.commit(
        "assignment-1-f2025", {"grading_config.yml": "title: Regression\n"}, "solution"
    )

    assert (
        scaffold.scaffold_assignment(
            "Org",
            "1",
            "f2026",
            "ipynb",
            "individual",
            name="Neural networks from scratch",
            copy_from="assignment-1-f2025",
        )
        == 0
    )

    assert (
        origins.read("assignment-1-f2026", "grading_config.yml", "solution")
        == "title: Regression"
    )
    (line,) = [l for l in capsys.readouterr().out.splitlines() if "were ignored" in l]
    for field in (
        "name",
        "format",
        "type",
        "team_formation",
        "submit_via",
        "autograde",
    ):
        assert field in line
    assert (
        "https://github.com/Org/assignment-1-f2026/blob/solution/grading_config.yml"
        in line
    )


def test_a_copied_assignment_needs_a_solution_branch(origins, capsys):
    # A template with no solution branch has no model answer and no grading_config.yml:
    # copied forward it would hand out a starter nobody can mark against, and grade on the
    # toolkit's defaults. Refused, rather than half a template reported ready.
    origins.commit("assignment-1-f2025", {"README.md": "# The brief\n"})

    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", copy_from="assignment-1-f2025"
        )
        == 1
    )
    assert "no solution branch" in capsys.readouterr().err
    assert origins.branches("assignment-1-f2026") == []


def test_a_copy_into_a_repo_that_already_has_content_lands_no_branch(origins, capsys):
    # `create_repo` reports an existing repo as success, so a re-run - or a tag already
    # spent on something else - copies into a repo git will not fast-forward. The rejected
    # `main` has to take `solution` with it: a solution branch left standing beside
    # somebody else's main is exactly what a plain re-scaffold then refuses to rebuild,
    # and the run would have red-flagged a repo it had already half-changed.
    origins.commit("assignment-1-f2025", {"README.md": "# The brief\n"})
    origins.commit(
        "assignment-1-f2025", {"grading_config.yml": "type: group\n"}, "solution"
    )
    origins.commit("assignment-1-f2026", {"README.md": "# Something else\n"})

    assert (
        scaffold.scaffold_assignment(
            "Org", "1", "f2026", copy_from="assignment-1-f2025"
        )
        == 1
    )
    assert origins.branches("assignment-1-f2026") == ["main"]
    assert origins.read("assignment-1-f2026", "README.md") == "# Something else"
    assert "could not push" in capsys.readouterr().err


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
    # It must say that the name, and its capitalisation, is what releases it - the trap the
    # sample schedule used to set - and that a PDF releases just as readily, which is what
    # ITDS actually uses.
    assert "capitalisation" in stub and "SYLLABUS.pdf" in stub


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
