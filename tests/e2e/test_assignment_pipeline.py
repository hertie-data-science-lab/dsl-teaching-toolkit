"""The assignment pipeline, end to end, against the demo tier - one shape at a time.

Hand out an assignment PER SUBMISSION SHAPE, have a student push to each one that takes a
push, freeze the snapshots, autograde, distribute the marks, and leave the two demo orgs
exactly as they were found. Everything here drives the REAL seeded workflows: nothing is
called in-process, because what this is testing is the wiring between a click, a cron, a
token and a repo - the part unit tests deliberately do not touch.

The five shapes (`shapes.SHAPES`) run SERIALLY in one pipeline, over one roster and one
cohort: each has its own slug, its own schedule entry and its own grading sheet, and they
share every tick. That is what keeps the run inside an hour - the expensive part of a
stage is the Actions run, not the assignment, so one handout pass hands out all five.

Run it after the merge to main has refreshed the demo org, and before Promote:

    DSL_E2E=1 \\
    DSL_ORG_ALLOWLIST=hertie-dsl-demo-course-e1234,hertie-dsl-demo-f2026 \\
    GH_TOKEN=<maintainer classic PAT, incl. delete_repo> \\
    DSL_E2E_STUDENT=<handle> \\
    DSL_E2E_STUDENT_TOKEN=<fine-grained PAT on the cohort org: Contents R/W, \\
                           Administration R/W> \\
    python3 -m pytest tests/e2e -q

Administration on the student's token is what the `student_choice` shape needs: the
student publishes their own repo with it, twice, and the run measures what the scheduler
does about it either side of the grading cutoff. Contents R/W is the push.

Optional: `DSL_E2E_ORGS` narrows the scope (never widens it); `DSL_VERBOSE=1` makes the
harness print repo and handle names locally. Budget ~60-75 minutes of wall clock, almost
all of it waiting on Actions - run it under `nohup`.

One-off setup: every onboarded enrolled student in the demo cohort must already have their
`grades-<handle>` gradebook - run
`python3 -c "from dsl_course import grades; grades.ensure_gradebooks('<cohort>')"` once.
The handout provisions them otherwise, and a repo this run created is estate drift the
teardown cannot take back - it is the student's namespace, not the run's.

Everything this run creates is namespaced `assignment-90-<run id>-<shape>`. If it dies
halfway, `python -m tests.e2e.cleanup --run-id <run id>` puts the orgs back.
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
    repos,
    roster,
    seed,
)
from dsl_course.log import log

from . import allowlist, cleanup, drive, estate, schedule_edit, shapes, student

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
# The tiers a demo course may run while the harness drives it: `main` between merges,
# `preview` while a branch is pinned there for inspection. Either way the tip must be THIS
# checkout, which the sha check below enforces.
EXPECTED_TIERS = ("main", "preview")

SUBMISSION = "submission.py"

# Where the cohort site keeps one page per assignment (`site.sync_site`'s collections).
ASSIGNMENT_PAGES = "_assignments"

# Ruff-clean on purpose (double quotes, trailing newline). Hooks are off for the student's
# push, so nothing lints this any more - but the file lands in a repo the maintainer may
# well clone next, and a submission that trips their formatter on arrival is noise the
# harness does not need to generate.
SUBMISSION_BODY = 'print("e2e submission")\n'

# What the harness types into the grading sheets. The note is a SENTINEL: it is the one
# field a student must never see, so it is written on purpose and then looked for in every
# place the toolkit could leak it to. The feedback names its SHAPE, because all five land
# in one gradebook README and "the feedback arrived" is only an answer if it says whose.
E2E_SCORE = "88"
E2E_PRIVATE_NOTE = "e2e-private-sentinel-do-not-publish"


def feedback_for(shape: shapes.Shape) -> str:
    """The feedback paragraph the grader types for one shape."""
    return f"e2e feedback for the {shape.name} student"


# The shapes the assertions below name one at a time. The tuple is the source of truth;
# these are here so a test can say which one it is about.
PRIVATE = shapes.BY_NAME["private"]
PUBLIC = shapes.BY_NAME["public"]
CHOICE = shapes.BY_NAME["student-choice"]
EXTERNAL = shapes.BY_NAME["external"]
SHARED = shapes.BY_NAME["shared"]


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
    student: str
    stages: dict[str, Stage]

    def slug(self, shape: shapes.Shape) -> str:
        """The assignment slug one shape was handed out under."""
        return shapes.slug(self.run_id, shape)

    def repo(self, shape: shapes.Shape) -> str:
        """The repo this run's student work lands in for one shape - `''` where the shape
        creates none at all."""
        return shape.repo(self.run_id, self.student)


# ------------------------------------------------------------------------- preflight


def _cohort_timezone() -> ZoneInfo:
    text = gh_contents.get_file_content(COHORT_ORG, course.CONFIG_REPO, "schedule.yml")
    return ZoneInfo(
        (yaml.safe_load(text or "") or {}).get("timezone") or "Europe/Berlin"
    )


def _declared_tier() -> str:
    """The tier the course org says it runs, resolved the way every renderer resolves it.

    A value that does not resolve is RETURNED as it stands rather than raised: a
    `MissingCentralRef` traceback out of here says neither what the org runs nor that it is
    the wrong thing, and the preflight assertion below says both. `staging` after
    2026-09-07 is exactly that case."""
    text = gh_contents.get_file_content(COURSE_ORG, ".github", "dsl-course.yml")
    declared = (yaml.safe_load(text or "") or {}).get("central_ref")
    try:
        return central.resolve_central_ref(declared, source=f"{COURSE_ORG}/.github")
    except central.MissingCentralRef:
        return str(declared)


CANNOT_DELETE = (
    "the token cannot delete repos - cleanup would leave the run's repos behind; "
    "use a classic PAT with delete_repo"
)

# What deletion needs, in classic-PAT vocabulary. `repo` on its own reads and writes but
# cannot remove; `delete_repo` on its own cannot see what to remove.
DELETE_SCOPES = frozenset({"repo", "delete_repo"})


def oauth_scopes(response: str) -> frozenset[str] | None:
    """The classic-PAT scopes out of a `gh api -i` response, or None if it carries no
    `X-OAuth-Scopes` header at all - which is how a FINE-GRAINED token answers, and means
    the question has to be asked a different way rather than answered `no`."""
    for line in response.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "x-oauth-scopes":
            return frozenset(s.strip() for s in value.split(",") if s.strip())
    return None


def _assert_can_delete_repos() -> None:
    """Refuse to create anything with a token that could not take it away again.

    This is the 403 that ends a run with its repos still in the org: `gh api --method
    DELETE` needs `delete_repo`, which is not in the scope set a `gh auth login` hands
    out, and nothing before the teardown asks for it."""
    code, response = ghcli.gh("api", "-i", "user")
    assert code == 0, f"cannot read the token's own scopes: {response[:200]}"
    scopes = oauth_scopes(response)
    if scopes is None:
        # A fine-grained PAT sends no scope header; what carries deletion there is the
        # Administration permission, and the only way to read that is to ask about a repo.
        code, admin = ghcli.gh(
            "api", f"repos/{COURSE_ORG}/.github", "-q", ".permissions.admin"
        )
        assert code == 0 and admin.strip() == "true", CANNOT_DELETE
        log(f"  no X-OAuth-Scopes - taking admin on {COURSE_ORG}/.github as the answer")
        return
    missing = sorted(DELETE_SCOPES - scopes)
    assert not missing, f"{CANNOT_DELETE} (missing {', '.join(missing)})"


def missing_gradebooks(students: list[roster.Student], listed: set[str]) -> int:
    """How many onboarded enrolled students have no gradebook repo yet.

    A COUNT and never the handles: the reason this is asked at all is that the handout
    provisions the missing ones itself now (gradebooks exist from onboarding, so the brief
    can point at one from day one), and a repo this run created in a STUDENT's namespace is
    drift the teardown cannot sweep - it owns only its own."""
    return sum(
        1
        for s in roster.enrolled(students)
        if s.onboarded and f"{course.GRADEBOOK_PREFIX}{s.github_handle}" not in listed
    )


def _preflight(run_id: str) -> None:
    """Refuse to start against an estate that would make the result meaningless.

    Each of these has been a wasted run: a token without `delete_repo` ends with the whole
    run still sitting in the org; an org still on `release` tests last month's code; a
    trunk that is not this checkout tests somebody else's; a workflow file in
    the org that is not the one this tip renders means the buttons this run
    presses are not the buttons under review; a missing roster row hands out to nobody;
    a cohort short of a gradebook ends in drift the teardown cannot undo;
    and a namespace that is not empty means a previous run is still lying around and its
    repos would be read as this one's.
    """
    allowlist.assert_fence()
    for org in (COURSE_ORG, COHORT_ORG):
        allowlist.assert_allowed(org)

    # Before anything is created, not after: a token that cannot delete leaves every repo
    # this run makes behind it.
    _assert_can_delete_repos()

    tier = _declared_tier()
    assert tier in EXPECTED_TIERS, (
        f"{COURSE_ORG} runs {tier}, not one of {EXPECTED_TIERS}"
    )

    tip = ghcli.gh_json("api", f"repos/{central.CENTRAL}/commits/{tier}")
    local = ghcli.git("rev-parse", "HEAD")[1].strip()
    assert tip["sha"] == local, (
        f"{tier} is at {tip['sha'][:8]} but this checkout is at {local[:8]} - "
        "`git checkout main && git pull`, or check out the SHA the demo org is running"
    )

    # The heartbeat says only that a refresh has EVER run here (see seed.HEARTBEAT_PATH):
    # its content is the date, so it is one commit a day at most, and a promotion later the
    # same day could never move it. It cannot answer "are these files current".
    assert gh_contents.get_file_content(COURSE_ORG, ".github", seed.HEARTBEAT_PATH), (
        "the course org has never recorded a refresh"
    )

    # What can: render the org's workflow set from this checkout, the way Refresh actions
    # does, and compare blob shas with what the org is holding.
    drift = estate.workflow_drift(
        COURSE_ORG, seed.github_workflow_files(COURSE_ORG, tier)
    )
    assert not drift, (
        f"org workflow {', '.join(drift)} differs from what this tip renders - "
        "run Refresh actions"
    )

    students = roster.load(COHORT_ORG) or []
    assert student.handle() in {s.github_handle for s in students}, (
        f"{student.handle()} has no row in {COHORT_ORG}'s students.csv"
    )

    # The handout provisions a missing gradebook now, and a repo it created is estate drift
    # no namespace sweep can undo (it is not this run's namespace - it is the student's).
    # Run `grades.ensure_gradebooks` once by hand and this passes for ever after.
    listed = {row["name"] for row in discovery.list_org_repos(COHORT_ORG)}
    assert f"{course.GRADEBOOK_PREFIX}{student.handle()}" in listed, (
        "the test student has no gradebook yet - the handout would create it and the "
        "teardown could not take it back; run grades.ensure_gradebooks once"
    )
    short = missing_gradebooks(students, listed)
    assert not short, (
        f"{short} onboarded student(s) in {COHORT_ORG} have no gradebook - the handout "
        f"would create them and the teardown could not take them back; run "
        f"grades.ensure_gradebooks('{COHORT_ORG}') once"
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
    """One assignment, as `assignments:` wants it - the template repo in the course org,
    and a `cohort_dest_repo` that defaults to the key, so every repo this makes falls
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


