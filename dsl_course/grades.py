"""dsl-course grades -- the grading sheet, and sending what a grader wrote in it.

A grader fills ONE file per assignment, `semester-config/grading_sheets/<slug>.yml`, and
`distribute` fans it out:

    grading_sheets/<slug>.yml   (the grader types here; the toolkit owns only `info:`)
          |
          +--> semester/grades-<handle>   (private; student = read) grades.yml + README.md
          +--> semester-config/.system/semester-gradebook.csv   (the registrar export, never logged)
          +--> an email saying there is something new to read (no marks in it)

ONE place a mark is written, and it is the student's gradebook. Nothing is posted into a
submission repo; that repo's issue carries the submission receipts and nothing else.

Nothing is said twice: every send is recorded in `.system/gradebook/distributed.csv`, so a re-run
after one correction reaches one student.

Usage:
    python3 -m dsl_course.grades distribute --semester-org hertie-dsl-demo-f2026 [--preview]
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import textwrap
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import yaml

from . import gh_teams, mailer, policy, records, roster, schedule, settings
from .access import FACULTY_READ_ACCESS, grant_faculty
from .course import (
    CONFIG_REPO,
    GRADEBOOK_PREFIX,
    MARKS_RETURNED_NOTE,
    NO_STARTER,
    NO_TEAMS,
    RECEIPTS_ISSUE_LABEL,
    RECEIPTS_ISSUE_LABELS,
    RECEIPTS_ISSUE_MARKS,
    RECEIPTS_ISSUE_TITLE,
    SELF_SELECT,
    SOLUTION_BRANCH,
    can_hold_solution,
    collects_commits,
    course_phrase,
    creates_repos,
    creates_unit_repos,
    has_receipts_issue,
    identifier,
    marks_returned_marker,
    receipt_body,
    receipts_issue_body,
    resolve_is_group,
    row_name,
    submission_repo,
    submit_shape,
    visibility_is_students,
)
from .discovery import (
    assignment_rows,
    course_name_for_semester,
    course_org_for_semester,
    exists_in,
    listing_by_name,
    listing_row,
)
from .faults import NOT_MIGRATED, ConfigFault, NotMigrated
from .gh_contents import (
    blob_sha,
    dump_csv,
    get_file_content,
    get_file_with_sha,
    put_file,
    put_files,
    read_csv,
    yaml_mark_line,
    yaml_problem,
)
from .ghcli import bot_login, clone, gh, is_missing_resource
from .issues import close_issues_titled, upsert_issue
from .log import (
    CLIParser,
    Summary,
    add_preview_flag,
    log,
    log_err,
    log_ok,
    log_person,
    log_step,
    plural,
)
from .repos import (
    add_collaborator,
    create_repo,
    ensure_label,
    listed_is_private,
    repo_is_archived,
    set_repo_topics,
)
from .settings import (
    SPEC_KEYS,
    Dropped,
    as_decimal,
    penalty_fault,
    read_settings,
    refuse_renamed,
)

GRADEBOOK_DIR = records.path(
    "gradebook"
)  # what has been sent, beside the retired files
# RETIRED. The old per-student notification marker, named here for one reason only: the
# migration in `_read_distributed` reads it once and deletes it in the same commit that
# writes `distributed.csv`, which records every channel rather than just the email.
NOTIFIED_PATH = f"{GRADEBOOK_DIR}/notified.csv"
SEMESTER_CSV_NAME = records.path(
    "semester_gradebook"
)  # the wide faculty-only glance view

# What a gradebook says before its student has been marked in anything. The legend names
# the keys `STUDENT_VIEW_KEYS` allows and no others: this is the first file a student opens,
# and promising them a team score or their own adjustment - neither of which a gradebook
# ever shows - contradicts the one thing the page is for. Replaced wholesale by
# `render_readme` on the first distribute.
_STARTER_README = (
    "# Your gradebook\n\n"
    "This private repository is viewable only by you. Grades and feedback for each "
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
    "| `team_feedback` | Group assignments only: feedback shared with the whole team. |\n"
)


# --------------------------------------------------------------------------- pure core


def render_yaml(book: dict) -> str:
    """Serialise one student's gradebook to YAML text (insertion order preserved)."""
    return yaml.safe_dump(book, sort_keys=False, allow_unicode=True)


# ------------------------------------------------------------------- the grading sheet

# `semester-config/grading_sheets/<slug>.yml` is the ONE place a grader types. One file per
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


class SheetUnreadable(RuntimeError):
    """A grading sheet whose YAML nobody can read - a grader mid-edit.

    Its own class because the answer to it is specific and is the same everywhere: LEAVE
    THE FILE ALONE. A parse that came back empty instead would read as "this assignment
    has no rows", and the next write would rebuild the sheet blank over a term's marking.

    A `RuntimeError`, so the CLIs that already answer one with a message rather than a
    traceback - `status.main`, `grades.main` - answer this the same way. A sheet mid-edit
    is an ordinary state, not a crash."""


def sheet_path(slug: str) -> str:
    """Where this assignment's grading sheet lives in `semester-config`."""
    return f"{SHEETS_DIR}/{slug}.yml"


class _Shape:
    """The rules that follow from an assignment's shape, for the two specs that carry one.

    A mixin rather than four properties written twice: `GradingSpec` is the file and
    `SheetSpec` is what the sheet keeps of it, and a rule spelt in both would be two
    answers to one question. Not a dataclass - `submit_via` and `visibility` are fields of
    whichever spec mixes this in."""

    submit_via: str
    visibility: str
    # Whether the two above were READ from an assignment's definition at all. A spec that
    # carries no such field was built from one, which is the ordinary case; `SheetSpec`
    # redeclares it as a field because a sheet whose assignment the schedule no longer
    # declares has to say so (`_spec_from_sheet`).
    shape_known: bool = True

    @property
    def submit_external(self) -> bool:
        """Handed in off GitHub (Moodle, Kaggle, in class), so no repo is created at all
        and nothing is ever collected."""
        return self.submit_via == "external"

    @property
    def submit_shared(self) -> bool:
        """Handed in by pushing into a folder of ONE private drop box the whole semester
        shares, rather than into a repo of the unit's own."""
        return self.submit_via == "shared_dropbox_repo"

    @property
    def submit_shape(self) -> str:
        """The one word the semester site branches this assignment on."""
        return submit_shape(self.submit_via, self.visibility)

    @property
    def has_receipts_issue(self) -> bool:
        """Whether this assignment's units have a Submission receipts issue to post into."""
        return has_receipts_issue(self.submit_via, self.visibility)

    @property
    def may_open_receipts_issue(self) -> bool:
        """Whether this run may OPEN a Submission receipts issue in a unit's repo.

        Two conditions, and the second is the one easily lost: the shape must HAVE a
        Submission receipts issue, and the shape must have been read from a real definition. A sheet
        whose assignment the schedule no longer declares falls back to the defaults -
        `assignment_repo` + `private` - and a guess may keep writing in the thread a
        student was told to read, but must never open a second one over it."""
        return self.shape_known and self.has_receipts_issue

    @property
    def collects_commits(self) -> bool:
        """Whether there is anything to freeze, time or grade against the cutoff."""
        return collects_commits(self.submit_via)

    @property
    def creates_unit_repos(self) -> bool:
        """Whether each unit has a repo of its own to name, link to and grant on."""
        return creates_unit_repos(self.submit_via)

    @property
    def creates_repos(self) -> bool:
        """Whether the handout creates anything at all for the work to land in."""
        return creates_repos(self.submit_via)

    @property
    def can_hold_solution(self) -> bool:
        """`course.can_hold_solution` - whether the model answer may be pushed into this
        assignment's repos at all."""
        return can_hold_solution(self.submit_via, self.visibility)

    @property
    def visibility_is_students(self) -> bool:
        """Whether the STUDENT owns this repo's visibility rather than the toolkit."""
        return visibility_is_students(self.visibility)


