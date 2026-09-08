"""dsl-course source digest -- the two digest issues schedule.yml earns, and the CLI that
names one of them.

The machinery is `config_digest`: an issue whose BODY is the current list and whose
COMMENTS are the events, with a mail beside it. What lives here is what is specific to
schedule.yml, which is the only file with TWO of them, because it goes wrong in two ways
that keep different time:

- `SOURCES` - the plan cites materials the course org does not have. Time-bound: each
  fault climbs the ladder as its release approaches, and its notifications are held
  overnight (see `Digest.scheduled`).
- `UNREADABLE` - the parser could not use an entry at all. Immediate: the entry is already
  out of the plan, so there is nothing to count down to.

Two issues rather than one because they are two clocks, and because both titles already
exist in every live cohort: `UNREADABLE`'s was opened by a block of bash in
validate-schedule.yml, which this replaces (the engine adopts the open issue by title).

`--title` prints the sources issue's exact title, for the workflow that has to find it.
"""

from __future__ import annotations

import argparse
import sys

from .config_digest import (
    _STATE,
    Context,
    Digest,
    DigestResult,
    _read_marker,
    cleared_body,
    current_state,
    in_quiet_hours,
    render_body,
    transitions,
)
from .config_digest import (
    hold as _hold,
)
from .config_digest import sync as _sync
from .faults import NOTIFY_FROM, Severity
from .schedule import SCHEDULE_PATH, SourceFault

# Re-exported: `scheduler`, `notify` and their tests speak to the engine through this
# module, which is where the schedule's own digests are declared.
__all__ = [
    "NOTIFY_FROM",
    "SOURCES",
    "TITLE",
    "UNREADABLE",
    "UNREADABLE_TITLE",
    "_STATE",
    "Context",
    "Digest",
    "DigestResult",
    "Severity",
    "_read_marker",
    "cleared_body",
    "current_state",
    "hold",
    "in_quiet_hours",
    "migrated",
    "render_body",
    "sync",
    "sync_unreadable",
    "transitions",
]

# Stable, because the workflow finds its own issue by searching this exact title - a title
# that varied with the faults would never match, and every run would open a new issue.
TITLE = "schedule.yml: planned releases cite sources not staged in the course org"
# The title the bash block in validate-schedule.yml has been opening since before any of
# this existed. Kept to the letter so the engine ADOPTS that issue rather than opening a
# second one beside it and leaving the first standing for the rest of the term.
UNREADABLE_TITLE = "schedule.yml has entries the scheduler cannot read"

_DOC = "docs/07-schedule-releases.md"

SOURCES = Digest(title=TITLE, file=SCHEDULE_PATH, doc=_DOC, scheduled=True)
UNREADABLE = Digest(title=UNREADABLE_TITLE, file=SCHEDULE_PATH, doc=_DOC)


def migrated(previous: dict[str, str], faults: list[SourceFault]) -> dict[str, str]:
    """`previous`, with any key written before `SourceFault.key` carried the deploy's path
    renamed to the key that same fault has today.

    The key gained `[path]` because two deploys under one entry are two faults, and every
    digest issue open at the moment that shipped carries keys in the old `<where>.<field>`
    shape. Read as they stand, each of them is a fault that CLEARED and a fault that
    APPEARED in the same tick - a close comment and a fresh mail about nothing, on the one
    channel this design exists to keep quiet.

    An old key matching exactly one of today's faults becomes that fault's key. One
    matching SEVERAL is dropped: those are the two deploys the path was added to tell
    apart, and there is no honest way to say which of them the recorded rung belonged to,
    so the loudest reappears rather than inheriting it. A key matching nothing is left
    alone - that is a fault that genuinely cleared, and it is owed its Cleared comment.

    Removable one release cycle after it ships: by then no open digest carries an
    old-shape key."""
    today: dict[str, list[str]] = {}
    for f in faults:
        today.setdefault(f"{f.where}.{f.field}", []).append(f.key)
    # Already-current keys first, so a body written mid-migration - carrying both shapes
    # for one fault - keeps the rung it actually reported at.
    out = {k: v for k, v in previous.items() if k in today.get(k, [k])}
    for key, rung in previous.items():
        current = today.get(key, [key])
        if key not in current and len(current) == 1:
            out.setdefault(current[0], rung)
    return out


def sync(
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
    resolve_mention=None,
) -> DigestResult:
    """The sources digest, for the faults `schedule.source_faults` found."""
    return _sync(
        SOURCES,
        cohort_org,
        course_org,
        faults,
        now,
        dry_run=dry_run,
        resolve_mention=resolve_mention,
        migrate=migrated,
    )


def sync_unreadable(
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
    resolve_mention=None,
) -> DigestResult:
    """The unreadable-entries digest, for what `schedule.parse` had to drop."""
    return _sync(
        UNREADABLE,
        cohort_org,
        course_org,
        faults,
        now,
        dry_run=dry_run,
        resolve_mention=resolve_mention,
    )


def hold(cohort_org: str, held: dict[str, str | None]) -> int:
    """Un-record a sources crossing whose mail did not go out - see `config_digest.hold`."""
    return _hold(SOURCES, cohort_org, held)


def main() -> int:
    """`--title` prints the digest issue's exact title, and nothing else.

    A CLI for one constant, because the alternative is a copy of it in the cohort's
    validate-schedule template - and every lookup of this issue matches the title
    EXACTLY (see `issues.find_issues`), so a copy stops finding the issue the day the
    wording changes, silently, on the one line that was meant to point at it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--title",
        action="store_true",
        help="print the digest issue's exact title - what a workflow searches for to "
        "link the record it has just written to",
    )
    if not parser.parse_args().title:
        parser.error("nothing to do - pass --title")
    print(TITLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
