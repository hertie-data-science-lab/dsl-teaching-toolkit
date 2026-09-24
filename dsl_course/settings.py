"""settings - every assignment setting: how its value is read, and where its effective
value comes from.

The readers: one per key, shared by an assignment's `grading_config.yml`, the course's
`assignment_defaults:` and the semester's `assignments.yml`, so one value is validated one
way whichever file it is written in.

The cascade: a RUN setting (`RUN_KEYS`: how teams form, the team cap, the late pair, the
repo visibility, the external submit link) resolves nearest-first through four layers -

    assignment   semester-config/assignments.yml  assignments.<slug>.<key>
    semester     semester-config/assignments.yml  defaults.<key>
    course       .github/dsl-course.yml            assignment_defaults.<key>
    institution  policy.yml                        defaults.<key>

and `effective` says which layer answered. The late pair is ONE rule: whichever layer
states either half states both, the other half absent meaning none (`_late_pair`).
Until the template loses its run keys (they move to `assignments.yml`), a key the
template's `grading_config.yml` declares sits in the assignment layer, below the
`assignments.yml` block for the same slug. `assignments.yml` is optional: absent, both of
its layers are empty. This is the ONE module that reads `assignment_defaults`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from functools import cache
from typing import Self
from urllib.parse import urlsplit

import yaml

from . import policy
from .course import (
    ASSIGNMENT_TYPES,
    ASSIGNMENTS_FILE,
    CONFIG_REPO,
    COURSE_CONFIG,
    FORMATS,
    NO_STARTER,
    SETTING_PLACEHOLDER,
    SUBMIT_VIA,
    TEAM_FORMATIONS,
    VISIBILITIES,
    canonical_submit_via,
)
from .discovery import course_org_for_semester, org_meta
from .faults import NOT_MIGRATED, not_migrated_text
from .gh_contents import get_file_content
from .log import log_err

# ------------------------------------------------------------------ values


def as_decimal(value: object) -> Decimal | None:
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


# Every way `late_penalty_per_day` can be written wrong, and what to say about it. One
# multiplies every late mark in the semester, so none of them may pass quietly: a bare `10`
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
    rate = as_decimal(raw[:-1] if percent else raw)
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


class Dropped(str):
    """One line the parse of an assignment's definition refused, and what it was about.

    A `str`, because that is what `GradingSpec.dropped` has always been and what every
    reader of it prints, logs and greps for. The key it names and the vocabulary it would
    have accepted ride along, so the same line can also become the fault that cites the
    line to edit and says what is allowed there; a second, parallel list of records would
    be a second answer to "what did this parse refuse"."""

    field: str
    what: str
    allowed: tuple[str, ...]
    code: str

    def __new__(
        cls,
        where: str,
        field: str,
        what: str,
        allowed: tuple[str, ...] = (),
        code: str = "",
    ) -> Self:
        # `  ! <where>: ` is the run-log form, unchanged; `what` on its own is what a
        # notification says, where the file is already named above it.
        out = super().__new__(cls, f"  ! {where}: {what}")
        out.field, out.what, out.allowed, out.code = field, what, allowed, code
        return out


def _one_of(
    value: object,
    allowed: tuple[str, ...],
    field: str,
    default: str,
    where: str,
    dropped: list[str],
) -> str:
    """A closed vocabulary, or the default with a warning. Never the raw value: an
    unrecognised `submit_via` would silently turn late arithmetic off for a semester."""
    text = str(value or "").strip().lower()
    if text in allowed:
        return text
    dropped.append(
        Dropped(
            where,
            field,
            f"`{field}: {value}` is not one of {'/'.join(allowed)} - using `{default}`",
            allowed,
        )
    )
    return default


def _formats(value: object, where: str, dropped: list[str]) -> tuple[str, ...]:
    """`formats:` - the starter formats, a YAML list (or, as the New assignment box types
    it, a comma-separated string). The FIRST is the runnable one: the completion check and
    the autograder run it. `none` stands alone. An unusable value is dropped with a warning
    and reads as no starter at all."""
    items = value if isinstance(value, list) else str(value or "").split(",")
    named = tuple(
        dict.fromkeys(str(t).strip().lower() for t in items if str(t).strip())
    )
    if all(t in FORMATS for t in named) and (
        NO_STARTER not in named or len(named) == 1
    ):
        return () if named == (NO_STARTER,) else named
    dropped.append(
        Dropped(
            where,
            "formats",
            f"`formats: {value}` is not a list of {'/'.join(FORMATS)} - no starter "
            f"format is recorded",
            FORMATS,
        )
    )
    return ()


# Settings RENAMED (decision 0012), old -> new. The old key is never read: it is dropped
# as NOT_MIGRATED, which names the new one, and the setting takes its default meanwhile.
RENAMED_SETTINGS = {"format": "formats"}


def refuse_renamed(data: dict, where: str, dropped: list[str]) -> dict:
    """`data` without its old keys, each one noted in `dropped` as NOT_MIGRATED."""
    out = dict(data)
    for old, new in RENAMED_SETTINGS.items():
        if old in out:
            del out[old]
            dropped.append(
                Dropped(where, old, not_migrated_text(old, new), code=NOT_MIGRATED)
            )
    return out


def _boolean(value: object, field: str, where: str, dropped: list[str]) -> bool:
    """A YAML boolean, or one spelt as text. Anything else is false with a warning:
    `autograde: "false"` is a non-empty string, and reading it as truthy turned hidden
    tests on for an assignment that had asked for the opposite."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0", ""):
        return False
    dropped.append(
        Dropped(
            where,
            field,
            f"`{field}: {value}` is not true or false - using false",
            ("true", "false"),
        )
    )
    return False


