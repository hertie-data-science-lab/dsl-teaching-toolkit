"""dsl-course schedule -- the per-semester semester-config/schedule.yml, this semester's
single home for the timed release plan AND the dates other tools display/enforce:

Each block encodes a BEHAVIOUR: `releases` deploy materials, `assignments` have a
lifecycle, `events` are display-only calendar rows.

    timezone: Europe/Berlin          # optional (default Europe/Berlin) - how naive times
                                     # below are interpreted; GitHub cron itself is UTC
    releases:                        # the auto-release plan - label ->
      lecture_02:                    # {event_datetime + deploys}. Each deploy ships at its
        event_datetime: 2026-09-15T10:00   # deploy_datetime (default: the event itself).
        title: Linear regression           # the session's name - the site's TITLE column
        details: Least squares by hand     # its DETAILS column, and the session's own page
        type: lecture                      # optional override: lecture/lab/readings.
                                           # Omitted, the deploy path decides, as before.
        show_on_site: true                 # optional (default true) - false deploys
        deploy:                            # silently, off the site's schedule.
          - course_source_repo: course-materials-f2026   # course_source_repo + course_source_path
            course_source_path: lectures/02_intro        # are the only required keys;
            semester_dest_repo: materials                  # semester_dest_repo, semester_dest_path
            semester_dest_path: lectures/02_intro          # and deploy_datetime are optional.
            deploy_datetime: 2026-09-15T09:00
    assignments:                     # each assignment's TIMINGS. The key is a label;
      assignment-1:                  # course_source_repo names the COURSE-org repo
        course_source_repo: assignment-1-f2026   # it hands out from, and is REQUIRED.
        details: Fit it by hand      # the DETAILS column, on its hand-out and due rows
        handout_datetime: 2026-09-22T09:00  # A bare due_datetime is END of day (23:59:59)
        due_datetime: 2026-10-13     # - "due on the 13th" closes at day's end. The late
                                     # cutoff is due + `late_window_days` (assignments.yml).
        marks_return_datetime: 2026-10-27  # optional: marks go back then, once all marked
    events:                          # display-only rows - nothing deploys, the site just
      mid-term:                      # shows them. `type` is `exam` or `special_event`
        type: exam                   # (the default when omitted). `event_datetime` is a
        title: MidTerm Exam          # whole day, or a full datetime when the start time
        details: Open book, 2 hours  # is known.
        event_datetime: 2026-11-03
      project-clinic:
        title: Project Clinic
        event_datetime: 2026-10-14T10:00
    semester_start: 2026-09-07
    semester_end: 2026-12-18
    archive:                         # OPTIONAL - and the SWITCH: with no block, nothing
      event_datetime: 2027-02-16     # ever freezes this semester. default: semester_end + 60
      title: Semester archived         # optional: the row's TITLE (the default, as shown)
      details: We freeze here.       # optional: ALL that row and the Updates box say
      show_on_site: true             # default true: a row on the site's Schedule tab

Every block takes the same vocabulary, named for the site column it fills: `title:` (the
Title column), `details:` (the Details column - markdown, and additive: it renders ABOVE
whatever that cell already generates), a `*_datetime:`, `show_on_site:` and `tbc:`, plus
`type:` on the two blocks where it discriminates.

Every field is optional - a semester with no schedule.yml (or a blank one) behaves exactly
as before everywhere that reads it (releases are skipped, dates synthesised).

Times are timezone-aware: a naive datetime/date is interpreted in `timezone`; an explicit
offset (e.g. `...T14:00+02:00`) names the same instant and is converted into `timezone`,
so every parsed datetime is already the semester's own wall clock (what the site shows, and
what it fires at, are then the same number).

Parsing is total but never silent: an entry that is valid YAML yet not a valid schedule
entry (a typo'd key, a missing date) is dropped so the rest of the term still parses, and
recorded in `Schedule.dropped` for `load` to log, `--validate` to fail on, and Check semester setup
to count.

Validation is offline by design - it parses the file it is given and nothing else, so its
verdict depends on nothing but that file. `--check-sources` bolts an online, ADVISORY
half onto it: whether the repos and paths the plan names exist in the course org yet. That
answer changes week to week (a lecture nobody has written yet is normal in August and a
fault in November), so it is reported alongside the verdict and never folded into it.

Usage:
    python3 -m dsl_course.schedule --semester-org hertie-dsl-demo-f2026
    python3 -m dsl_course.schedule --semester-org hertie-dsl-demo-f2026 --validate
    python3 -m dsl_course.schedule --file semester-config/schedule.yml --validate
    python3 -m dsl_course.schedule --file schedule.yml --validate \\
        --check-sources hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from . import policy, settings
from .course import (
    ASSIGNMENTS_FILE,
    CONFIG_REPO,
    SOLUTION_WARNING,
    assignment_slug,
    coerce_date,
    is_repo_root,
    pages_repo,
    semester_of,
)
from .discovery import discover_assignments
from .faults import (
    NOT_MIGRATED,
    NOTIFY_FROM,
    SOURCE_CRITICAL_WINDOW,
    SOURCE_URGENT_WINDOW,
    SOURCE_WARN_WINDOW,
    ConfigFault,
    FaultKind,
    Severity,
    hours,
    moved_text,
    not_migrated_text,
)
from .gh_contents import (
    LINES,
    get_file_content,
    get_file_with_sha,
    line_of,
    load_yaml_lines,
    put_file,
    repo_tree,
    take_lines,
    yaml_mark_line,
)
from .log import CLIParser, log, log_err
from .releaseignore import RELEASEIGNORE, excluded_in_tree
from .repos import default_branch, repo_missing

SCHEDULE_PATH = "schedule.yml"

# How long after the declared term end a semester is left running before it is frozen
# read-only (see `dsl_course.teardown`). Sixty days, because the real courses this toolkit
# was measured against went on being pushed to for about three weeks past their last class,
# and a semester that freezes while somebody is still finishing their marking is worse than
# one that freezes late. A semester that wants another date says so in
# `archive.event_datetime`, and
# only a semester that writes the block at all is ever archived off this (`_parse_archive`).
ARCHIVE_GRACE = timedelta(days=policy.defaults()["archive"]["grace_days"])  # policy.yml

# How long before that date a semester is TOLD. Two weeks is long enough to move the date,
# to finish a late piece of marking or to pull a copy of anything somebody wants to keep,
# and short enough that the notice is still about something imminent. Spelled once here
# because two surfaces count back from the same date - the site's Updates box and the
# notice issue-and-mail the scheduler files - and a fortnight on one and ten days on the
# other would be the site and the inbox disagreeing about when a term ends.
ARCHIVE_NOTICE = timedelta(days=14)

# What the archive row is CALLED when the block names no `title:` of its own. A default
# rather than a fixed string in the renderer, because `archive:` now takes the same
# `title:`/`details:` pair as every other block and faculty may name the day whatever
# their programme calls it ("Semester closed", "Repositories frozen").
ARCHIVE_TITLE = "Semester archived"

# What a fault in schedule.yml has always been called here, and still is: `ConfigFault`
# with a `kind` set. One type rather than two, so the digest, the mail and the ladder are
# the same code for a source the plan cites and for an entry the parser had to drop -
# see `faults`. Every Part I call site (and its `isinstance`) reads unchanged.
SourceFault = ConfigFault

# The zone a schedule.yml that names none is read in, and where a release lands when its
# deploy entry names no semester repo: both the institution's (policy.yml).
DEFAULT_TZ = policy.defaults()["timezone"]
DEFAULT_DEST_REPO = policy.defaults()["semester_dest_repo"]

# What a release SYNTHESISED from `assignments.<slug>.handout_datetime` is labelled:
# `<slug>-handout`. There is no such entry in any file - `scheduler._handout_releases`
# invents it so the handout fires through the `releases:` machinery, and `cadence` reads the
# slug back out of the label to report a late handout against the assignment block faculty
# would actually edit. Written once here because the two sides must agree exactly: spelled
# separately, a rename would leave cadence reporting `releases.<slug>-handout`, a field that
# exists nowhere.
HANDOUT_SUFFIX = "-handout"


# --------------------------------------------------------------------------- pure core


def _tz(name: str | None) -> ZoneInfo:
    """Resolve a timezone name, falling back to the default if it's missing/unknown."""
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


# The date-level coercion (semester bounds, whole-day events) is the shared canonical one
# in `course` - `active_today` uses the same, so the two can never drift. Aliased under the
# module's historical private name for its internal callers (and the tests that pin it).
_coerce_date = coerce_date


def _coerce_datetime(
    value: object, tz: ZoneInfo, *, end_of_day: bool = False
) -> datetime | None:
    """A YAML datetime/date or ISO string -> a datetime in the semester timezone `tz` (None
    if unparseable). A bare date has no time, so it becomes start-of-day (00:00) or, when
    `end_of_day`, 23:59:59.

    A naive datetime is stamped with `tz`; one written with an explicit offset
    (`...T10:00+00:00`) names the same instant, and is CONVERTED to `tz` here - so every
    datetime this module hands out is already the semester's wall clock. Instant-preserving,
    so firing and sorting are untouched; what it buys is that no consumer has to re-derive
    the semester zone to display a time (the site used to thread `tz` through every renderer
    to convert at print time, and a consumer that forgot printed 10:00 for a class that
    happens at 12:00)."""

    def _from_date(d: date) -> datetime:
        return datetime.combine(d, time(23, 59, 59) if end_of_day else time(0, 0))

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):  # bare YAML date (no time component)
        dt = _from_date(value)
    elif isinstance(value, str):
        s = value.strip()
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            d = _coerce_date(s)
            if d is None:
                return None
            dt = _from_date(d)
        else:
            # A date-only string parses to 00:00 - honour end_of_day for it too.
            if end_of_day and "T" not in s and ":" not in s:
                dt = _from_date(dt.date())
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)  # same instant, expressed in the semester's own clock


def _coerce_date_or_datetime(value: object, tz: ZoneInfo) -> date | datetime | None:
    """A whole-day value -> `date`; one that carries a time -> a `datetime` in the semester
    timezone (coerced exactly like a release `when`: naive is stamped with `tz`, an
    explicit offset is converted to `tz`). Keeping the two distinct is what lets a reader
    tell "no time was given" from "midnight" - the website renders a placeholder time for
    the former."""
    if isinstance(value, datetime) or (
        isinstance(value, str) and ("T" in value or ":" in value)
    ):
        return _coerce_datetime(value, tz)
    return _coerce_date(value)