@dataclass(frozen=True)
class SheetSpec(_Shape):
    """What the sheet needs to know about one assignment: its `grading_config.yml`
    definition, plus the two `schedule.yml` moments already rendered for a human to read.

    `questions` maps a question name to its maximum AS WRITTEN - text, never a number. The
    maxima are a display, and a course that writes `1.5` must see back what it wrote rather
    than this module's idea of how to print it."""

    slug: str
    title: str
    is_group: bool
    # The assignment's SHAPE, carried verbatim so the `_Shape` rules above are the
    # vocabulary's own rather than a second spelling of them here. The defaults are what an
    # assignment that says nothing gets: one private repo per unit.
    submit_via: str = "assignment_repo"
    visibility: str = "private"
    # False for a sheet whose assignment the schedule no longer declares
    # (`_spec_from_sheet`): the shape above is then a guess, and the one thing a guess may
    # never do is open a Submission receipts issue in a student's repo (`may_open_receipts_issue`).
    shape_known: bool = True
    questions: dict[str, str] | None = None
    late_window_days: int | None = None
    late_penalty_per_day: str | None = None
    autograde: bool = False
    completion_check: bool = False
    due_display: str = ""
    cutoff_display: str = ""
    # The same two moments spelt out in full - `Sunday 4 October 2026, 23:59
    # (Europe/Berlin)`. The Submission receipts issue uses these: a student reads that line once and
    # has to act on it, where a grader scans the sheet's header and wants it short.
    due_long: str = ""
    cutoff_long: str = ""
    # The due moment itself rather than a rendering of it, because one reader has to
    # COMPARE it with the clock: `distribute` says out loud when a mark is going out
    # before the submission facts behind it exist (`_undue_marks`). None for a sheet whose
    # assignment the schedule no longer declares - there is no date to read.
    due_at: datetime | None = None

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
    `contributions` exists only where CONTRIBUTIONS.md does, `autograde` only where tests
    will run and `completion` only where a notebook is executed at the cutoff - an
    always-blank key is a question a grader keeps re-asking.

    Two toolkit facts are deliberately NOT here, because they have nothing to say until
    there is something to say: `checked` (the minute this unit was last read) appears from
    the first derivation, and `submitted_note` only where the server's push time
    contradicts the commit's own date. `_merged_block` unions whatever was derived over
    this, so both arrive the moment they exist."""
    info: dict = {"submitted": None, "days_late": None}
    if spec.is_group:
        info["contributions"] = None
    if spec.autograde:
        info["autograde"] = None
    if spec.completion_check:
        info["completion"] = None
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

    An assignment that collects no commits - one handed in somewhere else - has nothing to
    time and so no `info:` block at all, rather than one full of blanks, which reads as a
    toolkit that tried to fill it and failed."""
    block: dict = {}
    if spec.collects_commits:
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
    elif spec.collects_commits:
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
    feedback, keys they invented, and blocks for units that have since left the semester (a
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
    `str`. The arithmetic never needed the typing - `as_decimal` parses the text - and this
    is what lets `adjustment_individual: +4` still say `+4` after a refresh.

    A SUBCLASS, like `_SheetDumper`: `yaml.safe_load` is called all over this package and
    must keep reading ordinary YAML."""


_SheetLoader.yaml_implicit_resolvers = _null_only(
    yaml.SafeLoader.yaml_implicit_resolvers
)


def _no_duplicate_keys(loader: yaml.SafeLoader, node, deep: bool = False) -> dict:
    """YAML's mapping rule, minus last-one-wins.

    PyYAML keeps the LAST of two identical keys and says nothing. In a file two people
    hand-edit in a browser that is a mark silently discarded - the mock-up's own worked
    example has `adjustment_individual` twice in one block - so the sheet is refused
    instead and left exactly as it is until somebody fixes it.

    `flatten_mapping` first, so an anchor merge (`<<`) still behaves as YAML says."""
    loader.flatten_mapping(node)
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            # The key is NOT named. In a grading sheet the key of a unit is a student
            # handle (or a team name), and this message reaches a public run log, a
            # public issue and an email - see the privacy rule in CLAUDE.md. The line
            # the mark carries is what somebody needs anyway.
            raise yaml.constructor.ConstructorError(
                "while reading a grading sheet",
                node.start_mark,
                "the key on this line appears twice - the second copy would "
                "silently replace the first",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_SheetLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys
)


def _unreadable(exc: yaml.YAMLError) -> str:
    """`yaml_problem`, plus the line - what a reader with no other citation is told."""
    problem, line = yaml_problem(exc), yaml_mark_line(exc)
    if not problem:
        return "the file is not valid YAML"
    return f"{problem} (line {line})" if line else problem


def parse_sheet(
    text: str, faults: list[ConfigFault] | None = None, slug: str = ""
) -> dict:
    """A grading sheet's YAML into a dict ({} when the file is empty).

    Raises `SheetUnreadable` rather than returning anything for a file that does not parse
    or is not a mapping. This is hand-typed YAML in a repo a grader edits in the browser,
    so a broken save is ordinary; what must never happen is the toolkit reading one as an
    empty sheet and writing a blank file back over it.

    A caller that passes `faults` gets the same thing RECORDED as well as raised - the
    raise is what every reader already does the right thing about (leave the file alone),
    and the fault is what reaches the grader who saved it. Recorded first, so the caller
    catching the error still has it. `slug` names the sheet the fault is in."""
    try:
        data = yaml.load(text, Loader=_SheetLoader) or {}
    except yaml.YAMLError as exc:
        if faults is not None:
            problem = yaml_problem(exc)
            faults.append(
                _sheet_fault(
                    slug,
                    "this sheet is not valid YAML, so nothing on it is refreshed or "
                    "sent" + (f": {problem}" if problem else ""),
                    lineno=yaml_mark_line(exc),
                    fix="fix the YAML on the line above; nothing on this sheet is "
                    "refreshed or sent until it parses",
                    plain=f"The {slug} marking sheet is not valid YAML, so nothing on "
                    f"it is updated or returned.",
                )
            )
        raise SheetUnreadable(_unreadable(exc)) from exc
    if not isinstance(data, dict):
        if faults is not None:
            faults.append(
                _sheet_fault(
                    slug,
                    "this sheet is not a mapping of submission units, so it is left "
                    "exactly as it is",
                    fix="restore the sheet's shape - a `submissions:` (or `teams:`) "
                    "block of one entry per submission unit",
                    plain=_SHEET_SHAPE_PLAIN.format(sheet=slug),
                )
            )
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
    maxima = [as_decimal(maximum) for maximum in questions.values()]
    if None in maxima:
        return f"({listed})"
    return f"{_plain(sum(maxima, Decimal(0)))} points ({listed})"


def _config_facts(spec: SheetSpec) -> list[str]:
    """The assignment's definition as the two lines a grader reads before marking. Every
    one of them is edited in `grading_config.yml` or `schedule.yml`, never here."""
    shape = ["group assignment" if spec.is_group else "individual assignment"]
    if spec.questions:
        shape.append(_points_clause(spec.questions))
    if not spec.collects_commits:
        shape.append("submitted outside GitHub")
    shape.append("autograde on" if spec.autograde else "autograde off")
    timing = [f"due {spec.due_display}"] if spec.due_display else []
    if not spec.collects_commits:
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
    if not spec.collects_commits:
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
        clauses.append("`autograde` fills once at the late cutoff")
    return (
        "Auto-filled by the toolkit (you never type these): every `info:` block. "
        + "; ".join(clauses)
        + ". All of them freeze at the late cutoff."
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
        f"reaches a student until you run Distribute grades. YAML comments you add are "
        f"not preserved when the toolkit rewrites the file."
    )


# The sheet says its own state in its header, and `collect` reads it back to decide
# whether the file has already been sealed. A contract, therefore, not a formatting choice:
# both spellings live here so a reworded header cannot silently un-freeze a sheet.
STATUS_PREFIX = "# Status: "
FROZEN_STATUS = "FROZEN"


def sheet_header(text: str) -> str:
    """The leading comment block of a sheet - the part the toolkit owns and keeps true.

    Where the line between "ours" and "the grader's" is drawn for the write decision: the
    header (status, the config facts, the two sentences) is re-emitted on every write, and
    everything below it is theirs, formatting and comments included."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.strip() and not line.startswith("#"):
            break
        out.append(line)
    return "".join(out)


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
        return as_decimal(score)
    total, marked = Decimal(0), False
    for key, value in score.items():
        if questions and key not in questions:
            continue
        if value is None or not str(value).strip():
            continue
        number = as_decimal(value)
        if number is None:
            return None
        total += number
        marked = True
    return total if marked else None


def penalty_rate(text: object) -> Decimal | None:
    """`late_penalty_per_day` as a fraction: `10%` and `0.1` both give 0.10.

    None for anything `penalty_fault` refuses - which `parse_grading_spec` has already
    said out loud, once, when the assignment's definition was read."""
    if penalty_fault(text):
        return None
    raw = str(text or "").strip()
    if not raw:
        return None
    return as_decimal(raw[:-1]) / 100 if raw.endswith("%") else as_decimal(raw)


def final_grade(
    total: object, rate: object, days_late: object, adjustment: object
) -> Decimal | None:
    """`total x (1 - rate x days_late) + adjustment`, floored at 0.

    Derived on OUTPUT and never stored: the sheet has no `final_grade` field, so there is
    no stale cell for this to disagree with. A blank adjustment is 0 and a blank rate or
    day count is no penalty, so an assignment with no late policy needs no special case.
    A total that is not a number gets no arithmetic at all and comes back None - the caller
    distributes the mark exactly as the grader typed it."""
    earned = as_decimal(total)
    if earned is None:
        return None
    penalty, days = as_decimal(rate), as_decimal(days_late)
    if penalty is not None and days is not None and days > 0:
        earned *= Decimal(1) - penalty * days
    return max(Decimal(0), earned + (as_decimal(adjustment) or Decimal(0)))


# ------------------------------------------------- the assignment's own definition

# `grading_config.yml`, on the course template's `solution` branch, is where an assignment
# is DEFINED. It lives here rather than in `collect` because both readers need it and only
# one of them is the autograder: `collect` takes `type`/`autograde`/`tests` from it, and
# everything else in it exists to shape the grading sheet - which is this module's. The
# names `collect` still spells are re-exported there, so no caller had to move.
GRADING_FILE = "grading_config.yml"  # on the template's solution branch
# The name it had before the rename. The engine stopped reading that one, so a template
# still carrying it declares NOTHING - which looks exactly like an assignment nobody has
# configured, and grades like one.
LEGACY_GRADING_FILE = "grading.yml"


# The institution's defaults, read once: the run settings of a spec nobody has resolved yet.
_INSTITUTION = policy.defaults()


@dataclass(frozen=True)
class GradingSpec(_Shape):
    """One assignment's whole definition: what it is, how it is handed in, and how it is
    marked. `schedule.yml` says WHEN; this says WHAT, and the two never overlap.

    Frozen, because the read is memoised per template per process and several passes of
    one tick share it. `dropped` carries every line the parse refused - an unknown key, a
    value it could not use - in the words it printed them."""

    title: str = ""
    type: str = "individual"
    # The run settings (`settings.RUN_KEYS`) default to the institution's policy; a spec
    # built by `load_grading_spec` carries their EFFECTIVE values (`with_run_settings`).
    team_formation: str = _INSTITUTION["team_formation"]
    max_team_size: int | None = None
    submit_via: str = "assignment_repo"
    # PRIVATE unless the assignment asks otherwise (the policy's default): everything the
    # toolkit creates for a student is private to them and the teaching team, and a
    # default that published a semester's work would be a default nobody chose.
    visibility: str = _INSTITUTION["visibility"]
    # Where an `external` assignment is handed in (Moodle, Kaggle). Only the site reads it,
    # and only to put a button beside the brief.
    submit_url: str = ""
    # The starter formats, in order; the FIRST is the runnable one (see `format`).
    formats: tuple[str, ...] = ()
    questions: dict[str, str] | None = None
    # The institution's late rule unless a nearer layer says otherwise, so an assignment
    # nobody has written a late policy for is still graded by the one the syllabi state.
    # A layer that declares ONE of the two leaves the other empty rather than taking half
    # a default it never asked for - see `_late_pair`.
    late_window_days: int | None = _INSTITUTION["late_window_days"]
    late_penalty_per_day: str | None = _INSTITUTION["late_penalty_per_day"]
    # OFF unless the assignment asks for it. Most assignments are hand-marked, and a
    # default of true made every template without the key try to run hidden tests that
    # were never written - a red tick every quarter of an hour for the rest of the term.
    autograde: bool = False
    # UNDECLARED, and that is a third state rather than a missing false: the default
    # depends on the `format:` beside it (see `runs_completion_check`), so a file that
    # never mentions the key has to be told from one that turned the check off.
    completion_check: bool | None = None
    tests: str = "tests"
    # The grader's reading copy, filtered to the HAND-marked questions and archived beside
    # the autograde detail. Off unless the assignment asks for it: it clones the whole
    # semester a second time at the cutoff, and most assignments are read in the browser.
    grader_pdf: bool = False
    # The file still names its starters as `format:` (decision 0012): refused whole, and
    # nothing hands out or grades from it until it is migrated.
    not_migrated: bool = False
    dropped: tuple[str, ...] = ()
    # The keys this `grading_config.yml` itself states: its run settings are the
    # template's part of the assignment layer until they move to `assignments.yml`.
    declared: frozenset[str] = field(default=frozenset(), compare=False)
    # `{run key: layer}` - where each run setting's value came from (`with_run_settings`).
    sources: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    @property
    def format(self) -> str:
        """The runnable format: the first of `formats`, or `none`. What the completion
        check and the autograder run; the other formats are there to be read."""
        return self.formats[0] if self.formats else NO_STARTER

    @property
    def is_group(self) -> bool:
        """One repo per team rather than one per student."""
        return self.type == "group"

    @property
    def team_formation_resolved(self) -> str:
        """`self_select` | `assigned` | `none` - the answer the Join-team form needs, and
        the one the lock file carries. An individual assignment has no teams to form, so
        it answers `none`: a shape nobody writes and every reader can refuse on."""
        return self.team_formation if self.is_group else NO_TEAMS

    @property
    def submit_host(self) -> str:
        """`moodle.hertie-school.org` out of `submit_url` - what the site's button is
        labelled with, so a student can see where it goes before they press it."""
        return urlsplit(self.submit_url).hostname or "" if self.submit_url else ""

    @property
    def runs_completion_check(self) -> bool:
        """Whether the cutoff executes this assignment's notebook and records whether it
        runs top to bottom (`collect`).

        ON by default for `format: ipynb` and off for everything else, because the rule it
        verifies - restart the kernel and run all before you hand in - is a rule only a
        notebook course states, and it is the one rule nobody could check by hand. An
        explicit `completion_check:` wins either way: a notebook assignment can turn it
        off, and an assignment that keeps its notebooks somewhere the `format:` does not
        name can turn it on."""
        if self.completion_check is None:
            return self.format == "ipynb"
        return self.completion_check


def _cross_check(values: dict, dropped: list[str]) -> None:
    """The settings that are only wrong BESIDE another one, refused the same way the rest
    are: the value is corrected in place and the reason is recorded.

    Each reader above sees one key and cannot see the shape it sits in, so this is where a
    combination the toolkit will not act on is caught - and it is caught at the parse, once,
    rather than by each of the handout, the sheet, the receipts and the site making their
    own guess about what was meant."""
    via = values.get("submit_via", "assignment_repo")
    if via == "external" and values.get("visibility", "private") != "private":
        # Nothing is created, so there is nothing for a visibility to describe.
        values["visibility"] = "private"
        dropped.append(
            Dropped(
                GRADING_FILE,
                "visibility",
                "`visibility:` says nothing about an assignment handed in off GitHub - "
                "no repo is created for it - ignored",
            )
        )
    if (
        via == "shared_dropbox_repo"
        and values.get("visibility", "private") != "private"
    ):
        # Corrected rather than obeyed - see the `shared_dropbox_repo` note beside
        # `course.SUBMIT_VIA`.
        values["visibility"] = "private"
        dropped.append(
            Dropped(
                GRADING_FILE,
                "visibility",
                "`visibility:` is not read for a shared drop box - one repo holds the "
                "whole semester's work, so v1 keeps it private - ignored",
            )
        )
    if via == "shared_dropbox_repo":
        for key in ("autograde", "completion_check", "grader_pdf"):
            if values.get(key):
                # All three stages run PER UNIT against the unit's own repo, and a drop box
                # is one repo for the whole semester: each of fifty students would have the
                # whole semester's work cloned, run and archived under their own key, and
                # every one of them would get the same result. Same note beside
                # `course.SUBMIT_VIA`.
                values[key] = False
                dropped.append(
                    Dropped(
                        GRADING_FILE,
                        key,
                        f"`{key}:` is not read for a shared drop box - one repo holds "
                        f"the whole semester's work, so it is hand-marked - ignored",
                    )
                )
            elif key == "completion_check":
                # The one tri-state setting: undeclared means "whatever `format:` implies",
                # so a drop box of notebooks would switch the check on with no line in the
                # file to report as dropped. Turned off here rather than in
                # `runs_completion_check`, so the whole "is this hand-marked?" answer is
                # settled at the parse.
                values[key] = False
    if via != "external" and values.get("submit_url"):
        values["submit_url"] = ""
        dropped.append(
            Dropped(
                GRADING_FILE,
                "submit_url",
                "`submit_url:` is only read for `submit_via: external` - ignored",
            )
        )


def _late_pair(values: dict) -> None:
    """The late-work default is a PAIR, and a file that states half of it gets no half.

    `late_window_days` and `late_penalty_per_day` describe one rule, so the standard
    (10% a day for 10 days) only stands behind a file that says nothing about late work at
    all. A file naming just one of them has stated a rule of its own - `late_window_days:
    3` with no penalty is three days late accepted free, and a penalty with no window is a
    rate nothing is collected to spend it on - and completing it from the syllabus would
    grade a semester by a sentence nobody wrote."""
    declared = [
        key for key in ("late_window_days", "late_penalty_per_day") if key in values
    ]
    if len(declared) == 1:
        values.setdefault("late_window_days", None)
        values.setdefault("late_penalty_per_day", None)


def parse_grading_spec(text: str) -> GradingSpec:
    """Parse a `grading_config.yml` into a `GradingSpec` - the TEMPLATE's view.

    A missing key falls back to the field's own default; `with_run_settings` then resolves
    the run settings through the cascade (`settings`), which is what `load_grading_spec`
    hands every reader. A malformed VALUE is logged and dropped, never raised and never
    passed through: this file is hand-edited by faculty and read by an hourly cron, so one
    bad line costs the field it sits on and nothing else."""
    data = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(data, dict):
        data = {}
    # A file that still names its starters under the old key alone has not migrated, and
    # is refused WHOLE: read without them, it would grade as an assignment with no
    # starter at all - the completion check off, the runnable format gone - and say nothing.
    if "format" in data and "formats" not in data:
        raise NotMigrated("format", "formats", GRADING_FILE)
    dropped: list[str] = []
    data = refuse_renamed(data, GRADING_FILE, dropped)
    values = read_settings(data, SPEC_KEYS, GRADING_FILE, dropped)
    _late_pair(values)
    declared = frozenset(values)
    _cross_check(values, dropped)
    for line in dropped:
        log_err(line)
    return GradingSpec(**values, dropped=tuple(dropped), declared=declared)


def with_run_settings(
    spec: GradingSpec, course_org: str, semester_org: str = "", slug: str = ""
) -> GradingSpec:
    """`spec` with every run setting at its EFFECTIVE value (`settings.effective_all`) and
    `sources` saying which layer gave it. Without a semester, the semester's layers are
    empty. The shape rules the parse applies to the file hold for what the cascade brings
    too, silently - a course default of `public` says nothing about an assignment that
    creates no repo of its own."""
    template = {
        key: getattr(spec, key) for key in settings.RUN_KEYS if key in spec.declared
    }
    resolved = settings.effective_all(
        semester_org, slug, course_org=course_org, template=template
    )
    values = {key: value for key, (value, _) in resolved.items()}
    if not creates_unit_repos(spec.submit_via):
        values["visibility"] = "private"
    if spec.submit_via != "external":
        values["submit_url"] = ""
    return replace(
        spec,
        **values,
        sources=tuple((key, source) for key, (_, source) in resolved.items()),
    )


@cache
def _grading_text(course_org: str, template: str) -> str | None:
    """The template's `grading_config.yml`, read ONCE per template per process.

    An hourly tick asks the same template the same question from the scheduler, the sheet
    refresh and the collection that follows them. Memoising the TEXT (like
    `schedule._schedule_text`) means every caller still parses its own spec - nothing
    shared to mutate - and still sees its own warnings. tests/conftest.py clears it."""
    return get_file_content(course_org, template, GRADING_FILE, ref=SOLUTION_BRANCH)


def declared_grading_spec(
    course_org: str, template: str, *, semester_org: str = "", slug: str = ""
) -> GradingSpec | None:
    """`load_grading_spec`, but None when there is no definition to read at all - the
    template repo does not exist yet, or it carries no `grading_config.yml`.

    Only the lock file asks the question this way. Every other reader wants the defaults
    for an undeclared assignment and calls `load_grading_spec`; the lock file has to tell
    "declared individual" from "nobody has said yet", because the second one must lock the
    Join-team form rather than answer it."""
    try:
        text = _grading_text(course_org, template)
    except RuntimeError as exc:
        log_err(f"  ! could not read {template}/{GRADING_FILE}: {exc}")
        return None
    if text is None:
        return None
    try:
        spec = parse_grading_spec(text)
    except NotMigrated as exc:
        # Refused, and SAYS so: the lock reads it as no definition (its Join-team form
        # refuses), and the handout and the grader refuse to act on it.
        log_err(f"  ! {template}/{GRADING_FILE}: {exc} - refused")
        return GradingSpec(not_migrated=True)
    except yaml.YAMLError as exc:
        log_err(
            f"  ! {template}/{GRADING_FILE} is not valid YAML - using defaults: {exc}"
        )
        spec = GradingSpec()
    return with_run_settings(spec, course_org, semester_org, slug)


def load_grading_spec(
    course_org: str, template: str, *, semester_org: str = "", slug: str = ""
) -> GradingSpec:
    """The assignment's definition from the course template's `solution` branch, with its
    run settings resolved for `slug` (the schedule key) of `semester_org`.

    A template with no solution branch, no definition file, or one that does not parse
    leaves the rest of the tick running on the defaults rather than taking the semester
    down with it. A failed read of a CASCADE layer (the course's `dsl-course.yml`, the
    semester's `assignments.yml`) does raise (`settings.course_defaults`): those decide
    what the defaults are."""
    spec = declared_grading_spec(
        course_org, template, semester_org=semester_org, slug=slug
    )
    if spec is None:
        spec = with_run_settings(GradingSpec(), course_org, semester_org, slug)
    return spec


# ------------------------------------ what an assignment's definition will not grade as
#
# TIME-BOUND, unlike everything else that reaches a digest. `submit_via: emial` is a typo
# in a file, and until the assignment is graded it is a typo that costs nothing: the fix
# is the same in August and on the morning of the deadline. So it keeps the SOURCE clock -
# it climbs the ladder as the grading moment approaches - rather than shouting from the
# day somebody saved it.


def _spec_fault(
    slug: str,
    template: str,
    course_org: str,
    fires: datetime | None,
    what: str,
    field: str = "",
    lineno: int | None = None,
    fix: str = "",
    file: str = GRADING_FILE,
    plain: str = "",
    consequence: str = "",
    per_semester: bool = False,
    code: str = "",
) -> ConfigFault:
    """One value in one assignment's definition that will not grade as written.

    The fault is in the COURSE org, on the template's `solution` branch, and the digest
    that carries it is the SEMESTER's - the semester is what the grading happens to, and the
    people who can fix it are the ones in its instructors.yml. So the file's own address rides
    on the fault (`in_org`, `in_repo`, `ref`), which is what the deep link and the blame
    query both go by."""
    return ConfigFault(
        f"assignments.{slug}",
        what,
        fires=fires,
        field=field,
        lineno=lineno,
        file=file,
        in_repo=template,
        in_org=course_org,
        ref=SOLUTION_BRANCH,
        fix_text=fix,
        plain=plain,
        consequence=consequence,
        per_semester=per_semester,
        code=code,
    )


def grading_spec_faults(
    slug: str,
    template: str,
    course_org: str,
    text: str,
    fires: datetime | None,
    handed_out: list[dict] | None = None,
    releases_solution: bool = False,
    semester_org: str = "",
) -> tuple[list[ConfigFault], GradingSpec | None]:
    """Everything in ONE `grading_config.yml` the parse had to refuse, as faults - and the
    spec that parse produced, so the caller does not read and parse the same file again.
    `None` in its place is a file that is not YAML at all; the faults say so.

    Handing the spec back is not a convenience: `parse_grading_spec` LOGS every line it
    drops, so a second parse of the same file printed every one of them twice in the same
    digest run.

    The parse's own sentences, in the words it already logs them in: it is the one place
    that knows the vocabulary each key accepts, and a fault that paraphrased it would be a
    second, slightly different answer about what is allowed.

    `handed_out` is the repos this assignment actually created, off the caller's listing:
    one more fault, off the SAME parse, because parsing a second time here would log every
    refused line in this file twice. None means there is nothing to compare the file
    against - the assignment has not gone out, or the listing could not be read.

    `releases_solution` is whether this assignment's `schedule.yml` entry carries a
    `solution_datetime:` - the one fact about it that the definition here can contradict
    (see below), and the only thing read from outside this file.

    The checks against the handed-out repos and the solution release read the RESOLVED
    spec (`with_run_settings` for `slug` of `semester_org`), the one the handout acts on:
    a visibility set in `assignments.yml` or the course's defaults is what the repos were
    created with, and the template alone would give the digest a second answer."""
    try:
        spec = parse_grading_spec(text)
    except NotMigrated as exc:
        return [
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                f"{exc} - the whole file is refused, so the assignment is not handed "
                f"out or graded until it is",
                field="format",
                lineno=key_lines(text).get(("format",)),
                fix="run the migration, which rewrites it to the new name",
                code=NOT_MIGRATED,
            )
        ], None
    except yaml.YAMLError as exc:
        return [
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                "this file is not valid YAML, so none of it is read and the whole "
                "assignment grades on the toolkit's defaults",
                lineno=yaml_mark_line(exc),
                fix="fix the YAML on the line above",
                plain=f"The {slug} template's settings file is not valid YAML, so "
                f"the assignment is marked on the toolkit's defaults.",
            )
        ], None
    spec = with_run_settings(spec, course_org, semester_org, slug)
    lines = key_lines(text)
    faults = [
        _spec_fault(
            slug,
            template,
            course_org,
            fires,
            dropped.what,
            field=dropped.field,
            lineno=lines.get((dropped.field,)),
            fix=_spec_fix(dropped),
            code=dropped.code,
        )
        for dropped in spec.dropped
        if isinstance(dropped, Dropped)
    ]
    if handed_out:
        faults += _visibility_faults(
            spec, slug, template, course_org, lines, fires, handed_out
        )
    if releases_solution and not spec.can_hold_solution:
        # A moment that will pass and do nothing. `provision_all` refuses to push the
        # model answer where there is no repo of the unit's own to put it in, or none the
        # toolkit can promise is private - and the two files are written by different
        # people, so nothing else would notice that the plan schedules a release the
        # definition forbids. ONE predicate, asked in both places
        # (`course.can_hold_solution`).
        faults.append(
            _spec_fault(
                slug,
                template,
                course_org,
                fires,
                f"this assignment's entry in `schedule.yml` carries a "
                f"`solution_datetime:`, but `submit_via: {spec.submit_via}` with "
                f"`visibility: {spec.visibility}` gives it no private repo of its own to "
                f"push the model solution into - it is never pushed where the world can "
                f"read it, where the students can publish it, or where the whole semester "
                f"shares one repo, so that moment passes and nothing is released",
                field="visibility",
                lineno=lines.get(("visibility",)),
                fix="remove `solution_datetime:` from this assignment's entry in "
                f"{CONFIG_REPO}/schedule.yml - the model answer stays on this "
                "template's `solution` branch, which is where the instructors read "
                "it - or give this assignment a private repo per unit here "
                "(`submit_via: assignment_repo`, `visibility: private`)",
                plain=f"{slug} has a solution shown date in this semester's schedule, "
                f"but its settings give it no private repo to put the solution in.",
                consequence="the solution shown date passes and no solution is "
                "released",
                per_semester=True,
            )
        )
    return faults, spec