def _schedule_blocks(
    run_id: str, handout: datetime, due: datetime, cutoff: datetime
) -> str:
    """All five of this run's assignments, one entry per shape, on the same three dates.

    The same dates deliberately: the shapes differ in what a deadline MEANS to them (an
    external assignment freezes nothing, a drop box freezes a folder), and giving them one
    clock is what lets a single tick exercise every arm of the same pass."""
    return "\n".join(
        _schedule_block(shapes.slug(run_id, shape), handout, due, cutoff)
        for shape in shapes.SHAPES
    )


def _write_schedule(
    run_id: str, handout: datetime, due: datetime, cutoff: datetime
) -> Stage:
    """Put (or move) this run's fenced block into the cohort's schedule.yml.

    One fenced block for all five assignments, replaced whole on every move: the fence is
    keyed on the run, so removing it at teardown takes the whole run out in one edit
    whatever it got as far as adding.

    The push is itself a driver now: the cohort's seeded `dispatch-scheduled-release.yml`
    fires the course org's Scheduled release from every schedule.yml push, so the edit
    starts a real tick. It is waited out here rather than raced, so the pass dispatched
    next is the one whose log and artefacts the stage after it reads."""
    read = gh_contents.get_file_with_sha(COHORT_ORG, course.CONFIG_REPO, "schedule.yml")
    assert read is not None, f"{COHORT_ORG} has no schedule.yml"
    text, sha = read
    edited = schedule_edit.insert_block(
        text, run_id, _schedule_blocks(run_id, handout, due, cutoff)
    )
    before = drive.run_ids(CONTROL_REPO, SCHEDULED_RELEASE)
    assert schedule_edit.put_schedule(COHORT_ORG, edited, sha)
    driven = drive.wait_for_push_driven_tick(CONTROL_REPO, SCHEDULED_RELEASE, before)
    return Stage(
        "schedule",
        detail={"due": due, "cutoff": cutoff, "text": edited, "driven": driven},
    )


def _dispatch_scheduler(name: str) -> Stage:
    """One real Scheduled release tick, waited out.

    Three of these are needed, in this order, because `scheduler.run` snapshots what is
    already past its deadline BEFORE it hands anything out - so the pass that hands out
    can never be the pass that collects - and the due date and the cutoff drive different
    passes. Each dispatch reaches both of the workflow's jobs: the release job walks every
    cohort, the autograde job one matrix leg per cohort."""
    drive.wait_for_idle(CONTROL_REPO, SCHEDULED_RELEASE)
    run_id = drive.dispatch(CONTROL_REPO, SCHEDULED_RELEASE, {"dry_run": False})
    conclusion = drive.wait_for_run(CONTROL_REPO, run_id)
    return Stage(
        name,
        run_id=run_id,
        conclusion=conclusion,
        log=drive.run_log(CONTROL_REPO, run_id),
    )


def _scaffold(run_id: str) -> Stage:
    """Press New assignment once per shape, waiting each one out.

    SERIALLY, and that is the single biggest cost in the run: the button ends by
    re-rendering the org's own workflows (the assignment dropdowns), so two presses in
    flight at once would race each other's commit into `.github`."""
    conclusions: dict[str, str] = {}
    for shape in shapes.SHAPES:
        created = drive.dispatch(
            CONTROL_REPO,
            NEW_ASSIGNMENT,
            {
                "assignment_name": shapes.title(run_id, shape),
                "assignment_number": cleanup.ASSIGNMENT_NUMBER,
                "semester_tag": f"{run_id}-{shape.name}",
                "format": "py",
                "type": "individual",
                "team_formation": "self_select",
                "submit_via": shape.submit_via,
                "visibility": shape.visibility or "private",
                "autograde": shape.autograde,
            },
        )
        conclusions[shape.name] = drive.wait_for_run(CONTROL_REPO, created)
    return Stage("scaffold", detail={"conclusions": conclusions})


