"""The other half of the team-formation window: who has NOT formed a team, and the fault
that says so.

A parked group handout was silent - `assign.provision_all` logs `[wait] no teams` and
returns green - so the teaching team heard nothing until somebody asked. These pin the
four things it has to get right: which assignments earn a window at all, that the fault
climbs towards the moment formation SHUTS and STAYS once it has passed, that only a
window a student can still act on is mailed about, and that nothing it says names a
person.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import ROSTER_HEADER

from dsl_course import discovery, team_formation
from dsl_course.faults import Severity
from dsl_course.grades import GradingSpec
from dsl_course.mailer import send_bulk as _SEND_BULK
from dsl_course.schedule import AssignmentEntry, Schedule

BERLIN = ZoneInfo("Europe/Berlin")
COURSE, COHORT = "Course-Org", "Cohort-f2026"
OPENS = datetime(2026, 9, 22, 7, 0, tzinfo=BERLIN)
SHUTS = datetime(2026, 10, 4, 23, 59, 59, tzinfo=BERLIN)
INSIDE = datetime(2026, 9, 25, 9, 0, tzinfo=BERLIN)

GROUP = GradingSpec(type="group", team_formation="self_select")

# Four onboarded students and one who has not joined GitHub yet - the row every count
# below has to leave out, because a student with no handle cannot be in a team.
ROSTER = (
    f"{ROSTER_HEADER}\n"
    "anna@x.edu,Anna Adams,enrolled,anna-adams,1,,\n"
    "ben@x.edu,Ben Baker,enrolled,ben-baker,2,,\n"
    "carla@x.edu,Carla Cohen,enrolled,Carla-Cohen,3,,\n"
    "dan@x.edu,Dan Doyle,enrolled,dan-doyle,4,,\n"
    "eve@x.edu,Eve Evans,enrolled,,,,\n"
    "fred@x.edu,Fred Frey,auditor,fred-frey,6,,"
)
TEAMS_HEADER = "assignment,team,github_handle"


def _entry(**kw) -> AssignmentEntry:
    return AssignmentEntry(
        course_source_repo="a2-f2026",
        due_datetime=SHUTS,
        handout_datetime=OPENS,
        lines={"due_datetime": 12, "grading_datetime": 14},
        **kw,
    )


def _sched(**entries: AssignmentEntry) -> Schedule:
    return Schedule(assignments=dict(entries or {"assignment-2": _entry()}))


@pytest.fixture
def cohort(monkeypatch):
    """The cohort's two hand-edited files and each template's definition, as TEXT where
    there is text - so the real parsers run and the fold-casing rules under test are the
    ones the toolkit actually applies."""

    def _wire(roster_csv: str = ROSTER, teams_csv: str = "", spec=GROUP):
        monkeypatch.setattr(
            team_formation.roster, "_roster_text", lambda org: roster_csv
        )
        monkeypatch.setattr(
            team_formation.teams,
            "_teams_text",
            lambda org: teams_csv or None,
        )
        monkeypatch.setattr(
            team_formation.grades,
            "declared_grading_spec",
            lambda org, template: spec,
        )
        # The cap the window carries and the mail prints: `grades.team_cap` falls back to
        # the COURSE org's assignment_defaults when the template names no size, and that
        # is a live read of its dsl-course.yml.
        monkeypatch.setattr(
            team_formation.grades,
            "course_assignment_defaults",
            lambda org: {"max_team_size": 4},
        )

    return _wire


def _rows(*pairs: tuple[str, str], key: str = "assignment-2") -> str:
    return "\n".join([TEAMS_HEADER, *(f"{key},{team},{h}" for team, h in pairs)])


# ------------------------------------------------------------------ which windows open


def test_an_open_self_select_group_window_is_reported_with_when_it_shuts(cohort):
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert (window.key, window.closes) == ("assignment-2", SHUTS)
    assert (window.teams, window.enrolled) == (0, 4)


def test_the_cohort_side_name_rides_along_beside_the_schedule_key(cohort):
    # teams.csv is keyed on the SCHEDULE key and every repo is named after the cohort-side
    # name, and they differ exactly when `cohort_dest_repo` is set - so a surface that had
    # only one of them would either find no teams or name a repo nobody has.
    cohort(teams_csv=_rows(("team-x", "anna-adams"), key="assignment-2"))
    (window,) = team_formation.open_windows(
        COURSE,
        COHORT,
        _sched(**{"assignment-2": _entry(cohort_dest_repo="a2")}),
        INSIDE,
    )
    assert (window.key, window.name) == ("assignment-2", "a2")
    assert window.teams == 1


def test_an_individual_assignment_never_opens_a_window(cohort):
    cohort(spec=GradingSpec(type="individual"))
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_an_assigned_group_assignment_never_opens_a_window(cohort):
    # The teaching team writes teams.csv for these, and the Join-team form refuses them -
    # so "nobody has self-selected yet" is not a thing that can be true of one.
    cohort(spec=GradingSpec(type="group", team_formation="assigned"))
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_a_template_with_no_definition_opens_no_window(cohort):
    # The lock has already locked such an assignment's form to `none`, so there is no door
    # for a student to be waiting at.
    cohort(spec=None)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_a_window_that_has_not_opened_yet_is_not_reported(cohort):
    cohort()
    when = OPENS - timedelta(minutes=1)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), when) == []


@pytest.mark.parametrize("when", [SHUTS, SHUTS + timedelta(days=30)])
def test_a_shut_window_is_still_reported_while_somebody_is_unteamed(cohort, when):
    # The door shutting is the moment the problem becomes PERMANENT - no team, so no repo,
    # so nothing to hand in - and a window that disappeared here would be read by the
    # digest as a fault that cleared. Marked `shut`, so nothing mails about it.
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), when)
    assert window.shut is True and len(window.waiting) == 4


def test_a_shut_window_everybody_teamed_up_for_is_simply_over(cohort):
    cohort(
        teams_csv=_rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
        )
    )
    assert team_formation.open_windows(COURSE, COHORT, _sched(), SHUTS) == []


def test_an_open_window_is_not_marked_shut(cohort):
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert window.shut is False


@pytest.mark.parametrize("when", [INSIDE, SHUTS + timedelta(days=1)])
def test_an_assignment_handed_out_by_hand_has_no_window_to_be_waiting_at(cohort, when):
    # No `handout_datetime` is no hour from which "form your team now" would be true. Asked
    # past the grading pin too: `formation_state` calls both a door that never opens and one
    # that has shut `closed`, and only the second left anybody standing at it - read as the
    # second, every hand-handed-out group assignment in the plan would carry a MISSED fault.
    cohort()
    sched = _sched(
        **{
            "assignment-2": AssignmentEntry(
                due_datetime=SHUTS, course_source_repo="a2-f2026"
            )
        }
    )
    assert team_formation.open_windows(COURSE, COHORT, sched, when) == []


# -------------------------------------------------------------------------- who waits


def test_only_enrolled_onboarded_rows_count_as_waiting(cohort):
    # An auditor gets no assignment repo at all, and a row with no handle cannot be in a
    # team - so neither is anybody to chase.
    cohort()
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert {s.github_handle for s in window.waiting} == {
        "anna-adams",
        "ben-baker",
        "Carla-Cohen",
        "dan-doyle",
    }


def test_a_handle_typed_in_another_casing_is_the_same_student(cohort):
    # teams.csv is parsed casefolded (GitHub logins are case-insensitive); a roster handle
    # compared in its own casing would read as a student who never joined, and would be
    # chased for a team she is already in.
    cohort(teams_csv=_rows(("team-x", "CARLA-COHEN")))
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert "Carla-Cohen" not in {s.github_handle for s in window.waiting}
    assert window.enrolled == 4 and len(window.waiting) == 3


def test_an_auditor_sharing_a_handle_with_a_student_is_still_not_counted(cohort):
    # The population is one filter over the ENROLLED rows, never a round trip through a
    # set of handles: a set re-admits every row of the unfiltered roster that happens to
    # carry the handle, and both numbers this window hands out - `enrolled` and the size
    # of `waiting` - are printed into a public digest issue and mailed to the teaching
    # team. Two rows for one person must not read as two people to chase.
    shared = ROSTER.replace(
        "fred@x.edu,Fred Frey,auditor,fred-frey,6,,",
        "fred@x.edu,Fred Frey,auditor,anna-adams,6,,",
    )
    cohort(roster_csv=shared)
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert window.enrolled == 4
    assert [s.hertie_email for s in window.waiting].count("fred@x.edu") == 0


def test_rows_for_another_assignment_do_not_team_anybody_up(cohort):
    cohort(teams_csv=_rows(("team-x", "anna-adams"), key="assignment-1"))
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert window.teams == 0 and len(window.waiting) == 4


# ------------------------------------------------------------------- a read that failed


def test_a_teams_csv_that_could_not_be_read_reports_no_windows_at_all(
    cohort, monkeypatch, capsys
):
    # None, not []: "we could not look" reported as "everybody has a team" is the failure
    # this whole pass exists to stop. The None travels on through the fault builder, so the
    # pre-flight can tell it from a cohort that really has nothing wrong with it.
    cohort()

    def boom(org):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(team_formation.teams, "_teams_text", boom)
    windows = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    assert windows is None
    assert "rate limit" in capsys.readouterr().err
    assert team_formation.window_faults(_sched(), windows) is None


def test_a_cohort_with_no_self_select_assignment_reads_neither_csv(cohort, monkeypatch):
    # `open_windows` runs on EVERY cohort on every tick, and most cohorts' `assignments:`
    # block declares nothing a student forms a team for. Asking the memoised spec question
    # first is free (the tick has read every template already); the two contents reads it
    # would otherwise pay - students.csv and teams.csv, ~192 a day - could only ever
    # produce an empty list.
    cohort(spec=GradingSpec(type="individual"))

    def refuse(org):
        raise AssertionError(
            "the cohort was read for a plan with no self-select window"
        )

    monkeypatch.setattr(team_formation.roster, "_roster_text", refuse)
    monkeypatch.setattr(team_formation.teams, "_teams_text", refuse)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) == []


def test_an_absent_roster_reports_no_windows_rather_than_a_teamed_cohort(cohort):
    # The roster is the allowlist. The empty set an absent file implies would report every
    # student as teamed; students.csv already carries its own fault on its own digest.
    cohort(roster_csv=None)
    assert team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE) is None


# ------------------------------------------------------------------------- the fault


def test_no_fault_once_every_enrolled_student_has_a_team(cohort):
    cohort(
        teams_csv=_rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
        )
    )
    sched = _sched()
    windows = team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    assert [w.teams for w in windows] == [2]
    assert team_formation.window_faults(sched, windows) == []


def test_with_no_team_at_all_the_fault_says_so_in_counts(cohort):
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.what == (
        "team formation is open and no team has formed yet - all 4 enrolled student(s) "
        "are still without one"
    )


def test_with_some_teams_formed_the_fault_counts_the_students_left_over(cohort):
    cohort(teams_csv=_rows(("team-x", "anna-adams"), ("team-x", "ben-baker")))
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.what == (
        "team formation is open and 2 of 4 enrolled student(s) are not in a team yet, "
        "across the 1 team(s) formed so far"
    )


def test_nothing_in_the_fault_names_a_student(cohort):
    # This runs in a PUBLIC workflow, and the fault's text reaches a run log, a digest
    # issue and an email. Every handle, name and address on the fixture roster, and the
    # team names students typed, are checked against every string the fault carries.
    cohort(teams_csv=_rows(("wizards", "anna-adams")))
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    text = (
        f"{fault.what} {fault.where} {fault.field} "
        f"{fault.label} {fault.fix()} {fault.line()}"
    ).casefold()
    for secret in (
        "anna",
        "adams",
        "ben-baker",
        "carla",
        "dan-doyle",
        "x.edu",
        "wizards",
    ):
        assert secret not in text, f"{secret!r} reached a public surface"


def test_the_fault_points_at_the_line_that_decides_when_the_window_shuts(cohort):
    # The deep link has to land on the line a reader would EDIT to give the cohort longer,
    # and that is whichever key closed the window.
    cohort()
    plain = _sched()
    (fault,) = team_formation.window_faults(
        plain, team_formation.open_windows(COURSE, COHORT, plain, INSIDE)
    )
    assert (fault.where, fault.field, fault.lineno) == (
        "assignments.assignment-2",
        "due_datetime",
        12,
    )
    assert fault.at == "schedule.yml:12"
    assert fault.in_repo == "classroom-config"

    pinned = _sched(
        **{"assignment-2": _entry(grading_datetime=SHUTS - timedelta(days=1))}
    )
    (fault,) = team_formation.window_faults(
        pinned, team_formation.open_windows(COURSE, COHORT, pinned, INSIDE)
    )
    assert (fault.field, fault.lineno) == ("grading_datetime", 14)


def test_the_fault_carries_its_own_consequence_and_fix(cohort):
    # schedule.yml's own sentence says the entry "is not scheduled" - which is exactly
    # false here: the entry is scheduled, it has fired, and what is missing is the teams.
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.consequence == (
        "no repos are provisioned for that assignment, so the students still without a "
        "team cannot hand anything in"
    )
    assert fault.fix() == (
        "write the missing rows into classroom-config/teams.csv yourself, or move the "
        "date on the line above to keep team formation open for longer."
    )


# ------------------------------------------------------------- the rungs across the close


def test_the_fault_climbs_the_rungs_towards_the_moment_formation_shuts(cohort):
    # An immediate fault that carries a MOMENT: `kind` is None, so the letter is the one
    # for a line somebody has to act on, while `fires` makes it get louder as the window
    # runs out. Both follow from `severity` branching on `fires`, never on `is_source`.
    cohort()
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.is_source is False and fault.fires == SHUTS
    rungs = [
        fault.severity(SHUTS - timedelta(days=5)),
        fault.severity(SHUTS - timedelta(hours=20)),
        fault.severity(SHUTS - timedelta(hours=8)),
        fault.severity(SHUTS - timedelta(hours=2)),
        fault.severity(SHUTS + timedelta(hours=1)),
    ]
    assert rungs == [
        Severity.ADVISORY,
        Severity.WARNING,
        Severity.URGENT,
        Severity.CRITICAL,
        Severity.MISSED,
    ]


def test_the_fault_survives_the_close_and_reaches_MISSED(cohort):
    # The rung above is unreachable unless the fault OUTLIVES the window: `fires` IS the
    # moment formation shuts, so a fault that only existed while the window was open could
    # never be read at a `now` past it. And `config_digest.transitions` reads a key that
    # stops appearing as CLEARED - so a fault that vanished at the close would post the
    # teaching team a *Cleared* comment at the exact minute five students stopped being
    # able to form a team at all.
    cohort(teams_csv=_rows(("team-x", "anna-adams"), ("team-x", "ben-baker")))
    sched = _sched()
    after = SHUTS + timedelta(days=3)
    (fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, after)
    )
    assert fault.severity(after) is Severity.MISSED
    assert fault.what == (
        "team formation has closed and 2 of 4 enrolled student(s) are still without a "
        "team, across the 1 team(s) that formed"
    )
    # Same key as while it was open, so the digest sees one fault that ESCALATED and not a
    # clear followed by a new appearance.
    (open_fault,) = team_formation.window_faults(
        sched, team_formation.open_windows(COURSE, COHORT, sched, INSIDE)
    )
    assert fault.key == open_fault.key


def test_the_shut_window_s_fault_clears_when_the_missing_rows_are_written(cohort):
    # What the fault is FOR: it goes away because somebody did the thing, never because the
    # calendar moved past it. (The other two ways out are moving the date and taking the
    # entry out of the plan, and both are edits to schedule.yml.)
    cohort(
        teams_csv=_rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
        )
    )
    sched = _sched()
    windows = team_formation.open_windows(
        COURSE, COHORT, sched, SHUTS + timedelta(days=3)
    )
    assert team_formation.window_faults(sched, windows) == []


def test_nothing_the_shut_window_s_fault_says_names_a_student(cohort):
    cohort(teams_csv=_rows(("wizards", "anna-adams")))
    sched = _sched()
    (fault,) = team_formation.window_faults(
        sched,
        team_formation.open_windows(COURSE, COHORT, sched, SHUTS + timedelta(days=3)),
    )
    text = f"{fault.what} {fault.where} {fault.field} {fault.fix()}".casefold()
    for secret in (
        "anna",
        "adams",
        "ben-baker",
        "carla",
        "dan-doyle",
        "x.edu",
        "wizards",
    ):
        assert secret not in text, f"{secret!r} reached a public surface"


# ----------------------------------------------------------------- the public team list


class Issues:
    """The welcome repo's issues, as `dsl_course.issues` sees them: what was searched for,
    what was opened, and what was pinned."""

    def __init__(self, existing: int | None = None, fail: bool = False):
        self.existing, self.fail = existing, fail
        self.searched: list[tuple[str, str, str]] = []
        self.made: list[tuple[str, str, str, tuple]] = []
        self.pinned: list[str] = []

    def find_issue(self, repo, title, label=""):
        self.searched.append((repo, title, label))
        if self.fail:
            raise RuntimeError("API rate limit exceeded")
        n = self.existing
        return team_formation.issues.Issue(n, "") if n else None

    def upsert_issue(self, repo, title, body, comment=None, existing=None, labels=()):
        self.made.append((repo, title, body, tuple(labels)))
        return team_formation.issues.Upserted(0, f"https://github.com/{repo}/issues/7")

    def gh(self, *args):
        self.pinned.append(args[2] if len(args) > 2 else "")
        return 0, ""


@pytest.fixture
def welcome_issues(monkeypatch):
    """The issue primitives, on the CONSUMER's imported names."""

    def _wire(**kw) -> Issues:
        fake = Issues(**kw)
        monkeypatch.setattr(team_formation.issues, "find_issue", fake.find_issue)
        monkeypatch.setattr(team_formation.issues, "upsert_issue", fake.upsert_issue)
        monkeypatch.setattr(team_formation, "gh", fake.gh)
        return fake

    return _wire


