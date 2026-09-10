"""scheduler pure core: due_releases (datetime, timezone-correct) + _execute()'s dispatch
to the release functions - monkeypatched so a schema<->signature mismatch (the class of bug
that silently broke scheduled releases once) is caught without any real gh/git I/O. Plus the
deadline-driven phases (snapshot, then fire-once autograde) and a renderer guard (the cron is
hourly and has NO check-team gate - scheduled runs have no actor).
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from conftest import source_fault

from dsl_course import (
    access,
    config_digest,
    course,
    deploy,
    ghcli,
    notify,
    repos,
    scheduler,
    seed,
    source_digest,
)
from dsl_course import collect as collect_mod
from dsl_course import faults as faults_mod
from dsl_course import issues as issues_mod
from dsl_course.faults import ConfigFault, Unusable
from dsl_course.grades import GradingSpec
from dsl_course.schedule import (
    AssignmentEntry,
    Deploy,
    Release,
    Schedule,
)

BERLIN = ZoneInfo("Europe/Berlin")
WHEN = datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN)


def _verdict(
    now: datetime = WHEN, prev: datetime | None = None
) -> scheduler.cadence.Verdict:
    return scheduler.cadence.Verdict(
        armed=True,
        last_dispatch_at=now,
        last_schedule_at=now,
        ds01_dead=False,
        prev_executed_at=now if prev is None else prev,
        recent_gaps=[],
        now=now,
    )


@pytest.fixture(autouse=True)
def cadence_calls(monkeypatch):
    """Stub the whole cadence surface and hand the test what was asked of it.

    The check reads this workflow's own run history and files two issues, all real gh I/O -
    and `conftest` refuses a live `gh`, so every `--all-cohorts` test below would otherwise
    fail on the read rather than on what it is about. `late_items` is deliberately left
    real: it is pure, and it is the half the wiring can get wrong."""
    calls: dict[str, list] = {
        "fetch_runs": [],
        "evaluate": [],
        "report_course": [],
        "report_cohort": [],
    }

    def _record(name: str, answer):
        def stub(*args, **kwargs):
            calls[name].append(args)
            return answer

        return stub

    monkeypatch.setattr(scheduler.cadence, "fetch_runs", _record("fetch_runs", []))
    monkeypatch.setattr(scheduler.cadence, "evaluate", _record("evaluate", _verdict()))
    monkeypatch.setattr(scheduler.cadence, "report_course", _record("report_course", 0))
    monkeypatch.setattr(scheduler.cadence, "report_cohort", _record("report_cohort", 0))
    return calls


@pytest.fixture(autouse=True)
def _grading_spec_defaults(monkeypatch):
    """The cutoff is read out of each template's grading_config.yml (`late_window_days`), which is
    real gh I/O. Answered with the defaults - no window - so every test below keeps
    measuring the behaviour it was written for; the tests that are ABOUT the window declare
    their own spec."""
    monkeypatch.setattr(
        scheduler, "load_grading_spec", lambda org, template: GradingSpec()
    )


@pytest.fixture(autouse=True)
def _no_open_notices(monkeypatch):
    """Every tick asks the cohort's `classroom-config` which archive notices are open, so
    it can close one whose date has moved or been taken away
    (`scheduler._stale_archive_notices`) - real gh I/O, on a path these tests are not
    about. Answered with "none open", which is the ordinary case; the tests that ARE
    about it stub it themselves."""
    monkeypatch.setattr(scheduler.issues, "open_titles", lambda repo: set())


@pytest.fixture(autouse=True)
def _no_source_preflight(monkeypatch):
    """`run` also pre-flights the plan's sources against the course org, which is real gh
    I/O. These tests are about the release/snapshot/autograde phases, so it is stubbed to
    "everything is staged" by default; the pre-flight has its own tests below."""
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [])
    monkeypatch.setattr(
        scheduler.source_digest, "sync", lambda *a, **k: source_digest.DigestResult()
    )


def _r(label: str, when: datetime, **kw) -> Release:
    return Release(label=label, when=when, **kw)


def _sched_with(releases: list[Release]) -> Schedule:
    return Schedule(releases=releases)


def test_due_releases_in_when_order():
    releases = sorted(
        [
            _r("b", datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN), assignment="x"),
            _r("a", datetime(2026, 9, 1, 9, 0, tzinfo=BERLIN), assignment="x"),
            _r("c", datetime(2026, 9, 29, 9, 0, tzinfo=BERLIN), assignment="x"),
        ],
        key=lambda r: r.when,
    )
    now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    assert [r.label for r in scheduler.due_releases(releases, now)] == ["a", "b"]
    assert (
        scheduler.due_releases(releases, datetime(2026, 8, 1, tzinfo=timezone.utc))
        == []
    )
    assert (
        len(
            scheduler.due_releases(releases, datetime(2026, 12, 1, tzinfo=timezone.utc))
        )
        == 3
    )


def test_due_releases_honours_time_of_day_across_timezones():
    # 14:00 Europe/Berlin (CEST) == 12:00 UTC. At 11:00 UTC not yet due; at 13:00 UTC due.
    r = _r("s", datetime(2026, 9, 15, 14, 0, tzinfo=BERLIN), assignment="x")
    assert (
        scheduler.due_releases([r], datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc))
        == []
    )
    assert scheduler.due_releases(
        [r], datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
    ) == [r]


def test_display_only_entries_are_never_due():
    # An event_datetime with no actions is a site schedule row, not work - the scheduler
    # must never consider it due, no matter how far past its datetime we are.
    r = _r("project-clinic", datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN))
    assert r.is_event_only
    assert scheduler.due_releases([r], datetime(2026, 12, 1, tzinfo=timezone.utc)) == []


def test_deploy_datetime_fires_on_its_own_clock():
    # The class is announced for 10:00 (event_datetime); its materials carry a
    # deploy_datetime an hour earlier. The deploy is due at 09:00, before the entry's
    # own datetime - and a second copy without an override still waits for 10:00.
    early = Deploy(
        "cm-f2026",
        "lectures/02_intro",
        "materials",
        None,
        deploy_datetime=datetime(2026, 9, 15, 9, 0, tzinfo=BERLIN),
    )
    at_class = Deploy("cm-f2026", "readings/02_intro", "materials", None)
    r = _r(
        "session-2",
        datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
        deploy=[early, at_class],
    )
    between = datetime(2026, 9, 15, 7, 30, tzinfo=timezone.utc)  # 09:30 Berlin
    assert scheduler.due_releases([r], between) == [r]
    assert r.due_deploys(between) == [early]
    after = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)  # 11:00 Berlin
    assert r.due_deploys(after) == [early, at_class]


def test_describe_marks_not_yet_due_actions():
    # Dry-run legibility: an entry due only for its early deploy must not read as if the
    # handout (or a later deploy) were firing now.
    early = Deploy(
        "cm-f2026",
        "lectures/02_intro",
        "materials",
        None,
        deploy_datetime=datetime(2026, 9, 15, 9, 0, tzinfo=BERLIN),
    )
    r = _r(
        "session-2",
        datetime(2026, 9, 15, 10, 0, tzinfo=BERLIN),
        deploy=[early],
        assignment="assignment-1-f2026",
    )
    lines = scheduler.describe(r, datetime(2026, 9, 15, 7, 30, tzinfo=timezone.utc))
    deploy_line = next(ln for ln in lines if ln.startswith("deploy "))
    assert "not yet due" not in deploy_line  # the early deploy IS firing
    assignment_line = next(ln for ln in lines if ln.startswith("assignment "))
    assert "not yet due" in assignment_line


def test_describe_lists_every_action():
    r = _r(
        "s2",
        WHEN,
        deploy=[
            Deploy("cm-f2026", "lectures/02_intro", "materials", None),
            Deploy("data-f2026", "w7/housing.csv", "materials", "datasets/housing.csv"),
        ],
        assignment="assignment-1-f2026",
    )
    lines = scheduler.describe(r)
    assert any(
        "cm-f2026/lectures/02_intro -> materials/lectures/02_intro" in ln
        for ln in lines
    )
    assert any("materials/datasets/housing.csv" in ln for ln in lines)
    assert any(ln.startswith("assignment ") for ln in lines)


# _execute_nondeploy() and the deploy batching ARE pure wiring (no gh/git of their own),
# but a schema<->signature mismatch is exactly the class of bug that silently broke
# scheduled releases - monkeypatching the release functions catches it without real I/O.


def test_run_batches_all_deploys_through_deploy_many(monkeypatch):
    # The clone-once win: every due release's deploys go through ONE deploy_many call
    # (which clones each source/dest once), not one call per copy. deploy_many is now the
    # single executor for both paths - the manual Release materials button batches its
    # comma-separated paths through the same call (see test_release.py).
    calls = []
    monkeypatch.setattr(
        "dsl_course.scheduler.deploy_many",
        lambda source_org, cohort_org, deploys, sync=True: (
            calls.append((source_org, cohort_org, list(deploys), sync)) or (0, True)
        ),
    )
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _sched_with(
            [
                _r(
                    "w1",
                    datetime(2026, 9, 1, tzinfo=BERLIN),
                    deploy=[
                        Deploy("cm", "lectures/00_x", "lectures", None),
                        Deploy("cm", "labs/00_y", "labs", None),
                    ],
                ),
                _r(
                    "w2",
                    datetime(2026, 9, 8, tzinfo=BERLIN),
                    deploy=[
                        Deploy("cm", "lectures/01_z", "lectures", None),
                    ],
                ),
            ]
        ),
    )
    synced = []
    monkeypatch.setattr(
        "dsl_course.site.sync_site", lambda c, o: synced.append((c, o)) or 0
    )
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0
    # exactly ONE deploy_many call, carrying all 3 deploys across both releases, sync=False
    assert len(calls) == 1
    source_org, cohort_org, deploys, sync = calls[0]
    assert (source_org, cohort_org, sync) == ("Course-Org", "Cohort-Org", False)
    assert len(deploys) == 3
    # the scheduler syncs the site exactly once, itself (deploy_many was told not to)
    assert synced == [("Course-Org", "Cohort-Org")]


def test_a_tick_that_provisions_nothing_does_not_re_render_the_site(monkeypatch):
    # `due_releases` is CUMULATIVE: a handed-out assignment is due again on every hourly
    # tick for the rest of the term. Marking the tick as having assigned regardless of what
    # provisioning actually did meant a full cohort website re-render, once an hour, off a
    # pass in which every repo was skipped.
    monkeypatch.setattr(
        "dsl_course.scheduler.provision_all",
        lambda *a, **kw: (0, False),  # every unit `skipped`
    )
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _sched_with([_r("w1", WHEN, assignment="assignment-2-f2026")]),
    )
    synced = []
    monkeypatch.setattr(
        "dsl_course.site.sync_site", lambda c, o: synced.append((c, o)) or 0
    )
    now = datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0
    assert synced == [], "an unchanged tick re-rendered the site"

    # ... and a tick that DID provision something still syncs, exactly once.
    monkeypatch.setattr(
        "dsl_course.scheduler.provision_all", lambda *a, **kw: (0, True)
    )
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0
    assert synced == [("Course-Org", "Cohort-Org")]


def test_execute_nondeploy_assignment_calls_provision_all(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dsl_course.scheduler.provision_all",
        lambda master_org, template, cohort_org, solution=False, touch_existing=True, scheduled=False, slug="": (
            (
                calls.append(
                    (
                        master_org,
                        template,
                        cohort_org,
                        solution,
                        touch_existing,
                        scheduled,
                        slug,
                    )
                ),
                (0, True),
            )[1]
        ),
    )
    r = _r("s", WHEN, assignment="assignment-2-f2026", assignment_slug="assignment-2")
    assert scheduler._execute_nondeploy("Course-Org", "Cohort-Org", r) == (0, True)
    # The hourly path never re-touches an existing repo (the manual button does), says it
    # is the cron - a group handout with no teams yet then waits instead of going red -
    # and names WHICH entry it is firing, since two may hand out from one template.
    assert calls[0] == (
        "Course-Org",
        "assignment-2-f2026",
        "Cohort-Org",
        False,
        False,
        True,
        "assignment-2",
    )

    # The solution release is the SAME call, asked to push the solution too - so a
    # scheduled solution can never diverge from what include_solution does by hand.
    r = _r("s", WHEN, assignment="assignment-2-f2026", assignment_slug="assignment-2")
    r.assignment_solution = True
    assert scheduler._execute_nondeploy("Course-Org", "Cohort-Org", r) == (0, True)
    assert calls[1] == (
        "Course-Org",
        "assignment-2-f2026",
        "Cohort-Org",
        True,
        False,
        True,
        "assignment-2",
    )


def _git_with_staged_changes(*args):
    """A git fake that answers the two questions deploy_many asks about a dest off what
    the copy actually left in the clone, so a release that copied NOTHING cannot report a
    commit it could not have made:

    - `git diff --cached --quiet` exits 1 while there are files there (staged changes to
      commit on `upstream`), 0 when the clone is empty;
    - `git merge-base --is-ancestor upstream <base>` exits 1 after such a commit (there is
      a merge to make into the branch students read), 0 otherwise.

    Every other call (add/commit/checkout/merge/push) succeeds. The end-to-end shape of
    all this - against real repositories rather than a stub with an opinion - is
    tests/test_release_merge.py."""
    dd = Path(args[args.index("-C") + 1]) if "-C" in args else None
    copied = bool(dd and any(p.is_file() for p in dd.rglob("*")))
    if "diff" in args and "--cached" in args:
        return (1, "") if copied else (0, "")
    if "merge-base" in args:
        return (1, "") if copied else (0, "")
    return (0, "")


def test_deploy_many_clones_each_repo_once(monkeypatch):
    # The optimisation: 3 deploys from one source into two dests clone the source ONCE
    # and each dest ONCE (3 clones total), not once per copy (6).
    clones = []

    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            spec, dest = args[2], args[3]
            clones.append(spec)
            p = Path(dest)
            p.mkdir(parents=True, exist_ok=True)
            if spec.startswith(
                "Course-Org/"
            ):  # source repo: seed the paths deploys read
                for sp in ("lectures/00_x", "labs/00_y", "lectures/01_z"):
                    d = p / sp
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "f.txt").write_text("x")
            return (0, "")
        return (0, "")

    monkeypatch.setattr(ghcli, "gh", fake_gh)
    # `git diff --cached --quiet` reports staged changes (exit 1) so the copies commit;
    # everything else (add/commit/push) succeeds.
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    deploys = [
        Deploy("cm", "lectures/00_x", "lectures", None),
        Deploy("cm", "labs/00_y", "labs", None),
        Deploy("cm", "lectures/01_z", "lectures", None),
    ]
    errors, changed = deploy.deploy_many(
        "Course-Org", "Cohort-Org", deploys, sync=False
    )
    assert (errors, changed) == (0, True)
    assert clones.count("Course-Org/cm") == 1  # source cloned once for all 3 copies
    assert clones.count("Cohort-Org/lectures") == 1
    assert clones.count("Cohort-Org/labs") == 1
    assert len(clones) == 3  # 1 source + 2 dests, not 6


def test_deploy_many_missing_course_source_path_is_an_error_not_silent(monkeypatch):
    # A wrong course_source_path must be a loud error (return count), never a silent no-op.
    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            Path(args[3]).mkdir(parents=True, exist_ok=True)  # empty clones
            return (0, "")
        return (0, "")

    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(deploy, "git", lambda *a: (0, ""))
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/does-not-exist", "materials", None)],
        sync=False,
    )
    assert errors == 1 and changed is False


def _clone_failing(*failing: str):
    """A fake gh where cloning any repo in `failing` fails; others clone empty."""

    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            if args[2] in failing:
                return (1, "boom")
            Path(args[3]).mkdir(parents=True, exist_ok=True)
            return (0, "")
        return (0, "")

    return fake_gh


def _no_io(monkeypatch, fake_gh):
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(deploy, "git", lambda *a: (0, ""))
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)


def test_a_released_repo_grants_the_instructors_push(monkeypatch):
    # Write, because an edit made in the cohort repo is now durable: the release lands on
    # `upstream` and is merged in, so a correction typed here survives the next tick. It
    # was read for exactly as long as a re-release copied straight over it.
    _no_io(monkeypatch, _clone_failing("Course-Org/cm"))
    faculty = []
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: faculty.append(a))
    deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert faculty == [("Cohort-Org", "materials", deploy.COURSE_TEAM_ACCESS)]
    assert deploy.COURSE_TEAM_ACCESS[access.INSTRUCTORS_TEAM] == "push"


def test_a_released_repo_is_made_forkable(monkeypatch):
    # Students are told to fork the materials and work in their own copy, and a private
    # repo needs its OWN flag as well as the org's. Converged on every release, not only
    # at creation, so a dest that predates the flag gets it too.
    _no_io(monkeypatch, _clone_failing("Course-Org/cm"))
    forkable = []
    monkeypatch.setattr(deploy, "allow_forking", lambda *a: forkable.append(a) or True)
    deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert forkable == [("Cohort-Org", "materials")]


def test_a_dest_that_could_not_be_made_forkable_still_releases(monkeypatch):
    # A missing Fork button is worth a line, not a red release: whether the setting can be
    # set at all depends on the org's plan, and a quarter-hourly cron that is always red
    # is how a real failure stops being noticed.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a: False)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    assert deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    ) == (0, True)


def test_an_archived_dest_is_skipped_rather_than_failed(monkeypatch, capsys):
    # A closed cohort still has `deploy:` entries in its schedule. Every write into an
    # archived repo 403s, so the release used to fail its grants and its push every
    # quarter of an hour, for ever, for a cohort that is deliberately finished.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    for name in ("create_repo", "allow_forking", "grant_read_teams", "grant_faculty"):
        monkeypatch.setattr(
            deploy,
            name,
            lambda *a, _called=name, **k: pytest.fail(
                f"wrote into an archived repo: {_called}"
            ),
        )

    assert deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    ) == (0, False)
    assert "[skip]" in capsys.readouterr().out


def test_a_live_dest_beside_an_archived_one_still_releases(monkeypatch):
    # The skip is per DEST, not per run: one closed repo in a batch must not take the
    # rest of the release with it.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(
        deploy, "repo_is_archived", lambda org, repo: repo == "last-term"
    )

    assert deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [
            Deploy("cm", "lectures/00_x", "last-term", None),
            Deploy("cm", "lectures/00_x", "materials", None),
        ],
        sync=False,
    ) == (0, True)


def test_a_dest_whose_archived_flag_cannot_be_read_is_released_anyway(monkeypatch):
    # `repo_is_archived` fails OPEN, and this is the call site that makes that matter: a
    # rate-limited read must not silently skip a live cohort's release. Guess wrong that
    # way and the write itself fails loudly, which is the alarm we want.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 - bad gateway"))
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    assert deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    ) == (0, True)


def test_deploy_many_counts_a_doomed_deploy_once(monkeypatch):
    # Source AND dest clone both fail: that is ONE copy lost, not two errors (a
    # double-count made `deploy` report 2 failures for a single deploy).
    _no_io(monkeypatch, _clone_failing("Course-Org/cm", "Cohort-Org/materials"))
    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (1, False)


def test_deploy_many_counts_each_unrunnable_deploy_once(monkeypatch):
    # 3 deploys: the shared source fails AND one dest fails - still 3 lost copies.
    _no_io(monkeypatch, _clone_failing("Course-Org/cm", "Cohort-Org/labs"))
    deploys = [
        Deploy("cm", "lectures/00_x", "lectures", None),
        Deploy("cm", "labs/00_y", "labs", None),
        Deploy("cm", "lectures/01_z", "lectures", None),
    ]
    assert deploy.deploy_many("Course-Org", "Cohort-Org", deploys, sync=False) == (
        3,
        False,
    )


# ------------------------------------------------ deploy path-safety + commit failures


def _clone_with_tree(tree: dict[str, str]):
    """A gh fake whose source clone is seeded with `tree` (relpath -> file text); dest
    clones are empty. `tree` may include a `.git/...` entry to prove it is never copied."""

    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            spec, dest = args[2], args[3]
            base = Path(dest)
            base.mkdir(parents=True, exist_ok=True)
            if spec.startswith("Course-Org/"):
                for rel, text in tree.items():
                    p = base / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(text)
            return (0, "")
        return (0, "")

    return fake_gh


def _git_spying_staged(sink: list[str]):
    """A `_git_with_staged_changes` that first records every file present in the dest clone
    at `git add` time - the only moment the copied tree can be inspected, before
    deploy_many's TemporaryDirectory is cleaned up."""

    def spy_git(*args):
        if "add" in args:
            dd = Path(args[args.index("-C") + 1])
            sink.extend(str(p.relative_to(dd)) for p in dd.rglob("*") if p.is_file())
        return _git_with_staged_changes(*args)

    return spy_git


