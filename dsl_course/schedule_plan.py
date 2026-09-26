"""Interpret a parsed `schedule.Schedule` into the rows a site or syllabus shows.

A row is a `releases:` entry: its date, its kind, its name and the copies it makes. Rows
come in date order and nothing is inferred from folder names beyond one fallback - an entry
that declares no `kind` takes it from the section its first copy lands in
(`materials.infer_kind`). An `NN_` prefix on a folder means nothing here. Pure: nothing
touches GitHub or renders anything - `site` turns rows into pages, `syllabus` into a table.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from . import schedule
from .materials import (
    DEFAULT_KIND,
    DEFAULT_SYLLABUS,
    Declared,
    alias_kind,
    infer_kind,
    publishable,
)

# A source repo -> its `materials.yml` folder aliases. The caller reads them; the plan
# stays pure.
Aliases = Callable[[str], Mapping[str, str]]


def _no_aliases(_repo: str) -> Mapping[str, str]:
    return {}


def deploy_dest(deploy: schedule.Deploy) -> str:
    """Where a deploy lands inside its destination repo - `semester_dest_path` when it is
    set, else the source path mirrored. Stated once, because the release, the site and the
    status all read it."""
    return (deploy.semester_dest_path or deploy.course_source_path).strip("/")


def deploy_section(deploy: schedule.Deploy) -> str:
    """The section a deploy lands in - the top-level directory of its destination path,
    or the destination repo itself when the copy lands at its root (a repo that IS one
    section, `semester_dest_repo: labs`)."""
    head, sep, _ = deploy_dest(deploy).partition("/")
    return head if sep else deploy.semester_dest_repo


def entry_kind(
    release: schedule.Release, aliases: Aliases = _no_aliases
) -> tuple[str, bool]:
    """`(kind, inferred)` for a `releases:` entry: the kind it declares, else the kind the
    section of its first copy implies, else `lecture` (an entry that copies nothing yet)."""
    if release.kind:
        return release.kind, False
    if release.deploy:
        first = release.deploy[0]
        return infer_kind(
            deploy_section(first), aliases(first.course_source_repo)
        ), True
    return DEFAULT_KIND, True


# A label's own number: trailing (`lecture_03`, `lab-9`, `s5`) or leading (`01_lab`), as
# the console has always read it.
_LABEL_NUMBER = re.compile(r"0*(\d+)$|^0*(\d+)[-_ ]")


def label_number(label: str) -> int | None:
    """The number a label carries (`lecture_03` -> 3, `01_lab` -> 1), or None."""
    m = _LABEL_NUMBER.search(label.strip())
    return int(m.group(1) or m.group(2)) if m else None


@dataclass
class PlannedRow:
    """What the plan says about one row, before anything has shipped.

    `key` is the entry's label (unique in the file); `deploys` are its copies in plan
    order, which is what the row links once they have landed. `shown` is the entry's
    `show_on_site`: a silent row stays off the schedule and the Updates box."""

    key: str
    kind: str
    when: date | datetime
    kind_inferred: bool = False
    tbc: bool = False
    subtitle: str = ""
    details: str = ""
    shown: bool = True
    number: int | None = None  # the entry's own `number:`
    deploys: tuple[schedule.Deploy, ...] = ()

    @property
    def dests(self) -> list[str]:
        """The semester-side `repo/path`s its copies land in, deduped, in plan order."""
        return list(
            dict.fromkeys(
                f"{d.semester_dest_repo}/{deploy_dest(d)}" for d in self.deploys
            )
        )


def planned_rows(
    sched: schedule.Schedule, aliases: Aliases = _no_aliases
) -> list[PlannedRow]:
    """One row per dated `releases:` entry, in date order (ties in plan order). An
    undated (`event_datetime: tbc`) entry has no place on a dated list and is left out."""
    rows = []
    dated = [r for r in sched.releases if r.when is not None]
    for release in sorted(dated, key=lambda r: r.when):
        kind, inferred = entry_kind(release, aliases)
        rows.append(
            PlannedRow(
                key=release.label,
                kind=kind,
                when=release.when,
                kind_inferred=inferred,
                tbc=release.tbc,
                subtitle=release.title,
                details=release.details,
                shown=release.show_on_site,
                number=release.number,
                deploys=tuple(release.deploy),
            )
        )
    return rows


@dataclass
class SiteRow:
    """One row the site shows: an entry, its number, and the untitled readings entries
    that attach to it (a lecture's week's readings)."""

    row: PlannedRow
    number: int | None
    readings: list[PlannedRow] = field(default_factory=list)


def site_rows(rows: list[PlannedRow]) -> list[SiteRow]:
    """The rows the site shows, in date order (decision 0013).

    - An untitled `readings` entry attaches to the first SHOWN lecture on or after its
      date; a titled one, or one no lecture follows, is its own row.
    - A silent (`show_on_site: false`) entry is no row, unless it is readings: those are
      rows of the Readings tab, unnumbered.
    - A shown row's number: the entry's `number:`, else its label's number, else its
      position among the shown rows of its kind. The syllabus reads the same numbers."""
    lectures = [r for r in rows if r.shown and r.kind == "lecture"]
    attached: dict[str, list[PlannedRow]] = {}
    own = []
    for r in rows:
        if r.kind == "readings" and not r.subtitle:
            host = next((lec for lec in lectures if lec.when >= r.when), None)
            if host is not None:
                attached.setdefault(host.key, []).append(r)
                continue
        if r.shown or r.kind == "readings":
            own.append(r)
    out, position = [], {}
    for r in own:
        number = None
        if r.shown:
            position[r.kind] = position.get(r.kind, 0) + 1
            number = r.number or label_number(r.key) or position[r.kind]
        out.append(SiteRow(r, number, attached.get(r.key, [])))
    return out


def declared_syllabus(
    sched: schedule.Schedule,
    live_repos: Collection[str],
    tree: Callable[[str], Collection[str]],
    declaration: Callable[[str], Declared],
) -> tuple[str, str] | None:
    """`(repo, path)` of the syllabus released to this semester, or None.

    Decision 0013 item 5, in order:
    1. each source repo's declared syllabus (`materials.yml` `syllabus:`, default
       `SYLLABUS.md`), followed through the copy that ships it (that file, a folder
       holding it, the whole repo) to where it landed;
    2. the same file at its own path in a released repo (a copy made off the plan);
    3. when no repo declares one: a root file whose name contains `syllab`, an exact
       `syllabus.*` stem first.
    `tree(repo)` is a released repo's blob paths; `declaration(source repo)` its
    `materials.yml`. The site pins the answer on its home page; the student status names
    it."""
    declared: dict[str, None] = {}
    any_declared = False
    for release in sched.releases:
        for d in release.deploy:
            decl = declaration(d.course_source_repo)
            declared[decl.syllabus] = None
            any_declared = any_declared or decl.declared
            if d.semester_dest_repo not in live_repos:
                continue
            src, dest = d.course_source_path.strip("/"), deploy_dest(d)
            if src == decl.syllabus:
                path = dest
            elif not src or decl.syllabus.startswith(f"{src}/"):
                path = f"{dest}/{decl.syllabus[len(src) :].lstrip('/')}".strip("/")
            else:
                continue
            if path in tree(d.semester_dest_repo):
                return d.semester_dest_repo, path
    declared.setdefault(DEFAULT_SYLLABUS, None)
    for repo in sorted(live_repos):
        blobs = tree(repo)
        for path in declared:
            if path in blobs:
                return repo, path
    if any_declared:
        return None
    fallback = None
    for repo in sorted(live_repos):
        for path in tree(repo):
            if "/" in path or "syllab" not in path.lower():
                continue
            if path.rsplit(".", 1)[0].lower() == "syllabus":
                return repo, path
            fallback = fallback or (repo, path)
    return fallback


def offplan_folders(
    deploys: Iterable[schedule.Deploy], trees: Mapping[str, Iterable[str]]
) -> list[tuple[str, str, str]]:
    """`(repo, folder, kind)` for every released folder of a kind-named section
    (`lectures/05_x`, `labs/lab-2`, ..., or a top folder of a repo so named) that none of
    `deploys` covers: material released outside the plan (decision 0013 item 4). `trees`
    is `{semester repo: its blob paths}`. The site's off-plan rows follow the same rule."""
    copies = [(d.semester_dest_repo, deploy_dest(d)) for d in deploys]

    def covered(repo: str, folder: str) -> bool:
        return any(
            r == repo
            and (
                not p
                or p == folder
                or folder.startswith(f"{p}/")
                or p.startswith(f"{folder}/")
            )
            for r, p in copies
        )

    found: dict[tuple[str, str], str] = {}
    for repo in sorted(trees):
        for path in sorted(trees[repo]):
            parts = path.split("/")
            if len(parts) >= 3 and (kind := alias_kind(parts[0])):
                folder = "/".join(parts[:2])
            elif len(parts) >= 2 and (kind := alias_kind(repo)):
                folder = parts[0]
            else:
                continue
            if not publishable(path) or covered(repo, folder):
                continue
            found.setdefault((repo, folder), kind)
    return [(repo, folder, kind) for (repo, folder), kind in found.items()]
