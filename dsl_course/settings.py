"""settings - every assignment setting: how its value is read, and where its effective
value comes from.

Every value is read by its key's reader (`setting_readers`).

The cascade: a RUN setting (`RUN_KEYS`: how teams form, the team cap, the late pair, the
repo visibility, the external submit link) resolves nearest-first through four layers -

    assignment   semester-config/assignments.yml  assignments.<slug>.<key>
    semester     semester-config/assignments.yml  defaults.<key>
    course       .github/dsl-course.yml            assignment_defaults.<key>
    institution  policy.yml                        defaults.<key>

and `effective` says which layer answered. The late pair is ONE rule: whichever layer
states either half states both, the other half absent meaning none (`_late_pair`).
`assignments.yml` is optional: absent, both of its layers are empty. A template's
`grading_config.yml` states no run setting (a run key there is NOT_MIGRATED). This is the
ONE module that reads `assignment_defaults`, and the one parser of `assignments.yml`
(`parse_instance`): its `semester_dest_repo` per assignment is read here too, for
`schedule.load`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache

import yaml

from . import policy
from .course import ASSIGNMENTS_FILE, CONFIG_REPO, COURSE_CONFIG
from .discovery import course_org_for_semester, org_meta
from .faults import ConfigFault, Unusable
from .gh_contents import (
    get_file_content,
    line_of,
    load_yaml_lines,
    take_lines,
    yaml_mark_line,
)
from .log import log_err
from .setting_readers import read_settings, refuse_renamed

# What a COURSE may set once for every assignment under it, in `dsl-course.yml`
# `assignment_defaults:` - the course layer of the cascade below. The run settings among
# them resolve at READ time; `formats`, `submit_via`, `team_formation` and `visibility` also
# answer New assignment's boxes left at `course.COURSE_DEFAULT_CHOICE`
# (`scaffold.resolve_answers`). The per-assignment keys - the title, the type, the question
# maxima - are deliberately not among them: they are what makes one assignment different
# from the next.
COURSE_DEFAULT_KEYS = (
    "max_team_size",
    "late_window_days",
    "late_penalty_per_day",
    "formats",
    "submit_via",
    "team_formation",
    "visibility",
)
# Where the course-wide block lives, for the warnings it produces.
ASSIGNMENT_DEFAULTS_KEY = "assignment_defaults"
_DEFAULTS_WHERE = f"{COURSE_CONFIG} {ASSIGNMENT_DEFAULTS_KEY}"


def parse_assignment_defaults(raw: object) -> dict:
    """The course-wide `assignment_defaults:` block, validated exactly as an assignment's
    own file is. Returns the settings it declares; anything else it says is warned about
    and dropped, and a value its reader refuses states nothing (the institution's
    answers). A course that declares none gets `{}`."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        log_err(f"  ! {_DEFAULTS_WHERE}: must be a block of settings - ignored")
        return {}
    dropped: list[str] = []
    raw = refuse_renamed(raw, _DEFAULTS_WHERE, dropped)
    values = read_settings(raw, COURSE_DEFAULT_KEYS, _DEFAULTS_WHERE, dropped)
    for line in dropped:
        log_err(line)
    return {key: value for key, value in values.items() if value is not None}


# ------------------------------------------------------------------ the cascade

# The settings that say how ONE semester runs an assignment, as opposed to what the
# assignment is (the template's). Each resolves through the four layers.
RUN_KEYS = (
    "team_formation",
    "max_team_size",
    "late_window_days",
    "late_penalty_per_day",
    "visibility",
    "submit_url",
)
LATE_PAIR = ("late_window_days", "late_penalty_per_day")
SOURCES = ("assignment", "semester", "course", "institution")
ASSIGNMENTS_DEFAULTS = "defaults"
ASSIGNMENTS_BLOCKS = "assignments"

Layer = tuple[str, Mapping]


@cache
def course_defaults(course_org: str) -> dict:
    """A course's `assignment_defaults:` block, validated, read ONCE per course per process.

    An absent `dsl-course.yml` (a 404) declares nothing. Any OTHER failure to read it - no
    permission, rate limit, malformed YAML - RAISES and is not cached: a run that cannot
    see the course's defaults must stop rather than hand out and grade on the
    institution's. tests/conftest.py clears it."""
    if not course_org:
        return {}
    return parse_assignment_defaults(org_meta(course_org).get(ASSIGNMENT_DEFAULTS_KEY))


