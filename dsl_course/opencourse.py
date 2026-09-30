"""`opencourse.yml`: the public website's one declaration (decision 0016).

INSTRUCTOR-OWNED, in the course org's `.github`, seeded `enabled: false` by Bootstrap Course
Org (and by the migration, from a site's leftover `_publish-config.yml`):

    enabled: true                     # false: nothing publishes, the daily update stops
    source_repo: course-materials-f2026
    readings_mode: reading-list       # reading-list | actual-readings | none
    include_lectures: true            # the repo's file sections, as a group
    withhold:                         # kept off the site; `.releaseignore` syntax
      - "labs/**/solutions/"

Both the Publish public website operation and the daily update read it; neither takes an
input of its own. `withhold` patterns match the source repo's paths from its root and add
to what the repo's own `.releaseignore` and the publication denylist already keep back.
Licence, contact and description are course facts in `dsl-course.yml`, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .faults import Unusable
from .gh_contents import load_yaml_config
from .records import SYSTEM_DIR
from .schema_check import validate

OPENCOURSE_FILE = "opencourse.yml"
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


def read(org: str) -> OpenCourse | None:
    """The course's declaration, or None when it has no `opencourse.yml`. A file that does
    not parse or does not validate raises `Unusable`."""
    where = f"{OPENCOURSE_REPO}/{OPENCOURSE_FILE}"
    try:
        data = load_yaml_config(org, OPENCOURSE_REPO, OPENCOURSE_FILE)
    except yaml.YAMLError as exc:
        raise Unusable(f"{where} is not valid YAML") from exc
    return None if data is None else parse(data, where)


def seed_text(oc: OpenCourse | None = None) -> str:
    """The seeded file, with `oc`'s values (default: off) live and every key explained
    once. A seed for a website that is off, with nothing to withhold, gets
    `DEFAULT_WITHHOLD`: it is a first write, so an empty list there was never anyone's
    choice. A website already on (the migration, from an earlier publish) keeps its empty
    list, or its next publish would take files off a live site."""
    oc = oc or OpenCourse()
    withhold = yaml.safe_dump(
        {"withhold": list(oc.withhold or (() if oc.enabled else DEFAULT_WITHHOLD))},
        default_flow_style=False,
        allow_unicode=True,
    )
    return (
        "# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n"
        "#\n"
        "# The public website: an open version of one materials repo, for anyone.\n"
        "# Edit it on the console's Public website tab, or here.\n"
        f"enabled: {str(oc.enabled).lower()}   # false: nothing is published\n"
        f"source_repo: {oc.source_repo}   # the materials repo it is built from\n"
        f"readings_mode: {oc.readings_mode}   # reading-list, actual-readings or none\n"
        f"include_lectures: {str(oc.include_lectures).lower()}   # publish the repo's files\n"
        "# Paths kept off the website, as in .releaseignore. A course whose website is\n"
        "# off starts with the system folder kept off, and anything whose name has the\n"
        "# word solution, exam, grade, marks or private in it:\n" + withhold
    )
