"""dsl-course student-status.json -- what every student of a semester may know, in one
PUBLIC file.

The student console reads a semester's shared facts from here (the schedule by kind, the
assignments' dates, rules and briefs, the instructors' cards, the home text and
announcements, the materials index) instead of from the semester site. The engine writes
it to the semester org's `.github` under `.system/`, beside `status.json`'s refresh: the
same facts, the same moment.

PUBLIC, and so ALLOW-LISTED. `.github` is a public repo, so every key at every level is
named below and the exported schema refuses any other (`additionalProperties: false`
throughout). It carries nothing about a person: no roster, no marks, no handles, no
emails but the ones an instructor chose to show (`show_email: true`), no enrol codes, no
team membership - a team is its name, its headcount and its cap. The brief and the shape
note are written only once an assignment has been handed out, exactly as the site
withheld them.

TWO HALVES, like `status_json`: `gather` reads what `SemesterFacts` does not already hold,
`render` is pure.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

import yaml

from . import grades, policy, records, schedule, teams
from .course import (
    CONFIG_REPO,
    CUTOFF_SENTENCE,
    INSTRUCTORS_FILE,
    SELF_SELECT,
    identifier,
    is_repo_root,
    late_rule,
    pages_repo,
    row_name,
    semester_label,
    semester_of,
    shape_note,
)
from .discovery import handed_out_assignments
from .faults import Unusable
from .gh_contents import get_file_content, repo_tree
from .materials import read as read_materials
from .readings import demote_headings, is_reading_overlay
from .repos import default_branch, has_denied_component, has_never_material_component
from .schedule_plan import (
    PlannedRow,
    declared_syllabus,
    deploy_dest,
    planned_rows,
    site_rows,
)
from .site_repo import people_cards, yaml_file
from .status_json import CourseFacts, SemesterFacts

SCHEMA = "dsl.student-status/1"
# In the SEMESTER org's `.github`, which is public.
REPO = ".github"
PATH = records.path("student_status")
ANNOUNCEMENTS_DIR = "_announcements"
SITE_HOME = "index.md"

# ---------------------------------------------------------------- the allow-list

TOP_KEYS = (
    "schema",
    "semester",
    "course_name",
    "term_label",
    "timezone",
    "archive_datetime",
    "home_markdown",
    "syllabus",
    "kinds",
    "rows",
    "assignments",
    "instructors",
    "late_policy",
    "materials_repos",
    "materials_index",
    "announcements",
)
ROW_KEYS = (
    "id",
    "kind",
    "number",
    "when",
    "all_day",
    "title",
    "subtitle",
    "details",
    "assignment",
    "released",
    "tbc",
    "off_schedule",
    "links",
    "readings",
    "reading_list",
    "readings_pending",
)
LINK_KEYS = ("name", "repo", "path", "url")
ASSIGNMENT_KEYS = (
    "slug",
    "title",
    "subtitle",
    "handout_datetime",
    "due_datetime",
    "grading_cutoff_datetime",
    "late_rule",
    "cutoff_sentence",
    "submit_via",
    "shape",
    "shape_note",
    "private_repo",
    "submit_url",
    "group",
    "team_formation",
    "teams",
    "solution_datetime",
    "max_points",
    "handed_out",
    "brief",
    "tbc",
)
TEAM_FORMATION_KEYS = ("closes_datetime", "max_team_size")
# A team as the public may know it: never who.
TEAM_KEYS = ("name", "members", "cap")
INSTRUCTOR_KEYS = ("name", "title", "webpage", "picture", "role", "email")
ANNOUNCEMENT_KEYS = ("when", "title", "details")
SYLLABUS_KEYS = ("repo", "path")
KIND_KEYS = ("label", "colour", "background")
INDEX_KEYS = ("repo", "paths")

_S, _SN = {"type": "string"}, {"type": ["string", "null"]}
_B, _IN = {"type": "boolean"}, {"type": ["integer", "null"]}


def _closed(props: dict[str, dict], nullable: bool = False) -> dict:
    return {
        "type": ["object", "null"] if nullable else "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _list(item: dict) -> dict:
    return {"type": "array", "items": item}


def json_schema() -> dict:
    """The file's shape, every level closed: the allow-list the PII test holds it to, and
    what the console validates against (`console/schemas/student-status.schema.json`)."""
    link = _closed({k: _S for k in LINK_KEYS})
    row_types = {
        "number": _IN,
        "all_day": _B,
        "released": _B,
        "tbc": _B,
        "off_schedule": _B,
        "readings_pending": _B,
        "when": _SN,
        "assignment": _SN,
        "links": _list(link),
        "readings": _list(link),
    }
    assignment_types = {
        "handout_datetime": _SN,
        "due_datetime": _SN,
        "grading_cutoff_datetime": _SN,
        "solution_datetime": _SN,
        "private_repo": _B,
        "group": _B,
        "handed_out": _B,
        "tbc": _B,
        "max_points": {"type": ["number", "null"]},
        "team_formation": _closed(
            {"closes_datetime": _S, "max_team_size": _IN}, nullable=True
        ),
        "teams": _list(
            _closed({"name": _S, "members": {"type": "integer"}, "cap": _IN})
        ),
    }
    top_types = {
        "archive_datetime": _SN,
        "syllabus": _closed({k: _S for k in SYLLABUS_KEYS}, nullable=True),
        "kinds": {
            "type": "object",
            "additionalProperties": _closed({k: _S for k in KIND_KEYS}),
        },
        "rows": _list(_closed({k: row_types.get(k, _S) for k in ROW_KEYS})),
        "assignments": _list(
            _closed({k: assignment_types.get(k, _S) for k in ASSIGNMENT_KEYS})
        ),
        "instructors": _list(
            _closed(
                {
                    **{k: _S for k in INSTRUCTOR_KEYS},
                    "role": {"enum": ["instructor", "teaching_assistant"]},
                }
            )
        ),
        "late_policy": _list(_S),
        "materials_repos": _list(_S),
        "materials_index": _list(
            _closed({"repo": _S, "paths": _list(_S)}),
        ),
        "announcements": _list(_closed({k: _S for k in ANNOUNCEMENT_KEYS})),
    }
    body = _closed({k: top_types.get(k, _S) for k in TOP_KEYS})
    body["properties"]["schema"] = {"enum": [SCHEMA]}
    return body


# ---------------------------------------------------------------- facts


@dataclass
class StudentFacts:
    """What the student file needs beyond `SemesterFacts`, read by `gather`."""

    # The semester-side names of the assignments that have been handed out.
    handed_out: frozenset[str] = frozenset()
    # `{schedule key: the template's README}`, for handed-out assignments only.
    readmes: dict[str, str] = field(default_factory=dict)
    # `{schedule key: the team cap}`, for self-select group assignments.
    caps: dict[str, int] = field(default_factory=dict)
    # `{(repo, path): text}` of every reading-list overlay that landed.
    overlays: dict[tuple[str, str], str] = field(default_factory=dict)
    syllabus: tuple[str, str] | None = None
    # (instructors, teaching assistants) as site cards; None when instructors.yml
    # declares nobody.
    cards: tuple[list[dict], list[dict]] | None = None
    home: str = ""
    announcements: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- pure core


def _iso(when: datetime | date | None) -> str | None:
    return when.isoformat() if when is not None else None


def _all_day(when: datetime | date | None) -> bool:
    return when is not None and not isinstance(when, datetime)


def _kind_label(kind: str) -> str:
    return next((k["label"] for k in policy.kinds() if k["key"] == kind), kind)


def _url(org: str, repo: str, path: str, folder: bool) -> str:
    return f"https://github.com/{org}/{repo}/{'tree' if folder else 'blob'}/HEAD/{path}"


def _material(path: str) -> bool:
    """A released path the public may see named: not denylisted (`solution/`, `tests/`,
    `grading_config.yml`), not machine clutter."""
    return not has_denied_component(path) and not has_never_material_component(path)


def _copy_links(
    org: str, repo: str, dest: str, paths: Iterable[str], readings: bool
) -> tuple[list[dict], list[str]]:
    """(links, reading-list overlays) for what one copy has landed: a file is one link, a
    folder its own files plus one link per subfolder (as GitHub lists it). A readings
    copy's `READINGS.md` is prose, not a link."""
    blobs = [p for p in paths if _material(p)]
    if dest and dest in blobs:
        name = dest.rsplit("/", 1)[-1]
        if readings and is_reading_overlay(name):
            return [], [dest]
        return [
            {
                "name": name,
                "repo": repo,
                "path": dest,
                "url": _url(org, repo, dest, False),
            }
        ], []
    prefix = f"{dest}/" if dest else ""
    inside = [p for p in blobs if p.startswith(prefix)]
    overlays = [p for p in inside if readings and is_reading_overlay(p)]
    files, folders = [], {}
    for p in inside:
        if p in overlays:
            continue
        rest = p[len(prefix) :]
        head, sep, _ = rest.partition("/")
        if sep:
            folders[head] = folders.get(head, 0) + 1
        else:
            files.append(
                {
                    "name": rest,
                    "repo": repo,
                    "path": p,
                    "url": _url(org, repo, p, False),
                }
            )
    for head, n in folders.items():
        path = f"{prefix}{head}"
        files.append(
            {
                "name": f"{head}/ ({n} file{'' if n == 1 else 's'})",
                "repo": repo,
                "path": path,
                "url": _url(org, repo, path, True),
            }
        )
    return files, overlays


