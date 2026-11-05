"""Interpret a parsed `schedule.Schedule` into the rows a site or syllabus shows.

A row is one slot of one entry (decision 0038): its kind is the block it is written in,
its number and session the entry's, its files the entry's `files`. Nothing is inferred from
folder names. Pure: nothing touches GitHub or renders anything - `site` turns rows into
pages, `syllabus` into a table.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from . import policy, schedule
from .course import is_repo_root, within
from .faults import ConfigFault
from .gh_contents import line_of
from .log import Summary, log_err
from .materials import ASSETS_KIND, DEFAULT_SYLLABUS
from .readings import absolute_links

# The kind whose entries attach to a unit's first row (decision 0038 rule 5).
READINGS_KIND = "readings"


def deploy_dest(deploy: schedule.Deploy) -> str:
    """Where a deploy lands inside its destination repo - `semester_dest_path` when it is
    set, else the source path mirrored. Stated once, because the release, the site and the
    status all read it."""
    return (deploy.semester_dest_path or deploy.course_source_path).strip("/")


def dest_section(deploy: schedule.Deploy) -> str:
    """The section a copy's links are filed under: the top folder of where it lands, or
    the destination repo for a copy into its root."""
    head, sep, _ = deploy_dest(deploy).partition("/")
    return head if sep else deploy.semester_dest_repo


@dataclass
class PlannedRow:
    """What the plan says about one row, before anything has shipped: one slot of one
    entry.

    `key` is the entry's label (unique in the file), `slot` which of its slots this is;
    `deploys` are its copies in plan order, which is what the row links once they have
    landed. `shown` is the entry's `show_on_site`: a silent row stays off the schedule and
    the Updates box. `unit` is the entry's `session` (the teaching unit), "" for none.

    `when` is the slot's date, None when it is `tbc` (`undated`). `sort_at` is where the
    row sorts (`planned_rows`): its `when` when dated. It orders and nothing else."""

    key: str
    kind: str
    when: date | datetime | None
    sort_at: date | datetime
    tbc: bool = False
    undated: bool = False
    subtitle: str = ""
    details: str = ""
    shown: bool = True
    number: int | None = None
    deploys: tuple[schedule.Deploy, ...] = ()
    unit: str = ""
    location: str = ""
    # The entry's `Release.reading_list`: inline text, the course file, or None.
    reading_list: str | schedule.Deploy | None = None
    slot: int = 0
    # The entry's `Release.opens`: every slot's inline reading list shows from then.
    opens: datetime | None = None

    @property
    def reading_list_dest(self) -> tuple[str, str] | None:
        """`(repo, path)` its named reading-list file lands at in the semester, or None."""
        f = self.reading_list
        return list_dest(f, self.deploys) if isinstance(f, schedule.Deploy) else None

    @property
    def dests(self) -> list[str]:
        """The semester-side `repo/path`s its copies land in, deduped, in plan order. A
        path under another one listed (`labs/03_lab/solutions` beside `labs/03_lab`) is
        dropped: the pending-row sentence would otherwise link the same tree twice. Its
        reading-list file is no place a student opens, so it is not named."""
        named = self.reading_list_dest
        return outermost(
            f"{d.semester_dest_repo}/{deploy_dest(d)}"
            for d in self.deploys
            if (d.semester_dest_repo, deploy_dest(d)) != named
        )


def list_dest(
    listed: schedule.Deploy, deploys: Collection[schedule.Deploy]
) -> tuple[str, str]:
    """`(repo, path)` where a readings entry's course-file reading list
    (`Release.reading_list`) lands in the semester. Shipped as one of the entry's
    `deploys` itself, its own destination; else inside the copy whose source holds it
    (its folder): that copy's destination plus the rest of its path."""
    if not any(d is listed for d in deploys):
        for d in deploys:
            if d.course_source_repo != listed.course_source_repo:
                continue
            rest = within(d.course_source_path, listed.course_source_path)
            if rest is not None:
                to = "" if is_repo_root(deploy_dest(d)) else deploy_dest(d)
                return d.semester_dest_repo, "/".join(p for p in (to, rest) if p)
    return listed.semester_dest_repo, deploy_dest(listed)