def _spec_fix(dropped: Dropped) -> str:
    """What would put one refused line right: the vocabulary the key accepts, or - for a
    key the toolkit has no reader for at all - the keys it does read.

    "Correct the value" is not an instruction about a line whose KEY is the mistake: a
    setting that moved here from schedule.yml, or a plain misspelling, has no value to
    correct."""
    if dropped.code == NOT_MIGRATED:
        return "run the migration, which rewrites it to the new name"
    if dropped.field not in SPEC_KEYS:
        return f"remove the line above, or spell it as one of: {', '.join(SPEC_KEYS)}"
    allowed = f" (allowed: {'/'.join(dropped.allowed)})" if dropped.allowed else ""
    return f"correct the value on the line above{allowed}"


def grading_config_faults(
    course_org: str,
    semester_org: str,
    sched,
    found: list[ConfigFault],
    listing: dict[str, dict] | None,
) -> None:
    """Every assignment this semester's plan declares, and everything in its definition that
    will not grade as written.

    `fires` is the moment the value is USED: the assignment's `grading_datetime`, and its
    due date where it declares none (which is what `schedule.grading_cutoff_datetime` resolves the freeze to
    anyway). An assignment with neither has no moment, and its faults simply sit in the
    issue.

    `listing` is the SEMESTER's repos keyed by name, off the one listing the tick already
    holds, and it is read for one check: an assignment whose `visibility:` no longer
    describes the repos it handed out (see `_visibility_faults`). None is "we could not
    look", and that check reports nothing - it is not worth dropping a whole file's digest
    for, since every other fault in it was read from the template.

    `semester_org` is read for one more thing, and only where a definition asks for it: an
    assignment that hands the visibility flag to its students depends on two settings of
    the ORG, which cannot be set through the API at all (`_org_settings_faults`). One GET
    for the whole plan, and none at all for a semester with no such assignment.

    Everything else here is the definition alone, which lives in the course org. Nothing
    is appended until every template has been read: a read that failed is "we could not
    look", and the digest closes what it is not handed."""
    faults: list[ConfigFault] = []
    # Whether any assignment under this plan has handed the visibility flag to its
    # students, which is what makes the org's own two switches worth an API read - see
    # `_org_settings_faults`.
    hands_the_flag_over = False
    for slug, entry in sorted(sched.assignments.items()):
        template = entry.course_source_repo
        if not template:
            continue  # the plan itself is faulty; schedule.yml's own digest says so
        # The window-less cutoff: the spec that holds the window is what is read next.
        fires = schedule.grading_cutoff_datetime(sched, slug)
        text = _grading_text(course_org, template)
        if text is None:
            faults += _undeclared_faults(slug, template, course_org, fires)
            continue
        # Nothing to compare a file against until the assignment has gone out. Archived
        # repos are left out: one is read-only and a finished semester is meant to stay
        # frozen, so nothing it says can be anybody's fault.
        handed_out = None
        if listing is not None and entry.handout_datetime is not None:
            handed_out = assignment_rows(listing, schedule.semester_name(slug, entry))
        spec_faults, spec = grading_spec_faults(
            slug,
            template,
            course_org,
            text,
            fires,
            handed_out,
            releases_solution=entry.solution_datetime is not None,
            semester_org=semester_org,
        )
        faults += spec_faults
        if handed_out and spec is not None and spec.visibility_is_students:
            hands_the_flag_over = True
    # ONE read of the org, after every template has been parsed and only when something
    # in this semester actually depends on it: an org with no such assignment is not
    # misconfigured, it is an org the question does not apply to.
    if hands_the_flag_over:
        faults += _org_settings_faults(semester_org)
    found.extend(faults)


def _visibility_faults(
    spec: GradingSpec,
    slug: str,
    template: str,
    course_org: str,
    lines: dict[tuple[str, ...], int],
    fires: datetime | None,
    rows: list[dict],
) -> list[ConfigFault]:
    """`visibility:` against the repos this assignment actually handed out, `rows`.

    The value is read at CREATE and nowhere else (see `repos.set_visibility`), so editing
    the line afterwards is a silent no-op: the file says `public`, the semester's work stays
    private, and every page the toolkit writes describes repos that do not exist. Nothing
    else notices, which is why this is a fault and not a log line.

    The listing's word against the file's, compared as both are written - they are the same
    vocabulary. A shape whose repos are legitimately a MIXTURE, because the students own
    the flag, is exempted by its own predicate in `course.py`, never by a name spelt
    here. `creates_repos` rather than `creates_unit_repos`: a shared drop box is one repo
    for the whole semester and `visibility:` describes it exactly as it describes the many."""
    if not spec.creates_repos or spec.visibility_is_students:
        # `student_choice` says the STUDENT decides, so twenty private repos and four
        # public ones is the assignment working - `course.visibility_is_students`. The
        # thing that CAN be wrong about it is the org, not the file: see
        # `_org_settings_faults`.
        return []
    wrong = [r for r in rows if r.get("visibility") != spec.visibility]
    if not wrong:
        return []
    return [
        _spec_fault(
            slug,
            template,
            course_org,
            fires,
            # A COUNT and never a name: a submission repo is `<slug>-<handle>`, and this
            # sentence is repeated into a public run log, a digest issue and an email.
            f"`visibility: {spec.visibility}` does not describe the repos this assignment "
            f"handed out - {len(wrong)} of {len(rows)} are not {spec.visibility}. The "
            f"value is read when each repo is CREATED, so editing it afterwards moves "
            f"nothing on its own",
            field="visibility",
            lineno=lines.get(("visibility",)),
            fix=f"set `visibility:` back to what those repos are, or make each of them "
            f"{spec.visibility} by hand from its GitHub Settings - the toolkit never "
            f"re-opens a repo it has already created",
            plain=f"{slug}'s settings say its repos are {spec.visibility}, but "
            f"{len(wrong)} of {len(rows)} handed out in this semester are not.",
            consequence="those repos stay as they are, and the pages the toolkit "
            "writes describe them wrongly",
            per_semester=True,
        )
    ]


# WHERE the org-settings fault sits in the digest's state. ONE key for the whole semester
# and not one per assignment: the two switches belong to the org, so three `student_choice`
# assignments under one plan are three readings of one problem, and three keys would mail
# about it three times and clear it three times.
ORG_SETTINGS = "org settings"
# The page both switches are on. Web-only: the API can read them and cannot set them.
MEMBER_PRIVILEGES_URL = (
    "https://github.com/organizations/{org}/settings/member_privileges"
)


def _org_settings_faults(semester_org: str) -> list[ConfigFault]:
    """The org's own two switches, against what `visibility: student_choice` needs of them.

    That shape gives the student `admin` on their own repo, because `admin` is the only
    permission carrying GitHub's visibility control. It carries other things too, and the
    org is the only place they can be taken back - so the shape is safe exactly where
    members may change a repo's visibility and may NOT delete or transfer one.

    READ-only, both of them: they are reported by `GET /orgs/{org}` and absent from
    `PATCH /orgs/{org}` (they are web-only settings), so the maintainer sets them once per
    semester org and this is what notices when nobody did. Explicit `is True` / `is False`,
    never truthiness: a plan whose payload omits a key has said nothing about it, and
    faulting on a missing field would red every semester on an account tier that does not
    carry it."""
    settings = gh_teams.org_settings(semester_org)
    if settings is None:
        return []  # we could not look; `org_settings` has already said why
    wrong: list[str] = []
    costs: list[str] = []
    if settings.get(gh_teams.MEMBERS_CAN_DELETE) is True:
        wrong.append(
            "**Allow members to delete or transfer repositories** is ON, so a student "
            "can delete or move their own submission"
        )
        costs.append("a student can delete or move their own submission")
    if settings.get(gh_teams.MEMBERS_CAN_PUBLISH) is False:
        wrong.append(
            "**Allow members to change repository visibilities** is OFF, so no student "
            "can publish their work and the shape does nothing for them"
        )
        costs.append("no student can publish their work")
    if not wrong:
        return []
    return [
        ConfigFault(
            ORG_SETTINGS,
            f"an assignment in this semester is handed out with "
            f"`visibility: student_choice`, which makes each student an admin of their "
            f"own repo - and {' and '.join(wrong)}",
            file=GRADING_FILE,
            in_org=semester_org,
            plain="This semester's GitHub member privileges do not suit an assignment "
            "that lets students choose their repo's visibility.",
            consequence=" and ".join(costs),
            per_semester=True,
            # Both switches named whichever one is wrong: the fix is one visit to one
            # page, and a sentence that named only the offender would send somebody back
            # there a second time for the other.
            fix_text=(
                f"on the semester org's Member privileges page "
                f"({MEMBER_PRIVILEGES_URL.format(org=semester_org)}) set **Allow members to change repository "
                f"visibilities** ON and **Allow members to delete or transfer "
                f"repositories** OFF. Both are web-only org settings - the toolkit reads "
                f"them and cannot set them - and they are the one-time semester-org step "
                f"in docs/DEPLOYMENT-CHECKLIST.md"
            ),
        )
    ]


def _undeclared_faults(
    slug: str, template: str, course_org: str, fires: datetime | None
) -> list[ConfigFault]:
    """The one fault a template with NO `grading_config.yml` can still have: it is
    carrying the file under the name the engine stopped reading.

    An assignment that declares nothing is not a fault - it grades as an individual
    hand-marked one, which is a real choice - so this asks the question only when there is
    a file to find, and pays one API read for it only on a template that has no definition
    at all."""
    try:
        legacy = get_file_content(
            course_org, template, LEGACY_GRADING_FILE, ref=SOLUTION_BRANCH
        )
    except RuntimeError:
        return []  # cannot say; `get_file_content` has already said why
    if legacy is None:
        return []
    return [
        _spec_fault(
            slug,
            template,
            course_org,
            fires,
            f"this assignment is defined in `{LEGACY_GRADING_FILE}`, which nothing "
            f"reads any more - it grades on the toolkit's defaults",
            file=LEGACY_GRADING_FILE,
            fix=f"rename `{LEGACY_GRADING_FILE}` to `{GRADING_FILE}` on the template's "
            f"`{SOLUTION_BRANCH}` branch",
            plain=f"{slug} keeps its settings in {LEGACY_GRADING_FILE}, which is no "
            f"longer read, so it is marked on the toolkit's defaults.",
        )
    ]


# --------------------------------------------------- the team-formation lock file

# `semester-config/.system/assignments.lock.yml` is a MIRROR, written by the toolkit and read by
# the Join-team form in the semester's public `join` repo. It exists because of who can
# read what: the form runs on an `issues: opened` event any stranger can trigger, in a
# public repo, under a token deliberately scoped away from the course org's assignment
# templates - so it cannot open `grading_config.yml` and ask what the assignment is. It
# used to scrape `type:` and `max_team_size:` out of the semester's own `schedule.yml`
# instead, which is why a slug with neither let any student mint a real GitHub team.
#
# Flat scalars per schedule key, and no vocabulary the form has to interpret twice.
TEAM_LOCK_PATH = records.path("lock")
_TEAM_LOCK_HEADER = f"""\
# SYSTEM-OWNED - do not edit, edits here are overwritten. Written by the DSL teaching
# toolkit from each assignment's `{GRADING_FILE}`, one entry per assignment in
# `schedule.yml`. Faculty change an assignment by editing its own `{GRADING_FILE}` on
# the course template's `{SOLUTION_BRANCH}` branch; this file catches up next sync.
#
# The Join-team form in this semester's `join` repo reads THIS FILE and nothing else.
#
#   team_formation: self_select   students form their own teams with the Join-team form
#                   assigned      the teaching team writes teams.csv; the form refuses
#                   none          an individual assignment; the form refuses
#   max_team_size:  the cap the form enforces (group assignments only)
#   team_formation_window:
#                   open      students may form teams for it now
#                   pending   handed out later; the window has not opened yet
#                   closed    the window has shut, or there is no date to open it on
#                   none      not a self-select group assignment, so there is no window
#   team_formation_closes:
#                   the date that window shuts, bare ISO (`2026-10-04`), for the refusal
#                   to name - empty when there is no window, or no date to give
#   team_formation_page:
#                   the assignment's page on the semester site, which lists the teams that
#                   exist - for the refusal to link; empty for anything not self-select
#
# An assignment whose course template does not exist yet is locked to `{NO_TEAMS}`:
# until the template says what it is, nobody can mint a GitHub team for it.
"""


def team_lock_text(entries: dict[str, tuple[str, int, str, str, str]]) -> str:
    """The lock file's whole text, from
    `{schedule key: (team_formation, cap, window, closes, page)}`.

    Hand-rolled rather than `yaml.safe_dump`, for the same reason the workflows are: it is
    read by a line scanner with no YAML library to hand (the Join-team form's JavaScript),
    and that scanner reads a two-space key with four-space scalars under it. `parse_team_lock`
    below is this file's Python reader, and the two are kept together on purpose. Keys
    sorted, so a re-sync of an unchanged semester produces an identical blob and `put_file`
    writes nothing.

    `team_formation_closes` and `team_formation_page` are written even when they are empty
    - the key with nothing after it. The scanner reads one shape, and a line that comes and
    goes is a second shape: the entry whose close date the schedule cannot give is exactly
    the entry a reader is most likely to get wrong."""
    lines = [_TEAM_LOCK_HEADER, "assignments:"]
    if not entries:
        lines.append("  {}")
    for key in sorted(entries):
        formation, cap, window, closes, page = entries[key]
        lines += [
            f"  {key}:",
            f"    team_formation: {formation}",
            f"    max_team_size: {cap}",
            f"    team_formation_window: {window}",
            f"    team_formation_closes: {closes}".rstrip(),
            f"    team_formation_page: {page}".rstrip(),
        ]
    return "\n".join(lines) + "\n"


# The shape `team_lock_text` writes, read back: a two-space key, four-space scalars under
# it. Every scalar, in one scan, so a second Python caller wanting a different one of them
# needs no second scanner 250 lines from the writer.
_LOCK_KEY_RE = re.compile(r"^ {2}([\w.-]+):$")
_LOCK_SCALAR_RE = re.compile(r"^ {4}([\w.-]+):\s*(.*)$")


def parse_team_lock(text: str) -> dict[str, dict[str, str]]:
    """`assignments.lock.yml` back into `{schedule key: {scalar: value}}`.

    The file's own writer is directly above, which is the whole point of putting its reader
    here: the format is hand-rolled for a line scanner, so a reader written anywhere else
    is a second opinion about a shape only this module decides.

    Forgiving in the same way the JavaScript is - a line it does not recognise is skipped,
    and a comment is cut off the end - because this file is read to decide what a form may
    OFFER, and a semester whose lock is half-written is better served by the entries that did
    parse than by an exception."""
    entries: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        found = _LOCK_KEY_RE.match(line)
        if found:
            current = entries.setdefault(found.group(1), {})
            continue
        scalar = _LOCK_SCALAR_RE.match(line)
        if current is not None and scalar:
            current[scalar.group(1)] = scalar.group(2).strip()
    return entries


def team_lock_entries(
    course_org: str,
    sched: schedule.Schedule,
    now: datetime | None = None,
    pages: Mapping[str, str] | None = None,
) -> dict[str, tuple[str, int, str, str, str]]:
    """What each of this semester's assignments allows, resolved off the ONE place that
    declares it - the template's `grading_config.yml` - plus where `now` falls in its
    team-formation window, and the day that window shuts.

    A template with no definition to read is locked to `none` and says so: the alternative
    is the toolkit guessing a shape for an assignment nobody has described, and the guess
    that costs least is the one where a team cannot be formed yet.

    The window is `none` for anything but a self-select assignment, because the form
    refuses those on the `team_formation` scalar alone - a window over an assignment whose
    teams the teaching team writes says nothing anyone can act on. `pending` is the window
    that has not opened yet and `closed` the one that has shut - kept apart because the
    close date is still in the FUTURE while a window is pending, so one sentence for both
    would tell a September student the door closed in October. An entry carrying no dates
    to judge by is `closed`: there is no hour from which it would be true.

    The close is a bare DATE, not the pin's full moment: the only reader is a refusal
    comment a student reads, and an hour in the workflow's timezone answers a question
    nobody asked. Empty whenever there is no window, or no pin to take one from - the
    refusal then says only that the window is shut.

    `pages` is each assignment's page on the semester site by schedule key
    (`_formation_pages`), written for a self-select entry alone: that page lists the teams
    that exist, and it is what a refused Join links."""
    now = now if now is not None else datetime.now(UTC)
    pages = pages or {}
    entries: dict[str, tuple[str, int, str, str, str]] = {}
    for key, entry in sched.assignments.items():
        spec = declared_grading_spec(
            course_org, entry.course_source_repo, semester_org=sched.org, slug=key
        )
        if spec is None or spec.not_migrated:
            # Named by SLUG, never by anyone in it: this runs in a public workflow.
            log_err(
                f"  ! {key}: {entry.course_source_repo} has no {GRADING_FILE} yet - "
                f"locking it to `{NO_TEAMS}`, so no team can be formed for it until the "
                f"template declares what the assignment is"
            )
            cap = team_cap(course_org, spec, sched.org, key)
            entries[key] = (NO_TEAMS, cap, "none", "", "")
            continue
        formation = spec.team_formation_resolved
        window, shuts, page = "none", "", ""
        if formation == SELF_SELECT:
            # The same call the semester site's team-formation callout makes, so the page
            # that invites a student in and the form that lets them in shut together.
            window, closes = schedule.formation_state(sched, key, now)
            shuts = closes.date().isoformat() if closes is not None else ""
            page = pages.get(key, "")
        entries[key] = (
            formation,
            team_cap(course_org, spec),
            window,
            shuts,
            page,
        )
    return entries


def self_select_keys(course_org: str, sched: schedule.Schedule) -> list[str]:
    """The semester's assignments whose teams the STUDENTS form, in schedule order.

    A template nobody has written declares nothing and is not one of them - the lock has
    already locked its form to `none` - and `team_formation_resolved` answers `none` for
    anything that is not a group assignment, so the shape question and the group question
    are one test.

    Free to ask: `declared_grading_spec` memoises the template's text per process, and by
    the time a tick reaches this every one of them has been read already."""
    return [
        key
        for key, entry in sched.assignments.items()
        if (
            spec := declared_grading_spec(
                course_org, entry.course_source_repo, semester_org=sched.org, slug=key
            )
        )
        is not None
        and not spec.not_migrated
        and spec.team_formation_resolved == SELF_SELECT
    ]


