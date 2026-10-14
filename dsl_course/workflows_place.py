"""Converge the run-from-repo workflows of one repo: a content repo or an assignment template.

Decision 0041 retired both run-from-repo Release workflows - releasing and handing out are
Console operations - so no repo hosts one any more, and placing is now removing: every
refresh and every scaffold deletes the retired files from repos seeded before the change
(and from a template or materials repo copied forward from one). The hosted tuples stay,
empty, so a future run-from-repo workflow has a place to go and the migration's drift check
reads one shape.

Split out of `seed` because `scaffold` needs it for a repo it has just made and `seed`
needs `scaffold` to converge that repo's SYSTEM-owned files.
"""

from __future__ import annotations

from collections.abc import Callable

from .gh_contents import put_files
from .log import log_err, log_ok
from .workflows_render import for_placement

RELEASE_MATERIALS = ".github/workflows/release-materials.yml"
RELEASE_ASSIGNMENT = ".github/workflows/release-assignment.yml"

# The run-from-repo workflows a content repo hosts, and what an assignment template hosts.
RELEASE_WORKFLOWS: tuple[str, ...] = ()
TEMPLATE_WORKFLOWS: tuple[str, ...] = ()

# Retired content-repo and template workflows, removed from repos seeded before the change.
# Emptied once every live org has refreshed (decision 0042).
RETIRED_WORKFLOWS: tuple[str, ...] = (RELEASE_MATERIALS, RELEASE_ASSIGNMENT)

# What no student repo may carry: every faculty release button, whether a repo hosts it
# today or was seeded before it was retired. `assign.withhold_from_template` strips these
# off a semester template before per-student repos generate from it, and `patch_released`
# refuses to push one. RETIRED is in the set deliberately: a course template whose nightly
# refresh has not run yet still carries the file, and a set that named only what is hosted
# TODAY would stop stripping exactly the path a retirement leaves lying around.
NEVER_IN_STUDENT_REPOS = tuple(
    dict.fromkeys(RELEASE_WORKFLOWS + TEMPLATE_WORKFLOWS + RETIRED_WORKFLOWS)
)

# Renderer per hosted path; empty while nothing is hosted.
_RENDER: dict[str, Callable[[list[str], list[str], str], str]] = {}


def content_workflow_files(
    semester_orgs: list[str],
    assignments: list[str],
    repo: str,
    central_ref: str,
    *,
    workflows: tuple[str, ...],
) -> dict[str, bytes]:
    """`{path: bytes}` of the run-from-repo workflows `repo` hosts (`workflows`), as
    `push_content_workflows` writes them - what a drift check compares with."""
    return {
        path: for_placement(
            _RENDER[path](semester_orgs, assignments, repo), central_ref
        ).encode()
        for path in workflows
    }


def push_content_workflows(
    org: str,
    repo: str,
    semester_orgs: list[str],
    assignments: list[str],
    central_ref: str,
    *,
    workflows: tuple[str, ...],
) -> int:
    """Converge one repo's run-from-repo workflows as ONE commit: write what it hosts
    (`workflows`), delete the retired ones. put_files skips identical files and absent
    deletes, so a converged repo is written nothing.

    Returns 1 if that commit didn't land, so refresh can report a run that didn't
    converge."""
    if not put_files(
        org,
        repo,
        content_workflow_files(
            semester_orgs, assignments, repo, central_ref, workflows=workflows
        ),
        "ci: refresh release workflows",
        delete=RETIRED_WORKFLOWS,
    ):
        log_err(f"release workflows not converged in {org}/{repo}")
        return 1
    log_ok(f"workflows -> {org}/{repo}")
    return 0