def _landed(
    facts: SemesterFacts, row: PlannedRow, readings: bool
) -> tuple[list[dict], list[str], bool]:
    """(links, overlays, landed) for every copy of `row` that is in its destination. A
    copy inside another copy of the same row (a lab's `solutions/`) is that one's."""
    dests = [
        (d.semester_dest_repo, "" if is_repo_root(deploy_dest(d)) else deploy_dest(d))
        for d in row.deploys
    ]
    links, overlays, landed = [], [], False
    for repo, dest in dests:
        paths = facts.dest_paths.get(repo)
        if not paths:
            continue
        if any(
            r == repo and p != dest and (not p or dest.startswith(f"{p}/"))
            for r, p in dests
        ):
            continue
        if (
            dest
            and dest not in paths
            and not any(p.startswith(f"{dest}/") for p in paths)
        ):
            continue
        got, prose = _copy_links(facts.org, repo, dest, paths, readings)
        landed = landed or bool(got or prose)
        links += got
        overlays += [(repo, p) for p in prose]
    return links, overlays, landed


def _reading_list(extra: StudentFacts, overlays: list[tuple[str, str]]) -> str:
    parts = [
        demote_headings(extra.overlays[o].strip())
        for o in overlays
        if (extra.overlays.get(o) or "").strip()
    ]
    return "\n\n".join(parts)


