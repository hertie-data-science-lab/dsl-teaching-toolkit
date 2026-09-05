"""What the demo orgs look like, before and after - the harness's "left no trace" proof.

A pipeline run creates repos, flips topics and writes into `classroom-config`. Cleanup is
meant to undo all of it; the only way to know it did is to photograph the estate first and
compare afterwards. The fingerprint is deliberately cheap (one repo listing per org, one
recursive tree per config repo) so it can be taken twice in a 20-minute run.

`workflow_drift` answers the other estate question, asked before a run rather than after:
are the workflows the org would actually execute the ones this checkout renders?
"""

from __future__ import annotations

from pathlib import PurePosixPath

from dsl_course import course, discovery, gh_contents, ghcli, repos

# Where the seeded org-level workflows live in the course org's `.github` repo.
WORKFLOWS_DIR = ".github/workflows"

# Whatever a run touches inside classroom-config - the schedule it edits, and the
# snapshots/, autograde/ and grading_sheets/ artefacts the scheduler writes - shows up as
# a blob sha that was not there before.
CONFIG_REPO = course.CONFIG_REPO


def fingerprint(org: str) -> dict[str, dict]:
    """`{"repos": {name: {...}}, "classroom-config": {path: blob sha}}` for one org.

    `private`, `topics` and `archived` are the three fields the pipeline can change
    without adding or removing a repo: a submission repo that came back public, a lost
    `dsl-assignment` topic or an archived cohort repo are all silent until something
    compares them."""
    listing = discovery.list_org_repos(org)
    fp: dict[str, dict] = {
        "repos": {
            row["name"]: {
                "private": row["visibility"] != "public",
                "topics": sorted(row.get("topics") or []),
                "archived": bool(row.get("archived")),
            }
            for row in listing
        }
    }
    config = {}
    if CONFIG_REPO in fp["repos"]:
        branch = repos.default_branch(org, CONFIG_REPO, fallback="main")
        config = gh_contents.repo_blob_shas(org, CONFIG_REPO, branch)
    fp[CONFIG_REPO] = config
    return fp


def _flat(fp: dict[str, dict]) -> dict[str, object]:
    return {
        f"{section}/{key}": value
        for section, entries in fp.items()
        for key, value in entries.items()
    }


def diff(before: dict[str, dict], after: dict[str, dict]) -> dict[str, tuple]:
    """`{what: (before, after)}` for everything that changed - `None` where it was absent.

    Flattened to one key space so a repo that appeared, a topic that moved and a file that
    was left behind in classroom-config all read the same way in the failure message."""
    a, b = _flat(before), _flat(after)
    return {
        key: (a.get(key), b.get(key))
        for key in sorted(a.keys() | b.keys())
        if a.get(key) != b.get(key)
    }


def deployed_workflow_shas(course_org: str) -> dict[str, str]:
    """`{filename: blob sha}` for every workflow the course org's `.github` repo holds.

    One tree fetch of the workflows directory alone - not the whole repo - because the
    only thing the answer is compared with is a rendered workflow set."""
    tree = ghcli.gh_json(
        "api", f"repos/{course_org}/.github/git/trees/HEAD:{WORKFLOWS_DIR}"
    )
    return {
        entry["path"]: entry["sha"]
        for entry in tree.get("tree") or []
        if entry.get("type") == "blob"
    }


def workflow_drift(course_org: str, rendered: dict[str, bytes]) -> list[str]:
    """The workflow files where the org and `rendered` disagree - `[]` when they match.

    `rendered` is `seed.github_workflow_files(course_org, tier)`: the exact bytes a
    refresh would write, placement banner and pinned central ref included. Every input to
    that render is discovered from the org itself, so nothing here has to be told the org
    name, the course code or which cohorts exist - the comparison is byte-for-byte, over
    the whole set, with no file excused.

    A name reported here is either stale in the org, missing from it, or a retired
    workflow the org has not dropped yet. All three mean the same thing: the files the org
    would run are not the ones this checkout describes."""
    ours = {
        PurePosixPath(path).name: gh_contents.blob_sha(content)
        for path, content in rendered.items()
    }
    theirs = deployed_workflow_shas(course_org)
    return sorted(
        name
        for name in ours.keys() | theirs.keys()
        if ours.get(name) != theirs.get(name)
    )
