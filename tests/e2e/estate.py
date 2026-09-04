"""What the demo orgs look like, before and after - the harness's "left no trace" proof.

A pipeline run creates repos, flips topics and writes into `classroom-config`. Cleanup is
meant to undo all of it; the only way to know it did is to photograph the estate first and
compare afterwards. The fingerprint is deliberately cheap (one repo listing per org, one
recursive tree per config repo) so it can be taken twice in a 20-minute run.
"""

from __future__ import annotations

from dsl_course import course, discovery, gh_contents, repos

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