def _configure(run_id: str) -> Stage:
    """Write each template's shape into its own `grading_config.yml`, as an instructor
    would: on the solution branch, over what the form seeded.

    The form's boxes say most of it, but not all - `submit_url` is a commented line the
    instructor uncomments once they have the address - and this file is the only thing the
    engine reads. So the run states the whole shape here and asserts the file afterwards,
    rather than trusting the dropdown it clicked."""
    written: dict[str, str] = {}
    for shape in shapes.SHAPES:
        slug = shapes.slug(run_id, shape)
        text, sha = shapes.read_config(COURSE_ORG, slug)
        wanted = shapes.configure(text, shape)
        written[shape.name] = wanted
        if wanted != text:
            shapes.write_config(COURSE_ORG, slug, wanted, sha)
    return Stage("configure", detail={"configs": written})


def _sheet(slug: str) -> str:
    """This assignment's grading sheet, as classroom-config holds it right now."""
    return (
        gh_contents.get_file_content(
            COHORT_ORG, course.CONFIG_REPO, grades.sheet_path(slug)
        )
        or ""
    )


def _receipts_issue(repo: str):
    """This repo's receipts issue, as `grades` finds it: `(number, state)`, None where
    there is none (a repo with no issue, and a shape with no repo alike), or the
    `LOOKUP_FAILED` sentinel - which is deliberately NOT collapsed into None, because
    "there is no issue" is exactly what four of the five shapes are asserting."""
    return grades.find_receipts_issue(COHORT_ORG, repo) if repo else None


def _issue_comments(repo: str) -> list[str]:
    """Every comment on a submission repo's receipts issue, oldest first."""
    found = _receipts_issue(repo)
    if not isinstance(found, tuple):
        return []
    return ghcli.gh_json(
        "api",
        f"repos/{COHORT_ORG}/{repo}/issues/{found[0]}/comments?per_page=100",
        "--jq",
        "[.[].body]",
    )


def _role(repo: str, handle: str) -> str:
    """What GitHub says `handle`'s role on `repo` is - `admin`, `push`, `pull`, ...

    EFFECTIVE, which is why the assertions that read it say so: an org owner reads `admin`
    on every repo in the org whatever grant was made, and the demo test student is one.
    The grant itself is proved by `repos.direct_collaborators` instead.

    `gh` rather than `gh_json`: a jq expression that selects one STRING prints it raw, and
    a raw `admin` is not JSON."""
    code, out = ghcli.gh(
        "api",
        f"repos/{COHORT_ORG}/{repo}/collaborators/{handle}/permission",
        "--jq",
        ".role_name",
    )
    if code != 0:
        raise RuntimeError(f"could not read a role on {repo}: {out[:200]}")
    return out.strip()


PLAN_REFUSED = "plan-refused"


def _ruleset_names(repo: str) -> list[str]:
    """Every branch ruleset on `repo`, by name - or `[PLAN_REFUSED]` where the org's plan
    has no rulesets on private repos (GitHub Free), which is what every Hertie org runs
    until the Education upgrade. The toolkit warns and hands out anyway in that case, so
    the harness records the refusal rather than failing the walk on it."""
    try:
        return ghcli.gh_json(
            "api",
            f"repos/{COHORT_ORG}/{repo}/rulesets?per_page=100",
            "--jq",
            "[.[].name]",
        )
    except RuntimeError as exc:
        if repos.plan_refused_rulesets(str(exc)):
            return [PLAN_REFUSED]
        raise


def _site_page(slug: str) -> str:
    """One assignment's page on the cohort site, as the handout's site sync wrote it.

    Found by listing the collection rather than by composing the filename: the page is
    named `<ordinal>-<slug>.md`, and the ordinal is this assignment's position among ALL
    of the cohort's, which nothing here can know."""
    pages = course.pages_repo(COHORT_ORG)
    names = ghcli.gh_json(
        "api",
        f"repos/{COHORT_ORG}/{pages}/contents/{ASSIGNMENT_PAGES}",
        "--jq",
        "[.[].name]",
    )
    found = [name for name in names if name.endswith(f"-{slug}.md")]
    assert len(found) == 1, f"{slug} has {len(found)} page(s) in {pages}"
    return (
        gh_contents.get_file_content(
            COHORT_ORG, pages, f"{ASSIGNMENT_PAGES}/{found[0]}"
        )
        or ""
    )


def _per_shape(what) -> dict[str, object]:
    """`{shape name: what(shape)}` - the shape of every reading this run records, so a
    failure names the shape it is about rather than a repo."""
    return {shape.name: what(shape) for shape in shapes.SHAPES}


def _listing() -> dict[str, dict]:
    """The cohort org's repos right now, keyed by name - visibility included."""
    return {row["name"]: row for row in discovery.list_org_repos(COHORT_ORG)}


def _visibility(listing: dict[str, dict], repo: str) -> str:
    """What the listing says a repo's visibility is, or `''` when it has no such repo."""
    return (listing.get(repo) or {}).get("visibility", "")


def _mark_the_sheet(slug: str, feedback: str) -> str:
    """Type a score, a feedback paragraph and a private note into the student's block.

    A TEXT edit through the Contents API, exactly as a grader typing in the web editor
    makes one - not a re-dump from the parsed sheet, because half of what is being tested
    is that the toolkit leaves a hand-edited file alone."""
    read = gh_contents.get_file_with_sha(
        COHORT_ORG, course.CONFIG_REPO, grades.sheet_path(slug)
    )
    assert read is not None, f"{slug}'s grading sheet is not there to mark"
    text, sha = read
    marked = (
        text.replace("    score_individual:\n", f"    score_individual: {E2E_SCORE}\n")
        .replace("    feedback_individual:\n", f"    feedback_individual: {feedback}\n")
        .replace(
            f"    {grades.NOTES_KEY}:\n",
            f"    {grades.NOTES_KEY}: {E2E_PRIVATE_NOTE}\n",
        )
    )
    assert marked != text, f"{slug}'s sheet had no blank cells to type into"
    assert gh_contents.put_file(
        COHORT_ORG,
        course.CONFIG_REPO,
        grades.sheet_path(slug),
        marked.encode(),
        "e2e: mark the assignment",
        expected_sha=sha,
    )
    return marked


def _shared_state(student_handle: str) -> dict[str, bytes | None]:
    """Everything a distribute touches that no run id owns - so the teardown can put it
    back, and so a test can say what changed.

    BYTES (`cleanup.file_bytes`), because this is both halves of the estate proof: what
    the teardown hands back, and what "changed nothing" is measured against. The estate
    check compares blob shas, and a round trip through text is not the same blob - see
    `file_bytes` for what it loses."""
    gradebook = f"{course.GRADEBOOK_PREFIX}{student_handle}"
    return {
        "registrar": cleanup.file_bytes(
            COHORT_ORG, course.CONFIG_REPO, grades.COHORT_CSV_NAME
        ),
        "distributed": cleanup.file_bytes(
            COHORT_ORG, course.CONFIG_REPO, grades.DISTRIBUTED_PATH
        ),
        "grades_yml": cleanup.file_bytes(COHORT_ORG, gradebook, "grades.yml"),
        "readme": cleanup.file_bytes(COHORT_ORG, gradebook, "README.md"),
    }


