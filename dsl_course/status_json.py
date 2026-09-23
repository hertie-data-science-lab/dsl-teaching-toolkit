"""dsl-course status.json -- one machine-readable account of a course and a cohort.

`status.py`'s checklist answers "which files have I filled in?" for a person reading a run
summary. This answers the lifecycle's question for a program: which stage each scope is
at, what is wrong and where to fix it, what happens this week, and what state every
release and assignment is in. The shape is `dsl.status/1` (build contracts, section 3);
the stages, predicates and states are `design/lifecycle.md`'s, and every sentence in a
`text` or `stops` is in `design/vocabulary.md`'s words.

TWO HALVES, so the model can be tested without a single `gh` call:

- `gather_course` / `gather_cohort` read live GitHub state into `CourseFacts` /
  `CohortFacts`, through the loaders the rest of the toolkit already uses (the digest
  parsers, `schedule.load`, `roster.load`, the grading-spec reader) - nothing is
  re-derived here that a loader already answers.
- `render_course` / `render_cohort` turn facts into the document. Pure.

`collect_course` / `collect_cohort` are the two together. The writer is `status.write`,
which puts the rendered file where the console reads it.

NO TIMESTAMP of its own. The file carries `inputs` - the blob shas of the files it was
computed from - so a reader tells a stale status from a current one with one tree read,
and a render that matches the file byte for byte makes no commit. The moments inside it
(a release's `when`, the site's last update) are facts about the cohort, not about when
this was written.
Nothing that moves on every scheduler tick is in it - the automation heartbeat
included, which the console reads off the workflow's run list - or every cohort's
classroom-config would take a commit every quarter hour.

PUBLIC AND PRIVATE. The cohort file lives in the private `classroom-config`; the course
file lives in the course org's PUBLIC `.github`, so `render_course` puts nothing in it but
repo names, counts and the course-side problems that already stand in that repo's own
digest issue - never a handle, an email or a student repo name.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import yaml

from . import grades, roster, schedule, sync_faculty, team_formation, teams
from .course import (
    COURSE_ADMIN_TEAM,
    COURSE_CONFIG,
    INSTRUCTORS_TEAM,
    MATERIALS_REPO_PREFIX,
    PUBLISH_FILE,
    SOLUTION_BRANCH,
    active_today,
    assignment_slug,
    is_repo_root,
    pages_repo,
    term_label,
    term_tag,
)
from .discovery import (
    COHORTS_PATH,
    assignment_rows,
    list_org_repos,
    org_meta,
    read_cohort_registry,
)
from .faults import ConfigFault, FaultKind, Unusable
from .gh_contents import (
    get_file_content,
    is_untouched_stub,
    repo_path_shas,
    repo_tree,
)
from .gh_teams import get_team_members
from .ghcli import gh
from .ops.outcome import OUTCOMES_DIR
from .ops.registry import STATUS_SCHEMA
from .repos import default_branch
from .schedule_plan import deploy_dest, deploy_section, row_kind
from .sync_teams import known_handles

# Where each file lives, inside `classroom-config` (cohort) or `.github` (course).
STATUS_PATH = ".dsl/status.json"
# How many recent operations the cohort file lists.
RECENT_OPERATIONS = 10

SYLLABUS_FILE = "SYLLABUS.md"
README_FILE = "README.md"
SITE_HOME = "index.md"

# Stage identifiers, in lifecycle order.
COURSE_STAGES = ("C1", "C2", "C3", "C4", "C5", "C6")
COHORT_STAGES = ("K1", "K2", "K3", "K4", "K5", "K6", "K7")
# A stage that is not done while one of these is not done either is `blocked`, not
# `todo`: there is nothing the instructor can do about it yet.
PREREQUISITES = {
    "C2": ("C1",),
    "C3": ("C2",),
    "C4": ("C2",),
    "C5": ("C2",),
    "C6": ("C4",),
    "K2": ("K1",),
    "K3": ("K2",),
    "K4": ("K2",),
    "K5": ("K2",),
    "K6": ("K2",),
}
DONE, TODO, BLOCKED, PROBLEM = "done", "todo", "blocked", "problem"
# What a blocked stage is waiting for, named by the prerequisite it waits on.
WAITING_FOR = {
    "C1": "the course org",
    "C2": "the course to be set up",
    "C4": "the course's materials to be ready",
    "K1": "the cohort org",
    "K2": "the cohort to be set up",
}


# ---------------------------------------------------------------------------- facts


@dataclass
class TemplateFacts:
    """One assignment template in the course org, as far as C5 asks about it."""

    repo: str
    readme: str | None = None
    faults: list[ConfigFault] = field(default_factory=list)


@dataclass
class MaterialsFacts:
    """One `course-materials-*` repo, as far as C4 asks about it."""

    repo: str
    syllabus: str | None = None
    has_publish: bool = False


@dataclass
class CourseFacts:
    """Everything the course half of the document is computed from."""

    org: str
    meta: dict = field(default_factory=dict)
    # `.github`'s tree, `{path: sha}`; None when the org could not be read at all.
    github_paths: dict[str, str] | None = None
    registry: list[str] = field(default_factory=list)
    # What is wrong with dsl-course.yml and the cohort registry - the COURSE digest's list.
    faults: list[ConfigFault] = field(default_factory=list)
    materials: list[MaterialsFacts] = field(default_factory=list)
    templates: list[TemplateFacts] = field(default_factory=list)
    public_site: bool = False


@dataclass
class CohortFacts:
    """Everything the cohort half of the document is computed from. Built by
    `gather_cohort`; a test builds one by hand."""

    org: str
    listing: dict[str, dict] = field(default_factory=dict)
    # classroom-config's tree, `{path: sha}` - the cohort half of `inputs`.
    config_paths: dict[str, str] = field(default_factory=dict)
    sched: schedule.Schedule = field(default_factory=schedule.Schedule)
    # schedule.yml's whole digest list: sources not found, entries the parser dropped,
    # team-formation windows somebody is still waiting on.
    schedule_faults: list[ConfigFault] = field(default_factory=list)
    people: dict[str, list[dict]] | None = None
    people_faults: list[ConfigFault] = field(default_factory=list)
    students: list[roster.Student] | None = None
    roster_faults: list[ConfigFault] = field(default_factory=list)
    teams: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    teams_faults: list[ConfigFault] = field(default_factory=list)
    sheet_faults: list[ConfigFault] = field(default_factory=list)
    # The cohort's view of the course-side templates it cites: every value in their
    # grading_config.yml that will not grade as written. Rolled up as COURSE problems.
    template_faults: list[ConfigFault] = field(default_factory=list)
    specs: dict[str, grades.GradingSpec] = field(
        default_factory=dict
    )  # by schedule key
    sheets: dict[str, dict] = field(default_factory=dict)  # by cohort-side name
    # `{handle, casefolded: when its gradebook was last written}`, off distributed.csv.
    # A gradebook holds every assignment, so its record names none.
    returned_at: dict[str, datetime] = field(default_factory=dict)
    # `{cohort-side name: when its grading sheet last changed}`; None = not known.
    sheet_changed: dict[str, datetime | None] = field(default_factory=dict)
    dest_paths: dict[str, set[str]] = field(default_factory=dict)  # release dest trees
    site_home: str | None = None
    site_last_update: datetime | None = None
    config_last_update: datetime | None = None
    # Whether the instructors team holds exactly who people.yml grants. None = unread.
    staff_synced: bool | None = None
    outcomes: list[dict] = field(default_factory=list)  # parsed dsl.outcome/1 files

    @property
    def archived(self) -> bool:
        """The finished marker (`discovery.cohort_is_live`): an archived classroom-config."""
        row = self.listing.get(schedule.CONFIG_REPO) or {}
        return bool(row.get("archived"))


# ---------------------------------------------------------------------------- pure core


def _sentence(text: str, capitalise: bool = True) -> str:
    """A fault's lower-case, unpunctuated clause as a sentence the console shows verbatim.
    `capitalise=False` for one that opens on a file or entry name, which keeps its case."""
    text = text.strip()
    if not text:
        return ""
    if capitalise:
        text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _day(when: datetime | date) -> str:
    """`Thu 8 Oct` - how a sentence names a day. No zone: every date in a cohort's
    sentences is in the cohort's own zone, which the file states once (`timezone`)."""
    return f"{when:%a} {when.day} {when:%b}"


