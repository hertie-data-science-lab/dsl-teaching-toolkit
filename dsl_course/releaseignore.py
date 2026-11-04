"""`.releaseignore`: the faculty-authored withhold list, with `.gitignore` semantics.

A `.releaseignore` in any directory of a staged content repo names what must NOT be
copied out of it. Syntax and precedence are `.gitignore`'s, because faculty already know
that language and every other answer would be one they have to learn.

Stdlib + pathspec only: no GitHub, no config. `pathspec` supplies the per-pattern regex;
what it has no notion of - nested files, per-directory anchoring, and the walk pruning
that makes `!` correct - is here.

`Ignore` is the whole rule and reads no files. Two adapters feed it, because the outbound
paths do not all have a clone to look at:

  local(root) / deny_for(root)           a local clone      (selection, assign, deploy)
  listed(lines)                           a pattern list     (selection's include/exclude)
  from_tree(paths, read)                  a GitHub tree      (assign's semester template,
                                                              schedule's source check)

Both anchor on a ROOT, not on the subpath being copied: a root `.releaseignore` governs a
`lectures/01` release the same way a root `.gitignore` governs a subdirectory.

A `default` (decision B, 2026-11-04) is a set of NAMES withheld when no pattern has an
opinion on them, as if it were an implied first line above the root `.releaseignore`: the
institution's content names (`selection.DENYLIST_CONTENT`) on a release's folder copy,
which a `!solutions/` line re-includes, last match winning as for any other line. It
judges only the names BELOW the path a copy names (`Ignore.excludes`' `copy_path`): what is
named outright is faculty's lines' alone. Only a semester release passes one
(`selection.RELEASE`); every other caller reads faculty's lines alone.

The file EXCLUDES ITSELF, everywhere (`_SELF_EXCLUDED`). That departs from git, which
tracks `.gitignore` - deliberately, because git has no equivalent of what happens here:
a released copy lands in a repo every student and auditor can read (`deploy` grants both
teams `pull`), and its contents are a list of the paths faculty held back. Nothing
downstream ever reads a released copy, so it would ship for no benefit at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

from pathspec import GitIgnoreSpec

from .fs import Deny

RELEASEIGNORE = ".releaseignore"
# The line a `.releaseignore` carries when somebody looked and chose to withhold nothing,
# so the materials checklist can tell a reviewed empty list from the seeded one. A comment:
# it withholds nothing. The console writes it; spelt once, here.
REVIEWED_MARK = "# Reviewed: nothing withheld."


def reviewed(text: str | None) -> bool:
    """A `.releaseignore` somebody has been through: one pattern (a line that is not blank
    or a comment), or `REVIEWED_MARK`. The seeded one is every line a comment (`scaffold`),
    in every wording it has had."""
    lines = [line.strip() for line in (text or "").splitlines()]
    return REVIEWED_MARK in lines or any(
        line and not line.startswith("#") for line in lines
    )


def confirmed(text: str | None) -> str:
    """`text` with `REVIEWED_MARK` appended (the Confirm of the materials checklist's
    "Withheld patterns reviewed", plan 0.11.0 9b ruling 6): nothing else changes."""
    body = (text or "").rstrip("\n")
    return f"{body}\n{REVIEWED_MARK}\n" if body else f"{REVIEWED_MARK}\n"


# Withheld by every copy, with no pattern needed and no way to opt back in - see the
# module docstring. A `!` cannot re-include it: the check runs before the chain.
_SELF_EXCLUDED = frozenset({RELEASEIGNORE})

# A directory's relative path from the anchor root, posix, "" for the root itself.
DirRel = str
# What a `.releaseignore` in that directory says, or None if it has none.
SpecFor = Callable[[DirRel], "GitIgnoreSpec | None"]


def parse(text: str) -> GitIgnoreSpec:
    """One `.releaseignore`'s lines as a matcher.

    `GitIgnoreSpec`, not the plainer `PathSpec`: it is the class that refuses to let a `!`
    re-include a path whose ancestor was excluded WITHIN one pattern list. Across nested
    files that rule is `Ignore`'s, through pruning."""
    return GitIgnoreSpec.from_lines(text.splitlines())


