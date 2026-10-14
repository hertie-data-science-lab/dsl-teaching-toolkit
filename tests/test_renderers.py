"""The workflow renderers must emit GitHub-parseable YAML with the right inputs/jobs.

A typo in any of these silently breaks a faculty button for every course, so the cheapest
useful guard is: render -> yaml.safe_load -> assert the contract. No network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import workflow_inputs, workflow_jobs

from dsl_course import (
    course,
    mailer,
    profile_readme,
    seed,
    welcome,
    workflows_place,
    workflows_render,
)
from dsl_course.ops.registry import REGISTRY, Request

ROOT = Path(__file__).resolve().parents[1]

# Every workflow renderer, rendered -> a "it parses, and it's gated" sweep. Completeness
# is enforced by test_every_renderer_is_covered_by_the_yaml_sweep below, so a new button
# cannot ship without passing through yaml.safe_load.
ALL_RENDERED = {
    "collect_submissions": workflows_render.render_collect_submissions(
        ["Semester-f2026"], ["assignment-1-f2026"]
    ),
    "sync_membership": workflows_render.render_sync_membership(),
    "send_codes": workflows_render.render_send_codes(),
    "distribute_grades": workflows_render.render_distribute_grades(["Semester-f2026"]),
    "refresh": workflows_render.render_refresh(),
    "sync_site": workflows_render.render_sync_site(),
    "publish_site": workflows_render.render_publish_site(),
    "scheduler": workflows_render.render_scheduler(),
    "console": workflows_render.render_console(),
}

# The renderers with no check-team gate: neither a cron run nor a repository_dispatch has
# an actor to check, and each job only re-calls idempotent functions (the scheduler's
# releases, refresh's re-seeding, the codes send's `code_sent_at` idempotence).
UNGATED = {"scheduler", "refresh", "send_codes"}

# Gated by a check-team STEP rather than a job: the Console is one job, so the run it
# dispatches has one job to follow.
STEP_GATED = {"console"}

# The seeded crons. Nobody watches them, and GitHub emails a scheduled-run failure only to
# whoever last committed the cron file - the bot - so each has to report itself.
CRONS = {"sync_membership", "sync_site", "refresh", "publish_site", "scheduler"}

# Everything that runs with nobody watching: the crons, plus the codes send, which has no
# button and no actor at all - a push to a semester's students.csv fires it. The failure
# contract is the same for all of them and the reason is the same, so it is asserted over
# the whole set rather than over the ones that happen to declare a `schedule:`.
UNATTENDED = CRONS | {"send_codes"}

# The CLI entrypoints that drive `git` against a REMOTE - clone over HTTPS, commit, push -
# rather than only the REST API. Named here rather than per-renderer so a new button
# cannot ship a pushing step without the credential helper: see
# test_every_step_that_drives_git_authenticates_it.
GIT_PUSHERS = (
    "dsl_course.assign",
    "dsl_course.deploy",
    "dsl_course.propagate",
    "dsl_course.teardown",
    "dsl_course.scaffold",
    "dsl_course.site",
    "dsl_course.scheduler",
    # Every op the Console runs goes through it, releases and hand-outs included.
    "dsl_course.console",
)
# The two scheduler sub-commands that only READ: one lists the semesters, the other
# validates the course's own config. Neither clones anything, so neither needs git.
GIT_READ_ONLY = ("--list-semesters", "--check-course-config")


# Every renderer whose run ends in `seed refresh` - and the subset that may join a shared
# concurrency group. See test_only_the_nightly_refresh_joins_the_seed_refresh_group.
# The Console's New materials / New assignment / New semester end in one too, in process
# (`console.execute`), so it never appears in their workflow text.
SEED_REFRESH = {"refresh"}
SEED_REFRESH_GROUPED = {"refresh"}

# Job time budgets, per workflow, where they differ from the ordinary 30 minutes. A
# timeout that fires on a HEALTHY run is an outage, not a safety net: grading budgets 300s
# per submission subprocess and walks a semester serially.
DEFAULT_TIMEOUT = 30
CHECK_TEAM_TIMEOUT = 5
# Distribute grades walks every submission repo and every gradebook in the semester, so
# it is bounded like the other grading jobs rather than by the 30-minute default.
JOB_TIMEOUTS = {
    "distribute_grades": 120,
    # The Console runs any op, the grading ones included, in its one job.
    "console": 120,
}
# The scheduler is the one workflow whose jobs carry DIFFERENT budgets: it releases and
# grades in two jobs precisely so the two-hour one is never in the release's way, and giving
# the release job that budget back would hide a hung release for two hours. 60 rather than
# the ordinary 30 for releasing, because a handout provisions one repo per student across
# every semester in series.
PER_JOB_TIMEOUTS = {"scheduler": {"release": 60, "autograde": 120}}


def _trigger(rendered: str) -> dict:
    doc = yaml.safe_load(rendered)
    return doc.get("on", doc.get(True))


# Every renderer that takes a discovered list of orgs/repos, rendered with TWO semesters
# (and two of everything else) so dropdown ORDER is observable - ALL_RENDERED passes
# single-element lists, which cannot tell "newest first" from "oldest first".
SEMESTERS_2 = ["Semester-f2025", "Semester-f2026"]
REPOS_2 = ["course-materials-f2025", "course-materials-f2026"]
ASSIGNMENTS_2 = ["assignment-1-f2025", "assignment-1-f2026"]
DATED_RENDERED = {
    "collect_submissions": workflows_render.render_collect_submissions(
        SEMESTERS_2, ASSIGNMENTS_2
    ),
    "distribute_grades": workflows_render.render_distribute_grades(SEMESTERS_2),
}


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_renders_valid_yaml(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    assert isinstance(doc, dict) and doc.get("name")
    # Every faculty workflow is a workflow_dispatch with a check-team gate.
    assert ("check-team" in workflow_jobs(ALL_RENDERED[name])) is (
        name not in UNGATED | STEP_GATED
    )


@pytest.mark.parametrize("name", sorted(DATED_RENDERED))
def test_every_org_repo_dropdown_pre_selects_the_newest(name):
    # Dropdowns are listed alphabetically, which puts the OLDEST semester/materials repo
    # first - and GitHub selects the first option. Every one of them must therefore carry
    # an explicit `default:` naming the current year's, or faculty release last year's
    # materials to last year's semester with one wrong click.
    for field, spec in workflow_inputs(DATED_RENDERED[name]).items():
        options = spec.get("options", [])
        if not any("2026" in o for o in options):
            continue  # a fixed vocabulary (reading-list / individual / group / ...)
        default = spec.get("default")
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


def test_publish_site_takes_no_inputs():
    # opencourse.yml says what to publish (decision 0016), so the button asks nothing.
    assert workflow_inputs(workflows_render.render_publish_site()) == {}


def test_publish_site_has_publish_job_running_public_sync():
    rendered = workflows_render.render_publish_site()
    run = next(
        s
        for s in workflow_jobs(rendered)["publish"]["steps"]
        if s.get("name") == "Publish course website"
    )["run"]
    assert 'python3 -m dsl_course.site public-sync --course-org "$COURSE_ORG"' in run
    assert "--daily" not in run  # a person asked: an off website is refused


def test_publish_site_cron_republishes_from_opencourse():
    # The daily cron runs the same publish, quiet where opencourse.yml is absent or off;
    # the manual button keeps its check-team gate.
    rendered = workflows_render.render_publish_site()
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
    assert "--daily" in run
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


def test_collect_submissions_refreshes_the_sheet_and_freezes_nothing():
    # The button is the on-demand half of the quarter-hourly refresh; the FREEZE belongs to
    # the cron, at the assignment's grading deadline. A button that could freeze early
    # would move what a semester is marked on, so it passes --refresh-only and no deadline.
    rendered = workflows_render.render_collect_submissions(
        ["Semester-f2026"], ["assignment-1-f2026"]
    )
    inp = workflow_inputs(rendered)
    assert set(inp) == {"semester_org", "course_source_repo", "assignment", "preview"}
    assert inp["preview"]["default"] is True
    assert "dsl_course.collect" in rendered and "--refresh-only" in rendered
    assert "--deadline" not in rendered


def test_no_button_asks_which_of_two_schedule_entries():
    # The schedule knows which entry is which (decision 0009): Collect takes the
    # template, and a template two entries share is refused by the run, naming them.
    rendered = workflows_render.render_collect_submissions(
        ["Semester-f2026"], ["a-f2026"]
    )
    assert "slug" not in workflow_inputs(rendered)
    assert "--slug" not in rendered


def test_sync_membership_is_a_consolidated_reconcile():
    # One consolidated, fully-automatic reconcile (roster + teams + faculty) - no
    # --prune toggle at this level, config is always the live truth.
    rendered = workflows_render.render_sync_membership()
    # No dropdown (decision 0041): a manual run walks every semester, as the cron does.
    assert workflow_inputs(rendered) == {}
    steps = workflow_jobs(rendered)["sync-dispatch"]["steps"]
    run = next(s for s in steps if s.get("name") == "Sync membership")["run"]
    assert "--all-semesters" in run
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


def test_the_student_landing_page_invites_a_pull_request():
    # Students cannot push to the materials; forking and opening a pull request is how
    # they fix a typo, and it has to be said where they actually land: the org profile.
    # The join repo's README is only a pointer to it (decision 0010), so the landing text
    # is written once. Both files are seeded ONCE: live semesters keep what they have.
    invitation = "open a pull request"
    page = profile_readme.render_profile_readme(
        "My-Course-f2026", "My Course", [], True, central_ref="main"
    )
    assert invitation in page
    assert "fork" in page.lower()
    pointer = welcome.template("join/README.md")
    assert invitation not in pointer
    assert "(https://github.com/{org})" in pointer


def test_dotgithub_readme_orients_faculty():
    # The .github repo's own README points faculty at the Actions tab where the buttons live.
    course = profile_readme.render_dotgithub_readme(
        "My-Course-E1", "My Course", is_semester=False
    )
    assert "control panel" in course
    assert "My-Course-E1/.github/actions" in course
    # A semester org's own `.github` is not where any of the work happens, so its README is
    # deliberately a stub that says so - naming the org, and nothing a reader would have to
    # act on. It must NOT grow into a second control panel competing with the course org's.
    semester = profile_readme.render_dotgithub_readme(
        "My-Course-f2026", "My Course", is_semester=True
    )
    assert "My-Course-f2026" in semester
    assert "rarely need to touch" in semester
    assert "/.github/actions" not in semester


def test_content_repos_and_templates_lose_both_retired_release_buttons(monkeypatch):
    # Decision 0041: releasing and handing out are Console ops, so no content repo or
    # template hosts a workflow. Refresh (and every scaffold) deletes the two retired
    # ones in ONE commit and writes nothing else.
    commits = []

    def fake_put_files(org, repo, files, message, *, delete=()):
        commits.append((files, list(delete)))
        return True

    monkeypatch.setattr(workflows_place, "put_files", fake_put_files)
    for hosted in (
        workflows_place.RELEASE_WORKFLOWS,
        workflows_place.TEMPLATE_WORKFLOWS,
    ):
        assert hosted == ()
        assert (
            workflows_place.push_content_workflows(
                "Course", "repo", ["Semester-f2026"], [], "release", workflows=hosted
            )
            == 0
        )
    assert commits == [({}, list(workflows_place.RETIRED_WORKFLOWS))] * 2
    assert set(workflows_place.RETIRED_WORKFLOWS) == {
        ".github/workflows/release-materials.yml",
        ".github/workflows/release-assignment.yml",
    }
    # A course template not yet refreshed still carries one: it is stripped off the
    # semester copy before students' repos generate from it.
    assert set(workflows_place.RETIRED_WORKFLOWS) <= set(
        workflows_place.NEVER_IN_STUDENT_REPOS
    )


def test_the_org_level_buttons_land_as_one_commit(monkeypatch):
    # The whole org-level set rendered from one set of inputs by shared helpers: an edit
    # to the run preamble or a dropdown helper re-renders every one of them, so file-by-file
    # writes turned each such edit into a wall of near-identical commits in the repo whose
    # history faculty actually browse. The retired buttons ride along in the same commit
    # rather than earning three more.
    retired = (".github/workflows/propagate-cohort.yml",)
    monkeypatch.setattr(seed, "RETIRED_GITHUB_WORKFLOWS", retired)
    monkeypatch.setattr(seed, "discover_semesters", lambda org: ["Semester-f2026"])
    monkeypatch.setattr(
        seed, "discover_content_repos", lambda org: ["course-materials"]
    )
    monkeypatch.setattr(
        seed, "discover_materials_repos", lambda org: ["course-materials"]
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
    # Decision 0041: the Console, the crons, and the two grading workflows the Console
    # dispatches.
    assert len(files) == 9
    assert ".github/workflows/console.yml" in files
    assert all(path.startswith(".github/workflows/") for path in files)
    assert deleted == list(retired)
    assert retired[0] not in files


def test_every_retired_org_workflow_is_deleted_and_never_rendered(monkeypatch):
    # A retired button left on an org is a second way to do one job, and one nobody
    # maintains: the refresh that stops rendering it is the one that deletes it.
    monkeypatch.setattr(seed, "discover_semesters", lambda org: ["Semester-f2026"])
    monkeypatch.setattr(seed, "discover_content_repos", lambda org: ["m"])
    monkeypatch.setattr(seed, "discover_materials_repos", lambda org: ["m"])
    monkeypatch.setattr(seed, "discover_assignments", lambda org: ["a-f2026"])
    files = seed.github_workflow_files("Course", "release")
    assert not set(seed.RETIRED_GITHUB_WORKFLOWS) & set(files)
    for name in ("check-semester-setup", "archive-semester", "patch-assignment"):
        assert f".github/workflows/{name}.yml" in seed.RETIRED_GITHUB_WORKFLOWS


def test_bootstrap_org_workflow_routes_inputs_through_env_not_the_shell():
    wf = (ROOT / ".github" / "workflows" / "bootstrap-org.yml").read_text()
    step = yaml.safe_load(wf)["jobs"]["bootstrap"]["steps"][-1]
    assert "${{" not in step["run"]
    assert step["env"]["ORG"] == "${{ inputs.org }}"
    assert step["env"]["COURSE_NAME"] == "${{ inputs.course_name }}"
    assert step["env"]["COURSE_CODE"] == "${{ inputs.course_code }}"


def test_choice_falls_back_when_empty():
    # An empty dropdown must still be valid YAML (a placeholder option), never blank.
    assert "(none-yet)" in workflows_render._choice([])
    inp = workflow_inputs(workflows_render.render_collect_submissions([], []))
    assert inp["semester_org"]["options"] == ["(none-yet)"]


def test_sync_site_auto_resyncs_on_sourced_changes():
    # Sync site must auto-fire (no manual click) on the things the site reads: a push to
    # the course dsl-course.yml, a repository_dispatch from a semester's schedule.yml, and a
    # daily cron catch-all. The auto path is ungated (no check-team); manual stays gated.
    doc = yaml.safe_load(workflows_render.render_sync_site())
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


# The semester-config dispatcher's paths map (decision 0041): which course-org event each
# hand-edited file wakes. Sync membership rewrites `assignments.lock.yml` from the
# schedule, so schedule.yml wakes it; the site reads teams.csv for the teams with room;
# the scheduler's pre-flight checks every one; codes go out only on a roster push.
# Grading sheets are deliberately absent everywhere - a grader saves one all day.
DISPATCH_MAP = {
    "scheduled-release": {
        "schedule.yml",
        "assignments.yml",
        "instructors.yml",
        "students.csv",
        ".system/roster.csv",
    },
    "sync-membership": {
        "students.csv",
        ".system/roster.csv",
        "instructors.yml",
        "schedule.yml",
        "assignments.yml",
    },
    "sync-site": {"schedule.yml", "assignments.yml", "instructors.yml", "students.csv"},
    "send-codes": {"students.csv"},
}


def _dispatcher() -> tuple[str, dict]:
    tmpl = (ROOT / "templates" / "semester-config" / "dispatch.yml").read_text()
    return tmpl, yaml.safe_load(tmpl)


def test_one_semester_config_dispatcher_carries_the_paths_map():
    tmpl, doc = _dispatcher()
    trigger = doc.get("on", doc.get(True))
    assert set(trigger) == {"push"}
    assert trigger["push"]["branches"] == ["main"]
    assert set(trigger["push"]["paths"]) == set().union(*DISPATCH_MAP.values())
    run = doc["jobs"]["dispatch"]["steps"][0]["run"]
    block = run.split('paths="', 1)[1].split('"', 1)[0]
    rows = {
        line.split()[0]: set(line.split()[1:]) for line in block.split("\n") if line
    }
    assert rows == DISPATCH_MAP
    # DSL_BOT_TOKEN does every call here, so the ambient token gets no scopes at all.
    assert doc["permissions"] == {}
    # The course org is read from THIS semester's own pointer, by a YAML reader.
    assert "__CONFIG_REPO__/contents/__POINTER__" in tmpl
    assert "grep '^course:'" not in tmpl
    assert tmpl.splitlines()[0].startswith("# SYSTEM-OWNED")


def _fire(tmp_path, changed: str, before: str = "abc") -> list[str]:
    """Run the dispatcher's step under bash with `gh` and `yq` stubbed: the events fired."""
    _tmpl, doc = _dispatcher()
    script = tmp_path / "run.sh"
    script.write_text(
        doc["jobs"]["dispatch"]["steps"][0]["run"]
        .replace("__CONFIG_REPO__", "semester-config")
        .replace("__POINTER__", ".system/dsl-course.yml")
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "fired"
    (bin_dir / "gh").write_text(
        "#!/bin/bash\n"
        'case "$2" in\n'
        "  */contents/*) echo 'course: \"the-course\"  # a comment' ;;\n"
        '  */compare/*) printf "%s\\n" $CHANGED ;;\n'
        f'  */dispatches) echo "$2 $4" >> {log} ;;\n'
        "esac\n"
    )
    (bin_dir / "yq").write_text(
        f"#!/bin/bash\nexec {sys.executable} -c "
        "'import sys, yaml; print(yaml.safe_load(sys.stdin)[\"course\"])'\n"
    )
    for tool in ("gh", "yq"):
        (bin_dir / tool).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CHANGED": changed,
        "BEFORE": before,
        "AFTER": "def",
        "SEMESTER_ORG": "sem",
        "REPO": "sem/semester-config",
    }
    subprocess.run(["bash", "-eo", "pipefail", str(script)], env=env, check=True)
    lines = log.read_text().splitlines() if log.exists() else []
    assert all(line.startswith("repos/the-course/.github/dispatches") for line in lines)
    return [line.split("event_type=")[1] for line in lines]


