"""Shared test helpers.

The package imports cleanly without network (the gh/git calls only fire when a function
runs), so tests import dsl_course modules directly and exercise the PURE logic: the
workflow renderers (their output must be GitHub-parseable YAML) and the content
transforms in site/release. The thin gh/git orchestration is deliberately NOT mocked -
that only asserts we wrote the call we wrote; its real failure modes need a live org.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml

from dsl_course import site


@pytest.fixture(autouse=True)
def _no_live_gh(monkeypatch):
    """Refuse any live `gh` call from a test.

    Nothing here is meant to reach GitHub (see the module docstring), but a tokenless CI
    box and an authenticated dev box disagree about what happens when something does: CI
    errors and the developer's machine quietly succeeds against real orgs. That is how a
    test that stubbed `site._session_files` but not `site._repo_tree` passed locally for a
    whole branch and failed only on the PR.

    Guards the `gh` BINARY rather than `utils.gh`, so the retry ladder and return-pair
    contract of `utils.gh` itself stay testable, and `git` against a tmp repo still runs. A
    test that legitimately fakes `gh` or `git` sets its own after this fixture and wins."""
    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        # `gh <cmd> --help` reads gh's own built-in flag list: no network, no auth, and it
        # is how test_gh_contract.py proves a flag the code passes really exists.
        if cmd and cmd[0] == "gh" and "--help" not in cmd:
            raise AssertionError(
                f"live `{' '.join(map(str, cmd[:3]))}` from a test - stub what the code "
                "under test reads (site._repo_tree, utils.get_file_content, ...) instead."
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture(autouse=True)
def _clear_repo_tree_memo():
    """`site._repo_tree` memoises a repo's tree for the whole process (one CLI run); tests
    reuse the same org/repo names with different fakes, so clear it between them."""
    site._repo_tree.cache_clear()


def workflow_inputs(rendered: str) -> dict:
    """Parse a rendered workflow and return its `workflow_dispatch.inputs`.

    PyYAML follows YAML 1.1, where the bare key `on:` parses to boolean True, so the
    top-level trigger key is `True`, not the string "on" (GitHub's own parser is fine
    with `on:`). Accept either so the test asserts real structure, not the quirk."""
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    return trigger["workflow_dispatch"].get("inputs") or {}


def workflow_jobs(rendered: str) -> dict:
    return yaml.safe_load(rendered)["jobs"]