def _row(
    id_: str,
    kind: str,
    when: datetime | date | None,
    title: str,
    *,
    all_day: bool | None = None,
    subtitle: str = "",
    details: str = "",
    assignment: str | None = None,
    released: bool = True,
    tbc: bool = False,
    number: int | None = None,
    off_schedule: bool = False,
    links: list[dict] | None = None,
    readings: list[dict] | None = None,
    reading_list: str = "",
    readings_pending: bool = False,
) -> dict:
    return {
        "id": id_,
        "kind": kind,
        "number": number,
        "when": _iso(when),
        "all_day": _all_day(when) if all_day is None else all_day,
        "title": title,
        "subtitle": subtitle,
        "details": details or "",
        "assignment": assignment,
        "released": released,
        "tbc": tbc,
        "off_schedule": off_schedule,
        "links": links or [],
        "readings": readings or [],
        "reading_list": reading_list,
        "readings_pending": readings_pending,
    }


def release_rows(facts: SemesterFacts, extra: StudentFacts) -> list[dict]:
    """A row per entry the site shows (`schedule_plan.site_rows`), numbered as the site
    numbers it, with the files it has landed and the readings attached to it."""
    aliases = lambda repo: facts.aliases.get(repo, {})
    out = []
    for sr in site_rows(planned_rows(facts.sched, aliases)):
        r = sr.row
        own = r.kind == "readings"
        links, prose, landed = _landed(facts, r, own)
        if not r.shown and not landed:
            continue
        readings, pending = [], False
        for attached in sr.readings:
            got, more, done = _landed(facts, attached, True)
            readings += got
            prose += more
            pending = pending or not done
        label = _kind_label(r.kind)
        title = f"{label} {sr.number}" if sr.number is not None else label
        out.append(
            _row(
                r.key,
                r.kind,
                r.when,
                title,
                subtitle=row_name(r.subtitle, title),
                details=r.details,
                released=landed or bool(readings),
                tbc=r.tbc,
                number=sr.number,
                off_schedule=not r.shown,
                links=links + readings,
                readings=links if own else readings,
                reading_list=_reading_list(extra, prose),
                readings_pending=pending,
            )
        )
    return out