@pytest.mark.parametrize("changed", sorted(set().union(*DISPATCH_MAP.values())))
def test_a_push_wakes_exactly_the_events_its_file_maps_to(tmp_path, changed):
    expected = [e for e, files in DISPATCH_MAP.items() if changed in files]
    assert _fire(tmp_path, f"{changed} README.md") == expected


def test_a_new_branch_wakes_everything(tmp_path):
    assert _fire(tmp_path, "", before="0000000000") == list(DISPATCH_MAP)


def test_send_codes_only_ever_runs_off_a_roster_push():
    # A roster edit is the ONLY way codes go out: no button, no semester dropdown, no
    # dry_run. The job is ungated (a repository_dispatch has no actor for check-team to
    # ask about) and it sends for real, because nobody is watching it.
    rendered = workflows_render.render_send_codes()
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    assert trigger == {"repository_dispatch": {"types": ["send-codes"]}}
    jobs = doc["jobs"]
    assert set(jobs) == {"send-codes"}
    assert "check-team" not in str(jobs["send-codes"].get("needs", ""))
    # The failure-notice steps trail this job too now, so address the work step by name.
    step = next(
        s
        for s in jobs["send-codes"]["steps"]
        if s.get("name", "").startswith("Send enrolment codes")
    )
    assert step["env"]["DISPATCH_SEMESTER"] == (
        "${{ github.event.client_payload.semester_org }}"
    )
    # No box to preview with: the push is the send, so it says so explicitly.
    assert "inputs.preview" not in rendered and "--no-preview" in step["run"]
    # The trust boundary: a client_payload is written by whoever holds a SEMESTER's bot
    # token, so the semester it names is checked against this course org's registry.
    assert '--dispatched-by "$COURSE"' in step["run"]
    assert step["env"]["COURSE"] == "${{ github.repository_owner }}"
    # Per semester, off the payload alone: two semesters pushing rosters at once must not
    # drop each other's queued send.
    assert (
        "send-codes-${{ github.event.client_payload.semester_org }}"
        in doc["concurrency"]["group"]
    )


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
    # Seeded into a semester's semester-config, so it must carry the central repo and ref
    # baked in - the semester repo has no other way to reach the parser.
    from dsl_course.central import CENTRAL, CENTRAL_REF
    from dsl_course.welcome import config_system_files

    raw = config_system_files(CENTRAL_REF)[
        ".github/workflows/validate-schedule.yml"
    ].decode()
    assert "__CENTRAL__" not in raw and "__CENTRAL_REF__" not in raw
    doc = yaml.safe_load(raw)
    trigger = doc.get("on", doc.get(True))

    # fires where the file is edited, and on demand
    assert trigger["push"]["paths"] == ["schedule.yml", "assignments.yml"]
    assert trigger["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in trigger

    steps = doc["jobs"]["validate"]["steps"]
    central = next(s for s in steps if s.get("with", {}).get("repository"))
    assert central["with"]["repository"] == CENTRAL
    assert central["with"]["ref"] == CENTRAL_REF

    # validates the semester's OWN file, not a fetched copy - no token needed to read it
    run = next(s for s in steps if s.get("id") == "validate")["run"]
    assert "--file ../semester/schedule.yml --validate" in run
    assert "$GITHUB_STEP_SUMMARY" in run

    # The run must end red so the commit is marked. Nothing here writes an ISSUE - the
    # engine's digest does - so that scope is gone; the commit comment needs contents.
    assert doc["permissions"] == {"contents": "write"}
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
            {"name": "join", "url": "u", "visibility": "private", "description": ""}
        ],
    )
    monkeypatch.setattr(P, "discover_semesters", lambda org: [])
    commits = []
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: commits.append(files) or True
    )
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)

    P.update_profile_readme("Semester-f2026", central_ref="release")  # must not raise
    # Both READMEs, using the org name as the fallback - and in ONE commit, since they are
    # rendered from the same org snapshot and always move together.
    assert len(commits) == 1
    assert set(commits[0]) == {"profile/README.md", "README.md"}


