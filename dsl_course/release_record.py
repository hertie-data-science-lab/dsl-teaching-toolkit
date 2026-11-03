"""The semester's release record: `semester-config/.system/releases.json` (decision 0043).

A release is ONE copy at its time. This file is how the engine knows a copy has been made:
one entry per copy, keyed by its source repo + path and its destination repo + path (a
`files:` item re-pointed elsewhere is a new copy), carrying the schedule entry it belongs
to, the source commit it was copied from, when it first and last shipped, and the
manifest of what was copied - each file's path relative to the copy, and its blob sha.
Repo, path and sha only: nothing here names a person.

Who writes it:

- the scheduled release (`scheduler`), for every copy it ships, and for a due copy that
  has no entry but whose destination is already there - SEEDED from the destination tree,
  `first_shipped_at: null`, and not copied (this is what upgrades a semester released into
  before the record existed, on its first tick, with nothing re-shipped);
- Release materials (`deploy.main`): `--entry` (the console's Release early / now / again)
  for every copy of the entry, and a release of paths for each schedule copy those paths
  cover (`schedule_plan.covered_copies`).

ONE read per tick and at most one write, sha-guarded (`put_file_as_read`): a scoped tick
and a console run racing each other re-apply their own change on the fresh file rather
than overwrite it. Nothing is written when nothing changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import records, schedule
from .course import is_repo_root
from .gh_contents import get_file_with_sha, put_file_as_read
from .schedule_plan import deploy_dest, within

PATH = records.path("releases")
CONFIG_REPO = schedule.CONFIG_REPO

# A copy's key: source repo, source path, destination repo, destination path.
Key = tuple[str, str, str, str]


def key_of(d: schedule.Deploy) -> Key:
    """The copy `d` makes, as the record keys it. A root path is ""."""
    return (
        d.course_source_repo,
        d.course_source_path.strip("/"),
        d.semester_dest_repo,
        deploy_dest(d),
    )


def stamp(at: datetime) -> str:
    """A ship time as the record writes it: UTC, to the second."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def inside(files: dict[str, str], sub: str) -> dict[str, str]:
    """The part of `files` (`{path: blob sha}`: a manifest, or a destination tree) at
    `sub`, keyed as a manifest is - relative to `sub`, a single file by its own name; all
    of it for the root. Empty when nothing is there."""
    if is_repo_root(sub):
        return dict(files)
    out = {}
    for path, sha in files.items():
        rel = within(sub, path)
        if rel is not None:
            out[rel or path.rsplit("/", 1)[-1]] = sha
    return out


def _entry(
    label: str,
    k: Key,
    commit: str | None,
    first: str | None,
    last: str | None,
    files: dict[str, str],
) -> dict:
    return {
        "entry": label,
        "source": {"repo": k[0], "path": k[1], "commit": commit},
        "dest": {"repo": k[2], "path": k[3]},
        "first_shipped_at": first,
        "last_shipped_at": last,
        "files": dict(sorted(files.items())),
    }


def _key_of_entry(e: dict) -> Key:
    return (
        e["source"]["repo"],
        e["source"]["path"],
        e["dest"]["repo"],
        e["dest"]["path"],
    )


def parse(text: str) -> dict[Key, dict]:
    """The record's copies by key. Raises `ValueError` on a file that is not the record."""
    doc = json.loads(text)
    if not isinstance(doc, dict) or not isinstance(doc.get("copies"), list):
        raise ValueError(f"{PATH} is not a release record")  # noqa: TRY004
    try:
        return {_key_of_entry(e): e for e in doc["copies"]}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{PATH} has a copy it cannot read ({exc})") from exc


def dump(copies: dict[Key, dict]) -> str:
    """The file's exact text: copies in key order, so one state is one blob."""
    doc = {"copies": [copies[k] for k in sorted(copies)]}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _apply(copies: dict[Key, dict], seeded: bool, k: Key, e: dict) -> None:
    """One change to `copies`: a seed never replaces an entry; a ship keeps the entry's
    first label and first ship time."""
    old = copies.get(k)
    if old is None:
        copies[k] = e
    elif not seeded:
        copies[k] = {
            **e,
            "entry": old.get("entry", e["entry"]),
            "first_shipped_at": old.get("first_shipped_at"),
        }


@dataclass
class Record:
    """One semester's record as read, plus this run's changes to it. `sha` is the blob
    it was read at ("" = there was none)."""

    copies: dict[Key, dict] = field(default_factory=dict)
    sha: str = ""
    # This run's changes, `(seeded?, key, entry)`, replayed over a fresh read if the
    # write is refused (`_apply`).
    _changes: list[tuple[bool, Key, dict]] = field(default_factory=list)

    def has(self, d: schedule.Deploy) -> bool:
        return key_of(d) in self.copies

    def _change(self, seeded: bool, k: Key, e: dict) -> None:
        self._changes.append((seeded, k, e))
        _apply(self.copies, seeded, k, e)

    def seed(self, label: str, d: schedule.Deploy, files: dict[str, str]) -> None:
        """Record a copy found already at its destination, without shipping it."""
        k = key_of(d)
        if k not in self.copies:
            self._change(True, k, _entry(label, k, None, None, None, files))

    def ship(
        self,
        label: str,
        d: schedule.Deploy,
        commit: str,
        files: dict[str, str],
        at: datetime,
        refresh: bool,
    ) -> None:
        """Record a copy that has landed. An entry already there is left alone when its
        manifest is unchanged, unless `refresh` (a deliberate release: Release again, a
        hand release) - an automatic re-ship of identical files is no change."""
        k = key_of(d)
        old = self.copies.get(k)
        if old is not None and not refresh and old.get("files") == files:
            return
        self._change(False, k, _entry(label, k, commit, stamp(at), stamp(at), files))

    def _rebuild(self, text: str | None) -> str | None:
        """This run's changes over a fresh read of the file (`put_file_as_read`)."""
        try:
            fresh = {} if text is None else parse(text)
        except ValueError:
            return None
        for change in self._changes:
            _apply(fresh, *change)
        self.copies = fresh
        return dump(fresh)

    def save(self, semester_org: str) -> bool:
        """Write this run's changes, if any. False when the write was not accepted."""
        if not self._changes:
            return True
        seeded = sum(1 for s, _k, _e in self._changes if s)
        message = (
            f"release record: {len(self._changes) - seeded} shipped, {seeded} seeded"
        )
        return (
            put_file_as_read(
                semester_org,
                CONFIG_REPO,
                PATH,
                dump(self.copies),
                self.sha,
                self._rebuild,
                message,
            )
            is not None
        )


def load(semester_org: str) -> Record:
    """The semester's record; an empty one when there is none yet. Raises
    `RuntimeError` when it cannot be read and `ValueError` when it is not the record."""
    read = get_file_with_sha(semester_org, CONFIG_REPO, PATH)
    if read is None:
        return Record()
    text, sha = read
    return Record(parse(text), sha)
