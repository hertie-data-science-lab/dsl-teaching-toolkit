"""dsl-course grades -- the grading sheet, and sending what a grader wrote in it.

A grader fills ONE file per assignment, `classroom-config/grading_sheets/<slug>.yml`, and
`distribute` fans it out:

    grading_sheets/<slug>.yml   (the grader types here; the toolkit owns only `info:`)
          |
          +--> a comment on each submission repo's Feedback issue
          |      team repos get TEAM-level feedback only - the whole team can read them
          +--> cohort/grades-<handle>   (private; student = read) grades.yml + README.md
          +--> classroom-config/cohort-gradebook.csv   (the registrar export, never logged)
          +--> an email saying there is something new to read (no marks in it)

Nothing is said twice: every comment carries a content hash and every send is recorded in
`gradebook/distributed.csv`, so a re-run after one correction reaches one student.

Usage:
    python3 -m dsl_course.grades distribute --cohort-org hertie-dsl-demo-f2026 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import textwrap
from collections import Counter
from collections.abc import Container
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

import yaml

from . import mailer, roster, schedule
from .access import FACULTY_READ_ACCESS, grant_faculty
from .course import (
    CONFIG_REPO,
    FEEDBACK_ISSUE_LABEL,
    FEEDBACK_ISSUE_MARKS,
    FEEDBACK_ISSUE_TITLE,
    GRADEBOOK_PREFIX,
    SOLUTION_BRANCH,
    feedback_issue_body,
    receipt_body,
    resolve_is_group,
    submission_repo,
)
from .discovery import (
    course_name_for_cohort,
    course_org_for_cohort,
    list_org_repos,
)
from .gh_contents import (
    blob_sha,
    dump_csv,
    get_file_content,
    put_file,
    put_files,
    read_csv,
)
from .ghcli import clone, gh
from .log import log, log_err, log_ok, log_person, log_step
from .repos import (
    add_collaborator,
    create_repo,
    ensure_label,
    repo_exists,
    set_repo_topics,
)

GRADES_DIR = "grades"  # RETIRED: the pre-sheet grade tables, still READ in transition
GRADEBOOK_DIR = (
    "gradebook"  # what has been sent (distributed.csv), beside the retired files
)
# RETIRED. The old per-student notification marker, named here for one reason only: the
# migration in `_read_distributed` reads it once and deletes it in the same commit that
# writes `distributed.csv`, which records every channel rather than just the email.
NOTIFIED_PATH = f"{GRADEBOOK_DIR}/notified.csv"
COHORT_CSV_NAME = "cohort-gradebook.csv"  # generated wide faculty-only glance view

# One assignment CSV row. `autograde_score` is the machine's passing-test count on both
# individual and group assignments; `manual_score` is the faculty & instructors' hand-marked
# part of an individual one. Group rows additionally carry the shared `team_score`, that
# member's private `individual_adjustment`, and the shared `team_comments`. `final_grade` is
# authoritative (stored explicitly so faculty & instructors own any rounding/combination).
# `autograde_score`/`manual_score` are faculty-internal working columns - they never appear in
# the student's gradebook. Values stay strings - a grade may be a letter, a percentage, or
# "+4" - we never coerce.
#
# Every name says its scope and its role outright: a marker opening the CSV in Excel reads the
# header, not this file, and the old `auto`/`manual`/`final` said neither which part of the
# mark they held nor who owned them.
GRADE_FIELDS = (
    "github_handle",
    "team",
    "autograde_score",
    "manual_score",
    "team_score",
    "individual_adjustment",
    "final_grade",
    "individual_comments",
    "team_comments",
)

# What a gradebook says before its student has been marked in anything. The legend names
# the keys `STUDENT_VIEW_KEYS` allows and no others: this is the first file a student opens,
# and promising them a team score or their own adjustment - neither of which a gradebook
# ever shows - contradicts the one thing the page is for. Replaced wholesale by
# `render_readme` on the first distribute.
_STARTER_README = (
    "# Your gradebook\n\n"
    "This private repository is accessible only to you. Grades and feedback for each "
    "piece of assessment appear in `grades.yml` as the course progresses.\n\n"
    "## What each field means\n\n"
    "| Field | Meaning |\n"
    "| --- | --- |\n"
    "| `final_grade` | Your mark for that assignment. This is the authoritative one. |\n"
    "| `score` | Individual assignments only: the marks behind that total. |\n"
    "| `feedback` | Your marker's feedback on your own work. |\n"
    "| `submitted`, `days_late`, `penalty` | When your work was recorded, and what any "
    "late days cost. |\n"
    "| `team` | Group assignments only: the team you submitted with. |\n"
    "| `team_comments` | Group assignments only: feedback shared with the whole team. |\n"
)


@dataclass
class GradeRow:
    github_handle: str = ""
    team: str = ""
    autograde_score: str = ""
    manual_score: str = ""
    team_score: str = ""
    individual_adjustment: str = ""
    final_grade: str = ""
    individual_comments: str = ""
    team_comments: str = ""


# --------------------------------------------------------------------------- pure core


# The pre-rename column names. A CSV still carrying them must never be parsed, because the
# rename left `github_handle`, `team` and `team_comments` untouched: an old row would parse
# PARTIALLY, keeping its handle while every renamed cell read blank. Nothing downstream can
# tell that apart from a legitimately sparse row, so a distribute would publish a gradebook
# with the marks missing - a green run that destroys a marker's work. Refusing to read the
# file is the only safe answer.
_RETIRED_GRADE_FIELDS = {
    "auto": "autograde_score",
    "manual": "manual_score",
    "team_grade": "team_score",
    "adjustment": "individual_adjustment",
    "final": "final_grade",
    "comments": "individual_comments",
}


class RetiredGradeHeader(Exception):
    """A grades CSV written against the pre-rename column names."""


def parse_grades(text: str) -> list[GradeRow]:
    """Parse one `grades/<assignment>.csv` into rows (blank/extra columns tolerated).

    Raises RetiredGradeHeader if the header uses the pre-rename names - see
    `_RETIRED_GRADE_FIELDS` for why that cannot be tolerated the way an unknown column is."""
    reader = read_csv(text, ("github_handle",), "grades CSV")
    stale = [f for f in (reader.fieldnames or []) if f.strip() in _RETIRED_GRADE_FIELDS]
    if stale:
        renames = ", ".join(f"{f} -> {_RETIRED_GRADE_FIELDS[f.strip()]}" for f in stale)
        raise RetiredGradeHeader(
            f"grades CSV uses retired column name(s): {renames}. Rename the header row and "
            f"re-run; reading it as-is would drop every mark in those columns."
        )
    return [
        GradeRow(**{f: (row.get(f) or "").strip() for f in GRADE_FIELDS})
        for row in reader
    ]


def render_yaml(book: dict) -> str:
    """Serialise one student's gradebook to YAML text (insertion order preserved)."""
    return yaml.safe_dump(book, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------------- the grading sheet

# `classroom-config/grading_sheets/<slug>.yml` is the ONE place a grader types. One file per
# assignment, one block per submission unit, created at handout and refreshed until the
# cutoff freezes it; the toolkit then distributes what it holds everywhere a grade goes.
#
# Declared vs derived is STRUCTURAL rather than conventional: everything the toolkit owns
# sits under `info:` or in the regenerated comment header, and everything else belongs to
# the grader, who may type it, retype it or delete it and never have it touched. Nothing
# from `grading_config.yml` or `schedule.yml` is copied in as data for the same reason - a
# fact a grader can edit but the toolkit ignores is worse than no fact at all - so the
# assignment's definition reaches the sheet only as comments, re-emitted on every write.
SHEETS_DIR = "grading_sheets"
INFO_KEY = "info"  # the toolkit-owned block inside a unit's entry
NOTES_KEY = "notes_not_shared_with_students"
INFO_COMMENT = "toolkit-owned, shown for information only - nothing is declared here"
_SCORE_COMMENT = "yours: the question names and maxima come from grading_config.yml"
_SEP = " · "  # what separates the facts on one header line
# Inline comments line up at one column across the whole sheet, so the maxima read as a
# column beside the marks rather than as ragged trailing text.
_COMMENT_COLUMN = 32
_HEADER_WIDTH = 93  # the ruled line, and the width the header prose wraps to


class SheetUnreadable(Exception):
    """A grading sheet whose YAML nobody can read - a grader mid-edit.

    Its own class because the answer to it is specific and is the same everywhere: LEAVE
    THE FILE ALONE. A parse that came back empty instead would read as "this assignment
    has no rows", and the next write would rebuild the sheet blank over a term's marking."""


def sheet_path(slug: str) -> str:
    """Where this assignment's grading sheet lives in `classroom-config`."""
    return f"{SHEETS_DIR}/{slug}.yml"


@dataclass(frozen=True)
class SheetSpec:
    """What the sheet needs to know about one assignment: its `grading_config.yml`
    definition, plus the two `schedule.yml` moments already rendered for a human to read.

    `questions` maps a question name to its maximum AS WRITTEN - text, never a number. The
    maxima are a display, and a course that writes `1.5` must see back what it wrote rather
    than this module's idea of how to print it."""

    slug: str
    title: str
    is_group: bool
    submit_external: bool = False
    questions: dict[str, str] | None = None
    late_window_days: int | None = None
    late_penalty_per_day: str | None = None
    autograde: bool = False
    due_display: str = ""
    cutoff_display: str = ""
    # The same two moments spelt out in full - `Sunday 4 October 2026, 23:59
    # (Europe/Berlin)`. The Feedback issue uses these: a student reads that line once and
    # has to act on it, where a grader scans the sheet's header and wants it short.
    due_long: str = ""
    cutoff_long: str = ""

    @property
    def container_key(self) -> str:
        """The sheet's top-level key: teams submit, or students do."""
        return "teams" if self.is_group else "submissions"

    @property
    def score_key(self) -> str:
        return "score_group" if self.is_group else "score_individual"

    @property
    def feedback_key(self) -> str:
        return "feedback_group" if self.is_group else "feedback_individual"


def _fresh_info(spec: SheetSpec) -> dict:
    """A blank `info:` block: every fact this assignment's toolkit will fill, and no other.
    `contributions` exists only where CONTRIBUTIONS.md does and `autograde` only where
    tests will run - an always-blank key is a question a grader keeps re-asking."""
    info: dict = {"submitted": None, "days_late": None}
    if spec.is_group:
        info["contributions"] = None
    if spec.autograde:
        info["autograde"] = None
    return info


def _blank_score(spec: SheetSpec):
    """The score cell: one blank per declared question, else a single blank scalar."""
    return {question: None for question in spec.questions} if spec.questions else None


def _blank_person() -> dict:
    """The three fields every individual carries, in both sheet shapes."""
    return {"adjustment_individual": None, "feedback_individual": None, NOTES_KEY: None}


def _fresh_block(spec: SheetSpec, members: list[str], info: dict | None = None) -> dict:
    """One unit's entry, brand new: the toolkit's facts first, then the grader's blanks.

    `info` is what the toolkit knows about this unit RIGHT NOW. A block created during a
    refresh - a student who onboarded after the handout, or a sheet the toolkit is writing
    for the first time - must arrive with the facts already derived; created blank and
    filled "on the next tick", a late onboarder's row would sit empty for a quarter of an
    hour with no way to tell it from a non-submission.

    `submit_external` means the work was handed in somewhere else, so there is no commit to
    time and no `info:` block at all - rather than one full of blanks, which reads as a
    toolkit that tried to fill it and failed."""
    block: dict = {}
    if not spec.submit_external:
        block[INFO_KEY] = _fresh_info(spec) | (info or {})
    block[spec.score_key] = _blank_score(spec)
    if spec.is_group:
        block[spec.feedback_key] = None
        block["members"] = {handle: _blank_person() for handle in members}
    else:
        block.update(_blank_person())
    return block


def new_sheet(spec: SheetSpec, units: list[tuple[str, list[str]]]) -> dict:
    """A brand-new sheet: every unit present from the moment of handout, every human field
    blank. A sheet that arrives complete is one a grader can start typing into as soon as
    anything is in, and one whose missing row is visibly missing.

    `units` is `[(unit key, member handles)]`: for an individual assignment the key IS the
    handle and the list holds only it; for a group one the key is the team name."""
    return {
        spec.container_key: {
            unit: _fresh_block(spec, members) for unit, members in units
        }
    }


def _merged_people(existing: object, members: list[str]) -> object:
    """A team's `members:` mapping brought up to date: everyone already there is kept
    exactly as found, and a member who joined the team since the last write is appended
    blank. Left alone entirely if the grader turned it into something else."""
    if not isinstance(existing, dict):
        return existing
    old = dict(existing)
    people = {
        handle: (old.pop(handle) if handle in old else _blank_person())
        for handle in members
    }
    people.update(old)  # a member who left the team keeps their marks, at the end
    return people


def _merged_block(
    spec: SheetSpec, block: dict, members: list[str], info: dict | None, frozen: bool
) -> dict:
    """One existing unit's entry, refreshed. `info:` is replaced wholesale by what this
    write DERIVED; every other key is carried over untouched, in the order the grader's
    file had it.

    `info is None` means this write derived nothing for this unit - a handout, any write
    before the due date, a unit that was not among this pass's targets. That is not the
    same as deriving blanks: the facts on the file are the last ones anybody looked up, and
    a write that did not look must not erase them. It used to, and the scheduler re-fires
    every handed-out assignment on every tick, so a sheet's `info:` was blanked four times
    an hour - permanently, once the cutoff had passed and nothing re-derived it."""
    merged: dict = {}
    if frozen or info is None:
        if INFO_KEY in block:
            merged[INFO_KEY] = block[INFO_KEY]  # nothing looked; nothing to replace it
    elif not spec.submit_external:
        merged[INFO_KEY] = _fresh_info(spec) | info
    for key, value in block.items():
        if key == INFO_KEY:
            continue
        merged[key] = _merged_people(value, members) if key == "members" else value
    return merged


def merge_sheet(
    existing: dict | None,
    spec: SheetSpec,
    units: list[tuple[str, list[str]]],
    info_updates: dict[str, dict],
    frozen: bool = False,
) -> dict:
    """The sheet on disk brought up to date without touching a word the grader wrote.

    `info:` is the toolkit's: it is RE-DERIVED from `info_updates` on every write, so a late
    push moves `submitted` and `days_late` under marks that are already there. Once `frozen`
    the cutoff's facts stand and an existing block is copied verbatim - that is what makes
    the freeze mean something.

    Everything else is the grader's and is kept exactly as found: their marks, their
    feedback, keys they invented, and blocks for units that have since left the cohort (a
    withdrawn student's marks are not ours to delete). Deleted keys are not re-added either
    - a grader who removed `notes_not_shared_with_students` meant it, and a file that grows
    the key back on every tick is one nobody can tidy.

    A unit the sheet has never seen gets a fresh blank block, so a late onboarder appears.
    Order follows `units`, with anything left over kept at the end.

    "Kept exactly as found" is meant literally, and covers the two shapes a grader's file
    takes while they are still typing in it: a unit whose entry is not a mapping at all
    (`team-alpha: TODO`) is copied through rather than merged into, and any key beside the
    container - a block that lost its indentation, a note somebody left at the top - is
    carried to the end instead of being dropped on the next tick."""
    container = (existing or {}).get(spec.container_key) or {}
    if not isinstance(container, dict):
        raise SheetUnreadable(f"`{spec.container_key}:` is not a mapping of units")
    old = dict(container)
    blocks: dict = {}
    for unit, members in units:
        block = old.pop(unit, None)
        if block is None:
            blocks[unit] = _fresh_block(spec, members, info_updates.get(unit))
        elif isinstance(block, dict):
            blocks[unit] = _merged_block(
                spec, block, members, info_updates.get(unit), frozen
            )
        else:
            blocks[unit] = block  # not ours to interpret, and not ours to delete
    blocks.update(old)
    kept = {
        key: value
        for key, value in (existing or {}).items()
        if key != spec.container_key
    }
    return {spec.container_key: blocks, **kept}


_NULL_TAG = "tag:yaml.org,2002:null"


def _null_only(resolvers: dict) -> dict:
    """`yaml_implicit_resolvers` with every rule but null removed."""
    return {
        first: [(tag, rx) for tag, rx in rules if tag == _NULL_TAG]
        for first, rules in resolvers.items()
    }


class _SheetLoader(yaml.SafeLoader):
    """The grading sheet's own loader: a blank cell is None, and EVERYTHING else a grader
    typed is the string they typed.

    YAML 1.1's implicit typing is wrong for a mark. It reads `010` as 8, `1:30` as 90,
    `+4` as 4, `yes` as True and a bare timestamp as a datetime - so a file a grader saved
    came back holding values they never wrote, and the toolkit rewrote their file to match.
    Only the null rule survives here, which is the one implicit type the sheet actually
    declares (`key:`, `~`, `null` all mean "not filled in"); every other plain scalar is a
    `str`. The arithmetic never needed the typing - `_decimal` parses the text - and this
    is what lets `adjustment_individual: +4` still say `+4` after a refresh.

    A SUBCLASS, like `_SheetDumper`: `yaml.safe_load` is called all over this package and
    must keep reading ordinary YAML."""


_SheetLoader.yaml_implicit_resolvers = _null_only(
    yaml.SafeLoader.yaml_implicit_resolvers
)


def parse_sheet(text: str) -> dict:
    """A grading sheet's YAML into a dict ({} when the file is empty).

    Raises `SheetUnreadable` rather than returning anything for a file that does not parse
    or is not a mapping. This is hand-typed YAML in a repo a grader edits in the browser,
    so a broken save is ordinary; what must never happen is the toolkit reading one as an
    empty sheet and writing a blank file back over it."""
    try:
        data = yaml.load(text, Loader=_SheetLoader) or {}
    except yaml.YAMLError as exc:
        raise SheetUnreadable(" ".join(str(exc).split())) from exc
    if not isinstance(data, dict):
        raise SheetUnreadable("the file is not a mapping")
    return data


class _SheetDumper(yaml.SafeDumper):
    """The grading sheet's own dumper.

    A SUBCLASS, deliberately: several modules here call `yaml.safe_dump`, and a representer
    registered on the shared `yaml.SafeDumper` would quietly change what all of them
    emit."""


# The loader's rules, on the way out as well as in. What decides whether a scalar needs
# quoting is whether reading it back would change it - so a dumper that knows `14` and `+4`
# and `2026-10-03T22:14+02:00` all come back as the strings they are writes them bare,
# exactly as the mock-up shows them, and quotes only the handful (`null`, `~`, an empty
# string) that the surviving null rule would still retype.
_SheetDumper.yaml_implicit_resolvers = _null_only(
    yaml.SafeDumper.yaml_implicit_resolvers
)


def _represent_sheet_str(dumper: yaml.SafeDumper, data: str):
    """Multi-line text as a literal block (`|`), so the paragraph a grader typed comes back
    as a paragraph rather than one escaped line they can neither read nor edit."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


def _represent_sheet_none(dumper: yaml.SafeDumper, _data: None):
    """A blank cell as `key:`, never `key: null`. This file is typed into by hand, and
    `null` reads as a value that has to be deleted before a mark can be written."""
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


def _represent_sheet_number(dumper: yaml.SafeDumper, data: float):
    """A number the toolkit computed - `info.days_late` - written as its plain text.

    The sheet is TEXT, both ways: its loader gives every plain scalar back as a `str`, so a
    value emitted under a number's tag would come back a different type than it went out,
    and the dumper would have to quote it to say so. Same bytes on the page, one type in
    the file."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data))


_SheetDumper.add_representer(str, _represent_sheet_str)
_SheetDumper.add_representer(type(None), _represent_sheet_none)
# bool is a subclass of int, and PyYAML dispatches on the exact type - so `True` keeps its
# own representer and is never written as `1`.
_SheetDumper.add_representer(int, _represent_sheet_number)
_SheetDumper.add_representer(float, _represent_sheet_number)


def _wrapped(text: str) -> list[str]:
    """`text` as comment lines, wrapped to the header width."""
    return [
        f"# {line}"
        for line in textwrap.wrap(
            text, _HEADER_WIDTH, break_long_words=False, break_on_hyphens=False
        )
    ]


def _points_clause(questions: dict[str, str]) -> str:
    """`50 points (Q1 15, Q2 15, Q3 10, Q4 10)` - with the total only when every maximum is
    a number, since `questions` holds whatever the course wrote."""
    listed = ", ".join(f"{name} {maximum}" for name, maximum in questions.items())
    maxima = [_decimal(maximum) for maximum in questions.values()]
    if None in maxima:
        return f"({listed})"
    return f"{_plain(sum(maxima, Decimal(0)))} points ({listed})"


def _config_facts(spec: SheetSpec) -> list[str]:
    """The assignment's definition as the two lines a grader reads before marking. Every
    one of them is edited in `grading_config.yml` or `schedule.yml`, never here."""
    shape = ["group assignment" if spec.is_group else "individual assignment"]
    if spec.questions:
        shape.append(_points_clause(spec.questions))
    if spec.submit_external:
        shape.append("submitted outside GitHub")
    shape.append("autograde on" if spec.autograde else "autograde off")
    timing = [f"due {spec.due_display}"] if spec.due_display else []
    if spec.submit_external:
        timing.append("no late arithmetic")
    elif spec.late_window_days and spec.cutoff_display:
        rate = spec.late_penalty_per_day
        timing.append(
            f"late work to {spec.cutoff_display}" + (f" at {rate}/day" if rate else "")
        )
    else:
        timing.append("no late work accepted")
    return [_SEP.join(line) for line in (shape, timing) if line]


def _auto_filled_sentence(spec: SheetSpec) -> str:
    """Which fields the toolkit fills, and WHEN - the first question every grader asks of
    one of these, which is why it is answered in the file rather than in the docs."""
    if spec.submit_external:
        return (
            "Auto-filled by the toolkit: nothing. This assignment is submitted outside "
            "GitHub, so there is no `info:` block and no late arithmetic."
        )
    clauses = [
        "`submitted` and `days_late` fill at the due date and refresh after each late push"
    ]
    if spec.is_group:
        clauses.append(
            "`contributions` is read from CONTRIBUTIONS.md at the same moments"
        )
    if spec.autograde:
        clauses.append("`autograde` fills once at the cutoff")
    return (
        "Auto-filled by the toolkit (you never type these): every `info:` block. "
        + "; ".join(clauses)
        + ". All of them freeze at the cutoff."
    )


def _you_fill_in_sentence(spec: SheetSpec) -> str:
    """The human keys of THIS sheet's shape, named in the order they appear in it."""
    qualifier = (
        " (one value per question; max points shown beside each)"
        if spec.questions
        else ""
    )
    fields = [f"{spec.score_key}{qualifier}"]
    if spec.is_group:
        fields.append(spec.feedback_key)
    fields += ["adjustment_individual", "feedback_individual", NOTES_KEY]
    return (
        f"You fill in: {', '.join(fields)}. Anything you type is never touched. Nothing "
        f"reaches a student until you run Distribute grades."
    )


# The sheet says its own state in its header, and `collect` reads it back to decide
# whether the file has already been sealed. A contract, therefore, not a formatting choice:
# both spellings live here so a reworded header cannot silently un-freeze a sheet.
STATUS_PREFIX = "# Status: "
FROZEN_STATUS = "FROZEN"


def sheet_is_frozen(text: str) -> bool:
    """Whether this sheet's own header says it has been sealed.

    The header is regenerated on every write, so it is the toolkit's own last word on the
    file - and it survives what the snapshot cannot: a sheet sealed on the facts it held
    because no snapshot could be read stays sealed. Only the comment block is looked at;
    the word appearing inside a grader's feedback means nothing."""
    for line in text.splitlines():
        if line.startswith(STATUS_PREFIX):
            return line[len(STATUS_PREFIX) :].strip().startswith(FROZEN_STATUS)
        if line and not line.startswith("#"):
            break  # past the header block
    return False


def _sheet_header(spec: SheetSpec, status_line: str) -> str:
    """The comment block at the top of the sheet, regenerated on every write so that it is
    still true after `grading_config.yml` changes under a sheet that already has marks."""
    rule = "# " + "-" * (_HEADER_WIDTH - 2)
    title = _SEP.join(["GRADING SHEET", spec.slug, spec.title, "INSTRUCTOR-OWNED"])
    lines = [
        rule,
        f"# {title}",
        "#",
        "# From grading_config.yml / schedule.yml (edit THERE, not here):",
        *(f"#   {fact}" for fact in _config_facts(spec)),
        f"{STATUS_PREFIX}{status_line}",
        "#",
        *_wrapped(_auto_filled_sentence(spec)),
        *_wrapped(_you_fill_in_sentence(spec)),
        rule,
    ]
    return "\n".join(lines) + "\n"


def _with_comment(line: str, text: str) -> str:
    """`line` with `# text` at the sheet's comment column (two spaces at the very least)."""
    return f"{line}{' ' * max(_COMMENT_COLUMN - len(line), 2)}# {text}"


def _annotate(body: str, spec: SheetSpec) -> str:
    """Re-emit the inline comments a YAML dumper cannot carry: what `info:` is, and each
    question's maximum beside its blank.

    A post-dump text pass on purpose. These are comments, not data - carrying the maxima as
    values would invite a grader to edit them where nothing reads them - and re-deriving
    them on every write is what keeps them true when `grading_config.yml` changes under a
    sheet that is already half marked."""
    out: list[str] = []
    score_indent: int | None = None
    for line in body.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if score_indent is not None and indent <= score_indent:
            score_indent = None
        if stripped == f"{INFO_KEY}:":
            line = _with_comment(line, INFO_COMMENT)
        elif stripped == f"{spec.score_key}:" and spec.questions:
            line = _with_comment(line, _SCORE_COMMENT)
            score_indent = indent
        elif score_indent is not None:
            question = stripped.split(":", 1)[0].strip("'\"")
            if question in (spec.questions or {}):
                line = _with_comment(line, f"/{spec.questions[question]}")
        out.append(line)
    return "\n".join(out) + "\n"


def dump_sheet(sheet: dict, spec: SheetSpec, status_line: str) -> str:
    """The sheet as the file that gets written: the regenerated comment header, the YAML,
    and the inline comments annotated back on.

    Deterministic - the same sheet, spec and status give byte-identical text - because the
    write path compares blob shas, and text that churned would commit on every tick."""
    body = yaml.dump(
        sheet,
        Dumper=_SheetDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        # Never fold a long line: the grader's own wrapping is the readable one, and a fold
        # that moved with the text would churn the file.
        width=10**6,
    )
    return _sheet_header(spec, status_line) + _annotate(body, spec)


def _decimal(value: object) -> Decimal | None:
    """`value` as a Decimal, or None when it is blank or not a number.

    Grades are free text and stay that way: `pass`, `A-` and `see me` are legitimate marks
    that no arithmetic applies to, so they come back None and are passed through verbatim
    rather than coerced into a number nobody typed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    # `Decimal` accepts `nan` and `Infinity`, and comparing either of them RAISES - so a
    # grader who typed one into a score cell would take the whole distribution down rather
    # than have that one mark passed through as the text it is.
    return number if number.is_finite() else None


def _plain(number: Decimal) -> str:
    """A Decimal with no exponent and no trailing zeros - `50`, not `5E+1` or `50.0`."""
    return format(number.normalize(), "f")


def score_total(
    score: object, questions: dict[str, str] | None = None
) -> Decimal | None:
    """What a `score_group`/`score_individual` cell adds up to: the scalar itself, or the
    sum of a per-question map.

    None when there is nothing to add - the cell is blank, the map is entirely blank, or a
    value is not a number. A PARTLY filled map still totals, so the running total is right
    while marking is in progress; one non-numeric question, though, makes the whole total a
    guess, and a guess is exactly what must not reach a student.

    `questions` is the assignment's own list. Given one, a key it does not declare is NOT
    added: a stray `Q5: 10` used to make 53 out of a 50-point assignment, with no maximum
    beside it and nothing said. The unit is held for a person either way (see
    `sheet_hold_reasons`); this is what stops the number existing at all."""
    if not isinstance(score, dict):
        return _decimal(score)
    total, marked = Decimal(0), False
    for key, value in score.items():
        if questions and key not in questions:
            continue
        if value is None or not str(value).strip():
            continue
        number = _decimal(value)
        if number is None:
            return None
        total += number
        marked = True
    return total if marked else None


# Every way `late_penalty_per_day` can be written wrong, and what to say about it. One
# multiplies every late mark in the cohort, so none of them may pass quietly: a bare `10`
# meant no penalty at all while the header still advertised one, and `-10%` ADDED marks for
# being late.
_PENALTY_FAULTS = {
    "unwritten": "is not a number - write `10%` or `0.1`",
    "bare": "is neither a percentage nor a fraction - write `10%` or `0.1`",
    "negative": "is negative - that would ADD marks for lateness",
    "over": "is more than 100% a day",
}


def penalty_fault(text: object) -> str:
    """Why `late_penalty_per_day` cannot be used, as a `_PENALTY_FAULTS` key, or "".

    Blank and absent are not faults - plenty of assignments accept no late work at all, or
    accept it without a deduction."""
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw:
        return ""
    percent = raw.endswith("%")
    rate = _decimal(raw[:-1] if percent else raw)
    if rate is None:
        return "unwritten"
    if percent:
        rate /= 100
    elif rate >= 1:
        # A BARE `10` is read neither as 1000% nor, silently, as 10%. The two spellings a
        # course actually writes are the percentage and the fraction; guessing between
        # them on a number that multiplies every late mark is not a guess worth making.
        return "bare"
    if rate < 0:
        return "negative"
    return "over" if rate > 1 else ""


def penalty_rate(text: object) -> Decimal | None:
    """`late_penalty_per_day` as a fraction: `10%` and `0.1` both give 0.10.

    None for anything `penalty_fault` refuses - which `parse_grading_spec` has already
    said out loud, once, when the assignment's definition was read."""
    if penalty_fault(text):
        return None
    raw = str(text or "").strip()
    if not raw:
        return None
    return _decimal(raw[:-1]) / 100 if raw.endswith("%") else _decimal(raw)


def final_grade(
    total: object, rate: object, days_late: object, adjustment: object
) -> Decimal | None:
    """`total x (1 - rate x days_late) + adjustment`, floored at 0.

    Derived on OUTPUT and never stored: the sheet has no `final_grade` field, so there is
    no stale cell for this to disagree with. A blank adjustment is 0 and a blank rate or
    day count is no penalty, so an assignment with no late policy needs no special case.
    A total that is not a number gets no arithmetic at all and comes back None - the caller
    distributes the mark exactly as the grader typed it."""
    earned = _decimal(total)
    if earned is None:
        return None
    penalty, days = _decimal(rate), _decimal(days_late)
    if penalty is not None and days is not None and days > 0:
        earned *= Decimal(1) - penalty * days
    return max(Decimal(0), earned + (_decimal(adjustment) or Decimal(0)))


# ------------------------------------------------- the assignment's own definition

# `grading_config.yml`, on the course template's `solution` branch, is where an assignment
# is DEFINED. It lives here rather than in `collect` because both readers need it and only
# one of them is the autograder: `collect` takes `type`/`autograde`/`tests` from it, and
# everything else in it exists to shape the grading sheet - which is this module's. The
# names `collect` still spells are re-exported there, so no caller had to move.
GRADING_FILE = "grading_config.yml"  # on the template's solution branch

# What the file was called while it held three autograder fields. It defines the whole
# assignment now - dates aside - and the name says so. Templates scaffolded before the
# rename still carry the old one, so it is still READ; nothing writes it any more.
LEGACY_GRADING_FILE = "grading.yml"

# The assignment's own definition. `type`/`autograde`/`tests` drive the autograder;
# everything below them drives the grading sheet - its shape, its maxima, its header - so a
# course states each fact once, in the file that already holds the others.
_DEFAULT_SPEC = {
    "type": "individual",
    # OFF unless the assignment asks for it. Most assignments are hand-marked, and a
    # default of true made every template without the key try to run hidden tests that were
    # never written - a red tick every quarter of an hour for the rest of the term.
    "autograde": False,
    "tests": "tests",
    "title": "",
    "submit_via": "github",
    "questions": None,
    "late_window_days": None,
    "late_penalty_per_day": None,
}

SUBMIT_VIA = (
    "github",
    "external",
)  # `external` = handed in off GitHub (Moodle, Kaggle)


def _one_of(value: object, allowed: tuple[str, ...], field: str, default: str) -> str:
    """A closed vocabulary, or the default with a warning. Never the raw value: an
    unrecognised `submit_via` would silently turn late arithmetic off for a cohort."""
    text = str(value or "").strip().lower()
    if text in allowed:
        return text
    log_err(
        f"  ! {GRADING_FILE}: `{field}: {value}` is not one of "
        f"{'/'.join(allowed)} - using `{default}`"
    )
    return default


def _questions(value: object) -> dict[str, str] | None:
    """`questions:` as {name: maximum AS TEXT}.

    Text, because the maxima are only ever DISPLAYED - beside each blank in the sheet, and
    in its header - and a course that writes `1.5` must read back what it wrote. Anything
    that is not a mapping is dropped with a warning rather than half-read."""
    if not isinstance(value, dict):
        log_err(
            f"  ! {GRADING_FILE}: `questions:` must be a mapping of name -> points - ignored"
        )
        return None
    questions = {
        str(name).strip(): ("" if points is None else str(points).strip())
        for name, points in value.items()
        if str(name).strip()
    }
    return questions or None


def _whole_days(value: object) -> int | None:
    """`late_window_days` as a whole number of days, or None with a warning."""
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        log_err(
            f"  ! {GRADING_FILE}: `late_window_days: {value}` is not a whole number of "
            f"days - ignored"
        )
        return None


def _penalty(value: object) -> str | None:
    """`late_penalty_per_day` as it was typed, or None with a warning saying which way it
    is wrong.

    Checked here, once per spec, like every other malformed field: the derivation itself
    stays pure and is called per student. Refusing without saying so meant every late mark
    in that cohort quietly lost its deduction while the sheet's header still advertised
    one."""
    raw = str(value or "").strip()
    if not raw:
        return None
    fault = penalty_fault(raw)
    if fault:
        log_err(
            f"  ! {GRADING_FILE}: `late_penalty_per_day: {value}` "
            f"{_PENALTY_FAULTS[fault]}; no late penalty is applied"
        )
        return None
    return raw


def parse_grading_spec(text: str) -> dict:
    """Parse a `grading_config.yml` (missing keys fall back to defaults; extras ignored).

    A malformed VALUE is logged and dropped, never raised and never passed through: this
    file is hand-edited by faculty and read by an hourly cron, so one bad line costs the
    field it sits on and nothing else."""
    data = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        data = {}
    spec = dict(_DEFAULT_SPEC)
    spec.update({k: data[k] for k in ("type", "autograde", "tests") if k in data})
    if "title" in data:
        spec["title"] = str(data["title"] or "").strip()
    if "submit_via" in data:
        spec["submit_via"] = _one_of(
            data["submit_via"], SUBMIT_VIA, "submit_via", "github"
        )
    if "questions" in data:
        spec["questions"] = _questions(data["questions"])
    if "late_window_days" in data:
        spec["late_window_days"] = _whole_days(data["late_window_days"])
    if "late_penalty_per_day" in data:
        spec["late_penalty_per_day"] = _penalty(data["late_penalty_per_day"])
    return spec


@cache
def _grading_text(course_org: str, template: str) -> str | None:
    """The template's `grading_config.yml`, read ONCE per template per process.

    An hourly tick asks the same template the same question from the scheduler, the sheet
    refresh and the collection that follows them. Memoising the TEXT (like
    `schedule._schedule_text`) means every caller still parses its own dict - nothing
    shared to mutate - and still sees its own warnings. tests/conftest.py clears it.

    The old name is a FALLBACK, not a second spelling: a template scaffolded before the
    rename is read and its owner told to rename it, once per template per process - this
    cache is what makes it once rather than once per tick per pass."""
    text = get_file_content(course_org, template, GRADING_FILE, ref=SOLUTION_BRANCH)
    if text is not None:
        return text
    legacy = get_file_content(
        course_org, template, LEGACY_GRADING_FILE, ref=SOLUTION_BRANCH
    )
    if legacy is not None:
        log_err(
            f"  ! {template} still defines the assignment in {LEGACY_GRADING_FILE} - "
            f"rename to {GRADING_FILE}; the old name stops working next term"
        )
    return legacy


def load_grading_spec(course_org: str, template: str) -> dict:
    """The assignment's definition from the course template's `solution` branch.

    NEVER raises: it sits under the hourly cron, and a template with no solution branch, no
    definition file, or one that does not parse must leave the rest of the tick running on
    the defaults rather than take the cohort down with it."""
    try:
        text = _grading_text(course_org, template)
    except RuntimeError as exc:
        log_err(f"  ! could not read {template}/{GRADING_FILE}: {exc}")
        return dict(_DEFAULT_SPEC)
    try:
        return parse_grading_spec(text or "")
    except yaml.YAMLError as exc:
        log_err(
            f"  ! {template}/{GRADING_FILE} is not valid YAML - using defaults: {exc}"
        )
        return dict(_DEFAULT_SPEC)


def _display_moment(at: datetime | None) -> str:
    """`Sun 4 Oct 2026 23:59` - a date a grader reads, not one a machine parses. Built by
    hand rather than with `%-d`, which is a glibc/BSD extension."""
    if at is None:
        return ""
    return f"{at:%a} {at.day} {at:%b} {at.year} {at:%H:%M}"


def _display_long(at: datetime | None, tz_name: str = "") -> str:
    """`Sunday 4 October 2026, 23:59 (Europe/Berlin)` - the form a STUDENT reads, once, in
    an issue they have to act on. The sheet's header uses the short form beside it: a
    grader scans that file rather than reading it."""
    if at is None:
        return ""
    zone = f" ({tz_name})" if tz_name else ""
    return f"{at:%A} {at.day} {at:%B %Y}, {at:%H:%M}{zone}"


def cutoff_at(sched: schedule.Schedule, key: str, gspec: dict) -> datetime | None:
    """When this assignment stops accepting work: an explicit `grading_datetime`, else the
    due date plus the template's late window, else the due date.

    THE cutoff. Everything that has to agree about when the door shuts reads it here - the
    sheet's header and its late-policy line, the receipts that quote that policy to a
    student, the snapshot that freezes the pin and the autograder that fires off it. It
    needs the template's `grading_config.yml` to know the window, which is why it lives
    beside the spec reader rather than in `schedule`; `schedule.grading_datetime_at` is the
    same question answered without one, and is only right when there is no window at all."""
    entry = sched.assignments.get(key)
    if entry is None:
        return None
    if entry.grading_datetime is not None:
        return entry.grading_datetime
    days = gspec.get("late_window_days")
    return entry.due_datetime + timedelta(days=days) if days else entry.due_datetime


def sheet_spec(
    sched: schedule.Schedule, key: str, slug: str, gspec: dict, is_group: bool
) -> SheetSpec:
    """What the sheet needs to know about this assignment, gathered from the two files
    that own it: `grading_config.yml` on the template's solution branch, and the cohort's
    `schedule.yml`. Nothing here is written into the sheet as data - it reaches the grader
    as the comment header, which is regenerated on every write."""
    entry = sched.assignments.get(key)
    return SheetSpec(
        slug=slug,
        title=gspec["title"] or (entry.title if entry else "") or slug,
        is_group=is_group,
        submit_external=gspec["submit_via"] == "external",
        questions=gspec["questions"],
        late_window_days=gspec["late_window_days"],
        late_penalty_per_day=gspec["late_penalty_per_day"],
        autograde=bool(gspec["autograde"]),
        due_display=_display_moment(entry.due_datetime if entry else None),
        cutoff_display=_display_moment(cutoff_at(sched, key, gspec)),
        due_long=_display_long(entry.due_datetime if entry else None, sched.timezone),
        cutoff_long=_display_long(cutoff_at(sched, key, gspec), sched.timezone),
    )


# ----------------------------------------------------------- the Feedback issue, in situ

# Reading and writing the issue whose CONTRACT lives in `course`. Everything that decides
# WHAT is said is there; everything that decides whether a call is made is here.
_FEEDBACK_LABEL_COLOUR = "0e8a16"
_FEEDBACK_LABEL_DESCRIPTION = (
    "Submission receipts, feedback and grades from the toolkit"
)


def late_policy(spec) -> str:
    """`accepted until Sunday 11 October 2026, 23:59 (Europe/Berlin), at 10% of your grade
    per day started.` - or "" when nothing is accepted after the deadline.

    ONE sentence for both places a student meets the policy - the Feedback issue at handout
    and every receipt after it. Two spellings of the same rule is how a student ends up
    reading two different deadlines.

    The rate TRAILS the date rather than bracketing it: `cutoff_long` already ends in the
    cohort's timezone, and two parentheticals in a row read as a typo."""
    if spec.submit_external or not (spec.late_window_days and spec.cutoff_long):
        return ""
    rate = (
        f", at {spec.late_penalty_per_day} of your grade per day started"
        if spec.late_penalty_per_day
        else ""
    )
    return f"accepted until {spec.cutoff_long}{rate}."


def feedback_body(
    spec, unit: str = "", members: tuple[str, ...] | list[str] = ()
) -> str:
    """The Feedback issue's body for one submission repo, from the assignment's own spec.

    `unit` is the row that repo belongs to - a team name on a group assignment, the
    student's handle on an individual one - and WHICH VARIANT to write is read off the
    spec rather than left to the caller. Only a group body names the team and asks for
    CONTRIBUTIONS.md, and the openers that reach a repo late (the first refresh after the
    due date, distribute) each know their unit but used to pass neither - so a team whose
    issue was opened lazily never got the ask, on the one path where there was still time
    to act on it."""
    late = late_policy(spec)
    team = unit if spec.is_group else ""
    # Handles, not names: this body is written from the provisioning path, which knows the
    # repo's collaborators and not the roster's display names - and a handle is what the
    # repo shows the team anyway.
    team_line = (
        f"{team} ({', '.join('@' + h for h in members)})" if team and members else ""
    )
    return feedback_issue_body(
        due_display=spec.due_long,
        late_policy_line=late,
        team_line=team_line,
        external=spec.submit_external,
    )


def late_line(spec) -> str:
    """The late policy as the sentence a receipt carries, or "" when there is no window."""
    policy = late_policy(spec)
    return f"Late work is {policy}" if policy else ""


def penalty_display(spec, days: int) -> str:
    """`-20%` for the receipt - `_penalty_display` under the spec the caller already holds.

    The same function as the gradebook's and the feedback comment's, deliberately: those
    are the three places one student reads one deduction, and two renderers of it drifted
    apart above two decimal places the moment one of them rounded."""
    return _penalty_display(penalty_rate(spec.late_penalty_per_day), days)


def receipt(
    spec, event: str, *, sha: str = "", pushed_display: str = "", days: int = 0
) -> str:
    """One receipt for this assignment, composed from its own late policy."""
    return receipt_body(
        event,
        sha=sha,
        pushed_display=pushed_display,
        days_late=days,
        penalty_display=penalty_display(spec, days),
        late_line=late_line(spec),
    )


def _feedback_issues(
    cohort_org: str, repo: str, query: str, jq: str
) -> list[str] | None:
    code, out = gh("api", f"repos/{cohort_org}/{repo}/issues?{query}", "--jq", jq)
    if code != 0:
        return None
    return [line for line in out.splitlines() if line.strip()]


def find_feedback_issue(cohort_org: str, repo: str) -> tuple[int, str] | None:
    """`(number, state)` of this repo's Feedback issue, or None.

    Three rungs, cheapest first: the LABEL, then a body carrying one of the marks, then the
    exact title. Deliberately the LIST endpoint rather than `gh issue list --search`: the
    search index lags behind by minutes, and a lookup that comes back empty here does not
    mean "not there", it means "opened a second one" - which is what the search path did.

    Pull requests are issues to this endpoint, so they are filtered out: a PR titled
    Feedback would otherwise be commented on instead."""
    by_label = _feedback_issues(
        cohort_org,
        repo,
        f"labels={FEEDBACK_ISSUE_LABEL}&state=all&per_page=5",
        '.[] | select(.pull_request == null) | "\\(.number)\\t\\(.state)"',
    )
    if by_label:
        number, _, state = by_label[0].partition("\t")
        return int(number), state
    marks = " or ".join(f'contains("{mark}")' for mark in FEEDBACK_ISSUE_MARKS)
    listed = _feedback_issues(
        cohort_org,
        repo,
        "state=all&per_page=50",
        ".[] | select(.pull_request == null) | "
        f'"\\(.number)\\t\\(.state)\\t\\(if ((.body // "") | {marks}) then "mark" else "" end)'
        '\\t\\(.title)"',
    )
    if not listed:
        return None
    rows = [line.split("\t") for line in listed]
    marked = [r for r in rows if len(r) > 2 and r[2] == "mark"]
    titled = [r for r in rows if len(r) > 3 and r[3] == FEEDBACK_ISSUE_TITLE]
    for candidate in (marked, titled):
        if candidate:
            return int(candidate[0][0]), candidate[0][1]
    return None


def ensure_feedback_issue(
    cohort_org: str, repo: str, body: str, dry_run: bool = False
) -> int | None:
    """This repo's Feedback issue number, opening one if it has none.

    A CLOSED issue is reopened: a student who closes theirs must still receive their
    receipts and their grade, and a second issue would split the thread they were told to
    read. Never two - see `find_feedback_issue` for why the lookup is not a search."""
    found = find_feedback_issue(cohort_org, repo)
    if found is not None:
        number, state = found
        if state == "closed" and not dry_run:
            # `--method PATCH` so ghcli's write pacer counts it (see `_is_mutating`).
            code, out = gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{cohort_org}/{repo}/issues/{number}",
                "--field",
                "state=open",
            )
            if code != 0:
                log_err(f"  ! could not reopen the Feedback issue: {out[:160]}")
        return number
    if dry_run:
        log("    DRY-RUN  would open the Feedback issue")
        return None
    # The label first: GitHub silently drops a label the repo does not have, and the label
    # is the cheapest rung of the lookup above.
    ensure_label(
        cohort_org,
        repo,
        FEEDBACK_ISSUE_LABEL,
        color=_FEEDBACK_LABEL_COLOUR,
        description=_FEEDBACK_LABEL_DESCRIPTION,
        person=True,
    )
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{cohort_org}/{repo}/issues",
        "--field",
        f"title={FEEDBACK_ISSUE_TITLE}",
        "--field",
        f"body={body}",
        "--field",
        f"labels[]={FEEDBACK_ISSUE_LABEL}",
        "--jq",
        ".number",
    )
    if code != 0:
        log_err(f"  ! could not open the Feedback issue: {out[:160]}")
        return None
    return int(out.strip()) if out.strip().isdigit() else None