# Keys an `assignments.yml` block may state that are not cascaded run settings: the
# semester-side repo name belongs to one assignment only (two sharing it would share every
# repo, snapshot and sheet), so it is refused under `defaults:`.
ASSIGNMENT_ONLY_KEYS = ("semester_dest_repo",)
INSTANCE_KEYS = RUN_KEYS + ASSIGNMENT_ONLY_KEYS
_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Instance:
    """One semester's `assignments.yml`, read: its `defaults:` block and each
    `assignments.<slug>` block (run settings through their readers), each assignment's
    `semester_dest_repo`, and every line the parse refused as a `ConfigFault`.
    `unparseable` = the file is not YAML, or not a mapping: nothing in it is read."""

    defaults: dict = field(default_factory=dict)
    blocks: dict[str, dict] = field(default_factory=dict)
    dests: dict[str, str] = field(default_factory=dict)
    faults: tuple[ConfigFault, ...] = ()
    # `{slug: line of its block}`, for a fault about the block as a whole.
    lines: dict[str, int] = field(default_factory=dict)
    unparseable: bool = False


def _fault(where: str, what: str, lineno: int | None, field_name: str = "", **kw):
    return ConfigFault(
        where, what, file=ASSIGNMENTS_FILE, field=field_name, lineno=lineno, **kw
    )


def parse_instance(text: str | None) -> Instance:
    """`assignments.yml` as an `Instance`. Pure: text in, the read file out. A value its
    reader refuses states nothing (the next layer answers) and is a fault; so is an unknown
    key, a `semester_dest_repo` under `defaults:` and a block that is not a mapping."""
    if not text or not text.strip():
        return Instance()
    try:
        data = load_yaml_lines(text)
    except yaml.YAMLError as exc:
        return Instance(
            faults=(
                _fault(
                    ASSIGNMENTS_FILE,
                    "this file is not valid YAML, so none of it is read and nothing is "
                    "handed out or marked from it until it parses",
                    yaml_mark_line(exc),
                    ASSIGNMENTS_FILE,
                    fix_text="fix the YAML on the line above",
                ),
            ),
            unparseable=True,
        )
    if data is None:
        return Instance()
    if not isinstance(data, dict):
        return Instance(
            faults=(
                _fault(
                    ASSIGNMENTS_FILE,
                    f"this file parses as {type(data).__name__}, not a mapping of "
                    f"`{ASSIGNMENTS_DEFAULTS}:` / `{ASSIGNMENTS_BLOCKS}:`, so none of it "
                    f"is read",
                    None,
                    ASSIGNMENTS_FILE,
                ),
            ),
            unparseable=True,
        )
    top = take_lines(data)
    faults: list[ConfigFault] = []

    def block(raw: object, where: str, allowed: tuple[str, ...], at: int | None):
        if raw is None:
            return {}, {}
        if not isinstance(raw, dict):
            faults.append(_fault(where, "must be a block of settings - ignored", at))
            return {}, {}
        lines = take_lines(raw)
        dropped: list[str] = []
        values = read_settings(
            {k: v for k, v in raw.items() if k not in ASSIGNMENT_ONLY_KEYS},
            allowed,
            f"{ASSIGNMENTS_FILE} {where}",
            dropped,
        )
        for line in dropped:
            name = getattr(line, "field", "")
            faults.append(
                _fault(
                    where, getattr(line, "what", str(line)), line_of(lines, name), name
                )
            )
        extra = {k: v for k, v in raw.items() if k in ASSIGNMENT_ONLY_KEYS}
        return values, {"lines": lines, **extra}

    defaults, extra = block(
        data.get(ASSIGNMENTS_DEFAULTS),
        ASSIGNMENTS_DEFAULTS,
        RUN_KEYS,
        top.get(ASSIGNMENTS_DEFAULTS),
    )
    for key in ASSIGNMENT_ONLY_KEYS:
        if key in extra:
            faults.append(
                _fault(
                    ASSIGNMENTS_DEFAULTS,
                    f"`{key}:` names one assignment's repos, so it is only read under "
                    f"`{ASSIGNMENTS_BLOCKS}.<key>` - ignored",
                    line_of(extra["lines"], key),
                    key,
                )
            )
    raw_blocks = data.get(ASSIGNMENTS_BLOCKS)
    if raw_blocks is not None and not isinstance(raw_blocks, dict):
        faults.append(
            _fault(
                ASSIGNMENTS_BLOCKS,
                "must be a mapping of schedule key -> settings - ignored",
                top.get(ASSIGNMENTS_BLOCKS),
            )
        )
        raw_blocks = None
    block_lines = take_lines(raw_blocks) if isinstance(raw_blocks, dict) else {}
    for key in data:
        if key not in (ASSIGNMENTS_DEFAULTS, ASSIGNMENTS_BLOCKS):
            faults.append(
                _fault(
                    str(key),
                    f"`{key}:` is not a block the toolkit reads (only "
                    f"`{ASSIGNMENTS_DEFAULTS}:` and `{ASSIGNMENTS_BLOCKS}:`) - ignored",
                    top.get(str(key)),
                    str(key),
                )
            )
    blocks: dict[str, dict] = {}
    dests: dict[str, str] = {}
    for slug, raw in (raw_blocks or {}).items():
        where = f"{ASSIGNMENTS_BLOCKS}.{slug}"
        values, extra = block(raw, where, RUN_KEYS, block_lines.get(str(slug)))
        blocks[str(slug)] = values
        dest = str(extra.get("semester_dest_repo") or "").strip()
        if dest and _REPO_NAME.match(dest):
            dests[str(slug)] = dest
        elif "semester_dest_repo" in extra and extra["semester_dest_repo"] is not None:
            faults.append(
                _fault(
                    where,
                    f"`semester_dest_repo: {extra['semester_dest_repo']}` is not a repo "
                    f"name (letters, digits, `.`, `_`, `-`) - the schedule key names "
                    f"the repos instead",
                    line_of(extra["lines"], "semester_dest_repo"),
                    "semester_dest_repo",
                )
            )
    return Instance(
        defaults=defaults,
        blocks=blocks,
        dests=dests,
        faults=tuple(faults),
        lines={str(k): v for k, v in block_lines.items()},
    )