def _public_assignments(
    sched: schedule.Schedule,
) -> list[tuple[str, schedule.AssignmentEntry]]:
    """The plan's assignments the site may show: `show_on_site: false` keeps one out of
    every public surface."""
    return [(k, e) for k, e in sched.assignments.items() if e.show_on_site]


def event_rows(facts: SemesterFacts) -> list[dict]:
    """The display-only rows: hand-out and due, events, marks expected, the term's
    boundaries and the archive."""
    sched = facts.sched
    out = []
    for key, entry in _public_assignments(sched):
        name = schedule.semester_name(key, entry)
        title = identifier(name)
        if entry.handout_datetime is not None:
            out.append(
                _row(
                    f"{name}:handout",
                    "assignment",
                    entry.handout_datetime,
                    title,
                    details=entry.details,
                    assignment=name,
                    tbc=entry.tbc,
                )
            )
        out.append(
            _row(
                f"{name}:due",
                "due",
                entry.due_datetime,
                title,
                details=entry.details,
                assignment=name,
                tbc=entry.tbc,
            )
        )
        if entry.marks_return_datetime is not None and entry.marks_return_on_site:
            out.append(
                _row(
                    f"marks-{name}",
                    "special_event",
                    entry.marks_return_datetime,
                    f"Marks expected: {title}",
                    assignment=name,
                    tbc=entry.tbc,
                )
            )
    for ev in sched.events:
        if not ev.show_on_site:
            continue
        # An undated event (`event_datetime: tbc`) sorts at the end of term, marked TBC.
        when = ev.when if ev.when is not None else sched.semester_end
        out.append(
            _row(
                ev.label,
                "exam" if ev.kind == "exam" else "special_event",
                when,
                ev.title or ev.label.replace("-", " ").replace("_", " ").title(),
                details=ev.details,
                tbc=ev.tbc or ev.when is None,
            )
        )
    for id_, title, when in (
        ("term-start", "Semester starts", sched.semester_start),
        ("term-end", "Semester ends", sched.semester_end),
    ):
        if when is not None:
            out.append(_row(id_, "term_date", when, title, all_day=True))
    archive = sched.archive
    if archive and archive.when and archive.show_on_site:
        details = (archive.details or "").replace("{date}", archive.when.isoformat())
        out.append(
            _row(
                "semester-archived",
                "special_event",
                archive.when,
                archive.title,
                all_day=True,
                details=details,
                tbc=archive.tbc,
            )
        )
    return out


def _brief(readme: str) -> tuple[str, str]:
    """(heading, body) of a template README: the `# ` heading names it, the rest is the
    brief."""
    lines = readme.splitlines()
    heading = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    body = "\n".join(ln for ln in lines if not ln.startswith("# ")).strip()
    return heading, body


def render_assignments(
    facts: SemesterFacts, extra: StudentFacts, now: datetime
) -> list[dict]:
    """One per assignment the site may show. What the plan publishes (the dates, the
    shape, the rules) is always there; the brief, the name its README gives it and the
    shape note wait for the hand-out, as the site's did."""
    out = []
    for key, entry in _public_assignments(facts.sched):
        spec = facts.specs.get(key, grades.GradingSpec())
        name = schedule.semester_name(key, entry)
        title = identifier(name)
        pinned = entry.handout_datetime is not None and entry.handout_datetime <= now
        out_now = name in extra.handed_out or pinned
        heading, brief = _brief(extra.readmes.get(key, "")) if out_now else ("", "")
        window, shuts = schedule.formation_state(facts.sched, key, now)
        forming = (
            window == "open"
            and spec.team_formation_resolved == SELF_SELECT
            and shuts is not None
        )
        cap = extra.caps.get(key)
        rooms = (
            [
                {"name": team, "members": len(members), "cap": cap}
                for team, members in sorted(teams.teams_for(facts.teams, key).items())
            ]
            if forming
            else []
        )
        out.append(
            {
                "slug": name,
                "title": title,
                "subtitle": row_name(spec.title or heading, title),
                "handout_datetime": _iso(entry.handout_datetime),
                "due_datetime": _iso(entry.due_datetime),
                "grading_cutoff_datetime": _iso(
                    schedule.grading_cutoff_datetime(facts.sched, key)
                ),
                "late_rule": late_rule(spec.late_window_days, spec.late_penalty_per_day)
                if spec.collects_commits
                else "",
                "cutoff_sentence": CUTOFF_SENTENCE if spec.collects_commits else "",
                "submit_via": spec.submit_via,
                "shape": spec.submit_shape,
                "shape_note": shape_note(spec.submit_shape) if out_now else "",
                "private_repo": spec.submit_shape == "assignment-repo-private",
                "submit_url": spec.submit_url
                if spec.submit_external and out_now
                else "",
                "group": spec.is_group,
                "team_formation": {
                    "closes_datetime": _iso(
                        schedule.in_semester_zone(facts.sched, shuts)
                    ),
                    "max_team_size": cap,
                }
                if forming
                else None,
                "teams": rooms,
                "solution_datetime": _iso(entry.solution_datetime),
                "max_points": _number(grades.total_points(spec)),
                "handed_out": out_now,
                "brief": brief,
                "tbc": entry.tbc,
            }
        )
    return out