def post_marked_comment(
    cohort_org: str,
    repo: str,
    issue_no: int,
    body: str,
    marker: str,
    dry_run: bool = False,
) -> bool:
    """Post one comment on the Feedback issue, unless it already carries `marker`.

    The marker is the whole idempotence story, and it is why both callers share this: the
    refresh pass runs four times an hour for the length of the late window, and distribute
    is re-run after every correction. Neither may say the same thing twice.

    Paginated, because "already said" is only true of the comments we actually read: a
    thread that outgrew one page would hide its own markers and be told everything
    again."""
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{cohort_org}/{repo}/issues/{issue_no}/comments?per_page=100",
        "--jq",
        ".[].body",
    )
    if code != 0:
        log_err(f"  ! could not read the Feedback issue's comments: {out[:160]}")
        return False
    if marker in out:
        return True  # already said, on this commit, for this event
    if dry_run:
        log("    DRY-RUN  would post a submission receipt")
        return True
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{cohort_org}/{repo}/issues/{issue_no}/comments",
        "--field",
        f"body={body}\n{marker}\n",
    )
    if code != 0:
        log_err(f"  ! could not comment on the Feedback issue: {out[:160]}")
        return False
    return True


def post_receipt(
    cohort_org: str, repo: str, issue_no: int, body: str, marker: str, dry_run=False
) -> bool:
    """A submission receipt - `post_marked_comment` under the name its caller uses."""
    return post_marked_comment(cohort_org, repo, issue_no, body, marker, dry_run)


