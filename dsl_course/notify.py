"""dsl-course notify -- the email that rides beside a notification issue.

Two mails, each about a fault whose own channel reaches nobody in time:

- `notify_source_transitions` mails the people git names when a source the release plan
  cites crosses a rung of the ladder. The digest issue (`source_digest`) is the durable
  record; the mail is what reaches somebody who is not reading GitHub notifications that
  week. One mail per tick per recipient set, covering every transition of that tick.
- `notify_run_failed` mails the MAINTAINER when an unattended run genuinely broke. The
  `<workflow> is failing` issue every cron files is the record; GitHub's own
  scheduled-failure email goes to whoever last committed the workflow file, which is
  always the bot, which is to say nobody.

WHO IS TOLD is decided by git, not by a mailing list (`route`): the planner of the
schedule.yml line and the last committer of the materials repo it names are the two people
who can act, and telling the whole teaching team about every entry is how a notification
stops being read. The whole team is the FALLBACK, for a line nobody can be named for.

Neither mail ever fails a run. A notification that could not be delivered must not take a
release cron down with it - the same contract the digest has.

PUBLIC LOG RULE: every faculty workflow runs in a PUBLIC repo, so nothing here logs an
address. Counts on stdout; a handle or a masked address only through `log.log_person`.

Usage (the step appended to every cron - see `workflows_render._CRON_MAIL`):
    gh run view "$GITHUB_RUN_ID" --repo "$REPO" --log-failed | tail -n 30 \
      | python3 -m dsl_course.notify --run-failed --course-org ORG \
          --workflow "Scheduled release" --run-url URL
"""

from __future__ import annotations

import argparse
import html
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

from . import ghcli, mailer, sync_faculty
from .gh_contents import blame_logins, last_committer, load_yaml_config
from .log import log, log_err, log_ok, log_person
from .schedule import (
    SCHEDULE_PATH,
    SOURCE_CRITICAL_WINDOW,
    SOURCE_URGENT_WINDOW,
    Severity,
    SourceFault,
    zone_name,
)
from .source_digest import NOTIFY_FROM, DigestResult, deep_link


