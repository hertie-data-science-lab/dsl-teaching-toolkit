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

from .central import CENTRAL, CENTRAL_REF
from .discovery import central_ref_for
from .issues import close_issues_titled, find_issue, upsert_issue
from .log import log_err, log_ok, log_step
from .schedule import (
    CONFIG_REPO,
    SOURCE_ERROR_WINDOW,
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

_RUNG_BLURB = {
    Severity.ERROR: (
        f"**Deploys within {int(SOURCE_ERROR_WINDOW.total_seconds() // 3600)}h "
        f"(or already passed) - these will ship nothing.**"
    ),
    Severity.WARNING: f"**Deploys within {SOURCE_WARN_WINDOW.days} days.**",
    Severity.ADVISORY: (
        "Further out - listed so the picture is complete, not to be acted on yet."
    ),
}


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


def transitions(
    previous: dict[str, str], current: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """(appeared, escalated, cleared) between two states, filtered to what deserves an
    email.

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
    return sorted(appeared), sorted(escalated), sorted(cleared)


def render_body(
    faults: list[SourceFault],
    now,
    course_org: str,
    state: dict[str, str] | None = None,
    central_ref: str = CENTRAL_REF,
) -> str:
    """The whole issue body: the current list grouped by rung, plus the state marker.

    Every line names the FIELD to edit, not just the entry - "something is wrong with
    lecture-2" is not an instruction, `releases.lecture_02 -> course_source_path` is.

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
                f"- **`{f.where}`** -> `{f.field}`  \n  {f.what}  \n  _due {f.due}_"
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


def _comment(appeared, escalated, cleared, current: dict[str, str]) -> str:
    """The transition comment - short on purpose. It is an email subject line more than a
    document; the body above is where the detail lives."""
    parts = []
    if escalated:
        parts.append(
            "**Escalated** (closer to its deadline):\n"
            + "\n".join(f"- `{k}` is now **{current[k]}**" for k in escalated)
        )
    if appeared:
        parts.append(
            "**New**:\n" + "\n".join(f"- `{k}` ({current[k]})" for k in appeared)
        )
    if cleared:
        parts.append("**Cleared**:\n" + "\n".join(f"- `{k}`" for k in cleared))
    return "\n\n".join(parts)


def sync(
    cohort_org: str,
    course_org: str,
    faults: list[SourceFault],
    now,
    dry_run: bool = False,
) -> int:
    """Bring this cohort's digest issue in line with `faults`. Returns the error count.

    Never raises past the caller's isolation and never fails a run: a notification that
    could not be delivered must not take a release cron down with it."""
    repo = f"{cohort_org}/{CONFIG_REPO}"
    # Read first, and not only to decide create-vs-edit: the PREVIOUS state rides along in
    # the body, and it is what tells "still broken" from "just got worse".
    try:
        existing = find_issue(repo, TITLE)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1

    if not faults:
        if existing:
            if dry_run:
                log_step(f"[dry-run] would close the source digest in {repo}")
                return 0
            if close_issues_titled(
                repo,
                TITLE,
                "Every source the plan names is now staged in the course org.",
            ):
                return 1
            log_ok(f"source digest cleared and closed in {repo}")
        return 0

    # Nothing has reached the notify rung and there is no issue to keep current, so this
    # stays silent: an advisory-only plan is a term written ahead of time, not a fault.
    if not existing and worst_severity(faults, now) < NOTIFY_FROM:
        return 0

    previous = read_state(existing[1]) if existing else {}
    current = current_state(faults, now)
    appeared, escalated, cleared = transitions(previous, current)
    # A ref that cannot be resolved is not worth failing a notification over - the
    # digest's own contract is that it never takes a release cron down.
    try:
        ref = central_ref_for(course_org)
    except RuntimeError:
        ref = CENTRAL_REF
    body = render_body(faults, now, course_org, current, ref)
    note = _comment(appeared, escalated, cleared, current)
    if dry_run:
        log_step(
            f"[dry-run] would {'update' if existing else 'open'} the source digest in "
            f"{repo} ({len(faults)} fault(s)"
            + (
                f"; comment: {len(appeared + escalated + cleared)} transition(s))"
                if note
                else ")"
            )
        )
        return 0

    # A comment is the only half of this that emails anyone, so it is posted ONLY for a
    # transition - and `upsert_issue` withholds it on an issue it had to CREATE, which
    # notifies on its own.
    if upsert_issue(
        repo,
        TITLE,
        body,
        comment=f"{note}\n\ncc @{cohort_org}/instructors" if note else None,
    ):
        return 1
    log_ok(
        f"source digest in {repo}: {len(faults)} fault(s), "
        f"{len(appeared)} new, {len(escalated)} escalated, {len(cleared)} cleared"
    )
    return 0