# ------------------------------------------------------------- what a student is shown

# The ONLY keys that may reach a student, in the order a view writes them. An ALLOWLIST,
# not a redaction list: a key added to the sheet next term - a fact the toolkit starts
# recording, a column a grader invents for themselves - is invisible here until someone
# names it. A denylist would have to be right about every key anyone ever adds; this has
# to be right about nine.
#
# Deliberately absent: `notes_not_shared_with_students`, every `info:` fact but
# `submitted` and `days_late` (`autograde`, `completion`, `contributions`), and - because
# a gradebook shows the final grade and never its disaggregation - the team's score and
# the member's own `adjustment_individual`. Those last two are INPUTS to the number the
# student sees, not results: the team score is shared with the whole team in the team's
# own repo (`team_issue_body`), where it is already common knowledge, and the adjustment
# stays between a member and their grader.
STUDENT_VIEW_KEYS = (
    "final_grade",  # derived on output, never stored - the authoritative mark
    "score",  # individual assignments only: what the grader typed, per question or flat
    "max_points",  # the declared maxima summed, so a 40 reads as "40 / 50"
    "feedback",  # feedback_individual
    "submitted",  # info.submitted, as a person reads a date
    "days_late",  # info.days_late
    "penalty",  # what those days cost, e.g. "-20%"
    "team",  # group assignments only
    "team_feedback",  # feedback_group
)