def planned_rows(sched: schedule.Schedule) -> list[PlannedRow]:
    """One row per slot of every content entry (`Schedule.releases`), in date order:
    sorted on `sort_at`, stably, so ties keep plan order. An undated (`tbc`) row is
    flagged `undated` and sorts at the end of its kind (`_undated_placer`). A slot's own
    `location`/`details` beat the entry's."""

    def row(r: schedule.Release, i: int, slot: schedule.Slot, at) -> PlannedRow:
        return PlannedRow(
            key=r.label,
            kind=r.kind,
            when=slot.when,
            sort_at=at,
            tbc=r.tbc,
            undated=slot.when is None,
            subtitle=r.title,
            details=slot.details or r.details,
            shown=r.show_on_site,
            number=r.number,
            deploys=tuple(r.deploy),
            unit=r.session,
            location=slot.location or r.location,
            reading_list=r.reading_list,
            slot=i,
            opens=r.opens,
        )

    slots = [(r, i, slot) for r in sched.releases for i, slot in enumerate(r.row_slots)]
    dated = [(r, i, s) for r, i, s in slots if s.when is not None]
    place = _undated_placer(sched, [(r.kind, s.when) for r, _i, s in dated])
    tz = ZoneInfo(sched.timezone)
    rows = [
        row(r, i, s, s.when)
        for r, i, s in sorted(dated, key=lambda d: schedule._instant(d[2].when, tz))
    ]
    rows += [row(r, i, s, place(r.kind)) for r, i, s in slots if s.when is None]
    return sorted(rows, key=lambda r: schedule._instant(r.sort_at, tz))


def _undated_placer(
    sched: schedule.Schedule, dated: list[tuple[str, date | datetime]]
) -> Callable[[str], datetime]:
    """Where each next undated row of a kind sorts, given the `(kind, when)` of every
    dated row: a minute after the last dated row of that kind, one more per undated row of
    it placed before - so "Lab 9, TBC" follows Lab 8 on the schedule rather than piling up
    after the exams. A kind with no dated row sorts at the end of term (`term_end`)."""
    tz = ZoneInfo(sched.timezone)
    last: dict[str, datetime] = {}
    for kind, when in dated:
        at = schedule._instant(when, tz)
        last[kind] = max(last.get(kind, at), at)
    end = term_end(sched, list(last.values()))
    taken: dict[str, int] = {}

    def place(kind: str) -> datetime:
        taken[kind] = taken.get(kind, 0) + 1
        return last.get(kind, end) + timedelta(minutes=taken[kind])

    return place


def term_end(sched: schedule.Schedule, dated: list[datetime]) -> datetime:
    """Where an undated row of a kind with no dated row sorts: the end of term, else the
    last dated row, else the start of term (else today)."""
    tz = ZoneInfo(sched.timezone)
    if sched.semester_end:
        return datetime.combine(sched.semester_end, time(23, 0), tzinfo=tz)
    if dated:
        return max(dated)
    return datetime.combine(sched.semester_start or date.today(), time(9, 0), tzinfo=tz)


@dataclass
class SiteRow:
    """One row the site shows: a row, its number, the readings entries attached to it
    (on the first row of its unit only), and `chip`: its unit has readings, so the row
    carries the readings chip."""

    row: PlannedRow
    number: int | None
    readings: list[PlannedRow] = field(default_factory=list)
    chip: bool = False


def unit_hosts(rows: list[PlannedRow]) -> dict[str, PlannedRow]:
    """Each unit's FIRST row by date: a shown row of any kind but readings and supporting
    files. What a readings entry with that `session:` shows on (decision 0038 rule 5). A
    dated row comes before any undated (`tbc`) one; a unit with no date yet takes its first
    row in the policy's kind order (the lecture before the lab)."""
    order = {kind["key"]: i for i, kind in enumerate(policy.kinds())}
    hosts: dict[str, PlannedRow] = {}
    for r in sorted(
        rows,
        key=lambda r: (r.undated, 0 if not r.undated else order.get(r.kind, 0)),
    ):
        if r.unit and r.shown and r.kind not in (READINGS_KIND, ASSETS_KIND):
            hosts.setdefault(r.unit, r)
    return hosts


