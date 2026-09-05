"""Undo one end-to-end run - as a fixture teardown, and as a command when that never ran.

    python -m tests.e2e.cleanup --run-id e2eab12cd [--dry-run]

Re-runnable and narrow by construction. It deletes ONLY repos whose names match this run's
own namespace (`assignment-90-<run>`, plus the `-<handle>` and `-template` repos GitHub
Classroom-style provisioning hangs off it), removes only the fenced schedule block this run
inserted, and only the snapshot / autograde / grading-sheet artefacts named after this
run's slug. Anything else that looks like e2e leavings is REPORTED and left alone: a
cleanup that guesses is how a demo org loses a real repo.

It refuses to start unless `DSL_ORG_ALLOWLIST` is set, because deletion runs with a
maintainer token that carries `delete_repo` - the bot never holds one, which is also why
cleanup is a command here and never a seeded workflow.
"""

from __future__ import annotations

import argparse
import re
import secrets

from dsl_course import (
    collect,
    course,
    discovery,
    gh_contents,
    ghcli,
    grades,
    repos,
    schedule,
)
from dsl_course.log import log, log_err, log_ok, log_person, log_step

from . import allowlist, schedule_edit

# The number the harness hands out under - far past any real assignment, so a real
# `assignment-1` can never fall inside this run's namespace.
ASSIGNMENT_NUMBER = 90

# `e2e` + six characters, which is the shape the deletion pattern is written around.
_RUN_ID = re.compile(r"^e2e[0-9a-z]{6}$")

# What gets REPORTED rather than deleted: leavings of some OTHER run (or of a run whose id
# nobody wrote down), which a human has to look at before anything removes them.
_DRIFT = re.compile(rf"^assignment-{ASSIGNMENT_NUMBER}-|e2e[0-9a-z]{{6}}")

# The engine's own names, every one of them, so a rename there cannot leave artefacts
# uncollected here.
ARTEFACT_DIRS = (collect.SNAPSHOT_DIR, collect.AUTOGRADE_DIR, grades.SHEETS_DIR)


def new_run_id() -> str:
    """A fresh namespace for one run."""
    return f"e2e{secrets.token_hex(3)}"


def check_run_id(run_id: str) -> str:
    """The run id, or a refusal. Never interpolate an unchecked one into a delete."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(
            f"'{run_id}' is not a run id - expected `e2e` followed by six characters, "
            "as `new_run_id` writes it"
        )
    return run_id


def slug(run_id: str) -> str:
    """The assignment slug this run hands out under."""
    return f"assignment-{ASSIGNMENT_NUMBER}-{check_run_id(run_id)}"


def is_run_repo(name: str, run_id: str) -> bool:
    """Whether `name` is a repo THIS run created: the template, the assignment itself, or
    one of its `-<handle>` submission repos - and nothing else."""
    return re.fullmatch(rf"{re.escape(slug(run_id))}(-.+)?", name) is not None


def is_drift(name: str, run_id: str) -> bool:
    """Whether `name` looks like e2e leavings that are NOT this run's."""
    return bool(_DRIFT.search(name)) and not is_run_repo(name, run_id)


def _delete_repo(org: str, name: str, dry_run: bool) -> bool:
    if dry_run:
        log_person(f"  would delete {org}/{name}")
        return True
    code, out = ghcli.gh("api", "--method", "DELETE", f"repos/{org}/{name}")
    if code != 0:
        log_err(f"could not delete {org}/{name}: {out[:200]}")
        return False
    log_person(f"  deleted {org}/{name}")
    return True


def _clean_repos(org: str, run_id: str, dry_run: bool) -> int:
    """Delete this run's repos in one org. Returns the number of failures."""
    names = sorted(row["name"] for row in discovery.list_org_repos(org))
    mine = [n for n in names if is_run_repo(n, run_id)]
    failures = sum(not _delete_repo(org, n, dry_run) for n in mine)
    log_ok(f"{org}: {len(mine)} repo(s) {'to delete' if dry_run else 'deleted'}")
    for name in names:
        if is_drift(name, run_id):
            log_err(
                f"{org}/{name} looks like e2e leavings from another run - LEFT ALONE"
            )
    return failures


