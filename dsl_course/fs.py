"""Local-filesystem copying: the one tree copy every outbound path runs through.

Stdlib only - no GitHub, no config. What the release path (deploy), the public site
(public_site) and the solution push (assign) withhold is each their own policy and stays
with them; what they must NOT get wrong is here. Faculty's own withhold list is the one
policy all three share - see `releaseignore`.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

# A copytree `ignore`: (directory being walked, its entry names) -> the names to skip.
Deny = Callable[[str, list[str]], set[str]]


def union_deny(*denies: Deny) -> Deny:
    """One `Deny` that withholds whatever ANY of `denies` withholds.

    Mechanism, not policy - it names no filter and no filename, so each caller's own
    exclusions still live with the caller. Here because every outbound path now composes
    its own rules with faculty's `.releaseignore`, and two hand-written unions of the same
    shape differ by one character (`&` for `|`) from silently withholding nothing at all,
    on a green run."""

    def deny(dirpath: str, names: list[str]) -> set[str]:
        return set().union(*(d(dirpath, names) for d in denies))

    return deny


def copy_tree(src: Path, dst: Path, deny: Deny | None = None) -> None:
    """Copy the tree at `src` over `dst`, skipping whatever `deny` names (nothing by
    default).

    `dirs_exist_ok=True` because every caller copies into a checkout that may already hold
    an earlier copy.

    `symlinks=True` copies each link AS a link, and is the reason this is one function.
    Following them, a symlink pointing at nothing raises shutil.Error and a directory
    symlink pointing at its own parent recurses - and the release runs under an hourly
    cron, so one such path in one materials repo aborted a whole cohort's release every
    hour. On the public path it is worse than an abort: a `notes.pdf ->
    ../solution/answers.pdf` would be published as the answers themselves, under a name
    no denylist has any reason to refuse."""
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True, ignore=deny)
