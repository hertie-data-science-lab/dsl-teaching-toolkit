"""dsl-course source digest -- one self-updating issue per cohort for sources the release
plan names but the course org does not have.

The problem this solves is notification volume, not detection. `schedule.source_faults`
already finds every missing source; a term written up front has dozens of them, all of
them normal, and any scheme that files a ticket per fault (or comments on every hourly
tick) buries the one that matters under the twenty that don't.

So the issue is STATE and its comments are EVENTS:

- the **body** is rewritten from scratch on every run and always shows the current list,
  grouped by rung. GitHub does not email on a body edit, so this is free to run hourly.
- a **comment** is posted when a fault APPEARS at the quietest reported rung, when one
  ESCALATES towards its deadline, and when one CLEARS (see `_comment`).
- the **mail** beside it (`notify`) goes to the same people on every reported rung, and is
  HELD overnight: see `in_quiet_hours` and `_mail_plan`, whose ledger rides in the body
  too.
- the issue **closes itself** when the last fault clears.

Previous state rides along in the body as an HTML comment (invisible when rendered), so
the digest needs no committed state file and no database: the issue IS the record. On a
tick that has to re-OPEN the issue, the state of the newest CLOSED one with the same title
is adopted, so an issue somebody closed by hand does not report every standing fault as
new and mail the cohort about all of it again.

Who is @mentioned is decided by git, not by the team: `notify.route` names the planner of
the line and the last committer of the repo, and the same people are the mail's To line.
The cohort's instructors team is the fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import NamedTuple

from .central import CENTRAL, CENTRAL_REF
from .discovery import central_ref_for
from .issues import close_issues_titled, find_closed_issue, find_issue, upsert_issue
from .log import log_err, log_ok, log_step
from .schedule import (
    CONFIG_REPO,
    SCHEDULE_PATH,
    SOURCE_CRITICAL_WINDOW,
    SOURCE_URGENT_WINDOW,
    SOURCE_WARN_WINDOW,
    Severity,
    SourceFault,
    worst_severity,
    zone_name,
)

# Stable, because the workflow finds its own issue by searching this exact title - a title
# that varied with the faults would never match, and every run would open a new issue.
TITLE = "schedule.yml: planned releases cite sources not staged in the course org"

# The rung at which anything is said at all. Below it the fault is real and listed, but a
# session nobody has written yet is the normal state of a term planned months ahead, so it
# does not earn a notification.
NOTIFY_FROM = Severity.WARNING

# The rung at which a MAIL goes out. The same one: a release a day away with nothing to
# ship is already an emergency by the standards of a term, and an issue comment is only
# read by somebody who reads GitHub notifications. Kept as its own name because the two
# are separate decisions that happen to agree, and it lives here rather than in `notify`
# because the ledger that defers a mail overnight is part of this issue's state.
MAIL_FROM = NOTIFY_FROM

# When mail is held. A rung crossed at 02:00 is real and the issue says so at 02:00; the
# EMAIL waits for the morning, because a notification nobody can act on for five hours has
# woken somebody for nothing, and that is the fastest way to have a channel muted. Local
# hours, in the cohort's own zone - 02:00 in a datacentre is nobody's night.
QUIET_FROM = 22
QUIET_UNTIL = 7

_STATE_RE = re.compile(r"<!-- dsl-source-state: (\{.*?\}) -->", re.DOTALL)
# A SECOND marker rather than a richer first one: every live cohort has a body carrying the
# flat `{key: severity}` state, and re-shaping it would make the next tick read no previous
# state at all - which reports every standing fault as new and mails the lot.
_PENDING_RE = re.compile(r"<!-- dsl-source-pending: (\{.*?\}) -->", re.DOTALL)


def in_quiet_hours(when) -> bool:
    """Whether `when` - which must already be in the COHORT's zone - is inside the window
    where mail is held. See QUIET_FROM."""
    return when.hour >= QUIET_FROM or when.hour < QUIET_UNTIL


def _h(window: timedelta) -> int:
    return int(window.total_seconds() // 3600)


# The heading each rung is listed under. The hours are formatted from the windows, so
# moving a rung cannot leave a heading claiming the old deadline.
_RUNG_HEADING = {
    Severity.MISSED: "MISSED",
    Severity.CRITICAL: f"CRITICAL ({_h(SOURCE_CRITICAL_WINDOW)}h)",
    Severity.URGENT: f"URGENT ({_h(SOURCE_URGENT_WINDOW)}h)",
    Severity.WARNING: f"WARNING ({_h(SOURCE_WARN_WINDOW)}h)",
    Severity.ADVISORY: "advisory",
}


def deep_link(cohort_org: str, fault: SourceFault) -> str | None:
    """The GitHub URL of the exact line to edit, or None when the line is not known.

    `main` is hard-coded because that is the only branch anything reads schedule.yml from
    - the cohort's own workflows included."""
    if not cohort_org or not fault.lineno:
        return None
    return (
        f"https://github.com/{cohort_org}/{CONFIG_REPO}/blob/main/{SCHEDULE_PATH}"
        f"#L{fault.lineno}"
    )


