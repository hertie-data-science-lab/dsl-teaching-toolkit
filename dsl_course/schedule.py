"""dsl-course schedule -- the per-cohort classroom-config/schedule.yml, this cohort's
single home for the timed release plan AND the dates other tools display/enforce:

Each block encodes a BEHAVIOUR: `releases` deploy materials, `assignments` have a
lifecycle, `events` are display-only calendar rows.

    timezone: Europe/Berlin          # optional (default Europe/Berlin) - how naive times
                                     # below are interpreted; GitHub cron itself is UTC
    releases:                        # the auto-release plan - label ->
      lecture_02:                    # {event_datetime + deploys}. Each deploy ships at its
        event_datetime: 2026-09-15T10:00   # deploy_datetime (default: the event itself).
        title: Linear regression           # optional, display-only: the session's name,
        description: Least squares by hand # and a sentence about what is in it.
        show_on_site: true                 # optional (default true) - false deploys
        deploy:                            # silently, off the site's schedule.
          - course_source_repo: course-materials-f2026   # course_source_repo + course_source_path
            course_source_path: lectures/02_intro        # are the only required keys;
            cohort_dest_repo: materials                  # cohort_dest_repo, cohort_dest_path
            cohort_dest_path: lectures/02_intro          # and deploy_datetime are optional.
            deploy_datetime: 2026-09-15T09:00
    assignments:                     # each assignment's whole lifecycle. The slug is a
      assignment-1:                  # label; course_source_repo names the COURSE-org repo
        course_source_repo: assignment-1-f2026   # it hands out from, and is REQUIRED.
        title: Linear regression     # optional, display-only: the assignment's name,
                                     # beside the slug on the site
        handout_datetime: 2026-09-22T09:00  # A bare due_datetime is END of day (23:59:59)
        due_datetime: 2026-10-13     # - "due on the 13th" closes at day's end.
        grading_datetime: 2026-10-15 # Snapshot freezes + autograder fires (default: due).
    events:                          # display-only rows - nothing deploys, the site just
      mid-term:                      # shows them. `type` is `exam` or `special_event`
        type: exam                   # (the default when omitted).
        title: MidTerm Exam          # `event_datetime` is a whole day, or a full datetime
        event_datetime: 2026-11-03   # when the start time is known.
      project-clinic:
        title: Project Clinic
        event_datetime: 2026-10-14T10:00
    semester_start: 2026-09-07
    semester_end: 2026-12-18

Every field is optional - a cohort with no schedule.yml (or a blank one) behaves exactly
as before everywhere that reads it (releases are skipped, dates synthesised).

Times are timezone-aware: a naive datetime/date is interpreted in `timezone`; an explicit
offset (e.g. `...T14:00+02:00`) names the same instant and is converted into `timezone`,
so every parsed datetime is already the cohort's own wall clock (what the site shows, and
what it fires at, are then the same number).

Parsing is total but never silent: an entry that is valid YAML yet not a valid schedule
entry (a typo'd key, a missing date) is dropped so the rest of the term still parses, and
recorded in `Schedule.dropped` for `load` to log, `--validate` to fail on, and Check cohort setup
to count.

Validation is offline by design - it parses the file it is given and nothing else, so its
verdict depends on nothing but that file. `--check-sources` bolts an online, ADVISORY
half onto it: whether the repos and paths the plan names exist in the course org yet. That
answer changes week to week (a lecture nobody has written yet is normal in August and a
fault in November), so it is reported alongside the verdict and never folded into it.

Usage:
    python3 -m dsl_course.schedule --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.schedule --cohort-org hertie-dsl-demo-f2026 --validate
    python3 -m dsl_course.schedule --file classroom-config/schedule.yml --validate
    python3 -m dsl_course.schedule --file schedule.yml --validate \\
        --check-sources hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum, IntEnum
from functools import cache
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .course import CONFIG_REPO, coerce_date, is_repo_root
from .gh_contents import get_file_content, get_file_with_sha, put_file, repo_tree
from .log import log, log_err, log_step
from .releaseignore import RELEASEIGNORE, excluded_in_tree
from .repos import default_branch, repo_missing

SCHEDULE_PATH = "schedule.yml"
DEFAULT_TZ = "Europe/Berlin"

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
    """A YAML datetime/date or ISO string -> a datetime in the cohort timezone `tz` (None
    if unparseable). A bare date has no time, so it becomes start-of-day (00:00) or, when
    `end_of_day`, 23:59:59.

    A naive datetime is stamped with `tz`; one written with an explicit offset
    (`...T10:00+00:00`) names the same instant, and is CONVERTED to `tz` here - so every
    datetime this module hands out is already the cohort's wall clock. Instant-preserving,
    so firing and sorting are untouched; what it buys is that no consumer has to re-derive
    the cohort zone to display a time (the site used to thread `tz` through every renderer
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
    return dt.astimezone(tz)  # same instant, expressed in the cohort's own clock


def _coerce_date_or_datetime(value: object, tz: ZoneInfo) -> date | datetime | None:
    """A whole-day value -> `date`; one that carries a time -> a `datetime` in the cohort
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
    """One source->dest copy: a path in a COURSE-org source repo copied into a COHORT-org
    dest repo. `cohort_dest_path` defaults to `course_source_path` (mirror).

    `deploy_datetime` optionally overrides the copy's own ship time; unset, it ships at
    the parent entry's `event_datetime`. This is what disaggregates the class from its
    materials: the entry's `event_datetime` is the session the site announces, a deploy's
    `deploy_datetime` ships the files an hour (or a week) before or after it."""

    course_source_repo: str
    course_source_path: str
    cohort_dest_repo: str = "materials"
    cohort_dest_path: str | None = None
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

    `when` holds the entry's `event_datetime`: what the cohort site's schedule shows AND
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
    # template (each with its own `cohort_dest_repo`) - so the template alone no longer
    # says which assignment is firing, and the key travels with the release rather than
    # being looked up again at the far end.
    assignment_slug: str = ""
    # True on a release synthesised from `assignments.<slug>.solution_datetime`: the same
    # provisioning call, asked additionally to push the template's `solution/` into every
    # repo it already made. Never set from the YAML - the scheduler owns it.
    assignment_solution: bool = False
    title: str = ""  # display-only: the session's name, beside its ordinal on the site
    # display-only: a sentence about the session, shown under its heading on the Lectures
    # tab. `title` names the session, this says what is in it.
    description: str = ""
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
    repos are provisioned), `due_datetime` (what students see), `grading_datetime` (when
    the snapshot freezes and the autograder fires), `solution_datetime` (when the model
    solution goes out).

    What the assignment IS - its shape, its team cap, how it is handed in, how it is
    marked - lives in the assignment's own `grading_config.yml`, on the course template's
    solution branch. The two files were both allowed to declare the shape, and a cohort
    that said one thing while the template said another got repos of one kind graded as
    the other."""

    due_datetime: datetime
    # The COURSE-org repo this assignment hands out from - the template one repo per
    # student (or per team) is generated from. Required and named outright: it used to be
    # derived as `<slug>-<cohort tag>`, which was right almost always and invisible in the
    # file that depended on it. Same meaning as a deploy's `course_source_repo`.
    course_source_repo: str
    # What the COHORT-side artefacts are called - the frozen cohort template repo, the
    # `<name>-<handle>` student repos, the teams.csv key, the snapshot and grades files.
    # None = the entry's slug, which is almost always right. Mirrors a deploy's
    # `cohort_dest_repo`: source names the course side, dest names the cohort side.
    cohort_dest_repo: str | None = None
    # An explicit freeze. Left unset the cutoff is the due date plus the template's
    # `late_window_days` - resolved by `grades.cutoff_at`, which holds the spec this file
    # cannot read, and NOT here: answering it in the parser would shut the door on the due
    # date and refuse every late push the receipts had just promised to accept.
    grading_datetime: datetime | None = None
    # When to provision one repo per student (or per team - see `type`) from the
    # `<slug>-<tag>` template. The scheduler synthesises a release from this, so it fires
    # exactly like a `releases` entry. None = hand out manually (the workflow
    # then records the release moment here).
    handout_datetime: datetime | None = None
    # Display-only: the assignment's NAME, shown beside the slug on the site exactly as a
    # `releases:` entry's `title` sits beside its session ordinal. Declared here rather
    # than left to the template README's `# ` heading, which is the fallback: the README is
    # embargoed until hand-out, so a name that lives only there cannot appear on the
    # schedule that publishes the assignment's dates. "" = fall back to the heading.
    title: str = ""
    # When to push the template's `solution/` folder into every provisioned repo - the
    # scheduled twin of Release assignment's `include_solution` tick. Deliberately NOT
    # defaulted to the due date: a solution released the moment submissions close is a
    # gift to anyone who pushes late, so faculty name the moment or it never fires.
    # None = release the solution by hand, or not at all.
    solution_datetime: datetime | None = None
    # The line each of this entry's keys is written on - see `Deploy.lines`.
    lines: dict[str, int] = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Event:
    """A display-only calendar row: an exam, or any other session the cohort should see
    on the schedule but which releases nothing (a guest lecture, a project clinic).
    Nothing here ever fires - the site renders the row and that is all."""

    label: str
    title: str
    # A bare date = whole day; a datetime = real start time; None = `event_datetime: tbc`
    # (the site shows a TBC row). `tbc: true` next to a real date = provisional, "(TBC)".
    when: date | datetime | None
    # 'exam' | 'special_event'. Exams render as their own (red) row on the site.
    type: str = "special_event"
    tbc: bool = False


@dataclass
class Schedule:
    timezone: str = DEFAULT_TZ
    releases: list[Release] = field(default_factory=list)
    semester_start: date | None = None
    semester_end: date | None = None
    assignments: dict[str, AssignmentEntry] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    # Everything this parse could not use, one human-readable line each, naming the YAML
    # path and what it costs the cohort: entries thrown away outright (`_drop` - no date,
    # no source), and entries KEPT but not as written (`_flag_unknown_keys` for a stray
    # key, `_flag_bad_value` for a value that had to fall back). None of it may vanish
    # quietly: `load` logs each line, `--validate` exits non-zero on them, and Check cohort
    # setup counts them.
    dropped: list[str] = field(default_factory=list)
    # Set by `load` when the file could not be read AS A SCHEDULE at all: the YAML did not
    # parse, or its top level is not a mapping. Distinct from `dropped` (a file that parsed,
    # minus some entries) and from a cohort that simply has no schedule.yml. `load` still
    # returns an empty Schedule so nothing downstream raises - but the hourly scheduler
    # fails its run on this, because an unreadable plan means NOTHING is released, handed
    # out, snapshotted or graded for the cohort, and an hourly green tick is how that goes
    # unnoticed for a term.
    unparseable: bool = False


# `yaml.safe_load` drops positions, and a fault that cannot name the LINE to edit leaves
# faculty scrolling a file they wrote in August. So the loader stamps every mapping with
# the line it starts on and the parse hands that to the entry it builds: one pass, and the
# answer comes from the parser that read the file rather than from a scan of the text
# afterwards - which was a second opinion about what the file says, and gave two deploys
# under one entry the same line.
_LINES = "__lines__"


class _LineLoader(yaml.SafeLoader):
    """SafeLoader that records, under `__lines__`, the 1-based line every key of a mapping
    is written on - plus `""` for the line the mapping itself opens on.

    Per KEY, because that is the granularity a fault cites: `releases.lecture_02 ->
    course_source_path` sends faculty to the `course_source_path:` line, and the copy's
    `course_source_repo:` is a different line of the same block. A reserved key rather
    than a parallel index of YAML paths, because the parse walks the mappings and not the
    paths: every entry meets its own lines where it is built. `_take_lines` removes the
    stamp as the parse consumes it, so no key check and no label loop downstream ever
    sees it."""

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        # `node.value` has been flattened by now, so a merged (`<<:`) key is here too,
        # at the line it was written on in the block it came from.
        lines = {
            str(k.value): k.start_mark.line + 1
            for k, _v in node.value
            if isinstance(k, yaml.ScalarNode)
        }
        lines[""] = node.start_mark.line + 1
        mapping[_LINES] = lines
        return mapping


def _load_yaml(text: str) -> object:
    """`yaml.safe_load` plus the line stamps `parse` reads. Same failure modes exactly -
    `yaml.YAMLError` on a file that does not parse, any object for one that does."""
    return yaml.load(text, _LineLoader)


def _take_lines(mapping: dict) -> dict[str, int]:
    """This mapping's key lines, REMOVING the loader's stamp. `{}` for a mapping this
    module did not load - a dict built by hand in a test, or a caller that parsed the YAML
    itself - which every consumer already treats as "the line is not known"."""
    lines = mapping.pop(_LINES, None)
    return lines if isinstance(lines, dict) else {}


def _line_of(lines: dict[str, int], key: str) -> int | None:
    """The line to cite for a fault about `key`: that key's own line, else the line the
    entry opens on, else None.

    The fallback is for a field the entry does not carry at all. Every fault this module
    reports names a key the entry must have declared to have parsed - but a citation
    pointing at the right BLOCK beats no citation, and beats a link to line 1."""
    return lines.get(key) or lines.get("")


def _drop(drops: list[str], where: str, why: str, cost: str) -> None:
    """Record a thrown-away entry: where it is in the YAML, what is wrong, and what the
    cohort loses by it. The cost is the point - "entry dropped" alone tells faculty
    nothing about whether their term still runs."""
    drops.append(f"{where}: {why} - entry dropped, so {cost}")


def _require_mapping(
    raw: object, drops: list[str], block: str, noun: str, cost: str
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
        )
        return None
    # The block's own lines interest nobody; taking them keeps the stamp out of the label
    # loop that follows, which would otherwise read `__lines__` as an entry.
    _take_lines(raw)
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
        # DEPRECATED and ignored: enrolment codes are mailed on a push to students.csv,
        # not on a window. Still RECOGNISED, because live cohorts carry the block until it
        # is swept out of their schedule.yml by hand, and flagging it as an unknown key
        # would red every one of their validate-schedule runs in the gap. `parse` says so
        # out loud instead.
        "enrolment",
    }
)
KNOWN_RELEASE = frozenset(
    {
        "event_datetime",
        "deploy",
        "assignment",
        "title",
        "description",
        "tbc",
        "show_on_site",
    }
)
KNOWN_DEPLOY = frozenset(
    {
        "course_source_repo",
        "course_source_path",
        "cohort_dest_repo",
        "cohort_dest_path",
        "deploy_datetime",
    }
)
KNOWN_ASSIGNMENT = frozenset(
    {
        "due_datetime",
        "course_source_repo",
        "cohort_dest_repo",
        "grading_datetime",
        "handout_datetime",
        "solution_datetime",
        "title",
    }
)
# Settings that USED to live in an `assignments:` entry and now live in the assignment's
# own `grading_config.yml`, on the course template's solution branch. Flagged BY NAME
# rather than as generic unknown keys: a cohort still carrying `type: group` is not making
# a typo, it is declaring something in a file that no longer reads it, and the message has
# to say where the declaration went.
_GRADING_CONFIG_HOME = "in the assignment's own grading_config.yml, on the course template's `solution` branch"
MOVED_ASSIGNMENT_KEYS = {
    "type": (
        f"`type:` {_GRADING_CONFIG_HOME}",
        (
            "the assignment is handed out and graded in whatever shape grading_config.yml "
            "declares - individual when it declares none"
        ),
    ),
    "max_team_size": (
        f"`max_team_size:` {_GRADING_CONFIG_HOME}",
        "the 'Join team' flow uses the cap declared there, or the course default",
    ),
}
KNOWN_EVENT = frozenset({"type", "title", "event_datetime", "tbc"})


