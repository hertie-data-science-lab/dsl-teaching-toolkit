"""dsl-course grading sheet -- `semester-config/grading_sheets/<slug>.yml`, the one file a
grader types in.

Its shape (`SheetSpec`), its loader and dumper, the merge that refreshes it until the
cutoff freezes it, the mark arithmetic (totals, the late penalty, the final grade), and what
in a sheet holds a mark back (`sheet_hold_reasons`, `sheet_faults`). It knows nothing of the
template's `grading_config.yml` (`grading_spec`) or of where a mark goes (`gradebook`,
`distribute`).
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import yaml

from .course import (
    CONFIG_REPO,
    can_hold_solution,
    collects_commits,
    creates_repos,
    creates_unit_repos,
    has_receipts_issue,
    submit_shape,
    visibility_is_students,
)
from .faults import ConfigFault
from .gh_contents import (
    yaml_mark_line,
    yaml_problem,
)
from .ghcli import gh
from .setting_readers import (
    as_decimal,
    read_penalty,
)

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
# One feedback cell per declared question, beside the overall `feedback_*`: the team's on a
# group sheet, the student's on an individual one. Always written where `questions:`
# exist, all optional; a blank cell is never sent.
QUESTION_FEEDBACK_KEY = "feedback_per_question"
# The grader's say over one SUBMISSION UNIT's late penalty, beside its `info:`: `apply` (the
# late rule's own arithmetic, and what an absent key means), `waive`, or the whole penalty
# as a percent or fraction (`10%`, `0.1`). Never seeded: it appears when a grader writes it.
LATE_PENALTY_KEY = "late_penalty"
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
    # The questions marked from a file other than the runnable one, named beside each
    # question's maximum so a grader knows which file to open.
    question_files: dict[str, str] | None = None
    late_window_days: int | None = None
    late_penalty_per_day: str | None = None
    # The run setting: returned marks show each question's mark and feedback.
    marks_per_question: bool = True
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
    # The template's `grading_config.yml` was refused (`GradingSpec.not_migrated`): the
    # shape and late rule above are defaults nobody wrote, so nothing may be derived from
    # them - no sheet refresh, no return (decision 0009).
    not_migrated: bool = False

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
    """One unit's entry, brand new: the toolkit's facts first, then the grader's blanks,
    nested as the marking grid is - the team's (or the student's) own cells, then the
    members, then the questions (the score and the per-question feedback).

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
    if spec.is_group:
        block[spec.feedback_key] = None
        block["members"] = {handle: _blank_person() for handle in members}
    else:
        block.update(_blank_person())
    block[spec.score_key] = _blank_score(spec)
    if spec.questions:
        block[QUESTION_FEEDBACK_KEY] = {question: None for question in spec.questions}
    return block


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
    fields = [spec.feedback_key] if spec.is_group else []
    fields += ["adjustment_individual", "feedback_individual", NOTES_KEY]
    fields.append(f"{spec.score_key}{qualifier}")
    if spec.questions:
        fields.append(f"{QUESTION_FEEDBACK_KEY} (optional, one per question)")
    if spec.collects_commits:
        fields.append(
            f"{LATE_PENALTY_KEY} (optional, on a late unit: apply, waive, "
            "or the whole penalty as 10%)"
        )
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
                marked_from = (spec.question_files or {}).get(question)
                line = _with_comment(
                    line,
                    f"/{spec.questions[question]}"
                    + (f"{_SEP}{marked_from}" if marked_from else ""),
                )
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

    None for anything `read_penalty` refuses - which `parse_grading_spec` has already
    said out loud, once, when the assignment's definition was read."""
    return read_penalty(text)[0]


class LateChoice(NamedTuple):
    """A unit's `late_penalty` (`LATE_PENALTY_KEY`), read once: `apply` (what an absent or
    blank one means too), `waive`, `set` - the whole penalty is `fraction` - or `invalid`,
    which nobody can apply."""

    mode: str
    fraction: Decimal | None = None


