"""dsl-course config digest -- one self-updating issue per hand-edited file per cohort,
for everything in it a human has to fix.

The problem this solves is notification volume, not detection. The parsers already find
every fault; a term written up front has dozens of missing sources, all of them normal,
and any scheme that files a ticket per fault (or comments on every hourly tick) buries the
one that matters under the twenty that don't.

ONE ENGINE, one issue per file: `Digest` says which file, which issue title and which
team, and everything below works the same for a source the release plan cites and for a
students.csv row nobody can be enrolled from. The two differ in their CLOCK, and that is
all: a source climbs a ladder as its moment approaches (`Digest.scheduled`), while an
immediate fault is at the notify bar from the moment it appears and is repeated at 48
hours and 7 days (AGE_REMINDERS) until somebody fixes it.

So the issue is STATE and its comments are EVENTS:

- the **body** is rewritten from scratch on every run and always shows the current list,
  grouped by rung. GitHub does not email on a body edit, so this is free to run hourly.
- a **comment** is posted when a fault APPEARS at the quietest reported rung, when one
  ESCALATES towards its deadline, and when one CLEARS (see `_comment`).
- the **mail** beside it (`notify`) goes to the same people on every reported rung, and
  neither it nor the comment goes out overnight: see `in_quiet_hours`.
- the issue **closes itself** when the last fault clears.

Previous state rides along in the body as an HTML comment (invisible when rendered), so
the digest needs no committed state file and no database: the issue IS the record. On a
tick that has to re-OPEN the issue, the state of the newest CLOSED one with the same title
is adopted, so an issue somebody closed by hand does not report every standing fault as
new and mail the cohort about all of it again.

Who is @mentioned is decided by git, not by the team: `notify.route` names the planner of
the line and the last committer of the repo, and the same people are the mail's To line.
That answer costs several API reads, so it is asked for only on a tick with something to
say, and the logins it gave ride in the body too - see `resolve_mention`. The cohort's
instructors team is the fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

from .central import CENTRAL, CENTRAL_REF
from .discovery import central_ref_for
from .faults import (
    NOTIFY_FROM,
    SOURCE_CRITICAL_WINDOW,
    SOURCE_URGENT_WINDOW,
    SOURCE_WARN_WINDOW,
    FaultKind,
    Severity,
    hours,
    zone_name,
)
from .issues import close_issues_titled, find_issues, issue_url, upsert_issue
from .log import log_err, log_ok, log_step
from .roster import ROSTER_PATH
from .schedule import CONFIG_REPO, SourceFault, worst_severity
from .sync_faculty import COHORT_PEOPLE_PATH
from .teams import TEAMS_PATH


@dataclass(frozen=True)
class Digest:
    """One self-updating issue: which file it is about, what it is called, and how its
    faults keep time.

    `title` is STABLE, because every lookup of the issue searches for this exact string -
    a title that varied with the faults would never match, and every run would open a new
    issue. The two schedule.yml digests keep the titles their issues already carry.

    `state_key` names the marker the body carries its state in. It is `source` for all of
    them and must stay that way for the two that predate this engine: the marker name is
    the key to what an OPEN issue has already reported, and renaming it would read as
    "nothing recorded" and re-announce - and re-mail - every standing fault in every live
    cohort.

    `scheduled` says the faults in it have fire times: they climb the ladder, their
    notifications are held overnight, and they are never age-reminded (the deadline does
    that). An immediate digest is the other way round on all three counts."""

    title: str
    file: str
    doc: str
    state_key: str = "source"
    repo: str = CONFIG_REPO
    cc_team: str = "instructors"
    scheduled: bool = False

    def cite_file(self) -> str:
        return f"`{self.repo}/{self.file}`"


# The digests that are not schedule.yml's (those two are declared in `source_digest`,
# which owns the CLI the validate workflow calls). One per FILE, because that is what a
# reader is asked to go and fix and what the mail's subject names; a second problem class
# in one file would only earn its own issue if it kept different time, as schedule.yml's
# two do.
PEOPLE = Digest(
    title="people.yml has entries the sync cannot use",
    file=COHORT_PEOPLE_PATH,
    doc="docs/05-manage-teaching-team.md",
)
ROSTER = Digest(
    title="students.csv has rows the toolkit cannot use",
    file=ROSTER_PATH,
    doc="docs/06-enrol-students-to-cohort.md",
)
TEAMS = Digest(
    title="teams.csv has rows the toolkit cannot use",
    file=TEAMS_PATH,
    doc="docs/09-release-assignment-to-cohort.md",
)

# When an immediate fault is said again. It cannot escalate on its own - nothing about it
# changes with time - so the only thing that can grow louder is how long it has stood, and
# these are the two moments that is worth an inbox. After the second, the issue stays open
# and says nothing more: a third and a fourth reminder about a fault somebody has decided
# not to fix is how the whole channel gets filtered.
AGE_REMINDERS: tuple[tuple[timedelta, str], ...] = (
    (timedelta(hours=48), "2 days"),
    (timedelta(days=7), "7 days"),
)

# When a notification is held. A rung crossed at 02:00 is real and the issue's BODY says
# so at 02:00; the comment and the email wait for the morning, because a notification
# nobody can act on for five hours has woken somebody for nothing, and that is the fastest
# way to have a channel muted. Local hours, in the cohort's own zone - 02:00 in a
# datacentre is nobody's night.
QUIET_FROM = 23
QUIET_UNTIL = 7

# The state markers this module keeps in the issue body. Two, because they answer
# different questions and a body that has only ever carried one must still read: a missing
# marker is "nothing recorded", which is the right answer for both.
_STATE = "state"  # {fault key: {rung: last reported, since: first seen}}
_MENTION = "mention"  # the logins git named, reused by a tick with nothing to ask
_CLOCK = "clock"  # {sent: how many age reminders have gone out for this issue}
_MARKER_RE = "<!-- dsl-{key}-{name}: (.*?) -->"


def in_quiet_hours(when) -> bool:
    """Whether `when` - which must already be in the COHORT's zone - is inside the window
    where a notification is held. See QUIET_FROM."""
    return when.hour >= QUIET_FROM or when.hour < QUIET_UNTIL


# The heading each rung is listed under. The hours are formatted from the windows, so
# moving a rung cannot leave a heading claiming the old deadline.
def _age_heading(oldest: datetime | None, now: datetime) -> str:
    """What an IMMEDIATE fault is filed under: how long the file has been unusable.

    One heading for the whole list rather than one per fault, because there is one clock
    per issue - the reminders count from the oldest fault in it, and a body that grouped
    each fault by its own age would say four different things about a single deadline."""
    days = 0 if oldest is None else max((now - oldest).days, 0)
    return f"unfixed for {days} days" if days else "needs fixing"


_RUNG_HEADING = {
    Severity.MISSED: "MISSED",
    Severity.CRITICAL: f"CRITICAL ({hours(SOURCE_CRITICAL_WINDOW)}h)",
    Severity.URGENT: f"URGENT ({hours(SOURCE_URGENT_WINDOW)}h)",
    Severity.WARNING: f"WARNING ({hours(SOURCE_WARN_WINDOW)}h)",
    Severity.ADVISORY: "advisory",
}


class Context(NamedTuple):
    """Who and where one digest is about: the orgs, the tier whose docs to cite, and the
    logins to @mention.

    One value rather than four positional arguments threaded through the body renderer and
    the comment renderer, which is how the two ended up disagreeing about which was
    which."""

    course_org: str
    cohort_org: str = ""
    central_ref: str = CENTRAL_REF
    mention: tuple[str, ...] = ()


def _cite(digest: Digest, cohort_org: str, fault: SourceFault | None) -> str:
    """`ConfigFault.cite`, plus the one case a fault cannot answer for: a key whose fault
    is GONE, which is the CLEARED line - and there is no line to point at any more."""
    return fault.cite(cohort_org) if fault else f"`{digest.file}`"


def _mention(digest: Digest, ctx: Context) -> str:
    """`cc @who`, falling back to the cohort's instructors team.

    A team mention reaches everybody and is therefore what nobody reads; the fallback is
    for a line git could not attribute to anyone in people.yml."""
    if ctx.mention:
        return "cc " + " ".join(f"@{login}" for login in ctx.mention)
    return f"cc @{ctx.cohort_org}/{digest.cc_team}"


class Transitions(NamedTuple):
    """What changed since the last run, and the rung each current fault now sits at.

    `rung` is what makes this enough for a notifier: appeared/escalated are keys, and who
    hears about a key depends entirely on how loud it has become."""

    appeared: list[str]
    escalated: list[str]
    cleared: list[str]
    rung: dict[str, Severity]

    def __bool__(self) -> bool:
        return bool(self.appeared or self.escalated or self.cleared)


@dataclass
class DigestResult:
    """What one `sync` did: the error count its caller used to get on its own, plus the
    material a notifier needs to mail the same transitions - the rungs they crossed, the
    issue to link to, and the faults themselves (their lines, their due dates)."""

    errors: int = 0
    # The digest issue itself, so the mail beside it can link the full list. Populated on
    # the tick that OPENS the issue too - `upsert_issue` reports the URL `gh issue create`
    # printed - which is the tick a first-appeared notification goes out on. None only
    # when there is no issue (nothing has reached the notify rung) or the write failed.
    issue_url: str | None = None
    faults_by_key: dict[str, SourceFault] = field(default_factory=dict)
    # What the notifier owes an email for, and the rung to say it at. Empty inside the
    # quiet window, where the crossing is deliberately left unrecorded so that the first
    # tick after 07:00 finds it and says it once (see `_announce`).
    mail: dict[str, Severity] = field(default_factory=dict)
    # For each of those keys, the rung it was last REPORTED at - None for one that had
    # never been reported. A send that failed hands these back to `hold`, which puts the
    # record where it was so the next tick owes the same notification again.
    was: dict[str, str | None] = field(default_factory=dict)
    # Set when this tick crossed one of AGE_REMINDERS: how long the file has been
    # unusable, as the mail and the comment say it ("2 days"). The whole issue is owed one
    # message, not one per fault - there is one clock per issue - and the maintainer is
    # copied on it. Never set for a scheduled digest, whose faults have a deadline to
    # escalate towards instead.
    reminder: str | None = None


def _read_marker(body: str, name: str, default, key: str = "source"):
    """The JSON in `<!-- dsl-<key>-<name>: ... -->`, or `default` for a body that does not
    carry that marker, carries junk in it, or was written before the marker existed.

    One reader for every piece of state this issue keeps in its own body: an unreadable
    marker must degrade to "nothing recorded" rather than raise inside a release tick, and
    a second copy of that rule is a second chance to get it wrong."""
    m = re.search(_MARKER_RE.format(key=key, name=name), body or "", re.DOTALL)
    if not m:
        return default
    try:
        value = json.loads(m.group(1))
    except json.JSONDecodeError:
        return default
    return value if isinstance(value, type(default)) else default


def _write_marker(name: str, value, key: str = "source") -> str:
    """One state marker, as the HTML comment the body ends with (invisible when
    rendered)."""
    return f"<!-- dsl-{key}-{name}: {json.dumps(value, sort_keys=True)} -->"


def _rungs(state: dict) -> dict[str, str]:
    """The rung each key was last reported at, out of a state marker in EITHER shape.

    Part I stored the rung alone (`{key: "warning"}`); it now travels with the moment the
    fault was first seen, which is what an immediate fault's reminders count from. Every
    digest issue open at the moment this ships carries the old shape, and read as junk
    each of its faults would appear afresh - a comment and a mail apiece, about nothing
    that changed."""
    out = {}
    for key, value in (state or {}).items():
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, dict) and isinstance(value.get("rung"), str):
            out[key] = value["rung"]
    return out


def _first_seen(state: dict) -> dict[str, str]:
    """When each recorded key was first seen, for the shape that carries it. A key from
    the old shape has no answer, and is taken to have been seen now (see `sync`) - which
    delays its first reminder by at most one deploy."""
    return {
        key: value["since"]
        for key, value in (state or {}).items()
        if isinstance(value, dict) and isinstance(value.get("since"), str)
    }


def _state_marker(current: dict[str, str], since: dict[str, str]) -> dict[str, dict]:
    """What goes in the body: the rung each fault stands at, and when it first appeared."""
    return {
        key: {"rung": rung, "since": since.get(key, "")}
        for key, rung in current.items()
    }


def current_state(faults: list[SourceFault], now) -> dict[str, str]:
    """Each fault's identity mapped to the severity it is at right now. Stored as the
    severity's NAME, because this round-trips through JSON in the issue body."""
    return {f.key: str(f.severity(now)) for f in faults}