# ------------------------------------- the semester landing page is instructor-owned, bar
# its repo table. These pin the three ways a refresh can meet an existing page.

_REPOS = [
    {"name": "join", "url": "u", "visibility": "PUBLIC", "description": "front door"}
]


def test_repo_table_drops_submission_and_gradebook_repos():
    from dsl_course.profile_readme import _repo_table

    # The students' landing page must not name every classmate's private repo: the topics
    # are the same ones discovery._is_infra_repo filters on.
    rows = _repo_table(
        [
            {
                "name": "join",
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
    assert "join" in rows and "materials" in rows and "org.github.io" in rows
    assert "assignment-1-ada" not in rows
    assert "grades-ada" not in rows
    assert "| [assignment-1]" not in rows


def _semester_org_repos():
    org = "semester-f2026"
    return [
        {"name": n, "url": "u", "visibility": v, "description": "d", "topics": []}
        for n, v in (
            ("semester-config", "PRIVATE"),
            (f"{org}.github.io", "PUBLIC"),
            ("labs", "PRIVATE"),
            ("materials", "PRIVATE"),
            ("join", "PUBLIC"),
        )
    ]


def test_semester_table_names_the_audience_not_githubs_visibility():
    from dsl_course.profile_readme import _semester_repo_table

    # "private" is the answer to a question nobody landing on the students' page is
    # asking: it does not say whether enrolling will let them in.
    cells = [
        row.split("|")[2].strip()
        for row in _semester_repo_table(_semester_org_repos()).splitlines()
    ]
    assert cells == [
        "public (students join here)",
        "enrolled students & auditors only",
        "private",  # a repo we did not seed claims no grant we did not make
        "instructor-only",
        "public",
    ]


def test_semester_table_runs_students_first_then_config_then_the_site():
    from dsl_course.profile_readme import _semester_repo_table

    names = [
        row.split("[")[1].split("]")[0]
        for row in _semester_repo_table(_semester_org_repos()).splitlines()
    ]
    assert names == [
        "join",
        "materials",
        "labs",
        "semester-config",
        "semester-f2026.github.io",
    ]


def test_course_table_keeps_bare_visibility():
    from dsl_course.profile_readme import _repo_table

    # Faculty read that one, and they can already open everything - what they want is
    # which repos a release would expose.
    rows = _repo_table(_semester_org_repos())
    assert "| private |" in rows and "| public |" in rows
    assert "instructor-only" not in rows


def _readme_run(monkeypatch, put_ok):
    from dsl_course import profile_readme as P

    monkeypatch.setattr(P, "org_meta", lambda org: {})
    monkeypatch.setattr(P, "course_name_for_semester", lambda org: "")
    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_semesters", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    said_ok: list[str] = []
    monkeypatch.setattr(P, "log_ok", lambda msg: said_ok.append(msg))
    monkeypatch.setattr(P, "log_err", lambda *a, **k: None)
    monkeypatch.setattr(P, "put_files", lambda *a, **k: put_ok)
    return P.update_profile_readme("Semester-f2026", central_ref="release"), said_ok


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


def test_semester_page_title_follows_the_course_pointer(monkeypatch):
    from dsl_course import profile_readme as P

    # A semester has no dsl-course.yml of its own, only its pointer, so this used to title
    # the students' landing page with the org slug.
    monkeypatch.setattr(P, "org_meta", lambda org: {})
    monkeypatch.setattr(
        P,
        "course_name_for_semester",
        lambda org: "Deep Learning" if org == "Semester-f2026" else "",
    )
    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: None)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_semesters", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: written.update(files) or True
    )
    P.update_profile_readme("Semester-f2026", central_ref="release")
    page = written["profile/README.md"].decode()
    assert "# Deep Learning" in page
    assert "# Semester-f2026" not in page


def _semester_readme(monkeypatch, existing):
    from dsl_course import profile_readme as P

    monkeypatch.setattr(P, "get_file_content", lambda *a, **k: existing)
    monkeypatch.setattr(P, "list_org_repos", lambda org: _REPOS)
    monkeypatch.setattr(P, "discover_semesters", lambda org: [])
    monkeypatch.setattr(P, "log", lambda *a, **k: None)
    monkeypatch.setattr(P, "log_ok", lambda *a, **k: None)
    written = {}
    monkeypatch.setattr(
        P, "put_files", lambda org, repo, files, msg, **k: written.update(files) or True
    )
    P.update_profile_readme("Semester-f2026", "Deep Learning", central_ref="release")
    return written


def test_semester_page_is_seeded_whole_when_absent(monkeypatch):
    from dsl_course import profile_readme as P

    page = _semester_readme(monkeypatch, None)["profile/README.md"].decode()
    assert "INSTRUCTOR-OWNED" in page
    assert P.TABLE_START in page and P.TABLE_END in page
    assert "front door" in page


def test_semester_refresh_replaces_only_the_marked_table(monkeypatch):
    from dsl_course import profile_readme as P

    edited = (
        "# Our course, our words\n\nSee you Tuesdays in room 4.\n\n"
        f"{P.TABLE_START} -->\n| stale | rows |\n{P.TABLE_END}\n\nGood luck!\n"
    )
    page = _semester_readme(monkeypatch, edited)["profile/README.md"].decode()
    assert "Our course, our words" in page  # prose above survives
    assert "See you Tuesdays in room 4." in page
    assert "Good luck!" in page  # and prose below
    assert "| stale | rows |" not in page  # the table alone is replaced
    assert "front door" in page


def test_semester_refresh_leaves_any_page_without_markers_alone(monkeypatch):
    # No markers = wholly the instructor's, whether they deleted them or predate them.
    # There is no way to tell those apart from the bytes, so neither is overwritten.
    written = _semester_readme(monkeypatch, "# Entirely mine\n\nNo markers here.\n")
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
    # kills legitimate grading runs mid-semester, and a timeout that fires on a healthy run is
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


# What a rendered `run:` block looks like when it executes the STUDENTS' own code - the
# module that clones a submission and runs its notebook, its `run.sh` and the hidden tests,
# by either of the two ways in.
STUDENT_CODE = ("dsl_course.collect", "--autograde-only")


def _runs_student_code(step: dict) -> bool:
    run = step.get("run", "")
    # `collect --refresh-only` re-derives the sheet's facts and runs nobody's code.
    if "dsl_course.collect" in run and "--refresh-only" in run:
        return False
    return any(entry in run for entry in STUDENT_CODE)


# The two shapes a reporting job comes in, as `{what "the run failed" is: its success
# counterpart}`.
#
# The first is ordinary: the reporting steps sit IN the job they report on, so the job's
# own status answers, and the log is the one the failing step teed to $RUNNER_TEMP.
#
# The second is the scheduler's grading legs. `autograde` ends on the students' own code -
# the runner sources whatever a step leaves in $GITHUB_ENV before starting the next one, so
# a step after that one carrying the bot token would run their code as the org owner - so
# its reporting moved to a job of its own and has to ASK the jobs API instead. `skipped`
# there means "no leg ran at all", which is neither a failure to file nor a recovery to
# close; every other non-success conclusion, `cancelled` included, is the fault to report.
#
# The third is the first with the scheduler release job's scope: a run a semester's push
# scoped to one semester neither files nor closes the course-wide issue.
FAILURE_GATES = {
    "(failure() || cancelled())": "success()",
    "(failure() || cancelled()) && env.SCOPED == ''": "success() && env.SCOPED == ''",
    (
        "steps.graded.outputs.result != 'success' "
        "&& steps.graded.outputs.result != 'skipped'"
    ): "steps.graded.outputs.result == 'success'",
}
MANUAL = " && github.event_name != 'workflow_dispatch'"


def _failure_gate(step: dict) -> str:
    """The `if` fragment that says "the run this reports on went wrong", checked against the
    two shapes above so a third cannot appear unremarked."""
    gate = step["if"].partition(" && steps.notice")[0].removesuffix(MANUAL)
    assert gate in FAILURE_GATES, step["if"]
    return gate


def _assert_reports_a_failure(opener: dict) -> None:
    # An issue is the only channel that reaches a human: the scheduled-failure email goes
    # to the bot. One open issue tracks the current state - opened/commented on failure,
    # closed by the next green run.
    assert "gh issue create" in opener["run"]
    # The title is the workflow's own ambient name, so each keeps its own issue (a shared
    # title would let one recovery close another's open failure) with no mirrored string.
    # A workflow whose unattended jobs run CONCURRENTLY scopes it further - the scheduler's
    # grading job names the semester it grades - because there the sibling that closes the
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
    # reliably runs out of time is exactly the silent failure this exists to surface. Both
    # gates cover it: `cancelled()` in the job's own status, and "concluded as anything but
    # success" in the jobs-API answer the detached reporting job reads.
    _failure_gate(opener)
    # A manual run's failure is already in front of the person who clicked.
    assert opener["if"].endswith(MANUAL)
    # A transient search failure must not abort the step BEFORE it files anything (the
    # step runs under `bash -e`, so an unguarded capture would).
    assert ") || true" in opener["run"]
    # `permissions: {}` leaves the ambient token unable to file anything.
    assert opener["env"]["GH_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"
    # Teaching staff are cc'd on this issue and a broken run is not theirs to fix, so it
    # says who is already on it rather than leaving them to wonder.
    assert "The toolkit maintainer has been emailed the log" in opener["run"]
    # And it publishes whether it actually reported, which is what gates the mail step.
    assert opener["id"] == "notice"
    assert opener["run"].count("report=true") == 2  # created, and commented
    assert "report=false" in opener["run"]


def _assert_emails_the_maintainer(step: dict, detached: bool) -> None:
    # The issue is the durable record; this is the only channel the MAINTAINER is on.
    # GitHub's own scheduled-failure email goes to whoever last committed the workflow
    # file, which is always the bot, which is to say nobody.
    if not detached:
        # The step's OWN log, teed by the step that failed. The jobs API cannot answer this
        # from in here: the job is still RUNNING, so its `conclusion` is null and a lookup
        # for the failed job mailed an empty tail - and on the grading matrix it could
        # match another semester's leg.
        assert f"tail -n 30 {workflows_render._RUN_LOG}" in step["run"]
        assert "actions/runs" not in step["run"]
    else:
        # Reporting from a job of its own is the one case where the API IS the answer: the
        # leg it reports on has finished, so its log can be fetched - and it must be,
        # because $RUNNER_TEMP belonged to a runner this job never shared.
        assert 'gh api "repos/$REPO/actions/jobs/$JOB_ID/logs"' in step["run"]
        assert "| tail -n 30" in step["run"]
        assert step["env"]["JOB_ID"] == "${{ steps.graded.outputs.job_id }}"
        assert step["env"]["GH_TOKEN"] == "${{ secrets.DSL_BOT_TOKEN }}"
    assert "dsl_course.notify run-failed" in step["run"]
    # Piped, never interpolated: a log tail is arbitrary text and must not become argv.
    assert "${{" not in step["run"]
    # A run that left no log to read, and a missing tail must not lose the mail as well -
    # the run URL is in it either way. This job has also already failed for its own
    # reasons; reddening it twice says nothing new.
    assert "|| true" in step["run"]
    # Throttled WITH the issue, off the notice step's own output, so a maintainer who gets
    # an email can always find the issue it came from - and a thread being kept quiet for
    # six hours does not mail either.
    assert "steps.notice.outputs.report == 'true'" in step["if"]
    _failure_gate(step)
    assert "github.event_name != 'workflow_dispatch'" in step["if"]
    # It can only mail if it carries the transport.
    assert set(mailer.GRAPH_ENV) <= set(step["env"])
    assert step["env"][mailer.MAINTAINER_ENV] == _secret_ref(mailer.MAINTAINER_ENV)
    assert step["env"]["COURSE"] == "${{ github.repository_owner }}"


def _assert_the_reported_step_writes_its_log(job: dict, where: str) -> None:
    """The mail above tails a log, so the step it reports on has to write one."""
    teed = [i for i, s in enumerate(job["steps"]) if "(tee " in s.get("run", "")]
    assert len(teed) == 1, where
    step = job["steps"][teed[0]]
    assert step["run"].rstrip().endswith(workflows_render._TEE_RUN_LOG), where
    # `exec`'d, so a cancel signals Python itself and the step exits with Python's status.
    last = step["run"].rstrip().split("\n")
    command = next(line for line in reversed(last) if "python" in line)
    assert command.strip().startswith("exec python"), where
    mail = next(
        i for i, s in enumerate(job["steps"]) if "dsl_course.notify" in s.get("run", "")
    )
    assert teed[0] < mail, where


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
        "title": "Scheduled release (autograde Semester-f2026) is failing",
        "updatedAt": "2020-01-01T00:00:00Z",
    },
]


