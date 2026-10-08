"""dsl-course status.json -- one machine-readable account of a course and a semester.

`status.py`'s checklist answers "which files have I filled in?" for a person reading a run
summary. This answers the lifecycle's question for a program: which stage each scope is
at, what is wrong and where to fix it, what happens this week, and what state every
release and assignment is in. The shape is `dsl.status/1` (build contracts, section 3);
the stages, predicates and states are `design/lifecycle.md`'s, and every sentence in a
`text` or `stops` is in `design/vocabulary.md`'s words.

TWO HALVES, so the model can be tested without a single `gh` call:

- `gather_course` / `gather_semester` read live GitHub state into `CourseFacts` /
  `SemesterFacts`, through the loaders the rest of the toolkit already uses (the digest
  parsers, `schedule.load`, `roster.load`, the grading-spec reader) - nothing is
  re-derived here that a loader already answers.
- `render_course` / `render_semester` turn facts into the document. Pure.

`collect_course` / `collect_semester` are the two together. The writer is `status.write`,
which puts the rendered file where the console reads it.

NO TIMESTAMP of its own. The file carries `inputs` - the blob shas of the files it was
computed from - so a reader tells a stale status from a current one with one tree read,
and a render that matches the file byte for byte makes no commit. The moments inside it
(a release's `when`, the site's last update) are facts about the semester, not about when
this was written.
Nothing that moves on every scheduler tick is in it - the automation heartbeat
included, which the console reads off the workflow's run list - or every semester's
semester-config would take a commit every quarter hour.

PUBLIC AND PRIVATE. The semester file lives in the private `semester-config`; the course
file lives in the course org's PUBLIC `.github`, so `render_course` puts nothing in it but
repo names, counts and the course-side problems that already stand in that repo's own
digest issue - never a handle, an email or a student repo name.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import yaml

from . import (
    grades,
    policy,
    records,
    roster,
    schedule,
    settings,
    sync_faculty,
    team_formation,
    teams,
)
from .course import (
    ASSIGNMENTS_FILE,
    COURSE_ADMIN_TEAM,
    COURSE_CONFIG,
    INSTRUCTORS_TEAM,
    JOIN_REPO,
    MATERIALS_REPO_PREFIX,
    SELF_SELECT,
    SOLUTION_BRANCH,
    SOLUTION_DIR,
    STARTER_DERIVED,
    STARTER_HANDWRITTEN,
    active_today,
    is_repo_root,
    pages_repo,
    semester_label,
    semester_of,
    session_number,
)
from .derive import (
    STARTER_RECORD,
    declared_starter,
    derivable_sources,
)
from .discovery import (
    SEMESTERS_PATH,
    TEMPLATE_TOPIC,
    assignment_rows,
    handed_out_assignments,
    is_assignment_template,
    is_untopicked_template,
    list_org_repos,
    org_meta,
    read_semester_registry,
)
from .faults import NOT_MIGRATED, ConfigFault, FaultKind, Unusable
from .gh_commits import last_commit_at
from .gh_contents import (
    WITHHELD_ROOT_STUBS,
    file_exists,
    get_file_content,
    is_untouched_stub,
    line_of,
    repo_path_shas,
    repo_tree,
    root_stub_unwritten,
    top_level,
)
from .gh_teams import get_team_members
from .log import log_err, plural
from .materials import (
    ASSETS_KIND,
    DEFAULT_SYLLABUS,
    MATERIALS_FILE,
    MATERIALS_TOPIC,
    PLAN_START,
    Declared,
    alias_kind,
    infer_kind,
    is_materials_repo,
    publishable,
)
from .materials import read as read_materials
from .opencourse import OPENCOURSE_FILE, OPENCOURSE_REPO
from .opencourse import load as load_opencourse
from .opencourse import parse as parse_opencourse
from .ops.outcome import OUTCOMES_DIR
from .ops.registry import STATUS_SCHEMA
from .releaseignore import RELEASEIGNORE, REVIEWED_MARK, listed
from .repos import default_branch
from .schedule_plan import (
    Unnumbered,
    deploy_dest,
    duplicate_numbers,
    duplicate_text,
    entry_kind,
    entry_landing,
    own_number,
    planned_rows,
    site_rows,
    unnumbered,
)
from .sync_teams import known_handles

# Where each file lives, inside `semester-config` (semester) or `.github` (course).
STATUS_PATH = records.path("status")
# How many recent operations the semester file lists.
RECENT_OPERATIONS = 10

README_FILE = "README.md"
SITE_HOME = "index.md"

# Stage identifiers, in lifecycle order.
COURSE_STAGES = ("C1", "C2", "C3", "C4", "C5", "C6")
SEMESTER_STAGES = ("K1", "K2", "K3", "K4", "K5", "K6")
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
# Decision 0034: every setup step, repo check and to-do is needed (without it automation
# cannot act, or a student gets something wrong) or suggested (everything else).
NEEDED, SUGGESTED = "needed", "suggested"
COURSE_STAGE_NEED = {
    s: NEEDED if s in ("C1", "C2", "C3") else SUGGESTED for s in COURSE_STAGES
}
SEMESTER_STAGE_NEED = dict.fromkeys(SEMESTER_STAGES, NEEDED)
# When a problem bites (`problems[].bites`): its moment has passed or it has none, it
# falls inside the horizon, or it lies beyond. Only `now` and `soon` mark a stage or a
# verdict; `later` is "coming up". The digest's mail ladder is a separate clock.
NOW_, SOON, LATER = "now", "soon", "later"
# The horizon: a rolling window from the tick. The one constant; every other reader
# (the console, the CLI's words) takes its length off `horizon.days`.
PROBLEM_HORIZON = timedelta(days=7)
# The container verdict (`course.verdict`, `semester.verdict`).
FIXING, NOT_READY, READY = "fixing", "not_ready", "ready"
# What a blocked stage is waiting for, named by the prerequisite it waits on.
WAITING_FOR = {
    "C1": "the course org",
    "C2": "the course to be set up",
    "C4": "the course's materials to be ready",
    "K1": "the semester org",
    "K2": "the semester to be set up",
}


# ---------------------------------------------------------------------------- facts


@dataclass
class TemplateFacts:
    """One assignment template in the course org, as far as C5 asks about it."""

    repo: str
    readme: str | None = None
    faults: list[ConfigFault] = field(default_factory=list)
    # False for an `assignment-*` GitHub template without the `dsl-assignment` topic yet.
    topic: bool = True
    # Decision 0028: how `main` is written (the key, else read off the solution tree).
    starter: str = STARTER_DERIVED
    # What the starter still needs (`starter_check`), None when it is in place.
    starter_todo: str | None = None
    # Derived: the first recorded starter file whose blob on `main` is not Derive's.
    main_edited: str | None = None


@dataclass
class MaterialsFacts:
    """One materials repo (topic `dsl-materials`), as far as C4 asks about it.
    `syllabus` is the declared syllabus's text when it is markdown, "" when it is another
    file that is there (a PDF), None when it is absent."""

    repo: str
    syllabus: str | None = None
    syllabus_path: str = DEFAULT_SYLLABUS
    # False for a `course-materials-*` repo without the `dsl-materials` topic yet.
    topic: bool = True
    # The top-level folders (dot-folders left out) and `materials.yml`'s `kinds`.
    folders: tuple[str, ...] = ()
    kinds: Mapping[str, str] = field(default_factory=dict)
    # The top-level `.releaseignore`'s text; None when there is none.
    releaseignore: str | None = None
    # The released top folders no kind names (no `kinds:` entry, no alias) that hold
    # numbered subfolders: each got rows under the old `lecture` default and is now
    # supporting files (`kindless_problems`).
    numbered: tuple[str, ...] = ()


@dataclass
class CourseFacts:
    """Everything the course half of the document is computed from."""

    org: str
    meta: dict = field(default_factory=dict)
    # `.github`'s tree, `{path: sha}`; None when the org could not be read at all.
    github_paths: dict[str, str] | None = None
    registry: list[str] = field(default_factory=list)
    # What is wrong with dsl-course.yml and the semester registry - the COURSE digest's list.
    faults: list[ConfigFault] = field(default_factory=list)
    materials: list[MaterialsFacts] = field(default_factory=list)
    templates: list[TemplateFacts] = field(default_factory=list)
    public_site: bool = False
    # `opencourse.yml` says `enabled: true` (and parses).
    website_on: bool = False
    # `opencourse.yml` is there but does not parse or validate.
    website_unusable: bool = False


@dataclass
class SemesterFacts:
    """Everything the semester half of the document is computed from. Built by
    `gather_semester`; a test builds one by hand."""

    org: str
    listing: dict[str, dict] = field(default_factory=dict)
    # semester-config's tree, `{path: sha}` - the semester half of `inputs`.
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
    # The semester's view of the course-side templates it cites: every value in their
    # grading_config.yml that will not grade as written. Rolled up as COURSE problems.
    template_faults: list[ConfigFault] = field(default_factory=list)
    specs: dict[str, grades.GradingSpec] = field(
        default_factory=dict
    )  # by schedule key
    sheets: dict[str, dict] = field(default_factory=dict)  # by semester-side name
    # `{schedule key: its name}`: the template's title, else its README heading
    # (`grades.assignment_title`).
    titles: dict[str, str] = field(default_factory=dict)
    # `{handle, casefolded: when its gradebook was last written}`, off distributed.csv.
    # A gradebook holds every assignment, so its record names none.
    returned_at: dict[str, datetime] = field(default_factory=dict)
    # `{semester-side name: when its grading sheet last changed}`; None = not known.
    sheet_changed: dict[str, datetime | None] = field(default_factory=dict)
    dest_paths: dict[str, set[str]] = field(default_factory=dict)  # release dest trees
    dest_branches: dict[str, str] = field(default_factory=dict)  # and their branches
    # `{source repo: its materials.yml folder aliases}` - what an undeclared kind is
    # inferred through.
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    # `{(source repo, root stub path): still the placeholder}` for every copy the plan
    # makes of a root SYLLABUS.md / README.md: the release leaves such a copy out.
    stubs: dict[tuple[str, str], bool] = field(default_factory=dict)
    site_home: str | None = None
    site_last_update: datetime | None = None
    config_last_update: datetime | None = None
    # Whether the instructors team holds exactly who instructors.yml grants. None = unread.
    staff_synced: bool | None = None
    outcomes: list[dict] = field(default_factory=list)  # parsed dsl.outcome/1 files

    @property
    def archived(self) -> bool:
        """The finished marker (`discovery.semester_is_live`): an archived semester-config."""
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
    """`Thu 8 Oct` - how a sentence names a day. No zone: every date in a semester's
    sentences is in the semester's own zone, which the file states once (`timezone`)."""
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
    sync_faculty.SEMESTER_PEOPLE_PATH: (
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
        "Automation skips this course: instructor access and semesters are not updated."
    ),
    SEMESTERS_PATH: (
        "Automation skips this course: instructor access and semesters are not updated."
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
    schedule.SCHEDULE_PATH: _Where(
        "schedule", "semester", "K4", "schedule", "SCHEDULE"
    ),
    # How the semester runs each assignment: the schedule's partner, and the same stage.
    ASSIGNMENTS_FILE: _Where(
        "assignments", "semester", "K4", "assignment", "ASSIGNMENTS"
    ),
    # The old file's name too, so a NOT_MIGRATED fault about it files under K3.
    **dict.fromkeys(
        (sync_faculty.SEMESTER_PEOPLE_PATH, sync_faculty.OLD_PEOPLE_FILE),
        _Where("people", "semester", "K3", "instructors", "PEOPLE"),
    ),
    roster.ROSTER_PATH: _Where("roster", "semester", "K5", "roster", "ROSTER"),
    teams.TEAMS_PATH: _Where("teams", "semester", "K5", "teams", "TEAMS"),
    grades.GRADING_FILE: _Where(
        "template", "course", "C5", "template", "GRADING_CONFIG"
    ),
    grades.LEGACY_GRADING_FILE: _Where(
        "template", "course", "C5", "template", "GRADING_CONFIG"
    ),
    COURSE_CONFIG: _Where("course", "course", "C3", "course", "COURSE"),
    SEMESTERS_PATH: _Where("registry", "course", "C2", "course", "COURSE"),
}
# A grading sheet belongs to the running phase, not to a setup stage: its stage is the
# marking phase, and the assignment is the entry in its id.
MARKING = "marking"
_SHEET = _Where("sheet", "semester", MARKING, "marks", "GRADING_SHEETS")
# Any other semester-config file: the semester's own setup.
_OTHER = _Where("config", "semester", "K2", "", "CONFIG")
# A template fault only one semester pays for (`ConfigFault.per_semester`): the repos it
# handed out, or its schedule entry. The semester's, in the phase after hand out.
HANDED_OUT = "open"
_SEMESTER_TEMPLATE = _Where(
    "template", "semester", HANDED_OUT, "template", "GRADING_CONFIG"
)
# A run setting that no longer describes what was handed out (visibility drift): the
# phase after hand out, fixed in assignments.yml.
_HANDED_OUT_SETTING = _Where(
    "assignments", "semester", HANDED_OUT, "assignment", "ASSIGNMENTS"
)
# Marks whose `marks_return_datetime` has come with units unmarked: the marking phase.
_MARKS_DUE = _Where("schedule", "semester", MARKING, "marks", "MARKS_DUE")
# The semester org's own member privileges: part of setting the semester up, fixed on a
# GitHub settings page rather than in a file.
_ORG = _Where("org", "semester", "K2", "", "ORG_SETTINGS")

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
        where = "course details" if filed.kind == "course" else "instructors.yml"
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
        "people": "instructors.yml",
        "roster": "The roster",
        "teams": "The teams file",
        "course": "Course details",
        "registry": "The course's list of semesters",
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
    if fault.field == "marks_return_datetime" and fault.file == schedule.SCHEDULE_PATH:
        return _MARKS_DUE
    if fault.per_semester and fault.file == ASSIGNMENTS_FILE:
        return _HANDED_OUT_SETTING
    if fault.per_semester and fault.file in (
        grades.GRADING_FILE,
        grades.LEGACY_GRADING_FILE,
    ):
        return _SEMESTER_TEMPLATE
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