def _formation_pages(
    course_org: str, semester_org: str, sched: schedule.Schedule
) -> dict[str, str]:
    """Each self-select assignment's page URL on the semester site, by schedule key.

    Asked only when the plan HAS a self-select assignment: the pages cost a listing of the
    course org, and the lock is written on every tick of a semester with any assignment."""
    if not self_select_keys(course_org, sched):
        return {}
    return {
        key: page.url(semester_org)
        for key, page in schedule.assignment_pages_by_key(
            course_org, semester_org, sched
        ).items()
    }


def team_cap(
    course_org: str,
    spec: GradingSpec | None,
    semester_org: str = "",
    slug: str = "",
) -> int:
    """How many may be in one team: the effective `max_team_size` (`settings`) - the spec's
    own when it carries one, else the cascade's answer for an assignment whose template
    says nothing at all, which is what `spec=None` is.

    One place, because the number is now both enforced and PRINTED: the lock file the
    Join-team form refuses on, and the semester site's callout inviting a student to form a
    team of up to this many. A page naming five against a form that refuses the fifth
    would be the page's fault."""
    if spec is not None and spec.max_team_size:
        return spec.max_team_size
    value, _ = settings.effective(
        semester_org, slug, "max_team_size", course_org=course_org
    )
    return int(value)


class LockWrite(NamedTuple):
    """`ok` = the file is now current. `changed` = its CONTENT moved in this call."""

    ok: bool
    changed: bool


def team_lock_content(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule | None = None,
    *,
    now: datetime | None = None,
) -> bytes:
    """The lock exactly as `sync_team_lock` writes it, without writing - for a caller that
    asks "is this semester's lock current?" (the migration's drift check)."""
    sched = sched if sched is not None else schedule.load(semester_org)
    return team_lock_text(
        team_lock_entries(
            course_org, sched, now, _formation_pages(course_org, semester_org, sched)
        )
    ).encode()


def sync_team_lock(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule | None = None,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> LockWrite:
    """Mirror every assignment's team rules into `semester-config/.system/assignments.lock.yml`,
    and say whether that changed anything.

    Written from everything that could have moved one of its inputs: the membership
    sync (whose dispatcher fires on a push to `schedule.yml`), the handout, and the nightly
    refresh - which is also what seeds it, since a semester's bootstrap ends in one. The blob
    compare makes every one of those a no-op when nothing changed, so the cost of writing
    it from four places is four reads a day.

    `changed` is that same compare, handed BACK: a caller that renders something off this
    file needs to know when to re-render, and it would otherwise pay a second read to find
    out what this call already knows.

    A CLOSED-OUT semester is skipped: `teardown` archives `semester-config` last, an
    archived repo is read-only, and the membership sync reaches such a semester every day -
    the registry it fans out over is not what teardown seals. The check lives here rather
    than at one call site because it is the same answer for all of them: a finished term
    forms no teams, so there is nothing for the mirror to say."""
    if repo_is_archived(semester_org, CONFIG_REPO):
        log(f"  [skip] {TEAM_LOCK_PATH} in {semester_org} (semester archived)")
        return LockWrite(True, False)
    sched = sched if sched is not None else schedule.load(semester_org)
    if dry_run:
        # Above the render, not below it: resolving the entries reads every template's
        # `grading_config.yml`, and a preview that never writes has nothing to do with them.
        log(f"    PREVIEW  {TEAM_LOCK_PATH} ({len(sched.assignments)} assignment(s))")
        return LockWrite(True, False)
    content = team_lock_content(course_org, semester_org, sched, now=now)
    try:
        existing = get_file_with_sha(semester_org, CONFIG_REPO, TEAM_LOCK_PATH)
    except RuntimeError:
        # A read that failed for anything but a 404. `changed` is only a render HINT, so
        # the safe answer is the pessimistic one: one spurious re-render costs far less
        # than a missed one, and `put_file` fetches its own sha when we pass none.
        sha: str | None = None
        changed = True
    else:
        sha = existing[1] if existing else ""
        changed = sha != blob_sha(content)
    # The sha the content was READ at: both the safe read-modify-write (GitHub refuses the
    # write if the file moved since) and the no-op short circuit, since `put_file` returns
    # True without writing when `expected_sha` already matches what we would write.
    ok = put_file(
        semester_org,
        CONFIG_REPO,
        TEAM_LOCK_PATH,
        content,
        "ci: refresh the team-formation lock from each assignment's definition",
        expected_sha=sha,
    )
    if not ok:
        log_err(
            f"could not write {TEAM_LOCK_PATH} in {semester_org} - the Join-team form reads "
            f"it, so it answers from whatever the file last said"
        )
    return LockWrite(ok, changed and ok)


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


def sheet_spec(
    sched: schedule.Schedule, key: str, slug: str, gspec: GradingSpec, is_group: bool
) -> SheetSpec:
    """What the sheet needs to know about this assignment, gathered from the two files
    that own it: `grading_config.yml` on the template's solution branch, and the semester's
    `schedule.yml`. Nothing here is written into the sheet as data - it reaches the grader
    as the comment header, which is regenerated on every write."""
    entry = sched.assignments.get(key)
    return SheetSpec(
        slug=slug,
        title=gspec.title or (entry.title if entry else "") or slug,
        is_group=is_group,
        submit_via=gspec.submit_via,
        visibility=gspec.visibility,
        questions=gspec.questions,
        late_window_days=gspec.late_window_days,
        late_penalty_per_day=gspec.late_penalty_per_day,
        autograde=gspec.autograde,
        completion_check=gspec.runs_completion_check,
        due_display=_display_moment(entry.due_datetime if entry else None),
        cutoff_display=_display_moment(
            schedule.grading_cutoff_datetime(sched, key, gspec.late_window_days)
        ),
        due_long=_display_long(entry.due_datetime if entry else None, sched.timezone),
        cutoff_long=_display_long(
            schedule.grading_cutoff_datetime(sched, key, gspec.late_window_days),
            sched.timezone,
        ),
        due_at=entry.due_datetime if entry else None,
    )


# ----------------------------------------------------------- the Submission receipts issue, in situ

# Reading and writing the issue whose CONTRACT lives in `course`. Everything that decides
# WHAT is said is there; everything that decides whether a call is made is here.
_RECEIPTS_LABEL_COLOUR = "0e8a16"
_RECEIPTS_LABEL_DESCRIPTION = "Submission receipts from the toolkit"


def late_policy(spec) -> str:
    """`accepted until Sunday 11 October 2026, 23:59 (Europe/Berlin), at 10% of your grade
    per day started.` - or "" when nothing is accepted after the deadline.

    ONE sentence for both places a student meets the policy - the Submission receipts issue at handout
    and every receipt after it. Two spellings of the same rule is how a student ends up
    reading two different deadlines.

    The rate TRAILS the date rather than bracketing it: `cutoff_long` already ends in the
    semester's timezone, and two parentheticals in a row read as a typo."""
    if not spec.collects_commits or not (spec.late_window_days and spec.cutoff_long):
        return ""
    rate = (
        f", at {spec.late_penalty_per_day} of your grade per day started"
        if spec.late_penalty_per_day
        else ""
    )
    return f"accepted until {spec.cutoff_long}{rate}."


def receipts_thread_body(
    spec, unit: str = "", members: tuple[str, ...] | list[str] = ()
) -> str:
    """The Submission receipts issue's body for one submission repo, from the assignment's own spec.

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
    return receipts_issue_body(
        due_display=spec.due_long,
        late_policy_line=late,
        team_line=team_line,
    )


def late_line(spec) -> str:
    """The late policy as the sentence a receipt carries, or "" when there is no window."""
    policy = late_policy(spec)
    return f"Late work is {policy}" if policy else ""


def penalty_display(spec, days: int) -> str:
    """`-20%` for the receipt - `_penalty_display` under the spec the caller already holds.

    The same function as the gradebook's, deliberately: those are the two places one
    student reads one deduction, and two renderers of it drifted apart above two decimal
    places the moment one of them rounded."""
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


class IssueLookupFailed:
    """The receipts-issue lookup could not be READ - as against finding nothing there.

    A 5xx or a secondary limit that outlived the retry ladder used to come back as "this
    repo has no Submission receipts issue", and the very next thing that happens is a SECOND issue
    opened over the thread the student was told to read. Falsy, so `if not found` still
    reads naturally; distinguished from None by identity, never by truth."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False


LOOKUP_FAILED = IssueLookupFailed()

# Oldest first, and the author with it. The toolkit opens the Submission receipts issue at handout,
# so ours is the oldest one carrying the label - but a student holds `maintain` on their
# own submission repo and can open and label their own, and GitHub's default ordering is
# newest first, which handed it theirs.
_ISSUE_ORDER = "sort=created&direction=asc"


def _receipts_issues(
    semester_org: str, repo: str, query: str, jq: str
) -> list[str] | None:
    """The issues this query matches, or None when the question could not be ANSWERED.

    A 404 IS an answer: there is no such repo, so it has no Submission receipts issue. Every shape
    can reach one - a student who never onboarded, a team formed after the handout, an
    assignment handed in off GitHub whose repos were never created - and answering "could
    not read it" for them turned a semester of absent repos into a red run and a `[wait]`
    line per student. What stops the 404 being read as "so open one" is the policy
    (`receipts_thread_policy`), which never creates where the listing did not show a
    private repo.

    Anything else is genuinely "could not answer" - a token that lost its grant is not a
    repo with no issues - and the caller must not act on the difference."""
    code, out = gh("api", f"repos/{semester_org}/{repo}/issues?{query}", "--jq", jq)
    if code != 0:
        return [] if is_missing_resource(out) else None
    return [line for line in out.splitlines() if line.strip()]


def _ours(rows: list[list[str]], login_at: int) -> list[str]:
    """The issue this toolkit opened, if one of these is - else the oldest of them.

    Ordering already puts ours first whenever it was opened first; preferring the author
    closes the case where a student's own labelled issue predates the handout's. One
    candidate is not a choice, and asking who we are costs a call, so it is not asked."""
    if len(rows) < 2:
        return rows[0]
    mine = bot_login()
    for row in rows:
        if mine and len(row) > login_at and row[login_at] == mine:
            return row
    return rows[0]


def find_receipts_issue(
    semester_org: str, repo: str
) -> tuple[int, str] | IssueLookupFailed | None:
    """`(number, state)` of this repo's Submission receipts issue, None if it has none, or
    `LOOKUP_FAILED` if the question could not be answered.

    Three rungs, cheapest first: the LABEL, then a body carrying one of the marks, then the
    exact title. Deliberately the LIST endpoint rather than `gh issue list --search`: the
    search index lags behind by minutes, and a lookup that comes back empty here does not
    mean "not there", it means "opened a second one" - which is what the search path did.

    Pull requests are issues to this endpoint, so they are filtered out: a PR titled
    A pull request titled like the issue would otherwise be commented on instead."""
    # One query per label in the chain: `labels=a,b` means BOTH, not either.
    for label in RECEIPTS_ISSUE_LABELS:
        by_label = _receipts_issues(
            semester_org,
            repo,
            f"labels={label}&state=all&per_page=5&{_ISSUE_ORDER}",
            ".[] | select(.pull_request == null) | "
            '"\\(.number)\\t\\(.state)\\t\\(.user.login // "")"',
        )
        if by_label is None:
            return LOOKUP_FAILED
        if by_label:
            row = _ours([line.split("\t") for line in by_label], 2)
            return int(row[0]), row[1]
    marks = " or ".join(f'contains("{mark}")' for mark in RECEIPTS_ISSUE_MARKS)
    listed = _receipts_issues(
        semester_org,
        repo,
        f"state=all&per_page=50&{_ISSUE_ORDER}",
        ".[] | select(.pull_request == null) | "
        f'"\\(.number)\\t\\(.state)\\t\\(if ((.body // "") | {marks}) then "mark" else "" end)'
        '\\t\\(.title)\\t\\(.user.login // "")"',
    )
    if listed is None:
        return LOOKUP_FAILED
    if not listed:
        return None
    rows = [line.split("\t") for line in listed]
    marked = [r for r in rows if len(r) > 2 and r[2] == "mark"]
    titled = [r for r in rows if len(r) > 3 and r[3] == RECEIPTS_ISSUE_TITLE]
    for candidate in (marked, titled):
        if candidate:
            row = _ours(candidate, 4)
            return int(row[0]), row[1]
    return None


def ensure_receipts_issue(
    semester_org: str, repo: str, body: str, dry_run: bool = False, create: bool = True
) -> int | IssueLookupFailed | None:
    """This repo's Submission receipts issue number, opening one if it has none.

    A CLOSED issue is reopened: a student who closes theirs must still receive their
    receipts and their grade, and a second issue would split the thread they were told to
    read. Never two - see `find_receipts_issue` for why the lookup is not a search, and
    why a lookup that FAILED opens nothing: `LOOKUP_FAILED` comes straight back, and the
    caller leaves this unit for the next tick.

    `create=False` finds one without ever opening one - what a shape with no receipts
    issue of its own does, so a semester handed out before that shape existed keeps
    getting its receipts in the thread it was told to read. A FLAG rather than a second
    function: reopening a closed issue and the `LOOKUP_FAILED` rule are the same either
    way, and two spellings of them would drift."""
    found = find_receipts_issue(semester_org, repo)
    if isinstance(found, IssueLookupFailed):
        log_err(
            f"  ! could not read the Submission receipts issues in {semester_org} - opening none, "
            f"posting none; the next run tries again"
        )
        return LOOKUP_FAILED
    if found is not None:
        number, state = found
        if state == "closed" and not dry_run:
            # `--method PATCH` so ghcli's write pacer counts it (see `_is_mutating`).
            code, out = gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{semester_org}/{repo}/issues/{number}",
                "--field",
                "state=open",
            )
            if code != 0:
                log_err(
                    f"  ! could not reopen the Submission receipts issue: {out[:160]}"
                )
        return number
    if not create:
        return None
    if dry_run:
        log("    PREVIEW  would open the Submission receipts issue")
        return None
    # The label first: GitHub silently drops a label the repo does not have, and the label
    # is the cheapest rung of the lookup above.
    ensure_label(
        semester_org,
        repo,
        RECEIPTS_ISSUE_LABEL,
        color=_RECEIPTS_LABEL_COLOUR,
        description=_RECEIPTS_LABEL_DESCRIPTION,
        person=True,
    )
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{semester_org}/{repo}/issues",
        "--field",
        f"title={RECEIPTS_ISSUE_TITLE}",
        "--field",
        f"body={body}",
        "--field",
        f"labels[]={RECEIPTS_ISSUE_LABEL}",
        "--jq",
        ".number",
    )
    if code != 0:
        log_err(f"  ! could not open the Submission receipts issue: {out[:160]}")
        return None
    return int(out.strip()) if out.strip().isdigit() else None


def post_marked_comment(
    semester_org: str,
    repo: str,
    issue_no: int,
    body: str,
    marker: str,
    dry_run: bool = False,
) -> bool:
    """Post one comment on the Submission receipts issue, unless it already carries `marker`.

    The marker is the whole idempotence story, and it is why both callers share this: the
    refresh pass runs four times an hour for the length of the late window, and Patch
    released assignment is re-pressed after every correction. Neither may say the same
    thing twice.

    Paginated, because "already said" is only true of the comments we actually read: a
    thread that outgrew one page would hide its own markers and be told everything
    again."""
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{semester_org}/{repo}/issues/{issue_no}/comments?per_page=100",
        "--jq",
        ".[].body",
    )
    if code != 0:
        log_err(
            f"  ! could not read the Submission receipts issue's comments: {out[:160]}"
        )
        return False
    if marker in out:
        return True  # already said, on this commit, for this event
    if dry_run:
        log("    PREVIEW  would post a submission receipt")
        return True
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{semester_org}/{repo}/issues/{issue_no}/comments",
        "--field",
        f"body={body}\n{marker}\n",
    )
    if code != 0:
        log_err(f"  ! could not comment on the Submission receipts issue: {out[:160]}")
        return False
    return True


def post_receipt(
    semester_org: str, repo: str, issue_no: int, body: str, marker: str, dry_run=False
) -> bool:
    """A submission receipt - `post_marked_comment` under the name its caller uses."""
    return post_marked_comment(semester_org, repo, issue_no, body, marker, dry_run)


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
# student sees, not results, and the gradebook is the ONE place a mark is written: the
# team's shared feedback reaches each member through `team_feedback` below, and the
# adjustment stays between a member and their grader.
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
# German-locale Actions runner writes "Okt" into one semester's gradebook and "Oct" into
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


def spoken_day(at: datetime) -> str:
    """`26th Oct` - the toolkit's own spelling of a day, in whatever zone `at` is already
    in.

    ONE spelling, because three surfaces name the same day and a student who is told one
    date by the Join-team form and another by the mail beside it has been told two things.
    The team-formation mail, the semester site's team-formation callout and the form's
    refusal all come through here; the form's own JavaScript carries its copy
    (`spokenDate`) because it cannot import this one. The gradebook's Submitted column does
    NOT (`_submitted_display`): respelling it would rewrite every student's grades.yml."""
    day = at.day
    suffix = (
        "th"
        if 11 <= day % 100 <= 13
        else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    )
    return f"{day}{suffix} {_MONTHS[at.month - 1]}"


_PRIVACY_HEADER = (
    "This gradebook is private to you. It is regenerated each time grades are "
    "distributed; do not edit it."
)
_README_COLUMNS = ("Assignment", "Final grade", "Submitted", "Late", "Team")
# What the Submitted column says where there is no time to show. The two are different
# facts and a student reads them as such: `external` is "we never expected a commit here",
# `not submitted` is "we looked, and nothing was in the repo". Calling the second one
# external told a student who missed a deadline that their assignment was handed in
# somewhere else.
_EXTERNAL = "external"
_NOT_SUBMITTED = "not submitted"
_REGISTRAR_FIELDS = ("hertie_email", "name", "github_handle")