def _window(cohort, teams_csv: str = "") -> team_formation.Window:
    cohort(teams_csv=teams_csv)
    (window,) = team_formation.open_windows(COURSE, COHORT, _sched(), INSIDE)
    return window


def test_the_list_issue_opens_with_the_window_rather_than_on_the_first_join(
    cohort, welcome_issues
):
    # The workflow maintains it from the first join onwards, which is one join too late:
    # day one is the day the whole cohort is asked to form a team, and both the mail and
    # the form header want a URL to link.
    fake = welcome_issues()
    url = team_formation.ensure_list_issue(
        COHORT, _window(cohort), "Europe/Berlin", dry_run=False
    )
    assert url == f"https://github.com/{COHORT}/welcome/issues/7"
    (repo, title, body, labels) = fake.made[0]
    assert (repo, title) == (f"{COHORT}/welcome", "Teams for assignment-2")
    # The label is how the OTHER writer finds it again: the Join-team workflow looks it up
    # by that label and this exact title, and would open a second list without it.
    assert labels == ("team-list",)
    assert fake.searched == [(f"{COHORT}/welcome", title, "team-list")]
    assert "No teams yet" in body and "Up to 4 people per team" in body
    assert "closes on 4 Oct" in body
    assert fake.pinned, "the form and the mail both call it the pinned list"