def _closer(job: dict) -> dict:
    """The step that closes this job's failure issue once its run comes back green."""
    (closer,) = [s for s in job["steps"] if "gh issue close" in s.get("run", "")]
    return closer


def _step_outputs(path: Path) -> dict[str, str]:
    """The `name=value` lines a step wrote to $GITHUB_OUTPUT."""
    if not path.exists():
        return {}
    return dict(
        line.split("=", 1) for line in path.read_text().splitlines() if "=" in line
    )


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
    outputs = work / "github_output"
    outputs.write_text("")
    subprocess.run(
        # `bash -e`, which is how GitHub runs a `run:` block.
        ["bash", "-e", "-c", step["run"]],
        env={
            "PATH": f"{work}:{os.environ['PATH']}",
            "LOG": str(log),
            "GITHUB_OUTPUT": str(outputs),
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
    # never engaged - ~96 mentions a day per faulty semester.
    jobs = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]
    release_closer = _closer(jobs["release"])
    assert _run_issue_step(release_closer, tmp_path / "r", _OPEN_ISSUES) == ["close 11"]
    # The grading legs' recovery is closed from `autograde-report`, not from the job that
    # graded: that one ends on the students' own code, and nothing carrying the bot token
    # may follow it on the same runner.
    grading_closer = _closer(jobs["autograde-report"])
    assert _run_issue_step(
        grading_closer, tmp_path / "a", _OPEN_ISSUES, SEMESTER="Semester-f2026"
    ) == ["close 22"]


def test_a_failure_files_its_own_issue_rather_than_commenting_on_a_sibling(tmp_path):
    # The same match on the dedupe lookup. Reading a sibling's issue as "already open"
    # would leave the release failure reported nowhere - it would comment on a grading
    # thread nobody watching releases is subscribed to.
    opener = next(
        s
        for s in yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]["release"]["steps"]
        if s.get("id") == "notice"
    )
    grading_only = [i for i in _OPEN_ISSUES if "autograde" in i["title"]]
    assert _run_issue_step(opener, tmp_path / "x", grading_only) == ["create --repo"]
    # ...and it comments on the one that IS its own (an old timestamp, so not throttled).
    assert _run_issue_step(opener, tmp_path / "y", _OPEN_ISSUES) == ["comment 11"]


# Two strikes, and only for the two crons that fire often enough for a one-off GitHub blip to
# reach course-admin and the maintainer: a FIRST failure files and mails only if the same job
# also failed in the previous run. `{workflow: the jobs that wait}`.
TWO_STRIKES = {
    "scheduler": {"release", "autograde-report"},
    "sync_membership": {"sync-auto"},
}


def _notice(name: str, job: str) -> dict:
    steps = yaml.safe_load(ALL_RENDERED[name])["jobs"][job]["steps"]
    return next(s for s in steps if s.get("id") == "notice")


def _run_strike_step(
    step: dict,
    work: Path,
    runs: list[dict] | None,
    jobs: dict[int, list[dict]],
    **env: str,
) -> tuple[list[str], dict[str, str]]:
    """Execute a notice step for real with no issue open, against a faked run history.

    The fake `gh` routes by endpoint and applies the `--jq` filter itself, as gh does, so
    what is under test is the step's own filters. `runs=None` makes the runs listing fail.
    Returns the issue commands made and the step's outputs."""
    if not shutil.which("jq"):  # pragma: no cover - present on every CI runner
        pytest.skip("jq is what applies gh's --jq filter")
    work.mkdir(parents=True, exist_ok=True)
    (work / "issues.json").write_text("[]")
    if runs is not None:
        (work / "runs.json").write_text(json.dumps({"workflow_runs": runs}))
    for run_id, run_jobs in jobs.items():
        (work / f"jobs-{run_id}.json").write_text(json.dumps({"jobs": run_jobs}))
    fake = work / "gh"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "issue close"|"issue comment"|"issue create") echo "$2 $3" >>"$LOG"; exit 0 ;;\n'
        "esac\n"
        'case "$2" in\n'
        '  */actions/workflows/scheduler.yml/runs*) data="$DIR/runs.json" ;;\n'
        '  */actions/runs/*/jobs) id=${2%/jobs}; data="$DIR/jobs-${id##*/}.json" ;;\n'
        '  *) data="$DIR/issues.json" ;;\n'
        "esac\n"
        "while [ $# -gt 0 ]; do\n"
        '  [ "$1" = "--jq" ] && filter="$2"\n'
        "  shift\n"
        "done\n"
        '[ -f "$data" ] || exit 1\n'
        'jq -r "$filter" "$data"\n'
    )
    fake.chmod(0o755)
    log = work / "gh.log"
    log.write_text("")
    outputs = work / "github_output"
    outputs.write_text("")
    subprocess.run(
        ["bash", "-e", "-c", step["run"]],
        env={
            "PATH": f"{work}:{os.environ['PATH']}",
            "LOG": str(log),
            "DIR": str(work),
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_RUN_ID": "100",
            "GITHUB_WORKFLOW_REF": "Course-Org/.github/.github/workflows/scheduler.yml@refs/heads/main",
            "WORKFLOW": "Scheduled release",
            "REPO": "Course-Org/.github",
            "RUN_URL": "https://example.invalid/run/100",
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in log.read_text().splitlines() if line], _step_outputs(
        outputs
    )


def _run(
    run_id: int, event: str = "schedule", title: str = "Scheduled release"
) -> dict:
    return {"id": run_id, "event": event, "display_title": title}


def _job(name: str, conclusion: str | None, ran: bool = True) -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "steps": [{"name": "x"}] if ran else [],
    }