def test_deploy_many_releases_the_whole_repo_from_a_root_source_path(monkeypatch):
    # The end-to-end proof of "release everything": a root course_source_path survives
    # clone -> copytree -> git add carrying the content and none of the faculty side.
    # (Which spellings mean the root is _resolve_within's job - unit-tested in test_deploy.)
    staged: list[str] = []
    _no_io(
        monkeypatch,
        _clone_with_tree(
            {
                "labs/01.md": "lab one",
                "labs/.github/keep.yml": "faculty's own, not plumbing",
                "SYLLABUS.md": "syllabus",
                "MAINTAINING.md": "faculty notes - never released",
                ".git/config": "SOURCE-REMOTE",
                ".github/workflows/release-materials.yml": "BUTTON",
            }
        ),
    )
    monkeypatch.setattr(deploy, "git", _git_spying_staged(staged))

    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "/", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (0, True)
    assert sorted(staged) == ["SYLLABUS.md", "labs/.github/keep.yml", "labs/01.md"]


def test_a_copied_in_gitignore_cannot_swallow_released_files(monkeypatch):
    # A whole-repo release brings the source's own `.gitignore` with it. `git add -A` would
    # then honour it and skip every released file it matches - and a source that force-added
    # its lecture PDFs past a `*.pdf` rule is exactly the repo whose faculty would never
    # think to check. The release would report success having shipped nothing. Asserting on
    # the argv, not on the copied tree: these fakes never run real git, so only the flag
    # itself can prove the staged set is the copied set.
    calls: list[tuple[str, ...]] = []
    _no_io(
        monkeypatch,
        _clone_with_tree({".gitignore": "*.pdf\n", "lectures/slides.pdf": "%PDF"}),
    )

    def recording_git(*args):
        calls.append(args)
        return _git_with_staged_changes(*args)

    monkeypatch.setattr(deploy, "git", recording_git)

    errors, changed = deploy.deploy_many(
        "Course-Org", "Cohort-Org", [Deploy("cm", "/", "materials", None)], sync=False
    )
    assert (errors, changed) == (0, True)
    add = next(c for c in calls if "add" in c)
    assert "-f" in add, f"release staged without --force: {add}"


def test_deploy_many_rejects_a_dotdot_escaping_source_path(monkeypatch):
    _no_io(monkeypatch, _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "../../etc/passwd", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (1, False)


def test_deploy_many_never_copies_a_dot_git_directory(monkeypatch):
    # Even a legitimate folder copy must exclude any nested .git: it would clobber the
    # dest's git metadata and misdirect the push. Inspect the dest tree at `git add` time,
    # before the TemporaryDirectory is cleaned up.
    monkeypatch.setattr(
        ghcli,
        "gh",
        _clone_with_tree(
            {"lectures/00_x/f.txt": "hi", "lectures/00_x/.git/config": "x"}
        ),
    )
    copied_rel: list[str] = []
    monkeypatch.setattr(deploy, "git", _git_spying_staged(copied_rel))
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (0, True)
    assert "lectures/00_x/f.txt" in copied_rel
    assert not any(".git" in rel for rel in copied_rel)


# --------------------------------------------------------- .releaseignore, end to end
#
# The matcher's agreement with git is `tests/test_releaseignore.py`'s job. These are about
# the WIRING: that the release path consults it at all, anchors it at the clone root
# rather than the copied subpath, and refuses a source path that is itself excluded.


def _release(monkeypatch, tree: dict[str, str], deploys, staged: list[str]):
    """One deploy_many run over `tree`, recording what reached the dest at `git add`."""
    _no_io(monkeypatch, _clone_with_tree(tree))
    monkeypatch.setattr(deploy, "git", _git_spying_staged(staged))
    return deploy.deploy_many("Course-Org", "Cohort-Org", deploys, sync=False)


def test_a_whole_repo_release_honours_a_releaseignore(monkeypatch):
    staged: list[str] = []
    errors, changed = _release(
        monkeypatch,
        {
            ".releaseignore": "**/solutions.ipynb\n__pycache__/\n",
            "labs/01.md": "lab one",
            "labs/solutions.ipynb": "THE ANSWERS",
            "labs/__pycache__/x.pyc": "junk",
        },
        [Deploy("cm", "/", "materials", None)],
        staged,
    )
    assert (errors, changed) == (0, True)
    # The ignore file itself is NOT released - students have pull on this repo, and its
    # contents are the list of paths faculty held back (see releaseignore's docstring).
    assert sorted(staged) == ["labs/01.md"]


def test_a_subpath_release_still_obeys_the_ROOT_releaseignore(monkeypatch):
    # The anchor is the CLONE root, not the folder being copied - a root `.releaseignore`
    # reaches a `labs/01` release the way a root `.gitignore` reaches a subdirectory.
    # Getting this wrong is the difference between a rule that works and one that only
    # works for whole-repo releases.
    staged: list[str] = []
    errors, changed = _release(
        monkeypatch,
        {
            ".releaseignore": "solutions.ipynb\n",
            "labs/01/lab.md": "lab",
            "labs/01/solutions.ipynb": "THE ANSWERS",
        },
        [Deploy("cm", "labs/01", "materials", "week01")],
        staged,
    )
    assert (errors, changed) == (0, True)
    assert staged == ["week01/lab.md"]


def test_naming_an_excluded_path_outright_releases_nothing_and_stays_green(
    monkeypatch, capsys
):
    # `git add <ignored-path>` refuses rather than adding the file, so a named path that
    # is excluded is a no-op, not a copy. GREEN, though: the hourly scheduler runs through
    # the same deploy_many, and a permanently red cron is how real failures stop being
    # noticed (see deploy._warn_withheld_stub).
    staged: list[str] = []
    errors, changed = _release(
        monkeypatch,
        {".releaseignore": "solutions/\n", "solutions/01.ipynb": "THE ANSWERS"},
        [Deploy("cm", "solutions", "materials", "week01")],
        staged,
    )
    assert (errors, changed) == (0, False)
    assert staged == []
    err = capsys.readouterr().err
    assert "::warning::" in err
    assert ".releaseignore" in err


def test_an_excluded_single_file_is_refused_too(monkeypatch):
    # The single-file copy is `shutil.copy2`, which has no ignore hook - so the check in
    # front of it is the only thing standing there.
    staged: list[str] = []
    errors, changed = _release(
        monkeypatch,
        {".releaseignore": "*.key\n", "deploy.key": "SECRET"},
        [Deploy("cm", "deploy.key", "materials", "week01/deploy.key")],
        staged,
    )
    assert (errors, changed) == (0, False)
    assert staged == []


def _git_commit_failing(*args):
    """git fake: staged changes present (diff --cached exits 1), but the commit itself
    fails (exit 1) - a real disk/lock/hook failure, distinct from an empty index."""
    if "diff" in args and "--cached" in args:
        return (1, "")
    if "commit" in args:
        return (1, "error: could not write commit - No space left on device")
    return (0, "")


def test_deploy_many_counts_a_real_commit_failure(monkeypatch, capsys):
    # A non-zero commit with staged changes is a real failure (disk/lock/hook), NOT the
    # "nothing new to release" no-op - it must count as an error, not be silently dropped.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(deploy, "git", _git_commit_failing)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (1, False)
    out = capsys.readouterr()
    assert "commit failed" in out.err
    assert "nothing new to release" not in out.out


def test_deploy_many_reports_nothing_new_when_index_is_empty(monkeypatch, capsys):
    # An empty index (diff --cached exits 0) is the genuine idempotent no-op: no error, no
    # commit attempted, "nothing new to release".
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(
        deploy, "git", lambda *a: (0, "")
    )  # diff --cached: nothing staged
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=False,
    )
    assert (errors, changed) == (0, False)
    assert "nothing new to release" in capsys.readouterr().out


