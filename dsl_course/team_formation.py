"""dsl-course team-formation -- who is still waiting for a team, open window or shut.

`schedule.formation_window` answers WHEN a self-selected team may be formed, off the
parsed plan alone and with no I/O, because `grades` reads it. This module owns the other
half of the same question - WHO has not formed one yet - which needs the roster and
teams.csv and therefore cannot live there. `grades` must never import it.

Until this existed, a parked group handout told the teaching team nothing at all. A
self-select assignment with no teams yet logs `[wait] no teams for <key>` and returns
green (`assign.provision_all`), which is the right answer for the tick - teams arrive days
after the handout, and the cron re-fires until they do - but the WHOLE cohort could be
sitting on an assignment nobody had told them to team up for, and nothing said so until
somebody asked on Slack.

So the wait earns a fault, on the assignment's own entry, once the window is open and
somebody is still without a team. It is an IMMEDIATE fault that carries a moment: no
`kind`, so the letter that goes out is the one for a line somebody has to act on rather
than a source counting down to a release, and `fires` set to the moment formation shuts,
so it climbs the rungs as that moment nears and pins at MISSED once it has passed. Both
follow from `ConfigFault.severity` branching on `fires`, never on `is_source`.

THE FAULT OUTLIVES THE WINDOW, and that is the whole of what makes MISSED mean anything.
A window whose door has shut on five unteamed students is the moment the problem becomes
permanent - no team, so no repo, so nothing to hand in - and a fault that vanished there
would be read by `config_digest.transitions` as a key that stopped appearing, which owes
the teaching team a *Cleared* comment. Telling them it is fixed at the exact minute it
became unfixable is the one thing this must not do. So a window that has shut short of its
teams keeps its fault, at MISSED, until somebody writes the missing rows into teams.csv,
moves the date, or takes the entry out of the plan - which are the three things that
genuinely clear it. It does not nag: MISSED is the top rung, so after the one comment for
the crossing there is no further comment and no further mail (`config_digest.transitions`
announces appearances, escalations and clears, and a standing fault is none of the three).
It is only ever the MAIL to the students that stops at the door.

And the students themselves are told, once when the window opens and once more when it is
about to shut (`notify_windows`). This is the FIRST mail the toolkit sends off a clock
rather than off a person's action - the enrolment codes fire on a push to students.csv,
the grade notifications on a button - so every one of its safety properties is a claim on
a whole cohort's inbox:

- no transport, nothing claimed (asked BEFORE the claim, as `enrol_codes.run` asks it);
- quiet hours hold the MAIL, never the window - the lock, the form, the site and the fault
  all still turn at the handout's own minute;
- claim, then send, then release the unspent, so a crash loses a nudge rather than
  duplicating one;
- a window `open_windows` reports as SHUT is never mailed about, so a held mail is a mail
  never sent rather than one that arrives after the door - which is the honest answer,
  since the Join-team form would by then refuse the team it asked for.

COUNTS ONLY, everywhere. This runs in a public workflow, and a fault's text reaches a run
log, a digest issue and an email. Who is waiting - and every address the mail goes to - is
per-person detail and goes nowhere but `log.log_person`.

Usage:
    python3 -m dsl_course.team_formation --course-org Course-Org \\
        --cohort-org hertie-dsl-demo-f2026 [--assignment assignment-2] [--no-dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from . import config_digest, grades, mailer, roster, schedule, teams
from .course import CONFIG_REPO, course_phrase
from .discovery import cohort_is_live, course_name_of, welcome_issue_url
from .faults import ConfigFault, Unusable
from .gh_contents import dump_csv, get_file_with_sha, put_file, read_csv
from .grades import self_select_keys
from .log import log, log_err, log_ok, log_person, log_step

# What the cohort LOSES while somebody is still unteamed, and what would put it right -
# this fault's own two sentences, in the voice of `faults.CONSEQUENCE` and `faults._FIX`.
#
# The consequence has to be carried rather than looked up: `faults.CONSEQUENCE` is keyed on
# the FILE, and schedule.yml's sentence says the entry "is not scheduled: nothing releases,
# hands out or grades from it" - which is exactly false here. The entry is scheduled, it
# has fired, and what is missing is the teams it fires into.
CONSEQUENCE = (
    "no repos are provisioned for that assignment, so the students still without a team "
    "cannot hand anything in"
)
# What the letter and its subject line call these. The generic wording would be a false
# description here - the entry is read perfectly well, it is simply short of the teams it
# fires into - and a subject claiming the file cannot be used sends its owner hunting for
# a syntax error instead of at the thing that is actually missing.
NOUN = (
    "assignment whose teams have not formed",
    "assignments whose teams have not formed",
)

FIX = (
    "write the missing rows into classroom-config/teams.csv yourself, or move the date on "
    "the line above to keep team formation open for longer."
)

# How long a window may stand open with somebody still waiting before its fault reaches
# the notify bar (`faults.NOTIFY_FROM`) on its own. The ladder alone says nothing until a
# day before the close, and a fortnight-long window nobody has acted on for a week is a
# cohort that has not been told - worth hearing about while there is still time to act.
WARN_AFTER_OPEN = timedelta(days=7)


@dataclass(frozen=True)
class Window:
    """One assignment's team-formation window - open at the moment it was asked about, or
    shut with somebody still waiting - and what the cohort has done with it so far.

    TWO NAMES, for the reason `assign.provision_all` spells out: `key` is the SCHEDULE key,
    which teams.csv is keyed on (the Join-team form validates against `assignments:` and
    writes that key), and `name` is the cohort-side name every repo is called after. They
    differ exactly when the entry sets `cohort_dest_repo`.

    `waiting` carries the Students themselves and not a count, because the mail below is
    addressed to them - and because a count is all any public surface may print off it.
    """

    key: str
    name: str
    # What a student calls this assignment: `gspec.title or entry.title or name`, the same
    # chain `grades.sheet_spec` walks for the gradebook's header. The mail is addressed to
    # a person, so it names the assignment the way the course site and the brief do rather
    # than by the slug the plan is keyed on.
    title: str
    closes: datetime
    # Every team with at least one row for `key` in teams.csv, as `(name, member count)`,
    # sorted by name. `teams` counts them for the fault; the cohort site prints the names and the
    # room each has left off the same file (`site._formed_teams`).
    sizes: tuple[tuple[str, int], ...]
    # Enrolled, onboarded roster rows with no teams.csv row for `key` - and the people the
    # mail below is addressed to.
    #
    # ONBOARDED only, and a student who has not joined GitHub yet is deliberately not asked
    # to form a team: the only thing they can do about such a mail is the Join course issue
    # the enrolment code already asked them for, and a second mail naming a step they cannot
    # reach is noise at best. Nobody is missed by that - the claim is per recipient, so a
    # student who onboards on day five of a twelve-day window enters `waiting` on the next
    # tick and is sent the OPEN message then, at the moment they can act on it.
    waiting: tuple[roster.Student, ...]
    # How many enrolled, onboarded rows there are in all - the population `waiting` is a
    # subset of, so a surface can say "3 of 42" without a second read of the roster.
    enrolled: int
    # How many people one team may hold - `grades.team_cap`, off the same spec the fault
    # above was decided from, so it is free here and cannot disagree with the Join-team
    # form that refuses the (cap + 1)th member or with the cohort site that prints it.
    cap: int
    # Whether the door has already SHUT on the people in `waiting`. Such a window is
    # carried for the teaching team's fault alone (see the module docstring): the mail is
    # for windows a student can still act on, and nothing else here reports one.
    shut: bool = False
    # When the window OPENED - the handout - from which `WARN_AFTER_OPEN` is counted.
    opens: datetime | None = None

    @property
    def teams(self) -> int:
        """How many teams have formed - what every count this module prints is drawn from,
        and the only shape of `sizes` a public surface may say out loud on its own."""
        return len(self.sizes)


def open_windows(
    course_org: str, cohort_org: str, sched: schedule.Schedule, now: datetime
) -> list[Window] | None:
    """Every assignment whose team-formation window is open at `now` - plus every one that
    has SHUT with somebody still unteamed - with the roster x teams.csv diff for each.
    `None` when the cohort could not be read.

    None is NOT an empty list, for the reason `scheduler._config_faults` gives: "we could
    not look" must not be reported as "there is nothing to report". A rate limit on
    teams.csv would otherwise say every student has a team.

    A window is here only when the assignment's teams are SELF-SELECTED
    (`self_select_keys`). Whether `now` falls inside it is `schedule.formation_state`'s
    comparison and is not re-derived here: the lock the Join-team form refuses on, the
    cohort site's callout and this all have to shut at the same moment.

    A SHUT window is marked `shut` and carried anyway, while anybody is still waiting at
    it, because that is the state the teaching team most needs told - see the module
    docstring. `formation_state` calls two different things `closed`, so `formation_window`
    is asked for the opening hour as well: an assignment handed out by hand has none, and a
    door that never opens left nobody standing at it.
    """
    # Asked BEFORE the two reads below, and free by then: `declared_grading_spec` memoises
    # each template's text per process and the tick has read every one of them already. A
    # cohort with no self-select assignment at all - most of them, for most of a term - has
    # no window any date could open, so students.csv and teams.csv would be two contents
    # reads a quarter of an hour that could only ever produce an empty list.
    keys = self_select_keys(course_org, sched)
    if not keys:
        return []
    try:
        students = roster.load(cohort_org)
        # ABSENT as well as unreadable, on `_config_faults`' terms: the roster is the
        # allowlist, and the empty set it implies would report a whole cohort as teamed.
        # A cohort with no students.csv already carries that fault on its own digest.
        if students is None:
            return None
        per_assignment = teams.load(cohort_org)
    except Exception as exc:
        log_err(
            f"could not work out who is still without a team in {cohort_org} "
            f"({type(exc).__name__}): {exc}"
        )
        return None
    # The same population every group handout provisions for, built the same way and with
    # the same idiom `assign` and `grades` use: ENROLLED because an auditor gets no
    # assignment repo at all, ONBOARDED because those are the only accounts teams.csv may
    # name. One filter over the roster rows, not a round trip through a handle set - that
    # re-admits any row of the unfiltered list sharing a handle, so an auditor who shares
    # one with an enrolled student would inflate the counts a public digest prints.
    participants = [s for s in roster.enrolled(students) if s.onboarded]
    found: list[Window] = []
    for key in keys:
        entry = sched.assignments[key]
        opens, closes = schedule.formation_window(sched, key)
        state, _ = schedule.formation_state(sched, key, now)
        if closes is None or state == "pending":
            continue
        # `closed` is two different things: a door that never opens - an assignment handed
        # out by hand has no hour from which "form your team now" would be true - and one
        # that has already shut. Only the second left anybody standing at it, and the
        # opening hour is what tells them apart.
        shut = state == "closed"
        if shut and opens is None:
            continue
        # teams.csv is keyed on the SCHEDULE key, and parsed CASEFOLDED - GitHub logins are
        # case-insensitive, so the roster's own casing is folded to meet it rather than the
        # other way round.
        groups = teams.teams_for(per_assignment, key)
        teamed = {handle for members in groups.values() for handle in members}
        waiting = tuple(
            s for s in participants if s.github_handle.casefold() not in teamed
        )
        # A window that shut with everybody in a team is simply over: no fault to raise, no
        # mail to send, and nothing any surface would show off it.
        if shut and not waiting:
            continue
        # The template's own definition, asked once for the two facts below: memoised per
        # template per process, and read already by `self_select_keys` above.
        spec = grades.declared_grading_spec(course_org, entry.course_source_repo)
        name = schedule.cohort_name(key, entry)
        found.append(
            Window(
                key=key,
                name=name,
                # The gradebook's chain (`grades.sheet_spec`), so the mail, the sheet and
                # the site all call the assignment one thing.
                title=spec.title or entry.title or name,
                closes=closes,
                sizes=tuple(sorted((t, len(m)) for t, m in groups.items())),
                waiting=waiting,
                enrolled=len(participants),
                cap=grades.team_cap(course_org, spec),
                shut=shut,
                opens=opens,
            )
        )
    return found


def window_faults(
    sched: schedule.Schedule, windows: list[Window] | None
) -> list[ConfigFault] | None:
    """One fault per window that somebody is still waiting on - open or shut. Nothing for a
    window every student has already teamed up for, and `None` - not an empty list - when
    the cohort could not be read (`windows is None`).

    That `None` travels all the way to `scheduler._preflight_sources`, which skips the
    digest sync on it. An empty list is what CLOSES the schedule.yml issue, so answering
    "nothing is wrong" for a tick that could not look would tell a cohort with thirty
    unteamed students that its fault had been fixed, and file it again as new an hour
    later.

    A SHUT one is where this matters most: its `fires` has passed, so it sits at MISSED,
    and it is the state nobody can put right by waiting. It clears when teams.csv, the
    date or the plan changes - never merely because the calendar moved past it.

    They ride into the SCHEDULE digest, which already carries both notifiers and the
    overnight hold - see `scheduler._preflight_sources`. No digest of their own: the entry
    a reader would edit is in schedule.yml, and one issue per file is what makes that file
    fixable in one place.
    """
    if windows is None:
        return None
    return [_fault(sched, w) for w in windows if w.waiting]


def _fault(sched: schedule.Schedule, window: Window) -> ConfigFault:
    """The fault for one window, filed on the line that decided when it SHUTS.

    An explicit `grading_datetime` is that line where the entry sets one, and the due date
    where it does not (`schedule.formation_window` closes on the grading pin). Naming the
    key that actually decided is what makes the deep link land where somebody would edit
    to give the cohort more time.

    FLOORED at WARNING once the window has been open for `WARN_AFTER_OPEN` (`warn_from`):
    the ladder off `fires` stays as it is, and only lifts the fault above that floor.
    """
    entry = sched.assignments[window.key]
    field = "grading_datetime" if entry.grading_datetime is not None else "due_datetime"
    return ConfigFault(
        f"assignments.{window.key}",
        _what(window),
        fires=window.closes,
        warn_from=window.opens + WARN_AFTER_OPEN if window.opens else None,
        field=field,
        lineno=schedule.line_of(entry.lines, field),
        file=schedule.SCHEDULE_PATH,
        in_repo=CONFIG_REPO,
        fix_text=FIX,
        consequence=CONSEQUENCE,
        noun=NOUN,
    )


def _what(window: Window) -> str:
    """What is wrong, in counts. NEVER a handle, a name or a team name: this text travels
    to a public run log, to a digest issue and to an email.

    Three sentences, because the states want different reading. Nobody having formed a team
    is a cohort that has not been told to; some students left over is a handful of people to
    chase, and the teams already formed are what a reader would put them in. A window that
    has SHUT is neither - it is a fact about the term, and its tense says so."""
    if window.shut:
        return (
            f"team formation has closed and {len(window.waiting)} of {window.enrolled} "
            f"enrolled student(s) are still without a team, across the {window.teams} "
            f"team(s) that formed"
        )
    if not window.teams:
        return (
            f"team formation is open and no team has formed yet - all "
            f"{window.enrolled} enrolled student(s) are still without one"
        )
    return (
        f"team formation is open and {len(window.waiting)} of {window.enrolled} enrolled "
        f"student(s) are not in a team yet, across the {window.teams} team(s) formed so "
        f"far"
    )


# ------------------------------------------------------------------- telling the cohort

# One row per thing SAID, in the PRIVATE classroom-config, shaped like
# `grades.DISTRIBUTED_PATH`: a re-run says nothing twice, and a message that could not be
# sent is retried exactly once, because the row holding its claim is given back.
MAILED_PATH = "team-formation/mailed.csv"
MAILED_HEADER = (
    # The SCHEDULE key, not the cohort-side name: it is what teams.csv and the Join-team
    # form are keyed on, and it survives an edit to `cohort_dest_repo` - which, keyed on
    # the name, would read as an assignment nobody had been told about and re-mail the
    # whole cohort.
    "assignment",
    # The student's `hertie_email`, CASEFOLDED. Not the handle: the address is what the
    # message is actually addressed to, and it is what collapses a roster row somebody
    # duplicated onto one message, exactly as `enrol_codes.run` collapses it.
    "recipient",
    "phase",
    "mailed_at",
)

# The two things this mail ever says. `open` is the announcement; `reminder` is the same
# facts again, for whoever still has no team as the door closes.
PHASE_OPEN = "open"
PHASE_REMINDER = "reminder"

# How long before it shuts the reminder goes out. Two days is long enough that agreeing a
# team with somebody is still possible, and short enough that it reads as a deadline.
REMINDER_LEAD = timedelta(hours=48)

# Bounded, for the reason `enrol_codes.WRITE_ATTEMPTS` gives: each attempt costs a read and
# a write, and the only other writer of this file is another tick.
WRITE_ATTEMPTS = 3

# `(assignment key, casefolded address, phase)` - one row of MAILED_PATH.
Claim = tuple[str, str, str]


def spoken_date(when: datetime, tz_name: str) -> str:
    """`4th Oct`, in the cohort's own zone - what the Join-team form calls the same day.

    The spelling is `grades.spoken_day`'s, which is also the cohort site's and the form's
    (`spokenDate`, in templates/welcome/team-formation.yml): written out rather than left
    to `strftime`, which answers in the runner's locale, and shared so the
    mail, the site and the refusal a late student gets all name one day one way.

    The zone matters at both ends of it: a window shutting at 00:30 Berlin is the 3rd in
    UTC and the 4th to everybody who reads the mail."""
    return grades.spoken_day(schedule.in_zone(tz_name, when))


def greeting(name: str) -> str:
    """`Dear Anna,` off a roster row's full name - the FIRST token of it, because that is
    how a person is addressed and `Dear Anna Adams,` reads like a form letter.

    A roster row with no name at all falls back to `Hello,`: blank is what a cohort
    imported from a system that only had addresses looks like, and a greeting to nobody is
    worse than none."""
    first = next(iter(name.split()), "")
    return f"Dear {first}," if first else "Hello,"


def numbered(title: str, name: str, number: int | None) -> str:
    """`Assignment 3: Project` - the assignment as its page on the cohort site is
    numbered, then as a student calls it.

    Not doubled up: a title that already starts `Assignment 3`, or that is only the slug,
    is the number and nothing more to add. Without a number (the page could not be looked
    up) it is the title alone."""
    if number is None:
        return title
    lead = f"Assignment {number}"
    if title == name:
        return lead
    if re.match(rf"assignment[\s_-]*{number}\b", title, re.IGNORECASE):
        return title
    return f"{lead}: {title}"


def _render(
    salutation: str,
    title: str,
    cap: object,
    day: str,
    cohort_org: str,
    course_name: str,
    phase: str,
    page_url: str | None,
) -> tuple[str, str]:
    """The wording, off plain values - so the preview's placeholders and a real window's
    facts go through ONE template and the sample cannot drift from the send. The
    SALUTATION is one of those values rather than a name to format here: the dry run has a
    placeholder to stand in its place, and no student's name may reach a public log.

    It carries exactly what a student needs in order to act, and nothing else: who it is
    for, which course, which assignment, how big a team may be, the day formation shuts,
    the form, and the page that says which teams have room. Plain text, like the other two
    student mails.

    The assignment is named by its number and TITLE (`numbered`), not by the schedule key:
    this is the one mail about it a student ever gets, and `assignment-2` is what the plan
    calls it rather than what the site and the brief do.

    `page_url` is the assignment's page on the cohort site, which lists the teams that
    exist, and the sentence pointing at it goes ONLY when there is one to point at. A
    course org whose templates could not be listed gets a shorter mail rather than a link
    to the wrong page.

    It says NOTHING about working alone, about a solo team or about a minimum size.
    Whether a one-person team is allowed is the instructor's call, it is written down
    nowhere the toolkit can read, and a mail that guessed would be overruling them in the
    students' inbox."""
    course = course_phrase(course_name)
    welcome = welcome_issue_url(cohort_org)
    if phase == PHASE_REMINDER:
        subject = f"Team formation for {title} closes on {day}"
        opening = (
            f"Team formation for {title} in {course} closes on {day}, and you are not in "
            f"a team yet."
        )
        cap_line = f"Teams are up to {cap} people."
    else:
        subject = f"Form your team for {title}"
        opening = f"{title} in {course} is a group assignment."
        cap_line = f"Teams are up to {cap} people. Team formation closes on {day}."
    listed = (
        f"\n\nThe teams that exist, and how much room each has, are listed on the "
        f"assignment's page:\n  {page_url}"
        if page_url
        else ""
    )
    body = (
        f"{salutation}\n\n"
        f"{opening}\n\n"
        f"{cap_line}\n\n"
        f"To start a team, or to join one, open a 'Join team' issue here:\n"
        f"  {welcome}"
        f"{listed}\n"
    )
    return (f"{subject} - {course_name}" if course_name else subject), body


