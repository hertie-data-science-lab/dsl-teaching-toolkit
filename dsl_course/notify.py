"""dsl-course notify -- the email that rides beside a notification issue.

Two mails, each about a fault whose own channel reaches nobody in time:

- `notify_source_transitions` mails the people git names when a source the release plan
  cites crosses a rung of the ladder. The digest issue (`source_digest`) is the durable
  record; the mail is what reaches somebody who is not reading GitHub notifications that
  week. One mail per tick per recipient set, covering every transition of that tick.
- `notify_overwritten_edits` mails whoever hand-edited a generated file in a site repo
  that the sync has just rebuilt over. The issue the sync files there is the record; the
  mail is what tells a person their work is in a commit and not on the site.
- `notify_run_failed` mails the MAINTAINER when an unattended run genuinely broke. The
  `<workflow> is failing` issue every cron files is the record; GitHub's own
  scheduled-failure email goes to whoever last committed the workflow file, which is
  always the bot, which is to say nobody.

WHO IS TOLD is decided by git, not by a mailing list (`route`): the planner of the
schedule.yml line and the last committer of the materials repo it names are the two people
who can act, and telling the whole teaching team about every entry is how a notification
stops being read. The whole team is the FALLBACK, for a line nobody can be named for, and
below it the course admins and then the maintainer (`_fallback_to`), for a cohort whose
people.yml can address nobody at all.

A fault in the COURSE org's own config is the one exception (`route_course`). Its
addressees are the course admins, out of an org SECRET rather than out of the public file
half these faults are IN - so there is no handle to address one of them by, and git's
answer is spent on the digest's @mention instead of on the To line.

Neither mail ever fails a run. A notification that could not be delivered must not take a
release cron down with it - the same contract the digest has.

PUBLIC LOG RULE: every faculty workflow runs in a PUBLIC repo, so nothing here logs an
address. Counts on stdout; a handle or a masked address only through `log.log_person`.

Usage (the step appended to every cron - see `workflows_render._CRON_MAIL`). The tail is
the failed step's OWN log, teed as it ran: this step runs inside the still-running job, so
the jobs API cannot be asked which job failed.
    tail -n 30 "$RUNNER_TEMP/run.log" \
      | python3 -m dsl_course.notify run-failed --course-org ORG \
          --workflow "Scheduled release" --run-url URL
"""

from __future__ import annotations

import argparse
import html
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

from . import faults, ghcli, mailer, sync_faculty
from .config_digest import Digest, DigestResult
from .course import term_tag
from .discovery import course_name_of
from .faults import (
    NOTIFY_FROM,
    SOURCE_CRITICAL_WINDOW,
    SOURCE_URGENT_WINDOW,
    SOURCE_WARN_WINDOW,
    ConfigFault,
    FaultKind,
    Severity,
    hours,
)
from .gh_contents import blame_logins, last_committer, path_committers, read_error
from .log import log, log_err, log_ok, log_person
from .schedule import SourceFault

# How much of the deadline is left, in the subject. Formatted from the windows themselves,
# so moving a rung cannot leave a hand-typed number of hours in somebody's inbox.
_LEFT = {
    Severity.WARNING: f"{hours(SOURCE_WARN_WINDOW)}h left",
    Severity.URGENT: f"{hours(SOURCE_URGENT_WINDOW)}h left",
    Severity.CRITICAL: f"{hours(SOURCE_CRITICAL_WINDOW)}h left",
}

# The first two rungs say the same thing, because the same thing is true: the materials
# are not there and the release will ship nothing. Only the hours differ, and the subject
# line carries those - so the sentence is written once and both rungs point at it.
_NOT_STAGED = (
    "Your schedule.yml plans a release whose materials are not in the course org yet. "
    "It will ship nothing to students until they are staged."
)

_INTRO = {
    Severity.WARNING: _NOT_STAGED,
    Severity.URGENT: _NOT_STAGED,
    Severity.CRITICAL: (
        f"This release fires within {hours(SOURCE_CRITICAL_WINDOW)} hours and will ship "
        f"nothing as things stand."
    ),
    Severity.MISSED: (
        "This release fired and shipped nothing, because its materials were not in the "
        "course org."
    ),
}

# --------------------------------------------------------------------- who to tell


class Unsent(NamedTuple):
    """What a tick could not deliver: how many addressees went unmailed, and the fault
    keys their message carried.

    The keys are the point. A count alone said only that something had gone wrong, into a
    run log nobody reads, while the digest had already recorded the new rung - so the mail
    was owed once, failed once, and was never owed again. Handed to
    `source_digest.hold`, they un-record the crossing and the next tick owes it afresh."""

    addressees: int = 0
    keys: tuple[str, ...] = ()


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