def test_deploy_many_counts_a_raised_site_sync(monkeypatch):
    # site.sync_site RAISES on a genuine read failure - deploy_many must catch it, count it,
    # and return non-zero, not let the traceback escape.
    monkeypatch.setattr(ghcli, "gh", _clone_with_tree({"lectures/00_x/f.txt": "x"}))
    monkeypatch.setattr(deploy, "git", _git_with_staged_changes)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)

    def boom(course, cohort):
        raise RuntimeError("tree read failed")

    monkeypatch.setattr("dsl_course.site.sync_site", boom)
    errors, changed = deploy.deploy_many(
        "Course-Org",
        "Cohort-Org",
        [Deploy("cm", "lectures/00_x", "materials", None)],
        sync=True,
    )
    assert errors == 1 and changed is True


# ------------------------------------------------------------- deadline snapshots
# The hourly cron is what makes the grading pin trustworthy: it freezes each assignment's
# commits at a moment the SERVER chose, because committer dates are client-supplied. So the
# trigger condition (deadline passed, not yet frozen) and its write-once-ness are the logic
# that matters here.


def _assignments(**entries: AssignmentEntry) -> Schedule:
    return Schedule(assignments=dict(entries))


def _due(day: int, grading_day: int | None = None) -> AssignmentEntry:
    return AssignmentEntry(
        course_source_repo="a-f2026",
        due_datetime=datetime(2026, 10, day, 23, 59, 59, tzinfo=BERLIN),
        grading_datetime=(
            datetime(2026, 10, grading_day, 23, 59, 59, tzinfo=BERLIN)
            if grading_day is not None
            else None
        ),
    )


def test_due_snapshots_only_passed_deadlines_in_deadline_order():
    sched = _assignments(
        **{
            "assignment-2": _due(20),
            "assignment-1": _due(13),
            "assignment-3": _due(30),
        }
    )
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    assert [slug for slug, _dl in scheduler.due_snapshots("COURSE", sched, now)] == [
        "assignment-1",
        "assignment-2",
    ]


def test_due_snapshots_uses_the_explicit_grading_datetime_when_set():
    # grading_datetime wins over due_datetime, and snapshot + autograde must agree on it.
    sched = _assignments(**{"assignment-1": _due(13, grading_day=15)})
    assert (
        scheduler.due_snapshots(
            "COURSE", sched, datetime(2026, 10, 14, tzinfo=timezone.utc)
        )
        == []
    )
    ((slug, deadline),) = scheduler.due_snapshots(
        "COURSE", sched, datetime(2026, 10, 16, tzinfo=timezone.utc)
    )
    assert slug == "assignment-1"
    assert deadline.startswith("2026-10-15T23:59:59")


def test_due_snapshots_empty_without_assignments():
    assert (
        scheduler.due_snapshots(
            "COURSE", Schedule(), datetime(2030, 1, 1, tzinfo=timezone.utc)
        )
        == []
    )


def _window(monkeypatch, days: int | None) -> None:
    """Declare a late window in the template's grading_config.yml, as a real course does."""
    monkeypatch.setattr(
        scheduler,
        "load_grading_spec",
        lambda org, template: GradingSpec(late_window_days=days),
    )


def test_the_freeze_waits_for_the_end_of_the_declared_late_window(monkeypatch):
    # The sheet's header and every receipt promise work is accepted for seven more days.
    # Freezing at the due date refused all of it - on a green run, with the promise still
    # on the student's issue.
    _window(monkeypatch, 7)
    sched = _assignments(**{"assignment-1": _due(13)})
    assert (
        scheduler.due_snapshots(
            "COURSE", sched, datetime(2026, 10, 15, tzinfo=timezone.utc)
        )
        == []
    )
    ((slug, deadline),) = scheduler.due_snapshots(
        "COURSE", sched, datetime(2026, 10, 21, tzinfo=timezone.utc)
    )
    assert slug == "assignment-1"
    assert deadline.startswith("2026-10-20T23:59:59")


def test_an_explicit_grading_datetime_beats_the_declared_window(monkeypatch):
    _window(monkeypatch, 7)
    sched = _assignments(**{"assignment-1": _due(13, grading_day=15)})
    ((_slug, deadline),) = scheduler.due_snapshots(
        "COURSE", sched, datetime(2026, 10, 16, tzinfo=timezone.utc)
    )
    assert deadline.startswith("2026-10-15T23:59:59")


def test_no_declared_window_freezes_at_the_due_date(monkeypatch):
    _window(monkeypatch, None)
    sched = _assignments(**{"assignment-1": _due(13)})
    ((_slug, deadline),) = scheduler.due_snapshots(
        "COURSE", sched, datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    assert deadline.startswith("2026-10-13T23:59:59")


def test_the_sheet_refresh_runs_through_the_whole_late_window(monkeypatch):
    # Between the due date and the cutoff the assignment is NOT frozen, so the refresh pass
    # owns it - which is what makes a late push move `info:` and earn its receipt.
    _window(monkeypatch, 7)
    sched = _assignments(**{"assignment-1": _due(13)})
    refreshed = _no_sheet_refresh(monkeypatch)
    _window(monkeypatch, 7)  # _no_sheet_refresh re-stubs the spec reader
    monkeypatch.setattr(scheduler, "_assignment_template", lambda *a: "assignment-1")
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: None)
    monkeypatch.setattr(scheduler, "snapshot_assignment", lambda *a, **k: None)
    scheduler._refresh_sheets(
        "COURSE",
        "COHORT",
        sched,
        datetime(2026, 10, 17, tzinfo=timezone.utc),
        False,
    )
    assert refreshed == [("assignment-1", "assignment-1")]


def _stub_autograde(monkeypatch, marked: bool = True):
    """Neutralise the autograde phase's I/O (it shares due_snapshots with the snapshot
    phase). `marked` = every slug already has its autograde/<slug>/ marker, so nothing
    fires."""
    monkeypatch.setattr(scheduler, "has_autograde_results", lambda org, slug: marked)
    monkeypatch.setattr(
        scheduler, "_assignment_template", lambda org, slug, entry: None
    )


def _stub_snapshots(monkeypatch, existing: set[str]):
    """Track snapshot_assignment calls; `existing` are the slugs already frozen."""
    taken: list[tuple[str, str, str, str | None]] = []
    monkeypatch.setattr(
        scheduler, "load_snapshots", lambda org, slug: {} if slug in existing else None
    )
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        # `is_group` is REQUIRED (no default), so a scheduler that stopped passing it fails
        # these tests loudly instead of silently freezing every assignment as individual.
        lambda org, slug, deadline, *, is_group, teams_key=None, tz=None: (
            taken.append((org, slug, deadline, teams_key))
            or scheduler.SnapshotResult.WRITTEN
        ),
    )
    # The snapshot pass resolves group-ness from the template grading_config.yml when
    # the schedule leaves type unset; keep that network-free and individual by default.
    monkeypatch.setattr(
        scheduler, "_assignment_template", lambda org, slug, entry: None
    )
    _stub_autograde(monkeypatch)
    return taken


def test_run_snapshots_a_passed_deadline_that_has_no_snapshot_yet(monkeypatch):
    taken = _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0
    org, slug, deadline, _key = taken[0]
    assert (org, slug) == ("Cohort-Org", "assignment-1")
    assert deadline.startswith("2026-10-13T23:59:59")


def test_the_snapshot_is_named_by_the_cohort_name_and_keyed_by_the_schedule_slug(
    monkeypatch,
):
    # TWO names, and they are not interchangeable. `cohort_dest_repo` makes them differ:
    # the repos (and so the snapshot) are named after the cohort NAME, while teams.csv is
    # keyed on the SCHEDULE SLUG - the Join-team form writes what schedule.yml declares.
    # Freezing a group assignment under the name found no teams and froze nothing at all.
    taken = _stub_snapshots(monkeypatch, existing=set())
    entry = _due(13)
    entry.cohort_dest_repo = "group-project"
    monkeypatch.setattr(
        scheduler.schedule, "load", lambda cohort: _assignments(project=entry)
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0
    org, slug, _deadline, teams_key = taken[0]
    assert (org, slug, teams_key) == ("Cohort-Org", "group-project", "project")


def test_run_never_re_snapshots_an_assignment_already_frozen(monkeypatch):
    # Idempotence is the integrity property: re-freezing hourly would let a late push
    # (backdated) replace the commit that was recorded at the deadline.
    taken = _stub_snapshots(monkeypatch, existing={"assignment-1"})
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-Org", datetime(2026, 12, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert taken == []


def test_run_does_not_snapshot_before_the_deadline_passes(monkeypatch):
    taken = _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-Org", datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert taken == []


def test_run_snapshots_even_with_no_releases(monkeypatch):
    # A cohort can pin due dates without using the auto-release plan at all - the old
    # early-return on `not sched.releases` would have skipped its snapshots forever.
    taken = _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-Org", datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert [slug for _org, slug, _dl, _key in taken] == ["assignment-1"]


def test_run_dry_run_snapshots_nothing(monkeypatch):
    taken = _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now, dry_run=True) == 0
    assert taken == []


def test_run_reports_a_failed_snapshot(monkeypatch):
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: None)
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        lambda org, slug, deadline, *, is_group, teams_key=None, tz=None: (
            scheduler.SnapshotResult.FAILED
        ),
    )
    _stub_autograde(monkeypatch)
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-Org", datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        == 1
    )


# ------------------------------------------------------- fire-once autograding
# Autograding is zero-config: every assignment with a passed grading deadline is graded on
# the next tick, exactly once. The marker is the autograde/<slug>/ results directory - so
# what matters is that a present marker stops the run dead (an hourly re-grade would
# recompute over a marker's hand-edits).


def _stub_source_repos(monkeypatch, answers: dict[str, set[str] | None]):
    """`schedule.source_repo_paths`, the shared "is this source there?" test, answering
    off a dict: a set of paths, `set()` for absent-or-empty, `None` for unreadable."""
    monkeypatch.setattr(
        "dsl_course.schedule.source_repo_paths",
        lambda org, repo: answers.get(repo, set()),
    )


def test_assignment_template_is_the_named_course_source_repo(monkeypatch):
    _stub_source_repos(monkeypatch, {"wk3-regression-f2026": {"README.md"}})
    entry = AssignmentEntry(
        course_source_repo="wk3-regression-f2026",
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
    )
    # the repo is taken from the entry, never derived from the slug - so a slug that looks
    # nothing like its repo resolves exactly as well as one that matches
    assert (
        scheduler._assignment_template("Course-Org", "regression", entry)
        == "wk3-regression-f2026"
    )


def test_a_course_source_repo_that_does_not_exist_says_so(monkeypatch, capsys):
    # The name is required and hand-written, so one that resolves to nothing can only be a
    # typo - and its only other symptom is an assignment that never hands out or grades.
    _stub_source_repos(monkeypatch, {})
    entry = AssignmentEntry(
        course_source_repo="typo-repo",
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
    )
    assert scheduler._assignment_template("Course-Org", "assignment-1", entry) is None
    err = capsys.readouterr().err
    assert "assignments.assignment-1.course_source_repo" in err
    assert "typo-repo" in err and "Course-Org" in err


def test_an_empty_source_repo_gets_the_same_answer_as_source_faults_gives(monkeypatch):
    # `repo_exists` is optimistic and said yes to a repo created but never pushed to, so
    # `source_faults` mailed faculty about a missing source in the same run that this
    # handed out from it. One predicate, one answer.
    _stub_source_repos(monkeypatch, {"empty-f2026": set()})
    entry = AssignmentEntry(
        course_source_repo="empty-f2026",
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
    )
    assert scheduler._assignment_template("Course-Org", "assignment-1", entry) is None


def test_an_unreadable_source_repo_still_hands_out(monkeypatch):
    # `None` is "could not tell" - a 403, a 5xx, a rate limit. `source_faults` passes it
    # over in silence; here it stays optimistic, because refusing to hand out on a
    # transient read failure is worse than trying and failing.
    _stub_source_repos(monkeypatch, {"wk3-f2026": None})
    entry = AssignmentEntry(
        course_source_repo="wk3-f2026",
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
    )
    assert scheduler._assignment_template("Course-Org", "wk3", entry) == "wk3-f2026"


def _stub_collect(monkeypatch, marked: set[str], templates: set[str], rc: int = 0):
    """Record collect() calls. `marked` = slugs whose autograde/<slug>/ already exists;
    `templates` = the template repos that exist in the course org."""
    graded: list[tuple[str, str, str, str, bool, bool, str]] = []
    monkeypatch.setattr(
        scheduler, "has_autograde_results", lambda org, slug: slug in marked
    )
    monkeypatch.setattr(
        scheduler,
        "_assignment_template",
        lambda org, slug, entry: t if (t := f"{slug}-f2026") in templates else None,
    )
    monkeypatch.setattr(
        "dsl_course.scheduler.collect",
        # `scheduled=True` is the cron's contract with collect (an empty target list is a
        # "not yet", not a permanent skip) - a scheduler that stopped passing it fails here.
        lambda m, t, c, deadline=None, group=False, *, scheduled, slug: (
            graded.append((m, t, c, deadline, group, scheduled, slug)) or rc
        ),
    )
    return graded


def _no_sheet_refresh(monkeypatch) -> list[tuple[str, str]]:
    """The grading-sheet pass, stubbed to a recorder of `(slug, cohort name)`.

    It sits between the freeze and the autograde and reads the template's grading_config.yml, so
    every `run()` test would otherwise reach the API for a file it does not care about."""
    refreshed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scheduler,
        "sync_sheet",
        lambda course, cohort, sched, key, slug, template, **kw: (
            refreshed.append((key, slug)) or True
        ),
    )
    return refreshed


def _only_snapshots_taken(monkeypatch):
    """Snapshots always succeed and are never the subject of these tests."""
    _no_sheet_refresh(monkeypatch)
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: {})
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        lambda org, slug, dl, *, is_group, teams_key=None: (
            scheduler.SnapshotResult.WRITTEN
        ),
    )