def _flag_unknown_keys(
    drops: list[str], entry: dict, known: frozenset[str], where: str, cost: str
) -> None:
    """Record every key of `entry` not in `known`. Unlike `_drop`, the entry itself is
    KEPT (only the stray key is ignored) - a typo'd or legacy key otherwise passes
    validation while silently changing what the file means. Only called for entries that
    parse; a dropped entry already gets its own line."""
    for key in entry:
        if str(key) not in known:
            loc = f"{where}.{key}" if where else str(key)
            drops.append(f"{loc}: unrecognised key - ignored, so {cost}")


def _flag_bad_value(
    drops: list[str], where: str, key: str, value: object, cost: str
) -> None:
    """Record a key whose value is PRESENT but unusable - a date that doesn't parse, a cap
    that isn't a number, a `type:` that isn't one of the known ones.

    Like `_flag_unknown_keys` (and unlike `_drop`), the entry itself is KEPT: the parser
    falls back exactly as it always has. The fallback is the problem - it is invisible.
    `handout_datetime: 2026-13-01` reads as a scheduled handout and provisions nothing;
    `grading_datetime: nxt week` silently grades at the due date. Both leave a green run
    and a plan that is not the one faculty wrote, so both belong in `dropped`."""
    loc = f"{where}.{key}" if where else str(key)
    drops.append(f"{loc}: unusable value {value!r} - ignored, so {cost}")


