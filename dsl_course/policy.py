"""The institution: every default the toolkit applies when nobody said otherwise.

`institution.default.yml` beside this module is what the toolkit ships (Hertie's values);
an optional `institution.yml` at the root of the toolkit checkout overrides it. The engine
runs from a checkout of the org's `toolkit_version_ref`, so the institution file an org
gets is the one at that ref. (The module keeps its name: CLI module names are frozen.)

`defaults` is the institution layer of the defaults cascade (`settings`); `kinds` are the
schedule row kinds; `institution` is the site block, its `contacts` the escalation list
(the first is where a fault goes when nobody nearer has an address); `selection_floor`
the hard hosting limits; `licences` the open site's choices, default first. A file that
does not validate RAISES: it is maintainer-owned, and an engine running on a guess about
its own defaults would be worse than one that stops.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from . import legacy
from .course import VISIBILITIES
from .schema_check import validate
from .setting_readers import READERS

DEFAULT_PATH = Path(__file__).with_name("institution.default.yml")
OVERRIDE_PATH = Path(__file__).resolve().parents[1] / "institution.yml"

# The kinds the engine creates rows for itself, and the one an unknown kind lands on: an
# override may relabel or recolour them, never drop them.
SYSTEM_KINDS = ("assignment", "semester", "archive")
FALLBACK_KIND = "other"
# The blocks an override merges key by key; every other block it names replaces ours whole.
MERGED = ("defaults", "institution")
# The `defaults:` keys every level writes (decision 0039 rule 2), in file order.
DEFAULT_KEYS = (
    "timezone",
    "late_window_days",
    "late_penalty_per_day",
    "max_team_size",
    "assignment_repo_visibility",
    "archive_grace_days",
    "materials_repo",
    "marks_per_question",
)
# The defaults a course or a semester can also write, with the reader that reads them
# there: each must come back unchanged - a default no course could write
# (`max_team_size: -3`, `late_penalty_per_day: 100.9%`) is refused.
READ_DEFAULTS = {
    "late_window_days": "late_window_days",
    "late_penalty_per_day": "late_penalty_per_day",
    "max_team_size": "max_team_size",
    "assignment_repo_visibility": "visibility",
    "archive_grace_days": "late_window_days",
    "marks_per_question": "marks_per_question",
}

_HEX = "^#[0-9a-fA-F]{6}$"
_KEY = r"^[a-z][a-z0-9_-]*$"
# `10%` or a fraction `0.1`: the two spellings `setting_readers.penalty_fault` accepts.
_PENALTY = r"^(?:(?:100|[0-9]{1,2})(?:\.[0-9]+)?%|0?\.[0-9]+|0)$"
_WHOLE = {"type": "integer"}

SCHEMA = {
    "type": "object",
    "properties": {
        "defaults": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string"},
                "late_window_days": _WHOLE,
                "late_penalty_per_day": {"type": "string", "pattern": _PENALTY},
                "max_team_size": _WHOLE,
                "assignment_repo_visibility": {
                    "type": "string",
                    "enum": list(VISIBILITIES),
                },
                "archive_grace_days": _WHOLE,
                "materials_repo": {"type": "string", "pattern": r"^[\w.-]+$"},
                "marks_per_question": {"type": "boolean"},
            },
            "required": list(DEFAULT_KEYS),
            "additionalProperties": False,
        },
        "kinds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "pattern": _KEY},
                    "plural": {"type": "string", "pattern": _KEY},
                    "numbered": {"type": "boolean"},
                    "shown": {"type": "boolean"},
                    "label": {"type": "string"},
                    "colour": {"type": "string", "pattern": _HEX},
                    "background": {"type": "string", "pattern": _HEX},
                    "system": {"type": "boolean"},
                },
                "required": [
                    "key",
                    "plural",
                    "numbered",
                    "label",
                    "colour",
                    "background",
                    "system",
                ],
                "additionalProperties": False,
            },
        },
        "institution": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "url": {"type": "string"},
                "address": {"type": "string"},
                "dsl_org_url": {"type": "string"},
                "console_url": {"type": "string"},
                "contacts": {
                    "type": "array",
                    "items": {"type": "string", "pattern": r"^[^@\s]+@[^@\s]+$"},
                },
            },
            "required": ["name", "url", "address", "dsl_org_url", "contacts"],
            "additionalProperties": False,
        },
        # Its shape is the hosting selection's (decision 0040); none at Hertie.
        "selection_floor": {"type": "object"},
        "licences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
                "required": ["name", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["institution", "defaults", "kinds", "selection_floor", "licences"],
    "additionalProperties": False,
}


class PolicyError(ValueError):
    """The institution file(s) do not describe an institution the engine can run on."""


def _parse(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path.name} must be a mapping of blocks")
    return data


def merge(base: dict, override: dict) -> dict:
    """`override` over `base`: the `MERGED` blocks key by key, every other block whole."""
    out = dict(base)
    for key, value in override.items():
        if key in MERGED and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def _problems(policy: dict) -> list[str]:
    """What the JSON Schema subset cannot say: each default as its layer's reader reads
    it, unique kinds, the system kinds present, a non-empty licence list, a zone the tz
    database knows."""
    out = validate(policy, SCHEMA, "institution")
    if out:
        return out
    ours = policy["defaults"]
    for key, reader in READ_DEFAULTS.items():
        refused: list[str] = []
        value = ours[key]
        if READERS[reader](value, "institution", refused) != value or refused:
            out.append(f"institution.defaults.{key}: `{value}` is not a usable value")
    keys = [kind["key"] for kind in policy["kinds"]]
    if len(keys) != len(set(keys)):
        out.append("institution.kinds: each key may appear once")
    plurals = [kind["plural"] for kind in policy["kinds"]]
    if len(plurals) != len(set(plurals)):
        out.append("institution.kinds: each plural may appear once")
    for key in (*SYSTEM_KINDS, FALLBACK_KIND):
        if key not in keys:
            out.append(
                f"institution.kinds: `{key}` is required (the engine creates its rows)"
            )
    for kind in policy["kinds"]:
        if kind["system"] != (kind["key"] in SYSTEM_KINDS):
            out.append(
                f"institution.kinds.{kind['key']}: `system` is fixed by the engine - "
                f"true for {', '.join(SYSTEM_KINDS)} only"
            )
    if not policy["licences"]:
        out.append("institution.licences: at least one (the first is the default)")
    if not policy["institution"]["contacts"]:
        out.append("institution.institution.contacts: at least one address")
    try:
        ZoneInfo(policy["defaults"]["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        out.append("institution.defaults.timezone: not a zone the tz database knows")
    return out


def read(default: Path = DEFAULT_PATH, override: Path | None = OVERRIDE_PATH) -> dict:
    """The institution file: `default`, with `override` merged over it when that file
    exists. Raises `PolicyError` naming every problem - and for a checkout that still
    carries the old `policy.yml` beside it, which nothing reads any more: an institution's
    own values silently dropped would be worse than a stop."""
    if override is not None:
        (row,) = legacy.rows(legacy.TOOLKIT_ROOT)
        if override.with_name(row.old).is_file():
            raise PolicyError(f"{row.old} is now {row.new}: rename it (decision 0039)")
    policy = _parse(default)
    if override is not None and override.is_file():
        policy = merge(policy, _parse(override))
    problems = _problems(policy)
    if problems:
        raise PolicyError("; ".join(problems))
    for kind in policy["kinds"]:
        kind.setdefault("shown", True)
    return policy


@cache
def load() -> dict:
    """The institution file this checkout runs on, read once per process."""
    return read()


def defaults() -> dict:
    """The institution's defaults block."""
    return load()["defaults"]