def _rung(name: str) -> Severity | None:
    """A severity name back into the ordered value, or None for a name this version of the
    ladder does not have.

    None rather than the quietest rung, which is what it used to be: the ladder was
    renamed on the way in (`error` became `urgent` and `critical`), so every issue open at
    that moment records a rung that no longer exists. Read as ADVISORY, each of those
    faults is an ESCALATION on the first tick after the deploy - a comment and a mail
    apiece, about nothing that changed. An unreadable rung is no information at all, and
    `transitions` treats a key it cannot read as already sitting where it now sits."""
    try:
        return Severity[name.upper()]
    except (AttributeError, KeyError):
        return None


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


def transitions(previous: dict[str, str], current: dict[str, str]) -> Transitions:
    """What changed between two states, filtered to what deserves telling somebody, plus
    the rung every current fault sits at.

    `appeared` and `escalated` are held to NOTIFY_FROM - a new advisory is not news. A
    `cleared` fault is always news whatever rung it left from, because "it is fixed" is
    the message that lets someone stop worrying about it.

    A previous rung this version cannot read (see `_rung`) is not an escalation: there is
    no earlier rung to have climbed from, so the body is simply rewritten and the key
    stands at whatever it stands at now."""
    at = {k: _rung(sev) or Severity.ADVISORY for k, sev in current.items()}
    appeared = [
        k for k, rung in at.items() if k not in previous and rung >= NOTIFY_FROM
    ]
    escalated = [
        k
        for k, rung in at.items()
        if k in previous
        and (was := _rung(previous[k])) is not None
        and rung > was
        and rung >= NOTIFY_FROM
    ]
    cleared = [k for k in previous if k not in current]
    return Transitions(sorted(appeared), sorted(escalated), sorted(cleared), at)


