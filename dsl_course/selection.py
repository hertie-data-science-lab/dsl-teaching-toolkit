"""One Selection shape for every "which files" question (decision 0040).

A `Selection` is four optional keys, the same wherever one is written:

    kinds:   [lectures, labs]          # schedule block names; a path's kind is the block
                                       # of the entry that landed it, else none
    types:   [html, pdf]               # file extensions
    include: ["readings/**/key.pdf"]   # gitignore syntax, repo-root anchored
    exclude: ["**/drafts/"]

Three uses: the materials repo's `.releaseignore` (exclude only: what students never get),
`semester.yml student_site.host` (viewable copies on the PUBLIC semester site) and
`course.yml open_course_site.publish` (the open-courseware site). Every outbound copy asks
`select` (a path list) or `deny` (a `copytree` walk), which compose the same layers in a
fixed order:

    1. the institution denylist (`DENYLIST`, `CLUTTER`): never copied anywhere
    2. the repo's `.releaseignore` files
    3. the use-site selection
    4. `institution.yml selection_floor` for that use: a hard limit, applied last

A key left out is "no filter on that key"; a use fills its own defaults in first
(`with_defaults`). A path with no kind matches no `kinds:` list: it is still released, but
never hosted or opened unless an `include` names it.

Stdlib + pathspec only, like `releaseignore`: no GitHub, no config file read here except
the institution's floor (`policy`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cached_property
from pathlib import Path

from . import policy
from .faults import Unusable
from .fs import Deny
from .releaseignore import Ignore, deny_for, excludes, listed

KEYS = ("kinds", "types", "include", "exclude")

# The institution denylist (decision 0040 rule 3): path COMPONENTS no copy ever carries,
# matched by name at every depth, case-insensitively, as globs (`.env.local`). Solutions,
# hidden tests, grading files, secrets, git metadata and the toolkit's own `.system/`. A
# release, a hosted copy and the open site all refuse them; no pattern re-includes one.
DENYLIST = (
    ".system",
    "solution",
    "solutions",
    "grading_config.yml",
    "grading.yml",  # the pre-rename name; the engine stopped reading it, this list has not
    "tests",
    ".env",
    ".env.*",
    ".git",
)

# Never course material, in any course, on any page: what a file manager or an interpreter
# leaves in a folder. Kept apart from DENYLIST because nothing here leaks anything (it is
# clutter), and a closed list of NAMES is the only honest form of the rule. Not "never
# committed": `.gitkeep` still holds an unwritten session folder open in a source repo.
CLUTTER = frozenset(
    {
        ".DS_Store",
        ".gitkeep",
        "Thumbs.db",
        "desktop.ini",
        "__pycache__",
        ".ipynb_checkpoints",
    }
)
_CLUTTER_FOLDED = frozenset(n.casefold() for n in CLUTTER)

# The uses that take a floor, by their `institution.yml selection_floor` key.
HOST = "student_site_host"
PUBLISH = "open_course_site_publish"
FLOOR_USES = (HOST, PUBLISH)

# `html` covers `htm`: one type, two spellings.
_TYPE_ALIASES = {"htm": "html"}


def is_clutter(name: str) -> bool:
    """Whether one path COMPONENT is never course material (case-insensitive)."""
    return name.casefold() in _CLUTTER_FOLDED


def is_denied(name: str) -> bool:
    """Whether one path COMPONENT is on the institution denylist (case-insensitive)."""
    folded = name.casefold()
    return any(fnmatch(folded, pattern) for pattern in DENYLIST)


def denied(path: str) -> bool:
    """Whether any component of `path` is on the denylist or is clutter: layer 1."""
    return any(is_denied(p) or is_clutter(p) for p in path.split("/") if p)


def publishable(path: str) -> bool:
    """Whether a path may be listed or copied at all, whatever a selection says."""
    return not denied(path)


def _type(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return _TYPE_ALIASES.get(ext, ext)


def file_type(path: str) -> str:
    """A path's type for `types:`: its extension, lowercased, `htm` read as `html`."""
    name = path.rsplit("/", 1)[-1]
    return _type(name.rsplit(".", 1)[-1]) if "." in name.lstrip(".") else ""


def exact_pattern(path: str) -> str:
    """The pattern that names exactly `path` (and, for a folder, all inside it): anchored
    at the root, every character a pattern reads specially escaped."""
    escaped = "".join(f"\\{c}" if c in "[]*?\\" else c for c in path.strip("/"))
    return f"/{escaped}"


def _matches(ignore: Ignore, path: str) -> bool:
    """Whether a pattern list names `path`: git's rule (`releaseignore.Ignore.excludes`),
    every proper prefix of a listed file being a folder."""
    return ignore.excludes(path, lambda rel: rel != path.strip("/"))