def message(
    window: Window,
    cohort_org: str,
    course_name: str,
    phase: str,
    tz_name: str,
    name: str = "",
    page: schedule.AssignmentPage | None = None,
) -> tuple[str, str]:
    """The `(subject, body)` one open window sends ONE of its unteamed students.

    Per student, for the greeting and for nothing else: the send path already builds one
    `mailer.Message` per recipient, so this costs nothing, and a mail asking somebody to go
    and find three people to work with reads better addressed to them than to a cohort.
    The name therefore appears in the BODY and nowhere else - not in a log line, not in the
    dry run's sample, and not in a subject that a mail client shows in a list.

    `page` is the assignment's page on the cohort site (`schedule.assignment_pages`): its
    number names the assignment, and its URL is the list of teams."""
    return _render(
        greeting(name),
        numbered(window.title, window.name, page.number if page else None),
        window.cap,
        spoken_date(window.closes, tz_name),
        cohort_org,
        course_name,
        phase,
        page.url(cohort_org) if page else None,
    )


def sample_message(cohort_org: str, course_name: str = "") -> tuple[str, str]:
    """The message rendered from PLACEHOLDERS, for the dry run - `mailer.sample_of`'s job,
    done by hand because the facts this message stands on are an assignment's and not a
    student's.

    The name is a placeholder too, and that is the point: the preview is printed in a
    public run log, so it shows the SHAPE of the greeting and never a student's.

    The `open` phase, because that is the one every window sends."""
    return _render(
        "Dear <first name>,",
        "Assignment <n>: <title>",
        "<n>",
        "<date>",
        cohort_org,
        course_name,
        PHASE_OPEN,
        "<assignment page>",
    )


