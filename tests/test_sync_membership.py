"""sync_membership orchestrates course-admin (always) plus per-cohort roster/teams/
instructors. The gh wiring is left live per the testing strategy; these pin the
orchestration decisions: an empty registry is visible (not a silent green), one
cohort's failure is isolated from the rest of the batch, and a file faculty have to fix
skips its cohort without reddening the cron.
"""

from __future__ import annotations

import pytest
import yaml

from dsl_course import faults, roster, sync_membership


def _stub_course_admins(monkeypatch, rv: int = 0):
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_course_admins", lambda *a, **k: rv
    )


@pytest.fixture(autouse=True)
def _team_lock_is_current(monkeypatch):
    """The Join-team form's mirror, written at the end of every cohort's sync. It reads
    the cohort's schedule and each template's definition, so it is stubbed here for the
    tests that are about the sync's own orchestration; the ones that are about the lock
    file itself set their own."""
    monkeypatch.setattr(
        sync_membership, "write_team_lock", lambda course, cohort, dry_run=False: True
    )


def test_empty_registry_is_visible_but_not_fatal(monkeypatch, capsys):
    # An empty registry can be legitimate for a brand-new course org, so the run does not
    # fail - but it must be loudly visible, not a silent green "Sync complete".
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: [])
    _stub_course_admins(monkeypatch)
    errors = sync_membership.sync("Course", all_cohorts=True)
    assert errors == 0
    assert "no cohorts are registered" in capsys.readouterr().err


def test_one_cohort_failure_does_not_abort_the_whole_batch(monkeypatch, capsys):
    # The read helpers now raise on a non-404 failure; without isolation, one cohort's
    # transient error aborts every other cohort's sync. Each is wrapped: log, count, carry on.
    monkeypatch.setattr(
        sync_membership, "discover_cohorts", lambda org: ["A", "B", "C"]
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
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )

    errors = sync_membership.sync("Course", all_cohorts=True)
    assert processed == ["A", "B", "C"]  # C still ran despite B blowing up
    assert errors == 1  # B's failure counted
    assert "cohort B failed to sync" in capsys.readouterr().err


def test_an_unregistered_cohort_org_is_refused(monkeypatch, capsys):
    # --cohort-org arrives on the automatic path straight from a repository_dispatch's
    # client_payload, written by whoever holds a cohort's DSL_BOT_TOKEN. Naming SOMEONE
    # ELSE'S cohort would have this run reconcile - and prune - that cohort's roster and
    # teams. The registry is the authority on which cohorts this course org owns.
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: ["A", "B"])
    reconciled: list = []
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_course_admins",
        lambda *a, **k: reconciled.append(a) or 0,
    )

    assert sync_membership.sync("Course", cohort_org="Someone-Elses-Cohort") == 1
    assert reconciled == []  # refused BEFORE anything is reconciled anywhere
    assert "not registered under Course" in capsys.readouterr().err


def test_a_registered_cohort_is_matched_case_insensitively(monkeypatch):
    # GitHub org names are case-insensitive, and the registry's spelling need not match
    # the dispatch's - a case difference must not read as a cross-cohort dispatch.
    monkeypatch.setattr(
        sync_membership, "discover_cohorts", lambda org: ["Cohort-F2026"]
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
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", cohort_org="cohort-f2026") == 0
    assert synced == ["cohort-f2026"]


def test_an_empty_registry_authorises_no_cohort(monkeypatch, capsys):
    # "Nothing registered means nothing to check against" made the authorisation check
    # opt-out: a course org whose registry was empty, or whose registry read came back
    # empty, accepted any org a dispatch named - and then reconciled and PRUNED its
    # roster and teams. Bootstrap registers the cohort before any sync names it.
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    touched: list[str] = []
    monkeypatch.setattr(
        sync_membership.sync_roster, "sync", lambda org, **k: touched.append(org) or 0
    )
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", cohort_org="Cohort-f2026") == 1
    assert touched == []
    assert "is not registered under Course" in capsys.readouterr().err


def test_an_empty_registry_still_reconciles_the_course_admins(monkeypatch):
    # A brand-new course org legitimately has no cohorts; the course-org half of the
    # sync must still run, so an unnamed cohort is not an error.
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)

    assert sync_membership.sync("Course") == 0


