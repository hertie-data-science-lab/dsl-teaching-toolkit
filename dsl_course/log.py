"""Console output for every CLI in the package: the five prefixes the Actions logs are
read through, plus the channel that keeps per-person lines out of a public log.
"""

from __future__ import annotations

import argparse
import os
import sys

from .faults import not_migrated_text


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


def log_err_person(public: str, detail: str) -> None:
    """A failure whose only identifying detail is somebody's repo, split in two.

    The public half says WHAT failed and what to do about it; the half that names the repo,
    the path or the handle goes through `log_person`. Every faculty workflow runs in the
    course org's PUBLIC `.github`, and the failure branches are exactly where that was
    forgotten - a green run says nothing, while `could not commit to <org>/grades-<handle>`
    on a bad day publishes the roster one student at a time.

    A faculty member who needs the name re-runs the CLI locally with `DSL_VERBOSE=1`, or
    reads the private classroom-config; a count of what failed is in the run's summary."""
    log_err(public)
    log_person(f"  ! {detail}")


def log_person(msg: str) -> None:
    """A line that NAMES SOMEBODY - printed only when `DSL_VERBOSE` is set.

    Named for the rule rather than for the mechanism, so a reviewer can see at the call
    site that the line is a per-person one. Every faculty workflow runs in the course org's
    PUBLIC `.github`, so its Actions log is world-readable, and a line naming one student's
    handle, their `<slug>-<handle>` repo, or a team's roster publishes who is in the semester
    and who is grouped with whom. Those lines are INFORMATIONAL; what a faculty member
    actually reads is the aggregate `Done - {...}` summary, which stays. So they go here:
    printed when someone runs the CLI locally with `DSL_VERBOSE=1`, absent from every
    workflow, because no rendered workflow sets the variable (a test enforces that).

    An ERROR a faculty member must act on keeps its handle and stays on `log_err` - those
    are rare, and unactionable without saying who."""
    if os.environ.get("DSL_VERBOSE"):
        print(msg, flush=True)


class Summary(int):
    """A CLI's exit code that also says, in one sentence, what the run did.

    An `int`, so it travels every path an exit code already travels unchanged:
    `sys.exit(main())` exits with its value, and a caller that sums or compares codes
    reads a number. The Console run (`python -m dsl_course.console`) runs a CLI's `main`
    in-process and, when what comes back is one of these, turns it into the operation's
    outcome - `text` is its `summary`, shown verbatim, so it is written in the words of
    the design vocabulary and names nobody: it lands in a public annotation.

    `counts` are integers only, `reasons` are `{"code", "text"}` pairs (a code in
    UPPER_SNAKE, always with its sentence), `details` are one line per thing the run
    touched (a file derived), for the console to list, `block` is generated text the
    console shows verbatim (the syllabus session list), and `conclusion` is set only to say
    a run that exited 0 did nothing (`nothing_to_do`) or was passed over (`skipped`)."""

    text: str
    counts: dict[str, int]
    reasons: list[dict]
    details: list[str]
    block: str
    conclusion: str | None

    def __new__(
        cls,
        text: str,
        counts: dict[str, int] | None = None,
        reasons: list[dict] | None = None,
        *,
        code: int = 0,
        conclusion: str | None = None,
        details: list[str] | None = None,
        block: str = "",
    ):
        obj = super().__new__(cls, int(code))
        obj.text = text
        obj.counts = dict(counts or {})
        obj.reasons = list(reasons or [])
        obj.details = list(details or [])
        obj.block = block
        obj.conclusion = conclusion
        return obj

    def __repr__(self) -> str:
        return f"Summary({self.text!r}, code={int(self)})"


def plural(n: int, one: str, many: str | None = None) -> str:
    """`1 file`, `3 files`: a count with its noun, for a Summary's sentence."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


def add_preview_flag(parser: argparse.ArgumentParser, help: str) -> None:
    """`--preview/--no-preview` on a CLI, ON by default (decision 0012): trying an
    operation without changing anything is what a bare invocation does, and acting takes
    the explicit `--no-preview`. Every rendered workflow and every console op spells one of
    the two, so no caller depends on the default (`tests/test_renderers.py`)."""
    parser.add_argument(
        "--preview", action=argparse.BooleanOptionalAction, default=True, help=help
    )


# Old CLI spellings (decision 0012), old -> new. None is ever parsed as its new flag - and,
# with `allow_abbrev=False`, none is prefix-matched onto one either (`--format` would
# otherwise resolve to `--formats`, `--solution` to `--solution-datetime`). A command line
# that spells one is refused as NOT_MIGRATED, naming the new flag - unless the CLI still
# defines that spelling for a meaning of its own (`status --format`, `status --write`).
OLD_FLAGS = {
    "--cohort-org": "--semester-org",
    "--all-cohorts": "--all-semesters",
    "--list-cohorts": "--list-semesters",
    "--cohort-dest-repo": "--semester-dest-repo",
    "--cohort-dest-path": "--semester-dest-path",
    "--cohort": "--semester",
    "--tag": "--semester",
    "--format": "--formats",
    "--solution": "--solution-datetime now",
    "--dry-run": "--preview",
    "--no-dry-run": "--no-preview",
    "--write": "--no-preview",
    "--master-org": "--course-org",
    "--source-org": "--course-org",
}


class CLIParser(argparse.ArgumentParser):
    """Every CLI's parser: no abbreviations, and old flags refused as NOT_MIGRATED."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def _known_flags(self) -> set[str]:
        flags = set(self._option_string_actions)
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    flags |= (
                        sub._known_flags()
                        if isinstance(sub, CLIParser)
                        else set(sub._option_string_actions)
                    )
        return flags

    def parse_known_args(self, args=None, namespace=None):
        argv = sys.argv[1:] if args is None else list(args)
        known = self._known_flags()
        for token in argv:
            flag = str(token).split("=", 1)[0]
            if flag in OLD_FLAGS and flag not in known:
                self.error(not_migrated_text(flag, OLD_FLAGS[flag]))
        return super().parse_known_args(args, namespace)
