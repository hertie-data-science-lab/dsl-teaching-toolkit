"""dsl-course derive -- write a template's student starter from its model solution.

ONE authored file, two branches. Faculty keep the notebook (or Rmd/qmd/py) they actually
teach from on the template's `solution` branch, under `solution/`, with the answers fenced
off in the nbgrader/Otter vocabulary every notebook toolchain already speaks:

    ### BEGIN SOLUTION
    model = fit(X, y)
    ### END SOLUTION

    (or a whole cell tagged `solution`, or an Rmd chunk with `solution=TRUE`)

**Derive student version** reads that branch, replaces each fenced region with a
placeholder, and writes the result onto `main` - which is the branch, and the only branch,
that native template-generate copies into a student's repo. So the starter is never
maintained by hand beside the answer it is meant to be missing, and the two cannot drift.

    solution branch:  solution/starter.ipynb   (the model answer, fenced)
            |  derive
            v
    main:             starter.ipynb            (what students get)

Three rules the transformations keep, because each of them is a way of shipping the answer
by accident:

1. **A file with nothing fenced is never written.** No markers and no `solution` tag means
   the derived file WOULD BE the model answer, byte for byte, published as the starter.
   That is reported per file and reds the run rather than being written.
2. **An unbalanced fence is refused**, not guessed at. A `BEGIN` with no `END` could as
   easily mean "the rest of this cell is the answer" as "the marker is a typo", and one of
   those two readings publishes it.
3. **A stripped code cell loses its outputs.** A solution notebook is usually a RUN
   notebook, and its stored outputs are the answers in print - the numbers, the fitted
   coefficients, the plots. Only cells this pass actually changed are cleared; a cell it
   left alone is left alone whole.

Nothing here ever writes to `solution`. `main` is the only destination, through the same
`put_files` every other seeded write uses - so an unchanged starter is no commit at all.

Usage:
    python3 -m dsl_course.derive --course-org hertie-dsl-demo-course-e1234 \\
        --course-source-repo assignment-1-f2026 [--no-dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import PurePosixPath
from typing import NamedTuple

from .course import SOLUTION_BRANCH, SOLUTION_DIR
from .gh_contents import get_file_content, put_files, repo_tree
from .log import log, log_err, log_ok, log_step

# ------------------------------------------------------------------ the fence vocabulary

# nbgrader writes `### BEGIN SOLUTION`, Otter writes `# BEGIN SOLUTION`, and people type
# `#BEGIN SOLUTION`. All three are the same instruction, so the match is on the WORDS: a
# line that is nothing but comment hashes and the phrase. Case-insensitive, because a
# marker that reads correctly to a human and not to this parser is the worst of both.
_MARKER = re.compile(r"^\s*#*\s*(BEGIN|END)\s+SOLUTION\s*$", re.IGNORECASE)

# The cell-level form: nbgrader's `solution` tag on a whole cell, which is how a written
# (markdown) answer is marked - there is no comment syntax to hang a fence off.
SOLUTION_TAG = "solution"

# The Rmd/qmd form: a chunk option. `solution=TRUE` is the knitr spelling; `T` and lower
# case are what people actually type. The option is REMOVED from the header this writes,
# not just honoured - a student starter must not carry a faculty-only flag that says which
# chunks held the answer.
_SOLUTION_OPT = re.compile(r"\s*,?\s*solution\s*=\s*(?:TRUE|T|true)\b", re.IGNORECASE)
_CHUNK_OPEN = re.compile(r"^(\s*)(`{3,})\s*\{(.*)\}\s*$")
_CHUNK_CLOSE = re.compile(r"^\s*`{3,}\s*$")

# What replaces a fenced region. Python gets a statement, because an empty function body is
# a SyntaxError and a starter that will not even parse teaches nothing; every other language
# here gets a comment. The words are the same in all three so a student meets one phrase.
PY_PLACEHOLDER = "pass  # YOUR CODE HERE"
CODE_PLACEHOLDER = "# YOUR CODE HERE"
TEXT_PLACEHOLDER = "_YOUR ANSWER HERE_"

# The suffixes this knows how to strip. Anything else under `solution/` is left where it is:
# writing a faculty data file or a stray PDF onto `main` from a button called "derive the
# student version" is a surprise, and `Release materials` is how a file gets published.
DERIVABLE = (".ipynb", ".rmd", ".qmd", ".py", ".r")

COMMIT_MESSAGE = "chore: derive the student starter from the solution branch"


class DeriveError(ValueError):
    """A file that cannot be derived SAFELY - malformed JSON, or a fence that does not
    close. Raised rather than worked around: every recoverable reading of a broken fence
    ends with the model answer on `main`."""


class Stripped(NamedTuple):
    """One derived file: the text to write, and what was taken out of it.

    The two counts are the whole of what a dry run reports. They are also the safety
    predicate - `replaced == 0` means nothing was fenced, so the "starter" is the answer."""

    text: str
    regions: int  # BEGIN/END fences replaced
    cells: int  # whole cells (a `solution` tag) or chunks (`solution=TRUE`) replaced

    @property
    def replaced(self) -> int:
        return self.regions + self.cells


# ----------------------------------------------------------------------------- pure core


def strip_regions(source: str, placeholder: str, where: str) -> tuple[str, int]:
    """Replace every `BEGIN SOLUTION` / `END SOLUTION` region in `source` with
    `placeholder`, indented as the BEGIN line was. Returns `(text, regions replaced)`.

    The indent comes from the marker rather than from the code it fences, because the code
    is gone by the time it would be measured and a `pass` at column 0 inside a function
    body is a SyntaxError. Nested and unclosed fences raise: see the module docstring."""
    out: list[str] = []
    indent: str | None = None
    count = 0
    for number, line in enumerate(source.split("\n"), 1):
        match = _MARKER.match(line)
        keyword = match.group(1).upper() if match else ""
        if keyword == "BEGIN":
            if indent is not None:
                raise DeriveError(
                    f"{where}: line {number} opens a solution region inside one that is "
                    f"still open - refusing to guess where the answer ends"
                )
            indent = line[: len(line) - len(line.lstrip())]
        elif keyword == "END":
            if indent is None:
                raise DeriveError(
                    f"{where}: line {number} closes a solution region that was never "
                    f"opened"
                )
            out.append(indent + placeholder)
            count += 1
            indent = None
        elif indent is None:
            out.append(line)
    if indent is not None:
        raise DeriveError(
            f"{where}: a solution region is opened and never closed - refusing to derive "
            f"a starter that would either keep the answer or swallow the rest of the file"
        )
    return "\n".join(out), count


def _headers_only(source: str, placeholder: str) -> str:
    """A whole-cell replacement that keeps the cell's HEADING and drops its body.

    The leading run of blank and `#`-prefixed lines is what carries the question - the
    markdown `### Question 3 (5 points)` a rubric is keyed on, or the `# Q3 (5 points)`
    comment above a code answer. Dropping it with the answer would leave the student a
    numbered placeholder with no question attached and the marks unattributable."""
    lines = source.split("\n")
    kept = 0
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            break
        kept += 1
    head = lines[:kept]
    while head and not head[-1].strip():
        head.pop()  # no blank run left dangling before the placeholder
    return "\n".join([*head, placeholder]) if head else placeholder


def _notebook_language(nb: dict) -> str:
    """The notebook's kernel language, lower-cased (`""` when it declares none)."""
    meta = nb.get("metadata") or {}
    info = meta.get("language_info") or {}
    kernel = meta.get("kernelspec") or {}
    return str(info.get("name") or kernel.get("language") or "").lower()