def _iso(when: datetime | date | None) -> str | None:
    return when.isoformat() if when is not None else None


def _entry_of(where: str) -> str:
    """`releases.s5` -> `s5`, `assignments.a2` -> `a2`; anything else as it stands."""
    for prefix in ("releases.", "assignments."):
        if where.startswith(prefix):
            return where[len(prefix) :]
    return where


def _slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "file"


# What a file's problems cost, in the vocabulary's words - the console's `stops` line. The
# engine's own CONSEQUENCE sentences say "grading" and "enrolled"; the console does not.
STOPS = {
    schedule.SCHEDULE_PATH: (
        "That entry is not scheduled: nothing is released, handed out or marked from it."
    ),
    sync_faculty.COHORT_PEOPLE_PATH: (
        "This person has no access and is not told about problems."
    ),
    roster.ROSTER_PATH: (
        "The whole roster is skipped: nobody new can join or is sent a code."
    ),
    teams.TEAMS_PATH: (
        "That row is ignored: the team is not created or the member not added."
    ),
    grades.SHEETS_DIR: "That sheet is not updated, and none of its marks are returned.",
    grades.GRADING_FILE: "Marking uses the toolkit's default for that value.",
    COURSE_CONFIG: (
        "Automation skips this course: staff access and cohorts are not updated."
    ),
    COHORTS_PATH: (
        "Automation skips this course: staff access and cohorts are not updated."
    ),
}


class _Where:
    """How one hand-edited file's problems are filed: id prefix, scope, stage, screen,
    and the code a non-source fault in it carries."""

    def __init__(
        self, kind: str, scope: str, stage: str | None, screen: str, code: str
    ):
        self.kind, self.scope, self.stage, self.screen, self.code = (
            kind,
            scope,
            stage,
            screen,
            code,
        )


_FILES = {
    schedule.SCHEDULE_PATH: _Where("schedule", "cohort", "K4", "schedule", "SCHEDULE"),
    sync_faculty.COHORT_PEOPLE_PATH: _Where(
        "people", "cohort", "K3", "staff", "PEOPLE"
    ),
    roster.ROSTER_PATH: _Where("roster", "cohort", "K5", "roster", "ROSTER"),
    teams.TEAMS_PATH: _Where("teams", "cohort", "K5", "teams", "TEAMS"),
    grades.GRADING_FILE: _Where(
        "template", "course", "C5", "template", "GRADING_CONFIG"
    ),
    grades.LEGACY_GRADING_FILE: _Where(
        "template", "course", "C5", "template", "GRADING_CONFIG"
    ),
    COURSE_CONFIG: _Where("course", "course", "C3", "course", "COURSE"),
    COHORTS_PATH: _Where("registry", "course", "C2", "course", "COURSE"),
}
# A grading sheet belongs to the running phase, not to a setup stage: its stage is the
# marking phase, and the assignment is the entry in its id.
MARKING = "marking"
_SHEET = _Where("sheet", "cohort", MARKING, "marks", "GRADING_SHEETS")
# Any other classroom-config file: the cohort's own setup.
_OTHER = _Where("config", "cohort", "K2", "", "CONFIG")
# A template fault only one cohort pays for (`ConfigFault.per_cohort`): the repos it
# handed out, or its schedule entry. The cohort's, in the phase after hand out.
HANDED_OUT = "open"
_COHORT_TEMPLATE = _Where(
    "template", "cohort", HANDED_OUT, "template", "GRADING_CONFIG"
)
# The cohort org's own member privileges: part of setting the cohort up, fixed on a
# GitHub settings page rather than in a file.
_ORG = _Where("org", "cohort", "K2", "", "ORG_SETTINGS")

_SOURCE_CODES = {
    FaultKind.MISSING_PATH: "SOURCE_MISSING",
    FaultKind.MISSING_REPO: "REPO_MISSING",
    FaultKind.WITHHELD: "SOURCE_WITHHELD",
}


# The vocabulary pass (design/vocabulary.md) over an engine sentence that has no `plain`
# of its own: the engine's words on the left, the console's on the right. Whole words
# only, so `grading_config.yml` and `grading_sheets/` - file names the fix edits - stay.
_WORDS = (
    ("submission units", "students or teams"),
    ("submission unit", "student or team"),
    ("an onboarded", "a joined"),
    ("onboarded", "joined"),
    ("enrolment codes", "codes"),
    ("enrolment code", "code"),
    ("enrolled", "joined"),
    ("grading", "marking"),
    ("graded", "marked"),
    ("grades", "marks"),
    ("grade", "mark"),
    ("dry run", "preview"),
)
_ROLES = {
    "instructors": "Instructor",
    "teaching_assistants": "Teaching assistant",
    "course_admins": "Course admin",
}
_PERSON = re.compile(r"^people\.(\w+)\[(\d+)\]$")
_ROW = re.compile(r"^row (\d+)$")