# The month names are the toolkit's own, never the runner's locale: otherwise a
# German-locale Actions runner writes "Okt" into one cohort's gradebook and "Oct" into
# the next one's.
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_PRIVACY_HEADER = (
    "This gradebook is private to you. It is regenerated each time grades are "
    "distributed; do not edit it."
)
_GRADEBOOK_POINTER = (
    "Your own final grade and personal feedback are in your private gradebook: "
    "`grades-<your handle>`."
)
_README_COLUMNS = ("Assignment", "Final grade", "Submitted", "Late", "Team")
# What the Submitted column says where there is no time to show. The two are different
# facts and a student reads them as such: `external` is "we never expected a commit here",
# `not submitted` is "we looked, and nothing was in the repo". Calling the second one
# external told a student who missed a deadline that their assignment was handed in
# somewhere else.
_EXTERNAL = "external"
_NOT_SUBMITTED = "not submitted"
_NO_SUBMISSION_LINE = "No submission was recorded."
_REGISTRAR_FIELDS = ("hertie_email", "name", "github_handle")


def _blank(value: object) -> bool:
    """Whether a cell holds nothing. `0` is a value, not a blank - a student who was 0
    days late must see that, and dropping it would read as "we never looked"."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_blank(inner) for inner in value.values())
    return False


def _marked(score: object) -> dict:
    """A per-question score with its unmarked questions dropped ({} for a flat score).

    A blank question is one nobody has marked yet, and `Q3: null` in a student's file
    reads as a mark of nothing rather than as no mark at all."""
    if not isinstance(score, dict):
        return {}
    return {name: value for name, value in score.items() if not _blank(value)}


def _verbatim(score: object) -> str:
    """A score no arithmetic applies to, exactly as typed - `pass`, `A-`, `see me`.

    Only a flat cell has one: a per-question map with a word in it has no single value to
    pass through, and the map itself is already in the view."""
    return "" if isinstance(score, dict) or _blank(score) else str(score).strip()


def _max_points(spec: SheetSpec) -> str:
    """The assignment's total, or "" when the maxima are not all numbers - `questions`
    holds them as written, and a course may declare `Q1: see rubric`."""
    maxima = [_decimal(maximum) for maximum in (spec.questions or {}).values()]
    if not maxima or None in maxima:
        return ""
    return _plain(sum(maxima, Decimal(0)))


def _penalty_display(rate: Decimal | None, days_late: object) -> str:
    """What the late days cost, in the words the student is told them in: `-20%`.

    Rounded to two decimals, because `rate` is a hundredth of whatever the course wrote and
    the product carries its trailing digits: `3.333%` for three days is a deduction, not
    `-9.999%`."""
    days = _decimal(days_late)
    if rate is None or days is None or days <= 0:
        return ""
    return f"-{_plain((rate * days * 100).quantize(Decimal('0.01')))}%"


def _submitted_display(value: object, external: bool = False) -> str:
    """A recorded submission time as a person reads it: `3 Oct 22:14`.

    `external` - the assignment is handed in off GitHub - says so instead, and is the ONLY
    thing that may: there is no commit to time, so a blank here is the shape of the
    assignment rather than a missing submission. An `info:` block with no `submitted` in
    it is the other case, and comes back "" for the caller to name.

    Anything that does not parse as a timestamp comes back verbatim. `info:` is the
    toolkit's, but a grader may have typed over it, and their words about their own
    cohort beat this module's guess at what they meant."""
    if external:
        return _EXTERNAL
    if _blank(value):
        return ""
    text = value.isoformat() if isinstance(value, datetime) else str(value).strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    day = f"{moment.day} {_MONTHS[moment.month - 1]}"
    return day if "T" not in text and " " not in text else f"{day} {moment:%H:%M}"