def _h(window) -> int:
    return int(window.total_seconds() // 3600)


# How much of the deadline is left, in the subject. Formatted from the windows themselves,
# so moving a rung cannot leave a hand-typed number of hours in somebody's inbox.
_SUFFIX = {
    Severity.URGENT: f" ({_h(SOURCE_URGENT_WINDOW)}h)",
    Severity.CRITICAL: f" ({_h(SOURCE_CRITICAL_WINDOW)}h)",
}

_INTRO = {
    # The first two rungs say the same thing, because the same thing is true: the
    # materials are not there and the release will ship nothing. Only the hours differ,
    # and the subject line carries those.
    Severity.WARNING: (
        "Your schedule.yml plans a release whose materials are not in the course org "
        "yet. It will ship nothing to students until they are staged."
    ),
    Severity.URGENT: (
        "Your schedule.yml plans a release whose materials are not in the course org "
        "yet. It will ship nothing to students until they are staged."
    ),
    Severity.CRITICAL: (
        f"This release fires within {_h(SOURCE_CRITICAL_WINDOW)} hours and will ship "
        f"nothing as things stand."
    ),
    Severity.MISSED: (
        "This release fired and shipped nothing, because its materials were not in the "
        "course org."
    ),
}

# Every value in the block is padded to this column so the four labels line up in a
# proportional-font client - which is the whole reason the block is a `<pre>`.
_LABEL_WIDTH = 15


# --------------------------------------------------------------------- who to tell


class Routed(NamedTuple):
    """The addressees of one fault: `to` acts on it, `cc` is told it happened."""

    to: tuple[str, ...]
    cc: tuple[str, ...]


@dataclass
class Routing:
    """Who to tell about this tick's faults, and who to @mention on the issue.

    `logins` is what the digest comment mentions - the same people the mail is addressed
    to, so the two channels reach one set of humans. Empty means git could name nobody in
    people.yml, and the digest falls back to the cohort's instructors team."""

    by_key: dict[str, Routed] = field(default_factory=dict)
    logins: list[str] = field(default_factory=list)


def _blame(cohort_org: str) -> dict[int, str]:
    """Who wrote each line of this cohort's schedule.yml. `{}` when it cannot be read.

    A blame that failed must not read as "nobody wrote this": absence here degrades to
    mailing the whole teaching team, which is noisier but never wrong."""
    try:
        return blame_logins(cohort_org, sync_faculty.CONFIG_REPO, SCHEDULE_PATH)
    except Exception as exc:
        log(
            f"  [skip] could not read who wrote {SCHEDULE_PATH} in {cohort_org} "
            f"({type(exc).__name__}) - notifying the whole teaching team"
        )
        return {}


def _last_committer(course_org: str, repo: str) -> str | None:
    """Who last committed to a materials or template repo, or None if it cannot be told."""
    try:
        return last_committer(course_org, repo)
    except Exception as exc:
        log(
            f"  [skip] could not read who last committed to {course_org}/{repo} "
            f"({type(exc).__name__})"
        )
        return None


def _bot() -> str:
    """The token's own login, lower-cased, or "" when it cannot be read.

    Skipped as a committer: the bot writes `handout_datetime` back into schedule.yml and
    seeds every repo, so on a file it has touched it is the blame answer for lines nobody
    at the school has ever edited."""
    try:
        return ghcli.bot_login().lower()
    except Exception:
        return ""


def route(
    cohort_org: str, course_org: str, faults: list[SourceFault], now: datetime
) -> Routing:
    """Work out who hears about each fault, once per tick.

    Called before the digest is written, because the digest's @mention and the mail's To
    line are the same answer and asking git twice would be two API reads and two chances
    to disagree.

    Only faults at or above the digest's own threshold are routed - below it nothing is
    said on either channel, so nothing needs addressing and a quiet tick costs no API
    calls at all."""
    loud = [f for f in faults if f.severity(now) >= NOTIFY_FROM]
    if not loud:
        return Routing()
    try:
        meta = load_yaml_config(
            cohort_org, sync_faculty.CONFIG_REPO, sync_faculty.COHORT_PEOPLE_PATH
        )
    except Exception as exc:
        log_err(f"could not read {cohort_org}'s people.yml ({exc})")
        meta = None
    contacts = sync_faculty.teaching_contacts(meta or {})
    by_handle = {c.handle.lower(): c for c in contacts}
    everyone = tuple(dict.fromkeys(c.email for c in contacts))
    instructors = tuple(dict.fromkeys(c.email for c in contacts if not c.is_ta))

    blame = _blame(cohort_org)
    bot = _bot()
    committers: dict[str, str | None] = {}
    routed: dict[str, Routed] = {}
    mention: list[str] = []
    unaddressable: set[str] = set()
    for f in loud:
        if f.repo and f.repo not in committers:
            committers[f.repo] = _last_committer(course_org, f.repo)
        named: list[str] = []
        for login in (
            blame.get(f.lineno) if f.lineno else None,
            committers.get(f.repo),
        ):
            if login and login.lower() != bot and login not in named:
                named.append(login)
        matched = [by_handle[n.lower()] for n in named if n.lower() in by_handle]
        unaddressable |= {n for n in named if n.lower() not in by_handle}
        if matched:
            to = tuple(dict.fromkeys(c.email for c in matched))
            # The instructors are copied when a TA is addressed, because a TA staging a
            # lecture folder is doing it on somebody's behalf.
            cc = (
                tuple(a for a in instructors if a not in to)
                if any(c.is_ta for c in matched)
                else ()
            )
            mention += [c.handle for c in matched]
            routed[f.key] = Routed(to, cc)
        else:
            # Git named nobody in people.yml: the team is the fallback, and there is
            # nobody in particular to copy because everybody is already in `to`.
            routed[f.key] = Routed(everyone, ())
    if unaddressable:
        # Count only, in a public log. The handles themselves are public but the line
        # says who is not on the teaching team, which is a judgement about a person.
        log(f"  [skip] {len(unaddressable)} addressee(s) without email in people.yml")
        for handle in sorted(unaddressable):
            log_person(f"    no people.yml address: {handle}")
    return Routing(routed, sorted(dict.fromkeys(mention)))


# ---------------------------------------------------------------------- the mail


def _short(when: datetime | None) -> str:
    """`Wed 9 Sep 08:00 Europe/Berlin` - the subject-line form, no year."""
    if when is None:
        return "no date (tbc)"
    return f"{when:%a} {when.day} {when:%b} {when:%H:%M} {zone_name(when)}"


def _anchor(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def _folder_link(course_org: str, fault: SourceFault) -> str | None:
    """A link into the repo the materials belong in - the folder's PARENT, because the
    folder itself is exactly what is not there yet.

    `main` because every repo this toolkit creates has one, and a branch lookup per fault
    would be an API call to decorate an email."""
    if not fault.repo:
        return None
    base = f"https://github.com/{course_org}/{fault.repo}"
    parent = fault.path.rpartition("/")[0]
    if not parent:
        return _anchor(base, fault.repo)
    return _anchor(f"{base}/tree/main/{parent}", f"{fault.repo}/{parent}")


def _row(label: str, value: str) -> str:
    """One labelled line of the block, value aligned to `_LABEL_WIDTH`."""
    return f"  <b>{label}</b>{' ' * max(1, _LABEL_WIDTH - len(label))}{value}"


def _block(cohort_org: str, course_org: str, fault: SourceFault, rung: Severity) -> str:
    """One fault, as the labelled `<pre>` block the appendix specifies.

    Every value comes out of a faculty-authored schedule.yml, so every value is escaped:
    the body is HTML, and `course_source_path: <tbc>` would otherwise swallow the rest of
    the mail."""
    at = f"{SCHEDULE_PATH}:{fault.lineno}" if fault.lineno else SCHEDULE_PATH
    rows = [
        _row(
            "error line:",
            html.escape(f"{at} - {fault.where} -> {fault.field}"),
        ),
        _row("error content:", html.escape(fault.what)),
        _row(
            "fired:" if rung is Severity.MISSED else "fix by:",
            html.escape(
                fault.due
                if rung is Severity.MISSED
                else f"{_moment(fault)} fires {fault.due}"
            ),
        ),
    ]
    # The folder first, because staging the materials is the fix; the schedule line
    # second, because correcting the path is the other one.
    line_url = deep_link(cohort_org, fault)
    links = [
        link
        for link in (
            _folder_link(course_org, fault),
            _anchor(line_url, f"{SCHEDULE_PATH}#L{fault.lineno}") if line_url else None,
        )
        if link
    ]
    if links:
        rows.append("  " + "  |  ".join(links))
    return "<pre>" + "\n".join(rows) + "</pre>"


def _moment(fault: SourceFault) -> str:
    """What the fault's date IS: a release ships, an assignment is handed out."""
    return "handout" if fault.where.startswith("assignments.") else "release"


def _subject(cohort_org: str, fault: SourceFault, rung: Severity, others: int) -> str:
    """The subject, named for the loudest entry in the mail.

    `others` is how many more faults share the mail. The appendix's format names one
    entry, and grouping by recipient set can put two in one message, so the count is
    appended rather than the subject naming neither."""
    entry = fault.where.partition(".")[2] or fault.where
    if rung is Severity.MISSED:
        out = f"[{cohort_org}] {entry} released nothing - materials still missing"
    else:
        out = (
            f"[{cohort_org}] {entry} materials missing - releases "
            f"{_short(fault.fires)}{_SUFFIX.get(rung, '')}"
        )
    return out + (f" (+{others} more)" if others else "")


def _mail(
    cohort_org: str,
    course_org: str,
    digest: DigestResult,
    keys: list[str],
    loudest: Severity,
) -> tuple[str, str]:
    """The (subject, HTML body) of one message: intro, a block and a fix per fault, and
    the issue that holds the history."""
    parts = [f"<p>{_INTRO[loudest]}</p>"]
    for key in keys:
        fault = digest.faults_by_key[key]
        rung = digest.mail[key]
        parts.append(_block(cohort_org, course_org, fault, rung))
        parts.append(f"<p><b>fix:</b> {html.escape(fault.fix(course_org, rung))}</p>")
    if digest.issue_url:
        parts.append(
            f"<p>Record and history: {_anchor(digest.issue_url, digest.issue_url)}</p>"
        )
    first = digest.faults_by_key[keys[0]]
    return _subject(cohort_org, first, loudest, len(keys) - 1), "\n".join(parts) + "\n"


def notify_source_transitions(
    cohort_org: str,
    course_org: str,
    digest: DigestResult,
    now: datetime,
    routing: Routing,
    *,
    dry_run: bool,
) -> int:
    """Mail what the digest says is owed. Returns the failure count; never raises.

    `digest.mail` is the whole decision: which faults crossed a mailing rung, plus
    anything held over the quiet window, each at the rung to speak in. A fault that only
    reached the digest's own rung is carried by the issue alone, and a CLEARED fault is
    mailed to nobody - the issue closes itself, and an inbox does not need to be told a
    problem stopped existing.

    `now` is the tick's clock, kept in the signature because every phase of a scheduler
    run takes it. Nothing here recomputes a rung or a date against it: they come off
    `digest`, so the mail and the digest comment can never disagree about how loud a
    fault has become."""
    try:
        # The mail IS the fault's own lines, so a key the digest owes a mail for but could
        # not hand over has nothing to say. Not expected; not worth a KeyError inside a
        # release run.
        events = [
            k for k in digest.mail if k in digest.faults_by_key and k in routing.by_key
        ]
        if not events:
            return 0
        maintainer = mailer.maintainer_address()
        # One message per recipient SET, not per fault: two entries the same person
        # planned are one conversation.
        groups: dict[Routed, list[str]] = {}
        for key in events:
            groups.setdefault(routing.by_key[key], []).append(key)
        if not any(g.to for g in groups):
            log(
                f"  [skip] no notification address for {cohort_org} - the digest "
                f"@mention is the only channel"
            )
            return 0
        if not dry_run and mailer.graph_config_from_env() is None:
            log("  [skip] mail not configured - issue @mention only")
            return 0
        failures = 0
        addressed = 0
        for routed, keys in groups.items():
            if not routed.to:
                continue
            keys.sort(key=lambda k: (-digest.mail[k], k))
            loudest = digest.mail[keys[0]]
            subject, body = _mail(cohort_org, course_org, digest, keys, loudest)
            # The maintainer is copied at the two rungs where a release is about to ship
            # nothing, or already has - not at 48h, which is still faculty's own week.
            copies = list(routed.cc)
            if maintainer and loudest >= Severity.CRITICAL:
                copies.append(maintainer)
            if dry_run:
                log(f"  [dry-run] would mail {len(routed.to)} recipient(s): {subject}")
                for to in routed.to:
                    log_person(f"    would mail {mailer.mask_email(to)}")
                continue
            sent = mailer.send_bulk(
                [(to, subject, body) for to in routed.to], html=True, cc=copies
            )
            addressed += len(sent)
            failures += len(routed.to) - len(sent)
        if not dry_run:
            log_ok(
                f"mailed {addressed} recipient(s) in {len(groups)} message(s) about "
                f"{len(events)} source fault(s)"
            )
        return failures
    except Exception as exc:
        # Inside a release cron. Whatever went wrong - an unreadable people.yml, a
        # credential Graph refused - the release itself is the job.
        log_err(
            f"could not mail {cohort_org}'s source faults ({type(exc).__name__}): {exc}"
        )
        return 1


# ------------------------------------------------------------------ a failed run


# What of the failed step's log the maintainer gets. Enough to recognise the fault without
# turning a mail into a log file; the run URL is right above it either way.
_TAIL_LINES = 30
_TAIL_BYTES = 4096


def _tail(text: str) -> str:
    """The last few lines of a log, capped in bytes as well as lines - one line of a
    stack-trace dump can be very long indeed."""
    lines = (text or "").strip().splitlines()[-_TAIL_LINES:]
    raw = "\n".join(lines).encode()[-_TAIL_BYTES:]
    return raw.decode(errors="replace")


def notify_run_failed(course_org: str, workflow: str, run_url: str, tail: str) -> int:
    """Mail the maintainer that an unattended run broke. Returns the failure count.

    The maintainer alone: a run that failed is the toolkit's problem, not the teaching
    team's, and the log tail can carry anything the job was doing when it died - so it
    goes to one mailbox and is never echoed into the public run log."""
    to = mailer.maintainer_address()
    if to is None:
        log(
            f"  [skip] mail not configured - the failure issue in {course_org} is the "
            f"only channel"
        )
        return 0
    subject = f"[{course_org}] {workflow} is failing"
    body = "\n".join(
        [run_url, "", "Last lines of the failed step:", "", _tail(tail), ""]
    )
    try:
        sent = mailer.send_bulk([(to, subject, body)])
    except Exception as exc:
        log_err(f"could not mail the maintainer about {workflow} ({exc})")
        return 1
    if not sent:
        return 1
    log_ok(f"mailed the maintainer about {workflow} in {course_org}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-failed",
        action="store_true",
        required=True,
        help="mail the maintainer about a failed unattended run, reading the failed "
        "step's log tail from stdin",
    )
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    # Always 0. This runs in the `if: failure()` tail of a cron that has already failed
    # for its own reasons; a notifier that reddened the run a second time would say
    # nothing new and would hide the recovery when the next green run closes the issue.
    notify_run_failed(args.course_org, args.workflow, args.run_url, sys.stdin.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
