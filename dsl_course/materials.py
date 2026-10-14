"""Materials repos: the topic that makes one, the syllabus default, and the folder kinds.

A materials repo is any course-org repo carrying the `dsl-materials` topic; the name
`course-materials-<tag>` is only the default the scaffold picks. Nothing about its layout is
required, and no folder name means anything to the semester side (decision 0038: the
schedule says what each entry is). The engine reads no `materials.yml` (decision 0039 and
the maintainer's ruling of 14 Oct 2026): a semester's syllabus is `semester.yml`
`student_site.syllabus` (`semester_syllabus`), and a folder's kind on the open site is the
institution's kind model (`folder_kind`). `SCHEMA` and the built-in aliases are still
exported to the console's Folder kinds panel (`schemas.materials_json`, until WP-C2), and
`migrate` reads an old `materials.yml` once.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import policy
from .repos import has_denied_component, has_never_material_component

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


def semester_syllabus(meta: object) -> str:
    """A parsed `semester.yml`'s `student_site.syllabus` (decision 0039): the released path
    the home page pins, "" when it declares none (`DEFAULT_SYLLABUS` is looked for)."""
    site = meta.get("student_site") if isinstance(meta, dict) else None
    value = site.get("syllabus") if isinstance(site, dict) else None
    return str(value or "").strip().strip("/")


def folder_kind(section: str) -> str:
    """The kind a top-level folder names in the institution's kind model: its plural
    (`labs`) or its key (`lab`), else supporting files."""
    name = section.strip("/").lower()
    found = policy.kind_for_plural(name) or (
        name if name in policy.content_kinds() else None
    )
    return found or DEFAULT_KIND


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