def _allowlisted(fields: dict) -> dict:
    """`fields` reduced to the student-visible keys, in STUDENT_VIEW_KEYS order, blanks
    dropped. Every student-facing value in this module passes through here."""
    return {
        key: fields[key]
        for key in STUDENT_VIEW_KEYS
        if key in fields and not _blank(fields[key])
    }


def student_view(
    spec: SheetSpec, unit_key: str, block: dict | None, handle: str
) -> dict:
    """What ONE student may be shown of one submission unit's grading-sheet entry.

    `block` is that unit's entry: `submissions[handle]` on an individual assignment, where
    `unit_key` IS the handle; `teams[team]` on a group one, where `unit_key` is the team
    and `handle` picks the member whose view this is. Nothing of any other member is read,
    and nothing outside STUDENT_VIEW_KEYS is emitted, so the two ways a gradebook leaks -
    the grader's private notes, and a team-mate's marks - are both closed structurally.

    The final grade is DERIVED here (the score total, the late penalty, then this member's
    own adjustment) because the sheet has no field for it. A score no arithmetic applies
    to - `pass`, `A-` - is passed through exactly as typed, and `needs_hand_decision`
    flags it where a penalty would have applied to a number."""
    block = block or {}
    info = block.get(INFO_KEY) or {}
    person = (
        ((block.get("members") or {}).get(handle) or {}) if spec.is_group else block
    )
    score = block.get(spec.score_key)
    days_late = info.get("days_late")
    rate = penalty_rate(spec.late_penalty_per_day)
    final = final_grade(
        score_total(score, spec.questions),
        rate,
        days_late,
        person.get("adjustment_individual"),
    )
    return _allowlisted(
        {
            "final_grade": _plain(final) if final is not None else _verbatim(score),
            # A group's score is the TEAM's: it reaches the team in the team's own repo,
            # and reaches the member only through the final grade derived from it.
            "score": None if spec.is_group else (_marked(score) or score),
            "max_points": _max_points(spec),
            "feedback": person.get("feedback_individual"),
            "submitted": _submitted_display(
                info.get("submitted"), spec.submit_external
            ),
            "days_late": days_late,
            "penalty": _penalty_display(rate, days_late),
            "team": unit_key if spec.is_group else None,
            "team_feedback": block.get("feedback_group") if spec.is_group else None,
        }
    )


# Why a mark cannot be sent as it stands. The code is what the pipeline carries; the
# sentence is what the dry run prints. Every one of these used to pass silently.
HOLD_REASONS = {
    "penalty": "a mark no late penalty can be applied to",
    "score": "a non-numeric value in a per-question map",
    "adjustment": "a non-numeric adjustment",
    "question": "a question the assignment does not declare",
    "duplicate": "a handle in more than one submission unit",
}


def _is_typo(value: object) -> bool:
    """A cell that was typed in and is not a number.

    Blank is not a typo (nobody has marked it yet) and neither is a deliberate free-text
    mark in a SCALAR cell (`pass`, `A-`) - only the callers that know they are looking at
    arithmetic ask this. `−3` with a Unicode minus, which is what a word processor
    produces, is exactly the case: it read as no adjustment at all while the grader
    believed a penalty had been waived."""
    return not _blank(value) and _decimal(value) is None


def _score_fault(spec: SheetSpec, score: object) -> str:
    """What is wrong with one unit's score cell, as a `HOLD_REASONS` code, or ""."""
    if not isinstance(score, dict):
        return ""  # a scalar mark is free text by design
    if spec.questions and set(score) - set(spec.questions):
        return "question"
    return "score" if any(_is_typo(value) for value in score.values()) else ""


def sheet_hold_reasons(spec: SheetSpec, sheet: dict) -> dict[str, str]:
    """Every handle in this sheet whose mark a person still has to settle, and why.

    What a grader TYPED, checked before any of it is sent - as against
    `needs_hand_decision`, which is about what the arithmetic could not then do with it.
    Each of these went out silently: `Q1: 14/15` deleted the student's grade and sent
    their feedback anyway; `−3` was read as no adjustment while the grader believed a
    penalty had been waived; a stray `Q5` was added to a total the assignment has no
    maximum for; and a handle in two teams took whichever team the loop reached last.

    One reason per handle, the first found: this is a line in a log, not a diagnosis."""
    held: dict[str, str] = {}
    seen: set[str] = set()
    for unit_key, block in ((sheet or {}).get(spec.container_key) or {}).items():
        if not isinstance(block, dict):
            continue  # a unit mid-edit; the next run reads a whole block
        fault = _score_fault(spec, block.get(spec.score_key))
        people = (block.get("members") or {}) if spec.is_group else {unit_key: block}
        for handle, person in people.items():
            if handle in seen:
                held[handle] = "duplicate"
                continue
            seen.add(handle)
            person = person if isinstance(person, dict) else {}
            reason = fault or (
                "adjustment" if _is_typo(person.get("adjustment_individual")) else ""
            )
            if reason:
                held[handle] = reason
    return held


def needs_hand_decision(view: dict) -> bool:
    """Whether this mark needs a person to settle it: a score that is not a number, under
    a late penalty that cannot be applied to it.

    `pass`, two days late, is not `-20% of pass`. The distribute dry run counts these so
    that a grader decides them before anything is sent, rather than after a student reads
    it.

    A row with NOTHING typed in it yet is not one of them - there is no decision to take
    about a mark nobody has written. It carries a penalty (the toolkit counted the days)
    and no `final_grade`, which is how the two are told apart."""
    return (
        "penalty" in view
        and "final_grade" in view
        and _decimal(view["final_grade"]) is None
    )


def _views_from_sheet(spec: SheetSpec, sheet: dict) -> dict[str, dict]:
    """Every student's view of one grading sheet, keyed by handle."""
    views: dict[str, dict] = {}
    for unit_key, block in ((sheet or {}).get(spec.container_key) or {}).items():
        if not isinstance(block, dict):
            continue  # a unit mid-edit; the next run reads a whole block
        handles = (block.get("members") or {}) if spec.is_group else {unit_key: None}
        for handle in handles:
            views[handle] = student_view(spec, unit_key, block, handle)
    return views


def _views_from_grade_rows(rows: list[GradeRow]) -> dict[str, dict]:
    """The same views off a legacy `grades/<slug>.csv`, so a cohort that began marking
    before the grading sheet existed still distributes from the table it is using.

    That CSV's `final_grade` is authoritative - it was typed, not derived - so nothing is
    recomputed from it. Its `team_score` and `individual_adjustment` columns have no
    student-visible home any more (see STUDENT_VIEW_KEYS) and are not carried over."""
    return {
        row.github_handle: _allowlisted(
            {
                "final_grade": row.final_grade,
                "feedback": row.individual_comments,
                "team": row.team,
                "team_feedback": row.team_comments,
            }
        )
        for row in rows
        if row.github_handle
    }


def build_gradebooks(
    sources: dict[str, tuple[SheetSpec, dict] | list[GradeRow]],
) -> dict[str, dict[str, dict]]:
    """Pivot every assignment's source into `{handle: {slug: view}}` - one book per
    student, one entry per assignment they have a mark or a word of feedback on.

    A source is either a grading sheet with the spec that reads it, or a legacy grade
    CSV's rows. Assignments are folded in sorted order, so a re-run renders byte-identical
    files and only a gradebook that really changed is committed and emailed. An empty view
    is not an entry: a student with nothing in the sheet yet has nothing to be told."""
    books: dict[str, dict[str, dict]] = {}
    canonical: dict[str, str] = {}  # fold key -> the first spelling seen for it
    for slug in sorted(sources):
        source = sources[slug]
        views = (
            _views_from_grade_rows(source)
            if isinstance(source, list)
            else _views_from_sheet(*source)
        )
        for handle, view in views.items():
            if not view:
                continue
            key = canonical.setdefault(handle.casefold(), handle)
            books.setdefault(key, {})[slug] = view
    return books


def _cell(value: object) -> str:
    """One value as a Markdown table cell: no `|` to close the column early, no newline to
    end the row. A grader's feedback is free text and can reach a table either way."""
    text = "" if _blank(value) else " ".join(str(value).split())
    return text.replace("|", "\\|")


def _over_max(value: object, max_points: object) -> str:
    """`40 / 50` where the assignment declares a total, `40` where it does not."""
    text = "" if _blank(value) else str(value).strip()
    return f"{text} / {max_points}" if text and not _blank(max_points) else text


def _late_display(days_late: object) -> str:
    """`on time`, `1 day late`, `2 days late` - "" where nothing was timed."""
    days = _decimal(days_late)
    if days is None:
        return ""
    if days <= 0:
        return "on time"
    return f"{_plain(days)} day{'' if days == 1 else 's'} late"


def _when_clause(days_late: object, submitted: object) -> list[str]:
    """The clause on a score line that says when the work came in: `submitted on time`,
    `2 days late`, or - where nothing counted the days - `submitted 3 Oct 22:14`."""
    late = _late_display(days_late)
    if late:
        return [f"submitted {late}" if late == "on time" else late]
    if _blank(submitted) or submitted == _EXTERNAL:
        return []  # nothing was timed, and "submitted external" says nothing
    return [f"submitted {submitted}"]


def _questions_clause(score: object) -> str:
    """` (Q1 14, Q2 13)` - the marks behind a total, or "" for a flat score."""
    marked = _marked(score)
    if not marked:
        return ""
    return " (" + ", ".join(f"{name} {value}" for name, value in marked.items()) + ")"


def _readme_row(title: str, view: dict, timed: bool = True) -> str:
    """One assignment's row in the summary table."""
    values = (
        title,
        _over_max(view.get("final_grade", ""), view.get("max_points")),
        # An external assignment says so; anything else with no time on it is a repo
        # nothing was ever pushed to - unless nothing timed this assignment at all, in
        # which case the cell is blank rather than an accusation.
        view.get("submitted") or (_NOT_SUBMITTED if timed else ""),
        _late_display(view.get("days_late")),
        view.get("team", ""),
    )
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def _readme_section(title: str, view: dict) -> str:
    """One assignment's section: the final grade, the student's own feedback verbatim, and
    the team's feedback as a blockquote that says who else has read it."""
    grade = _over_max(view.get("final_grade", ""), view.get("max_points"))
    parts = [f"## {title}" + (f"\n**Final grade:** {grade}" if grade else "")]
    if not _blank(view.get("feedback")):
        parts.append(str(view["feedback"]).strip())
    if not _blank(view.get("team_feedback")):
        label = f"**Team feedback (shared with {view.get('team', 'your team')}):**"
        lines = str(view["team_feedback"]).strip().split("\n")
        quoted = [f"> {label} {lines[0]}".rstrip()]
        quoted += [f"> {line}".rstrip() for line in lines[1:]]
        parts.append("\n".join(quoted))
    return "\n\n".join(parts)