def _instant(value: date | datetime, tz: ZoneInfo) -> datetime:
    """A sortable tz-aware instant for a value that may be whole-day or timed. Mixing the
    two in one list is otherwise unsortable (`date` and `datetime` don't compare, nor do
    naive and aware ones); a whole-day value sorts at the start of its day in `tz`."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=tz)
    return datetime.combine(value, time(0, 0), tzinfo=tz)


@dataclass
class Deploy:
    """One source->dest copy: a path in a COURSE-org source repo copied into a SEMESTER-org
    dest repo. `semester_dest_path` defaults to `course_source_path` (mirror).

    `deploy_datetime` optionally overrides the copy's own ship time; unset, it ships at
    the parent entry's `event_datetime`. This is what disaggregates the class from its
    materials: the entry's `event_datetime` is the session the site announces, a deploy's
    `deploy_datetime` ships the files an hour (or a week) before or after it."""

    course_source_repo: str
    course_source_path: str
    semester_dest_repo: str = DEFAULT_DEST_REPO
    semester_dest_path: str | None = None
    deploy_datetime: datetime | None = None
    # The line each of this copy's keys is written on, as the loader saw them (see
    # `_LineLoader`), plus "" for the line the copy itself opens on. A fault cites the
    # line of the FIELD it names (`_line_of`), because that is the line to go and edit -
    # and `course_source_repo` and `course_source_path` are two different lines of the
    # same copy. Out of `==` and `repr`, so two Deploys are equal when the COPY is,
    # whatever lines each was typed on.
    lines: dict[str, int] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Release:
    """A labelled scheduled entry: when the thing HAPPENS (`event_datetime` in the YAML),
    plus, optionally, `deploy` actions (content copies) - or an `assignment` handout
    synthesised by the scheduler from `assignments.<slug>.handout_datetime`.

    `when` holds the entry's `event_datetime`: what the semester site's schedule shows AND
    the default fire time for its deploys. An individual deploy may carry its own
    `deploy_datetime` to ship earlier or later than the session it belongs to. An entry
    with no actions at all fires nothing, but it is not invisible: a numbered label still
    raises its session row on the site, marked not-yet-released, so a term written up
    front reads as a syllabus from day one. `events:` is for a row that is not a numbered
    session at all."""

    label: str
    # None = the event_datetime is literally `tbc`: the site shows a TBC row and nothing
    # can fire until faculty replace it with a real date.
    when: datetime | None
    deploy: list[Deploy] = field(default_factory=list)
    assignment: str | None = None
    # The SCHEDULE KEY this handout belongs to, on a synthesised assignment release.
    # `assignment` names the course-org TEMPLATE, and two entries may hand out from one
    # template (each with its own `semester_dest_repo`) - so the template alone no longer
    # says which assignment is firing, and the key travels with the release rather than
    # being looked up again at the far end.
    assignment_slug: str = ""
    # True on a release synthesised from `assignments.<slug>.solution_datetime`: the same
    # provisioning call, asked additionally to push the template's `solution/` into every
    # repo it already made. Never set from the YAML - the scheduler owns it.
    assignment_solution: bool = False
    title: str = ""  # display-only: the session's name, beside its ordinal on the site
    # display-only: a sentence about the session. `title` names the session, this says
    # what is in it - so it fills the schedule table's Details column, and renders again
    # under the session's heading on the Lectures/Labs/Readings tabs. One word per column:
    # `title` is the Title cell everywhere, `details` the Details cell everywhere.
    details: str = ""
    # Which schedule row this entry belongs to: 'lecture' | 'lab' | 'readings', or ""
    # to infer it from where the deploys LAND, which is what every semester relies on today.
    # An override for an entry whose destination path cannot say - materials that belong
    # to a lab but do not land under `labs/`. It travels with the DESTINATION
    # (`schedule_plan.dest_row_kind`), so the plan and the discovered folder place the
    # same row rather than one each.
    # 'readings' names no row of its own, exactly as a `readings-N` label does.
    kind: str = ""
    # `tbc: true` next to a REAL date = a provisional sketch: everything fires at that
    # date as normal, but the site marks it "(TBC)" to signal it may still move.
    tbc: bool = False
    # `show_on_site: false` = a SILENT release: it deploys exactly as written, but tells
    # the site's schedule nothing - no date, no title, no not-yet-released placeholder.
    # For content that ships against a session without being an occasion of its own, a
    # session's readings being the case it exists for: they land in the same site row as
    # that session's lecture, and an entry dated a week earlier than the class would
    # otherwise pull the row's date - and its name - back to the day the PDFs went up.
    # Default true: an entry says what it is on the schedule unless faculty opt out.
    show_on_site: bool = True

    @property
    def is_event_only(self) -> bool:
        """No actions - nothing for the SCHEDULER to fire. The site still shows the row
        (see the class docstring); this is about firing, not visibility."""
        return not self.deploy and not self.assignment

    def due_deploys(self, now: datetime) -> list[Deploy]:
        """The deploys whose own ship time (`deploy_datetime`, else this entry's
        `event_datetime`) has arrived. An undated (TBC) entry's deploys can never be due -
        except one carrying its own explicit `deploy_datetime`."""
        return [
            d
            for d in self.deploy
            if (d.deploy_datetime or self.when) is not None
            and (d.deploy_datetime or self.when) <= now
        ]


@dataclass
class AssignmentEntry:
    """One assignment's TIMING, and nothing else: `handout_datetime` (when student/team
    repos are provisioned), `due_datetime` (what students see), `solution_datetime` (when
    the model solution goes out), `marks_return_datetime` (when marks go back). The late
    cutoff is never written: it is `grading_cutoff_datetime`, due + `late_window_days`.

    What the assignment IS - its shape, its team cap, how it is handed in, how it is
    marked - lives in the assignment's own `grading_config.yml`, on the course template's
    solution branch. The two files were both allowed to declare the shape, and a semester
    that said one thing while the template said another got repos of one kind graded as
    the other."""

    due_datetime: datetime
    # The COURSE-org repo this assignment hands out from - the template one repo per
    # student (or per team) is generated from. Required and named outright: it used to be
    # derived as `<slug>-<semester tag>`, which was right almost always and invisible in the
    # file that depended on it. Same meaning as a deploy's `course_source_repo`.
    course_source_repo: str
    # What the SEMESTER-side artefacts are called - the frozen semester template repo, the
    # `<name>-<handle>` student repos, the teams.csv key, the snapshot and grades files.
    # None = the entry's slug, which is almost always right. Declared in `assignments.yml`
    # (`assignments.<key>.semester_dest_repo`), a run fact rather than a timing; `parse`
    # is handed it.
    semester_dest_repo: str | None = None
    # When to provision one repo per student (or per team - see `type`) from the
    # `<slug>-<tag>` template. The scheduler synthesises a release from this, so it fires
    # exactly like a `releases` entry. None = hand out manually (the workflow
    # then records the release moment here).
    handout_datetime: datetime | None = None
    # Display-only: a sentence about the assignment, filling the Details column of both
    # its schedule rows (out and due) exactly as a `releases:` entry's does on a session
    # row. It is written ABOVE what those cells already generate (the link to the brief,
    # the "submit via" address), never instead of it.
    details: str = ""
    # `tbc: true` = a provisional deadline: the site marks both rows "(TBC)". DISPLAY-ONLY
    # and nothing else - the snapshot freeze, the late window and the grading cutoff all
    # derive from `due_datetime`, which this does not touch. An assignment cannot be
    # undated the way a release can (`due_datetime` is required), so there is no
    # `event_datetime: tbc` twin here: this marks a real date as still moveable.
    tbc: bool = False
    # `show_on_site: false` = the assignment runs exactly as written - handed out, due,
    # snapshotted, graded - and the semester site says nothing about it: no schedule row, no
    # due row, no Assignments-tab entry. The twin of a silent release, for an assignment
    # announced somewhere other than the site.
    show_on_site: bool = True
    # When to push the template's `solution/` folder into every provisioned repo - the
    # scheduled twin of Release assignment's `solution_datetime: now`. Deliberately NOT
    # defaulted to the due date: a solution released the moment submissions close is a
    # gift to anyone who pushes late, so faculty name the moment or it never fires.
    # None = release the solution by hand, or not at all.
    solution_datetime: datetime | None = None
    # When automation returns the marks (`scheduler`): only once every unit is marked, and
    # until then a problem that says how many are not. None = marks go back by hand.
    marks_return_datetime: datetime | None = None
    # Whether the site shows a "marks expected" row for it: only when the key is written
    # `{event_datetime: ..., show_on_site: true}` - the date is internal by default.
    marks_return_on_site: bool = False
    # The line each of this entry's keys is written on - see `Deploy.lines`.
    lines: dict[str, int] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Event:
    """A display-only calendar row: an exam, or any other session the semester should see
    on the schedule but which releases nothing (a guest lecture, a project clinic).
    Nothing here ever fires - the site renders the row and that is all."""

    label: str
    title: str
    # A bare date = whole day; a datetime = real start time; None = `event_datetime: tbc`
    # (the site shows a TBC row). `tbc: true` next to a real date = provisional, "(TBC)".
    when: date | datetime | None
    # 'exam' | 'special_event'. Exams render as their own (red) row on the site.
    kind: str = "special_event"
    tbc: bool = False
    # Display-only: the row's Details cell. `title` says which row this is, `details` what
    # there is to say about it - the same two words, and the same two columns, as on a
    # session row and on an assignment's.
    details: str = ""
    # `show_on_site: false` = the row stays in the plan and off the schedule. An event
    # fires nothing, so this is the whole of what it does here; the twin of a silent
    # release, for a date faculty keep in the file without publishing it.
    show_on_site: bool = True


@dataclass
class ArchiveRow:
    """The optional `archive:` block - when this semester freezes read-only, and what the
    site says about it.

    A row like an `events:` one, modelled like one: it carries the same display vocabulary
    (`title`, `details`, `show_on_site`, `tbc`) over a date of its own. It was six parallel
    `archive_*` fields on `Schedule` and a six-element tuple out of the parser, where the
    seventh key would have cost six edits.

    Its PRESENCE is the switch. `Schedule.archive` is None for a semester that wrote no
    block at all, and that semester is never frozen automatically. A block written but
    undatable is an ArchiveRow with `when=None`: it asked, and there is no clock to freeze
    it against. `scheduler._no_archive_date` tells those two apart, because they need
    different sentences."""

    # The day the scheduler acts on. None = the block was written and no date can be
    # derived from it, which is a different thing from no block at all.
    when: date | None = None
    show_on_site: bool = True
    # The row's Title cell. A field with a default rather than a fixed string in the
    # renderer, because `archive:` takes the same `title:`/`details:` pair as every other
    # block and faculty may name the day whatever their programme calls it ("Semester
    # closed", "Repositories frozen").
    title: str = ARCHIVE_TITLE
    # What the site's archive row and its Updates box SAY - the whole of it, because the
    # toolkit writes no sentence of its own here. None leaves the row as its title and
    # date and the Updates bullet unwritten (`site._archive_entry`); the seeded skeleton
    # carries a sentence ready to uncomment.
    details: str | None = None
    # `tbc: true` = the freeze date is provisional and the row says so. Display-only: the
    # date the scheduler acts on is `when`, which this does not touch.
    tbc: bool = False


@dataclass
class Schedule:
    timezone: str = DEFAULT_TZ
    # The semester org it was loaded from ("" for a plan parsed from text): what an
    # assignment's run settings resolve against (`settings`, `grades.load_grading_spec`).
    org: str = field(default="", compare=False)
    releases: list[Release] = field(default_factory=list)
    semester_start: date | None = None
    semester_end: date | None = None
    assignments: dict[str, AssignmentEntry] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    # The `archive:` block, or None where the semester wrote none - see `ArchiveRow`, which
    # holds every key of it. None IS "nobody asked for archiving", which is a different
    # answer from an ArchiveRow whose `when` could not be derived. Resolved by `parse` and
    # never re-derived downstream: `archive.event_datetime` where the block names one,
    # else `semester_end + ARCHIVE_GRACE` - which is what stops a term with no dates
    # freezing off a guess.
    archive: ArchiveRow | None = None
    # Everything this parse could not use, one human-readable line each, naming the YAML
    # path and what it costs the semester: entries thrown away outright (`_drop` - no date,
    # no source), and entries KEPT but not as written (`_flag_unknown_keys` for a stray
    # key, `_flag_bad_value` for a value that had to fall back). None of it may vanish
    # quietly: `load` logs each line, `--validate` exits non-zero on them, and Check semester
    # setup counts them.
    dropped: list[str] = field(default_factory=list)
    # The same leftovers as `ConfigFault`s - what the digest issue lists and the mail
    # names, one per line of `dropped`. Two shapes of one fact, built together: see
    # `Drops`. A dropped entry is an IMMEDIATE fault (no `fires`), because the entry is
    # already not in the plan - there is no moment it is about to bite at.
    faults: list[ConfigFault] = field(default_factory=list)
    # Set by `load` when the file could not be read AS A SCHEDULE at all: the YAML did not
    # parse, or its top level is not a mapping. Distinct from `dropped` (a file that parsed,
    # minus some entries) and from a semester that simply has no schedule.yml. `load` still
    # returns an empty Schedule so nothing downstream raises, and files the one fault that
    # says so in `faults` - an unreadable plan means NOTHING is released, handed out,
    # snapshotted or graded for the semester, and the digest issue and the mail beside it are
    # how the person who has to fix it hears about that.
    unparseable: bool = False
    # Set by `load` when `assignments.yml` is not YAML: its run settings are unknown, so
    # nothing that reads one (a cutoff, a hand out) can act for this semester until it is
    # fixed. The fault is in `faults` like any other.
    instance_unparseable: bool = False


@dataclass
class Drops:
    """Everything one parse could not use, in the two shapes it has to take.

    `report` is the human-readable line the run summary prints, `--validate` fails on and
    Check semester setup counts - unchanged, because faculty read it and three surfaces
    quote it. `faults` is the same fact as a `ConfigFault`, which is what the digest issue
    and the mail carry: a dropped entry used to reach people only through a red X and a
    hand-rolled issue on the push, so an entry dropped by an hourly tick reached nobody.

    Built together, from one call, because a fault the report does not mention (or the
    other way round) is the two channels disagreeing about what the file says.

    `top` is the top-level key lines, for a fault about a whole BLOCK (`releases:` written
    as a list): the block's own entries carry their lines, and the block itself does
    not."""

    report: list[str] = field(default_factory=list)
    faults: list[ConfigFault] = field(default_factory=list)
    top: dict[str, int] = field(default_factory=dict)

    def _add(
        self,
        report: str,
        where: str,
        field_name: str,
        what: str,
        lines: dict[str, int] | None,
        code: str = "",
    ) -> None:
        self.report.append(report)
        self.faults.append(
            ConfigFault(
                where,
                what,
                file=SCHEDULE_PATH,
                field=field_name,
                lineno=line_of(lines if lines is not None else self.top, field_name),
                code=code,
            )
        )

    def note(
        self,
        where: str,
        field_name: str,
        what: str,
        lines: dict[str, int] | None = None,
        code: str = "",
    ) -> None:
        """A fault with its own wording - a key that MOVED to another file, a timezone
        that is not a zone. The two below are the shapes every other fault takes."""
        loc = f"{where}.{field_name}" if where else field_name
        self._add(f"{loc}: {what}", where, field_name, what, lines, code)


def _drop(
    drops: Drops,
    where: str,
    why: str,
    cost: str,
    lines: dict[str, int] | None = None,
    field_name: str = "",
) -> None:
    """Record a thrown-away entry: where it is in the YAML, what is wrong, and what the
    semester loses by it. The cost is the point - "entry dropped" alone tells faculty
    nothing about whether their term still runs."""
    what = f"{why} - entry dropped, so {cost}"
    drops._add(f"{where}: {what}", where, field_name, what, lines)


def _require_mapping(
    raw: object, drops: Drops, block: str, noun: str, cost: str
) -> dict | None:
    """A top-level `releases:`/`assignments:`/`events:` block must be a `label -> entry`
    mapping. Returns it, or None when it is absent (nothing to parse) or authored as a
    list/scalar - the latter recorded as a drop rather than left to raise on `.items()`,
    which would break `load`'s never-raise contract (a list is the common mistake, since
    `deploy:` nested below IS a list)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _drop(
            drops,
            block,
            f"not a mapping (it must be {noun} -> entry, not a list or value)",
            cost,
            field_name=block,
        )
        return None
    # The block's own lines interest nobody; taking them keeps the stamp out of the label
    # loop that follows, which would otherwise read `__lines__` as an entry.
    take_lines(raw)
    return raw