def test_a_list_issue_that_already_exists_is_left_exactly_as_it_is(
    cohort, welcome_issues
):
    # Once it is there the Join-team workflow owns the body: it rewrites it from the rows
    # it just wrote, which is the only state that can be sure what teams.csv now says.
    fake = welcome_issues(existing=12)
    url = team_formation.ensure_list_issue(
        COHORT, _window(cohort), "Europe/Berlin", dry_run=False
    )
    assert url == f"https://github.com/{COHORT}/welcome/issues/12"
    assert fake.made == [] and fake.pinned == []


def test_a_listing_that_failed_opens_no_second_list(cohort, welcome_issues, capsys):
    # Absence has to be a real answer. A rate-limited tick that created one anyway would
    # leave a second list beside the one the cohort is already reading.
    fake = welcome_issues(fail=True)
    assert (
        team_formation.ensure_list_issue(
            COHORT, _window(cohort), "Europe/Berlin", dry_run=False
        )
        is None
    )
    assert fake.made == []
    assert "rate limit" in capsys.readouterr().err


def test_a_dry_run_opens_nothing_and_says_what_it_would_open(
    cohort, welcome_issues, capsys
):
    fake = welcome_issues()
    team_formation.ensure_list_issue(
        COHORT, _window(cohort), "Europe/Berlin", dry_run=True
    )
    assert (fake.searched, fake.made) == ([], [])
    assert "would ensure `Teams for assignment-2`" in capsys.readouterr().out


def test_the_list_body_is_names_and_counts_and_never_a_handle(cohort):
    # The welcome repo is PUBLIC and this issue is the one thing in it the whole cohort is
    # sent to. A team name is student-chosen and public by construction; who is in it is not.
    window = _window(
        cohort,
        _rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
            ("team-y", "Carla-Cohen"),
        ),
    )
    body = team_formation.list_body(window, "Europe/Berlin")
    # The same shape the Join-team workflow rewrites it into after every join.
    assert "- **team-x** - 2/4, room for 2" in body
    assert "- **team-y** - 2/4, room for 2" in body
    for handle in ("anna-adams", "ben-baker", "carla-cohen", "dan-doyle", "x.edu"):
        assert handle not in body


def test_a_full_team_is_called_full_rather_than_offered_no_room(cohort):
    window = _window(
        cohort,
        _rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-x", "carla-cohen"),
            ("team-x", "dan-doyle"),
        ),
    )
    assert "- **team-x** - 4/4, full" in team_formation.list_body(window, "UTC")


