"""`.releaseignore`: the faculty-authored withhold list, with `.gitignore` semantics.

A `.releaseignore` in any directory of a staged content repo names what must NOT be
copied out of it. Syntax and precedence are `.gitignore`'s, because faculty already know
that language and every other answer would be one they have to learn.

Stdlib + pathspec only: no GitHub, no config. `pathspec` supplies the per-pattern regex;
what it has no notion of - nested files, per-directory anchoring, and the walk pruning
that makes `!` correct - is here.

`Ignore` is the whole rule and reads no files. Two adapters feed it, because the outbound
paths do not all have a clone to look at:

  deny_for(root) / excludes(root, path)   a local clone      (deploy, public_site, assign)
  from_tree(paths, read)                  a GitHub tree      (assign's cohort template,
                                                              schedule's source check)

Both anchor on a ROOT, not on the subpath being copied: a root `.releaseignore` governs a
`lectures/01` release the same way a root `.gitignore` governs a subdirectory.

The file EXCLUDES ITSELF, everywhere (`_SELF_EXCLUDED`). That departs from git, which
tracks `.gitignore` - deliberately, because git has no equivalent of what happens here:
a released copy lands in a repo every student and auditor can read (`deploy` grants both
teams `pull`), and its contents are a list of the paths faculty held back. Nothing
downstream ever reads a released copy, so it would ship for no benefit at all.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from pathspec import GitIgnoreSpec

from .fs import Deny

RELEASEIGNORE = ".releaseignore"

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
    """The nested-`.releaseignore` rule. Reads nothing - `spec_for` supplies the texts.

    Chains are memoised per directory rather than pushed and popped on a stack, because
    the caller that drives this (`shutil.copytree`) announces entering a directory and
    never leaving one, so there is no moment to pop at.

    Each level is `(prefix, spec)`, where `prefix` is the directory's path RELATIVE to
    that level's `.releaseignore` - computed once when the directory is memoised, since it
    depends only on the directory and the anchor. Only the entry name varies per file, so
    judging one costs a string join rather than a path-relativise per level per entry."""

    def __init__(self, spec_for: SpecFor) -> None:
        self._spec_for = spec_for
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

    def verdict(self, dirrel: DirRel, name: str, is_dir: bool) -> bool | None:
        """True withheld / False re-included by a `!` / None no opinion, for one entry.

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
        return None

    def excludes(self, relpath: str, is_dir: Callable[[str], bool]) -> bool:
        """Whether `relpath` is withheld, judging every component from the root down.

        Component by component so a withheld ancestor decides on the spot, the way pruning
        decides it for a tree walk - otherwise a `!` deeper in would re-include a file out
        of a directory that was never going to be walked at all."""
        parts = [p for p in relpath.strip("/").split("/") if p]
        for i, part in enumerate(parts):
            here = "/".join(parts[: i + 1])
            if self.verdict("/".join(parts[:i]), part, is_dir(here)) is True:
                return True
        return False


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


def _local(root: Path) -> Ignore:
    return Ignore(lambda dirrel: _spec_in(root / dirrel if dirrel else root))


def deny_for(root: Path) -> Deny:
    """An `fs.Deny` withholding whatever the `.releaseignore` files under `root` exclude.

    Withholding a directory PRUNES it: `copytree` does not descend into a name this
    returns, so nothing inside is ever tested and no `!` written inside (or below) it can
    resurrect anything - which is exactly git's rule, for exactly git's reason.

    `root` must be an ancestor of every directory `copytree` walks, which it is: it walks
    the tree it was given. Paths are never resolved, so matching is on the names in the
    tree and a `notes.pdf -> ../solution/answers.pdf` is judged as `notes.pdf`."""
    ignore = _local(root)

    def deny(dirpath: str, names: list[str]) -> set[str]:
        here = Path(dirpath)
        dirrel = here.relative_to(root).as_posix() if here != root else ""
        # No rules anywhere above this directory and none in it: the common case for a
        # repo that has no `.releaseignore` at all, and worth not stat-ing every name for.
        if not ignore.chain(dirrel):
            return {n for n in names if n in _SELF_EXCLUDED}
        return {
            name
            for name in names
            if ignore.verdict(dirrel, name, _is_dir(here / name)) is True
        }

    return deny


def excludes(root: Path, path: Path) -> bool:
    """Whether one named `path` under the clone at `root` is withheld."""
    if path == root or root not in path.parents:
        return False
    return _local(root).excludes(
        path.relative_to(root).as_posix(), lambda rel: _is_dir(root / rel)
    )


# ------------------------------------------------------------------- GitHub tree adapter


def from_tree(
    paths: Iterable[str], read: Callable[[str], str | None]
) -> tuple[Ignore, Callable[[str], bool]]:
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
    dirs = {
        "/".join(parts[:i])
        for p in every
        for parts in [p.split("/")]
        for i in range(1, len(parts))
    }
    return Ignore(specs.get), lambda rel: rel in dirs


def excluded_in_tree(
    paths: Iterable[str], read: Callable[[str], str | None]
) -> tuple[str, ...]:
    """Which of `paths` a `.releaseignore` in that same tree withholds, sorted.

    The paths as GIVEN - a caller deleting them needs the blobs it can address, not the
    folder they happen to share. A withheld folder therefore comes back as each of its
    files, which is also why `Ignore.excludes` walks components: the folder's own verdict
    is what decides them."""
    every = sorted({p.strip("/") for p in paths if p.strip("/")})
    ignore, is_dir = from_tree(every, read)
    return tuple(p for p in every if ignore.excludes(p, is_dir))