def problem_from_fault(
    fault: ConfigFault,
    org: str,
    now: datetime,
    moments: Mapping[str, Moment] | None = None,
    slug_handouts: Mapping[str, datetime | None] | None = None,
) -> dict:
    """One `problems[]` entry for one fault. `org` is where the fault's file is when the
    fault does not say otherwise (the semester, for everything in semester-config).
    `kind` is its code; `release` the schedule entry it holds back, when it holds one.

    `when` is the instant the fault bites: the fault's own `fires` (a release, a hand-out),
    except a template's, which bites at the first hand-out that consumes the template -
    `moments`, by template; an assignments.yml value's, at its assignment's hand-out -
    `slug_handouts`, by schedule key; and visibility drift's, which stands now. Undated
    without one; an undated source fault, and a course template's no dated hand-out
    cites, is marked `UNDATED_LATER`. `_tier` sets `bites`.

    The fix pointer names the repo, path and line to edit and the console screen that
    edits it; a file on a branch other than `main` (a template's `solution`) says which."""
    filed = _where_filed(fault)
    if fault.is_source:
        code = _SOURCE_CODES[fault.kind]
        text, stops = _source_sentences(fault, now)
        entry = _entry_of(fault.where)
    else:
        code = fault.code or filed.code
        if filed.kind == "template":
            entry = fault.in_repo
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
        "entry": entry
        if filed.kind in ("schedule", "assignments", "template", "sheet")
        else None,
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
    problem = {
        "id": f"{filed.kind}:{_slugify(entry)}:{code}",
        "scope": filed.scope,
        "stage": filed.stage,
        "kind": code,
        "text": text,
        "stops": stops,
        "fix": fix,
    }
    # The schedule entry it holds back: a source the entry cites, or the entry itself
    # (the parser dropped it). A marks-due fault holds nothing back.
    if fault.is_source or (
        filed.kind == "schedule"
        and filed is not _MARKS_DUE
        and fault.where.startswith(("releases.", "assignments."))
    ):
        problem["release"] = entry
    # The week the console's strip counts it under. Absent for a fault no date pins.
    if filed.kind == "template":
        m = (moments or {}).get(entry)
        when = m.when if m else None
    elif filed is _HANDED_OUT_SETTING:
        when = None  # visibility drift: it already stands
    elif filed.kind == "assignments":
        # A value in one assignment's block bites at that assignment's hand-out; a
        # file-level fault (it does not parse, `defaults:`) stands now.
        when = (slug_handouts or {}).get(entry)
    else:
        when = fault.fires
    if when is not None:
        problem["when"] = when.isoformat()
    elif fault.is_source or filed.scope == "course" and filed.kind == "template":
        # Decision 0034: a deliberately undated entry (TBC, hand-out by hand) puts
        # nothing at risk yet - nothing fires until it has a date; nor does a course
        # template no dated hand-out cites.
        problem[UNDATED_LATER] = True
    return problem


def horizon() -> dict:
    """`status.json`'s `horizon` (decision 0034, amended): the rolling window's length,
    the one place it reaches a reader. No tick time: the file carries no moment of its
    own, so an unchanged state renders the same bytes and makes no commit; a reader
    counts the window from its own clock."""
    return {"days": PROBLEM_HORIZON.days}


def bites(when: datetime | None, now: datetime) -> str:
    """`now` once the moment has passed or there is none, `soon` up to `PROBLEM_HORIZON`
    from now (that instant included), `later` beyond it."""
    if when is None or when <= now:
        return NOW_
    return SOON if when <= now + PROBLEM_HORIZON else LATER


# Set by a builder on a problem whose missing moment means "later", not "now": an undated
# source fault, an undated SOURCE_UNWRITTEN copy, a course template no dated hand-out
# cites. `_tier` reads it and drops it.
UNDATED_LATER = "undated_later"


def _tier(problems: list[dict], now: datetime) -> list[dict]:
    """Decision 0034's one tiering site, and the only place `bites` is set; each builder
    has set `when` already. A problem with no moment is `now`, unless its builder marked
    it `UNDATED_LATER`; one with a moment is `now`, `soon` or `later` by `bites`."""
    for p in problems:
        undated_later = p.pop(UNDATED_LATER, False)
        when = datetime.fromisoformat(p["when"]) if p.get("when") else None
        p["bites"] = LATER if when is None and undated_later else bites(when, now)
    return problems


def _biting(problems: list[dict]) -> list[dict]:
    """The problems that mark a stage and a verdict: `now` and `soon`."""
    return [p for p in problems if p["bites"] != LATER]


def _distinct(problems: list[dict]) -> list[dict]:
    """One problem per id: the course block inside a semester's file sees the course's
    own problems twice, once rolled up from the semester."""
    seen: dict[str, dict] = {}
    for p in problems:
        seen.setdefault(p["id"], p)
    return list(seen.values())


@dataclass(frozen=True)
class Moment:
    """When a course template is first needed: its first dated citing hand-out (None:
    cited only by an assignment with no hand-out date), and that assignment's schedule
    key (`release`; None in the course file, which sees only the instant)."""

    when: datetime | None
    release: str | None = None


def _handouts(sched: schedule.Schedule) -> dict[str, Moment]:
    """`{template repo: the first hand-out that cites it}` in one schedule."""
    out: dict[str, Moment] = {}
    for slug, a in sched.assignments.items():
        was = out.get(a.course_source_repo)
        when = a.handout_datetime
        if was is None or (when is not None and (was.when is None or when < was.when)):
            out[a.course_source_repo] = Moment(when, slug if when else None)
    return out


def merge_moments(many: list[dict[str, Moment]]) -> dict[str, Moment]:
    """Several semesters' template moments as one: per template, the earliest dated
    hand-out any citing semester gives it."""
    out: dict[str, Moment] = {}
    for moments in many:
        for repo, m in moments.items():
            was = out.get(repo)
            if was is None or (
                m.when is not None and (was.when is None or m.when < was.when)
            ):
                out[repo] = m
    return out


def verdict(
    problems: list[dict],
    needed: list[str],
    suggestions: int,
    coming_up: int | None = None,
) -> dict:
    """Decision 0034's container verdict over distinct, tiered problems: `fixing` while a
    `now` or `soon` problem stands, else `not_ready` while a needed setup stage is open
    (`needed`: their sentences, in stage order; `missing` names the first), else `ready`.
    `coming_up` (the semester's `later` problems) is left out when None: the course has
    none."""
    biting = _biting(problems)
    out = {
        "state": FIXING if biting else NOT_READY if needed else READY,
        "problems": len(biting),
        "missing": needed[0] if needed else None,
        "suggestions": suggestions,
    }
    if coming_up is not None:
        out["coming_up"] = coming_up
    return out


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
    against it (`now` or `soon`; `problems` distinct), the prerequisite it waits for, or
    what its own predicate still lacks (`todo`, the sentence each predicate returns when it
    is not met)."""
    out: dict[str, str] = {}
    standing = _biting(problems)
    for stage, state in states.items():
        if state == PROBLEM:
            n = sum(p["stage"] == stage for p in standing)
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
    everything, when it bites `now` or `soon` (decision 0034: a `later` one is coming up,
    not wrong yet); otherwise a stage whose prerequisite is not done is blocked."""
    flagged = {p["stage"] for p in _biting(problems)}
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


