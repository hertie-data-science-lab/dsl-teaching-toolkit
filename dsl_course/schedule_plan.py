"""Interpret a parsed `schedule.Schedule` into the rows a site or syllabus shows.

A row is a `releases:` entry: its date, its kind, its name and the copies it makes. Rows
come in date order and nothing is inferred from folder names beyond one fallback - an entry
that declares no `kind` takes it from the section its first copy lands in
(`materials.infer_kind`). An `NN_` prefix on a folder means nothing here. Pure: nothing
touches GitHub or renders anything - `site` turns rows into pages, `syllabus` into a table.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from . import policy, schedule
from .faults import ConfigFault
from .gh_contents import line_of
from .materials import (
    DEFAULT_KIND,
    DEFAULT_SYLLABUS,
    Declared,
    alias_kind,
    infer_kind,
    publishable,
)
from .schedule import label_number

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
    """One row the site shows: an entry, its number, and the numbered readings entries
    that join it (a lecture's readings)."""

    row: PlannedRow
    number: int | None
    readings: list[PlannedRow] = field(default_factory=list)


def own_number(number: int | None, key: str) -> int | None:
    """An entry's number (decision 0020): its `number:`, else the number its label or key
    carries (`lecture_03`, `assignment-3`: the instructor typed it). Never a position."""
    return number or label_number(key)


def site_rows(rows: list[PlannedRow]) -> list[SiteRow]:
    """The rows the site shows, in date order (decisions 0013, 0020).

    - A shown row's number: `own_number`, else none (the row says "Lecture" alone).
      Re-dating, hiding or adding an entry moves nobody's number. The syllabus reads the
      same numbers.
    - A `readings` entry with a number (`number:`, else its label's, `readings_03`)
      joins the shown lecture with that number. Nothing is inferred from dates: any
      other readings entry, or one whose number no lecture carries, is its own row.
    - A silent (`show_on_site: false`) entry is no row, unless it is readings: an
      unjoined one is a row of the Readings tab, unnumbered."""
    lecture_numbers: dict[int, str] = {}
    for r in rows:
        if r.shown and r.kind == "lecture" and (n := own_number(r.number, r.key)):
            lecture_numbers.setdefault(n, r.key)
    attached: dict[str, list[PlannedRow]] = {}
    own = []
    for r in rows:
        n = own_number(r.number, r.key)
        if r.kind == "readings" and n and (host := lecture_numbers.get(n)):
            attached.setdefault(host, []).append(r)
        elif r.shown or r.kind == "readings":
            own.append(r)
    return [
        SiteRow(
            r,
            own_number(r.number, r.key) if r.shown else None,
            attached.get(r.key, []),
        )
        for r in own
    ]


# ------------------------------------------------------------------ explicit numbers
# Decision 0020: a number is explicit. An entry of a numbered kind with none is a problem,
# and its release or hand-out is refused with this sentence, on every path.
NOT_NUMBERED = "NOT_NUMBERED"


def give_a_number(key: str) -> str:
    """The one sentence an entry with no number earns, wherever it is refused."""
    return f"Give {key} a number first."


def needs_number(release: schedule.Release, kind: str) -> bool:
    """Whether a `releases:` entry must carry a number: every row the site shows, except
    readings - a readings number means "join that lecture" (decision 0013 rule 3), so a
    stand-alone readings row is rightly unnumbered."""
    return release.show_on_site and kind != "readings"


def _first_fire(release: schedule.Release) -> datetime | None:
    """The earliest moment any copy of `release` ships: its own `deploy_datetime`, else
    the entry's `event_datetime`."""
    moments = [d.deploy_datetime or release.when for d in release.deploy]
    return min((m for m in moments if m is not None), default=release.when)


@dataclass(frozen=True)
class Unnumbered:
    """An entry of a numbered kind with no number: `block` is `releases` or
    `assignments`, `fires` the release or hand-out it holds up (None: none is dated),
    `line` the schedule.yml line to fix."""

    block: str
    key: str
    kind: str
    fires: datetime | None
    line: int | None
    copies: bool = True  # False: a release row with nothing to copy


def unnumbered(
    sched: schedule.Schedule, aliases: Aliases = _no_aliases
) -> list[Unnumbered]:
    """Every entry that needs a number and has none, releases first, in plan order."""
    out = []
    for r in sched.releases:
        kind, _ = entry_kind(r, aliases)
        if needs_number(r, kind) and own_number(r.number, r.label) is None:
            out.append(
                Unnumbered(
                    "releases",
                    r.label,
                    kind,
                    _first_fire(r),
                    line_of(r.lines, "number"),
                    bool(r.deploy),
                )
            )
    for key, entry in sched.assignments.items():
        if own_number(entry.number, key) is None:
            out.append(
                Unnumbered(
                    "assignments",
                    key,
                    "assignment",
                    entry.handout_datetime,
                    line_of(entry.lines, "number"),
                )
            )
    return out


def duplicate_numbers(
    sched: schedule.Schedule, aliases: Aliases = _no_aliases
) -> list[tuple[str, int, list[str]]]:
    """`(kind, number, keys)` for every number two or more entries of one kind share."""
    groups: dict[tuple[str, int], list[str]] = {}
    for r in sched.releases:
        kind, _ = entry_kind(r, aliases)
        if needs_number(r, kind) and (n := own_number(r.number, r.label)):
            groups.setdefault((kind, n), []).append(r.label)
    for key, entry in sched.assignments.items():
        if n := own_number(entry.number, key):
            groups.setdefault(("assignment", n), []).append(key)
    return [(kind, n, keys) for (kind, n), keys in groups.items() if len(keys) > 1]


def kind_plural(kind: str) -> str:
    """`lectures`, `labs`, `assignments`: the kind's label, as a plural noun."""
    label = next((k["label"] for k in policy.kinds() if k["key"] == kind), kind)
    return f"{label.lower()}s"


def duplicate_text(kind: str, n: int, keys: list[str]) -> str:
    """ "Two lectures are numbered 3: lecture-a, lecture-b"."""
    count = {2: "Two", 3: "Three"}.get(len(keys), str(len(keys)))
    return f"{count} {kind_plural(kind)} are numbered {n}: {', '.join(keys)}"


def number_faults(
    sched: schedule.Schedule, aliases: Aliases = _no_aliases
) -> list[ConfigFault]:
    """One fault per entry with no number, for the schedule.yml digest: on the clock of
    the release or hand-out it stops, like a source that is not found."""
    out = []
    for m in unnumbered(sched, aliases):
        if m.block == "assignments":
            cost = "the hand out is skipped until it has one"
        elif m.copies:
            cost = "the release is skipped until it has one"
        else:
            cost = "its row on the site has no number until it has one"
        out.append(
            ConfigFault(
                f"{m.block}.{m.key}",
                f"{m.key} has no number",
                fires=m.fires if m.block == "assignments" or m.copies else None,
                field="number",
                lineno=m.line,
                file=schedule.SCHEDULE_PATH,
                fix_text="give the entry a `number:`",
                consequence=cost,
                noun=("entry without a number", "entries without a number"),
                plain=f"Give {m.key} a number.",
            )
        )
    return out


def unnumbered_assignment(
    sched: schedule.Schedule, template: str, slug: str = ""
) -> str | None:
    """The refusal for a manual run on an assignment entry `template` hands out (the
    entry `slug`, when given) that has no number; None when it has one."""
    for key, entry in schedule.entries_for_repo(sched, template):
        if (not slug or key == slug) and own_number(entry.number, key) is None:
            return give_a_number(key)
    return None


def unnumbered_release(
    sched: schedule.Schedule,
    repo: str,
    paths: Iterable[str],
    aliases: Aliases = _no_aliases,
) -> str | None:
    """The refusal for a manual release of `paths` from `repo` that a numbered entry of
    the plan copies and that entry has no number; None otherwise (an entry with a number,
    or a copy the plan does not name)."""
    wanted = {p.strip("/") for p in paths}
    held = {m.key for m in unnumbered(sched, aliases) if m.block == "releases"}
    for r in sched.releases:
        if r.label in held and any(
            d.course_source_repo == repo and d.course_source_path.strip("/") in wanted
            for d in r.deploy
        ):
            return give_a_number(r.label)
    return None


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
