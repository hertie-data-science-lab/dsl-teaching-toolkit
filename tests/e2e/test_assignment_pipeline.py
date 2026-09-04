"""The assignment pipeline, end to end, against the demo tier.

Hand out an assignment, have a student push to it, freeze the snapshot, autograde it, and
leave the two demo orgs exactly as they were found. Everything here drives the REAL seeded
workflows: nothing is called in-process, because what this is testing is the wiring
between a click, a cron, a token and a repo - the part unit tests deliberately do not
touch.

Run it after Promote to staging and before Promote to release:

    DSL_E2E=1 \\
    DSL_ORG_ALLOWLIST=hertie-dsl-demo-course-e1234,hertie-dsl-demo-f2026 \\
    GH_TOKEN=<maintainer classic PAT, incl. delete_repo> \\
    DSL_E2E_STUDENT=<handle> \\
    DSL_E2E_STUDENT_TOKEN=<fine-grained PAT, Contents R/W on the cohort org> \\
    python3 -m pytest tests/e2e -q

Optional: `DSL_E2E_ORGS` narrows the scope (never widens it); `DSL_VERBOSE=1` makes the
harness print repo and handle names locally. Budget ~15-25 minutes of wall clock, almost
all of it waiting on Actions.

Everything this run creates is namespaced `assignment-90-<run id>`. If it dies halfway,
`python -m tests.e2e.cleanup --run-id <run id>` puts the orgs back.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from dsl_course import central, collect, course, discovery, gh_contents, ghcli, roster

from . import allowlist, cleanup, drive, estate, schedule_edit, student

if os.environ.get("DSL_E2E") != "1":
    pytest.skip("live e2e - set DSL_E2E=1", allow_module_level=True)

pytestmark = pytest.mark.e2e

# Named here rather than derived from the frozenset, because the two orgs play different
# parts: the template and the buttons live in the course org, the students and their
# submissions in the cohort org.
COURSE_ORG = "hertie-dsl-demo-course-e1234"
COHORT_ORG = "hertie-dsl-demo-f2026"

# Every seeded workflow lives in the course org's `.github` repo.
CONTROL_REPO = f"{COURSE_ORG}/.github"
NEW_ASSIGNMENT = "new-assignment.yml"
SCHEDULED_RELEASE = "scheduled-release.yml"

# The tier the demo org must be on for this to be testing what is about to be released.
EXPECTED_TIER = "staging"

SUBMISSION = "submission.py"


@dataclass(frozen=True)
class Stage:
    """One step of the pipeline, as it happened - so the assertions below read a record
    rather than re-running anything."""

    name: str
    run_id: int | None = None
    conclusion: str = ""
    log: str = ""
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Pipeline:
    run_id: str
    slug: str
    submission_repo: str
    student: str
    stages: dict[str, Stage]


# ------------------------------------------------------------------------- preflight


def _iso(when: str) -> datetime:
    """GitHub's `2026-09-04T11:22:33Z` as an aware datetime."""
    return datetime.fromisoformat(when.replace("Z", "+00:00"))


def _cohort_timezone() -> ZoneInfo:
    text = gh_contents.get_file_content(COHORT_ORG, course.CONFIG_REPO, "schedule.yml")
    return ZoneInfo(
        (yaml.safe_load(text or "") or {}).get("timezone") or "Europe/Berlin"
    )


def _declared_tier() -> str:
    """The tier the course org says it runs, resolved the way every renderer resolves it."""
    text = gh_contents.get_file_content(COURSE_ORG, ".github", "dsl-course.yml")
    declared = (yaml.safe_load(text or "") or {}).get("central_ref")
    return central.resolve_central_ref(declared, source=f"{COURSE_ORG}/.github")


def _preflight(run_id: str) -> None:
    """Refuse to start against an estate that would make the result meaningless.

    Each of these has been a wasted run: an org still on `release` tests last month's
    code; a staging branch that is not this checkout tests somebody else's; a refresh that
    has not happened since means the org's workflow FILES are older than the engine they
    would run; a missing roster row hands out to nobody; and a namespace that is not empty
    means a previous run is still lying around and its repos would be read as this one's.
    """
    allowlist.assert_fence()
    for org in (COURSE_ORG, COHORT_ORG):
        allowlist.assert_allowed(org)

    tier = _declared_tier()
    assert tier == EXPECTED_TIER, f"{COURSE_ORG} runs {tier}, not {EXPECTED_TIER}"

    tip = ghcli.gh_json("api", f"repos/{central.CENTRAL}/commits/{EXPECTED_TIER}")
    local = ghcli.git("rev-parse", "HEAD")[1].strip()
    assert tip["sha"] == local, (
        f"{EXPECTED_TIER} is at {tip['sha'][:8]} but this checkout is at {local[:8]} - "
        "promote first, or check out what you are testing"
    )

    refreshed = ghcli.gh_json(
        "api",
        f"repos/{COURSE_ORG}/.github/commits?path=.github/.last-refresh&per_page=1",
    )
    assert refreshed, "the course org has never recorded a refresh"
    assert _iso(refreshed[0]["commit"]["committer"]["date"]) > _iso(
        tip["commit"]["committer"]["date"]
    ), "the org has not refreshed since the promotion - its workflow files are stale"

    students = roster.load(COHORT_ORG) or []
    assert student.handle() in {s.github_handle for s in students}, (
        f"{student.handle()} has no row in {COHORT_ORG}'s students.csv"
    )

    for org in (COURSE_ORG, COHORT_ORG):
        clash = [
            row["name"]
            for row in discovery.list_org_repos(org)
            if cleanup.is_run_repo(row["name"], run_id)
        ]
        assert not clash, (
            f"{org} already holds this run's namespace ({len(clash)} repos)"
        )


