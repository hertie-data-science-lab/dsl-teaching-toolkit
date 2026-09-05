"""Dispatching a seeded workflow and waiting for it - the harness's hands.

Everything goes through `ghcli`, not `subprocess`: the retry ladder, the 120-second
per-call ceiling, the write pacer and - the point of the exercise - the
`DSL_ORG_ALLOWLIST` fence all live there, and a harness that reached round them would be
the one process in the estate running unfenced.

Dispatch is a POST rather than `gh workflow run` for the same reason: `--method POST` is
what `ghcli._is_mutating` recognises, so the fence sees it.
"""

from __future__ import annotations

import json
import re
import textwrap
import time

import yaml

from dsl_course import ghcli, workflows_render


def _release_group() -> str:
    """The queue a REAL Scheduled release lands in, read out of the renderer.

    Read rather than retyped, so that renaming it there cannot leave the harness waiting
    on a group nothing is queued in. The scheduler's group sits on its release JOB now
    that releasing and grading are two of them, and its value is an expression: a manual
    dry run gets a group per run, everything else the literal here. This harness only ever
    dispatches real passes, so the literal is the queue its runs join."""
    expression = yaml.safe_load(textwrap.dedent(workflows_render._RELEASE_CONCURRENCY))[
        "concurrency"
    ]["group"]
    literal = re.search(r"'([^']+)'\s*}}", expression)
    if not literal:
        raise RuntimeError(f"no literal group in the release job's {expression!r}")
    return literal.group(1)


# Runs in this group queue instead of overlapping (one pending run at a time), so a
# dispatch aimed at a busy Scheduled release either waits behind the tick already running
# or is dropped - `wait_for_idle` before dispatching is what makes the scheduler passes
# deterministic.
SCHEDULED_RELEASE_GROUP: str = _release_group()

# A workflow run that has not finished yet, in GitHub's vocabulary.
ACTIVE_STATUSES = frozenset(
    {"queued", "in_progress", "waiting", "requested", "pending"}
)

POLL_SECONDS = 10
# A grading tick clones and tests every submission repo; the seeded job's own budget is
# far longer, but nothing in a two-student demo cohort should take more than this.
RUN_TIMEOUT_SECONDS = 900
# Between the POST and the run appearing in the listing.
DISPATCH_TIMEOUT_SECONDS = 120

# Named at module level so a test can drive the waits with a fake clock.
_now = time.monotonic
_sleep = time.sleep


def _runs(repo: str, workflow: str, limit: int = 30) -> list[dict]:
    """The most recent runs of one workflow file, newest first."""
    payload = ghcli.gh_json(
        "api", f"repos/{repo}/actions/workflows/{workflow}/runs?per_page={limit}"
    )
    return payload.get("workflow_runs") or []


def run_ids(repo: str, workflow: str) -> set[int]:
    """The ids of `workflow`'s recent runs - the "before" of a wait for a new one."""
    return {r["id"] for r in _runs(repo, workflow)}


def busy(runs: list[dict]) -> list[dict]:
    """The runs that have not finished. Pure - the filter every wait here shares."""
    return [r for r in runs if r.get("status") in ACTIVE_STATUSES]


def wait_for_idle(repo: str, workflow: str, timeout: int = RUN_TIMEOUT_SECONDS) -> None:
    """Block until no run of `workflow` is queued or in progress.

    Called before every dispatch of Scheduled release: Actions holds exactly one pending
    run per concurrency group, so dispatching into a busy group can silently drop the
    click. The whole RUN, not just its release job - the pass this harness is about to
    dispatch reads what the one before it wrote, and the autograde job is where the
    marker lands."""
    deadline = _now() + timeout
    while busy(_runs(repo, workflow)):
        if _now() >= deadline:
            raise RuntimeError(
                f"{repo} {workflow} was still busy after {timeout}s "
                f"(concurrency group {SCHEDULED_RELEASE_GROUP})"
            )
        _sleep(POLL_SECONDS)


def dispatch(
    repo: str,
    workflow: str,
    inputs: dict[str, object],
    *,
    ref: str = "main",
    timeout: int = DISPATCH_TIMEOUT_SECONDS,
) -> int:
    """Fire `workflow` in `repo` with `inputs` and return the run id it started.

    The dispatch endpoint answers 204 with no body, so the run has to be found by
    difference: the ids present before the POST are remembered and the first new one is
    the run. Inputs are stringified because that is all a `workflow_dispatch` input ever
    is, booleans included."""
    before = {r["id"] for r in _runs(repo, workflow)}
    body = json.dumps(
        {
            "ref": ref,
            "inputs": {
                k: str(v).lower() if isinstance(v, bool) else str(v)
                for k, v in inputs.items()
            },
        }
    )
    code, out = ghcli.gh(
        "api",
        "--method",
        "POST",
        f"repos/{repo}/actions/workflows/{workflow}/dispatches",
        "--input",
        "-",
        stdin=body,
    )
    if code != 0:
        raise RuntimeError(f"could not dispatch {workflow} in {repo}: {out[:200]}")
    deadline = _now() + timeout
    while True:
        new = {r["id"] for r in _runs(repo, workflow)} - before
        if new:
            return max(new)
        if _now() >= deadline:
            raise RuntimeError(
                f"{workflow} was dispatched in {repo} but no new run appeared "
                f"within {timeout}s"
            )
        _sleep(POLL_SECONDS)


def wait_for_run(repo: str, run_id: int, timeout: int = RUN_TIMEOUT_SECONDS) -> str:
    """Block until run `run_id` completes and return its conclusion (`success`, ...)."""
    deadline = _now() + timeout
    while True:
        run = ghcli.gh_json("api", f"repos/{repo}/actions/runs/{run_id}")
        if run.get("status") == "completed":
            return run.get("conclusion") or ""
        if _now() >= deadline:
            raise RuntimeError(
                f"run {run_id} in {repo} was still {run.get('status')} after {timeout}s"
            )
        _sleep(POLL_SECONDS)


def run_log(repo: str, run_id: int) -> str:
    """The whole log of a finished run - what the privacy assertions are made against.

    `gh run view --log` rather than the API's log endpoint, which answers a redirect to a
    zip archive."""
    code, out = ghcli.gh("run", "view", str(run_id), "-R", repo, "--log")
    if code != 0:
        raise RuntimeError(
            f"could not read the log of run {run_id} in {repo}: {out[:200]}"
        )
    return out


def wait_for_push_driven_tick(
    repo: str,
    workflow: str,
    before: set[int],
    appear: int = DISPATCH_TIMEOUT_SECONDS,
    timeout: int = RUN_TIMEOUT_SECONDS,
) -> int | None:
    """Wait out the Scheduled release a cohort's own schedule.yml push starts.

    A cohort's seeded `dispatch-scheduled-release.yml` fires the course org's Scheduled
    release from every push to its schedule.yml, so this harness's schedule edits now
    drive the scheduler themselves. That run does exactly the work the pass dispatched
    next does, and it is idempotent - what it must not do is arrive in the middle of the
    stage after it. Returns the run id it waited on, or None when none appeared: the
    dispatcher is only in cohorts that have refreshed since it shipped, and its absence
    is not what this test is about."""
    deadline = _now() + appear
    while True:
        new = run_ids(repo, workflow) - before
        if new:
            run_id = max(new)
            wait_for_run(repo, run_id, timeout)
            return run_id
        if _now() >= deadline:
            return None
        _sleep(POLL_SECONDS)