def _announce(
    digest: Digest,
    changed: Transitions,
    previous: dict[str, str],
    current: dict[str, str],
    now,
) -> tuple[dict[str, str], Transitions]:
    """`(the state to STORE, the transitions to say out loud)` - the quiet window, and the
    whole of it.

    Outside the window both are simply what was computed. Inside it, every key whose
    crossing would notify somebody is written back at the rung it was ALREADY reported at
    (an appearance is not written at all), and dropped from what is announced. The next
    tick therefore recomputes the very same transition against the very same previous
    rung - and the first one at or after 07:00 says it once, at whatever rung the fault
    has reached by then. Two rungs crossed overnight are one morning notification, because
    what matters in the morning is how bad it is now, not the order it got there.

    A ledger of owed mails did this before, and it had to be merged, aged and spent
    correctly on every path out of `sync`. This holds no debt: it declines to record the
    crossing, and being stateless it cannot deliver the same notification twice however
    the clock jumps.

    CLEARED keys are announced immediately whatever the hour: the issue closes itself and
    nobody is emailed about it, so there is no notification to hold.

    ONLY a scheduled digest holds. An immediate fault is what a push just left behind, and
    the whole value of telling somebody within the minute is that they are still at the
    keyboard that put it there - holding it until the morning trades the one moment it can
    be fixed cheaply for a quieter night."""
    if not digest.scheduled or not in_quiet_hours(now):
        return current, changed
    stored = dict(current)
    for key in changed.appeared:
        stored.pop(key, None)
    for key in changed.escalated:
        stored[key] = previous[key]
    return stored, Transitions([], [], changed.cleared, changed.rung)