def _number(text: str) -> int | float | None:
    """`grades.total_points`'s "10" / "7.5" as a number; None when there is no total."""
    if not text:
        return None
    return int(text) if text.lstrip("-").isdigit() else float(text)


def _picture(src: str, org: str) -> str:
    """A card picture as an absolute URL: a site-relative one resolves against the
    semester's site; anything else that is not https is dropped."""
    if src.startswith("https://"):
        return src
    return f"https://{pages_repo(org).lower()}{src}" if src.startswith("/") else ""


def render_instructors(
    org: str, cards: tuple[list[dict], list[dict]] | None
) -> list[dict]:
    """The cards the site showed (`site_repo.people_cards`: active today, with a name, the
    email only where `show_email: true`), instructors first."""
    if cards is None:
        return []
    out = []
    for role, people in zip(("instructor", "teaching_assistant"), cards, strict=True):
        for c in people:
            out.append(
                {
                    "name": c.get("name", ""),
                    "title": c.get("title", ""),
                    "webpage": c.get("webpage", ""),
                    "picture": _picture(c.get("profile_pic", ""), org),
                    "role": role,
                    "email": c.get("email", ""),
                }
            )
    return out


_IF = re.compile(r"\{%-?\s*if\s+site\.(\w+)\s*-?%\}([\s\S]*?)\{%-?\s*endif\s*-?%\}")
_VAR = re.compile(r"\{\{-?\s*site\.(\w+)\s*-?\}\}")
_LIQUID = re.compile(r"\{%[\s\S]*?%\}|\{\{[\s\S]*?\}\}")
_FRONT = re.compile(r"\A---\r?\n[\s\S]*?\r?\n---\r?\n?")


def home_markdown(text: str, config: dict[str, str]) -> str:
    """The site's home page (`index.md`, the instructors' own words) as plain markdown:
    front matter dropped, `{{ site.x }}` filled from the course's identity keys, an
    `{% if site.x %}` block kept only when x is set, any other Liquid dropped."""
    body = _FRONT.sub("", text or "")
    body = _IF.sub(lambda m: m.group(2) if config.get(m.group(1)) else "", body)
    body = _VAR.sub(lambda m: config.get(m.group(1), ""), body)
    body = _LIQUID.sub("", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def site_config(course: CourseFacts, org: str) -> dict[str, str]:
    """The `site.*` keys a home page may name, as the site sync writes them."""
    meta = course.meta or {}
    config = {
        k: str(meta[k])
        for k in ("course_name", "course_code", "course_description")
        if meta.get(k)
    }
    if semester_label(semester_of(org)):
        config["course_semester"] = semester_label(semester_of(org))
    config["github_org"] = org
    return config


def render(
    course: CourseFacts, facts: SemesterFacts, extra: StudentFacts, now: datetime
) -> dict:
    """The document. Pure: everything it names was read by `gather` or is in `facts`."""
    sched = facts.sched
    rows = release_rows(facts, extra) + event_rows(facts)
    rows.sort(key=lambda r: (r["when"] is None, str(r["when"] or ""), r["id"]))
    assignments = render_assignments(facts, extra, now)
    late = list(dict.fromkeys(a["late_rule"] for a in assignments if a["late_rule"]))
    tag = semester_of(facts.org)
    return {
        "schema": SCHEMA,
        "semester": facts.org,
        "course_name": str((course.meta or {}).get("course_name") or ""),
        "term_label": semester_label(tag) or "",
        "timezone": sched.timezone,
        # Always, whatever `show_on_site` says: a student loses write access either way.
        "archive_datetime": _iso(sched.archive.when if sched.archive else None),
        "home_markdown": home_markdown(extra.home, site_config(course, facts.org)),
        "syllabus": {"repo": extra.syllabus[0], "path": extra.syllabus[1]}
        if extra.syllabus
        else None,
        "kinds": {
            k["key"]: {
                "label": k["label"],
                "colour": k["colour"],
                "background": k["background"],
            }
            for k in policy.kinds()
        },
        "rows": rows,
        "assignments": assignments,
        "instructors": render_instructors(facts.org, extra.cards),
        "late_policy": late,
        "materials_repos": sorted(facts.dest_paths),
        "materials_index": [
            {
                "repo": repo,
                "paths": sorted(p for p in facts.dest_paths[repo] if _material(p)),
            }
            for repo in sorted(facts.dest_paths)
        ],
        "announcements": extra.announcements,
    }


# ---------------------------------------------------------------- gh wiring


def _front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"\A---\r?\n([\s\S]*?)\r?\n---\r?\n?", text or "")
    if not m:
        return {}, text or ""
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        fm = None
    return (fm if isinstance(fm, dict) else {}), text[m.end() :]


