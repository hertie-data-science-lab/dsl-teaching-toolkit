"""Local-filesystem copying: the one tree copy every outbound path runs through.

Stdlib only - no GitHub, no config. What an outbound copy withholds is `selection`'s
(the solution push in `assign` keeps its own); what every copy must NOT get wrong is here.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

# A copytree `ignore`: (directory being walked, its entry names) -> the names to skip.
Deny = Callable[[str, list[str]], set[str]]


def copy_tree(src: Path, dst: Path, deny: Deny | None = None) -> None:
    """Copy the tree at `src` over `dst`, skipping whatever `deny` names (nothing by
    default).

    `dirs_exist_ok=True` because every caller copies into a checkout that may already hold
    an earlier copy.

    `symlinks=True` copies each link AS a link, and is the reason this is one function.
    Following them, a symlink pointing at nothing raises shutil.Error and a directory
    symlink pointing at its own parent recurses - and the release runs under an hourly
    cron, so one such path in one materials repo aborted a whole semester's release every
    hour. On the public path it is worse than an abort: a `notes.pdf ->
    ../solution/answers.pdf` would be published as the answers themselves, under a name
    no denylist has any reason to refuse."""
    shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True, ignore=deny)