def test_the_sheet_refresh_runs_from_the_due_date_not_the_cutoff(monkeypatch):
    # Marking starts at the due date, and the first late push lands then too - so the sheet
    # has to be current from then, not from the cutoff a week later.
    refreshed = _no_sheet_refresh(monkeypatch)
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: None)
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        lambda *a, **k: scheduler.SnapshotResult.FAILED,
    )
    monkeypatch.setattr(scheduler, "_assignment_template", lambda org, slug, entry: "t")
    monkeypatch.setattr(scheduler, "has_autograde_results", lambda org, slug: True)
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(
            **{
                "assignment-1": _due(13, grading_day=20),  # due passed, cutoff has not
                "assignment-2": _due(30),  # not due yet
            }
        ),
    )
    scheduler.run(
        "Course-Org", "Cohort-f2026", datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    assert refreshed == [("assignment-1", "assignment-1")]


def test_a_frozen_assignments_sheet_is_left_to_the_cutoff(monkeypatch):
    # `collect` seals the sheet at the cutoff. Refreshing it in the same tick would
    # re-derive facts the freeze has already settled - and would do it FIRST, since this
    # pass runs before the autograde.
    refreshed = _no_sheet_refresh(monkeypatch)
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: {})  # frozen
    monkeypatch.setattr(scheduler, "_assignment_template", lambda org, slug, entry: "t")
    monkeypatch.setattr(scheduler, "has_autograde_results", lambda org, slug: True)
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    scheduler.run(
        "Course-Org", "Cohort-f2026", datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    assert refreshed == []


def test_run_autogrades_a_passed_deadline_with_no_marker(monkeypatch):
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"})
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-f2026", now) == 0
    ((course, template, cohort, deadline, group, scheduled, slug),) = graded
    assert (course, template, cohort) == (
        "Course-Org",
        "assignment-1-f2026",
        "Cohort-f2026",
    )
    # graded at exactly the instant the snapshot froze, and never guessed as a group run
    assert deadline.startswith("2026-10-13T23:59:59") and group is False
    # ... and told WHICH entry it is, so two on one template cannot be confused
    assert slug == "assignment-1"
    assert (
        scheduled is True
    )  # a cron run, so no-targets waits rather than being recorded


def test_run_never_autogrades_twice_the_marker_is_the_state(monkeypatch):
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(
        monkeypatch, marked={"assignment-1"}, templates={"assignment-1-f2026"}
    )
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 12, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert graded == []


def test_run_does_not_autograde_before_the_grading_deadline(monkeypatch):
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"})
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13, grading_day=15)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 10, 14, tzinfo=timezone.utc)
        )
        == 0
    )
    assert graded == []


def test_run_skips_an_assignment_with_no_template_repo(monkeypatch):
    # A due date can be pinned for the website alone, with no template behind it - a skip,
    # never a red run.
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(monkeypatch, marked=set(), templates=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"reading-week": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert graded == []


def test_run_treats_a_non_autogradable_template_as_a_skip(monkeypatch):
    # collect() itself returns 0 for "no solution branch" / `autograde: false` - the
    # scheduler must pass that through as success, not count it as a failed action.
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(
        monkeypatch, marked=set(), templates={"assignment-1-f2026"}, rc=0
    )
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert len(graded) == 1


def test_run_reports_a_failed_autograde(monkeypatch):
    _only_snapshots_taken(monkeypatch)
    _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"}, rc=1)
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        == 1
    )


def test_run_dry_run_autogrades_nothing(monkeypatch):
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"})
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-f2026", now, dry_run=True) == 0
    assert graded == []


def test_run_autogrades_at_the_explicit_grading_deadline(monkeypatch):
    # `grading_datetime` overrides `due_datetime`, and snapshot + autograde must agree on
    # that one instant.
    _only_snapshots_taken(monkeypatch)
    graded = _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"})
    entry = AssignmentEntry(
        course_source_repo="a-f2026",
        due_datetime=datetime(2026, 10, 13, 23, 59, 59, tzinfo=BERLIN),
        grading_datetime=datetime(2026, 10, 15, 23, 59, 59, tzinfo=BERLIN),
    )
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": entry}),
    )
    # past grading_datetime (10-15) but well before what due_datetime alone would imply
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2026, 10, 16, tzinfo=timezone.utc)
        )
        == 0
    )
    assert graded[0][3].startswith("2026-10-15T23:59:59")


def test_main_all_cohorts_with_none_registered_is_a_noop(monkeypatch):
    # A freshly bootstrapped course org runs the hourly cron before any cohort is
    # registered - that gap must be a quiet no-op, not a red run (and a failure
    # email to the bot owner) every hour.
    monkeypatch.setattr(scheduler.discovery, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(
        sys, "argv", ["scheduler", "--course-org", "Course-Org", "--all-cohorts"]
    )
    assert scheduler.main() == 0


def test_scheduler_workflow_quarter_hourly_and_ungated():
    doc = yaml.safe_load(seed.render_scheduler())
    assert doc.get("name") == "Scheduled release"
    # cron trigger present (YAML 1.1: `on:` may parse to True)
    trigger = doc.get("on", doc.get(True))
    assert "schedule" in trigger
    # Four off-peak ticks an hour: a release's `when` is usually a class time, and on
    # `0 * * * *` GitHub delivered 6 of 24 ticks a day, so releases landed hours late.
    assert trigger["schedule"][0]["cron"] == "7,22,37,52 * * * *"
    # deliberately NOT gated by check-team (no actor on a scheduled run)
    assert "check-team" not in doc["jobs"]


def test_handout_releases_synthesised_from_the_assignments_block(monkeypatch):
    # The whole lifecycle lives under assignments.<slug>; a `handout_datetime:` datetime
    # becomes a normal release (its repo named by course_source_repo, as for autograding),
    # so it fires through the same due/idempotency/site-sync machinery.
    monkeypatch.setattr(
        scheduler,
        "_assignment_template",
        lambda org, slug, entry: (
            None if slug == "missing-repo" else entry.course_source_repo
        ),
    )
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="a-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
            ),
            # names a repo that isn't there - skipped, not fatal to its neighbours
            "missing-repo": AssignmentEntry(
                course_source_repo="gone-f2026",
                due_datetime=datetime(2026, 11, 1, 23, 59, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 10, 1, 9, 0, tzinfo=BERLIN),
            ),
            "manual": AssignmentEntry(
                course_source_repo="a-f2026",
                due_datetime=datetime(2026, 12, 1, 23, 59, tzinfo=BERLIN),
            ),
        }
    )
    (r,) = scheduler._handout_releases("Course-Org", "Cohort-f2026", sched, WHEN)
    assert r.label == "assignment-1-handout"
    assert r.assignment == "a-f2026"
    assert r.when == datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN)
    # and it is due like any release once its datetime passes - not a minute before
    assert scheduler.due_releases([r], datetime(2026, 9, 22, 8, 0, tzinfo=BERLIN)) == []
    assert scheduler.due_releases([r], datetime(2026, 9, 22, 10, 0, tzinfo=BERLIN)) == [
        r
    ]


def test_the_solution_rides_on_the_handout_release_once_its_datetime_passes(
    monkeypatch,
):
    # ONE release does both jobs. A second synthesised release would re-fire every tick
    # for the rest of the term, and provision_all's solution pass clones every student
    # repo - so a separate release costs a clone per student per hour, indefinitely.
    monkeypatch.setattr(
        scheduler, "_assignment_template", lambda org, slug, entry: "a-f2026"
    )
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="a-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
                solution_datetime=datetime(2026, 10, 16, 9, 0, tzinfo=BERLIN),
            )
        }
    )

    released = {"yet": False}
    monkeypatch.setattr(
        "dsl_course.scheduler.solution_released", lambda org, slug: released["yet"]
    )

    def one(now):
        (r,) = scheduler._handout_releases("Course-Org", "Cohort-f2026", sched, now)
        return r

    # between handout and solution time: exactly one release, carrying no solution
    before = one(datetime(2026, 9, 23, tzinfo=BERLIN))
    assert before.label == "assignment-1-handout"
    assert before.assignment_solution is False
    assert scheduler.describe(before) == ["assignment a-f2026"]

    # not a minute early
    assert one(datetime(2026, 10, 16, 8, 0, tzinfo=BERLIN)).assignment_solution is False

    # once past it: still ONE release, now carrying the solution
    after = one(datetime(2026, 10, 17, tzinfo=BERLIN))
    assert after.assignment_solution is True
    assert scheduler.describe(after) == ["assignment a-f2026 + model solution"]

    # and ONCE only. due_releases is cumulative, so without the fire-once marker every
    # later tick would re-push - and push_solution clones every student repo.
    released["yet"] = True
    assert one(datetime(2026, 10, 18, tzinfo=BERLIN)).assignment_solution is False
    assert one(datetime(2027, 1, 5, tzinfo=BERLIN)).assignment_solution is False


def test_a_solution_datetime_without_a_handout_never_synthesises_a_release(monkeypatch):
    # There is no release to carry it, which is what makes the rule structural rather than
    # a run-time skip. The parser flags the combination - see test_schedule.py.
    monkeypatch.setattr(
        scheduler, "_assignment_template", lambda org, slug, entry: "a-f2026"
    )
    sched = Schedule(
        assignments={
            "hand-released": AssignmentEntry(
                course_source_repo="a-f2026",
                due_datetime=datetime(2026, 11, 1, 23, 59, tzinfo=BERLIN),
                solution_datetime=datetime(2026, 11, 3, 9, 0, tzinfo=BERLIN),
            )
        }
    )
    assert scheduler._handout_releases("Course-Org", "Cohort-f2026", sched, WHEN) == []


def test_run_re_sorts_handouts_into_the_release_plan(monkeypatch):
    # due_releases documents event_datetime order, and the plan is sorted at parse time -
    # but the synthesised handouts are appended afterwards, so without a re-sort a
    # September handout is processed after a December release.
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, slug: {})
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        lambda org, slug, dl, *, is_group, teams_key=None: (
            scheduler.SnapshotResult.WRITTEN
        ),
    )
    _stub_autograde(monkeypatch)
    monkeypatch.setattr(
        scheduler, "_assignment_template", lambda org, slug, entry: "a-f2026"
    )
    sched = Schedule(
        releases=[_r("december", datetime(2026, 12, 1, 9, 0, tzinfo=BERLIN))],
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="a-f2026",
                due_datetime=datetime(2026, 10, 13, 23, 59, tzinfo=BERLIN),
                handout_datetime=datetime(2026, 9, 22, 9, 0, tzinfo=BERLIN),
            )
        },
    )
    monkeypatch.setattr(scheduler.schedule, "load", lambda cohort: sched)
    ordered = []
    monkeypatch.setattr(
        scheduler,
        "due_releases",
        lambda releases, now: ordered.extend(r.label for r in releases) or [],
    )
    assert (
        scheduler.run(
            "Course-Org", "Cohort-f2026", datetime(2027, 1, 1, tzinfo=timezone.utc)
        )
        == 0
    )
    assert ordered == ["assignment-1-handout", "december"]


def test_release_order_puts_undated_tbc_entries_last():
    dated = _r("dated", datetime(2026, 9, 1, tzinfo=BERLIN))
    tbc = _r("tbc", None)
    assert sorted([tbc, dated], key=scheduler.release_order) == [dated, tbc]


def test_an_unparseable_plan_leaves_the_exit_code_alone_and_keeps_the_digest_open(
    monkeypatch, capsys
):
    # The original incident: an unparseable schedule.yml raised inside schedule.load and
    # killed the hourly tick for the cohort. It must still not RAISE - one cohort's typo
    # cannot be allowed to abort the others under --all-cohorts, which is why load falls
    # back to an empty Schedule.
    #
    # Nor may it red the run. A file faculty have to fix is a CONTENT fault: the exit code
    # spent on it bought 4-8 red runs an hour and a `Scheduled release is failing` issue
    # every six, mailing the maintainer about a bad indent only faculty can correct. The
    # channel is the fault `load` files - which is also what has to keep schedule.yml's
    # digest issue OPEN: an empty fault list closes it, and it used to say "every entry in
    # schedule.yml is now usable" about a file that would not parse.
    from tests.test_schedule import MALFORMED_SCHEDULE

    _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "get_file_content",
        lambda org, repo, path: MALFORMED_SCHEDULE,
    )
    synced: dict = {}
    monkeypatch.setattr(
        scheduler.source_digest,
        "sync",
        lambda *a, **k: synced.update(faults=a[2]) or source_digest.DigestResult(),
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)

    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0

    unreadable, no_archive = synced["faults"]
    assert unreadable.file == "schedule.yml" and "not valid YAML" in unreadable.what
    # A file nobody can parse declares no archive date either, so it earns that advisory
    # in the same digest.
    assert "ever archive it" in no_archive.what
    captured = capsys.readouterr()
    assert "is NOT valid YAML" in captured.err
    assert "0/0 release(s) due" in captured.out


def test_a_dry_run_of_an_unparseable_plan_is_green_too(monkeypatch):
    # The manual dispatch defaults to dry-run, and a preview writes nothing at all - so
    # there is nothing for it to be red about either. What it PRINTS is the report.
    from tests.test_schedule import MALFORMED_SCHEDULE

    _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "get_file_content",
        lambda org, repo, path: MALFORMED_SCHEDULE,
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now, dry_run=True) == 0


def test_dropped_entries_alone_stay_advisory(monkeypatch):
    # A file that PARSES but loses an entry is a different fault: the rest of the plan
    # still runs, so it is logged (loudly, by load) and left advisory as before.
    _stub_snapshots(monkeypatch, existing=set())
    monkeypatch.setattr(
        scheduler.schedule,
        "get_file_content",
        lambda org, repo, path: "releases:\n  lab-1:\n    title: no date at all\n",
    )
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert scheduler.run("Course-Org", "Cohort-Org", now) == 0


# ---------------------------------------------- per-cohort isolation (--all-cohorts)