class Ignore:
    """The nested-`.releaseignore` rule. Reads nothing - `spec_for` supplies the texts, and
    `default` the names withheld when no level has an opinion (module docstring).

    Chains are memoised per directory rather than pushed and popped on a stack, because
    the caller that drives this (`shutil.copytree`) announces entering a directory and
    never leaving one, so there is no moment to pop at.

    Each level is `(prefix, spec)`, where `prefix` is the directory's path RELATIVE to
    that level's `.releaseignore` - computed once when the directory is memoised, since it
    depends only on the directory and the anchor. Only the entry name varies per file, so
    judging one costs a string join rather than a path-relativise per level per entry."""

    def __init__(
        self, spec_for: SpecFor, default: Callable[[str], bool] | None = None
    ) -> None:
        self._spec_for = spec_for
        self._default = default
        self._cache: dict[DirRel, tuple[tuple[str, GitIgnoreSpec], ...]] = {}

    def chain(self, dirrel: DirRel) -> tuple[tuple[str, GitIgnoreSpec], ...]:
        """The `(prefix, spec)` levels governing `dirrel`'s entries, root-to-leaf."""
        hit = self._cache.get(dirrel)
        if hit is not None:
            return hit
        if dirrel:
            parent, _, name = dirrel.rpartition("/")
            # One level deeper than the parent, so every inherited prefix gains this
            # directory's name.
            chain = tuple(
                (f"{prefix}/{name}" if prefix else name, spec)
                for prefix, spec in self.chain(parent)
            )
        else:
            chain = ()
        own = self._spec_for(dirrel)
        if own is not None:
            chain = (*chain, ("", own))
        self._cache[dirrel] = chain
        return chain

    def verdict(
        self, dirrel: DirRel, name: str, is_dir: bool, default: bool = True
    ) -> bool | None:
        """True withheld / False re-included by a `!` / None no opinion, for one entry. A
        name the `default` withholds is True when no level matches it: the implied first
        line (`default=False` reads faculty's lines alone).

        Every level is one ordered pattern list root-to-leaf and the LAST match wins, so
        the deepest level with an opinion decides - each level already applying
        last-match-wins within itself. Walked leaf-first so that level returns first.

        A directory's own `.releaseignore` judges its CHILDREN; the directory itself was
        judged by its parent's chain, so nothing written inside a withheld folder can
        release it. That is git's rule too."""
        if name in _SELF_EXCLUDED:
            return True
        suffix = "/" if is_dir else ""
        for prefix, spec in reversed(self.chain(dirrel)):
            rel = f"{prefix}/{name}{suffix}" if prefix else f"{name}{suffix}"
            matched = spec.check_file(rel).include
            if matched is not None:
                return matched
        if default and self._default is not None and self._default(name):
            return True
        return None

    def excludes(
        self, relpath: str, is_dir: Callable[[str], bool], copy_path: str = ""
    ) -> bool:
        """Whether `relpath` is withheld, judging every component from the root down, the
        `default` only the components below `copy_path` (the path a copy names, "" for the
        root): a copy that names `solutions/labs/02` ships it, one of `labs/01` leaves the
        `solutions/` inside behind.

        Component by component so a withheld ancestor decides on the spot, the way pruning
        decides it for a tree walk - otherwise a `!` deeper in would re-include a file out
        of a directory that was never going to be walked at all."""
        parts = _parts(relpath)
        named = len(_parts(copy_path))
        for i, part in enumerate(parts):
            here = "/".join(parts[: i + 1])
            if self.verdict("/".join(parts[:i]), part, is_dir(here), i >= named):
                return True
        return False


def _parts(path: str) -> list[str]:
    return [p for p in path.strip("/").split("/") if p]


def ancestors(paths: Iterable[str]) -> set[str]:
    """Every folder above each of `paths` (`a/b/c.md` gives `a/b` and `a`): a tree's
    directories, derived from its files."""
    out = set()
    for p in paths:
        parts = _parts(p)
        out.update("/".join(parts[:i]) for i in range(1, len(parts)))
    return out


# ------------------------------------------------------------------ local clone adapter


def _spec_in(directory: Path) -> GitIgnoreSpec | None:
    f = directory / RELEASEIGNORE
    if not f.is_file():
        return None
    return parse(f.read_text(encoding="utf-8", errors="replace"))


def _is_dir(path: Path) -> bool:
    """Whether a trailing-slash (directory-only) pattern may match `path`.

    A symlink is never a directory here: `man gitignore` has a trailing `/` refuse "a
    plain file or symlink of that name", and `fs.copy_tree` copies links as links rather
    than descending through them anyway."""
    return path.is_dir() and not path.is_symlink()


def local(root: Path, default: Callable[[str], bool] | None = None) -> Ignore:
    """An `Ignore` over the `.releaseignore` files of the clone at `root`, read once each
    however many copies judge by it."""
    return Ignore(lambda dirrel: _spec_in(root / dirrel if dirrel else root), default)