def render_body(
    digest: Digest,
    faults: list[SourceFault],
    now,
    ctx: Context,
    state: dict | None = None,
    since: dict[str, str] | None = None,
) -> str:
    """The whole issue body: the current list grouped by rung, plus the state markers.

    Every line names the FIELD to edit, not just the entry - "something is wrong with
    lecture-2" is not an instruction, `releases.lecture_02 -> course_source_path` is -
    carries the one sentence that would fix it (`SourceFault.fix`, shared with the mail so
    the two cannot disagree), and links straight at the line in the cohort's schedule.yml
    when the parser found it.

    `state` is the map the caller decided to record; passing it makes "the marker matches
    what was compared" true by construction rather than by both sides recomputing it from
    the same inputs and happening to agree.

    `ctx.central_ref` is the tier this org runs, so the field reference points at the docs
    for the engine that will read the file - not at whatever `main` says today."""
    seen = since or {}
    by_rung: dict[Severity, list[SourceFault]] = {}
    for f in faults:
        by_rung.setdefault(f.severity(now), []).append(f)
    oldest = min(
        (_moment(seen.get(f.key)) for f in faults), default=None, key=_sortable
    )

    out = [
        (
            f"{digest.cite_file()} has broken entries. **Do not close or edit "
            f"this issue by hand.** Fix the file and this issue closes itself."
        ),
        "",
        _mention(digest, ctx),
    ]
    for rung in sorted(by_rung, reverse=True):  # loudest first
        rows = by_rung[rung]
        # No count in the heading: the rows are right under it, and a number that has to
        # agree with them is a number that can disagree with them.
        heading = _RUNG_HEADING[rung] if digest.scheduled else _age_heading(oldest, now)
        out += ["", f"### {heading}", ""]
        for f in sorted(rows, key=lambda f: (f.fires is None, f.fires or now)):
            # Past or future is the CLOCK's answer, not the rung's: a withheld source is
            # held at WARNING by its ceiling and its date goes by regardless, and "fires
            # yesterday" reads as a plan rather than as a release that shipped nothing.
            when = (
                f"_{'fired' if f.fires <= now else 'fires'} {f.due}_"
                if f.fires
                else _seen_line(seen.get(f.key), digest)
            )
            out.append(
                f"- **{f.label}** at {_cite(digest, ctx.cohort_org, f)}  \n  "
                f"{f.what}  |  fix: {f.fix(ctx.course_org, rung)}  \n  {when}"
            )
    out += [
        "",
        "---",
        (
            f"Field reference: https://github.com/{CENTRAL}/blob/{ctx.central_ref}"
            f"/{digest.doc}"
        ),
        "",
        _write_marker(
            _STATE,
            _state_marker(current_state(faults, now), seen) if state is None else state,
            digest.state_key,
        ),
        _write_marker(_MENTION, list(ctx.mention), digest.state_key),
    ]
    return "\n".join(out)