def _lock_state() -> bytes | None:
    """`assignments.lock.yml` as it stands, or None if the cohort has none yet.

    Read BEFORE the walk, unlike everything in `_shared_state`: the handout is what moves
    this file (`assign.provision_all` ends in `grades.write_team_lock`, and every schedule
    push dispatches the membership sync, which writes it too), so by the time distribute
    runs it already carries this run's assignments. Recording it at step 12 would hand back
    the drift instead of the file."""
    return cleanup.file_bytes(COHORT_ORG, course.CONFIG_REPO, grades.TEAM_LOCK_PATH)


def _config_restore(
    lock: bytes | None, recorded: Stage | None
) -> dict[str, bytes | None]:
    """Every `classroom-config` file the walk moves that no run id owns, keyed by path -
    what the teardown hands back in one commit.

    Two recordings, because the two move at different points: the lock before the walk
    starts, the distribute pair at step 12. A walk that died before step 12 still has a
    lock to put back, so `recorded` may be None and the lock is unconditional.

    A value of None is a file that was ABSENT - a cohort bootstrapped before the lock
    existed - and `cleanup.restore_files` deletes those rather than writing them;
    `gh_contents.put_files` in turn drops a delete for a path that is not there, so an
    absent-then-still-absent file costs no commit."""
    files: dict[str, bytes | None] = {grades.TEAM_LOCK_PATH: lock}
    if recorded is not None:
        files[grades.COHORT_CSV_NAME] = recorded.detail["registrar"]
        files[grades.DISTRIBUTED_PATH] = recorded.detail["distributed"]
    return files


def _every_comment(stage: Stage) -> str:
    """Every issue comment one press left, across all five shapes, as one text - what the
    privacy scans are made against. Receipts, now that a mark never reaches a repo, and
    the scans stay because a receipt is still a fact about a person."""
    return "\n".join(
        body for bodies in stage.detail["comments"].values() for body in bodies
    )


def _text(recorded: bytes | None) -> str:
    """One recorded file as text, for the assertions that read words out of it."""
    return (recorded or b"").decode()


def _distribute(name: str, dry_run: bool) -> Stage:
    """One real Distribute grades press, waited out. `silent` always: a live e2e run must
    not put a real message in a real inbox.

    ONE press covers all five shapes: the button walks the cohort's assignments, so the
    shapes differ in what it does per assignment and not in how often it is pressed."""
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


def _distributed(name: str, dry_run: bool, run_id: str, who: str) -> Stage:
    """A Distribute press and everything it could have written, recorded together."""
    pressed = _distribute(name, dry_run)
    return Stage(
        pressed.name,
        run_id=pressed.run_id,
        conclusion=pressed.conclusion,
        log=pressed.log,
        detail={
            "after": _shared_state(who),
            "comments": _per_shape(lambda s: _issue_comments(s.repo(run_id, who))),
            "issues": _per_shape(lambda s: _receipts_issue(s.repo(run_id, who))),
        },
    )


def _push_submissions(run_id: str, who: str) -> Stage:
    """The student hands in, for real, with their own token - once per shape that takes a
    push.

    `external` takes none by definition (the work went to Moodle), and the drop box takes
    one into the student's OWN FOLDER, which is the whole of the shape: same push, same
    token, a path instead of a repo of one's own."""
    pushed: dict[str, dict] = {}
    for shape in shapes.SHAPES:
        if not shape.collects_commits:
            continue
        repo = shape.repo(run_id, who)
        path = f"{shape.folder(who)}{SUBMISSION}"
        with tempfile.TemporaryDirectory() as tmp:
            sha = student.push_file(
                f"{COHORT_ORG}/{repo}",
                Path(tmp) / "clone",
                path,
                SUBMISSION_BODY,
                "e2e: submit",
            )
        pushed[shape.name] = {"repo": repo, "path": path, "sha": sha}
    return Stage("submission", detail={"pushed": pushed})


def _walk(run_id: str, stages: dict[str, Stage]) -> dict[str, Stage]:
    """Drive the pipeline, recording each step INTO `stages` as it happens.

    The caller's dict, not a fresh one, because the teardown reads it: a walk that dies
    after distribute has run still has to hand back the files distribute wrote."""
    who = student.handle()
    tz = _cohort_timezone()
    now = datetime.now(tz)
    private_slug = shapes.slug(run_id, PRIVATE)

    # 1. New assignment, five times - one template repo per shape, main + solution.
    stages["scaffold"] = _scaffold(run_id)

    # 2. Each template's own definition, as the instructor leaves it. BEFORE the handout:
    #    the shape is read when the assignment is handed out, and editing it afterwards
    #    moves nothing that has already been created.
    stages["configure"] = _configure(run_id)

    # 3. The schedule block: handed out five minutes ago, due in twenty, cutoff later
    #    still - so nothing has happened yet but the handout.
    handout = now - timedelta(minutes=5)
    stages["schedule"] = _write_schedule(
        run_id, handout, now + timedelta(minutes=20), now + timedelta(minutes=40)
    )

    # 4. Scheduler pass one: the handout of all five. The grading sheets come with it.
    stages["handout"] = _dispatch_scheduler("handout")
    listing = _listing()
    stages["at_handout"] = Stage(
        "at_handout",
        detail={
            "listing": listing,
            "sheets": _per_shape(lambda s: _sheet(shapes.slug(run_id, s))),
            "issues": _per_shape(lambda s: _receipts_issue(s.repo(run_id, who))),
            "pages": _per_shape(lambda s: _site_page(shapes.slug(run_id, s))),
            # The gradebook the marks will land in, asked for BEFORE anything distributes:
            # that is the promise the external brief makes on day one.
            "gradebook": f"{course.GRADEBOOK_PREFIX}{who}" in listing,
        },
    )

    # 5. The student pushes, for real, with their own token.
    stages["submission"] = _push_submissions(run_id, who)

    # 6. ...and publishes the one repo that is theirs to publish, while the grading cutoff
    #    is still ahead. The next tick has to close it again.
    choice_repo = CHOICE.repo(run_id, who)
    stages["published_early"] = Stage(
        "published_early",
        detail={"ok": student.set_visibility(COHORT_ORG, choice_repo, "public")},
    )

    # 7. Move the DUE date into the past but leave the cutoff ahead - the only way to
    #    reach the refresh pass inside the budget without adding a `now` input to the
    #    ungated cron workflow.
    stages["due"] = _write_schedule(
        run_id,
        handout,
        datetime.now(tz) - timedelta(minutes=1),
        datetime.now(tz) + timedelta(minutes=30),
    )

    # 8. Scheduler pass two: the sheet refresh, the due-date receipts, and the
    #    re-privatising of anything published before its cutoff.
    stages["refresh"] = _dispatch_scheduler("refresh")
    stages["after_due"] = Stage(
        "after_due",
        detail={
            "listing": _listing(),
            "sheets": _per_shape(lambda s: _sheet(shapes.slug(run_id, s))),
            "comments": _per_shape(lambda s: _issue_comments(s.repo(run_id, who))),
        },
    )

    # 9. Collect submissions, twice: the button is the refresh on demand, and pressing it
    #    over an unchanged cohort must write nothing at all.
    before_button = _sheet(private_slug)
    pressed = drive.dispatch(
        CONTROL_REPO,
        COLLECT_SUBMISSIONS,
        {
            "cohort_org": COHORT_ORG,
            "course_source_repo": private_slug,
            "dry_run": False,
        },
    )
    stages["collect_button"] = Stage(
        "collect_button",
        run_id=pressed,
        conclusion=drive.wait_for_run(CONTROL_REPO, pressed),
        log=drive.run_log(CONTROL_REPO, pressed),
        detail={"before": before_button, "after": _sheet(private_slug)},
    )

    # 10. Move the cutoff into the past. From here on the toolkit owes the student_choice
    #     repo nothing, so publishing it AFTER this edit has landed - and before the tick
    #     that freezes - is the other half of the promise: this one stands.
    stages["cutoff"] = _write_schedule(
        run_id,
        handout,
        datetime.now(tz) - timedelta(minutes=2),
        datetime.now(tz) - timedelta(minutes=1),
    )
    stages["published_late"] = Stage(
        "published_late",
        detail={"ok": student.set_visibility(COHORT_ORG, choice_repo, "public")},
    )
    stages["grading"] = _dispatch_scheduler("grading")

    # 11. What the freeze left in classroom-config, and what the org looks like after it.
    stages["artefacts"] = Stage(
        "artefacts",
        detail={
            "listing": _listing(),
            "snapshots": _per_shape(
                lambda s: gh_contents.get_file_content(
                    COHORT_ORG,
                    course.CONFIG_REPO,
                    collect.snapshot_path(shapes.slug(run_id, s)),
                ),
            ),
            "markers": _per_shape(
                lambda s: gh_contents.get_file_content(
                    COHORT_ORG,
                    course.CONFIG_REPO,
                    f"{collect.AUTOGRADE_DIR}/{shapes.slug(run_id, s)}/_graded.json",
                ),
            ),
            "sheets": _per_shape(lambda s: _sheet(shapes.slug(run_id, s))),
            "collaborators": _per_shape(
                lambda s: (
                    sorted(
                        repos.direct_collaborators(COHORT_ORG, s.repo(run_id, who))
                        or ()
                    )
                    if s.repo(run_id, who)
                    else []
                ),
            ),
            "roles": _per_shape(
                lambda s: (
                    _role(s.repo(run_id, who), who) if s.repo(run_id, who) else ""
                ),
            ),
            "rulesets": _ruleset_names(SHARED.repo(run_id, who)),
        },
    )

    # 12. The grader types a mark, a feedback paragraph and a private note into all five.
    stages["marked"] = Stage(
        "marked",
        detail={
            "sheets": _per_shape(
                lambda s: _mark_the_sheet(shapes.slug(run_id, s), feedback_for(s))
            )
        },
    )

    # 13. Distribute, dry run: it must read everything and change nothing. The shared
    #     state is recorded FIRST, both to compare against and to hand back at teardown.
    stages["shared_before"] = Stage("shared_before", detail=_shared_state(who))
    stages["distribute_dry"] = _distributed("distribute_dry", True, run_id, who)

    # 14. Distribute, for real, and again over the same marks: it must say nothing twice.
    stages["distribute"] = _distributed("distribute", False, run_id, who)
    stages["distribute_again"] = _distributed("distribute_again", False, run_id, who)
    return stages