def test_the_url_is_looked_up_by_the_same_label_and_title_the_workflow_uses(
    welcome_issues,
):
    fake = welcome_issues(existing=5)
    assert (
        team_formation.list_issue_url(COHORT, "assignment-2")
        == f"https://github.com/{COHORT}/welcome/issues/5"
    )
    assert fake.searched == [
        (f"{COHORT}/welcome", "Teams for assignment-2", "team-list")
    ]


def test_a_url_nobody_could_look_up_is_no_url_rather_than_a_link_to_nowhere(
    welcome_issues,
):
    welcome_issues(fail=True)
    assert team_formation.list_issue_url(COHORT, "assignment-2") is None


# ------------------------------------------------------------------ telling the cohort
#
# The first mail this toolkit sends off a CLOCK rather than off somebody's action, so
# every test below is about a way a whole cohort's inbox could be got wrong: mailed twice,
# mailed at 2am, mailed after the door shut, mailed with somebody's address in a public
# log, or recorded as mailed and never sent.

ADDRESSES = ("anna@x.edu", "ben@x.edu", "carla@x.edu", "dan@x.edu")
# Enrolled and not onboarded: no handle, so by construction no teams.csv row, and the
# Join-team form would refuse her - she is exactly who the Join-course sentence is for.
EVE = "eve@x.edu"
# An auditor gets no assignment repo at all, so there is nothing for her to team up for.
AUDITOR = "fred@x.edu"

QUIET = datetime(2026, 9, 25, 2, 0, tzinfo=BERLIN)
NEAR_CLOSE = SHUTS - timedelta(hours=10)


class Record:
    """The cohort's `team-formation/mailed.csv`, as a file that remembers its sha.

    `refuse` is how many writes GitHub turns down, which is what another tick's commit
    looks like from here; `on_refuse` is that other tick's content landing. `refuse_from`
    is the write index after which every attempt is refused - a claim that lands and a
    release that then cannot. `race` is the other writer's commit landing BETWEEN the read
    and the write, which is the case `expected_sha` exists for; it fires once.

    `expected_sha` is honoured as GitHub honours it, because that is the whole protocol
    under test: a sha that no longer matches is refused, `""` means the file must not exist
    yet, and None means "look the sha up and overwrite whatever is there"."""

    def __init__(
        self,
        text: str | None = None,
        refuse: int = 0,
        on_refuse=None,
        refuse_from: int | None = None,
        race=None,
    ):
        self.text = text
        self.refuse = refuse
        self.on_refuse = on_refuse
        self.refuse_from = refuse_from
        self.race = race
        self.attempts: list[str] = []
        self.shas: list[str | None] = []
        self.version = 0

    @property
    def sha(self) -> str:
        return f"sha{self.version}" if self.text is not None else ""

    def get(self, org, repo, path, ref=""):
        assert path == team_formation.MAILED_PATH
        return None if self.text is None else (self.text, self.sha)

    def put(self, org, repo, path, content, message, expected_sha=None, person=False):
        body = content.decode()
        self.attempts.append(body)
        self.shas.append(expected_sha)
        if self.race is not None:
            self.race, race = None, self.race
            race(self)
        if self.refuse_from is not None and len(self.attempts) > self.refuse_from:
            return False
        if self.refuse > 0:
            self.refuse -= 1
            if self.on_refuse is not None:
                self.on_refuse(self)
            return False
        if expected_sha is not None and expected_sha != self.sha:
            return False
        self.text = body
        self.version += 1
        return True

    @property
    def rows(self) -> dict:
        return team_formation.parse_mailed(self.text) if self.text else {}

    def recipients(self, phase: str = "") -> set[str]:
        return {r for _a, r, p in self.rows if not phase or p == phase}


class Post:
    """The mail transport, recording every batch it is handed.

    `deliver` is how many of a batch actually go out - the transport stops at its own time
    budget and reports what it managed, which is the routine case the release path exists
    for. It is a PREFIX, as the budget's own stop is."""

    def __init__(self, transport: bool = True, deliver: int | None = None, boom=False):
        self.transport = transport
        self.deliver = deliver
        self.boom = boom
        self.batches: list[list] = []
        self.previews: list[list] = []
        self.preflights = 0

    def config(self):
        return object() if self.transport else None

    def preflight(self):
        """What a dry run is FOR: `mailer.send_bulk` proves the credential inside its own
        preview, and a rehearsal that never reaches here validates nothing."""
        self.preflights += 1

    def send_bulk(
        self, messages, dry_run: bool = False, sample: str | None = None, **kw
    ):
        # The dry run alone: a real send goes through `send_indexed`, because addresses
        # cannot tell two messages to one address apart.
        assert dry_run, (
            "the real send must ask which MESSAGES went out, not which addresses"
        )
        # The real preview, so what it prints (and that it preflights) is under test;
        # `preflight` above is the only part of it stubbed out.
        self.previews.append(list(messages))
        return _SEND_BULK(messages, dry_run=True, sample=sample, **kw)

    def send_indexed(self, messages, html: bool = False):
        self.batches.append(list(messages))
        if self.boom:
            raise RuntimeError("Graph refused the credential")
        return list(range(len(messages) if self.deliver is None else self.deliver))

    @property
    def sent(self) -> list:
        return [m for batch in self.batches for m in batch]

    @property
    def to(self) -> set[str]:
        return {m.to for m in self.sent}


@pytest.fixture
def post(monkeypatch):
    """The record file and the transport, on the CONSUMER's imported names."""

    def _wire(record: Record | None = None, **kw) -> tuple[Record, Post]:
        rec = record if record is not None else Record()
        sender = Post(**kw)
        monkeypatch.setattr(team_formation, "get_file_with_sha", rec.get)
        monkeypatch.setattr(team_formation, "put_file", rec.put)
        monkeypatch.setattr(
            team_formation.mailer, "graph_config_from_env", sender.config
        )
        monkeypatch.setattr(team_formation.mailer, "send_bulk", sender.send_bulk)
        monkeypatch.setattr(team_formation.mailer, "send_indexed", sender.send_indexed)
        monkeypatch.setattr(team_formation.mailer, "preflight", sender.preflight)
        monkeypatch.setattr(
            team_formation, "course_name_of", lambda org: "Deep Learning"
        )
        # The public team list the body links. A real one is opened by the tick that opens
        # the window; here it is one issue search stubbed at the module boundary, so the
        # mail tests below neither reach GitHub nor lose the sentence.
        monkeypatch.setattr(
            team_formation,
            "list_issue_url",
            lambda org, key: f"https://github.com/{org}/welcome/issues/12",
        )
        return rec, sender

    return _wire


def _tick(now=INSIDE, sched=None, dry_run=False) -> int:
    """One scheduler tick's worth of this pass: read who is waiting, then mail them."""
    sched = sched or _sched()
    windows = team_formation.open_windows(COURSE, COHORT, sched, now)
    return team_formation.notify_windows(
        COURSE, COHORT, sched, windows, now, dry_run=dry_run
    )


def _already(*claims: tuple[str, str, str]) -> Record:
    """A record that already carries these `(assignment, recipient, phase)` rows."""
    return Record(team_formation.dump_mailed(dict.fromkeys(claims, "2026-09-25T07:00")))