def _addresses(emails: Iterable[str]) -> tuple[str, ...]:
    """Addresses in declaration order, once each, case-insensitively.

    Two people.yml entries sharing a mailbox are one recipient, and a shared address
    written two ways is the same mailbox. One implementation, because a second copy of
    this rule is a second answer to "have we already told them"."""
    out: dict[str, str] = {}
    for email in emails:
        out.setdefault(email.lower(), email)
    return tuple(out.values())


def _blame(org: str, repo: str, path: str, ref: str = "main") -> dict[int, str]:
    """Who wrote each line of one config file. `{}` when it cannot be read.

    A blame that failed must not read as "nobody wrote this": absence here degrades to
    mailing the whole teaching team, which is noisier but never wrong.

    `org` and `ref` because not every file a cohort's digest carries is in the cohort org
    on `main`: an assignment's `grading_config.yml` is in the course org, on the
    template's `solution` branch, and blaming the cohort for it would name nobody."""
    try:
        return blame_logins(org, repo, path, f"refs/heads/{ref}")
    except Exception as exc:
        log(
            f"  [skip] could not read who wrote {path} in {org} "
            f"({type(exc).__name__}) - notifying the whole teaching team"
        )
        return {}


def _pushed(cohort_org: str, repo: str, path: str) -> tuple[str, ...]:
    """Who last pushed one file, newest first. `()` when it cannot be read.

    For a CSV, where blame is the wrong question: the bot writes two columns of
    students.csv back into rows faculty typed, so half the roster blames to an account
    that cannot fix anything - see `gh_contents.path_committers`."""
    try:
        return path_committers(cohort_org, repo, path)
    except Exception as exc:
        log(
            f"  [skip] could not read who last pushed {path} in {cohort_org} "
            f"({type(exc).__name__}) - notifying the whole teaching team"
        )
        return ()


def _wrote_it(cohort_org: str, fault: ConfigFault, bot: str) -> str | None:
    """The one login git holds responsible for this fault's line, or None.

    A CSV is asked who PUSHED it and a YAML who wrote the LINE, because a CSV's rows are
    written by a form and rewritten by the bot while a YAML's are typed by a person. The
    bot is skipped either way: it is the last committer of every file it maintains, and
    mailing it is mailing nobody. On the BLAME side that matters as much - the bot writes
    `central_ref:` into every dsl-course.yml and `handout_datetime:` into schedule.yml, so
    it is the honest blame answer for lines nobody at the school has ever typed - and
    None here is what lets `route_course` fall through to whoever pushed the file."""
    if not fault.file:
        return None
    if fault.file.endswith(".csv"):
        return _last_pusher(cohort_org, fault, bot)
    if not fault.lineno:
        return None
    wrote = _blame(
        fault.in_org or cohort_org, fault.in_repo, fault.file, fault.ref
    ).get(fault.lineno)
    return wrote if wrote and wrote.lower() != bot else None


def _last_pusher(org: str, fault: ConfigFault, bot: str) -> str | None:
    """Who last pushed the FILE this fault is in, skipping the bot, or None.

    The answer for a fault about the whole of a file rather than one line of it. `_wrote_it`
    asks git blame, and blame needs a line - a file that is missing, unparseable or the
    wrong shape has none, and those are precisely the faults somebody has just pushed."""
    if not fault.file:
        return None
    return next(
        (
            login
            for login in _pushed(org, fault.in_repo, fault.file)
            if login.lower() != bot
        ),
        None,
    )


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


def _fallback_to() -> tuple[str, ...]:
    """The To line for a cohort fault its own people.yml can address nobody for: the course
    ADMINS, then the MAINTAINER, then nobody.

    A cohort with no `email:` anywhere is not a cohort with nothing to hear: its releases
    still ship nothing, and its digest issue is still open. Left to the @mention alone the
    fault stood for two days before the 48h rung copied the maintainer - so it falls one
    level up instead, to the admins whose course this cohort is, and past them to the
    maintainer, who is the last person who can act on an org that declares neither.

    A count and which fallback it was, never an address: this runs in a PUBLIC repo."""
    admins = _addresses(mailer.course_admin_addresses())
    if admins:
        log(f"  [fallback] no cohort address - mailing {len(admins)} course admin(s)")
        return admins
    maintainer = mailer.maintainer_address()
    if maintainer:
        log("  [fallback] no cohort address - mailing the maintainer")
        return (maintainer,)
    return ()


