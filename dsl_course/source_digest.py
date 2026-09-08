"""dsl-course source digest -- schedule.yml's digest issue, and the CLI that names it.

The machinery is `config_digest`: one issue per file, whose BODY is the current list and
whose COMMENTS are the events, with a mail beside it. What lives here is what is specific
to schedule.yml - which goes wrong in two ways that keep different time, both of them in
the ONE issue:

- the plan cites materials the course org does not have. Time-bound: each fault climbs the
  ladder as its release approaches, and its notifications are held overnight.
- the parser could not use an entry at all. Immediate: the entry is already out of the
  plan, so there is nothing to count down to, and it is filed under how long it has stood.

One issue because a reader asked to fix schedule.yml should find everything wrong with it
in one place, and because the engine files a fault under its own clock (`ConfigFault`)
rather than under its issue's. `ABSORBED` is the title the unreadable entries used to be
reported under, by a block of bash in validate-schedule.yml: the first tick that finds
that issue reads its state into this one and closes it.

`--title` prints the issue's exact title, for the workflow that has to find it.
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
    "ABSORBED",
    "NOTIFY_FROM",
    "SCHEDULE",
    "SOURCES",
    "TITLE",
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
    "transitions",
]

# Stable, because the workflow finds its own issue by searching this exact title - a title
# that varied with the faults would never match, and every run would open a new issue.
TITLE = "schedule.yml: planned releases cite sources not staged in the course org"
# The title the bash block in validate-schedule.yml has been opening since before any of
# this existed. Kept to the letter so the first tick can find that issue, take its state
# and close it, rather than leaving it standing for the rest of the term beside the one
# that now carries the same faults.
ABSORBED = "schedule.yml has entries the scheduler cannot read"

SCHEDULE = Digest(title=TITLE, file=SCHEDULE_PATH, doc="docs/07-schedule-releases.md")
# What this digest was called while it carried sources alone. Kept because three modules
# and their tests name it.
SOURCES = SCHEDULE


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
    """schedule.yml's digest, for everything wrong with it: the sources
    `schedule.source_faults` found missing AND the entries `schedule.parse` had to drop."""
    return _sync(
        SCHEDULE,
        cohort_org,
        course_org,
        faults,
        now,
        dry_run=dry_run,
        resolve_mention=resolve_mention,
        migrate=migrated,
        absorb=ABSORBED,
    )


def hold(cohort_org: str, held: dict[str, str | None]) -> int:
    """Un-record a crossing whose mail did not go out - see `config_digest.hold`."""
    return _hold(SCHEDULE, cohort_org, held)


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
