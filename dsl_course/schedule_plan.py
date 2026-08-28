"""Interpret a parsed `schedule.Schedule` into the session rows a site or syllabus shows.

Pure reading of the plan: which numbered rows it declares, when each happens, what it
calls them and where their materials will land. Nothing here touches GitHub or renders
anything - `site` turns these rows into pages, `syllabus` into a table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import schedule
from .course import session_number

# The one section with copyright semantics of its own (--readings-mode); every OTHER
# section a repo happens to have is published as files, whatever it's called.
READINGS_SECTION = "readings"

# A week's lecture and its lab are two separate rows of the theme's schedule table, and
# the labs page selects `type: lab` out of the `_lectures` collection - so which row a
# released folder lands in is decided by its section (the directory it was released into),
# never by anything faculty declare. Everything that isn't `labs` is lecture material.
LAB_SECTION = "labs"


def row_kind(section: str) -> str:
    """The schedule-row type a released section belongs to: 'lab' or 'lecture'."""
    return "lab" if section == LAB_SECTION else "lecture"


def deploy_dest(deploy: schedule.Deploy) -> str:
    """Where a deploy lands inside its destination repo - `cohort_dest_path` when it is
    set, else the source path mirrored. Stated once: the ordinal and the section of a row
    are both read off this, and deriving them from two separate copies of the rule is how
    they come to disagree."""
    return (deploy.cohort_dest_path or deploy.course_source_path).strip("/")


def deploy_section(deploy: schedule.Deploy) -> str:
    """The section a deploy lands in - the top-level directory of its destination path,
    or the destination repo itself when the path is a bare session folder (a release into
    a repo that IS one section). The read-side twin is `site._source_section`, which reports for
    an already-released folder, so both sides classify a row the same way."""
    head, sep, _ = deploy_dest(deploy).partition("/")
    return head if sep else deploy.cohort_dest_repo


@dataclass
class PlannedRow:
    """What the release PLAN says about one session row, before anything has shipped.

    `when` is the earliest event_datetime touching the row; `dests` the cohort-side
    `repo/path`s its deploys will land in (ordered, deduped); `subtitle` and `description`
    the display text its entry declared; `readings_planned` whether any of its deploys
    targets the readings section - which is how a row can say a reading list is still to
    come rather than leaving the session off the Materials tab entirely."""

    when: date | datetime
    dests: dict[str, None] = field(default_factory=dict)
    subtitle: str = ""
    description: str = ""
    readings_planned: bool = False
    # When the entry that supplied `subtitle`/`description` happens. Kept on the row so
    # one row's state lives in one object: `when` is the min over every entry touching the
    # row, which is not the same thing as "which entry named it".
    named_at: datetime | None = None


# A schedule label's own ordinal and row kind: `lecture-12` -> ('12', 'lecture'),
# `lab-4` -> ('4', 'lab'). The deploy-keyed path below places a row from where its files
# LAND, which cannot place an entry that stages nothing yet - so the label is the fallback,
# and it is a reliable one because the label is what faculty write the ordinal into.
_LABEL_ROW_RE = re.compile(r"^([a-z]+)[-_]0*(\d+)$", re.IGNORECASE)


# The label heads that NAME a session row, and which kind each raises. A label outside this
# table raises no row: the site's rows come from the ordinal session folders a deploy lands
# in (docs/07 - "the label is yours, and never shown to students"), and the label is only a
# fallback for an entry that has not shipped yet. Anything-plus-a-number used to read as a
# lecture, so `bonus-1`, `quiz-2` or `topic-3` invented a phantom "Session N - not released
# yet" row AND folded into the real lecture-N row, where `row.when = min(...)` dragged that
# session's published date - and its title - back to the bonus entry's date.
# `readings` maps to None for the same reason: readings belong to a session without being
# one, so an entry that forgets `show_on_site: false` must still not raise a lecture row.
_LABEL_ROW_KINDS = {
    "lecture": "lecture",
    "lectures": "lecture",
    "lab": "lab",
    "labs": "lab",
    "readings": None,
    "reading": None,
}


def row_from_label(label: str) -> tuple[str, str] | None:
    """The (ordinal, kind) row a schedule label names, or None if it names no session.

    `course-intro` and any other label whose head is not a known session kind return None:
    they are not a numbered session, so they raise no row of their own."""
    m = _LABEL_ROW_RE.match(label.strip())
    if not m:
        return None
    kind = _LABEL_ROW_KINDS.get(m.group(1).lower())
    return (m.group(2), kind) if kind else None


def planned_sessions(sched: schedule.Schedule) -> dict[tuple[str, str], PlannedRow]:
    """Every session row the PLAN declares - (ordinal, 'lecture'|'lab') -> what the plan
    says about it (see `PlannedRow`).

    Keyed by the ordinal and section of each deploy's destination folder, so the site can
    both date a released row from the plan that released it AND raise a row for a session
    whose materials have not shipped yet (`sync_site` unions these keys with what
    discovery found). An entry NO deploy can place - it stages nothing yet, or stages
    nothing ordinal-prefixed - falls back to its own label (`row_from_label`), because
    docs/07 promises a row from the moment the entry is written, not from the moment it
    ships. Keying on the row, not the week, is what lets Wednesday's lab carry
    its own time rather than inheriting Monday's lecture. Deploys may ship on their own
    `deploy_datetime` clocks; the site announces the class, not the copy. Earliest wins
    when several releases touch the same row, and the destinations are collected in plan
    order (deduped - two deploys of one entry can name the same one) so a placeholder row
    can name where its materials are going to appear. An entry marked `show_on_site:
    false` raises, dates and names nothing, but still contributes its destinations to a row
    another entry already raised - see the two loops."""
    out: dict[tuple[str, str], PlannedRow] = {}

    def place(
        key: tuple[str, str],
        release: schedule.Release,
        dest: str | None = None,
        section: str | None = None,
    ) -> None:
        """Fold one entry into the row it touches. Shared by the deploy-keyed path and the
        label fallback so a row means the same thing however it was placed."""
        row = out.setdefault(key, PlannedRow(when=release.when))
        row.when = min(row.when, release.when)
        if dest is not None:
            # dict-as-ordered-set, not a list: dedupe where the destinations are
            # collected, so the consumer is a plain join and the returned value means
            # what the docstring says it does.
            row.dests[dest] = None
        if section is not None:
            row.readings_planned = row.readings_planned or section == READINGS_SECTION
        # A row is NAMED by the same entry it is DATED by: the earliest one touching
        # it. Title and description are adopted as a pair - they describe one session,
        # and taking the name from one entry and the blurb from another would read as
        # a mismatch nobody wrote.
        if (release.title or release.description) and (
            row.named_at is None or release.when < row.named_at
        ):
            row.named_at = release.when
            row.subtitle = release.title
            row.description = release.description

    for release in sched.releases:
        if release.when is None:
            continue  # event_datetime: tbc - undated, can't place a session
        if not release.show_on_site:
            continue  # second pass, below - a silent entry may not raise or date a row
        placed = False
        for d in release.deploy:
            dest = deploy_dest(d)
            n = session_number(dest.rsplit("/", 1)[-1])
            if n is None:
                continue
            section = deploy_section(d)
            place(
                (str(n), row_kind(section)),
                release,
                dest=f"{d.cohort_dest_repo}/{dest}",
                section=section,
            )
            placed = True
        if placed or release.assignment is not None:
            # An assignment entry gets its own out/due rows elsewhere; it is never a
            # session row, so the label fallback must not claim one for it.
            continue
        # No deploy placed this entry: it stages nothing yet, or nothing it stages is an
        # ordinal-prefixed session folder. docs/07 promises "a row appears as soon as you
        # write it, not when it ships", so the entry's own label places it - dated, named,
        # and flagged unreleased, with no destinations to name yet.
        key = row_from_label(release.label)
        if key is not None:
            place(key, release)

    # SECOND pass for the silent entries. A silent release ships on its own clock but says
    # nothing here, so it may neither raise a row of its own nor pull an existing one's date
    # or name back to whenever the files went up. Its DESTINATIONS still count, though: they
    # are what lets an unreleased row say "readings will appear in materials/readings/02_x"
    # and carry `readings_pending`. Since readings moved out of the lecture entries into
    # their own silent ones, dropping them here left both of those permanently unreachable.
    #
    # A second pass, not one: releases are sorted by datetime and readings usually ship
    # AHEAD of their session, so a silent readings entry is reached before the lecture entry
    # that raises its row. Folding in one pass would silently lose exactly the common case.
    for release in sched.releases:
        if release.show_on_site or release.when is None:
            continue
        for d in release.deploy:
            dest = deploy_dest(d)
            n = session_number(dest.rsplit("/", 1)[-1])
            if n is None:
                continue
            section = deploy_section(d)
            row = out.get((str(n), row_kind(section)))
            if row is None:
                continue  # raising a row is precisely what silence forbids
            row.dests[f"{d.cohort_dest_repo}/{dest}"] = None
            row.readings_planned = row.readings_planned or section == READINGS_SECTION
    return out