def _cite(cohort_org: str, fault: SourceFault | None) -> str:
    """`schedule.yml:36` as a link where the line is known, as code where it is not."""
    if fault is None or not fault.lineno:
        return f"`{SCHEDULE_PATH}`"
    where = f"{SCHEDULE_PATH}:{fault.lineno}"
    url = deep_link(cohort_org, fault)
    return f"[`{where}`]({url})" if url else f"`{where}`"


def _mention(cohort_org: str, logins: list[str] | None) -> str:
    """`cc @who`, falling back to the cohort's instructors team.

    A team mention reaches everybody and is therefore what nobody reads; the fallback is
    for a line git could not attribute to anyone in people.yml."""
    if logins:
        return "cc " + " ".join(f"@{login}" for login in logins)
    return f"cc @{cohort_org}/instructors"


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
    transitions: Transitions | None = None
    # The digest issue itself, so the mail beside it can link the full list. Populated on
    # the tick that OPENS the issue too - `upsert_issue` reports the URL `gh issue create`
    # printed - which is the tick a first-appeared notification goes out on. None only
    # when there is no issue (nothing has reached the notify rung) or the write failed.
    issue_url: str | None = None
    faults_by_key: dict[str, SourceFault] = field(default_factory=dict)
    # What the notifier owes an email for, and the rung to say it at: this tick's
    # crossings plus anything held overnight, or nothing at all inside the quiet window.
    # Decided here because the ledger that defers a mail is part of the issue's state, and
    # two modules keeping the same clock is two clocks (see `_mail_plan`).
    mail: dict[str, Severity] = field(default_factory=dict)


def read_state(body: str) -> dict[str, str]:
    """The severity each fault was last reported at, recovered from a digest body. `{}` for
    a body this module did not write (or an issue that does not exist yet)."""
    m = _STATE_RE.search(body or "")
    if not m:
        return {}
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def read_pending(body: str) -> dict[str, str]:
    """The mails this issue owes but has not sent, recovered from its own body. `{}` for a
    body written before quiet hours existed, which is the right answer: nothing is owed."""
    m = _PENDING_RE.search(body or "")
    if not m:
        return {}
    try:
        held = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return held if isinstance(held, dict) else {}


def _mail_plan(
    changed: Transitions,
    held: dict[str, str],
    current: dict[str, str],
    now,
) -> tuple[dict[str, Severity], dict[str, str]]:
    """`(what to mail now, what stays held)` - the quiet-hours ledger.

    A crossing inside the quiet window is recorded per key and delivered on the first tick
    after it, at the LOUDEST rung it reached overnight: one mail in the morning, not the
    three that crossing URGENT, CRITICAL and MISSED between 22:00 and 07:00 would send.

    A fault that has CLEARED owes nobody a mail, whatever it was queued at - it shipped,
    and an email about it arriving after the fact is worse than silence."""
    crossed = {
        k: _rung(current[k])
        for k in changed.appeared + changed.escalated
        if _rung(current[k]) >= MAIL_FROM
    }
    owed = {k: v for k, v in held.items() if k in current}
    for k, rung in crossed.items():
        if k not in owed or rung > _rung(owed[k]):
            owed[k] = str(rung)
    if in_quiet_hours(now):
        return {}, owed
    return {k: _rung(v) for k, v in owed.items()}, {}


def adopted_state(repo: str) -> dict[str, str]:
    """The state left behind by the newest CLOSED digest in `repo`, or `{}`.

    Read only when there is no open issue to read state from. Somebody who closes this
    issue by hand has not staged anything, so without this the next tick reports every
    standing fault as newly appeared and mails the cohort about all of it again - which is
    exactly the volume problem the whole design exists to avoid. A closed issue is never
    ADOPTED (see `issues._titled`), only read."""
    try:
        found = find_closed_issue(repo, TITLE)
    except RuntimeError as exc:
        # Not worth failing over: the worst case is one re-notification.
        log_err(f"could not look for a closed digest in {repo}: {exc}")
        return {}
    return read_state(found[1]) if found else {}


def current_state(faults: list[SourceFault], now) -> dict[str, str]:
    """Each fault's identity mapped to the severity it is at right now. Stored as the
    severity's NAME, because this round-trips through JSON in the issue body."""
    return {f.key: str(f.severity(now)) for f in faults}


