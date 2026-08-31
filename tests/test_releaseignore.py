"""`.releaseignore` matching. If this is wrong, faculty are told a file is withheld and it
is published anyway - to a private cohort repo, or to the open web.

The spec is `.gitignore`'s, so most of this file is a DIFFERENTIAL test against real git
rather than a restatement of what we think git does: each case's tree is built twice, once
with `.releaseignore` files and once with the same lines as `.gitignore`, and the set our
matcher copies must equal the set `git add -A` tracks. A hand-written expectation could be
confidently wrong in exactly the way the implementation is; git cannot.

The targeted tests below it cover what that oracle cannot see: pruning, self-exclusion
(the ONE place we deliberately depart from git - see the module docstring), the
single-path `excludes` entry point, and the repo-tree adapter, none of which git has an
equivalent for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dsl_course import releaseignore
from dsl_course.fs import copy_tree

# name -> {relpath: contents}. A `.releaseignore` entry is the rules; everything else is
# content. Ordered roughly as `man gitignore` introduces each rule.
CASES: dict[str, dict[str, str]] = {
    # --- anchoring
    "no-slash matches at any depth": {
        ".releaseignore": "solutions.ipynb\n",
        "solutions.ipynb": "",
        "w1/solutions.ipynb": "",
        "w1/lab.md": "",
    },
    "a mid-pattern slash anchors": {
        ".releaseignore": "doc/frotz\n",
        "doc/frotz": "",
        "x/doc/frotz": "",
        "doc/other": "",
    },
    "a leading slash anchors": {
        ".releaseignore": "/top.md\n",
        "top.md": "",
        "sub/top.md": "",
    },
    "a trailing slash is directory-only": {
        ".releaseignore": "build/\n",
        "build/x": "",
        "build2/x": "",
    },
    "a trailing slash does not match a file of that name": {
        ".releaseignore": "build/\n",
        "build": "",
        "keep": "",
    },
    # --- wildcards
    "a star does not cross a slash": {
        ".releaseignore": "foo/*\n",
        "foo/test.json": "",
        "foo/bar/hello.c": "",
    },
    "a leading globstar matches at any depth": {
        ".releaseignore": "**/tmp\n",
        "tmp": "",
        "a/tmp": "",
        "a/b/tmp": "",
        "a/keep": "",
    },
    "a trailing globstar matches everything inside": {
        ".releaseignore": "abc/**\n",
        "abc/x": "",
        "abc/d/y": "",
        "abd/z": "",
    },
    "a middle globstar spans zero or more directories": {
        ".releaseignore": "a/**/b\n",
        "a/b": "",
        "a/x/b": "",
        "a/x/y/b": "",
        "ab": "",
    },
    "a character class matches one of its members": {
        ".releaseignore": "q[0-9].md\n",
        "q1.md": "",
        "qz.md": "",
    },
    "a question mark matches exactly one character": {
        ".releaseignore": "l?.md\n",
        "l1.md": "",
        "l12.md": "",
    },
    # --- line syntax
    "comments and blank lines are not patterns": {
        ".releaseignore": "# a comment\n\n*.log\n",
        "x.log": "",
        "keep.md": "",
    },
    "an escaped hash is a literal hash": {
        ".releaseignore": "\\#lit\n",
        "#lit": "",
        "other": "",
    },
    "an escaped bang is a literal bang": {
        ".releaseignore": "\\!lit\n",
        "!lit": "",
        "other": "",
    },
    "trailing whitespace is stripped": {".releaseignore": "tr  \n", "tr": ""},
    # --- negation
    "a bang re-includes": {
        ".releaseignore": "*.log\n!keep.log\n",
        "a.log": "",
        "keep.log": "",
    },
    "the last matching pattern wins": {
        ".releaseignore": "!keep.log\n*.log\n",
        "keep.log": "",
    },
    "a bang cannot resurrect a file under an excluded directory": {
        ".releaseignore": "build/\n!build/keep.txt\n",
        "build/keep.txt": "",
        "build/x": "",
    },
    "the canonical exclude-everything-but idiom works": {
        ".releaseignore": "/*\n!/foo\n/foo/*\n!/foo/bar\n",
        "a.txt": "",
        "foo/x": "",
        "foo/bar/y": "",
    },
    # --- nesting
    "a child file overrides its parent": {
        ".releaseignore": "*.md\n",
        "w1/.releaseignore": "!lab.md\n",
        "w1/lab.md": "",
        "w1/other.md": "",
        "top.md": "",
    },
    "a sibling subtree does not inherit another's patterns": {
        "w1/.releaseignore": "*.md\n",
        "w1/a.md": "",
        "w2/a.md": "",
    },
    "a child's leading slash anchors to the child, not the root": {
        "w1/.releaseignore": "/only.md\n",
        "w1/only.md": "",
        "w1/deep/only.md": "",
    },
    "a child adds to its parent rather than replacing it": {
        ".releaseignore": "*.log\n",
        "w1/.releaseignore": "*.tmp\n",
        "w1/a.log": "",
        "w1/a.tmp": "",
        "w1/a.md": "",
    },
    # (Whether the file itself travels is the one deliberate departure from git, so it is
    # not a differential case - see test_the_ignore_file_never_travels.)
    # --- depth
    "an excluded directory takes its whole subtree": {
        ".releaseignore": "secret/\n",
        "secret/a/b/c.txt": "",
        "open/a/b/c.txt": "",
    },
}


def _build(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)


def _git_tracks(tmp_path: Path, files: dict[str, str]) -> set[str]:
    """What real git tracks, with every `.releaseignore`'s lines ALSO as a `.gitignore`."""
    r = tmp_path / "git"
    _build(r, files)
    for p in list(r.rglob(releaseignore.RELEASEIGNORE)):
        (p.parent / ".gitignore").write_text(p.read_text())
    subprocess.run(["git", "-c", "init.templateDir=", "init", "-q"], cwd=r, check=True)
    subprocess.run(
        ["git", "config", "core.excludesFile", "/dev/null"], cwd=r, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    out = subprocess.run(
        # Isolated from whoever runs this: a global `core.excludesFile` (a personal
        # `~/.gitignore` carrying `*.log`) or an `init.templateDir` would skew the oracle
        # towards AGREEMENT, since both sides would then hide the same file.
        ["git", "ls-files", "-z"],
        cwd=r,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    # Both ignore-file names drop out: the `.gitignore` copies are this harness's, and
    # `.releaseignore` is the one deliberate departure (git tracks its own, we withhold
    # ours - test_the_ignore_file_never_travels covers that separately).
    return {
        f
        for f in out
        if f and Path(f).name not in (".gitignore", releaseignore.RELEASEIGNORE)
    }


def _we_copy(tmp_path: Path, files: dict[str, str]) -> set[str]:
    """The FILES that reach the destination."""
    return {
        p for p in _we_copy_all(tmp_path, files) if (tmp_path / "dst" / p).is_file()
    }


def _we_copy_all(tmp_path: Path, files: dict[str, str]) -> set[str]:
    """Every entry that reaches the destination - directories included."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    _build(src, files)
    copy_tree(src, dst, releaseignore.deny_for(src))
    return {str(p.relative_to(dst)) for p in dst.rglob("*")}


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_we_withhold_exactly_what_git_ignores(case, tmp_path):
    files = CASES[case]
    assert _we_copy(tmp_path / "ours", files) == _git_tracks(tmp_path, files)


def test_the_oracle_is_not_comparing_empty_sets(tmp_path):
    """A harness bug that made both sides empty would pass every case above without
    testing anything - and several fixtures legitimately withhold every file, so the
    parametrised test cannot assert non-emptiness itself. Both sides, one known answer."""
    files = {".releaseignore": "*.log\n", "a.log": "", "keep.md": ""}
    assert _git_tracks(tmp_path, files) == {"keep.md"}
    assert _we_copy(tmp_path / "ours", files) == {"keep.md"}


# ------------------------------------------------------- self-exclusion (departs from git)


def test_the_ignore_file_never_travels(tmp_path):
    """The one deliberate departure from git, which tracks its own `.gitignore`.

    A released copy lands in a repo every student and auditor can read (`deploy` grants
    both teams `pull`), and its contents are the list of paths faculty held back. Nothing
    downstream reads a released copy, so it would ship purely to leak."""
    got = _we_copy_all(
        tmp_path, {".releaseignore": "*.log\n", "keep.md": "", "a.log": ""}
    )
    assert got == {"keep.md"}


def test_a_nested_ignore_file_never_travels_either(tmp_path):
    got = _we_copy_all(
        tmp_path, {"w1/.releaseignore": "*.log\n", "w1/keep.md": "", "w1/a.log": ""}
    )
    assert got == {"w1", "w1/keep.md"}


def test_no_pattern_can_re_include_the_ignore_file(tmp_path):
    """Not even an explicit `!`. It is withheld before the chain is consulted, so there is
    no ordering a faculty member could write that ships it."""
    got = _we_copy_all(tmp_path, {".releaseignore": "!.releaseignore\n", "keep.md": ""})
    assert got == {"keep.md"}
    assert releaseignore.excludes(tmp_path, tmp_path / releaseignore.RELEASEIGNORE)


# ------------------------------------------------------------------------------- pruning


def test_an_excluded_directory_is_never_walked(tmp_path):
    """The reason `!` cannot resurrect anything under a withheld directory is that the
    directory is never descended into - so a `.releaseignore` INSIDE one is never even
    read. The broadest possible re-inclusion written in there changes nothing, which is
    also why a folder cannot un-withhold itself. Asserted through what arrives."""
    got = _we_copy_all(
        tmp_path,
        {
            ".releaseignore": "secret/\n",
            "secret/.releaseignore": "!*\n",
            "secret/answers.md": "",
            "open/lab.md": "",
        },
    )
    assert got == {"open", "open/lab.md"}


def test_a_symlink_is_not_a_directory_for_a_trailing_slash_pattern(tmp_path):
    """`man gitignore`: a trailing `/` will not match "a plain file or symlink" of that
    name. `fs.copy_tree` copies links as links rather than descending them, so treating
    one as a directory here would withhold a link the rules do not name."""
    src = tmp_path / "src"
    _build(src, {".releaseignore": "link/\n", "real/x": ""})
    (src / "link").symlink_to("real", target_is_directory=True)
    copy_tree(src, tmp_path / "dst", releaseignore.deny_for(src))
    assert (tmp_path / "dst" / "link").is_symlink()


def test_a_tree_with_no_ignore_file_anywhere_withholds_nothing(tmp_path):
    assert _we_copy_all(tmp_path, {"a.md": "", "w1/b.md": ""}) == {
        "a.md",
        "w1",
        "w1/b.md",
    }


# ------------------------------------------------------- the single-path entry point


def test_excludes_judges_one_named_path(tmp_path):
    """`excludes` is what `deploy` asks before releasing a path a faculty member NAMED,
    and what stands in for a copytree filter on the single-file copy, which has no
    ignore hook of its own."""
    _build(
        tmp_path,
        {
            ".releaseignore": "*.log\nsecret/\n",
            "a.log": "",
            "keep.md": "",
            "secret/answers.md": "",
        },
    )
    assert releaseignore.excludes(tmp_path, tmp_path / "a.log")
    assert not releaseignore.excludes(tmp_path, tmp_path / "keep.md")
    assert releaseignore.excludes(tmp_path, tmp_path / "secret")


def test_excludes_follows_an_excluded_ancestor_down(tmp_path):
    """The pruning rule in single-path form: a file inside a withheld directory is
    withheld, and a `!` deeper in does not rescue it - the tree walk would never have
    reached it to read that `!` at all."""
    _build(
        tmp_path,
        {
            ".releaseignore": "secret/\n",
            "secret/.releaseignore": "!answers.md\n",
            "secret/answers.md": "",
        },
    )
    assert releaseignore.excludes(tmp_path, tmp_path / "secret" / "answers.md")


def test_excludes_never_withholds_the_root_itself(tmp_path):
    """A whole-repo release names the clone root. Even a `/*` cannot make that mean
    "release nothing" - the root is the ask, and its contents are filtered on the way
    through instead."""
    _build(tmp_path, {".releaseignore": "/*\n", "a.md": ""})
    assert not releaseignore.excludes(tmp_path, tmp_path)


def test_excludes_declines_a_path_outside_the_anchor(tmp_path):
    """A path that is not under `root` is not this file's business to judge."""
    _build(tmp_path, {"clone/.releaseignore": "*.log\n"})
    assert not releaseignore.excludes(tmp_path / "clone", tmp_path / "elsewhere.log")


# ------------------------------------------------------------- the GitHub tree adapter
#
# Same rule, no clone: for the copies GitHub makes server-side (a template-generated
# assignment repo) and for the commit-time source check, neither of which has a
# working tree. The rule itself is `Ignore`'s and is covered above; these pin the
# adapter - that it reads only the ignore files present, and derives directories from
# the tree rather than from disk.


def _tree(files: dict[str, str]):
    reads: list[str] = []

    def read(path: str) -> str | None:
        reads.append(path)
        return files.get(path)

    return tuple(files), read, reads


def test_the_tree_adapter_withholds_what_the_clone_adapter_would(tmp_path):
    files = {
        ".releaseignore": "**/solutions.ipynb\ndrafts/\n",
        "labs/01.md": "",
        "labs/solutions.ipynb": "",
        "drafts/wip.md": "",
    }
    paths, read, _ = _tree(files)
    assert releaseignore.excluded_in_tree(paths, read) == (
        ".releaseignore",
        "drafts/wip.md",
        "labs/solutions.ipynb",
    )
    # ...and the same tree through a real clone agrees, bar the directory-vs-its-contents
    # shape (the clone answer is "what arrived", the tree answer is "what to delete").
    assert _we_copy(tmp_path, files) == {"labs/01.md"}


def test_the_tree_adapter_reads_only_the_ignore_files(tmp_path):
    paths, read, reads = _tree(
        {".releaseignore": "*.key\n", "a.key": "", "b.md": "", "c/d.md": ""}
    )
    releaseignore.excluded_in_tree(paths, read)
    assert reads == [".releaseignore"]


def test_a_tree_with_no_ignore_file_withholds_nothing_and_reads_nothing():
    paths, read, reads = _tree({"a.md": "", "w1/b.md": ""})
    assert releaseignore.excluded_in_tree(paths, read) == ()
    assert reads == []


def test_the_tree_adapter_withholds_a_folders_files_from_a_blob_only_tree():
    """A directory-only pattern has to bite on a tree that lists no directories - the
    caller may hand over `repo_tree(kind="blob")`. Directories are derived, to any depth,
    and the answer comes back as the blobs a caller can actually delete."""
    paths, read, _ = _tree(
        {
            ".releaseignore": "secret/\n",
            "secret/a/b.md": "",
            "secret/c.md": "",
            "ok.md": "",
        }
    )
    assert releaseignore.excluded_in_tree(paths, read) == (
        ".releaseignore",
        "secret/a/b.md",
        "secret/c.md",
    )


def test_a_nested_ignore_file_works_through_the_tree_adapter():
    paths, read, _ = _tree(
        {
            ".releaseignore": "*.md\n",
            "w1/.releaseignore": "!lab.md\n",
            "w1/lab.md": "",
            "w1/other.md": "",
            "top.md": "",
        }
    )
    assert releaseignore.excluded_in_tree(paths, read) == (
        ".releaseignore",
        "top.md",
        "w1/.releaseignore",
        "w1/other.md",
    )


# ----------------------------------------------------- the documented example must work


def test_the_example_in_the_docs_does_what_the_docs_say(tmp_path):
    """The docs' snippet, run through the real matcher, straight out of the file.

    It shipped withholding a file it said it re-included: `drafts/` excludes the DIRECTORY,
    and nothing under a withheld directory can be brought back - so `!drafts/week01.md`
    after it was a silent no-op, and faculty copying the example would have lost that file
    from every release on a green run. Read from the page rather than restated here, so the
    page itself cannot drift back."""
    page = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "08-release-materials-to-cohort.md"
    ).read_text()
    block = page.split("## Withholding files with `.releaseignore`")[1]
    rules = block.split("```")[1].strip("\n")

    _build(
        tmp_path,
        {
            releaseignore.RELEASEIGNORE: rules + "\n",
            "labs/solutions.ipynb": "",
            "labs/lab.md": "",
            "__pycache__/x.pyc": "",
            "deploy.key": "",
            "drafts/week01.md": "",
            "drafts/week02.md": "",
        },
    )
    deny = releaseignore.deny_for(tmp_path)
    withheld = deny(str(tmp_path), ["__pycache__", "deploy.key", "drafts", "labs"])
    assert withheld == {"__pycache__", "deploy.key"}
    assert deny(str(tmp_path / "labs"), ["solutions.ipynb", "lab.md"]) == {
        "solutions.ipynb"
    }
    # The point of the fix: week01 comes back, week02 does not.
    assert deny(str(tmp_path / "drafts"), ["week01.md", "week02.md"]) == {"week02.md"}
