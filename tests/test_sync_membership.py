"""sync_membership orchestrates course-admin (always) plus per-semester roster/teams/
instructors. The gh wiring is left live per the testing strategy; these pin the
orchestration decisions: an empty registry is visible (not a silent green), one
semester's failure is isolated from the rest of the batch, and a file faculty have to fix
skips its semester without reddening the cron.
"""

from __future__ import annotations

import pytest
import yaml

from dsl_course import faults, roster, sync_membership
from dsl_course.grades import LockWrite


def _stub_course_admins(monkeypatch, rv: int = 0):
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_course_admins", lambda *a, **k: rv
    )


@pytest.fixture(autouse=True)
def _team_lock_is_current(monkeypatch):
    """The Join-team form's mirror, written at the end of every semester's sync. It reads
    the semester's schedule and each template's definition, so it is stubbed here for the
    tests that are about the sync's own orchestration; the ones that are about the lock
    file itself set their own."""
    monkeypatch.setattr(
        sync_membership,
        "sync_team_lock",
        lambda course, semester, dry_run=False: LockWrite(True, False),
    )


@pytest.fixture(autouse=True)
def gradebooks(monkeypatch):
    """A private gradebook per onboarded student, provisioned at the end of every semester's
    sync. It reads the roster and takes the semester listing this pass already holds, so it
    is stubbed here for the tests about the sync's own orchestration; the one about it sets
    its own. Records `(org, the listing it was handed)`."""
    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        sync_membership,
        "ensure_gradebooks",
        lambda org, dry_run=False, existing=None, budget_minutes=None: (
            calls.append((org, existing, budget_minutes)) or 0
        ),
    )
    return calls


@pytest.fixture(autouse=True)
def listed(monkeypatch):
    """The ONE semester listing a per-semester pass takes, which the off-boarding prune and the
    gradebooks then share. Live here, so a test that forgets it reaches GitHub."""
    monkeypatch.setattr(
        sync_membership,
        "listing_by_name",
        lambda org: {f"{org}-welcome": {"name": f"{org}-welcome"}},
    )


def test_every_live_semesters_sync_provisions_its_gradebooks(monkeypatch, gradebooks):
    # The gradebook is where feedback goes for every shape that has no Submission receipts issue, and
    # the assignment brief points at it from the day it is published - so it exists from
    # the moment a student onboards, not from the first distribute.
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(
        sync_membership, "discover_semesters", lambda org: ["Semester-A"]
    )
    monkeypatch.setattr(sync_membership, "semester_is_live", lambda org: True)
    for name in ("sync_roster", "sync_teams"):
        monkeypatch.setattr(getattr(sync_membership, name), "sync", lambda *a, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])

    assert sync_membership.sync("Course", all_semesters=True) == 0
    # And off the listing this pass already took, keyed by name: the off-boarding prune
    # asks the same rows, and each used to take a paginated listing of its own. This is
    # also the ONE caller that bounds the wall clock, because nothing in this run waits on
    # the repos it makes.
    assert gradebooks == [
        (
            "Semester-A",
            {"Semester-A-welcome": {"name": "Semester-A-welcome"}},
            sync_membership.GRADEBOOK_BUDGET_MINUTES,
        )
    ]


def test_a_listing_that_could_not_be_read_still_reconciles_the_roster(
    monkeypatch, gradebooks
):
    # The listing is taken for the off-boarding prune and the gradebooks, and it used to
    # RAISE - before the roster reconcile, so a rate limit on it stopped a semester's
    # enrolment for the night. None is "we could not look", and each step answers it.
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership, "listing_by_name", lambda org: None)
    monkeypatch.setattr(
        sync_membership, "discover_semesters", lambda org: ["Semester-A"]
    )
    monkeypatch.setattr(sync_membership, "semester_is_live", lambda org: True)
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    rostered: list[object] = []
    monkeypatch.setattr(
        sync_membership.sync_roster,
        "sync",
        lambda org, **k: rostered.append(k.get("existing")) or 0,
    )
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda *a, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert rostered == [None]
    assert gradebooks == [
        ("Semester-A", None, sync_membership.GRADEBOOK_BUDGET_MINUTES)
    ]


def test_empty_registry_is_visible_but_not_fatal(monkeypatch, capsys):
    # An empty registry can be legitimate for a brand-new course org, so the run does not
    # fail - but it must be loudly visible, not a silent green "Sync complete".
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: [])
    _stub_course_admins(monkeypatch)
    errors = sync_membership.sync("Course", all_semesters=True)
    assert errors == 0
    assert "no semesters are registered" in capsys.readouterr().err