def test_run_releases_counts_a_raised_site_sync(monkeypatch):
    # site.sync_site RAISES on a genuine tree/team read failure (post-PR2). _run_releases
    # must catch it, count it, and return non-zero - not let the traceback abort the tick
    # (and, under --all-cohorts, every cohort scheduled after it).
    monkeypatch.setattr(
        "dsl_course.scheduler.deploy_many",
        lambda *a, **k: (0, True),  # something changed
    )

    def boom(course, cohort):
        raise RuntimeError("tree read failed")

    monkeypatch.setattr("dsl_course.site.sync_site", boom)
    due = [_r("wk1", WHEN, deploy=[Deploy("cm", "lectures/01", "materials", None)])]
    assert scheduler._run_releases("Course-Org", "Cohort-Org", due, WHEN) == 1


def test_all_cohorts_loop_survives_one_cohorts_raised_failure(monkeypatch, capsys):
    # The lesson PR #151/#146 applied to the nightly refresh: one cohort's raised failure
    # (unreachable API, a blown-up site sync) must not abort the remaining cohorts' work.
    # `_registered_cohorts` filters the registry through `discovery.live_cohorts`, so
    # the registry read itself is what a test replaces.
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A", "Cohort-B"]
    )
    seen: list[str] = []

    def fake_run(
        course, cohort, now, dry_run=False, release=True, autograde=True, verdict=None
    ):
        seen.append(cohort)
        if cohort == "Cohort-A":
            raise RuntimeError("Cohort-A: gh: HTTP 502")
        return 0

    monkeypatch.setattr(scheduler, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["scheduler", "--course-org", "Course-Org", "--all-cohorts"],
    )
    # Cohort-A raises, Cohort-B must still run, and the batch reports failure.
    assert scheduler.main() == 1
    assert seen == ["Cohort-A", "Cohort-B"]
    assert "Cohort-A" in capsys.readouterr().err


# --------------------------------------------------------------- the two phase flags


def _phase_spies(monkeypatch, sched: Schedule):
    """Record which phases a run reaches, with none of their I/O."""
    calls: list[str] = []
    monkeypatch.setattr(scheduler.schedule, "load", lambda cohort: sched)
    monkeypatch.setattr(
        scheduler,
        "_snapshot_passed_deadlines",
        lambda *a: calls.append("snapshot") or 0,
    )
    monkeypatch.setattr(
        scheduler, "_preflight_sources", lambda *a: calls.append("preflight") or 0
    )
    # Not a phase these tests are about, and it reads four files over the API: stubbed
    # silently rather than spied, so the sequence they assert stays the release sequence.
    monkeypatch.setattr(scheduler, "_preflight_configs", lambda *a: 0)
    monkeypatch.setattr(
        scheduler, "_run_releases", lambda *a: calls.append("release") or 0
    )
    monkeypatch.setattr(
        scheduler,
        "_autograde_passed_deadlines",
        lambda *a: calls.append("autograde") or 0,
    )
    return calls


_DUE_RELEASE = Schedule(
    releases=[_r("wk1", WHEN, deploy=[Deploy("cm", "lectures/01", "materials", None)])]
)


def test_skip_autograde_releases_without_grading(monkeypatch):
    # The release job's invocation. Grading is the slow half (two hours, a clone per
    # submission); leaving it in this job is what made a queued release wait on it.
    calls = _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--all-cohorts",
            "--skip-autograde",
            "--now",
            WHEN.isoformat(),
        ],
    )
    assert scheduler.main() == 0
    assert calls == ["snapshot", "preflight", "release"]


def test_autograde_only_grades_without_releasing_anything(monkeypatch):
    # The grading job's invocation, one cohort per matrix leg. It must not re-snapshot
    # (the freeze is deadline-pinned in the release job) and must release nothing.
    calls = _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--cohort-org",
            "Cohort-A",
            "--autograde-only",
            "--now",
            WHEN.isoformat(),
        ],
    )
    assert scheduler.main() == 0
    assert calls == ["autograde"]


def test_autograde_only_waits_for_the_snapshot_file(monkeypatch):
    # The phases run in different jobs, so the handoff is the snapshot FILE. Absent means
    # "not frozen yet": grading then would pin on student-controlled committer dates, or
    # write permanent zeros for a cohort whose repos do not exist yet.
    graded = _stub_collect(monkeypatch, marked=set(), templates={"assignment-1-f2026"})
    monkeypatch.setattr(
        scheduler.schedule,
        "load",
        lambda cohort: _assignments(**{"assignment-1": _due(13)}),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--cohort-org",
            "Cohort-A",
            "--autograde-only",
            "--now",
            "2026-11-01",
        ],
    )
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, name: None)
    assert scheduler.main() == 0
    assert graded == []
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, name: {"anna": "sha"})
    assert scheduler.main() == 0
    assert len(graded) == 1


def test_the_two_phase_flags_are_refused_together(monkeypatch):
    # They ask for opposite halves of the same run, so honouring both would silently do
    # nothing at all - on a green run, four times an hour.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--all-cohorts",
            "--skip-autograde",
            "--autograde-only",
        ],
    )
    assert scheduler.main() == 1


# ------------------------------------------------------------------ the cadence wiring
# The alarms themselves are tests/test_cadence.py's job. These are about WHICH invocations
# are allowed to touch them: a real, whole-course release pass and nothing else, because a
# dry-run preview or a laptop run that armed or closed an alarm would be lying about the
# state of the drivers.


def _all_cohorts_argv(monkeypatch, *extra: str, now: datetime = WHEN) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--all-cohorts",
            "--skip-autograde",
            "--now",
            now.isoformat(),
            *extra,
        ],
    )


def test_the_cadence_is_read_once_per_course_not_once_per_cohort(
    monkeypatch, cadence_calls
):
    # One request for the whole course: the run history it reads is the WORKFLOW's, which is
    # one workflow per course org however many cohorts it serves.
    _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A", "Cohort-B"]
    )
    _all_cohorts_argv(monkeypatch)
    assert scheduler.main() == 0
    assert len(cadence_calls["fetch_runs"]) == 1
    assert len(cadence_calls["evaluate"]) == 1
    assert len(cadence_calls["report_course"]) == 1
    # lateness, though, is per cohort - it is that cohort's plan that was late
    assert [c[1] for c in cadence_calls["report_cohort"]] == ["Cohort-A", "Cohort-B"]


def test_a_dry_run_never_reads_or_writes_the_cadence(monkeypatch, cadence_calls):
    # The manual dispatch button defaults to dry-run, so a curious click must not close a
    # live alarm, arm a disarmed org, or comment on anything.
    _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )
    _all_cohorts_argv(monkeypatch, "--dry-run")
    assert scheduler.main() == 0
    assert cadence_calls["fetch_runs"] == []
    assert cadence_calls["report_course"] == cadence_calls["report_cohort"] == []


def test_the_autograde_job_never_touches_the_cadence(monkeypatch, cadence_calls):
    # Cadence belongs to the release phase: the grading job is one cohort, runs after it,
    # and would measure the gap against the run it is itself part of.
    _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--cohort-org",
            "Cohort-A",
            "--autograde-only",
            "--now",
            WHEN.isoformat(),
        ],
    )
    assert scheduler.main() == 0
    assert cadence_calls["fetch_runs"] == []
    assert cadence_calls["report_course"] == cadence_calls["report_cohort"] == []


def test_a_single_cohort_invocation_never_touches_the_cadence(
    monkeypatch, cadence_calls
):
    # A laptop, or a break-glass run during an Actions outage. It knows nothing about the
    # other cohorts and must not report on the drivers' behalf.
    _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--cohort-org",
            "Cohort-A",
            "--now",
            WHEN.isoformat(),
        ],
    )
    assert scheduler.main() == 0
    assert cadence_calls["fetch_runs"] == []
    assert cadence_calls["report_cohort"] == []


def test_lateness_is_asked_about_the_whole_plan_not_just_what_is_due(
    monkeypatch, cadence_calls
):
    # `late_items` is left real here on purpose: the wiring has to hand it the MERGED plan
    # (`releases:` entries plus the synthesised handouts) and the verdict's previous tick.
    _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )
    late = _verdict(now=WHEN + timedelta(hours=3), prev=WHEN - timedelta(hours=1))
    monkeypatch.setattr(scheduler.cadence, "evaluate", lambda *a: late)
    # An hour of queue between GitHub accepting this run and the runner starting it: the
    # window is measured off the verdict's instants, so the lateness is 3h, not 4.
    _all_cohorts_argv(monkeypatch, now=WHEN + timedelta(hours=4))
    assert scheduler.main() == 0
    (_course, cohort, verdict, items, _dry) = cadence_calls["report_cohort"][0]
    assert cohort == "Cohort-A" and verdict is late
    assert [i.label for i in items] == ["releases.wk1 -> deploy[0]"]
    assert items[0].late == timedelta(hours=3)


def test_a_cadence_read_that_fails_reddens_the_run_and_releases_anyway(
    monkeypatch, cadence_calls
):
    # This is the check that watches the drivers, so its own failure is worth a red X - and
    # worth nothing more: every cohort's release still fires, and nothing is reported off a
    # reading that was never taken.
    calls = _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )

    def boom(org):
        raise RuntimeError("gh: HTTP 502")

    monkeypatch.setattr(scheduler.cadence, "fetch_runs", boom)
    _all_cohorts_argv(monkeypatch)
    assert scheduler.main() == 1
    assert calls == ["snapshot", "preflight", "release"]
    assert cadence_calls["report_course"] == cadence_calls["report_cohort"] == []


def test_a_lateness_check_that_raises_reddens_the_run_and_releases_anyway(
    monkeypatch, cadence_calls
):
    # `late_items` is the one piece of the check that used to be evaluated OUTSIDE the
    # try - as an argument to the reporter - so a raise there escaped the release phase and
    # cost the cohort its releases, which is the one thing the cadence check may never do.
    calls = _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )

    def boom(*a):
        raise RuntimeError("a plan this reader cannot walk")

    monkeypatch.setattr(scheduler.cadence, "late_items", boom)
    _all_cohorts_argv(monkeypatch)
    assert scheduler.main() == 1
    assert calls == ["snapshot", "preflight", "release"]
    assert cadence_calls["report_cohort"] == []


def test_a_cadence_report_that_fails_reddens_the_run_and_releases_anyway(
    monkeypatch, cadence_calls
):
    calls = _phase_spies(monkeypatch, _DUE_RELEASE)
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A"]
    )
    monkeypatch.setattr(scheduler.cadence, "report_cohort", lambda *a: 1)
    monkeypatch.setattr(scheduler.cadence, "report_course", lambda *a: 1)
    _all_cohorts_argv(monkeypatch)
    assert scheduler.main() == 1
    assert calls == ["snapshot", "preflight", "release"]


def test_list_cohorts_prints_json_and_nothing_else(monkeypatch, capsys):
    # It IS the grading matrix: the workflow captures stdout and hands it to fromJSON, so
    # one stray log line on stdout would take grading out for the whole course.
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A", "Cohort-B"]
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["scheduler", "--course-org", "Course-Org", "--list-cohorts"],
    )
    assert scheduler.main() == 0
    assert json.loads(capsys.readouterr().out) == ["Cohort-A", "Cohort-B"]


def test_list_cohorts_keeps_a_retry_notice_off_stdout(monkeypatch, capsys):
    # `ghcli.gh` prints its retry notices to STDOUT before a successful retry. A second
    # line in the captured output writes a step-output line with no `=`, GitHub rejects
    # the whole outputs file, and because the listing runs first the release step is then
    # skipped - one transient rate-limit costing a whole release tick.
    def noisy(org):
        print("  [wait] rate-limited, retry 1/3 in 30s")
        return ["Cohort-A"]

    monkeypatch.setattr(scheduler.discovery, "discover_cohorts", noisy)
    monkeypatch.setattr(
        sys, "argv", ["scheduler", "--course-org", "Course-Org", "--list-cohorts"]
    )
    assert scheduler.main() == 0
    out = capsys.readouterr()
    assert out.out == '["Cohort-A"]\n'
    assert "[wait]" in out.err  # not swallowed - just off the answer's channel


@pytest.mark.parametrize("flag", ["--all-cohorts", "--list-cohorts"])
def test_a_cohort_listing_that_cannot_be_read_says_so_and_goes_red(
    monkeypatch, capsys, flag
):
    # The listing is the first read of every tick and sat outside any try, so one transient
    # fault ended the run in a raw traceback - nothing a faculty member could act on. It is
    # also not "no cohorts": the run must go red so the failure issue still files.
    def boom(org):
        raise RuntimeError("gh: Internal Server Error (HTTP 500)")

    monkeypatch.setattr(scheduler.discovery, "discover_cohorts", boom)
    monkeypatch.setattr(sys, "argv", ["scheduler", "--course-org", "Course-Org", flag])
    assert scheduler.main() == 1
    out = capsys.readouterr()
    assert "[err] could not list cohorts for Course-Org" in out.err
    assert "Traceback" not in out.err
    assert out.out == ""  # never a half-written cohort matrix on stdout


@pytest.mark.parametrize("flag", ["--all-cohorts", "--list-cohorts"])
def test_a_registry_nobody_can_parse_releases_nothing_and_stays_green(
    monkeypatch, capsys, flag
):
    # A malformed registry is a hand-edited file a course admin has to fix, and it was the
    # one content fault still spending an exit code: `Scheduled release is failing` in the
    # course org and a maintainer mail every six hours, about a file the maintainer cannot
    # edit. `_preflight_course` has already put it on the course digest and mailed the
    # admins, so the listing lists nothing and the tick is green.
    def boom(org):
        raise Unusable("malformed cohort registry in Course-Org/.github")

    monkeypatch.setattr(scheduler.discovery, "discover_cohorts", boom)
    monkeypatch.setattr(scheduler, "_preflight_course", lambda *a: 0)
    monkeypatch.setattr(sys, "argv", ["scheduler", "--course-org", "Course-Org", flag])
    assert scheduler.main() == 0
    out = capsys.readouterr()
    assert "malformed cohort registry" in out.err
    assert "this run stays green" in out.err
    if flag == "--list-cohorts":
        assert out.out == "[]\n"  # an empty grading matrix, never a half-written one
    else:
        assert "nothing to release" in out.out


# ----------------------------------------------------- source pre-flight (unattended)


# A cohort's archive date, far enough out that the notice window is not open.
ARCHIVES = date(2027, 2, 16)