def render_readme(
    handle: str,
    book: dict[str, dict],
    titles: dict[str, str],
    timed: Container[str] | None = None,
) -> str:
    """One student's gradebook README - the file they actually open.

    The privacy line, one row per assignment, then a section per assignment with their
    feedback. It gives the final grade and never the sum behind it: the team's score and
    their own adjustment are their grader's working, and a student reading their own
    deduction beside their team-mates' shared mark is exactly the conversation this
    workflow exists to avoid.

    `handle` is the student the book belongs to; the text names nobody - the repo is
    already private to them - and takes it so that every per-student write reads the
    same at the call site. `titles` maps a slug to the assignment's name, falling back to
    the slug rather than rendering an empty heading.

    `timed` is the set of slugs whose source records WHEN the work came in - the grading
    sheets. An assignment distributed from a legacy CSV is not in it, and its Submitted
    cell is left blank: "not submitted" there would be this module asserting something no
    source told it. None (the default) means every assignment is timed, which is what a
    caller holding only sheets has."""
    del handle
    slugs = sorted(book)
    table = [
        "| " + " | ".join(_README_COLUMNS) + " |",
        "|" + "---|" * len(_README_COLUMNS),
        *(
            _readme_row(
                titles.get(slug, slug), book[slug], timed is None or slug in timed
            )
            for slug in slugs
        ),
    ]
    sections = [_readme_section(titles.get(slug, slug), book[slug]) for slug in slugs]
    return "\n\n".join([_PRIVACY_HEADER, "\n".join(table), *sections]) + "\n"


def render_registrar_csv(
    students: list[roster.Student], books: dict[str, dict[str, dict]]
) -> str:
    """The registrar's export: one row per ENROLLED student, one column per assignment,
    each cell the final grade exactly as that student was told it.

    Every enrolled student is a row, marked or not, and a student who has not onboarded
    yet is a row with no handle: a missing row reads as somebody who left the course, and
    this is the file a grade is transcribed from. Auditors are never assessed and are
    never rows. It lives in the private classroom-config and is never logged."""
    slugs = sorted({slug for book in books.values() for slug in book})
    by_handle = {handle.casefold(): book for handle, book in books.items()}

    def row(student: roster.Student) -> list[str]:
        book = by_handle.get(student.github_handle.casefold()) or {}
        return [
            student.hertie_email,
            student.name,
            student.github_handle,
            *(str(book.get(slug, {}).get("final_grade", "")) for slug in slugs),
        ]

    return dump_csv(
        [*_REGISTRAR_FIELDS, *slugs],
        (
            row(student)
            for student in sorted(
                roster.enrolled(students), key=lambda s: s.hertie_email.casefold()
            )
        ),
    )


@dataclass(frozen=True)
class TeamResult:
    """Everything that may be said in a TEAM's repo, and nothing else.

    A team repo grants the whole team `maintain`, so a comment posted there is read by
    every member. This type is what enforces that: it carries no member fields at all, so
    `team_issue_body` cannot name one member's adjustment, feedback or final grade even by
    mistake. `team_score` is the `score_group` cell as the grader typed it - a per-question
    map or a flat value - because the comment shows the breakdown beside the total."""

    team: str
    team_score: object = None
    max_points: str = ""
    submitted_display: str = ""
    days_late: object = None
    penalty: str = ""
    feedback_group: str = ""


def team_result(spec: SheetSpec, team: str, block: dict | None) -> TeamResult:
    """One team's shareable result off its grading-sheet entry. `members:` is not read."""
    block = block or {}
    info = block.get(INFO_KEY) or {}
    days_late = info.get("days_late")
    return TeamResult(
        team=team,
        team_score=block.get(spec.score_key),
        max_points=_max_points(spec),
        submitted_display=_submitted_display(
            info.get("submitted"), spec.submit_external
        ),
        days_late=days_late,
        penalty=_penalty_display(penalty_rate(spec.late_penalty_per_day), days_late),
        feedback_group=block.get(spec.feedback_key) or "",
    )


def _feedback_body(title: str, score_line: str, *blocks: str) -> str:
    """A feedback comment: the heading with the score line under it, then each block."""
    heading = f"### Feedback · {title}"
    return (
        "\n\n".join(
            [heading + (f"\n{score_line}" if score_line else "")]
            + [block for block in blocks if block]
        )
        + "\n"
    )


def individual_issue_body(title: str, view: dict) -> str:
    """The feedback comment for a student's OWN assignment repo.

    Plain where nothing moved the mark (`**Grade:** 9`); where a late penalty applied, the
    score, the days, the penalty and the grade they produced - the arithmetic done to
    their work, rather than a number they cannot account for."""
    final = _over_max(view.get("final_grade", ""), view.get("max_points"))
    score = view.get("score")
    when = _when_clause(view.get("days_late"), view.get("submitted"))
    if "penalty" not in view:
        clauses = [f"**Grade:** {final}{_questions_clause(score)}", *when]
    else:
        total = score_total(score)
        earned = _plain(total) if total is not None else _verbatim(score)
        clauses = [
            (
                f"**Score:** {_over_max(earned, view.get('max_points'))}"
                f"{_questions_clause(score)}"
            ),
            *when,
            f"penalty {view['penalty']}",
            f"**Final grade:** {final}",
        ]
    feedback = "" if _blank(view.get("feedback")) else str(view["feedback"]).strip()
    # A mark on a repo nothing was pushed to - a 0, usually - needs to say why it is one.
    # An external assignment carries `submitted: external`, so it is never this case.
    nothing_in = _NO_SUBMISSION_LINE if final and "submitted" not in view else ""
    return _feedback_body(
        title, _SEP.join(clauses) if final else "", nothing_in, feedback
    )


def team_issue_body(title: str, result: TeamResult) -> str:
    """The feedback comment for a TEAM's repo - the shared result, and nothing personal.

    No member's final grade appears here, and none can: `TeamResult` has no member fields.
    The closing line is what stops that reading as an omission."""
    total = score_total(result.team_score)
    shown = _plain(total) if total is not None else _verbatim(result.team_score)
    clauses = [
        (
            f"**Team score:** {_over_max(shown, result.max_points)}"
            f"{_questions_clause(result.team_score)}"
        ),
        *_when_clause(result.days_late, result.submitted_display),
    ]
    if result.penalty:
        clauses.append(f"penalty {result.penalty}")
    feedback = (
        "" if _blank(result.feedback_group) else str(result.feedback_group).strip()
    )
    return _feedback_body(
        title, _SEP.join(clauses) if shown else "", feedback, _GRADEBOOK_POINTER
    )


def load_sheets(wd: Path) -> dict[str, dict]:
    """Every grading sheet in a classroom-config checkout, keyed by assignment slug.

    A checkout rather than the API: distribute has the repo cloned already, and reading
    the sheets out of it costs nothing and cannot half-succeed the way a file-by-file
    fetch can.

    Raises `SheetUnreadable`, naming the file, if one of them does not parse."""
    folder = wd / SHEETS_DIR
    if not folder.is_dir():
        return {}
    sheets: dict[str, dict] = {}
    for path in sorted(folder.glob("*.yml")):
        try:
            sheets[path.stem] = parse_sheet(path.read_text(encoding="utf-8"))
        except SheetUnreadable as exc:
            raise SheetUnreadable(
                f"{SHEETS_DIR}/{path.name} is not valid YAML: {exc}"
            ) from exc
    return sheets


# ---------------------------------------------------------------------- gh/git wiring


def _config_dir_names(cohort_org: str, folder: str) -> list[str]:
    """The file names in one folder of the cohort's classroom-config ([] when it has no
    such folder - which is the normal state of both of these, not a fault)."""
    code, out = gh(
        "api",
        f"repos/{cohort_org}/{CONFIG_REPO}/contents/{folder}",
        "--jq",
        ".[].name",
    )
    return sorted(out.splitlines()) if code == 0 else []


def load_grade_sources(cohort_org: str) -> dict[str, list[GradeRow] | dict]:
    """Every source of marks in the cohort's classroom-config, keyed by assignment slug.

    TWO kinds, because a cohort part-way through a term keeps marking where it started:
    `grading_sheets/<slug>.yml` (the sheet, and where every new assignment goes) parsed to
    a dict, and the legacy `grades/<slug>.csv` parsed to rows. A slug with both is the
    sheet's - that is the file a grader was told to type in."""
    sheets: dict[str, list[GradeRow] | dict] = {}
    for name in _config_dir_names(cohort_org, SHEETS_DIR):
        if not name.endswith(".yml"):
            continue
        content = get_file_content(cohort_org, CONFIG_REPO, f"{SHEETS_DIR}/{name}")
        if content is not None:
            sheets[name[:-4]] = parse_sheet(content)
    names = _config_dir_names(cohort_org, GRADES_DIR)
    if not names and not sheets:
        log_err(
            f"no {SHEETS_DIR}/ or {GRADES_DIR}/ in {cohort_org}/{CONFIG_REPO} - hand out "
            f"an assignment (which creates its grading sheet) first"
        )
        return {}
    per: dict[str, list[GradeRow] | dict] = {}
    stale = []
    for name in names:
        if not name.endswith(".csv"):
            continue
        content = get_file_content(cohort_org, CONFIG_REPO, f"{GRADES_DIR}/{name}")
        if content is None:
            continue
        try:
            per[name[:-4]] = parse_grades(content)
        except RetiredGradeHeader as exc:
            # Name the file and carry on reading the others, so one un-migrated CSV
            # reports itself by name rather than aborting on the first one it meets.
            log_err(f"{GRADES_DIR}/{name}: {exc}")
            stale.append(name)
    if stale:
        # Returning a partial set would render gradebooks for the cohort MINUS these
        # assignments, which reads as "those students have no marks" rather than as an
        # error. Nothing is rendered until every CSV can be read.
        log_err(
            f"{len(stale)} grade CSV(s) still use retired column names - rename their "
            f"header rows before rendering: {', '.join(stale)}"
        )
        return {}
    return per | sheets


def _existing_repos(cohort_org: str) -> frozenset[str] | None:
    """The cohort's repo names off ONE paginated listing, or None when it could not be read.

    Asking `repo_exists` per student cost a GET per student on every nightly sync, for a
    question one listing answers for the whole cohort. None falls the caller back to that
    probe: the listing is an optimisation, not a new way for a sync to fail."""
    try:
        return frozenset(r["name"] for r in list_org_repos(cohort_org))
    except RuntimeError as exc:
        log_err(
            f"could not list {cohort_org}'s repos - falling back to a probe per repo: {exc}"
        )
        return None


def provision_one(
    cohort_org: str, handle: str, existing: frozenset[str] | None = None
) -> str:
    """Ensure a private grades-<handle> repo exists with the student as read collaborator.

    `existing` is the cohort's repo names off ONE listing (`_existing_repos`); membership
    in it answers "is this gradebook already there?" without a GET per student. None - no
    listing to hand - falls back to probing this one repo."""
    repo = f"{GRADEBOOK_PREFIX}{handle}"
    existed = (
        repo in existing if existing is not None else repo_exists(cohort_org, repo)
    )
    if existed:
        log_person(f"  [skip] gradebook {cohort_org}/{repo}")
    else:
        if not create_repo(
            cohort_org,
            repo,
            private=True,
            description=f"Private gradebook for @{handle}",
            person=True,
        ):
            return "failed-create"
        put_file(
            cohort_org, repo, "README.md", _STARTER_README.encode(), "init gradebook"
        )
        if not set_repo_topics(cohort_org, repo, ["gradebook"]):
            # Not named: this log is public. The nightly sweep converges the topic.
            log_err("  ! a gradebook is untagged - the nightly sweep converges it")

        # At creation only: a team grant does not decay, and the nightly sweep
        # (access.converge_faculty_access) owns the floor for every gradebook that already
        # exists - so re-granting on every sync cost two PUTs per student for nothing.
        #
        # Read, not write: `distribute` rewrites grades.yml from the grading sheet, so a
        # mark corrected here would be overwritten on the next run. The sheet is where a
        # mark belongs.
        grant_faculty(cohort_org, repo, FACULTY_READ_ACCESS, missing_is_note=True)
    if add_collaborator(cohort_org, repo, handle, permission="pull", person=True):
        log_person(f"  [ok]   + @{handle} (read)")
        return "skipped" if existed else "ok"
    # A gradebook the student can't open is a failure, not a partial success - the status
    # starts with "failed" so it reaches the exit code (see sync).
    log_err(f"  ! could not add @{handle} (not a real account?)")
    return "failed-no-collaborator"


def ensure_gradebooks(cohort_org: str, dry_run: bool = False) -> int:
    """Provision one private gradebook repo per onboarded enrolled student. Idempotent.

    Named for what it does, and called by `distribute` before anything is written into
    one: a student who onboarded since the last run has no repo to push a grade into, and
    the failure a moment later would say only "could not write".

    Auditors are read-only and are never assessed, so they get no gradebook."""
    students = roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - no gradebooks to sync.")
        return 1
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    log_step(f"Syncing {len(onboarded)} gradebook repo(s) in {cohort_org}")
    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, never assessed)")

    # ONE listing of the cohort answers "is it already there?" for every student below.
    # A dry run creates nothing, so it needs no answer.
    existing = None if dry_run else _existing_repos(cohort_org)
    results: dict[str, int] = {}
    for s in onboarded:
        if dry_run:
            log_person(f"    DRY-RUN  {cohort_org}/{GRADEBOOK_PREFIX}{s.github_handle}")
            continue
        status = provision_one(cohort_org, s.github_handle, existing)
        results[status] = results.get(status, 0) + 1
    if dry_run:
        return 0
    log_ok(f"Done - {json.dumps(results)}")
    return 1 if any(k.startswith("failed") for k in results) else 0