# The keys each schema level understands. Anything else - a typo (`grading_dateime:`), a
# legacy name (`dest_repo:`), or a whole plan under an unknown top-level key
# (`materials_releases:`) - is silently ignored by the parser and so means something other
# than what faculty wrote; `_flag_unknown_keys` surfaces it so `--validate` catches it.
KNOWN_TOP_LEVEL = frozenset(
    {
        "timezone",
        "releases",
        "semester_start",
        "semester_end",
        "assignments",
        "events",
        "archive",
    }
)
# Keys that went (decision 0009): never read, each a NOT_MIGRATED fault saying where the
# fact lives now. The migration deletes them.
RETIRED_TOP_KEYS = {
    "enrolment": "enrolment codes are mailed on a push to students.csv",
}
KNOWN_RELEASE = frozenset(
    {
        "event_datetime",
        "deploy",
        "kind",
        "title",
        "details",
        "tbc",
        "show_on_site",
    }
)
# The row kind's old key (decision 0012), on a release or an event: never read, noted as
# NOT_MIGRATED - the row is placed as if it declared no kind.
RENAMED_ROW_KEYS = {"type": "kind"}
# A release that handed out an assignment: an undocumented second route to what
# `assignments.<key>.handout_datetime` does. Noted and ignored; the entry still deploys.
RETIRED_RELEASE_KEYS = {
    "assignment": "an assignment hands out at its `assignments.<key>.handout_datetime`",
}
# What `releases.<label>.kind` may say. 'readings' is here and is not a row: it declares
# that the entry belongs to no session row of its own, exactly as a `readings-N` label
# does (`schedule_plan._LABEL_ROW_KINDS`, which is the one table both routes read).
KNOWN_ROW_KINDS = frozenset({"lecture", "lab", "readings"})
KNOWN_DEPLOY = frozenset(
    {
        "course_source_repo",
        "course_source_path",
        "semester_dest_repo",
        "semester_dest_path",
        "deploy_datetime",
    }
)
KNOWN_ASSIGNMENT = frozenset(
    {
        "due_datetime",
        "course_source_repo",
        "handout_datetime",
        "solution_datetime",
        "marks_return_datetime",
        "details",
        "tbc",
        "show_on_site",
    }
)
# Keys an assignment entry no longer takes (decision 0009: schedule.yml is timings only).
# The ENTRY is dropped, as for a renamed key: read without them it would grade to another
# cutoff or hand out into repos of another name, and say nothing.
RETIRED_ASSIGNMENT_KEYS = {
    "grading_datetime": (
        f"the late cutoff is the due date plus `late_window_days` ({ASSIGNMENTS_FILE})"
    ),
    "semester_dest_repo": (
        f"it is `assignments.<key>.semester_dest_repo` in {ASSIGNMENTS_FILE}"
    ),
    "cohort_dest_repo": (
        f"it is `assignments.<key>.semester_dest_repo` in {ASSIGNMENTS_FILE}"
    ),
}
RETIRED_COST = "entry dropped: no hand out, freeze or grading until fixed"
# Display-only, so the entry is KEPT and the key faulted: nothing it runs depends on it.
RETIRED_DISPLAY_KEYS = {
    "title": "the assignment's name is `title:` in its template's grading_config.yml",
}


def _retired(old: str, home: str) -> str:
    return f"{moved_text(old, home)} - {RETIRED_COST}"


# Settings that USED to live in an `assignments:` entry and now live in the assignment's
# own `grading_config.yml`, on the course template's solution branch. Flagged BY NAME
# rather than as generic unknown keys: a semester still carrying `type: group` is not making
# a typo, it is declaring something in a file that no longer reads it, and the message has
# to say where the declaration went.
_GRADING_CONFIG_HOME = "in the assignment's own grading_config.yml, on the course template's `solution` branch"
# key -> what ignoring it here costs. Where it MOVED TO is the same sentence for both and
# is built from the key at the flag site, so a third retired key cannot be filed under a
# home that names a different setting.
MOVED_ASSIGNMENT_KEYS = {
    "type": (
        "the assignment is handed out and graded in whatever shape grading_config.yml "
        "declares - individual when it declares none"
    ),
    "max_team_size": (
        "the 'Join team' flow uses the cap declared there, or the course default"
    ),
}
# Keys RENAMED (decision 0012), old -> new. Never read: an entry that still spells one is
# DROPPED as NOT_MIGRATED rather than half-read - a destination silently defaulted to the
# slug would hand out into a repo nobody named.
RENAMED_DEST_KEYS = {
    "cohort_dest_repo": "semester_dest_repo",
    "cohort_dest_path": "semester_dest_path",
}


def not_migrated_keys(
    drops: Drops,
    entry: dict,
    where: str,
    renames: dict[str, str],
    lines: dict[str, int] | None = None,
    say=not_migrated_text,
) -> bool:
    """Whether `entry` spells any old key, each noted as NOT_MIGRATED (`say(old, value)`
    words it: a rename by default, `moved_text` for a key that went). Called before
    `take_lines` (or handed its `lines`), so the note can cite the old key's line."""
    if lines is None:
        lines = entry.get(LINES) if isinstance(entry.get(LINES), dict) else {}
    found = [old for old in renames if old in entry]
    for old in found:
        drops.note(where, old, say(old, renames[old]), lines, NOT_MIGRATED)
    return bool(found)


KNOWN_EVENT = frozenset(
    {"kind", "title", "details", "event_datetime", "tbc", "show_on_site"}
)


def _flag_unknown_keys(
    drops: Drops,
    entry: dict,
    known: frozenset[str],
    where: str,
    cost: str,
    lines: dict[str, int] | None = None,
) -> None:
    """Record every key of `entry` not in `known`. Unlike `_drop`, the entry itself is
    KEPT (only the stray key is ignored) - a typo'd or legacy key otherwise passes
    validation while silently changing what the file means. Only called for entries that
    parse; a dropped entry already gets its own line."""
    for key in entry:
        if str(key) not in known:
            drops.note(where, str(key), f"unrecognised key - ignored, so {cost}", lines)


def _flag_bad_value(
    drops: Drops,
    where: str,
    key: str,
    value: object,
    cost: str,
    lines: dict[str, int] | None = None,
) -> None:
    """Record a key whose value is PRESENT but unusable - a date that doesn't parse, a cap
    that isn't a number, a `type:` that isn't one of the known ones.

    Like `_flag_unknown_keys` (and unlike `_drop`), the entry itself is KEPT: the parser
    falls back exactly as it always has. The fallback is the problem - it is invisible.
    `handout_datetime: 2026-13-01` reads as a scheduled handout and provisions nothing;
    `solution_datetime: nxt week` silently never ships. Both leave a green run
    and a plan that is not the one faculty wrote, so both belong in `dropped`."""
    drops.note(where, str(key), f"unusable value {value!r} - ignored, so {cost}", lines)


def _flagged_datetime(
    entry: dict,
    key: str,
    tz: ZoneInfo,
    drops: Drops,
    where: str,
    cost: str,
    lines: dict[str, int] | None = None,
    *,
    end_of_day: bool = False,
) -> datetime | None:
    """`entry[key]` as a datetime, flagging a value that is there but does not parse.

    An ABSENT key is a legitimate None everywhere this is used (hand out manually, grade
    at the due date, ship at the event) - only a value faculty actually wrote and we
    cannot read is a fault worth surfacing."""
    raw = entry.get(key)
    when = _coerce_datetime(raw, tz, end_of_day=end_of_day)
    if when is None and raw is not None:
        _flag_bad_value(drops, where, key, raw, cost, lines)
    return when


def _flagged_date(
    entry: dict,
    key: str,
    drops: Drops,
    where: str,
    cost: str,
    lines: dict[str, int] | None = None,
) -> date | None:
    """`entry[key]` as a whole-day date, flagging a value that is there but does not
    parse. The date-only twin of `_flagged_datetime`, with the same absent-vs-unreadable
    rule: no key means "not declared", which every caller handles."""
    raw = entry.get(key)
    when = _coerce_date(raw)
    if when is None and raw is not None:
        _flag_bad_value(drops, where, key, raw, cost, lines)
    return when


def _flagged_flag(
    entry: dict,
    key: str,
    default: bool,
    drops: Drops,
    where: str,
    cost: str,
    lines: dict[str, int] | None = None,
) -> bool:
    """`entry[key]` as a boolean, flagging a value that is there but is not one. The
    boolean twin of `_flagged_date`, with the same absent-vs-unusable rule: no key means
    "not declared", and `default` stands.

    NOT `entry.get(key) is not False` / `is True`, which is how all four of these were
    written. That form reads every value it cannot parse as the default, so the one thing
    the key exists to do silently does not happen - `show_on_site: "no"` publishes the row
    it was written to hide. YAML spells both booleans several ways and they all arrive
    here as a bool; anything else - `"nope"`, `0`, a list - is a hand edit that did not
    take, and is flagged like an unreadable date. The default still stands, because that
    is what the file said before the edit, and the fault is now visible to whoever made
    it."""
    raw = entry.get(key)
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        _flag_bad_value(drops, where, key, raw, cost, lines)
    return default


# What an unusable `tbc:` costs, wherever one is written: the same key on four blocks,
# doing the same one display-only thing on all of them.
TBC_COST = 'the row is not marked "(TBC)"'
# The same, for `details:`: one key on four blocks, filling one column on all of them.
DETAILS_COST = "the row shows no sentence at all - only what its own cell generates"


def _flagged_details(
    entry: dict,
    drops: Drops,
    where: str,
    lines: dict[str, int] | None = None,
) -> str | None:
    """`entry`'s optional `details:` - the prose that fills its row's Details column.

    Anything that is not a STRING is FLAGGED and dropped, never raised and never printed:
    a list or a mapping here would otherwise reach the deployed site as `['a', 'b']`. The
    row then reads as it does for a semester that wrote no `details:` at all - its title and
    its date - which is a hand edit that visibly did not take, and that is what `dropped`
    is for.

    A BLANK string is not that. It is an empty slot - the shape a file carries for prose
    nobody has written yet - so it reads as absent, and is never reported.

    ONE guard for all four blocks that take the key, like `_flagged_flag` above: the word
    means the same thing wherever it is written, so it has to be READ the same way
    wherever it is written. Guarding one block is three blocks on which the mapping still
    ships."""
    said = entry.get("details")
    if said is None:
        return None
    if isinstance(said, str):
        # Blank reads as absent, NOT as a mistake. `details:` with nothing after it is how
        # a schedule.yml carries a slot for prose somebody has yet to write, and a bare key
        # and a `""` are the same intention typed two ways - so flagging one and not the
        # other emailed faculty about a difference they could not see.
        return said if said.strip() else None
    _flag_bad_value(drops, where, "details", said, DETAILS_COST, lines)
    return None


def _parse_deploy(raw: object, tz: ZoneInfo, drops: Drops, label: str) -> list[Deploy]:
    """Parse a release's `deploy:` - a list (or a single mapping) of source->dest copies.
    Entries missing course_source_repo/course_source_path are skipped (nothing to copy).
    A malformed `deploy_datetime` parses to None - the copy ships at the entry's
    event_datetime, and the unusable value is flagged (see `_flag_bad_value`)."""
    items = [raw] if isinstance(raw, dict) else (raw or [])
    out: list[Deploy] = []
    for i, d in enumerate(items):
        where = f"releases.{label}.deploy[{i}]"
        if not isinstance(d, dict):
            _drop(drops, where, "not a mapping", "this copy never ships")
            continue
        if not_migrated_keys(drops, d, where, RENAMED_DEST_KEYS):
            continue
        lines = take_lines(d)
        src_repo, src_path = d.get("course_source_repo"), d.get("course_source_path")
        if not src_repo or not src_path:
            _drop(
                drops,
                where,
                "missing `course_source_repo` and/or `course_source_path`",
                "this copy never ships",
                lines,
                "course_source_path",
            )
            continue
        dest_path = d.get("semester_dest_path")
        _flag_unknown_keys(
            drops,
            d,
            KNOWN_DEPLOY,
            where,
            "that setting is ignored for this copy",
            lines,
        )
        out.append(
            Deploy(
                course_source_repo=str(src_repo),
                course_source_path=str(src_path),
                semester_dest_repo=str(
                    d.get("semester_dest_repo") or DEFAULT_DEST_REPO
                ),
                semester_dest_path=str(dest_path) if dest_path else None,
                deploy_datetime=_flagged_datetime(
                    d,
                    "deploy_datetime",
                    tz,
                    drops,
                    where,
                    "this copy ships at the entry's `event_datetime` instead of the "
                    "time written here",
                    lines,
                ),
                lines=lines,
            )
        )
    return out


