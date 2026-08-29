"""Place the run-from-repo faculty & instructors workflows into one content repo.

Split out of `seed` because `scaffold` needs it for a repo it has just made and `seed`
needs `scaffold` to converge that repo's SYSTEM-owned files - the two modules pointed at
each other for this one function. Nothing else here depends on either.
"""

from __future__ import annotations

from .gh_contents import put_files
from .log import log_err, log_ok
from .workflows_render import for_placement, render_provision, render_release

# The run-from-repo workflows push_content_workflows places in every content repo.
WORKFLOWS = (
    ".github/workflows/release-materials.yml",
    ".github/workflows/release-assignment.yml",
)

# Retired in favour of the consolidated Release materials workflow (whose course_source_path
# takes any folder or file, which is all Release code ever did) - removed from content repos
# seeded before that change, so no repo keeps a workflow whose CLI no longer exists.
RETIRED_WORKFLOWS = (".github/workflows/release-code.yml",)


def push_content_workflows(
    org: str,
    repo: str,
    cohort_orgs: list[str],
    assignments: list[str],
    central_ref: str,
) -> int:
    """Place the run-from-repo workflows in one content repo, as ONE commit.

    Both workflows are re-rendered from the same inputs and change together (a new cohort
    org, a new assignment template, an edit to the template here), so writing them file by
    file put a pair of near-identical `ci: ... wrapper` commits into a repo faculty
    actually read, for what is one logical change. put_files makes it one commit - and
    folds the retired-workflow removal into it, so retiring a workflow costs no commit of its
    own either.

    `central_ref` is the ref of the central toolkit this org's workflows check the engine
    out at (discovery.central_ref_for); it is required rather than defaulted, so a caller
    cannot place a workflow without saying which tier it is placing it at.

    Returns 1 if that commit didn't land, so refresh can report a run that didn't
    converge. It is all-or-nothing: put_files moves the branch once, at the end."""
    if not put_files(
        org,
        repo,
        {
            WORKFLOWS[0]: for_placement(
                render_release(cohort_orgs, repo), central_ref
            ).encode(),
            WORKFLOWS[1]: for_placement(
                render_provision(cohort_orgs, assignments), central_ref
            ).encode(),
        },
        "ci: refresh release workflows",
        delete=RETIRED_WORKFLOWS,
    ):
        log_err(f"release workflows not written to {org}/{repo}")
        return 1
    log_ok(f"workflows -> {org}/{repo}")
    return 0