def test_one_semester_failure_does_not_abort_the_whole_batch(monkeypatch, capsys):
    # The read helpers now raise on a non-404 failure; without isolation, one semester's
    # transient error aborts every other semester's sync. Each is wrapped: log, count, carry on.
    monkeypatch.setattr(
        sync_membership, "discover_semesters", lambda org: ["A", "B", "C"]
    )
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)

    processed = []

    def fake_roster_sync(org, **k):
        processed.append(org)
        if org == "B":
            raise RuntimeError("transient HTTP 502 reading B's roster")
        return 0

    monkeypatch.setattr(sync_membership.sync_roster, "sync", fake_roster_sync)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    errors = sync_membership.sync("Course", all_semesters=True)
    assert processed == ["A", "B", "C"]  # C still ran despite B blowing up
    assert errors == 1  # B's failure counted
    assert "semester B failed to sync" in capsys.readouterr().err


def test_an_unregistered_semester_org_is_refused(monkeypatch, capsys):
    # --semester-org arrives on the automatic path straight from a repository_dispatch's
    # client_payload, written by whoever holds a semester's DSL_BOT_TOKEN. Naming SOMEONE
    # ELSE'S semester would have this run reconcile - and prune - that semester's roster and
    # teams. The registry is the authority on which semesters this course org owns.
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A", "B"])
    reconciled: list = []
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_course_admins",
        lambda *a, **k: reconciled.append(a) or 0,
    )

    assert sync_membership.sync("Course", semester_org="Someone-Elses-Semester") == 1
    assert reconciled == []  # refused BEFORE anything is reconciled anywhere
    assert "not registered under Course" in capsys.readouterr().err


def test_a_registered_semester_is_matched_case_insensitively(monkeypatch):
    # GitHub org names are case-insensitive, and the registry's spelling need not match
    # the dispatch's - a case difference must not read as a cross-semester dispatch.
    monkeypatch.setattr(
        sync_membership, "discover_semesters", lambda org: ["Semester-F2026"]
    )
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    synced: list[str] = []
    monkeypatch.setattr(
        sync_membership.sync_roster, "sync", lambda org, **k: synced.append(org) or 0
    )
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", semester_org="semester-f2026") == 0
    assert synced == ["semester-f2026"]


def test_an_empty_registry_authorises_no_semester(monkeypatch, capsys):
    # "Nothing registered means nothing to check against" made the authorisation check
    # opt-out: a course org whose registry was empty, or whose registry read came back
    # empty, accepted any org a dispatch named - and then reconciled and PRUNED its
    # roster and teams. Bootstrap registers the semester before any sync names it.
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    touched: list[str] = []
    monkeypatch.setattr(
        sync_membership.sync_roster, "sync", lambda org, **k: touched.append(org) or 0
    )
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", semester_org="Semester-f2026") == 1
    assert touched == []
    assert "is not registered under Course" in capsys.readouterr().err


def test_an_empty_registry_still_reconciles_the_course_admins(monkeypatch):
    # A brand-new course org legitimately has no semesters; the course-org half of the
    # sync must still run, so an unnamed semester is not an error.
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)

    assert sync_membership.sync("Course") == 0


def test_a_clean_multi_semester_run_reports_no_errors(monkeypatch):
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )
    assert sync_membership.sync("Course", all_semesters=True) == 0


def test_every_semesters_sync_refreshes_the_team_formation_lock(monkeypatch):
    # The Join-team form reads `assignments.lock.yml` and nothing else, and this sync is
    # what a push to schedule.yml wakes (semester-config/dispatch-sync.yml). Without the
    # write here, adding an assignment left the form answering off the previous list.
    locked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sync_membership,
        "sync_team_lock",
        lambda course, semester, dry_run=False: (
            locked.append((course, semester)) or LockWrite(True, False)
        ),
    )
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert locked == [("Course", "A"), ("Course", "B")]