def _is_tbc(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() == "tbc"


def _parse_releases(raw: object, tz: ZoneInfo, drops: Drops) -> list[Release]:
    """Parse `releases:` (label -> {event_datetime + deploys}) into Releases sorted by
    their event_datetime.

    TBC: `event_datetime: tbc` keeps the entry as an UNDATED site row (when=None -
    nothing can fire); `tbc: true` next to a real date keeps everything firing but marks
    the site row "(TBC)". An entry with no date and no tbc can never fire or be shown,
    so it's dropped.

    `type:` is an OPTIONAL override of which row the entry belongs to. Left out - which is
    every semester today - the row is placed by where the deploys land, unchanged. A value
    that is not a known row type is flagged and ignored rather than dropping the entry:
    the cost of a typo here is a row in the wrong column, never a session missing from the
    schedule."""
    out: list[Release] = []
    mapping = _require_mapping(
        raw, drops, "releases", "label", "the whole release plan is ignored"
    )
    if mapping is None:
        return out
    for label, entry in mapping.items():
        where = f"releases.{label}"
        if not isinstance(entry, dict):
            _drop(
                drops, where, "not a mapping", "nothing deploys and no site row appears"
            )
            continue
        lines = take_lines(entry)
        raw_when = entry.get("event_datetime")
        when = _coerce_datetime(raw_when, tz)
        marked = _flagged_flag(entry, "tbc", False, drops, where, TBC_COST, lines)
        tbc = marked or _is_tbc(raw_when)
        if when is None and not tbc:
            _drop(
                drops,
                where,
                "no valid `event_datetime` (use `tbc` if the date is not settled)",
                "nothing deploys and no site row appears",
                lines,
                "event_datetime",
            )
            continue
        not_migrated_keys(drops, entry, where, RENAMED_ROW_KEYS, lines)
        not_migrated_keys(
            drops, entry, where, RETIRED_RELEASE_KEYS, lines, say=moved_text
        )
        _flag_unknown_keys(
            drops,
            entry,
            KNOWN_RELEASE
            | frozenset(RENAMED_ROW_KEYS)
            | frozenset(RETIRED_RELEASE_KEYS),
            where,
            "that setting is ignored",
            lines,
        )
        # `deploy:` written with nothing under it. YAML reads that as None, which is
        # indistinguishable from the key being ABSENT once it reaches _parse_deploy - and
        # absent is legitimate (a display-only session row). Only here can the two be told
        # apart, so the flag lives here. Silently coercing it to [] meant an instructor who
        # wrote the key and then never filled it got a row that quietly ships nothing, with
        # the truncated block looking for all the world like it should deploy.
        #
        # `None` only, NOT a falsy test: an explicit `deploy: []` is someone saying "no
        # copies" in as many words, and is left alone.
        if "deploy" in entry and entry["deploy"] is None:
            _flag_bad_value(
                drops,
                where,
                "deploy",
                entry["deploy"],
                "this entry ships NOTHING and is a display-only row - fill the deploy "
                "in, or delete the key to say that is what you meant",
                lines,
            )
        kind = str(entry.get("kind") or "").strip().lower()
        if kind and kind not in KNOWN_ROW_KINDS:
            # Flagged, not dropped, and not obeyed: the entry keeps its row, placed by
            # where its files land exactly as an entry that declared no type at all. A
            # typo'd override must not be able to take a session off the schedule.
            _flag_bad_value(
                drops,
                where,
                "kind",
                kind,
                "the row is placed by where its deploys land, as if no kind were "
                f"declared (expected one of {', '.join(sorted(KNOWN_ROW_KINDS))})",
                lines,
            )
            kind = ""
        if kind == "readings":
            # `type: readings` says the entry claims no row of its own, so its display
            # text has nowhere to render: the session's row is named and described by the
            # entry that RAISES it, and a readings entry only folds its destinations in.
            # Kept-but-ignored, like an unknown key or an unusable value - the entry
            # deploys exactly as written. Flagged rather than swallowed because faculty
            # writing a title here are not making a typo: docs/07 offers both fields on
            # any `releases:` entry, so they are writing prose they expect to read on the
            # schedule, and an empty cell is the one outcome they cannot tell apart from
            # a rendering bug.
            for key in ("title", "details"):
                if str(entry.get(key) or "").strip():
                    drops.note(
                        where,
                        key,
                        "ignored, because a `type: readings` entry claims no row of its "
                        "own - so nothing on the site shows this. Write it on the entry "
                        "that raises the session's row instead",
                        lines,
                    )
        out.append(
            Release(
                label=str(label),
                when=when,
                deploy=_parse_deploy(entry.get("deploy"), tz, drops, str(label)),
                kind=kind,
                title=str(entry.get("title") or ""),
                details=_flagged_details(entry, drops, where, lines) or "",
                tbc=tbc,
                show_on_site=_flagged_flag(
                    entry,
                    "show_on_site",
                    True,
                    drops,
                    where,
                    "this entry's session row is shown on the site anyway",
                    lines,
                ),
            )
        )
    # Undated (TBC) entries sort to the end of the plan.
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda r: (r.when is None, r.when or epoch))
    return out


def _shared_sources(mapping: dict, dests: dict[str, str]) -> set[str]:
    """The `course_source_repo`s more than one assignment may legitimately hand out from.

    Two entries on one template is normally a copy-paste, and nothing downstream can tell
    them apart. It IS legitimate when both say, explicitly, what their semester-side repos
    are called - a resit off the same brief, or one template handed out to two halves of a
    semester - because `semester_dest_repo` is what every artefact keys on, and two explicit
    ones cannot collide (`_parse_assignments` refuses that separately). One entry leaving
    it to default is enough to make the pair ambiguous again, so the permission is
    all-or-nothing across the citing entries."""
    citing: dict[str, list[str]] = {}
    for slug, entry in mapping.items():
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("course_source_repo") or "").strip()
        if source:
            citing.setdefault(source, []).append(dests.get(str(slug), ""))
    return {src for src, dests in citing.items() if len(dests) > 1 and all(dests)}


def _parse_assignments(
    raw: object, tz: ZoneInfo, drops: Drops, dests: dict[str, str]
) -> dict[str, AssignmentEntry]:
    # Only the nested {due_datetime, ...} form is accepted - matching the one schema
    # documented everywhere - rather than also silently accepting a bare due-date scalar.
    # A malformed `handout_datetime`/`solution_datetime`/`marks_return_datetime` keeps the
    # entry on its documented fallback, and is flagged (see `_flag_bad_value`). `dests` is
    # each key's `semester_dest_repo`, from assignments.yml.
    out: dict[str, AssignmentEntry] = {}
    cost = "no deadline for students, no submission snapshot and no autograding"
    mapping = _require_mapping(
        raw,
        drops,
        "assignments",
        "slug",
        "no assignment has a deadline, snapshot or autograding",
    )
    if mapping is None:
        return out
    sources: dict[str, str] = {}  # course_source_repo -> the slug that claimed it
    names: dict[str, str] = {}  # semester-side name -> the slug that claimed it
    mapping = {
        slug: entry
        for slug, entry in mapping.items()
        if not (
            isinstance(entry, dict)
            and not_migrated_keys(
                drops,
                entry,
                f"assignments.{slug}",
                RETIRED_ASSIGNMENT_KEYS,
                say=_retired,
            )
        )
    }
    shared = _shared_sources(
        mapping, dests
    )  # sources every citing entry names a dest for
    for slug, entry in mapping.items():
        where = f"assignments.{slug}"
        if not isinstance(entry, dict):
            _drop(
                drops, where, "not a mapping (it needs a nested `due_datetime:`)", cost
            )
            continue
        lines = take_lines(entry)
        due = _coerce_datetime(entry.get("due_datetime"), tz, end_of_day=True)
        if due is None:
            _drop(drops, where, "no valid `due_datetime`", cost, lines, "due_datetime")
            continue
        source_repo = str(entry.get("course_source_repo") or "").strip()
        if not source_repo:
            _drop(
                drops,
                where,
                "no `course_source_repo`",
                cost,
                lines,
                "course_source_repo",
            )
            continue
        if source_repo in sources and source_repo not in shared:
            # A copy-paste (Maths f2026 had assignments 3 and 4 both citing assignment-2's
            # repo). Nothing downstream can tell the two apart: the handout would "skip"
            # the other assignment's existing repos and hand out nothing, then the
            # autograder would re-grade the other assignment every hour under this key.
            _drop(
                drops,
                where,
                f"`course_source_repo: {source_repo}` is already used by "
                f"assignments.{sources[source_repo]} - two assignments may only hand out "
                f"the same repo when EVERY one of them sets its own `semester_dest_repo` "
                f"in {ASSIGNMENTS_FILE} (a copy-paste?)",
                cost,
                lines,
                "course_source_repo",
            )
            continue
        dest = dests.get(str(slug), "")
        # `semester_name` - `semester_dest_repo`, else the slug - is what EVERY semester-side
        # artefact keys on: the generated repos, the teams.csv rows, the snapshot, the
        # autograde marker, the grading sheet. Two entries resolving to one name share all
        # of them silently: the second handout finds the first's repos and "skips" them,
        # then both assignments read and freeze the same snapshot under one another's
        # marks. Refused like a duplicate source, and for the same reason - nothing
        # downstream can tell the two apart.
        name = dest or str(slug)
        if name in names:
            _drop(
                drops,
                where,
                f"`{name}` is the semester-side name of assignments.{names[name]} too - "
                f"two assignments cannot share one (the student repos, teams.csv rows, "
                f"snapshot and grading sheet all key on it; set a distinct "
                f"`semester_dest_repo` in {ASSIGNMENTS_FILE})",
                cost,
                lines,
                "course_source_repo",
            )
            continue
        sources[source_repo] = str(slug)
        names[name] = str(slug)
        for moved, moved_cost in MOVED_ASSIGNMENT_KEYS.items():
            if moved in entry:
                drops.note(
                    where,
                    moved,
                    f"moved to `{moved}:` {_GRADING_CONFIG_HOME} - ignored here, so "
                    f"{moved_cost}",
                    lines,
                )
        not_migrated_keys(
            drops, entry, where, RETIRED_DISPLAY_KEYS, lines, say=moved_text
        )
        _flag_unknown_keys(
            drops,
            entry,
            KNOWN_ASSIGNMENT
            | frozenset(MOVED_ASSIGNMENT_KEYS)
            | frozenset(RETIRED_DISPLAY_KEYS),
            where,
            "that setting is ignored",
            lines,
        )
        handout = _flagged_datetime(
            entry,
            "handout_datetime",
            tz,
            drops,
            where,
            "the handout NEVER fires - no student or team repos are provisioned "
            "from it, and nobody gets the assignment",
            lines,
        )
        solution = _flagged_datetime(
            entry,
            "solution_datetime",
            tz,
            drops,
            where,
            "the model solution NEVER ships automatically - it stays on the "
            "template's solution branch until someone hands it out by hand with `solution_datetime: now`",
            lines,
        )
        # The solution rides on the handout release, so these two dates are only meaningful
        # relative to each other - and both ways of getting that wrong are silent, which is
        # why they are checked here rather than left to the scheduler.
        #
        # REFUSED, not merely flagged, because the failure is not symmetrical: a date
        # earlier than the handout would push the model solution into every student repo on
        # the very first firing, shipping the answers WITH the questions, and no later run
        # can take that back. Dropping to None is the documented "omit it" behaviour - the
        # solution then waits for a human, which is the safe direction to fail.
        if solution is not None and handout is None:
            _flag_bad_value(
                drops,
                where,
                "solution_datetime",
                entry.get("solution_datetime"),
                "the model solution NEVER ships automatically - the schedule can only push "
                "it into repos it provisioned, which needs `handout_datetime` set too",
                lines,
            )
            solution = None
        elif solution is not None and handout is not None and solution <= handout:
            _flag_bad_value(
                drops,
                where,
                "solution_datetime",
                entry.get("solution_datetime"),
                "it is not AFTER handout_datetime, which would ship the model solution "
                "together with the assignment on the very first release - refused, so the "
                "solution now waits for a human",
                lines,
            )
            solution = None
        # A date, or `{event_datetime, show_on_site}` when the site is to show it: the
        # row is internal by default and has its own switch (decision 0009).
        raw_marks = entry.get("marks_return_datetime")
        marks_row = isinstance(raw_marks, dict)
        marks_on_site = False
        if marks_row:
            marks_on_site = _flagged_flag(
                raw_marks,
                "show_on_site",
                False,
                drops,
                f"{where}.marks_return_datetime",
                "the marks row stays off the site",
                lines,
            )
        marks = _flagged_datetime(
            {"marks_return_datetime": raw_marks.get("event_datetime")}
            if marks_row
            else entry,
            "marks_return_datetime",
            tz,
            drops,
            where,
            "marks are not returned automatically - they go back when somebody runs "
            "Return marks",
            lines,
            end_of_day=True,
        )
        if marks is not None and marks <= due:
            _flag_bad_value(
                drops,
                where,
                "marks_return_datetime",
                entry.get("marks_return_datetime"),
                "it is not AFTER due_datetime, so marks would go back before the work "
                "is in - refused, so marks go back when somebody runs Return marks",
                lines,
            )
            marks = None
        out[str(slug)] = AssignmentEntry(
            due_datetime=due,
            course_source_repo=source_repo,
            semester_dest_repo=dest or None,
            details=_flagged_details(entry, drops, where, lines) or "",
            # Display-only, and deliberately read nowhere near the dates above: an
            # assignment marked provisional still freezes, closes and grades on exactly
            # the moments `due_datetime` and the late cutoff name.
            tbc=_flagged_flag(entry, "tbc", False, drops, where, TBC_COST, lines),
            show_on_site=_flagged_flag(
                entry,
                "show_on_site",
                True,
                drops,
                where,
                "this assignment's schedule rows are shown on the site anyway",
                lines,
            ),
            handout_datetime=handout,
            solution_datetime=solution,
            marks_return_datetime=marks,
            marks_return_on_site=marks_on_site,
            lines=lines,
        )
    return out