def _rung(name: str) -> Severity:
    """A severity name back into the ordered value. An unreadable one reads as the quietest
    rung, so a hand-edited body can only ever under-report a transition, never invent one."""
    try:
        return Severity[name.upper()]
    except KeyError:
        return Severity.ADVISORY


def transitions(previous: dict[str, str], current: dict[str, str]) -> Transitions:
    """What changed between two states, filtered to what deserves telling somebody, plus
    the rung every current fault sits at.

    `appeared` and `escalated` are held to NOTIFY_FROM - a new advisory is not news. A
    `cleared` fault is always news whatever rung it left from, because "it is fixed" is
    the message that lets someone stop worrying about it."""
    appeared = [
        k
        for k, sev in current.items()
        if k not in previous and _rung(sev) >= NOTIFY_FROM
    ]
    escalated = [
        k
        for k, sev in current.items()
        if k in previous
        and _rung(sev) > _rung(previous[k])
        and _rung(sev) >= NOTIFY_FROM
    ]
    cleared = [k for k in previous if k not in current]
    return Transitions(
        sorted(appeared),
        sorted(escalated),
        sorted(cleared),
        {k: _rung(sev) for k, sev in current.items()},
    )


def render_body(
    faults: list[SourceFault],
    now,
    course_org: str,
    cohort_org: str = "",
    state: dict[str, str] | None = None,
    central_ref: str = CENTRAL_REF,
    mention: list[str] | None = None,
    held: dict[str, str] | None = None,
) -> str:
    """The whole issue body: the current list grouped by rung, plus the state marker.

    Every line names the FIELD to edit, not just the entry - "something is wrong with
    lecture-2" is not an instruction, `releases.lecture_02 -> course_source_path` is -
    carries the one sentence that would fix it (`SourceFault.fix`, shared with the mail so
    the two cannot disagree), and links straight at the line in `cohort_org`'s
    schedule.yml when the scan found it.

    `state` is the map the caller computed transitions against; passing it makes "the
    marker matches what was compared" true by construction rather than by both sides
    recomputing it from the same inputs and happening to agree.

    `central_ref` is the tier this org runs, so the field reference points at the docs for
    the engine that will read the file - not at whatever `main` says today."""
    by_rung: dict[Severity, list[SourceFault]] = {}
    for f in faults:
        by_rung.setdefault(f.severity(now), []).append(f)

    out = [
        (
            f"`{CONFIG_REPO}/{SCHEDULE_PATH}` has broken entries. **Do not close or edit "
            f"this issue by hand.** Fix the file and this issue closes itself."
        ),
        "",
        _mention(cohort_org, mention),
    ]
    for rung in sorted(by_rung, reverse=True):  # loudest first
        rows = by_rung[rung]
        out += ["", f"### {_RUNG_HEADING[rung]} ({len(rows)})", ""]
        for f in sorted(rows, key=lambda f: (f.fires is None, f.fires or now)):
            when = (
                f"_{'fired' if rung is Severity.MISSED else 'fires'} {f.due}_"
                if f.fires
                else "_no date (tbc)_"
            )
            out.append(
                f"- **{f.where} -> {f.field}** at {_cite(cohort_org, f)}  \n  "
                f"{f.what}  |  fix: {f.fix(course_org, rung)}  \n  {when}"
            )
    marker = json.dumps(
        current_state(faults, now) if state is None else state, sort_keys=True
    )
    out += [
        "",
        "---",
        (
            f"Field reference: https://github.com/{CENTRAL}/blob/{central_ref}"
            f"/docs/07-schedule-releases.md"
        ),
        "",
        f"<!-- dsl-source-state: {marker} -->",
        f"<!-- dsl-source-pending: {json.dumps(held or {}, sort_keys=True)} -->",
    ]
    return "\n".join(out)


def _comment(
    t: Transitions,
    faults: dict[str, SourceFault],
    now,
    cohort_org: str = "",
    mention: list[str] | None = None,
) -> str:
    """The transition comment - short on purpose. It is an email subject line more than a
    document; the body above is where the detail lives.

    ESCALATED and CLEARED always. An APPEARANCE only at the QUIETEST reported rung, which
    is where a fault normally enters this issue: it starts the thread, so the escalations
    and the clearing have something to be a history of. A fault that appears already
    louder than that - an entry written the day before it fires - gets no comment, because
    the body lists it and the mail is out; a comment repeating that is the noise this whole
    design exists to avoid."""

    def cite(k: str) -> str:
        return _cite(cohort_org, faults.get(k))

    parts = []
    if t.escalated:
        parts.append(
            "**Escalated** (closer to its deadline):\n"
            + "\n".join(
                f"- `{k}` is now **{str(t.rung[k]).upper()}** - {cite(k)}"
                for k in t.escalated
            )
        )
    quiet = [k for k in t.appeared if t.rung[k] == NOTIFY_FROM]
    if quiet:
        parts.append(
            f"**New** (fires within {_h(SOURCE_WARN_WINDOW)}h):\n"
            + "\n".join(f"- `{k}` - {cite(k)}" for k in quiet)
        )
    if t.cleared:
        cleared_at = f"{now:%H:%M} {zone_name(now)}" if now.tzinfo else f"{now:%H:%M}"
        parts.append(
            "**Cleared**:\n"
            + "\n".join(f"- `{k}` - cleared at {cleared_at}" for k in t.cleared)
        )
    if not parts:
        return ""
    return "\n\n".join([*parts, _mention(cohort_org, mention)])