def _phases(window: Window, now: datetime) -> tuple[str, ...]:
    """Which messages this window owes at `now`.

    `open` always - `notify_windows` hands in only the windows still open. `reminder` as well
    once the door is inside REMINDER_LEAD, so a window SHORTER than that owes both at its
    first tick: they go as ONE message (see `_Nudge.phase`), because two mails in the same
    minute is the thing a reminder must never become.

    Neither can outlive the window. A tick after it shuts does not mail about it at all,
    so a message the quiet hours held overnight is one never sent rather than one that
    arrives after the door - which is the honest answer, since the form would by then
    refuse the team it asked for."""
    if window.closes - now <= REMINDER_LEAD:
        return (PHASE_OPEN, PHASE_REMINDER)
    return (PHASE_OPEN,)


class _Nudge(NamedTuple):
    """One message this tick owes: which window it is about, the address as the roster
    spells it, that address casefolded (the record's key), the name the body greets, and
    the phases it discharges.

    The NAME rides here because the body is per student now, and the record is not: it is
    keyed on the address, exactly as before, so a roster row renamed between two ticks
    changes what the next mail says and nothing about what is owed."""

    window: Window
    to: str
    fold: str
    name: str
    phases: tuple[str, ...]

    @property
    def claims(self) -> set[Claim]:
        return {(self.window.key, self.fold, p) for p in self.phases}

    @property
    def phase(self) -> str:
        """The one phase whose WORDING goes out. `open` whenever it is owed: a message
        headed like a reminder, to somebody who was never told in the first place, refers
        to a mail that does not exist."""
        return PHASE_OPEN if PHASE_OPEN in self.phases else PHASE_REMINDER