def sync(cohort_org: str, dry_run: bool = False) -> int:
    """`ensure_gradebooks` under the name the seeded workflows still call.

    UNDOCUMENTED and temporary: an org runs whichever toolkit ref its `central_ref` names,
    so a `sync-gradebooks.yml` written before the rename keeps invoking this until that
    org's nightly Refresh replaces the file. Removed in Phase 4, once every org has
    refreshed past this."""
    return ensure_gradebooks(cohort_org, dry_run=dry_run)


# ---------------------------------------------------------------- what was distributed

# One row per thing SAID, so a re-run says nothing twice and a failure is retried exactly
# once. It replaces `gradebook/notified.csv`, which recorded only the email and only per
# student - so a corrected grade re-emailed the whole cohort, and a comment that failed to
# post was never retried because nothing recorded that it had not.
DISTRIBUTED_PATH = f"{GRADEBOOK_DIR}/distributed.csv"
DISTRIBUTED_HEADER = (
    "target",  # a handle, or a TEAM name for a team-repo comment
    "assignment",  # the cohort-side slug; "" for the whole-book email
    "channel",
    "content_hash",
    "distributed_at",
)
CHANNEL_ISSUE = "issue"
CHANNEL_GRADEBOOK = "gradebook"
CHANNEL_EMAIL = "email"
# `{(target, assignment, channel): (content hash, when)}`
Distributed = dict[tuple[str, str, str], tuple[str, str]]

# The hidden marker on a feedback comment. Keyed on the CONTENT, so a corrected grade is a
# new comment and an unchanged one is silence - and so a lost `distributed.csv` costs one
# listing per repo rather than a duplicate comment for every student.
GRADE_MARK = "<!-- dsl-grade:{} -->"


def content_hash(text: str) -> str:
    """The short hash a feedback comment is marked with and `distributed.csv` records."""
    return blob_sha(text.encode())[:12]


def parse_distributed(text: str) -> Distributed:
    """`distributed.csv` into its lookup. Machine-written, but it sits in a repo faculty
    can edit, so it goes through the same BOM/delimiter guard as the roster."""
    return {
        (
            (row.get("target") or "").strip(),
            (row.get("assignment") or "").strip(),
            (row.get("channel") or "").strip(),
        ): (
            (row.get("content_hash") or "").strip(),
            (row.get("distributed_at") or "").strip(),
        )
        for row in read_csv(text, ("target",), DISTRIBUTED_PATH)
        if (row.get("target") or "").strip()
    }


def dump_distributed(records: Distributed) -> str:
    """Sorted, so a run that changed one row shows one line in the diff."""
    return dump_csv(
        DISTRIBUTED_HEADER,
        (
            (target, assignment, channel, digest, when)
            for (target, assignment, channel), (digest, when) in sorted(records.items())
        ),
    )


def _read_distributed(wd: Path) -> tuple[Distributed, bool]:
    """`(what has been distributed, whether this run is the migration)`.

    A cohort part-way through the term has `notified.csv` and no `distributed.csv`. Its
    rows become EMAIL rows here, so nobody is emailed again for a book they already know
    about - the hash is over different bytes now, so the first run after the migration
    does re-tell everyone once; that is what `--no-notify` is for."""
    live = wd / DISTRIBUTED_PATH
    if live.is_file():
        return parse_distributed(live.read_text()), False
    old = wd / NOTIFIED_PATH
    if not old.is_file():
        return {}, False
    # read_csv, not a bare DictReader: the file is machine-written, but it sits in a repo
    # faculty can edit, so it goes through the same BOM/delimiter guard as the roster.
    return {
        ((row.get("github_handle") or "").strip(), "", CHANNEL_EMAIL): (
            (row.get("grades_sha") or "").strip(),
            (row.get("notified_at") or "").strip(),
        )
        for row in read_csv(old.read_text(), ("github_handle",), NOTIFIED_PATH)
        if (row.get("github_handle") or "").strip()
    }, True


def _retired_gradebook_files(wd: Path) -> list[str]:
    """The per-student YAML the retired `render` staged for its preview PR. The gradebook
    repos hold the real thing now, and a stale copy of a grade is worse than none."""
    folder = wd / GRADEBOOK_DIR
    if not folder.is_dir():
        return []
    return sorted(f"{GRADEBOOK_DIR}/{p.name}" for p in folder.glob("*.yml"))


def load_legacy_grades(wd: Path) -> dict[str, list[GradeRow]]:
    """Every `grades/<slug>.csv` in a classroom-config checkout, keyed by slug.

    The transition reader: a cohort that began marking before the grading sheet existed
    keeps distributing from the table it is using. Raises nothing - a CSV on the retired
    column names names itself and is skipped, because a partial read would publish a
    gradebook with that assignment silently missing."""
    folder = wd / GRADES_DIR
    if not folder.is_dir():
        return {}
    out: dict[str, list[GradeRow]] = {}
    for path in sorted(folder.glob("*.csv")):
        try:
            out[path.stem] = parse_grades(path.read_text(encoding="utf-8"))
        except RetiredGradeHeader as exc:
            log_err(f"{GRADES_DIR}/{path.name}: {exc}")
    return out


def _spec_from_sheet(slug: str, sheet: dict) -> SheetSpec:
    """A minimal spec for a sheet whose assignment the schedule no longer declares - a
    term whose entry has been deleted, or a hand-written sheet. Its shape is read off the
    file itself so the marks still reach their students; the maxima and the late policy
    are simply unknown, and nothing is derived from them."""
    return SheetSpec(slug=slug, title=slug, is_group="teams" in (sheet or {}))


def sheet_specs(course_org: str, sched) -> dict[str, SheetSpec]:
    """One spec per assignment the cohort's schedule declares, keyed by its COHORT-side
    name - which is what the sheets, the repos and the gradebooks are all named after."""
    specs: dict[str, SheetSpec] = {}
    for key, entry in sched.assignments.items():
        name = schedule.cohort_name(key, entry)
        gspec = (
            load_grading_spec(course_org, entry.course_source_repo)
            if course_org
            else dict(_DEFAULT_SPEC)
        )
        specs[name] = sheet_spec(
            sched,
            key,
            name,
            gspec,
            resolve_is_group(
                force=False,
                schedule_type=entry.type,
                template_group=gspec["type"] == "group",
            ),
        )
    return specs


def _adjusted_count(spec: SheetSpec, sheet: dict) -> int:
    """How many individual adjustments a grader has written into one sheet - the dry run
    reports it, because an adjustment is the one thing in the file no arithmetic explains."""
    total = 0
    for block in ((sheet or {}).get(spec.container_key) or {}).values():
        if not isinstance(block, dict):
            continue
        people = (block.get("members") or {}) if spec.is_group else {"": block}
        total += sum(
            1
            for person in people.values()
            if isinstance(person, dict)
            and not _blank(person.get("adjustment_individual"))
        )
    return total


def _issue_targets(
    spec: SheetSpec, sheet: dict, books: dict[str, dict[str, dict]]
) -> list[tuple[str, str, str]]:
    """`(target, submission repo, comment body, members)` for every unit of one assignment.

    A TEAM's comment is built from `TeamResult`, which has no member fields at all, so the
    thing that must never appear in a repo the whole team can read cannot be put there by
    mistake. An individual's comment is built from their own allowlisted view. The members
    come along because this may be the moment the Feedback issue is first opened, and a
    team's body names them."""
    out: list[tuple[str, str, str, list[str]]] = []
    for unit, block in ((sheet or {}).get(spec.container_key) or {}).items():
        if not isinstance(block, dict):
            continue
        repo = submission_repo(spec.slug, unit)
        if spec.is_group:
            result = team_result(spec, unit, block)
            if _blank(result.team_score) and _blank(result.feedback_group):
                continue  # nothing marked yet; a bare heading tells a team nothing
            members = list(block.get("members") or {})
            out.append((unit, repo, team_issue_body(spec.title, result), members))
        else:
            view = (books.get(unit) or {}).get(spec.slug) or {}
            if not view:
                continue
            out.append((unit, repo, individual_issue_body(spec.title, view), [unit]))
    return out


def _hold_undecided(
    books: dict[str, dict[str, dict]], reasons: dict[str, dict[str, str]]
) -> dict[str, dict[str, tuple[str, str]]]:
    """Take every mark that still needs a person out of the books, and say whose it was.

    Two kinds arrive here. `reasons` is what a grader TYPED that cannot be acted on
    (`sheet_hold_reasons`); the rest is arithmetic that could not be done - `pass`, two
    days late, is not `pass minus 20%`. Sending either puts a line a student can read
    where a decision should have been, and the dry run that counted it has already gone
    by. So the mark is HELD: no comment, no gradebook entry, no column in the registrar's
    export, until a grader settles it in the sheet. Nothing else that student has is held
    with it.

    Returns `{slug: {handle: (submission unit, reason)}}` - the unit, because a group's
    comment is built from the team block rather than from any member's view, so it has to
    be skipped by name.
    """
    held: dict[str, dict[str, tuple[str, str]]] = {}
    for handle, book in books.items():
        for slug, view in list(book.items()):
            reason = (reasons.get(slug) or {}).get(handle) or (
                "penalty" if needs_hand_decision(view) else ""
            )
            if not reason:
                continue
            held.setdefault(slug, {})[handle] = (view.get("team") or handle, reason)
            del book[slug]
    # A student whose ONLY mark was held has nothing to be sent: leaving the empty book in
    # would commit a gradebook page with nothing new on it and email them about it.
    for handle in [h for h, book in books.items() if not book]:
        del books[handle]
    return held


def _gradebook_files(
    handle: str, book: dict[str, dict], titles, timed
) -> dict[str, bytes]:
    """The two files a student's private gradebook holds: the data and the page."""
    return {
        "grades.yml": render_yaml({"student": handle, "assignments": book}).encode(),
        "README.md": render_readme(handle, book, titles, timed).encode(),
    }


