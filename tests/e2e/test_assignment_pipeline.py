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
harness print repo and handle names locally. Budget ~20-30 minutes of wall clock, almost
all of it waiting on Actions.

One-off setup: the test student's `grades-<handle>` repo must already exist - run
`python3 -m dsl_course.grades sync --cohort-org <cohort>` once (the Sync gradebooks button
was retired). Distribute would otherwise create it, and a repo this run created is estate
drift the teardown cannot take back - it is the student's namespace, not the run's.

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

from dsl_course import (
    central,
    collect,
    course,
    discovery,
    gh_contents,
    ghcli,
    grades,
    roster,
)

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
COLLECT_SUBMISSIONS = "collect-submissions.yml"
DISTRIBUTE_GRADES = "distribute-grades.yml"

# The tier the demo org must be on for this to be testing what is about to be released.
EXPECTED_TIER = "staging"

SUBMISSION = "submission.py"

# What the harness types into the grading sheet. The note is a SENTINEL: it is the one
# field a student must never see, so it is written on purpose and then looked for in every
# place the toolkit could leak it to.
E2E_SCORE = "88"
E2E_FEEDBACK = "e2e feedback for the student"
E2E_PRIVATE_NOTE = "e2e-private-sentinel-do-not-publish"


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

    # Distribute provisions a missing gradebook, and a repo it created is estate drift no
    # namespace sweep can undo (it is not this run's namespace - it is the student's).
    # `python3 -m dsl_course.grades sync` once by hand and this passes for ever after.
    gradebook = f"{course.GRADEBOOK_PREFIX}{student.handle()}"
    assert gradebook in {row["name"] for row in discovery.list_org_repos(COHORT_ORG)}, (
        f"{gradebook} does not exist yet - distribute would create it and the teardown "
        f"could not take it back"
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


def _schedule_block(
    slug: str, handout: datetime, due: datetime, cutoff: datetime
) -> str:
    """This run's assignment, as `assignments:` wants it - the template repo in the course
    org, and a `cohort_dest_repo` that defaults to the key, so every repo this makes falls
    inside the run's namespace.

    `cutoff` is separate from `due` because the two drive different passes: from the due
    date the cron REFRESHES the sheet and posts receipts, and only at the cutoff does it
    freeze. Collapsing them would skip the refresh entirely, which is most of what there
    is to test here."""
    return "\n".join(
        [
            f"  {slug}:",
            f"    title: e2e {slug}",
            f"    course_source_repo: {slug}",
            f"    handout_datetime: {handout:%Y-%m-%dT%H:%M}",
            f"    due_datetime: {due:%Y-%m-%dT%H:%M}",
            f"    grading_datetime: {cutoff:%Y-%m-%dT%H:%M}",
        ]
    )


def _write_schedule(
    slug: str, run_id: str, handout: datetime, due: datetime, cutoff: datetime
) -> Stage:
    """Put (or move) this run's fenced block into the cohort's schedule.yml."""
    read = gh_contents.get_file_with_sha(COHORT_ORG, course.CONFIG_REPO, "schedule.yml")
    assert read is not None, f"{COHORT_ORG} has no schedule.yml"
    text, sha = read
    edited = schedule_edit.insert_block(
        text, run_id, _schedule_block(slug, handout, due, cutoff)
    )
    assert schedule_edit.put_schedule(COHORT_ORG, edited, sha)
    return Stage("schedule", detail={"due": due, "cutoff": cutoff, "text": edited})


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


def _sheet(slug: str) -> str:
    """This assignment's grading sheet, as classroom-config holds it right now."""
    return (
        gh_contents.get_file_content(
            COHORT_ORG, course.CONFIG_REPO, grades.sheet_path(slug)
        )
        or ""
    )


def _feedback_comments(repo: str) -> list[str]:
    """Every comment on a submission repo's Feedback issue, oldest first."""
    found = grades.find_feedback_issue(COHORT_ORG, repo)
    if found is None:
        return []
    return ghcli.gh_json(
        "api",
        f"repos/{COHORT_ORG}/{repo}/issues/{found[0]}/comments?per_page=100",
        "--jq",
        "[.[].body]",
    )


def _mark_the_sheet(slug: str) -> str:
    """Type a score, a feedback paragraph and a private note into the student's block.

    A TEXT edit through the Contents API, exactly as a grader typing in the web editor
    makes one - not a re-dump from the parsed sheet, because half of what is being tested
    is that the toolkit leaves a hand-edited file alone."""
    read = gh_contents.get_file_with_sha(
        COHORT_ORG, course.CONFIG_REPO, grades.sheet_path(slug)
    )
    assert read is not None, "the grading sheet is not there to mark"
    text, sha = read
    marked = (
        text.replace("    score_individual:\n", f"    score_individual: {E2E_SCORE}\n")
        .replace(
            "    feedback_individual:\n",
            f"    feedback_individual: {E2E_FEEDBACK}\n",
        )
        .replace(
            f"    {grades.NOTES_KEY}:\n",
            f"    {grades.NOTES_KEY}: {E2E_PRIVATE_NOTE}\n",
        )
    )
    assert marked != text, "the sheet had no blank cells to type into"
    assert gh_contents.put_file(
        COHORT_ORG,
        course.CONFIG_REPO,
        grades.sheet_path(slug),
        marked.encode(),
        "e2e: mark the assignment",
        expected_sha=sha,
    )
    return marked


def _shared_state(student_handle: str) -> dict:
    """Everything a distribute touches that no run id owns - so the teardown can put it
    back, and so a test can say what changed."""
    gradebook = f"{course.GRADEBOOK_PREFIX}{student_handle}"
    return {
        "registrar": gh_contents.get_file_content(
            COHORT_ORG, course.CONFIG_REPO, grades.COHORT_CSV_NAME
        ),
        "distributed": gh_contents.get_file_content(
            COHORT_ORG, course.CONFIG_REPO, grades.DISTRIBUTED_PATH
        ),
        "grades_yml": gh_contents.get_file_content(COHORT_ORG, gradebook, "grades.yml"),
        "readme": gh_contents.get_file_content(COHORT_ORG, gradebook, "README.md"),
    }


def _distribute(name: str, dry_run: bool) -> Stage:
    """One real Distribute grades press, waited out. `silent` always: a live e2e run must
    not put a real message in a real inbox."""
    drive.wait_for_idle(CONTROL_REPO, DISTRIBUTE_GRADES)
    run_id = drive.dispatch(
        CONTROL_REPO,
        DISTRIBUTE_GRADES,
        {"cohort_org": COHORT_ORG, "dry_run": dry_run, "silent": True},
    )
    return Stage(
        name,
        run_id=run_id,
        conclusion=drive.wait_for_run(CONTROL_REPO, run_id),
        log=drive.run_log(CONTROL_REPO, run_id),
    )


def _walk(run_id: str, stages: dict[str, Stage]) -> dict[str, Stage]:
    """Drive the pipeline, recording each step INTO `stages` as it happens.

    The caller's dict, not a fresh one, because the teardown reads it: a walk that dies
    after distribute has run still has to hand back the files distribute wrote."""
    slug = cleanup.slug(run_id)
    tz = _cohort_timezone()
    now = datetime.now(tz)

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

    # 2. The schedule block: handed out five minutes ago, due in twenty, cutoff later
    #    still - so nothing has happened yet but the handout.
    handout = now - timedelta(minutes=5)
    stages["schedule"] = _write_schedule(
        slug,
        run_id,
        handout,
        now + timedelta(minutes=20),
        now + timedelta(minutes=40),
    )

    # 3. Scheduler pass one: the handout. The grading sheet is created with it.
    stages["handout"] = _dispatch_scheduler("handout")
    stages["at_handout"] = Stage("at_handout", detail={"sheet": _sheet(slug)})

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

    # 5. Move the DUE date into the past but leave the cutoff ahead - the only way to
    #    reach the refresh pass inside the budget without adding a `now` input to the
    #    ungated cron workflow.
    stages["due"] = _write_schedule(
        slug,
        run_id,
        handout,
        datetime.now(tz) - timedelta(minutes=1),
        datetime.now(tz) + timedelta(minutes=30),
    )

    # 6. Scheduler pass two: the sheet refresh, and the due-date receipt with it.
    stages["refresh"] = _dispatch_scheduler("refresh")
    stages["after_due"] = Stage(
        "after_due",
        detail={"sheet": _sheet(slug), "comments": _feedback_comments(repo)},
    )

    # 7. Collect submissions, twice: the button is the refresh on demand, and pressing it
    #    over an unchanged cohort must write nothing at all.
    before_button = _sheet(slug)
    pressed = drive.dispatch(
        CONTROL_REPO,
        COLLECT_SUBMISSIONS,
        {"cohort_org": COHORT_ORG, "course_source_repo": slug, "dry_run": False},
    )
    stages["collect_button"] = Stage(
        "collect_button",
        run_id=pressed,
        conclusion=drive.wait_for_run(CONTROL_REPO, pressed),
        log=drive.run_log(CONTROL_REPO, pressed),
        detail={"before": before_button, "after": _sheet(slug)},
    )

    # 8. Move the cutoff into the past, and take the freeze.
    stages["cutoff"] = _write_schedule(
        slug,
        run_id,
        handout,
        datetime.now(tz) - timedelta(minutes=2),
        datetime.now(tz) - timedelta(minutes=1),
    )
    stages["grading"] = _dispatch_scheduler("grading")

    # 9. What the freeze left in classroom-config.
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
            "sheet": _sheet(slug),
            "collaborators": ghcli.gh_json(
                "api",
                f"repos/{COHORT_ORG}/{repo}/collaborators?affiliation=direct",
                "--jq",
                "[.[].login]",
            ),
        },
    )

    # 10. The grader types a mark, a feedback paragraph and a private note.
    stages["marked"] = Stage("marked", detail={"sheet": _mark_the_sheet(slug)})

    # 11. Distribute, dry run: it must read everything and change nothing. The shared
    #     state is recorded FIRST, both to compare against and to hand back at teardown.
    shared_before = _shared_state(student.handle())
    stages["shared_before"] = Stage("shared_before", detail=shared_before)
    dry = _distribute("distribute_dry", dry_run=True)
    stages["distribute_dry"] = Stage(
        dry.name,
        run_id=dry.run_id,
        conclusion=dry.conclusion,
        log=dry.log,
        detail={
            "after": _shared_state(student.handle()),
            "comments": _feedback_comments(repo),
        },
    )

    # 12. Distribute, for real.
    real = _distribute("distribute", dry_run=False)
    stages["distribute"] = Stage(
        real.name,
        run_id=real.run_id,
        conclusion=real.conclusion,
        log=real.log,
        detail={
            "after": _shared_state(student.handle()),
            "comments": _feedback_comments(repo),
        },
    )

    # 13. Re-dispatched over the same marks, it must say nothing twice.
    again = _distribute("distribute_again", dry_run=False)
    stages["distribute_again"] = Stage(
        again.name,
        run_id=again.run_id,
        conclusion=again.conclusion,
        log=again.log,
        detail={
            "after": _shared_state(student.handle()),
            "comments": _feedback_comments(repo),
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
    # Held outside the try so the teardown can read what the walk got as far as recording,
    # however it ended.
    stages_recorded: dict[str, Stage] = {}
    try:
        stages = _walk(run_id, stages_recorded)
        yield Pipeline(
            run_id=run_id,
            slug=cleanup.slug(run_id),
            submission_repo=stages["submission"].detail["repo"],
            student=student.handle(),
            stages=stages,
        )
    finally:
        assert cleanup.cleanup(run_id) == 0, f"cleanup of {run_id} left work undone"
        # Distribute writes three files this run's namespace does not cover. They were
        # recorded before it ran; hand them back, or the fingerprint below is a false
        # alarm every time and a real change hides behind it.
        recorded = stages_recorded.get("shared_before")
        if recorded is not None:
            handle = student.handle()
            book = f"{course.GRADEBOOK_PREFIX}{handle}"
            assert (
                cleanup.restore_files(
                    COHORT_ORG,
                    course.CONFIG_REPO,
                    {
                        grades.COHORT_CSV_NAME: recorded.detail["registrar"],
                        grades.DISTRIBUTED_PATH: recorded.detail["distributed"],
                    },
                )
                == 0
            )
            assert (
                cleanup.restore_files(
                    COHORT_ORG,
                    book,
                    {
                        "grades.yml": recorded.detail["grades_yml"],
                        "README.md": recorded.detail["readme"],
                    },
                )
                == 0
            )
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


# ------------------------------------------------------- the sheet, from handout to freeze


def test_the_sheet_is_created_at_handout_with_the_students_row(pipeline):
    # Created empty and dated, not at the first mark: a grader has to be able to plan
    # around it, and a missing row must be visibly missing.
    sheet = grades.parse_sheet(pipeline.stages["at_handout"].detail["sheet"])
    assert pipeline.student in sheet["submissions"]
    block = sheet["submissions"][pipeline.student]
    assert set(block) >= {"info", "score_individual", grades.NOTES_KEY}
    assert block["score_individual"] is None
    assert block["info"] == {"submitted": None, "days_late": None}


def test_the_handout_sheets_header_says_open_and_nothing_submitted(pipeline):
    header = pipeline.stages["at_handout"].detail["sheet"]
    assert "# Status: OPEN - 0 of " in header
    assert "INSTRUCTOR-OWNED" in header


def test_the_due_date_fills_info_from_the_students_own_push(pipeline):
    sheet = grades.parse_sheet(pipeline.stages["after_due"].detail["sheet"])
    info = sheet["submissions"][pipeline.student]["info"]
    assert info["submitted"], "the refresh recorded no submission time"
    assert info["days_late"] == 0
    assert "# Status: OPEN - 1 of " in pipeline.stages["after_due"].detail["sheet"]


def test_the_due_date_posts_one_submission_receipt(pipeline):
    # The student is told what was recorded for them, in their own repo, before anyone
    # marks anything.
    receipts = [
        body
        for body in pipeline.stages["after_due"].detail["comments"]
        if "**Submission recorded**" in body
    ]
    assert len(receipts) == 1
    assert pipeline.stages["submission"].detail["sha"][:7] in receipts[0]
    assert "<!-- dsl-receipt:" in receipts[0]


def test_collect_submissions_over_an_unchanged_cohort_writes_nothing(pipeline):
    # The button is the refresh on demand. Pressing it must be free - byte for byte - or
    # nobody can lean on it, and every press would churn a commit in classroom-config.
    stage = pipeline.stages["collect_button"]
    assert stage.conclusion == "success"
    assert stage.detail["after"] == stage.detail["before"]


def test_the_cutoff_freezes_the_sheet(pipeline):
    sheet_text = pipeline.stages["artefacts"].detail["sheet"]
    assert "# Status: FROZEN " in sheet_text
    info = grades.parse_sheet(sheet_text)["submissions"][pipeline.student]["info"]
    assert info["submitted"] and info["days_late"] == 0


# ----------------------------------------------------------------- distribute the marks


def test_a_dry_run_distributes_nothing(pipeline):
    # The documented review step. It reads everything and writes nothing: not the
    # registrar export, not the record, not the gradebook, not a comment.
    stage = pipeline.stages["distribute_dry"]
    assert stage.conclusion == "success"
    assert stage.detail["after"] == pipeline.stages["shared_before"].detail
    before = pipeline.stages["after_due"].detail["comments"]
    assert [b for b in stage.detail["comments"] if "dsl-grade:" in b] == [
        b for b in before if "dsl-grade:" in b
    ]


def test_the_real_run_posts_exactly_one_feedback_comment(pipeline):
    stage = pipeline.stages["distribute"]
    assert stage.conclusion == "success"
    graded = [b for b in stage.detail["comments"] if "<!-- dsl-grade:" in b]
    assert len(graded) == 1
    assert E2E_FEEDBACK in graded[0]
    assert E2E_SCORE in graded[0]


def test_the_real_run_writes_the_students_private_gradebook(pipeline):
    after = pipeline.stages["distribute"].detail["after"]
    assert after["grades_yml"] and after["readme"]
    assert E2E_SCORE in after["readme"]
    assert E2E_FEEDBACK in after["readme"]
    assert pipeline.slug in after["grades_yml"]


def test_the_real_run_adds_the_column_to_the_registrar_export(pipeline):
    csv_text = pipeline.stages["distribute"].detail["after"]["registrar"] or ""
    assert csv_text.splitlines()[0].startswith("hertie_email,name,github_handle")
    assert pipeline.slug in csv_text.splitlines()[0]


def test_the_real_run_records_what_it_sent(pipeline):
    recorded = pipeline.stages["distribute"].detail["after"]["distributed"] or ""
    assert recorded.splitlines()[0] == ",".join(grades.DISTRIBUTED_HEADER)
    rows = [line for line in recorded.splitlines()[1:] if pipeline.student in line]
    assert any(f",{pipeline.slug},{grades.CHANNEL_ISSUE}," in row for row in rows)
    assert any(f",,{grades.CHANNEL_GRADEBOOK}," in row for row in rows)


def test_distribute_says_nothing_twice(pipeline):
    # A re-press after one correction must reach one student. Pressed twice over the same
    # marks it must reach nobody, and leave every byte where it was.
    again = pipeline.stages["distribute_again"]
    assert again.conclusion == "success"
    assert again.detail["after"] == pipeline.stages["distribute"].detail["after"]
    assert len(again.detail["comments"]) == len(
        pipeline.stages["distribute"].detail["comments"]
    )


# --------------------------------------------------------------------------- privacy


def test_the_private_note_reaches_nobody(pipeline):
    # `notes_not_shared_with_students` is the one field that must never leave
    # classroom-config. It is written on purpose above, so its absence here is a
    # measurement rather than an assumption.
    after = pipeline.stages["distribute"].detail["after"]
    for where, text in (
        (
            "the feedback comment",
            "\n".join(pipeline.stages["distribute"].detail["comments"]),
        ),
        ("grades.yml", after["grades_yml"] or ""),
        ("the gradebook README", after["readme"] or ""),
        ("the registrar export", after["registrar"] or ""),
    ):
        assert E2E_PRIVATE_NOTE not in text, f"the private note leaked into {where}"


def test_the_autograde_count_reaches_nobody(pipeline):
    # `info.autograde` is a count for the grader. It is never a mark and never a student's
    # field, so no student-facing artefact may carry the word at all.
    after = pipeline.stages["distribute"].detail["after"]
    for text in (
        "\n".join(pipeline.stages["distribute"].detail["comments"]),
        after["grades_yml"] or "",
        after["readme"] or "",
    ):
        assert "autograde" not in text


def test_no_public_log_line_names_the_student_anywhere(pipeline):
    # Every one of these runs in the course org's PUBLIC .github repo. A handle, an email
    # or a `<slug>-<handle>` repo name in any of them publishes the roster.
    for key in (
        "handout",
        "refresh",
        "collect_button",
        "grading",
        "distribute_dry",
        "distribute",
        "distribute_again",
    ):
        stage = pipeline.stages[key]
        assert pipeline.student not in stage.log, f"{stage.name} named the student"
        assert pipeline.submission_repo not in stage.log, f"{stage.name} named the repo"
        assert E2E_PRIVATE_NOTE not in stage.log