def route(
    cohort_org: str, course_org: str, faults: list[SourceFault], now: datetime
) -> Routing:
    """Work out who hears about each fault, once per tick.

    The digest's @mention and the mail's To line are the same answer, so it is asked once
    and both channels are handed it - asking git twice would be two API reads and two
    chances to disagree. `source_digest.sync` calls this only on a tick that has something
    to say; an hourly tick with a standing fault reuses what the last one found.

    Only faults at or above the digest's own threshold are routed - below it nothing is
    said on either channel, so nothing needs addressing and a quiet tick costs no API
    calls at all.

    A cohort whose people.yml holds no address at all falls through to `_fallback_to` -
    the course admins, then the maintainer - so the To line is empty only for a course
    that declares neither."""
    loud = [f for f in faults if f.severity(now) >= NOTIFY_FROM]
    if not loud:
        return Routing()
    try:
        faculty = sync_faculty.load_cohort_faculty(cohort_org) or {}
    except Exception as exc:
        log_err(f"could not read {cohort_org}'s people.yml ({read_error(exc)})")
        faculty = {}
    contacts = sync_faculty.teaching_contacts(faculty, now.date().isoformat())
    by_handle = {c.handle.lower(): c for c in contacts}
    everyone = _addresses(c.email for c in contacts)
    instructors = _addresses(c.email for c in contacts if not c.is_ta)

    bot = _bot()
    committers: dict[str, str | None] = {}
    routed: dict[str, Routed] = {}
    mention: list[str] = []
    unaddressable: set[str] = set()
    for f in loud:
        # Only a SOURCE fault has a second person to tell: whoever is writing the
        # materials repo the entry points at, as against whoever wrote the entry.
        if f.repo and f.repo not in committers:
            committers[f.repo] = _last_committer(course_org, f.repo)
        named: list[str] = []
        for login in (
            _wrote_it(cohort_org, f, bot),
            committers.get(f.repo),
        ):
            if login and login.lower() != bot and login not in named:
                named.append(login)
        matched = [by_handle[n.lower()] for n in named if n.lower() in by_handle]
        unaddressable |= {n for n in named if n.lower() not in by_handle}
        if matched:
            to = _addresses(c.email for c in matched)
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
    if not any(r.to for r in routed.values()):
        # people.yml holds no address at all, so `everyone` is empty and so is every group
        # git could name from it - all of this tick's faults or none. One fallback set for
        # the lot, and the @mention (`mention`) is untouched: who wrote the line does not
        # change because nobody in the cohort can be written to.
        fallback = _fallback_to()
        routed = {key: Routed(fallback, ()) for key in routed}
    return Routing(routed, sorted(dict.fromkeys(mention)))


def route_course(
    course_org: str,
    faults: list[ConfigFault],
    now: datetime,
    admins: Iterable[str] = (),
) -> Routing:
    """Who hears about a fault in the COURSE org's own config. `route`'s course-level twin.

    Two things differ, and both follow from the file being the course's rather than a
    cohort's. The addresses are the course ADMINS' and they come from an org SECRET
    (`mailer.course_admin_addresses`), never from the public `dsl-course.yml` that half
    these faults are IN - so there is no handle-to-address map here, and therefore no way
    to write to one admin rather than another. And the person git names is @mentioned on
    the issue rather than addressed: the issue is where the line is, and the mail goes to
    the people who own the course.

    `admins` is the handles the course declares, for the degraded-mode count alone - the
    caller has already parsed them, and a second read to print a number would be an API
    call spent on a log line."""
    loud = [f for f in faults if f.severity(now) >= NOTIFY_FROM]
    if not loud:
        return Routing()
    to = _addresses(mailer.course_admin_addresses())
    if not to:
        # A COUNT and the variable's NAME, never an address and never who is missing one:
        # this runs in the course org's public `.github`. `_deliver` then says that the
        # digest @mention is the only channel left.
        log(
            f"  [skip] {len(list(admins))} addressee(s) without email - "
            f"{mailer.COURSE_ADMIN_ENV} is not set on {course_org}"
        )
    bot = _bot()
    mention: list[str] = []
    for f in loud:
        # Blame first, then whoever last pushed the file: a fault about the whole of a
        # file (missing, unparseable, the wrong shape) has no line to blame, and that is
        # the commonest way this config breaks.
        login = _wrote_it(course_org, f, bot) or _last_pusher(course_org, f, bot)
        if login and login not in mention:
            mention.append(login)
    return Routing({f.key: Routed(to, ()) for f in loud}, sorted(mention))


# ---------------------------------------------------------------------- the mail


def _anchor(url: str, text: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(text)}</a>'


def _place_link(course_org: str, fault: SourceFault) -> tuple[str, str] | None:
    """`(text, url)` for the place the fix happens, as the fix sentence names it.

    A missing PATH names `<course_org>/<repo>`, linked to the folder's PARENT - the folder
    itself is exactly what is not there yet. A missing REPO names the course org alone,
    linked to the org: the repo is the thing that is absent, so a URL inside it is a 404
    in an email whose whole job is to say where to go.

    `main` because every repo this toolkit creates has one, and a branch lookup per fault
    would be an API call to decorate an email."""
    if not fault.is_source:
        # An immediate fault is fixed in the file it is in, which the `error line:` row
        # above already links at the line. There is nowhere else to send anybody.
        return None
    org_url = f"https://github.com/{course_org}"
    if fault.kind is FaultKind.MISSING_REPO or not fault.repo:
        return course_org, org_url
    parent = fault.path.rpartition("/")[0]
    url = f"{org_url}/{fault.repo}" + (f"/tree/main/{parent}" if parent else "")
    return f"{course_org}/{fault.repo}", url


