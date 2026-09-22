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

COUNTS ONLY, everywhere. This runs in a public workflow, and a fault's text reaches a run
log, a digest issue and an email. Who is waiting is per-person detail and goes nowhere but
`log.log_person`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import grades, roster, schedule, sync_teams, teams
from .course import CONFIG_REPO, SELF_SELECT
from .faults import ConfigFault
from .log import log_err

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

    `waiting` carries the Students themselves and not a count, because the mail PR 7 adds
    is addressed to them - and because a count is all any public surface may print off it.
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
    allowed = sync_teams.known_handles(roster.enrolled(students))
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