def shown_list(
    row: PlannedRow,
    host: PlannedRow | None,
    landed: bool,
    now: datetime,
    present: Callable[[str, str], bool],
    read: Callable[[str, str], str | None],
    url: Callable[[str, str], str],
) -> str:
    """The reading list readings entry `row` shows now, or "" - the ONE rule the semester
    site and the student file both apply (decision 0044 point 2: students see the RELEASED
    copy). A course file's is the text of its copy in the semester (`read(repo, path)`),
    once that is there (`present`), its relative links made the released copies' URLs
    (`url(repo, path)`, `readings.absolute_links`). Inline text (no folder) is left as
    written; it shows once the entry has released: one of
    its copies landed (`landed`), or, for an entry that ships none, at its `opens` (an
    undated one attached to a unit: its `host` row's)."""
    if (named := row.reading_list_dest) is not None:
        if not present(*named):
            return ""
        repo, path = named
        folder = path.rpartition("/")[0]
        return absolute_links(read(repo, path) or "", folder, lambda p: url(repo, p))
    opens = row.opens if row.opens is not None or host is None else host.opens
    released = landed if row.deploys else opens is not None and opens <= now
    return (row.reading_list or "") if released else ""


class Landing(NamedTuple):
    """Where a readings entry's reading-list file lands in its semester repo, and the
    entry's key: shown as text on that row, never linked."""

    path: str
    key: str


def landings(rows: Iterable[PlannedRow]) -> dict[str, tuple[Landing, ...]]:
    """Every reading-list file `rows` land (`PlannedRow.reading_list_dest`), per
    semester repo: the paths `site_repo.landed_links` never links, and counts as landed
    only for the row whose list it is."""
    out: dict[str, dict[Landing, None]] = {}
    for r in rows:
        if (named := r.reading_list_dest) is not None:
            out.setdefault(named[0], {})[Landing(named[1], r.key)] = None
    return {repo: tuple(found) for repo, found in out.items()}


def site_rows(rows: list[PlannedRow]) -> list[SiteRow]:
    """The rows the site shows, in date order (decisions 0013, 0020, 0038).

    - A shown row's number is its entry's; re-dating, hiding or adding an entry moves
      nobody's number. The syllabus reads the same numbers.
    - A readings entry whose `session` names a unit with a shown row is attached to that
      unit's first row by date (`unit_hosts`), silent or not, and every shown row of the
      unit carries the readings chip. Any other readings entry is its own row.
    - A silent (`show_on_site: false`) entry is no row, unless it is readings: an
      unattached one is a row of the Readings tab, unnumbered.
    - A supporting-files (`assets`) entry is never a row: it is released, not shown
      (decision 0026 rule 3)."""
    hosts = unit_hosts(rows)
    attached: dict[tuple[str, int], list[PlannedRow]] = {}
    seen: set[str] = set()
    own = []
    for r in rows:
        if r.kind == ASSETS_KIND:
            continue
        if r.kind == READINGS_KIND and r.unit in hosts:
            host = hosts[r.unit]
            if r.key not in seen:
                seen.add(r.key)
                attached.setdefault((host.key, host.slot), []).append(r)
        elif r.shown or r.kind == READINGS_KIND:
            own.append(r)
    with_readings = {h.unit for h in hosts.values() if (h.key, h.slot) in attached}
    return [
        SiteRow(
            r,
            r.number if r.shown else None,
            attached.get((r.key, r.slot), []),
            r.kind != READINGS_KIND and r.unit in with_readings,
        )
        for r in own
    ]


# ------------------------------------------------------------------ explicit numbers
# Decision 0038 (amendment): a lecture or lab names its unit (`session:`, its number by
# default) and an assignment its number, explicitly. One without is a problem, and its
# release or hand-out is refused with these sentences, on every path.
NOT_NUMBERED = "NOT_NUMBERED"
NO_SESSION = "NO_SESSION"


def give_a_number(key: str) -> str:
    """The one sentence an assignment with no number earns, wherever it is refused."""
    return f"Give {key} a number first."


def give_a_due_date(key: str) -> str:
    """The one sentence an assignment with no `due_datetime` earns: it parses as TBC and
    nothing fires for it until it has one."""
    return f"Give {key} a due date."


def give_a_session(key: str) -> str:
    """The one sentence a lecture or lab with no session earns, wherever it is refused."""
    return f"Give {key} a session first."


