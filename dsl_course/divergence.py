"""Has a released copy drifted from what was released? (decision 0043 rule 6)

A release is one copy at its time, and the release record keeps its manifest: each file
it copied, relative to the copy, with its blob sha. Against that manifest, three things
can have happened since, each a different remedy:

- the course copy changed: a manifest file's course blob differs, or the course has a
  file under the copy the manifest lacks (Release again);
- deleted in course: a manifest file the course no longer carries, still in the released
  copy (Release again, removing it on request);
- the released copy was edited: a manifest file's blob differs in the branch students
  read, a file was added under it there (Keep for future terms), or a manifest file is
  gone from it - students lack a file they were given (Release again puts it back).

The course side is filtered exactly as a copy would be (`source_files`): the institution
denylist below the copy's own path (a content name a `!` line re-includes aside),
`.releaseignore`, and for a whole-repo copy the root
plumbing and any root stub still the placeholder. A path another copy lands at inside
this one (a lab's `solutions/` copy) is that copy's, and left out of all three sides.

Pure: the trees come in as `{path: blob sha}`, read once per repo by the status render.
Repo, path and sha only - nothing here names a person.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from typing import NamedTuple

from .course import is_repo_root
from .gh_contents import WITHHELD_ROOT_STUBS
from .release_record import inside
from .schedule_plan import within
from .selection import kept, never_copied, release_use, swept

COURSE = "DIVERGED_COURSE"
DELETED = "DIVERGED_DELETED"
RELEASED = "DIVERGED_RELEASED"
KINDS = (COURSE, DELETED, RELEASED)


class Drift(NamedTuple):
    """One kind of drift in one copy: the files, relative to the copy, and the changed
    side's `(path, blob sha)` pairs the problem's fingerprint is taken over. `removed`
    (the released side only): manifest files gone from the released copy."""

    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    side: tuple[tuple[str, str], ...] = ()
    removed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.deleted or self.removed)


def blobs_of(shas: Mapping[str, str]) -> dict[str, str]:
    """The files of a full tree listing (`{path: sha}`, directories included): every path
    nothing else sits under. Git has no empty directories, so this is exact."""
    dirs = {p.rsplit("/", i)[0] for p in shas for i in range(1, p.count("/") + 1)}
    return {p: s for p, s in shas.items() if p not in dirs}


def source_files(
    src_path: str,
    dest_path: str,
    blobs: Mapping[str, str],
    withheld: Collection[str],
    unwritten: Collection[str],
    reincluded: Collection[str] = (),
) -> dict[str, str]:
    """What a copy of `src_path` would take from a course repo's files now, keyed as its
    manifest is (`deploy._copy_listed`): relative to the copy, a single file by its
    destination name. `withheld` is the repo's `.releaseignore` verdict (repo paths);
    `unwritten` the root stubs still the placeholder, left out of a whole-repo copy;
    `reincluded` the content names a `!` line re-includes (repo paths), which the copy
    carries (`selection.swept`)."""
    src = src_path.strip("/")
    root = is_repo_root(src)
    if not root and src in withheld:
        return {}  # the copy's own path is withheld: it copies nothing
    if not root and src in blobs:
        if src in WITHHELD_ROOT_STUBS and src in unwritten:
            return {}
        return {dest_path.rsplit("/", 1)[-1] or src: blobs[src]}
    use = release_use(root, unwritten)  # what `deploy` copies through, layer 3
    out = {}
    for path, sha in blobs.items():
        rel = within(src, path)
        if (
            rel
            and path not in withheld
            and not never_copied(rel)
            and not swept(rel, reincluded, "" if root else src)
            and use.keeps(rel, None)
        ):
            out[rel] = sha
    return out


def dest_files(
    dest_blobs: Mapping[str, str], dest_path: str, manifest: Mapping[str, str]
) -> dict[str, str]:
    """The files under a copy's destination, keyed as its manifest is. For a copy into the
    repo's root, the destination's own plumbing (`.github/`, the denylist) and a root stub
    the copy never carried are the semester's, not a released file edited or added."""
    files = inside(dict(dest_blobs), dest_path)
    if not is_repo_root(dest_path):
        return files
    use = release_use(True, WITHHELD_ROOT_STUBS)
    return {p: s for p, s in files.items() if p in manifest or kept(p, use=use)}


def source_present(src_path: str, blobs: Mapping[str, str]) -> bool:
    """Whether the course repo still has the copy's own path at all (a file, or a folder
    with a file in it). One that has gone is its source problem, not a divergence."""
    src = src_path.strip("/")
    if is_repo_root(src):
        return bool(blobs)
    return src in blobs or any(p.startswith(f"{src}/") for p in blobs)


def outside(files: Mapping[str, str], nested: Collection[str]) -> dict[str, str]:
    """`files` without what sits at or under one of the `nested` sub-paths (`nested_in`):
    those are another copy's."""
    return {
        p: s
        for p, s in files.items()
        if not any(p == n or p.startswith(f"{n}/") for n in nested)
    }


def compare(
    manifest: Mapping[str, str],
    source: Mapping[str, str] | None,
    dest: Mapping[str, str] | None,
    nested: Collection[str] = (),
) -> dict[str, Drift]:
    """Each kind of drift one copy shows, by kind. `source` is what the copy takes from the
    course now (`source_files`), `dest` the files under the copy in the branch students
    read (relative to it); None for a side that could not be read, whose kinds are not
    judged. `nested` are the sub-paths other copies land at, left out everywhere."""
    m = outside(manifest, nested)
    out: dict[str, Drift] = {}
    if source is not None:
        s = outside(source, nested)
        out[COURSE] = _edits(m, s)
        if dest is not None:
            gone = tuple(sorted(p for p in m if p not in s and p in dest))
            out[DELETED] = Drift(deleted=gone, side=tuple((p, "") for p in gone))
    if dest is not None:
        d = outside(dest, nested)
        edits = _edits(m, d)
        # Gone from the released copy, and still in the course (when that side is read):
        # a file gone from both leaves nothing for Release again to put back.
        kept = m if source is None else outside(source, nested)
        removed = tuple(sorted(p for p in m if p not in d and p in kept))
        out[RELEASED] = edits._replace(
            removed=removed, side=edits.side + tuple((p, "") for p in removed)
        )
    return {kind: drift for kind, drift in out.items() if drift}


def _edits(manifest: Mapping[str, str], side: Mapping[str, str]) -> Drift:
    """What one side changed and added against the manifest, with that side's blobs."""
    changed = tuple(sorted(p for p in manifest if p in side and side[p] != manifest[p]))
    added = tuple(sorted(p for p in side if p not in manifest))
    return Drift(
        added, changed, (), tuple((p, side[p]) for p in sorted(added + changed))
    )


def nested_in(dest: tuple[str, str], others: Collection[tuple[str, str]]) -> list[str]:
    """The sub-paths, relative to the copy landing at `dest` (repo, path), where another
    copy lands strictly inside it."""
    repo, path = dest
    return sorted(
        {
            rel
            for o_repo, o_path in others
            if o_repo == repo and o_path != path and (rel := within(path, o_path))
        }
    )


def fingerprint(sides: Collection[tuple[str, str]]) -> str:
    """Eight hex of sha1 over the changed side's sorted `(path, blob sha)` pairs: the
    problem's id changes when that side changes again, and only then."""
    text = "\n".join(f"{p}\t{s}" for p, s in sorted(sides))
    return hashlib.sha1(text.encode()).hexdigest()[:8]