def _flagged_datetime(
    entry: dict,
    key: str,
    tz: ZoneInfo,
    drops: list[str],
    where: str,
    cost: str,
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
        _flag_bad_value(drops, where, key, raw, cost)
    return when


def _flagged_date(
    entry: dict, key: str, drops: list[str], where: str, cost: str
) -> date | None:
    """`entry[key]` as a whole-day date, flagging a value that is there but does not
    parse. The date-only twin of `_flagged_datetime`, with the same absent-vs-unreadable
    rule: no key means "not declared", which every caller handles."""
    raw = entry.get(key)
    when = _coerce_date(raw)
    if when is None and raw is not None:
        _flag_bad_value(drops, where, key, raw, cost)
    return when


def _parse_deploy(
    raw: object, tz: ZoneInfo, drops: list[str], label: str
) -> list[Deploy]:
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
        lines = _take_lines(d)
        src_repo, src_path = d.get("course_source_repo"), d.get("course_source_path")
        if not src_repo or not src_path:
            _drop(
                drops,
                where,
                "missing `course_source_repo` and/or `course_source_path`",
                "this copy never ships",
            )
            continue
        dest_path = d.get("cohort_dest_path")
        _flag_unknown_keys(
            drops, d, KNOWN_DEPLOY, where, "that setting is ignored for this copy"
        )
        out.append(
            Deploy(
                course_source_repo=str(src_repo),
                course_source_path=str(src_path),
                cohort_dest_repo=str(d.get("cohort_dest_repo") or "materials"),
                cohort_dest_path=str(dest_path) if dest_path else None,
                deploy_datetime=_flagged_datetime(
                    d,
                    "deploy_datetime",
                    tz,
                    drops,
                    where,
                    "this copy ships at the entry's `event_datetime` instead of the "
                    "time written here",
                ),
                lines=lines,
            )
        )
    return out


def _is_tbc(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() == "tbc"


def _parse_releases(raw: object, tz: ZoneInfo, drops: list[str]) -> list[Release]:
    """Parse `releases:` (label -> {event_datetime + deploys}) into Releases sorted by
    their event_datetime.

    TBC: `event_datetime: tbc` keeps the entry as an UNDATED site row (when=None -
    nothing can fire); `tbc: true` next to a real date keeps everything firing but marks
    the site row "(TBC)". An entry with no date and no tbc can never fire or be shown,
    so it's dropped."""
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
        _take_lines(entry)
        raw_when = entry.get("event_datetime")
        when = _coerce_datetime(raw_when, tz)
        tbc = _is_tbc(raw_when) or entry.get("tbc") is True
        if when is None and not tbc:
            _drop(
                drops,
                where,
                "no valid `event_datetime` (use `tbc` if the date is not settled)",
                "nothing deploys and no site row appears",
            )
            continue
        _flag_unknown_keys(
            drops, entry, KNOWN_RELEASE, where, "that setting is ignored"
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
            )
        assignment = entry.get("assignment")
        out.append(
            Release(
                label=str(label),
                when=when,
                deploy=_parse_deploy(entry.get("deploy"), tz, drops, str(label)),
                assignment=str(assignment) if assignment else None,
                title=str(entry.get("title") or ""),
                description=str(entry.get("description") or ""),
                tbc=tbc,
                show_on_site=entry.get("show_on_site") is not False,
            )
        )
    # Undated (TBC) entries sort to the end of the plan.
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out.sort(key=lambda r: (r.when is None, r.when or epoch))
    return out


def _shared_sources(mapping: dict) -> set[str]:
    """The `course_source_repo`s more than one assignment may legitimately hand out from.

    Two entries on one template is normally a copy-paste, and nothing downstream can tell
    them apart. It IS legitimate when both say, explicitly, what their cohort-side repos
    are called - a resit off the same brief, or one template handed out to two halves of a
    cohort - because `cohort_dest_repo` is what every artefact keys on, and two explicit
    ones cannot collide (`_parse_assignments` refuses that separately). One entry leaving
    it to default is enough to make the pair ambiguous again, so the permission is
    all-or-nothing across the citing entries."""
    citing: dict[str, list[str]] = {}
    for entry in mapping.values():
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("course_source_repo") or "").strip()
        if source:
            citing.setdefault(source, []).append(
                str(entry.get("cohort_dest_repo") or "").strip()
            )
    return {src for src, dests in citing.items() if len(dests) > 1 and all(dests)}


def _parse_assignments(
    raw: object, tz: ZoneInfo, drops: list[str]
) -> dict[str, AssignmentEntry]:
    # Only the nested {due_datetime, ...} form is accepted - matching the one schema
    # documented everywhere - rather than also silently accepting a bare due-date scalar.
    # A malformed `grading_datetime`/`handout_datetime`/`solution_datetime` keeps the
    # entry on its documented fallback, and is flagged (see `_flag_bad_value`).
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
    names: dict[str, str] = {}  # cohort-side name -> the slug that claimed it
    shared = _shared_sources(mapping)  # sources every citing entry names a dest for
    for slug, entry in mapping.items():
        where = f"assignments.{slug}"
        if not isinstance(entry, dict):
            _drop(
                drops, where, "not a mapping (it needs a nested `due_datetime:`)", cost
            )
            continue
        lines = _take_lines(entry)
        due = _coerce_datetime(entry.get("due_datetime"), tz, end_of_day=True)
        if due is None:
            _drop(drops, where, "no valid `due_datetime`", cost)
            continue
        source_repo = str(entry.get("course_source_repo") or "").strip()
        if not source_repo:
            _drop(drops, where, "no `course_source_repo`", cost)
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
                f"the same repo when EVERY one of them sets its own `cohort_dest_repo` "
                f"(a copy-paste?)",
                cost,
            )
            continue
        dest = str(entry.get("cohort_dest_repo") or "").strip()
        # `cohort_name` - `cohort_dest_repo`, else the slug - is what EVERY cohort-side
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
                f"`{name}` is the cohort-side name of assignments.{names[name]} too - "
                f"two assignments cannot share one (the student repos, teams.csv rows, "
                f"snapshot and grading sheet all key on it; set a distinct "
                f"`cohort_dest_repo`)",
                cost,
            )
            continue
        sources[source_repo] = str(slug)
        names[name] = str(slug)
        for moved, (home, moved_cost) in MOVED_ASSIGNMENT_KEYS.items():
            if moved in entry:
                drops.append(
                    f"{where}.{moved}: moved to {home} - ignored here, so {moved_cost}"
                )
        _flag_unknown_keys(
            drops,
            entry,
            KNOWN_ASSIGNMENT | frozenset(MOVED_ASSIGNMENT_KEYS),
            where,
            "that setting is ignored",
        )
        handout = _flagged_datetime(
            entry,
            "handout_datetime",
            tz,
            drops,
            where,
            "the handout NEVER fires - no student or team repos are provisioned "
            "from it, and nobody gets the assignment",
        )
        solution = _flagged_datetime(
            entry,
            "solution_datetime",
            tz,
            drops,
            where,
            "the model solution NEVER ships automatically - it stays on the "
            "template's solution branch until someone ticks include_solution by hand",
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
            )
            solution = None
        out[str(slug)] = AssignmentEntry(
            due_datetime=due,
            course_source_repo=source_repo,
            cohort_dest_repo=dest or None,
            title=str(entry.get("title") or "").strip(),
            grading_datetime=_flagged_datetime(
                entry,
                "grading_datetime",
                tz,
                drops,
                where,
                "grading falls back to the end of the late window - the due date plus "
                "the template's `late_window_days`, and the due date itself when it "
                "declares none. The submission snapshot freezes and the autograder fires "
                "then, not when this says",
                end_of_day=True,
            ),
            handout_datetime=handout,
            solution_datetime=solution,
            lines=lines,
        )
    return out