FILED = (["create --repo"], {"report": "true"})
HELD = ([], {"report": "false"})


@pytest.mark.parametrize("before, outcome", [("success", HELD), ("failure", FILED)])
def test_a_frequent_cron_reports_only_the_second_failure_in_a_row(
    tmp_path, before, outcome
):
    # One failed hourly Sync membership, green the next run, mailed course-admin and the
    # maintainer about a GitHub blip. A first failure after a good run now waits; the
    # second in a row files and mails exactly as before, both off `report`.
    for name, job, job_name in [
        ("scheduler", "release", "release"),
        ("sync_membership", "sync-auto", "sync-auto"),
    ]:
        jobs = {100: [], 99: [_job(job_name, before)]}
        got = _run_strike_step(
            _notice(name, job), tmp_path / name, [_run(100), _run(99)], jobs
        )
        assert got == outcome, name


def test_the_previous_run_is_the_last_unattended_one_whose_job_ran(tmp_path):
    # Passed over: a button press (someone watched it), a run a semester's push scoped to
    # one semester (it never reports on the course-wide issue), and a run whose release job
    # was cancelled while it waited in the queue. The run before those failed, so this is
    # the second strike.
    runs = [
        _run(100),
        _run(99, event="workflow_dispatch"),
        _run(
            98, event="repository_dispatch", title=f"{course.SCOPED_RUN_TITLE} S-f2026"
        ),
        _run(97),
        _run(96),
    ]
    jobs = {
        99: [_job("release", "success")],
        98: [_job("release", "success")],
        97: [_job("release", "cancelled", ran=False)],
        96: [_job("release", "failure")],
    }
    opener = _notice("scheduler", "release")
    assert _run_strike_step(opener, tmp_path / "a", runs, jobs) == FILED
    jobs[96] = [_job("release", "success")]
    assert _run_strike_step(opener, tmp_path / "b", runs, jobs) == HELD


def test_a_grading_leg_counts_strikes_for_its_own_semester(tmp_path):
    # The legs fail independently, so a strike is THIS semester's leg in the previous run.
    jobs = {
        99: [
            _job("release", "success"),
            _job("autograde S-a2026", "failure"),
            _job("autograde S-b2026", "success"),
        ]
    }
    opener = _notice("scheduler", "autograde-report")
    runs = [_run(100), _run(99)]
    assert (
        _run_strike_step(opener, tmp_path / "a", runs, jobs, SEMESTER="S-a2026")
        == FILED
    )
    assert (
        _run_strike_step(opener, tmp_path / "b", runs, jobs, SEMESTER="S-b2026") == HELD
    )
    # A semester with no leg anywhere in the history has nothing to compare: it reports.
    assert (
        _run_strike_step(opener, tmp_path / "c", runs, jobs, SEMESTER="S-c2026")
        == FILED
    )


def test_a_failed_history_lookup_reports_rather_than_staying_quiet(tmp_path):
    # Fail OPEN: the point of the issue is that somebody hears, so a lookup that cannot say
    # "the last run was fine" never holds a report back.
    opener = _notice("scheduler", "release")
    assert _run_strike_step(opener, tmp_path / "a", None, {}) == FILED
    # ...and a run whose jobs cannot be read is no evidence either.
    assert _run_strike_step(opener, tmp_path / "b", [_run(100), _run(99)], {}) == FILED


@pytest.mark.parametrize("name", sorted(UNATTENDED | {"console"}))
def test_only_the_frequent_crons_wait_for_a_second_failure(name):
    # Everything else is daily or pushed, so its first failure is worth a mail.
    doc = yaml.safe_load(ALL_RENDERED[name])
    waiting = {
        job
        for job, spec in doc["jobs"].items()
        for s in spec.get("steps", [])
        if s.get("id") == "notice" and "/actions/workflows/" in s["run"]
    }
    assert waiting == TWO_STRIKES.get(name, set())


@pytest.mark.parametrize("name", sorted(UNATTENDED))
def test_every_unattended_run_files_and_closes_its_own_failure_issue(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    reporting = [
        (n, j)
        for n, j in doc["jobs"].items()
        if any("gh issue create" in s.get("run", "") for s in j.get("steps", []))
    ]
    assert reporting, f"{name}: nothing reports its unattended failures"
    for job_name, job in reporting:
        openers = [s for s in job["steps"] if "gh issue create" in s.get("run", "")]
        mailers = [s for s in job["steps"] if "dsl_course.notify" in s.get("run", "")]
        # ONE of each per job: the durable issue, and the mail that is how the maintainer
        # hears at all. The scheduler releases and grades in CONCURRENT jobs, so the
        # contract is per job - but two notices in one job still double-file.
        assert len(openers) == 1, f"{name}.{job_name}"
        assert len(mailers) == 1, f"{name}.{job_name}: nobody emails the maintainer"
        (opener,) = openers
        # ...and the job is UNGATED. check-team only runs on workflow_dispatch, so a job
        # that needs it is SKIPPED on the cron - parking the notice on a trailing gated job
        # would report nothing at all, silently.
        assert "check-team" not in str(job.get("needs", "")), (
            f"{name}: the notice rides {job_name}, which is skipped on the cron"
        )
        _assert_reports_a_failure(opener)
        detached = _failure_gate(opener).startswith("steps.graded")
        _assert_emails_the_maintainer(mailers[0], detached)
        if not detached:
            _assert_the_reported_step_writes_its_log(job, f"{name}.{job_name}")
        else:
            # A reporting job that is not the job it reports on must never be the one
            # running student code - moving the secrets off that runner is the whole point.
            assert not any(_runs_student_code(s) for s in job["steps"]), job_name

    # "Fix it and re-run" is how a human confirms the recovery, so EVERY job a human can
    # dispatch closes the ticket too - not just the schedule-gated one carrying the notice.
    # The exception is a job that runs the STUDENTS' code: no step holding the bot token
    # may follow that, so its recovery is closed by the reporting job that `needs:` it.
    graders = {
        n
        for n, j in doc["jobs"].items()
        if any(_runs_student_code(s) for s in j.get("steps", []))
    }
    closers = {
        n
        for n, j in doc["jobs"].items()
        if any("gh issue close" in s.get("run", "") for s in j.get("steps", []))
    }
    assert closers == set(doc["jobs"]) - {"check-team"} - graders
    for grader in graders:
        assert any(grader in str(doc["jobs"][n].get("needs", "")) for n in closers), (
            f"{name}.{grader}: nothing closes the ticket its failures file"
        )
    for closer_job in closers:
        closer = _closer(doc["jobs"][closer_job])
        # A job closes exactly the title it FILES, on exactly the condition that title was
        # filed on - or a recovery closes nothing, and, worse, on a workflow whose jobs run
        # at once it closes a sibling's still-standing failure instead of its own.
        own = dict(reporting).get(closer_job)
        opener = (
            next(s for s in own["steps"] if s.get("id") == "notice") if own else None
        )
        want = _issue_title(opener) if opener else '"$WORKFLOW is failing"'
        assert _issue_title(closer) == want, f"{name}.{closer_job}"
        gate = _failure_gate(opener) if opener else "(failure() || cancelled())"
        assert closer["if"] == FAILURE_GATES[gate], f"{name}.{closer_job}"


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_student_code_is_the_last_thing_its_runner_ever_does(name):
    # RELEASE-BLOCKING if this ever stops holding. A graded run executes the students' own
    # notebooks, `run.sh` and hidden tests, and the runner SOURCES whatever a step left in
    # $GITHUB_ENV / $GITHUB_PATH before it starts the next step of the same job. So a cell
    # appending `BASH_ENV=/tmp/x` runs in that next step - and every step this estate has
    # after a grading step used to carry `secrets.DSL_BOT_TOKEN`, i.e. the org owner.
    #
    # The environment scrub in collect._sanitised_env is the second lock, not this one.
    # This is the structural fix: the grading step is the LAST step of its job, so anything
    # that has to happen afterwards happens in a separate `jobs:` entry - which is a fresh
    # runner, with none of that job's files or exported environment.
    doc = yaml.safe_load(ALL_RENDERED[name])
    found = 0
    for job_name, job in doc["jobs"].items():
        steps = job.get("steps", [])
        for i, step in enumerate(steps):
            if not _runs_student_code(step):
                continue
            found += 1
            assert i == len(steps) - 1, (
                f"{name}.{job_name}: {steps[i + 1].get('name')!r} runs on the runner that "
                f"just executed student code"
            )
            # ...and the job creates the account that code runs AS. The step ordering above
            # keeps the token off the runner AFTER the fact; the uid keeps it out of reach
            # during, because /proc/<pid>/environ is readable by any process of the same
            # user. `collect` fails closed without the account, so a grading job that skips
            # this step grades nothing - silently, but for the skip record.
            made = [j for j, s in enumerate(steps) if "useradd" in s.get("run", "")]
            assert made and made[0] < i, f"{name}.{job_name}: no sandbox account"
            assert workflows_render.SANDBOX_USER in steps[made[0]]["run"]
            # It carries no secret of its own, and it must not: it runs before the step
            # that hands the runner over to the students' code.
            assert not steps[made[0]].get("env")
    # ...and the sweep is looking at something: the scheduler's `--autograde-only`. The
    # Collect submissions button only refreshes the sheet (`--refresh-only`).
    if name == "scheduler":
        assert found == 1, name


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
# half-reconciled team. Read-only buttons are deliberately absent.
SERIALISED_WRITERS = {
    "collect_submissions": "collect-submissions",
    # Two overlapping Send-codes runs generate two codes for the same blank cell: one is
    # written and the other is emailed, so that student's code enrols nobody. Scoped PER
    # SEMESTER, because that raced state is one semester's students.csv: a roster push in one
    # semester must not drop a queued send in another (a group holds one pending run, and a
    # third arrival cancels the second).
    "send_codes": "send-codes-${{ github.event.client_payload.semester_org }}",
    "sync_membership": "sync-membership",
    "sync_site": "sync-site",
    "publish_site": "publish-course-website",
    # Per ACTOR: one person's presses run in order, and two people never wait on each other.
    "console": "console-${{ github.actor }}",
}


@pytest.mark.parametrize("name", sorted(SERIALISED_WRITERS))
def test_every_writer_serialises_against_itself_per_repo(name):
    doc = yaml.safe_load(ALL_RENDERED[name])
    assert doc.get("concurrency") == {
        "group": "${{ github.repository }}-" + SERIALISED_WRITERS[name],
        "cancel-in-progress": False,
    }, f"{name} can overlap itself"


def test_distribute_grades_queues_real_returns_per_semester_and_never_a_preview():
    # A real return - the scheduler's `return-marks` or a press, the Console's included -
    # queues behind any other in its semester; a preview queues behind nothing, so the
    # next automatic return cannot cancel it while it waits.
    group = yaml.safe_load(ALL_RENDERED["distribute_grades"])["concurrency"]["group"]
    assert "inputs.preview != false && github.run_id" in group
    assert "-distribute-grades-{1}', github.repository," in group
    assert "github.event.client_payload.semester_org || inputs.semester_org" in group
    job = yaml.safe_load(ALL_RENDERED["distribute_grades"])["jobs"]["distribute-grades"]
    assert job["if"].startswith("!cancelled()")


def test_a_grading_leg_dropped_from_its_queue_is_not_a_failure():
    # A leg cancelled before it started (a later run's leg took its queue slot) has no
    # steps; reported as a failure, it filed an issue about a healthy semester.
    jobs = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]
    outcome = next(
        s for s in jobs["autograde-report"]["steps"] if s.get("id") == "graded"
    )
    assert (
        'if .conclusion == \\"cancelled\\" and ((.steps // []) | length) == 0 '
        'then \\"skipped\\" else .conclusion end' in outcome["run"]
    )


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
    # No workflow-level group: that was ONE queue for every action in every semester, and a
    # two-hour grading pass in one semester then held up a release due in another. Each job
    # declares its own instead.
    doc = yaml.safe_load(ALL_RENDERED["scheduler"])
    assert "concurrency" not in doc
    jobs = doc["jobs"]
    # Releases are fire-once, so a tick arriving mid-pass still queues behind one - but a
    # manual PREVIEW writes nothing, and joining that queue is how an operator's preview
    # gets silently dropped (a group holds one pending run; a third arrival cancels it).
    assert jobs["release"]["concurrency"] == {
        "group": "${{ github.event_name == 'workflow_dispatch' && inputs.preview != false && github.run_id || 'scheduled-release' }}",
        "cancel-in-progress": False,
    }
    # Grading queues PER SEMESTER: the fire-once marker is a semester-side file, and a pass
    # over another semester shares nothing with it. A dry-run leg is per run and per semester,
    # for the same reason the release job's is.
    assert jobs["autograde"]["concurrency"] == {
        "group": (
            "${{ github.event_name == 'workflow_dispatch' && inputs.preview != false "
            "&& format('{0}-{1}', github.run_id, "
            "matrix.semester) || format('scheduled-autograde-{0}', matrix.semester) }}"
        ),
        "cancel-in-progress": False,
    }
    for name in ("release", "autograde"):
        assert jobs[name]["concurrency"]["cancel-in-progress"] is False
    # The reporting job is deliberately UNGROUPED. It writes nothing but a deduped issue,
    # and a group holds one pending run - so joining either queue could only ever drop the
    # report of a failure, which is the one thing this workflow may not do silently.
    assert "concurrency" not in jobs["autograde-report"]


