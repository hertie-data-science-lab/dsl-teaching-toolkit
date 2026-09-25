"""A materials repo's optional `materials.yml`, and the kind a folder implies.

A materials repo is any course-org repo carrying the `dsl-materials` topic; the name
`course-materials-<tag>` is only the default the scaffold picks. Nothing about its layout is
required. `materials.yml` at its root is the one escape hatch for what the convention cannot
say, and is absent for the default layout:

    syllabus: E1282_syllabus.pdf   # the syllabus file (default SYLLABUS.md)
    kinds:                         # a folder name -> the row kind it holds
      tutorials: lab
      quiz: other

A release entry that declares no `kind` takes one from the section its first copy lands in
(the top folder of the destination, or the destination repo itself when the copy lands at
its root): this repo's `kinds:` first, then the built-in aliases, else `lecture`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache

from . import policy
from .gh_contents import load_yaml_config
from .schema_check import validate

MATERIALS_TOPIC = "dsl-materials"
MATERIALS_FILE = "materials.yml"
DEFAULT_SYLLABUS = "SYLLABUS.md"
DEFAULT_KIND = "lecture"

# The folder names every course gets for free.
BUILTIN_ALIASES = {
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
    """What one repo's `materials.yml` declares, defaults filled in."""

    syllabus: str = DEFAULT_SYLLABUS
    kinds: Mapping[str, str] = field(default_factory=dict)


def parse(data: object, where: str = MATERIALS_FILE) -> Declared:
    """`materials.yml` as parsed YAML (None or {} = absent). Raises ValueError naming every
    problem: the file is instructor-owned, and a guess at what it meant would place rows in
    the wrong tab without a word."""
    if not data:
        return Declared()
    problems = validate(data, SCHEMA, where)
    if problems:
        raise ValueError("; ".join(problems))
    syllabus = str(data.get("syllabus") or DEFAULT_SYLLABUS).strip().strip("/")
    kinds = {
        str(folder).strip().strip("/"): known_kind(str(kind))
        for folder, kind in (data.get("kinds") or {}).items()
    }
    return Declared(syllabus or DEFAULT_SYLLABUS, kinds)


@cache
def read(org: str, repo: str) -> Declared:
    """`repo`'s declaration, read once per run. Absent is the defaults; a file that does
    not parse or does not validate raises, like `publish.yml` next to it."""
    return parse(
        load_yaml_config(org, repo, MATERIALS_FILE), f"{repo}/{MATERIALS_FILE}"
    )


def known_kind(kind: str) -> str:
    """A kind as the engine uses it: a content kind of the policy, else `other`."""
    kind = kind.strip().lower()
    return kind if kind in policy.content_kinds() else policy.FALLBACK_KIND


def infer_kind(section: str, aliases: Mapping[str, str] | None = None) -> str:
    """The kind a section implies: the repo's own alias, else the built-in one, else
    `lecture`."""
    return (aliases or {}).get(section) or known_kind(
        BUILTIN_ALIASES.get(section.lower(), DEFAULT_KIND)
    )


def is_materials_repo(row: dict) -> bool:
    """Whether a repo listing row is a materials repo: it carries the topic."""
    return MATERIALS_TOPIC in (row.get("topics") or [])
