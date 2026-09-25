"""The institution policy: every default the toolkit applies when nobody said otherwise.

`policy.default.yml` beside this module is what the toolkit ships (Hertie's values); an
optional `policy.yml` at the root of the toolkit checkout overrides it. The engine runs from
a checkout of the org's `central_ref`, so the policy an org gets is the one at that ref.

`defaults` is the institution layer of the run-settings cascade (`settings`); `kinds` are the
schedule row kinds; `institution` is the site block; `contact` is where a course fault goes
when no course admin declares an address; `licences` are the open site's choices, default
first. A policy that does not validate RAISES: it is maintainer-owned, and an engine running
on a guess about its own defaults would be worse than one that stops.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .course import FORMATS, TEAM_FORMATIONS, VISIBILITIES
from .schema_check import validate
from .setting_readers import READERS

DEFAULT_PATH = Path(__file__).with_name("policy.default.yml")
OVERRIDE_PATH = Path(__file__).resolve().parents[1] / "policy.yml"

# The kinds the engine creates rows for itself, and the one an unknown kind lands on: an
# override may relabel or recolour them, never drop them.
SYSTEM_KINDS = ("assignment", "term", "archive")
FALLBACK_KIND = "other"
# The blocks an override merges key by key; every other block it names replaces ours whole.
MERGED = ("defaults", "institution")
# The defaults a course or a semester can also write: each is read by the same reader as
# those layers (`setting_readers`), and must come back unchanged - a default no course
# could write (`max_team_size: -3`, `late_penalty_per_day: 100.9%`) is refused.
READ_DEFAULTS = (
    "late_window_days",
    "late_penalty_per_day",
    "max_team_size",
    "visibility",
    "team_formation",
    "formats",
)

_HEX = "^#[0-9a-fA-F]{6}$"
# `10%` or a fraction `0.1`: the two spellings `setting_readers.penalty_fault` accepts.
_PENALTY = r"^(?:(?:100|[0-9]{1,2})(?:\.[0-9]+)?%|0?\.[0-9]+|0)$"
_WHOLE = {"type": "integer"}

SCHEMA = {
    "type": "object",
    "properties": {
        "defaults": {
            "type": "object",
            "properties": {
                "late_window_days": _WHOLE,
                "late_penalty_per_day": {"type": "string", "pattern": _PENALTY},
                "max_team_size": _WHOLE,
                "visibility": {"type": "string", "enum": list(VISIBILITIES)},
                "team_formation": {"type": "string", "enum": list(TEAM_FORMATIONS)},
                "formats": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(FORMATS)},
                },
                "timezone": {"type": "string"},
                "archive": {
                    "type": "object",
                    "properties": {"grace_days": _WHOLE},
                    "required": ["grace_days"],
                    "additionalProperties": False,
                },
                "semester_dest_repo": {"type": "string", "pattern": r"^[\w.-]+$"},
            },
            "required": [
                "late_window_days",
                "late_penalty_per_day",
                "max_team_size",
                "visibility",
                "team_formation",
                "formats",
                "timezone",
                "archive",
                "semester_dest_repo",
            ],
            "additionalProperties": False,
        },
        "kinds": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "pattern": r"^[a-z][a-z0-9_-]*$"},
                    "label": {"type": "string"},
                    "colour": {"type": "string", "pattern": _HEX},
                    "background": {"type": "string", "pattern": _HEX},
                    "system": {"type": "boolean"},
                },
                "required": ["key", "label", "colour", "background", "system"],
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
            },
            "required": ["name", "url", "address", "dsl_org_url"],
            "additionalProperties": False,
        },
        "contact": {"type": "string", "pattern": r"^[^@\s]+@[^@\s]+$"},
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
    "required": ["defaults", "kinds", "institution", "contact", "licences"],
    "additionalProperties": False,
}


class PolicyError(ValueError):
    """The policy file(s) do not describe a policy the engine can run on."""


def _parse(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path.name} must be a mapping of blocks")
    return data


def merge(base: dict, override: dict) -> dict:
    """`override` over `base`: the `MERGED` blocks key by key (the `archive` block inside
    `defaults` too), every other block whole."""
    out = dict(base)
    for key, value in override.items():
        if key in MERGED and isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = {**out[key], **value}
            inner = out[key].get("archive")
            if isinstance(inner, dict) and isinstance(value.get("archive"), dict):
                merged["archive"] = {**inner, **value["archive"]}
            out[key] = merged
        else:
            out[key] = value
    return out


def _problems(policy: dict) -> list[str]:
    """What the JSON Schema subset cannot say: each default as its layer's reader reads
    it, unique kinds, the system kinds present, a non-empty licence list, a zone the tz
    database knows."""
    out = validate(policy, SCHEMA, "policy")
    if out:
        return out
    ours = policy["defaults"]
    days = READERS["late_window_days"]
    read = [(key, READERS[key], ours[key]) for key in READ_DEFAULTS]
    read.append(("archive.grace_days", days, ours["archive"]["grace_days"]))
    for key, reader, value in read:
        refused: list[str] = []
        want = tuple(value) if isinstance(value, list) else value
        if reader(value, "policy", refused) != want or refused:
            out.append(f"policy.defaults.{key}: `{value}` is not a usable value")
    keys = [kind["key"] for kind in policy["kinds"]]
    if len(keys) != len(set(keys)):
        out.append("policy.kinds: each key may appear once")
    for key in (*SYSTEM_KINDS, FALLBACK_KIND):
        if key not in keys:
            out.append(
                f"policy.kinds: `{key}` is required (the engine creates its rows)"
            )
    for kind in policy["kinds"]:
        if kind["system"] != (kind["key"] in SYSTEM_KINDS):
            out.append(
                f"policy.kinds.{kind['key']}: `system` is fixed by the engine - "
                f"true for {', '.join(SYSTEM_KINDS)} only"
            )
    if not policy["licences"]:
        out.append("policy.licences: at least one (the first is the default)")
    if not ours["formats"]:
        out.append("policy.defaults.formats: at least one starter format")
    try:
        ZoneInfo(policy["defaults"]["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        out.append("policy.defaults.timezone: not a zone the tz database knows")
    return out


def read(default: Path = DEFAULT_PATH, override: Path | None = OVERRIDE_PATH) -> dict:
    """The policy: `default`, with `override` merged over it when that file exists.
    Raises `PolicyError` naming every problem."""
    policy = _parse(default)
    if override is not None and override.is_file():
        policy = merge(policy, _parse(override))
    problems = _problems(policy)
    if problems:
        raise PolicyError("; ".join(problems))
    return policy


@cache
def load() -> dict:
    """The policy this checkout runs on, read once per process."""
    return read()


def defaults() -> dict:
    """The institution's defaults block."""
    return load()["defaults"]


def kinds() -> list[dict]:
    """The row kinds, in display order: `key`, `label`, `colour`, `background`, `system`."""
    return load()["kinds"]


def console_link(semester_org: str, screen: str = "week") -> str:
    """One semester's screen in the student console (`institution.console_url`), or "" when
    the institution runs no console."""
    url = load()["institution"].get("console_url") or ""
    return f"{url}?semester={semester_org}#{screen}" if url else ""


def content_kinds() -> tuple[str, ...]:
    """The kinds a `releases:` entry may declare (every kind the engine does not create
    rows for itself), in display order."""
    return tuple(k["key"] for k in kinds() if not k["system"])