def _parse_events(raw: object, tz: ZoneInfo, drops: list[str]) -> list[Event]:
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
        _take_lines(entry)
        raw_when = entry.get("event_datetime")
        when = _coerce_date_or_datetime(raw_when, tz)
        tbc = _is_tbc(raw_when) or entry.get("tbc") is True
        if when is None and not tbc:
            _drop(
                drops,
                where,
                "no valid `event_datetime` (use `tbc` if the date is not settled)",
                "the row never appears on the site",
            )
            continue
        _flag_unknown_keys(drops, entry, KNOWN_EVENT, where, "that setting is ignored")
        kind = str(entry.get("type") or "").strip().lower()
        if kind and kind not in ("exam", "special_event"):
            # A typo'd `type` (e.g. `exma`) still shows the row, but as a plain special
            # event - the exam styling, and "this is an exam", quietly disappear.
            _flag_bad_value(
                drops,
                where,
                "type",
                kind,
                "the row is shown as a plain special event, not an exam "
                "(expected 'exam' or 'special_event')",
            )
        out.append(
            Event(
                label=str(label),
                title=str(entry.get("title") or ""),
                when=when,
                # anything other than the two known values -> the display-only default:
                # a typo'd `type` still shows the row (flagged above, not silent)
                type="exam" if kind == "exam" else "special_event",
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


def parse(meta: dict) -> Schedule:
    """Parse a loaded schedule.yml dict into a Schedule. Tolerant of missing/blank fields
    (a cohort with no schedule.yml behaves exactly as before). Anything it has to throw
    away is recorded in `Schedule.dropped` rather than vanishing - parsing stays total,
    but never silent.

    Pure but for one line: a deprecated `enrolment:` block is announced to the log rather
    than recorded as a drop, because a drop reds `--validate` and every live cohort still
    carries the block (see KNOWN_TOP_LEVEL)."""
    meta = meta if isinstance(meta, dict) else {}
    _take_lines(meta)
    drops: list[str] = []
    # A whole plan under an unknown top-level key (`materials_releases:` instead of
    # `releases:`) otherwise validates as "OK: nothing dropped" with zero releases - the
    # worst kind of silent failure, since the file looks full. Flag it here.
    _flag_unknown_keys(
        drops, meta, KNOWN_TOP_LEVEL, "", "nothing it contains is scheduled or shown"
    )
    tz_name = meta.get("timezone")
    tz = _tz(tz_name)
    if tz_name and str(tz_name).strip() != str(tz):
        drops.append(
            f"timezone: `{tz_name}` is not a known zone - falling back to {DEFAULT_TZ}, "
            f"so every naive time below is read in {DEFAULT_TZ}"
        )
    if meta.get("enrolment") is not None:
        log_step(
            "enrolment: in schedule.yml is DEPRECATED and does nothing - enrolment codes "
            "are mailed on a push to students.csv now. Delete the block."
        )
    term_cost = "the site synthesises term dates, shifting every session row"
    semester_start = _flagged_date(meta, "semester_start", drops, "", term_cost)
    return Schedule(
        timezone=str(tz_name or DEFAULT_TZ),
        releases=_parse_releases(meta.get("releases"), tz, drops),
        # `01/09/2026` coerces to None exactly like an absent key, and the site then
        # SYNTHESISES term dates from what it does know - so a bad separator quietly
        # shifts every weekly session row. Flag it.
        semester_start=semester_start,
        semester_end=_flagged_date(meta, "semester_end", drops, "", term_cost),
        assignments=_parse_assignments(meta.get("assignments"), tz, drops),
        events=_parse_events(meta.get("events"), tz, drops),
        dropped=drops,
    )


def cohort_name(slug: str, entry: AssignmentEntry) -> str:
    """The ONE cohort-side name for an assignment: `cohort_dest_repo`, else its slug.
    Every cohort-side artefact keys on it - generated repos, teams.csv, snapshots,
    autograde markers, grades - and the scheduler's fire-once check must agree with what
    collect writes, so both resolve it here rather than each deriving its own."""
    return entry.cohort_dest_repo or slug


def entries_for_repo(sched: Schedule, repo: str) -> list[tuple[str, AssignmentEntry]]:
    """Every `(slug, entry)` that hands out from `repo`, in the plan's own order.

    Usually one. Two is legitimate when each names its own `cohort_dest_repo` (see
    `_shared_sources`) - a resit off the same brief, one template split across two halves
    of a cohort - and a caller that acts on ONE of them has to say which, because the two
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
    second: only use it where ANY of them will do. Anything that writes cohort-side state
    goes through `entries_for_repo` and refuses the ambiguity."""
    found = entries_for_repo(sched, repo)
    return found[0] if found else None


def pick_entry(
    sched: Schedule, repo: str, slug: str = ""
) -> tuple[str, AssignmentEntry] | None | str:
    """`(slug, entry)` for the assignment `repo` hands out from, `None` when the plan does
    not name it, or an ERROR MESSAGE (a `str`) when it names more than one and `slug` does
    not say which.

    The one place the ambiguity is resolved, so the handout and the collection cannot
    disagree about which of two entries they are acting on. `slug` is the SCHEDULE KEY, the
    same one `teams.csv` and the grading sheet are keyed on."""
    found = entries_for_repo(sched, repo)
    if slug:
        chosen = [pair for pair in found if pair[0] == slug]
        if chosen:
            return chosen[0]
        return (
            f"`{slug}` is not an assignment in this cohort's schedule.yml that hands out "
            f"from {repo} (it names {', '.join(s for s, _ in found) or 'none'})"
        )
    if len(found) > 1:
        return (
            f"{repo} is handed out by {len(found)} assignments in this cohort's "
            f"schedule.yml ({', '.join(s for s, _ in found)}) - say which with `slug`, "
            f"since they make different repos and keep different grades"
        )
    return found[0] if found else None


def grading_datetime_at(sched: Schedule, slug: str) -> datetime | None:
    """The grading pin as far as THIS FILE can tell: an explicit `grading_datetime`, else
    `due_datetime`. None if unscheduled.

    The spec-less fallback, and only correct for an assignment with no late window. The
    window lives in the template's `grading_config.yml`, which `schedule` cannot read, so
    everything that freezes or grades goes through `grades.cutoff_at` instead - answering
    this question here would shut the door on the due date and refuse every late push the
    receipts had just promised to accept."""
    entry = sched.assignments.get(slug)
    if entry is None:
        return None
    if entry.grading_datetime is not None:
        return entry.grading_datetime
    return entry.due_datetime


def grading_datetime_iso(sched: Schedule, slug: str) -> str | None:
    """`grading_datetime_at` as an ISO string, or None if unscheduled."""
    at = grading_datetime_at(sched, slug)
    return at.isoformat() if at is not None else None


# ---------------------------------------------------------------------- gh/git wiring


@cache
def _schedule_text(cohort_org: str) -> str | None:
    """schedule.yml's text, read ONCE per cohort per process.

    An hourly tick reads the plan repeatedly - the scheduler itself, then again inside
    every handout and collection it fires - for a file that changes only when a person
    edits it or `record_handout` writes it. The TEXT is memoised rather than the parsed
    `Schedule`, so every caller gets its own object (nothing shared to mutate) and the
    loud "N entries DROPPED" report is still printed once per caller, exactly as before.
    `record_handout` clears it after its write; tests/conftest.py clears it between
    tests."""
    return get_file_content(cohort_org, CONFIG_REPO, SCHEDULE_PATH)


def load(cohort_org: str) -> Schedule:
    """Fetch + parse schedule.yml from the cohort's PRIVATE classroom-config repo. A
    pure loader: a missing file returns an empty Schedule silently (every field
    optional everywhere it's read).

    A file that does not PARSE (faculty-editable YAML - an unclosed brace, a bad indent)
    is treated exactly as an absent one: the error is logged loudly, with the parser's own
    line/column, and an empty Schedule is returned. It must never raise: `load` sits under
    the hourly scheduler AND the site sync, and one cohort's typo froze both."""
    content = _schedule_text(cohort_org)
    unparseable = False
    try:
        meta = _load_yaml(content) if content else {}
    except yaml.YAMLError as exc:
        log_err(
            f"{cohort_org}/{CONFIG_REPO}/{SCHEDULE_PATH} is NOT valid YAML - the whole "
            f"schedule is ignored:"
        )
        # the parser's own message: it carries the line/column and the offending snippet
        log_err(str(exc))
        log_err(
            f"fix {CONFIG_REPO}/{SCHEDULE_PATH} on main in {cohort_org} - until then "
            f"NOTHING is scheduled for this cohort (no releases, no handouts, no deadline "
            f"snapshots, no autograding) and the site builds without schedule data."
        )
        meta = {}
        unparseable = True
    if meta is not None and not isinstance(meta, dict):
        # Valid YAML, but not a schedule: a bare list, or a stray document separator that
        # left a string at the top level. Same consequence as a parse failure - nothing in
        # the file is read - so it must not read as an empty plan either.
        log_err(
            f"{cohort_org}/{CONFIG_REPO}/{SCHEDULE_PATH} parses as "
            f"{type(meta).__name__}, not a mapping - the whole schedule is ignored. "
            f"Its top level must be keys like `releases:` / `assignments:`."
        )
        unparseable = True
    sched = parse(meta if isinstance(meta, dict) else {})
    sched.unparseable = unparseable
    if sched.dropped:
        # Loud, because this is the failure faculty cannot see: the file is valid YAML and
        # the run goes green, but an entry they wrote is not in the plan. Every caller
        # comes through here - the hourly scheduler, the site sync, Check cohort setup - so
        # saying it once here says it everywhere.
        log_err(
            f"{cohort_org}/{CONFIG_REPO}/{SCHEDULE_PATH}: {len(sched.dropped)} entry/ies "
            f"DROPPED - they parse as YAML but not as schedule entries:"
        )
        for line in sched.dropped:
            log_err(f"  {line}")
        log_err(f"fix them on main in {cohort_org}; everything else is unaffected.")
    return sched


def load_file(path: str) -> tuple[Schedule | None, str | None]:
    """Parse a schedule.yml from DISK: returns (schedule, None), or (None, error) when the
    file is missing or is not valid YAML.

    The opposite stance to `load`, deliberately. `load` treats an unparseable cohort file
    as an absent one, because it sits under the hourly cron and one typo must not be able
    to freeze a cohort. Here the caller is a validator whose whole job is to fail, so a
    broken file is an error and not an empty schedule."""
    p = Path(path)
    try:
        text = p.read_text()
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    try:
        meta = _load_yaml(text) or {}
    except yaml.YAMLError as exc:
        # the parser's own message carries the line/column and the offending snippet
        return None, f"{path} is not valid YAML:\n{exc}"
    if not isinstance(meta, dict):
        return None, f"{path} is valid YAML but not a mapping - it needs top-level keys"
    return parse(meta), None


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


# How close a missing source has to be to its fire time before it stops being "not
# written yet" and starts being a fault. A term planned up front names paths nobody has
# authored, which is why distance is what separates the normal state from the broken one.
# Three windows rather than one, because how loudly it is said changes as the moment
# approaches: the first rung opens the digest issue and mails the people git names, the
# second says how little time is left, and the maintainer joins at the third. They
# are deliberately tight - a day, half a day, a quarter of a day - because a source is
# staged in minutes once somebody knows, and a week of warnings is a week of ignoring them.
SOURCE_WARN_WINDOW = timedelta(hours=24)
SOURCE_URGENT_WINDOW = timedelta(hours=12)
SOURCE_CRITICAL_WINDOW = timedelta(hours=6)


class Severity(IntEnum):
    """How loud a source fault is. ORDERED, and that is the point: every consumer wants to
    compare (worst of a run, has this escalated, is it past the notify bar), and as bare
    strings each of those had to spell the ladder out again through a lookup table."""

    ADVISORY = 0
    WARNING = 1
    URGENT = 2
    CRITICAL = 3
    MISSED = 4

    def __str__(self) -> str:
        return self.name.lower()


# The rung at which anything is said at all - the digest issue opens, its comment goes out
# and the mail beside it is addressed. Below it the fault is real and listed, but a session
# nobody has written yet is the normal state of a term planned months ahead, so it earns no
# notification. Here rather than in either notifier because both answer to it and they must
# answer to the SAME one.
NOTIFY_FROM = Severity.WARNING


def hours(window: timedelta) -> int:
    """A window in whole hours. Public because every surface that names one - a rung
    heading, a subject suffix, a prose blurb, the commit comment - formats it from the
    constant rather than typing the number, so moving a rung cannot leave a stale 24h in
    somebody's inbox."""
    return int(window.total_seconds() // 3600)


def _window_blurb() -> str:
    """The ladder in one sentence, formatted from the windows themselves so changing one
    cannot leave three hand-written prose copies claiming the old numbers."""
    return (
        f"advisory until {hours(SOURCE_WARN_WINDOW)}h out, then a warning, urgent "
        f"inside {hours(SOURCE_URGENT_WINDOW)}h, critical inside "
        f"{hours(SOURCE_CRITICAL_WINDOW)}h, missed once it has fired"
    )


def in_cohort_zone(sched: Schedule, when: datetime) -> datetime:
    """The same instant, told in the cohort's own zone.

    The scheduler ticks in UTC, but everything a notification says about time is local by
    definition: a deadline faculty wrote as 08:00 Berlin, and a quiet window where 02:00
    means somebody's actual night rather than 02:00 in a datacentre."""
    return when.astimezone(_tz(sched.timezone))


def zone_name(when: datetime) -> str:
    """The zone as faculty wrote it in schedule.yml (`Europe/Berlin`), not the abbreviation
    in force that week.

    A notification that says 08:00 without saying whose 08:00 is a notification about
    nothing, and `%Z` answers `CEST` - which is not a string anybody can look up against
    the `timezone:` line they typed."""
    return getattr(when.tzinfo, "key", "") or when.strftime("%Z")


class FaultKind(Enum):
    """What is WRONG with a source, as against which key to go and edit.

    The field cannot carry this: a path that is absent and a path a `.releaseignore`
    withholds are both `course_source_path`, and they want opposite instructions - push
    the files, or stop holding back the ones already there. Sniffed off the field name and
    a bool flag, each surface grew its own copy of the guess."""

    MISSING_REPO = "missing repo"
    MISSING_PATH = "missing path"
    WITHHELD = "withheld"


_CREATE_REPO = (
    "create the repo named on {at} in {course_org} and push its files, or correct the "
    "line above."
)
_CREATE_TEMPLATE = (
    "create the assignment template repo named on {at} in {course_org} and push the "
    "starter files to it, or correct the line above."
)
# The one sentence that would put each fault right, keyed on the three things it actually
# depends on: the KIND, whether the entry hands out an assignment template (a repo to
# create, not a folder to fill), and whether the moment has already passed - once a
# release has fired, "fix it by Wednesday" is no longer the instruction. A table rather
# than a chain of `if`s, so a fourth kind cannot quietly inherit a sentence meant for
# another. Combinations the check cannot produce are absent: only a deploy names a path,
# and a withheld path caps at WARNING and so never fires.
_FIX: dict[tuple[FaultKind, bool, bool], str] = {
    (FaultKind.WITHHELD, False, False): "remove the pattern, or change {field}.",
    (FaultKind.MISSING_REPO, False, False): _CREATE_REPO,
    (FaultKind.MISSING_REPO, False, True): _CREATE_REPO,
    (FaultKind.MISSING_REPO, True, False): _CREATE_TEMPLATE,
    (FaultKind.MISSING_REPO, True, True): _CREATE_TEMPLATE,
    (FaultKind.MISSING_PATH, False, False): (
        "push the materials to that folder in {course_org}/{repo}, or correct the path "
        "on the line above."
    ),
    (FaultKind.MISSING_PATH, False, True): (
        "push the materials to that folder now - the next 15-minute tick releases them. "
        "Nothing else is needed."
    ),
}


@dataclass
class SourceFault:
    """One source in the plan that is not in the course org, and when it is needed.

    `fires` is what makes it actionable: the same missing folder is a note in August and a
    failure the day before the lecture. None = nothing pins it to a moment (an undated
    `tbc` entry, or an assignment handed out by hand), so it can never escalate."""

    where: str  # the YAML path, e.g. "releases.lecture-2"
    what: str  # what is missing, e.g. "`cm/lectures/02_b` does not exist yet"
    fires: datetime | None
    # The key to go and edit - `course_source_path` or `course_source_repo`. Naming the
    # field is what turns "something is wrong with lecture-2" into an instruction. No
    # default: it is part of `key`, so a caller that forgot it would not fail, it would
    # quietly give this fault someone else's identity in the digest's state.
    field: str
    # Which of the three things went wrong - see `FaultKind`. No default for the same
    # reason: the remedy hangs off it, and a wrong guess reads as a real instruction.
    kind: FaultKind
    # The loudest this fault may ever get. A MISSING source climbs the whole ladder,
    # because the copy will not ship and nobody meant that. A source a `.releaseignore`
    # withholds is a decision faculty already made, so it caps at WARNING: it is listed,
    # and it never earns anyone an email at 24h or a "this did not ship" once its moment
    # has passed.
    ceiling: Severity = Severity.MISSED
    # The line of schedule.yml the entry is written on, as the parser saw it (see
    # `_LineLoader`). Every surface turns it into `schedule.yml:36` and a deep link,
    # because the entry name alone still leaves faculty scrolling a file they wrote in
    # August.
    lineno: int | None = None
    # The source repo the entry names (`course-materials-f2026`). `what` spells it inside
    # a `<org>/<repo>/<path>` phrase, which reads well and parses badly - the fault mail
    # has to name `<course_org>/<repo>` as the place to go and stage the thing.
    repo: str = ""
    # The path inside that repo, where the entry names one (an assignment names only the
    # repo). Carried rather than re-read out of `what`, so a link into the repo is built
    # from the value the check used and not from parsing a sentence.
    path: str = ""

    @property
    def is_assignment(self) -> bool:
        """Whether the entry hands out an assignment template rather than deploying a
        folder. Asked once here: two surfaces sniffed `where` for the prefix themselves,
        which is a parse of a display string in the middle of deciding what to tell
        somebody."""
        return self.where.startswith("assignments.")

    @property
    def key(self) -> str:
        """A stable identity for this fault across runs, so a digest can tell a fault that
        ESCALATED from one that is merely still there. Deliberately excludes `fires`: an
        entry whose date faculty push back is the same fault, at a new distance.

        The PATH is part of it, because two deploys under one entry are two faults: keyed
        on the entry alone the second inherited the first's recorded rung, so neither
        could appear, escalate or clear on its own."""
        if not self.path:
            return f"{self.where}.{self.field}"
        return f"{self.where}[{self.path}].{self.field}"

    @property
    def at(self) -> str:
        """`schedule.yml:36`, or a bare `schedule.yml` when the line is not known.

        The citation every surface shows - the mail, the digest body and comment, the CLI
        report, the fix sentence - so all of them cite the file the same way."""
        return f"{SCHEDULE_PATH}:{self.lineno}" if self.lineno else SCHEDULE_PATH

    def cite(self, cohort_org: str = "") -> str:
        """`at` for a MARKDOWN surface: a link to the exact line where the line and the
        cohort are both known, plain code where either is not.

        Every markdown surface - the digest body, its transition comment, the commit
        comment on the push - cites the file through this, so the fix is one click from
        wherever somebody first hears about it rather than a scroll through a file they
        wrote in August."""
        url = deep_link(cohort_org, self)
        return f"[`{self.at}`]({url})" if url else f"`{self.at}`"

    @property
    def due(self) -> str:
        """The moment this fault bites, zone and all - see `zone_name`."""
        if self.fires is None:
            return "no date (tbc)"
        return f"{self.fires:%a %d %b %Y, %H:%M} {zone_name(self.fires)}"

    @property
    def due_short(self) -> str:
        """`due` without the year - the subject-line form, where every character is paid
        for by the sender's name beside it."""
        if self.fires is None:
            return "no date (tbc)"
        return (
            f"{self.fires:%a} {self.fires.day} {self.fires:%b} "
            f"{self.fires:%H:%M} {zone_name(self.fires)}"
        )

    @property
    def moment(self) -> str:
        """What the date IS: a release ships, an assignment is handed out."""
        return "handout" if self.is_assignment else "release"

    def severity(self, now: datetime) -> Severity:
        """How loud this should be at `now` - see the SOURCE_*_WINDOW constants.

        A fault whose moment has already PASSED is MISSED and stays there: the copy did
        not ship, and going quiet once the lecture is over is the one thing that must not
        happen. The rungs decide who is TOLD (source_digest, and the mail beside it); none
        of them touches an exit code, because a source nobody has written yet is a content
        fault and the red X belongs to the run itself."""
        if self.fires is None:
            return Severity.ADVISORY
        left = self.fires - now
        if left <= timedelta(0):
            rung = Severity.MISSED
        elif left <= SOURCE_CRITICAL_WINDOW:
            rung = Severity.CRITICAL
        elif left <= SOURCE_URGENT_WINDOW:
            rung = Severity.URGENT
        elif left <= SOURCE_WARN_WINDOW:
            rung = Severity.WARNING
        else:
            rung = Severity.ADVISORY
        return min(rung, self.ceiling)

    def fix(self, course_org: str, rung: Severity) -> str:
        """What would put this right, in one sentence, lower-case and unpunctuated at the
        front so a caller can prefix it with `fix:`.

        Here rather than in the notifier because BOTH channels say it - the digest issue
        body and the mail - and a fault whose issue and whose email disagree about the
        remedy is worse than either on its own."""
        fired = rung is Severity.MISSED
        # Falls back to the not-yet-fired sentence for a (kind, entry, rung) triple the
        # table does not list. Unreachable today; a KeyError inside a release cron is not
        # the way to find out that stopped being true.
        template = (
            _FIX.get((self.kind, self.is_assignment, fired))
            or _FIX[self.kind, False, False]
        )
        return template.format(
            at=self.at, course_org=course_org, repo=self.repo, field=self.field
        )

    def line(self, cite: str | None = None) -> str:
        """The one-line form, everywhere: the entry, the field, the line, what is wrong and
        when it bites.

        It names the FIELD as well as the entry, because "something is wrong with
        lecture-2" is not an instruction. Dash-separated, in this order, because the
        commit-time validator lists these lines verbatim (see `source_comment`).

        `cite` replaces the plain `schedule.yml:36` with the caller's own rendering of it -
        the markdown deep link, on the surfaces that are markdown. The rest of the line is
        the same line, because a reader comparing the commit comment with the run summary
        is reading about the same fault."""
        at = f" - {cite or self.at}" if self.lineno else ""
        when = f"fires {self.due}" if self.fires else self.due
        return f"{self.where} -> {self.field}{at} - {self.what} - {when}"


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
        return _line_of(self.lines, field)


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
                    lineno=w.lineno("course_source_path"),
                    repo=repo,
                    path=clean,
                )
            )
    return out


def deep_link(cohort_org: str, fault: SourceFault) -> str | None:
    """The GitHub URL of the exact line to edit, or None when the line - or the cohort it
    is in - is not known.

    `main` is hard-coded because that is the only branch anything reads schedule.yml from,
    the cohort's own workflows included."""
    if not cohort_org or not fault.lineno:
        return None
    return (
        f"https://github.com/{cohort_org}/{CONFIG_REPO}/blob/main/{SCHEDULE_PATH}"
        f"#L{fault.lineno}"
    )


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
    faults: list[SourceFault], now: datetime, cohort_org: str = ""
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

    `cohort_org` turns each citation into a link at the line (`SourceFault.cite`). A
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
            *(f"- {f.line(f.cite(cohort_org))}" for f in coming),
        ]
    if fired:
        out += [
            f"{len(fired)} planned release(s) have already fired with nothing to ship:",
            *(f"- {f.line(f.cite(cohort_org))}" for f in fired),
        ]
    out += ["", "You will get one email about each as its deadline nears."]
    return "\n".join(out)