def _matches_dir(ignore: Ignore, path: str) -> bool:
    """Whether a pattern list names the FOLDER `path`."""
    return ignore.excludes(path, lambda _rel: True)


@dataclass(frozen=True)
class Selection:
    """One selection. `None` = the key is absent (no filter on it); `()` = written empty."""

    kinds: tuple[str, ...] | None = None
    types: tuple[str, ...] | None = None
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] | None = None

    @cached_property
    def _include(self) -> Ignore:
        return listed(self.include or ())

    @cached_property
    def _exclude(self) -> Ignore:
        return listed(self.exclude or ())

    def keeps(self, path: str, kind: str | None) -> bool:
        """Layer 3: `exclude` beats everything; else a path is kept when its kind is in
        `kinds` (or `kinds` is absent) or an `include` names it, and its type is in
        `types` (or `types` is absent)."""
        if self.exclude and _matches(self._exclude, path):
            return False
        by_kind = self.kinds is None or (kind is not None and kind in self.kinds)
        if not by_kind and not (self.include and _matches(self._include, path)):
            return False
        return self.types is None or file_type(path) in self.types

    def as_data(self) -> dict:
        """The written keys, as YAML would hold them."""
        out: dict = {}
        for key in KEYS:
            value = getattr(self, key)
            if value is not None:
                out[key] = list(value)
        return out


def with_defaults(sel: Selection, default: Selection) -> Selection:
    """`sel` with each key it leaves out taken from `default`, key by key."""
    return Selection(
        kinds=default.kinds if sel.kinds is None else sel.kinds,
        types=default.types if sel.types is None else sel.types,
        include=default.include if sel.include is None else sel.include,
        exclude=default.exclude if sel.exclude is None else sel.exclude,
    )


def kind_names() -> tuple[str, ...]:
    """The names `kinds:` takes: the schedule's content blocks (`lectures`), in order."""
    return tuple(policy.plural_of(k) for k in policy.content_kinds())


def shown_kind_names() -> tuple[str, ...]:
    """`kind_names` that are rows and tabs (not supporting files), in order."""
    shown = set(policy.shown_kinds())
    return tuple(policy.plural_of(k) for k in policy.content_kinds() if k in shown)


def _strings(data: dict, key: str, where: str, problems: list[str]) -> tuple | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        problems.append(f"{where}: `{key}` must be a list of strings")
        return None
    return tuple(x.strip() for x in value if x.strip())


def parse(
    data: object,
    where: str,
    *,
    extra: Iterable[str] = (),
    exclude_only: bool = False,
) -> Selection:
    """A written selection (None = nothing written). Raises `Unusable` naming every
    problem: a guess at which files go public would publish the wrong ones. `extra` are
    the use's own keys beside the four, which its caller reads; `exclude_only` is the
    `.releaseignore` use's shape."""
    if data is None:
        return Selection()
    if not isinstance(data, dict):
        raise Unusable(f"{where} must be a mapping of {', '.join(KEYS)}")
    allowed = ("exclude",) if exclude_only else KEYS
    problems = [
        f"{where}: `{key}` is not a selection key (one of {', '.join(allowed)})"
        for key in data
        if key not in allowed and key not in extra
    ]
    values = {key: _strings(data, key, where, problems) for key in allowed}
    plurals = set(kind_names())
    problems += [
        f"{where}: `kinds` names `{kind}`, which is not a kind of the institution "
        f"({', '.join(sorted(plurals))})"
        for kind in values.get("kinds") or ()
        if kind not in plurals
    ]
    if problems:
        raise Unusable("; ".join(problems))
    types = values.get("types")
    return Selection(
        kinds=values.get("kinds"),
        types=None if types is None else tuple(_type(t) for t in types),
        include=values.get("include"),
        exclude=values.get("exclude"),
    )


# ------------------------------------------------------------------- the floor (layer 4)


@dataclass(frozen=True)
class Floor:
    """One use's hard limit from `institution.yml selection_floor`: kinds that are
    `never` kept, and patterns that are always excluded."""

    never: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def allows(self, path: str, kind: str | None) -> bool:
        if kind is not None and kind in self.never:
            return False
        return not (self.exclude and _matches(listed(self.exclude), path))


def floor(use: str) -> Floor:
    """`use`'s floor (`HOST` or `PUBLISH`); none written = no limit. A floor that does not
    validate raises `Unusable`: it is maintainer-owned."""
    raw = (policy.load().get("selection_floor") or {}).get(use)
    if raw is None:
        return Floor()
    where = f"institution.yml selection_floor.{use}"
    if not isinstance(raw, dict) or set(raw) - {"never", "exclude"}:
        raise Unusable(f"{where} must be a mapping of `never` (kinds) and `exclude`")
    problems: list[str] = []
    never = _strings(raw, "never", where, problems) or ()
    exclude = _strings(raw, "exclude", where, problems) or ()
    if problems:
        raise Unusable("; ".join(problems))
    return Floor(never, exclude)