@pytest.fixture(scope="module")
def pipeline():
    """Walk the pipeline ONCE; the tests below read what it recorded.

    Teardown is not optional and not conditional: it runs whether the walk finished or
    died halfway, and it asserts that the estate came back byte for byte - the repos, their
    visibility and topics, every blob in classroom-config, and the org-level workflow set,
    which the run's own templates rewrote and cleanup re-renders."""
    run_id = cleanup.new_run_id()
    _preflight(run_id)
    before = {org: estate.fingerprint(org) for org in (COURSE_ORG, COHORT_ORG)}
    # Recorded at the same instant as the fingerprint, because that is what the assertion
    # below measures against: the handout adds this run's assignments to the lock file, and
    # cleanup cannot sweep a file no run id owns. In production the removal push dispatches
    # the membership sync, which rewrites it within the minute; here the check runs the
    # moment cleanup returns.
    lock_before = _lock_state()
    # Held outside the try so the teardown can read what the walk got as far as recording,
    # however it ended.
    stages_recorded: dict[str, Stage] = {}
    try:
        stages = _walk(run_id, stages_recorded)
        yield Pipeline(run_id=run_id, student=student.handle(), stages=stages)
    finally:
        assert cleanup.cleanup(run_id) == 0, f"cleanup of {run_id} left work undone"
        # The handout and distribute between them move four files this run's namespace
        # does not cover. They were recorded before they moved; hand them back, or the
        # fingerprint below is a false alarm every time and a real change hides behind it.
        recorded = stages_recorded.get("shared_before")
        assert (
            cleanup.restore_files(
                COHORT_ORG, course.CONFIG_REPO, _config_restore(lock_before, recorded)
            )
            == 0
        )
        if recorded is not None:
            book = f"{course.GRADEBOOK_PREFIX}{student.handle()}"
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


# ----------------------------------------------------------------- every shape, at once


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_every_shape_got_its_template(pipeline, shape):
    assert pipeline.stages["scaffold"].detail["conclusions"][shape.name] == "success"
    assert gh_contents.get_file_content(COURSE_ORG, pipeline.slug(shape), "README.md")


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_every_template_declares_the_shape_it_was_handed_out_as(pipeline, shape):
    # Read back off the SOLUTION branch with the engine's own reader, not from the text
    # the harness held: a config written to the wrong branch, or one the parser drops,
    # hands out the DEFAULT shape - and every assertion below it would then be about
    # `github/private` wearing another name.
    spec = grades.load_grading_spec(COURSE_ORG, pipeline.slug(shape))
    assert spec.submit_via == shape.submit_via
    assert spec.submit_shape == shape.key
    assert spec.submit_url == shape.submit_url


def test_the_handout_pass_succeeded(pipeline):
    assert pipeline.stages["handout"].conclusion == "success"


def test_the_grading_pass_succeeded(pipeline):
    assert pipeline.stages["grading"].conclusion == "success"


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_the_handout_created_exactly_the_repos_the_shape_promises(pipeline, shape):
    # THE difference between the shapes, in one assertion: a repo per unit, one drop box
    # for the cohort, or nothing at all.
    listing = pipeline.stages["at_handout"].detail["listing"]
    slug = pipeline.slug(shape)
    mine = sorted(n for n in listing if n == slug or n.startswith(f"{slug}-"))
    repo = pipeline.repo(shape)
    if not repo:
        assert mine == [], f"an external assignment created {mine}"
        return
    # The frozen cohort-side template the brief lives in, plus the one repo this shape
    # hands the student - and in a two-student demo cohort, the other student's.
    assert slug in mine
    assert repo in mine


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_a_feedback_issue_exists_only_where_the_shape_has_one(pipeline, shape):
    # DERIVED, never configured: the issue lives in the unit's own repo, so it exists
    # exactly where there is one that only that unit can read. Asked twice - at the
    # handout, where the issue is opened, and after distribute, which is the other thing
    # that could open one.
    for when in ("at_handout", "distribute"):
        found = pipeline.stages[when].detail["issues"][shape.name]
        if shape.has_receipts_issue:
            assert isinstance(found, tuple), (
                f"{shape.name} has no receipts issue ({when})"
            )
        else:
            assert found is None, f"{shape.name} was given a receipts issue ({when})"


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_every_shape_gets_a_grading_sheet_with_the_students_row(pipeline, shape):
    # Including `external`, which creates no repo at all: the sheet is how it is marked,
    # and a shape with nothing to clone still has a cohort to grade.
    sheet = grades.parse_sheet(
        pipeline.stages["at_handout"].detail["sheets"][shape.name]
    )
    assert pipeline.student in sheet["submissions"]


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_every_shape_writes_its_feedback_into_the_students_gradebook(pipeline, shape):
    # THE channel. Every shape has it and no shape has another.
    readme = _text(pipeline.stages["distribute"].detail["after"]["readme"])
    assert feedback_for(shape) in readme
    assert pipeline.slug(shape) in _text(
        pipeline.stages["distribute"].detail["after"]["grades_yml"]
    )


