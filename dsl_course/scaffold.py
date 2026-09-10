"""dsl-course scaffold -- create correctly-structured course-materials / assignment repos.

Replaces the old "use this template" repo: the required structure is defined here in
code, so a new repo is always laid out the way the Release actions expect.

    scaffold materials   --org X --tag f2026                 -> course-materials-f2026
    scaffold assignment  --org X --number 1 --tag f2026      -> assignment-1-f2026

Materials repos get `lectures/`, `readings/` and `labs/` `01_session-1/` skeletons (any
top-level directory with an ordinal-prefixed subdirectory is a releasable section - add
more, e.g. `datasets/`, freely; delete `labs/` if unused) and the run-from-repo Release
workflows. Assignment repos get a starter on `main` for each format asked for (no tests -
grading is faculty-side) and a `solution` branch carrying the model solution,
`grading_config.yml`, and the HIDDEN tests, so generate never ships any of them to
students.

Either kind can start from last year's instead of from the stubs: `--copy-from <repo>`
copies every branch of it, history and all, and rewrites only the SYSTEM-owned files.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from .access import COURSE_TEAM_ACCESS, grant_faculty, grant_tagged_team_access
from .central import CENTRAL
from .course import (
    ASSIGNMENT_TYPES,
    DEFAULT_MAX_TEAM_SIZE,
    FACULTY_ONLY_HEADING,
    FORMATS,
    MATERIALS_REPO_PREFIX,
    PROPOSAL_BRANCH_PREFIX,
    SOLUTION_BRANCH,
    SOLUTION_DIR,
    SUBMIT_VIA,
    SYLLABUS_SAMPLE_FILE,
    TEAM_FORMATIONS,
    UPSTREAM_BRANCH,
    pages_repo,
)
from .derive import BEGIN_SOLUTION, END_SOLUTION, SOLUTION_CHUNK_OPT
from .discovery import central_ref_for, discover_assignments, discover_cohorts
from .gh_contents import put_files
from .ghcli import GIT_ENV, clone, gh, git, is_already_exists
from .grades import course_assignment_defaults
from .log import log, log_err, log_ok, log_skip, log_step
from .readings import READING_OVERLAY_FILE
from .releaseignore import RELEASEIGNORE
from .repos import create_repo, repo_exists, set_repo_topics
from .welcome import TEMPLATES, example_course_file
from .workflows_place import (
    RELEASE_WORKFLOWS,
    TEMPLATE_WORKFLOWS,
    push_content_workflows,
)

# The site repo's Pages build, seeded as its FIRST commit. `create_repo` does not auto-init,
# and Pages cannot be enabled - nor the first deploy dispatched - on a repo with no branch.
# Everything else a site holds arrives with the first `site sync`.
SITE_DEPLOY_WORKFLOW = ".github/workflows/deploy.yml"

# The `--format` answer that means no starter at all, and the only one that may not share
# the box: `none` beside a real format is a contradiction, not a default (see
# `parse_formats`).
NO_STARTER = "none"


_SYLLABUS_STUB = """\
# {tag} syllabus

*Optional - delete this file if your course does not need it.*

<!-- dsl-stub: still the scaffold's, so the toolkit keeps it up to date. Delete this
     comment (or just write over the file) and it is yours - never touched again.

     FACULTY & INSTRUCTORS: this is the students' syllabus, and it is yours to write - the
     headings below are the standard Hertie shape, so delete what your course does not use.
     Release it by naming this file as the release path (see MAINTAINING.md); the name and
     its capitalisation must match exactly, and any format works - rename this to
     SYLLABUS.pdf and release that instead if you author in Word.
     A filled example sits beside this file in SYLLABUS.md.sample. -->

## 1. General information

| | |
| --- | --- |
| Instructor | |
| E-mail | |
| Office hours | |
| Term | {tag} |
| Sessions | |
| Language of instruction | English |

## 2. Course contents and learning objectives

### Course contents

### Main learning objectives

### Target group

### Prerequisites

## 3. Grading and assignments

| Component | Weight | Due |
| --- | --- | --- |
| | | |

## 4. General readings

## 5. Course sessions and readings