def test_a_clean_multi_cohort_run_reports_no_errors(monkeypatch):
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )
    assert sync_membership.sync("Course", all_cohorts=True) == 0


def test_every_cohorts_sync_refreshes_the_team_formation_lock(monkeypatch):
    # The Join-team form reads `assignments.lock.yml` and nothing else, and this sync is
    # what a push to schedule.yml wakes (classroom-config/dispatch-sync.yml). Without the
    # write here, adding an assignment left the form answering off the previous list.
    locked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sync_membership,
        "write_team_lock",
        lambda course, cohort, dry_run=False: locked.append((course, cohort)) or True,
    )
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: ["A", "B"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_cohorts=True) == 0
    assert locked == [("Course", "A"), ("Course", "B")]


def test_a_lock_file_that_did_not_land_is_counted(monkeypatch):
    monkeypatch.setattr(
        sync_membership, "write_team_lock", lambda course, cohort, dry_run=False: False
    )
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: ["A"])
    monkeypatch.setattr(sync_membership, "discover_content_repos", lambda org: [])
    monkeypatch.setattr(sync_membership, "discover_assignments", lambda org: [])
    _stub_course_admins(monkeypatch)
    monkeypatch.setattr(sync_membership.sync_roster, "sync", lambda org, **k: 0)
    monkeypatch.setattr(sync_membership.sync_teams, "sync", lambda org, **k: 0)
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_cohort_instructors", lambda *a, **k: 0
    )

    assert sync_membership.sync("Course", all_cohorts=True) == 1


# ------------------------------------------- a file faculty must fix is not a failed run


def _semicolon_roster_fault() -> Exception:
    """The exception a `;`-delimited students.csv really raises, taken from the reader the
    sync actually calls - not a hand-built stand-in whose type could drift from it."""
    with pytest.raises(faults.Unusable) as caught:
        roster.parse("hertie_email;name;github_handle\na;b;c\n")
    return caught.value


def _one_cohort(monkeypatch, **steps):
    """Cohorts A, B and C, with every per-cohort step green unless `steps` says otherwise."""
    monkeypatch.setattr(
        sync_membership, "discover_cohorts", lambda org: ["A", "B", "C"]
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
        "sync_cohort_instructors",
        steps.get("instructors", lambda *a, **k: 0),
    )


def test_a_roster_nobody_can_read_skips_its_cohort_and_leaves_the_run_green(
    monkeypatch, capsys
):
    # The whole point of the change: a students.csv saved as a German-locale Excel export
    # is faculty's to fix, and it used to red this cron every night - filing "Sync
    # membership is failing" in the course org and emailing the maintainer about a CSV in
    # a cohort org that neither of them can fix. The fault reaches the person who saved it
    # through the cohort's digest issue instead; the run says so and carries on.
    fault = _semicolon_roster_fault()
    reached = []

    def roster_sync(org, **k):
        reached.append(org)
        if org == "B":
            raise fault
        return 0

    _one_cohort(monkeypatch, roster=roster_sync)
    assert sync_membership.sync("Course", all_cohorts=True) == 0
    assert reached == ["A", "B", "C"]  # B is skipped, not the batch
    err = capsys.readouterr().err
    assert "cohort B has a config file the sync cannot read" in err
    assert "this run stays green" in err