def test_the_scheduler_grades_every_semester_without_waiting_on_the_releases():
    jobs = yaml.safe_load(ALL_RENDERED["scheduler"])["jobs"]
    autograde = jobs["autograde"]
    # It needs the release job only for the semester LIST, so it must run whatever became of
    # the releases: grading is gated on the durable snapshot marker, not on this run.
    assert autograde["needs"] == ["release"]
    # Not on a run an operator cancelled, though: that would go on running student code.
    assert autograde["if"].startswith("!cancelled()")
    assert jobs["autograde-report"]["if"].startswith("!cancelled()")
    # ...but not on an empty list: GitHub errors on a matrix with no vectors, and a course
    # org with no semesters registered yet is a normal state, not a failure.
    assert "needs.release.outputs.semesters != '[]'" in autograde["if"]
    assert "needs.release.outputs.semesters != ''" in autograde["if"]
    assert jobs["release"]["outputs"] == {
        "semesters": "${{ steps.semesters.outputs.semesters }}"
    }
    steps = jobs["release"]["steps"]
    lister = next(s for s in steps if s.get("id") == "semesters")
    assert "--list-semesters" in lister["run"]
    # And it is listed BEFORE the release pass runs: a failed step skips the ones after
    # it, so listing afterwards would let one semester's release fault skip everyone's
    # grading - on a run that has already reported the release failure and moved on.
    releaser = next(s for s in steps if "--all-semesters" in str(s.get("run", "")))
    assert steps.index(lister) < steps.index(releaser)
    assert autograde["strategy"] == {
        "fail-fast": False,  # one semester's grading failure cancels nobody else's
        "matrix": {"semester": "${{ fromJSON(needs.release.outputs.semesters) }}"},
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
    # The scheduler autogrades at every passed deadline through the grading preamble,
    # which installs requirements-autograde.txt and nothing else. When pytest
    # lived only in the manual grading step, `python -m pytest` was "No module
    # named pytest" on the cron: silent zeros for the whole semester, no sentinel, and the
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


def _jobs_of(rendered: str) -> dict:
    return yaml.safe_load(rendered)["jobs"]


def _grades(job: dict) -> bool:
    return any(
        "useradd" in str(step.get("run", ""))
        and workflows_render.SANDBOX_USER in str(step.get("run", ""))
        for step in job.get("steps", [])
    )


def test_only_the_jobs_that_grade_install_the_autograder():
    # pytest, nbconvert and ipykernel were most of every job's 9-15s install, and only the
    # jobs that run students' code use them - the ones that create the sandbox account.
    grading, core = 0, 0
    for name, rendered in ALL_RENDERED.items():
        for job_name, job in _jobs_of(rendered).items():
            installs = [
                s["run"].strip()
                for s in job.get("steps", [])
                if "pip install" in str(s.get("run"))
            ]
            if not installs:
                continue
            setup = next(
                s for s in job["steps"] if "setup-python" in str(s.get("uses", ""))
            )
            if _grades(job):
                grading += 1
                assert installs == ["pip install -r requirements-autograde.txt"], (
                    name,
                    job_name,
                )
                # A grading job saves its cache AFTER the students' code has run, and pip
                # installs from a cache without a hash check - so it never names one.
                assert "cache" not in setup["with"], (name, job_name)
            else:
                core += 1
                assert installs == ["pip install -r requirements.txt"], (name, job_name)
                assert setup["with"]["cache"] == "pip"
                # Relative to $GITHUB_WORKSPACE, where the central repo is checked out.
                assert setup["with"]["cache-dependency-path"] == "requirements.txt"
    assert grading == 1  # the scheduler's autograde legs
    assert core > grading


def test_the_core_requirements_carry_no_autograder():
    core = (ROOT / "requirements.txt").read_text()
    for pkg in ("pytest", "nbconvert", "ipykernel"):
        assert not re.search(rf"^{pkg}==", core, re.MULTILINE), pkg
    extra = (ROOT / "requirements-autograde.txt").read_text()
    assert re.search(r"^-r requirements\.txt$", extra, re.MULTILINE)
    assert re.search(r"^ipykernel==", extra, re.MULTILINE)
    # The tests exercise the grader too.
    dev = (ROOT / "requirements-dev.txt").read_text()
    assert re.search(r"^-r requirements-autograde\.txt$", dev, re.MULTILINE)


_SCOPED = (
    "(github.event_name == 'repository_dispatch' "
    "&& (github.event.client_payload.driver == 'semester-config' "
    "|| github.event.client_payload.driver == 'migrate') "
    "&& github.event.client_payload.semester_org || '')"
)


def _block(run: str, first: str, last: str) -> str:
    lines = run.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(first))
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == last)
    return "\n".join(lines[start : end + 1])


def _args_under_bash(script: str, env: dict) -> list[str]:
    out = subprocess.run(
        # `bash -e`, which is how GitHub runs a `run:` block.
        ["bash", "-e", "-c", f'{script}\nprintf "%s\\n" "${{args[@]}}"'],
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def test_a_config_push_run_releases_into_its_own_semester_only():
    # A semester-config push (driver=semester-config, semester_org=<its org>) also fires
    # Sync site, so its run takes that one semester and leaves the site to Sync site's queue.
    # The GitHub cron and the ds01 timer (no semester) still walk every semester.
    release = _jobs_of(ALL_RENDERED["scheduler"])["release"]
    listing, step = (
        next(s for s in release["steps"] if s.get("id") == "semesters"),
        next(s for s in release["steps"] if "DRIVER" in (s.get("env") or {})),
    )
    # Job-level, so the failure-issue steps' `if:` can read it.
    assert release["env"]["SCOPED"] == "${{ " + _SCOPED + " }}"
    for s in (listing, step):
        assert "SCOPED" not in (s.get("env") or {})
    choose = _block(step["run"], 'if [ -n "$SCOPED" ]', "fi")
    base = {"COURSE": "Course-Org"}
    assert _args_under_bash(choose, base | {"SCOPED": "Semester-A"}) == [
        "--course-org", "Course-Org", "--semester-org", "Semester-A",
        "--skip-autograde", "--defer-site-sync",
    ]  # fmt: skip
    assert _args_under_bash(choose, base | {"SCOPED": ""}) == [
        "--course-org", "Course-Org", "--all-semesters", "--skip-autograde",
    ]  # fmt: skip
    lines = listing["run"].splitlines()
    pick = "\n".join(line for line in lines if line.strip().startswith(("args", "[")))
    assert _args_under_bash(pick, base | {"SCOPED": "Semester-A"})[-2:] == [
        "--semester-org",
        "Semester-A",
    ]
    assert "--semester-org" not in _args_under_bash(pick, base | {"SCOPED": ""})
    # The title is what cadence reads to tell the two apart; the fire-once queue is the
    # same one whichever arrival it is.
    assert yaml.safe_load(ALL_RENDERED["scheduler"])["run-name"] == (
        "${{ " + _SCOPED + " && format('" + course.SCOPED_RUN_TITLE
        + " {0}', github.event.client_payload.semester_org) || 'Scheduled release' }}"
    )  # fmt: skip
    assert release["concurrency"]["group"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.preview != false && github.run_id || 'scheduled-release' }}"
    )


