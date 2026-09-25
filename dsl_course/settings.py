"""settings - every assignment setting: how its value is read, and where its effective
value comes from.

Every value is read by its key's reader (`setting_readers`).

The cascade: a RUN setting (`RUN_KEYS`: how teams form, the team cap, the late pair, the
repo visibility, the external submit link) resolves nearest-first through four layers -

    assignment   semester-config/assignments.yml  assignments.<slug>.<key>
    semester     semester-config/assignments.yml  defaults.<key>
    course       .github/dsl-course.yml            assignment_defaults.<key>
    institution  policy.yml                        defaults.<key>

and `effective` says which layer answered. The late pair is ONE rule: whichever layer
states either half states both, the other half absent meaning none (`_late_pair`).
Until the template loses its run keys (they move to `assignments.yml`), a key the
template's `grading_config.yml` declares sits in the assignment layer, below the
`assignments.yml` block for the same slug. `assignments.yml` is optional: absent, both of
its layers are empty. This is the ONE module that reads `assignment_defaults`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache

import yaml

from . import policy
from .course import ASSIGNMENTS_FILE, CONFIG_REPO, COURSE_CONFIG
from .discovery import course_org_for_semester, org_meta
from .gh_contents import get_file_content
from .log import log_err
from .setting_readers import read_settings, refuse_renamed

# What a COURSE may set once for every assignment under it, in `dsl-course.yml`
# `assignment_defaults:` - the course layer of the cascade below. The run settings among
# them resolve at READ time; `formats`, `submit_via`, `team_formation` and `visibility` also
# answer New assignment's boxes left at `course.COURSE_DEFAULT_CHOICE`
# (`scaffold.resolve_answers`). The per-assignment keys - the title, the type, the question
# maxima - are deliberately not among them: they are what makes one assignment different
# from the next.
COURSE_DEFAULT_KEYS = (
    "max_team_size",
    "late_window_days",
    "late_penalty_per_day",
    "formats",
    "submit_via",
    "team_formation",
    "visibility",
)
# Where the course-wide block lives, for the warnings it produces.
ASSIGNMENT_DEFAULTS_KEY = "assignment_defaults"
_DEFAULTS_WHERE = f"{COURSE_CONFIG} {ASSIGNMENT_DEFAULTS_KEY}"


def parse_assignment_defaults(raw: object) -> dict:
    """The course-wide `assignment_defaults:` block, validated exactly as an assignment's
    own file is. Returns the settings it declares; anything else it says is warned about
    and dropped, and a value its reader refuses states nothing (the institution's
    answers). A course that declares none gets `{}`."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        log_err(f"  ! {_DEFAULTS_WHERE}: must be a block of settings - ignored")
        return {}
    dropped: list[str] = []
    raw = refuse_renamed(raw, _DEFAULTS_WHERE, dropped)
    values = read_settings(raw, COURSE_DEFAULT_KEYS, _DEFAULTS_WHERE, dropped)
    for line in dropped:
        log_err(line)
    return {key: value for key, value in values.items() if value is not None}


# ------------------------------------------------------------------ the cascade

# The settings that say how ONE semester runs an assignment, as opposed to what the
# assignment is (the template's). Each resolves through the four layers.
RUN_KEYS = (
    "team_formation",
    "max_team_size",
    "late_window_days",
    "late_penalty_per_day",
    "visibility",
    "submit_url",
)
LATE_PAIR = ("late_window_days", "late_penalty_per_day")
SOURCES = ("assignment", "semester", "course", "institution")
ASSIGNMENTS_DEFAULTS = "defaults"
ASSIGNMENTS_BLOCKS = "assignments"

Layer = tuple[str, Mapping]


@cache
def course_defaults(course_org: str) -> dict:
    """A course's `assignment_defaults:` block, validated, read ONCE per course per process.

    An absent `dsl-course.yml` (a 404) declares nothing. Any OTHER failure to read it - no
    permission, rate limit, malformed YAML - RAISES and is not cached: a run that cannot
    see the course's defaults must stop rather than hand out and grade on the
    institution's. tests/conftest.py clears it."""
    if not course_org:
        return {}
    return parse_assignment_defaults(org_meta(course_org).get(ASSIGNMENT_DEFAULTS_KEY))


