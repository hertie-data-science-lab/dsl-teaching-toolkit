"""Console output for every CLI in the package: the five prefixes the Actions logs are
read through, plus the channel that keeps per-person lines out of a public log.
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


def log_withheld(msg: str) -> None:
    """A path this toolkit deliberately did NOT copy.

    The `  (withheld) ` line faculty read, plus the `::warning::` annotation that carries it
    onto the run summary WITHOUT touching the exit code. Withholding is this working, not a
    fault, and the hourly scheduler shares the code that does it - so an error here would
    redden a cron every hour for the rest of the term, which is how real failures stop being
    noticed. Both halves live here because the prefix is what faculty grep for and the
    annotation is what keeps the run green; spelling either one twice loses that."""
    log(f"  (withheld) {msg}")
    print(f"::warning::{msg}", file=sys.stderr, flush=True)


def log_person(msg: str) -> None:
    """A line that NAMES SOMEBODY - printed only when `DSL_VERBOSE` is set.

    Named for the rule rather than for the mechanism, so a reviewer can see at the call
    site that the line is a per-person one. Every faculty workflow runs in the course org's
    PUBLIC `.github`, so its Actions log is world-readable, and a line naming one student's
    handle, their `<slug>-<handle>` repo, or a team's roster publishes who is in the cohort
    and who is grouped with whom. Those lines are INFORMATIONAL; what a faculty member
    actually reads is the aggregate `Done - {...}` summary, which stays. So they go here:
    printed when someone runs the CLI locally with `DSL_VERBOSE=1`, absent from every
    workflow, because no rendered workflow sets the variable (a test enforces that).

    An ERROR a faculty member must act on keeps its handle and stays on `log_err` - those
    are rare, and unactionable without saying who."""
    if os.environ.get("DSL_VERBOSE"):
        print(msg, flush=True)