def needs_session(release: schedule.Release) -> bool:
    """Whether a content entry must name its unit: a lecture or a lab
    (`schedule.SESSION_KINDS`). Readings name one only to attach; exams, events and
    supporting files never."""
    return release.kind in schedule.SESSION_KINDS


def _first_fire(release: schedule.Release) -> datetime | None:
    """The earliest moment any copy of `release` ships: its own `deploy_datetime`, else
    the entry's `event_datetime`."""
    moments = [d.deploy_datetime or release.when for d in release.deploy]
    return min((m for m in moments if m is not None), default=release.when)


@dataclass(frozen=True)
class Unnumbered:
    """An entry missing what names it: `block` is `releases` (a lecture or lab with no
    `session`; `key` its key) or `assignments` (no `number`), `where` its YAML path,
    `fires` the release or hand-out it holds up (None: none is dated), `line` the
    schedule.yml line to fix."""

    block: str
    key: str
    kind: str
    fires: datetime | None
    line: int | None
    copies: bool = True  # False: a release row with nothing to copy
    where: str = ""

    @property
    def field(self) -> str:
        """What it lacks: `session` (a lecture or lab) or `number` (an assignment)."""
        return "number" if self.block == "assignments" else "session"


def unnumbered(sched: schedule.Schedule) -> list[Unnumbered]:
    """Every lecture or lab with no `session` and every assignment with no `number`,
    content entries first, in plan order."""
    out = []
    for r in sched.releases:
        if needs_session(r) and not r.session:
            out.append(
                Unnumbered(
                    "releases",
                    r.label,
                    r.kind,
                    _first_fire(r),
                    line_of(r.lines, "session"),
                    bool(r.deploy),
                    r.where,
                )
            )
    for key, entry in sched.assignments.items():
        if entry.number is None:
            out.append(
                Unnumbered(
                    "assignments",
                    key,
                    "assignment",
                    entry.handout_datetime,
                    line_of(entry.lines, "number"),
                    where=f"assignments.{key}",
                )
            )
    return out


def duplicate_numbers(sched: schedule.Schedule) -> list[tuple[str, int, list[str]]]:
    """`(kind, number, keys)` for every number two or more entries of one kind share: a
    shown entry's number (its `number:`, else its `session:`), per kind (decision 0038
    amendment rule 3)."""
    groups: dict[tuple[str, int], list[str]] = {}
    for r in sched.releases:
        if r.show_on_site and (n := r.number):
            groups.setdefault((r.kind, n), []).append(r.label)
    for key, entry in sched.assignments.items():
        if n := entry.number:
            groups.setdefault(("assignment", n), []).append(key)
    return [(kind, n, keys) for (kind, n), keys in groups.items() if len(keys) > 1]


def kind_plural(kind: str) -> str:
    """`lectures`, `labs`, `readings`: the kind's policy `plural`."""
    return policy.plural_of(kind)


def duplicate_text(kind: str, n: int, keys: list[str]) -> str:
    """ "Two lectures are numbered 3: lecture-a, lecture-b"."""
    count = {2: "Two", 3: "Three"}.get(len(keys), str(len(keys)))
    return f"{count} {kind_plural(kind)} are numbered {n}: {', '.join(keys)}"


def number_faults(missing: list[Unnumbered]) -> list[ConfigFault]:
    """One fault per entry missing its session or number (`unnumbered`), for the
    schedule.yml digest: on the clock of the release or hand-out it stops, like a source
    that is not found."""
    out = []
    for m in missing:
        if m.block == "assignments":
            cost = "the hand out is skipped until it has one"
        elif m.copies:
            cost = "the release is skipped until it has one"
        else:
            cost = "its row on the site has no number until it has one"
        out.append(
            ConfigFault(
                m.where or m.key,
                f"{m.key} has no {m.field}",
                fires=m.fires if m.block == "assignments" or m.copies else None,
                field=m.field,
                lineno=m.line,
                file=schedule.SCHEDULE_PATH,
                fix_text=f"give the entry a `{m.field}:`",
                consequence=cost,
                noun=(f"entry without a {m.field}", f"entries without a {m.field}"),
                plain=f"Give {m.key} a {m.field}.",
            )
        )
    return out


