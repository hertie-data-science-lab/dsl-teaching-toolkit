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

`publish.yml` beside it says which files the semester site hosts openly. Its patterns match
the paths of THIS repo (`hosted_paths`), and a release that renames a path is translated
back through its copies (`hosted_copy`), so the hosted set is what the console previewed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cache

import yaml

from . import policy
from .course import is_repo_root
from .faults import Unusable
from .gh_contents import load_yaml_config
from .releaseignore import parse as parse_patterns
from .repos import has_denied_component, has_never_material_component
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


# ---------------------------------------------------------------- publish.yml

# A rendered deck: the format GitHub shows as SOURCE, and the one whose assets sit beside it.
DECK_EXTENSIONS = ("html", "htm")
# Asset folders a rendered deck may keep beside it besides `<stem>_files/` (Quarto,
# reveal.js and hand-made decks). They travel with every hosted deck in their folder.
BUNDLE_DIRS = ("media", "libs", "images")


def publishable(path: str) -> bool:
    """Whether a path may be hosted at all, whatever a pattern says: not on the denylist
    (`solution/`, `tests/`, grading files, `.env`) and not a never-material name."""
    return not has_denied_component(path) and not has_never_material_component(path)


def bundle_prefixes(path: str) -> tuple[str, ...]:
    """The directories a rendered deck's assets sit in, beside it: `<stem>_files/`, plus
    `media/`, `libs/` and `images/` in the deck's own folder."""
    folder = f"{path.rsplit('/', 1)[0]}/" if "/" in path else ""
    return (
        f"{path.rsplit('.', 1)[0]}_files/",
        *(f"{folder}{name}/" for name in BUNDLE_DIRS),
    )


def _is_deck(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return "." in name and name.rsplit(".", 1)[-1].lower() in DECK_EXTENSIONS


def publish_lines(public: Iterable[str]) -> list[str]:
    """`public:` patterns with one rule git lacks: a negated FOLDER (`!labs/sub/`) excludes
    its whole subtree, whatever matched before - what faculty mean by it, and the safe
    direction (git's `!dir/` cannot un-match the files of a `dir/**` above it)."""
    out = []
    for line in public:
        body = line.strip()
        if body.startswith("!") and body.endswith("/") and body.strip("!/"):
            folder = body[1:].rstrip("/")
            anchored = "/" in folder.lstrip("/")
            out.append(f"!{folder}/**" if anchored else f"!**/{folder}/**")
        else:
            out.append(line)
    return out


def hosted_paths(paths: Iterable[str], public: Iterable[str]) -> frozenset[str]:
    """Which of a materials repo's paths its `publish.yml` `public:` patterns host: every
    publishable path a pattern matches (gitignore syntax, the last match wins, a negated
    folder excludes its subtree: `publish_lines`), plus the asset folders beside each
    matched deck. Pure: the console's Files badges run the same rule
    (`console/schemas/materials.json` holds cases both sides are tested against)."""
    paths = tuple(paths)
    spec = parse_patterns("\n".join(publish_lines(public)))
    matched = {p for p in paths if publishable(p) and spec.check_file(p).include}
    for deck in [p for p in matched if _is_deck(p)]:
        prefixes = bundle_prefixes(deck)
        matched |= {p for p in paths if p.startswith(prefixes) and publishable(p)}
    return frozenset(matched)


@dataclass(frozen=True)
class Feed:
    """One source repo feeding a semester repo: its `public:` patterns and the
    (source path, semester path) pair of each copy the schedule makes from it."""

    public: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]


def _root(path: str) -> str:
    return "" if is_repo_root(path) else path.strip("/")


def _under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(f"{prefix}/")


def _translate(path: str, src: str, dst: str) -> str:
    rest = path[len(dst) :].lstrip("/") if dst else path
    return "/".join(x for x in (src, rest) if x)


def source_path(path: str, pairs: Iterable[tuple[str, str]]) -> str | None:
    """The source path a semester path was copied from: the copy with the longest
    semester prefix covering it, translated back; None when no copy covers it."""
    best = max(
        ((_root(src), _root(dst)) for src, dst in pairs if _under(path, _root(dst))),
        key=lambda pair: len(pair[1]),
        default=None,
    )
    return None if best is None else _translate(path, *best)


def hosted_copy(paths: Iterable[str], feeds: Iterable[Feed]) -> frozenset[str]:
    """Which paths of a SEMESTER repo the site hosts. Each path belongs to ONE copy: the
    one, across every source repo, whose destination is the longest prefix covering it
    (a code repo copied into `lectures/05/code` owns that folder, not the repo copied
    to `lectures`). It is judged under its source path by that repo's patterns only, and
    a repo with no patterns hosts nothing. A path no copy covers (released by hand) is
    judged as it is named, by every repo's patterns."""
    paths, feeds = tuple(paths), tuple(feeds)
    copies = [
        (i, _root(src), _root(dst))
        for i, feed in enumerate(feeds)
        for src, dst in feed.pairs
    ]
    owned: list[dict[str, list[str]]] = [{} for _ in feeds]
    loose: list[str] = []
    for p in paths:
        best = max(
            (c for c in copies if _under(p, c[2])),
            key=lambda c: len(c[2]),
            default=None,
        )
        if best is None:
            loose.append(p)
        else:
            i, src, dst = best
            owned[i].setdefault(_translate(p, src, dst), []).append(p)
    out: set[str] = set()
    for feed, back in zip(feeds, owned):
        for p in loose:
            back.setdefault(p, []).append(p)
        chosen = hosted_paths(back, feed.public)
        out |= {p for src in chosen for p in back[src] if publishable(p)}
    return frozenset(out)
