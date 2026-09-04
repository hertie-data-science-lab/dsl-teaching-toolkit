"""Hand this directory the real transport back.

The rest of the suite is hermetic: `tests/conftest.py` refuses any live `gh` and answers
the central-ref probe with a stub, because a test that reaches GitHub passes on an
authenticated laptop and fails in CI. This directory is the deliberate exception - it
exists to run against real orgs. That is the escape hatch the parent conftest's own
docstrings describe: a fixture defined lower down runs after the autouse one above it and
wins.

The GATE itself - skip unless `DSL_E2E=1` - sits at the top of each test module here and
NOT in this file. pytest imports the conftest of an explicitly-named path while it is
still parsing arguments, where a `Skipped` is not caught, so a `pytest tests/e2e` with the
flag unset would end in a traceback rather than a skip. Raised from the module, both a
bare `pytest -q` and a targeted run report an ordinary skip.

Switching it on needs more than the flag - see `test_assignment_pipeline.py` for the full
environment (allowlist, tokens, test student).
"""

from __future__ import annotations

import subprocess

import pytest

from dsl_course import central, ghcli

# Captured at import time, which is before any fixture has replaced it.
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _live_gh(monkeypatch):
    """Both halves matter: `_no_live_gh` guards the `gh` BINARY, so restoring
    `subprocess.run` is what lets a call leave the process at all, and `central.gh` is
    stubbed to answer "the ref is there" - which is precisely the preflight fact this
    harness must check for real, since the whole run is about whether the demo tier is on
    `staging`."""
    monkeypatch.setattr(subprocess, "run", _REAL_RUN)
    monkeypatch.setattr(central, "gh", ghcli.gh)