def _linked(text: str, name: str, url: str) -> str:
    """`text`, HTML-escaped, with its first mention of `name` turned into a link. A text
    that does not mention it comes back escaped and otherwise untouched."""
    escaped = html.escape(text)
    target = html.escape(name)
    return escaped.replace(target, _anchor(url, name), 1)


def _content(fault: SourceFault) -> str:
    """The `error content:` cell: what is missing, named as the thing the entry points at
    rather than as a URL - the link lives on the fix row, this row says what is wrong."""
    if fault.kind is FaultKind.MISSING_PATH and fault.path:
        return (
            f"the specified path <code>{html.escape(fault.path)}</code> does not exist."
        )
    if fault.kind is FaultKind.MISSING_REPO and fault.repo:
        return (
            f"the specified repo <code>{html.escape(fault.repo)}</code> does not exist "
            f"or is empty."
        )
    return html.escape(fault.what)


def _rows(rows: list[tuple[str, str]]) -> str:
    """A two-column table: bold lower-case labels down the left, values already MARKUP
    on the right. A table rather than a `<pre>`, so the values wrap and read in the
    client's own face instead of arriving as a code snippet."""
    cells = "".join(
        f'<tr><td style="padding:0 1em 0.25em 0;white-space:nowrap;vertical-align:top">'
        f'<b>{label}</b></td><td style="padding:0 0 0.25em 0">{markup}</td></tr>'
        for label, markup in rows
    )
    return f'<table style="border-collapse:collapse">{cells}</table>'


def _block(
    cohort_org: str,
    course_org: str,
    fault: SourceFault,
    rung: Severity,
    issue_url: str | None,
    now: datetime,
) -> str:
    """One fault, as the labelled table the appendix specifies.

    Whether the moment has PASSED is read off the clock and not off the rung. A rung can
    be held below MISSED by its ceiling - that is what `.releaseignore` withholding does -
    and the date has still gone by, so "fix by: release fires <yesterday>" is not an
    instruction anybody can follow."""
    fired = fault.fires is not None and fault.fires <= now
    line_url = fault.link(cohort_org)
    where = html.escape(f" - {fault.label}")
    at = _anchor(line_url, fault.at) if line_url else html.escape(fault.at)
    fix = fault.fix(course_org, rung)
    place = _place_link(course_org, fault)
    rows = [
        ("error line:", at + where),
        ("error content:", _content(fault)),
    ]
    # No date row for a fault with no moment: an unreadable line is not going to happen at
    # a time, and "fix by date: no date (tbc)" is a row that says nothing twice.
    if fault.fires:
        rows.append(
            (
                "fired:" if fired else "fix by date:",
                html.escape(
                    fault.due if fired else f"{fault.moment} fires {fault.due}"
                ),
            )
        )
    rows.append(("to fix:", _linked(fix, *place) if place else html.escape(fix)))
    if issue_url:
        rows.append(("GH issue record:", _anchor(issue_url, issue_url)))
    return _rows(rows)


def _course_name(course_org: str) -> str:
    """The course's display name, or its org slug when nothing names it.

    Guarded, because `course_name_of` reads the course org's `dsl-course.yml` - which is
    one of the files this module mails ABOUT. A malformed one raises out of the YAML
    loader, and letting that through would mean the single fault that most needs an email
    is the one fault that sends none."""
    try:
        return course_name_of(course_org) or course_org
    except Exception:
        return course_org


def _course_label(course_org: str, cohort_org: str) -> str:
    """`Deep Learning (Demo) f2026`: the course's display name and the cohort's term tag,
    which is how a reader tells two cohorts of one course apart in a subject line. The
    org slug stands in for a course that declares no name."""
    name = _course_name(course_org)
    # A COURSE-level fault is the course org's own, so there is no cohort and no term to
    # name - and a course org whose slug happens to carry one would otherwise put a
    # cohort's tag on a subject line that is not about that cohort.
    tag = None if cohort_org == course_org else term_tag(cohort_org)
    return f"{name} {tag}" if tag else name


def _subject(
    label: str, faults: list[SourceFault], rung: Severity, now: datetime
) -> str:
    """The subject, named for the loudest entry in the mail.

    `[<course> <tag>] Missing materials: <entry> fires <when> - <n>h left`, or `fired
    <when> - nothing shipped` once the moment has gone. Grouping by recipient set can put
    two entries in one message; then it counts them and names the nearest deadline."""
    first = faults[0]
    fired = first.fires is not None and first.fires <= now
    when = (
        f"{first.fires:%a} {first.fires.day} {first.fires:%b} {first.fires:%H:%M}"
        if first.fires
        else "no date (tbc)"
    )
    entry = first.where.partition(".")[2] or first.where
    what = f"{len(faults)} {first.moment}s, next" if len(faults) > 1 else entry
    if fired:
        tail = f"fired {when} - nothing shipped"
    else:
        tail = f"fires {when}" + (f" - {_LEFT[rung]}" if rung in _LEFT else "")
    return f"[{label}] Missing materials: {what} {tail}"