def plain_words(text: str) -> str:
    """`text` without markdown and in the vocabulary's words."""
    out = re.sub(r"`([^`]+?):`", r"\1", text)  # `email:` names the key, `email`
    out = out.replace("`", "").replace("**", "")
    for engine, console in _WORDS:
        out = re.sub(rf"(?<![\w-]){re.escape(engine)}(?![\w-])", console, out)
        out = re.sub(
            rf"(?<![\w-]){re.escape(engine.capitalize())}(?![\w-])",
            console.capitalize(),
            out,
        )
    return re.sub(r"\s+", " ", out).strip()


def _first_sentence(text: str) -> str:
    return re.split(r"(?<=[a-z0-9)])\. (?=[A-Z])", text, maxsplit=1)[0].rstrip(".")


def _clause(what: str) -> str:
    """The first clause of an engine sentence: what is wrong. What it costs follows a
    dash (`_cost`), and a second sentence is detail."""
    return plain_words(_first_sentence(what.split(" - ", 1)[0]))


def _cost(what: str) -> str:
    """What an engine sentence says the fault costs - the part after its dash - as a
    sentence, or "" when it says nothing about that."""
    if " - " not in what:
        return ""
    return _sentence(plain_words(_first_sentence(what.split(" - ", 1)[1])))


def _subject(fault: ConfigFault, filed: _Where, entry: str) -> str:
    """What a derived sentence is about, named the way the console names it."""
    person = _PERSON.match(fault.where)
    if person:
        role = _ROLES.get(person[1], "Entry")
        where = "course details" if filed.kind == "course" else "people.yml"
        return f"{role} {int(person[2]) + 1} in {where}"
    row = _ROW.match(fault.where)
    if row and filed.kind in ("roster", "teams"):
        return f"{'Roster' if filed.kind == 'roster' else 'Teams'} row {row[1]}"
    if filed.kind == "schedule":
        return f"Schedule entry {entry}" if entry != fault.where else "The schedule"
    if filed.kind == "sheet":
        sheet = f"{entry} marking sheet"
        return f"Line {fault.lineno} of the {sheet}" if fault.lineno else f"The {sheet}"
    if filed.kind == "template":
        return f"The {entry} template's settings"
    return {
        "people": "people.yml",
        "roster": "The roster",
        "teams": "The teams file",
        "course": "Course details",
        "registry": "The course's list of cohorts",
    }.get(filed.kind, fault.file)


def plain_text(fault: ConfigFault, filed: _Where, entry: str) -> str:
    """One plain sentence for a problem: the fault's own `plain` when its parser wrote
    one, else its subject and the first clause of what is wrong."""
    if fault.plain:
        return fault.plain
    clause = _clause(fault.what)
    subject = _subject(fault, filed, entry)
    return f"{subject}: {clause}." if clause else f"{subject} has a problem."


def _where_filed(fault: ConfigFault) -> _Where:
    if fault.where == grades.ORG_SETTINGS:
        return _ORG
    if fault.per_cohort:
        return _COHORT_TEMPLATE
    if fault.file.startswith(f"{grades.SHEETS_DIR}/"):
        return _SHEET
    return _FILES.get(fault.file, _OTHER)


def _source_sentences(fault: ConfigFault, now: datetime) -> tuple[str, str]:
    """`(text, stops)` for a source the plan cites that automation cannot release."""
    entry = _entry_of(fault.where)
    subject = entry if fault.is_assignment else f"Release {entry}"
    if fault.kind is FaultKind.WITHHELD:
        text = (
            f"{subject} cites {fault.path} in {fault.repo}, which .releaseignore holds "
            f"back."
        )
    elif fault.kind is FaultKind.MISSING_REPO:
        noun = "template" if fault.is_assignment else "repo"
        text = f"{subject} cites {noun} {fault.repo}, which is not found in the course."
    else:
        text = (
            f"{subject} cites folder {fault.path}, which is not found in {fault.repo}."
        )
    moment = "hand out" if fault.is_assignment else "release"
    if fault.fires is None:
        stops = (
            f"The {moment} has no date yet, and will be skipped until this is fixed."
        )
    elif fault.fires <= now:
        stops = f"The {moment} on {_day(fault.fires)} was skipped."
    else:
        stops = f"The {moment} on {_day(fault.fires)} will be skipped."
    if fault.kind is FaultKind.WITHHELD:
        stops = stops.replace("will be skipped", "will leave these files out").replace(
            "was skipped", "left these files out"
        )
    return text, stops


def problem_from_fault(fault: ConfigFault, org: str, now: datetime) -> dict:
    """One `problems[]` entry for one fault. `org` is where the fault's file is when the
    fault does not say otherwise (the cohort, for everything in classroom-config).

    The fix pointer names the repo, path and line to edit and the console screen that
    edits it; a file on a branch other than `main` (a template's `solution`) says which."""
    filed = _where_filed(fault)
    if fault.is_source:
        code = _SOURCE_CODES[fault.kind]
        text, stops = _source_sentences(fault, now)
        entry = _entry_of(fault.where)
    else:
        code = filed.code
        if filed.kind == "template":
            entry = assignment_slug(fault.in_repo)
        elif filed is _SHEET:
            entry = fault.file.removeprefix(f"{grades.SHEETS_DIR}/").removesuffix(
                ".yml"
            )
        else:
            entry = _entry_of(fault.where)
        text = plain_text(fault, filed, entry)
        stops = (
            _sentence(plain_words(fault.consequence))
            if fault.consequence
            else _cost(fault.what)
            or STOPS.get(fault.file)
            or STOPS.get(grades.SHEETS_DIR if filed is _SHEET else "", "")
            or STOPS.get(grades.GRADING_FILE if filed.kind == "template" else "", "")
        )
    fix = {
        "repo": f"{fault.in_org or org}/{fault.in_repo}",
        "path": fault.file,
        "line": fault.lineno,
        "screen": filed.screen or None,
        "entry": entry if filed.kind in ("schedule", "template", "sheet") else None,
    }
    if filed is _ORG:
        # A settings page, not a file: `url` is where the console sends the fix.
        owner = fault.in_org or org
        fix = {
            "repo": f"{owner}/{fault.in_repo}",
            "path": "",
            "line": None,
            "screen": None,
            "entry": None,
            "url": grades.MEMBER_PRIVILEGES_URL.format(org=owner),
        }
    elif fault.ref and fault.ref != "main":
        fix["ref"] = fault.ref
    return {
        "id": f"{filed.kind}:{_slugify(entry)}:{code}",
        "scope": filed.scope,
        "stage": filed.stage,
        "text": text,
        "stops": stops,
        "fix": fix,
    }