def _moment(iso: str | None) -> datetime | None:
    """An ISO timestamp this module wrote, back as a datetime. None for anything it
    cannot read - a marker somebody edited by hand is no information, not a crash inside a
    release tick."""
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _sortable(when: datetime | None) -> datetime:
    """Sort key that puts "not known" last, so one unreadable timestamp cannot make the
    whole issue look older (or newer) than it is."""
    return when or datetime.max.replace(tzinfo=None)


def _seen_line(iso: str | None, digest: Digest) -> str:
    """The italic line under a fault with no deadline: when it turned up, or that it has
    no date at all - which is a different thing, and only a scheduled digest has it."""
    when = _moment(iso)
    if when is None:
        return "_no date (tbc)_" if digest.scheduled else "_first seen just now_"
    return f"_first seen {when:%a %d %b %Y}_"


def cleared_body(digest: Digest) -> str:
    """What the issue is left saying once its last fault has gone - and, the load-bearing
    half, an EMPTY state marker.

    The body IS the record, and the record of a CLOSED issue is what the next tick adopts
    if the fault comes back (see `sync`). Left as it stood, a fault that recurs at the
    same rung or a quieter one is neither appeared nor escalated: the issue re-opens
    carrying it and nobody is told anything at all. Blanked here - and only here, where
    the digest closed the issue ITSELF - adoption keeps doing the job it exists for, which
    is somebody closing the issue by hand while the fault still stands."""
    return "\n".join(
        [
            (
                f"Every entry in {digest.cite_file()} was usable when this issue closed. "
                f"It reopens on its own if that changes."
            ),
            "",
            _write_marker(_STATE, {}, digest.state_key),
            _write_marker(_MENTION, [], digest.state_key),
            _write_marker(_CLOCK, {}, digest.state_key),
        ]
    )