@pytest.mark.parametrize("shape", shapes.SHAPES, ids=lambda s: s.name)
def test_no_shape_records_an_issue_channel(pipeline, shape):
    # `distributed.csv` is the record of what was SENT, so it is where "nothing was
    # posted into a repo" is proved rather than inferred. The private shape HAS a thread
    # and is in this list with the rest: having one is no longer a reason to use one.
    recorded = _text(pipeline.stages["distribute"].detail["after"]["distributed"])
    rows = [line for line in recorded.splitlines()[1:] if pipeline.student in line]
    assert not [
        r for r in rows if f",{pipeline.slug(shape)},{grades.CHANNEL_ISSUE}," in r
    ]


# --------------------------------------------------- github/private: the shape that was


def test_the_student_is_a_direct_collaborator(pipeline):
    # The listing, not the permission level: the test student is an org owner, so every
    # permission query answers `admin` whether or not the grant was ever made.
    assert (
        pipeline.student.casefold()
        in (pipeline.stages["artefacts"].detail["collaborators"][PRIVATE.name])
    )


def test_the_snapshot_pins_the_pushed_commit(pipeline):
    rows = collect.parse_snapshots(
        pipeline.stages["artefacts"].detail["snapshots"][PRIVATE.name]
    )
    pushed = pipeline.stages["submission"].detail["pushed"][PRIVATE.name]
    assert rows[pipeline.repo(PRIVATE)] == pushed["sha"]


def test_the_freeze_timed_the_submission_by_githubs_own_push_record(pipeline):
    # The live proof that the bot token may read `GET /repos/{o}/{r}/activity` on a
    # PRIVATE submission repo. That endpoint is the one rung of `collect._submitted`
    # nobody can write - a committer date is client-supplied, so a student can set it to
    # whatever they like - and it is the rung every late penalty rests on. It is also the
    # one thing no unit test can show: a 403 or a 404 there falls straight through to the
    # committer date, and the run would still pass every other assertion in this file.
    row = collect.parse_snapshot_rows(
        pipeline.stages["artefacts"].detail["snapshots"][PRIVATE.name]
    )[pipeline.repo(PRIVATE)]
    assert row.submitted_at
    assert row.submitted_source == collect.SUBMITTED_SOURCE_PUSH, (
        f"the freeze recorded {row.submitted_source!r}, not "
        f"{collect.SUBMITTED_SOURCE_PUSH!r} - the bot could not read the repository "
        f"activity of a private submission repo, so the student timed their own hand-in"
    )
    # The sheet's half of the same fact. `info.submitted_note` exists only for the two
    # rungs built from the student's clock - `COMMIT_ONLY_NOTE` and `SUSPECT_NOTE` - so a
    # row GitHub timed carries none at all.
    info = grades.parse_sheet(
        pipeline.stages["artefacts"].detail["sheets"][PRIVATE.name]
    )["submissions"][pipeline.student]["info"]
    assert "submitted_note" not in info, info.get("submitted_note")


def test_the_autograde_marker_was_written(pipeline):
    # `_graded.json`, not the bare `autograde/<slug>/` directory: that is the fire-once
    # sentinel the next tick reads to decide it has nothing to do. Only the shape that
    # asked New assignment for hidden tests has one.
    markers = pipeline.stages["artefacts"].detail["markers"]
    assert markers[PRIVATE.name]
    assert not markers[EXTERNAL.name]


def test_the_sheet_is_created_at_handout_with_the_students_row(pipeline):
    # Created empty and dated, not at the first mark: a grader has to be able to plan
    # around it, and a missing row must be visibly missing.
    sheet = grades.parse_sheet(
        pipeline.stages["at_handout"].detail["sheets"][PRIVATE.name]
    )
    block = sheet["submissions"][pipeline.student]
    assert set(block) >= {"info", "score_individual", grades.NOTES_KEY}
    assert block["score_individual"] is None
    # `info:` carries exactly the facts THIS assignment's toolkit will fill, and
    # `autograde` exists only where hidden tests will run. The run asks New assignment for
    # autograding, but the shape is read off the template's own grading config rather than
    # written down here twice.
    expected = {"submitted": None, "days_late": None}
    if grades.load_grading_spec(COURSE_ORG, pipeline.slug(PRIVATE)).autograde:
        expected["autograde"] = None
    assert block["info"] == expected


def test_the_handout_sheets_header_says_open_and_nothing_submitted(pipeline):
    header = pipeline.stages["at_handout"].detail["sheets"][PRIVATE.name]
    assert "# Status: OPEN - 0 of " in header
    assert "INSTRUCTOR-OWNED" in header


def test_the_due_date_fills_info_from_the_students_own_push(pipeline):
    sheet = grades.parse_sheet(
        pipeline.stages["after_due"].detail["sheets"][PRIVATE.name]
    )
    info = sheet["submissions"][pipeline.student]["info"]
    assert info["submitted"], "the refresh recorded no submission time"
    # `parse_sheet` hands back what the file says, as text (`grades._SheetLoader`): a mark
    # a grader typed must come back the way they typed it, so `0` here is "0".
    assert int(info["days_late"]) == 0
    # The refresh reads no push records (`_provisional_pins` says why), so the only note
    # it can write is `SUSPECT_NOTE` - the pinned commit dated before the push that
    # delivered it. A genuine push seconds ago earns none, and `days_late: 0` above is
    # therefore a time the grader can act on. The push rung itself is proved at the
    # freeze, where it is decided once and written down.
    assert "submitted_note" not in info, info.get("submitted_note")
    assert (
        "# Status: OPEN - 1 of "
        in (pipeline.stages["after_due"].detail["sheets"][PRIVATE.name])
    )


def test_the_due_date_posts_one_submission_receipt(pipeline):
    # The student is told what was recorded for them, in their own repo, before anyone
    # marks anything.
    receipts = [
        body
        for body in pipeline.stages["after_due"].detail["comments"][PRIVATE.name]
        if "**Submission recorded**" in body
    ]
    assert len(receipts) == 1
    pushed = pipeline.stages["submission"].detail["pushed"][PRIVATE.name]
    assert pushed["sha"][:7] in receipts[0]
    assert "<!-- dsl-receipt:" in receipts[0]


def test_only_a_shape_with_a_thread_gets_a_receipt(pipeline):
    # The receipt rides on the receipts issue, so the four shapes without one are silent
    # by construction. Measured rather than assumed: `_post_receipts` asks the live
    # visibility too, and a receipt posted into a public repo is a hand-in time published.
    for shape in shapes.SHAPES:
        if shape is PRIVATE:
            continue
        assert pipeline.stages["after_due"].detail["comments"][shape.name] == []