<!-- The course website publishes this session by session, built from
     `classroom-config/schedule.yml` (each session's title and learning objectives) and
     each session's `readings/NN_.../` folder (its reading list). If you also list the
     sessions here - Hertie syllabi normally do - keep the two in step, or students will
     read one and see the other. -->
"""

# The filled syllabus faculty copy from, seeded beside their own SYLLABUS.md as
# SYLLABUS.md.sample. Its BODY is the worked example course's real syllabus
# (example-course/course-org/course-materials-f2026/SYLLABUS.md) rather than a second copy
# authored here - the same rule the classroom-config samples follow, so the syllabus the
# docs link to as the live example is the one faculty actually receive. Only the ownership
# notice is added here, at the write site: the example file is a course team's own
# INSTRUCTOR-OWNED syllabus in its own org, and must not claim otherwise.
_SYLLABUS_SAMPLE_NOTICE = """
*Optional - a worked example to copy from; delete it if you do not want it.*

<!-- SYSTEM-OWNED - do not edit, edits here are overwritten. A FILLED example, kept
     current by the toolkit: copy from it. Your own syllabus is SYLLABUS.md beside this
     file. This file is never released to students. -->
"""

EXAMPLE_SYLLABUS = "course-materials-f2026/SYLLABUS.md"


def _syllabus_sample() -> str:
    """The worked example syllabus with the sample's ownership notice under its title."""
    title, _, body = example_course_file(EXAMPLE_SYLLABUS).partition("\n")
    return f"{title}\n{_SYLLABUS_SAMPLE_NOTICE}{body}"


# The seeded reading list, shaped like a Hertie syllabus's readings block so a course
# team can paste theirs straight in: `Required Readings` / `Optional Readings` as
# sub-headings, and any further category (some syllabi add `Application Readings`)
# works the same way - the site renders whatever headings this file has, nested under
# the session's own. A stub rather than a `.gitkeep`: the folder's files are listed
# automatically, but nothing about an empty folder said so, nor that this file is where
# an online reading goes - the tab read blank with nothing to explain why.
_READINGS_STUB = (
    b"# Session 1 readings\n\n"
    b"<!-- dsl-stub: still the scaffold's, so the toolkit keeps it up to date.\n"
    b"     Write over it and it is yours. This file is OPTIONAL - delete it and the\n"
    b"     files you put in this folder are still listed. -->\n\n"
    b"Drop the readings themselves into this folder - PDFs, slides, notebooks,\n"
    b"anything. Every file here is listed and linked for enrolled students and\n"
    b"auditors automatically; you do not have to name them here as well.\n\n"
    b"This file is for what a file cannot say: a link to read online, or a proper\n"
    b"citation. Anything goes - a bare URL on its own line is fine.\n\n"
    b"## Required Readings\n\n"
    b"- Author, *Title*, ch. 1.\n"
    b"- https://example.org/an-online-reading\n\n"
    b"## Optional Readings\n\n"
    b"- Author, *Title*, ch. 2.\n\n"
    b"What you write here is PUBLIC (it is a citation list). The files beside it\n"
    b"stay behind the enrolled-student/auditor gate, unless the course runs a\n"
    b"public open-courseware site in `actual-readings` mode, which serves them\n"
    b"too. The session's learning objectives come from `description:` in\n"
    b"schedule.yml.\n"
)

# The assignment's own definition, written from what New assignment was asked for and
# what the course declares in `dsl-course.yml assignment_defaults`. INSTRUCTOR-OWNED from
# the moment it lands: nothing ever rewrites it, and every setting in it is meant to be
# edited here afterwards. A setting the course has no default for is seeded COMMENTED OUT,
# so the file teaches the whole vocabulary without asserting an opinion nobody expressed.
_GRADING_STAMP = (
    "# INSTRUCTOR-OWNED - defines the assignment. "
    "Dates live in the cohort's schedule.yml."
)
_QUESTIONS_STUB = """\
# questions:                  # OPTIONAL - the score skeleton, and the maximum shown beside
#   Q1: 15                    # each blank in the grading sheet. The total is their sum;
#   Q2: 10                    # there is no `points:` field anywhere.
"""


# YAML's indicator characters, which mean something else when a scalar OPENS with one.
# Inside a word they are ordinary text, which is why `10%` and `A - B` need no quotes.
_YAML_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"


def _yaml_scalar(value: object) -> str:
    """A value safe to write on the right of a `key:`. Quoted only when it has to be - a
    title with `: ` in it would otherwise make that line a nested mapping, and the reader
    would drop the key it thought it was reading."""
    text = str(value)
    unsafe = (
        text == ""
        or text != text.strip()
        or text[0] in _YAML_INDICATORS
        or ": " in text
        or " #" in text
        or text.endswith(":")
    )
    if unsafe:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _setting(key: str, value: object, comment: str, live: bool = True) -> str:
    """One line of grading_config.yml: `key: value` padded to a common column, then its
    explanation. `live=False` comments the whole line out - the setting is documented and
    inert until an instructor uncomments it."""
    line = f"{key}: {_yaml_scalar(value)}"
    if not live:
        line = f"# {line}"
    return f"{line:<29} # {comment}".rstrip()


def _grading_config(
    *,
    title: str,
    kind: str,
    team_formation: str,
    submit_via: str,
    formats: list[str],
    autograde: bool,
    defaults: dict,
) -> str:
    """`grading_config.yml` as New assignment writes it."""
    group = kind == "group"
    cap = defaults.get("max_team_size")
    window = defaults.get("late_window_days")
    penalty = defaults.get("late_penalty_per_day")
    lines = [
        _GRADING_STAMP,
        _setting(
            "title", title, "the assignment's name, shown to graders and on the site"
        ),
        _setting("type", kind, "individual | group"),
        _setting(
            "team_formation",
            team_formation,
            "self_select (students use the Join-team form) | assigned (you write teams.csv)",
            live=group,
        ),
        _setting(
            "max_team_size",
            cap or DEFAULT_MAX_TEAM_SIZE,
            "group only",
            live=group and cap is not None,
        ),
        _setting(
            "submit_via", submit_via, "github | external (Moodle, Kaggle, in class...)"
        ),
        # ONE format, because `grades` reads one: the key is the vocabulary this file
        # teaches and the default behind `completion_check`, which is written out
        # explicitly below either way. A template seeded with several starters records
        # the one it was named first by.
        _setting(
            "format",
            formats[0] if formats else NO_STARTER,
            "ipynb | py | rmd | qmd | latex | none - chooses the starter stub only; "
            "grading reads whatever is in the repo",
        ),
        "",
        _QUESTIONS_STUB.rstrip(),
        "",
        _setting(
            "late_window_days",
            window if window is not None else 7,
            "0 or absent = nothing after the due date is accepted",
            live=window is not None,
        ),
        _setting(
            "late_penalty_per_day",
            penalty or "10%",
            "of the EARNED grade, per day started",
            live=penalty is not None,
        ),
        "",
        _setting(
            "autograde",
            "true" if autograde else "false",
            "true: run tests/ at the cutoff and show the count to graders",
        ),
        _setting(
            "completion_check",
            "true" if "ipynb" in formats else "false",
            "true: execute the notebook at the cutoff, record whether it runs clean",
        ),
        _setting(
            "grader_pdf",
            "false",
            "true: at the cutoff, archive each submission filtered to its marked "
            "questions as a PDF for graders",
        ),
    ]
    return "\n".join(lines) + "\n"


_HIDDEN_TEST_PY = """\
# HIDDEN tests - run faculty-side at the cutoff, never shipped to students.
# They import the student's submission (the repo root) and check it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
"""

_HIDDEN_TEST_NOTEBOOK = """\
# HIDDEN tests - run faculty-side at the cutoff, never shipped to students.
# The submitted notebook is nbconvert'd to starter.py first; this imports it and checks it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
"""


# The starter each `format` seeds on `main`. `none` seeds nothing at all - the raw-repo
# option. A stub is a CONVENIENCE and nothing more: grading reads whatever is in the repo,
# so a student who works in a notebook on a `py` assignment still grades.
#
# Every stub is a document of its own kind that ALREADY BUILDS: an .Rmd that knits, a .qmd
# that renders, a .tex that compiles, a notebook that runs. A stub a student has to repair
# before it does anything teaches them, on day one, that the scaffolding is broken.
_STARTER_CODE = "def solve():\n    raise NotImplementedError  # TODO"
# THE graded-artefact convention, in the one sentence every starter and every brief says.
# A source format is only markable if the BUILT artefact is committed beside it: nobody
# marks a `.tex`, and a notebook whose outputs were cleared is a file nobody can read
# without running it. Declared once because two audiences read it at two moments - the
# student opening the starter, and the faculty member writing the brief.
_RENDER_RULE = (
    "The rendered document is what we read and mark; the source is what we check it "
    "against."
)
# The same rule for a notebook, where the "rendering" is the run itself - and the rule the
# completion check at the cutoff verifies.
_NOTEBOOK_RULE = (
    "**Restart the kernel and run all cells before you commit**, and commit the notebook "
    "with its outputs saved: we read it as it stands, and we run it the same way."
)


def _one_line(text: str) -> str:
    """`text` with every run of whitespace collapsed to one space.

    A title safe to drop into a `%` comment, a Markdown heading or a single-line YAML
    scalar. The button's box is single-line, but a title pasted out of a syllabus is not
    always, and a newline inside a `\\title{...}` or a front-matter key ends the construct
    rather than the line."""
    return " ".join(str(text).split())


# The characters TeX reads as syntax. A title carrying an `&`, a `%` or an underscore -
# `R&D`, `100% coverage`, `train_test_split` - either refuses to compile or silently
# swallows the rest of its line, and a stub that compiles unedited is the whole point.
_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _latex_text(text: str) -> str:
    """`text` as LaTeX body text - every syntax character escaped, on one line."""
    return "".join(_LATEX_ESCAPES.get(ch, ch) for ch in _one_line(text))


def _py_docstring(text: str) -> str:
    """`text` inside a triple-quoted docstring: one line, no closing-quote run of its own,
    and nothing at the end that would run into the terminator."""
    safe = _one_line(text).replace("\\", "/").replace('"""', "'''").rstrip('"')
    return safe or "Assignment"


def _latex_starter(title: str) -> str:
    """A document that compiles as it stands - `pdflatex starter.tex` and no more."""
    return (
        f"% {_one_line(title)}\n"
        "%\n"
        "% Compile this file (`pdflatex starter.tex`, or your editor's Build button) and\n"
        "% commit the starter.pdf it produces beside it.\n"
        f"% {_RENDER_RULE}\n"
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{amsmath,amssymb}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage[margin=2.5cm]{geometry}\n\n"
        f"\\title{{{_latex_text(title)}}}\n"
        "\\author{Your name}\n"
        "\\date{\\today}\n\n"
        "\\begin{document}\n"
        "\\maketitle\n\n"
        "\\section{Task}\n\n"
        "Replace this section with your answer.\n\n"
        "\\end{document}\n"
    )


# The front-matter key each markdown format names its output with - the ONE word `.Rmd` and
# `.qmd` differ by here. Read by the starter on `main` and by the model answer on `solution`,
# so the two cannot drift.
_MARKDOWN_OUTPUT = {"rmd": "output: html_document", "qmd": "format: html"}


def _markdown_starter(title: str, fmt: str, *, output: str) -> str:
    """An .Rmd / .qmd that knits or renders as it stands: front matter, a heading, one
    chunk, and the artefact rule.

    The two formats differ in exactly one word here - the front-matter key naming the
    output (`output:` vs `format:`) - so they are one function rather than two that drift;
    the verb and the built filename come from `_BUILDS` like everywhere else. Both seed an
    `{r}` chunk, which is what a Quarto document at a school teaching R renders with; swap
    it for `{python}` and nothing else moves."""
    return (
        "---\n"
        f"title: {_yaml_scalar(_one_line(title))}\n"
        f"{output}\n"
        "---\n\n"
        "## Task\n\n"
        "Replace this section with your answer.\n\n"
        "```{r}\n"
        "# Your code here.\n"
        "```\n\n"
        "## What you hand in\n\n"
        f"{_hand_in(fmt)}\n"
    )


def _py_starter(title: str) -> str:
    """A module that imports and runs as it stands - the hidden tests, where there are
    any, `from starter import ...`."""
    return (
        f'"""{_py_docstring(title)}\n\n'
        "Run this file top to bottom before you commit: we run what is in the\n"
        "repository, not what is open in your editor.\n"
        '"""\n\n\n'
        f"{_STARTER_CODE}\n"
    )


def _notebook_starter(title: str) -> str:
    """A notebook that opens and runs as it stands, carrying the run-all rule in its own
    first cell - the rule the completion check at the cutoff then verifies."""
    return _notebook([f"# {_one_line(title)}", "", _NOTEBOOK_RULE], _STARTER_CODE)


# THE stem every starter shares - the hidden tests `from starter import ...`, `collect`
# converts `starter.ipynb` to `starter.py` before they do, and the artefact rule names
# `starter.html` / `starter.pdf`. A contract, not a preference, so it is spelt once.
STARTER_STEM = "starter"
# The `format`s whose graded artefact is BUILT from the source: `format` -> (what to call
# the build, what it produces). One row is what the starter, the brief and the seeded
# `%` preamble all read, so the three cannot drift into three wordings of one rule.
_BUILDS = {
    "rmd": ("Knit", "html"),
    "qmd": ("Render", "html"),
    "latex": ("Compile", "pdf"),
}


def starter_name(fmt: str) -> str:
    """The starter's filename for `fmt` - `starter` plus its extension."""
    return f"{STARTER_STEM}{_STARTERS[fmt][0]}"


def _hand_in(fmt: str) -> str:
    """What "hand it in" means for `fmt`, in the one sentence the starter and the brief
    both carry."""
    if fmt not in _BUILDS:
        return _ARTEFACT_NOTE.get(fmt, "")
    verb, built = _BUILDS[fmt]
    return (
        f"{verb} `{starter_name(fmt)}` and commit **both** it and the "
        f"`{STARTER_STEM}.{built}` it produces. {_RENDER_RULE}"
    )


# `format` -> (the extension its starter takes, how to build it).
_STARTERS = {
    "ipynb": (".ipynb", _notebook_starter),
    "py": (".py", _py_starter),
    "rmd": (
        ".Rmd",
        lambda title: _markdown_starter(title, "rmd", output=_MARKDOWN_OUTPUT["rmd"]),
    ),
    "qmd": (
        ".qmd",
        lambda title: _markdown_starter(title, "qmd", output=_MARKDOWN_OUTPUT["qmd"]),
    ),
    "latex": (".tex", _latex_starter),
}
# The formats whose artefact rule is NOT "build it": the two that hand in what they are.
_ARTEFACT_NOTE = {
    "ipynb": (
        "Commit the notebook with its outputs saved, after **Restart kernel and run "
        "all**. We read it as it stands, and we run it the same way."
    ),
    "py": "Commit your `.py` files; we run them from the repository root.",
}
# CONTRIBUTIONS.md goes into a GROUP assignment's `main` only. It carries the stub mark,
# because `collect._contributions` reads it at the pin and has to tell an untouched
# scaffold from a team that wrote nothing - "(not filled in)" is a fact about the team,
# and a blank would read as "the toolkit did not look".
_CONTRIBUTIONS_STUB = """\
# Contributions

<!-- dsl-stub: replace this with who did what. The teaching team reads it at the
     deadline, alongside your commit history, when deciding individual adjustments. -->

| Member | What they did |
|---|---|
| @handle | |
"""


def _brief_stub(title: str, defaults: dict, formats: list[str]) -> str:
    """`README.md` on `main` - the page students read, and the only one only faculty can
    write. A STUB, unmistakably: seeding a plausible-looking brief invites shipping it
    unedited. The late-work line repeats what the course already declared, so the two
    cannot disagree on the page a student actually opens.

    `formats` add the one line the stub is NOT free to leave to its author: what counts
    as handing each of them in (`_ARTEFACT_NOTE`), one per format. A brief that never says
    the knitted HTML has to come with the `.Rmd` is a brief that collects `.Rmd` files
    nobody can mark."""
    window = defaults.get("late_window_days")
    penalty = defaults.get("late_penalty_per_day")
    if not window:
        late = "not accepted after the deadline"
    elif penalty:
        late = f"{penalty} per day, up to {window} days"
    else:
        late = f"accepted up to {window} days late"
    artefacts = [sentence for fmt in formats if (sentence := _hand_in(fmt))]
    return (
        f"# {title}\n\n"
        f"**Points:** __ · **Due:** see the course schedule · **Late work:** {late}\n\n"
        "## Task\n\n"
        "_Write the assignment here (dsl-stub: replace this whole file)._\n\n"
        "## What to submit\n\n"
        + "".join(f"{sentence}\n\n" for sentence in artefacts)
        + "_Say which files you expect back, and in what shape._\n"
    )


def _notebook(title_lines: list[str], code: str) -> str:
    """A minimal valid .ipynb: one markdown cell + one code cell. language_info carries
    `.py` so the grader's nbconvert names its output starter.py (see collect)."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [line + "\n" for line in title_lines],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in code.splitlines()],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "file_extension": ".py"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb, indent=1) + "\n"


# The stub model answer's one piece of work, fenced in each language's own vocabulary so
# **Derive student version** works on a freshly scaffolded template rather than refusing it:
# a file with nothing fenced would derive the model answer itself as the starter, so
# `derive` names it and goes red.
_MODEL_PY = (
    f"def solve():\n"
    f"    {BEGIN_SOLUTION}\n"
    f"    return 42  # TODO - the model answer\n"
    f"    {END_SOLUTION}"
)
_MODEL_R = "answer <- 42  # TODO - the model answer"


def _model_answer(number: int, fmt: str) -> str | None:
    """The model answer seeded on the `solution` branch for `fmt`, or None where there is
    nothing to seed.

    Same stem AND same suffix as the starter on `main`. `derive` writes `solution/X` onto
    `main` as `X`, so a model answer in the wrong format - it was `starter.py` for every
    non-notebook assignment - put a second file beside the real starter instead of becoming
    it: `starter.py` landing next to an untouched `starter.Rmd`.

    `latex` and `none` seed nothing at all: `.tex` is not derivable and `none` has no
    starter to become, so a stub there could only ever be a file the button refuses."""
    title = f"Assignment {number} - model solution (stub)"
    if fmt == "ipynb":
        return _notebook([f"# {title}"], _MODEL_PY)
    if fmt == "py":
        return (
            f'"""Model solution for assignment {number} (stub)."""\n\n\n{_MODEL_PY}\n'
        )
    if fmt in _MARKDOWN_OUTPUT:
        return (
            "---\n"
            f"title: {_yaml_scalar(title)}\n"
            f"{_MARKDOWN_OUTPUT[fmt]}\n"
            "---\n\n"
            "## Task\n\n"
            f"```{{r {SOLUTION_CHUNK_OPT}}}\n"
            f"{_MODEL_R}\n"
            "```\n"
        )
    return None


# Seeded inert - every line a comment - so it withholds nothing until faculty write a
# pattern. It exists to be FOUND: a withhold list nobody knows about is one nobody uses,
# and a docs page is only reachable by someone who already suspects it exists.
_RELEASEIGNORE_STUB = f"""\
# INSTRUCTOR-OWNED - yours. Written once when this repo was scaffolded, and never
# rewritten by the toolkit, so anything you put here stays.
#
# Name a file here and it is never copied out of this repo - not into a cohort's
# materials, not onto the public site, not with a solution. Same syntax as .gitignore,
# and you can add another in any subfolder. The full rules:
# https://github.com/{CENTRAL}/blob/main/docs/08-release-materials-to-cohort.md
"""


def _actions_table(org: str) -> str:
    # The course org's `.github` Actions tab hosts the workflows that operate this course.
    # Both README (faculty & instructors orientation, pre-release) and MAINTAINING link it.
    actions_url = f"https://github.com/{org}/.github/actions"
    return (
        f"The course org's [`.github` Actions tab]({actions_url}) hosts the workflows that "
        "operate this course:\n\n"
        "| Action | What it does |\n"
        "| --- | --- |\n"
        "| **Release materials** | Copy any path - session folders, root files - into a "
        "cohort's `materials` repo by default, or a destination path you name. |\n"
        "| **Release assignment** | Freeze an assignment template, then generate one private "
        "repo per student (or per team). |\n"
        "| **New materials repo** | Scaffold a correctly structured materials repo; the "
        "release workflows come bootstrapped with it. |\n"
        "| **New assignment** | Scaffold an assignment template (brief + starter(s); the "
        "`solution` branch holds the model answer and `grading_config.yml`); the release "
        "workflows come bootstrapped with it. |\n"
        "| **Refresh actions** | Re-seed the run-from-repo workflows and repopulate dropdowns "
        "after you add sessions/sections. |\n"
        "| **Check cohort setup** | Read-only per-cohort checklist of what's configured. |\n\n"
        "(**Release materials** and **Release assignment** also appear in this repo's own "
        "Actions tab.)\n"
    )


def _maintaining(org: str, repo: str) -> str:
    """The generated maintainer guide - SYSTEM-owned docs, built from the actions table."""
    actions_table = _actions_table(org)
    return (
        "<!-- SYSTEM-OWNED - do not edit, edits here are overwritten. This file is "
        "regenerated by every Refresh actions run. -->\n\n"
        f"# Maintaining `{repo}`\n\n"
        "Reference for faculty & instructors on how to populate and operate this materials "
        "**source** repo. This file is **not** released to students. Keep student-facing "
        "wording in `README.md` and operational notes here.\n\n"
        "## What to edit vs leave alone\n\n"
        "| You edit / add | Visible to students? | Notes |\n"
        "| --- | --- | --- |\n"
        "| `lectures/`, `labs/`, `readings/` (and any other section folders) | Yes, when you "
        "release that session | The released files are copied into the cohort `materials` "
        "repo, or another destination path you name. |\n"
        "| root files - `SYLLABUS.md`, `README.md`, or any name you use | Yes, when you name "
        "the file as the release path | A root file is released like any other path: type "
        "`SYLLABUS.pdf` (or whatever the file is called) as the `course_source_path`. |\n"
        "| `MAINTAINING.md` (this file) | No | Your reference; never released. Leave it in the "
        "repo. |\n"
        "| `.github/workflows/` (the Release workflows) | No | **Infrastructure - do not edit or "
        "delete.** These run-from-repo workflows are what make releasing work; **Refresh actions** "
        "re-seeds them. |\n\n"
        "Rule of thumb: edit the content folders and the two root files; leave `MAINTAINING.md` "
        "and `.github/workflows/` alone.\n\n"
        "## Structure\n\n"
        "Any top-level directory containing at least one ordinal-prefixed subdirectory "
        "(`01_`, `02_`, `03_`, ...) is a releasable section:\n\n"
        "- `lectures/01_session-1/` - one folder per session's lecture files\n"
        "- `labs/01_session-1/` - one folder per session's lab (delete the `labs/` folder "
        "if your course has none)\n"
        "- `readings/01_session-1/` - one folder per session's readings. Just drop the "
        "readings in and every file is listed and linked for enrolled students "
        "automatically. An additional `READINGS.md` (or `.txt`/`.bib`) is OPTIONAL prose - "
        "a link to read online, or a citation; it is published publicly, while the files "
        "stay behind the enrolled-student gate (unless a public site toggles on "
        "`actual-readings`)\n"
        "- root files - your syllabus under any name (`SYLLABUS.md`, `SYLLABUS.pdf`, ...) "
        "and `README.md`: released by naming the file as the release path (the runner is "
        "case sensitive)\n\n"
        "Add more sessions by creating `lectures/02_session-2/`, `readings/02_session-2/`, ... "
        "(only the ordinal prefix matters - name the rest whatever you like), or add a whole "
        "new section (e.g. `datasets/01_intro/`). Nothing needs refreshing afterwards: the "
        "Release workflows take the path as free text (`course_source_path`), so a new "
        "session or section is releasable the moment you push it. **Refresh actions** "
        "repopulates the repo/cohort dropdowns, and runs itself nightly.\n\n"
        "## Available actions\n\n" + actions_table + "\n"
        "## Public course website (optional) **[DEFERRED]**\n\n"
        "The **Publish course website** action can share this repo's materials on a public "
        "open-courseware site. Lecture files are always hosted; for readings you choose "
        "`reading-list` (text/citation files are shown as a list - keep copyrighted PDFs out "
        "of the list by leaving them as non-text files) or `actual-readings` (every reading "
        "file is hosted and downloadable - you carry the copyright responsibility).\n"
    )


def materials_system_files(org: str, repo: str) -> dict[str, bytes]:
    """The SYSTEM-owned files in a materials repo: this toolkit describing itself, so
    they are REFRESHED rather than frozen - faculty edits here are overwritten, which
    each file says in its own text.

    Named here, where they are written, and pushed by `refresh_materials_system_files`
    from both the scaffold and the nightly refresh - one list rather than two that drift,
    the SYSTEM-owned half of the ownership split; the skeleton above is the other."""
    return {
        SYLLABUS_SAMPLE_FILE: _syllabus_sample().encode(),
        "MAINTAINING.md": _maintaining(org, repo).encode(),
    }


def refresh_materials_system_files(org: str, repo: str) -> int:
    """Re-push a materials repo's SYSTEM-owned files (materials_system_files).

    Called both at scaffold time and on the nightly refresh, so an improvement to the
    maintainer guide or the syllabus example reaches the courses already running. Before
    this they were written only by the scaffold, which made "SYSTEM-owned" true of new
    repos and nothing else: a course scaffolded before the example existed was never going
    to get one. `put_files` compares blob shas, so a repo already current is written
    nothing, and both files land in one commit because they always change together.

    Unlike the stubs this CREATES as well as updates - back-filling a file added after the
    repo was made is the point - so it is gated on the repo NAME, and the gate lives here
    rather than at the call site because no caller may skip it: `discover_content_repos`
    hands the nightly sweep the code and dataset repos too, and a materials-repo
    maintainer guide in `lecture-code-f2026` is the nonsense the scaffold's own gate
    `create=False` exists to avoid. Every materials repo is named `course-materials-<tag>`
    by `scaffold_materials`, from a workflow that takes only the tag, so the prefix is a
    toolkit guarantee rather than a convention.

    Returns 1 if the commit didn't land, so callers go red rather than report a converged
    repo - this runs unattended on a cron, where a silent skip is invisible for weeks."""
    if not repo.startswith(MATERIALS_REPO_PREFIX):
        return 0
    if not put_files(
        org,
        repo,
        materials_system_files(org, repo),
        "docs: maintainer guide + syllabus example",
    ):
        log_err(f"system files not written to {org}/{repo}")
        return 1
    return 0


def materials_readme(org: str) -> str:
    """The materials repo's student-facing README placeholder.

    Module-level, not inline in scaffold_materials, so it can be rendered WITHOUT creating
    a repo. The file is create-only, like every other instructor-owned file in the
    skeleton, so a wording fix reaches a repo that already exists only by writing it
    deliberately, after checking the placeholder is still untouched
    (deploy.UNEDITED_README_MARKERS).

    Release materials with the README toggle copies this file into the cohort's materials
    repo, where enrolled students read it - so it is written for them. How the source repo
    is structured, and how to operate it, is MAINTAINING.md, which is never released.
    """
    actions_table = _actions_table(org)
    return (
        # Two lines below carry deploy.py's UNEDITED_README_MARKERS - the "Replace this
        # placeholder" note and FACULTY_ONLY_HEADING. A release refuses to ship a README
        # still holding BOTH, so edit this stub's wording freely but keep those two intact
        # (test_scaffold.py asserts the seeded file still trips the guard).
        "<!-- INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n\n"
        "     FACULTY & INSTRUCTORS: replace the content below with a real, student-facing\n"
        "     overview of your course materials. Release materials with the 'include README'\n"
        "     toggle copies THIS file into the cohort's materials repo, where enrolled\n"
        "     students read it - so write it for them, not as internal notes. How this source\n"
        "     repo is structured, and how to operate it, is in MAINTAINING.md (for faculty &\n"
        "     instructors only - never released to students). -->\n\n"
        "# Course materials\n\n"
        "> **Replace this placeholder.** This file becomes the students' README for the\n"
        "> released materials. Add a short overview of the course, how the materials are\n"
        "> organised, and anything students should read first.\n\n"
        "---\n\n"
        f"## For faculty & instructors ({FACULTY_ONLY_HEADING})\n\n"
        "- **How to populate & operate this repo:** see [`MAINTAINING.md`](MAINTAINING.md) - "
        "it explains what to edit, what gets released to students, and what to leave alone. "
        "`MAINTAINING.md` is **not** deployed to the cohort org; leave it here as a persistent "
        "reference.\n"
        "- **Available actions:** " + actions_table
    )


# Where a copied branch waits in the new repo's clone before it is pushed. Not
# `refs/heads/`: git refuses to fetch into the branch a working copy has checked out, and
# a clone of a repo with no commits has its default branch checked out (unborn), so the
# obvious refspec fails on exactly the branch that matters.
_COPY_REFS = "refs/copy"


def _source_branches(wd: Path) -> list[str]:
    """Every branch a clone's origin has, in name order.

    `refs/remotes/origin/HEAD` is the symref naming the default branch rather than a
    branch of its own; pushed under that name it would open the new repo with a branch
    called `HEAD`."""
    code, out = git(
        "-C",
        str(wd),
        "for-each-ref",
        "--format=%(refname:strip=3)",
        "refs/remotes/origin",
    )
    if code != 0:
        return []
    # The human branches only: `upstream` and `from-<cohort-org>` are toolkit-owned working
    # branches, regenerated by release and propagate, and a copy has nothing to carry over.
    return [
        b
        for b in out.split()
        if b != "HEAD"
        and b != UPSTREAM_BRANCH
        and not b.startswith(PROPOSAL_BRANCH_PREFIX)
    ]


def _copy_branches(org: str, source: str, repo: str, *, needs: str = "") -> bool:
    """Copy every branch of `org/source`, history and all, into the just-created
    `org/repo`. False (having said why) if any of it failed.

    `needs` names a branch the copy would be worthless without - an assignment whose
    source has no `solution` branch would arrive with no model answer and no
    `grading_config.yml`, and grade on the defaults, so the run refuses instead."""
    with tempfile.TemporaryDirectory() as work:
        src, new = Path(work) / "source", Path(work) / "new"
        if not clone(org, source, src):
            log_err(f"  ! could not clone {org}/{source} - nothing was copied")
            return False
        branches = _source_branches(src)
        if not branches:
            log_err(f"  ! {org}/{source} has no branches to copy")
            return False
        if needs and needs not in branches:
            log_err(
                f"  ! {org}/{source} has no {needs} branch - copy from a repo that has "
                "one, or leave copy_from empty for a fresh starter"
            )
            return False
        if not clone(org, repo, new):
            log_err(f"  ! could not clone {org}/{repo} to copy into")
            return False
        fetched = [f"refs/remotes/origin/{b}:{_COPY_REFS}/{b}" for b in branches]
        if git("-C", str(new), *GIT_ENV, "fetch", "-q", str(src), *fetched)[0] != 0:
            log_err(f"  ! could not read {org}/{source}'s branches")
            return False
        pushed = [f"{_COPY_REFS}/{b}:refs/heads/{b}" for b in branches]
        # `--atomic`, as a release's own two-branch push is: the destination is normally
        # empty, but a re-run against a tag that already has a repo has one branch git
        # rejects and others it does not, and half a copy is worse than none - a
        # `solution` that arrived beside somebody else's `main` is what the plain
        # scaffold then refuses to rebuild.
        code, _ = git(
            "-C", str(new), *GIT_ENV, "push", "-q", "--atomic", "origin", *pushed
        )
        if code != 0:
            log_err(f"  ! could not push {org}/{source}'s branches into {org}/{repo}")
            return False
    log_ok(f"copied {org}/{source} into {org}/{repo} ({', '.join(branches)})")
    return True


def scaffold_materials(org: str, tag: str, copy_from: str = "") -> int:
    repo = f"{MATERIALS_REPO_PREFIX}{tag}"
    log_step(f"Scaffolding {org}/{repo}")
    if not create_repo(
        org,
        repo,
        private=True,
        description=(
            "Course materials (lectures/labs/readings/datasets/other) by session"
        ),
    ):
        return 1
    grant_faculty(org, repo, COURSE_TEAM_ACCESS)
    grant_tagged_team_access(org, repo, tag)
    failures = 0
    # Copying comes FIRST, before a single API write: `_copy_branches` pushes whole
    # branches, and a `main` the Contents API had already opened would make that push a
    # non-fast-forward. The skeleton is then not written at all - a copied repo already
    # holds a README, a syllabus and its sections, authored.
    if copy_from and not _copy_branches(org, copy_from, repo):
        return 1
    failures += refresh_materials_system_files(org, repo)
    if not copy_from:
        # INSTRUCTOR-OWNED, every one of them, and all CREATE-ONLY: written when the repo
        # is scaffolded and never again. A re-run against a repo faculty have since
        # authored must not revert their work, and neither must the nightly refresh - see
        # the note on `_SYLLABUS_STUB` for why "is it still ours?" is not a question this
        # can ask safely. A failed seed (an absent file whose write failed) reds the
        # scaffold.
        user_files = {
            "README.md": materials_readme(org).encode(),
            "SYLLABUS.md": _SYLLABUS_STUB.format(tag=tag).encode(),
            "lectures/01_session-1/.gitkeep": b"",
            # A stub, not a .gitkeep: a text file here IS the published reading list (its
            # contents are inlined on the site's Materials tab), and an empty folder gave
            # no sign of that - the tab then reads blank with nothing to explain why.
            f"readings/01_session-1/{READING_OVERLAY_FILE}": _READINGS_STUB,
            "labs/01_session-1/.gitkeep": b"",
            RELEASEIGNORE: _RELEASEIGNORE_STUB.encode(),
        }
        # One commit for the skeleton: they all carried the same subject anyway, so
        # writing them one at a time opened a repo faculty then author by hand with a
        # column of identical `init: materials skeleton` lines.
        if not put_files(
            org, repo, user_files, "init: materials skeleton", create_only=True
        ):
            failures += 1
    # Equip the run-from-repo Release workflows (same as Refresh does for content repos).
    # push_content_workflows lands both in one commit, logs its own failure, and returns
    # 1 - a materials repo with no Release workflows must not report success.
    cohorts = discover_cohorts(org)
    failures += push_content_workflows(
        org,
        repo,
        cohorts,
        discover_assignments(org),
        central_ref_for(org),
        workflows=RELEASE_WORKFLOWS,
    )
    if failures:
        return 1
    log_ok(f"materials repo ready: {org}/{repo}")
    return 0


def _seed_template_workflows(org: str, repo: str) -> int:
    """Equip the hand-out button, as scaffold_materials equips the release ones: faculty
    run Release assignment from this repo's own Actions tab, and it opens on THIS
    template. Which buttons a template hosts, and why, is `TEMPLATE_WORKFLOWS`;
    `assign.withhold_from_template` is what strips them off the cohort copy, so no student
    repo generated from this template inherits one.

    Every path that makes a template calls it, the copied one included: a copy arrives
    carrying the SOURCE template's button, pre-selected on last year's assignment, and
    until something re-renders it the new template's own button hands out the wrong
    assignment by default."""
    return push_content_workflows(
        org,
        repo,
        discover_cohorts(org),
        discover_assignments(org),
        central_ref_for(org),
        workflows=TEMPLATE_WORKFLOWS,
    )


def parse_formats(answer: str) -> list[str]:
    """The `--format` answer as the starters to seed, in the order they were named.

    The system edge: one comma-separated box, typed by hand, becomes the list the rest of
    the module works in. Duplicates collapse - a starter is seeded once - and `none`
    stands alone, because guessing which half of `ipynb,none` was meant is how a template
    ends up with a file its brief never mentions.

    Raises ValueError, which the CLI prints as one line before the repo is created, so a
    mistyped format costs a re-run and nothing else."""
    named = list(
        dict.fromkeys(
            cleaned for token in answer.split(",") if (cleaned := token.strip().lower())
        )
    )
    if not named:
        raise _not_a_format("no starter named")
    unknown = [token for token in named if token not in FORMATS]
    if unknown:
        raise _not_a_format(f"{unknown[0]} is not a starter")
    if NO_STARTER in named:
        if len(named) > 1:
            raise _not_a_format(
                f"{NO_STARTER} means no starter at all, so it cannot be asked for "
                "beside one"
            )
        return []
    return named


def _not_a_format(problem: str) -> ValueError:
    """Every refusal of a `--format` answer, in the one line that names what may be
    typed - the box takes free text, so the answer to a bad one is the vocabulary."""
    listed = ", ".join(fmt for fmt in FORMATS if fmt != NO_STARTER)
    return ValueError(
        f"--format: {problem} - name any of {listed}, comma-separated, or "
        f"{NO_STARTER} on its own"
    )


def scaffold_assignment(
    org: str,
    number: str,
    tag: str,
    formats: list[str],
    kind: str = "individual",
    *,
    name: str = "",
    team_formation: str = "self_select",
    submit_via: str = "github",
    autograde: bool = False,
    copy_from: str = "",
) -> int:
    """Create `assignment-<number>-<tag>` and write the assignment's own definition into it.

    Every argument but `formats` lands verbatim in the solution branch's
    `grading_config.yml`, which is what the handout, the grading sheet and the Join-team
    form all read - so the answers given on the button are the ones the rest of the term
    obeys, and nothing has to be hand-edited in afterwards. What the button does NOT ask -
    the team cap, the late window, the penalty - comes from the course's own
    `assignment_defaults`.

    `formats` is the exception: it picks which starter stubs are seeded on `main`, one
    each with its model answer on `solution`, and nothing else. The grader reads whatever
    is in the repo, so a student who works in a notebook on a `py` assignment still
    grades; an empty list seeds no starter at all.

    `copy_from` overrides all but the name and the number: last year's template arrives
    whole, both branches, and the `grading_config.yml` that came with it is the
    assignment's definition. Writing the button's answers over it would silently
    re-declare an assignment the course has already run."""
    repo = f"assignment-{number}-{tag}"
    named = name.strip()
    starters = ", ".join(formats) or NO_STARTER
    log_step(
        f"Scaffolding {org}/{repo} "
        + (
            f"(copied from {copy_from})"
            if copy_from
            else f"({kind}, {starters}; template + solution branch)"
        )
    )
    # The name is the one answer both paths keep: it is this repo's description on the
    # org's landing page, which is the only place a copied template says which assignment
    # it is without opening its grading_config.yml.
    if not create_repo(
        org,
        repo,
        private=True,
        is_template=True,
        description=(
            f"Assignment {number}: {named}"
            if named
            else f"Assignment {number} template"
        ),
    ):
        return 1
    grant_faculty(org, repo, COURSE_TEAM_ACCESS)
    grant_tagged_team_access(org, repo, tag)
    set_repo_topics(org, repo, [f"assignment-{number}", "assignment"])
    if copy_from:
        # Nothing to SEED: both branches, every stub's grown-up version and the definition
        # itself came with the copy. The button is the exception - the copy brought the
        # source's, aimed at the source - so it is re-rendered for this repo, as on the
        # fresh path.
        if not _copy_branches(org, copy_from, repo, needs=SOLUTION_BRANCH):
            return 1
        log(
            "  (boxes 5-9 - format, type, team_formation, submit_via and autograde - "
            "were ignored: the number and the tag name the repo, the name describes it, "
            "and the copied definition governs the rest: "
            f"https://github.com/{org}/{repo}/blob/"
            f"{SOLUTION_BRANCH}/grading_config.yml)"
        )
        if _seed_template_workflows(org, repo):
            return 1
        log_ok(f"assignment template ready: {org}/{repo} (copied from {copy_from})")
        return 0
    title = named or f"Assignment {number}"
    defaults = course_assignment_defaults(org)
    # main: the brief, a starter stub per format, and (group only) CONTRIBUTIONS.md -
    # what students receive on generate. No tests, no autograder - grading runs
    # faculty-side from the solution branch. ONE commit, create-only, exactly as
    # scaffold_materials seeds its skeleton: a re-run against a repo whose starter faculty
    # have since authored leaves it alone and logs the skip, and the repo they then author
    # by hand opens on one `init:` line rather than three identical ones.
    seeds = {"README.md": _brief_stub(title, defaults, formats)}
    for fmt in formats:
        seeds[starter_name(fmt)] = _STARTERS[fmt][1](title)
    if kind == "group":
        seeds["CONTRIBUTIONS.md"] = _CONTRIBUTIONS_STUB
    # A failed create-only write (not a skip of a live file) reds the scaffold rather than
    # reporting a green "ready" over a template missing its brief.
    seeded = put_files(
        org,
        repo,
        {path: text.encode() for path, text in seeds.items()},
        "init: assignment starter",
        create_only=True,
    )

    # solution branch: the model solution, grading_config.yml, and the HIDDEN tests -
    # all kept OFF main so generate never copies them into student repos.
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "r"
        if not clone(org, repo, wd):
            log_err("  ! could not clone to add the solution branch")
            return 1
        # A solution branch left by a prior run holds a real model solution and hidden
        # tests; overwriting it would destroy faculty work. Probe the REMOTE, because in
        # this fresh clone no local `solution` exists yet - `checkout -b` would happily
        # succeed and the run would only fail much later, at the push, with a misleading
        # error. ls-remote exits 0 when the branch exists, 2 when it does not. Probe the
        # FULL ref: a bare `solution` pattern tail-matches, so an unrelated
        # `feature/solution` branch would exit 0 and wrongly refuse the scaffold.
        if (
            git(
                "-C",
                str(wd),
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                f"refs/heads/{SOLUTION_BRANCH}",
            )[0]
            == 0
        ):
            log_err(
                "  ! solution branch already exists - re-run refuses to overwrite it "
                f"(delete {org}/{repo}'s solution branch first if you really want it rebuilt)"
            )
            return 1
        if (
            git("-C", str(wd), *GIT_ENV, "checkout", "-q", "-b", SOLUTION_BRANCH)[0]
            != 0
        ):
            # Any other local failure here must not be swallowed and then misreported as
            # a push failure below.
            log_err("  ! could not create the solution branch")
            return 1
        sol = wd / SOLUTION_DIR
        sol.mkdir()
        # `starter`, not `solution`: `derive` writes `solution/X` onto `main` as `X`, so a
        # model answer called `solution.ipynb` derives a SECOND notebook beside the
        # untouched starter instead of becoming it. The stem AND the suffix are the same
        # contract on both branches (see `_model_answer` and `_STARTERS`).
        for fmt in formats:
            model = _model_answer(number, fmt)
            if model is not None:
                (sol / starter_name(fmt)).write_text(model)
        (sol / "README.md").write_text(
            f"# Assignment {number} - model solution\n\n"
            "Goes out to students after the deadline, two ways:\n\n"
            "- **On a clock** - set `solution_datetime:` on this assignment in the "
            "cohort's `classroom-config/schedule.yml`, beside its `due_datetime`. The "
            "hourly cron pushes this folder into every student/team repo at that "
            "moment. Needs `handout_datetime:` set too - the schedule can only push a "
            "solution into repos it provisioned. There is no default: leave it out and "
            "the solution never ships automatically.\n"
            "- **By hand** - run **Release assignment** with **include_solution** "
            "ticked.\n\n"
            "Both do the same thing, idempotently, so a scheduled release you then "
            "re-run by hand changes nothing.\n"
        )
        # The assignment's whole definition, from the button's answers and the course's
        # own defaults - edit this file to change any of it later.
        (wd / "grading_config.yml").write_text(
            _grading_config(
                title=title,
                kind=kind,
                team_formation=team_formation,
                submit_via=submit_via,
                formats=formats,
                autograde=autograde,
                defaults=defaults,
            )
        )
        # Hidden tests ONLY when the assignment asked to be autograded. Seeded next to a
        # hand-marked assignment they were never written for, they read as work the course
        # is expected to do - and `autograde: true` over a `tests/` full of placeholders is
        # a machine score nobody meant.
        if autograde:
            tests = wd / "tests"
            tests.mkdir()
            (tests / "test_solution.py").write_text(
                _HIDDEN_TEST_NOTEBOOK if "ipynb" in formats else _HIDDEN_TEST_PY
            )
        git("-C", str(wd), *GIT_ENV, "add", "-A")
        git(
            "-C",
            str(wd),
            *GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            f"solution: assignment {number} (model answer + grading_config.yml)",
        )
        if (
            git("-C", str(wd), *GIT_ENV, "push", "-q", "-u", "origin", SOLUTION_BRANCH)[
                0
            ]
            != 0
        ):
            log_err("  ! could not push the solution branch")
            return 1
    if not seeded:
        log_err(
            "  ! the starter files could not be written - the assignment template is "
            "incomplete"
        )
        return 1
    if _seed_template_workflows(org, repo):
        return 1
    log_ok(f"assignment template ready: {org}/{repo} (main + solution)")
    return 0


def _latest_deploy_run_id(org: str, site: str) -> str | None:
    """Newest deploy.yml run id for the site repo, or None if there are none yet."""
    code, out = gh(
        "api",
        f"repos/{org}/{site}/actions/workflows/deploy.yml/runs",
        "--jq",
        ".workflow_runs[0].id // empty",
    )
    return out.strip() if code == 0 and out.strip() else None


def _await_run(org: str, site: str, run_id: str, timeout: int = 180) -> str | None:
    """Poll a workflow run to completion; return its conclusion (e.g. 'success',
    'failure') or None on timeout."""
    waited = 0
    while waited < timeout:
        code, out = gh(
            "api",
            f"repos/{org}/{site}/actions/runs/{run_id}",
            "--jq",
            ".status,.conclusion",
        )
        if code == 0:
            parts = out.split()
            if parts and parts[0] == "completed":
                return parts[1] if len(parts) > 1 else ""
        time.sleep(6)
        waited += 6
    return None


def _dispatch_deploy(org: str, site: str) -> str | None:
    """Dispatch deploy.yml and return the id of the run it triggers, or None. The
    workflow takes a few seconds to index after it is seeded, so retry the
    dispatch; then wait for a new run (distinct from any prior one) to appear."""
    before = _latest_deploy_run_id(org, site)
    for _ in range(6):
        if gh("workflow", "run", "deploy.yml", "--repo", f"{org}/{site}")[0] == 0:
            break
        time.sleep(5)
    else:
        return None
    for _ in range(10):
        rid = _latest_deploy_run_id(org, site)
        if rid and rid != before:
            return rid
        time.sleep(3)
    return None


def scaffold_site(org: str) -> int:
    """Create an org's public website repo, seed its Pages build and enable GitHub Pages.
    Used for both the per-cohort student-facing site and the opt-in public course site -
    the org is whatever's passed.

    The repo is created EMPTY and the first `site sync` writes the site into it: the whole
    of a cohort site ships from `templates/site/` and `templates/site-seed/` in this repo,
    so there is no template repo to fall behind them.

    The repo is named `<org>.github.io` so it serves at the org root. It must be PUBLIC
    on the Free plan (Pages requires it); on GitHub Enterprise Cloud / Campus it can be
    made private with Pages access control. The site redeploys on every push."""
    site = pages_repo(org)
    log_step(f"Scaffolding website {org}/{site}")
    if repo_exists(org, site):
        log_skip(f"repo {org}/{site}")
    elif not create_repo(
        org,
        site,
        private=False,
        # Generated and rewritten on every sync, which is what the reader needs to know
        # from the org's landing page.
        description="[do not touch]: Course website (auto-deployed)",
    ):
        return 1

    # The first commit, through the Contents API - the only one that will create it in a
    # repo that has none (see gh_contents._commit_tree). It has to be the deploy workflow:
    # the two calls below enable Pages on it and dispatch it.
    if not put_files(
        org,
        site,
        {
            SITE_DEPLOY_WORKFLOW: (
                TEMPLATES / "site" / SITE_DEPLOY_WORKFLOW
            ).read_bytes()
        },
        "site: seed the Pages build",
        create_only=True,
    ):
        log_err(f"  ! could not seed the Pages build into {org}/{site}")
        return 1

    # Enable Pages with the GitHub Actions ("workflow") build, so deploy.yml publishes the
    # site. Ignore "already enabled".
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{site}/pages",
        "-f",
        "build_type=workflow",
    )
    if code != 0 and not is_already_exists(out):
        # POST creates; PUT updates a site that already has a different build type. Its
        # return used to be dropped, so a repo where BOTH calls failed - no Pages at all -
        # went on to "site scaffolded -> https://...", a URL that has never served
        # anything. Nothing downstream re-enables Pages, so this is the only chance.
        code, out = gh(
            "api",
            "--method",
            "PUT",
            f"repos/{org}/{site}/pages",
            "-f",
            "build_type=workflow",
        )
        if code != 0:
            log_err(f"  ! could not enable Pages on {org}/{site}: {out[:200]}")
            return 1

    # The auto-created github-pages environment restricts which branches may deploy -
    # clear the policy so any branch (the default, plus sync-site's pushes)
    # can deploy. Not fatal: Pages IS on, the default branch usually deploys anyway, and
    # the environment can lag its repo - but a silent failure here is what makes a
    # sync-site push deploy nothing, so say it.
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{site}/environments/github-pages",
        "-F",
        "deployment_branch_policy=null",
    )
    if code != 0:
        log_err(
            f"  ! could not clear the github-pages branch policy on {org}/{site}: "
            f"{out[:160]} - pushes from a non-default branch will not deploy"
        )

    # Seeding a workflow through the API doesn't fire it, so kick the first deploy by hand AND
    # confirm it lands. Enabling Pages with build_type=workflow races the platform's
    # provisioning, so the first deploy often fails transiently ("Deployment failed, try
    # again later"); re-dispatch a couple of times, waiting for each run to finish. A
    # miss is non-fatal - the site deploys on the first content push (your first Release)
    # anyway - but confirming here avoids a freshly-bootstrapped org showing a dead site.
    for attempt in range(1, 4):
        run_id = _dispatch_deploy(org, site)
        if run_id is None:
            continue
        conclusion = _await_run(org, site, run_id)
        if conclusion == "success":
            log_ok(f"site deployed -> https://{site}/")
            return 0
        log(
            f"  (deploy attempt {attempt} did not succeed: {conclusion or 'timed out'})"
        )
        time.sleep(10)
    log(
        "  (site not deployed yet - it will deploy on the next push to the site repo, "
        "e.g. your first Release materials)"
    )
    log_ok(f"site scaffolded -> https://{site}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("materials")
    pm.add_argument("--org", required=True)
    pm.add_argument("--tag", required=True, help="Year tag, e.g. f2026 or s2026")
    pm.add_argument(
        "--copy-from",
        dest="copy_from",
        default="",
        help="An existing course-materials-* repo to start from: its branches and their "
        "history are copied into the new repo, and only the SYSTEM-owned files are "
        "rewritten. Omit it for the fresh skeleton.",
    )
    pa = sub.add_parser("assignment")
    pa.add_argument("--org", required=True)
    pa.add_argument("--number", required=True)
    pa.add_argument("--tag", required=True, help="Year tag, e.g. f2026 or s2026")
    pa.add_argument(
        "--name",
        default="",
        help="The assignment's name, e.g. 'Neural networks from scratch' (default: "
        "'Assignment <number>'). Goes into grading_config.yml as `title:`.",
    )
    pa.add_argument(
        "--format",
        dest="formats",
        default="py",
        help="Which starter stubs to seed on main, and nothing else: a comma-separated "
        f"list of {', '.join(FORMATS)}, with `none` on its own for no starter at all "
        "(grading reads whatever is in the repo either way)",
    )
    pa.add_argument(
        "--type",
        dest="kind",
        choices=list(ASSIGNMENT_TYPES),
        default="individual",
        help="individual = one repo per student; group = one repo per team (teams.csv)",
    )
    pa.add_argument(
        "--team-formation",
        dest="team_formation",
        choices=list(TEAM_FORMATIONS),
        default="self_select",
        help="Group assignments only: self_select = students use the welcome repo's "
        "'Join team' form; assigned = you write classroom-config/teams.csv",
    )
    pa.add_argument(
        "--submit-via",
        dest="submit_via",
        choices=list(SUBMIT_VIA),
        default="github",
        help="external = handed in off GitHub (Moodle, Kaggle, in class): the repo "
        "carries the brief and the Feedback issue, and nothing is ever collected",
    )
    pa.add_argument(
        "--autograde",
        choices=["false", "true"],
        default="false",
        help="true = seed a tests/ stub on the solution branch and run it at the "
        "cutoff; the count is shown to graders and never to a student",
    )
    pa.add_argument(
        "--copy-from",
        dest="copy_from",
        default="",
        help="An existing assignment-* template to start from: `main` and `solution` are "
        "copied whole, and every option above is ignored - the copied "
        "grading_config.yml defines the assignment.",
    )
    ps = sub.add_parser("site")
    ps.add_argument("--org", required=True)
    args = parser.parse_args()
    # The `--format` box, refused here - before the repo exists, so a mistyped answer
    # costs a re-run and nothing else. Caught on its OWN, the way `deploy.main` catches
    # `parse_path_pairs`: a ValueError from anywhere deeper is a bug and still earns its
    # traceback, rather than being printed as though a faculty member had mistyped a box.
    if args.cmd == "assignment":
        try:
            formats = parse_formats(args.formats)
        except ValueError as exc:
            log_err(str(exc))
            return 1
    # scaffold_materials equips the new repo's Release workflows, which reads the cohort
    # registry + assignment list; a read helper that couldn't reach the API raises, and in
    # an Actions log a one-line error beats a traceback.
    try:
        if args.cmd == "materials":
            return scaffold_materials(args.org, args.tag, args.copy_from)
        if args.cmd == "site":
            return scaffold_site(args.org)
        return scaffold_assignment(
            args.org,
            args.number,
            args.tag,
            formats,
            args.kind,
            name=args.name,
            team_formation=args.team_formation,
            submit_via=args.submit_via,
            autograde=args.autograde == "true",
            copy_from=args.copy_from,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