def _questions(value: object, where: str, dropped: list[str]) -> dict[str, str] | None:
    """`questions:` as {name: maximum AS TEXT}.

    Text, because the maxima are only ever DISPLAYED - beside each blank in the sheet, and
    in its header - and a course that writes `1.5` must read back what it wrote. Anything
    that is not a mapping is dropped with a warning rather than half-read."""
    if not isinstance(value, dict):
        dropped.append(
            Dropped(
                where,
                "questions",
                "`questions:` must be a mapping of name -> points - ignored",
            )
        )
        return None
    questions = {
        str(name).strip(): ("" if points is None else str(points).strip())
        for name, points in value.items()
        if str(name).strip()
    }
    return questions or None


def _whole_days(value: object, where: str, dropped: list[str]) -> int | None:
    """`late_window_days` as a whole number of days, or None with a warning."""
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        dropped.append(
            Dropped(
                where,
                "late_window_days",
                f"`late_window_days: {value}` is not a whole number of days - ignored",
            )
        )
        return None


def _team_cap(value: object, where: str, dropped: list[str]) -> int | None:
    """`max_team_size` as a positive whole number, or None with a warning. None means
    the Join-team form falls back to the course default, so a typo costs the cap and
    nothing else."""
    try:
        cap = int(str(value).strip())
    except (TypeError, ValueError):
        cap = 0
    if cap > 0:
        return cap
    dropped.append(
        Dropped(
            where,
            "max_team_size",
            f"`max_team_size: {value}` is not a whole number of members - ignored",
        )
    )
    return None


def _penalty(value: object, where: str, dropped: list[str]) -> str | None:
    """`late_penalty_per_day` as it was typed, or None with a warning saying which way it
    is wrong.

    Checked here, once per spec, like every other malformed field: the derivation itself
    stays pure and is called per student. Refusing without saying so meant every late mark
    in that semester quietly lost its deduction while the sheet's header still advertised
    one."""
    raw = str(value or "").strip()
    if not raw:
        return None
    fault = penalty_fault(raw)
    if fault:
        dropped.append(
            Dropped(
                where,
                "late_penalty_per_day",
                f"`late_penalty_per_day: {value}` {_PENALTY_FAULTS[fault]}; no late "
                f"penalty is applied",
            )
        )
        return None
    return raw


def _submit_url(value: object, where: str, dropped: list[str]) -> str:
    """Where an EXTERNAL assignment is handed in - the address behind the site's
    `Submit on <host>` button.

    `https://` only, and FILLED IN: this is the one link on a public course site that sends
    a whole semester somewhere on the strength of one hand-typed line, and `CHANGE-ME` is the
    placeholder the scaffold seeds - a file still carrying it has had the line uncommented
    and not answered. Refused rather than raised, so the site shows the brief with no
    button."""
    text = str(value or "").strip()
    if not text:
        return ""
    if (
        text.lower().startswith("https://")
        and urlsplit(text).hostname
        and SETTING_PLACEHOLDER not in text
    ):
        return text
    dropped.append(
        Dropped(
            where,
            "submit_url",
            f"`submit_url: {value}` is not a filled-in `https://` address - the site "
            f"shows the brief with no submit button",
        )
    )
    return ""