def test_a_push_that_moved_the_lock_moves_the_join_team_form_with_it(monkeypatch):
    # The lock decides whether a team MAY form; the form's Assignment field decides whether
    # a student can ask at all, and it is a REQUIRED dropdown rendered off that same lock.
    # Written here but not regenerated, a push that opened an assignment left the chooser
    # offering only the previous ones - so nobody could file the issue until the nightly
    # refresh, however current the mirror was.
    forms: list[str] = []
    monkeypatch.setattr(
        sync_membership,
        "sync_team_lock",
        lambda course, semester, dry_run=False: LockWrite(True, semester == "A"),
    )
    monkeypatch.setattr(
        sync_membership,
        "refresh_join_team_form",
        lambda org: forms.append(org) or 0,
    )
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert forms == ["A"], "only the semester whose mirror actually moved"


def test_a_lock_file_that_did_not_land_is_counted(monkeypatch):
    monkeypatch.setattr(
        sync_membership,
        "sync_team_lock",
        lambda course, semester, dry_run=False: LockWrite(False, False),
    )
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_semesters=True) == 1


# ------------------------------------------- a file faculty must fix is not a failed run


def _semicolon_roster_fault() -> Exception:
    """The exception a `;`-delimited students.csv really raises, taken from the reader the
    sync actually calls - not a hand-built stand-in whose type could drift from it."""
    with pytest.raises(faults.Unusable) as caught:
        roster.parse("hertie_email;name;github_handle\na;b;c\n")
    return caught.value


def _one_semester(monkeypatch, **steps):
    """Semesters A, B and C, with every per-semester step green unless `steps` says otherwise."""
    monkeypatch.setattr(
        sync_membership, "discover_semesters", lambda org: ["A", "B", "C"]
    )
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(
        sync_membership.sync_roster, "sync", steps.get("roster", lambda org, **k: 0)
    )
    monkeypatch.setattr(
        sync_membership.sync_teams, "sync", steps.get("teams", lambda org, **k: 0)
    )
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_semester_instructors",
        steps.get("instructors", lambda *a, **k: 0),
    )


def test_a_roster_nobody_can_read_skips_its_semester_and_leaves_the_run_green(
    monkeypatch, capsys
):
    # The whole point of the change: a students.csv saved as a German-locale Excel export
    # is faculty's to fix, and it used to red this cron every night - filing "Sync
    # membership is failing" in the course org and emailing the maintainer about a CSV in
    # a semester org that neither of them can fix. The fault reaches the person who saved it
    # through the semester's digest issue instead; the run says so and carries on.
    fault = _semicolon_roster_fault()
    reached = []

    def roster_sync(org, **k):
        reached.append(org)
        if org == "B":
            raise fault
        return 0

    _one_semester(monkeypatch, roster=roster_sync)
    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert reached == ["A", "B", "C"]  # B is skipped, not the batch
    err = capsys.readouterr().err
    assert "semester B has a config file the sync cannot read" in err
    assert "this run stays green" in err


def test_a_people_yml_that_is_not_yaml_skips_its_semester_too(monkeypatch, capsys):
    # The other half of the same rule, and the other exception a content fault arrives as:
    # `load_yaml_config` raises the loader's own error for a instructors.yml that does not
    # parse. Reconciling from what a half-read file says would prune every instructor the
    # parse dropped.
    def instructors(course, org, *a, **k):
        if org == "B":
            raise yaml.YAMLError("mapping values are not allowed here")
        return 0

    _one_semester(monkeypatch, instructors=instructors)
    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert (
        "semester B has a config file the sync cannot read" in capsys.readouterr().err
    )


def test_the_teams_the_semester_did_reconcile_are_not_undone_by_a_later_fault(
    monkeypatch,
):
    # A content fault stops the semester AT the fault: whatever ran before it stands. The
    # test that matters is the negative one - nothing after it runs on a file nobody could
    # read, so a broken teams.csv never reaches the instructor reconcile with an empty map.
    ran: list[str] = []
    fault = _semicolon_roster_fault()

    def teams(org, **k):
        ran.append(f"teams {org}")
        raise fault

    _one_semester(
        monkeypatch,
        roster=lambda org, **k: ran.append(f"roster {org}") or 0,
        teams=teams,
        instructors=lambda course, org, *a, **k: ran.append(f"people {org}") or 0,
    )
    assert sync_membership.sync("Course", semester_org="A") == 0
    assert ran == ["roster A", "teams A"]