# ------------------------------------------------------------------- who gets the mail


def test_the_mail_goes_to_every_enrolled_student_without_a_team_and_to_nobody_else(
    cohort, post
):
    cohort()
    _rec, sender = post()
    assert _tick() == 0
    assert sender.to == set(ADDRESSES), "the recipients are the window's, not the org's"
    assert AUDITOR not in sender.to
    assert len(sender.batches) == 1, "one batch a tick, whatever the windows"
    assert len(sender.sent) == len(sender.to), "one message per student"


def test_a_student_who_has_not_joined_github_is_not_asked_to_form_a_team(cohort, post):
    # The only thing they could do about this mail is the Join course issue their
    # enrolment code already asked them for, so a second message naming a step they cannot
    # reach is noise. It also keeps this set and the fault's counts describing ONE
    # population - otherwise the mail says five where the digest beside it says four.
    cohort()
    _rec, sender = post()
    assert _tick() == 0
    assert EVE not in sender.to


def test_a_student_who_joins_mid_window_is_asked_on_the_very_next_tick(cohort, post):
    # Which is why leaving them out costs nothing: the claim is per recipient, so nobody
    # is skipped for good. They are asked at the moment they can act, not before it.
    cohort()
    rec, sender = post()
    assert _tick() == 0
    assert EVE not in sender.to

    onboarded = ROSTER.replace(
        "eve@x.edu,Eve Evans,enrolled,,,,", "eve@x.edu,Eve Evans,enrolled,eve-evans,5,,"
    )
    cohort(roster_csv=onboarded)
    # The SAME record: this is the next tick, carrying what the last one claimed.
    rec2, sender2 = post(record=rec)
    assert _tick() == 0
    assert sender2.to == {EVE}, (
        "only the newcomer, and not a second copy for anybody else"
    )
    assert rec2.recipients(team_formation.PHASE_OPEN) == {*ADDRESSES, EVE}


def test_a_student_already_in_a_team_is_not_asked_to_form_one(cohort, post):
    cohort(teams_csv=_rows(("team-x", "anna-adams")))
    _rec, sender = post()
    _tick()
    assert "anna@x.edu" not in sender.to
    assert sender.to == {"ben@x.edu", "carla@x.edu", "dan@x.edu"}


def test_a_roster_row_somebody_duplicated_gets_one_message(cohort, post):
    # Two rows, one address (a re-enrolment, a second GitHub account). Keyed on the
    # handle, this cohort would get two copies of the same mail - and `enrol_codes`
    # collapses a duplicated row exactly this way.
    cohort(roster_csv=f"{ROSTER}\nanna@x.edu,Anna Adams,enrolled,anna-second,7,,")
    _rec, sender = post()
    _tick()
    assert len([m for m in sender.sent if m.to == "anna@x.edu"]) == 1


def test_a_window_everybody_has_teamed_up_for_does_not_even_read_the_record(
    cohort, post, monkeypatch
):
    # A window stands open for WEEKS, and once the cohort has teamed up there is nothing
    # left to owe off it - so reading the record on every tick until the grading pin buys
    # a contents read per cohort per quarter of an hour and nothing else. Exactly
    # equivalent: `_nudges` iterates `waiting`, so an all-empty `waiting` owes nothing.
    cohort(
        teams_csv=_rows(
            ("team-x", "anna-adams"),
            ("team-x", "ben-baker"),
            ("team-y", "carla-cohen"),
            ("team-y", "dan-doyle"),
        )
    )
    _rec, sender = post()

    def refuse(*a, **k):
        raise AssertionError("the record was read for a window nobody is waiting on")

    monkeypatch.setattr(team_formation, "get_file_with_sha", refuse)
    assert _tick() == 0
    assert sender.batches == []


def test_a_window_that_has_shut_is_never_mailed_about(cohort, post, monkeypatch):
    # It is carried only for the teaching team's fault. The Join-team form would refuse the
    # team this message asks for, so the mail would be asking a cohort for something it
    # cannot do - and the record is not even read, since nothing could ever be owed off it.
    cohort()

    def refuse(*a, **k):
        raise AssertionError("the record was read for a window that has already shut")

    monkeypatch.setattr(team_formation, "get_file_with_sha", refuse)
    _rec, sender = post()
    assert _tick(now=SHUTS + timedelta(days=1)) == 0
    assert sender.batches == [] and sender.previews == []


def test_a_press_will_not_mail_a_window_that_has_shut_either(
    cohort, post, plan, capsys
):
    # The button is a second way into the same pass, so the door is shut for it too - and
    # the refusal reads as "nothing is open", which is exactly what is true.
    cohort()
    rec, sender = post()
    plan()
    assert _press(now=SHUTS + timedelta(days=1)) == 0
    assert rec.attempts == [] and sender.batches == []
    assert "no team-formation window is open" in capsys.readouterr().out


def test_a_re_run_mails_nobody(cohort, post):
    cohort()
    rec, sender = post()
    assert _tick() == 0
    assert _tick() == 0
    assert len(sender.batches) == 1, "the record is what makes a tick idempotent"
    assert len(rec.attempts) == 1


def test_a_student_who_joined_a_team_since_the_last_tick_is_not_mailed_again(
    cohort, post
):
    cohort()
    _rec, sender = post()
    _tick()
    # She acted on it. The record already holds her `open` row, and she has dropped out of
    # `waiting` - either alone would be enough, and the reminder below relies on both.
    cohort(teams_csv=_rows(("team-x", "anna-adams")))
    assert _tick() == 0
    assert len(sender.batches) == 1


# ------------------------------------------------------------------------ the two phases


def test_only_the_open_phase_goes_out_while_the_door_is_still_far_off(cohort, post):
    cohort()
    rec, _sender = post()
    _tick()
    assert rec.recipients(team_formation.PHASE_REMINDER) == set()
    assert rec.recipients(team_formation.PHASE_OPEN) == set(ADDRESSES)


def test_the_reminder_goes_out_inside_the_last_48_hours_to_whoever_is_still_unteamed(
    cohort, post
):
    cohort(teams_csv=_rows(("team-x", "anna-adams")))
    rec, sender = post(
        record=_already(*(("assignment-2", a, "open") for a in ADDRESSES))
    )
    assert _tick(now=NEAR_CLOSE) == 0
    assert sender.to == {"ben@x.edu", "carla@x.edu", "dan@x.edu"}
    assert rec.recipients(team_formation.PHASE_REMINDER) == sender.to
    (subject, _body) = (sender.sent[0].subject, sender.sent[0].body)
    assert subject.startswith("Team formation for assignment-2 closes on 4 Oct")


def test_a_window_shorter_than_the_reminder_lead_sends_one_message_for_both_phases(
    cohort, post
):
    # Two mails a minute apart is what a reminder must never become. The message is the
    # OPEN one: a "reminder" to somebody who was never told refers to a mail that does not
    # exist.
    cohort()
    rec, sender = post()
    short = _sched(
        **{
            "assignment-2": AssignmentEntry(
                course_source_repo="a2-f2026",
                due_datetime=INSIDE + timedelta(hours=6),
                handout_datetime=INSIDE - timedelta(hours=1),
                lines={"due_datetime": 12},
            )
        }
    )
    assert _tick(sched=short) == 0
    assert len(sender.sent) == 4, "one message each, not one per phase"
    assert sender.sent[0].subject.startswith("Form your team for assignment-2")
    assert rec.recipients("open") == rec.recipients("reminder") == set(ADDRESSES)


