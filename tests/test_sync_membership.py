"""sync_membership orchestrates course-admin (always) plus per-cohort roster/teams/
instructors. The gh wiring is left live per the testing strategy; these pin the
orchestration decisions: an empty registry is visible (not a silent green), and one
cohort's failure is isolated from the rest of the batch.
"""

from __future__ import annotations

from dsl_course import sync_membership


def _stub_course_admins(monkeypatch, rv: int = 0):
    monkeypatch.setattr(
        sync_membership.sync_faculty, "sync_course_admins", lambda *a, **k: rv
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