def test_a_registry_nobody_can_parse_reconciles_nothing_and_stays_green(
    monkeypatch, capsys
):
    # The course org's own files are hand-edited too, and they used to be the exception:
    # both reads happen BEFORE the per-semester try, so a malformed registry reddened this
    # cron every night - and every 15-minute scheduler tick - over a file only a course
    # admin can fix. The course digest issue and the admin mail carry it.
    def boom(org):
        raise faults.Unusable(f"malformed semester registry in {org}/.github")

    monkeypatch.setattr(sync_membership, "discover_semesters", boom)
    _stub_course_admins(monkeypatch)
    assert sync_membership.sync("Course", all_semesters=True) == 0
    err = capsys.readouterr().err
    assert "Course has a course config file the sync cannot read" in err
    assert "nothing under this course is reconciled or pruned" in err


def test_a_dsl_course_yml_that_is_not_yaml_reconciles_nothing_and_stays_green(
    monkeypatch, capsys
):
    # The other course file: the admin reconcile reads it for the `people:` block, and it
    # arrives as the loader's own error rather than as `Unusable`.
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A"])
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_course_admins",
        lambda *a, **k: (_ for _ in ()).throw(yaml.YAMLError("bad indent")),
    )
    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert "has a course config file the sync cannot read" in capsys.readouterr().err


def test_a_course_config_read_that_failed_still_reds_the_run(monkeypatch, capsys):
    # The same line as below, drawn at the course level: a rate limit is not a file
    # faculty have to fix, and the maintainer is the one who has to hear about it.
    def boom(org):
        raise RuntimeError("HTTP 502 listing the semesters")

    monkeypatch.setattr(sync_membership, "discover_semesters", boom)
    with pytest.raises(RuntimeError, match="HTTP 502"):
        sync_membership.sync("Course", all_semesters=True)


def test_a_read_that_failed_still_reds_the_run(monkeypatch, capsys):
    # The line the whole distinction rests on. `Unusable` IS a RuntimeError, so a plain
    # one - a rate limit, a token that lost its scope - must not fall into the content
    # branch and be reported to faculty as "your file is broken" on a green run.
    def roster_sync(org, **k):
        raise RuntimeError("HTTP 502 reading the roster")

    _one_semester(monkeypatch, roster=roster_sync)
    assert sync_membership.sync("Course", semester_org="A") == 1
    assert "semester A failed to sync" in capsys.readouterr().err


def test_a_closed_out_semester_is_not_reconciled(monkeypatch):
    # Its org is read-only: every team grant and roster write would 403 nightly. The
    # registry still lists it (that is what authorises a dispatch); the sync skips it.
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    monkeypatch.setattr(sync_membership, "semester_is_live", lambda org: org != "A")
    admins: list[list[str]] = []
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_course_admins",
        lambda course, semesters, **k: admins.append(list(semesters)) or 0,
    )
    processed: list[str] = []
    monkeypatch.setattr(
        sync_membership.sync_roster,
        "sync",
        lambda org, **k: processed.append(org) or 0,
    )
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_semester_instructors", lambda *a, **k: 0
    )
    assert sync_membership.sync("Course", all_semesters=True) == 0
    assert (processed, admins) == (["B"], [["B"]])


def test_a_dispatch_from_a_closed_out_semester_reconciles_nothing(monkeypatch):
    monkeypatch.setattr(sync_membership, "discover_semesters", lambda org: ["A"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    monkeypatch.setattr(sync_membership, "semester_is_live", lambda org: False)
    _stub_course_admins(monkeypatch)

    def boom(org, **k):
        raise AssertionError("a frozen semester must not be reconciled")

    monkeypatch.setattr(sync_membership.sync_roster, "sync", boom)
    assert sync_membership.sync("Course", semester_org="A") == 0


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            ["--semester-org", "Semester-f2026", "--no-preview"],
            [("Course", "Semester-f2026")],
        ),
        (["--semester-org", "Semester-f2026", "--preview"], []),
        (["--all-semesters"], []),
    ],
)
def test_a_dispatched_semester_sync_refreshes_that_semesters_status(
    monkeypatch, extra, expected
):
    # A push to one semester's roster, teams or instructors.yml dispatches this with its name:
    # the status follows. A preview writes nothing, and the nightly sweep leaves status
    # to the nightly refresh.
    monkeypatch.setattr(sync_membership, "acting_login", lambda: "bot")
    monkeypatch.setattr(sync_membership, "sync", lambda *a, **k: 0)
    refreshed: list = []
    monkeypatch.setattr(
        sync_membership.status, "refresh", lambda *a: refreshed.append(a) or 1
    )
    monkeypatch.setattr(
        "sys.argv", ["sync_membership", "--course-org", "Course", *extra]
    )
    assert sync_membership.main() == 0
    assert refreshed == expected
