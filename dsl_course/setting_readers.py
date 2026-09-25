"""setting_readers - how every assignment setting's value is read: one reader per key,
shared by an assignment's `grading_config.yml`, the course's `assignment_defaults:`, the
semester's `assignments.yml` and the institution's `policy.yml` defaults, so one value is
validated one way whichever file it is written in.

A reader returns the usable value, or records a `Dropped` line and returns what the
setting falls back to: None for a run setting, so the next layer of the cascade
(`settings`) answers.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Self
from urllib.parse import urlsplit

from .course import (
    ASSIGNMENT_TYPES,
    FORMATS,
    NO_STARTER,
    SETTING_PLACEHOLDER,
    SUBMIT_VIA,
    TEAM_FORMATIONS,
    VISIBILITIES,
    canonical_submit_via,
)
from .faults import NOT_MIGRATED, not_migrated_text


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
    default: str | None,
    where: str,
    dropped: list[str],
) -> str | None:
    """A closed vocabulary, or `default` with a warning. Never the raw value: an
    unrecognised `submit_via` would silently turn late arithmetic off for a semester.

    A RUN setting passes `default=None`: a refused value states nothing, and the next
    layer of the cascade answers - a typo in one layer must not override the layer below
    under the typing layer's name."""
    text = str(value or "").strip().lower()
    if text in allowed:
        return text
    using = f"using `{default}`" if default is not None else "ignored"
    dropped.append(
        Dropped(
            where,
            field,
            f"`{field}: {value}` is not one of {'/'.join(allowed)} - {using}",
            allowed,
        )
    )
    return default


def read_formats(
    value: object, where: str, dropped: list[str]
) -> tuple[str, ...] | None:
    """`formats:` - the starter formats, a YAML list (or, as the New assignment box types
    it, a comma-separated string). The FIRST is the runnable one: the completion check and
    the autograder run it. `none` stands alone and reads as `()`. An unusable value - one
    naming nothing, an unknown format, `none` beside another - is None with a warning: it
    states nothing, and the setting's fallback applies."""
    items = value if isinstance(value, list) else str(value or "").split(",")
    named = tuple(
        dict.fromkeys(str(t).strip().lower() for t in items if str(t).strip())
    )
    if (
        named
        and all(t in FORMATS for t in named)
        and (NO_STARTER not in named or len(named) == 1)
    ):
        return () if named == (NO_STARTER,) else named
    dropped.append(
        Dropped(
            where,
            "formats",
            f"`formats: {value}` is not a list of {'/'.join(FORMATS)} - ignored",
            FORMATS,
        )
    )
    return None


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


# One reader per key, so the per-assignment file, the course-wide defaults block, the
# semester's `assignments.yml` and the institution's policy validate the same value the same way and cannot drift into two vocabularies.
READERS = {
    "title": lambda v, w, d: str(v or "").strip(),
    "type": lambda v, w, d: _one_of(v, ASSIGNMENT_TYPES, "type", "individual", w, d),
    "team_formation": lambda v, w, d: _one_of(
        v, TEAM_FORMATIONS, "team_formation", None, w, d
    ),
    "max_team_size": _team_cap,
    # `canonical_submit_via` first, so the legacy `github` spelling reads as
    # `assignment_repo` and never earns a Dropped warning; `shared` (renamed before it
    # ever shipped) has no alias and is Dropped like any other unrecognised word.
    "submit_via": lambda v, w, d: _one_of(
        canonical_submit_via(v), SUBMIT_VIA, "submit_via", "assignment_repo", w, d
    ),
    "visibility": lambda v, w, d: _one_of(v, VISIBILITIES, "visibility", None, w, d),
    "submit_url": _submit_url,
    "formats": read_formats,
    "questions": _questions,
    "late_window_days": _whole_days,
    "late_penalty_per_day": _penalty,
    "autograde": lambda v, w, d: _boolean(v, "autograde", w, d),
    "completion_check": lambda v, w, d: _boolean(v, "completion_check", w, d),
    "tests": lambda v, w, d: str(v or "tests").strip() or "tests",
    "grader_pdf": lambda v, w, d: _boolean(v, "grader_pdf", w, d),
}
SPEC_KEYS = tuple(READERS)


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
        out[name] = READERS[name](value, where, dropped)
    return out
