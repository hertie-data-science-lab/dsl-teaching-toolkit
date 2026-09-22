"""dsl-course team-formation -- who is still waiting for a team while the window is open.

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
- the window is already shut by the time `open_windows` stops reporting it, so a held mail
  is a mail never sent rather than one that arrives after the door.

COUNTS ONLY, everywhere. This runs in a public workflow, and a fault's text reaches a run
log, a digest issue and an email. Who is waiting - and every address the mail goes to - is
per-person detail and goes nowhere but `log.log_person`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from . import config_digest, grades, mailer, roster, schedule, sync_teams, teams
from .course import CONFIG_REPO, SELF_SELECT, course_phrase
from .discovery import cohort_is_live, course_name_of
from .faults import ConfigFault, Unusable
from .gh_contents import dump_csv, get_file_with_sha, put_file, read_csv
from .log import log, log_err, log_ok, log_person

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


@dataclass(frozen=True)
class Window:
    """One assignment's team-formation window, open at the moment it was asked about, and
    what the cohort has done with it so far.

    TWO NAMES, for the reason `assign.provision_all` spells out: `key` is the SCHEDULE key,
    which teams.csv is keyed on (the Join-team form validates against `assignments:` and
    writes that key), and `name` is the cohort-side name every repo is called after. They
    differ exactly when the entry sets `cohort_dest_repo`.

    `waiting` carries the Students themselves and not a count, because the mail below is
    addressed to them - and because a count is all any public surface may print off it.
    """

    key: str
    name: str
    opens: datetime
    closes: datetime
    # Distinct team names with at least one row for `key` in teams.csv.
    teams: int
    # Enrolled, onboarded roster rows with no teams.csv row for `key`.
    waiting: tuple[roster.Student, ...]
    # How many enrolled, onboarded rows there are in all - the population `waiting` is a
    # subset of, so a surface can say "3 of 42" without a second read of the roster.
    enrolled: int
    # How many people one team may hold - `grades.team_cap`, off the same spec the fault
    # above was decided from, so it is free here and cannot disagree with the Join-team
    # form that refuses the (cap + 1)th member or with the cohort site that prints it.
    cap: int


def open_windows(
    course_org: str, cohort_org: str, sched: schedule.Schedule, now: datetime
) -> list[Window] | None:
    """Every assignment whose team-formation window is open at `now`, with the roster x
    teams.csv diff for each. `None` when the cohort could not be read.

    None is NOT an empty list, for the reason `scheduler._config_faults` gives: "we could
    not look" must not be reported as "there is nothing to report". A rate limit on
    teams.csv would otherwise say every student has a team.

    A window is here only when the assignment's template RESOLVES (a template nobody has
    written declares nothing, and the lock has already locked its form to `none`), the
    assignment is a group one, its teams are self-selected, and `now` falls inside the
    window. That last comparison is `schedule.formation_state`'s and is not re-derived
    here: the lock the Join-team form refuses on, the cohort site's callout and this all
    have to shut at the same moment.
    """
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
    # The same population every group handout provisions for, built the same way: ENROLLED
    # is this caller's own filter (an auditor gets no assignment repo at all), and
    # `sync_teams.known_handles` is the single home of the rest - the onboarded rows, which
    # are the only accounts teams.csv may name.
    enrolled_rows = roster.enrolled(students)
    allowed = sync_teams.known_handles(enrolled_rows)
    participants = [s for s in students if s.github_handle in allowed]
    found: list[Window] = []
    for key, entry in sched.assignments.items():
        spec = grades.declared_grading_spec(course_org, entry.course_source_repo)
        if spec is None or not spec.is_group:
            continue
        if spec.team_formation_resolved != SELF_SELECT:
            continue
        state, closes = schedule.formation_state(sched, key, now)
        opens = entry.handout_datetime
        if state != "open" or opens is None or closes is None:
            continue
        # teams.csv is keyed on the SCHEDULE key, and parsed CASEFOLDED - GitHub logins are
        # case-insensitive, so the roster's own casing is folded to meet it rather than the
        # other way round.
        groups = teams.teams_for(per_assignment, key)
        teamed = {handle for members in groups.values() for handle in members}
        found.append(
            Window(
                key=key,
                name=schedule.cohort_name(key, entry),
                opens=opens,
                closes=closes,
                teams=len(groups),
                waiting=tuple(
                    s for s in participants if s.github_handle.casefold() not in teamed
                ),
                enrolled=len(participants),
                cap=grades.team_cap(course_org, spec),
            )
        )
    return found


def window_faults(
    sched: schedule.Schedule, windows: list[Window] | None
) -> list[ConfigFault]:
    """One fault per open window that somebody is still waiting on. Nothing for a window
    every student has already teamed up for, and nothing at all when the cohort could not
    be read (`windows is None`).

    They ride into the SCHEDULE digest, which already carries both notifiers and the
    overnight hold - see `scheduler._preflight_sources`. No digest of their own: the entry
    a reader would edit is in schedule.yml, and one issue per file is what makes that file
    fixable in one place.
    """
    return [_fault(sched, w) for w in windows or () if w.waiting]


def _fault(sched: schedule.Schedule, window: Window) -> ConfigFault:
    """The fault for one window, filed on the line that decided when it SHUTS.

    An explicit `grading_datetime` is that line where the entry sets one, and the due date
    where it does not (`schedule.formation_window` closes on the grading pin). Naming the
    key that actually decided is what makes the deep link land where somebody would edit
    to give the cohort more time.
    """
    entry = sched.assignments[window.key]
    field = "grading_datetime" if entry.grading_datetime is not None else "due_datetime"
    return ConfigFault(
        f"assignments.{window.key}",
        _what(window),
        fires=window.closes,
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

    Two sentences, because the two states want different reading. Nobody having formed a
    team is a cohort that has not been told to; some students left over is a handful of
    people to chase, and the teams already formed are what a reader would put them in."""
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
    # The student's `hertie_email`, CASEFOLDED. Not the handle: half the point of this
    # mail is to reach the enrolled students who have not joined GitHub yet and so have no
    # handle to be keyed on. It is also what collapses a roster row somebody duplicated
    # onto one message, exactly as `enrol_codes.run` collapses it.
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