def _preflight(monkeypatch, faults, now=WHEN, dry_run=False, digest=None):
    """Drive _preflight_sources with a fixed fault list, capturing every call it makes.

    Routing is stubbed too: it reads blame and people.yml over the API, and what these
    tests are about is the exit code and what the phases are handed."""
    seen: dict = {}
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: faults)
    monkeypatch.setattr(
        scheduler.notify,
        "route",
        lambda *a, **k: seen.update(route=a) or notify.Routing(logins=["JanG"]),
    )

    def _sync(*a, **k):
        seen.update(args=a, kw=k)
        # `sync` decides whether to ask at all; a test that never called the resolver
        # would be asserting the mention of a tick with nothing to say.
        if k.get("resolve_mention"):
            seen["mention"] = k["resolve_mention"]()
        return digest or source_digest.DigestResult()

    monkeypatch.setattr(scheduler.source_digest, "sync", _sync)
    monkeypatch.setattr(
        scheduler.notify,
        "notify_source_transitions",
        lambda *a, **k: seen.update(mailed=a, mail_kw=k) or notify.Unsent(),
    )
    # A cohort that names no archive date earns an advisory of its own, which is not
    # what any of these tests is about - see the archive-phase section.
    rc = scheduler._preflight_sources(
        "Course-Org", "Cohort-Org", Schedule(archive_date=ARCHIVES), now, dry_run
    )
    return rc, seen


def test_a_missed_rung_source_leaves_the_exit_code_alone(monkeypatch):
    # No rung goes red, the top one included. A source nobody staged is faculty's to fix
    # and the digest issue is where they hear about it; spending the exit code on it meant
    # up to eight red runs an hour mailing a bot account about a folder it cannot write.
    missed = source_fault("releases.a", fires=WHEN - timedelta(hours=2))
    critical = source_fault("releases.b", fires=WHEN + timedelta(hours=2))
    distant = source_fault("releases.c", fires=WHEN + timedelta(days=40))
    assert _preflight(monkeypatch, [missed])[0] == 0
    assert _preflight(monkeypatch, [critical])[0] == 0
    assert _preflight(monkeypatch, [distant])[0] == 0
    assert _preflight(monkeypatch, [])[0] == 0


def test_a_mail_that_did_not_go_out_puts_the_digests_record_back(monkeypatch):
    # The digest wrote the new rung before the mail was attempted, so a failed send has to
    # un-record the crossing or the notification is owed once, fails once and is never
    # owed again.
    fault = source_fault("releases.a", fires=WHEN + timedelta(hours=2))
    digest = source_digest.DigestResult(
        faults_by_key={fault.key: fault},
        mail={fault.key: scheduler.schedule.Severity.CRITICAL},
        was={fault.key: "warning"},
    )
    held: dict = {}
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [fault])
    monkeypatch.setattr(scheduler.notify, "route", lambda *a, **k: notify.Routing())
    monkeypatch.setattr(scheduler.source_digest, "sync", lambda *a, **k: digest)
    monkeypatch.setattr(
        scheduler.notify,
        "notify_source_transitions",
        lambda *a, **k: notify.Unsent(1, (fault.key,)),
    )
    monkeypatch.setattr(
        scheduler.source_digest,
        "hold",
        lambda org, keys, clock=None: held.update(org=org, keys=keys, clock=clock) or 0,
    )
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )
    assert held == {"org": "Cohort-Org", "keys": {fault.key: "warning"}, "clock": None}


def test_a_dry_run_holds_nothing(monkeypatch):
    fault = source_fault("releases.a", fires=WHEN + timedelta(hours=2))
    calls: list = []
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [fault])
    monkeypatch.setattr(scheduler.notify, "route", lambda *a, **k: notify.Routing())
    monkeypatch.setattr(
        scheduler.source_digest, "sync", lambda *a, **k: source_digest.DigestResult()
    )
    monkeypatch.setattr(
        scheduler.notify,
        "notify_source_transitions",
        lambda *a, **k: notify.Unsent(1, (fault.key,)),
    )
    monkeypatch.setattr(
        scheduler.source_digest, "hold", lambda *a, **k: calls.append(a) or 0
    )
    scheduler._preflight_sources("Course-Org", "Cohort-Org", Schedule(), WHEN, True)
    assert calls == []


def test_a_digest_error_is_reported_but_never_returned(monkeypatch):
    # The digest counts its own failures; the pre-flight reads them so the run log says so,
    # and still returns 0 - an undelivered notification must not stop a release.
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [])
    monkeypatch.setattr(
        scheduler.source_digest,
        "sync",
        lambda *a, **k: source_digest.DigestResult(errors=1),
    )
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )


def test_preflight_reports_every_fault_however_distant(monkeypatch):
    # Severity gates who is TOLD, not what is listed: the issue body carries the lot, so
    # the advisories are there as context the moment one of them escalates.
    distant = source_fault("releases.b", fires=WHEN + timedelta(days=40))
    _, seen = _preflight(monkeypatch, [distant])
    assert seen["args"][2] == [distant]


def test_a_digest_that_cannot_be_written_never_stops_a_release(monkeypatch):
    # This runs inside the hourly release cron. A notification problem is not worth
    # failing a release run over - the release itself is the job.
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [])

    def boom(*a, **k):
        raise RuntimeError("GitHub is having a day")

    monkeypatch.setattr(scheduler.source_digest, "sync", boom)
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )


def test_a_source_check_that_cannot_run_is_not_read_as_everything_missing(monkeypatch):
    # A rate limit must not be reported as 22 broken entries.
    def boom(sched, org):
        raise RuntimeError("API rate limit exceeded")

    monkeypatch.setattr(scheduler.schedule, "source_faults", boom)
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )


def test_preflight_passes_dry_run_through(monkeypatch):
    _, seen = _preflight(
        monkeypatch, [source_fault("releases.a", fires=WHEN)], dry_run=True
    )
    assert seen["kw"]["dry_run"] is True


def _seed_source(path: Path, readme: str) -> None:
    """A course materials repo: a root README + SYLLABUS, and one session folder."""
    (path / "README.md").write_text(readme)
    (path / "SYLLABUS.md").write_text("# Real syllabus\n")
    d = path / "lectures" / "01_intro"
    d.mkdir(parents=True, exist_ok=True)
    (d / "slides.pdf").write_text("x")


def _run_release(monkeypatch, seed_source, deploys) -> tuple[int, set[str]]:
    """Run a real `deploy_many` and hand back (errors, what-landed-in-the-dest).

    The dest is snapshotted when `git add` runs, not after the call returns: `deploy_many`
    works in a TemporaryDirectory that is already gone by then."""
    landed: set[str] = set()

    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            spec, dest = args[2], args[3]
            path = Path(dest)
            path.mkdir(parents=True, exist_ok=True)
            if spec.startswith("Course-Org/"):
                seed_source(path)
            return (0, "")
        return (0, "")

    def fake_git(*args):
        if "add" in args:
            wd = Path(args[1])
            landed.clear()
            landed.update(
                q.relative_to(wd).as_posix() for q in wd.rglob("*") if q.is_file()
            )
        return _git_with_staged_changes(*args)

    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(deploy, "git", fake_git)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)
    errors, _changed = deploy.deploy_many(
        "Course-Org", "Cohort-Org", deploys, sync=False
    )
    return errors, landed


UNEDITED = (
    "<!-- FACULTY & INSTRUCTORS: replace the content below -->\n\n"
    "> **Replace this placeholder.** This file becomes the students' README.\n\n"
    f"## For faculty & instructors ({course.FACULTY_ONLY_HEADING})\n\n"
)


def test_a_whole_repo_release_withholds_an_unedited_readme(monkeypatch):
    # The incident: a whole-repo release carried the scaffold's faculty-facing README to
    # students as their course overview. Everything else must still ship - and the run stays
    # GREEN, because withholding a placeholder is the guard working, not a fault. The
    # scheduler drives this same deploy_many, so counting it would have reddened the hourly
    # cron forever for any course that never rewrote its README.
    errors, landed = _run_release(
        monkeypatch,
        lambda p: _seed_source(p, UNEDITED),
        [Deploy("cm", "/", "materials", None)],
    )
    assert "README.md" not in landed
    assert {"SYLLABUS.md", "lectures/01_intro/slides.pdf"} <= landed
    assert errors == 0


def test_a_whole_repo_release_ships_a_real_readme(monkeypatch):
    real = "# Foundations of ML\n\nWelcome - slides go up Tuesdays.\n"
    errors, landed = _run_release(
        monkeypatch,
        lambda p: _seed_source(p, real),
        [Deploy("cm", "/", "materials", None)],
    )
    assert "README.md" in landed and errors == 0


def test_naming_an_unedited_readme_directly_withholds_it(monkeypatch):
    # The other way it ships: named outright as the source path.
    errors, landed = _run_release(
        monkeypatch,
        lambda p: _seed_source(p, UNEDITED),
        [Deploy("cm", "README.md", "materials", None)],
    )
    # Named outright, so nothing else was asked for: the copy is simply a no-op.
    assert landed == set() and errors == 0


def test_a_section_release_never_guards_that_sections_own_readme(monkeypatch):
    # Only the repo ROOT holds the stub. A section copy's own README is faculty writing
    # about that section - here carrying the stub's exact TEXT, so only the path can tell
    # them apart - and withholding it would be the guard overreaching.
    def seed(path: Path) -> None:
        (path / "lectures").mkdir(parents=True, exist_ok=True)
        (path / "lectures" / "README.md").write_text(UNEDITED)

    errors, landed = _run_release(
        monkeypatch, seed, [Deploy("cm", "lectures", "materials", None)]
    )
    # Mirrored dest, so it lands at `lectures/README.md` - shipped, and not counted.
    assert landed == {"lectures/README.md"} and errors == 0


def test_withholding_the_stub_never_deletes_the_cohorts_own_readme(monkeypatch):
    # The sequel to the incident: the placeholder leaked, faculty fixed it by editing the
    # README in the COHORT repo (the fastest fix students see), and the course-org source
    # is still the stub. Withholding by deleting after the copy would stage that fix as a
    # deletion on the next whole-repo release - while the log said everything else shipped.
    good = "# Foundations of ML\n\nWritten by faculty, in the cohort repo.\n"
    landed: dict[str, str] = {}

    def fake_gh(*args):
        if args[:2] == ("repo", "clone"):
            spec, dest = args[2], args[3]
            path = Path(dest)
            path.mkdir(parents=True, exist_ok=True)
            if spec.startswith("Course-Org/"):
                _seed_source(path, UNEDITED)
            else:
                (path / "README.md").write_text(good)  # the cohort's existing good one
            return (0, "")
        return (0, "")

    def fake_git(*args):
        if "add" in args:
            wd = Path(args[1])
            landed.clear()
            landed.update(
                {
                    q.relative_to(wd).as_posix(): q.read_text()
                    for q in wd.rglob("*")
                    if q.is_file()
                }
            )
        return _git_with_staged_changes(*args)

    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(deploy, "git", fake_git)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "repo_is_archived", lambda *a, **k: False)
    monkeypatch.setattr(deploy, "allow_forking", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)
    errors, _changed = deploy.deploy_many(
        "Course-Org", "Cohort-Org", [Deploy("cm", "/", "materials", None)], sync=False
    )
    assert errors == 0  # reported, not failed
    assert landed["README.md"] == good  # untouched, not replaced and not removed
    assert "SYLLABUS.md" in landed  # everything else still shipped


def test_withholding_the_stub_is_visible_without_failing_the_run(monkeypatch, capsys):
    # "True, worth seeing, not a failure" - the channel validate-schedule.yml already uses.
    # A green run needs the annotation, or the withholding is invisible on the run summary.
    errors, _landed = _run_release(
        monkeypatch,
        lambda p: _seed_source(p, UNEDITED),
        [Deploy("cm", "/", "materials", None)],
    )
    captured = capsys.readouterr()
    assert errors == 0
    assert "::warning::" in captured.err
    assert "was NOT released" in captured.err
    assert "Write it for students, then release again." in captured.err


def test_the_snapshot_pass_reads_each_passed_deadline_once(monkeypatch):
    # One read per passed deadline: the pass asks whether the snapshot file is there, and
    # freezes the ones it isn't. A failed freeze is counted and writes nothing, so the
    # autograde phase (which asks the same file, in its own job) still refuses it.
    reads: list[str] = []
    monkeypatch.setattr(
        scheduler, "load_snapshots", lambda org, name: reads.append(name) or None
    )
    monkeypatch.setattr(scheduler, "_assignment_template", lambda org, slug, entry: "t")
    monkeypatch.setattr(
        scheduler,
        "snapshot_assignment",
        lambda org, name, deadline, **k: (
            scheduler.SnapshotResult.WRITTEN
            if name != "assignment-2"
            else scheduler.SnapshotResult.FAILED
        ),
    )
    sched = _assignments(
        **{"assignment-1": _due(13), "assignment-2": _due(14)},
    )
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert scheduler._snapshot_passed_deadlines("C", "K", sched, now, False) == 1
    assert reads == ["assignment-1", "assignment-2"], "one read per passed deadline"


def _real_snapshot_then_autograde(monkeypatch, targets):
    """Both deadline phases over the REAL snapshot_assignment, with `targets` as the
    assignment's submission units. Returns the (course_org, template, ...) collect got.

    `load_snapshots` stays None throughout, which is the truth here: nothing was frozen,
    so the file the autograde phase gates on is still absent."""
    graded: list[tuple] = []
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, name: None)
    monkeypatch.setattr(collect_mod, "load_snapshots", lambda org, name: None)
    monkeypatch.setattr(
        collect_mod,
        "submission_targets",
        lambda org, slug, is_group, teams_key=None: targets,
    )
    monkeypatch.setattr(
        collect_mod,
        "_snapshot_sha",
        lambda org, repo, deadline, at="": collect_mod.Pin(absent=True),
    )
    monkeypatch.setattr(collect_mod, "_pushed_at", lambda org: {})

    def no_write(*a, **k):
        raise AssertionError("nothing may be written when there is nothing to freeze")

    monkeypatch.setattr(collect_mod, "put_file", no_write)
    monkeypatch.setattr(scheduler, "_assignment_template", lambda org, slug, entry: "t")
    monkeypatch.setattr(scheduler, "has_autograde_results", lambda org, slug: False)
    monkeypatch.setattr(scheduler, "collect", lambda *a, **k: graded.append(a) or 0)
    sched = _assignments(**{"assignment-1": _due(13)})
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    errors = scheduler._snapshot_passed_deadlines("C", "K", sched, now, False)
    errors += scheduler._autograde_passed_deadlines("C", "K", sched, now, False)
    return errors, graded