def _syllabus_written(m: MaterialsFacts) -> bool:
    """The declared syllabus is there, and a markdown one is no longer our stub."""
    return m.syllabus == "" or _written(m.syllabus)


def _released_folders(m: MaterialsFacts) -> list[str]:
    """The top folders a release can copy: `solution/`, `tests/` and the like never go
    anywhere, so they need no kind."""
    return [f for f in m.folders if publishable(f)]


def _reviewed(text: str | None) -> bool:
    """A `.releaseignore` somebody has been through: one pattern (a line that is not blank
    or a comment), or the mark the console writes when nothing is withheld. The seeded one
    is every line a comment (`scaffold`), in every wording it has had."""
    lines = [line.strip() for line in (text or "").splitlines()]
    return REVIEWED_MARK in lines or any(
        line and not line.startswith("#") for line in lines
    )


def _plan_written(m: MaterialsFacts) -> bool:
    """The Markdown syllabus carries the weekly plan's marked block."""
    return PLAN_START in (m.syllabus or "")


def _syllabus_why(m: MaterialsFacts) -> str:
    """The one syllabus check's why (decision 0034): the unmet parts - the file itself,
    and the weekly plan the console writes into it from the schedule."""
    path = m.syllabus_path
    if m.syllabus == "":
        return f"{path} is not Markdown: copy the weekly plan and paste it in."
    if m.syllabus is None:
        return f"There is no {path} yet."
    file = None if _syllabus_written(m) else f"{path} is still the placeholder"
    if _plan_written(m):
        return f"{file}."
    if file is None:
        return f"The weekly plan (written from the schedule) is not in {path} yet."
    return f"{file}, and the weekly plan (written from the schedule) is not in it yet."


def _kinds_found(m: MaterialsFacts) -> list[dict]:
    """Every content kind but supporting files, in the policy's order, each with the
    released top folders of that kind; empty for a kind no folder has, so the console shows
    which kinds are present and which are not."""
    named = {f: alias_kind(f, m.kinds) for f in _released_folders(m)}
    return [
        {"kind": kind, "folders": [f for f, k in named.items() if k == kind]}
        for kind in policy.content_kinds()
        if kind != ASSETS_KIND
    ]


def _shown_folders(m: MaterialsFacts) -> list[str]:
    """The released top folders of a kind the sites show: a supporting-files folder (any
    folder no kind names) is released but gets no page, so on its own it does not make a
    repo releasable."""
    return [f for f in _released_folders(m) if infer_kind(f, m.kinds) != ASSETS_KIND]


def _kind_folder_why(released: list[str]) -> str:
    if not released:
        return (
            "There is no lectures/, labs/ or readings/ folder yet; add one or set a "
            "folder's kind under Folder kinds."
        )
    return "Only supporting files so far; set a folder's kind under Folder kinds."


def materials_checks(m: MaterialsFacts) -> list[dict]:
    """Decision 0022 rule 5: a materials repo's checklist, folder kinds first, then the
    syllabus with the weekly plan it carries (one check, decision 0034), and the withheld
    patterns. Every folder has a kind since decision 0031 (supporting files by default),
    so there is no check that each is mapped: `kindless_problems` names the folders the
    new default hid. `need` (decision 0034): only a content-kind folder is needed - the
    syllabus is suggested, since releases run without it; `blocks` is the same fact, kept
    for the console that reads it. `why` names what is missing, None once the check is
    done; `kind_folder` carries `detail`, the folders found per content kind."""
    rows = (
        (
            "kind_folder",
            "At least one folder of a content kind",
            NEEDED,
            bool(_shown_folders(m)),
            _kind_folder_why(_released_folders(m)),
        ),
        (
            "syllabus",
            "Syllabus",
            SUGGESTED,
            _syllabus_written(m) and _plan_written(m),
            _syllabus_why(m),
        ),
        (
            "withheld",
            "Withheld patterns reviewed",
            SUGGESTED,
            _reviewed(m.releaseignore),
            "Nothing is withheld from students yet; review the withheld patterns.",
        ),
    )
    return [
        {
            "id": cid,
            "label": label,
            "done": done,
            "why": None if done else why,
            "need": need,
            "blocks": need == NEEDED,
            **({"detail": _kinds_found(m)} if cid == "kind_folder" else {}),
        }
        for cid, label, need, done, why in rows
    ]


def materials_state(m: MaterialsFacts, checks: list[dict]) -> str:
    """C4, per repo: `problem` until the migration gives it the topic; `ready` once every
    needed check of `checks` (its `materials_checks`) is done."""
    if not m.topic:
        return PROBLEM
    needed = (c for c in checks if c["need"] == NEEDED)
    return "ready" if all(c["done"] for c in needed) else TODO


def _nearest_materials_why(
    materials: list[MaterialsFacts], checks: Mapping[str, list[dict]]
) -> str:
    """C4's sentence while no materials repo is ready (decision 0034): the first unmet
    needed check of the repo nearest to ready (fewest unmet, then by name), named by its
    repo - never "no repo is ready". `checks`: each repo's `materials_checks`."""
    unmet = {
        m.repo: [c for c in checks[m.repo] if c["need"] == NEEDED and not c["done"]]
        for m in materials
        if m.topic
    }
    if not unmet:
        return "No materials repo is ready yet."
    repo = min(unmet, key=lambda r: (len(unmet[r]), r))
    first = unmet[repo][0]["id"] if unmet[repo] else None
    if first == "kind_folder":
        m = next(m for m in materials if m.repo == repo)
        if not _released_folders(m):
            return f"{repo} has no lectures/, labs/ or readings/ folder yet."
        return f"{repo} has only supporting files so far."
    return f"{repo} is not ready yet."


def materials_problem(m: MaterialsFacts, org: str) -> dict:
    """The NOT_MIGRATED problem of a materials repo that has no topic yet."""
    return {
        "id": f"materials:{_slugify(m.repo)}:{NOT_MIGRATED}",
        "scope": "course",
        "stage": "C4",
        "kind": NOT_MIGRATED,
        "text": (
            f"{m.repo} is a materials repo by its old name only: it has no "
            f"{MATERIALS_TOPIC} topic yet."
        ),
        "stops": "Run the migration, which adds the topic.",
        "fix": {
            "repo": f"{org}/{m.repo}",
            "path": "",
            "line": None,
            "screen": None,
            "entry": None,
            "url": f"https://github.com/{org}/{m.repo}",
        },
    }


# Decision 0031 rule 10: a folder no kind names is supporting files, where it used to be a
# lecture. Content that got rows under the old default would vanish from the sites without a
# word, so each such folder is a problem until a kind is set.
KINDLESS_STOPS = "Its files are still released, but get no page of their own."
NO_KIND = "NO_KIND"


def kindless_problems(m: MaterialsFacts, org: str) -> list[dict]:
    """`kinds:<repo>:<folder>` for each top folder of a materials repo that no kind names
    and that holds numbered subfolders (`MaterialsFacts.numbered`): sessions the public
    website and an unkinded schedule entry showed as lectures before."""
    return [
        {
            "id": f"kinds:{_slugify(m.repo)}:{_slugify(folder)}",
            "scope": "course",
            "stage": "C4",
            "kind": NO_KIND,
            "text": (
                f"{folder}/ in {m.repo} has numbered folders but no kind; set its kind "
                "under Folder kinds."
            ),
            "stops": KINDLESS_STOPS,
            "fix": {
                "repo": f"{org}/{m.repo}",
                "path": MATERIALS_FILE,
                "line": None,
                "screen": "materials",
                "entry": m.repo,
            },
        }
        for folder in m.numbered
        if alias_kind(folder, m.kinds) is None
    ]


def kindless_entry_problems(facts: SemesterFacts) -> list[dict]:
    """`kinds:<key>` for each shown `releases:` entry that declares no kind and lands in a
    folder no kind names: it was a lecture row before decision 0031 and is now supporting
    files, no row at all."""
    aliases = lambda repo: facts.aliases.get(repo, {})
    out = []
    for r in facts.sched.releases:
        landing = entry_landing(r, aliases)
        if landing.section is None or landing.named:
            continue
        if not r.show_on_site:
            continue
        first, section = r.deploy[0], landing.section
        where = (
            f"{section}/"
            if "/" in deploy_dest(first)
            else f"the top of {first.semester_dest_repo}"
        )
        # Folder kinds lists the source repo's top folders: it can settle this only when
        # the copy lands in a folder of the same name. Otherwise only the entry can.
        source_top, sep, _ = first.course_source_path.strip("/").partition("/")
        if sep and source_top.lower() == section.lower():
            fix = "set its kind under Folder kinds, or give the entry a kind"
        else:
            fix = "give the entry a kind"
        out.append(
            {
                "id": f"kinds:{_slugify(r.label)}",
                "scope": "semester",
                "stage": "K4",
                "kind": NO_KIND,
                "text": f"{r.label} lands in {where}, which has no kind; {fix}.",
                "stops": "It gets no row on the student site.",
                "fix": _schedule_fix(facts.org, r.label, line_of(r.lines, "kind")),
            }
        )
    return out


def template_problem(t: TemplateFacts, org: str) -> dict:
    """The NOT_MIGRATED problem of an assignment template that has no topic yet."""
    return {
        "id": f"template:{_slugify(t.repo)}:{NOT_MIGRATED}",
        "scope": "course",
        "stage": "C5",
        "kind": NOT_MIGRATED,
        "text": (
            f"{t.repo} is an assignment template by its old name only: it has no "
            f"{TEMPLATE_TOPIC} topic yet."
        ),
        "stops": "Run the migration, which adds the topic.",
        "fix": {
            "repo": f"{org}/{t.repo}",
            "path": "",
            "line": None,
            "screen": None,
            "entry": t.repo,
            "url": f"https://github.com/{org}/{t.repo}",
        },
    }


NOT_NUMBERED, DUPLICATE_NUMBER = "NOT_NUMBERED", "DUPLICATE_NUMBER"


