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
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .utils import (
    coerce_date,
    default_branch,
    get_file_content,
    log_err,
    repo_exists,
    repo_tree,
)

CONFIG_REPO = "classroom-config"
SCHEDULE_PATH = "schedule.yml"
DEFAULT_TZ = "Europe/Berlin"


# --------------------------------------------------------------------------- pure core


def _tz(name: str | None) -> ZoneInfo:
    """Resolve a timezone name, falling back to the default if it's missing/unknown."""
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


# The date-level coercion (semester bounds, whole-day events) is the shared canonical one
# in utils - `active_today` uses the same, so the two can never drift. Aliased under the
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


@dataclass
class Release:
    """A labelled scheduled entry: when the thing HAPPENS (`event_datetime` in the YAML),
    plus, optionally, `deploy` actions (content copies) - or an `assignment` handout
    synthesised by the scheduler from `assignments.<slug>.handout_datetime`.

    `when` holds the entry's `event_datetime`: what the cohort site's schedule shows AND
    the default fire time for its deploys. An individual deploy may carry its own
    `deploy_datetime` to ship earlier or later than the session it belongs to. An entry
    with no actions at all is inert: it fires nothing and the site shows nothing - a row
    with nothing to release belongs in `events:`."""

    label: str
    # None = the event_datetime is literally `tbc`: the site shows a TBC row and nothing
    # can fire until faculty replace it with a real date.
    when: datetime | None
    deploy: list[Deploy] = field(default_factory=list)
    assignment: str | None = None
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
        """No actions - nothing to fire, and nothing for the site to show."""
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
    """One assignment's whole lifecycle, in one place: `handout_datetime` (when
    student/team repos are provisioned), `due_datetime` (what students see),
    `grading_datetime` (when the snapshot freezes and the autograder fires), `type` and
    `max_team_size` (group assignments)."""

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
    grading_datetime: datetime | None = None  # explicit pin; defaults to due_datetime
    # When to provision one repo per student (or per team - see `type`) from the
    # `<slug>-<tag>` template. The scheduler synthesises a release from this, so it fires
    # exactly like a `releases` entry. None = hand out manually (the button
    # then records the release moment here).
    handout_datetime: datetime | None = None
    # 'group' | 'individual' | None. The COHORT-level declaration of how this assignment
    # fans out; when set it wins over the template's own grading.yml `type:` (the
    # design-time fallback). None = defer to grading.yml (then individual).
    type: str | None = None
    # Group assignments: the team-size cap the welcome repo's "Join team" flow enforces
    # (templates/welcome/team-formation.yml reads it straight from schedule.yml; its
    # default when unset lives there). None = not set here.
    max_team_size: int | None = None


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
    return raw


# The keys each schema level understands. Anything else - a typo (`grading_dateime:`), a
# legacy name (`dest_repo:`), or a whole plan under an unknown top-level key
# (`materials_releases:`) - is silently ignored by the parser and so means something other
# than what faculty wrote; `_flag_unknown_keys` surfaces it so `--validate` catches it.
KNOWN_TOP_LEVEL = frozenset(
    {"timezone", "releases", "semester_start", "semester_end", "assignments", "events"}
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
        "type",
        "max_team_size",
    }
)
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


