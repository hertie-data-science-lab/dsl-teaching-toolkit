"""Console output for every CLI in the package: the five prefixes the Actions logs are
read through, plus the verbose channel that keeps per-person lines out of a public log.
"""

from __future__ import annotations

import os
import sys


def log(msg: str) -> None:
    print(msg, flush=True)


def log_step(msg: str) -> None:
    print(f"\n-> {msg}", flush=True)


def log_ok(msg: str) -> None:
    print(f"  [ok] {msg}", flush=True)


def log_skip(msg: str) -> None:
    print(f"  [skip] {msg} (already exists)", flush=True)


def log_err(msg: str) -> None:
    print(f"  [err] {msg}", file=sys.stderr, flush=True)


def log_verbose(msg: str) -> None:
    """Print `msg` only when `DSL_VERBOSE` is set in the environment.

    Every faculty workflow runs in the course org's PUBLIC `.github`, so its Actions log is
    world-readable - and a line naming one student's handle, their `<slug>-<handle>` repo,
    or a team's roster publishes who is in the cohort and who is grouped with whom. Those
    lines are INFORMATIONAL; what a faculty member actually reads is the aggregate
    `Done - {...}` summary, which stays. So they are routed through here: printed when
    someone runs the CLI locally with `DSL_VERBOSE=1`, absent from every workflow, because
    no rendered workflow sets the variable (a test enforces that).

    An ERROR a faculty member must act on keeps its handle and stays on `log_err` - those
    are rare, and unactionable without saying who."""
    if os.environ.get("DSL_VERBOSE"):
        print(msg, flush=True)