def _mail(
    cohort_org: str,
    course_org: str,
    digest: DigestResult,
    keys: list[str],
    loudest: Severity,
    now: datetime,
) -> tuple[str, str]:
    """The (subject, HTML body) of one message: who sends it, why, and a table per fault
    that ends on the issue holding the history."""
    label = _course_label(course_org, cohort_org)
    org_url = f"https://github.com/{course_org}"
    sender = html.escape(_course_name(course_org))
    parts = [
        f"<p>This is an automated email sent on behalf of {sender}.</p>",
        f"<p>{_linked(_INTRO[loudest], 'course org', org_url)}</p>",
    ]
    faults = [digest.faults_by_key[k] for k in keys]
    for key, fault in zip(keys, faults, strict=True):
        parts.append(
            _block(
                cohort_org, course_org, fault, digest.mail[key], digest.issue_url, now
            )
        )
    return _subject(label, faults, loudest, now), "\n".join(parts) + "\n"


def _deliver(
    cohort_org: str,
    groups: dict[Routed, list[str]],
    message: Callable[[Routed, list[str]], tuple[str, str]],
    copy_maintainer: Callable[[list[str]], bool],
    what: str,
    dry_run: bool,
) -> Unsent:
    """Send one message per recipient SET and report what did NOT go out.

    The one delivery path for both kinds of fault, because the accounting is the subtle
    part and two copies of it would drift: a group is one Graph POST for one message, so
    its recipients are all in or all out, and the keys that message carried are exactly
    what has to be owed again (see `Unsent`). What differs between the two callers is only
    the text and who is copied, so those arrive as functions.

    Never raises: a notification that could not be delivered must not take a release cron
    down with it."""
    events = [k for keys in groups.values() for k in keys]
    maintainer = mailer.maintainer_address()
    # Neither of these is HELD: an org with no addresses and an org with no transport
    # are standing states, and holding the crossing would make every tick for the rest
    # of the term recompute a notification that cannot be delivered - and comment on
    # it again each time.
    if not any(g.to for g in groups):
        log(
            f"  [skip] no notification address for {cohort_org} - the digest "
            f"@mention is the only channel"
        )
        return Unsent()
    if not dry_run and mailer.graph_config_from_env() is None:
        log("  [skip] mail not configured - issue @mention only")
        return Unsent()
    messages: list[mailer.Message] = []
    addressed = 0
    for routed, keys in groups.items():
        if not routed.to:
            continue
        subject, body = message(routed, keys)
        copies = list(routed.cc)
        # Not twice: a cohort no address of its own could be found for is addressed TO the
        # maintainer (`_fallback_to`), and the rung that copies them must not then put the
        # same mailbox on the Cc line - the same mailbox written two ways included.
        addressed_to = {a.lower() for a in routed.to}
        if (
            maintainer
            and copy_maintainer(keys)
            and maintainer.lower() not in addressed_to
        ):
            copies.append(maintainer)
        if dry_run:
            log(f"  [dry-run] would mail {len(routed.to)} recipient(s): {subject}")
            for to in routed.to:
                log_person(f"    would mail {mailer.mask_email(to)}")
            continue
        # One message for the whole group: the text is identical for everybody on the
        # line, and a copy per recipient pays the rate limiter's slot for each.
        messages.append(mailer.Message(routed.to, subject, body, tuple(copies)))
        addressed += len(routed.to)
    if not messages:
        return Unsent()
    # One batch, so the Graph token is minted once however many groups this tick has.
    sent = mailer.send_bulk(messages, html=True)
    delivered = set(sent)
    held: list[str] = []
    unmailed = 0
    for routed, keys in groups.items():
        if not routed.to or delivered.issuperset(routed.to):
            continue
        held += keys
        unmailed += len(routed.to)
    if held:
        log_err(
            f"mailed {len(sent)} of {addressed} recipient(s); {unmailed} on "
            f"{len(held)} {what} were not reached - held for the next tick"
        )
        return Unsent(unmailed, tuple(held))
    log_ok(
        f"mailed {len(sent)} recipient(s) in {len(messages)} message(s) about "
        f"{len(events)} {what}"
    )
    return Unsent()


def _groups(
    digest: DigestResult, routing: Routing, source: bool
) -> dict[Routed, list[str]]:
    """This tick's owed mails of ONE kind, gathered by recipient set: two entries the same
    person wrote are one conversation, not two messages.

    `source` splits them, because schedule.yml's issue carries both and they are two
    different letters: a source that is about to ship nothing names the entry and its
    deadline, an entry nobody can read names the file and what it costs. One tick can owe
    both, to the same person, and they are still two letters.

    A key the digest owes a mail for but could not hand over a fault (or an addressee)
    for has nothing to say. Not expected; not worth a KeyError inside a release run."""
    groups: dict[Routed, list[str]] = {}
    for key in digest.mail:
        fault = digest.faults_by_key.get(key)
        if fault is None or fault.is_source is not source or key not in routing.by_key:
            continue
        groups.setdefault(routing.by_key[key], []).append(key)
    return groups