def _parse_events(raw: object, tz: ZoneInfo, drops: Drops) -> list[Event]:
    """Parse `events:` (label -> {type, title, event_datetime}) into display-only rows,
    in calendar order.

    `event_datetime` is a whole-day date, or a full datetime when the start time is known
    (the website then shows that time instead of its placeholder). `event_datetime: tbc`
    keeps the event as an undated TBC row; `tbc: true` next to a real date marks it
    provisional ("(TBC)"). An entry with no date and no tbc can never be shown, so it's
    dropped."""
    out: list[Event] = []
    mapping = _require_mapping(
        raw, drops, "events", "label", "no calendar rows appear on the site"
    )
    if mapping is None:
        return out
    for label, entry in mapping.items():
        where = f"events.{label}"
        if not isinstance(entry, dict):
            _drop(drops, where, "not a mapping", "the row never appears on the site")
            continue
        lines = take_lines(entry)
        raw_when = entry.get("event_datetime")
        when = _coerce_date_or_datetime(raw_when, tz)
        marked = _flagged_flag(entry, "tbc", False, drops, where, TBC_COST, lines)
        tbc = marked or _is_tbc(raw_when)
        if when is None and not tbc:
            _drop(
                drops,
                where,
                "no valid `event_datetime` (use `tbc` if the date is not settled)",
                "the row never appears on the site",
                lines,
                "event_datetime",
            )
            continue
        not_migrated_keys(drops, entry, where, RENAMED_ROW_KEYS, lines)
        _flag_unknown_keys(
            drops,
            entry,
            KNOWN_EVENT | frozenset(RENAMED_ROW_KEYS),
            where,
            "that setting is ignored",
            lines,
        )
        kind = str(entry.get("kind") or "").strip().lower()
        if kind and kind not in ("exam", "special_event"):
            # A typo'd `kind` (e.g. `exma`) still shows the row, but as a plain special
            # event - the exam styling, and "this is an exam", quietly disappear.
            _flag_bad_value(
                drops,
                where,
                "kind",
                kind,
                "the row is shown as a plain special event, not an exam "
                "(expected 'exam' or 'special_event')",
                lines,
            )
        out.append(
            Event(
                label=str(label),
                title=str(entry.get("title") or ""),
                details=_flagged_details(entry, drops, where, lines) or "",
                show_on_site=_flagged_flag(
                    entry,
                    "show_on_site",
                    True,
                    drops,
                    where,
                    "the row is shown on the site anyway",
                    lines,
                ),
                when=when,
                # anything other than the two known values -> the display-only default:
                # a typo'd `type` still shows the row (flagged above, not silent)
                kind="exam" if kind == "exam" else "special_event",
                tbc=tbc,
            )
        )
    # Undated (TBC) events sort to the end of the term, as they do in the release plan.
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(
        key=lambda e: (
            e.when is None,
            epoch if e.when is None else _instant(e.when, tz),
        )
    )
    return out


KNOWN_ARCHIVE = frozenset(
    {
        "event_datetime",
        "grace_days",
        "title",
        "details",
        "show_on_site",
        "tbc",
    }
)


def _whole_days(value: object) -> int | None:
    """A non-negative whole number of days, or None when `value` cannot be one."""
    if isinstance(value, bool):
        return None
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return days if days >= 0 else None


def _grace(raw: object, drops: Drops) -> timedelta:
    """`archive.grace_days` as the time after `semester_end` the default date falls,
    else `ARCHIVE_GRACE`. An unusable value is flagged and the sixty days stand."""
    if not isinstance(raw, dict) or raw.get("grace_days") is None:
        return ARCHIVE_GRACE
    days = _whole_days(raw["grace_days"])
    if days is None:
        _flag_bad_value(
            drops,
            "archive",
            "grace_days",
            raw["grace_days"],
            f"the default date counts {ARCHIVE_GRACE.days} days from semester_end instead",
        )
        return ARCHIVE_GRACE
    return timedelta(days=days)


def _parse_archive(
    meta: dict, semester_end: date | None, drops: Drops
) -> ArchiveRow | None:
    """The optional `archive:` block as an `ArchiveRow`, or None where none was written.

    The block IS the switch. A semester that writes none is never frozen automatically:
    every repository in an org going read-only is far too large a thing to happen off a
    date nobody typed, and the site row announcing it is worse still - it tells students a
    term ends on a day their own schedule.yml never mentions. Writing the block, empty or
    not, is what turns archiving on; sixty days after `semester_end` by default.

    Inside the block neither half is ever a crash, because this file is edited by hand: a
    date nobody can read falls back to `semester_end + ARCHIVE_GRACE` and is FLAGGED, so
    it reaches the person who wrote it through the digest issue rather than by freezing
    the semester on a day they did not choose. A block that is not a mapping at all is
    dropped the same way.

    A block with neither `event_datetime:` nor a `semester_end` to count from is an
    `ArchiveRow` with no `when` - it asked, but there is no clock to freeze it against,
    which is a different answer from the None a semester that wrote no block gets, and
    `scheduler._no_archive_date` says which out loud.

    `title:` and `tbc:` are display only and never reach the freeze: the row may be called
    anything and may say the day is still moving, and the day the scheduler acts on is the
    one this returns either way."""
    if "archive" not in meta:
        return None
    raw = meta["archive"]
    grace = _grace(raw, drops)
    default = semester_end + grace if semester_end else None
    cost = (
        f"this semester freezes at its default date instead ({default})"
        if default
        else "nothing freezes this semester automatically"
    )
    if not isinstance(raw, dict):
        # `archive:` with nothing under it asks for the default date and says nothing
        # else, which is the seeded skeleton's own shape. Anything that is not a mapping
        # at all - a scalar, a list - asked for the same thing and got the keys wrong, so
        # it is dropped and lands in the same place.
        if raw is not None:
            _drop(
                drops,
                "archive",
                "not a mapping (it must be `event_datetime:`, `title:`, `details:` "
                "and/or `show_on_site:` under it)",
                cost,
                field_name="archive",
            )
        return ArchiveRow(when=default)
    lines = take_lines(raw)
    _flag_unknown_keys(
        drops, raw, KNOWN_ARCHIVE, "archive", "that setting is ignored", lines
    )
    return ArchiveRow(
        when=_flagged_date(raw, "event_datetime", drops, "archive", cost, lines)
        or default,
        show_on_site=_flagged_flag(
            raw,
            "show_on_site",
            True,
            drops,
            "archive",
            "the site's archive row is shown anyway",
            lines,
        ),
        title=str(raw.get("title") or "").strip() or ARCHIVE_TITLE,
        details=_flagged_details(raw, drops, "archive", lines),
        tbc=_flagged_flag(raw, "tbc", False, drops, "archive", TBC_COST, lines),
    )


def parse(meta: dict, instance: settings.Instance | None = None) -> Schedule:
    """Parse a loaded schedule.yml dict into a Schedule. Tolerant of missing/blank fields
    (a semester with no schedule.yml behaves exactly as before). Anything it has to throw
    away is recorded in `Schedule.dropped` rather than vanishing - parsing stays total,
    but never silent.

    `instance` is the semester's `assignments.yml`, read (`settings.Instance`): each
    entry's `semester_dest_repo` comes from it, its own faults are carried here beside the
    plan's, and a block for a key the plan does not name is one more. None = no file."""
    meta = meta if isinstance(meta, dict) else {}
    instance = instance or settings.Instance()
    drops = Drops(top=take_lines(meta))
    # A whole plan under an unknown top-level key (`materials_releases:` instead of
    # `releases:`) otherwise validates as "OK: nothing dropped" with zero releases - the
    # worst kind of silent failure, since the file looks full. Flag it here.
    not_migrated_keys(drops, meta, "", RETIRED_TOP_KEYS, drops.top, say=moved_text)
    _flag_unknown_keys(
        drops,
        meta,
        KNOWN_TOP_LEVEL | frozenset(RETIRED_TOP_KEYS),
        "",
        "nothing it contains is scheduled or shown",
    )
    tz_name = meta.get("timezone")
    tz = _tz(tz_name)
    if tz_name and str(tz_name).strip() != str(tz):
        drops.note(
            "",
            "timezone",
            f"`{tz_name}` is not a known zone - falling back to {DEFAULT_TZ}, so every "
            f"naive time below is read in {DEFAULT_TZ}",
        )
    term_cost = "the site synthesises semester dates, shifting every session row"
    semester_start = _flagged_date(meta, "semester_start", drops, "", term_cost)
    semester_end = _flagged_date(meta, "semester_end", drops, "", term_cost)
    return Schedule(
        timezone=str(tz_name or DEFAULT_TZ),
        releases=_parse_releases(meta.get("releases"), tz, drops),
        # `01/09/2026` coerces to None exactly like an absent key, and the site then
        # SYNTHESISES term dates from what it does know - so a bad separator quietly
        # shifts every weekly session row. Flag it.
        semester_start=semester_start,
        semester_end=semester_end,
        assignments=(
            assignments := _parse_assignments(
                meta.get("assignments"), tz, drops, instance.dests
            )
        ),
        events=_parse_events(meta.get("events"), tz, drops),
        archive=_parse_archive(meta, semester_end, drops),
        dropped=drops.report + _instance_report(instance, meta, assignments, drops),
        faults=drops.faults,
    )


def _instance_report(
    instance: settings.Instance, meta: dict, kept: dict, drops: Drops
) -> list[str]:
    """`assignments.yml`'s faults, and one for each block keyed on nothing this plan
    names, as report lines (their faults join `drops.faults`). A schedule key without a
    block is no fault: its settings are the defaults."""
    named = meta.get("assignments")
    named = set(map(str, named)) if isinstance(named, dict) else set()
    faults = list(instance.faults) + [
        ConfigFault(
            f"assignments.{slug}",
            f"no assignment `{slug}` in {SCHEDULE_PATH} - this block is not read",
            file=ASSIGNMENTS_FILE,
            lineno=instance.lines.get(slug),
            fix_text=f"rename the block to a key under `assignments:` in {SCHEDULE_PATH}, "
            "or delete it",
        )
        for slug in sorted(instance.blocks)
        if slug not in named
    ]
    drops.faults.extend(faults)
    return [f"{ASSIGNMENTS_FILE} {f.label}: {f.what}" for f in faults]


def semester_name(slug: str, entry: AssignmentEntry) -> str:
    """The ONE semester-side name for an assignment: `semester_dest_repo`, else its slug.
    Every semester-side artefact keys on it - generated repos, teams.csv, snapshots,
    autograde markers, grades - and the scheduler's fire-once check must agree with what
    collect writes, so both resolve it here rather than each deriving its own."""
    return entry.semester_dest_repo or slug


def entries_for_repo(sched: Schedule, repo: str) -> list[tuple[str, AssignmentEntry]]:
    """Every `(slug, entry)` that hands out from `repo`, in the plan's own order.

    Usually one. Two is legitimate when each names its own `semester_dest_repo` (see
    `_shared_sources`) - a resit off the same brief, one template split across two halves
    of a semester - and a caller that acts on ONE of them has to say which, because the two
    make different repos and keep different grades. The callers that do (`provision_all`,
    `collect`) refuse rather than pick."""
    return [
        (slug, entry)
        for slug, entry in sched.assignments.items()
        if entry.course_source_repo == repo
    ]


def entry_for_repo(sched: Schedule, repo: str) -> tuple[str, AssignmentEntry] | None:
    """The FIRST `(slug, entry)` handing out from `repo`, or None.

    Callers that start from a REPO name - the autograder, the website - must find its
    schedule entry by matching `course_source_repo`, never by deriving a slug from the
    repo name. The slug is now a free label, so `wk3-regression-f2026` may legitimately be
    keyed `regression`; deriving would silently miss it, and the symptoms are quiet ones
    (no due date on the site, a group assignment provisioned per student).

    For a repo two entries cite, this answers with the first and says nothing about the
    second: only use it where ANY of them will do. Anything that writes semester-side state
    goes through `entries_for_repo` and refuses the ambiguity."""
    found = entries_for_repo(sched, repo)
    return found[0] if found else None


class AssignmentPage(NamedTuple):
    """One assignment's page on the semester site: its ordinal, its semester-side name, the
    course template it is drawn from, and its plan entry (None for a template the plan
    does not name)."""

    number: int
    name: str
    repo: str
    hit: tuple[str, AssignmentEntry] | None

    @property
    def key(self) -> str:
        """The SCHEDULE key, or "" for a template the plan does not name."""
        return self.hit[0] if self.hit else ""

    @property
    def stem(self) -> str:
        """`03-assignment-3` - the page's file under `_assignments/` is this plus `.md`,
        and its URL this plus `.html`, so the file and the link cannot disagree."""
        return f"{self.number:02d}-{self.name}"

    def url(self, semester_org: str) -> str:
        """Where the semester site serves it: the collection's default permalink, at the org
        root the site is published to (`_view_url`'s base)."""
        return f"https://{pages_repo(semester_org)}/assignments/{self.stem}.html"


def assignment_pages(
    semester_org: str, sched: Schedule, templates: list[str]
) -> list[AssignmentPage]:
    """Every assignment the semester site has a page for, numbered as the site numbers them -
    the ONE place that numbering is decided, because the site names the pages off it and
    the team-formation mail, the lock and the Join-team form all link them.

    `templates` is the course org's `assignment-*` template repos
    (`discovery.discover_assignments`), cut to this semester's own term tag. From BOTH sides:
    the templates, so one handed out off-plan still has a page, and the plan's entries, so
    one appears before its template is staged. Keyed on the SEMESTER-side name, because two
    plan entries may cite one `course_source_repo`; sorted by it, so a page keeps its URL
    when faculty add another mid-term.

    HIDDEN ones are included (`show_on_site: false`): the ordinal is a position in the full
    list, and the site skips a hidden page rather than renumbering around it, so hiding one
    mid-term moves nobody else's URL."""
    tag = semester_of(semester_org)
    if tag:
        templates = [a for a in templates if a.lower().endswith(tag)]
    by_name: dict[str, tuple[str, tuple[str, AssignmentEntry] | None]] = {}
    for repo in templates:
        hit = entry_for_repo(sched, repo)
        name = semester_name(*hit) if hit else assignment_slug(repo)
        by_name.setdefault(name, (repo, hit))
    for key, entry in sched.assignments.items():
        by_name.setdefault(
            semester_name(key, entry), (entry.course_source_repo, (key, entry))
        )
    return [
        AssignmentPage(i + 1, name, repo, hit)
        for i, (name, (repo, hit)) in enumerate(sorted(by_name.items()))
    ]