def _number_stops(m: Unnumbered, now: datetime, released: bool = False) -> str:
    """What an entry with no number stops, said the way a skipped release is. A release
    whose every copy is already there stops nothing: only its row goes without."""
    if m.block == "releases" and not m.copies:
        return "Its row on the site shows no number."
    if released:
        return "Its rows show no number until then."
    moment = "hand out" if m.block == "assignments" else "release"
    if m.fires is None:
        if m.block == "assignments":
            return "It cannot be handed out until it has one."
        return f"The {moment} has no date yet, and will be skipped until this is fixed."
    if m.fires <= now:
        return f"The {moment} on {_day(m.fires)} was skipped."
    return f"The {moment} on {_day(m.fires)} will be skipped."


def _schedule_fix(org: str, key: str, line: int | None) -> dict:
    return {
        "repo": f"{org}/{schedule.CONFIG_REPO}",
        "path": schedule.SCHEDULE_PATH,
        "line": line,
        "screen": "schedule",
        "entry": key,
    }


def _entry_lines(sched: schedule.Schedule, key: str) -> dict[str, int]:
    if key in sched.assignments:
        return sched.assignments[key].lines
    return next((r.lines for r in sched.releases if r.label == key), {})


def number_problems(facts: SemesterFacts, now: datetime) -> list[dict]:
    """Decision 0020 rules 2 and 4: `number:<kind>:<key>` for an entry of a numbered
    kind with no number, dated at the release or hand-out it stops (so the Dashboard
    counts it in that week), and `number:<kind>:<n>` for a number two entries share."""
    aliases = lambda repo: facts.aliases.get(repo, {})
    released = {
        r.label
        for r in facts.sched.releases
        if r.deploy and all(_dest_present(facts, d) for d in r.deploy)
    }
    out = []
    for m in unnumbered(facts.sched, aliases):
        shipped = m.block == "releases" and m.key in released
        problem = {
            "id": f"number:{m.kind}:{_slugify(m.key)}",
            "scope": "semester",
            "stage": "K4",
            "kind": NOT_NUMBERED,
            "text": f"Give {m.key} a number.",
            "stops": _number_stops(m, now, shipped),
            "fix": _schedule_fix(facts.org, m.key, m.line),
        }
        # It holds its entry back while there is something still to copy or hand out.
        if not shipped and (m.block == "assignments" or m.copies):
            problem["release"] = m.key
            if m.fires is not None:
                problem["when"] = m.fires.isoformat()
        out.append(problem)
    for kind, n, keys in duplicate_numbers(facts.sched, aliases):
        out.append(
            {
                "id": f"number:{kind}:{n}",
                "scope": "semester",
                "stage": "K4",
                "kind": DUPLICATE_NUMBER,
                "text": f"{duplicate_text(kind, n, keys)}.",
                "stops": "Students see the same number more than once.",
                "fix": _schedule_fix(
                    facts.org,
                    keys[-1],
                    line_of(_entry_lines(facts.sched, keys[-1]), "number"),
                ),
            }
        )
    return out


MAIN_EDITED = "MAIN_EDITED"
# The branch a template hands out: the starter students receive (decision 0028).
TEMPLATE_MAIN = "main"


def main_edited_problem(t: TemplateFacts, org: str) -> dict:
    """Decision 0028 rule 2: a derived template's `main` carries a commit Derive did not
    make, which the next Derive overwrites."""
    return {
        "id": f"template:{_slugify(t.repo)}:{MAIN_EDITED}",
        "scope": "course",
        "stage": "C5",
        "kind": MAIN_EDITED,
        "text": "main is derived; edit the solution branch and derive again.",
        "stops": (
            f"{t.main_edited} on main is not what Derive wrote; the next Derive "
            f"overwrites it."
        ),
        "fix": {
            "repo": f"{org}/{t.repo}",
            "path": "",
            "line": None,
            "screen": "template",
            "entry": t.repo,
        },
    }


def template_problems(
    t: TemplateFacts, org: str, moments: Mapping[str, Moment] | None
) -> list[dict]:
    """A template's own problems beside its grading_config.yml faults: NOT_MIGRATED
    without the topic, MAIN_EDITED on a derived one. Each bites at the template's first
    dated citing hand-out (`moments`); one no dated hand-out cites is `later`."""
    if not t.topic:
        out = [template_problem(t, org)]
    else:
        out = [main_edited_problem(t, org)] if t.main_edited else []
    m = (moments or {}).get(t.repo)
    for p in out:
        if m is not None and m.when is not None:
            p["when"] = m.when.isoformat()
        else:
            p[UNDATED_LATER] = True
    return out


BRIEF_TODO = f"The brief ({README_FILE}) is not written yet."


def template_todo_problems(
    t: TemplateFacts, org: str, moments: Mapping[str, Moment] | None
) -> list[dict]:
    """Decision 0034: a template's needed to-do (`brief`, `starter`) is a problem once a
    dated hand-out cites the template - `when` is that hand-out (`moments`), `release` its
    assignment where the file knows it; `_tier` decides whether it bites yet. The to-do
    stays in `course.todo[]` as well."""
    m = (moments or {}).get(t.repo)
    if not t.topic or m is None or m.when is None:
        return []
    day = f"The hand-out on {_day(m.when)} would give students"
    parts = []
    if not _written(t.readme):
        parts.append(("brief", BRIEF_TODO, f"{day} a placeholder brief."))
    if t.starter_todo:
        what = (
            "no starter files"
            if t.starter == STARTER_HANDWRITTEN
            else "a starter out of step with the solution"
        )
        parts.append(("starter", t.starter_todo, f"{day} {what}."))
    out = []
    for check, text, stops in parts:
        problem = {
            "id": f"template:{_slugify(t.repo)}:{check}",
            "scope": "course",
            "stage": "C5",
            "kind": check.upper(),
            "text": text,
            "stops": stops,
            "fix": {
                "repo": f"{org}/{t.repo}",
                "path": "",
                "line": None,
                "screen": "template",
                "entry": t.repo,
            },
            "when": m.when.isoformat(),
        }
        if m.release:
            problem["release"] = m.release
        out.append(problem)
    return out


def _record(text: str | None) -> dict | None:
    """Derive's `.system/starter.json`, or None when it is absent or not its shape."""
    try:
        record = json.loads(text) if text else None
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        return None
    return record


def starter_check(
    starter: str,
    solution: Mapping[str, str],
    main: Mapping[str, str],
    record_text: str | None,
) -> tuple[str | None, str | None]:
    """`(to-do, hand-edited path)` for one template's starter (decision 0028), off the two
    trees (`{path: sha}`) and Derive's record. Hand-written: `main` holds something other
    than the brief. Derived: a source to derive from, a record, every recorded blob still
    on `main` (else that path is a hand edit), and the solution unchanged since."""
    if starter == STARTER_HANDWRITTEN:
        filled = any(p != README_FILE and not p.startswith(".") for p in main)
        return (None if filled else "main has no starter files yet."), None
    if not derivable_sources(list(solution)):
        return f"There is nothing under {SOLUTION_DIR}/ to derive a starter from.", None
    record = _record(record_text)
    if record is None:
        return "Derive has not been run yet.", None
    edited = next(
        (p for p, sha in sorted(record["files"].items()) if main.get(p) != sha), None
    )
    if record.get("solution_tree") != solution.get(SOLUTION_DIR):
        return "The solution changed since the last Derive; derive again.", edited
    return None, edited


def template_state(t: TemplateFacts) -> str:
    """C5, per template: `problem` until the migration gives it the topic, while its
    grading_config.yml will not grade as written, or while a derived `main` carries a
    hand edit; `ready` once its README is written and its starter is in place (derived:
    `starter_check` finds nothing to do), `todo` before."""
    if t.faults or template_problems(t, "", None):
        return PROBLEM
    return "ready" if _written(t.readme) and not t.starter_todo else TODO


def course_admin_count(meta: dict) -> int:
    faculty = sync_faculty.parse_faculty_from_meta(meta) if meta else {}
    today = date.today().isoformat()
    return len(
        sync_faculty.desired_team_members(faculty, today).get(COURSE_ADMIN_TEAM, set())
    )


# `dsl-course.yml`'s list of optional setup steps and to-dos the course has set aside
# (decision 0032).
SET_ASIDE_KEY = "set_aside"


def set_aside_ids(meta: dict) -> frozenset[str]:
    """The ids `dsl-course.yml` sets aside: its `set_aside:` list, the strings in it. Any
    other shape, or an absent key, sets nothing aside; an unknown id is never a fault, it
    simply matches nothing (decision 0032 rule 2)."""
    raw = meta.get(SET_ASIDE_KEY)
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(x.strip() for x in raw if isinstance(x, str) and x.strip())


def stage_set_aside(stages: dict[str, str], aside: frozenset[str]) -> dict[str, bool]:
    """Decision 0032: a suggested stage that is not done and that the course lists. A
    needed stage's id, or a done stage's, is ignored."""
    return {
        s: COURSE_STAGE_NEED[s] == SUGGESTED and state != DONE and s in aside
        for s, state in stages.items()
    }


def course_verdict(
    stages: dict[str, str],
    why: dict[str, str],
    todo: list[dict],
    problems: list[dict],
    set_aside: Mapping[str, bool],
) -> dict:
    """The course's `verdict` (decision 0034) over its course-scope problems (distinct),
    its needed stages (C1-C3) and the suggested items not set aside. It has no
    `coming_up`: a template's `later` problems are the citing semester's Coming up."""
    needed = [
        why[s]
        for s, state in stages.items()
        if COURSE_STAGE_NEED[s] == NEEDED and state not in (DONE, PROBLEM)
    ]
    suggestions = sum(
        COURSE_STAGE_NEED[s] == SUGGESTED and state != DONE and not set_aside.get(s)
        for s, state in stages.items()
    ) + sum(t["need"] == SUGGESTED and not t["set_aside"] for t in todo)
    return verdict([p for p in problems if p["scope"] == "course"], needed, suggestions)


def website_problem(org: str) -> dict:
    """C6: `opencourse.yml` does not parse while the public website is on (decision 0034):
    the daily update stands still and students see a stale site."""
    return {
        "id": f"website:{_slugify(OPENCOURSE_FILE)}:WEBSITE",
        "scope": "course",
        "stage": "C6",
        "kind": "WEBSITE",
        "text": f"The public website settings file ({OPENCOURSE_FILE}) does not parse.",
        "stops": "The public website is not updated until it parses.",
        "fix": {
            "repo": f"{org}/{OPENCOURSE_REPO}",
            "path": OPENCOURSE_FILE,
            "line": None,
            "screen": "website",
            "entry": None,
        },
    }


