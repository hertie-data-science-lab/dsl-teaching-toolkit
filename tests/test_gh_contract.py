"""The gh CLI is an external contract, and a monkeypatched `gh` in a unit test will
happily accept a flag that does not exist - which is how `--body-file` shipped green and
then failed in production with "unknown flag" on every secret write.

These tests drive the real call sites with a recording `gh`, then check each flag they
pass against the flag list `gh --help` actually publishes. They skip where gh is absent
(CI images without the CLI) rather than fail.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

from dsl_course import bootstrap_course as bc
from dsl_course import seed

needs_gh = pytest.mark.skipif(shutil.which("gh") is None, reason="gh CLI not installed")


def test_ci_actually_has_the_gh_cli_to_check_against():
    # Every test below skips when `gh` is absent, so an image without the CLI turns this
    # whole file into a green no-op - which is precisely the state in which an invented
    # flag ships. Locally that is a reasonable convenience; in CI it is the failure this
    # file exists to prevent, so there the CLI has to be there.
    if not os.environ.get("CI"):
        pytest.skip("local run - the gh CLI is optional here")
    assert shutil.which("gh") is not None, (
        "CI has no `gh` on PATH, so every flag-contract test below silently skipped. "
        "Install the CLI in the workflow image (it is preinstalled on ubuntu-latest)."
    )


# Flag definition lines only: "  -b, --body string   The value..." / "      --no-store".
# Anchored to the definition column so flags mentioned in the EXAMPLES prose are ignored.
_FLAG_DEF = re.compile(r"^\s+(?:-\w, )?(--[a-z][a-z0-9-]*)", re.MULTILINE)


def _published_flags(*command: str) -> set[str]:
    """The long flags `gh <command> --help` documents, including inherited ones."""
    help_text = subprocess.run(
        ["gh", *command, "--help"], capture_output=True, text=True, check=True
    ).stdout
    return set(_FLAG_DEF.findall(help_text))


def _record_secret_set_calls(monkeypatch) -> list[tuple[str, ...]]:
    """Every `gh secret set ...` argv the code builds, captured from the real call sites
    rather than restated here - a new call site is covered the moment it is added."""
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str, **kwargs) -> tuple[int, str]:
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(seed, "gh", fake_gh)
    monkeypatch.setattr(bc, "gh", fake_gh)
    monkeypatch.setattr(bc, "repo_exists", lambda org, r: True)
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: True)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")

    seed._propagate_repo_secret("Course-Org", ["cm-f2026"])
    bc.set_org_secret("Course-Org", "DSL_BOT_TOKEN", "s3cret")

    secret_sets = [a for a in calls if a[:2] == ("secret", "set")]
    assert secret_sets, "no `gh secret set` calls captured - the harness has drifted"
    return secret_sets


@needs_gh
def test_every_secret_set_flag_the_code_passes_really_exists(monkeypatch):
    published = _published_flags("secret", "set")
    for args in _record_secret_set_calls(monkeypatch):
        for token in args:
            if token.startswith("--"):
                flag = token.split("=", 1)[0]
                assert flag in published, (
                    f"`gh secret set {flag}` is not a real flag - gh publishes "
                    f"{sorted(published)}"
                )


@needs_gh
def test_the_help_probe_would_catch_an_invented_flag():
    # Guards the guard: if _FLAG_DEF ever over-matched (e.g. picked up the EXAMPLES
    # prose), the test above would pass for any flag at all and catch nothing.
    published = _published_flags("secret", "set")
    assert "--body" in published  # the real flag we deliberately omit, to use stdin
    assert "--body-file" not in published  # the invented one that shipped green