def late_choice(late_penalty: object) -> LateChoice:
    """The grader's `late_penalty` as a `LateChoice`: a percent or fraction (`10%`, `0.1`)
    sets the whole penalty; anything else that is not apply or waive is `invalid`. A set
    penalty of nothing (`0%`) IS a waiver, and reads as one everywhere it is shown."""
    text = "" if late_penalty is None else str(late_penalty).strip()
    if text.casefold() in ("", "apply"):
        return LateChoice("apply")
    if text.casefold() == "waive":
        return LateChoice("waive")
    fraction = read_penalty(text)[0]
    if fraction is None:
        return LateChoice("invalid")
    return LateChoice("waive") if fraction == 0 else LateChoice("set", fraction)


class Penalty(NamedTuple):
    """What a unit loses for lateness (`effective_penalty`): `fraction` of its earned
    total, and the `mode` of the grader's `LateChoice` behind it."""

    fraction: Decimal
    mode: str


def effective_penalty(
    rate: object, days_late: object, late_penalty: object = None
) -> Penalty:
    """The fraction of the earned total a unit loses for lateness, and why.

    `late_penalty` is the grader's say (`late_choice`): absent or `apply` is `rate x
    days_late`, `waive` is nothing, and a percent or fraction (`set`) is the WHOLE penalty
    in place of that arithmetic. A unit that is not late loses nothing whatever is written.
    An `invalid` one keeps the late rule's arithmetic - not a penalty anybody can apply,
    so `sheet_hold_reasons` holds a late unit for a hand decision rather than guess.

    An assignment with NO late rule (`rate` None) takes nothing off, and whatever
    `late_penalty` says is ignored (`apply`): there is no policy for it to waive or
    override, so it neither deducts nor holds (`ignored_late_penalties` counts it for the
    preview)."""
    per_day = as_decimal(rate)
    if per_day is None:
        return Penalty(Decimal(0), "apply")
    choice = late_choice(late_penalty)
    days = as_decimal(days_late)
    if days is None or days <= 0 or choice.mode == "waive":
        return Penalty(Decimal(0), choice.mode)
    if choice.fraction is not None:
        return Penalty(choice.fraction, choice.mode)
    return Penalty(per_day * days, choice.mode)


def final_grade(
    total: object,
    rate: object,
    days_late: object,
    adjustment: object,
    late_penalty: object = None,
) -> Decimal | None:
    """`total x (1 - penalty) + adjustment`, floored at 0, where the penalty is `rate x
    days_late` unless the grader waived or set it (`effective_penalty`).

    Derived on OUTPUT and never stored: the sheet has no `final_grade` field, so there is
    no stale cell for this to disagree with. A blank adjustment is 0 and a blank rate or
    day count is no penalty, so an assignment with no late policy needs no special case.
    A total that is not a number gets no arithmetic at all and comes back None - the caller
    distributes the mark exactly as the grader typed it. A `late_penalty` nobody can read
    leaves the late rule's arithmetic here, and the unit held (`sheet_hold_reasons`)."""
    earned = as_decimal(total)
    if earned is None:
        return None
    earned *= Decimal(1) - effective_penalty(rate, days_late, late_penalty).fraction
    return max(Decimal(0), earned + (as_decimal(adjustment) or Decimal(0)))


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


def _units(spec: SheetSpec, sheet: dict | None) -> list[tuple[str, dict]]:
    """Every `(unit key, block)` on a sheet that is a block. A unit mid-edit
    (`team-alpha: TODO`) is passed over - the next run reads a whole block - and so is
    everything on a sheet whose container is not a mapping (`_misshapen` says so)."""
    container = (sheet or {}).get(spec.container_key)
    if not isinstance(container, dict):
        return []
    return [
        (unit, block) for unit, block in container.items() if isinstance(block, dict)
    ]


def _people(spec: SheetSpec, unit_key: str, block: dict) -> dict:
    """`{handle: their own fields}` of one unit: a group's `members:`, or the individual
    whose block it is. A `members:` that is not a mapping holds nobody here - it is a
    shape fault (`_misshapen`), and nothing on that sheet is returned while it stands."""
    if not spec.is_group:
        return {unit_key: block}
    members = block.get("members")
    return members if isinstance(members, dict) else {}