def _unique_ids(problems: list[dict]) -> list[dict]:
    """Two faults on one entry with one code (two deploys under `s5`, both not found) are
    two problems: the second and later take a `:2`, `:3` suffix, in the order given."""
    seen: Counter[str] = Counter()
    for p in problems:
        seen[p["id"]] += 1
        if seen[p["id"]] > 1:
            p["id"] = f"{p['id']}:{seen[p['id']]}"
    return problems


def stage_why(
    states: dict[str, str], todo: dict[str, str | None], problems: list[dict]
) -> dict[str, str]:
    """One sentence per stage that is not done, saying why: the problems standing
    against it, the prerequisite it waits for, or what its own predicate still lacks
    (`todo`, the sentence each predicate returns when it is not met)."""
    out: dict[str, str] = {}
    for stage, state in states.items():
        if state == PROBLEM:
            n = sum(p["stage"] == stage for p in problems)
            out[stage] = f"{n} problem{' needs' if n == 1 else 's need'} fixing."
        elif state == BLOCKED:
            pre = next(p for p in PREREQUISITES[stage] if states.get(p) != DONE)
            out[stage] = f"Waiting for {WAITING_FOR.get(pre, pre)}."
        elif state == TODO:
            out[stage] = todo.get(stage) or "Not done yet."
    return out


def _stage_states(
    stages: tuple[str, ...], done: dict[str, bool], problems: list[dict]
) -> dict[str, str]:
    """`done | todo | blocked | problem` per stage. A problem targeting a stage wins over
    everything; otherwise a stage whose prerequisite is not done is blocked."""
    flagged = {p["stage"] for p in problems}
    out: dict[str, str] = {}
    for stage in stages:
        if stage in flagged:
            out[stage] = PROBLEM
        elif done.get(stage):
            out[stage] = DONE
        elif any(not done.get(pre) for pre in PREREQUISITES.get(stage, ())):
            out[stage] = BLOCKED
        else:
            out[stage] = TODO
    return out


def _written(text: str | None) -> bool:
    """A seeded file somebody has written: present, and no longer our stub."""
    return bool(text and text.strip()) and not is_untouched_stub(text or "")


def materials_state(m: MaterialsFacts) -> str:
    """C4, per repo: `ready` once SYLLABUS.md is written and publish.yml is there."""
    return "ready" if _written(m.syllabus) and m.has_publish else TODO


def template_state(t: TemplateFacts) -> str:
    """C5, per template: `problem` while its grading_config.yml will not grade as written,
    `ready` once its README is written, `todo` before that."""
    if t.faults:
        return PROBLEM
    return "ready" if _written(t.readme) else TODO


def app_installed() -> bool | None:
    """C1/K1's "App installed" predicate. TODO(decision 0002): there is no App yet, so
    nothing can be asked; None is "not known", and no stage waits on it."""
    return None


def course_admin_count(meta: dict) -> int:
    faculty = sync_faculty.parse_faculty_from_meta(meta) if meta else {}
    today = date.today().isoformat()
    return len(
        sync_faculty.desired_team_members(faculty, today).get(COURSE_ADMIN_TEAM, set())
    )


def render_course(
    facts: CourseFacts, now: datetime, rolled_up: list[dict] = ()
) -> tuple[dict, list[dict]]:
    """`(the course block, the course-side problems)`. `rolled_up` is the course-scope
    problems a cohort has already built from its own view of the templates it cites, so the
    course block inside a cohort's file marks the same stages its problem list does.

    Stage predicates (lifecycle, course stages):
    - C1 the org resolves (`app_installed` is a stub until decision 0002);
    - C2 `.github` holds dsl-course.yml and its seeded workflows;
    - C3 dsl-course.yml names the course, its code and description, and one course admin;
    - C4 at least one materials repo, every one of them `ready`;
    - C5 at least one template, every one of them `ready`;
    - C6 the public website repo exists.
    `ready` is C1-C5 done: nothing on the course side would stop a cohort."""
    problems = [problem_from_fault(f, facts.org, now) for f in facts.faults]
    for t in facts.templates:
        problems += [problem_from_fault(f, facts.org, now) for f in t.faults]
    meta = facts.meta
    todo = course_checks(facts)
    done = {stage: todo[stage] is None for stage in COURSE_STAGES}
    standing = [*problems, *rolled_up]
    stages = _stage_states(COURSE_STAGES, done, standing)
    block = {
        "org": facts.org,
        "name": str(meta.get("course_name") or meta.get("org_name") or ""),
        "code": str(meta.get("course_code") or ""),
        "app_installed": app_installed(),
        "stages": stages,
        "stage_why": stage_why(stages, todo, standing),
        "ready": all(stages[s] == DONE for s in COURSE_STAGES[:5]),
        "materials": [
            {"repo": m.repo, "state": materials_state(m)} for m in facts.materials
        ],
        "templates": [
            {
                "repo": t.repo,
                "slug": assignment_slug(t.repo),
                "state": template_state(t),
            }
            for t in facts.templates
        ],
        "cohorts": list(facts.registry),
    }
    return block, problems


def _materials_why(m: MaterialsFacts) -> str:
    if not _written(m.syllabus):
        return f"{m.repo}'s SYLLABUS.md is still the placeholder"
    return f"{m.repo} has no {PUBLISH_FILE} yet"


def _first_of(what: str, reasons: list[str]) -> str:
    """One sentence about the first of several things that are not ready."""
    if len(reasons) == 1:
        return f"{reasons[0]}."
    return f"{len(reasons)} {what} are not ready yet; the first: {reasons[0]}."


def course_checks(facts: CourseFacts) -> dict[str, str | None]:
    """Each course stage's predicate: None when it is met, else the one sentence that
    says what is still missing (lifecycle, course stages)."""
    paths = facts.github_paths or {}
    meta = facts.meta
    out: dict[str, str | None] = dict.fromkeys(COURSE_STAGES)
    if facts.github_paths is None:
        out["C1"] = "The course org could not be read."
    if COURSE_CONFIG not in paths:
        out["C2"] = f"The course's .github has no {COURSE_CONFIG} yet."
    elif not any(p.startswith(".github/workflows/") for p in paths):
        out["C2"] = "The course's .github has no workflows yet."
    if not all(meta.get(k) for k in ("course_name", "course_code")):
        out["C3"] = "Course details have no course name or code yet."
    elif not meta.get("course_description"):
        out["C3"] = "Course details have no description yet."
    elif course_admin_count(meta) == 0:
        out["C3"] = "No course admin is declared in course details yet."
    if not facts.materials:
        out["C4"] = "There is no materials repo yet."
    else:
        pending = [
            _materials_why(m) for m in facts.materials if materials_state(m) != "ready"
        ]
        if pending:
            out["C4"] = _first_of("materials repos", pending)
    if not facts.templates:
        out["C5"] = "There is no assignment template yet."
    else:
        pending = [
            f"{t.repo}'s README.md is still the placeholder"
            for t in facts.templates
            if template_state(t) == TODO
        ]
        if pending:
            out["C5"] = _first_of("assignment templates", pending)
        elif any(template_state(t) != "ready" for t in facts.templates):
            out["C5"] = "An assignment template has settings that need fixing."
    if not facts.public_site:
        out["C6"] = "There is no public website; it is optional."
    return out