def render_course(
    facts: CourseFacts,
    now: datetime,
    rolled_up: list[dict] = (),
    moments: Mapping[str, Moment] | None = None,
) -> tuple[dict, list[dict]]:
    """`(the course block, the course-side problems)`. `rolled_up` is the course-scope
    problems a semester has already built from its own view of the templates it cites, so the
    course block inside a semester's file marks the same stages its problem list does.
    `moments` dates the template problems (`_handouts`): this semester's hand-outs inside a
    semester's file, every live semester's in the course file (`merge_moments`); a
    template no dated hand-out cites is `later`, and a needed template to-do a dated
    hand-out cites is a problem too (`template_todo_problems`). Every other course
    problem has no moment: `now`.

    Stage predicates (lifecycle, course stages):
    - C1 the org resolves;
    - C2 `.github` holds dsl-course.yml and its seeded workflows;
    - C3 dsl-course.yml names the course and its code, and one course admin;
    - C4 any materials repo `ready` (decision 0022: the others are to-dos);
    - C5 any template `ready`;
    - C6 `opencourse.yml` turns the public website on and its repo exists.
    C1-C3 are needed, C4-C6 suggested (`stage_need`, decision 0034); the description is a
    suggested to-do. `verdict` rolls them up with the `now`/`soon` course problems and the
    to-dos; `ready` is its `ready`. A suggested stage may be set aside in `dsl-course.yml`'s
    `set_aside:` (decision 0032), and `stage_set_aside` marks those still not done."""
    problems = [problem_from_fault(f, facts.org, now) for f in facts.faults]
    if facts.website_unusable and facts.website_on:
        problems.append(website_problem(facts.org))
    problems += [
        materials_problem(m, facts.org) for m in facts.materials if not m.topic
    ]
    problems += [p for m in facts.materials for p in kindless_problems(m, facts.org)]
    for t in facts.templates:
        problems += template_problems(t, facts.org, moments)
        problems += template_todo_problems(t, facts.org, moments)
    for t in facts.templates:
        problems += [problem_from_fault(f, facts.org, now, moments) for f in t.faults]
    _tier(problems, now)
    meta = facts.meta
    aside = set_aside_ids(meta)
    checks = {m.repo: materials_checks(m) for m in facts.materials}
    todo = course_checks(facts, checks)
    done = {stage: todo[stage] is None for stage in COURSE_STAGES}
    standing = _distinct([*rolled_up, *problems])
    stages = _stage_states(COURSE_STAGES, done, standing)
    why = stage_why(stages, todo, standing)
    set_aside = stage_set_aside(stages, aside)
    todos = course_todo(facts, checks, aside)
    judged = course_verdict(stages, why, todos, standing, set_aside)
    block = {
        "org": facts.org,
        "name": str(meta.get("course_name") or ""),
        "code": str(meta.get("course_code") or ""),
        "stages": stages,
        "stage_why": why,
        "stage_need": dict(COURSE_STAGE_NEED),
        # Decision 0032, derived from `stage_need`: which stages may be set aside, and are.
        "stage_optional": {s: COURSE_STAGE_NEED[s] == SUGGESTED for s in stages},
        "stage_set_aside": set_aside,
        "verdict": judged,
        "ready": judged["state"] == READY,
        "materials": [
            {
                "repo": m.repo,
                "state": materials_state(m, checks[m.repo]),
                "checks": checks[m.repo],
            }
            for m in facts.materials
        ],
        "templates": [
            {
                "repo": t.repo,
                "slug": t.repo,
                "state": template_state(t),
                "starter": t.starter,
            }
            for t in facts.templates
        ],
        "semesters": list(facts.registry),
        "todo": todos,
    }
    return block, problems


def _todo(need: str, aside: frozenset[str], **entry) -> dict:
    """One to-do: `need`, plus the two fields decision 0032 reads - `optional` (it is
    suggested) and `set_aside` (a suggested one the course lists). Every entry names its
    `check`: the materials check, or `brief` / `starter` / `description` / `home` /
    `archive_date` / `email`."""
    return {
        **entry,
        "need": need,
        "optional": need == SUGGESTED,
        "set_aside": need == SUGGESTED and entry["id"] in aside,
    }


def course_todo(
    facts: CourseFacts,
    checks: Mapping[str, list[dict]],
    aside: frozenset[str] = frozenset(),
) -> list[dict]:
    """Decision 0022 rule 3: work started and not finished, one entry per missing item -
    every unmet check of a materials repo (the suggested ones of a ready repo too), every
    template whose brief is the placeholder or whose starter is not in place, and the
    course's description. Course first, then materials, then by repo, then check order.
    Never a problem: a repo without its topic is the migration's problem, not a to-do.
    `need` (decision 0034): a materials check's own; a template's brief and starter are
    needed; the description is suggested. A needed to-do's id in `aside` is ignored.
    `checks`: each materials repo's `materials_checks`. A template's to-do is undated: a
    dated hand-out citing it makes it a problem as well (`template_todo_problems`)."""
    out = []
    if not facts.meta.get("course_description") and facts.meta:
        out.append(
            _todo(
                SUGGESTED,
                aside,
                id="course:description",
                kind="course",
                check="description",
                repo=".github",
                text="Course details have no description yet.",
                screen="details",
            )
        )
    for m in facts.materials:
        if not m.topic:
            continue
        out += [
            _todo(
                c["need"],
                aside,
                id=f"materials:{_slugify(m.repo)}:{c['id']}",
                kind="materials",
                check=c["id"],
                repo=m.repo,
                text=c["why"],
                screen="materials",
                entry=m.repo,
            )
            for c in checks[m.repo]
            if not c["done"]
        ]
    out += [
        _todo(
            NEEDED,
            aside,
            id=f"template:{_slugify(t.repo)}:brief",
            kind="template",
            check="brief",
            repo=t.repo,
            text=BRIEF_TODO,
            screen="template",
            entry=t.repo,
        )
        for t in facts.templates
        if t.topic and not _written(t.readme)
    ]
    out += [
        _todo(
            NEEDED,
            aside,
            id=f"template:{_slugify(t.repo)}:starter",
            kind="template",
            check="starter",
            repo=t.repo,
            text=text,
            screen="template",
            entry=t.repo,
        )
        for t in facts.templates
        if t.topic and (text := t.starter_todo)
    ]
    order = {"course": 0, "materials": 1, "template": 2}
    return sorted(out, key=lambda e: (order[e["kind"]], e["repo"]))


def course_checks(
    facts: CourseFacts, checks: Mapping[str, list[dict]]
) -> dict[str, str | None]:
    """Each course stage's predicate: None when it is met, else the one sentence that
    says what is still missing (lifecycle, course stages). `checks`: each materials
    repo's `materials_checks`."""
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
    elif course_admin_count(meta) == 0:
        out["C3"] = "No course admin is declared in course details yet."
    # Decision 0022 rule 2: done once ANY repo is ready; the rest are to-dos.
    if not facts.materials:
        out["C4"] = "There is no materials repo yet."
    elif not any(
        materials_state(m, checks[m.repo]) == "ready" for m in facts.materials
    ):
        out["C4"] = _nearest_materials_why(facts.materials, checks)
    if not facts.templates:
        out["C5"] = "There is no assignment template yet."
    elif not any(template_state(t) == "ready" for t in facts.templates):
        out["C5"] = "No assignment template is ready yet."
    if facts.website_unusable:
        out["C6"] = "The public website settings file does not parse."
    elif not facts.website_on:
        out["C6"] = "The public website is off; it is optional."
    elif not facts.public_site:
        out["C6"] = "The public website is on but not published yet."
    return out