def _cell_source(cell: dict) -> str:
    """A cell's source as one string, whichever of the two shapes nbformat wrote it in."""
    source = cell.get("source")
    return "".join(source) if isinstance(source, list) else str(source or "")


def strip_notebook(text: str, where: str) -> Stripped:
    """Derive a student notebook from a solution notebook.

    Both fences are honoured: a `solution` TAG replaces the whole cell (keeping its
    heading), and BEGIN/END regions replace part of one. A code cell this pass changed
    also loses its stored outputs and execution count - see rule 3 in the module
    docstring."""
    try:
        nb = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeriveError(f"{where}: not a readable notebook - {exc}") from exc
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise DeriveError(f"{where}: not a readable notebook - no `cells` array")

    python = _notebook_language(nb).startswith("python") or not _notebook_language(nb)
    regions = cells = 0
    for cell in nb["cells"]:
        if not isinstance(cell, dict):
            continue
        markdown = cell.get("cell_type") == "markdown"
        placeholder = (
            TEXT_PLACEHOLDER
            if markdown
            else (PY_PLACEHOLDER if python else CODE_PLACEHOLDER)
        )
        source = _cell_source(cell)
        tags = (cell.get("metadata") or {}).get("tags") or []
        if SOLUTION_TAG in tags:
            derived, replaced = _headers_only(source, placeholder), 0
            cells += 1
        else:
            derived, replaced = strip_regions(source, placeholder, where)
            regions += replaced
        if derived == source:
            continue
        cell["source"] = (
            derived.splitlines(keepends=True)
            if isinstance(cell.get("source"), list)
            else derived
        )
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
    # indent=1 with a trailing newline is what nbformat itself writes, so a derived
    # notebook diffs against a hand-saved one instead of against its own formatting.
    return Stripped(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", regions, cells)


def strip_rmd(text: str, where: str) -> Stripped:
    """Derive a student Rmd/qmd from a solution one.

    Two fences again: BEGIN/END regions anywhere in the file, and a whole chunk marked
    `solution=TRUE`, whose body becomes the placeholder comment. The chunk HEADER survives
    (minus the flag) because it carries the chunk's name and its knitr options, which the
    document's cross-references and figure sizes depend on."""
    body, regions = strip_regions(text, CODE_PLACEHOLDER, where)
    out: list[str] = []
    chunks = 0
    skipping = False
    for line in body.split("\n"):
        if skipping:
            if _CHUNK_CLOSE.match(line):
                out.append(line)
                skipping = False
            continue
        opened = _CHUNK_OPEN.match(line)
        if opened and _SOLUTION_OPT.search(opened.group(3)):
            indent, fence, options = opened.groups()
            out.append(f"{indent}{fence}{{{_SOLUTION_OPT.sub('', options).strip()}}}")
            out.append(f"{indent}{CODE_PLACEHOLDER}")
            chunks += 1
            skipping = True
            continue
        out.append(line)
    return Stripped("\n".join(out), regions, chunks)


def strip_source(path: str, text: str) -> Stripped:
    """Derive one file, dispatching on its suffix. `path` is used for the error messages
    as well, so a refusal names the file a faculty member has to go and fix."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".ipynb":
        return strip_notebook(text, path)
    if suffix in (".rmd", ".qmd"):
        return strip_rmd(text, path)
    placeholder = PY_PLACEHOLDER if suffix == ".py" else CODE_PLACEHOLDER
    derived, regions = strip_regions(text, placeholder, path)
    return Stripped(derived, regions, 0)


# ------------------------------------------------- the grader's copy of one submission

# Otter's manual-grading fences, written in a MARKDOWN cell (notebook) or on their own line
# (Rmd/qmd). Everything from a BEGIN to the next END is one hand-marked question; everything
# outside them is setup, imports and machine-marked work a grader does not read.
#
# The attribute form Otter also writes - `<!-- BEGIN QUESTION\nname: q1\nmanual: true\n-->`
# - opens with the same two words, which is why this matches the OPENING rather than the
# whole comment.
_QUESTION = re.compile(r"<!--\s*(BEGIN|END)\s+QUESTION\b")


class Filtered(NamedTuple):
    """One submission filtered to its hand-marked questions, and how many were found.

    `questions == 0` is the ordinary "this assignment does not use the fences" answer, not a
    fault: the caller archives nothing rather than a whole notebook nobody asked for."""

    text: str
    questions: int


def _question_marker(source: str) -> str:
    """`"BEGIN"`, `"END"` or `""` for a cell/line, by its FIRST question fence.

    First, not last: a cell holding both closes the question it opened, and treating it as
    an END would run the next question's content into this one."""
    found = _QUESTION.search(source)
    return found.group(1).upper() if found else ""


def _without_markers(source: str) -> str:
    """The same text with the fence comments taken out - they are Otter's plumbing, and a
    grader reading the exported page should see the question, not the tooling."""
    return "\n".join(
        line for line in source.split("\n") if not _QUESTION.search(line)
    ).strip("\n")


def filter_notebook_questions(text: str, where: str) -> Filtered:
    """Keep only the cells inside `<!-- BEGIN QUESTION -->` ... `<!-- END QUESTION -->`.

    The notebook's own metadata (kernel, language, nbformat) travels with them, because it
    is what an exporter needs to render the result at all. A fence that never closes raises,
    like every other unbalanced fence here: the alternative is a "filtered" grader copy that
    is silently the whole submission."""
    try:
        nb = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeriveError(f"{where}: not a readable notebook - {exc}") from exc
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        raise DeriveError(f"{where}: not a readable notebook - no `cells` array")
    kept: list[dict] = []
    inside = False
    questions = 0
    for cell in nb["cells"]:
        if not isinstance(cell, dict):
            continue
        marker = _question_marker(_cell_source(cell))
        if marker == "BEGIN":
            inside = True
        if inside:
            if marker:
                stripped = _without_markers(_cell_source(cell))
                if not stripped:
                    # A fence on its own in a cell of its own: dropped rather than exported
                    # as a blank page-break in the middle of the question.
                    if marker == "END":
                        inside, questions = False, questions + 1
                    continue
                cell = {**cell, "source": stripped}
            kept.append(cell)
        if marker == "END":
            if not inside:
                raise DeriveError(
                    f"{where}: a question is closed that was never opened"
                )
            inside, questions = False, questions + 1
    if inside:
        raise DeriveError(f"{where}: a question is opened and never closed")
    return Filtered(json.dumps({**nb, "cells": kept}, indent=1) + "\n", questions)


def filter_rmd_questions(text: str, where: str) -> Filtered:
    """The same filter for an Rmd/qmd, line by line, keeping the YAML front matter.

    The front matter is not optional decoration: it carries the title, the output format
    and the knitr options, and a document without it renders as plain text or not at all."""
    lines = text.split("\n")
    kept: list[str] = []
    start = 0
    if lines and lines[0].strip() == "---":
        end = next(
            (n for n, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0
        )
        if end:
            kept, start = lines[: end + 1], end + 1
    inside = False
    questions = 0
    for line in lines[start:]:
        marker = _question_marker(line)
        if marker == "BEGIN":
            if inside:
                raise DeriveError(f"{where}: a question is opened inside another")
            inside = True
            continue
        if marker == "END":
            if not inside:
                raise DeriveError(
                    f"{where}: a question is closed that was never opened"
                )
            inside, questions = False, questions + 1
            continue
        if inside:
            kept.append(line)
    if inside:
        raise DeriveError(f"{where}: a question is opened and never closed")
    return Filtered("\n".join(kept) + "\n", questions)


def filter_questions(path: str, text: str) -> Filtered:
    """Filter one submission document to its hand-marked questions, by suffix."""
    if PurePosixPath(path).suffix.lower() == ".ipynb":
        return filter_notebook_questions(text, path)
    return filter_rmd_questions(text, path)


def student_path(path: str) -> str:
    """Where a `solution/`-relative source lands on `main`: the same tree, one level up.

    `solution/starter.ipynb` is the file students get as `starter.ipynb`, and
    `solution/data/prep.py` as `data/prep.py` - the folder is the branch's, not the
    assignment's."""
    return path[len(SOLUTION_DIR) + 1 :]


def derivable_sources(tree: tuple[str, ...] | list[str]) -> list[str]:
    """Every path in a `solution`-branch listing this knows how to derive from."""
    prefix = f"{SOLUTION_DIR}/"
    return sorted(
        path
        for path in tree
        if path.startswith(prefix)
        and PurePosixPath(path).suffix.lower() in DERIVABLE
        and student_path(path)
    )


# -------------------------------------------------------------------------- the button


def derive_student_version(course_org: str, template: str, dry_run: bool = True) -> int:
    """Write `template`'s student starter onto `main` from its `solution` branch.

    0 when every derivable file was derived (and written, on a real run); 1 if any of them
    could not be, which includes the "nothing was fenced" refusal. Partial success is still
    a failure: a starter set where one file kept its answers is not a starter set, and the
    faculty member has to be told which file rather than left to notice.

    No student, repo or person is named anywhere in this run's output - it walks a COURSE
    org's own template - so everything here is an ordinary log line."""
    log_step(
        f"Deriving the student version of {course_org}/{template} from "
        f"`{SOLUTION_BRANCH}`{' (dry run)' if dry_run else ''}"
    )
    try:
        tree = repo_tree(course_org, template, SOLUTION_BRANCH, "blob")
    except RuntimeError as exc:
        log_err(f"could not read {template}'s `{SOLUTION_BRANCH}` branch: {exc}")
        return 1
    sources = derivable_sources(tree)
    if not sources:
        log_err(
            f"{course_org}/{template} has no {'/'.join(DERIVABLE)} file under "
            f"`{SOLUTION_DIR}/` on its `{SOLUTION_BRANCH}` branch - there is nothing to "
            f"derive a starter from. Put the notebook you teach from there first."
        )
        return 1

    files: dict[str, bytes] = {}
    regions = cells = failures = 0
    for path in sources:
        text = get_file_content(course_org, template, path, ref=SOLUTION_BRANCH)
        if text is None:
            # The tree listed it a moment ago, so this is a race or a permission fault
            # rather than an absence - either way the derived set is incomplete.
            log_err(f"  ! {path} could not be read - not derived")
            failures += 1
            continue
        try:
            stripped = strip_source(path, text)
        except DeriveError as exc:
            log_err(f"  ! {exc}")
            failures += 1
            continue
        if not stripped.replaced:
            log_err(
                f"  ! {path} has no `BEGIN SOLUTION` region, no `{SOLUTION_TAG}` cell tag "
                f"and no `solution=TRUE` chunk - NOT written, because the starter derived "
                f"from it would be the model answer itself"
            )
            failures += 1
            continue
        files[student_path(path)] = stripped.text.encode()
        regions += stripped.regions
        cells += stripped.cells
        log(
            f"  {path} -> {student_path(path)}: {stripped.regions} region(s), "
            f"{stripped.cells} cell(s)/chunk(s) replaced"
        )

    summary = f"{len(files)} file(s), {regions} region(s) and {cells} cell(s)/chunk(s) replaced"
    if dry_run:
        # The file LIST and the counts, never a line of the content: this log is the course
        # org's public `.github` Actions tab, and the content is the model answer.
        log_ok(f"dry run: would write {summary} onto main")
        return 1 if failures else 0
    if files and not put_files(course_org, template, files, COMMIT_MESSAGE):
        log_err(
            f"the derived starter was NOT written to {course_org}/{template} - main still "
            f"holds whatever it held before; re-run once the cause is fixed"
        )
        return 1
    if failures:
        log_err(
            f"{failures} file(s) could not be derived (named above) - main has {summary}, "
            f"and the rest of the starter is still whatever was already there"
        )
        return 1
    log_ok(f"{course_org}/{template} main <- {summary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True, help="Course org (the template)")
    parser.add_argument(
        "--course-source-repo",
        dest="template",
        required=True,
        help="Assignment template repo (e.g. assignment-1-f2026)",
    )
    # Default ON, like every other write button: the rendered workflow passes --dry-run /
    # --no-dry-run explicitly, so a bare local invocation cannot overwrite a starter.
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="List the files and the counts; write nothing to main (default).",
    )
    args = parser.parse_args()
    # A read helper that could not reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return derive_student_version(
            args.course_org, args.template, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
