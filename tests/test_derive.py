"""Deriving a student starter from the model solution.

Everything here is about ONE hazard: the derived file is published to students, so a
transformation that quietly does nothing publishes the answer. The transformations are
pure and tested on real notebook JSON and real Rmd text; the button around them is tested
for the refusals, not for the happy path alone.
"""

from __future__ import annotations

import json

import pytest

from dsl_course import derive

# A solution notebook in the shape nbformat writes: a markdown question, a fenced code
# answer, an untouched helper cell, and a `solution`-tagged prose answer with outputs on
# the code cells (a solution notebook is a RUN notebook).
SOLUTION_NB = {
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": ["## Question 1 (5 points)\n", "\n", "Fit the model.\n"],
        },
        {
            "cell_type": "code",
            "execution_count": 7,
            "metadata": {},
            "outputs": [{"output_type": "stream", "text": ["accuracy 0.93\n"]}],
            "source": [
                "def fit(x, y):\n",
                "    ### BEGIN SOLUTION\n",
                "    return x @ y\n",
                "    ### END SOLUTION\n",
            ],
        },
        {
            "cell_type": "code",
            "execution_count": 8,
            "metadata": {},
            "outputs": [{"output_type": "stream", "text": ["loaded\n"]}],
            "source": ["import numpy as np\n"],
        },
        {
            "cell_type": "markdown",
            "metadata": {"tags": ["solution"]},
            "source": [
                "### Question 2 (3 points)\n",
                "\n",
                "Because the estimator is unbiased.\n",
            ],
        },
    ],
    "metadata": {"language_info": {"name": "python"}},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def _derived(nb: dict) -> tuple[dict, derive.Stripped]:
    stripped = derive.strip_notebook(json.dumps(nb), "solution/starter.ipynb")
    return json.loads(stripped.text), stripped


def _source(cell: dict) -> str:
    return (
        "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
    )


def test_a_fenced_region_becomes_a_placeholder_at_its_own_indent():
    out, stripped = _derived(SOLUTION_NB)
    assert stripped.regions == 1
    assert _source(out["cells"][1]) == f"def fit(x, y):\n    {derive.PY_PLACEHOLDER}\n"
    # The answer itself is gone, markers and all.
    assert "x @ y" not in stripped.text
    assert "BEGIN SOLUTION" not in stripped.text


def test_a_solution_tagged_cell_keeps_its_question_and_loses_its_answer():
    out, stripped = _derived(SOLUTION_NB)
    assert stripped.cells == 1
    assert _source(out["cells"][3]) == (
        f"### Question 2 (3 points)\n{derive.TEXT_PLACEHOLDER}"
    )
    assert "unbiased" not in stripped.text


def test_a_stripped_code_cell_loses_the_outputs_that_are_the_answer_in_print():
    out, _ = _derived(SOLUTION_NB)
    assert out["cells"][1]["outputs"] == []
    assert out["cells"][1]["execution_count"] is None
    # A cell this pass did not change keeps everything, outputs included: clearing those
    # too would throw away worked examples the brief deliberately shows.
    assert out["cells"][2]["outputs"]
    assert out["cells"][2]["execution_count"] == 8


def test_an_r_notebook_gets_a_comment_where_python_gets_pass():
    nb = json.loads(json.dumps(SOLUTION_NB))
    nb["metadata"]["language_info"]["name"] = "R"
    out, _ = _derived(nb)
    assert (
        _source(out["cells"][1]) == f"def fit(x, y):\n    {derive.CODE_PLACEHOLDER}\n"
    )
    assert "pass" not in _source(out["cells"][1])


def test_a_notebook_with_nothing_fenced_reports_zero_rather_than_a_quiet_copy():
    nb = {"cells": [{"cell_type": "code", "metadata": {}, "source": "answer = 42\n"}]}
    stripped = derive.strip_notebook(json.dumps(nb), "solution/x.ipynb")
    assert stripped.replaced == 0


@pytest.mark.parametrize(
    "source",
    [
        "### BEGIN SOLUTION\nx = 1\n",  # never closed
        "x = 1\n### END SOLUTION\n",  # never opened
        "### BEGIN SOLUTION\n### BEGIN SOLUTION\nx = 1\n### END SOLUTION\n",  # nested
    ],
)
def test_an_unbalanced_fence_is_refused_rather_than_guessed_at(source):
    with pytest.raises(derive.DeriveError):
        derive.strip_regions(source, derive.PY_PLACEHOLDER, "solution/x.py")


def test_a_notebook_that_is_not_json_is_refused():
    with pytest.raises(derive.DeriveError):
        derive.strip_notebook("not a notebook", "solution/x.ipynb")