def notify_source_transitions(
    cohort_org: str,
    course_org: str,
    digest: DigestResult,
    now: datetime,
    routing: Routing,
    *,
    dry_run: bool,
) -> Unsent:
    """Mail what the digest says is owed. Reports what did NOT go out; never raises.

    `digest.mail` is the whole decision: which faults crossed a mailing rung, and the rung
    to speak at. A fault that only reached the digest's own rung is carried by the issue
    alone, a crossing inside the quiet window is not owed until the morning, and a CLEARED
    fault is mailed to nobody - the issue closes itself, and an inbox does not need to be
    told a problem stopped existing.

    `now` is the tick's clock. No rung is recomputed against it - every rung comes off
    `digest`, so the mail and the digest comment can never disagree about how loud a fault
    has become - but whether a deadline has PASSED is read off it, because a ceiling can
    hold a rung below MISSED while the date has gone by all the same."""
    events: list[str] = []
    try:
        groups = _groups(digest, routing, source=True)
        events = [k for keys in groups.values() for k in keys]
        if not events:
            return Unsent()

        def message(_routed: Routed, keys: list[str]) -> tuple[str, str]:
            keys.sort(key=lambda k: (-digest.mail[k], k))
            return _mail(
                cohort_org, course_org, digest, keys, digest.mail[keys[0]], now
            )

        return _deliver(
            cohort_org,
            groups,
            message,
            # The maintainer is copied at the two rungs where a release is about to ship
            # nothing, or already has. Not at the quieter two: those are still faculty's
            # own day, and a maintainer copied on every one of them stops reading them.
            lambda keys: max(digest.mail[k] for k in keys) >= Severity.CRITICAL,
            "source fault(s)",
            dry_run,
        )
    except Exception as exc:
        # Inside a release cron. Whatever went wrong - an unreadable people.yml, a
        # credential Graph refused - the release itself is the job. Nothing went out, so
        # everything this tick owed is held.
        log_err(
            f"could not mail {cohort_org}'s source faults ({type(exc).__name__}): {exc}"
        )
        return Unsent(1, tuple(events))


# ------------------------------------------------- a file the toolkit cannot read


def _plural(n: int) -> str:
    """`entry` / `entries`, spelled once: the subject line and the first line of the body
    both count the same faults, and a message whose subject says 1 and whose body says
    are is a message somebody wrote by hand."""
    return "entry" if n == 1 else "entries"


def _immediate_intro(
    spec: Digest, count: int, reminder: str | None, dated: bool = False
) -> str:
    """The first line, which is the only part a reminder changes.

    It leads with the CONSEQUENCE, because "students.csv has 1 entry the toolkit cannot
    use" says nothing about whether anybody's term is affected - and the answer differs
    sharply per file (see `faults.CONSEQUENCE`).

    `dated` is whether these faults carry a MOMENT. An assignment's `grading_config.yml`
    is the one file here that does: its faults are held back until the grading pass that
    reads them is close, so the letter typically goes out months after the line was
    written and "a recent edit" names the wrong week - under a table whose own row says
    when it bites."""
    what = f"{count} {_plural(count)} the toolkit cannot use"
    file = f"<code>{html.escape(spec.file)}</code>"
    if reminder:
        opening = f"Still unfixed after {reminder}: {file} has {what}."
    elif dated:
        opening = f"{file} has {what} by the time it is read."
    else:
        opening = f"A recent edit to {file} left {what}."
    consequence = faults.CONSEQUENCE.get(spec.file, "")
    tail = f" Until they are fixed: {html.escape(consequence)}." if consequence else ""
    return f"<p>{opening}{tail}</p>"