# ----------------------------------------------------------------------- the stages


def _schedule_block(slug: str, handout: datetime, due: datetime) -> str:
    """This run's assignment, as `assignments:` wants it - the template repo in the course
    org, and a `cohort_dest_repo` that defaults to the key, so every repo this makes falls
    inside the run's namespace."""
    return "\n".join(
        [
            f"  {slug}:",
            f"    title: e2e {slug}",
            f"    course_source_repo: {slug}",
            f"    handout_datetime: {handout:%Y-%m-%dT%H:%M}",
            f"    due_datetime: {due:%Y-%m-%dT%H:%M}",
            f"    grading_datetime: {due:%Y-%m-%dT%H:%M}",
        ]
    )


def _write_schedule(slug: str, run_id: str, handout: datetime, due: datetime) -> Stage:
    """Put (or move) this run's fenced block into the cohort's schedule.yml."""
    read = gh_contents.get_file_with_sha(COHORT_ORG, course.CONFIG_REPO, "schedule.yml")
    assert read is not None, f"{COHORT_ORG} has no schedule.yml"
    text, sha = read
    edited = schedule_edit.insert_block(
        text, run_id, _schedule_block(slug, handout, due)
    )
    assert schedule_edit.put_schedule(COHORT_ORG, edited, sha)
    return Stage("schedule", detail={"due": due, "text": edited})


def _dispatch_scheduler(name: str) -> Stage:
    """One real Scheduled release tick, waited out.

    Two of these are needed, in this order, because `scheduler.run` snapshots what is
    already past its deadline BEFORE it hands anything out - so the pass that hands out
    can never be the pass that collects."""
    drive.wait_for_idle(CONTROL_REPO, SCHEDULED_RELEASE)
    run_id = drive.dispatch(CONTROL_REPO, SCHEDULED_RELEASE, {"dry_run": False})
    conclusion = drive.wait_for_run(CONTROL_REPO, run_id)
    return Stage(
        name,
        run_id=run_id,
        conclusion=conclusion,
        log=drive.run_log(CONTROL_REPO, run_id),
    )


def _walk(run_id: str) -> dict[str, Stage]:
    slug = cleanup.slug(run_id)
    tz = _cohort_timezone()
    now = datetime.now(tz)
    stages: dict[str, Stage] = {}

    # 1. New assignment - the template repo, main + solution branches.
    created = drive.dispatch(
        CONTROL_REPO,
        NEW_ASSIGNMENT,
        {
            "number": cleanup.ASSIGNMENT_NUMBER,
            "tag": run_id,
            "format": "py",
            "type": "individual",
        },
    )
    stages["new_assignment"] = Stage(
        "new_assignment",
        run_id=created,
        conclusion=drive.wait_for_run(CONTROL_REPO, created),
        log=drive.run_log(CONTROL_REPO, created),
    )

    # 2. The schedule block: handed out five minutes ago, due in twenty.
    stages["schedule"] = _write_schedule(
        slug, run_id, now - timedelta(minutes=5), now + timedelta(minutes=20)
    )

    # 3. Scheduler pass one: the handout.
    stages["handout"] = _dispatch_scheduler("handout")

    # 4. The student pushes, for real, with their own token.
    repo = f"{slug}-{student.handle()}"
    with tempfile.TemporaryDirectory() as tmp:
        sha = student.push_file(
            f"{COHORT_ORG}/{repo}",
            Path(tmp) / "clone",
            SUBMISSION,
            "print('e2e submission')\n",
            "e2e: submit",
        )
    stages["submission"] = Stage("submission", detail={"repo": repo, "sha": sha})

    # 5. Move the cutoff into the past - the only way to reach the grading pass inside a
    #    20-minute budget without adding a `now` input to the ungated cron workflow.
    stages["cutoff"] = _write_schedule(
        slug,
        run_id,
        now - timedelta(minutes=5),
        datetime.now(tz) - timedelta(minutes=1),
    )

    # 6. Scheduler pass two: snapshot, then autograde.
    stages["grading"] = _dispatch_scheduler("grading")

    # 7. What the pass left in classroom-config.
    marker = f"{collect.AUTOGRADE_DIR}/{slug}/_graded.json"
    stages["artefacts"] = Stage(
        "artefacts",
        detail={
            "snapshot": gh_contents.get_file_content(
                COHORT_ORG, course.CONFIG_REPO, collect.snapshot_path(slug)
            ),
            "marker": gh_contents.get_file_content(
                COHORT_ORG, course.CONFIG_REPO, marker
            ),
            "collaborators": ghcli.gh_json(
                "api",
                f"repos/{COHORT_ORG}/{repo}/collaborators?affiliation=direct",
                "--jq",
                "[.[].login]",
            ),
        },
    )
    return stages