def _misshapen(spec: SheetSpec, sheet: dict | None) -> tuple[str, ...] | None:
    """The key path of the first place this sheet is not one block per unit and one block
    per member, or None. A grader editing in the browser turns `members:` into a list or
    a member into a word as easily as a cell into a typo, and that crashed Return marks,
    the status build and the sheet digest instead of naming the line to fix."""
    container = (sheet or {}).get(spec.container_key)
    if container is None:
        return None
    if not isinstance(container, dict):
        return (spec.container_key,)
    for unit, block in _units(spec, sheet) if spec.is_group else []:
        members = block.get("members")
        if members is None:
            continue
        if not isinstance(members, dict):
            return (spec.container_key, unit, "members")
        for handle, person in members.items():
            if person is not None and not isinstance(person, dict):
                return (spec.container_key, unit, "members", handle)
    return None


# Why a mark cannot be sent as it stands. The code is what the pipeline carries; the
# sentence is what the dry run prints. Every one of these used to pass silently.
HOLD_REASONS = {
    "penalty": "a mark no late penalty can be applied to",
    "score": "a non-numeric value in a per-question map",
    "adjustment": "a non-numeric adjustment",
    "late_penalty": "a late penalty that is not apply, waive or a percentage",
    "question": "a question the assignment does not declare",
    "duplicate": "a handle in more than one submission unit",
    "returned": "another of their marks held, one they have already been sent",
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

    # No late rule: nothing a `late_penalty` could apply to (`effective_penalty`).
    has_rule = penalty_rate(spec.late_penalty_per_day) is not None
    for unit_key, block in _units(spec, sheet):
        # An unreadable `late_penalty` holds only a LATE unit: on an on-time one, or under
        # no late rule, there is nothing for it to apply to (`effective_penalty`), so it
        # holds nobody's mark.
        days = as_decimal((block.get(INFO_KEY) or {}).get("days_late"))
        fault = _score_fault(spec, block.get(spec.score_key)) or (
            "late_penalty"
            if has_rule
            and days is not None
            and days > 0
            and late_choice(block.get(LATE_PENALTY_KEY)).mode == "invalid"
            else ""
        )
        for handle, person in _people(spec, unit_key, block).items():
            # Folded, as the gradebooks are (`build_gradebooks`): `Alice` in one team and
            # `alice` in another is one student in two units.
            if str(handle).casefold() in seen:
                record(unit_key, handle, "duplicate")
                continue
            seen.add(str(handle).casefold())
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
    "late_penalty": (
        (
            "the late penalty on this line is not apply, waive or a percentage, so "
            "nothing for this unit is sent"
        ),
        "write apply, waive or the whole penalty as a percentage (10%) on {at}",
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
    "late_penalty": "{where} has a late penalty that is not apply, waive or a "
    "percentage, so nothing is returned for that student or team.",
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
    if reason == "late_penalty":
        return LATE_PENALTY_KEY
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
    elif reason == "late_penalty":
        path = base + (LATE_PENALTY_KEY,)
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
    path = _misshapen(spec, sheet)
    if path == (spec.container_key,):
        return [
            _sheet_fault(
                slug,
                f"`{spec.container_key}:` is not a mapping of submission units, so the "
                f"sheet is left exactly as it is",
                lineno=key_lines(text).get(path),
                field=spec.container_key,
                fix=f"restore `{spec.container_key}:` to one indented entry per "
                f"submission unit",
                plain=_SHEET_SHAPE_PLAIN.format(sheet=slug),
            )
        ]
    if path:
        # The line, never the team or the member on it (see `_sheet_fault`).
        lineno = key_lines(text).get(path) or key_lines(text).get(path[:3])
        return [
            _sheet_fault(
                slug,
                "a team's `members:` on this line is not one entry per member, so "
                "nothing on this sheet is returned",
                lineno=lineno,
                field="members",
                fix=f"restore {f'line {lineno}' if lineno else '`members:`'} to one "
                f"indented `handle:` entry per member, each with its own fields",
                plain=_SHEET_SHAPE_PLAIN.format(sheet=slug),
            )
        ]
    sheet_hold_reasons(spec, sheet, faults, text, slug)
    return faults


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