def test_a_snapshot_that_froze_nothing_does_not_licence_autograding(monkeypatch):
    # Nobody onboarded yet: snapshot_assignment writes nothing and is green. Reading that
    # as frozen let the tick autograde, and collect then wrote write-once ZEROS for the
    # whole cohort and marked the assignment graded - green, and unrecoverable.
    assert _real_snapshot_then_autograde(monkeypatch, targets=[]) == (0, [])


def test_a_snapshot_whose_repos_are_all_absent_does_not_licence_autograding(
    monkeypatch,
):
    # Same, one step later: the repos are declared but not generated yet (every target 404s).
    assert _real_snapshot_then_autograde(
        monkeypatch, targets=[("assignment-1-anna", "anna", ["anna"])]
    ) == (0, [])


def test_autograde_waits_for_a_completed_snapshot(monkeypatch):
    # Without a snapshot, collect pins on committer dates - and when no submission repo
    # exists at all it records a permanent write-once ZERO for every student and marks the
    # assignment graded, on a green run. A missing snapshot means "not now", never "grade".
    graded = []
    monkeypatch.setattr(scheduler, "has_autograde_results", lambda org, slug: False)
    monkeypatch.setattr(scheduler, "_assignment_template", lambda org, slug, entry: "t")
    monkeypatch.setattr(scheduler, "collect", lambda *a, **k: graded.append(a) or 0)
    sched = _assignments(**{"assignment-1": _due(13)})
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    # The gate is the DURABLE marker - the snapshot file - not what the release phase
    # froze a moment ago: this phase is its own job, in its own process.
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, name: None)
    assert scheduler._autograde_passed_deadlines("C", "K", sched, now, False) == 0
    assert graded == []
    monkeypatch.setattr(scheduler, "load_snapshots", lambda org, name: {"anna": "sha"})
    assert scheduler._autograde_passed_deadlines("C", "K", sched, now, False) == 0
    assert len(graded) == 1


def test_who_to_tell_is_asked_once_and_handed_to_both_channels(monkeypatch):
    # The digest @mentions them and the mail is addressed to them. Asking git twice would
    # be two API reads and two chances for the issue and the email to disagree.
    fault = source_fault("releases.a", fires=WHEN + timedelta(hours=3))
    digest = source_digest.DigestResult(faults_by_key={fault.key: fault})
    _, seen = _preflight(monkeypatch, [fault], digest=digest)
    assert seen["route"][:2] == ("Cohort-Org", "Course-Org")
    assert seen["mention"] == ["JanG"]
    assert seen["mailed"][2] is digest
    assert seen["mailed"][4].logins == ["JanG"]


def test_every_phase_reads_the_clock_in_the_cohorts_own_zone(monkeypatch):
    # The tick is UTC; the deadline faculty wrote and the window where mail is held are
    # both local. A quiet-hours decision made in UTC holds the wrong five hours.
    sched = Schedule(timezone="Australia/Sydney")
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda s, org: [])
    seen: dict = {}
    monkeypatch.setattr(scheduler.notify, "route", lambda *a, **k: notify.Routing())
    monkeypatch.setattr(
        scheduler.source_digest,
        "sync",
        lambda *a, **k: seen.update(now=a[3]) or source_digest.DigestResult(),
    )
    monkeypatch.setattr(
        scheduler.notify, "notify_source_transitions", lambda *a, **k: notify.Unsent()
    )
    scheduler._preflight_sources("Course-Org", "Cohort-Org", sched, WHEN, False)
    assert str(seen["now"].tzinfo) == "Australia/Sydney"
    assert seen["now"] == WHEN  # the same instant, told differently


def test_a_notifier_that_raised_never_changes_the_exit_code(monkeypatch, capsys):
    # Same contract as the digest: this runs inside the release cron, and an undelivered
    # notification is not worth a release.
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [])
    monkeypatch.setattr(scheduler.notify, "route", lambda *a, **k: notify.Routing())
    monkeypatch.setattr(
        scheduler.source_digest, "sync", lambda *a, **k: source_digest.DigestResult()
    )

    def boom(*a, **k):
        raise RuntimeError("Graph is having a day")

    monkeypatch.setattr(scheduler.notify, "notify_source_transitions", boom)
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )
    assert "could not mail" in capsys.readouterr().err


def test_a_routing_that_raised_still_lets_the_digest_speak(monkeypatch, capsys):
    # Blame is a nicety - it decides WHO hears. Losing it must not lose the issue, which
    # falls back to @mentioning the cohort's instructors team.
    monkeypatch.setattr(scheduler.schedule, "source_faults", lambda sched, org: [])
    seen: dict = {}
    monkeypatch.setattr(
        scheduler.notify, "notify_source_transitions", lambda *a, **k: notify.Unsent()
    )
    monkeypatch.setattr(
        scheduler.source_digest,
        "sync",
        lambda *a, **k: seen.update(kw=k) or source_digest.DigestResult(),
    )

    def boom(*a, **k):
        raise RuntimeError("GraphQL is having a day")

    monkeypatch.setattr(scheduler.notify, "route", boom)
    assert (
        scheduler._preflight_sources(
            "Course-Org", "Cohort-Org", Schedule(), WHEN, False
        )
        == 0
    )
    assert seen["kw"]["resolve_mention"]() == []
    assert "could not work out who to tell" in capsys.readouterr().err


# ------------------------------------------------ config pre-flight (unattended)
#
# The hourly floor under the push fast path: every hand-edited file in classroom-config,
# one digest issue each. What matters is that it cannot red the run, that one unreadable
# file does not stop the next, and that "we could not look" never reports a file as fixed.


def _config_preflight(
    monkeypatch,
    *,
    roster_faults=None,
    roster_raises=None,
    roster_absent=False,
    sheet_faults=None,
    spec_faults=None,
    vetted_against=None,
):
    """Drive `_preflight_configs` with every reader stubbed, capturing what each digest
    was handed.

    `vetted_against` is an out-parameter: a one-item list that ends up holding the
    allowlist teams.csv was vetted against, which is how the roster's own state reaches
    the teams check."""
    synced: dict = {}
    mailed: list = []

    def _roster(cohort, found=None):
        if found is not None:
            found.extend(roster_faults or [])
            # What the real loader records for an absent file, so the digest sees the
            # fault AND the caller sees the None that says "there was no allowlist".
            if roster_absent:
                found.append(
                    ConfigFault(
                        "students.csv", "this file is missing", file="students.csv"
                    )
                )
        if roster_raises:
            raise roster_raises
        return None if roster_absent else []

    def _teams(cohort, found=None, known=None):
        if vetted_against is not None:
            vetted_against.append(known)
        return {}

    monkeypatch.setattr(scheduler.roster, "load", _roster)
    monkeypatch.setattr(
        scheduler.sync_faculty, "read_cohort_people", lambda cohort, found: {}
    )
    monkeypatch.setattr(scheduler.teams, "load", _teams)
    monkeypatch.setattr(
        scheduler,
        "cohort_sheet_faults",
        lambda course, cohort, sched, found: found.extend(sheet_faults or []),
    )
    monkeypatch.setattr(
        scheduler,
        "grading_config_faults",
        lambda course, cohort, sched, found: found.extend(spec_faults or []),
    )
    monkeypatch.setattr(
        scheduler.config_digest,
        "sync",
        lambda spec, *a, **k: (
            synced.update({spec.file: a[2]}) or config_digest.DigestResult()
        ),
    )
    monkeypatch.setattr(
        scheduler.notify,
        "notify_config_faults",
        lambda spec, *a, **k: mailed.append(spec.file) or notify.Unsent(),
    )
    rc = scheduler._preflight_configs(
        "Course-Org", "Cohort-Org", Schedule(), WHEN, False
    )
    return rc, synced, mailed


def _csv_fault():
    return ConfigFault(
        "row 4", "unrecognised role", file="students.csv", field="role", lineno=4
    )


def test_the_config_preflight_checks_every_hand_edited_file(monkeypatch):
    # schedule.yml is not here: everything wrong with it belongs in the one issue the
    # source pre-flight keeps, and the plan this tick is running is already parsed.
    rc, synced, mailed = _config_preflight(monkeypatch)
    assert rc == 0
    everything = {
        "people.yml",
        "students.csv",
        "teams.csv",
        "grading_sheets/",
        "grading_config.yml",
    }
    assert set(synced) == everything
    assert sorted(mailed) == sorted(everything)


def test_a_content_fault_reaches_that_files_digest_and_nobody_elses(monkeypatch):
    fault = _csv_fault()
    _rc, synced, _mailed = _config_preflight(monkeypatch, roster_faults=[fault])
    assert synced["students.csv"] == [fault]
    assert synced["teams.csv"] == []


def test_a_file_that_could_not_be_read_is_left_exactly_as_it_was(monkeypatch):
    # Syncing an empty list CLOSES the issue and tells the cohort the file is fine. A rate
    # limit is not that, so the file drops out of the tick.
    _rc, synced, mailed = _config_preflight(
        monkeypatch, roster_raises=RuntimeError("rate limited")
    )
    assert "students.csv" not in synced and "students.csv" not in mailed
    assert "people.yml" in synced  # the others are still checked


def test_an_unreadable_header_is_a_content_fault_not_a_read_failure(monkeypatch):
    # `roster.parse` records the fault and then raises, because every consumer skips the
    # file on that. Faults in hand mean the file WAS read.
    fault = ConfigFault(
        "header", "header lacks hertie_email", file="students.csv", lineno=1
    )
    _rc, synced, _mailed = _config_preflight(
        monkeypatch, roster_faults=[fault], roster_raises=RuntimeError("header")
    )
    assert synced["students.csv"] == [fault]


def test_an_absent_roster_is_a_fault_of_its_own_not_a_healthy_empty_one(monkeypatch):
    # An empty fault list CLOSES the digest issue and tells the cohort students.csv is
    # fine. A cohort with no students.csv at all enrols nobody, which is the loudest thing
    # this file can be wrong about.
    _rc, synced, _mailed = _config_preflight(monkeypatch, roster_absent=True)
    (fault,) = synced["students.csv"]
    assert fault.file == "students.csv" and "missing" in fault.what


def test_an_absent_roster_does_not_report_every_teams_row_as_a_stranger(monkeypatch):
    # The roster is the allowlist teams.csv is vetted against. Absent, it implies the
    # EMPTY allowlist - and vetting against that files one "not an onboarded handle"
    # fault per row, on top of the one fault that is actually true.
    seen: list = []
    _config_preflight(monkeypatch, roster_absent=True, vetted_against=seen)
    assert seen == [None]


def test_a_roster_that_was_read_still_vets_teams_against_it(monkeypatch):
    seen: list = []
    _config_preflight(monkeypatch, vetted_against=seen)
    assert seen == [set()]


def test_a_digest_that_cannot_be_written_never_touches_the_exit_code(monkeypatch):
    # A release is the job. An unreachable issue tracker is not a reason to stop shipping
    # one, and the whole point of this pre-flight is that a content fault never reds a run.
    def boom(*a, **k):
        raise RuntimeError("the digest is unreachable")

    monkeypatch.setattr(
        scheduler, "_config_faults", lambda *a: {config_digest.ROSTER: [_csv_fault()]}
    )
    monkeypatch.setattr(scheduler.config_digest, "sync", boom)
    rc = scheduler._preflight_configs(
        "Course-Org", "Cohort-Org", Schedule(), WHEN, False
    )
    assert rc == 0


# ---------------------------------------------- the COURSE org's own config (unattended)
#
# dsl-course.yml and the cohort registry belong to the course, not to a cohort: one issue
# for the whole course, once per tick, addressed to the admins rather than to a teaching
# team. What matters is that it never reds the tick, that it is reported BEFORE the cohort
# listing a malformed registry makes raise, and that "we could not look" leaves the issue
# alone.


def _course_preflight(
    monkeypatch, *, config_faults=None, registry_faults=None, raises=None
):
    """Drive `_preflight_course` with both readers stubbed, capturing what the digest and
    the mail were handed."""
    seen: dict = {}

    def _config(org, found):
        if raises:
            raise raises
        found.extend(config_faults or [])
        return {"course_admins": [{"github_handle": "jan-g"}]}

    monkeypatch.setattr(scheduler.sync_faculty, "read_course_config", _config)
    monkeypatch.setattr(
        scheduler.discovery,
        "read_cohort_registry",
        lambda org, found: found.extend(registry_faults or []) or [],
    )
    monkeypatch.setattr(
        scheduler.config_digest,
        "sync",
        lambda spec, *a, **k: (
            seen.update(spec=spec, faults=a[2], mention=k["resolve_mention"]())
            or config_digest.DigestResult()
        ),
    )
    monkeypatch.setattr(
        scheduler.notify,
        "notify_config_faults",
        lambda spec, *a, **k: seen.update(mailed=spec) or notify.Unsent(),
    )
    monkeypatch.setattr(
        scheduler.notify, "route_course", lambda *a: notify.Routing({}, ["JanG"])
    )
    rc = scheduler._preflight_course("Course-Org", WHEN, False)
    return rc, seen


def _course_fault(file: str = "dsl-course.yml"):
    return ConfigFault(
        file, "this file is not valid YAML", file=file, field="", in_repo=".github"
    )


def test_both_course_files_reach_the_one_course_digest(monkeypatch):
    registry = _course_fault("cohort-courses-pages.yml")
    config = _course_fault()
    rc, seen = _course_preflight(
        monkeypatch, config_faults=[config], registry_faults=[registry]
    )
    assert rc == 0
    assert seen["spec"] is config_digest.COURSE
    assert seen["faults"] == [config, registry]
    assert seen["mailed"] is config_digest.COURSE
    # Course admins, not a cohort's teaching team - and git's answer on the @mention.
    assert seen["mention"] == ["JanG"]