@pytest.fixture(scope="module")
def pipeline():
    """Walk the pipeline ONCE; the tests below read what it recorded.

    Teardown is not optional and not conditional: it runs whether the walk finished or
    died halfway, and it asserts that the estate came back byte for byte - the repos, their
    visibility and topics, and every blob in classroom-config."""
    run_id = cleanup.new_run_id()
    _preflight(run_id)
    before = {org: estate.fingerprint(org) for org in (COURSE_ORG, COHORT_ORG)}
    try:
        stages = _walk(run_id)
        yield Pipeline(
            run_id=run_id,
            slug=cleanup.slug(run_id),
            submission_repo=stages["submission"].detail["repo"],
            student=student.handle(),
            stages=stages,
        )
    finally:
        assert cleanup.cleanup(run_id) == 0, f"cleanup of {run_id} left work undone"
        drift = {
            org: estate.diff(before[org], estate.fingerprint(org))
            for org in (COURSE_ORG, COHORT_ORG)
        }
        assert not any(drift.values()), f"the run changed the estate: {drift}"


# --------------------------------------------------------------- one row, one assertion


def test_the_template_repo_was_scaffolded(pipeline):
    assert pipeline.stages["new_assignment"].conclusion == "success"
    assert gh_contents.get_file_content(COURSE_ORG, pipeline.slug, "README.md")


def test_the_handout_pass_succeeded(pipeline):
    assert pipeline.stages["handout"].conclusion == "success"


def test_the_handout_created_the_students_repo(pipeline):
    names = {row["name"] for row in discovery.list_org_repos(COHORT_ORG)}
    assert pipeline.submission_repo in names


def test_the_student_is_a_direct_collaborator(pipeline):
    # The listing, not the permission level: the test student is an org owner, so every
    # permission query answers `admin` whether or not the grant was ever made.
    assert pipeline.student in pipeline.stages["artefacts"].detail["collaborators"]


def test_the_grading_pass_succeeded(pipeline):
    assert pipeline.stages["grading"].conclusion == "success"


def test_the_snapshot_pins_the_pushed_commit(pipeline):
    rows = collect.parse_snapshots(pipeline.stages["artefacts"].detail["snapshot"])
    assert rows[pipeline.submission_repo] == pipeline.stages["submission"].detail["sha"]


def test_the_autograde_marker_was_written(pipeline):
    # `_graded.json`, not the bare `autograde/<slug>/` directory: that is the fire-once
    # sentinel the next tick reads to decide it has nothing to do.
    assert pipeline.stages["artefacts"].detail["marker"]


def test_no_public_log_line_names_the_student(pipeline):
    # The cardinal rule of this repo: these runs happen in a PUBLIC .github repo, so a
    # handle, an email or a `<slug>-<handle>` repo name in the log publishes the roster.
    for stage in (pipeline.stages["handout"], pipeline.stages["grading"]):
        assert pipeline.student not in stage.log, f"{stage.name} named the student"
        assert pipeline.submission_repo not in stage.log


# ------------------------------------------------- stages 8-14, once Phase 1 has landed


@pytest.mark.parametrize(
    "what",
    [
        # 8. The grading sheet exists from HANDOUT, one block per enrolled student, with
        #    an `OPEN 0/n submitted` header - grades/<slug>.yml in classroom-config.
        "the sheet is created at handout",
        # 9. The grading pass fills `info.submitted`, `info.days_late` and
        #    `info.autograde`, and marks the sheet FROZEN as its last write.
        "the cutoff freezes the sheet",
        # 10. Collect submissions dispatched twice writes nothing the second time (the
        #     blob sha is unchanged), which is what makes the button safe to lean on.
        "Collect submissions is idempotent",
        # 11. The harness fills score/feedback for the test student, then
        #     distribute-grades with dry_run=true: no gradebook repo, no issue comment,
        #     no distributed.csv row.
        "a dry run distributes nothing",
        # 12. dry_run=false: exactly one gradebook changed, one Feedback issue comment
        #     carrying `<!-- dsl-grade:{hash} -->`, one distributed.csv row.
        "distribute reaches one student",
        # 13. Re-dispatched, distribute adds no second comment and no second row.
        "distribute is idempotent",
        # 14. No team-repo issue body carries a member-level field, and the registrar CSV
        #     is never printed.
        "no member field leaks into a team issue",
    ],
)
def test_grading_and_return(pipeline, what):
    pytest.skip("Phase 1 not merged yet")