# One reader per key, so the per-assignment file and the course-wide defaults block below
# validate the same value the same way and cannot drift into two vocabularies.
_READERS = {
    "title": lambda v, w, d: str(v or "").strip(),
    "type": lambda v, w, d: _one_of(v, ASSIGNMENT_TYPES, "type", "individual", w, d),
    "team_formation": lambda v, w, d: _one_of(
        v, TEAM_FORMATIONS, "team_formation", policy.defaults()["team_formation"], w, d
    ),
    "max_team_size": _team_cap,
    # `canonical_submit_via` first, so the legacy `github` spelling reads as
    # `assignment_repo` and never earns a Dropped warning; `shared` (renamed before it
    # ever shipped) has no alias and is Dropped like any other unrecognised word.
    "submit_via": lambda v, w, d: _one_of(
        canonical_submit_via(v), SUBMIT_VIA, "submit_via", "assignment_repo", w, d
    ),
    "visibility": lambda v, w, d: _one_of(
        v, VISIBILITIES, "visibility", policy.defaults()["visibility"], w, d
    ),
    "submit_url": _submit_url,
    "formats": _formats,
    "questions": _questions,
    "late_window_days": _whole_days,
    "late_penalty_per_day": _penalty,
    "autograde": lambda v, w, d: _boolean(v, "autograde", w, d),
    "completion_check": lambda v, w, d: _boolean(v, "completion_check", w, d),
    "tests": lambda v, w, d: str(v or "tests").strip() or "tests",
    "grader_pdf": lambda v, w, d: _boolean(v, "grader_pdf", w, d),
}
SPEC_KEYS = tuple(_READERS)
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


def read_settings(
    data: dict, allowed: tuple[str, ...], where: str, dropped: list[str]
) -> dict:
    """The keys of `data` this schema understands, each through its own reader; everything
    else recorded as an unknown key. Unknown rather than ignored, because the settings
    that used to live in schedule.yml now live here and a misfiled one has to say so."""
    out: dict = {}
    for key, value in data.items():
        name = str(key)
        if name not in allowed:
            dropped.append(
                Dropped(
                    where,
                    name,
                    f"`{name}:` is not a setting the toolkit reads - ignored",
                )
            )
            continue
        out[name] = _READERS[name](value, where, dropped)
    return out