def refuse_unnumbered(text: str) -> Summary:
    """A manual run's refusal of an entry with no session or number: `text` is
    `give_a_session`'s or `give_a_number`'s, which names a schedule key and nobody."""
    log_err(text)
    code = NO_SESSION if "session" in text else NOT_NUMBERED
    return Summary(text, reasons=[{"code": code, "text": text}], code=1)


def unnumbered_assignment(
    sched: schedule.Schedule, template: str, slug: str = ""
) -> str | None:
    """The refusal for a manual run on the assignment entry `template` hands out (the
    entry `slug`, when given) when that entry has no number; None when it has one. With
    no slug and two entries citing `template`, which one is meant is not known: None, and
    `schedule.resolve_target` refuses the run, naming the remedy."""
    entries = schedule.entries_for_repo(sched, template)
    if slug:
        entries = [(key, entry) for key, entry in entries if key == slug]
    elif len(entries) > 1:
        return None
    for key, entry in entries:
        if entry.number is None:
            return give_a_number(key)
    return None


def outermost(paths: Iterable[str]) -> list[str]:
    """`paths` deduped, in order, without any that lies inside another one listed
    (`within`: `labs/03/solutions` beside `labs/03`; everything beside the root "")."""
    found = list(dict.fromkeys(paths))
    return [
        p for p in found if not any(q != p and within(q, p) is not None for q in found)
    ]


def unnumbered_release(
    sched: schedule.Schedule, repo: str, paths: Iterable[str]
) -> str | None:
    """The refusal for a manual release of `paths` from `repo` that a lecture or lab with
    no session copies, itself or inside a requested folder; None otherwise."""
    wanted = list(paths)
    held = {m.key for m in unnumbered(sched) if m.block == "releases"}
    for r in sched.releases:
        if r.label in held and any(
            d.course_source_repo == repo
            and any(within(w, d.course_source_path) is not None for w in wanted)
            for d in r.deploy
        ):
            return give_a_session(r.label)
    return None


def covered_copies(
    sched: schedule.Schedule, made: schedule.Deploy
) -> list[tuple[schedule.Release, schedule.Deploy, str]]:
    """The schedule copies a hand release `made` (a path pair, off the plan) has made:
    `(entry, the copy, where it sits inside made)` for each copy of the same source repo
    whose source is `made`'s or inside it, and which lands where `made` put it - a folder
    released to somewhere else is not that copy."""
    out = []
    for r in sched.releases:
        for d in r.deploy:
            if (d.course_source_repo, d.semester_dest_repo) != (
                made.course_source_repo,
                made.semester_dest_repo,
            ):
                continue
            sub = within(made.course_source_path, d.course_source_path)
            if sub is not None and within(deploy_dest(made), deploy_dest(d)) == sub:
                out.append((r, d, sub))
    return out


def declared_syllabus(
    sched: schedule.Schedule,
    live_repos: Collection[str],
    tree: Callable[[str], Collection[str]],
    declared_path: str = "",
) -> tuple[str, str] | None:
    """`(repo, path)` of the syllabus released to this semester, or None.

    Decision 0013 item 5, in order:
    1. the semester's declared syllabus (`semester.yml` `student_site.syllabus`, decision
       0039; "" = undeclared, `SYLLABUS.md`), followed through the copy that ships it
       (that file, a folder holding it, the whole repo) to where it landed;
    2. the same file at its own path in a released repo (a copy made off the plan);
    3. when the semester declares none: a root file whose name contains `syllab`, an
       exact `syllabus.*` stem first.
    `tree(repo)` is a released repo's blob paths. The site pins the answer on its home
    page; the student status names it."""
    syllabus = declared_path.strip().strip("/") or DEFAULT_SYLLABUS
    declared: dict[str, None] = {syllabus: None}
    any_declared = bool(declared_path.strip())
    for release in sched.releases:
        for d in release.deploy:
            if d.semester_dest_repo not in live_repos:
                continue
            src, dest = d.course_source_path.strip("/"), deploy_dest(d)
            if src == syllabus:
                path = dest
            elif not src or syllabus.startswith(f"{src}/"):
                path = f"{dest}/{syllabus[len(src) :].lstrip('/')}".strip("/")
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