def test_a_scoped_run_neither_files_nor_closes_the_course_failure_issue():
    # A green push in semester A closing the issue semester B's fault holds open, and the next
    # full tick re-filing it with a cc and a mail, is one fault mentioned once per push.
    steps = _jobs_of(ALL_RENDERED["scheduler"])["release"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    for name in (
        "Report an unattended failure as an issue",
        "Close the failure issue once a run succeeds",
    ):
        assert "&& env.SCOPED == ''" in by_name[name]["if"], name
    # The mail rides on the notice step's own output, so it is gated with it.
    mail = by_name["Email the maintainer the failed step's log"]
    assert "steps.notice.outputs.report == 'true'" in mail["if"]
    # Every other workflow's reporting is untouched.
    assert "env.SCOPED" not in ALL_RENDERED["sync_site"]


@pytest.mark.parametrize(
    "semester, everyone, expected",
    [
        ("", "true", ["--all-semesters"]),
        ("", "false", []),
        ("Semester-A", "true", ["--semester-org", "Semester-A"]),
        ("Semester-A", "false", ["--semester-org", "Semester-A"]),
    ],
)
def test_the_ds01_membership_dispatch_can_ask_for_every_semester(
    semester, everyone, expected
):
    # ds01's hourly `sync-membership` dispatch sends {"driver": "ds01", "all_semesters":
    # true}; a semester's own dispatch names its semester, which wins over all_semesters.
    step = next(
        s
        for s in _jobs_of(ALL_RENDERED["sync_membership"])["sync-auto"]["steps"]
        if "DISPATCH_ALL" in (s.get("env") or {})
    )
    # The JSON boolean and nothing else: a string "true" or a 1 is absent.
    assert step["env"]["DISPATCH_ALL"] == (
        "${{ (toJSON(github.event.client_payload.all_semesters) == 'true' || toJSON(github.event.client_payload.all_cohorts) == 'true') }}"
    )
    script = "args=()\n" + _block(step["run"], 'case "$EVENT" in', "esac")
    env = {
        "EVENT": "repository_dispatch",
        "DISPATCH_SEMESTER": semester,
        "DISPATCH_ALL": everyone,
    }
    assert _args_under_bash(script, env) == expected


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
        "[`join`](https://github.com/hertie-nlp-e1282-f2026/join/issues/new/choose)"
        " to enrol\n"
    )
    out, was = profile_readme.retitle_renamed_org(page, "hertie-nlp-f2026")
    assert was == "hertie-nlp-e1282-f2026"
    assert "e1282-f2026" not in out
    assert out.count("hertie-nlp-f2026") == 5  # heading, prose, link text, both URLs


def test_a_course_named_after_its_slug_keeps_its_own_join_link():
    # `course_name` defaults to the org slug, so the seeded page can open on `# hertie-dl`
    # while every link names the semester org `hertie-dl-f2026`. That heading is a course
    # name, not a former org name: rewriting it once turned the Join link into
    # `github.com/hertie-dl-f2026-f2026/join`, an org that does not exist.
    page = profile_readme.render_profile_readme(
        "hertie-dl-f2026", "hertie-dl", [], True, central_ref="release"
    )
    assert page.startswith("<!--") and "\n# hertie-dl\n" in page
    assert profile_readme.retitle_renamed_org(page, "hertie-dl-f2026") == (page, None)


def test_a_page_already_naming_its_own_org_is_untouched():
    page = "# hertie-nlp-f2026.\n\nWelcome to **hertie-nlp-f2026**.\n"
    assert profile_readme.retitle_renamed_org(page, "hertie-nlp-f2026") == (page, None)


def test_an_instructor_retitled_page_is_left_alone():
    # The signal is the H1 naming an org that is not this one. A human title matches
    # nothing, so prose someone has made their own is never rewritten - which is the
    # difference between this and "the page still looks generated", the heuristic
    # _semester_profile_body's docstring rejects.
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
        out = profile_readme._semester_profile_body("new-org-f2026", [], "SEEDED")
    finally:
        profile_readme.get_file_content = original
    assert out is not None, "a rename must still be written even with no markers"
    assert "old-org-f2026" not in out


# --------------------------------------------------------- the workflows that send email

# Everything that can put an email on the wire: the codes send, off a roster push, and
# Distribute grades, the one button left that emails a whole semester.
MAIL_SENDERS = ("send_codes", "distribute_grades")

# Everything whose job env must carry the transport secrets: the senders, the scheduler,
# which mails a semester about a source it has not staged, and the Console, which runs
# every mailing op (team formation, new codes) and whose Check REPORTS whether a send
# could - status' mail-transport row reads the very same variables through
# `mailer.graph_config_from_env`, and a job that does not carry them would report "unset"
# on every org whatever the truth. Every cron's failure step mails the maintainer the log
# too (asserted per cron in test_every_unattended_run_files_and_closes_its_own_failure_issue).
MAIL_ENV_CARRIERS = MAIL_SENDERS + ("scheduler", "console")


def _secret_ref(name: str) -> str:
    """How a rendered workflow spells "read `name` out of this org's secrets"."""
    return f"${{{{ secrets.{name} }}}}"


# The mail senders that HAVE a button, i.e. the ones a preview is possible for at all.
# Send enrolment codes is deliberately not among them: it has no button and no preview -
# a push to a semester's students.csv is what fires it, and it sends for real.
MAIL_BUTTONS = ("distribute_grades",)


@pytest.mark.parametrize("name", MAIL_BUTTONS)
def test_every_mail_button_defaults_to_a_dry_run(name):
    # The entire safety rail on a button that emails a whole semester, and until now it was
    # asserted nowhere: a renderer edit flipping it would have been green.
    dry_run = workflow_inputs(ALL_RENDERED[name])["preview"]
    assert dry_run["type"] == "boolean"
    assert dry_run["default"] is True