# The Join-team form's own spelling of a date (`spokenDate`, in
# templates/welcome/team-formation.yml). Written out rather than left to `strftime`, which
# answers in the runner's locale - and kept the same as the form's, so the mail, the pinned
# team list and the refusal a late student gets all name one day one way.
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def spoken_date(when: datetime, tz_name: str) -> str:
    """`4 Oct`, in the cohort's own zone - what the Join-team form calls the same day.

    The zone matters at both ends of it: a window shutting at 00:30 Berlin is the 3rd in
    UTC and the 4th to everybody who reads the mail."""
    local = schedule.in_zone(tz_name, when)
    return f"{local.day} {_MONTHS[local.month - 1]}"


def _render(
    assignment: str,
    cap: object,
    day: str,
    cohort_org: str,
    course_name: str,
    phase: str,
) -> tuple[str, str]:
    """The wording, off plain values - so the preview's placeholders and a real window's
    facts go through ONE template and the sample cannot drift from the send.

    It carries exactly what a student needs in order to act, and nothing else: which
    course, which assignment, that they have no team, how big a team may be, the day
    formation shuts, the form, and the pinned list that says which teams have room. Plain
    text, like the other two student mails.

    It says NOTHING about working alone, about a solo team or about a minimum size.
    Whether a one-person team is allowed is the instructor's call, it is written down
    nowhere the toolkit can read, and a mail that guessed would be overruling them in the
    students' inbox.

    The Join-course sentence is addressed to everybody, as the cohort site's callout is:
    the recipients include enrolled students who have not joined GitHub yet, whose team the
    form would refuse, and one body cannot ask who is reading it."""
    course = course_phrase(course_name)
    welcome = f"https://github.com/{cohort_org}/welcome/issues/new/choose"
    if phase == PHASE_REMINDER:
        subject = f"Team formation for {assignment} closes on {day}"
        opening = (
            f"Team formation for {assignment} in {course} closes on {day}, and you are "
            f"not in a team yet."
        )
        cap_line = f"Teams are up to {cap} people."
    else:
        subject = f"Form your team for {assignment}"
        opening = (
            f"{assignment} in {course} is a group assignment, and you are not in a team "
            f"yet."
        )
        cap_line = f"Teams are up to {cap} people. Team formation closes on {day}."
    body = (
        f"Hello,\n\n"
        f"{opening}\n\n"
        f"{cap_line}\n\n"
        f"To start a team, or to join one, open a 'Join team' issue here:\n"
        f"  {welcome}\n\n"
        f"The pinned 'Teams for {assignment}' issue in that repo lists the teams that "
        f"exist and how much room each has.\n"
    )
    return (f"{subject} - {course_name}" if course_name else subject), body