def notify_config_faults(
    spec: Digest,
    cohort_org: str,
    course_org: str,
    digest: DigestResult,
    now: datetime,
    routing: Routing,
    *,
    dry_run: bool,
) -> Unsent:
    """Mail the people git names about a file the toolkit cannot read. Never raises.

    The immediate ladder, in three lines: a fault that APPEARS is mailed to whoever left
    it there; the same list goes out again at 48 hours and at 7 days with the maintainer
    copied (`digest.reminder`); after that the issue stays open and the inbox goes quiet.
    There is no rung to climb - nothing about an unreadable line changes with time - so
    what escalates is only how long it has stood."""
    events: list[str] = []
    try:
        groups = _groups(digest, routing, source=False)
        events = [k for keys in groups.values() for k in keys]
        if not events:
            return Unsent()
        # Both read the course org's identity file, so they are taken ONCE and not once
        # per recipient group - `message` is called per group by `_deliver`.
        label = _course_label(course_org, cohort_org)
        sender = html.escape(_course_name(course_org))

        def message(_routed: Routed, keys: list[str]) -> tuple[str, str]:
            keys.sort()
            faults_in = [digest.faults_by_key[k] for k in keys]
            parts = [
                f"<p>This is an automated email sent on behalf of {sender}.</p>",
                _immediate_intro(
                    spec,
                    len(faults_in),
                    digest.reminder,
                    any(f.fires for f in faults_in),
                ),
            ]
            parts += [
                _block(cohort_org, course_org, f, digest.mail[k], digest.issue_url, now)
                for k, f in zip(keys, faults_in, strict=True)
            ]
            subject = (
                f"[{label}] {spec.file} has {len(faults_in)} "
                f"{_plural(len(faults_in))} the toolkit cannot use"
            )
            return subject, "\n".join(parts) + "\n"

        return _deliver(
            cohort_org,
            groups,
            message,
            # The maintainer joins once the file has been unusable for two days: by then
            # it is not a slip somebody is about to fix, and somebody outside the cohort
            # has to know its enrolment (or its teams, or its plan) is not running. On a
            # COURSE-level digest they are on it from the first mail (`cc_maintainer`):
            # the course admins it goes to are the same small group who may have written
            # the line, so there is nobody else outside it to notice.
            lambda _keys: spec.cc_maintainer or bool(digest.reminder),
            f"{spec.file} fault(s)",
            dry_run,
        )
    except Exception as exc:
        log_err(
            f"could not mail {cohort_org}'s {spec.file} faults "
            f"({type(exc).__name__}): {exc}"
        )
        return Unsent(1, tuple(events))


# --------------------------------------------- an edit the site sync rebuilt over


# What the person who made the edit has lost, and where to make it again. Verbatim in the
# mail's first line and in every fault's fix sentence, because the issue the sync files in
# the site repo says the same two things - and a notice whose email and whose issue
# disagree about the remedy is worse than either on its own.
OVERWRITTEN_CONSEQUENCE = (
    "generated files are rebuilt every run, so the change is not on the site"
)
OVERWRITTEN_FIX = "move the change to the file the docs name as yours"


def overwritten_fault(site: str, path: str, sha: str) -> ConfigFault:
    """One generated file whose hand edit the sync at HEAD replaced.

    A `ConfigFault` so it reads and routes like every other thing a human has to put
    right, and an IMMEDIATE one (`fires` is None) because nothing about it happens at a
    moment. It carries no LINE: the file was rebuilt whole, so no line in it is anybody's
    edit any more - which is also why this is not a digest (see
    `notify_overwritten_edits`). `where` is the path, so two files lost in one commit are
    two faults and not one."""
    return ConfigFault(
        path,
        f"this file is generated by the site sync, which has just rebuilt it over the "
        f"edit committed in {sha[:7]}",
        file=path,
        in_repo=site,
        fix_text=OVERWRITTEN_FIX,
    )


def _overwritten_block(
    fault: ConfigFault, org: str, sha: str, issue_url: str | None
) -> str:
    """One overwritten file, as the labelled table the appendix specifies - the same rows
    an immediate config fault gets, minus the two it cannot fill.

    Not `_block`: there is no line to deep-link and no deadline, and the fault's `where`
    IS its file, so `_block`'s first row would print the path twice. The links that matter
    here are different ones - the file as the sync has rebuilt it, and the COMMIT the edit
    is still recoverable from."""
    site_url = f"https://github.com/{org}/{fault.in_repo}"
    rows = [
        ("error line:", _anchor(f"{site_url}/blob/main/{fault.file}", fault.at)),
        (
            "error content:",
            _linked(fault.what, sha[:7], f"{site_url}/commit/{sha}"),
        ),
        ("to fix:", html.escape(fault.fix())),
    ]
    if issue_url:
        rows.append(("GH issue record:", _anchor(issue_url, issue_url)))
    return _rows(rows)


