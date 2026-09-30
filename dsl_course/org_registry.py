"""The course orgs this toolkit serves: `orgs.yml` at the root of the toolkit checkout.

A `dsl-course-hub` topic on a public `.github` is something anyone can put on their own org,
so the topic alone never makes an org a course. The registry does: the bot accepts an org
invitation only from an org named here (`invitations`), and the tier fan-out refreshes, and
so hands the bot token to, only the orgs named here (`list_orgs`). Semester orgs are not
listed: each is registered by its course's `semesters.yml`, as before.

Maintainer-edited, like `policy.yml`, and read the same way: from the checkout of the ref
the run is on. A registry that is missing or does not parse RAISES: an empty answer would
read as "no courses", and a fan-out that refreshes nothing must not look like one that
refreshed everything.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import yaml

PATH = Path(__file__).resolve().parents[1] / "orgs.yml"
_ORG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def parse(text: str) -> frozenset[str]:
    """The registered course orgs in `text`, casefolded (GitHub's names are not
    case-sensitive). Raises on anything that is not `course_orgs:` and a list of org names."""
    return frozenset(o.casefold() for o in parse_names(text))


def parse_names(text: str) -> tuple[str, ...]:
    """The registered course orgs in `text` as the file spells them, in its order."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"orgs.yml does not parse: {exc}") from exc
    orgs = doc.get("course_orgs") if isinstance(doc, dict) else None
    if not isinstance(orgs, list) or not all(
        isinstance(o, str) and _ORG.match(o) for o in orgs
    ):
        raise RuntimeError("orgs.yml must be `course_orgs:` and a list of org names")
    return tuple(orgs)


@cache
def names() -> tuple[str, ...]:
    """The registry of the checkout this process runs from, as spelt, read once."""
    try:
        text = PATH.read_text()
    except OSError as exc:
        raise RuntimeError(
            f"could not read the course org registry {PATH.name}: {exc}"
        ) from exc
    return parse_names(text)


def course_orgs() -> frozenset[str]:
    """The registered course orgs, casefolded."""
    return frozenset(o.casefold() for o in names())


def registered(org: str) -> bool:
    return org.casefold() in course_orgs()