def _comment(
    digest: Digest,
    t: Transitions,
    faults: dict[str, SourceFault],
    now,
    ctx: Context,
    reminder: str | None = None,
) -> str:
    """The transition comment - short on purpose. It is an email subject line more than a
    document; the body above is where the detail lives.

    ESCALATED and CLEARED always. An APPEARANCE only at the QUIETEST reported rung, which
    is where a fault normally enters this issue: it starts the thread, so the escalations
    and the clearing have something to be a history of. A fault that appears already
    louder than that - an entry written the day before it fires - gets no comment, because
    the body lists it and the mail is out; a comment repeating that is the noise this whole
    design exists to avoid.

    An age REMINDER is an escalation with no new rung to report: nothing about the fault
    changed, the length of time it has stood did."""

    def cite(k: str) -> str:
        return _cite(digest, ctx.cohort_org, faults.get(k))

    parts = []
    if t.escalated:
        parts.append(
            "**Escalated** (closer to its deadline):\n"
            + "\n".join(
                f"- `{k}` is now **{str(t.rung[k]).upper()}** - {cite(k)}"
                for k in t.escalated
            )
        )
    if reminder:
        parts.append(
            f"**Escalated** (unfixed for {reminder}):\n"
            + "\n".join(f"- `{k}` - {cite(k)}" for k in sorted(faults))
        )
    quiet = [k for k in t.appeared if t.rung[k] == NOTIFY_FROM]
    if quiet:
        heading = (
            f"**New** (fires within {hours(SOURCE_WARN_WINDOW)}h)"
            if digest.scheduled
            else "**New**"
        )
        parts.append(f"{heading}:\n" + "\n".join(f"- `{k}` - {cite(k)}" for k in quiet))
    if t.cleared:
        cleared_at = f"{now:%H:%M} {zone_name(now)}" if now.tzinfo else f"{now:%H:%M}"
        parts.append(
            "**Cleared**:\n"
            + "\n".join(f"- `{k}` - cleared at {cleared_at}" for k in t.cleared)
        )
    if not parts:
        return ""
    return "\n\n".join([*parts, _mention(digest, ctx)])


def hold(digest: Digest, cohort_org: str, held: dict[str, str | None]) -> int:
    """Put the state marker back where it was for `held` - a rung name to re-record, or
    None for a key that had never been recorded. Returns the error count.

    This is the quiet window's trick, spent on a delivery failure instead of on the hour:
    the crossing is simply NOT recorded, so the next tick recomputes the very same
    transition and says it once. Nothing is queued - a marker is state, not a debt - so a
    failure that repeats cannot deliver twice, and a runner destroyed mid-tick loses
    nothing but a retry.

    A second write rather than a reordering, because the first mail LINKS the issue this
    tick had to create: the body has to be written before there is anything to link. So
    the body is written, the mail is sent, and only a failure comes back here."""
    if not held:
        return 0
    repo = f"{cohort_org}/{digest.repo}"
    try:
        found = find_issues(repo, digest.title)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    if not found.open:
        # No open issue means nothing recorded the crossing either, so there is nothing
        # to put back and the next tick will find it as new regardless.
        return 0
    body = found.open.body or ""
    state = dict(_read_marker(body, _STATE, {}, digest.state_key))
    for key, rung in held.items():
        if rung is None:
            state.pop(key, None)
        elif isinstance(state.get(key), dict):
            state[key] = {**state[key], "rung": rung}
        else:
            state[key] = rung
    # A body with no marker at all is one somebody rewrote by hand; appending would leave
    # two, and `_read_marker` takes the first. Left alone, it reads as "nothing recorded",
    # which is what a held mail wants anyway.
    marker = _MARKER_RE.format(key=digest.state_key, name=_STATE)
    if not re.search(marker, body, re.DOTALL):
        return 0
    patched = re.sub(
        marker,
        # A function, not a replacement string: the marker is JSON, and a backslash in it
        # would be read as a group reference.
        lambda _m: _write_marker(_STATE, state, digest.state_key),
        body,
        count=1,
        flags=re.DOTALL,
    )
    errors = upsert_issue(repo, digest.title, patched, existing=found.open).errors
    if not errors:
        log_ok(f"{len(held)} held notification(s) put back in {repo} for the next tick")
    return errors