def notify_overwritten_edits(
    site_org: str,
    site: str,
    course_org: str,
    by_login: dict[str, list[tuple[str, str]]],
    issue_url: str | None,
    now: datetime,
    *,
    dry_run: bool = False,
) -> Unsent:
    """Mail each person whose edit to a generated file the site sync just rebuilt over.
    Never raises.

    NOT a digest, deliberately. Everything the digest engine does is about a fault that
    STANDS: a body rewritten each tick from the state of the file, an issue that closes
    itself once the file parses, a rung that climbs as a deadline nears. An overwritten
    edit is the opposite shape - an EVENT, already over by the time anyone hears about it,
    with nothing in the repo left to observe. Synced as a digest it would close its own
    issue on the very next run and delete the only record of what was lost. So the site
    repo keeps its one issue (`site_repo.OVERWRITE_ISSUE_TITLE`, upserted on an exact
    title like every other) and this is the letter beside it.

    `by_login` is `{committer login: [(path, commit sha)]}`, keyed "" for a commit whose
    git email is linked to no GitHub account - those fall back to the whole teaching team,
    which is the same fallback a blame nobody could read gets. `site_org` is a cohort org
    for a cohort site and the course org itself for the public one; the latter declares no
    people.yml, so nobody is addressable there and the issue's cc is the only channel."""
    shas: dict[str, str] = {}
    faults_by_key: dict[str, ConfigFault] = {}
    groups: dict[Routed, list[str]] = {}
    try:
        faculty = sync_faculty.load_cohort_faculty(site_org) or {}
    except Exception as exc:
        log_err(f"could not read {site_org}'s people.yml ({read_error(exc)})")
        faculty = {}
    contacts = sync_faculty.teaching_contacts(faculty, now.date().isoformat())
    by_handle = {c.handle.lower(): c for c in contacts}
    everyone = _addresses(c.email for c in contacts)
    instructors = _addresses(c.email for c in contacts if not c.is_ta)
    for login, edits in by_login.items():
        who = by_handle.get(login.lower()) if login else None
        if who:
            # The instructors are copied when a TA is addressed, for the reason the config
            # faults copy them: a TA editing the site is doing it on somebody's behalf.
            to = _addresses([who.email])
            cc = tuple(a for a in instructors if a not in to) if who.is_ta else ()
        else:
            # Git named nobody in people.yml - the team is the fallback, and everybody is
            # already in `to`, so there is nobody in particular to copy.
            to, cc = everyone, ()
        for path, sha in edits:
            fault = overwritten_fault(site, path, sha)
            shas[fault.key] = sha
            faults_by_key[fault.key] = fault
            groups.setdefault(Routed(to, cc), []).append(fault.key)
    if not groups:
        return Unsent()

    try:
        # Inside the guard with the delivery: both of these read the course org's identity
        # file, and a rate limit on it must cost the mail rather than the sync's report.
        label = _course_label(course_org, site_org)
        sender = html.escape(_course_name(course_org))
    except Exception as exc:
        log_err(
            f"could not mail {site_org}'s overwritten edits "
            f"({type(exc).__name__}): {exc}"
        )
        return Unsent(1, tuple(faults_by_key))

    def message(_routed: Routed, keys: list[str]) -> tuple[str, str]:
        keys.sort()
        found = [faults_by_key[k] for k in keys]
        one = html.escape(found[0].file)
        opening = (
            f"Your edit to <code>{one}</code> was overwritten by the sync"
            if len(found) == 1
            else f"{len(found)} files you edited were overwritten by the sync"
        )
        parts = [
            f"<p>This is an automated email sent on behalf of {sender}.</p>",
            f"<p>{opening} - {OVERWRITTEN_CONSEQUENCE}.</p>",
        ]
        parts += [
            _overwritten_block(f, site_org, shas[k], issue_url)
            for k, f in zip(keys, found, strict=True)
        ]
        subject = (
            f"[{label}] Your edit to {found[0].file} was overwritten by the site sync"
            if len(found) == 1
            else f"[{label}] {len(found)} of your edits were overwritten by the site sync"
        )
        return subject, "\n".join(parts) + "\n"

    try:
        return _deliver(
            site_org,
            groups,
            message,
            # Never the maintainer: a hand edit to a generated file is a habit to correct,
            # not a run that broke.
            lambda _keys: False,
            "overwritten edit(s)",
            dry_run,
        )
    except Exception as exc:
        log_err(
            f"could not mail {site_org}'s overwritten edits "
            f"({type(exc).__name__}): {exc}"
        )
        return Unsent(1, tuple(faults_by_key))


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
        sent = mailer.send_bulk([mailer.Message(to, subject, body)])
    except Exception as exc:
        log_err(f"could not mail the maintainer about {workflow} ({exc})")
        return 1
    if not sent:
        return 1
    log_ok(f"mailed the maintainer about {workflow} in {course_org}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # A subcommand rather than a flag: `--run-failed` was `required=True` and
    # `store_true`, which is a switch that can only ever be on - and a second mail here
    # would have had to be a second such switch, mutually exclusive with the first.
    commands = parser.add_subparsers(dest="command", required=True)
    failed = commands.add_parser(
        "run-failed",
        help="mail the maintainer about a failed unattended run, reading the failed "
        "step's log tail from stdin",
    )
    failed.add_argument("--course-org", required=True)
    failed.add_argument("--workflow", required=True)
    failed.add_argument("--run-url", required=True)
    args = parser.parse_args()
    # Always 0. This runs in the `if: failure()` tail of a cron that has already failed
    # for its own reasons; a notifier that reddened the run a second time would say
    # nothing new and would hide the recovery when the next green run closes the issue.
    notify_run_failed(args.course_org, args.workflow, args.run_url, sys.stdin.read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