def is_blank(value: object) -> bool:
    """Whether a cell holds nothing. `0` is a value, not a blank - a student who was 0
    days late must see that, and dropping it would read as "we never looked"."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(is_blank(inner) for inner in value.values())
    return False


def _marked(score: object) -> dict:
    """A per-question score with its unmarked questions dropped ({} for a flat score).

    A blank question is one nobody has marked yet, and `Q3: null` in a student's file
    reads as a mark of nothing rather than as no mark at all."""
    if not isinstance(score, dict):
        return {}
    return {name: value for name, value in score.items() if not is_blank(value)}


def _verbatim(score: object) -> str:
    """A score no arithmetic applies to, exactly as typed - `pass`, `A-`, `see me`.

    Only a flat cell has one: a per-question map with a word in it has no single value to
    pass through, and the map itself is already in the view."""
    return "" if isinstance(score, dict) or is_blank(score) else str(score).strip()


def total_points(spec: SheetSpec | GradingSpec) -> str:
    """The assignment's total, or "" when the maxima are not all numbers - `questions`
    holds them as written, and a course may declare `Q1: see rubric`.

    Public, and takes either spec, because the semester site prints the same total on the
    assignment's page (`site._assignment_entry`) that the gradebook prints beside a score.
    One implementation: a second sum of the same maxima is a second answer to "what is
    this assignment out of", and the two would part company the first time one was
    changed."""
    maxima = [as_decimal(maximum) for maximum in (spec.questions or {}).values()]
    if not maxima or None in maxima:
        return ""
    return _plain(sum(maxima, Decimal(0)))


def _penalty_display(rate: Decimal | None, days_late: object) -> str:
    """What the late days cost, in the words the student is told them in: `-20%`.

    Rounded to two decimals, because `rate` is a hundredth of whatever the course wrote and
    the product carries its trailing digits: `3.333%` for three days is a deduction, not
    `-9.999%`."""
    days = as_decimal(days_late)
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
    semester beat this module's guess at what they meant."""
    if external:
        return _EXTERNAL
    if is_blank(value):
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
        if key in fields and not is_blank(fields[key])
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
            # and reaches the member only through the final grade derived from it. Unless
            # there IS no team repo - a shape that creates none makes the gradebook the
            # only place that score can be read at all, and withholding it there would
            # leave a team with a grade nobody was ever shown the marks behind.
            # `STUDENT_VIEW_KEYS` is untouched: the allowlist stays the guarantee.
            "score": (
                None
                if (spec.is_group and spec.creates_unit_repos)
                else (_marked(score) or score)
            ),
            "max_points": total_points(spec),
            "feedback": person.get("feedback_individual"),
            "submitted": _submitted_display(
                info.get("submitted"), not spec.collects_commits
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
    return not is_blank(value) and as_decimal(value) is None


def _score_fault(spec: SheetSpec, score: object) -> str:
    """What is wrong with one unit's score cell, as a `HOLD_REASONS` code, or ""."""
    if not isinstance(score, dict):
        return ""  # a scalar mark is free text by design
    if spec.questions and set(score) - set(spec.questions):
        return "question"
    return "score" if any(_is_typo(value) for value in score.values()) else ""


def sheet_hold_reasons(
    spec: SheetSpec,
    sheet: dict,
    faults: list[ConfigFault] | None = None,
    text: str = "",
    slug: str = "",
) -> dict[str, str]:
    """Every handle in this sheet whose mark a person still has to settle, and why.

    What a grader TYPED, checked before any of it is sent - as against
    `needs_hand_decision`, which is about what the arithmetic could not then do with it.
    Each of these went out silently: `Q1: 14/15` deleted the student's grade and sent
    their feedback anyway; `−3` was read as no adjustment while the grader believed a
    penalty had been waived; a stray `Q5` was added to a total the assignment has no
    maximum for; and a handle in two teams took whichever team the loop reached last.

    One reason per handle, the first found: this is a line in a log, not a diagnosis.

    A caller that passes `faults` gets the same list as `ConfigFault`s, so the grader who
    typed it hears about it instead of a dry-run count nobody is watching. Each names its
    LINE in `text` and never the unit it is in: a unit key is a student handle or a team
    name, and a fault travels to a public issue and an email."""
    held: dict[str, str] = {}
    seen: set[str] = set()
    lines = key_lines(text) if faults is not None else {}
    # A group's mark is ONE line, so the four members it holds are one thing to fix. The
    # hold above is still per handle - it is what stops each person's return - but four
    # faults with one key printed the same bullet four times in the digest issue and made
    # the run summary count four entries where a grader has one to correct.
    recorded: set[str] = set()

    def record(unit_key: str, handle: str, reason: str) -> None:
        held[handle] = reason
        if faults is None:
            return
        lineno = _hold_line(lines, spec, unit_key, handle, reason)
        what, fix = _HOLD_FAULT[reason]
        fault = _sheet_fault(
            slug,
            what,
            lineno=lineno,
            # No field for a duplicate: the key that repeats IS the handle.
            field="" if reason == "duplicate" else _hold_field(spec, reason),
            fix=fix.format(at=f"line {lineno}" if lineno else "that line"),
            plain=_HOLD_PLAIN[reason].format(where=_sheet_where(slug, lineno)),
        )
        if fault.key not in recorded:
            recorded.add(fault.key)
            faults.append(fault)

    for unit_key, block in ((sheet or {}).get(spec.container_key) or {}).items():
        if not isinstance(block, dict):
            continue  # a unit mid-edit; the next run reads a whole block
        fault = _score_fault(spec, block.get(spec.score_key))
        people = (block.get("members") or {}) if spec.is_group else {unit_key: block}
        for handle, person in people.items():
            if handle in seen:
                record(unit_key, handle, "duplicate")
                continue
            seen.add(handle)
            person = person if isinstance(person, dict) else {}
            reason = fault or (
                "adjustment" if _is_typo(person.get("adjustment_individual")) else ""
            )
            if reason:
                record(unit_key, handle, reason)
    return held


# ------------------------------------------- what the grader is told, and where it is

# What each hold reason IS and what would put it right, in the words that reach a public
# issue and an email. Every sentence names a LINE and never the unit it is in, because a
# unit key is a student handle or a team name (see the privacy rule in CLAUDE.md) - which
# is also why the sentences read "this line" rather than naming what is on it.
_HOLD_FAULT = {
    "score": (
        "a mark on this line is not a number, so nothing for this unit is sent",
        "correct the mark on {at} - a mark must be a number, or blank",
    ),
    "adjustment": (
        (
            "the adjustment on this line is not a number, so nothing for this person "
            "is sent"
        ),
        "correct the adjustment on {at} - a word processor's minus sign is not one",
    ),
    "question": (
        (
            "this line marks a question the assignment does not declare, so nothing "
            "for this unit is sent"
        ),
        (
            "remove the question on {at}, or declare it in the assignment's "
            "grading_config.yml"
        ),
    ),
    "duplicate": (
        "the handle on this line is also in another submission unit, so both are held",
        "leave the handle on {at} in one submission unit only",
    ),
}


# The same four, as the console's problem list says them (`ConfigFault.plain`): where on
# the sheet, then what it costs, in the vocabulary's words. Still never the unit.
_HOLD_PLAIN = {
    "score": "{where} has a mark that is not a number, so nothing is returned for that "
    "student or team.",
    "adjustment": "{where} has an adjustment that is not a number, so nothing is "
    "returned for that student.",
    "question": "{where} marks a question the assignment does not have, so nothing is "
    "returned for that student or team.",
    "duplicate": "{where} lists a student who is also in another team or entry, so "
    "marks for both are held.",
}
_SHEET_SHAPE_PLAIN = (
    "The {sheet} marking sheet is not one entry per student or team, so it is left as "
    "it is."
)


def _sheet_where(slug: str, lineno: int | None) -> str:
    """`Line 47 of the assignment-3 marking sheet` - a hold's place, for `_HOLD_PLAIN`."""
    sheet = f"the {slug} marking sheet"
    return f"Line {lineno} of {sheet}" if lineno else sheet[0].upper() + sheet[1:]


def _hold_field(spec: SheetSpec, reason: str) -> str:
    """The key a hold reason is about - the one thing in the fault's heading that is safe
    to name, because it is the toolkit's own vocabulary rather than anything a grader
    typed."""
    return (
        spec.score_key if reason in ("score", "question") else "adjustment_individual"
    )


def _hold_line(
    lines: dict[tuple[str, ...], int],
    spec: SheetSpec,
    unit: str,
    handle: str,
    reason: str,
) -> int | None:
    """The line of the sheet a hold reason is written on, or None when the scan cannot
    see it - a fault citing the file without a line still beats no fault."""
    base = (spec.container_key, unit)
    if reason in ("score", "question"):
        path = base + (spec.score_key,)
    elif reason == "adjustment":
        path = base + (
            ("members", handle, "adjustment_individual")
            if spec.is_group
            else ("adjustment_individual",)
        )
    else:
        # The repeated handle itself: its own line in a group's `members:`, and the unit
        # key in an individual sheet, where the unit IS the handle.
        path = base + (("members", handle) if spec.is_group else ())
    return lines.get(path) or lines.get(base) or lines.get((spec.container_key,))


# A block key and the indent it sits at. A leading `- ` counts as indent, so a list item's
# first key nests under the list rather than beside it; a `#` line is not a key at all.
_BLOCK_KEY = re.compile(r"^(\s*(?:-\s+)?)(?![#\s])([^:#]+?):(?:\s|$)")


def key_lines(text: str) -> dict[tuple[str, ...], int]:
    """`{(key, sub-key, ...): the 1-based line it is written on}` for one YAML file.

    A TEXT scan, and deliberately so. The grading sheet's loader hands back a plain dict -
    stamping line numbers into it the way `gh_contents.LineLoader` does would put a
    reserved key inside a mapping that gets written straight back into the grader's file -
    and this is only ever asked WHERE a fault is, never what the file says. Indentation is
    the whole of the nesting rule, which is all these files use; a key the scan cannot see
    (a flow mapping, a folded block) simply leaves its fault citing the file with no line.

    The FIRST occurrence of a path wins: the sheet's own parser refuses a duplicate key
    long before anything asks this."""
    out: dict[tuple[str, ...], int] = {}
    stack: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        match = _BLOCK_KEY.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2).strip().strip("\"'")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        out.setdefault(tuple(k for _indent, k in stack) + (key,), lineno)
        stack.append((indent, key))
    return out


def _sheet_fault(
    slug: str,
    what: str,
    lineno: int | None = None,
    field: str = "",
    fix: str = "",
    plain: str = "",
) -> ConfigFault:
    """One thing in one grading sheet a grader has to settle.

    `where` is the sheet and the LINE, never the unit: a unit key is a student handle or a
    team name, and `where` is this fault's heading in the mail and its identity in the
    digest's state. The slug is part of it because one digest issue carries every sheet in
    the semester, so two sheets' line 42 must not be one fault."""
    return ConfigFault(
        f"{slug} line {lineno}" if lineno else slug,
        what,
        field=field,
        lineno=lineno,
        # The SHEET's own path, not the digest's label for the folder: this is what the
        # deep link and the blame query use, so each fault lands on the sheet it is in.
        file=sheet_path(slug),
        fix_text=fix,
        plain=plain,
    )


def sheet_faults(
    slug: str, text: str, spec: SheetSpec | None = None
) -> list[ConfigFault]:
    """Everything in ONE grading sheet that a grader has to settle before anything can be
    refreshed or sent: a file that does not parse, a container that is not a mapping of
    units, and every mark the pipeline would otherwise hold.

    Immediate faults, every one of them (`fires` is None): nothing about a mark nobody can
    read improves by waiting, and the sheet is not refreshed or distributed while it
    stands.

    `spec` is the assignment's definition, for the question names a mark is checked
    against; without one the sheet's own shape is read off it and the maxima are simply
    unknown."""
    faults: list[ConfigFault] = []
    try:
        sheet = parse_sheet(text, faults, slug)
    except SheetUnreadable:
        return faults  # `parse_sheet` recorded it; there is nothing else to read
    spec = spec or _spec_from_sheet(slug, sheet)
    container = sheet.get(spec.container_key)
    if container is not None and not isinstance(container, dict):
        return [
            _sheet_fault(
                slug,
                f"`{spec.container_key}:` is not a mapping of submission units, so the "
                f"sheet is left exactly as it is",
                lineno=key_lines(text).get((spec.container_key,)),
                field=spec.container_key,
                fix=f"restore `{spec.container_key}:` to one indented entry per "
                f"submission unit",
                plain=_SHEET_SHAPE_PLAIN.format(sheet=slug),
            )
        ]
    sheet_hold_reasons(spec, sheet, faults, text, slug)
    return faults


def semester_sheet_faults(
    course_org: str, semester_org: str, sched, found: list[ConfigFault]
) -> None:
    """Every grading sheet this semester's plan declares, and everything in each of them a
    grader has to settle.

    Asked on the scheduled tick and NOWHERE else - no push fast path. A sheet is edited
    all day while marking, so a notification per save would mail a grader about a file
    they are still typing into; the tick catches it once it has been left that way.

    Nothing is appended until every sheet has been READ. Half a list is worse than none:
    the digest closes what it is not handed, so a rate limit part-way through would report
    the sheets it never reached as repaired. A read that failed raises, and the caller
    drops the sheets from this tick (see `scheduler._config_faults`)."""
    specs = sheet_specs(course_org, sched)
    faults: list[ConfigFault] = []
    for name in sorted(specs):
        text = get_file_content(semester_org, CONFIG_REPO, sheet_path(name))
        if text is None:
            continue  # no sheet yet - the normal state before an assignment is due
        faults += sheet_faults(name, text, specs[name])
    found.extend(faults)


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
        and as_decimal(view["final_grade"]) is None
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


def build_gradebooks(
    sources: dict[str, tuple[SheetSpec, dict]],
) -> dict[str, dict[str, dict]]:
    """Pivot every assignment's grading sheet into `{handle: {slug: view}}` - one book per
    student, one entry per assignment they have a mark or a word of feedback on.

    Assignments are folded in sorted order, so a re-run renders byte-identical files and
    only a gradebook that really changed is committed and emailed. An empty view is not an
    entry: a student with nothing in the sheet yet has nothing to be told."""
    books: dict[str, dict[str, dict]] = {}
    canonical: dict[str, str] = {}  # fold key -> the first spelling seen for it
    for slug in sorted(sources):
        for handle, view in _views_from_sheet(*sources[slug]).items():
            if not view:
                continue
            key = canonical.setdefault(handle.casefold(), handle)
            books.setdefault(key, {})[slug] = view
    return books


def _on_the_roster(
    books: dict[str, dict[str, dict]], students: list[roster.Student] | None
) -> tuple[dict[str, dict[str, dict]], int]:
    """The books belonging to somebody this semester's roster knows, and how many marks the
    rest accounted for.

    A grading sheet is hand-typed, so it carries blocks for handles the semester does not
    have: a student who withdrew before onboarding, a handle typed from memory, a sheet
    carried over wholesale from the term before. `ensure_gradebooks`
    provisions one repo per ONBOARDED enrolled student and nothing else, so a book for any
    other handle was a write to a repo that does not exist - a 404 per row, in a PUBLIC
    log, naming `grades-<handle>` as it went. Dropping them here is what makes the two
    agree.

    A roster that could not be READ (None) filters nothing: the run is already going red
    for it, and treating an unreadable file as "nobody is enrolled" would withhold the
    whole semester's grades on the strength of a transient failure."""
    if students is None:
        return books, 0
    known = {
        s.github_handle.casefold() for s in roster.enrolled(students) if s.onboarded
    }
    kept = {
        handle: book for handle, book in books.items() if handle.casefold() in known
    }
    unknown = 0
    for handle in sorted(set(books) - set(kept)):
        unknown += len(books[handle])
        log_person(
            f"  [unknown] {handle} is not an onboarded student on this roster - "
            f"{len(books[handle])} mark(s) ignored"
        )
    return kept, unknown


def _cell(value: object) -> str:
    """One value as a Markdown table cell: no `|` to close the column early, no newline to
    end the row. A grader's feedback is free text and can reach a table either way."""
    text = "" if is_blank(value) else " ".join(str(value).split())
    return text.replace("|", "\\|")


def _over_max(value: object, max_points: object) -> str:
    """`40 / 50` where the assignment declares a total, `40` where it does not."""
    text = "" if is_blank(value) else str(value).strip()
    return f"{text} / {max_points}" if text and not is_blank(max_points) else text


def _late_display(days_late: object) -> str:
    """`on time`, `1 day late`, `2 days late` - "" where nothing was timed."""
    days = as_decimal(days_late)
    if days is None:
        return ""
    if days <= 0:
        return "on time"
    return f"{_plain(days)} day{'' if days == 1 else 's'} late"


def _readme_label(slug: str, title: str) -> str:
    """`Assignment 5 · Portfolio piece`: the identifier the site and schedule show, then the
    name, with a name that merely repeats the identifier folded away (`course.row_name`).
    A book whose slug has no title yet is labelled by the identifier alone."""
    ident = identifier(slug)
    name = row_name(title, ident) if title != slug else ""
    return f"{ident} · {name}" if name else ident


def _readme_row(title: str, view: dict) -> str:
    """One assignment's row in the summary table."""
    values = (
        title,
        _over_max(view.get("final_grade", ""), view.get("max_points")),
        # An external assignment says so; anything else with no time on it is a repo
        # nothing was ever pushed to.
        view.get("submitted") or _NOT_SUBMITTED,
        _late_display(view.get("days_late")),
        view.get("team", ""),
    )
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def _readme_grade_line(view: dict, grade: str) -> str:
    """The final grade, and - where a deadline moved it - the arithmetic that produced it:
    `**Score:** 20 / 50 · 2 days late · penalty -20% · **Final grade:** 16 / 50`.

    A student cannot reconstruct that from the final grade alone, and without it a
    deduction reads as a harsh mark. It used to be the opening line of the feedback comment
    on their own repo; that channel is closed, and the gradebook is the only place any of
    it is written now.

    Written only where there is a separate score AND something timed it. A hand-marked unit
    has a final grade and no score behind it, an `external` assignment counts no days, and
    a team's score is not in a member's view at all (`student_view`) - in each of those the
    final grade is the whole of what there is to say, and `**Score:** 40 · **Final grade:**
    40` would be arithmetic theatre. The helpers are the display ones the table already
    uses, so a row and its section cannot word the same fact differently."""
    total = score_total(view.get("score"))
    late = _late_display(view.get("days_late"))
    if total is None or not late:
        return f"**Final grade:** {grade}"
    facts = [f"**Score:** {_over_max(_plain(total), view.get('max_points'))}", late]
    if view.get("penalty"):
        facts.append(f"penalty {view['penalty']}")
    facts.append(f"**Final grade:** {grade}")
    return _SEP.join(facts)