# ------------------------------------------------------------------------ the layers

# A path's kind: the block of the schedule entry that landed it, or None.
KindOf = Callable[[str], str | None]


def _no_kind(_path: str) -> str | None:
    return None


def kept(
    path: str,
    kind_of: KindOf = _no_kind,
    *,
    use: Selection | None = None,
    withheld: Callable[[str], bool] | None = None,
    limit: Floor | None = None,
) -> bool:
    """Whether one path survives every layer. `withheld` is layer 2 (a `.releaseignore`
    predicate), `use` layer 3, `limit` layer 4; any left out is no filter."""
    if denied(path):
        return False
    if withheld is not None and withheld(path):
        return False
    kind = kind_of(path)
    if use is not None and not use.keeps(path, kind):
        return False
    return limit is None or limit.allows(path, kind)


def select(
    files: Iterable[str],
    kind_of: KindOf = _no_kind,
    *,
    use: Selection | None = None,
    withheld: Callable[[str], bool] | None = None,
    limit: Floor | None = None,
) -> tuple[str, ...]:
    """The files that survive every layer, in the order given (`kept`)."""
    return tuple(
        f for f in files if kept(f, kind_of, use=use, withheld=withheld, limit=limit)
    )


def releaseignored(root: Path) -> Callable[[str], bool]:
    """Layer 2 over a clone: whether the `.releaseignore` files under `root` withhold a
    path (relative to `root`)."""
    return lambda rel: excludes(root, root / rel)


def deny(
    root: Path,
    *,
    use: Selection | None = None,
    kind_of: KindOf = _no_kind,
    limit: Floor | None = None,
) -> Deny:
    """The layers as a `copytree` filter over the clone at `root`: a denied,
    `.releaseignore`-withheld or `use`-excluded name is pruned (nothing inside it is
    walked), and each FILE must also be kept by `use` and `limit`. A folder is never
    dropped for its kind: an `include` may name a file inside one whose kind is out."""
    ignored = deny_for(root)

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        here = Path(dirpath)
        skip = {n for n in names if is_denied(n) or is_clutter(n)}
        skip |= ignored(dirpath, names)
        if use is None and limit is None:
            return skip
        for name in set(names) - skip:
            p = here / name
            rel = p.relative_to(root).as_posix()
            if p.is_dir() and not p.is_symlink():
                if use is not None and use.exclude and _matches_dir(use._exclude, rel):
                    skip.add(name)
                continue
            kind = kind_of(rel)
            if (use is not None and not use.keeps(rel, kind)) or (
                limit is not None and not limit.allows(rel, kind)
            ):
                skip.add(name)
        return skip

    return ignore


def at_source(root: Path, path: Path) -> str | None:
    """Why a path named outright cannot be copied out of the clone at `root`: `denylist`,
    `releaseignore`, or None."""
    if (
        path != root
        and root in path.parents
        and denied(path.relative_to(root).as_posix())
    ):
        return "denylist"
    return "releaseignore" if excludes(root, path) else None


# ------------------------------------------------------------------------ the defaults

READINGS = "readings"
# What the open site leaves out unless its `exclude:` says otherwise: anything whose name
# says answers, assessment or marks. Word-shaped, each case by a character class: a
# pattern matches inside a name, so a bare `*exam*` would hide `examples/`.
PUBLISH_EXCLUDE = (
    "[Ss]olution*",
    "*[-_.][Ss]olution*",
    "*[Ee]xam",
    "*[Ee]xams",
    "*[Ee]xam[-_.]*",
    "[Gg]rade*",
    "*[-_.][Gg]rade*",
    "[Mm]arks*",
    "*[-_.][Mm]arks*",
    "*[Pp]rivate*",
)


def host_default() -> Selection:
    """`student_site.host` when a key is left out: every shown kind but readings
    (copyright: the semester site is public), as html and pdf."""
    kinds = tuple(k for k in shown_kind_names() if k != READINGS)
    return Selection(kinds=kinds, types=("html", "pdf"), include=(), exclude=())


def publish_default() -> Selection:
    """`open_course_site.publish` when a key is left out: lectures and labs, every type,
    `PUBLISH_EXCLUDE` kept off."""
    kinds = tuple(policy.plural_of(k) for k in ("lecture", "lab"))
    return Selection(kinds=kinds, types=None, include=(), exclude=PUBLISH_EXCLUDE)
