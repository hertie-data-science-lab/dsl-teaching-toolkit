"""A materials repo's optional `materials.yml`.

A materials repo is any course-org repo carrying the `dsl-materials` topic; the name
`course-materials-<tag>` is only the default the scaffold picks. Nothing about its layout is
required, and no folder name means anything (decision 0038: the schedule says what each
entry is). `materials.yml` at its root names the syllabus when it is not `SYLLABUS.md`:

    syllabus: E1282_syllabus.pdf

Its `kinds:` folder map, and the folder-name kinds below it, are no longer read by the
semester side: the open site still reads them (`public_site`, until WP-P rebuilds it on
the schedule), the console's `materials.json` still exports them (until WP-C), and
`migrate` reads them once, to write each 0.10 entry's kind into its block.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import yaml

from . import config, policy, run
from .config import ConfigFile
from .faults import Unusable
from .repos import has_denied_component, has_never_material_component
from .schema_check import validate

MATERIALS_TOPIC = "dsl-materials"
MATERIALS_FILE = "materials.yml"
DEFAULT_SYLLABUS = "SYLLABUS.md"
# The weekly plan's block in a Markdown syllabus (`syllabus.place`): a write owns what lies
# between these two lines and nothing else in the file.
PLAN_START = "<!-- dsl:weekly-plan -->"
PLAN_END = "<!-- /dsl:weekly-plan -->"
# The kind whose folders are released but get no row or tab on either site.
ASSETS_KIND = "assets"
# WP-P/WP-C remove: the kind a folder implies, for the open site, the console's
# materials.json and the 0.11.0 migration only.
# The kind of a folder no alias names (decision 0031 rule 10; it was `lecture`).
DEFAULT_KIND = ASSETS_KIND
# The kind a 0.10 `releases:` entry that named none and copied nothing had.
EMPTY_ENTRY_KIND = "lecture"

# How deep a folder's own name still names its kind: the top, and one level down. A
# `kinds:` path is honoured at any depth.
ALIAS_DEPTH = 2

# The folder names a kind was inferred from before decision 0038.
BUILTIN_ALIASES = {
    "lecture": "lecture",
    "lectures": "lecture",
    "lab": "lab",
    "labs": "lab",
    "tutorials": "lab",
    "reading": "readings",
    "readings": "readings",
    "literature": "readings",
    "assets": "assets",
    "data": "assets",
    "fig": "assets",
    "figures": "assets",
    "images": "assets",
    "img": "assets",
    "src": "assets",
    "static": "assets",
}
# The kinds a folder could be set to (the console's retired Folder kinds dropdown).
FOLDER_KINDS = tuple(
    k for k in policy.content_kinds() if k in set(BUILTIN_ALIASES.values())
)

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


@run.per_run(lambda org, repo: run.scope(org, repo, MATERIALS_FILE))
def read(org: str, repo: str) -> Declared:
    """`repo`'s declaration, read once per run. Absent is the defaults; a file that does
    not parse or does not validate raises `Unusable`."""
    return declared(config.read(org, repo, MATERIALS_FILE))


def declared(file: ConfigFile, quiet: bool = False) -> Declared:
    """THE parse of a `materials.yml`, from the API (`read`) or a clone
    (`public_site`). Absent is the defaults; a file that does not parse or does not
    validate raises `Unusable`. `quiet` leaves the file's own problem unlogged."""
    where = f"{file.repo}/{MATERIALS_FILE}"
    try:
        data = file.data() if quiet else file.mapping()
    except yaml.YAMLError as exc:
        raise Unusable(f"{where} is not valid YAML") from exc
    return parse(data, where)


def known_kind(kind: str) -> str:
    """A kind as the engine uses it: a content kind of the policy, else `other`."""
    kind = kind.strip().lower()
    return kind if kind in policy.content_kinds() else policy.FALLBACK_KIND


def infer_kind(section: str, aliases: Mapping[str, str] | None = None) -> str:
    """The kind a section implies: the repo's own alias, else the built-in one, else
    supporting files."""
    return alias_kind(section, aliases) or DEFAULT_KIND


def alias_kind(section: str, aliases: Mapping[str, str] | None = None) -> str | None:
    """The kind a section NAMES - the repo's alias, else a built-in one - or None. A
    section may be a folder path: the repo's alias names it at any depth, a built-in name
    only within `ALIAS_DEPTH` (`students/lectures`, never `a/b/lectures`)."""
    key = section.strip("/").lower()
    parts = key.split("/")
    found = (aliases or {}).get(key) or (
        BUILTIN_ALIASES.get(parts[-1]) if len(parts) <= ALIAS_DEPTH else None
    )
    return known_kind(found) if found else None


def is_materials_repo(row: dict) -> bool:
    """Whether a repo listing row is a materials repo: it carries the topic."""
    return MATERIALS_TOPIC in (row.get("topics") or [])


def publishable(path: str) -> bool:
    """Whether a path may be hosted at all, whatever a pattern says: not on the denylist
    (`solution/`, `tests/`, grading files, `.env`) and not a never-material name."""
    return not has_denied_component(path) and not has_never_material_component(path)