def _due_reminder(digest: Digest, oldest, now, sent: int) -> tuple[str | None, int]:
    """`(what to say, how many reminders have now gone out)` for this issue's own clock.

    ONE clock per issue, counted from the OLDEST fault still in it: an immediate fault
    cannot get louder on its own, so the only thing that can escalate is how long the file
    has been unusable, and saying that once per fault would make a four-row file four
    times as loud as a one-row file for no extra information.

    Past the last rung it goes quiet with the issue still open. A fault nobody has fixed
    in a week is a decision, and the twentieth reminder about it is what teaches people to
    filter the channel that the URGENT ones arrive on."""
    if digest.scheduled or oldest is None:
        return None, sent
    crossed = [label for window, label in AGE_REMINDERS if now - oldest >= window]
    if len(crossed) <= sent:
        return None, sent
    return crossed[-1], len(crossed)


def sync(
    digest: Digest,
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
    resolve_mention: Callable[[], list[str]] | None = None,
    migrate: Callable[[dict, list], dict] | None = None,
) -> DigestResult:
    """Bring this cohort's digest issue for one FILE in line with `faults`. Reports what
    it did - the error count, and what a notifier owes an email for on top of the
    @mention.

    `resolve_mention` is asked who git names for these faults (`notify.route`) and is
    called ONLY on a tick that has something to say: a comment to post, an issue to open,
    or a mail owed. An hourly tick with a standing fault has none of those, so it reuses
    the logins the last body recorded and spends no API call at all - which matters,
    because the answer costs a blame query, a people.yml read and a commit lookup per
    repo, every fifteen minutes for as long as the fault stands.

    `migrate` renames keys recorded under an older scheme (see `source_digest.migrated`).
    A digest whose keys have never changed shape passes nothing.

    Never raises past the caller's isolation and never fails a run: a notification that
    could not be delivered must not take a release cron down with it."""
    repo = f"{cohort_org}/{digest.repo}"
    by_key = {f.key: f for f in faults}
    # ONE listing, open and closed together. The PREVIOUS state rides along in the body,
    # and where there is no open issue to read it from, the newest CLOSED one is where it
    # is: somebody who closes this issue by hand has not staged anything, so without
    # adopting what it left behind the next tick reports every standing fault as newly
    # appeared and notifies the cohort about all of it again.
    try:
        found = find_issues(repo, digest.title)
    except RuntimeError as exc:
        log_err(str(exc))
        return DigestResult(errors=1, faults_by_key=by_key)
    open_issue, closed = found.open, found.last_closed
    url = issue_url(repo, open_issue.number) if open_issue else None

    if not faults:
        if open_issue:
            if dry_run:
                log_step(f"[dry-run] would close the {digest.file} digest in {repo}")
                return DigestResult(issue_url=url)
            # The body first, and with an empty state marker: see `cleared_body`. A write
            # that failed leaves the issue open with its old body, which is the state this
            # tick started in - so the next tick tries the whole thing again.
            wrote = upsert_issue(
                repo, digest.title, cleared_body(digest), existing=open_issue
            )
            if wrote.errors or close_issues_titled(
                repo,
                digest.title,
                f"Every entry in {digest.file} is now usable. Reopens on its own if "
                f"that changes.",
            ):
                return DigestResult(errors=1, issue_url=url)
            log_ok(f"{digest.file} digest cleared and closed in {repo}")
        return DigestResult(issue_url=url)

    # Nothing has reached the notify rung and there is no issue to keep current, so this
    # stays silent: an advisory-only plan is a term written ahead of time, not a fault.
    if not open_issue and worst_severity(faults, now) < NOTIFY_FROM:
        return DigestResult(faults_by_key=by_key)

    body = open_issue.body if open_issue else (closed.body if closed else "")
    recorded = _read_marker(body, _STATE, {}, digest.state_key)
    previous = _rungs(recorded)
    if migrate:
        previous = migrate(previous, faults)
    # When each standing fault was first seen. A key with nothing recorded is seen NOW -
    # which is right for one that has just appeared, and one deploy's delay for one whose
    # issue predates this marker.
    was_seen = _first_seen(recorded)
    since = {key: was_seen.get(key) or now.isoformat() for key in by_key}
    current = current_state(faults, now)
    state, changed = _announce(
        digest, transitions(previous, current), previous, current, now
    )
    # A `.releaseignore`-withheld source is listed and commented on, and mailed to nobody.
    # Its ceiling is WARNING, which is NOTIFY_FROM itself, so without this it earns a mail
    # the moment it appears - about files that are in the org, held back by a pattern
    # faculty wrote on purpose. The issue is the right surface for a decision somebody may
    # want to revisit; an inbox is not.
    mail = {
        k: changed.rung[k]
        for k in changed.appeared + changed.escalated
        if by_key[k].kind is not FaultKind.WITHHELD
    }
    oldest = min((_moment(iso) for iso in since.values()), default=None, key=_sortable)
    sent = _read_marker(body, _CLOCK, {}, digest.state_key).get("sent")
    reminder, sent = _due_reminder(
        digest, oldest, now, sent if isinstance(sent, int) else 0
    )
    if reminder:
        # Everything still open is in the reminder, at the rung it stands at: the mail
        # says what is unfixed, not what changed - nothing changed, that is the point.
        mail = {k: changed.rung[k] for k in by_key}
    # A ref that cannot be resolved is not worth failing a notification over - the
    # digest's own contract is that it never takes a release cron down.
    try:
        ref = central_ref_for(course_org)
    except RuntimeError:
        ref = CENTRAL_REF
    # A comment is the only half of this that emails anyone, so a tick with no transition
    # to announce and no issue to open has nobody to name: it reuses what the last body
    # recorded. `changed` covers the mail too - every key owed one is a key in it.
    speaking = bool(changed) or bool(reminder) or not open_issue
    mention = (
        tuple(resolve_mention() or ())
        if speaking and resolve_mention
        else tuple(_read_marker(body, _MENTION, [], digest.state_key))
    )
    ctx = Context(course_org, cohort_org, ref, mention)
    note = _comment(digest, changed, by_key, now, ctx, reminder)
    result = DigestResult(
        issue_url=url,
        faults_by_key=by_key,
        mail=mail,
        was={k: previous.get(k) for k in mail},
        reminder=reminder,
    )
    if dry_run:
        moved = changed.appeared + changed.escalated + changed.cleared
        log_step(
            f"[dry-run] would {'update' if open_issue else 'open'} the {digest.file} "
            f"digest in {repo} ({len(faults)} fault(s)"
            + (f"; comment: {len(moved)} transition(s))" if note else ")")
        )
        return result

    # `upsert_issue` withholds the comment on an issue it had to CREATE, which notifies on
    # its own; `existing` is the listing above, so it does not search again.
    wrote = upsert_issue(
        repo,
        digest.title,
        render_body(digest, faults, now, ctx, _state_marker(state, since), since)
        + "\n"
        + _write_marker(_CLOCK, {"sent": sent}, digest.state_key),
        comment=note or None,
        existing=open_issue,
    )
    # `url` is None on the tick that had to CREATE the issue, and that is exactly the tick
    # a notifier has something to say - so take the URL the create printed.
    result.issue_url = url or wrote.url
    if wrote.errors:
        result.errors = 1
        result.mail = {}
        result.reminder = None
        return result
    log_ok(
        f"{digest.file} digest in {repo}: {len(faults)} fault(s), "
        f"{len(changed.appeared)} new, {len(changed.escalated)} escalated, "
        f"{len(changed.cleared)} cleared"
        + (f", unfixed for {reminder}" if reminder else "")
        + (
            f" (held until {QUIET_UNTIL}:00)"
            if digest.scheduled and in_quiet_hours(now)
            else ""
        )
    )
    return result
