"""A materials repo's optional `materials.yml`, and the kind a folder implies.

A materials repo is any course-org repo carrying the `dsl-materials` topic; the name
`course-materials-<tag>` is only the default the scaffold picks. Nothing about its layout is
required. `materials.yml` at its root is the one escape hatch for what the convention cannot
say, and is absent for the default layout:

    syllabus: E1282_syllabus.pdf   # the syllabus file (default SYLLABUS.md)
    kinds:                         # a folder name -> the row kind it holds
      tutorials: lab
      quiz: other

A release entry that declares no `kind` takes one from the section its first copy lands in:
the top folder of the DESTINATION path, or the destination repo itself when the copy lands
at its root. The source repo's `kinds:` first, then the built-in aliases, both matched
case-insensitively, else `lecture`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache

import yaml

from . import policy
from .faults import Unusable
from .gh_contents import load_yaml_config
from .schema_check import validate

MATERIALS_TOPIC = "dsl-materials"
MATERIALS_FILE = "materials.yml"
DEFAULT_SYLLABUS = "SYLLABUS.md"
DEFAULT_KIND = "lecture"

# The folder names every course gets for free. A folder of one of these names that no
# schedule entry releases still gets its rows (`site`'s off-plan rows).
BUILTIN_ALIASES = {
    "lecture": "lecture",
    "lectures": "lecture",
    "lab": "lab",
    "labs": "lab",
    "tutorials": "lab",
    "reading": "readings",
    "readings": "readings",
    "literature": "readings",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "syllabus": {"type": "string"},
        "kinds": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Declared:
    """What one repo's `materials.yml` declares, defaults filled in. `declared` says
    whether `syllabus:` was written (else it is the default)."""

    syllabus: str = DEFAULT_SYLLABUS
    kinds: Mapping[str, str] = field(default_factory=dict)
    declared: bool = False


def parse(data: object, where: str = MATERIALS_FILE) -> Declared:
    """`materials.yml` as parsed YAML (None or {} = absent). Raises `Unusable` naming every
    problem: the file is instructor-owned, and a guess at what it meant would place rows in
    the wrong tab without a word. Folder keys are lowercased (matched case-insensitively)."""
    if not data:
        return Declared()
    problems = validate(data, SCHEMA, where)
    if problems:
        raise Unusable("; ".join(problems))
    syllabus = str(data.get("syllabus") or "").strip().strip("/")
    kinds = {
        str(folder).strip().strip("/").lower(): known_kind(str(kind))
        for folder, kind in (data.get("kinds") or {}).items()
    }
    return Declared(syllabus or DEFAULT_SYLLABUS, kinds, bool(syllabus))


@cache
def read(org: str, repo: str) -> Declared:
    """`repo`'s declaration, read once per run. Absent is the defaults; a file that does
    not parse or does not validate raises `Unusable`, like `publish.yml` next to it."""
    where = f"{repo}/{MATERIALS_FILE}"
    try:
        data = load_yaml_config(org, repo, MATERIALS_FILE)
    except yaml.YAMLError as exc:
        raise Unusable(f"{where} is not valid YAML") from exc
    return parse(data, where)


def known_kind(kind: str) -> str:
    """A kind as the engine uses it: a content kind of the policy, else `other`."""
    kind = kind.strip().lower()
    return kind if kind in policy.content_kinds() else policy.FALLBACK_KIND


def infer_kind(section: str, aliases: Mapping[str, str] | None = None) -> str:
    """The kind a section implies: the repo's own alias, else the built-in one, else
    `lecture`."""
    return alias_kind(section, aliases) or DEFAULT_KIND


def alias_kind(section: str, aliases: Mapping[str, str] | None = None) -> str | None:
    """The kind a section NAMES - the repo's alias, else a built-in one - or None."""
    key = section.lower()
    found = (aliases or {}).get(key) or BUILTIN_ALIASES.get(key)
    return known_kind(found) if found else None


def is_materials_repo(row: dict) -> bool:
    """Whether a repo listing row is a materials repo: it carries the topic."""
    return MATERIALS_TOPIC in (row.get("topics") or [])