def test_collect_submissions_over_an_unchanged_cohort_writes_nothing(pipeline):
    # The button is the refresh on demand. Pressing it must be free - byte for byte - or
    # nobody can lean on it, and every press would churn a commit in classroom-config.
    stage = pipeline.stages["collect_button"]
    assert stage.conclusion == "success"
    assert stage.detail["after"] == stage.detail["before"], (
        "the press rewrote the sheet: "
        f"{_status(stage.detail['before'])!r} -> {_status(stage.detail['after'])!r}"
    )


def test_the_cutoff_freezes_the_sheet(pipeline):
    sheet_text = pipeline.stages["artefacts"].detail["sheets"][PRIVATE.name]
    assert "# Status: FROZEN " in sheet_text
    info = grades.parse_sheet(sheet_text)["submissions"][pipeline.student]["info"]
    assert info["submitted"]
    assert int(info["days_late"]) == 0  # text, like every scalar on the sheet


# ------------------------------------------------------------------------ public repos


def test_a_public_assignment_hands_out_a_public_repo(pipeline):
    # `POST /generate` takes `private` and nothing else, so this is the PATCH that follows
    # it - the one step between "the config says public" and a repo anybody can read.
    listing = pipeline.stages["at_handout"].detail["listing"]
    assert _visibility(listing, pipeline.repo(PUBLIC)) == "public"
    # And its cohort-side template is not: the frozen hand-out carries the brief and, for
    # an autograded assignment, would carry the tests.
    assert _visibility(listing, pipeline.slug(PUBLIC)) == "private"


def test_a_public_assignments_page_says_which_shape_it_is(pipeline):
    # ONE word in the front matter, because the theme `case`s on it: a page that said
    # nothing told a public cohort exactly what it told a private one.
    page = pipeline.stages["at_handout"].detail["pages"][PUBLIC.name]
    assert f'submit_shape: "{PUBLIC.key}"' in page
    assert PUBLIC.key == "assignment-repo-public"


def test_a_public_assignments_feedback_goes_nowhere_but_the_gradebook(pipeline):
    # The pair that makes the shape safe: nothing at all in a repo the internet can read,
    # and the marks in the private gradebook instead.
    assert pipeline.stages["distribute"].detail["comments"][PUBLIC.name] == []
    assert feedback_for(PUBLIC) in _text(
        pipeline.stages["distribute"].detail["after"]["readme"]
    )


# --------------------------------------------------------- the student holds the flag


def test_a_student_choice_repo_is_handed_out_private(pipeline):
    listing = pipeline.stages["at_handout"].detail["listing"]
    assert _visibility(listing, pipeline.repo(CHOICE)) == "private"


def test_the_student_is_admin_of_their_own_student_choice_repo(pipeline):
    # What the shape is FOR: the flag is theirs, and nothing else in the toolkit hands a
    # student `admin` on anything. The grant itself is the direct collaborator row; the
    # role is GitHub's effective answer, which an org owner reads as `admin` regardless -
    # see `_role`. Both are asserted because between them they exclude the two ways this
    # can be wrong in a cohort of ordinary members.
    assert (
        pipeline.student.casefold()
        in (pipeline.stages["artefacts"].detail["collaborators"][CHOICE.name])
    )
    assert pipeline.stages["artefacts"].detail["roles"][CHOICE.name] == "admin"


def test_publishing_before_the_cutoff_is_undone_by_the_next_tick(pipeline):
    # The promise the assignment page makes to the REST of the cohort: until the grading
    # cutoff, a repo the world can read is a repo they can copy from. The student really
    # published it, with their own token, and the tick really closed it again.
    assert pipeline.stages["published_early"].detail["ok"]
    listing = pipeline.stages["after_due"].detail["listing"]
    assert _visibility(listing, pipeline.repo(CHOICE)) == "private"


def test_publishing_after_the_cutoff_stands(pipeline):
    # And the other half, which is what makes the first half a promise rather than a
    # policy: once the cutoff is past the toolkit never touches the flag again.
    assert pipeline.stages["published_late"].detail["ok"]
    listing = pipeline.stages["artefacts"].detail["listing"]
    assert _visibility(listing, pipeline.repo(CHOICE)) == "public"


# --------------------------------------------------------------- handed in off GitHub


def test_an_external_assignment_creates_nothing(pipeline):
    # The bug this shape exists to fix: 33 private repos in a live cohort that held
    # nothing but a receipts issue, for an assignment handed in on Moodle.
    assert pipeline.repo(EXTERNAL) == ""
    listing = pipeline.stages["at_handout"].detail["listing"]
    slug = pipeline.slug(EXTERNAL)
    assert [n for n in listing if n == slug or n.startswith(f"{slug}-")] == []


def test_the_gradebook_is_there_before_anything_distributes(pipeline):
    # Gradebooks are provisioned at ONBOARDING, not inside distribute, so the external
    # brief can point at "your gradebook" from day one - which is the only place its
    # feedback will ever appear.
    assert pipeline.stages["at_handout"].detail["gradebook"]


def test_an_external_assignments_page_carries_the_submit_button(pipeline):
    page = pipeline.stages["at_handout"].detail["pages"][EXTERNAL.name]
    assert f'submit_shape: "{EXTERNAL.key}"' in page
    assert f'submit_url: "{shapes.SUBMIT_URL}"' in page
    # The host is computed for the button's label, so the page says where it is sending
    # them rather than printing a URL at them.
    assert 'submit_host: "example.org"' in page
    # And no repo: there is none, so a page that named one would send the cohort looking
    # for a repo nobody will ever create.
    assert "repo_name:" not in page


def test_an_external_assignment_freezes_nothing(pipeline):
    # `collects_commits` is false, so the snapshot and autograde passes are skipped
    # entirely. Before that they 404ed once per student per tick, for ever.
    assert pipeline.stages["artefacts"].detail["snapshots"][EXTERNAL.name] is None


def test_an_external_assignments_feedback_reaches_the_gradebook(pipeline):
    assert pipeline.stages["distribute"].detail["issues"][EXTERNAL.name] is None
    assert feedback_for(EXTERNAL) in _text(
        pipeline.stages["distribute"].detail["after"]["readme"]
    )


# ------------------------------------------------------------- one drop box, one folder


def test_the_drop_box_is_one_private_repo_for_the_whole_cohort(pipeline):
    listing = pipeline.stages["at_handout"].detail["listing"]
    box = pipeline.repo(SHARED)
    assert box == course.shared_repo(pipeline.slug(SHARED))
    assert _visibility(listing, box) == "private"
    # ONE, not one per student: everything else named off this slug is the frozen
    # cohort-side template the brief lives in.
    slug = pipeline.slug(SHARED)
    assert sorted(n for n in listing if n.startswith(f"{slug}-")) == [box]


def test_the_student_may_push_into_the_drop_box(pipeline):
    detail = pipeline.stages["artefacts"].detail
    assert pipeline.student.casefold() in detail["collaborators"][SHARED.name]
    # The LEVEL is GitHub's effective answer and the demo student is an org owner, so
    # `admin` here is not evidence of a wrong grant - but anything below `push` is: a
    # `pull` or `triage` row is a student who cannot hand in at all.
    assert detail["roles"][SHARED.name] in ("push", "admin")


def test_the_drop_box_cannot_be_force_pushed_or_deleted(pipeline):
    # Every student in the cohort has push on this one repo. Without the ruleset, one of
    # them can erase the whole cohort's work - and the frozen snapshot then pins commits
    # that no longer exist.
    names = pipeline.stages["artefacts"].detail["rulesets"]
    # Free orgs cannot carry a ruleset on a private repo; the toolkit warns and the
    # handout stays green. Either the ruleset is there or the plan refused it - never
    # a silent absence.
    assert repos.DROP_BOX_RULESET in names or names == [PLAN_REFUSED], names