def _clean_config(org: str, run_id: str, dry_run: bool) -> int:
    """Take the fenced block out of schedule.yml and drop this run's artefacts.

    Skipped for an org with no classroom-config, which is every course org."""
    config = course.CONFIG_REPO
    if config not in {row["name"] for row in discovery.list_org_repos(org)}:
        return 0
    failures = 0
    read = gh_contents.get_file_with_sha(org, config, schedule.SCHEDULE_PATH)
    if read is not None:
        text, sha = read
        without = schedule_edit.remove_block(text, run_id)
        if without != text:
            log(f"  {org}: removing the fenced schedule block")
            if not dry_run and not schedule_edit.put_schedule(org, without, sha):
                log_err(f"could not rewrite {org}/{config}/{schedule.SCHEDULE_PATH}")
                failures += 1
    branch = repos.default_branch(org, config, fallback="main")
    live = gh_contents.repo_blob_shas(org, config, branch)
    mine = sorted(p for p in live if _is_artefact(p, run_id))
    if mine:
        log(f"  {org}: {len(mine)} artefact(s) {'to drop' if dry_run else 'dropped'}")
        for path in mine:
            log_person(f"    {path}")
        if not dry_run and not gh_contents.put_files(
            org, config, {}, f"e2e: drop {slug(run_id)} artefacts", delete=mine
        ):
            log_err(f"could not drop the artefacts in {org}/{config}")
            failures += 1
    return failures


def _is_artefact(path: str, run_id: str) -> bool:
    """A classroom-config path this run's slug owns - `snapshots/<slug>.csv`,
    `autograde/<slug>/...`, `grading_sheets/<slug>.yml`."""
    head, _, rest = path.partition("/")
    if head not in ARTEFACT_DIRS:
        return False
    mine = slug(run_id)
    return rest == mine or rest.startswith((f"{mine}/", f"{mine}."))


def restore_files(org: str, repo: str, before: dict[str, str | None]) -> int:
    """Put shared files back exactly as they were found, in ONE commit.

    Distribute writes two files no run id owns - `cohort-gradebook.csv` and
    `gradebook/distributed.csv` - plus the test student's own gradebook. They cannot be
    swept by namespace, so the harness records them before the run and hands them back
    here. A path recorded as None was absent and is deleted."""
    writes = {path: text.encode() for path, text in before.items() if text is not None}
    delete = [path for path, text in before.items() if text is None]
    if not gh_contents.put_files(
        org, repo, writes, "e2e: restore shared grading files", delete=delete
    ):
        log_err(f"could not restore the shared files in {org}/{repo}")
        return 1
    return 0


def cleanup(run_id: str, *, dry_run: bool = False) -> int:
    """Undo run `run_id` across every org in scope. Returns a process exit code."""
    check_run_id(run_id)
    allowlist.assert_fence()
    failures = 0
    log(
        f"cleanup {slug(run_id)}"
        f"{' (dry run)' if dry_run else ''} - set DSL_VERBOSE=1 to list repos by name"
    )
    for org in sorted(allowlist.orgs()):
        log_step(f"cleanup {slug(run_id)} in {org}")
        failures += _clean_repos(org, run_id, dry_run)
        failures += _clean_config(org, run_id, dry_run)
    if failures:
        log_err(f"cleanup left {failures} thing(s) undone - re-run it")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.e2e.cleanup", description=__doc__
    )
    parser.add_argument(
        "--run-id", required=True, help="the run to undo, e.g. e2eab12cd"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would go; delete nothing"
    )
    args = parser.parse_args(argv)
    try:
        return cleanup(args.run_id, dry_run=args.dry_run)
    except (RuntimeError, ValueError) as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