def _nudges(
    windows: list[Window], now: datetime, mailed: dict[Claim, str]
) -> list[_Nudge]:
    """What this tick owes, in one pass over the windows - the single place a message is
    decided on.

    Three things fall out of it rather than being coded for: a re-run sends nothing (every
    phase it would owe is already in the record), a student who joined a team since the
    last tick is not nudged again (they are no longer in `waiting`), and a roster row
    somebody duplicated gets one message rather than two (the address is the key, as it is
    in `enrol_codes.run`)."""
    out: list[_Nudge] = []
    for window in windows:
        seen: set[str] = set()
        for student in window.waiting:
            to = student.hertie_email.strip()
            fold = to.casefold()
            if not fold or fold in seen:
                continue
            seen.add(fold)
            phases = tuple(
                p for p in _phases(window, now) if (window.key, fold, p) not in mailed
            )
            if phases:
                out.append(_Nudge(window, to, fold, student.name, phases))
    return out


def parse_mailed(text: str) -> dict[Claim, str]:
    """`mailed.csv` into `{claim: when}`. Machine-written, but it sits in a repo faculty
    can edit, so it comes through the same BOM/delimiter guard as the roster."""
    rows: dict[Claim, str] = {}
    for row in read_csv(text, ("recipient",), MAILED_PATH):
        recipient = (row.get("recipient") or "").strip().casefold()
        if not recipient:
            continue
        key = (
            (row.get("assignment") or "").strip(),
            recipient,
            (row.get("phase") or "").strip(),
        )
        rows[key] = (row.get("mailed_at") or "").strip()
    return rows


