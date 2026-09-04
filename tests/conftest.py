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

from dsl_course import central, collect, ghcli, repos, roster, schedule, site, teams

# students.csv's header row, DERIVED from the columns the engine declares rather than
# re-typed. `roster.FIELDS` is a frozen public contract (the shipped JavaScript spells the
# same columns out by hand), and five test files each carried their own copy of it - so a
# column added to FIELDS left five fixtures describing a roster that no longer exists.
ROSTER_HEADER = ",".join(roster.FIELDS)


def repo_row(name: str, **extra) -> dict:
    """One row of a `discovery.list_org_repos` listing, carrying every field it really has.

    Three test files kept their own partial builder, each missing a different key, so code
    that reads `archived` or `topics` off a listing was tested against rows that have
    neither. Defaults are the uninteresting answer; `extra` overrides what a test is about.
    """
    return {
        "name": name,
        "description": "",
        "visibility": "private",
        "url": f"https://github.com/org/{name}",
        "isTemplate": False,
        "archived": False,
        "topics": [],
        **extra,
    }


@pytest.fixture(autouse=True)
def _empty_write_governor():
    """`ghcli`'s write pacer keeps its timestamps at module level, so they would otherwise
    accumulate across the session until an unrelated test slept for a real minute."""
    ghcli._write_times.clear()


@pytest.fixture(autouse=True)
def _no_live_gh(monkeypatch):
    """Refuse any live `gh` call from a test.

    Nothing here is meant to reach GitHub (see the module docstring), but a tokenless CI
    box and an authenticated dev box disagree about what happens when something does: CI
    errors and the developer's machine quietly succeeds against real orgs. That is how a
    test that stubbed `site._session_files` but not `site._repo_tree` passed locally for a
    whole branch and failed only on the PR.

    Guards the `gh` BINARY rather than `ghcli.gh`, so the retry ladder and return-pair
    contract of `ghcli.gh` itself stay testable, and `git` against a tmp repo still runs. A
    test that legitimately fakes `gh` or `git` sets its own after this fixture and wins."""
    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        # `gh <cmd> --help` reads gh's own built-in flag list: no network, no auth, and it
        # is how test_gh_contract.py proves a flag the code passes really exists.
        if cmd and cmd[0] == "gh" and "--help" not in cmd:
            raise AssertionError(
                f"live `{' '.join(map(str, cmd[:3]))}` from a test - stub what the code "
                "under test reads (site._repo_tree, gh_contents.get_file_content, ...) instead."
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture(autouse=True)
def _the_central_ref_is_present(monkeypatch):
    """Answer `central.central_ref_exists`'s probe with "yes" by default.

    Every workflow write goes through `central.pin_central_ref`, which asks GitHub whether
    the org's ref is on the central repo before pinning a workflow to it. Nothing here is
    meant to reach GitHub (see the module docstring), and "it is there" is the
    uninteresting answer for every test but the ones about the check itself - which set
    their own `central.gh` after this fixture and win.

    `identical` is what the SHA path reads off `compare/main...{sha}`; the branch path
    only looks at the exit code."""
    monkeypatch.setattr(central, "gh", lambda *a, **k: (0, "identical"))


@pytest.fixture(autouse=True)
def _clear_process_memos():
    """The per-process memos a single CLI run is entitled to keep: a repo's tree, a repo's
    metadata, whether a central ref exists, and the three classroom-config files a run
    re-reads (students.csv, teams.csv, schedule.yml). Tests reuse the same org/repo names
    with different fakes, so clear them between tests."""
    site._repo_tree.cache_clear()
    central.central_ref_exists.cache_clear()
    repos._repo.cache_clear()
    roster._roster_text.cache_clear()
    teams._teams_text.cache_clear()
    schedule._schedule_text.cache_clear()
    collect._grading_text.cache_clear()


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


def entry_links(rendered: str) -> list[tuple[str, str]]:
    """The (section, name) pairs of a rendered session entry's `links:` block.

    Parsed rather than substring-matched: `name` and `section` are two fields now, so an
    assertion written against the rendered bytes would be asserting the emitter's line
    order as much as its content."""
    front = rendered.split("---\n")[1]
    return [(l["section"], l["name"]) for l in yaml.safe_load(front).get("links") or []]