def semester_weeks(
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


def _dest_present(facts: SemesterFacts, d: schedule.Deploy) -> bool:
    paths = facts.dest_paths.get(d.semester_dest_repo) or set()
    dest = deploy_dest(d)
    return bool(paths) if is_repo_root(dest) else dest in paths


def release_state(
    release: schedule.Release,
    facts: SemesterFacts,
    faults: list[ConfigFault],
    now: datetime,
    numbered: bool = True,
) -> str:
    """`planned | will_be_skipped | released | late` (lifecycle, per scheduled release).

    released - every copy is on the destination's default branch (whenever it got there:
    an early release is released); late - a copy whose moment has passed is not there (a
    copy that landed and another still to come is not late); will_be_skipped - automation
    cannot perform it as written (a source not found or held back, or an entry that needs
    a number and has none); planned otherwise. An entry with nothing to copy is released
    once its moment has passed."""
    if release.deploy and all(_dest_present(facts, d) for d in release.deploy):
        return "released"
    if any(not _dest_present(facts, d) for d in release.due_deploys(now)):
        return "late"
    if faults or (release.deploy and not numbered):
        return "will_be_skipped"
    if not release.deploy and release.when is not None and release.when <= now:
        return "released"
    return "planned"


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


def handed_out(
    name: str,
    entry: schedule.AssignmentEntry,
    templates: frozenset[str],
    now: datetime,
) -> bool:
    """Whether the assignment `name` (semester-side) is out: its semester template exists
    (`handed_out_assignments`, whatever route fired the hand-out) or its
    `handout_datetime` has passed. One answer for both consoles: the student file's
    `handed_out` and the instructor's `open`."""
    pinned = entry.handout_datetime is not None and entry.handout_datetime <= now
    return name in templates or pinned


def assignment_state(
    now: datetime,
    entry: schedule.AssignmentEntry,
    cutoff: datetime | None,
    spec: grades.GradingSpec,
    units: int,
    returned: bool,
    out: bool = False,
) -> str:
    """`declared | teams_forming | blocked | open | late_window | marking | returned`
    (lifecycle, per assignment), by the assignment's own clock:

    returned once marks have gone back for every unit; marking from the cutoff;
    late_window between the due date and the cutoff; before the due date, open once the
    work is out: any unit's copy handed out, or, for a shape with no repo per unit
    (`external`, `shared_dropbox_repo`), the assignment `handed_out` (`out`). A group
    assignment past its hand-out moment with no copy yet is teams_forming while students
    choose their own teams, blocked while the teaching team has to assign them; declared
    before any of that. Which of the two is the EFFECTIVE `team_formation` (the
    cascade's, `self_select` unless somebody declared `assigned`)."""
    if returned:
        return "returned"
    if cutoff is not None and now >= cutoff:
        return "marking"
    if now >= entry.due_datetime:
        return "late_window"
    if units > 0 or (out and not spec.creates_unit_repos):
        return "open"
    if spec.is_group and entry.handout_datetime and now >= entry.handout_datetime:
        return (
            "teams_forming"
            if spec.team_formation_resolved == SELF_SELECT
            else "blocked"
        )
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


def run_settings(spec: grades.GradingSpec) -> dict[str, dict]:
    """Each run setting's effective value and the layer it came from (`settings`), so the
    console shows "5 days, this course's default" without a cascade of its own."""
    sources = dict(spec.sources)
    return {
        key: {"value": getattr(spec, key), "source": sources.get(key, "institution")}
        for key in settings.RUN_KEYS
    }


def render_assignments(
    facts: SemesterFacts, problems: list[dict], now: datetime
) -> list[dict]:
    """One row per assignment the schedule declares. `problem`: a `now` or `soon` problem
    names it or its template (a `later` one is coming up, not wrong yet)."""
    flagged = _problem_entries(_biting(problems))
    templates = handed_out_assignments(list(facts.listing.values()))
    sheet_specs = {}
    for k, e in facts.sched.assignments.items():
        name = schedule.semester_name(k, e)
        gspec = facts.specs.get(k, grades.GradingSpec())
        sheet_specs[name] = grades.sheet_spec(
            facts.sched, k, name, gspec, gspec.is_group
        )
    rows = []
    for slug, entry in facts.sched.assignments.items():
        spec = facts.specs.get(slug, grades.GradingSpec())
        name = schedule.semester_name(slug, entry)
        cutoff = schedule.grading_cutoff_datetime(facts.sched, slug)
        units = len(assignment_rows(facts.listing, name)) if facts.listing else 0
        sheet, sspec = facts.sheets.get(name), sheet_specs.get(name)
        filled, on_sheet, submitted = sheet_counts(sheet, sspec)
        total = on_sheet or units
        # The automatic return's once-only record is written only when every unit went
        # back, so it settles the question: a later sheet commit that changes no gradebook
        # leaves `distributed_at` where it was, and the comparison would say marking.
        returned = grades.marks_return_record(name) in facts.config_paths or (
            total > 0
            and filled == total
            and marks_returned(
                sheet,
                sspec,
                facts.returned_at,
                facts.sheet_changed.get(name),
            )
        )
        solution = entry.solution_datetime
        rows.append(
            {
                "slug": slug,
                # Its number (decision 0020), or None: the console never counts one.
                "number": own_number(entry.number, slug),
                "title": facts.titles.get(slug) or spec.title or slug,
                "template": entry.course_source_repo,
                "state": assignment_state(
                    now,
                    entry,
                    cutoff,
                    spec,
                    units,
                    returned,
                    handed_out(name, entry, templates, now),
                ),
                "handout": _iso(entry.handout_datetime),
                "due": _iso(entry.due_datetime),
                "grading_cutoff_datetime": _iso(cutoff),
                "solution_shown": _iso(solution),
                # Set before the late cutoff: the scheduler holds it until then.
                "solution_held_until": _iso(
                    schedule.solution_held_until(facts.sched, slug)
                ),
                "units": units,
                "submissions": submitted,
                "teams": len(teams.teams_for(facts.teams, slug))
                if spec.is_group
                else None,
                "marks": {"filled": filled, "total": total},
                "returned": returned,
                "problem": slug in flagged or entry.course_source_repo in flagged,
                "settings": run_settings(spec),
            }
        )
    return rows


def render_releases(
    facts: SemesterFacts, faults: list[ConfigFault], now: datetime
) -> list[dict]:
    """One row per `releases:` entry. Source and destination are the entry's FIRST copy."""
    rows = []
    aliases = lambda repo: facts.aliases.get(repo, {})
    # The number the site gives each row (`schedule_plan.site_rows`); None for a row it
    # does not number (silent, attached readings, undated).
    numbers = {
        sr.row.key: sr.number for sr in site_rows(planned_rows(facts.sched, aliases))
    }
    held = {m.key for m in unnumbered(facts.sched, aliases) if m.block == "releases"}
    for r in facts.sched.releases:
        own = [f for f in faults if f.is_source and f.where == f"releases.{r.label}"]
        first = r.deploy[0] if r.deploy else None
        kind, _ = entry_kind(r, aliases)
        rows.append(
            {
                "id": r.label,
                "when": _iso(r.when),
                # Inferred when the entry declares none (`schedule_plan.entry_kind`).
                "kind": kind,
                "number": numbers.get(r.label),
                "title": r.title,
                "state": release_state(r, facts, own, now, r.label not in held),
                "source": {
                    "repo": first.course_source_repo,
                    "path": first.course_source_path,
                }
                if first
                else None,
                "dest": {"repo": first.semester_dest_repo, "path": deploy_dest(first)}
                if first
                else None,
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
    semester's zone: releases, hand-outs, due dates and events. The start is included and the
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
                        "kind": kind,
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
            "exam" if ev.kind == "exam" else "event",
            ev.label,
            ev.title,
            "planned",
        )
    return [row for _, row in sorted(items, key=lambda p: p[0])]


def render_operations(outcomes: list[dict]) -> list[dict]:
    """The most recent operations first, off their private outcome files. Each row
    opens its run, so an outcome recorded outside a workflow run (no `run_id`) is left
    out."""
    keep = ("run_id", "op", "conclusion", "summary", "finished")
    rows = [
        {k: o.get(k) for k in keep}
        for o in outcomes
        if isinstance(o, dict) and o.get("op") and isinstance(o.get("run_id"), int)
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


def faculty_window_faults(
    sched: schedule.Schedule, windows: list[team_formation.Window] | None
) -> list[ConfigFault]:
    """The team-formation faults that are the teaching team's (decision 0031 rule 3).

    While a window is open, students still without a team are theirs to fix: joining a team
    is a student's step, like joining the course, so it is not a Problem on the overview.
    The mail `team_formation.notify_windows` sends is what reaches them, and the schedule
    digest still lists the fault. Once the window has SHUT, nobody but an instructor can
    place the students left over, so that fault stays."""
    shut = [w for w in windows or [] if w.shut]
    return team_formation.window_faults(sched, shut) or []


def semester_checks(
    facts: SemesterFacts, course: CourseFacts, instructors: int
) -> dict[str, str | None]:
    """Each semester stage's predicate: None when it is met, else the one sentence that
    says what is still missing (lifecycle, semester stages)."""
    sched = facts.sched
    students = facts.students or []
    site = pages_repo(facts.org)
    out: dict[str, str | None] = dict.fromkeys(SEMESTER_STAGES)
    if not facts.listing:
        out["K1"] = "The semester org could not be read, or holds no repos yet."
    missing = [
        r for r in (schedule.CONFIG_REPO, JOIN_REPO, site) if r not in facts.listing
    ]
    if missing:
        out["K2"] = f"The semester has no {' or '.join(missing)} repo yet."
    elif facts.org.casefold() not in {c.casefold() for c in course.registry}:
        out["K2"] = "The course does not list this semester yet."
    if facts.people is None:
        out["K3"] = f"{sync_faculty.SEMESTER_PEOPLE_PATH} could not be read."
    elif instructors == 0:
        out["K3"] = (
            f"No instructor is declared in {sync_faculty.SEMESTER_PEOPLE_PATH} yet."
        )
    if schedule.SCHEDULE_PATH not in facts.config_paths:
        out["K4"] = f"There is no {schedule.SCHEDULE_PATH} yet."
    elif sched.unparseable:
        out["K4"] = f"{schedule.SCHEDULE_PATH} does not parse."
    elif sched.semester_start is None or sched.semester_end is None:
        out["K4"] = "The schedule has no semester start or end date yet."
    elif not (sched.releases or sched.assignments):
        out["K4"] = "The schedule plans no releases or assignments yet."
    # Decision 0034: the codes not sent yet are the Students panel's meter, the site's
    # home page and the archive date are suggested to-dos (`semester_todo`).
    if not students:
        out["K5"] = "The roster has no students yet."
    if site not in facts.listing:
        out["K6"] = "The semester has no student site yet."
    return out


def semester_todo(
    facts: SemesterFacts, aside: frozenset[str] = frozenset()
) -> list[dict]:
    """Decision 0034: the semester's suggested to-dos - the student site's home page still
    the placeholder, no archive date in the schedule, instructor entries with no email (a
    count, never a handle). None of them stops automation or misleads a student."""
    out = []
    site = pages_repo(facts.org)
    if site in facts.listing and not _written(facts.site_home):
        out.append(
            _todo(
                SUGGESTED,
                aside,
                id="site:home",
                kind="site",
                check="home",
                repo=site,
                text="The student site's home page is still the placeholder.",
                screen="site",
            )
        )
    sched = facts.sched
    if (
        not facts.archived
        and schedule.SCHEDULE_PATH in facts.config_paths
        and not sched.unparseable
        and not (sched.archive and sched.archive.when)
    ):
        out.append(
            _todo(
                SUGGESTED,
                aside,
                id="schedule:archive_date",
                kind="schedule",
                check="archive_date",
                repo=schedule.CONFIG_REPO,
                text="The schedule sets no archive date.",
                screen="schedule",
            )
        )
    missing = sync_faculty.without_email(facts.people or {}, date.today().isoformat())
    if missing:
        n = len(missing)
        out.append(
            _todo(
                SUGGESTED,
                aside,
                id="instructors:email",
                kind="instructors",
                check="email",
                repo=schedule.CONFIG_REPO,
                text=f"{plural(n, 'instructor entry', 'instructor entries')} in "
                f"{sync_faculty.SEMESTER_PEOPLE_PATH} ha{'s' if n == 1 else 've'} no "
                f"email, so they are not told about problems.",
                screen="instructors",
            )
        )
    return out


def semester_verdict(
    stages: dict[str, str],
    why: dict[str, str],
    todo: list[dict],
    problems: list[dict],
) -> dict:
    """The semester's `verdict` (decision 0034) over every problem in its file, its
    stages (K1-K6, all needed; `missing` comes from these only) and its suggested to-dos.
    `coming_up` counts the `later` problems, the course's template problems included."""
    needed = [why[s] for s, state in stages.items() if state not in (DONE, PROBLEM)]
    suggestions = sum(t["need"] == SUGGESTED and not t["set_aside"] for t in todo)
    later = sum(p["bites"] == LATER for p in problems)
    return verdict(problems, needed, suggestions, later)


def late_problems(
    facts: SemesterFacts, releases: list[dict], skip: set[str], now: datetime
) -> list[dict]:
    """Decision 0034: a release whose moment has passed and that has not gone out, with no
    other problem to say why (`skip`: the entries another problem already holds back, its
    `release`) - a missed run, a failed one. Stands now."""
    out = []
    late = {r["id"] for r in releases if r["state"] == "late"}
    for r in facts.sched.releases:
        if r.label not in late or r.label in skip:
            continue
        moment = min(
            d.deploy_datetime or r.when
            for d in r.due_deploys(now)
            if not _dest_present(facts, d)
        )
        out.append(
            {
                "id": f"schedule:{_slugify(r.label)}:LATE",
                "scope": "semester",
                "stage": "K4",
                "kind": "LATE",
                "release": r.label,
                "text": f"{r.label} was due {_day(moment)} and has not gone out.",
                "stops": "Students do not have it yet; release it now.",
                "fix": _schedule_fix(facts.org, r.label, None),
                "when": moment.isoformat(),
            }
        )
    return out


def unwritten_problems(
    facts: SemesterFacts, course_org: str, now: datetime
) -> list[dict]:
    """`SOURCE_UNWRITTEN` (decision 0034): a copy of a root SYLLABUS.md / README.md that is
    still the placeholder, which the release leaves out (`scheduler._stub_decisions`).
    Dated at the copy's moment; a copy already at its destination is past caring."""
    out = []
    for r in facts.sched.releases:
        for d in r.deploy:
            path = d.course_source_path.strip("/")
            if not facts.stubs.get((d.course_source_repo, path)):
                continue
            if _dest_present(facts, d):
                continue
            when = d.deploy_datetime or r.when
            if when is None:
                stops = "The release has no date yet."
            elif when <= now:
                stops = f"The release on {_day(when)} left it out."
            else:
                stops = f"The release on {_day(when)} will leave it out."
            problem = {
                "id": f"schedule:{_slugify(r.label)}:SOURCE_UNWRITTEN",
                "scope": "semester",
                "stage": "K4",
                "kind": "SOURCE_UNWRITTEN",
                "release": r.label,
                "text": (
                    f"Release {r.label} cites {path} in {d.course_source_repo}, which "
                    f"is still the placeholder."
                ),
                "stops": stops,
                "fix": {
                    "repo": f"{course_org}/{d.course_source_repo}",
                    "path": path,
                    "line": None,
                    "screen": "materials",
                    "entry": d.course_source_repo,
                },
            }
            if when is not None:
                problem["when"] = when.isoformat()
            else:
                problem[UNDATED_LATER] = True
            out.append(problem)
    return out


def semester_inputs(facts: SemesterFacts, course: CourseFacts) -> dict[str, str | None]:
    """The blob (or, for the sheets, tree) shas this status was computed from."""
    paths = facts.config_paths
    return {
        schedule.SCHEDULE_PATH: paths.get(schedule.SCHEDULE_PATH),
        ASSIGNMENTS_FILE: paths.get(ASSIGNMENTS_FILE),
        sync_faculty.SEMESTER_PEOPLE_PATH: paths.get(sync_faculty.SEMESTER_PEOPLE_PATH),
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
        SEMESTERS_PATH: paths.get(SEMESTERS_PATH),
    }


def render_course_file(
    course: CourseFacts, now: datetime, moments: Mapping[str, Moment] | None = None
) -> dict:
    """The COURSE document, for the public `.github`: counts, repo names and the problems
    that already stand in `.github`'s own digest issue. Nothing about a person. `moments`:
    the live semesters' hand-outs per template (`merge_moments`), which date the template
    problems and to-dos."""
    block, problems = render_course(course, now, moments=moments)
    return {
        "schema": STATUS_SCHEMA,
        "inputs": course_inputs(course),
        "horizon": horizon(),
        "course": block,
        "problems": _unique_ids(problems),
    }


def render_semester(course: CourseFacts, facts: SemesterFacts, now: datetime) -> dict:
    """The SEMESTER document, for the private semester-config.

    Problems: every fault in the semester's own files, the course's own two files (every
    semester pays for those), and the templates THIS semester's schedule cites - a template
    fault is shown on every semester it will affect, tagged `scope: course`. Each carries
    `bites` (decision 0034): `now`, `soon` (inside `horizon`) or `later`.

    Stage predicates (lifecycle, semester stages):
    - K1 the org resolves;
    - K2 semester-config, join and the site repo exist, and the course registry lists it;
    - K3 instructors.yml is read and grants at least one instructor;
    - K4 schedule.yml parses, the term's start and end are set, and it plans something;
    - K5 the roster has rows;
    - K6 the site repo exists.
    All are needed (`stage_need`). The suggested to-dos (`todo`) are the site's home
    page, the archive date and instructor emails. `verdict` rolls it all up.
    `live` is the finished marker's opposite (`discovery.semester_is_live`), not K1-K5."""
    sched = facts.sched
    today = now.astimezone(ZoneInfo(sched.timezone)).date()
    faults = [
        *facts.schedule_faults,
        *facts.people_faults,
        *facts.roster_faults,
        *facts.teams_faults,
        *facts.sheet_faults,
    ]
    moments = _handouts(sched)
    slugs = {k: a.handout_datetime for k, a in sched.assignments.items()}
    problems = [problem_from_fault(f, facts.org, now, moments, slugs) for f in faults]
    problems += [problem_from_fault(f, course.org, now) for f in course.faults]
    if course.website_unusable and course.website_on:
        problems.append(website_problem(course.org))
    problems += [
        problem_from_fault(f, course.org, now, moments) for f in facts.template_faults
    ]
    problems += number_problems(facts, now)
    problems += kindless_entry_problems(facts)
    problems += unwritten_problems(facts, course.org, now)
    problems += [
        materials_problem(m, course.org) for m in course.materials if not m.topic
    ]
    problems += [p for m in course.materials for p in kindless_problems(m, course.org)]
    for t in course.templates:
        problems += template_problems(t, course.org, moments)
        problems += template_todo_problems(t, course.org, moments)
    releases = render_releases(facts, facts.schedule_faults, now)
    # A late release another problem already holds back is not told twice.
    held = {p["release"] for p in problems if "release" in p}
    problems += late_problems(facts, releases, held, now)
    problems = _unique_ids(_tier(problems, now))
    course_block, _ = render_course(
        course, now, [p for p in problems if p["scope"] == "course"], moments
    )

    students = facts.students or []
    instructors, tas = _staff_counts(facts.people)
    site = pages_repo(facts.org)
    checks = semester_checks(facts, course, instructors)
    done = {stage: checks[stage] is None for stage in SEMESTER_STAGES}
    stages = _stage_states(SEMESTER_STAGES, done, problems)
    why = stage_why(stages, checks, problems)
    todo = semester_todo(facts, set_aside_ids(course.meta))
    week, weeks = semester_weeks(sched.semester_start, sched.semester_end, today)
    tag = semester_of(facts.org)
    assignments = render_assignments(facts, problems, now)
    stale = facts.site_last_update is None or (
        facts.config_last_update is not None
        and facts.site_last_update < facts.config_last_update
    )
    return {
        "schema": STATUS_SCHEMA,
        "inputs": semester_inputs(facts, course),
        # Decision 0034: the rolling window a problem dated inside is `soon` in.
        "horizon": horizon(),
        "course": course_block,
        "semester": {
            "org": facts.org,
            "key": tag,
            "label": semester_label(tag),
            "timezone": sched.timezone,
            "start": _iso(sched.semester_start),
            "end": _iso(sched.semester_end),
            "week": week,
            "weeks": weeks,
            "live": not facts.archived,
            # Its end has passed and it is not archived yet: "ended, not archived".
            "ended": not facts.archived
            and sched.semester_end is not None
            and sched.semester_end < today,
            "stages": stages,
            "stage_why": why,
            "stage_need": dict(SEMESTER_STAGE_NEED),
            "verdict": semester_verdict(stages, why, todo, problems),
            "todo": todo,
            "archive_date": _iso(sched.archive.when if sched.archive else None),
            # Decision 0034: each cited template's first hand-out here, which the course
            # tick reads to date its template problems (`gather_moments`).
            "template_moments": {repo: _iso(m.when) for repo, m in moments.items()},
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


def _declaration(org: str, repo: str) -> Declared:
    """`repo`'s `materials.yml`, or the defaults when it does not parse: the status is no
    place to stop over it, and the site sync names the fault."""
    try:
        return read_materials(org, repo)
    except Unusable:
        return Declared()


def _materials_facts(course_org: str, repo: str) -> MaterialsFacts:
    """A materials repo's C4 facts: its declared syllabus (markdown read, anything else
    only looked for), its top folders and declared kinds, its `.releaseignore`, and the
    unkinded top folders holding numbered folders. The top is listed (and each unkinded
    folder's own top), never the whole tree: a repo too large for one recursive listing
    must not fail the course's status."""
    declared = _declaration(course_org, repo)
    path = declared.syllabus
    top = top_level(course_org, repo)
    if path.lower().endswith((".md", ".markdown")):
        syllabus = get_file_content(course_org, repo, path)
    elif "/" in path:
        syllabus = "" if file_exists(course_org, repo, path) else None
    else:
        syllabus = "" if path in top else None
    folders = sorted(
        name for name, kind in top.items() if kind == "dir" and not name.startswith(".")
    )
    releaseignore = (
        get_file_content(course_org, repo, RELEASEIGNORE)
        if RELEASEIGNORE in top
        else None
    )
    withheld = listed((releaseignore or "").splitlines())
    # Only a folder no kind names, and that a release copies, is listed (one read each,
    # usually none): the rest cannot have lost their rows.
    numbered = tuple(
        f
        for f in folders
        if publishable(f)
        and not withheld.excludes(f, lambda _rel: True)
        and alias_kind(f, declared.kinds) is None
        and any(
            kind == "dir" and session_number(name) is not None
            for name, kind in top_level(course_org, repo, f).items()
        )
    )
    return MaterialsFacts(
        repo,
        syllabus,
        path,
        folders=tuple(folders),
        kinds=declared.kinds,
        releaseignore=releaseignore,
        numbered=numbered,
    )


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
    facts.registry = read_semester_registry(course_org, facts.faults)
    for name in sorted(listing):
        row = listing[name]
        if row.get("archived"):
            continue
        if is_materials_repo(row):
            facts.materials.append(_materials_facts(course_org, name))
        elif name.startswith(MATERIALS_REPO_PREFIX):
            # A materials repo by its old mark, the name: listed, and NOT_MIGRATED until
            # the migration's topic step gives it the topic (decision 0013).
            m = _materials_facts(course_org, name)
            m.topic = False
            facts.materials.append(m)
        elif is_assignment_template(row) or is_untopicked_template(row):
            # One without the topic is listed, and NOT_MIGRATED until the migration's
            # topic step gives it the topic (decision 0014).
            t = TemplateFacts(
                name,
                get_file_content(course_org, name, README_FILE),
                topic=is_assignment_template(row),
            )
            text = get_file_content(
                course_org, name, grades.GRADING_FILE, ref=SOLUTION_BRANCH
            )
            if text is not None:
                t.faults, _ = grades.grading_spec_faults(
                    name, name, course_org, text, None
                )
            if t.topic:
                _starter_facts(course_org, t, text)
            facts.templates.append(t)
    facts.public_site = pages_repo(course_org) in listing
    raw = None
    try:
        raw = load_opencourse(course_org)
        oc = None if raw is None else parse_opencourse(raw)
    except Unusable:
        # Decision 0034: a file that does not parse is a problem while the website is on -
        # published already, or the file still says `enabled: true`.
        facts.website_unusable = True
        facts.website_on = facts.public_site or (
            isinstance(raw, dict) and raw.get("enabled") is True
        )
    else:
        facts.website_on = bool(oc and oc.enabled)
    return facts


def _starter_facts(org: str, t: TemplateFacts, config: str | None) -> None:
    """Decision 0028's facts for one template, from the solution and `main` trees and,
    for a derived one, Derive's record: three reads, never a source file's content. With
    no `starter:` key a template reads as derived when `solution/` holds a derivable
    source (Derive itself reads the markers; the migration writes the key)."""
    solution = repo_path_shas(org, t.repo, SOLUTION_BRANCH)
    has_source = bool(derivable_sources(list(solution)))
    t.starter = declared_starter(config) or (
        STARTER_DERIVED if has_source else STARTER_HANDWRITTEN
    )
    main = repo_path_shas(org, t.repo, TEMPLATE_MAIN)
    record = (
        get_file_content(org, t.repo, STARTER_RECORD, ref=TEMPLATE_MAIN)
        if t.starter == STARTER_DERIVED and has_source
        else None
    )
    t.starter_todo, t.main_edited = starter_check(t.starter, solution, main, record)


def _outcomes(semester_org: str, paths: dict[str, str]) -> list[dict]:
    out = []
    for path in sorted(
        p for p in paths if p.startswith(f"{OUTCOMES_DIR}/") and p.endswith(".json")
    ):
        text = get_file_content(semester_org, schedule.CONFIG_REPO, path)
        try:
            out.append(json.loads(text or ""))
        except json.JSONDecodeError:
            continue
    return out


def _returned_at(semester_org: str) -> dict[str, datetime]:
    text = get_file_content(semester_org, schedule.CONFIG_REPO, grades.DISTRIBUTED_PATH)
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


def gather_semester(course_org: str, semester_org: str, now: datetime) -> SemesterFacts:
    """Read one semester, through the same loaders its digest issues are built by, so a
    problem here is the fault that issue lists, except an open team-formation window's
    (`faculty_window_faults`). A read that fails raises."""
    facts = SemesterFacts(org=semester_org)
    facts.listing = {r["name"]: r for r in list_org_repos(semester_org)}
    branch = default_branch(semester_org, schedule.CONFIG_REPO, fallback="main")
    facts.config_paths = repo_path_shas(semester_org, schedule.CONFIG_REPO, branch)
    sched = facts.sched = schedule.load(semester_org)
    if sched.instance_unparseable:
        # Every run setting is unknown, so nothing about an assignment can be computed
        # (its cutoff, its window, its marks): the status lists the ASSIGNMENTS problem,
        # which is in `sched.faults`, and no assignment until the file parses.
        sched.assignments = {}
    # The schedule.yml digest's own sources (`scheduler._preflight_sources`): the sources
    # the plan cites, the entries the parser dropped (assignments.yml's with them), the
    # team-formation windows somebody is still waiting on (None = the roster could not be
    # read), and the marks due but not all written. Of the windows, only the shut ones
    # (`faculty_window_faults`): an open one is the students' to act on.
    windows = team_formation.open_windows(course_org, semester_org, sched, now)
    facts.schedule_faults = [
        *schedule.source_faults(sched, course_org),
        *sched.faults,
        *faculty_window_faults(sched, windows),
        *grades.marks_due(course_org, semester_org, sched, now)[0],
    ]
    facts.people = sync_faculty.read_semester_people(semester_org, facts.people_faults)
    try:
        facts.students = roster.load(semester_org, facts.roster_faults)
    except Unusable:
        facts.students = None  # the header fault is already in roster_faults
    known = known_handles(facts.students) if facts.students is not None else None
    try:
        facts.teams = teams.load(semester_org, facts.teams_faults, known)
    except Unusable:
        facts.teams = {}
    # `grades.semester_sheet_faults`, with each sheet read once for the faults and the marks.
    sheet_specs = grades.sheet_specs(course_org, sched)
    sheet_texts = {
        name: get_file_content(
            semester_org, schedule.CONFIG_REPO, grades.sheet_path(name)
        )
        for name in sorted(sheet_specs)
    }
    for name, text in sheet_texts.items():
        if text is not None:
            facts.sheet_faults += grades.sheet_faults(name, text, sheet_specs[name])
    grades.grading_config_faults(
        course_org, semester_org, sched, facts.template_faults, facts.listing
    )
    for slug, entry in sched.assignments.items():
        facts.specs[slug] = grades.load_grading_spec(
            course_org, entry.course_source_repo, semester_org=semester_org, slug=slug
        )
        facts.titles[slug] = grades.assignment_title(
            course_org, entry.course_source_repo, facts.specs[slug], slug
        )
        name = schedule.semester_name(slug, entry)
        text = sheet_texts[name]
        if text:
            try:
                facts.sheets[name] = grades.parse_sheet(text)
            except grades.SheetUnreadable:
                pass  # its fault is in sheet_faults
            facts.sheet_changed[name] = last_commit_at(
                semester_org, schedule.CONFIG_REPO, grades.sheet_path(name)
            )
    facts.returned_at = _returned_at(semester_org)
    for repo in sorted(
        {d.course_source_repo for r in sched.releases for d in r.deploy}
    ):
        facts.aliases[repo] = dict(_declaration(course_org, repo).kinds)
    for repo in sorted(
        {d.semester_dest_repo for r in sched.releases for d in r.deploy}
    ):
        if repo in facts.listing:
            branch = default_branch(semester_org, repo, fallback="main")
            facts.dest_branches[repo] = branch
            facts.dest_paths[repo] = set(repo_tree(semester_org, repo, branch))
    # The root stubs the plan copies, each read once: a placeholder is left out.
    for repo, path in sorted(
        {
            (d.course_source_repo, d.course_source_path.strip("/"))
            for r in sched.releases
            for d in r.deploy
            if d.course_source_path.strip("/") in WITHHELD_ROOT_STUBS
        }
    ):
        facts.stubs[(repo, path)] = root_stub_unwritten(course_org, repo, path)
    site = pages_repo(semester_org)
    if site in facts.listing:
        facts.site_home = get_file_content(semester_org, site, SITE_HOME)
        facts.site_last_update = last_commit_at(semester_org, site)
    moments = [
        last_commit_at(semester_org, schedule.CONFIG_REPO, p)
        for p in (schedule.SCHEDULE_PATH, sync_faculty.SEMESTER_PEOPLE_PATH)
    ]
    facts.config_last_update = max((m for m in moments if m), default=None)
    members = get_team_members(semester_org, INSTRUCTORS_TEAM)
    if members is not None and facts.people is not None:
        want = sync_faculty.desired_team_members(
            facts.people, date.today().isoformat()
        ).get(INSTRUCTORS_TEAM, set())
        facts.staff_synced = {m.casefold() for m in members} == {
            w.casefold() for w in want
        }
    facts.outcomes = _outcomes(semester_org, facts.config_paths)
    return facts


def collect_course(course_org: str, now: datetime | None = None) -> dict:
    """The course document (`dsl.status/1`), for `.github/.system/status.json`."""
    now = now or datetime.now(UTC)
    course = gather_course(course_org)
    return render_course_file(course, now, gather_moments(course.registry, now))


def gather_moments(semesters: list[str], now: datetime) -> dict[str, Moment]:
    """Decision 0034: when each course template is first needed, across the course's
    running semesters - ONE read per registered semester, its own `status.json`
    (`semester.template_moments`), never its schedule: the course tick's cost must not grow
    with the schedules. `semesters.yml` lists names only, so an archived semester costs its
    read too; its file (frozen when it was archived) is passed over by its end date, as is
    one past its end and not archived yet. A file that is missing, unreadable, or written
    by an engine before this field cites nothing: its templates fall back to `later`.

    One tick behind: the course sees a schedule edit once the semester's own tick has
    rewritten its status.json (within the same dispatch, or the next quarter hour)."""
    many = []
    for org in semesters:
        try:
            text = get_file_content(org, schedule.CONFIG_REPO, STATUS_PATH)
            doc = json.loads(text) if text else {}
        except (RuntimeError, json.JSONDecodeError) as exc:
            log_err(f"  ! could not read {org}'s status.json ({type(exc).__name__})")
            continue
        many.append(_moments_from_status(doc, now))
    return merge_moments(many)


def _moments_from_status(doc: object, now: datetime) -> dict[str, Moment]:
    """The template moments a semester's `status.json` records, or none when it is not a
    running semester's (not live, or past its end) or carries none."""
    semester = doc.get("semester") if isinstance(doc, dict) else None
    if not isinstance(semester, dict) or not semester.get("live"):
        return {}
    try:
        tz = ZoneInfo(str(semester.get("timezone") or "UTC"))
        end = date.fromisoformat(str(semester["end"])) if semester.get("end") else None
        raw = semester.get("template_moments") or {}
        moments = {
            str(repo): Moment(datetime.fromisoformat(when) if when else None)
            for repo, when in raw.items()
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return {}
    if end is not None and end < now.astimezone(tz).date():
        return {}
    return moments


def collect_semester(
    course_org: str, semester_org: str, now: datetime | None = None
) -> dict:
    """The semester document (`dsl.status/1`), for `semester-config/.system/status.json`."""
    now = now or datetime.now(UTC)
    return render_semester(
        gather_course(course_org), gather_semester(course_org, semester_org, now), now
    )


def dumps(doc: dict) -> bytes:
    """The file's exact bytes: stable key order as built, two-space indent, one trailing
    newline - so two renders of one state are one blob and the writer makes no commit."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