def assignment_pages_by_key(
    course_org: str, semester_org: str, sched: Schedule
) -> dict[str, AssignmentPage]:
    """`assignment_pages` by SCHEDULE key, off a fresh listing of the course org's
    templates - for a caller that links a page without building the site.

    `{}` when the listing failed: every caller uses a page to decide whether a sentence
    carries a link and a number, and one that could not look gets the wording that stands
    on its own rather than a link to the wrong page."""
    try:
        templates = discover_assignments(course_org)
    except RuntimeError as exc:
        log_err(f"could not list {course_org}'s assignment templates: {exc}")
        return {}
    return {p.key: p for p in assignment_pages(semester_org, sched, templates) if p.key}


def resolve_target(
    sched: Schedule,
    repo: str,
    slug: str = "",
    remedy: str = (
        "the scheduler acts on each on its own clock; nothing acts on one of them by hand"
    ),
) -> tuple[str, str] | str:
    """`(schedule key, semester-side name)` for the assignment `repo` hands out, or an ERROR
    MESSAGE (a `str`) when the plan names more than one of them and `slug` does not say
    which.

    The two names, and the only two, that every consumer starting from a TEMPLATE needs:
    the KEY is what `teams.csv`, the fire-once marker and the grading sheet are keyed on;
    the NAME is what the semester-side repos are called (`semester_dest_repo`, else the key).
    A template the plan does not name AT ALL answers with `assignment_slug(repo)` for
    both - the manual buttons must still work on a template nobody has scheduled - and
    that fallback lives here rather than at each call site, because a caller that copied
    only half of it would write semester-side artefacts under the schedule key.

    `slug` is the SCHEDULE KEY. Two entries handing out from one template are REFUSED
    rather than guessed between: they make different repos for different students and
    keep separate grades, so the handout and the collection must not be free to disagree
    about which of them they are acting on.

    `remedy` is what that refusal tells the reader to do about it, because the answer is
    the CALLER's: no button asks which entry (the schedule knows), so a manual run on a
    shared template refuses, naming them. The predicate lives
    here either way - one owner, so a caller cannot quietly stop refusing.

    A `str` rather than a raise, deliberately: the hourly scheduler calls straight into
    these consumers and has to count one assignment's refusal without abandoning the tick.
    """
    found = entries_for_repo(sched, repo)
    if slug:
        found = [pair for pair in found if pair[0] == slug]
        if not found:
            return (
                f"`{slug}` is not an assignment in this semester's schedule.yml that hands "
                f"out from {repo} (it names "
                f"{', '.join(s for s, _ in entries_for_repo(sched, repo)) or 'none'})"
            )
    elif len(found) > 1:
        return (
            f"{repo} is handed out by {len(found)} assignments in this semester's "
            f"schedule.yml ({', '.join(s for s, _ in found)}) - {remedy}, "
            f"since they make different repos and keep different grades"
        )
    if not found:
        unscheduled = assignment_slug(repo)
        return unscheduled, unscheduled
    return found[0][0], semester_name(*found[0])


def grading_cutoff_datetime(sched: Schedule, slug: str) -> datetime | None:
    """THE late cutoff: when this assignment stops accepting work - the due date plus the
    effective `late_window_days` (`settings`: assignments.yml, then the course, then the
    institution). `late_window_days: 0`, or a late rule that names no window, is the due
    date itself: no late work. None if unscheduled.

    Computed, never an input (decisions 0009, 0012), and computed HERE only. Everything
    that has to agree about when the door shuts reads it - the snapshot that freezes and
    the autograder that fires off it, the sheet's header, the receipts that quote the
    policy to a student, the team-formation window, the cadence check, status.json."""
    entry = sched.assignments.get(slug)
    if entry is None:
        return None
    days, _ = settings.effective(sched.org, slug, "late_window_days")
    return entry.due_datetime + timedelta(days=int(days or 0))


def formation_window(
    sched: Schedule, slug: str
) -> tuple[datetime | None, datetime | None]:
    """When a self-selected team may be formed: `(handout, grading pin)`, or `(None, None)`
    if the slug is not in the schedule at all.

    A window with no `opens` NEVER opens. An assignment with no `handout_datetime` is
    handed out by hand at a moment nobody has written down, so there is no hour from which
    telling a student "form your team now" would be true - and an invitation sent before
    the brief exists asks them to team up over an assignment they cannot read.

    It closes at the grading pin because that is when the snapshot freezes: a team minted
    after it has nothing left to hand in, and would be provisioned a repo against work
    already collected.

    No spec read: the window closes at the late cutoff (`grading_cutoff_datetime`), which
    reads the run settings and nothing of the template's."""
    entry = sched.assignments.get(slug)
    if entry is None:
        return (None, None)
    return (entry.handout_datetime, grading_cutoff_datetime(sched, slug))


def formation_state(
    sched: Schedule, slug: str, now: datetime
) -> tuple[str, datetime | None]:
    """Where `now` falls in this assignment's team-formation window, and the moment that
    window shuts: `pending` (the handout is still to come), `open` or `closed`.

    `closed` covers every case the window does not open, the two ends of it and the entry
    with no dates to judge by alike - `formation_window` returning no boundary at all is
    a door that never opens, not one that opens forever.

    Whether the assignment HAS a window is the caller's question, not this one's: it hangs
    on `team_formation`, which lives in the template's `grading_config.yml` and which this
    module deliberately cannot read. Both callers already hold the spec.

    One place, because two things now answer off it minutes apart - the lock file the
    Join-team form refuses on, and the semester site's team-formation callout - and a page
    that invites a student through a door the form has already shut is worse than either
    saying nothing."""
    opens, closes = formation_window(sched, slug)
    if opens is None or closes is None:
        return "closed", closes
    if now < opens:
        return "pending", closes
    if now < closes:
        return "open", closes
    return "closed", closes


# ---------------------------------------------------------------------- gh/git wiring


@cache
def _schedule_text(semester_org: str) -> str | None:
    """schedule.yml's text, read ONCE per semester per process.

    An hourly tick reads the plan repeatedly - the scheduler itself, then again inside
    every handout and collection it fires - for a file that changes only when a person
    edits it or `record_handout` writes it. The TEXT is memoised rather than the parsed
    `Schedule`, so every caller gets its own object (nothing shared to mutate) and the
    loud "N entries DROPPED" report is still printed once per caller, exactly as before.
    `record_handout` clears it after its write; tests/conftest.py clears it between
    tests."""
    return get_file_content(semester_org, CONFIG_REPO, SCHEDULE_PATH)


def _unreadable_fault(what: str, lineno: int | None = None) -> ConfigFault:
    """The whole FILE, unusable - what a human is asked to fix.

    `where` is the file itself, because there is no entry to name: nothing in it was read,
    so the fault is about its shape and every entry pays the same price. An IMMEDIATE
    fault (no `fires`), like every other line the parser cannot read: waiting changes
    nothing about it."""
    return ConfigFault(
        SCHEDULE_PATH,
        what,
        file=SCHEDULE_PATH,
        field="schedule",
        lineno=lineno,
        fix_text=(
            "fix the YAML on the line above; every entry is ignored until the file parses"
        ),
    )


def load(semester_org: str) -> Schedule:
    """Fetch + parse schedule.yml from the semester's PRIVATE semester-config repo. A
    pure loader: a missing file returns an empty Schedule silently (every field
    optional everywhere it's read).

    A file that does not PARSE (faculty-editable YAML - an unclosed brace, a bad indent)
    is treated exactly as an absent one: the error is logged loudly, with the parser's own
    line/column, and an empty Schedule is returned. It must never raise: `load` sits under
    the hourly scheduler AND the site sync, and one semester's typo froze both. It is also
    a FAULT on the file - the one that costs the semester everything - so the digest issue
    and the mail carry it like any other (see `Schedule.faults`)."""
    content = _schedule_text(semester_org)
    unparseable: list[ConfigFault] = []
    try:
        meta = load_yaml_lines(content) if content else {}
    except yaml.YAMLError as exc:
        log_err(
            f"{semester_org}/{CONFIG_REPO}/{SCHEDULE_PATH} is NOT valid YAML - the whole "
            f"schedule is ignored:"
        )
        # the parser's own message: it carries the line/column and the offending snippet
        log_err(str(exc))
        log_err(
            f"fix {CONFIG_REPO}/{SCHEDULE_PATH} on main in {semester_org} - until then "
            f"NOTHING is scheduled for this semester (no releases, no handouts, no deadline "
            f"snapshots, no autograding) and the site builds without schedule data."
        )
        meta = {}
        unparseable.append(
            _unreadable_fault(
                "this file is not valid YAML, so the whole plan is ignored: nothing "
                "releases, hands out, snapshots or grades",
                lineno=yaml_mark_line(exc),
            )
        )
    if meta is not None and not isinstance(meta, dict):
        # Valid YAML, but not a schedule: a bare list, or a stray document separator that
        # left a string at the top level. Same consequence as a parse failure - nothing in
        # the file is read - so it must not read as an empty plan either.
        log_err(
            f"{semester_org}/{CONFIG_REPO}/{SCHEDULE_PATH} parses as "
            f"{type(meta).__name__}, not a mapping - the whole schedule is ignored. "
            f"Its top level must be keys like `releases:` / `assignments:`."
        )
        unparseable.append(
            _unreadable_fault(
                f"this file parses as {type(meta).__name__}, not a mapping of "
                f"`releases:` / `assignments:` / `events:`, so the whole plan is "
                f"ignored: nothing releases, hands out, snapshots or grades"
            )
        )
    instance = settings.instance(semester_org)
    sched = parse(meta if isinstance(meta, dict) else {}, instance)
    sched.org = semester_org
    sched.instance_unparseable = instance.unparseable
    sched.unparseable = bool(unparseable)
    sched.faults.extend(unparseable)
    if sched.dropped:
        # Loud, because this is the failure faculty cannot see: the file is valid YAML and
        # the run goes green, but an entry they wrote is not in the plan. Every caller
        # comes through here - the hourly scheduler, the site sync, Check semester setup - so
        # saying it once here says it everywhere.
        log_err(
            f"{semester_org}/{CONFIG_REPO}/{SCHEDULE_PATH}: {len(sched.dropped)} entry/ies "
            f"DROPPED - they parse as YAML but not as schedule entries:"
        )
        for line in sched.dropped:
            log_err(f"  {line}")
        log_err(f"fix them on main in {semester_org}; everything else is unaffected.")
    return sched


def load_file(path: str) -> tuple[Schedule | None, str | None]:
    """Parse a schedule.yml from DISK: returns (schedule, None), or (None, error) when the
    file is missing or is not valid YAML.

    The opposite stance to `load`, deliberately. `load` treats an unparseable semester file
    as an absent one, because it sits under the hourly cron and one typo must not be able
    to freeze a semester. Here the caller is a validator whose whole job is to fail, so a
    broken file is an error and not an empty schedule."""
    p = Path(path)
    try:
        text = p.read_text()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        meta = load_yaml_lines(text) or {}
    except yaml.YAMLError as exc:
        # the parser's own message carries the line/column and the offending snippet
        return None, f"{path} is not valid YAML:\n{exc}"
    if not isinstance(meta, dict):
        return None, f"{path} is valid YAML but not a mapping - it needs top-level keys"
    beside = p.with_name(ASSIGNMENTS_FILE)
    instance = settings.parse_instance(beside.read_text() if beside.is_file() else None)
    return parse(meta, instance), None


@cache
def _repo_paths(course_org: str, repo: str) -> set[str] | None:
    """Every path in a course-org repo, files and directories alike, in ONE tree fetch.
    An empty set is a repo with nothing in it; `None` is "could not tell".

    Memoised per process, like `repos._repo`: a repo's tree does not change under a run,
    and the commit-time validator and the hourly pre-flight each ask about the same
    handful of materials repos from several entries. `tests/conftest.py` clears it.

    Kept distinct from "the repo is not there" (the caller asks `repo_missing` first),
    because the two want opposite handling: an absent repo is a fault worth naming, an
    unreadable one must be passed over in silence. `default_branch` is the fail-loud twin
    on purpose - `default_branch(fallback="main")` guesses when it cannot read the repo,
    `repo_tree` then 404s on the guess and reports `()`, and every deploy in the plan comes
    back as "no such repo": the exact cry-wolf this check exists to avoid."""
    try:
        return set(repo_tree(course_org, repo, default_branch(course_org, repo)))
    except RuntimeError:
        return None


def source_repo_paths(course_org: str, repo: str) -> set[str] | None:
    """Every path in a source repo the plan names - `set()` when the repo is absent OR
    empty, `None` when it could not be read.

    THE "is this source there?" test, and shared on purpose: `source_faults` and the
    scheduler's `_assignment_template` ask the same question in the same run and used to
    answer it differently. `repo_exists` is optimistic - it says yes to a repo that exists
    with no commits - so a template nobody had pushed to was reported as a missing source
    by one and handed out from by the other, on the same tick.

    `repo_missing` and not `repo_exists`, because the two failures want opposite handling:
    only a 404 says absent, and a 403, a 5xx or a rate limit must come back as `None` for
    the caller to pass over in silence rather than tell faculty their materials are gone.
    """
    if repo_missing(course_org, repo):
        return set()
    return _repo_paths(course_org, repo)