def test_two_open_windows_are_one_batch_of_one_message_each(cohort, post):
    cohort()
    _rec, sender = post()
    assert _tick(sched=_sched(**{"a-1": _entry(), "a-2": _entry()})) == 0
    assert len(sender.batches) == 1, (
        "one send_bulk a tick - one token, one rate limiter"
    )
    assert len(sender.sent) == 8
    assert len({(m.to, m.subject) for m in sender.sent}) == 8


# ----------------------------------------------------------------- nothing claimed yet


def test_an_org_with_no_mail_transport_claims_nothing_and_is_offered_it_again(
    cohort, post, capsys
):
    # The property that makes this feature inert rather than destructive on a cohort whose
    # GRAPH_* secrets were never set: asked BEFORE the claim, as enrol_codes asks it.
    cohort()
    rec, sender = post(transport=False)
    assert _tick() == 0
    assert rec.attempts == [] and sender.batches == []
    assert "[skip]" in capsys.readouterr().out
    # The next tick, after somebody wires the transport up, sends the same messages.
    rec2, sender2 = post(record=rec)
    assert _tick() == 0
    assert sender2.to == set(ADDRESSES) and rec2.attempts


def test_quiet_hours_hold_the_mail_and_claim_nothing(cohort, post, capsys):
    # A handout_datetime of 00:00 would otherwise mail a whole cohort at 2am. The WINDOW
    # is untouched - it opened at its own minute, upstream of this call.
    cohort()
    rec, sender = post()
    assert _tick(now=QUIET) == 0
    assert rec.attempts == [] and sender.batches == []
    assert "held until 07:00" in capsys.readouterr().out
    assert _tick(now=QUIET.replace(hour=9)) == 0
    assert sender.to == set(ADDRESSES)


def test_an_archived_cohort_mails_nobody(cohort, post, monkeypatch):
    # Its classroom-config is frozen, so the claim could not land anyway - and nobody is
    # forming a team in a term that is over.
    cohort()
    rec, sender = post()
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, repo: True)
    assert _tick() == 0
    assert rec.attempts == [] and sender.batches == []


def test_a_record_that_cannot_be_written_mails_nobody(cohort, post):
    # Claim-then-send means a write GitHub refuses costs a tick, not a duplicate cohort
    # mail. The next tick retries the lot.
    cohort()
    rec, sender = post(record=Record(refuse=team_formation.WRITE_ATTEMPTS))
    assert _tick() == 1
    assert sender.batches == []
    assert len(rec.attempts) == team_formation.WRITE_ATTEMPTS


def test_a_record_nobody_can_parse_mails_nobody(cohort, post):
    # An Excel `;` export of the record read as EMPTY would be a second copy of every
    # message to every student in the cohort.
    cohort()
    rec, sender = post(record=Record("assignment;recipient;phase;mailed_at"))
    assert _tick() == 1
    assert sender.batches == [] and rec.attempts == []


def test_the_first_claim_of_a_term_does_not_overwrite_a_racing_writer(cohort, post):
    # `mailed.csv` does not exist until the first window of a cohort's term claims it, and
    # that is exactly when a faculty press and a tick can both be holding the whole cohort.
    # An absent file read as "no sha" must be sent to GitHub as NO SHA - which it refuses if
    # the file has appeared since - and never as "fetch the sha yourself and overwrite",
    # which is what `put_file` does with None and which would discard the other writer's
    # rows and mail every unteamed student twice. `grades.sync_team_lock` passes "" for the
    # same absence and for the same reason.
    cohort()
    mine = "2026-09-25T07:00"

    def landed(rec: Record) -> None:
        # The other writer got there first: the file now exists, holding its own claims.
        rec.text = team_formation.dump_mailed(
            dict.fromkeys((("assignment-2", a, "open") for a in ADDRESSES), mine)
        )
        rec.version += 1

    rec, sender = post(record=Record(text=None, race=landed))
    assert _tick() == 0
    assert sender.batches == [], "every message was already another writer's to send"
    assert rec.recipients("open") == set(ADDRESSES), "and their rows are still there"
    assert set(rec.rows.values()) == {mine}, "nobody's claim was overwritten"
    assert rec.shas[0] == "", (
        "an absent record claims against no sha, never against None"
    )


def test_a_message_another_tick_claimed_first_is_not_sent_twice(cohort, post):
    # The claim reports what it INSERTED, not what it asked for: the other tick has
    # already taken responsibility for that student's message.
    cohort()

    def landed(rec: Record) -> None:
        rec.text = team_formation.dump_mailed(
            {("assignment-2", "anna@x.edu", "open"): "x"}
        )

    _rec, sender = post(record=Record(refuse=1, on_refuse=landed))
    assert _tick() == 0
    assert "anna@x.edu" not in sender.to
    assert sender.to == {"ben@x.edu", "carla@x.edu", "dan@x.edu"}


# --------------------------------------------------------------- what the send gave back


def test_a_partial_batch_releases_exactly_the_claims_it_did_not_spend(cohort, post):
    cohort()
    rec, sender = post(deliver=2)
    assert _tick() == 1, "students this tick could not reach must red it"
    went = {m.to for m in sender.sent[:2]}
    assert rec.recipients() == went, "only the spent claims are left standing"
    # And the next tick retries those and only those.
    _rec2, sender2 = post(record=rec)
    assert _tick() == 0
    assert sender2.to == set(ADDRESSES) - went


def test_a_batch_that_stops_inside_the_second_window_releases_that_window_s_claims(
    cohort, post
):
    # The one case a per-ADDRESS record cannot survive. Anna is unteamed in both windows, so
    # she owns two messages to one address, and the batch is ordered window by window. If it
    # stops after a-1, everybody mailed for a-1 has "gone out" against their address - so
    # read that way, their a-2 nudge counts as sent, its claim is never released, and
    # `mailed.csv` says for ever that they were told about an assignment they never heard
    # of. The record is per MESSAGE, so what stands is exactly what went out.
    cohort()
    two = _sched(**{"a-1": _entry(), "a-2": _entry()})
    rec, _sender = post(deliver=4)
    assert _tick(sched=two) == 1, "the messages this tick could not send must red it"
    assert {c[0] for c in rec.rows} == {"a-1"}, "a-2's claims are all given back"
    assert rec.recipients() == set(ADDRESSES)
    # And the next tick sends a-2 to everybody, rather than silently never sending it.
    _rec2, sender2 = post(record=rec)
    assert _tick(sched=two) == 0
    assert len(sender2.sent) == 4
    assert {m.to for m in sender2.sent} == set(ADDRESSES)
    assert all("a-2" in m.subject for m in sender2.sent)


def test_the_public_count_is_per_window_and_not_per_address(cohort, post, capsys):
    # The same miscount, on the line faculty read: four messages went out, all of them
    # a-1's, and a-2 is owed to everybody. Counted by address, a-2 would report four of
    # four mailed - the log agreeing with a record that is wrong.
    cohort()
    post(deliver=4)
    _tick(sched=_sched(**{"a-1": _entry(), "a-2": _entry()}))
    out = capsys.readouterr().out
    assert "mailed 4 of 4 student(s) about a-1" in out
    assert "mailed 0 of 4 student(s) about a-2" in out