@pytest.mark.parametrize(
    "marker", ["### BEGIN SOLUTION", "# BEGIN SOLUTION", "#begin solution"]
)
def test_every_spelling_the_toolchains_use_is_one_marker(marker):
    text, count = derive.strip_regions(
        f"{marker}\nx = 1\n### END SOLUTION\n", derive.PY_PLACEHOLDER, "x.py"
    )
    assert count == 1
    assert text.strip() == derive.PY_PLACEHOLDER


RMD = """---
title: Assignment 1
---

## Question 1 (4 points)

```{r fit, echo=TRUE, solution=TRUE}
model <- lm(y ~ x, data = d)
summary(model)
```

```{r plot, fig.width=6}
plot(d)
```
"""


def test_a_solution_chunk_keeps_its_header_and_loses_its_body():
    stripped = derive.strip_rmd(RMD, "solution/report.Rmd")
    assert stripped.cells == 1
    assert "```{r fit, echo=TRUE}" in stripped.text
    assert "lm(y ~ x" not in stripped.text
    assert derive.CODE_PLACEHOLDER in stripped.text
    # The flag itself must not travel to students - it labels which chunks held the answer.
    assert "solution=TRUE" not in stripped.text


def test_an_ordinary_chunk_is_untouched_options_and_all():
    stripped = derive.strip_rmd(RMD, "solution/report.Rmd")
    assert "```{r plot, fig.width=6}\nplot(d)\n```" in stripped.text


def test_rmd_also_honours_the_line_fences():
    stripped = derive.strip_rmd(
        "```{r}\n### BEGIN SOLUTION\nx <- 1\n### END SOLUTION\n```\n", "r.Rmd"
    )
    assert stripped.regions == 1
    assert "x <- 1" not in stripped.text


def test_only_the_formats_it_can_strip_are_picked_up_and_the_folder_is_dropped():
    tree = (
        "grading_config.yml",
        "solution/starter.ipynb",
        "solution/notes/helper.py",
        "solution/data/train.csv",
        "solution/",
        "tests/test_x.py",
    )
    assert derive.derivable_sources(tree) == [
        "solution/notes/helper.py",
        "solution/starter.ipynb",
    ]
    assert derive.student_path("solution/notes/helper.py") == "notes/helper.py"


# ------------------------------------------------------------------------- the button


class _Repo:
    """A template repo's `solution` branch, and whatever the run writes to `main`."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.written: dict[str, bytes] = {}
        self.commits = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(
            derive, "repo_tree", lambda o, r, b, k: tuple(sorted(self.files))
        )
        monkeypatch.setattr(
            derive,
            "get_file_content",
            lambda o, r, path, ref="": self.files.get(path),
        )

        def put_files(org, repo, files, message):
            self.commits += 1
            self.written.update(files)
            return True

        monkeypatch.setattr(derive, "put_files", put_files)
        return self


FENCED_PY = "def fit():\n    ### BEGIN SOLUTION\n    return 1\n    ### END SOLUTION\n"


def test_the_run_writes_main_and_never_reads_or_writes_anything_but_solution(
    monkeypatch,
):
    repo = _Repo({"solution/starter.py": FENCED_PY}).install(monkeypatch)
    refs = []
    monkeypatch.setattr(
        derive,
        "get_file_content",
        lambda o, r, path, ref="": refs.append(ref) or repo.files.get(path),
    )
    assert derive.derive_student_version("Course", "assignment-1-f2026", False) == 0
    assert refs == [derive.SOLUTION_BRANCH]
    assert list(repo.written) == ["starter.py"]
    assert derive.PY_PLACEHOLDER in repo.written["starter.py"].decode()


def test_a_dry_run_writes_nothing(monkeypatch):
    repo = _Repo({"solution/starter.py": FENCED_PY}).install(monkeypatch)
    assert derive.derive_student_version("Course", "assignment-1-f2026", True) == 0
    assert repo.commits == 0


def test_a_file_with_nothing_fenced_is_refused_rather_than_published(
    monkeypatch, capsys
):
    repo = _Repo({"solution/starter.py": "def fit():\n    return 1\n"}).install(
        monkeypatch
    )
    assert derive.derive_student_version("Course", "assignment-1-f2026", False) == 1
    assert repo.written == {}
    assert "NOT written" in capsys.readouterr().err


def test_one_broken_file_reds_the_run_even_though_the_others_landed(monkeypatch):
    repo = _Repo(
        {
            "solution/starter.py": FENCED_PY,
            "solution/broken.py": "### BEGIN SOLUTION\nx = 1\n",
        }
    ).install(monkeypatch)
    assert derive.derive_student_version("Course", "assignment-1-f2026", False) == 1
    assert list(repo.written) == ["starter.py"]


def test_a_template_with_no_derivable_source_says_so(monkeypatch, capsys):
    _Repo({"grading_config.yml": "type: individual\n"}).install(monkeypatch)
    assert derive.derive_student_version("Course", "assignment-1-f2026", True) == 1
    assert "nothing to derive" in capsys.readouterr().err