@cache
def _assignments_text(semester_org: str) -> str | None:
    """`semester-config/assignments.yml`, or None when the semester has none (a 404).
    Any other read failure raises (`get_file_content`)."""
    return get_file_content(semester_org, CONFIG_REPO, ASSIGNMENTS_FILE)


@cache
def semester_blocks(semester_org: str) -> tuple[dict, dict[str, dict]]:
    """`(defaults, {slug: block})` out of the semester's `assignments.yml`, each value
    through its reader; `({}, {})` when the file is absent (a 404). A read that fails for
    any other reason, or a file that is not YAML, RAISES and is not cached - the same
    fail-closed rule as `course_defaults`. tests/conftest.py clears it."""
    if not semester_org:
        return {}, {}
    text = _assignments_text(semester_org)
    data = yaml.safe_load(text) if text else None
    if not isinstance(data, dict):
        return {}, {}
    dropped: list[str] = []

    def block(raw: object, where: str) -> dict:
        if not isinstance(raw, dict):
            return {}
        return read_settings(raw, RUN_KEYS, where, dropped)

    defaults = block(
        data.get(ASSIGNMENTS_DEFAULTS), f"{ASSIGNMENTS_FILE} {ASSIGNMENTS_DEFAULTS}"
    )
    raw_blocks = data.get(ASSIGNMENTS_BLOCKS)
    blocks = {
        str(slug): block(raw, f"{ASSIGNMENTS_FILE} {ASSIGNMENTS_BLOCKS}.{slug}")
        for slug, raw in (raw_blocks.items() if isinstance(raw_blocks, dict) else ())
    }
    for line in dropped:
        log_err(line)
    return defaults, blocks


def institution_defaults() -> dict:
    """The policy's defaults for the run keys (and the starter formats)."""
    ours = policy.defaults()
    out = {key: ours[key] for key in RUN_KEYS if key in ours}
    out["formats"] = tuple(ours["formats"])
    return out


def layers(
    course_org: str,
    semester_org: str = "",
    slug: str = "",
    template: Mapping | None = None,
) -> list[Layer]:
    """The layers for one assignment, nearest first. `template` is the run settings its
    `grading_config.yml` declares (until they move to `assignments.yml`)."""
    defaults, blocks = semester_blocks(semester_org)
    return [
        ("assignment", _usable(blocks.get(slug, {}) if slug else {})),
        ("assignment", template or {}),
        ("semester", _usable(defaults)),
        ("course", _usable(course_defaults(course_org))),
        ("institution", institution_defaults()),
    ]


def _usable(block: Mapping) -> dict:
    """A block without the values its readers refused (None, for every run key): a value
    nobody can use states nothing, and the next layer answers. (A TEMPLATE's refused late
    key still states the pair - that file wrote a rule of its own; see `_late_pair`.)"""
    return {key: value for key, value in block.items() if value is not None}


def _states(block: Mapping, key: str) -> bool:
    """Whether a layer answers `key`. A late key is answered by a layer that states
    EITHER half of the pair; any other key by a value that is not blank (a reader's
    None is a value it refused, and the next layer answers)."""
    if key in LATE_PAIR:
        return any(k in block for k in LATE_PAIR)
    return block.get(key) not in (None, "")


def resolve(key: str, stack: Sequence[Layer]) -> tuple[object, str]:
    """`(value, source)` for `key`: the nearest layer that states it. A layer stating
    one half of the late pair answers the other half with None (no such rule)."""
    for source, block in stack:
        if _states(block, key):
            return block.get(key), source
    return None, "institution"


def effective(
    semester_org: str,
    slug: str,
    key: str,
    *,
    course_org: str = "",
    template: Mapping | None = None,
) -> tuple[object, str]:
    """The effective value of run setting `key` for assignment `slug` of `semester_org`,
    and the layer it came from: `assignment`, `semester`, `course` or `institution`."""
    course_org = course_org or course_org_for_semester(semester_org)
    return resolve(key, layers(course_org, semester_org, slug, template))


def effective_all(
    semester_org: str,
    slug: str,
    *,
    course_org: str = "",
    template: Mapping | None = None,
) -> dict[str, tuple[object, str]]:
    """`effective` for every run key, off one set of layers."""
    course_org = course_org or course_org_for_semester(semester_org)
    stack = layers(course_org, semester_org, slug, template)
    return {key: resolve(key, stack) for key in RUN_KEYS}
