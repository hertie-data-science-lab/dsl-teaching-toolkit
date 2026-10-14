"""`course.yml`'s `open_course_site:` block: the public website's one declaration
(decisions 0016, 0039).

INSTRUCTOR-OWNED, in the course org's `.github/course.yml`, seeded `enabled: false` by
Bootstrap Course Org:

    open_course_site:
      enabled: true                     # false: nothing publishes, the daily update stops
      source_repo: course-materials-f2026
      readings_mode: reading-list       # reading-list | actual-readings | none
      include_lectures: true            # the repo's file sections, as a group
      withhold:                         # kept off the site; `.releaseignore` syntax
        - "labs/**/solutions/"
      contact: course@example.edu       # the site's contact; unset = the institution's

Both the Publish website operation and the daily update read it; neither takes an
input of its own. `withhold` patterns match the source repo's paths from its root and add
to what the repo's own `.releaseignore` and the publication denylist already keep back.
Its selection keys are carried as they were until the hosting selection (decision 0040)
replaces them. Licence and description are course facts in the `course:` block.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .config import read_course
from .course import COURSE_CONFIG, OPEN_SITE_BLOCK
from .faults import Unusable
from .records import SYSTEM_DIR
from .schema_check import validate

OPENCOURSE_FILE = COURSE_CONFIG
OPENCOURSE_REPO = ".github"
READINGS_MODES = ("reading-list", "actual-readings", "none")
DEFAULT_READINGS_MODE = READINGS_MODES[0]
# What a new `opencourse.yml` keeps off a website that is not on yet: the engine's own
# folder, and anything whose name says answers, assessment or marks. Word-shaped, and each
# case by a character class: a pattern matches inside a name, so a bare `*exam*` would hide
# `examples/` and `*marks*` `remarks.md`. Broader than the publication denylist, which
# names exact folders.
DEFAULT_WITHHOLD = (
    f"{SYSTEM_DIR}/",
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

SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "source_repo": {"type": ["string", "null"]},
        "readings_mode": {"type": "string", "enum": list(READINGS_MODES)},
        "include_lectures": {"type": "boolean"},
        "withhold": {"type": ["array", "null"], "items": {"type": "string"}},
        # The open site's contact (decision 0019); unset = the institution's first.
        "contact": {"type": "string", "pattern": r"^[^@\s]+@[^@\s]+$"},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class OpenCourse:
    """What `opencourse.yml` declares, defaults filled in."""

    enabled: bool = False
    source_repo: str = ""
    readings_mode: str = DEFAULT_READINGS_MODE
    include_lectures: bool = True
    withhold: tuple[str, ...] = ()


def parse(data: object, where: str = OPENCOURSE_FILE) -> OpenCourse:
    """The parsed YAML (None or {} = every default, so off). Raises `Unusable` naming every
    problem: a guess at a hand-edited publishing switch would publish the wrong thing."""
    if not data:
        return OpenCourse()
    problems = validate(data, SCHEMA, where)
    if problems:
        raise Unusable("; ".join(problems))
    out = OpenCourse(
        enabled=bool(data.get("enabled", False)),
        source_repo=str(data.get("source_repo") or "").strip(),
        readings_mode=str(data.get("readings_mode") or DEFAULT_READINGS_MODE),
        include_lectures=bool(data.get("include_lectures", True)),
        withhold=tuple(str(p) for p in data.get("withhold") or ()),
    )
    if out.enabled and not out.source_repo:
        raise Unusable(f"{where}: enabled needs a source_repo")
    return out


WHERE = f"{OPENCOURSE_REPO}/{OPENCOURSE_FILE} {OPEN_SITE_BLOCK}"


def block(meta: object) -> dict | None:
    """`open_course_site:` out of a parsed `course.yml`, None when it has none; a block
    that is not a mapping raises `Unusable`."""
    raw = meta.get(OPEN_SITE_BLOCK) if isinstance(meta, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise Unusable(f"{WHERE} is not a mapping")
    return raw


def load(org: str) -> dict | None:
    """The course's `open_course_site:` block as parsed YAML, None when there is none. A
    `course.yml` that is not a YAML mapping raises `Unusable`; `parse` validates the
    rest."""
    try:
        return block(read_course(org).mapping())
    except yaml.YAMLError as exc:
        raise Unusable(
            f"{OPENCOURSE_REPO}/{OPENCOURSE_FILE} is not valid YAML"
        ) from exc


def read(org: str) -> OpenCourse | None:
    """The course's declaration, or None when it has no `open_course_site:`. A block that
    does not validate raises `Unusable`."""
    data = load(org)
    return None if data is None else parse(data, WHERE)


def seed_text(oc: OpenCourse | None = None, contact: str = "") -> str:
    """The seeded `open_course_site:` block of `course.yml`, with `oc`'s values (default:
    off) live and every key explained once. A seed for a website that is off, with nothing
    to withhold, gets `DEFAULT_WITHHOLD`: it is a first write, so an empty list there was
    never anyone's choice. A website already on (the migration, from an earlier publish)
    keeps its empty list, or its next publish would take files off a live site."""
    oc = oc or OpenCourse()
    withhold = yaml.safe_dump(
        {"withhold": list(oc.withhold or (() if oc.enabled else DEFAULT_WITHHOLD))},
        default_flow_style=False,
        allow_unicode=True,
    )
    withhold = "".join(f"  {line}\n" for line in withhold.splitlines())
    contact_line = (
        f"  contact: {contact}   # the site's contact\n"
        if contact
        else "  # contact: course@example.edu   # the site's contact; unset = the institution's\n"
    )
    return (
        f"{OPEN_SITE_BLOCK}:   # the public website, an open version of one materials repo\n"
        f"  enabled: {str(oc.enabled).lower()}   # false: nothing is published\n"
        f"  source_repo: {oc.source_repo}   # the materials repo it is built from\n"
        f"  readings_mode: {oc.readings_mode}   # reading-list, actual-readings or none\n"
        f"  include_lectures: {str(oc.include_lectures).lower()}   # publish the repo's files\n"
        + contact_line
        + "  # Paths kept off the website, as in .releaseignore. A course whose website is\n"
        "  # off starts with the system folder kept off, and anything whose name has the\n"
        "  # word solution, exam, grade, marks or private in it:\n" + withhold
    )