def test_a_transport_that_raised_gives_every_claim_back(cohort, post):
    # Nothing went out at all, and an unreleased claim is a whole cohort silently recorded
    # as told.
    cohort()
    rec, _sender = post(boom=True)
    with pytest.raises(RuntimeError):
        _tick()
    assert rec.rows == {}


def test_a_release_that_also_failed_says_which_stamp_to_delete_by_hand(
    cohort, post, capsys
):
    # The one failure that must never be swallowed: the record says these students were
    # told and they were not, so nothing will ever retry them.
    cohort()
    rec, sender = post(record=Record(refuse_from=1), deliver=0)
    assert _tick() == 1
    assert len(sender.sent) == 4 and rec.rows, "the claim landed; the send did not"
    err = capsys.readouterr().err
    assert "mailed_at=" in err and "delete those rows by hand" in err


# -------------------------------------------------------------------------- the wording


def _body(cohort, post, **kw) -> tuple[str, str]:
    cohort()
    _rec, sender = post()
    _tick(**kw)
    return sender.sent[0].subject, sender.sent[0].body


def test_the_message_carries_what_a_student_needs_in_order_to_act(cohort, post):
    subject, body = _body(cohort, post)
    assert subject == "Form your team for assignment-2 - Deep Learning"
    assert "the Deep Learning course" in body
    assert "up to 4 people" in body, "the cap the Join-team form enforces"
    assert "closes on 4 Oct" in body, "the day, in the cohort's own zone"
    assert f"https://github.com/{COHORT}/welcome/issues/new/choose" in body
    assert f"https://github.com/{COHORT}/welcome/issues/12" in body, "the team list"
    # No Join-course line: every recipient is onboarded by construction, and a student
    # who is not enters `waiting` on the tick after they join and is sent this then.
    assert "Join course" not in body
    assert len(body.splitlines()) < 20, "a student reads this once, on a phone"


def test_the_message_greets_the_student_it_is_addressed_to(cohort, post):
    # One Message per recipient already, so the body costs nothing to personalise - and a
    # mail asking somebody to go and find three people to work with reads better addressed
    # to them than to a cohort.
    cohort()
    _rec, sender = post()
    _tick()
    greeted = {m.to: m.body.splitlines()[0] for m in sender.sent}
    assert greeted["anna@x.edu"] == "Dear Anna,"
    assert greeted["dan@x.edu"] == "Dear Dan,"


def test_a_roster_row_with_no_name_is_greeted_without_one(cohort, post):
    # A cohort imported from a system that only had addresses. A greeting to nobody reads
    # worse than none at all.
    cohort(roster_csv=f"{ROSTER_HEADER}\nzoe@x.edu,,enrolled,zoe-z,9,,")
    _rec, sender = post()
    _tick()
    assert sender.sent[0].body.startswith("Hello,\n")


def test_the_assignment_is_named_as_the_site_names_it(cohort, post):
    # `assignment-2` is what the PLAN calls it. The gradebook, the brief and the course
    # site all print the title, and this is the one mail a student gets about it.
    cohort(
        spec=GradingSpec(type="group", team_formation="self_select", title="Project")
    )
    _rec, sender = post()
    _tick()
    assert sender.sent[0].subject == "Form your team for Project - Deep Learning"
    assert "Project in the Deep Learning course" in sender.sent[0].body


def test_the_message_says_nothing_about_working_alone(cohort, post):
    # Whether a one-person team is allowed is the instructor's call, it is written down
    # nowhere the toolkit can read, and a mail that guessed would overrule them in the
    # students' inbox.
    _subject, body = _body(cohort, post)
    for word in ("alone", "solo", "yourself", "on your own", "at least", "minimum"):
        assert word not in body.lower()


def test_the_closing_day_is_told_in_the_cohorts_zone(cohort, post):
    # A window shutting at 00:30 Berlin is the 3rd in UTC and the 4th to everybody reading
    # the mail.
    just_after_midnight = datetime(2026, 10, 5, 0, 30, tzinfo=BERLIN)
    assert team_formation.spoken_date(just_after_midnight, "Europe/Berlin") == "5 Oct"
    assert team_formation.spoken_date(just_after_midnight, "UTC") == "4 Oct"


def test_the_sample_is_placeholders_and_the_same_template_as_the_send(cohort, post):
    subject, body = team_formation.sample_message(COHORT, "Deep Learning")
    assert "<assignment>" in subject and "<n>" in body and "<date>" in body
    # The NAME is a placeholder too: the preview is printed in a public run log.
    assert body.startswith("Dear <first name>,")
    assert f"https://github.com/{COHORT}/welcome/issues/new/choose" in body


def test_the_dry_run_prints_the_sample_claims_nothing_and_sends_nothing(
    cohort, post, capsys
):
    cohort()
    rec, sender = post()
    assert _tick(dry_run=True) == 0
    out = capsys.readouterr().out
    assert rec.attempts == [] and sender.batches == []
    assert "<assignment>" in out, "the sample, never a real body"
    assert "would mail 4 of 4 enrolled student(s)" in out
    for address in ADDRESSES:
        assert address not in out


def test_the_dry_run_previews_the_real_batch_and_proves_the_credential(cohort, post):
    # `mailer.send_bulk`'s OWN preview, not a hand-rolled one: it runs `mailer.preflight`,
    # which is the only thing that makes a rehearsal say anything about the GRAPH_* secrets
    # - a preview that returns before the transport is chosen reads the same whether the
    # certificate is right, wrong or absent. This is the toolkit's first clock-driven
    # cohort-wide mail, so its rehearsal has to test something.
    cohort()
    _rec, sender = post()
    assert _tick(dry_run=True) == 0
    assert sender.preflights == 1
    (previewed,) = sender.previews
    assert {m.to for m in previewed} == set(ADDRESSES)


# ------------------------------------------------------------------------ the public log


def test_nothing_the_public_log_says_names_a_student(cohort, post, capsys, monkeypatch):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    cohort()
    _rec, _sender = post(deliver=3)
    _tick()
    printed = capsys.readouterr()
    public = printed.out + printed.err
    # Not vacuous: the counts and the assignment ARE there, and they are all that is.
    assert "mailed 3 of 4 student(s) about assignment-2" in public
    for named in (*ADDRESSES, "Anna Adams", "anna-adams", "a***@x.edu"):
        assert named not in public, f"{named} reached a world-readable log"


def test_the_per_recipient_lines_are_masked_and_verbose_only(cohort, post, capsys):
    # The mutation half of the test above: the lines DO exist, on the channel a faculty
    # member opts into locally - so "no address in the public log" is a statement about
    # where they go, not about a run that logged nothing.
    cohort()
    _rec, _sender = post(deliver=3)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DSL_VERBOSE", "1")
        _tick()
    verbose = capsys.readouterr().out
    assert "a***@x.edu" in verbose and "d***@x.edu" in verbose
    assert "NOT mailed" in verbose, "which of them to chase is per-person detail too"
    for address in ADDRESSES:
        assert address not in verbose


# ------------------------------------------------------- the faculty button, pressed

# The button is a second WAY IN to the pass above, not a second implementation of it, so
# what is asserted here is only what a press adds: which windows it acts on, what a
# mistyped key does, and that the tick's safety properties still hold when a person is the
# one who fired it. Everything else - the record, the claim ordering, the wording, quiet
# hours - is the same code and is pinned above.


