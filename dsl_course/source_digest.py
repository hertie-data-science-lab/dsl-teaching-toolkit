"""dsl-course source digest -- one self-updating issue per cohort for sources the release
plan names but the course org does not have.

The problem this solves is notification volume, not detection. `schedule.source_faults`
already finds every missing source; a term written up front has dozens of them, all of
them normal, and any scheme that files a ticket per fault (or comments on every hourly
tick) buries the one that matters under the twenty that don't.

So the issue is STATE and its comments are EVENTS:

- the **body** is rewritten from scratch on every run and always shows the current list,
  grouped by severity. GitHub does not email on a body edit, so this is free to run hourly.
- a **comment** is posted only when a fault crosses a rung - appears at or above the
  notify threshold, or escalates towards its deadline. GitHub does email on a comment, so
  a human hears exactly the transitions and nothing else.
- the issue **closes itself** when the last fault clears, which is the third email.

Appears, escalates, clears - three notifications over the life of a problem.

Previous state rides along in the body as an HTML comment (invisible when rendered), so
the digest needs no committed state file and no database: the issue IS the record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import NamedTuple

from .central import CENTRAL, CENTRAL_REF
from .discovery import central_ref_for
from .issues import close_issues_titled, find_issue, upsert_issue
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
)

# Stable, because the workflow finds its own issue by searching this exact title - a title
# that varied with the faults would never match, and every run would open a new issue.
TITLE = "schedule.yml: planned releases cite sources not staged in the course org"

# The rung at which a human is first told. Below it the fault is real and listed, but a
# session nobody has written yet is the normal state of a term planned months ahead, so it
# does not earn an email.
NOTIFY_FROM = Severity.WARNING

_STATE_RE = re.compile(r"<!-- dsl-source-state: (\{.*?\}) -->", re.DOTALL)


def _h(window: timedelta) -> int:
    return int(window.total_seconds() // 3600)


# One line per rung, because the rung is the only thing that says how much of somebody's
# day this deserves. MISSED is its own rung rather than the top of "deploys soon": the
# copy has already failed to ship, and it stays listed until the source appears.
_RUNG_BLURB = {
    Severity.MISSED: (
        "**Fired with nothing staged - the copy did not ship.** Stays here until the "
        "source appears."
    ),
    Severity.CRITICAL: f"**Fires within {_h(SOURCE_CRITICAL_WINDOW)}h.**",
    Severity.URGENT: f"**Fires within {_h(SOURCE_URGENT_WINDOW)}h.**",
    Severity.WARNING: f"**Fires within {SOURCE_WARN_WINDOW.days} days.**",
    Severity.ADVISORY: (
        "Further out - listed so the picture is complete, not to be acted on yet."
    ),
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
    """` ([schedule.yml:36](url))` for a fault whose line is known, else nothing."""
    if fault is None or not fault.lineno:
        return ""
    url = deep_link(cohort_org, fault)
    where = f"{SCHEDULE_PATH}:{fault.lineno}"
    return f" ([{where}]({url}))" if url else f" (`{where}`)"


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
    # None when the digest had to OPEN the issue: `gh issue create` prints the URL but
    # `upsert_issue` reports only a count, and a fresh issue notifies by being created.
    issue_url: str | None = None
    faults_by_key: dict[str, SourceFault] = field(default_factory=dict)


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
    """What changed between two states, filtered to what deserves an email, plus the rung
    every current fault sits at.

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
) -> str:
    """The whole issue body: the current list grouped by rung, plus the state marker.

    Every line names the FIELD to edit, not just the entry - "something is wrong with
    lecture-2" is not an instruction, `releases.lecture_02 -> course_source_path` is - and
    links straight at the line in `cohort_org`'s schedule.yml when the scan found it.

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
            f"`classroom-config/schedule.yml` names sources that are not in "
            f"`{course_org}` right now. Each one ships nothing when its moment arrives."
        ),
        "",
        (
            "Either stage the missing path in the course org, or correct the field named "
            "below. This issue rewrites itself every run and closes when the list empties."
        ),
    ]
    for rung in sorted(by_rung, reverse=True):  # loudest first
        rows = by_rung[rung]
        out += [
            "",
            f"### {str(rung).upper()} ({len(rows)})",
            "",
            _RUNG_BLURB[rung],
            "",
        ]
        for f in sorted(rows, key=lambda f: (f.fires is None, f.fires or now)):
            out.append(
                f"- **`{f.where}`** -> `{f.field}`{_cite(cohort_org, f)}  \n  "
                f"{f.what}  \n  _due {f.due}_"
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
    ]
    return "\n".join(out)


def _comment(
    t: Transitions, faults: dict[str, SourceFault], cohort_org: str = ""
) -> str:
    """The transition comment - short on purpose. It is an email subject line more than a
    document; the body above is where the detail lives. Each line links at the schedule.yml
    line to edit, so the fix is one click from the notification."""

    def cite(k: str) -> str:
        return _cite(cohort_org, faults.get(k))

    parts = []
    if t.escalated:
        parts.append(
            "**Escalated** (closer to its deadline):\n"
            + "\n".join(f"- `{k}` is now **{t.rung[k]}**{cite(k)}" for k in t.escalated)
        )
    if t.appeared:
        parts.append(
            "**New**:\n"
            + "\n".join(f"- `{k}` ({t.rung[k]}){cite(k)}" for k in t.appeared)
        )
    if t.cleared:
        parts.append("**Cleared**:\n" + "\n".join(f"- `{k}`" for k in t.cleared))
    return "\n\n".join(parts)


def sync(
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
) -> DigestResult:
    """Bring this cohort's digest issue in line with `faults`. Reports what it did - the
    error count, and the transitions a notifier can mail on top of the @mention.

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
                "Every source the plan names is now staged in the course org.",
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

    previous = read_state(existing[1]) if existing else {}
    current = current_state(faults, now)
    changed = transitions(previous, current)
    # A ref that cannot be resolved is not worth failing a notification over - the
    # digest's own contract is that it never takes a release cron down.
    try:
        ref = central_ref_for(course_org)
    except RuntimeError:
        ref = CENTRAL_REF
    body = render_body(faults, now, course_org, cohort_org, current, ref)
    note = _comment(changed, by_key, cohort_org)
    if dry_run:
        moved = changed.appeared + changed.escalated + changed.cleared
        log_step(
            f"[dry-run] would {'update' if existing else 'open'} the source digest in "
            f"{repo} ({len(faults)} fault(s)"
            + (f"; comment: {len(moved)} transition(s))" if note else ")")
        )
        return DigestResult(transitions=changed, issue_url=url, faults_by_key=by_key)

    # A comment is the only half of this that emails anyone, so it is posted ONLY for a
    # transition - and `upsert_issue` withholds it on an issue it had to CREATE, which
    # notifies on its own.
    if upsert_issue(
        repo,
        TITLE,
        body,
        comment=f"{note}\n\ncc @{cohort_org}/instructors" if note else None,
    ):
        return DigestResult(errors=1, issue_url=url, faults_by_key=by_key)
    log_ok(
        f"source digest in {repo}: {len(faults)} fault(s), "
        f"{len(changed.appeared)} new, {len(changed.escalated)} escalated, "
        f"{len(changed.cleared)} cleared"
    )
    return DigestResult(transitions=changed, issue_url=url, faults_by_key=by_key)