def _readme_section(title: str, view: dict) -> str:
    """One assignment's section: the final grade with whatever was done to it, the
    student's own feedback verbatim, and the team's feedback as a blockquote that says who
    else has read it."""
    grade = _over_max(view.get("final_grade", ""), view.get("max_points"))
    parts = [f"## {title}" + (f"\n{_readme_grade_line(view, grade)}" if grade else "")]
    if not is_blank(view.get("feedback")):
        parts.append(str(view["feedback"]).strip())
    if not is_blank(view.get("team_feedback")):
        label = f"**Team feedback (shared with {view.get('team', 'your team')}):**"
        lines = str(view["team_feedback"]).strip().split("\n")
        quoted = [f"> {label} {lines[0]}".rstrip()]
        quoted += [f"> {line}".rstrip() for line in lines[1:]]
        parts.append("\n".join(quoted))
    return "\n\n".join(parts)


def render_readme(handle: str, book: dict[str, dict], titles: dict[str, str]) -> str:
    """One student's gradebook README - the file they actually open.

    The privacy line, one row per assignment, then a section per assignment with their
    feedback. It gives the final grade and never the sum behind it: the team's score and
    their own adjustment are their grader's working, and a student reading their own
    deduction beside their team-mates' shared mark is exactly the conversation this
    workflow exists to avoid.

    `handle` is the student the book belongs to; the text names nobody - the repo is
    already private to them - and takes it so that every per-student write reads the
    same at the call site. `titles` maps a slug to the assignment's name, falling back to
    the slug rather than rendering an empty heading."""
    del handle
    slugs = sorted(book)
    table = [
        "| " + " | ".join(_README_COLUMNS) + " |",
        "|" + "---|" * len(_README_COLUMNS),
        *(
            _readme_row(_readme_label(slug, titles.get(slug, slug)), book[slug])
            for slug in slugs
        ),
    ]
    sections = [
        _readme_section(_readme_label(slug, titles.get(slug, slug)), book[slug])
        for slug in slugs
    ]
    return "\n\n".join([_PRIVACY_HEADER, "\n".join(table), *sections]) + "\n"


def render_registrar_csv(
    students: list[roster.Student], books: dict[str, dict[str, dict]]
) -> str:
    """The registrar's export: one row per ENROLLED student, one column per assignment,
    each cell the final grade exactly as that student was told it.

    Every enrolled student is a row, marked or not, and a student who has not onboarded
    yet is a row with no handle: a missing row reads as somebody who left the course, and
    this is the file a grade is transcribed from. Auditors are never assessed and are
    never rows. It lives in the private semester-config and is never logged."""
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


def load_sheets(wd: Path) -> dict[str, dict]:
    """Every grading sheet in a semester-config checkout, keyed by assignment slug.

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


def sheet_slugs(semester_org: str) -> list[str]:
    """The assignments this semester has a grading sheet for, off ONE listing.

    The names, not the sheets: the only caller is the setup checklist, which wants a count
    and a yes/no. Downloading and parsing each file to get them cost a request per
    assignment, and `parse_sheet` raises - so one sheet a grader had half-typed in the
    browser took the whole checklist down with it.

    [] where the folder does not exist, which is the normal state before the first handout
    rather than a fault - and, `gh` being optimistic here, also where the listing failed;
    the checklist reports that semester as having no sheets yet, which is what it would say
    anyway."""
    code, out = gh(
        "api",
        f"repos/{semester_org}/{CONFIG_REPO}/contents/{SHEETS_DIR}",
        "--jq",
        ".[].name",
    )
    if code != 0:
        return []
    return sorted(n[:-4] for n in out.splitlines() if n.endswith(".yml"))


def _tag_gradebook(semester_org: str, repo: str, have: set[str]) -> None:
    """Stamp `gradebook` on one private gradebook repo. Checked.

    Called on the ALREADY-EXISTS path too: the stamp is a separate PUT after the create,
    and one that failed used to stand until `access.converge_topics` came round on the
    nightly refresh. `have` is the repo's topics off the caller's listing, so a gradebook
    already carrying the topic costs no call, and whatever else it carries is written back
    with it (the PUT replaces the whole list).

    A failed PUT is said out loud rather than dropped, because the topic is what the
    faculty-access floor and the release targets read. It is not a leak, though:
    `discovery._has_infra_topic` recognises `grades-<handle>` by NAME whatever its topics
    say, precisely so a failed stamp cannot put it on a public page. A backstop is not a
    reason to leave the record wrong - it is the reason the line below does not cry fire.
    """
    if "gradebook" in have:
        return
    if not set_repo_topics(
        semester_org, repo, sorted(have | {"gradebook"}), person=True
    ):
        log_err(
            f"  ! a gradebook in {semester_org} carries no `gradebook` topic. The name rule "
            f"in `discovery._has_infra_topic` still keeps it off the public org landing "
            f"page, so no handle is published - but the record stays wrong until the "
            f"stamp lands. The next sync with a repo listing retries it, as does the "
            f"nightly refresh."
        )


def provision_one(
    semester_org: str, handle: str, existing: dict[str, dict] | None = None
) -> str:
    """Ensure a private grades-<handle> repo exists with the student as read collaborator.

    `existing` is the semester's repos off ONE listing (`discovery.listing_by_name`), keyed
    by name; membership in it answers "is this gradebook already there?" without a GET per student,
    and each row carries the `topics` that `_tag_gradebook` converges off. None - no
    listing to hand - falls back to probing this one repo, and skips that convergence
    rather than paying a read per student for it."""
    repo = f"{GRADEBOOK_PREFIX}{handle}"
    existed = exists_in(existing, semester_org, repo)
    if existed:
        log_person(f"  [skip] gradebook {semester_org}/{repo}")
        # Converge the stamp off the listing row that already answered "is it there?",
        # rather than pay a read per student for it. An ARCHIVED gradebook is passed over:
        # it is read-only, so the PUT would 403 on every sync, and a finished semester is
        # meant to stay frozen - `access.converge_topics` skips them for the same reason.
        row = existing[repo] if existing is not None else None
        if row is not None and not row.get("archived"):
            _tag_gradebook(semester_org, repo, set(row.get("topics") or []))
    else:
        if not create_repo(
            semester_org,
            repo,
            private=True,
            description=f"Private gradebook for @{handle}",
            person=True,
        ):
            return "failed-create"
        if existing is not None:
            # The caller's listing is now one repo out of date, and in a scheduler tick
            # the passes after this one read it (`discovery.listing_row`).
            existing[repo] = listing_row(semester_org, repo)
        put_file(
            semester_org,
            repo,
            "README.md",
            _STARTER_README.encode(),
            "init gradebook",
            person=True,
        )
        _tag_gradebook(semester_org, repo, set())

        # At creation only: a team grant does not decay, and the nightly sweep
        # (access.converge_faculty_access) owns the floor for every gradebook that already
        # exists - so re-granting on every sync cost two PUTs per student for nothing.
        #
        # Read, not write: `distribute` rewrites grades.yml from the grading sheet, so a
        # mark corrected here would be overwritten on the next run. The sheet is where a
        # mark belongs.
        grant_faculty(
            semester_org,
            repo,
            FACULTY_READ_ACCESS,
            missing_is_note=True,
            person=True,
        )
    if add_collaborator(semester_org, repo, handle, permission="pull", person=True):
        log_person(f"  [ok]   + @{handle} (read)")
        return "skipped" if existed else "ok"
    # A gradebook the student can't open is a failure, not a partial success - the status
    # starts with "failed" so it reaches the exit code (see sync).
    log_err(f"  ! could not add @{handle} (not a real account?)")
    return "failed-no-collaborator"


def ensure_gradebooks(
    semester_org: str,
    dry_run: bool = False,
    existing: dict[str, dict] | None = None,
    budget_minutes: float | None = None,
) -> int:
    """Provision one private gradebook repo per onboarded enrolled student. Idempotent.

    Named for what it does, and called by `distribute` before anything is written into
    one: a student who onboarded since the last run has no repo to push a grade into, and
    the failure a moment later would say only "could not write".

    Auditors are read-only and are never assessed, so they get no gradebook.

    `existing` is the semester's repos off a listing the CALLER already holds, keyed by name;
    None means take one here. Every caller but the nightly sync has just listed the org for
    its own reasons, and a second listing per release run answers the same question twice.

    `budget_minutes` bounds the WALL CLOCK: once it is spent this stops and says how many
    students are left, and they wait for the next run. None - the default - is unbounded,
    which is what a caller with work waiting on these repos needs: a handout that stopped
    halfway would publish a brief pointing at gradebooks half the semester does not have,
    and `distribute` is about to write a mark into every one of them. Only the nightly
    `sync_membership` passes a budget, because nothing in that run waits on the result -
    and the number of minutes is that caller's own
    (`sync_membership.GRADEBOOK_BUDGET_MINUTES`).

    A roster that is absent or empty is a SKIP, not a failure, for the reason
    `sync_roster.sync` gives: an empty roster is a freshly bootstrapped semester and a
    missing one is a content fault the roster's own digest issue already reports to the
    people who can fix it. This runs on every nightly Sync membership now, and reddening
    that run would tell a maintainer only that something is wrong in an org they cannot fix
    it in. `distribute` has its own guard: it is about to write a mark per student, so an
    unreadable roster stops it."""
    students = roster.load(semester_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 0
    if not students:
        log(f"  [skip] no gradebooks in {semester_org} - its roster has no rows yet")
        return 0
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    log_step(f"Syncing {len(onboarded)} gradebook repo(s) in {semester_org}")
    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, never assessed)")

    # ONE listing of the semester answers "is it already there?" for every student below.
    # A dry run creates nothing, so it needs no answer.
    if existing is None and not dry_run:
        existing = listing_by_name(semester_org)
    results: dict[str, int] = {}
    deferred = 0
    started = time.monotonic()
    for done, s in enumerate(onboarded):
        if dry_run:
            log_person(
                f"    PREVIEW  {semester_org}/{GRADEBOOK_PREFIX}{s.github_handle}"
            )
            continue
        if budget_minutes is not None and (
            time.monotonic() - started > budget_minutes * 60
        ):
            # STOP rather than step through what is left: the budget is spent, so every
            # student after this one is deferred by the same clock.
            deferred = len(onboarded) - done
            break
        status = provision_one(semester_org, s.github_handle, existing)
        results[status] = results.get(status, 0) + 1
    if dry_run:
        return 0
    if deferred:
        # A COUNT, and green: nothing is lost, and the students who do have one were all
        # reconciled. `ok` is `provision_one`'s word for a gradebook it CREATED, so the
        # line says what the budget actually bought. The next run takes the next batch.
        log(
            f"  ({deferred} more gradebook(s) on the next run - {results.get('ok', 0)} "
            f"created here, and one run spends at most {budget_minutes:g} minute(s) on it)"
        )
    log_ok(f"Done - {json.dumps(results)}")
    return 1 if any(k.startswith("failed") for k in results) else 0


# ---------------------------------------------------------------- what was distributed

# One row per thing SAID, so a re-run says nothing twice and a failure is retried exactly
# once. It replaces `gradebook/notified.csv`, which recorded only the email and only per
# student - so a corrected grade re-emailed the whole semester, and a write that failed was
# never retried because nothing recorded that it had not.
DISTRIBUTED_PATH = records.path("distributed")
DISTRIBUTED_HEADER = (
    "target",  # a handle; a TEAM name on the rows the retired issue channel left behind
    "assignment",  # the semester-side slug; "" for the whole-book email
    "channel",
    "content_hash",
    "distributed_at",
    # The issue a retired `issue` row's comment landed on. Nothing writes it now; the
    # column stays so a file written before marks moved to the gradebook alone still reads
    # and still round-trips, rather than losing a column on its first rewrite.
    "issue",
)
# Marks and feedback go to ONE place, the student's gradebook, so the channels are the
# gradebook, the registrar's export and the email. `issue` is RETIRED: no run writes it,
# and it is named here only so a row left by an older run is recognisable as one.
CHANNEL_ISSUE = "issue"
CHANNEL_GRADEBOOK = "gradebook"
CHANNEL_EMAIL = "email"
# `{(target, assignment, channel): (content hash, when, issue number or "")}`
Distributed = dict[tuple[str, str, str], tuple[str, str, str]]


def content_hash(text: str) -> str:
    """The short hash `distributed.csv` records a gradebook commit under."""
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
            (row.get("issue") or "").strip(),
        )
        for row in read_csv(text, ("target",), DISTRIBUTED_PATH)
        if (row.get("target") or "").strip()
    }


def dump_distributed(records: Distributed) -> str:
    """Sorted, so a run that changed one row shows one line in the diff."""
    return dump_csv(
        DISTRIBUTED_HEADER,
        (
            (target, assignment, channel, digest, when, issue)
            for (target, assignment, channel), (digest, when, issue) in sorted(
                records.items()
            )
        ),
    )


def _read_distributed(wd: Path) -> tuple[Distributed, bool]:
    """`(what has been distributed, whether this run is the migration)`.

    A semester part-way through the term has `notified.csv` and no `distributed.csv`. Its
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
            "",
        )
        for row in read_csv(old.read_text(), ("github_handle",), NOTIFIED_PATH)
        if (row.get("github_handle") or "").strip()
    }, True


def _retired_gradebook_files(wd: Path) -> list[str]:
    """The per-student YAML the retired `render` staged for its preview PR. The gradebook
    repos hold the real thing now, and a stale copy of a grade is worse than none.

    Asked on EVERY run, not only on the `notified.csv` migration: a semester that reached
    `distributed.csv` without ever having had a notified.csv - which is every semester
    bootstrapped since - was never on the migration path, so its `gradebook/*.yml` sat
    there for the rest of the term. They are dead either way, and the only file in that
    folder anything still reads is `distributed.csv`, which is not a `.yml`."""
    folder = wd / GRADEBOOK_DIR
    if not folder.is_dir():
        return []
    return sorted(f"{GRADEBOOK_DIR}/{p.name}" for p in folder.glob("*.yml"))


def _told_grades(wd: Path) -> tuple[dict[str, dict[str, str]], set[str]]:
    """`({handle (casefolded): {slug: final grade}}, the export's columns)` as the last
    real run exported them, so the preview can say which grades a run would CHANGE and
    which columns it would ADD. Empty when there is no export yet or it cannot be read,
    and every grade and column is then new."""
    path = wd / SEMESTER_CSV_NAME
    if not path.is_file():
        return {}, set()
    try:
        reader = read_csv(path.read_text(), ("github_handle",), SEMESTER_CSV_NAME)
        rows = list(reader)
    except RuntimeError:
        return {}, set()
    told = {
        row["github_handle"].strip().casefold(): {
            k: v for k, v in row.items() if k and v and k not in _REGISTRAR_FIELDS
        }
        for row in rows
        if (row.get("github_handle") or "").strip()
    }
    return told, set(reader.fieldnames or ())


def _spec_from_sheet(slug: str, sheet: dict) -> SheetSpec:
    """A minimal spec for a sheet whose assignment the schedule no longer declares - a
    term whose entry has been deleted, or a hand-written sheet. Its shape is read off the
    file itself so the marks still reach their students; the maxima and the late policy
    are simply unknown, and nothing is derived from them.

    `shape_known=False` says the rest is a guess: there is no definition to read
    `submit_via` or `visibility` from, so this spec may use a Submission receipts issue it finds and
    may never open one (`may_open_receipts_issue`)."""
    return SheetSpec(
        slug=slug,
        title=slug,
        is_group="teams" in (sheet or {}),
        shape_known=False,
    )


def sheet_specs(course_org: str, sched) -> dict[str, SheetSpec]:
    """One spec per assignment the semester's schedule declares, keyed by its SEMESTER-side
    name - which is what the sheets, the repos and the gradebooks are all named after."""
    specs: dict[str, SheetSpec] = {}
    for key, entry in sched.assignments.items():
        name = schedule.semester_name(key, entry)
        gspec = (
            load_grading_spec(
                course_org, entry.course_source_repo, semester_org=sched.org, slug=key
            )
            if course_org
            else GradingSpec()
        )
        specs[name] = sheet_spec(
            sched,
            key,
            name,
            gspec,
            resolve_is_group(force=False, template_type=gspec.type),
        )
    return specs


def _not_marked(spec: SheetSpec, sheet: dict) -> dict[str, list[str]]:
    """`{unit: the questions still blank}` for every unit with SOME question marked and
    some not, and `{unit: []}` for every unit with no mark at all.

    A partly filled map totals to what has been typed so far, and a real run sends that
    total: which is right (a grader who wants to release Q1 early may) and is also exactly
    how half a mark reaches a student unnoticed. So the dry run says where they are, and
    the decision stays the grader's."""
    out = {}
    for unit, block in ((sheet or {}).get(spec.container_key) or {}).items():
        if not isinstance(block, dict):
            continue
        score = block.get(spec.score_key)
        if isinstance(score, dict) and spec.questions:
            blank = [k for k in spec.questions if is_blank(score.get(k))]
            if len(blank) == len(spec.questions):
                out[str(unit)] = []
            elif blank:
                out[str(unit)] = blank
        elif is_blank(score) or (
            isinstance(score, dict) and all(is_blank(v) for v in score.values())
        ):
            out[str(unit)] = []
    return out


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
            and not is_blank(person.get("adjustment_individual"))
        )
    return total