def worst_severity(faults: list[SourceFault], now: datetime) -> Severity | None:
    """The loudest severity among `faults` at `now`, or None when there are none. A plain
    `max` - which is the whole reason Severity is ordered rather than a bare string."""
    return max((f.severity(now) for f in faults), default=None)


def _validate_report(sched: Schedule, source: str) -> str:
    """What the parser UNDERSTOOD, followed by anything it threw away.

    Reporting the totals matters as much as reporting the drops: a well-formed entry with
    the wrong date is invisible to validation, but "4 assignments" when you wrote five is
    not. This is what a reader sees in a run summary, so it stays plain text."""
    lines = [
        f"Parsed {source}",
        f"  term {sched.semester_start} -> {sched.semester_end}  ({sched.timezone})",
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
    cohort_org: str, slug: str, stamp: str, body: str, sha: str | None
) -> bool:
    """Write the recorded handout over schedule.yml at the sha its text was READ at, so a
    faculty edit committed during the run is refused rather than reverted; on a refusal,
    re-read, re-apply the handout to the fresh text and try once more."""
    message = f"schedule: record {slug} handout ({stamp})"

    def write(text: str, at: str | None) -> bool:
        return put_file(
            cohort_org,
            CONFIG_REPO,
            SCHEDULE_PATH,
            text.encode(),
            message,
            expected_sha=at,
        )

    if write(body, sha):
        return True
    log_err(
        f"{SCHEDULE_PATH} in {cohort_org} was edited while {slug} was being handed out - "
        f"re-reading and retrying once"
    )
    read = get_file_with_sha(cohort_org, CONFIG_REPO, SCHEDULE_PATH)
    if read is None:
        return False
    fresh, fresh_sha = read
    rebuilt = _insert_handout(fresh, slug, stamp)
    if rebuilt is None:
        return True  # the edit that beat us recorded the same handout
    if isinstance(rebuilt, _Declined):
        return False
    return write(rebuilt, fresh_sha)