def test_the_freeze_pins_the_students_own_folder(pipeline):
    # The whole of the shape, in the snapshot: one repo, one row per unit, and the row
    # says which folder it is about. `SnapshotRow.unit` is what keys it - the folder, not
    # the repo, because every row here carries the same repo.
    rows = collect.parse_snapshot_rows(
        pipeline.stages["artefacts"].detail["snapshots"][SHARED.name]
    )
    row = rows[pipeline.student]
    pushed = pipeline.stages["submission"].detail["pushed"][SHARED.name]
    assert row.repo == pipeline.repo(SHARED)
    assert row.path == SHARED.folder(pipeline.student)
    assert row.sha == pushed["sha"]


def test_a_commit_by_the_folders_own_owner_earns_no_note(pipeline):
    # Folders are a convention, not a boundary: the freeze notes it when the last person
    # to touch a unit's folder is not one of its members. This push was the student's own,
    # so the row - and the sheet - must say nothing.
    rows = collect.parse_snapshot_rows(
        pipeline.stages["artefacts"].detail["snapshots"][SHARED.name]
    )
    assert rows[pipeline.student].note == ""
    info = grades.parse_sheet(
        pipeline.stages["artefacts"].detail["sheets"][SHARED.name]
    )["submissions"][pipeline.student]["info"]
    assert "submitted_note" not in info, info.get("submitted_note")


def test_the_drop_boxs_feedback_goes_to_the_gradebook(pipeline):
    assert pipeline.stages["distribute"].detail["issues"][SHARED.name] is None
    assert feedback_for(SHARED) in _text(
        pipeline.stages["distribute"].detail["after"]["readme"]
    )


# ----------------------------------------------------------------- distribute the marks


def test_a_dry_run_distributes_nothing(pipeline):
    # The documented review step. It reads everything and writes nothing: not the
    # registrar export, not the record, not the gradebook, not a comment.
    stage = pipeline.stages["distribute_dry"]
    assert stage.conclusion == "success"
    assert stage.detail["after"] == pipeline.stages["shared_before"].detail
    assert stage.detail["comments"] == pipeline.stages["after_due"].detail["comments"]


def test_the_real_run_posts_no_comment_on_any_repo(pipeline):
    # Marks and feedback go to ONE place, the gradebook. Measured against every shape's
    # thread, including the private one that HAS a thread and is read only by its student:
    # the press must leave it exactly as the receipts pass left it.
    stage = pipeline.stages["distribute"]
    assert stage.conclusion == "success"
    assert stage.detail["comments"] == pipeline.stages["after_due"].detail["comments"]
    for name, bodies in stage.detail["comments"].items():
        assert not [b for b in bodies if "<!-- dsl-grade:" in b], name
        assert not [b for b in bodies if feedback_for(shapes.BY_NAME[name]) in b], name
        assert not [b for b in bodies if E2E_SCORE in b], name


def test_the_real_run_writes_the_students_private_gradebook(pipeline):
    after = pipeline.stages["distribute"].detail["after"]
    assert after["grades_yml"] and after["readme"]
    assert E2E_SCORE in _text(after["readme"])


def test_the_real_run_adds_a_column_per_assignment_to_the_registrar_export(pipeline):
    csv_text = _text(pipeline.stages["distribute"].detail["after"]["registrar"])
    header = csv_text.splitlines()[0]
    assert header.startswith("hertie_email,name,github_handle")
    for shape in shapes.SHAPES:
        assert pipeline.slug(shape) in header


def test_the_real_run_records_what_it_sent(pipeline):
    recorded = _text(pipeline.stages["distribute"].detail["after"]["distributed"])
    assert recorded.splitlines()[0] == ",".join(grades.DISTRIBUTED_HEADER)
    rows = [line for line in recorded.splitlines()[1:] if pipeline.student in line]
    # The gradebook is the only channel a `silent` press records, and the only one a
    # mark reaches a student by at all.
    assert {row.split(",")[2] for row in rows} == {grades.CHANNEL_GRADEBOOK}


def test_distribute_says_nothing_twice(pipeline):
    # A re-press after one correction must reach one student. Pressed twice over the same
    # marks it must reach nobody, and leave every byte where it was.
    again = pipeline.stages["distribute_again"]
    assert again.conclusion == "success"
    assert again.detail["after"] == pipeline.stages["distribute"].detail["after"]
    assert again.detail["comments"] == pipeline.stages["distribute"].detail["comments"]


# --------------------------------------------------------------------------- privacy


def test_the_private_note_reaches_nobody(pipeline):
    # `notes_not_shared_with_students` is the one field that must never leave
    # classroom-config. It is written into all five sheets on purpose above, so its
    # absence here is a measurement rather than an assumption.
    stage = pipeline.stages["distribute"]
    after = stage.detail["after"]
    for where, text in (
        ("the issue comments", _every_comment(stage)),
        ("grades.yml", _text(after["grades_yml"])),
        ("the gradebook README", _text(after["readme"])),
        ("the registrar export", _text(after["registrar"])),
    ):
        assert E2E_PRIVATE_NOTE not in text, f"the private note leaked into {where}"


def test_the_autograde_count_reaches_nobody(pipeline):
    # `info.autograde` is a count for the grader. It is never a mark and never a student's
    # field, so no student-facing artefact may carry the word at all.
    stage = pipeline.stages["distribute"]
    after = stage.detail["after"]
    for text in (
        _every_comment(stage),
        _text(after["grades_yml"]),
        _text(after["readme"]),
    ):
        assert "autograde" not in text


# What the `check-team` gate step prints before it lets a run start: the handle of the
# person who PRESSED the button and the repo the workflow lives in. That is the
# dispatching actor - a faculty member - and naming them in a public log is the point of
# the line. It is only confusable with a student here because the harness signs in as
# both. Everything the toolkit itself prints is still scanned.
GATE_STEP = "check-team\t"
GATE_FACTS = ("ACTOR:", "REPO:")


def _toolkit_lines(log: str) -> str:
    """`log` without the gate step's own ACTOR/REPO lines."""
    return "\n".join(
        line
        for line in log.splitlines()
        if not (line.startswith(GATE_STEP) and any(f in line for f in GATE_FACTS))
    )


def _status(sheet_text: str) -> str:
    """The one header line that says what a write changed."""
    return next(
        (line for line in sheet_text.splitlines() if line.startswith("# Status:")), ""
    )


def test_no_public_log_line_names_the_student_anywhere(pipeline):
    # Every one of these runs in the course org's PUBLIC .github repo. A handle, an email
    # or a `<slug>-<handle>` repo name in any of them publishes the roster.
    #
    # The drop box is the ONE submission-repo name a public log may print in full
    # (`course.shared_repo`): its name carries no handle. So it is not scanned for - and
    # the handle scan above it is what proves that exemption is safe.
    named = [
        pipeline.repo(shape) for shape in shapes.SHAPES if shape.creates_unit_repos
    ]
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
        scanned = _toolkit_lines(stage.log)
        assert pipeline.student not in scanned, f"{stage.name} named the student"
        for repo in named:
            assert repo not in scanned, f"{stage.name} named {repo}"
        assert E2E_PRIVATE_NOTE not in scanned