def _announcements(org: str, site: str) -> list[dict]:
    """The site repo's hand-written `_announcements/`, newest first. Instructor-owned
    files: one that does not parse is skipped, not fatal."""
    tree = repo_tree(org, site, default_branch(org, site, fallback="main"), "blob")
    out = []
    for path in sorted(
        p for p in tree if p.startswith(f"{ANNOUNCEMENTS_DIR}/") and p.endswith(".md")
    ):
        fm, body = _front_matter(get_file_content(org, site, path) or "")
        when = fm.get("date")
        if not when:
            continue
        out.append(
            {
                "when": when.isoformat()
                if isinstance(when, (date, datetime))
                else str(when),
                "title": str(fm.get("title") or ""),
                "details": str(fm.get("details") or "").strip() or body.strip(),
            }
        )
    out.sort(key=lambda a: a["when"], reverse=True)
    return out


def gather(course: CourseFacts, facts: SemesterFacts, now: datetime) -> StudentFacts:
    """Read what the student file needs beyond `facts`. A read that fails raises, as
    `status_json.gather_semester` does."""
    org, sched = facts.org, facts.sched
    extra = StudentFacts(
        handed_out=handed_out_assignments(list(facts.listing.values()))
    )
    for key, entry in _public_assignments(sched):
        spec = facts.specs.get(key, grades.GradingSpec())
        name = schedule.semester_name(key, entry)
        pinned = entry.handout_datetime is not None and entry.handout_datetime <= now
        if name in extra.handed_out or pinned:
            extra.readmes[key] = (
                get_file_content(course.org, entry.course_source_repo, "README.md")
                or ""
            )
        if spec.is_group and spec.team_formation_resolved == SELF_SELECT:
            extra.caps[key] = grades.team_cap(course.org, spec, org, key)
    # The reading lists: the prose overlays of every readings copy that has landed.
    for sr in site_rows(planned_rows(sched, lambda repo: facts.aliases.get(repo, {}))):
        own = [sr.row] if sr.row.kind == "readings" else []
        for row in [*own, *sr.readings]:
            for repo, path in _landed(facts, row, True)[1]:
                extra.overlays[(repo, path)] = get_file_content(org, repo, path) or ""
    try:
        extra.syllabus = declared_syllabus(
            sched,
            frozenset(facts.dest_paths),
            lambda repo: facts.dest_paths.get(repo, set()),
            lambda repo: read_materials(course.org, repo),
        )
    except Unusable:
        extra.syllabus = None  # the site sync names the bad materials.yml
    extra.cards = people_cards(
        yaml_file(org, CONFIG_REPO, INSTRUCTORS_FILE), semester=True
    )
    site = pages_repo(org)
    if site in facts.listing:
        extra.home = facts.site_home or ""
        extra.announcements = _announcements(org, site)
    return extra


def dumps(doc: dict) -> bytes:
    """Stable bytes, so an unchanged semester makes no commit."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