def _window_blurb() -> str:
    """The ladder in one sentence, formatted from the windows themselves so changing one
    cannot leave three hand-written prose copies claiming the old numbers."""
    return (
        f"advisory until {hours(SOURCE_WARN_WINDOW)}h out, then a warning, urgent "
        f"inside {hours(SOURCE_URGENT_WINDOW)}h, critical inside "
        f"{hours(SOURCE_CRITICAL_WINDOW)}h, missed once it has fired"
    )


def in_zone(tz_name: str, when: datetime) -> datetime:
    """The same instant, told in `tz_name` - falling back to DEFAULT_TZ for a name nothing
    recognises, exactly as the plan's own parse does.

    For a caller holding the zone but not the plan it came out of (`team_formation`'s mail
    renders one window's closing day and is handed the name alone)."""
    return when.astimezone(_tz(tz_name))


def in_semester_zone(sched: Schedule, when: datetime) -> datetime:
    """The same instant, told in the semester's own zone.

    The scheduler ticks in UTC, but everything a notification says about time is local by
    definition: a deadline faculty wrote as 08:00 Berlin, and a quiet window where 02:00
    means somebody's actual night rather than 02:00 in a datacentre."""
    return in_zone(sched.timezone, when)


class _Wanted(NamedTuple):
    """One source the plan names: the path inside the repo (empty for an assignment, whose
    repo IS the source), where it is cited, when it is needed, and the line each of its
    keys is written on.

    `lines` rather than one line, because the two fields a fault can name are two
    different lines of the same entry (see `Deploy.lines`)."""

    path: str
    where: str
    fires: datetime | None
    lines: dict[str, int]

    def lineno(self, field: str) -> int | None:
        return line_of(self.lines, field)


def source_faults(sched: Schedule, course_org: str) -> list[SourceFault]:
    """Every source the plan names that is not in the course org RIGHT NOW.

    A deploy whose `course_source_repo` or `course_source_path` is absent ships nothing
    when its moment comes, and an assignment whose `course_source_repo` is absent hands
    out to nobody - both fail at fire time, which for a November lecture means finding out
    in November. Checking the plan against the org catches it while there is still time to
    write the thing.

    Severity is NOT decided here: each fault carries the moment it is needed and callers
    ask it (`SourceFault.severity`), so the commit-time validator and the hourly pre-flight
    apply one ladder rather than two opinions.

    One tree fetch per distinct source repo, not per deploy. A repo whose tree cannot be
    READ is skipped entirely rather than reported as missing (see `_repo_paths`)."""
    # One `_Wanted` per source repo, so each repo is fetched once however many deploys
    # point into it.
    wanted: dict[str, list[_Wanted]] = {}
    for release in sched.releases:
        for d in release.deploy:
            wanted.setdefault(d.course_source_repo, []).append(
                _Wanted(
                    d.course_source_path,
                    f"releases.{release.label}",
                    d.deploy_datetime or release.when,
                    d.lines,
                )
            )
    for slug, a in sched.assignments.items():
        # An assignment with no handout pin is handed out by hand, so nothing dates it.
        wanted.setdefault(a.course_source_repo, []).append(
            _Wanted("", f"assignments.{slug}", a.handout_datetime, a.lines)
        )

    out: list[SourceFault] = []
    for repo in sorted(wanted):
        # Absent-or-empty is asked separately from unreadable, because they want opposite
        # answers: a repo that is not there is the typo this check exists to catch, while
        # a repo that cannot be READ must be passed over in silence. Both, and the reason
        # the optimistic `repo_exists` is not what asks, live in `source_repo_paths` - the
        # scheduler puts the same question to it.
        paths = source_repo_paths(course_org, repo)
        if paths is None:
            # Could not tell. Say nothing rather than cry wolf about every source in
            # the plan - and say out loud that this tick did not check them.
            log(
                f"  [skip] could not read {course_org}/{repo} - its sources are not "
                f"checked this tick"
            )
            continue
        if not paths:
            out.extend(
                SourceFault(
                    w.where,
                    f"no repo {course_org}/{repo} (or it is empty)",
                    w.fires,
                    field="course_source_repo",
                    kind=FaultKind.MISSING_REPO,
                    file=SCHEDULE_PATH,
                    lineno=w.lineno("course_source_repo"),
                    repo=repo,
                    path=w.path,
                )
                for w in wanted[repo]
            )
            continue
        # Which of this repo's paths a `.releaseignore` withholds - computed once per
        # repo, off the tree already fetched above, and `read` is called only for the
        # ignore files themselves. A repo without one costs nothing.
        try:
            withheld = set(
                excluded_in_tree(
                    paths,
                    # `repo` bound now, not looked up later: the read is eager, but a
                    # lambda over a loop variable is one edit away from not being.
                    lambda p, _r=repo: get_file_content(course_org, _r, p),
                )
            )
        except RuntimeError:
            # `get_file_content` raises on any non-404 read failure, and this function
            # promises that a repo it cannot READ is passed over in silence - its one
            # caller that matters here is the commit-time validator, where a transient 429
            # would otherwise be a traceback on faculty's own push. Degrade to "no
            # withheld paths known": a missing source is still reported below.
            withheld = set()
        for w in wanted[repo]:
            # "" is the assignment case: the repo IS the source, so its existence is all
            # there is to check. `/` and `.` mean the whole repo, likewise - and which
            # spellings those are is `course.is_repo_root`, the same rule the release
            # itself resolves by (deploy._resolve_within).
            if is_repo_root(w.path):
                continue
            clean = w.path.strip("/").strip()
            if clean in withheld:
                # The file EXISTS, so nothing looks wrong - which is why this is worth
                # saying here rather than leaving to the `::warning::` a green release run
                # buries. Same rung as a missing source: the copy ships nothing either way.
                out.append(
                    SourceFault(
                        w.where,
                        f"the files exist but {repo}/{RELEASEIGNORE} keeps them back",
                        w.fires,
                        field="course_source_path",
                        kind=FaultKind.WITHHELD,
                        file=SCHEDULE_PATH,
                        ceiling=Severity.WARNING,
                        lineno=w.lineno("course_source_path"),
                        repo=repo,
                        path=clean,
                    )
                )
                continue
            if clean in paths:
                continue
            out.append(
                SourceFault(
                    w.where,
                    f"{course_org}/{repo}/{clean} does not exist",
                    w.fires,
                    field="course_source_path",
                    kind=FaultKind.MISSING_PATH,
                    file=SCHEDULE_PATH,
                    lineno=w.lineno("course_source_path"),
                    repo=repo,
                    path=clean,
                )
            )
    return out


def source_report(faults: list[SourceFault], now: datetime, course_org: str) -> str:
    """The `--check-sources` half of the CLI report: every fault, loudest first, with the
    rung it sits at and the sentence that explains why distance is what decides.

    A function rather than a run of `print`s in `main` because two other things read this
    exact text - the run summary the workflow appends, and the test that proves the
    workflow's comment step is fed the engine's own lines rather than a grep of them."""
    if not faults:
        return f"  every source in the plan exists in {course_org}"
    return "\n".join(
        [
            f"  {len(faults)} SOURCE(S) NOT IN {course_org} YET:",
            *(
                f"    [{f.severity(now)}] {f.line()}"
                for f in sorted(faults, key=lambda f: -f.severity(now))
            ),
            "",
            "  A source you have not written yet looks exactly like this, so this is only",
            (
                f"  a fault once its moment is close: {_window_blurb()} - at "
                f"which point it is about to ship nothing."
            ),
        ]
    )


def source_comment(
    faults: list[SourceFault], now: datetime, semester_org: str = ""
) -> str:
    """The comment to leave on a push that plans a release with nothing to ship, or "" when
    there is nothing to say.

    The push is the cheapest moment to say it: the person who wrote the line is still at
    their keyboard, and the alternative is finding out from an email tomorrow. Distant
    faults get nothing - the digest issue holds those, and commenting on a term written in
    August is how faculty learn to scroll past this.

    Built here rather than in the workflow's shell, which grepped the report above back
    for a rung prefix: the pattern matched some rungs and not others, silently, and it
    counted an entry that had ALREADY fired as one "inside 24h" - which is a different
    thing to say to somebody and needs its own line.

    `semester_org` turns each citation into a link at the line (`SourceFault.cite`). A
    commit comment is markdown, and the whole point of saying this on the push is that the
    fix is one click away; without the org there is no URL to build, and the line is cited
    as code."""
    loud = sorted(
        (f for f in faults if f.severity(now) >= NOTIFY_FROM),
        key=lambda f: -f.severity(now),
    )
    if not loud:
        return ""
    fired = [f for f in loud if f.severity(now) is Severity.MISSED]
    coming = [f for f in loud if f.severity(now) is not Severity.MISSED]
    out: list[str] = []
    if coming:
        out += [
            (
                f"This push leaves {len(coming)} planned release(s) inside "
                f"{hours(SOURCE_WARN_WINDOW)}h whose materials are not in the course "
                f"org:"
            ),
            *(f"- {f.line(f.cite(semester_org))}" for f in coming),
        ]
    if fired:
        out += [
            f"{len(fired)} planned release(s) have already fired with nothing to ship:",
            *(f"- {f.line(f.cite(semester_org))}" for f in fired),
        ]
    out += ["", "You will get one email about each as its deadline nears."]
    return "\n".join(out)


def worst_severity(faults: list[SourceFault], now: datetime) -> Severity | None:
    """The loudest severity among `faults` at `now`, or None when there are none. A plain
    `max` - which is the whole reason Severity is ordered rather than a bare string."""
    return max((f.severity(now) for f in faults), default=None)


def solution_notices(before: dict, after: Schedule) -> list[ConfigFault]:
    """One informational fault for each assignment that has just GAINED a
    `solution_datetime:` - absent from `before`, the file as it was, read as plain YAML so
    an entry the parser would have dropped there still counts as what it said. Never a
    verdict: the push is fine; the person who made it is told, once, what the date will
    do."""
    raw = before.get("assignments") if isinstance(before, dict) else None
    was = {
        str(slug): entry
        for slug, entry in (raw.items() if isinstance(raw, dict) else ())
        if isinstance(entry, dict)
    }
    return [
        ConfigFault(
            f"assignments.{slug}",
            f"`solution_datetime: {entry.solution_datetime:%Y-%m-%d %H:%M}` - "
            f"{SOLUTION_WARNING}",
            field="solution_datetime",
            lineno=line_of(entry.lines, "solution_datetime"),
            file=SCHEDULE_PATH,
            ceiling=Severity.ADVISORY,
        )
        for slug, entry in after.assignments.items()
        if entry.solution_datetime is not None
        and (slug not in was or was[slug].get("solution_datetime") is None)
    ]


def _validate_report(sched: Schedule, source: str) -> str:
    """What the parser UNDERSTOOD, followed by anything it threw away.

    Reporting the totals matters as much as reporting the drops: a well-formed entry with
    the wrong date is invisible to validation, but "4 assignments" when you wrote five is
    not. This is what a reader sees in a run summary, so it stays plain text."""
    lines = [
        f"Parsed {source}",
        f"  semester {sched.semester_start} -> {sched.semester_end}  ({sched.timezone})",
        (
            f"  {len(sched.releases)} release(s), "
            f"{sum(len(r.deploy) for r in sched.releases)} deploy(s) | "
            f"{len(sched.assignments)} assignment(s) | {len(sched.events)} event(s)"
        ),
    ]
    if sched.dropped:
        lines.append("")
        lines.append(f"  {len(sched.dropped)} ENTRY/IES DROPPED:")
        lines.extend(f"    - {d}" for d in sched.dropped)
    return "\n".join(lines)


_HANDOUT_COMMENT = "   # set automatically by the Release assignment workflow"
_DUE_TODO = "# TODO: add `due_datetime:` - the date students see (required)"


class _Declined:
    """What `_insert_handout` returns when the file's shape defeats its line surgery.

    Distinct from its None, and the distinction is the point: None means "already on
    record" (the correct write-once no-op), DECLINED means the handout HAPPENED and is
    NOT recorded anywhere. Both used to be None, so a lost record looked exactly like a
    successful one."""


DECLINED = _Declined()