@pytest.fixture
def plan(monkeypatch):
    """The cohort's schedule, as `run` reads it for itself (the tick is handed one)."""

    def _wire(sched=None):
        chosen = sched or _sched()
        monkeypatch.setattr(team_formation.schedule, "load", lambda org: chosen)
        return chosen

    return _wire


def _press(only: str = "", now=INSIDE, dry_run: bool = False) -> int:
    return team_formation.run(COURSE, COHORT, now, only=only, dry_run=dry_run)


def test_a_press_mails_every_student_still_waiting_for_a_team(cohort, post, plan):
    cohort(teams_csv=_rows(("team-x", "anna-adams")))
    rec, sender = post()
    plan()
    assert _press() == 0
    assert sender.to == {"ben@x.edu", "carla@x.edu", "dan@x.edu"}
    assert rec.recipients(team_formation.PHASE_OPEN) == sender.to


def test_a_second_press_mails_nobody(cohort, post, plan):
    # The whole of the idempotence, and none of it is in the button: `mailed.csv` already
    # holds every phase the second press would owe, exactly as it does for a re-tick.
    cohort()
    rec, sender = post()
    plan()
    assert _press() == 0
    _rec2, sender2 = post(record=rec)
    assert _press() == 0
    assert sender2.batches == [], "the record is what makes a press idempotent"
    assert len(rec.attempts) == 1, "the second press writes nothing either"
    assert len(sender.batches) == 1


def test_a_press_the_tick_already_covered_mails_nobody_either(cohort, post, plan):
    # And the other way round: one record, so the button cannot re-send what the clock
    # already sent. This is why the two must not be two implementations.
    cohort()
    rec, _sender = post()
    plan()
    assert _tick() == 0
    _rec2, sender2 = post(record=rec)
    assert _press() == 0
    assert sender2.batches == []


def test_an_assignment_key_narrows_the_press_and_leaves_the_others_alone(
    cohort, post, plan
):
    cohort()
    rec, sender = post()
    plan(_sched(**{"a-1": _entry(), "a-2": _entry()}))
    assert _press(only="a-1") == 0
    assert {c[0] for c in rec.rows} == {"a-1"}, (
        "a-2 is not claimed, so a tick still owes it"
    )
    assert len(sender.sent) == 4


def test_a_key_with_no_open_window_is_an_error_and_mails_nobody(cohort, post, plan):
    # A mistyped box that exited 0 is a faculty member who believes a cohort was mailed
    # and was not. The refusal names what IS open, which is the list they wanted anyway.
    cohort()
    rec, sender = post()
    plan(_sched(**{"a-1": _entry(), "a-2": _entry()}))
    assert _press(only="a-3") == 1
    assert rec.attempts == [] and sender.batches == []


def test_a_key_the_schedule_does_not_have_at_all_says_so(cohort, post, plan, capsys):
    # Two mistakes with two fixes: a typo in the box, or a window that is not open yet.
    cohort()
    post()
    plan()
    assert _press(only="assignment-9") == 1
    said = capsys.readouterr()
    err = said.out + said.err
    assert "assignment-9" in err and "assignment-2" in err


def test_a_press_with_nothing_open_is_green_and_says_so(cohort, post, plan, capsys):
    cohort()
    rec, sender = post()
    plan()
    # Before the handout: the window has not opened yet.
    assert _press(now=OPENS - timedelta(days=1)) == 0
    assert rec.attempts == [] and sender.batches == []
    assert "no team-formation window is open" in capsys.readouterr().out


def test_a_cohort_the_press_could_not_read_is_red(cohort, post, plan, monkeypatch):
    # The tick treats the same "could not look" as nothing to report; a press must not,
    # because somebody is standing at the run and a press that reached nobody would look
    # exactly like a press that had nobody to reach.
    cohort()
    rec, sender = post()
    plan()
    monkeypatch.setattr(team_formation.roster, "load", lambda org: None)
    assert _press() == 1
    assert rec.attempts == [] and sender.batches == []


def test_the_press_previews_by_default(cohort, post, plan, capsys):
    # `run`'s own default, not the workflow's: a maintainer running the CLI by hand with
    # no flag at all must not mail a cohort.
    cohort()
    rec, sender = post()
    plan()
    assert team_formation.run(COURSE, COHORT, INSIDE) == 0
    assert rec.attempts == [] and sender.batches == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_the_overnight_hold_applies_to_a_press_too(cohort, post, plan, capsys):
    # The 23:00-07:00 rule is about the STUDENTS' night, and a cohort woken at 02:00 is
    # woken just as hard by a person as by a datetime. Nothing is lost to it: the press
    # claims nothing, so the next tick after 07:00 sends exactly what it asked for.
    cohort()
    rec, sender = post()
    plan()
    assert _press(now=QUIET) == 0
    assert rec.attempts == [] and sender.batches == []
    assert "held until 07:00" in capsys.readouterr().out
    _rec2, sender2 = post(record=rec)
    assert _tick(now=QUIET.replace(hour=9)) == 0
    assert sender2.to == set(ADDRESSES)


def _argv(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["team_formation", "--course-org", COURSE, "--cohort-org", COHORT, *args],
    )


@pytest.fixture
def pressed(monkeypatch):
    """What `main` asked `run` for. The clock is `main`'s own (`datetime.now`), so the
    press itself is exercised against a fixed `now` above; what is asserted here is the
    part that is only `main`'s - the flags it parses and the cohort it refuses."""
    calls: list[dict] = []

    def _run(course_org, cohort_org, now, *, only="", dry_run=True):
        calls.append(
            {"course": course_org, "cohort": cohort_org, "only": only, "dry": dry_run}
        )
        return 0

    monkeypatch.setattr(team_formation, "run", _run)
    return calls


def test_the_cli_refuses_an_archived_cohort(pressed, monkeypatch):
    # Its classroom-config is frozen, so the claim could not land - and nobody is forming
    # a team in a term that is over. Green, as every other sweep treats one, and refused
    # BEFORE the cohort is read.
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, repo: True)
    _argv(monkeypatch, "--no-dry-run")
    assert team_formation.main() == 0
    assert pressed == []


def test_the_cli_previews_unless_it_is_told_not_to(pressed, monkeypatch):
    # A bare invocation - the one a maintainer types by hand - must not mail a cohort.
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, repo: False)
    _argv(monkeypatch)
    assert team_formation.main() == 0
    assert pressed == [{"course": COURSE, "cohort": COHORT, "only": "", "dry": True}]


def test_the_cli_sends_for_real_and_narrows_only_when_asked(pressed, monkeypatch):
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, repo: False)
    _argv(monkeypatch, "--assignment", "assignment-2", "--no-dry-run")
    assert team_formation.main() == 0
    assert pressed == [
        {"course": COURSE, "cohort": COHORT, "only": "assignment-2", "dry": False}
    ]


def test_an_archived_cohort_mails_nobody_even_if_the_press_is_reached(
    cohort, post, plan, monkeypatch
):
    # The backstop, on the pass that WRITES: `notify_windows` asks the same question again
    # as its last guard, so a caller that skipped the CLI's check still claims nothing.
    cohort()
    rec, sender = post()
    plan()
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, repo: True)
    assert _press() == 0
    assert rec.attempts == [] and sender.batches == []