def dump_mailed(rows: dict[Claim, str]) -> str:
    """Sorted, so a tick that mailed one student shows one line in the diff."""
    return dump_csv(
        MAILED_HEADER,
        ((key, who, phase, when) for (key, who, phase), when in sorted(rows.items())),
    )


def _read_mailed(cohort_org: str) -> tuple[dict[Claim, str], str] | None:
    """`(what has already gone out, the sha it was read at)`, or None if it could not be
    read AS A RECORD.

    An absent file is `({}, "")` rather than a failure - the first window of a cohort's
    term creates it. A file nobody can parse is None, and None mails nobody: a record read
    as empty is a record saying nothing has ever been sent, which is a second copy of every
    message to every student in the cohort.

    `""` and NOT None, which is what `grades.sync_team_lock` passes for the same absence and
    for the same reason: `put_file` reads None as "fetch the sha yourself and overwrite",
    which is the exact opposite of what `_claim` promises. Empty is sent as no sha at all,
    so GitHub refuses the write if the file has appeared since - and the first claim of a
    term, which is the only time this file is absent, is precisely when a faculty press and
    a tick can both be holding the whole cohort."""
    try:
        read = get_file_with_sha(cohort_org, CONFIG_REPO, MAILED_PATH)
    except Exception as exc:
        log_err(
            f"could not read {MAILED_PATH} in {cohort_org} ({exc}) - nothing mailed"
        )
        return None
    if read is None:
        return {}, ""
    text, sha = read
    try:
        return parse_mailed(text), sha
    except Unusable as exc:
        log_err(f"{exc} Nothing mailed about team formation.")
        return None