def test_a_people_yml_that_is_not_yaml_skips_its_cohort_too(monkeypatch, capsys):
    # The other half of the same rule, and the other exception a content fault arrives as:
    # `load_yaml_config` raises the loader's own error for a people.yml that does not
    # parse. Reconciling from what a half-read file says would prune every instructor the
    # parse dropped.
    def instructors(course, org, *a, **k):
        if org == "B":
            raise yaml.YAMLError("mapping values are not allowed here")
        return 0

    _one_cohort(monkeypatch, instructors=instructors)
    assert sync_membership.sync("Course", all_cohorts=True) == 0
    assert "cohort B has a config file the sync cannot read" in capsys.readouterr().err


def test_the_teams_the_cohort_did_reconcile_are_not_undone_by_a_later_fault(
    monkeypatch,
):
    # A content fault stops the cohort AT the fault: whatever ran before it stands. The
    # test that matters is the negative one - nothing after it runs on a file nobody could
    # read, so a broken teams.csv never reaches the instructor reconcile with an empty map.
    ran: list[str] = []
    fault = _semicolon_roster_fault()

    def teams(org, **k):
        ran.append(f"teams {org}")
        raise fault

    _one_cohort(
        monkeypatch,
        roster=lambda org, **k: ran.append(f"roster {org}") or 0,
        teams=teams,
        instructors=lambda course, org, *a, **k: ran.append(f"people {org}") or 0,
    )
    assert sync_membership.sync("Course", cohort_org="A") == 0
    assert ran == ["roster A", "teams A"]


def test_a_registry_nobody_can_parse_reconciles_nothing_and_stays_green(
    monkeypatch, capsys
):
    # The course org's own files are hand-edited too, and they used to be the exception:
    # both reads happen BEFORE the per-cohort try, so a malformed registry reddened this
    # cron every night - and every 15-minute scheduler tick - over a file only a course
    # admin can fix. The course digest issue and the admin mail carry it.
    def boom(org):
        raise faults.Unusable(f"malformed cohort registry in {org}/.github")

    monkeypatch.setattr(sync_membership, "discover_cohorts", boom)
    _stub_course_admins(monkeypatch)
    assert sync_membership.sync("Course", all_cohorts=True) == 0
    err = capsys.readouterr().err
    assert "Course has a course config file the sync cannot read" in err
    assert "nothing under this course is reconciled or pruned" in err


def test_a_dsl_course_yml_that_is_not_yaml_reconciles_nothing_and_stays_green(
    monkeypatch, capsys
):
    # The other course file: the admin reconcile reads it for the `people:` block, and it
    # arrives as the loader's own error rather than as `Unusable`.
    monkeypatch.setattr(sync_membership, "discover_cohorts", lambda org: ["A"])
    monkeypatch.setattr(
        sync_membership.sync_faculty,
        "sync_course_admins",
        lambda *a, **k: (_ for _ in ()).throw(yaml.YAMLError("bad indent")),
    )
    assert sync_membership.sync("Course", all_cohorts=True) == 0
    assert "has a course config file the sync cannot read" in capsys.readouterr().err


def test_a_course_config_read_that_failed_still_reds_the_run(monkeypatch, capsys):
    # The same line as below, drawn at the course level: a rate limit is not a file
    # faculty have to fix, and the maintainer is the one who has to hear about it.
    def boom(org):
        raise RuntimeError("HTTP 502 listing the cohorts")

    monkeypatch.setattr(sync_membership, "discover_cohorts", boom)
    with pytest.raises(RuntimeError, match="HTTP 502"):
        sync_membership.sync("Course", all_cohorts=True)


def test_a_read_that_failed_still_reds_the_run(monkeypatch, capsys):
    # The line the whole distinction rests on. `Unusable` IS a RuntimeError, so a plain
    # one - a rate limit, a token that lost its scope - must not fall into the content
    # branch and be reported to faculty as "your file is broken" on a green run.
    def roster_sync(org, **k):
        raise RuntimeError("HTTP 502 reading the roster")

    _one_cohort(monkeypatch, roster=roster_sync)
    assert sync_membership.sync("Course", cohort_org="A") == 1
    assert "cohort A failed to sync" in capsys.readouterr().err