@pytest.mark.parametrize("name", MAIL_BUTTONS)
@pytest.mark.parametrize(
    "value", ["", "true", "True", "TRUE", "yes", "1", " false", "false"]
)
def test_the_dry_run_gate_is_fail_closed_under_bash(name, value, tmp_path):
    # Executed, not string-matched: `[ "$DRY_RUN" = "true" ] && args+=(--preview)` sent for
    # real on ANY value that was not the exact lowercase string - "True", "1", a blank from
    # a renamed input. Only an explicit `false` may send.
    gate = next(
        line.strip()
        for line in ALL_RENDERED[name].splitlines()
        if "--no-preview" in line
    )
    script = f'args=()\n{gate}\nprintf "%s\\n" "${{args[@]}}"'
    out = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={"PREVIEW": value, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    expected = "--no-preview" if value == "false" else "--preview"
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
    # WHERE a fault mail goes rides in the same env as the transport that sends it: a
    # workflow carrying one and not the other can mail, and has nobody to tell.
    assert step_env[mailer.MAINTAINER_ENV] == _secret_ref(mailer.MAINTAINER_ENV)


def test_the_release_pass_itself_carries_the_transport():
    # The source-fault mail goes out from this step, not from a trailing one - so the env
    # has to be on the step that runs `dsl_course.scheduler`, and the sweep above would
    # be satisfied by the failure step's copy alone.
    steps = workflow_jobs(ALL_RENDERED["scheduler"])["release"]["steps"]
    step = next(s for s in steps if "--all-semesters" in str(s.get("run", "")))
    assert set(mailer.GRAPH_ENV) <= set(step["env"])
    assert step["env"][mailer.MAINTAINER_ENV] == _secret_ref(mailer.MAINTAINER_ENV)


# The two steps that check dsl-course.yml and the semester registry mail a fault in them to
# the course admins, so they carry the transport.
@pytest.mark.parametrize(
    ("name", "command"),
    [
        ("scheduler", "--all-semesters --skip-autograde"),
        ("sync_membership", "--check-course-config"),
    ],
)
def test_the_course_config_check_carries_the_transport(name, command):
    steps = [
        s for job in workflow_jobs(ALL_RENDERED[name]).values() for s in job["steps"]
    ]
    step = next(s for s in steps if command in str(s.get("run", "")))
    assert set(mailer.GRAPH_ENV) <= set(step["env"])


def test_a_push_to_either_course_config_file_checks_it_within_the_minute():
    # The registry is the other half of the course's own config and shares its digest
    # issue, so an edit to either has to reach the same check.
    rendered = ALL_RENDERED["sync_membership"]
    doc = yaml.safe_load(rendered)
    paths = doc.get("on", doc.get(True))["push"]["paths"]
    assert paths == ["dsl-course.yml", "semesters.yml"]
    auto = workflow_jobs(rendered)["sync-auto"]["steps"]
    step = next(
        s for s in auto if "dsl_course.sync_membership" in str(s.get("run", ""))
    )
    # Before the reconcile, which is the pass that SKIPS a course it cannot read - so the
    # digest and the mail are written whatever that then does.
    run = step["run"]
    assert run.index("--check-course-config") < run.index("dsl_course.sync_membership")


def test_the_manual_sync_button_is_not_given_the_transport():
    # Somebody is standing at that run and reads its log; the automatic job is the one
    # with nobody watching.
    steps = workflow_jobs(ALL_RENDERED["sync_membership"])["sync-dispatch"]["steps"]
    assert all(not set(mailer.GRAPH_ENV) & set(s.get("env") or {}) for s in steps)


def test_a_semester_bootstrap_forwards_the_maintainer_address_to_the_new_org():
    # New semester runs in the Console job in the COURSE org, which has the address as an
    # org secret already, and --propagate-secret is what copies it down. A semester org
    # left without it is one whose own seeded workflows fall back to the shared mailbox.
    steps = _console_steps()
    step = next(s for s in steps if "dsl_course.console" in str(s.get("run", "")))
    assert step["env"][mailer.MAINTAINER_ENV] == _secret_ref(mailer.MAINTAINER_ENV)
    op = REGISTRY["semester.bootstrap"]
    assert "--propagate-secret" in op.argv(
        Request(op.name, "prof", "Course", "Course-f2026", {}, False)
    )


@pytest.mark.parametrize("name", sorted(ALL_RENDERED))
def test_every_step_that_drives_git_authenticates_it(name):
    # `GH_TOKEN` in the env is what `gh` reads; git reads a credential helper, and only
    # `gh auth setup-git` writes one. A pushing step without it dies on the first remote
    # with `fatal: could not read Username for 'https://github.com'` - which is how a
    # Propagate run died in a real course org, and how Archive semester would have died at
    # its step 0, which IS a propagate.
    for job, spec in workflow_jobs(ALL_RENDERED[name]).items():
        for step in spec.get("steps", []):
            run = str(step.get("run", ""))
            if not any(entry in run for entry in GIT_PUSHERS):
                continue
            if any(flag in run for flag in GIT_READ_ONLY):
                continue
            assert "gh auth setup-git" in run, f"{name}:{job} -> {step.get('name')}"


def test_the_git_authentication_sweep_sees_the_pushing_buttons():
    # A typo in an entrypoint name would make the sweep above vacuous, and it is the one
    # test standing between a new pushing button and a run that dies on its first clone.
    pushing = {
        name
        for name, rendered in ALL_RENDERED.items()
        if "gh auth setup-git" in rendered
    }
    assert {
        "console",
        "sync_site",
        "publish_site",
        "scheduler",
    } <= pushing


def _console_steps() -> list[dict]:
    (job,) = workflow_jobs(ALL_RENDERED["console"]).values()
    return job["steps"]


def test_the_console_takes_one_request_and_names_only_the_actor():
    doc = yaml.safe_load(ALL_RENDERED["console"])
    assert workflow_inputs(ALL_RENDERED["console"]) == {
        "request": {
            "description": "Filled in by the Instructor Console; not for hand use",
            "required": True,
            "type": "string",
        }
    }
    assert set(_trigger(ALL_RENDERED["console"])) == {"workflow_dispatch"}
    # The run name is what every viewer of the public Actions tab reads: the actor, never
    # the op or anything else the caller typed.
    assert doc["run-name"] == "Console by ${{ github.actor }}"
    assert len(doc["jobs"]) == 1


def test_the_console_gates_before_it_fetches_anything():
    steps = _console_steps()
    gate = steps[0]
    # The same check, and the same refusal, as every other button's check-team job.
    check_team = workflow_jobs(ALL_RENDERED["distribute_grades"])["check-team"][
        "steps"
    ][0]
    assert gate["run"] == check_team["run"]
    assert gate["env"] == check_team["env"]
    assert gate["id"] == "gate"
    checkout = next(
        i for i, s in enumerate(steps) if "actions/checkout" in s.get("uses", "")
    )
    assert checkout == 1
    assert steps[checkout]["with"]["repository"] == workflows_render.CENTRAL
    # Pinned at placement to the ref THIS org runs, like every other workflow.
    placed = yaml.safe_load(
        workflows_render.for_placement(ALL_RENDERED["console"], "main")
    )
    (job,) = placed["jobs"].values()
    assert job["steps"][checkout]["with"]["ref"] == "main"
    assert any(s.get("with", {}).get("cache") == "pip" for s in steps)


def test_the_console_gate_refuses_a_caller_without_write(tmp_path):
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho read\n")
    fake.chmod(0o755)
    run = subprocess.run(
        ["bash", "-e", "-c", _console_steps()[0]["run"]],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "ACTOR": "someone",
            "REPO": "Course-Org/.github",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 1
    assert "@someone lacks write on Course-Org/.github" in run.stdout


def test_the_request_reaches_the_cli_through_env_only():
    (step,) = [s for s in _console_steps() if "dsl_course.console" in s.get("run", "")]
    assert step["env"]["REQUEST"] == "${{ inputs.request }}"
    assert 'python -m dsl_course.console --request "$REQUEST"' in step["run"]
    assert "${{" not in step["run"]


def test_a_stop_reaches_the_logged_command_at_once(tmp_path):
    # Stop signals the step's own process. Through `exec` that is Python, which stops
    # at once; a shell waiting on a pipeline held it until GitHub killed the step.
    script = (
        'exec python3 -c \'import sys, time; print("started", flush=True); '
        "time.sleep(30)'" + workflows_render._TEE_RUN_LOG
    )
    proc = subprocess.Popen(
        ["bash", "-e", "-c", script],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout.readline() == "started\n"
    proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=5)
    assert proc.returncode != 0
    assert "KeyboardInterrupt" in out
    # The log the report tails holds what the run printed.
    assert (tmp_path / "run.log").read_text() == "started\n" + out


def test_a_broken_console_run_is_reported_but_a_refused_caller_is_not(tmp_path):
    (job,) = workflow_jobs(ALL_RENDERED["console"]).values()
    steps = job["steps"]
    (opener,) = [s for s in steps if s.get("id") == "notice"]
    (mail,) = [s for s in steps if "dsl_course.notify" in s.get("run", "")]
    closer = _closer(job)
    # Every Console run is a dispatch, so the crons' "not a manual run" guard would make
    # these steps dead; what they skip instead is a run the gate refused.
    for step in (opener, mail):
        assert "workflow_dispatch" not in step["if"]
        assert "steps.gate.outcome == 'success'" in step["if"]
        # Stop in the Console cancels the run: not a broken run, so no report.
        assert "cancelled()" not in step["if"]
    assert "steps.notice.outputs.report == 'true'" in mail["if"]
    assert set(mailer.GRAPH_ENV) <= set(mail["env"])
    assert closer["if"] == "success()"
    assert _issue_title(opener) == _issue_title(closer) == '"$WORKFLOW is failing"'
    _assert_the_reported_step_writes_its_log(job, "console")
    # Executed: with nothing open, a failure files the issue.
    assert _run_issue_step(opener, tmp_path / "x", [], WORKFLOW="Console") == [
        "create --repo"
    ]


def test_the_course_page_lists_the_console_as_auto_handled():
    page = profile_readme.render_profile_readme(
        "My-Course-E1", "My Course", [], False, central_ref="main"
    )
    auto = page.split("## Workflows in this repo")[1]
    (row,) = [line for line in auto.splitlines() if "console.yml" in line]
    assert "Runs what the Instructor Console asks for" in row
    assert row.rstrip().endswith("| Auto-handled |")


def test_the_course_page_lists_every_op_with_its_command():
    # Decision 0041: the page's table is the console + CLI one, one row per registry op,
    # and links no workflow the org no longer has.
    page = profile_readme.render_profile_readme(
        "My-Course-E1", "My Course", [], False, central_ref="main"
    )
    table = page.split("## What you can do")[1].split("## Workflows in this repo")[0]
    for op in REGISTRY.values():
        (row,) = [line for line in table.splitlines() if f"| `{op.name}` |" in line]
        assert f"`python -m dsl_course {op.name} --course-org My-Course-E1" in row
        assert op.help in row
    for retired in seed.RETIRED_GITHUB_WORKFLOWS:
        assert retired.removeprefix(".github/") not in page


# The grading report's `graded` step, as the scheduler renders it.
_GRADED_STEP = next(
    s
    for s in _jobs_of(ALL_RENDERED["scheduler"])["autograde-report"]["steps"]
    if s.get("id") == "graded"
)


def _graded_outputs(work: Path, legs: str, rows: list[str]) -> tuple[dict, int]:
    """Run the `graded` step under `bash -e`. The fake `gh` answers each jobs-API call
    with the next of `rows` (the `<conclusion> <id>` its --jq filter prints) and `sleep`
    is a no-op, so the retry loop costs nothing. Returns the step's outputs and how many
    times the API was asked."""
    work.mkdir(parents=True, exist_ok=True)
    (work / "rows").write_text("".join(f"{r}\n" for r in rows))
    (work / "calls").write_text("")
    (work / "gh").write_text(
        "#!/bin/sh\n"
        'echo x >>"$WORK/calls"\n'
        'n=$(wc -l <"$WORK/calls")\n'
        'sed -n "${n}p" "$WORK/rows"\n'
    )
    (work / "sleep").write_text("#!/bin/sh\n")
    for fake in ("gh", "sleep"):
        (work / fake).chmod(0o755)
    outputs = work / "github_output"
    subprocess.run(
        # `bash -e`, which is how GitHub runs a `run:` block.
        ["bash", "-e", "-c", _GRADED_STEP["run"]],
        env={
            "PATH": f"{work}:{os.environ['PATH']}",
            "WORK": str(work),
            "GITHUB_OUTPUT": str(outputs),
            "REPO": "Course-Org/.github",
            "RUN_ID": "1",
            "ATTEMPT": "1",
            "SEMESTER": "semester-a",
            "LEGS": legs,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return _step_outputs(outputs), len((work / "calls").read_text().splitlines())


def test_a_green_grading_matrix_never_asks_the_jobs_api(tmp_path):
    # The jobs API read a just-finished leg's conclusion as null, and the report filed a
    # failure issue and mailed the maintainer for a leg that had passed. A green matrix is
    # settled in `needs:` before this job starts, so it never asks the API at all.
    assert _GRADED_STEP["env"]["LEGS"] == "${{ needs.autograde.result }}"
    assert _graded_outputs(tmp_path, "success", ["null 7"]) == (
        {"result": "success"},
        0,
    )


@pytest.mark.parametrize(
    ("rows", "outputs", "calls"),
    [
        # A lagging API is asked again rather than reported as this semester's failure.
        (["null 7", "null 7", "success 7"], {"result": "success", "job_id": "7"}, 3),
        (["failure 7"], {"result": "failure", "job_id": "7"}, 1),
        # A null that never settles is reported, not swallowed: the leg did finish.
        (["null 7"] * 9, {"result": "null", "job_id": "7"}, 5),
        # No leg for this semester is still "nothing to report", and is not retried.
        ([], {"result": "skipped"}, 1),
    ],
    ids=["lagging", "red", "stuck", "absent"],
)
def test_a_red_grading_matrix_names_its_leg_once_the_api_settles(
    tmp_path, rows, outputs, calls
):
    assert _graded_outputs(tmp_path, "failure", rows) == (outputs, calls)