def _insert_handout(text: str, slug: str, stamp: str) -> str | _Declined | None:
    """Pure text surgery for `record_handout` - schedule.yml is USER-owned and
    comment-rich, so we insert lines rather than re-serialising (which would destroy
    every comment).

    Returns the new text; None when the entry already carries a handout (write-once, a
    scheduled value is never touched); or DECLINED when the `assignments:` block is shaped
    in a way this line surgery can't recognise (a flow mapping). Declining leaves the file
    untouched rather than fabricating a duplicate entry - the old code assumed exactly
    two-space indentation, missed a deeper-nested entry, and injected a fake `  {slug}:`
    that swallowed the real one, dropping its `due_datetime` for good - and the caller
    says so out loud, because nothing was recorded."""
    lines = text.splitlines(keepends=True)

    def indent_of(ln: str) -> int:
        return len(ln) - len(ln.lstrip())

    # locate the top-level `assignments:` mapping key (a bare block header at column 0)
    a_start = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.split("#")[0].rstrip() == "assignments:"
        ),
        None,
    )
    if a_start is None:
        # A flow-style `assignments: {...}` (or any other col-0 line beginning
        # `assignments:` that isn't a plain block header) can't take a line insertion -
        # leave it untouched rather than append a second, duplicate key.
        if any(re.match(r"^assignments:\s*\S", ln.split("#")[0]) for ln in lines):
            return DECLINED
        # no assignments block at all: append one (the documented 2-space shape),
        # flagging the due date still to add.
        return (
            (text if text.endswith("\n") or not text else text + "\n")
            + f"\nassignments:\n  {slug}:\n"
            + f"    handout_datetime: {stamp}{_HANDOUT_COMMENT}\n"
            + f"    {_DUE_TODO}\n"
        )

    # The block body runs until the next non-comment column-0 line.
    block_end = len(lines)
    for i in range(a_start + 1, len(lines)):
        stripped = lines[i].split("#")[0].rstrip()
        if stripped and not lines[i].startswith((" ", "\t")):
            block_end = i
            break

    # The slug key at WHATEVER indent it sits at - matching only exactly two spaces was
    # the bug. A positive indent inside the block is required (a col-0 match would be a
    # sibling top-level key, not an assignment). `(.*)` captures whatever follows the
    # colon so an inline flow value (`slug: {due_datetime: ...}`) is recognised as the
    # same key, not missed and then fabricated as a duplicate.
    slug_re = re.compile(rf"^(\s+){re.escape(slug)}:\s*(.*)$")
    for i in range(a_start + 1, block_end):
        m = slug_re.match(lines[i])
        if not m:
            continue
        if m.group(2).split("#")[0].strip():
            # The slug exists but is authored as an inline value (a flow mapping/scalar),
            # so there is no block body to append a handout line into. Leave the file
            # untouched rather than fabricate a duplicate key that PyYAML would silently
            # drop (losing the handout) - write-once, the operator can add it by hand.
            return DECLINED
        slug_indent = len(m.group(1))
        # Scan the slug's sub-block (lines indented deeper than the slug) for an existing
        # handout, learning the child indent from its first field.
        child_indent = slug_indent + 2
        seen_child = False
        for j in range(i + 1, block_end):
            stripped = lines[j].split("#")[0].rstrip()
            if not stripped:
                continue
            if indent_of(lines[j]) <= slug_indent:
                break  # next sibling slug, or out of the block
            if not seen_child:
                child_indent, seen_child = indent_of(lines[j]), True
            if stripped.lstrip().startswith("handout_datetime:"):
                return None  # write-once - never move a scheduled or recorded handout
        lines.insert(
            i + 1, f"{' ' * child_indent}handout_datetime: {stamp}{_HANDOUT_COMMENT}\n"
        )
        return "".join(lines)

    # Slug genuinely absent: fabricate a new entry, matched to the block's OWN entry
    # indent (learned from an existing sibling) so we never inject a 2-space entry into a
    # 4-space block. An empty block has no sibling to learn from - use the documented
    # 2-space shape.
    entry_indent = 2
    for i in range(a_start + 1, block_end):
        if lines[i].split("#")[0].rstrip():
            entry_indent = indent_of(lines[i])
            break
    pad, child = " " * entry_indent, " " * (entry_indent + 2)
    lines.insert(
        a_start + 1,
        f"{pad}{slug}:\n"
        f"{child}handout_datetime: {stamp}{_HANDOUT_COMMENT}\n"
        f"{child}{_DUE_TODO}\n",
    )
    return "".join(lines)


def _put_handout(
    semester_org: str, slug: str, stamp: str, body: str, sha: str | None
) -> bool:
    """Write the recorded handout over schedule.yml at the sha its text was READ at, so a
    faculty edit committed during the run is refused rather than reverted; on a refusal,
    re-read, re-apply the handout to the fresh text and try once more."""
    message = f"schedule: record {slug} handout ({stamp})"

    def write(text: str, at: str | None) -> bool:
        return put_file(
            semester_org,
            CONFIG_REPO,
            SCHEDULE_PATH,
            text.encode(),
            message,
            expected_sha=at,
        )

    if write(body, sha):
        return True
    log_err(
        f"{SCHEDULE_PATH} in {semester_org} was edited while {slug} was being handed out - "
        f"re-reading and retrying once"
    )
    read = get_file_with_sha(semester_org, CONFIG_REPO, SCHEDULE_PATH)
    if read is None:
        return False
    fresh, fresh_sha = read
    rebuilt = _insert_handout(fresh, slug, stamp)
    if rebuilt is None:
        return True  # the edit that beat us recorded the same handout
    if isinstance(rebuilt, _Declined):
        return False
    return write(rebuilt, fresh_sha)


def record_handout(semester_org: str, slug: str, stamp: str | None = None) -> None:
    """Record a manual handout back into schedule.yml (`assignments.<slug>.handout_datetime`),
    so the schedule stays the one record of when every assignment went out - whether
    the cron released it or a person ran the workflow. Write-once: an existing
    handout_datetime (scheduled, or recorded by an earlier run) is never modified. Best
    effort - a failure here must never fail the release itself, but it is never silent
    either: a file this can't edit means the handout happened and is on record nowhere.

    The edit is made against a FRESH read and written with that read's sha, so a faculty
    edit committed during a long run is refused rather than reverted; one retry re-reads
    and re-applies (as `enrol_codes.write_codes` does)."""
    read = get_file_with_sha(semester_org, CONFIG_REPO, SCHEDULE_PATH)
    text, sha = read if read is not None else ("", None)
    # This is the one writer of schedule.yml inside a run, so it is the one place the
    # per-process read memo can go stale. Dropped up front: every path below either
    # returns without writing or writes, and a memo cleared once too often only costs a
    # read, where one held too long hands the next caller a plan missing this handout.
    _schedule_text.cache_clear()
    if stamp is None:
        # the release moment, in the semester's own timezone (naive, like every other
        # schedule datetime - the parser reads it back in that same zone)
        try:
            tz_name = (yaml.safe_load(text) or {}).get("timezone")
        except yaml.YAMLError:
            tz_name = None
        stamp = datetime.now(
            _tz(tz_name if isinstance(tz_name, str) else None)
        ).strftime("%Y-%m-%dT%H:%M")
    new = _insert_handout(text, slug, stamp)
    if isinstance(new, _Declined):
        # The handout HAPPENED; the record of it is what we just failed to write. Say so -
        # the alternative (a silent return, indistinguishable from the write-once no-op)
        # leaves the schedule claiming the assignment was never handed out.
        log_err(
            f"could NOT record the {slug} handout in {semester_org}/{CONFIG_REPO}/"
            f"{SCHEDULE_PATH}: its `assignments:` block is authored in a shape this edit "
            f"cannot extend safely (a flow mapping). The handout went out at {stamp} but "
            f"is on record nowhere - add `handout_datetime: {stamp}` to "
            f"`assignments.{slug}` by hand."
        )
        return
    if new is None:
        return  # already recorded - write-once, nothing to do
    if _put_handout(semester_org, slug, stamp, new, sha):
        log(f"  recorded handout in {CONFIG_REPO}/{SCHEDULE_PATH}: {slug} @ {stamp}")
    else:
        # Same fault as the DECLINED branch above, one step later: the handout HAPPENED
        # and the write of its record is what failed. Best-effort stays (the repos are
        # out; nothing here raises), but it may not be silent.
        log_err(
            f"could NOT record the {slug} handout in {semester_org}/{CONFIG_REPO}/"
            f"{SCHEDULE_PATH}: the write failed. The handout went out at {stamp} but is "
            f"on record nowhere - add `handout_datetime: {stamp}` to "
            f"`assignments.{slug}` by hand."
        )


def _write_output(line: str) -> None:
    """Append one `name=value` to the workflow's step output, when there is one.

    Off a runner `GITHUB_OUTPUT` is unset and this is a no-op, which is what keeps
    `--annotate` usable by hand."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{line}\n")
    except OSError as exc:
        # A report the next step cannot read is not worth failing a validation over.
        log_err(f"could not write to GITHUB_OUTPUT: {exc}")


def main() -> int:
    parser = CLIParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--semester-org", help="fetch schedule.yml from a semester org")
    source.add_argument(
        "--file", help="validate a schedule.yml on disk (no GitHub access)"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="exit non-zero if the file is unparseable or any entry was dropped, "
        "instead of dumping the schedule",
    )
    parser.add_argument(
        "--check-sources",
        metavar="COURSE_ORG",
        help="additionally report sources the plan names that do not exist in this "
        "course org yet. Advisory: it never changes the exit code, because a session "
        "nobody has written yet is the normal state of a semester planned up front",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="also emit each source fault to stderr as a GitHub Actions ::warning:: "
        "against schedule.yml, so it shows on the commit's own diff view, and write "
        "`sources_notify=true|false` to $GITHUB_OUTPUT - whether anything has reached "
        "the rung a human is told about - for the steps that follow",
    )
    parser.add_argument(
        "--previous",
        metavar="PATH",
        help="the file as it was before this change: each assignment that has just "
        "gained a `solution_datetime:` gets a ::notice:: saying what it does. A file "
        "that does not parse gives no notices. Never changes the exit code",
    )
    parser.add_argument(
        "--comment-file",
        metavar="PATH",
        help="write the comment to leave on the push (the faults a human is told about, "
        "ready to post) to PATH, empty when there is nothing to say",
    )
    args = parser.parse_args()

    if args.file:
        sched, error = load_file(args.file)
        if error is not None:
            # Unlike a semester fetch, a broken FILE is a hard failure - see load_file.
            log_err(error)
            print(f"INVALID: {args.file} could not be parsed")
            return 1
        source_name = args.file
    else:
        # A semester fetch reads schedule.yml over the API: absent is an empty Schedule,
        # but an unreadable one raises - report it as a line, not a traceback.
        try:
            sched = load(args.semester_org)
        except RuntimeError as exc:
            log_err(str(exc))
            return 1
        source_name = f"{args.semester_org}/{SCHEDULE_PATH}"

    if not args.validate:
        parsed = asdict(sched)
        # `dropped` says the same thing in the shape this dump has always had; `faults` is
        # the notifier's copy of it, and a dump of the plan is not where anybody reads it.
        parsed.pop("faults", None)
        print(json.dumps(parsed, indent=2, default=str))
        return 0
    # Report what was UNDERSTOOD as well as what was dropped: validation cannot catch a
    # well-formed entry with the wrong date, but a count that is one short is visible.
    print(_validate_report(sched, source_name))
    if args.previous is not None:
        try:
            before = yaml.safe_load(Path(args.previous).read_text())
        except (OSError, yaml.YAMLError):
            before = None
        # A previous file nobody can read says nothing about what is new: no notices.
        notes = solution_notices(before, sched) if isinstance(before, dict) else []
        for note in notes:
            at = f",line={note.lineno}" if note.lineno else ""
            print(f"::notice file={SCHEDULE_PATH}{at}::{note.line()}", file=sys.stderr)
    if sched.unparseable:
        # The same verdict the --file form gives, in the same words: `load` returns an
        # empty Schedule for a file that does not parse (so the hourly cron cannot be
        # frozen by one), and a validator that read that as "nothing dropped" was the one
        # caller for which the fallback is the wrong answer. Nothing in the file was read,
        # so there are no sources to check either. `load` has already logged the parser's
        # own line and what the semester loses until it is fixed.
        print(f"\nINVALID: {source_name} could not be parsed")
        return 1
    # The source check is a separate question from the parse. `--validate` on its own
    # stays a pure offline read of the file it was given - deterministic, green or red for
    # reasons entirely inside that file. This half asks the org whether the plan's sources
    # exist, an answer that legitimately changes week to week, so it is reported apart and
    # only escalates on its own ladder (SourceFault.severity).
    if args.check_sources:
        faults = source_faults(sched, args.check_sources)
        now = datetime.now(_tz(sched.timezone))
        print()
        print(source_report(faults, now, args.check_sources))
        # Everything the steps after this one act on is written by the process that KNOWS
        # it. The run used to grep the report above back for a rung prefix and rebuild the
        # comment in a shell, which matched some rungs and not others, silently.
        # The semester is what makes a citation a LINK. Off a runner it is named on the
        # command line; in the semester's own validate-schedule run the checkout is a copy
        # of central, so the org comes from the ambient GitHub environment instead.
        comment = source_comment(
            faults,
            now,
            args.semester_org or os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
        )
        if args.annotate:
            for f in sorted(faults, key=lambda f: -f.severity(now)):
                # Straight to stderr as a workflow command, where Actions renders it
                # against schedule.yml in the commit's own diff view.
                at = f",line={f.lineno}" if f.lineno else ""
                print(
                    f"::warning file={SCHEDULE_PATH}{at}::{f.line()}", file=sys.stderr
                )
            # One boolean, so the next step's `if:` is one comparison rather than a list
            # of rung names that a new rung would silently fall out of.
            _write_output(f"sources_notify={'true' if comment else 'false'}")
        if args.comment_file:
            Path(args.comment_file).write_text(comment)
    # The source check never touches the verdict, exactly as --check-sources promises. A
    # source missing in August is not a broken file, and folding it into `rc` also meant
    # riding the dropped-entry channel - which opens an issue titled "entries the
    # scheduler cannot read" and closes it on the next clean PARSE, whether or not the
    # source was ever staged. The loud rungs are delivered by the semester's digest issue
    # (source_digest), which owns a channel of its own.
    if sched.dropped:
        print(f"\nINVALID: {len(sched.dropped)} entry/ies dropped")
        return 1
    print("\nOK: nothing dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