@cache
def _assignments_text(semester_org: str) -> str | None:
    """`semester-config/assignments.yml`, or None when the semester has none (a 404).
    Any other read failure raises (`get_file_content`)."""
    return get_file_content(semester_org, CONFIG_REPO, ASSIGNMENTS_FILE)


def instance(semester_org: str) -> Instance:
    """The semester's `assignments.yml`, read and parsed; empty for no semester or no
    file. A read that fails other than with a 404 raises."""
    return (
        parse_instance(_assignments_text(semester_org)) if semester_org else Instance()
    )


@cache
def semester_blocks(semester_org: str) -> tuple[dict, dict[str, dict]]:
    """`(defaults, {slug: block})` out of the semester's `assignments.yml`, each value
    through its reader; `({}, {})` when the file is absent (a 404). A read that fails for
    any other reason RAISES and is not cached, and a file that is not YAML raises
    `Unusable` - the same fail-closed rule as `course_defaults`: nothing hands out or
    grades on defaults the semester did not choose. `schedule.load` carries the fault.
    tests/conftest.py clears it."""
    read = instance(semester_org)
    if read.unparseable:
        raise Unusable(
            f"{semester_org}/{CONFIG_REPO}/{ASSIGNMENTS_FILE}: {read.faults[0].what}"
        )
    for fault in read.faults:
        log_err(f"  ! {ASSIGNMENTS_FILE} {fault.where}: {fault.what}")
    return read.defaults, read.blocks


def institution_defaults() -> dict:
    """The policy's defaults for the run keys (and the starter formats)."""
    ours = policy.defaults()
    out = {key: ours[key] for key in RUN_KEYS if key in ours}
    out["formats"] = tuple(ours["formats"])
    return out


def layers(course_org: str, semester_org: str = "", slug: str = "") -> list[Layer]:
    """The layers for one assignment, nearest first."""
    defaults, blocks = semester_blocks(semester_org)
    return [
        ("assignment", _usable(blocks.get(slug, {}) if slug else {})),
        ("semester", _usable(defaults)),
        ("course", _usable(course_defaults(course_org))),
        ("institution", institution_defaults()),
    ]


def _usable(block: Mapping) -> dict:
    """A block without the values its readers refused (None, for every run key): a value
    nobody can use states nothing, and the next layer answers."""
    return {key: value for key, value in block.items() if value is not None}


def _states(block: Mapping, key: str) -> bool:
    """Whether a layer answers `key`. A late key is answered by a layer that states
    EITHER half of the pair; any other key by a value that is not blank (a reader's
    None is a value it refused, and the next layer answers)."""
    if key in LATE_PAIR:
        return any(k in block for k in LATE_PAIR)
    return block.get(key) not in (None, "")


def resolve(key: str, stack: Sequence[Layer]) -> tuple[object, str]:
    """`(value, source)` for `key`: the nearest layer that states it. A layer stating
    one half of the late pair answers the other half with None (no such rule)."""
    for source, block in stack:
        if _states(block, key):
            return block.get(key), source
    return None, "institution"


def _course_of(semester_org: str, course_org: str) -> str:
    if course_org or not semester_org:
        return course_org
    return course_org_for_semester(semester_org)


def effective(
    semester_org: str, slug: str, key: str, *, course_org: str = ""
) -> tuple[object, str]:
    """The effective value of run setting `key` for assignment `slug` of `semester_org`,
    and the layer it came from: `assignment`, `semester`, `course` or `institution`."""
    course_org = _course_of(semester_org, course_org)
    return resolve(key, layers(course_org, semester_org, slug))


def effective_all(
    semester_org: str, slug: str, *, course_org: str = ""
) -> dict[str, tuple[object, str]]:
    """`effective` for every run key, off one set of layers."""
    course_org = _course_of(semester_org, course_org)
    stack = layers(course_org, semester_org, slug)
    return {key: resolve(key, stack) for key in RUN_KEYS}