def contact() -> str:
    """The first of the institution's `contacts`: where a fault goes when nobody nearer
    has an address, and the open site's contact when a course names none."""
    return load()["institution"]["contacts"][0]


def kinds() -> list[dict]:
    """The row kinds, in display order: `key`, `plural`, `numbered`, `shown`, `label`,
    `colour`, `background`, `system`."""
    return load()["kinds"]


def kind_label(kind: str) -> str:
    """The label of a kind ("Lecture", "Drop-in"); the key itself when unknown."""
    return next((k["label"] for k in kinds() if k["key"] == kind), kind)


def numbered_title(kind: str, number: int | None) -> str:
    """A row's title: its kind's label and number ("Lecture 3"), the label alone when it
    has none."""
    label = kind_label(kind)
    return f"{label} {number}" if number is not None else label


def console_link(semester_org: str, screen: str = "week") -> str:
    """One semester's screen in the student console (`institution.console_url`), or "" when
    the institution runs no console. Students are sent there only where their course opts
    in (`course.advertises_console`); the callers ask that first."""
    url = load()["institution"].get("console_url") or ""
    return f"{url}?semester={semester_org}#{screen}" if url else ""


def is_console_link(url: str) -> bool:
    """Whether `url` is a `console_link` - what a sentence around a stored link (the lock's
    `team_formation_page:`) reads to name the place it points at."""
    base = load()["institution"].get("console_url") or ""
    return bool(base) and url.startswith(base)


def content_kinds() -> tuple[str, ...]:
    """The kinds a `releases:` entry may declare (every kind the engine does not create
    rows for itself), in display order."""
    return tuple(k["key"] for k in kinds() if not k["system"])


def plural_of(key: str) -> str:
    """A kind's block and tab name (`lecture` -> `lectures`); the key itself when unknown."""
    return next((k["plural"] for k in kinds() if k["key"] == key), key)


def kind_for_plural(name: str) -> str | None:
    """The kind a block or tab name names (`labs` -> `lab`), or None."""
    return next((k["key"] for k in kinds() if k["plural"] == name), None)


def numbered_kinds() -> tuple[str, ...]:
    """The kinds whose entries are numbered (Lecture 3), in display order."""
    return tuple(k["key"] for k in kinds() if k["numbered"])


def shown_kinds() -> tuple[str, ...]:
    """The kinds that are rows and tabs (every kind but `shown: false` ones such as
    supporting files), in display order."""
    return tuple(k["key"] for k in kinds() if k["shown"])