def _parse_assignments(
    raw: object, tz: ZoneInfo, drops: list[str]
) -> dict[str, AssignmentEntry]:
    # Only the nested {due_datetime, ...} form is accepted - matching the one schema
    # documented everywhere - rather than also silently accepting a bare due-date scalar.
    # A malformed `grading_datetime`/`handout_datetime`/`max_team_size`/`type` keeps the
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
    for slug, entry in mapping.items():
        where = f"assignments.{slug}"
        if not isinstance(entry, dict):
            _drop(
                drops, where, "not a mapping (it needs a nested `due_datetime:`)", cost
            )
            continue
        due = _coerce_datetime(entry.get("due_datetime"), tz, end_of_day=True)
        if due is None:
            _drop(drops, where, "no valid `due_datetime`", cost)
            continue
        source_repo = str(entry.get("course_source_repo") or "").strip()
        if not source_repo:
            _drop(drops, where, "no `course_source_repo`", cost)
            continue
        _flag_unknown_keys(
            drops, entry, KNOWN_ASSIGNMENT, where, "that setting is ignored"
        )
        raw_cap = entry.get("max_team_size")
        cap = None
        if raw_cap is not None:
            try:
                cap = int(raw_cap)
            except (TypeError, ValueError):
                _flag_bad_value(
                    drops,
                    where,
                    "max_team_size",
                    raw_cap,
                    "no cap is set, so the welcome repo's 'Join team' flow falls back "
                    "to its own default team size",
                )
        kind = str(entry.get("type") or "").strip().lower()
        if kind and kind not in ("group", "individual"):
            # A typo'd `type` (e.g. `gruop`) silently falls back to individual, so a group
            # assignment would be provisioned one-repo-per-student. Keep the fallback but
            # surface it, since the functional consequence is otherwise invisible.
            _flag_bad_value(
                drops,
                where,
                "type",
                kind,
                "the assignment is treated as individual - one repo per student, not one "
                "per team (expected 'group' or 'individual')",
            )
        dest = str(entry.get("cohort_dest_repo") or "").strip()
        out[str(slug)] = AssignmentEntry(
            due_datetime=due,
            course_source_repo=source_repo,
            cohort_dest_repo=dest or None,
            grading_datetime=_flagged_datetime(
                entry,
                "grading_datetime",
                tz,
                drops,
                where,
                "grading falls back to the due date - the submission snapshot freezes "
                "and the autograder fires then, not when this says",
                end_of_day=True,
            ),
            handout_datetime=_flagged_datetime(
                entry,
                "handout_datetime",
                tz,
                drops,
                where,
                "the handout NEVER fires - no student or team repos are provisioned "
                "from it, and nobody gets the assignment",
            ),
            # anything other than the two known values -> None, i.e. the grading.yml
            # fallback (flagged above, not silent)
            type=kind if kind in ("group", "individual") else None,
            max_team_size=cap,
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
    """Parse a loaded schedule.yml dict into a Schedule. Pure; tolerant of missing/blank
    fields (a cohort with no schedule.yml behaves exactly as before). Anything it has to
    throw away is recorded in `Schedule.dropped` rather than vanishing - parsing stays
    total, but never silent."""
    meta = meta if isinstance(meta, dict) else {}
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
    term_cost = "the site synthesises term dates, shifting every session row"
    return Schedule(
        timezone=str(tz_name or DEFAULT_TZ),
        releases=_parse_releases(meta.get("releases"), tz, drops),
        # `01/09/2026` coerces to None exactly like an absent key, and the site then
        # SYNTHESISES term dates from what it does know - so a bad separator quietly
        # shifts every weekly session row. Flag it.
        semester_start=_flagged_date(meta, "semester_start", drops, "", term_cost),
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


def entry_for_repo(sched: Schedule, repo: str) -> tuple[str, AssignmentEntry] | None:
    """(slug, entry) for the assignment that hands out from `repo`, or None.

    Callers that start from a REPO name - the autograder, the website - must find its
    schedule entry by matching `course_source_repo`, never by deriving a slug from the
    repo name. The slug is now a free label, so `wk3-regression-f2026` may legitimately be
    keyed `regression`; deriving would silently miss it, and the symptoms are quiet ones
    (no due date on the site, a group assignment provisioned per student)."""
    for slug, entry in sched.assignments.items():
        if entry.course_source_repo == repo:
            return slug, entry
    return None


def grading_datetime_at(sched: Schedule, slug: str) -> datetime | None:
    """The grading pin for `slug` as a tz-aware datetime - the ONE instant at which the
    submission snapshot freezes and the autograder fires, so both always agree.

    An explicit `grading_datetime` wins; else `due_datetime`. None if unscheduled."""
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


def load(cohort_org: str) -> Schedule:
    """Fetch + parse schedule.yml from the cohort's PRIVATE classroom-config repo. A
    pure loader: a missing file returns an empty Schedule silently (every field
    optional everywhere it's read).

    A file that does not PARSE (faculty-editable YAML - an unclosed brace, a bad indent)
    is treated exactly as an absent one: the error is logged loudly, with the parser's own
    line/column, and an empty Schedule is returned. It must never raise: `load` sits under
    the hourly scheduler AND the site sync, and one cohort's typo froze both."""
    content = get_file_content(cohort_org, CONFIG_REPO, SCHEDULE_PATH)
    try:
        meta = yaml.safe_load(content) if content else {}
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
    sched = parse(meta if isinstance(meta, dict) else {})
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
        meta = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        # the parser's own message carries the line/column and the offending snippet
        return None, f"{path} is not valid YAML:\n{exc}"
    if not isinstance(meta, dict):
        return None, f"{path} is valid YAML but not a mapping - it needs top-level keys"
    return parse(meta), None


def _repo_paths(course_org: str, repo: str) -> set[str] | None:
    """Every path in a course-org repo, files and directories alike, in ONE tree fetch.
    An empty set is a repo with nothing in it; `None` is "could not tell".

    Kept distinct from "the repo is not there" (the caller asks `repo_exists` first),
    because the two want opposite handling: an absent repo is a fault worth naming, an
    unreadable one must be passed over in silence. `default_branch` is the fail-loud twin
    on purpose - `get_default_branch` guesses `main` when it cannot read the repo,
    `repo_tree` then 404s on the guess and reports `()`, and every deploy in the plan comes
    back as "no such repo": the exact cry-wolf this check exists to avoid."""
    try:
        return set(repo_tree(course_org, repo, default_branch(course_org, repo)))
    except RuntimeError:
        return None


# How close a missing source has to be to its fire time before it stops being "not
# written yet" and starts being a fault. A term planned up front names paths nobody has
# authored, which is why distance is what separates the normal state from the broken one.
SOURCE_ERROR_WINDOW = timedelta(hours=48)
SOURCE_WARN_WINDOW = timedelta(days=7)


class Severity(IntEnum):
    """How loud a source fault is. ORDERED, and that is the point: every consumer wants to
    compare (worst of a run, has this escalated, is it past the notify bar), and as bare
    strings each of those had to spell the ladder out again through a lookup table."""

    ADVISORY = 0
    WARNING = 1
    ERROR = 2

    def __str__(self) -> str:
        return self.name.lower()


def _window_blurb() -> str:
    """The ladder in one sentence, formatted from the windows themselves so changing one
    cannot leave three hand-written prose copies claiming the old numbers."""
    hours = int(SOURCE_ERROR_WINDOW.total_seconds() // 3600)
    return (
        f"advisory until {SOURCE_WARN_WINDOW.days} days out, then a warning, "
        f"then an ERROR inside {hours}h"
    )


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
    # default: it is half of `key`, so a caller that forgets it would not fail, it would
    # quietly give this fault someone else's identity in the digest's state.
    field: str

    @property
    def key(self) -> str:
        """A stable identity for this fault across runs, so a digest can tell a fault that
        ESCALATED from one that is merely still there. Deliberately excludes `fires`: an
        entry whose date faculty push back is the same fault, at a new distance."""
        return f"{self.where}.{self.field}"

    @property
    def due(self) -> str:
        return f"{self.fires:%a %d %b %Y, %H:%M}" if self.fires else "no date (tbc)"

    def severity(self, now: datetime) -> Severity:
        """How loud this should be at `now` - see SOURCE_ERROR_WINDOW / SOURCE_WARN_WINDOW.

        A fault whose moment has already PASSED stays an error: the copy did not ship, and
        going quiet once the lecture is over is the one thing that must not happen."""
        if self.fires is None:
            return Severity.ADVISORY
        left = self.fires - now
        if left <= SOURCE_ERROR_WINDOW:
            return Severity.ERROR
        if left <= SOURCE_WARN_WINDOW:
            return Severity.WARNING
        return Severity.ADVISORY

    def line(self) -> str:
        """The one-line form, everywhere. It names the FIELD as well as the entry, because
        "something is wrong with lecture-2" is not an instruction - and it is the CLI
        report, on the commit faculty just pushed, that most needs to say so."""
        return f"{self.where} -> {self.field} (due {self.due}): {self.what}"


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
    # (path, where, fires) per source repo, so each repo is fetched once however many
    # deploys point into it.
    wanted: dict[str, list[tuple[str, str, datetime | None]]] = {}
    for release in sched.releases:
        for d in release.deploy:
            wanted.setdefault(d.course_source_repo, []).append(
                (
                    d.course_source_path,
                    f"releases.{release.label}",
                    d.deploy_datetime or release.when,
                )
            )
    for slug, a in sched.assignments.items():
        # An assignment with no handout pin is handed out by hand, so nothing dates it.
        wanted.setdefault(a.course_source_repo, []).append(
            ("", f"assignments.{slug}", a.handout_datetime)
        )

    out: list[SourceFault] = []
    for repo in sorted(wanted):
        # Absent-or-empty is asked separately from unreadable, because they want opposite
        # answers: a repo that is not there is the typo this check exists to catch, while
        # a repo that cannot be READ must be passed over in silence.
        paths = (
            _repo_paths(course_org, repo) if repo_exists(course_org, repo) else set()
        )
        if paths is None:
            continue  # could not read it - say nothing rather than cry wolf
        if not paths:
            out.extend(
                SourceFault(
                    where,
                    f"no repo `{course_org}/{repo}` (or it is empty) - nothing to "
                    f"release from",
                    fires,
                    field="course_source_repo",
                )
                for _, where, fires in wanted[repo]
            )
            continue
        for path, where, fires in wanted[repo]:
            # "" is the assignment case: the repo IS the source, so its existence is all
            # there is to check. `/` and `.` mean the whole repo, likewise.
            clean = path.strip("/").strip()
            if clean in ("", ".") or clean in paths:
                continue
            out.append(
                SourceFault(
                    where,
                    f"`{repo}/{clean}` does not exist yet - this copy ships nothing",
                    fires,
                    field="course_source_path",
                )
            )
    return out


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


_HANDOUT_COMMENT = "   # set automatically by the Release assignment button"
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


def record_handout(cohort_org: str, slug: str, stamp: str | None = None) -> None:
    """Record a manual handout back into schedule.yml (`assignments.<slug>.handout_datetime`),
    so the schedule stays the one record of when every assignment went out - whether
    the cron released it or a person clicked the button. Write-once: an existing
    handout_datetime (scheduled, or recorded by an earlier click) is never modified. Best
    effort - a failure here must never fail the release itself, but it is never silent
    either: a file this can't edit means the handout happened and is on record nowhere."""
    from .utils import log, put_file

    text = get_file_content(cohort_org, CONFIG_REPO, SCHEDULE_PATH) or ""
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
    if put_file(
        cohort_org,
        CONFIG_REPO,
        SCHEDULE_PATH,
        new.encode(),
        f"schedule: record {slug} handout ({stamp})",
    ):
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
        "against schedule.yml, so it shows on the commit's own diff view",
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
        if faults:
            print(f"  {len(faults)} SOURCE(S) NOT IN {args.check_sources} YET:")
            for f in sorted(faults, key=lambda f: -f.severity(now)):
                print(f"    [{f.severity(now)}] {f.line()}")
                if args.annotate:
                    # Straight to stderr as a workflow command, NOT re-derived downstream
                    # from this report's text. The run used to grep the report back for a
                    # severity prefix, which silently matched only half the rungs - the
                    # process that KNOWS the severity is the one that should say it.
                    print(
                        f"::warning file={SCHEDULE_PATH}::{f.line()}",
                        file=sys.stderr,
                    )
            print(
                f"\n  A source you have not written yet looks exactly like this, so this "
                f"is only\n  a fault once its moment is close: {_window_blurb()} - at "
                f"which point it is about to ship nothing."
            )
        else:
            print(f"  every source in the plan exists in {args.check_sources}")
    # The source check never touches the verdict, exactly as --check-sources promises. A
    # source missing in August is not a broken file, and folding it into `rc` also meant
    # riding the dropped-entry channel - which opens an issue titled "entries the
    # scheduler cannot read" and closes it on the next clean PARSE, whether or not the
    # source was ever staged. The error rung is escalated by the hourly pre-flight
    # (scheduler._preflight_sources), which owns a channel of its own.
    if sched.dropped:
        print(f"\nINVALID: {len(sched.dropped)} entry/ies dropped")
        return 1
    print("\nOK: nothing dropped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