def record_handout(cohort_org: str, slug: str, stamp: str | None = None) -> None:
    """Record a manual handout back into schedule.yml (`assignments.<slug>.handout_datetime`),
    so the schedule stays the one record of when every assignment went out - whether
    the cron released it or a person ran the workflow. Write-once: an existing
    handout_datetime (scheduled, or recorded by an earlier run) is never modified. Best
    effort - a failure here must never fail the release itself, but it is never silent
    either: a file this can't edit means the handout happened and is on record nowhere.

    The edit is made against a FRESH read and written with that read's sha, so a faculty
    edit committed during a long run is refused rather than reverted; one retry re-reads
    and re-applies (as `enrol_codes.write_codes` does)."""
    read = get_file_with_sha(cohort_org, CONFIG_REPO, SCHEDULE_PATH)
    text, sha = read if read is not None else ("", None)
    # This is the one writer of schedule.yml inside a run, so it is the one place the
    # per-process read memo can go stale. Dropped up front: every path below either
    # returns without writing or writes, and a memo cleared once too often only costs a
    # read, where one held too long hands the next caller a plan missing this handout.
    _schedule_text.cache_clear()
    if stamp is None:
        # the release moment, in the cohort's own timezone (naive, like every other
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
            f"could NOT record the {slug} handout in {cohort_org}/{CONFIG_REPO}/"
            f"{SCHEDULE_PATH}: its `assignments:` block is authored in a shape this edit "
            f"cannot extend safely (a flow mapping). The handout went out at {stamp} but "
            f"is on record nowhere - add `handout_datetime: {stamp}` to "
            f"`assignments.{slug}` by hand."
        )
        return
    if new is None:
        return  # already recorded - write-once, nothing to do
    if _put_handout(cohort_org, slug, stamp, new, sha):
        log(f"  recorded handout in {CONFIG_REPO}/{SCHEDULE_PATH}: {slug} @ {stamp}")
    else:
        # Same fault as the DECLINED branch above, one step later: the handout HAPPENED
        # and the write of its record is what failed. Best-effort stays (the repos are
        # out; nothing here raises), but it may not be silent.
        log_err(
            f"could NOT record the {slug} handout in {cohort_org}/{CONFIG_REPO}/"
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
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cohort-org", help="fetch schedule.yml from a cohort org")
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
        "nobody has written yet is the normal state of a term planned up front",
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
        "--comment-file",
        metavar="PATH",
        help="write the comment to leave on the push (the faults a human is told about, "
        "ready to post) to PATH, empty when there is nothing to say",
    )
    args = parser.parse_args()

    if args.file:
        sched, error = load_file(args.file)
        if error is not None:
            # Unlike a cohort fetch, a broken FILE is a hard failure - see load_file.
            log_err(error)
            print(f"INVALID: {args.file} could not be parsed")
            return 1
        source_name = args.file
    else:
        # A cohort fetch reads schedule.yml over the API: absent is an empty Schedule,
        # but an unreadable one raises - report it as a line, not a traceback.
        try:
            sched = load(args.cohort_org)
        except RuntimeError as exc:
            log_err(str(exc))
            return 1
        source_name = f"{args.cohort_org}/{SCHEDULE_PATH}"

    if not args.validate:
        print(json.dumps(asdict(sched), indent=2, default=str))
        return 0
    # Report what was UNDERSTOOD as well as what was dropped: validation cannot catch a
    # well-formed entry with the wrong date, but a count that is one short is visible.
    print(_validate_report(sched, source_name))
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
        # The cohort is what makes a citation a LINK. Off a runner it is named on the
        # command line; in the cohort's own validate-schedule run the checkout is a copy
        # of central, so the org comes from the ambient GitHub environment instead.
        comment = source_comment(
            faults,
            now,
            args.cohort_org or os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
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
    # source was ever staged. The loud rungs are delivered by the cohort's digest issue
    # (source_digest), which owns a channel of its own.
    if sched.dropped:
        print(f"\nINVALID: {len(sched.dropped)} entry/ies dropped")
        return 1
    print("\nOK: nothing dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