def message(
    window: Window, cohort_org: str, course_name: str, phase: str, tz_name: str
) -> tuple[str, str]:
    """The `(subject, body)` one open window sends its unteamed students.

    ONE body for the whole window rather than one per student: nothing in it differs by
    reader, so no name, no handle and no address appears in the text at all - which is
    also why the preview's sample is the real wording with only the window's own facts
    stood in for."""
    return _render(
        window.name,
        window.cap,
        spoken_date(window.closes, tz_name),
        cohort_org,
        course_name,
        phase,
    )


def sample_message(cohort_org: str, course_name: str = "") -> tuple[str, str]:
    """The message rendered from PLACEHOLDERS, for the dry run - `mailer.sample_of`'s job,
    done by hand because the facts this message stands on are an assignment's and not a
    student's.

    The `open` phase, because that is the one every window sends."""
    return _render("<assignment>", "<n>", "<date>", cohort_org, course_name, PHASE_OPEN)


def _phases(window: Window, now: datetime) -> tuple[str, ...]:
    """Which messages this window owes at `now`.

    `open` always - `open_windows` hands back nothing that is not open. `reminder` as well
    once the door is inside REMINDER_LEAD, so a window SHORTER than that owes both at its
    first tick: they go as ONE message (see `_Nudge.phase`), because two mails in the same
    minute is the thing a reminder must never become.

    Neither can outlive the window. A tick after it shuts does not see the window at all,
    so a message the quiet hours held overnight is one never sent rather than one that
    arrives after the door - which is the honest answer, since the form would by then
    refuse the team it asked for."""
    if window.closes - now <= REMINDER_LEAD:
        return (PHASE_OPEN, PHASE_REMINDER)
    return (PHASE_OPEN,)


def _recipients(window: Window) -> list[roster.Student]:
    """Every enrolled student this window is still waiting on - never the cohort.

    ONBOARDED only, and a student who has not joined GitHub yet is deliberately not asked
    to form a team: the only thing they can do about this mail is the Join course issue
    the enrolment code already asked them for, and a second mail naming a step they
    cannot reach is noise at best.

    Nobody is missed by that. The claim is per recipient, so a student who onboards on day
    five of a twelve-day window enters `waiting` on the next tick and is sent the OPEN
    message then - at the moment they can act on it rather than before it. It also keeps
    this set and the fault's counts talking about one population, instead of the mail
    saying 45 while the digest beside it says 40."""
    return list(window.waiting)


class _Nudge(NamedTuple):
    """One message this tick owes: which window it is about, the address as the roster
    spells it, that address casefolded (the record's key), and the phases it discharges."""

    window: Window
    to: str
    fold: str
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
        for student in _recipients(window):
            to = student.hertie_email.strip()
            fold = to.casefold()
            if not fold or fold in seen:
                continue
            seen.add(fold)
            phases = tuple(
                p for p in _phases(window, now) if (window.key, fold, p) not in mailed
            )
            if phases:
                out.append(_Nudge(window, to, fold, phases))
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


