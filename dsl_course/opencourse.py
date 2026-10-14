"""`course.yml`'s `open_course_site:` block: the public website's one declaration
(decisions 0016, 0039, 0040).

INSTRUCTOR-OWNED, in the course org's `.github/course.yml`, seeded `enabled: false` by
Bootstrap Course Org:

    open_course_site:
      enabled: true                     # false: nothing publishes, the daily update stops
      source_repo: course-materials-f2026
      publish:                          # a Selection (decision 0040), plus reading_lists
        kinds: [lectures, labs]         # the kinds whose files are published
        reading_lists: true             # readings as a list of names, never the files
        exclude: ["labs/**/drafts/"]
      contact: course@example.edu       # the site's contact; unset = the institution's

Both the Publish website operation and the daily update read it; neither takes an input of
its own. A `publish:` key left out takes its default (`selection.publish_default`):
lectures and labs, reading lists on, readings files never unless `readings` is listed.
Licence and description are course facts in the `course:` block.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from . import legacy, selection
from .config import read_course
from .course import COURSE_CONFIG, OPEN_SITE_BLOCK
from .faults import Unusable
from .schema_check import validate
from .selection import Selection

OPENCOURSE_FILE = COURSE_CONFIG
OPENCOURSE_REPO = ".github"
# The open site's own key beside the four selection keys.
READING_LISTS = "reading_lists"

_LIST = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "source_repo": {"type": ["string", "null"]},
        "publish": {
            "type": ["object", "null"],
            "properties": {
                **{key: _LIST for key in selection.KEYS},
                READING_LISTS: {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        # The open site's contact (decision 0019); unset = the institution's first.
        "contact": {"type": "string", "pattern": r"^[^@\s]+@[^@\s]+$"},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class OpenCourse:
    """What `open_course_site:` declares. `publish` is the selection AS WRITTEN; the
    site reads `selection` (defaults filled in)."""

    enabled: bool = False
    source_repo: str = ""
    publish: Selection = field(default_factory=Selection)
    reading_lists: bool = True

    @property
    def selection(self) -> Selection:
        return selection.with_defaults(self.publish, selection.publish_default())

    def publish_data(self) -> dict:
        """`publish:` as written, `reading_lists` included when it is not the default."""
        out = self.publish.as_data()
        if not self.reading_lists:
            out[READING_LISTS] = False
        return out


def parse(data: object, where: str = OPENCOURSE_FILE) -> OpenCourse:
    """The parsed YAML (None or {} = every default, so off). Raises `Unusable` naming every
    problem: a guess at a hand-edited publishing switch would publish the wrong thing."""
    if not data:
        return OpenCourse()
    old = [k for k in data if legacy.flag(str(k), legacy.OPEN_SITE_KEYS)]
    if old:
        raise Unusable(
            f"{where}: {', '.join(f'`{k}`' for k in old)} "
            f"{'is' if len(old) == 1 else 'are'} retired (decision 0040): "
            f"{legacy.flag(str(old[0]), legacy.OPEN_SITE_KEYS).cost}"
        )
    problems = validate(data, SCHEMA, where)
    if problems:
        raise Unusable("; ".join(problems))
    raw = data.get("publish") or None
    out = OpenCourse(
        enabled=bool(data.get("enabled", False)),
        source_repo=str(data.get("source_repo") or "").strip(),
        publish=selection.parse(raw, f"{where} publish", extra=(READING_LISTS,)),
        reading_lists=bool((raw or {}).get(READING_LISTS, True)),
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


# What a new site's `publish:` says: the default, written out so it can be read.
SEED_PUBLISH = {"kinds": ["lectures", "labs"], READING_LISTS: True}


def seed_text(oc: OpenCourse | None = None, contact: str = "") -> str:
    """The seeded `open_course_site:` block of `course.yml`, with `oc`'s values (default:
    off) live and every key explained once. A `publish:` nobody wrote is written as the
    default (`SEED_PUBLISH`), so it can be read and changed."""
    oc = oc or OpenCourse()
    publish = oc.publish_data() or SEED_PUBLISH
    body = yaml.safe_dump(
        {"publish": publish},
        default_flow_style=None,
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    body = "".join(f"  {line}\n" for line in body.splitlines())
    contact_line = (
        f"  contact: {contact}   # the site's contact\n"
        if contact
        else "  # contact: course@example.edu   # the site's contact; unset = the institution's\n"
    )
    return (
        f"{OPEN_SITE_BLOCK}:   # the public website, an open version of one materials repo\n"
        f"  enabled: {str(oc.enabled).lower()}   # false: nothing is published\n"
        f"  source_repo: {oc.source_repo}   # the materials repo it is built from\n"
        + contact_line
        + "  # Which files it publishes: `kinds` (lectures, labs, readings, ...), `types`,\n"
        "  # `include` and `exclude` (as in .releaseignore). Reading lists are text; the\n"
        "  # readings themselves only when `readings` is listed. Solutions, tests and\n"
        "  # grading files never appear.\n" + body
    )