def _undue_marks(
    specs: dict[str, SheetSpec], books: dict[str, dict[str, dict]], now: datetime
) -> int:
    """How many marks are about to go out with nothing derived behind them, because the
    assignment's due date has not passed yet.

    Nothing fills `info:` before the due date - there is nothing to derive there, and a
    handout must not cost an API call per student (`collect.sync_sheet`) - so a grade sent
    early carries a gradebook row reading `not submitted` while the work sits in the repo.
    A grader may well mean it (a mark released early, a semester that has all handed in), so
    this is counted and said out loud and never blocks: the alternative is withholding a
    mark somebody decided to send.

    Read off the student VIEWS rather than the sheet, because that is what the gradebook
    row is rendered from: `submitted` is dropped from a view when there is no submission
    time in it (`_allowlisted`), and a shape that collects no commits has none to want."""
    return sum(
        1
        for book in books.values()
        for slug, view in book.items()
        if (spec := specs.get(slug)) is not None
        and spec.collects_commits
        and spec.due_at is not None
        and now < spec.due_at
        and "final_grade" in view
        and "submitted" not in view
    )


# What a run may do about one unit's Submission receipts issue. THREE answers and one function that
# gives them (`receipts_thread_policy`), asked by the submission receipts and by nothing
# else now that no mark is posted into a thread at all. THREE and not a boolean because
# the answers are about the REPO: `find` is "there may be a thread here, but do not open
# one", which is not the same fact as "there is nowhere to post", and the two used to be
# conflated in each caller that asked the question for itself.
THREAD_CREATE = "create"  # open one if this repo has none
THREAD_FIND = "find"  # use the thread it has; never open one
THREAD_NONE = "none"  # do not look, do not post


def receipts_thread_policy(
    spec: SheetSpec, listed: dict[str, dict] | None, repo: str
) -> str:
    """May this run touch `repo`'s Submission receipts issue, and may it OPEN one?

    Everything the toolkit knows about that, in one place: the assignment's SHAPE (does it
    have a Submission receipts issue at all?) and the org LISTING's word on the repo (is it there,
    and is it still private?). Both are needed - the file says what was handed out and the
    listing says what is there now - and each answer here is deliberately narrow in the
    direction that cannot hurt a student: a listing is a snapshot, and answering "no
    thread" off it wrongly drops a receipt.

    - `listed is None` - we could not look at all: FIND. The thread a student was told to
      read is still the right place for their receipts; opening one on an org nobody could
      list is how a second Submission receipts issue appears over it.
    - the repo is NOT in the listing: FIND where the shape creates a repo per unit (it may
      have been created since the listing was taken), NONE where it does not - an external
      assignment has no repos, and probing each would be an issues call per student for an
      answer already known.
    - the listing says the repo is not private: NONE. A hand-in time is a fact about a
      student, and it does not go where the world can read it - which is what a
      `visibility:` edited after handout leaves behind.
    - otherwise: CREATE for an assignment whose shape HAS a Submission receipts issue and whose shape
      was read from a real definition; FIND for every other one, which is what keeps a
      semester handed out before these shapes existed (Maths a1) getting its receipts in the
      thread it was told to read."""
    if listed is None:
        return THREAD_FIND
    row = listed.get(repo)
    if row is None:
        return THREAD_FIND if spec.creates_unit_repos else THREAD_NONE
    if not listed_is_private(row):
        return THREAD_NONE
    if spec.may_open_receipts_issue:
        return THREAD_CREATE
    return THREAD_FIND


def receipts_thread(
    spec: SheetSpec,
    semester_org: str,
    repo: str,
    unit: str,
    members: list[str],
    listed: dict[str, dict] | None,
    *,
    dry_run: bool = False,
) -> int | IssueLookupFailed | None:
    """The Submission receipts issue this unit's receipt goes on: its number, None where there is none
    this run may use, or `LOOKUP_FAILED` where the question could not be answered.

    THE entry point, and the only consumer of `receipts_thread_policy`. It takes the
    STRONGEST answer the policy gives and nothing weaker - the repo is in the listing, the
    listing says it is private, and the shape has a Submission receipts issue - because a receipt is
    the one thing that would OPEN a thread nobody has asked for yet. A repo the listing
    does not carry, or a listing that could not be read at all, waits for a tick that can
    say so: the grading sheet is the record and the receipt is a courtesy."""
    if receipts_thread_policy(spec, listed, repo) != THREAD_CREATE:
        return None
    return ensure_receipts_issue(
        semester_org, repo, receipts_thread_body(spec, unit, members), dry_run
    )


def _hold_undecided(
    books: dict[str, dict[str, dict]], reasons: dict[str, dict[str, str]]
) -> dict[str, dict[str, tuple[str, str]]]:
    """Take every mark that still needs a person out of the books, and say whose it was.

    Two kinds arrive here. `reasons` is what a grader TYPED that cannot be acted on
    (`sheet_hold_reasons`); the rest is arithmetic that could not be done - `pass`, two
    days late, is not `pass minus 20%`. Sending either puts a line a student can read
    where a decision should have been, and the dry run that counted it has already gone
    by. So the mark is HELD: no gradebook entry, no column in the registrar's export, no
    email, until a grader settles it in the sheet. Nothing else that student has is held
    with it.

    Returns `{slug: {handle: (submission unit, reason)}}` - the unit, because a group's
    mark is derived from the team block rather than from any member's view, so a grader
    reading the hold line is told which row to go and settle.
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


# The view keys a GRADER writes. The rest - `max_points`, `submitted`, `days_late` - are
# the toolkit's facts about the assignment and fill in on their own, so a book carrying
# only those has changed without there being anything for a student to read.
_GRADER_KEYS = ("final_grade", "score", "feedback", "team_feedback")


def _has_mark(book: dict[str, dict]) -> bool:
    """Whether any assignment in this book carries a mark or a word of feedback."""
    return any(key in view for view in book.values() for key in _GRADER_KEYS)


# Leads every EMAIL digest `_marks_digest` writes. A digest without it is LEGACY - a hash
# of the whole grades.yml, or of the whole book before that - and `distribute` carries it
# over rather than reading the change of scheme as a change of marks.
MARKS_DIGEST_PREFIX = "m1:"


def _canonical(value: object) -> object:
    """`value` with every mapping key a string, so a per-question map keyed `1:` beside
    `Q2:` still sorts."""
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    return value


def _marks_digest(book: dict[str, dict]) -> str:
    """What the EMAIL keys on: what a grader wrote, per assignment, and nothing else. A
    refreshed `submitted`, a new `max_points` or a respelt date moves grades.yml without
    there being anything new for a student to read; a late penalty that moves a mark
    moves `final_grade`, so it is still in here."""
    projection = {
        slug: {key: view[key] for key in _GRADER_KEYS if key in view}
        for slug, view in book.items()
    }
    text = json.dumps(
        _canonical({slug: keys for slug, keys in projection.items() if keys}),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return MARKS_DIGEST_PREFIX + content_hash(text)


# The data file of a gradebook. The commit is over the whole book; the email's LEGACY
# digest was over this file alone, and is recognised by hashing it (see `distribute`).
GRADES_DATA = "grades.yml"


def _gradebook_files(
    handle: str, book: dict[str, dict], titles: dict[str, str]
) -> dict[str, bytes]:
    """The two files a student's private gradebook holds: the data and the page."""
    return {
        GRADES_DATA: render_yaml({"student": handle, "assignments": book}).encode(),
        "README.md": render_readme(handle, book, titles).encode(),
    }


def _commit_record(
    semester_org: str, writes: dict[str, bytes], message: str, delete: list[str]
) -> bool:
    """Land `distributed.csv` and the registrar export, with ONE retry on a fresh head.

    The ref update is deliberately not forced, so a commit that landed between reading the
    head and moving it makes this fail - and the quarter-hourly scheduler commits into the
    same repo (a snapshot, a grading sheet) all through the term. The cost of losing that
    race was not a lost file but a lost RECORD: a gradebook commit is guarded by a blob
    compare, but the emails are guarded only by this file, so the next run mailed every
    student again.

    A retry is safe because the paths are DISJOINT from anything the cron writes and
    `put_files` re-reads the head, rebuilds the tree and re-filters the no-ops on each
    call: the second attempt is the same commit against whatever landed meanwhile. Two
    attempts, not a ladder - a second loss is a fault to report, not a race to keep
    running."""
    for attempt in (1, 2):
        if put_files(semester_org, CONFIG_REPO, writes, message, delete=delete):
            return True
        if attempt == 1:
            log(
                f"  ({CONFIG_REPO} moved under this run - rebuilding the record commit)"
            )
    return False


def _returned_units(
    specs: dict[str, SheetSpec],
    sheets: dict[str, dict],
    books: dict[str, dict[str, dict]],
    live: dict[str, str],
) -> list[tuple[SheetSpec, str]]:
    """`(spec, unit repo)` for every unit whose marks this run's gradebooks now hold: a
    member's book is live and carries a final grade for that assignment. Only shapes with
    a Submission receipts issue of their own, since the note goes there."""
    out: list[tuple[SheetSpec, str]] = []
    held = {h.casefold() for h in live}
    by_fold = {h.casefold(): h for h in books}
    for slug in sorted(sheets):
        spec = specs[slug]
        if not (spec.has_receipts_issue and spec.creates_unit_repos):
            continue
        for unit, block in ((sheets[slug] or {}).get(spec.container_key) or {}).items():
            if not isinstance(block, dict):
                continue
            members = (block.get("members") or {}) if spec.is_group else {unit: None}
            if any(
                str(m).casefold() in held
                and "final_grade"
                in (books.get(by_fold.get(str(m).casefold(), "")) or {}).get(slug, {})
                for m in members
            ):
                out.append((spec, submission_repo(slug, str(unit))))
    return out


def _post_returned_notes(
    semester_org: str,
    units: list[tuple[SheetSpec, str]],
    listed: dict[str, dict] | None,
) -> int:
    """Post `MARKS_RETURNED_NOTE` on each unit's Submission receipts issue, once per assignment
    (`marks_returned_marker`). Only on an issue that already exists and a repo the listing
    still says is private - the same guard every write into a Submission receipts issue takes.
    Returns how many units now carry the note. Never fatal: the gradebook is the record."""
    posted = 0
    for spec, repo in units:
        if receipts_thread_policy(spec, listed, repo) == THREAD_NONE:
            continue
        found = find_receipts_issue(semester_org, repo)
        if isinstance(found, IssueLookupFailed) or found is None:
            log_person(
                f"    [skip] {semester_org}/{repo}: no Submission receipts issue for the note"
            )
            continue
        if post_marked_comment(
            semester_org,
            repo,
            found[0],
            MARKS_RETURNED_NOTE,
            marks_returned_marker(spec.slug),
        ):
            posted += 1
            log_person(f"    marks-returned note on {semester_org}/{repo}#{found[0]}")
    return posted


def feedback_text(book: dict[str, dict], titles: dict[str, str]) -> str:
    """The feedback in one student's gradebook, as plain text for an email: one paragraph
    per assignment that has any. "" when there is none."""
    parts: list[str] = []
    for slug in sorted(book):
        view = book[slug]
        said = [
            str(view[key]).strip()
            for key in ("feedback", "team_feedback")
            if not is_blank(view.get(key))
        ]
        if said:
            parts.append(f"{titles.get(slug) or slug}:\n" + "\n\n".join(said))
    return "\n\n".join(parts)


def distribute(
    semester_org: str,
    notify: bool = True,
    dry_run: bool = False,
    *,
    receipt_note: bool = False,
    include_feedback: bool = False,
) -> int:
    """Send every mark a grader has written where it has to go: each student's private
    gradebook, the registrar's export, and an email saying there is something new to read.

    ONE place a mark is written, and it is the gradebook. Nothing is posted into a
    submission repo: a thread in a repo the toolkit does not own the visibility of is a
    mark one edited `visibility:` line publishes, and a student looking for a grade should
    have one address rather than one per assignment. The per-repo issue is still there and
    still carries the submission receipts (`collect._post_receipts`); it carries no mark.

    ONE clone of semester-config and one pass over it - the sheets and `distributed.csv`
    are both read locally, so the only per-student calls left are the
    writes. Every one of those is skipped when `distributed.csv` says the same content has
    already gone out, which is what makes a correction to one grade reach one student.

    There is no assignment to scope a run to, and the button no longer offers one. It
    narrowed the feedback comments and only ever those: a student's gradebook is the whole
    of what they have been given and is rendered from every sheet in the repo on every run,
    so it stays a pure function of the sheets rather than flip-flopping between a scoped
    and an unscoped write and re-mailing a semester each way. Rendering it from one selected
    sheet is what silently deleted every other assignment from every gradebook it touched.
    The registrar's export is the same file for the same reason.

    A preview - the default - writes no grades and sends nothing: it prints the counts a
    grader checks before pressing it for real, and posts the per-student detail as an
    issue in the private semester-config (`_preview`).

    `receipt_note` also posts `MARKS_RETURNED_NOTE` once per assignment on each returned
    unit's Submission receipts issue; `include_feedback` puts the feedback text into the email. Both
    off by default."""
    # ONE listing of the semester for the whole run: it answers "does this student already
    # have a gradebook?". A dry run writes nothing and needs none.
    listed = None if dry_run else listing_by_name(semester_org)
    provisioning_failed = bool(
        ensure_gradebooks(semester_org, dry_run=dry_run, existing=listed)
    )
    course_org = course_org_for_semester(semester_org)
    sched = schedule.load(semester_org)
    students = roster.load(semester_org)
    moment = datetime.now(UTC)
    now = moment.isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "cfg"
        if not clone(semester_org, CONFIG_REPO, wd):
            log_err(f"could not clone {semester_org}/{CONFIG_REPO}")
            return 1
        try:
            sheets = load_sheets(wd)
        except SheetUnreadable as exc:
            # Nothing goes out, rather than everything but this one: a grader fixes the
            # file and presses the button again, where a partial send would have to be
            # reconciled student by student.
            log_err(f"{exc} - nothing sent; fix the file and run this again")
            return 1
        if not sheets:
            log_err(
                f"no {SHEETS_DIR}/ in {semester_org}/{CONFIG_REPO} - hand out an "
                f"assignment (which creates its grading sheet) first"
            )
            return 1
        specs = sheet_specs(course_org, sched)
        sources: dict[str, tuple[SheetSpec, dict]] = {}
        for slug, sheet in sheets.items():
            specs.setdefault(slug, _spec_from_sheet(slug, sheet))
            sources[slug] = (specs[slug], sheet)
        titles = {slug: specs[slug].title for slug in sources}
        books, unknown = _on_the_roster(build_gradebooks(sources), students)
        distributed, migrating = _read_distributed(wd)
        retired = _retired_gradebook_files(wd)
        export = _told_grades(wd) if dry_run else ({}, set())

    held = _hold_undecided(
        books,
        {
            slug: sheet_hold_reasons(specs[slug], sheet)
            for slug, sheet in sheets.items()
        },
    )
    log_step(f"Distributing {len(books)} gradebook(s) in {semester_org}")
    record: Distributed = dict(distributed)
    # Key ORDER is the order the spec prints the `Done` line in, because that line is a
    # JSON dump of this dict and a grader reads it as text. `unknown` is the one key the
    # spec does not name: marks for handles nobody enrolled are worth a count, and it is
    # slotted where it disturbs the spec's own sequence least.
    counts = {
        "gradebooks": 0,
        "emails": 0,
        "held": sum(len(whose) for whose in held.values()),
        "unknown": unknown,
        "failed": 0,
    }
    for slug in sorted(held):
        for handle, (_unit, reason) in sorted(held[slug].items()):
            log_person(
                f"  [hold] {slug} for {handle} - {HOLD_REASONS[reason]}; nothing is sent "
                f"until it is settled in the sheet"
            )
    undue = _undue_marks(specs, books, moment)
    if undue:
        # A count, in both the dry run and the real one, and never a block: the gradebook
        # row will read `not submitted` for work that is sitting in the repo, because
        # nothing derives the submission facts until the due date has passed.
        log(
            f"  WARNING: {undue} mark(s) sent before the due date - the "
            f"submission facts are not derived yet"
        )

    # 1. The private gradebook: grades.yml and README.md in ONE commit per student, so a
    #    student never sees a page that disagrees with the data beside it.
    #
    #    A handle earns its place in `live` only where the gradebook really does hold that
    #    content. `live` is what step 2 keys the email on, so a student whose write FAILED
    #    must not be told to go and read a page that never changed - and, worse, have that
    #    telling recorded, which would stop them being told when it lands.
    #    TWO digests per student, because the two channels are answering different
    #    questions. The COMMIT is keyed on the whole book, so every wording change the
    #    toolkit makes to the README lands; the EMAIL is keyed on what a grader wrote
    #    (`_marks_digest`), so "there is something new to read" means a MARK moved. Keyed
    #    on one hash, a single standing sentence added to the page re-mailed every student
    #    in every live semester to tell them nothing.
    live: dict[str, str] = {}
    legacy: dict[str, str] = {}
    for handle in sorted(books):
        files = _gradebook_files(handle, books[handle], titles)
        digest = content_hash("".join(f.decode() for f in files.values()))
        marks = _marks_digest(books[handle])
        legacy[handle] = content_hash(files[GRADES_DATA].decode())
        if record.get((handle, "", CHANNEL_GRADEBOOK), ("",))[0] == digest:
            live[handle] = marks
            continue
        if dry_run:
            live[handle] = marks
            counts["gradebooks"] += 1
            continue
        if put_files(
            semester_org,
            f"{GRADEBOOK_PREFIX}{handle}",
            files,
            "grades: update",
            person=True,
        ):
            record[(handle, "", CHANNEL_GRADEBOOK)] = (digest, now, "")
            live[handle] = marks
            counts["gradebooks"] += 1
            log_person(f"  [ok] {GRADEBOOK_PREFIX}{handle}")
        else:
            counts["failed"] += 1

    # 2. Who still needs telling. Keyed on the MARKS, so a student whose book changed only
    #    because the page around their grades was reworded is not emailed, and one whose
    #    email FAILED last time is.
    pending: list[str] = []
    carried = 0
    for handle, marks in sorted(live.items()):
        told = record.get((handle, "", CHANNEL_EMAIL), ("",))[0]
        if told == marks:
            continue
        described = (
            legacy[handle],
            distributed.get((handle, "", CHANNEL_GRADEBOOK), ("",))[0],
        )
        if told and not told.startswith(MARKS_DIGEST_PREFIX) and told in described:
            # A LEGACY row that still describes this book: a hash of this very grades.yml,
            # or - from when BOTH channels were keyed on the whole book - of the content
            # this semester's last run committed. Either way this student has already been
            # told about everything it holds. Carried over to the marks digest in place -
            # the CSV keeps its columns, and the row means what it says once more. A
            # legacy row that matches neither falls through to exactly what the old code
            # did with it, so the change of scheme can never mail anybody on its own.
            record[(handle, "", CHANNEL_EMAIL)] = (marks, now, "")
            carried += 1
            continue
        if not _has_mark(books[handle]):
            # Nothing a grader wrote yet: the book moved on the toolkit's facts alone (a
            # submission time, the points available). Not recorded either, so the run
            # that brings the first mark is the one that tells them.
            continue
        pending.append(handle)
    if carried:
        # A count in the public log, and worth saying out loud once: a mark that first
        # appears in this very run is inside the window this carry-over covers, and
        # telling those students is then a person's job.
        log(
            f"  {carried} student(s) already knew what their gradebook said - their "
            f"record now keys on the marks alone and nothing is re-sent"
        )

    if dry_run and receipt_note:
        log(
            f"  would post the marks-returned note on up to "
            f"{len(_returned_units(specs, sheets, books, live))} Submission receipts issue(s)"
        )
    if dry_run:
        previewed = _preview(
            semester_org,
            sheets,
            specs,
            books,
            counts,
            pending if notify else [],
            held,
            students or [],
            export,
            first_email={
                h for h in pending if not record.get((h, "", CHANNEL_EMAIL), ("",))[0]
            },
            moment=moment,
            notify=notify,
        )
        # `not students` on both exits - no rows AND no file, for the same reason:
        # `ensure_gradebooks` passes over either rather than redden the nightly sync it now
        # also runs on, so saying so is this run's own job. Distribute is about to write a
        # mark per student, and a header-only students.csv is not a semester nobody enrolled
        # in by the time marks exist: `_on_the_roster` keeps only the books belonging to
        # somebody the roster knows, so an empty roster drops EVERY mark in the run as
        # `unknown`. This exit is the only signal that happened.
        if provisioning_failed or not students or not previewed:
            return 1
        return return_summary(counts, len(pending) if notify else 0, dry_run=True)

    failed_mail, told = (
        _email_updates(
            semester_org,
            pending,
            dry_run=False,
            feedback=(
                {h: feedback_text(books[h], titles) for h in pending}
                if include_feedback
                else None
            ),
        )
        if notify and pending
        else (0, [])
    )
    counts["emails"] = len(told)
    if receipt_note:
        counts["receipt_notes"] = _post_returned_notes(
            semester_org, _returned_units(specs, sheets, books, live), listed
        )
    for handle in told:
        record[(handle, "", CHANNEL_EMAIL)] = (live[handle], now, "")

    # 3. The registrar's export and the record of what went out, in ONE commit - together
    #    with the retired files this semester is migrating off, so the old and the new can
    #    never both be present for a reader to choose between.
    writes = {DISTRIBUTED_PATH: dump_distributed(record).encode()}
    if not students:
        # `roster.load` answers None for a roster it could not READ and [] for one with no
        # rows, and the export is one row per ENROLLED student - so regenerating it from
        # either would commit a header line over the file a registrar transcribes grades
        # from. Leaving it is the only safe answer; the run goes red and the next one
        # rebuilds it.
        log_err(
            f"roster in {semester_org} is empty or could not be read - "
            f"{SEMESTER_CSV_NAME} left as it is"
        )
    else:
        writes[SEMESTER_CSV_NAME] = render_registrar_csv(students, books).encode()
    recorded = _commit_record(
        semester_org,
        writes,
        f"grades: distribute ({counts['gradebooks']} gradebook(s), "
        f"{counts['emails']} email(s))",
        [*([NOTIFIED_PATH] if migrating else []), *retired],
    )
    if not recorded:
        log_err(
            f"grades were sent but {DISTRIBUTED_PATH} could not be written - the "
            f"next run re-posts and re-emails what it cannot see was already sent"
        )
    # The dry run's preview is out of date once anything has gone out. Not a reason to red
    # the run: a preview left open is closed by the next real one.
    close_issues_titled(f"{semester_org}/{CONFIG_REPO}", PREVIEW_TITLE, PREVIEW_SENT)
    # Counts only: this workflow's log is world-readable and every target here is a
    # student. The per-target lines above went through log_person.
    log_ok(f"Done - {json.dumps(counts)}")
    code = (
        1
        if provisioning_failed
        or counts["failed"]
        or failed_mail
        or not recorded
        or not students
        else 0
    )
    return return_summary(counts, counts["emails"], dry_run=False, code=code)