def test_a_clean_course_config_still_syncs_so_the_issue_can_close(monkeypatch):
    _rc, seen = _course_preflight(monkeypatch)
    assert seen["faults"] == []


def test_a_course_config_that_could_not_be_read_is_left_exactly_as_it_was(
    monkeypatch, capsys
):
    # Syncing an empty list CLOSES the issue and tells the course its config is fine. A
    # rate limit is not that.
    rc, seen = _course_preflight(monkeypatch, raises=RuntimeError("rate limited"))
    assert rc == 0 and "spec" not in seen
    assert "could not read Course-Org's course config" in capsys.readouterr().err


def test_the_course_preflight_never_touches_the_exit_code(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the digest is unreachable")

    monkeypatch.setattr(
        scheduler, "_course_faults", lambda org: ([_course_fault()], set())
    )
    monkeypatch.setattr(scheduler.config_digest, "sync", boom)
    assert scheduler._preflight_course("Course-Org", WHEN, False) == 0


def test_the_course_config_is_checked_before_the_cohort_listing(monkeypatch):
    # A registry nobody can parse is one of the faults this reports AND what makes the
    # listing raise. Reported first, or never.
    order: list[str] = []
    monkeypatch.setattr(
        scheduler, "_preflight_course", lambda *a: order.append("preflight") or 0
    )

    # None is what an unreadable listing looks like, which is exactly this case.
    monkeypatch.setattr(
        scheduler, "_registered_cohorts", lambda org: order.append("listing")
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--all-cohorts",
            "--skip-autograde",
        ],
    )
    assert scheduler.main() == 1
    assert order == ["preflight", "listing"]


def test_the_grading_matrix_leg_is_not_the_courses_own_tick(monkeypatch):
    # One issue per COURSE per tick. The autograde job runs once per cohort, and each leg
    # opening (or commenting on) the course's digest would be one notification per cohort
    # about one file.
    called: list = []
    monkeypatch.setattr(
        scheduler, "_preflight_course", lambda *a: called.append(a) or 0
    )
    monkeypatch.setattr(scheduler, "_registered_cohorts", lambda org: ["Cohort-f2026"])
    monkeypatch.setattr(scheduler, "run", lambda *a, **k: 0)
    monkeypatch.setattr(
        "sys.argv",
        [
            "scheduler",
            "--course-org",
            "Course-Org",
            "--all-cohorts",
            "--autograde-only",
        ],
    )
    assert scheduler.main() == 0
    assert called == []


def test_the_sync_membership_fast_path_checks_and_exits_green(monkeypatch):
    # What a push to dsl-course.yml or the registry runs. Never red: a config faculty have
    # to fix is a content fault, and Sync membership must not go red for one.
    called: list = []
    monkeypatch.setattr(
        scheduler, "_preflight_course", lambda *a: called.append(a) or 0
    )
    monkeypatch.setattr(
        "sys.argv",
        ["scheduler", "--course-org", "Course-Org", "--check-course-config"],
    )
    assert scheduler.main() == 0
    assert [a[0] for a in called] == ["Course-Org"]


# ------------------------------------------------------ a cohort that is closed out


def test_a_closed_out_cohort_is_not_released_into(monkeypatch, capsys):
    # Its whole org is read-only, so every release, snapshot and digest write this tick
    # would 403 - four times an hour, for the rest of the course's life. The live cohort
    # beside it still ticks.
    monkeypatch.setattr(
        scheduler.discovery, "discover_cohorts", lambda org: ["Cohort-A", "Cohort-B"]
    )
    monkeypatch.setattr(
        scheduler.discovery, "repo_is_archived", lambda org, name: org == "Cohort-A"
    )
    seen: list[str] = []
    monkeypatch.setattr(
        scheduler, "run", lambda course, cohort, now, **k: seen.append(cohort) or 0
    )
    monkeypatch.setattr(
        "sys.argv", ["scheduler", "--course-org", "Course-Org", "--all-cohorts"]
    )
    assert scheduler.main() == 0
    assert seen == ["Cohort-B"]
    assert "archived cohort - left frozen" in capsys.readouterr().out


def test_the_break_glass_single_cohort_run_skips_a_closed_out_cohort(monkeypatch):
    # `--cohort-org` is the by-hand path, and it takes the same answer as the loop: a
    # frozen cohort has nothing to release and the tick would be spent on 403s.
    monkeypatch.setattr(scheduler.discovery, "repo_is_archived", lambda org, name: True)

    def boom(*a, **k):
        raise AssertionError("a frozen cohort must not be run")

    monkeypatch.setattr(scheduler, "run", boom)
    monkeypatch.setattr(
        "sys.argv",
        ["scheduler", "--course-org", "Course-Org", "--cohort-org", "Cohort-A"],
    )
    assert scheduler.main() == 0


# --------------------------------------------------------------- the archive phase


def _archive(
    monkeypatch,
    archive_date,
    now,
    dry_run=False,
    existing=None,
    mails=True,
    open_notices=(),
):
    """Drive `_archive_phase` alone, recording the close-out, the issue, the mail and any
    stale notice it closed."""
    seen: dict = {"closed": [], "issues": [], "mailed": [], "notices_closed": []}
    monkeypatch.setattr(scheduler.issues, "open_titles", lambda repo: set(open_notices))
    monkeypatch.setattr(
        scheduler.issues,
        "close_issues_titled",
        lambda repo, title, comment=None: (
            seen["notices_closed"].append((title, comment)) or 0
        ),
    )
    monkeypatch.setattr(
        scheduler.teardown,
        "close_out",
        lambda course, cohort, dry_run=True: seen["closed"].append(cohort) or 0,
    )
    monkeypatch.setattr(scheduler.issues, "find_issue", lambda repo, title: existing)
    monkeypatch.setattr(
        scheduler.issues,
        "upsert_issue",
        lambda repo, title, body, **k: (
            seen["issues"].append((title, body)) or issues_mod.Upserted(0)
        ),
    )
    monkeypatch.setattr(
        scheduler.notify,
        "notify_cohort_archiving",
        lambda cohort, course, when, at: seen["mailed"].append(cohort) or mails,
    )
    rc = scheduler._archive_phase(
        "Course-Org",
        "Cohort-Org",
        Schedule(archive_date=archive_date),
        now,
        dry_run,
    )
    return rc, seen


def test_a_cohort_is_closed_out_on_its_archive_date(monkeypatch):
    when = date(2027, 2, 16)
    _, seen = _archive(monkeypatch, when, datetime(2027, 2, 16, 6, tzinfo=timezone.utc))
    assert seen["closed"] == ["Cohort-Org"]


def test_a_cohort_is_not_closed_out_the_day_before(monkeypatch):
    when = date(2027, 2, 16)
    _, seen = _archive(
        monkeypatch, when, datetime(2027, 2, 15, 23, tzinfo=timezone.utc)
    )
    assert seen["closed"] == []


def test_a_cohort_with_no_archive_date_is_never_closed_out(monkeypatch):
    # Freezing a whole org off a synthesised term end is the worst possible use of a
    # guess: such a cohort is closed out by hand, with --force.
    _, seen = _archive(monkeypatch, None, datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert (seen["closed"], seen["issues"], seen["mailed"]) == ([], [], [])


def test_a_fortnight_out_the_cohort_is_told_once(monkeypatch):
    when = date(2027, 2, 16)
    edge = datetime(2027, 2, 2, 9, tzinfo=timezone.utc)  # exactly 14 days
    rc, seen = _archive(monkeypatch, when, edge)
    assert rc == 0
    assert seen["mailed"] == ["Cohort-Org"]
    ((title, body),) = seen["issues"]
    assert title == "Cohort archives on 2027-02-16"
    assert scheduler._MAILED_MARK in body
    assert "read-only" not in title  # the date is the whole identity of this issue
    assert seen["closed"] == []


def test_the_notice_is_quiet_the_day_before_its_window_opens(monkeypatch):
    when = date(2027, 2, 16)
    _, seen = _archive(monkeypatch, when, datetime(2027, 2, 1, 9, tzinfo=timezone.utc))
    assert (seen["issues"], seen["mailed"]) == ([], [])


def test_the_mail_goes_once_however_many_ticks_the_fortnight_has(monkeypatch):
    # Four ticks an hour for a fortnight is about 1,300 chances to say it again. The issue
    # body is what records that it went.
    when = date(2027, 2, 16)
    _, seen = _archive(
        monkeypatch,
        when,
        datetime(2027, 2, 5, 9, tzinfo=timezone.utc),
        existing=issues_mod.Issue(7, scheduler._MAILED_MARK),
    )
    assert seen["mailed"] == []


def test_a_notice_whose_mail_never_went_offers_it_again(monkeypatch):
    # An org with no address in people.yml, or with no mail transport wired up, gets a
    # `False` - and that must NOT be recorded as "mailed", or the tick after somebody
    # fixes the transport would read the mark and stay quiet for the rest of the
    # fortnight. The issue is filed either way; only the mark waits for a real send.
    when = date(2027, 2, 16)
    _, seen = _archive(
        monkeypatch,
        when,
        datetime(2027, 2, 5, 9, tzinfo=timezone.utc),
        mails=False,
    )
    ((_, body),) = seen["issues"]
    assert scheduler._MAILED_MARK not in body
    assert "No email went with this notice" in body


def test_an_unchanged_notice_is_not_rewritten_every_tick(monkeypatch):
    # `upsert_issue` edits unconditionally, and this body changes exactly once in the
    # fortnight. Four ticks an hour for a fortnight is ~1,300 identical edits otherwise.
    when = date(2027, 2, 16)
    standing = scheduler._archive_notice_body("Cohort-Org", when, True)
    _, seen = _archive(
        monkeypatch,
        when,
        datetime(2027, 2, 5, 9, tzinfo=timezone.utc),
        existing=issues_mod.Issue(7, standing),
    )
    assert (seen["issues"], seen["mailed"]) == ([], [])


def test_a_dry_run_neither_freezes_nor_notifies(monkeypatch):
    when = date(2027, 2, 16)
    _, seen = _archive(
        monkeypatch, when, datetime(2027, 2, 16, tzinfo=timezone.utc), dry_run=True
    )
    assert (seen["closed"], seen["issues"], seen["mailed"]) == ([], [], [])


def test_a_cohort_with_no_archive_date_earns_an_advisory_in_its_own_digest(monkeypatch):
    # A fault with no clock: there is no moment it bites at, which is exactly what is
    # wrong with it. Not a parser drop, so `Validate schedule` stays green in August.
    (fault,) = scheduler._no_archive_date(Schedule())
    assert "writes no `archive:` block" in fault.what
    assert "ever archive it" in fault.what
    assert fault.fires is None and fault.file == "schedule.yml"
    # And it stays a LINE. An undated fault sits at the notify bar by default, so without
    # the cap this would be mailed to the teaching team and re-mailed by the digest's age
    # ladder every term, about a cohort whose term end nobody has typed yet.
    assert fault.severity(WHEN) < faults_mod.NOTIFY_FROM
    assert scheduler._no_archive_date(Schedule(archive_date=ARCHIVES)) == []


def test_a_block_no_date_can_be_derived_from_says_that_instead(monkeypatch):
    # Not writing the block is a decision; writing one nothing can date is a mistake, and
    # telling a cohort to write a block it already wrote helps nobody.
    (fault,) = scheduler._no_archive_date(Schedule(archive_declared=True))
    assert "archive date cannot be derived" in fault.what
    assert fault.severity(WHEN) < faults_mod.NOTIFY_FROM


def test_both_notice_surfaces_end_on_the_same_instruction(monkeypatch):
    # Moving or removing the date is the only thing a reader can DO about the notice, so
    # the issue and the mail beside it say it in the same words - and say only that,
    # rather than teaching the block's defaults to somebody holding a date.
    body = scheduler._archive_notice_body("Cohort-Org", date(2027, 2, 16), True)
    assert body.count("To move this archiving date or remove it altogether, edit ") == 1
    assert "`schedule.yml` in this repo." in body
    assert "sixty days" not in body


def test_a_notice_is_closed_when_the_archive_is_called_off(monkeypatch):
    # The `archive:` block was taken away after the notice went out. Nothing will freeze
    # this cohort now, and nothing else would ever close the issue: a cohort that is never
    # archived is never sealed, so `teardown._close_notices` never runs on it. Until this
    # swept, it stood open all term naming a date on which nothing would happen.
    stale = "Cohort archives on 2027-02-16"
    rc, seen = _archive(
        monkeypatch,
        None,
        datetime(2027, 2, 2, 9, tzinfo=timezone.utc),
        open_notices=(stale, "Roster digest"),
    )
    assert rc == 0
    ((title, comment),) = seen["notices_closed"]
    # Closed with a comment: closing it silently would read as "this happened".
    assert title == stale
    assert "taken away" in comment and "Nothing was frozen" in comment
    # And nothing was frozen, mailed or re-opened on the way.
    assert (seen["closed"], seen["issues"], seen["mailed"]) == ([], [], [])
    # Idempotent: the tick after it finds nothing open and writes nothing.
    _, again = _archive(monkeypatch, None, datetime(2027, 2, 3, 9, tzinfo=timezone.utc))
    assert again["notices_closed"] == []


def test_a_notice_that_is_still_due_is_left_alone(monkeypatch):
    # The one notice the schedule still names is the one that must survive the sweep,
    # every tick of the fortnight.
    when = date(2027, 2, 16)
    due = scheduler.teardown.archive_notice_title(when)
    _, seen = _archive(
        monkeypatch,
        when,
        datetime(2027, 2, 2, 9, tzinfo=timezone.utc),
        open_notices=(due,),
    )
    assert seen["notices_closed"] == []
    assert [title for title, _ in seen["issues"]] == [due]


def test_a_notice_whose_date_moved_out_of_the_window_goes_with_it(monkeypatch):
    # Pushed the date back a month: the notice for the old one is a false date, and the
    # phase now returns before ever opening a new one.
    _, seen = _archive(
        monkeypatch,
        date(2027, 3, 16),
        datetime(2027, 2, 2, 9, tzinfo=timezone.utc),
        open_notices=("Cohort archives on 2027-02-16",),
    )
    assert [title for title, _ in seen["notices_closed"]] == [
        "Cohort archives on 2027-02-16"
    ]
    assert seen["issues"] == []