def sync(
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
    mention: list[str] | None = None,
) -> DigestResult:
    """Bring this cohort's digest issue in line with `faults`. Reports what it did - the
    error count, and the transitions a notifier can mail on top of the @mention.

    `mention` is the logins git named for these faults (`notify.route`), so the comment
    reaches the same people the mail is addressed to.

    Never raises past the caller's isolation and never fails a run: a notification that
    could not be delivered must not take a release cron down with it."""
    repo = f"{cohort_org}/{CONFIG_REPO}"
    by_key = {f.key: f for f in faults}
    # Read first, and not only to decide create-vs-edit: the PREVIOUS state rides along in
    # the body, and it is what tells "still broken" from "just got worse".
    try:
        existing = find_issue(repo, TITLE)
    except RuntimeError as exc:
        log_err(str(exc))
        return DigestResult(errors=1, faults_by_key=by_key)
    url = f"https://github.com/{repo}/issues/{existing[0]}" if existing else None

    if not faults:
        if existing:
            if dry_run:
                log_step(f"[dry-run] would close the source digest in {repo}")
                return DigestResult(issue_url=url)
            if close_issues_titled(
                repo,
                TITLE,
                f"Every entry in {SCHEDULE_PATH} is now usable. Reopens on its own if "
                f"that changes.",
            ):
                return DigestResult(errors=1, issue_url=url)
            log_ok(f"source digest cleared and closed in {repo}")
            # Everything the issue held is now staged, and the caller may still want to
            # say so: the cleared keys are the previous state, read off the body.
            return DigestResult(
                transitions=transitions(read_state(existing[1]), {}), issue_url=url
            )
        return DigestResult()

    # Nothing has reached the notify rung and there is no issue to keep current, so this
    # stays silent: an advisory-only plan is a term written ahead of time, not a fault.
    if not existing and worst_severity(faults, now) < NOTIFY_FROM:
        return DigestResult(faults_by_key=by_key)

    previous = read_state(existing[1]) if existing else adopted_state(repo)
    current = current_state(faults, now)
    changed = transitions(previous, current)
    to_mail, still_held = _mail_plan(
        changed, read_pending(existing[1]) if existing else {}, current, now
    )
    # A ref that cannot be resolved is not worth failing a notification over - the
    # digest's own contract is that it never takes a release cron down.
    try:
        ref = central_ref_for(course_org)
    except RuntimeError:
        ref = CENTRAL_REF
    body = render_body(
        faults, now, course_org, cohort_org, current, ref, mention, still_held
    )
    note = _comment(changed, by_key, now, cohort_org, mention)
    if dry_run:
        moved = changed.appeared + changed.escalated + changed.cleared
        log_step(
            f"[dry-run] would {'update' if existing else 'open'} the source digest in "
            f"{repo} ({len(faults)} fault(s)"
            + (f"; comment: {len(moved)} transition(s))" if note else ")")
        )
        return DigestResult(
            transitions=changed, issue_url=url, faults_by_key=by_key, mail=to_mail
        )

    # A comment is the only half of this that emails anyone, so it is posted ONLY for a
    # transition - and `upsert_issue` withholds it on an issue it had to CREATE, which
    # notifies on its own.
    wrote = upsert_issue(repo, TITLE, body, comment=note or None)
    # `url` is None on the tick that had to CREATE the issue, and that is exactly the tick
    # a notifier has something to say - so take the URL the create printed.
    url = url or wrote.url
    if wrote.errors:
        return DigestResult(errors=1, issue_url=url, faults_by_key=by_key)
    log_ok(
        f"source digest in {repo}: {len(faults)} fault(s), "
        f"{len(changed.appeared)} new, {len(changed.escalated)} escalated, "
        f"{len(changed.cleared)} cleared"
        + (
            f", {len(still_held)} mail(s) held until {QUIET_UNTIL}:00"
            if still_held
            else ""
        )
    )
    return DigestResult(
        transitions=changed, issue_url=url, faults_by_key=by_key, mail=to_mail
    )