def return_summary(
    counts: dict[str, int], emails: int, dry_run: bool, code: int = 0
) -> Summary:
    """Return marks' sentence, off distribute's counts. Counts only - every target is a
    student, and this lands in a public annotation."""
    books = plural(counts["gradebooks"], "marks repo")
    mails = plural(emails, "email")
    if dry_run:
        text = f"Preview: {books} would be updated and {mails} sent"
    elif not counts["gradebooks"] and not emails:
        text = "No new marks to return"
    else:
        text = f"Marks returned: {books} updated, {mails} sent"
    if counts.get("held"):
        text += f"; {plural(counts['held'], 'mark')} held until the sheet is fixed"
    out = {k: v for k, v in counts.items() if isinstance(v, int)}
    conclusion = (
        "nothing_to_do"
        if not dry_run and not code and not counts["gradebooks"] and not emails
        else None
    )
    return Summary(f"{text}.", out, code=code, conclusion=conclusion)


# The dry run's per-student detail: an issue in the PRIVATE semester-config, found by
# this exact title, rewritten by every dry run and closed by the real one.
PREVIEW_TITLE = "Distribute grades preview"
PREVIEW_SENT = (
    "Grades sent by a real run of Distribute grades - this preview is out of date."
)
# GitHub refuses an issue body longer than this.
_ISSUE_BODY_CAP = 65_536


def _preview(
    semester_org: str,
    sheets: dict[str, dict],
    specs: dict[str, SheetSpec],
    books: dict[str, dict[str, dict]],
    counts: dict[str, int],
    emailed: list[str],
    held: dict[str, dict[str, tuple[str, str]]],
    students: list[roster.Student],
    told: tuple[dict[str, dict[str, str]], set[str]],
    *,
    first_email: set[str],
    moment: datetime,
    notify: bool,
) -> bool:
    """The dry run's report: counts a grader can check in the log, and the detail behind
    them in the preview issue. False when the issue could not be written.

    No names, and no marks, in the log: this is the log of a workflow that runs in a
    PUBLIC repo. Who would get what goes into the issue, in the private semester-config."""
    told_grades, columns = told
    not_marked = {slug: _not_marked(specs[slug], sheets[slug]) for slug in sheets}
    exported = {slug for book in books.values() for slug in book}
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
        partly = sum(1 for blank in not_marked[slug].values() if blank)
        if partly:
            log(f"    {partly} unit(s) have unmarked questions")
        if slug in exported and slug not in columns:
            log(f"  {SEMESTER_CSV_NAME}: would gain column {slug}")
    if counts["unknown"]:
        # Counted, not named: a handle nobody enrolled is still somebody's.
        log(f"  {counts['unknown']} mark(s) for handles not on the roster - ignored")
    log(
        f"  would update {counts['gradebooks']} gradebook(s) and email "
        f"{len(emailed)} student(s)"
    )
    repo = f"{semester_org}/{CONFIG_REPO}"
    wrote = upsert_issue(
        repo,
        PREVIEW_TITLE,
        _preview_body(
            semester_org,
            specs,
            books,
            emailed,
            held,
            students,
            told_grades,
            not_marked,
            first_email=first_email,
            moment=moment,
            notify=notify,
        ),
    )
    if not wrote.errors:
        log(f"  Who gets what: {wrote.url or f'{PREVIEW_TITLE} in {repo}'}")
    log_ok("PREVIEW - no grades written, no mail sent")
    return not wrote.errors


# Each hold reason as the preview says it of one student, after their handle and name.
# `HOLD_REASONS` is the log's noun phrase; this is the sentence a grader reads in the
# issue, in their own words for the sheet rather than the pipeline's.
_HOLD_PREVIEW = {
    "penalty": "was late, and their mark is not a number a late penalty can come off",
    "score": "has a question mark that is not a number",
    "adjustment": "has an adjustment that is not a number",
    "question": "has a mark for a question the assignment does not have",
    "duplicate": "is in more than one team",
}


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _preview_body(
    semester_org: str,
    specs: dict[str, SheetSpec],
    books: dict[str, dict[str, dict]],
    emailed: list[str],
    held: dict[str, dict[str, tuple[str, str]]],
    students: list[roster.Student],
    told: dict[str, dict[str, str]],
    not_marked: dict[str, dict[str, list[str]]],
    *,
    first_email: set[str],
    moment: datetime,
    notify: bool,
) -> str:
    """The preview issue, laid out by what a grader has to do: what to fix, what is not
    marked yet, which grades would change, and who would be emailed and why. A grade is
    listed only where it differs from the registrar's export, which is what the last real
    run sent. If it does not fit in one issue, what is left out is counted."""
    names = {s.github_handle.casefold(): s.name for s in students if s.github_handle}

    def who(handle: str) -> str:
        name = names.get(handle.casefold(), "")
        return f"`{handle}` ({name})" if name else f"`{handle}`"

    fixes = [
        f"- **{slug}** · {who(handle)} {_HOLD_PREVIEW[reason]}. Fix it in "
        f"`{SHEETS_DIR}/{slug}.yml`."
        for slug, whose in sorted(held.items())
        for handle, (_unit, reason) in sorted(whose.items())
    ]

    unmarked = []
    for slug, units in sorted(not_marked.items()):
        noun = ("team", "teams") if specs[slug].is_group else ("student", "students")
        by_blank: dict[tuple[str, ...], list[str]] = {}
        for unit, blank in sorted(units.items()):
            by_blank.setdefault(tuple(blank), []).append(unit)
        # The rows with no mark at all first: they are the bigger job.
        for blank, whose in sorted(by_blank.items(), key=lambda kv: (bool(kv[0]), kv)):
            what = f"{', '.join(blank)} blank" if blank else "no mark yet"
            unmarked.append(
                f"- **{slug}** · {_counted(len(whose), *noun)}: "
                f"{', '.join(f'`{u}`' for u in whose)} ({what})"
            )

    changes = []
    changed: set[str] = set()
    for handle, book in sorted(books.items()):
        was = told.get(handle.casefold(), {})
        for slug, view in sorted(book.items()):
            grade = str(view.get("final_grade", ""))
            if grade and grade != was.get(slug):
                changed.add(handle)
                changes.append(
                    (
                        slug,
                        handle,
                        f"- **{slug}** · {who(handle)} · {grade} "
                        + (f"(was {was[slug]})" if slug in was else "(new)"),
                    )
                )

    mails = []
    for handle in sorted(emailed):
        if handle in first_email:
            why = "their first grades email"
        elif handle in changed:
            why = "a grade changed"
        else:
            why = "feedback changed"
        shown = " · ".join(
            f"{slug} {view['final_grade']}"
            for slug, view in sorted(books.get(handle, {}).items())
            if not is_blank(view.get("final_grade"))
        )
        mails.append(
            f"- {who(handle)} - {why}.\n"
            f"  Their gradebook shows: {shown or 'feedback only, no grade yet'}"
        )

    sections = [
        (
            f"### ⚠️ Fix these first - held back, not sent ({len(fixes)})",
            fixes,
            "Nothing - no mark is held back.",
        ),
        (
            f"### Not marked yet ({sum(len(u) for u in not_marked.values())})",
            unmarked,
            "Nothing - every row in every sheet has a mark.",
        ),
        (
            f"### Grades that would change ({len(changes)})",
            [line for _slug, _handle, line in sorted(changes)],
            "Nothing new or changed since grades were last sent.",
        ),
        (
            f"### Students who would be emailed ({len(mails)})",
            mails,
            "Nothing - `notify` is unticked, so nobody is emailed."
            if not notify
            else "Nothing - nobody has a new mark to be told about.",
        ),
    ]
    head = [
        f"**Nothing has been sent.** Preview: {moment.day} {moment:%b %H:%M} UTC.",
        (
            "This is what running Distribute grades for real (with `preview` "
            "unticked) would do now."
        ),
        "Each preview replaces this text; the real run closes this issue.",
    ]
    tail = []
    if emailed:
        # Rendered exactly as the send renders it, course name and all: a preview that
        # showed the generic wording while the real mail named the course was reviewing
        # text nobody would ever receive.
        subject, body = sample_message(semester_org, _course_name(semester_org))
        tail = [
            "",
            "<details><summary>The email they would get</summary>",
            "",
            "```",
            f"Subject: {subject}",
            "",
            body.rstrip(),
            "```",
            "",
            "</details>",
        ]

    def render(shown: list[int]) -> str:
        out = list(head)
        for (heading, items, nothing), n in zip(sections, shown, strict=True):
            out += ["", heading]
            if not items:
                out.append(nothing)
                continue
            out += items[:n]
            if n < len(items):
                out.append(
                    f"_{len(items) - n} more not shown - the list is longer than one "
                    f"issue can hold._"
                )
        return "\n".join(out + tail)

    # Every heading, its empty line or its count of what was left out, and the email
    # always fit; the bullets fill what is left, in order, and stop at the first that
    # does not.
    shown = [0] * len(sections)
    room = _ISSUE_BODY_CAP - len(render(shown))
    for i, (_heading, items, _nothing) in enumerate(sections):
        for item in items:
            if len(item) + 1 > room:
                return render(shown)
            room -= len(item) + 1
            shown[i] += 1
    return render(shown)


def update_message(
    student: roster.Student,
    semester_org: str,
    course_name: str = "",
    feedback: str = "",
) -> mailer.Message:
    """The 'your grades have been updated' email for one student: (to, subject, body).

    The course goes in the SUBJECT as well as the body: the inbox list is where a student
    taking several of these actually tells them apart, and by the time they have opened it
    the body is redundant. A course with no name yet degrades to `course_phrase`'s plain
    "the course" in the body, and to the generic subject - never a blank, and never a
    literal placeholder."""
    url = f"https://github.com/{semester_org}/{GRADEBOOK_PREFIX}{student.github_handle}"
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"Your grades for {course_phrase(course_name)} have been updated. View them in "
        f"your private gradebook:\n"
        f"  {url}\n"
    )
    if feedback:
        body += f"\nFeedback from your markers:\n\n{feedback}\n"
    subject = (
        f"Your grades for {course_name} have been updated"
        if course_name
        else "Your grades have been updated"
    )
    return (student.hertie_email, subject, body)


def sample_message(
    semester_org: str, course_name: str = "", feedback: bool = False
) -> tuple[str, str]:
    """The notification's `(subject, body)` rendered with PLACEHOLDERS, for the preview.

    `update_message` with a placeholder in place of a student - see `mailer.sample_of`."""
    return mailer.sample_message_of(
        lambda student: update_message(
            student, semester_org, course_name, "<feedback>" if feedback else ""
        ),
        github_handle="<handle>",
    )


def sample_body(
    semester_org: str, course_name: str = "", feedback: bool = False
) -> str:
    """The body alone - what `send_bulk` prints beneath a dry-run send."""
    return sample_message(semester_org, course_name, feedback)[1]


def _course_name(semester_org: str) -> str:
    """The course's name for the subject and the body of an email, or "" if it cannot be
    read.

    Never fatal, and never skipped by the preview: the grades are already pushed by the
    time the send runs, so a transient read failure or a malformed dsl-course.yml must not
    turn a successful distribution into a traceback with zero notifications sent
    (`load_yaml_config` deliberately RAISES on both). A course that carries no name yet
    keeps the generic wording rather than emailing a blank."""
    try:
        return course_name_for_semester(semester_org)
    except Exception as exc:  # a name is never worth losing the notifications over
        log_err(f"could not read the course name ({exc}) - the email goes without it")
        return ""


def _email_updates(
    semester_org: str,
    handles: list[str],
    dry_run: bool = False,
    feedback: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Email each student a 'grades updated' notification to their Hertie email address,
    linking to their private gradebook repo (the grade's source of truth).

    Returns `(how many FAILED, which handles were told)`. `distribute` exits on the first
    and records the second: the grades themselves are already pushed by this point, so a
    mail failure is not a reason to undo anything - but a student who never got the
    notification does not know to look, and a green run told nobody."""
    # Fold-keyed: the gradebook names come from what a marker typed into the sheet and the
    # roster's casing is its own, so a case-only difference used to mean a student was
    # silently never told their grades had landed.
    students = roster.load(semester_org)
    if students is None:
        # Distinct from an empty roster: unreadable must red, as it does in enrol_codes.run.
        log_err(
            f"roster in {semester_org} could not be read - "
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
    course_name = _course_name(semester_org)
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
        messages.append(
            update_message(
                student, semester_org, course_name, (feedback or {}).get(handle, "")
            )
        )
    if not messages:
        # A withdrawn student is an ordinary state and must not red every distribution
        # from here on; a count says it happened without naming anyone.
        if handles:
            log_err(f"{len(handles)} gradebook(s) have no roster row with an email")
        return 0, []
    sent = mailer.send_bulk(
        messages,
        dry_run=dry_run,
        sample=sample_body(semester_org, course_name, feedback is not None),
    )
    failed = len(messages) - len(sent)
    if failed:
        log_err(f"{failed} of {len(messages)} grade notification(s) not sent")
    return failed, [
        h for to in sent for h in handles_for.get(to.strip().casefold(), [])
    ]


def main() -> int:
    parser = CLIParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("distribute")
    p.add_argument("--semester-org", required=True)
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip the email notification (just push the grades).",
    )
    p.add_argument(
        "--receipt-note",
        action="store_true",
        help="Also post 'Marks returned: see your marks repo.' once on each returned "
        "unit's Submission receipts issue.",
    )
    p.add_argument(
        "--include-feedback",
        action="store_true",
        help="Put the markers' feedback text into each student's email.",
    )
    # Default ON: the rendered workflow passes --preview / --no-preview explicitly, so a
    # bare local invocation cannot send by accident.
    add_preview_flag(
        p,
        "Post who gets what as an issue in semester-config; push no grades, send nothing (default).",
    )
    args = parser.parse_args()

    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return distribute(
            args.semester_org,
            notify=not args.no_notify,
            dry_run=args.preview,
            receipt_note=args.receipt_note,
            include_feedback=args.include_feedback,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