def parse_assignment_defaults(raw: object) -> dict:
    """The course-wide `assignment_defaults:` block, validated exactly as an assignment's
    own file is. Returns the settings it declares; anything else it says is warned about
    and dropped. A course that declares none gets `{}` and every assignment keeps the
    toolkit's own defaults."""
    if raw is None:
        return {}
    dropped: list[str] = []
    if not isinstance(raw, dict):
        log_err(f"  ! {_DEFAULTS_WHERE}: must be a block of settings - ignored")
        return {}
    # `formats` here answers New assignment's box, which takes a comma-separated list of
    # starters. Read as the box reads it, and dropped when unusable, so the toolkit's own
    # answer applies rather than `none`.
    raw = refuse_renamed(raw, _DEFAULTS_WHERE, dropped)
    starters = raw.get("formats")
    values = read_settings(
        {k: v for k, v in raw.items() if k != "formats"},
        COURSE_DEFAULT_KEYS,
        _DEFAULTS_WHERE,
        dropped,
    )
    if starters is not None:
        items = starters if isinstance(starters, list) else str(starters).split(",")
        named = [str(t).strip().lower() for t in items if str(t).strip()]
        if (
            named
            and all(t in FORMATS for t in named)
            and (NO_STARTER not in named or len(named) == 1)
        ):
            values["formats"] = ",".join(dict.fromkeys(named))
        else:
            dropped.append(
                Dropped(
                    _DEFAULTS_WHERE,
                    "formats",
                    f"`formats: {starters}` is not a list of "
                    f"{'/'.join(FORMATS)} - using the toolkit's default",
                    FORMATS,
                )
            )
    for line in dropped:
        log_err(line)
    return values


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

    NEVER raises: a malformed identity file must cost the defaults, not the run.
    tests/conftest.py clears it."""
    if not course_org:
        return {}
    try:
        meta = org_meta(course_org)
    except RuntimeError as exc:
        log_err(f"  ! could not read {course_org}/.github/{COURSE_CONFIG}: {exc}")
        return {}
    return parse_assignment_defaults(meta.get(ASSIGNMENT_DEFAULTS_KEY))


@cache
def _assignments_text(semester_org: str) -> str | None:
    """`semester-config/assignments.yml`, or None when the semester has none."""
    return get_file_content(semester_org, CONFIG_REPO, ASSIGNMENTS_FILE)


@cache
def semester_blocks(semester_org: str) -> tuple[dict, dict[str, dict]]:
    """`(defaults, {slug: block})` out of the semester's `assignments.yml`, each value
    through its reader; `({}, {})` when the file is absent or unreadable. NEVER raises.
    tests/conftest.py clears it."""
    if not semester_org:
        return {}, {}
    try:
        text = _assignments_text(semester_org)
        data = yaml.safe_load(text) if text else None
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(f"  ! {ASSIGNMENTS_FILE} in {semester_org} could not be read: {exc}")
        return {}, {}
    if not isinstance(data, dict):
        return {}, {}
    dropped: list[str] = []

    def block(raw: object, where: str) -> dict:
        if not isinstance(raw, dict):
            return {}
        return read_settings(raw, RUN_KEYS, where, dropped)

    defaults = block(
        data.get(ASSIGNMENTS_DEFAULTS), f"{ASSIGNMENTS_FILE} {ASSIGNMENTS_DEFAULTS}"
    )
    raw_blocks = data.get(ASSIGNMENTS_BLOCKS)
    blocks = {
        str(slug): block(raw, f"{ASSIGNMENTS_FILE} {ASSIGNMENTS_BLOCKS}.{slug}")
        for slug, raw in (raw_blocks.items() if isinstance(raw_blocks, dict) else ())
    }
    for line in dropped:
        log_err(line)
    return defaults, blocks


def institution_defaults() -> dict:
    """The policy's defaults for the run keys (and the starter formats)."""
    ours = policy.defaults()
    out = {key: ours[key] for key in RUN_KEYS if key in ours}
    out["formats"] = ",".join(ours["formats"])
    return out


def layers(
    course_org: str,
    semester_org: str = "",
    slug: str = "",
    template: Mapping | None = None,
) -> list[Layer]:
    """The layers for one assignment, nearest first. `template` is the run settings its
    `grading_config.yml` declares (until they move to `assignments.yml`)."""
    defaults, blocks = semester_blocks(semester_org)
    return [
        ("assignment", _usable(blocks.get(slug, {}) if slug else {})),
        ("assignment", template or {}),
        ("semester", _usable(defaults)),
        ("course", _usable(course_defaults(course_org))),
        ("institution", institution_defaults()),
    ]


def _usable(block: Mapping) -> dict:
    """A defaults block without the values its readers refused (None): a default nobody
    can use states nothing, and the next layer answers. (A TEMPLATE's refused late key
    still states the pair - that file wrote a rule of its own; see `_late_pair`.)"""
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


def effective(
    semester_org: str,
    slug: str,
    key: str,
    *,
    course_org: str = "",
    template: Mapping | None = None,
) -> tuple[object, str]:
    """The effective value of run setting `key` for assignment `slug` of `semester_org`,
    and the layer it came from: `assignment`, `semester`, `course` or `institution`."""
    course_org = course_org or course_org_for_semester(semester_org)
    return resolve(key, layers(course_org, semester_org, slug, template))


def effective_all(
    semester_org: str,
    slug: str,
    *,
    course_org: str = "",
    template: Mapping | None = None,
) -> dict[str, tuple[object, str]]:
    """`effective` for every run key, off one set of layers."""
    course_org = course_org or course_org_for_semester(semester_org)
    stack = layers(course_org, semester_org, slug, template)
    return {key: resolve(key, stack) for key in RUN_KEYS}