def deny_with(root: Path, ignore: Ignore) -> Deny:
    """An `fs.Deny` withholding what `ignore` (`local(root)`) excludes, for a `copytree`
    walk of `root` (`deny_for`)."""

    def deny(dirpath: str, names: list[str]) -> set[str]:
        here = Path(dirpath)
        dirrel = here.relative_to(root).as_posix() if here != root else ""
        # No rules anywhere above this directory and none in it: the common case for a
        # repo that has no `.releaseignore` at all, and worth not stat-ing every name for
        # (with no level, a verdict never asks whether a name is a directory).
        if not ignore.chain(dirrel):
            return {n for n in names if ignore.verdict(dirrel, n, False) is True}
        return {
            name
            for name in names
            if ignore.verdict(dirrel, name, _is_dir(here / name)) is True
        }

    return deny


def deny_for(root: Path, default: Callable[[str], bool] | None = None) -> Deny:
    """An `fs.Deny` withholding whatever the `.releaseignore` files under `root` exclude,
    and the names `default` withholds unless a `!` line re-includes them.

    Withholding a directory PRUNES it: `copytree` does not descend into a name this
    returns, so nothing inside is ever tested and no `!` written inside (or below) it can
    resurrect anything - which is exactly git's rule, for exactly git's reason.

    `root` must be an ancestor of every directory `copytree` walks, which it is: it walks
    the tree it was given. Paths are never resolved, so matching is on the names in the
    tree and a `notes.pdf -> ../solution/answers.pdf` is judged as `notes.pdf`."""
    return deny_with(root, local(root, default))


def listed(
    lines: Iterable[str], default: Callable[[str], bool] | None = None
) -> Ignore:
    """One pattern list read as a `.releaseignore` at the root, and nowhere else."""
    spec = parse("\n".join(lines))
    return Ignore(lambda dirrel: None if dirrel else spec, default)


def excludes(root: Path, path: Path, ignore: Ignore | None = None) -> bool:
    """Whether one named `path` under the clone at `root` is withheld by the
    `.releaseignore` files under `root` (`ignore`, else read afresh). Named outright, so a
    `default` never judges it."""
    if path == root or root not in path.parents:
        return False
    rel = path.relative_to(root).as_posix()
    return (ignore or local(root)).excludes(rel, lambda r: _is_dir(root / r), rel)


# ------------------------------------------------------------------- GitHub tree adapter


class Tree(NamedTuple):
    """An `Ignore` over a repo TREE, plus its `is_dir` (`from_tree`)."""

    ignore: Ignore
    is_dir: Callable[[str], bool]

    def carries(self, copy_path: str, path: str) -> bool:
        """Whether a copy of `copy_path` ("" the whole repo) carries `path` (a repo path at
        or under it) past the `.releaseignore` lines and, below `copy_path`, the
        `default`."""
        return not self.ignore.excludes(path, self.is_dir, copy_path)


def from_tree(
    paths: Iterable[str],
    read: Callable[[str], str | None],
    default: Callable[[str], bool] | None = None,
) -> Tree:
    """An `Ignore` over a repo TREE, plus its `is_dir`. No clone, so no `git` at all.

    For the copies GitHub makes server-side - a template-generated assignment repo - and
    for `schedule`'s commit-time check, neither of which has a working tree to walk. `read`
    is called only for the `.releaseignore` paths actually present, so a repo without one
    costs nothing beyond the tree fetch the caller already did.

    Directories are DERIVED, not asked about: a path is a directory if anything in the tree
    sits under it. That is what a trailing-slash pattern needs, and it means a caller may
    hand over a blob-only tree (`repo_tree(kind="blob")`) or a full one indifferently."""
    every = {p.strip("/") for p in paths if p.strip("/")}
    specs: dict[DirRel, GitIgnoreSpec] = {}
    for p in sorted(every):
        head, _, tail = p.rpartition("/")
        if tail == RELEASEIGNORE:
            body = read(p)
            if body is not None:
                specs[head] = parse(body)
    # EVERY ancestor, not just immediate parents: `a/b/c.md` on its own makes both `a/b`
    # and `a` directories, and an `a/` pattern has to match.
    dirs = ancestors(every)
    return Tree(Ignore(specs.get, default), lambda rel: rel in dirs)


def excluded_in_tree(
    paths: Iterable[str], read: Callable[[str], str | None]
) -> tuple[str, ...]:
    """Which of `paths` a `.releaseignore` in that same tree withholds, sorted.

    The paths as GIVEN - a caller deleting them needs the blobs it can address, not the
    folder they happen to share. A withheld folder therefore comes back as each of its
    files, which is also why `Ignore.excludes` walks components: the folder's own verdict
    is what decides them."""
    every = sorted({p.strip("/") for p in paths if p.strip("/")})
    tree = from_tree(every, read)
    return tuple(p for p in every if not tree.carries(p, p))