def _read_mailed(cohort_org: str) -> tuple[dict[Claim, str], str | None] | None:
    """`(what has already gone out, the sha it was read at)`, or None if it could not be
    read AS A RECORD.

    An absent file is `({}, None)` rather than a failure - the first window of a cohort's
    term creates it. A file nobody can parse is None, and None mails nobody: a record read
    as empty is a record saying nothing has ever been sent, which is a second copy of every
    message to every student in the cohort."""
    try:
        read = get_file_with_sha(cohort_org, CONFIG_REPO, MAILED_PATH)
    except Exception as exc:
        log_err(
            f"could not read {MAILED_PATH} in {cohort_org} ({exc}) - nothing mailed"
        )
        return None
    if read is None:
        return {}, None
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
    sha: str | None,
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
    write lands on top of nobody else's rows."""
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


def _preview(
    cohort_org: str, course_org: str, windows: list[Window], nudges: list[_Nudge]
) -> None:
    """The dry run's report: counts a reader can check, and the wording, from placeholders.

    No address and no name - this is the log of a workflow that runs in a PUBLIC repo. The
    sample is `sample_message`'s and never one of the bodies about to go out, which is the
    rule `grades._preview` follows."""
    for window in windows:
        owed = [n for n in nudges if n.window.key == window.key]
        phases = ", ".join(sorted({n.phase for n in owed})) or "nothing"
        # The FAULT's denominator, which is also this mail's: both are drawn from the
        # onboarded roster, so the preview and the digest cannot disagree about how many
        # students a window is waiting on.
        population = window.enrolled
        log(
            f"  {window.name}: would mail {len(owed)} of {population} enrolled "
            f"student(s) ({phases})"
        )
    subject, body = sample_message(cohort_org, _course_name(course_org))
    log("  Sample email (placeholders, not a real student):")
    log(f"    Subject: {subject}")
    for line in body.splitlines():
        log(f"    {line}")
    log_ok("DRY-RUN - nothing claimed, nothing sent")


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

    The order of the guards below IS the safety argument, and every one of them is a thing
    that must not be reachable from a claim:

    1. no window, or a cohort this tick could not read - nothing at all, and no I/O;
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
    if not windows:
        return 0
    read = _read_mailed(cohort_org)
    if read is None:
        return 1
    rows, sha = read
    nudges = _nudges(windows, now, rows)
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
        _preview(cohort_org, course_org, windows, nudges)
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
    course_name = _course_name(course_org)
    messages = [
        mailer.Message(
            n.to, *message(n.window, cohort_org, course_name, n.phase, sched.timezone)
        )
        for n in mine
    ]
    try:
        sent = mailer.send_bulk(messages)
    except Exception:
        # A transport that RAISED sent nothing at all, and the claim must not outlive it:
        # unreleased, it is a whole cohort silently recorded as told.
        _release(cohort_org, claimed, stamp)
        raise
    went_out = set(sent)
    for window in windows:
        owed = [n for n in mine if n.window.key == window.key]
        if not owed:
            continue
        # Counts only: every recipient here is a student, and this log is world-readable.
        log_ok(
            f"mailed {len([n for n in owed if n.to in went_out])} of {len(owed)} "
            f"student(s) about {window.name}"
        )
        for n in owed:
            log_person(
                f"    {'mailed' if n.to in went_out else 'NOT mailed'} about "
                f"{window.key} ({n.phase}): {mailer.mask_email(n.to)}"
            )
    # A partly-delivered batch is ROUTINE, not exotic: `send_bulk` stops at its own time
    # budget and says "re-run to continue". Releasing the claims it did not spend is what
    # keeps that true - without it, the tail of every throttled batch would be recorded as
    # told and never mailed at all.
    unsent = [n for n in mine if n.to not in went_out]
    if unsent:
        _release(cohort_org, {c for n in unsent for c in n.claims}, stamp)
        return 1
    return 0