def distribute(cohort_org: str, notify: bool = True, dry_run: bool = False) -> int:
    """Send every mark a grader has written where it has to go: a feedback comment on each
    submission repo's Feedback issue, each student's private gradebook, the registrar's
    export, and an email saying there is something new to read.

    ONE clone of classroom-config and one pass over it - the sheets, the transition CSVs
    and `distributed.csv` are all read locally, so the only per-student calls left are the
    writes. Every one of those is skipped when `distributed.csv` says the same content has
    already gone out, which is what makes a correction to one grade reach one student.

    Dry run - the default - reads everything, writes nothing, posts nothing, sends nothing,
    and prints the counts a grader checks before pressing it for real."""
    provisioning_failed = bool(ensure_gradebooks(cohort_org, dry_run=dry_run))
    course_org = course_org_for_cohort(cohort_org)
    sched = schedule.load(cohort_org)
    students = roster.load(cohort_org)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "cfg"
        if not clone(cohort_org, CONFIG_REPO, wd):
            log_err(f"could not clone {cohort_org}/{CONFIG_REPO}")
            return 1
        try:
            sheets = load_sheets(wd)
        except SheetUnreadable as exc:
            # Nothing goes out, rather than everything but this one: a grader fixes the
            # file and presses the button again, where a partial send would have to be
            # reconciled student by student.
            log_err(f"{exc} - nothing distributed; fix the file and run this again")
            return 1
        legacy = load_legacy_grades(wd)
        if not sheets and not legacy:
            log_err(
                f"no {SHEETS_DIR}/ or {GRADES_DIR}/ in {cohort_org}/{CONFIG_REPO} - hand "
                f"out an assignment (which creates its grading sheet) first"
            )
            return 1
        specs = sheet_specs(course_org, sched)
        sources: dict[str, tuple[SheetSpec, dict] | list[GradeRow]] = {}
        for slug, sheet in sheets.items():
            specs.setdefault(slug, _spec_from_sheet(slug, sheet))
            sources[slug] = (specs[slug], sheet)
        for slug, rows in legacy.items():
            # A slug with both is the SHEET's - that is the file a grader was told to type
            # in, and the CSV beside it is what they were typing in before.
            sources.setdefault(slug, rows)
        titles = {
            slug: specs[slug].title if slug in specs else slug for slug in sources
        }
        # Which assignments know when the work came in. A legacy CSV knows nothing about
        # timing, so its README cell is BLANK - "not submitted" there would be this
        # module asserting something it has no source for.
        timed = frozenset(sheets)
        books = build_gradebooks(sources)
        distributed, migrating = _read_distributed(wd)
        retired = _retired_gradebook_files(wd) if migrating else []

    held = _hold_undecided(
        books,
        {
            slug: sheet_hold_reasons(specs[slug], sheet)
            for slug, sheet in sheets.items()
        },
    )
    log_step(f"Distributing {len(books)} gradebook(s) in {cohort_org}")
    record: Distributed = dict(distributed)
    counts = {
        "comments": 0,
        "gradebooks": 0,
        "emails": 0,
        "held": sum(len(whose) for whose in held.values()),
        "skipped": 0,
        "failed": 0,
    }
    for slug in sorted(held):
        for handle, (_unit, reason) in sorted(held[slug].items()):
            log_person(
                f"  [hold] {slug} for {handle} - {HOLD_REASONS[reason]}; nothing is sent "
                f"until it is settled in the sheet"
            )

    # 1. The feedback comment on each submission repo's Feedback issue. Sheet-backed
    #    assignments only: a legacy CSV has no submission-unit structure to post against,
    #    and its marks reach the student through the gradebook instead.
    for slug in sorted(sheets):
        spec = specs[slug]
        withheld = {unit for unit, _reason in held.get(slug, {}).values()}
        for target, repo, body, members in _issue_targets(spec, sheets[slug], books):
            if target in withheld:
                # A team's comment is built from the team block, not from a member's view,
                # so pulling the views is not enough to keep this one back.
                continue
            digest = content_hash(body)
            if record.get((target, slug, CHANNEL_ISSUE), ("",))[0] == digest:
                continue  # already said, in these words
            if dry_run:
                counts["comments"] += 1
                continue
            issue = ensure_feedback_issue(
                cohort_org, repo, feedback_body(spec, target, members)
            )
            if issue is None:
                # A submission repo that is not there (a student who never onboarded, a
                # team formed after the handout). Counted, never named.
                counts["skipped"] += 1
                continue
            if post_marked_comment(
                cohort_org, repo, issue, body, GRADE_MARK.format(digest)
            ):
                record[(target, slug, CHANNEL_ISSUE)] = (digest, now)
                counts["comments"] += 1
                log_person(f"  [ok] feedback on {cohort_org}/{repo}#{issue}")
            else:
                counts["failed"] += 1

    # 2. The private gradebook: grades.yml and README.md in ONE commit per student, so a
    #    student never sees a page that disagrees with the data beside it.
    #
    #    A handle earns its place in `live` only where the gradebook really does hold that
    #    content. `live` is what step 3 keys the email on, so a student whose write FAILED
    #    must not be told to go and read a page that never changed - and, worse, have that
    #    telling recorded, which would stop them being told when it lands.
    live: dict[str, str] = {}
    for handle in sorted(books):
        files = _gradebook_files(handle, books[handle], titles, timed)
        digest = content_hash("".join(f.decode() for f in files.values()))
        if record.get((handle, "", CHANNEL_GRADEBOOK), ("",))[0] == digest:
            live[handle] = digest
            continue
        if dry_run:
            live[handle] = digest
            counts["gradebooks"] += 1
            continue
        if put_files(
            cohort_org,
            f"{GRADEBOOK_PREFIX}{handle}",
            files,
            "grades: update",
        ):
            record[(handle, "", CHANNEL_GRADEBOOK)] = (digest, now)
            live[handle] = digest
            counts["gradebooks"] += 1
            log_person(f"  [ok] {GRADEBOOK_PREFIX}{handle}")
        else:
            counts["failed"] += 1

    # 3. Who still needs telling. Keyed on the gradebook's content, so a student whose
    #    book did not change is not emailed and one whose email FAILED last time is.
    pending = [
        handle
        for handle, digest in sorted(live.items())
        if record.get((handle, "", CHANNEL_EMAIL), ("",))[0] != digest
    ]

    if dry_run:
        _preview(cohort_org, sheets, specs, books, counts, pending, notify, held)
        return 1 if provisioning_failed else 0

    failed_mail, told = (
        _email_updates(cohort_org, pending, dry_run=False)
        if notify and pending
        else (0, [])
    )
    counts["emails"] = len(told)
    for handle in told:
        record[(handle, "", CHANNEL_EMAIL)] = (live[handle], now)

    # 4. The registrar's export and the record of what went out, in ONE commit - together
    #    with the retired files this cohort is migrating off, so the old and the new can
    #    never both be present for a reader to choose between.
    writes = {DISTRIBUTED_PATH: dump_distributed(record).encode()}
    if students is None:
        # `roster.load` answers None for a roster it could not READ, and the export is one
        # row per ENROLLED student - so regenerating it from no rows would commit a header
        # line over the file a registrar transcribes grades from. Leaving it is the only
        # safe answer; the run goes red and the next one rebuilds it.
        log_err(
            f"roster in {cohort_org} could not be read - {COHORT_CSV_NAME} left as it is"
        )
    else:
        writes[COHORT_CSV_NAME] = render_registrar_csv(students, books).encode()
    recorded = put_files(
        cohort_org,
        CONFIG_REPO,
        writes,
        f"grades: distribute ({counts['comments']} comment(s), "
        f"{counts['gradebooks']} gradebook(s), {counts['emails']} email(s))",
        delete=([NOTIFIED_PATH, *retired] if migrating else []),
    )
    if not recorded:
        log_err(
            f"grades were distributed but {DISTRIBUTED_PATH} could not be written - the "
            f"next run re-posts and re-emails what it cannot see was already sent"
        )
    # Counts only: this workflow's log is world-readable and every target here is a
    # student. The per-target lines above went through log_person.
    log_ok(f"Done - {json.dumps(counts)}")
    return (
        1
        if provisioning_failed
        or counts["failed"]
        or failed_mail
        or not recorded
        or students is None
        else 0
    )


def _preview(
    cohort_org: str,
    sheets: dict[str, dict],
    specs: dict[str, SheetSpec],
    books: dict[str, dict[str, dict]],
    counts: dict[str, int],
    pending: list[str],
    notify: bool,
    held: dict[str, dict[str, tuple[str, str]]],
) -> None:
    """The dry run's report: what a real run would do, in counts a grader can check.

    No names, and no marks: this is the log of a workflow that runs in a PUBLIC repo. The
    sample email is rendered from placeholders, never from a student."""
    for slug in sorted(sheets):
        spec = specs[slug]
        units = (sheets[slug] or {}).get(spec.container_key) or {}
        views = [book[slug] for book in books.values() if slug in book]
        # Only rows that have been MARKED are counted as grades: an unmarked one is
        # neither a grade this run would derive nor a decision anybody has to take, and
        # counting it as either told a grader the sheet was further on than it is. The
        # held ones are no longer in the books - they are added back to the head count
        # here, because they are still students on this assignment.
        marked = [v for v in views if "final_grade" in v]
        hand = len(held.get(slug, {}))
        team_clause = f" in {len(units)} team(s)" if spec.is_group else ""
        log(
            f"  {slug}: {len(views) + hand} student(s){team_clause} · {len(marked)} "
            f"final grade(s) derived, {_adjusted_count(spec, sheets[slug])} adjusted, "
            f"{hand} held for a hand decision"
        )
        # Named, not just counted: "1 held" tells a grader to go looking without saying
        # what for, and every one of these is a thing they typed and can fix in a minute.
        tally = Counter(reason for _unit, reason in held.get(slug, {}).values())
        for reason, count in sorted(tally.items()):
            log(f"    {count} with {HOLD_REASONS[reason]}")
        log(f"  {COHORT_CSV_NAME}: would gain column {slug}")
    log(
        f"  would post {counts['comments']} comment(s), update "
        f"{counts['gradebooks']} gradebook(s), email "
        f"{len(pending) if notify else 0} student(s)"
    )
    if notify and pending:
        # Rendered exactly as the send renders it, course name and all: a preview that
        # showed the generic wording while the real mail named the course was reviewing
        # text nobody would ever receive - and the subject line, which is the half a
        # student reads first, was not shown at all.
        subject, body = sample_message(cohort_org, _course_name(cohort_org))
        log("  Sample email (placeholders, not a real student):")
        log(f"    Subject: {subject}")
        for line in body.splitlines():
            log(f"    {line}")
    log_ok("DRY-RUN - nothing written, nothing posted, nothing sent")


def update_message(
    student: roster.Student, cohort_org: str, course_name: str = ""
) -> mailer.Message:
    """The 'your grades have been updated' email for one student: (to, subject, body).

    The course goes in the SUBJECT as well as the body: the inbox list is where a student
    taking several of these actually tells them apart, and by the time they have opened it
    the body is redundant. A course that carries no name yet keeps the generic wording
    rather than emailing a blank."""
    url = f"https://github.com/{cohort_org}/{GRADEBOOK_PREFIX}{student.github_handle}"
    course_suffix = f" for {course_name}" if course_name else ""
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"Your grades{course_suffix} have been updated. View them in your private "
        f"gradebook:\n"
        f"  {url}\n"
    )
    subject = (
        f"Your grades for {course_name} have been updated"
        if course_name
        else "Your grades have been updated"
    )
    return (student.hertie_email, subject, body)


def sample_message(cohort_org: str, course_name: str = "") -> tuple[str, str]:
    """The notification's `(subject, body)` rendered with PLACEHOLDERS, for the preview.

    `update_message` with a placeholder in place of a student - see `mailer.sample_of`."""
    return mailer.sample_message_of(
        lambda student: update_message(student, cohort_org, course_name),
        github_handle="<handle>",
    )


def sample_body(cohort_org: str, course_name: str = "") -> str:
    """The body alone - what `send_bulk` prints beneath a dry-run send."""
    return sample_message(cohort_org, course_name)[1]


def _course_name(cohort_org: str) -> str:
    """The course's name for the subject and the body of an email, or "" if it cannot be
    read.

    Never fatal, and never skipped by the preview: the grades are already pushed by the
    time the send runs, so a transient read failure or a malformed dsl-course.yml must not
    turn a successful distribution into a traceback with zero notifications sent
    (`load_yaml_config` deliberately RAISES on both). A course that carries no name yet
    keeps the generic wording rather than emailing a blank."""
    try:
        return course_name_for_cohort(cohort_org)
    except Exception as exc:  # a name is never worth losing the notifications over
        log_err(f"could not read the course name ({exc}) - the email goes without it")
        return ""


def _email_updates(
    cohort_org: str, handles: list[str], dry_run: bool = False
) -> tuple[int, list[str]]:
    """Email each student a 'grades updated' notification to their hertie email address,
    linking to their private gradebook repo (the grade's source of truth).

    Returns `(how many FAILED, which handles were told)`. `distribute` exits on the first
    and records the second: the grades themselves are already pushed by this point, so a
    mail failure is not a reason to undo anything - but a student who never got the
    notification does not know to look, and a green run told nobody."""
    # Fold-keyed: the gradebook names come from what a marker typed into the sheet and the
    # roster's casing is its own, so a case-only difference used to mean a student was
    # silently never told their grades had landed.
    students = roster.load(cohort_org)
    if students is None:
        # Distinct from an empty roster: unreadable must red, as it does in enrol_codes.run.
        log_err(
            f"roster in {cohort_org} could not be read - "
            f"{len(handles)} notification(s) not sent."
        )
        return len(handles), []
    by_handle: dict[str, roster.Student] = {}
    for s in students:
        if s.github_handle:
            by_handle.setdefault(s.github_handle.casefold(), s)
    # Name the course in the body - a student taking several of these can't tell one
    # "your grades have been updated" from another. Read live from the course org's
    # dsl-course.yml; a course that carries no name yet keeps the generic wording rather
    # than emailing a blank.
    course_name = _course_name(cohort_org)
    messages = []
    # Keyed on the ADDRESS, holding every handle that maps to it: two roster rows sharing
    # an address (one student, two accounts) would otherwise record only the last, leaving
    # the first permanently unrecorded and re-notified on every run.
    handles_for: dict[str, list[str]] = {}
    for handle in handles:
        student = by_handle.get(handle.casefold())
        if not student or not student.hertie_email:
            continue
        email = student.hertie_email.strip().casefold()
        if email in handles_for:
            handles_for[email].append(handle)
            continue
        handles_for[email] = [handle]
        messages.append(update_message(student, cohort_org, course_name))
    if not messages:
        # A withdrawn student is an ordinary state and must not red every distribution
        # from here on; a count says it happened without naming anyone.
        if handles:
            log_err(f"{len(handles)} gradebook(s) have no roster row with an email")
        return 0, []
    sent = mailer.send_bulk(
        messages, dry_run=dry_run, sample=sample_body(cohort_org, course_name)
    )
    failed = len(messages) - len(sent)
    if failed:
        log_err(f"{failed} of {len(messages)} grade notification(s) not sent")
    return failed, [
        h for to in sent for h in handles_for.get(to.strip().casefold(), [])
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("sync", "distribute"):
        p = sub.add_parser(name)
        p.add_argument("--cohort-org", required=True)
        if name == "sync":
            p.add_argument("--dry-run", action="store_true")
        if name == "distribute":
            p.add_argument(
                "--no-notify",
                action="store_true",
                help="Skip the email notification (just push the grades).",
            )
            # Default ON: the rendered workflow passes --dry-run / --no-dry-run
            # explicitly, so a bare local invocation cannot send by accident.
            # `sync --dry-run` above keeps store_true - it is not a mail path.
            p.add_argument(
                "--dry-run",
                action=argparse.BooleanOptionalAction,
                default=True,
                help="Preview the grade emails; push nothing, send nothing (default).",
            )
    args = parser.parse_args()

    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        if args.action == "sync":
            return sync(args.cohort_org, dry_run=args.dry_run)
        return distribute(
            args.cohort_org, notify=not args.no_notify, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
