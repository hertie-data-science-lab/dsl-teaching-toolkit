"""settings - every assignment and semester default: how its value is read, and where
its effective value comes from.

Every value is read by its key's reader (`setting_readers`, and `_SEMESTER_READERS` for
the three semester-wide keys).

The cascade (decision 0039): a key of the fixed `defaults:` set resolves nearest-first,
per key never per block, through four layers -

    entry        semester-config/schedule.yml   assignments.<key>.<key> (inline, 0038)
    semester     semester-config/semester.yml   defaults.<key>
    course       .github/course.yml             defaults.<key>
    institution  institution.yml                defaults.<key>

and `effective` says which layer answered. A RUN setting (`RUN_KEYS`: how teams form, the
team cap, the late pair, the repo visibility, the external submit link, whether returned
marks show each question) has all four; a SEMESTER-WIDE one (`SEMESTER_KEYS`: timezone,
archive grace, the default materials repo) has no entry layer. `team_formation` has no
institution layer (decision 0039 rule 2). The late pair is ONE rule: whichever layer
states either half states both, the other half absent meaning none (`_late_pair`). The
files spell `visibility` as `assignment_repo_visibility` (`FILE_NAMES`). A template's
`grading_config.yml` states no run setting (a run key there is RUN_SETTING_IN_TEMPLATE).

This is the ONE module that reads a `defaults:` block, the one parser of the settings
written inline on a schedule entry (`inline_settings`), and of a 0.10 `assignments.yml`
(`parse_legacy_assignments`, for the 0.11.0 migration only).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from . import legacy, policy, run
from .config import read_semester
from .course import (
    CONFIG_REPO,
    COURSE_CONFIG,
    DEFAULTS_BLOCK,
    SCHEDULE_FILE,
    SELF_SELECT,
    SEMESTER_FILE,
)
from .discovery import course_org_for_semester, org_meta
from .faults import ConfigFault, Unusable
from .gh_contents import (
    line_of,
    load_yaml_lines,
    take_lines,
    yaml_mark_line,
)
from .legacy import OLD_ASSIGNMENTS_FILE
from .log import log_err
from .setting_readers import Dropped, read_settings, whole_days

# The settings that say how ONE semester runs an assignment, as opposed to what the
# assignment is (the template's). Each resolves through the four layers.
RUN_KEYS = (
    "team_formation",
    "max_team_size",
    "late_window_days",
    "late_penalty_per_day",
    "visibility",
    "submit_url",
    # Return marks shows the marks and feedback per question (`gradebook.student_view`).
    "marks_per_question",
)
LATE_PAIR = ("late_window_days", "late_penalty_per_day")
# The defaults that hold for a whole semester: no entry states them.
SEMESTER_KEYS = ("timezone", "archive_grace_days", "materials_repo")
# A setting as the files spell it: `visibility` says whose the repos are, so a file writes
# `assignment_repo_visibility` (decision 0038 rule 9, 0039 rule 2).
FILE_NAMES = {"visibility": "assignment_repo_visibility"}
INLINE_NAMES = FILE_NAMES
INLINE_KEYS = tuple(INLINE_NAMES.get(key, key) for key in RUN_KEYS)
# What a course's or a semester's `defaults:` block may state, by setting: the fixed key
# set (`policy.DEFAULT_KEYS`) and `team_formation`. The submit link is one assignment's.
DEFAULT_KEYS = (
    "timezone",
    "late_window_days",
    "late_penalty_per_day",
    "max_team_size",
    "visibility",
    "archive_grace_days",
    "materials_repo",
    "marks_per_question",
    "team_formation",
)
# The same, as the files spell them, in the order the seeded skeletons write them.
DEFAULT_FILE_KEYS = tuple(FILE_NAMES.get(key, key) for key in DEFAULT_KEYS)
# `submit_via` answers New assignment's box of that name when it is left at
# `course.COURSE_DEFAULT_CHOICE` (`scaffold.resolve_answers`); a course may still write it
# in `defaults:` (not in the fixed set: kept pending the maintainer's ruling).
COURSE_ASKED_KEYS = ("submit_via",)
# `team_formation` when no layer states it. Decision 0039 takes it out of the institution
# file; until the maintainer rules on what "absent everywhere" means for a group template,
# the engine keeps the value it has always fallen back to.
TEAM_FORMATION_FALLBACK = SELF_SELECT

_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _timezone(value: object, where: str, dropped: list[str]) -> str | None:
    name = str(value or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        dropped.append(
            Dropped(
                where, "timezone", f"`timezone: {value}` is not a known zone - ignored"
            )
        )
        return None
    return name


def _grace_days(value: object, where: str, dropped: list[str]) -> int | None:
    if (days := whole_days(value)) is None:
        dropped.append(
            Dropped(
                where,
                "archive_grace_days",
                f"`archive_grace_days: {value}` is not a whole number of days - ignored",
            )
        )
    return days


def _materials_repo(value: object, where: str, dropped: list[str]) -> str | None:
    name = str(value or "").strip()
    if _REPO_NAME.match(name):
        return name
    dropped.append(
        Dropped(
            where,
            "materials_repo",
            f"`materials_repo: {value}` is not a repo name (letters, digits, `.`, `_`, "
            f"`-`) - ignored",
        )
    )
    return None


_SEMESTER_READERS = {
    "timezone": _timezone,
    "archive_grace_days": _grace_days,
    "materials_repo": _materials_repo,
}


def read_defaults(
    raw: Mapping, where: str, dropped: list, allowed: tuple[str, ...] = DEFAULT_KEYS
) -> dict:
    """A `defaults:` block, keyed by setting (`visibility`, not its file spelling), each
    value through its reader; a value its reader refuses is None (the next layer answers)
    and an unknown key is ignored, each recorded in `dropped`."""
    spelt = {name: key for key, name in FILE_NAMES.items()}
    out: dict = {}
    for name, value in raw.items():
        key = spelt.get(str(name), str(name))
        if str(name) in FILE_NAMES or key not in allowed:
            dropped.append(
                Dropped(
                    where,
                    str(name),
                    f"`{name}:` is not a setting the toolkit reads - ignored",
                )
            )
            continue
        reader = _SEMESTER_READERS.get(key)
        if reader is None:
            out.update(read_settings({key: value}, (key,), where, dropped))
        else:
            out[key] = reader(value, where, dropped)
    return out


def inline_settings(entry: Mapping, where: str, dropped: list) -> dict:
    """The run settings written on one schedule.yml assignment entry, each through its
    reader, keyed by setting (`visibility`, not its inline spelling). A value its reader
    refuses is None (the next layer answers) and is recorded in `dropped`."""
    spelt = {inline: key for key, inline in INLINE_NAMES.items()}
    raw = {
        spelt.get(str(k), str(k)): v for k, v in entry.items() if str(k) in INLINE_KEYS
    }
    return read_settings(raw, RUN_KEYS, where, dropped)


def inline_blocks(assignments: object) -> dict[str, dict]:
    """`{schedule key: its inline run settings}` out of a schedule.yml `assignments:`
    block as YAML gives it; values a reader refuses are left out (the schedule's own parse
    reports them)."""
    if not isinstance(assignments, dict):
        return {}
    return {
        str(key): _usable(inline_settings(entry, f"assignments.{key}", []))
        for key, entry in assignments.items()
        if isinstance(entry, dict)
    }


SOURCES = ("entry", "semester", "course", "institution")

Layer = tuple[str, Mapping]

_COURSE_WHERE = f"{COURSE_CONFIG} {DEFAULTS_BLOCK}"


def parse_course_defaults(raw: object) -> dict:
    """A course's `defaults:` block. Returns the settings it declares; anything else it
    says is warned about and dropped, and a value its reader refuses states nothing (the
    institution's answers). A course that declares none gets `{}`."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        log_err(f"  ! {_COURSE_WHERE}: must be a block of settings - ignored")
        return {}
    dropped: list[str] = []
    values = read_defaults(
        raw, _COURSE_WHERE, dropped, (*DEFAULT_KEYS, *COURSE_ASKED_KEYS)
    )
    for line in dropped:
        log_err(line)
    return {key: value for key, value in values.items() if value is not None}


@run.per_run(lambda course_org: run.scope(course_org, ".github", COURSE_CONFIG))
def course_defaults(course_org: str) -> dict:
    """A course's `defaults:` block, validated, read ONCE per course per process.

    An absent `course.yml` (a 404) declares nothing. Any OTHER failure to read it - no
    permission, rate limit, malformed YAML - RAISES and is not cached: a run that cannot
    see the course's defaults must stop rather than hand out and grade on the
    institution's. Held by the run (`run.per_run`)."""
    if not course_org:
        return {}
    return parse_course_defaults(org_meta(course_org).get(DEFAULTS_BLOCK))


@dataclass(frozen=True)
class Instance:
    """One semester's `defaults:` as read (from `semester.yml`, or a 0.10
    `assignments.yml` by `parse_legacy_assignments`), every line the parse refused as a
    `ConfigFault`, and the semester-wide values the semester's plan is read with
    (`SEMESTER_KEYS`, effective: semester > course > institution). `blocks` / `dests` /
    `lines` are a 0.10 `assignments.yml`'s per-key blocks, for the migration only.
    `unparseable` = the file is not YAML, or not a mapping: nothing in it is read."""

    defaults: dict = field(default_factory=dict)
    blocks: dict[str, dict] = field(default_factory=dict)
    dests: dict[str, str] = field(default_factory=dict)
    faults: tuple[ConfigFault, ...] = ()
    # `{slug: line of its block}`, for a fault about the block as a whole.
    lines: dict[str, int] = field(default_factory=dict)
    unparseable: bool = False
    timezone: str = field(default_factory=lambda: policy.defaults()["timezone"])
    archive_grace_days: int = field(
        default_factory=lambda: policy.defaults()["archive_grace_days"]
    )
    materials_repo: str = field(
        default_factory=lambda: policy.defaults()["materials_repo"]
    )


def _fault(where: str, what: str, lineno: int | None, field_name: str = "", **kw):
    return ConfigFault(
        where, what, file=SEMESTER_FILE, field=field_name, lineno=lineno, **kw
    )


# A 0.10 `assignments.yml`'s two blocks; a per-key block could name its repos.
ASSIGNMENTS_DEFAULTS = "defaults"
ASSIGNMENTS_BLOCKS = "assignments"
ASSIGNMENT_ONLY_KEYS = ("semester_dest_repo",)


def _old_fault(where: str, what: str, lineno: int | None, field_name: str = "", **kw):
    return ConfigFault(
        where, what, file=OLD_ASSIGNMENTS_FILE, field=field_name, lineno=lineno, **kw
    )


def parse_instance(text: str | None, course: Mapping | None = None) -> Instance:
    """`semester.yml`'s `defaults:` as an `Instance`. Pure: text in, the read block out;
    `course` is the course's defaults when the caller holds them (the semester-wide values
    then cascade through it). A value its reader refuses states nothing (the next layer
    answers) and is a fault; so is an unknown key and a block that is not a mapping. The
    file's other blocks are read by their own readers."""
    try:
        data = load_yaml_lines(text) if text and text.strip() else None
    except yaml.YAMLError as exc:
        return Instance(
            faults=(
                _fault(
                    "",
                    "this file is not valid YAML, so none of it is read and nothing is "
                    "handed out or marked on its defaults until it parses",
                    yaml_mark_line(exc),
                    SEMESTER_FILE,
                    fix_text="fix the YAML on the line above",
                ),
            ),
            unparseable=True,
        )
    if data is not None and not isinstance(data, dict):
        return Instance(
            faults=(
                _fault(
                    "",
                    f"this file parses as {type(data).__name__}, not a mapping of "
                    f"`people:` / `{DEFAULTS_BLOCK}:` / `student_site:`, so none of it "
                    f"is read",
                    None,
                    SEMESTER_FILE,
                ),
            ),
            unparseable=True,
        )
    data = data or {}
    top = take_lines(data)
    faults: list[ConfigFault] = []
    raw = data.get(DEFAULTS_BLOCK)
    values: dict = {}
    if raw is not None and not isinstance(raw, dict):
        faults.append(
            _fault(
                DEFAULTS_BLOCK,
                "must be a block of settings - ignored",
                top.get(DEFAULTS_BLOCK),
            )
        )
    elif raw:
        lines = take_lines(raw)
        dropped: list = []
        values = read_defaults(raw, f"{SEMESTER_FILE} {DEFAULTS_BLOCK}", dropped)
        for line in dropped:
            name = getattr(line, "field", "")
            faults.append(
                _fault(
                    DEFAULTS_BLOCK,
                    getattr(line, "what", str(line)),
                    line_of(lines, name),
                    name,
                )
            )
    stack = [("semester", _usable(values)), ("course", _usable(course or {}))]
    stack.append(("institution", institution_defaults()))
    wide = {key: resolve(key, stack)[0] for key in SEMESTER_KEYS}
    return Instance(defaults=values, faults=tuple(faults), **wide)


def parse_legacy_assignments(text: str | None) -> Instance:
    """A 0.10 `assignments.yml` as an `Instance`, for the 0.11.0 migration only (its
    `defaults:` fold into semester.yml, its per-key blocks onto the schedule entries). Pure:
    text in, the read file out. A value its reader refuses states nothing and is a fault;
    so is an unknown key, a `semester_dest_repo` under `defaults:` and a block that is not
    a mapping. Its `defaults:` are read by the 0.10 key set (`visibility`, no
    semester-wide keys)."""
    if not text or not text.strip():
        return Instance()
    try:
        data = load_yaml_lines(text)
    except yaml.YAMLError as exc:
        return Instance(
            faults=(
                _old_fault(
                    "",
                    "this file is not valid YAML, so none of it is read and nothing is "
                    "handed out or marked from it until it parses",
                    yaml_mark_line(exc),
                    OLD_ASSIGNMENTS_FILE,
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
                _old_fault(
                    "",
                    f"this file parses as {type(data).__name__}, not a mapping of "
                    f"`{ASSIGNMENTS_DEFAULTS}:` / `{ASSIGNMENTS_BLOCKS}:`, so none of it "
                    f"is read",
                    None,
                    OLD_ASSIGNMENTS_FILE,
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
            faults.append(
                _old_fault(where, "must be a block of settings - ignored", at)
            )
            return {}, {}
        lines = take_lines(raw)
        dropped: list[str] = []
        values = read_settings(
            {k: v for k, v in raw.items() if k not in ASSIGNMENT_ONLY_KEYS},
            allowed,
            f"{OLD_ASSIGNMENTS_FILE} {where}",
            dropped,
        )
        for line in dropped:
            name = getattr(line, "field", "")
            faults.append(
                _old_fault(
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
                _old_fault(
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
            _old_fault(
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
                _old_fault(
                    str(key),
                    f"`{key}:` is not a block the toolkit reads (only "
                    f"`{ASSIGNMENTS_DEFAULTS}:` and `{ASSIGNMENTS_BLOCKS}:`) - ignored",
                    top.get(str(key)),
                    str(key),
                )
            )
    if raw_blocks:
        row = legacy.flag(ASSIGNMENTS_BLOCKS, legacy.ASSIGNMENTS_YML)
        faults.append(
            _old_fault(
                ASSIGNMENTS_BLOCKS,
                f"moved: each assignment's settings are written on its entry in "
                f"{SCHEDULE_FILE} (`{row.new}`) - ignored here, so {row.cost}",
                top.get(ASSIGNMENTS_BLOCKS),
                ASSIGNMENTS_BLOCKS,
                code=row.fault_code,
            )
        )
    # Read all the same, for the 0.11.0 migration (`migrate.schedule_shape`), which moves
    # each block onto its entry; nothing else reads them.
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
                _old_fault(
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


@run.per_run(lambda semester_org: run.scope(semester_org, CONFIG_REPO, SEMESTER_FILE))
def _semester_text(semester_org: str) -> str | None:
    """`semester-config/semester.yml`, or None when the semester has none (a 404). Any
    other read failure raises (`get_file_content`)."""
    return read_semester(semester_org).semester.text


def instance(semester_org: str) -> Instance:
    """The semester's `semester.yml` defaults, read and parsed, its semester-wide values
    cascaded through its course's; empty for no semester or no file. A read that fails
    other than with a 404 raises."""
    if not semester_org:
        return Instance()
    course = course_defaults(course_org_for_semester(semester_org))
    return parse_instance(_semester_text(semester_org), course)


@run.per_run(lambda semester_org: run.scope(semester_org, CONFIG_REPO))
def semester_defaults(semester_org: str) -> dict:
    """The semester's `semester.yml` `defaults:`, each value through its reader; `{}`
    when the file is not there (a 404). The entry layer - the settings written on each
    schedule.yml entry - is never read here: the caller hands it over from the parsed
    `Schedule` (`AssignmentEntry.settings`), so schedule.yml is read once. A read that
    fails for any other reason RAISES and is not cached, and a `semester.yml` that is not
    YAML raises `Unusable` - the same fail-closed rule as `course_defaults`: nothing hands
    out or grades on defaults the semester did not choose. `schedule.load` carries the
    fault. Held by the run (`run.per_run`)."""
    if not semester_org:
        return {}
    read = parse_instance(_semester_text(semester_org))
    if read.unparseable:
        raise Unusable(
            f"{semester_org}/{CONFIG_REPO}/{SEMESTER_FILE}: {read.faults[0].what}"
        )
    for fault in read.faults:
        log_err(f"  ! {SEMESTER_FILE} {fault.where}: {fault.what}")
    return read.defaults


def institution_defaults() -> dict:
    """The institution's defaults, keyed by setting, with `team_formation`'s fallback
    (`TEAM_FORMATION_FALLBACK`): the bottom layer of every stack."""
    spelt = {name: key for key, name in FILE_NAMES.items()}
    out = {spelt.get(k, k): v for k, v in policy.defaults().items()}
    out["team_formation"] = TEAM_FORMATION_FALLBACK
    return out


def layers(
    course_org: str, semester_org: str = "", inline: Mapping | None = None
) -> list[Layer]:
    """The layers for one assignment, nearest first. `inline` is its schedule.yml entry's
    own run settings (`AssignmentEntry.settings`), from the parsed plan."""
    return [
        ("entry", _usable(inline or {})),
        ("semester", _usable(semester_defaults(semester_org))),
        ("course", _usable(course_defaults(course_org))),
        ("institution", institution_defaults()),
    ]


def instance_layers(read: Instance, inline: Mapping) -> list[Layer]:
    """The two layers a semester states for one assignment, off files already read: its
    entry's inline settings and `semester.yml` `defaults:` - what a parse can resolve
    with no course and no institution in hand."""
    return [
        ("entry", _usable(inline)),
        ("semester", _usable(read.defaults)),
    ]


def _usable(block: Mapping) -> dict:
    """A block without the values its readers refused (None, for every key): a value
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
    semester_org: str,
    key: str,
    *,
    inline: Mapping | None = None,
    course_org: str = "",
) -> tuple[object, str]:
    """The effective value of setting `key` for an assignment of `semester_org` whose
    schedule.yml entry states `inline`, and the layer it came from: `entry`, `semester`,
    `course` or `institution`."""
    course_org = _course_of(semester_org, course_org)
    return resolve(key, layers(course_org, semester_org, inline))


def effective_all(
    semester_org: str, *, inline: Mapping | None = None, course_org: str = ""
) -> dict[str, tuple[object, str]]:
    """`effective` for every run key, off one set of layers."""
    course_org = _course_of(semester_org, course_org)
    stack = layers(course_org, semester_org, inline)
    return {key: resolve(key, stack) for key in RUN_KEYS}


def effective_defaults(
    course_org: str, semester_org: str = ""
) -> dict[str, dict[str, object]]:
    """Every `defaults:` key's effective value at the semester level (or the course level,
    with no semester), as the files spell the keys: `{key: {"value", "source"}}` - what
    status.json carries and the console shows as each input's placeholder."""
    stack = layers(course_org, semester_org)[1:]
    out = {}
    for key in DEFAULT_KEYS:
        value, source = resolve(key, stack)
        out[FILE_NAMES.get(key, key)] = {"value": value, "source": source}
    return out


# What each key's comment says in a seeded skeleton when no layer above states it.
_SKELETON_HINTS = {"team_formation": "self_select | assigned"}


def defaults_skeleton(inherited: Sequence[Layer], stated: Mapping | None = None) -> str:
    """A `defaults:` block with every key present (decision 0039 rule 2): the keys
    `stated` (this level's own values, keyed by setting) live, every other key commented
    with the value it inherits from `inherited` (the layers below, nearest first) and that
    layer's name. `team_formation` with no layer stating it names its choices instead."""
    stated = stated or {}
    lines = [
        (
            "defaults:   # every key present; uncomment to override. The comment shows "
            "the inherited value and its source."
        )
    ]
    for key in DEFAULT_KEYS:
        name = FILE_NAMES.get(key, key)
        if key in stated and stated[key] is not None:
            lines.append(f"  {name}: {_yaml_scalar(stated[key])}")
            continue
        if key in LATE_PAIR and any(stated.get(k) is not None for k in LATE_PAIR):
            lines.append(
                f"  # {name}:".ljust(44) + "(none: the late pair is set above)"
            )
            continue
        value, source = resolve(key, inherited)
        if key == "team_formation" and source == "institution":
            hint = _SKELETON_HINTS[key]
            lines.append(f"  # {name}:".ljust(44) + f"({hint}; not set above)")
            continue
        lines.append(f"  # {name}: {_yaml_scalar(value)}".ljust(44) + f"({source})")
    return "\n".join(lines) + "\n"


def _yaml_scalar(value: object) -> str:
    """One value as a YAML flow scalar on one line."""
    return (
        yaml.safe_dump(value, default_flow_style=True)
        .strip()
        .removesuffix("...")
        .strip()
    )