def _claim(
    cohort_org: str,
    claims: set[Claim],
    stamp: str,
    rows: dict[Claim, str],
    sha: str,
) -> set[Claim] | None:
    """Write `claims` into the record BEFORE anything is sent. Returns the claims this run
    actually inserted, or None if no attempt was accepted.

    Claim-then-send is `enrol_codes.run`'s ordering, and it is here for its reason: a
    record that cannot be written (an archived classroom-config, a token that lost write
    scope, a run of 5xx) must mean NOTHING WAS MAILED, which the next tick retries - not a
    batch that went out with no record of it, which the next tick sends again.

    What comes back is the subset this run INSERTED and not the set it asked for: a
    concurrent tick that claimed the same student between the read and the write has
    already taken responsibility for that message, and sending it as well is the duplicate
    the whole protocol exists to avoid.

    Retried like `enrol_codes.write_column`, against the sha the record was READ at, so a
    write lands on top of nobody else's rows - and against the EMPTY sha where there was no
    record to read at all, which GitHub refuses if another writer has created it since
    (`_read_mailed`). Either way a refusal means re-read, re-decide and retry."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        mine = {c for c in claims if c not in rows}
        if not mine:
            return set()
        if put_file(
            cohort_org,
            CONFIG_REPO,
            MAILED_PATH,
            dump_mailed(rows | dict.fromkeys(mine, stamp)).encode(),
            f"team formation: claim {len(mine)} message(s)",
            expected_sha=sha,
        ):
            return mine
        if attempt == WRITE_ATTEMPTS:
            break
        log_err(
            f"{MAILED_PATH} in {cohort_org} could not be written as read - re-reading "
            f"and retrying ({attempt}/{WRITE_ATTEMPTS - 1})"
        )
        fresh = _read_mailed(cohort_org)
        if fresh is None:
            break
        rows, sha = fresh
    return None


def _release(cohort_org: str, unsent: set[Claim], stamp: str) -> None:
    """Give back the claims the send did not spend, so a later tick retries them.

    Only rows still carrying THIS run's exact `stamp` are dropped, so a claim another tick
    has written since is left exactly where it is.

    Never raises: it runs on the failure path, including from an `except` block where a
    raise of its own would replace the exception the caller has to see."""
    try:
        for attempt in range(1, WRITE_ATTEMPTS + 1):
            read = _read_mailed(cohort_org)
            if read is None:
                break
            rows, sha = read
            keep = {k: v for k, v in rows.items() if not (k in unsent and v == stamp)}
            # Nothing of ours is left to give back - somebody else's write already took
            # the rows - so there is no claim outstanding and nothing to report.
            if len(keep) == len(rows) or put_file(
                cohort_org,
                CONFIG_REPO,
                MAILED_PATH,
                dump_mailed(keep).encode(),
                f"team formation: release {len(rows) - len(keep)} unsent claim(s)",
                expected_sha=sha,
            ):
                log_err(
                    f"{len(unsent)} team-formation message(s) in {cohort_org} were not "
                    f"sent - their claim was released, so the next tick retries them."
                )
                return
            if attempt == WRITE_ATTEMPTS:
                break
    except Exception as exc:  # a failed release must still be REPORTED, not raised
        log_err(f"releasing the unsent team-formation claims failed: {exc}")
    # The one failure that must never be swallowed: the record says these students were
    # told and they were not, so nothing will ever retry them. No address here - this line
    # lands in a world-readable Actions log - but the stamp is exact, and every row
    # carrying it is a row to clear.
    log_err(
        f"{len(unsent)} row(s) in {MAILED_PATH} in {cohort_org} are stamped "
        f"mailed_at={stamp} but were never sent, and the stamp could not be cleared - "
        f"delete those rows by hand, or those students never hear about team formation."
    )


def _course_name(course_org: str) -> str:
    """The course's name for the subject line, or "" if it cannot be read.

    Never fatal: `course_name_of` raises on a dsl-course.yml that is malformed or that the
    API would not hand over, and a name is not worth losing a cohort's only notice of team
    formation over. A course carrying no name keeps the generic wording (`course_phrase`)
    rather than mailing a blank."""
    try:
        return course_name_of(course_org)
    except Exception as exc:
        log_err(f"could not read the course name ({exc}) - mailing without it")
        return ""


def _messages(
    nudges: list[_Nudge],
    cohort_org: str,
    course_name: str,
    tz_name: str,
    pages: dict[str, schedule.AssignmentPage],
) -> list[mailer.Message]:
    """The batch these nudges are, rendered once - so the dry run previews exactly the
    messages a real run would send rather than a description of them."""
    return [
        mailer.Message(
            n.to,
            *message(
                n.window,
                cohort_org,
                course_name,
                n.phase,
                tz_name,
                n.name,
                pages.get(n.window.key),
            ),
        )
        for n in nudges
    ]


def _preview(
    cohort_org: str,
    course_org: str,
    windows: list[Window],
    nudges: list[_Nudge],
    sched: schedule.Schedule,
) -> None:
    """The dry run's report: counts a reader can check, then `send_bulk`'s own preview of
    the real batch.

    Its own and not a hand-rolled one, which is the whole value of a rehearsal here:
    `mailer.preflight` runs inside it and PROVES the GRAPH_* secrets, while a preview that
    returns before the transport is chosen reads the same whether the certificate is right,
    wrong or absent. This is the toolkit's first clock-driven cohort-wide mail, so its only
    rehearsal surface has to test something.

    No address and no name reaches the public log either way: `send_bulk` prints counts and
    subjects, and puts the masked recipients on `log_person`. The sample is
    `sample_message`'s placeholders and never one of the bodies about to go out, which is
    the rule `grades._email_updates` follows."""
    course_name = _course_name(course_org)
    for window in windows:
        owed = [n for n in nudges if n.window.key == window.key]
        phases = ", ".join(sorted({n.phase for n in owed})) or "nothing"
        # The FAULT's denominator, which is also this mail's: both are drawn from the
        # onboarded roster, so the preview and the digest cannot disagree about how many
        # students a window is waiting on.
        log(
            f"  {window.name}: would mail {len(owed)} of {window.enrolled} enrolled "
            f"student(s) ({phases})"
        )
    mailer.send_bulk(
        _messages(
            nudges,
            cohort_org,
            course_name,
            sched.timezone,
            schedule.assignment_pages_by_key(course_org, cohort_org, sched),
        ),
        dry_run=True,
        sample=sample_message(cohort_org, course_name)[1],
    )
    log_ok("DRY-RUN - nothing claimed")


def notify_windows(
    course_org: str,
    cohort_org: str,
    sched: schedule.Schedule,
    windows: list[Window] | None,
    now: datetime,
    *,
    dry_run: bool,
) -> int:
    """Mail the students an open team-formation window is still waiting on. Returns the
    ERROR count, which the scheduler's tick adds to its own.

    RAISES where the transport does - a Graph token request that failed is a RuntimeError
    out of `mailer` - and the claim is given back first. The scheduler contains it per
    cohort (`_team_formation_phase`); the faculty button lets `main` turn it into one line.

    The order of the guards below IS the safety argument, and every one of them is a thing
    that must not be reachable from a claim:

    0. a window that has already SHUT is not mailed about at all. It is here for the
       teaching team's fault, which outlives the door (`window_faults`); asking a student to
       form a team the Join-team form would now refuse is asking for something they cannot
       do, and it is why a message the quiet hours held overnight is one never sent;
    1. no window, no window anybody is still waiting on, or a cohort this tick could not
       read - nothing at all, and no I/O. A window stands open for WEEKS, so that second
       case is most of them: once the cohort has teamed up, the record below would be read
       on every tick until the grading pin with nothing ever owed off it. It is exactly
       equivalent - `_nudges` iterates `window.waiting`, so an all-empty `waiting` can only
       produce an empty list - and it is the same filter `window_faults` already applies;
    2. a record nobody can read - nothing, and the tick goes red. Read as empty it would
       be a second copy of every message to every student in the cohort;
    3. nothing owed - every phase is already recorded, which is what a re-run hits;
    4. QUIET HOURS - the MAIL is held, never the window. The window itself turned at its
       own minute: the lock, the Join-team form, the site's callout and the teaching team's
       fault are all upstream of this call, and only the message waits for 07:00 local,
       because a `handout_datetime` of 00:00 otherwise wakes a cohort at 2am;
    5. a cohort that has been closed out - its classroom-config is frozen, so the claim
       could not land, and nobody is forming a team in a term that is over;
    6. NO TRANSPORT, asked BEFORE the claim. An org whose GRAPH_* secrets were never set
       claims nothing, says so once, and is offered the same messages by the next tick -
       which is what makes this inert rather than destructive on such a cohort;
    7. the claim, then the send, then the release of whatever the send did not spend. A
       crash between the claim and the send loses a nudge; the other ordering duplicates
       one, to a whole cohort, and that asymmetry is deliberate."""
    live = [w for w in windows or () if not w.shut]
    if not any(w.waiting for w in live):
        return 0
    read = _read_mailed(cohort_org)
    if read is None:
        return 1
    rows, sha = read
    nudges = _nudges(live, now, rows)
    if not nudges:
        return 0
    if config_digest.in_quiet_hours(schedule.in_cohort_zone(sched, now)):
        log(
            f"  [skip] {len(nudges)} team-formation message(s) held until "
            f"{config_digest.QUIET_UNTIL:02d}:00 in {sched.timezone}"
        )
        return 0
    # Asked here rather than left to the scheduler's own guard because this module is the
    # one that WRITES. `cohort_is_live` logs its own line.
    if not cohort_is_live(cohort_org):
        return 0
    if dry_run:
        _preview(cohort_org, course_org, live, nudges, sched)
        return 0
    if mailer.graph_config_from_env() is None:
        log(
            f"  [skip] no mail transport for {cohort_org} - nothing claimed, and the "
            f"next tick offers these {len(nudges)} message(s) again"
        )
        return 0
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    claimed = _claim(
        cohort_org, {c for n in nudges for c in n.claims}, stamp, rows, sha
    )
    if claimed is None:
        log_err(
            f"could not write {MAILED_PATH} in {cohort_org} - nothing mailed, so the "
            f"next tick is safe to retry."
        )
        return 1
    # Only the nudges this run actually claimed, and only the phases of them it claimed: a
    # phase a concurrent tick recorded first is that tick's message to send, not this one's.
    mine = [
        n._replace(
            phases=tuple(p for p in n.phases if (n.window.key, n.fold, p) in claimed)
        )
        for n in nudges
    ]
    mine = [n for n in mine if n.phases]
    if not mine:
        return 0
    # The pages are looked up HERE, on a tick that has a message to write, and not in
    # `open_windows`, which runs on every cohort on every tick: they cost a listing of the
    # course org, and once the cohort has teamed up nothing is owed.
    messages = _messages(
        mine,
        cohort_org,
        _course_name(course_org),
        sched.timezone,
        schedule.assignment_pages_by_key(course_org, cohort_org, sched),
    )
    try:
        # WHICH MESSAGES went out, by position, and never which ADDRESSES: `mine` and the
        # batch are built in one order from one list, and a student unteamed in two open
        # windows owns two messages to one address. Read off addresses, a batch that stopped
        # at its time budget partway through the second window would count everybody already
        # mailed for the first as mailed for the second too - so their claim would never be
        # released, `mailed.csv` would say for ever that they had been told about the second
        # assignment, and they would never hear about it at all.
        delivered = set(mailer.send_indexed(messages))
    except Exception:
        # A transport that RAISED sent nothing at all, and the claim must not outlive it:
        # unreleased, it is a whole cohort silently recorded as told.
        _release(cohort_org, claimed, stamp)
        raise
    for window in live:
        owed = [(i, n) for i, n in enumerate(mine) if n.window.key == window.key]
        if not owed:
            continue
        # Counts only: every recipient here is a student, and this log is world-readable.
        log_ok(
            f"mailed {sum(i in delivered for i, _n in owed)} of {len(owed)} "
            f"student(s) about {window.name}"
        )
        for i, n in owed:
            log_person(
                f"    {'mailed' if i in delivered else 'NOT mailed'} about "
                f"{window.key} ({n.phase}): {mailer.mask_email(n.to)}"
            )
    # A partly-delivered batch is ROUTINE, not exotic: the send stops at its own time
    # budget and says "re-run to continue". Releasing the claims it did not spend is what
    # keeps that true - without it, the tail of every throttled batch would be recorded as
    # told and never mailed at all.
    unsent = [n for i, n in enumerate(mine) if i not in delivered]
    if unsent:
        _release(cohort_org, {c for n in unsent for c in n.claims}, stamp)
        return 1
    return 0


# --------------------------------------------------------------- the faculty button

# `python3 -m dsl_course.team_formation`, which the seeded **Open team formation**
# workflow invokes - so the module name is a frozen public contract from here on
# (docs/reference/maintainers.md).
#
# It is a SECOND WAY IN to the pass above and not a second implementation of it: `run`
# reads the same windows and calls the same `notify_windows`, so the claim record, the
# transport-before-claim ordering, the per-recipient claim and the wording are the tick's
# and cannot drift from it. Pressing twice mails nobody the second time for the one reason
# a re-tick does - `mailed.csv` already holds every phase the second press would owe - and
# that needed no code here at all.
#
# QUIET HOURS APPLY TO THE PRESS TOO, deliberately: the 23:00-07:00 hold is a rule about
# the STUDENTS' night, and a cohort woken at 02:00 is woken just as hard by a human as by
# a datetime. Nothing is lost to it either - a held message claims nothing, so the next
# quarter-hourly tick after 07:00 sends exactly what the press asked for, and the run says
# so in one line. The band is narrow enough that the case the button exists for (just told
# them in class, send it now) falls outside it.


def _narrow(
    cohort_org: str, sched: schedule.Schedule, windows: list[Window], only: str
) -> list[Window] | None:
    """`windows` cut to the one `only` names. None - an ERROR - when it names none of them.

    A key nobody recognises must not read as "nothing to do": a mistyped box that exits 0
    is a faculty member who believes a cohort has been mailed and has not been. So the
    refusal names what IS open, which is both the correction and the list they wanted.

    Whether the key is in schedule.yml at all is worth saying as well, because the two
    mistakes have different fixes: a typo in the box, or a window that is not open yet."""
    chosen = [w for w in windows if w.key == only]
    if chosen:
        return chosen
    listed = ", ".join(sorted(w.key for w in windows)) or "none"
    unknown = (
        ""
        if only in sched.assignments
        else f" (and no `{only}:` under `assignments:` in {schedule.SCHEDULE_PATH})"
    )
    log_err(
        f"no team-formation window is open for {only} in {cohort_org}{unknown} - open "
        f"right now: {listed}. Nothing mailed."
    )
    return None


def run(
    course_org: str,
    cohort_org: str,
    now: datetime,
    *,
    only: str = "",
    dry_run: bool = True,
) -> int:
    """One press of **Open team formation**: mail whoever is still without a team, for
    every open window or for the one `only` names. Returns the error count.

    `only` is the SCHEDULE key, because that is what teams.csv, the Join-team form and the
    record are keyed on - and it is free text on the button rather than a dropdown, since
    the keys are per cohort and the workflow is rendered once for the whole course org.

    A cohort this run could not READ is red here, where the tick treats the same None as
    "nothing to report": somebody is standing at this run, and a press that reached nobody
    must not look like a press that had nobody to reach."""
    sched = schedule.load(cohort_org)
    windows = open_windows(course_org, cohort_org, sched, now)
    if windows is None:
        log_err(
            f"could not work out who is waiting for a team in {cohort_org} - nothing "
            f"mailed. The line above says what could not be read."
        )
        return 1
    # The button mails, so it sees the windows a student can still act on. A shut one is
    # carried by `open_windows` for the teaching team's fault alone, and offering it here
    # would let a press ask a cohort to walk through a door the form has already locked.
    windows = [w for w in windows if not w.shut]
    if only:
        narrowed = _narrow(cohort_org, sched, windows, only)
        if narrowed is None:
            return 1
        windows = narrowed
    if not windows:
        log_ok(
            f"no team-formation window is open in {cohort_org} right now - nothing to "
            f"send."
        )
        return 0
    log_step(
        f"Team formation in {cohort_org}: {len(windows)} open window(s)"
        + (f", asked about {only} alone" if only else "")
    )
    return notify_windows(course_org, cohort_org, sched, windows, now, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument(
        "--assignment",
        default="",
        metavar="KEY",
        help=(
            "Only this schedule.yml `assignments:` key's window. Omitted: every window "
            "open right now. A key with no open window is an error, not a quiet no-op."
        ),
    )
    # Default ON, as Distribute grades' flag is and for its reason: the rendered workflow
    # passes --dry-run / --no-dry-run explicitly, so a bare local invocation cannot mail a
    # cohort by accident.
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preview the messages; claim nothing, send nothing (default).",
    )
    args = parser.parse_args()
    # A read helper (or the mail transport) that couldn't reach its API raises; in an
    # Actions log a one-line error beats a traceback, and the run still goes red.
    try:
        # A closed-out cohort's classroom-config is frozen, so the claim this send depends
        # on could not land - and nobody is forming a team in a term that is over. Green,
        # as every other sweep treats one: a finished term is a state somebody chose.
        # `notify_windows` asks the same question again as its own last guard before it
        # writes; this one is here so an archived cohort is not read first.
        if not cohort_is_live(args.cohort_org):
            return 0
        return run(
            args.course_org,
            args.cohort_org,
            datetime.now(UTC),
            only=args.assignment,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