def term_weeks(
    start: date | None, end: date | None, today: date
) -> tuple[int | None, int | None]:
    """`(week, weeks)` of the term: week 1 starts on `semester_start`, 0 is before it, and
    the count stops at the last week. `(None, None)` while either date is unset."""
    if start is None or end is None or end < start:
        return None, None
    weeks = (end - start).days // 7 + 1
    if today < start:
        return 0, weeks
    return min((today - start).days // 7 + 1, weeks), weeks


def _dest_present(facts: CohortFacts, d: schedule.Deploy) -> bool:
    paths = facts.dest_paths.get(d.cohort_dest_repo) or set()
    dest = deploy_dest(d)
    return bool(paths) if is_repo_root(dest) else dest in paths


def release_state(
    release: schedule.Release,
    facts: CohortFacts,
    faults: list[ConfigFault],
    now: datetime,
) -> str:
    """`planned | will_be_skipped | released | late` (lifecycle, per scheduled release).

    released - every copy is on the destination's default branch (whenever it got there:
    an early release is released); late - a copy is due and not there; will_be_skipped -
    automation cannot perform it as written (a source not found or held back); planned
    otherwise. An entry with nothing to copy is released once its moment has passed."""
    if release.deploy and all(_dest_present(facts, d) for d in release.deploy):
        return "released"
    if release.due_deploys(now):
        return "late"
    if faults:
        return "will_be_skipped"
    if not release.deploy and release.when is not None and release.when <= now:
        return "released"
    return "planned"


def _release_type(release: schedule.Release) -> str | None:
    if release.type:
        return release.type
    if release.deploy:
        return row_kind(deploy_section(release.deploy[0]))
    return None


def sheet_counts(
    sheet: dict | None, spec: grades.SheetSpec | None
) -> tuple[int, int, int | None]:
    """`(marked units, units on the sheet, units with a submission recorded)` off one
    parsed grading sheet. Submissions are None where the assignment collects no commits."""
    if not sheet or spec is None:
        return 0, 0, None
    container = sheet.get(spec.container_key)
    if not isinstance(container, dict):
        return 0, 0, None
    blocks = [b for b in container.values() if isinstance(b, dict)]
    filled = sum(not grades.is_blank(b.get(spec.score_key)) for b in blocks)
    if not spec.collects_commits:
        return filled, len(blocks), None
    submitted = sum(
        not grades.is_blank((b.get(grades.INFO_KEY) or {}).get("submitted"))
        for b in blocks
    )
    return filled, len(blocks), submitted


def assignment_state(
    now: datetime,
    entry: schedule.AssignmentEntry,
    cutoff: datetime | None,
    spec: grades.GradingSpec,
    units: int,
    returned: bool,
) -> str:
    """`declared | teams_forming | blocked | open | late_window | marking | returned`
    (lifecycle, per assignment), by the assignment's own clock:

    returned once marks have gone back for every unit; marking from the cutoff;
    late_window between the due date and the cutoff; before the due date, open once any
    copy is handed out. A group assignment past its hand-out moment with no copy yet is
    teams_forming while students choose their own teams, blocked while the teaching team
    has to assign them; declared before any of that."""
    if returned:
        return "returned"
    if cutoff is not None and now >= cutoff:
        return "marking"
    if now >= entry.due_datetime:
        return "late_window"
    if units > 0:
        return "open"
    if spec.is_group and entry.handout_datetime and now >= entry.handout_datetime:
        return "teams_forming" if spec.team_formation == "self_select" else "blocked"
    return "declared"


def marks_returned(
    sheet: dict | None,
    spec: grades.SheetSpec | None,
    returned_at: dict[str, datetime],
    changed: datetime | None,
) -> bool:
    """Whether every unit on the sheet has had its marks returned: each member's
    gradebook was written no earlier than the sheet last changed (at all, when that
    moment is not known). A sheet edited after the return is not returned yet."""
    if not sheet or spec is None:
        return False
    container = sheet.get(spec.container_key)
    if not isinstance(container, dict) or not container:
        return False
    for unit, block in container.items():
        members = (
            (block.get("members") or {})
            if spec.is_group and isinstance(block, dict)
            else [unit]
        )
        if not members:
            return False
        for handle in members:
            at = returned_at.get(str(handle).casefold())
            if at is None or (changed is not None and at < changed):
                return False
    return True


def _problem_entries(problems: list[dict]) -> set[str]:
    return {p["fix"].get("entry") for p in problems if p["fix"].get("entry")}


def render_assignments(
    facts: CohortFacts, problems: list[dict], now: datetime
) -> list[dict]:
    """One row per assignment the schedule declares."""
    flagged = _problem_entries(problems)
    sheet_specs = {
        schedule.cohort_name(k, e): grades.sheet_spec(
            facts.sched,
            k,
            schedule.cohort_name(k, e),
            facts.specs.get(k, grades.GradingSpec()),
            facts.specs.get(k, grades.GradingSpec()).is_group,
        )
        for k, e in facts.sched.assignments.items()
    }
    rows = []
    for slug, entry in facts.sched.assignments.items():
        spec = facts.specs.get(slug, grades.GradingSpec())
        name = schedule.cohort_name(slug, entry)
        cutoff = grades.cutoff_at(facts.sched, slug, spec)
        units = len(assignment_rows(facts.listing, name)) if facts.listing else 0
        filled, on_sheet, submitted = sheet_counts(
            facts.sheets.get(name), sheet_specs.get(name)
        )
        total = on_sheet or units
        returned = (
            total > 0
            and filled == total
            and marks_returned(
                facts.sheets.get(name),
                sheet_specs.get(name),
                facts.returned_at,
                facts.sheet_changed.get(name),
            )
        )
        solution = entry.solution_datetime
        rows.append(
            {
                "slug": slug,
                "title": entry.title or spec.title or slug,
                "template": entry.course_source_repo,
                "state": assignment_state(now, entry, cutoff, spec, units, returned),
                "handout": _iso(entry.handout_datetime),
                "due": _iso(entry.due_datetime),
                "late_until": _iso(cutoff),
                "solution_shown": _iso(solution),
                "units": units,
                "submissions": submitted,
                "teams": len(teams.teams_for(facts.teams, slug))
                if spec.is_group
                else None,
                "marks": {"filled": filled, "total": total},
                "returned": returned,
                "problem": slug in flagged
                or assignment_slug(entry.course_source_repo) in flagged,
            }
        )
    return rows


def render_releases(
    facts: CohortFacts, faults: list[ConfigFault], now: datetime
) -> list[dict]:
    """One row per `releases:` entry. Source and destination are the entry's FIRST copy;
    `copies` says how many it has."""
    rows = []
    for r in facts.sched.releases:
        own = [f for f in faults if f.is_source and f.where == f"releases.{r.label}"]
        first = r.deploy[0] if r.deploy else None
        rows.append(
            {
                "id": r.label,
                "when": _iso(r.when),
                "type": _release_type(r),
                "title": r.title,
                "state": release_state(r, facts, own, now),
                "source": {
                    "repo": first.course_source_repo,
                    "path": first.course_source_path,
                }
                if first
                else None,
                "dest": {"repo": first.cohort_dest_repo, "path": deploy_dest(first)}
                if first
                else None,
                "copies": len(r.deploy),
                "show_on_site": r.show_on_site,
                "tbc": r.tbc,
            }
        )
    return rows


def _local_midnight(now: datetime, tz: ZoneInfo) -> datetime:
    return datetime.combine(now.astimezone(tz).date(), time(0, 0), tzinfo=tz)


def this_week(
    sched: schedule.Schedule,
    releases: list[dict],
    assignments: list[dict],
    now: datetime,
) -> list[dict]:
    """What happens from the start of today to the same moment seven days on, in the
    cohort's zone: releases, hand-outs, due dates and events. The start is included and the
    end is not, so a moment belongs to exactly one week."""
    tz = ZoneInfo(sched.timezone)
    start = _local_midnight(now, tz)
    end = start + timedelta(days=7)
    items: list[tuple[datetime, dict]] = []

    def add(when: datetime | date | None, kind: str, ref: str, title: str, state: str):
        if when is None:
            return
        at = (
            when
            if isinstance(when, datetime)
            else datetime.combine(when, time(0, 0), tzinfo=tz)
        )
        if start <= at < end:
            items.append(
                (
                    at,
                    {
                        "when": at.isoformat(),
                        "type": kind,
                        "ref": ref,
                        "title": title,
                        "state": state,
                    },
                )
            )

    by_id = {r["id"]: r for r in releases}
    for r in sched.releases:
        row = by_id.get(r.label, {})
        add(r.when, "release", r.label, r.title or r.label, row.get("state", "planned"))
    by_slug = {a["slug"]: a for a in assignments}
    for slug, entry in sched.assignments.items():
        row = by_slug.get(slug, {})
        title, state = row.get("title", slug), row.get("state", "declared")
        add(entry.handout_datetime, "hand_out", slug, title, state)
        add(entry.due_datetime, "due", slug, title, state)
    for ev in sched.events:
        add(
            ev.when,
            "exam" if ev.type == "exam" else "event",
            ev.label,
            ev.title,
            "planned",
        )
    return [row for _, row in sorted(items, key=lambda p: p[0])]


def render_operations(outcomes: list[dict]) -> list[dict]:
    """The most recent operations first, off their private outcome files."""
    keep = ("run_id", "op", "conclusion", "summary", "finished")
    rows = [
        {k: o.get(k) for k in keep}
        for o in outcomes
        if isinstance(o, dict) and o.get("op")
    ]
    rows.sort(key=lambda r: str(r.get("finished") or ""), reverse=True)
    return rows[:RECENT_OPERATIONS]


def _staff_counts(people: dict[str, list[dict]] | None) -> tuple[int, int]:
    today = date.today().isoformat()

    def active(role: str) -> int:
        return sum(
            1
            for p in (people or {}).get(role, [])
            if active_today(p.get("start"), p.get("end"), today)
        )

    return active("instructors"), active("teaching_assistants")


def _no_email_problem(
    cohort_org: str, people: dict[str, list[dict]] | None
) -> list[dict]:
    """K3's "every entry has an email" as a problem: a count, never the handles."""
    missing = sync_faculty.without_email(people or {}, date.today().isoformat())
    if not missing:
        return []
    return [
        {
            "id": "people:staff:NO_EMAIL",
            "scope": "cohort",
            "stage": "K3",
            "text": f"{len(missing)} staff entr{'y' if len(missing) == 1 else 'ies'} in "
            f"people.yml ha{'s' if len(missing) == 1 else 've'} no email.",
            "stops": "They are not told about problems in this cohort.",
            "fix": {
                "repo": f"{cohort_org}/{schedule.CONFIG_REPO}",
                "path": sync_faculty.COHORT_PEOPLE_PATH,
                "line": None,
                "screen": "staff",
                "entry": None,
            },
        }
    ]


def cohort_checks(
    facts: CohortFacts, course: CourseFacts, instructors: int
) -> dict[str, str | None]:
    """Each cohort stage's predicate: None when it is met, else the one sentence that
    says what is still missing (lifecycle, cohort stages)."""
    sched = facts.sched
    students = facts.students or []
    site = pages_repo(facts.org)
    out: dict[str, str | None] = dict.fromkeys(COHORT_STAGES)
    if not facts.listing:
        out["K1"] = "The cohort org could not be read, or holds no repos yet."
    missing = [
        r for r in (schedule.CONFIG_REPO, "welcome", site) if r not in facts.listing
    ]
    if missing:
        out["K2"] = f"The cohort has no {' or '.join(missing)} repo yet."
    elif facts.org.casefold() not in {c.casefold() for c in course.registry}:
        out["K2"] = "The course does not list this cohort yet."
    if facts.people is None:
        out["K3"] = f"{sync_faculty.COHORT_PEOPLE_PATH} could not be read."
    elif instructors == 0:
        out["K3"] = (
            f"No instructor is declared in {sync_faculty.COHORT_PEOPLE_PATH} yet."
        )
    if schedule.SCHEDULE_PATH not in facts.config_paths:
        out["K4"] = f"There is no {schedule.SCHEDULE_PATH} yet."
    elif sched.unparseable:
        out["K4"] = f"{schedule.SCHEDULE_PATH} does not parse."
    elif sched.semester_start is None or sched.semester_end is None:
        out["K4"] = "The schedule has no term start or end date yet."
    elif not (sched.releases or sched.assignments):
        out["K4"] = "The schedule plans no releases or assignments yet."
    unsent = sum(not s.code_sent_at.strip() for s in students)
    if not students:
        out["K5"] = "The roster has no students yet."
    elif unsent:
        out["K5"] = (
            f"{unsent} student{' has' if unsent == 1 else 's have'} not been sent a "
            f"code yet."
        )
    if site not in facts.listing:
        out["K6"] = "The cohort has no student site yet."
    elif not _written(facts.site_home):
        out["K6"] = "The student site's home page is still the placeholder."
    if not facts.archived:
        when = sched.archive.when if sched.archive else None
        out["K7"] = (
            f"Not archived yet; the schedule archives it on {_day(when)}."
            if when
            else "Not archived yet; the schedule sets no archive date."
        )
    return out


def cohort_inputs(facts: CohortFacts, course: CourseFacts) -> dict[str, str | None]:
    """The blob (or, for the sheets, tree) shas this status was computed from."""
    paths = facts.config_paths
    return {
        schedule.SCHEDULE_PATH: paths.get(schedule.SCHEDULE_PATH),
        sync_faculty.COHORT_PEOPLE_PATH: paths.get(sync_faculty.COHORT_PEOPLE_PATH),
        roster.ROSTER_PATH: paths.get(roster.ROSTER_PATH),
        teams.TEAMS_PATH: paths.get(teams.TEAMS_PATH),
        grades.SHEETS_DIR: paths.get(grades.SHEETS_DIR),
        f"course/{COURSE_CONFIG}": (course.github_paths or {}).get(COURSE_CONFIG),
        grades.TEAM_LOCK_PATH: paths.get(grades.TEAM_LOCK_PATH),
    }


def course_inputs(course: CourseFacts) -> dict[str, str | None]:
    paths = course.github_paths or {}
    return {
        COURSE_CONFIG: paths.get(COURSE_CONFIG),
        COHORTS_PATH: paths.get(COHORTS_PATH),
    }


def render_course_file(course: CourseFacts, now: datetime) -> dict:
    """The COURSE document, for the public `.github`: counts, repo names and the problems
    that already stand in `.github`'s own digest issue. Nothing about a person."""
    block, problems = render_course(course, now)
    return {
        "schema": STATUS_SCHEMA,
        "inputs": course_inputs(course),
        "course": block,
        "problems": _unique_ids(problems),
    }


def render_cohort(course: CourseFacts, facts: CohortFacts, now: datetime) -> dict:
    """The COHORT document, for the private classroom-config.

    Problems: every fault in the cohort's own files, the course's own two files (every
    cohort pays for those), and the templates THIS cohort's schedule cites - a template
    fault is shown on every cohort it will affect, tagged `scope: course`.

    Stage predicates (lifecycle, cohort stages):
    - K1 the org resolves (`app_installed` is a stub until decision 0002);
    - K2 classroom-config, welcome and the site repo exist, and the course registry lists it;
    - K3 people.yml is read, grants at least one instructor, and every entry has an email;
    - K4 schedule.yml parses, the term's start and end are set, and it plans something;
    - K5 the roster has rows and every row has been sent a code;
    - K6 the site repo exists and its home page is written;
    - K7 classroom-config is archived.
    `live` is the finished marker's opposite (`discovery.cohort_is_live`), not K1-K5."""
    faults = [
        *facts.schedule_faults,
        *facts.people_faults,
        *facts.roster_faults,
        *facts.teams_faults,
        *facts.sheet_faults,
    ]
    problems = [problem_from_fault(f, facts.org, now) for f in faults]
    problems += [problem_from_fault(f, course.org, now) for f in course.faults]
    problems += [problem_from_fault(f, course.org, now) for f in facts.template_faults]
    problems += _no_email_problem(facts.org, facts.people)
    problems = _unique_ids(problems)
    course_block, _ = render_course(
        course, now, [p for p in problems if p["scope"] == "course"]
    )

    sched = facts.sched
    students = facts.students or []
    instructors, tas = _staff_counts(facts.people)
    site = pages_repo(facts.org)
    todo = cohort_checks(facts, course, instructors)
    done = {stage: todo[stage] is None for stage in COHORT_STAGES}
    stages = _stage_states(COHORT_STAGES, done, problems)
    today = now.astimezone(ZoneInfo(sched.timezone)).date()
    week, weeks = term_weeks(sched.semester_start, sched.semester_end, today)
    tag = term_tag(facts.org)
    releases = render_releases(facts, facts.schedule_faults, now)
    assignments = render_assignments(facts, problems, now)
    stale = facts.site_last_update is None or (
        facts.config_last_update is not None
        and facts.site_last_update < facts.config_last_update
    )
    return {
        "schema": STATUS_SCHEMA,
        "inputs": cohort_inputs(facts, course),
        "course": course_block,
        "cohort": {
            "org": facts.org,
            "term": tag,
            "term_label": term_label(tag),
            "timezone": sched.timezone,
            "week": week,
            "weeks": weeks,
            "live": not facts.archived,
            "app_installed": app_installed(),
            "stages": stages,
            "stage_why": stage_why(stages, todo, problems),
            "archive_date": _iso(sched.archive.when if sched.archive else None),
        },
        "problems": problems,
        "this_week": this_week(sched, releases, assignments, now),
        "releases": releases,
        "assignments": assignments,
        "students": {
            "rows": len(students),
            "codes_sent": sum(bool(s.code_sent_at.strip()) for s in students),
            "joined": sum(s.onboarded for s in students),
        },
        "staff": {"instructors": instructors, "tas": tas, "synced": facts.staff_synced},
        "site": {
            "url": f"https://{site}",
            "last_update": _iso(facts.site_last_update),
            "stale": stale,
        },
        "operations": render_operations(facts.outcomes),
    }


# ---------------------------------------------------------------------- gh/git wiring


def _last_commit_at(org: str, repo: str, path: str = "") -> datetime | None:
    """When `path` (or the repo, for "") last changed on the default branch. None when
    there is no such commit or it could not be read - staleness is a hint, not a gate."""
    query = f"repos/{org}/{repo}/commits?per_page=1" + (f"&path={path}" if path else "")
    code, out = gh("api", query, "--jq", ".[0].commit.committer.date // empty")
    if code != 0 or not out.strip():
        return None
    try:
        return datetime.fromisoformat(out.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def gather_course(course_org: str) -> CourseFacts:
    """Read the course org. A listing or tree that could not be read raises: a status that
    reported an unreadable org as an empty one would tell the console to start again."""
    listing = {r["name"]: r for r in list_org_repos(course_org)}
    facts = CourseFacts(org=course_org)
    facts.github_paths = repo_path_shas(
        course_org, ".github", default_branch(course_org, ".github", fallback="main")
    )
    try:
        facts.meta = org_meta(course_org)
    except (yaml.YAMLError, Unusable):
        facts.meta = {}  # the COURSE digest's faults below say what is wrong with it
    sync_faculty.read_course_config(course_org, facts.faults)
    facts.registry = read_cohort_registry(course_org, facts.faults)
    for name in sorted(listing):
        row = listing[name]
        if row.get("archived"):
            continue
        if name.startswith(MATERIALS_REPO_PREFIX):
            facts.materials.append(
                MaterialsFacts(
                    name,
                    get_file_content(course_org, name, SYLLABUS_FILE),
                    get_file_content(course_org, name, PUBLISH_FILE) is not None,
                )
            )
        elif name.startswith("assignment-") and row.get("isTemplate"):
            t = TemplateFacts(name, get_file_content(course_org, name, README_FILE))
            text = get_file_content(
                course_org, name, grades.GRADING_FILE, ref=SOLUTION_BRANCH
            )
            if text is not None:
                t.faults, _ = grades.grading_spec_faults(
                    assignment_slug(name), name, course_org, text, None
                )
            facts.templates.append(t)
    facts.public_site = pages_repo(course_org) in listing
    return facts


def _outcomes(cohort_org: str, paths: dict[str, str]) -> list[dict]:
    out = []
    for path in sorted(
        p for p in paths if p.startswith(f"{OUTCOMES_DIR}/") and p.endswith(".json")
    ):
        text = get_file_content(cohort_org, schedule.CONFIG_REPO, path)
        try:
            out.append(json.loads(text or ""))
        except json.JSONDecodeError:
            continue
    return out


def _returned_at(cohort_org: str) -> dict[str, datetime]:
    text = get_file_content(cohort_org, schedule.CONFIG_REPO, grades.DISTRIBUTED_PATH)
    if not text:
        return {}
    try:
        records = grades.parse_distributed(text)
    except Unusable:
        return {}
    out: dict[str, datetime] = {}
    for (target, assignment, channel), (_digest, when, _issue) in records.items():
        if channel != grades.CHANNEL_GRADEBOOK or assignment:
            continue
        try:
            at = datetime.fromisoformat(when)
        except ValueError:
            continue
        out[target.casefold()] = at if at.tzinfo else at.replace(tzinfo=UTC)
    return out


def gather_cohort(course_org: str, cohort_org: str, now: datetime) -> CohortFacts:
    """Read one cohort, through the same loaders its digest issues are built by, so a
    problem here is the fault that issue lists. A read that fails raises."""
    facts = CohortFacts(org=cohort_org)
    facts.listing = {r["name"]: r for r in list_org_repos(cohort_org)}
    branch = default_branch(cohort_org, schedule.CONFIG_REPO, fallback="main")
    facts.config_paths = repo_path_shas(cohort_org, schedule.CONFIG_REPO, branch)
    sched = facts.sched = schedule.load(cohort_org)
    # The schedule.yml digest's own three sources (`scheduler._preflight_sources`): the
    # sources the plan cites, the entries the parser dropped, and the team-formation
    # windows somebody is still waiting on (None = the roster could not be read).
    windows = team_formation.open_windows(course_org, cohort_org, sched, now)
    facts.schedule_faults = [
        *schedule.source_faults(sched, course_org),
        *sched.faults,
        *(team_formation.window_faults(sched, windows) or []),
    ]
    facts.people = sync_faculty.read_cohort_people(cohort_org, facts.people_faults)
    try:
        facts.students = roster.load(cohort_org, facts.roster_faults)
    except Unusable:
        facts.students = None  # the header fault is already in roster_faults
    known = known_handles(facts.students) if facts.students is not None else None
    try:
        facts.teams = teams.load(cohort_org, facts.teams_faults, known)
    except Unusable:
        facts.teams = {}
    # `grades.cohort_sheet_faults`, with each sheet read once for the faults and the marks.
    sheet_specs = grades.sheet_specs(course_org, sched)
    sheet_texts = {
        name: get_file_content(
            cohort_org, schedule.CONFIG_REPO, grades.sheet_path(name)
        )
        for name in sorted(sheet_specs)
    }
    for name, text in sheet_texts.items():
        if text is not None:
            facts.sheet_faults += grades.sheet_faults(name, text, sheet_specs[name])
    grades.grading_config_faults(
        course_org, cohort_org, sched, facts.template_faults, facts.listing
    )
    for slug, entry in sched.assignments.items():
        facts.specs[slug] = grades.load_grading_spec(
            course_org, entry.course_source_repo
        )
        name = schedule.cohort_name(slug, entry)
        text = sheet_texts[name]
        if text:
            try:
                facts.sheets[name] = grades.parse_sheet(text)
            except grades.SheetUnreadable:
                pass  # its fault is in sheet_faults
            facts.sheet_changed[name] = _last_commit_at(
                cohort_org, schedule.CONFIG_REPO, grades.sheet_path(name)
            )
    facts.returned_at = _returned_at(cohort_org)
    for repo in sorted({d.cohort_dest_repo for r in sched.releases for d in r.deploy}):
        if repo in facts.listing:
            facts.dest_paths[repo] = set(
                repo_tree(
                    cohort_org, repo, default_branch(cohort_org, repo, fallback="main")
                )
            )
    site = pages_repo(cohort_org)
    if site in facts.listing:
        facts.site_home = get_file_content(cohort_org, site, SITE_HOME)
        facts.site_last_update = _last_commit_at(cohort_org, site)
    moments = [
        _last_commit_at(cohort_org, schedule.CONFIG_REPO, p)
        for p in (schedule.SCHEDULE_PATH, sync_faculty.COHORT_PEOPLE_PATH)
    ]
    facts.config_last_update = max((m for m in moments if m), default=None)
    members = get_team_members(cohort_org, INSTRUCTORS_TEAM)
    if members is not None and facts.people is not None:
        want = sync_faculty.desired_team_members(
            facts.people, date.today().isoformat()
        ).get(INSTRUCTORS_TEAM, set())
        facts.staff_synced = {m.casefold() for m in members} == {
            w.casefold() for w in want
        }
    facts.outcomes = _outcomes(cohort_org, facts.config_paths)
    return facts


def collect_course(course_org: str, now: datetime | None = None) -> dict:
    """The course document (`dsl.status/1`), for `.github/.dsl/status.json`."""
    now = now or datetime.now(UTC)
    return render_course_file(gather_course(course_org), now)


def collect_cohort(
    course_org: str, cohort_org: str, now: datetime | None = None
) -> dict:
    """The cohort document (`dsl.status/1`), for `classroom-config/.dsl/status.json`."""
    now = now or datetime.now(UTC)
    return render_cohort(
        gather_course(course_org), gather_cohort(course_org, cohort_org, now), now
    )


def dumps(doc: dict) -> bytes:
    """The file's exact bytes: stable key order as built, two-space indent, one trailing
    newline - so two renders of one state are one blob and the writer makes no commit."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
