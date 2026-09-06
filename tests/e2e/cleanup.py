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
import base64
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


def _clean_repos(org: str, run_id: str, dry_run: bool, left: list[str]) -> int:
    """Delete this run's repos in one org. Returns the number of failures, and appends
    `<org>/<name>` for each repo still standing to `left`.

    The number REPORTED is of deletes that happened, not of deletes attempted. On a token
    without `delete_repo` the two differ completely: the first live run printed `[ok]
    hertie-dsl-demo-f2026: 3 repo(s) deleted` above three 403s, and the reassuring line is
    the one an operator reads."""
    names = sorted(row["name"] for row in discovery.list_org_repos(org))
    mine = [n for n in names if is_run_repo(n, run_id)]
    gone = [n for n in mine if _delete_repo(org, n, dry_run)]
    log_ok(f"{org}: {len(gone)} repo(s) {'to delete' if dry_run else 'deleted'}")
    left.extend(f"{org}/{n}" for n in mine if n not in gone)
    for name in names:
        if is_drift(name, run_id):
            log_err(
                f"{org}/{name} looks like e2e leavings from another run - LEFT ALONE"
            )
    return len(mine) - len(gone)


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


def file_bytes(org: str, repo: str, path: str) -> bytes | None:
    """A file's content BYTE FOR BYTE, or None if it is absent.

    `gh_contents.get_file_content` is the wrong instrument for a restore: it comes back
    through `ghcli.gh`, which reads the subprocess in text mode and strips the result - so
    a CRLF file arrives with every `\r` gone and every file arrives without its trailing
    newline. Written back, that is a different blob, and the estate check at teardown saw
    exactly that: `cohort-gradebook.csv` is `csv.writer` output, so it is CRLF, and every
    run "restored" it eight bytes shorter than it found it.

    So the base64 is decoded HERE, in Python, and only the base64 alphabet passes through
    the pipe. Same 404-is-None, anything-else-raises rule as `get_file_content`."""
    code, out = ghcli.gh(
        "api", f"repos/{org}/{repo}/contents/{path}", "--jq", ".content"
    )
    if code != 0:
        if ghcli.is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{repo}/{path}: {out[:200]}")
    return base64.b64decode(out)


def restore_files(org: str, repo: str, before: dict[str, bytes | None]) -> int:
    """Put shared files back exactly as they were found, in ONE commit.

    Distribute writes two files no run id owns - `cohort-gradebook.csv` and
    `gradebook/distributed.csv` - plus the test student's own gradebook. They cannot be
    swept by namespace, so the harness records them before the run and hands them back
    here. A path recorded as None was absent and is deleted.

    BYTES, from `file_bytes`: "exactly as they were found" is measured by the estate check
    as a blob sha, so a round trip through text is a restore that does not restore."""
    writes = {path: data for path, data in before.items() if data is not None}
    delete = [path for path, data in before.items() if data is None]
    if not gh_contents.put_files(
        org, repo, writes, "e2e: restore shared grading files", delete=delete
    ):
        log_err(f"could not restore the shared files in {org}/{repo}")
        return 1
    return 0


def cleanup(
    run_id: str, *, dry_run: bool = False, left: list[str] | None = None
) -> int:
    """Undo run `run_id` across every org in scope. Returns a process exit code.

    `left` collects `<org>/<repo>` for every repo that could not be deleted, so a caller
    can say what to do about them; the count in the summary line and the length of this
    list describe the same repos."""
    check_run_id(run_id)
    allowlist.assert_fence()
    left = [] if left is None else left
    failures = 0
    log(
        f"cleanup {slug(run_id)}"
        f"{' (dry run)' if dry_run else ''} - set DSL_VERBOSE=1 to list repos by name"
    )
    for org in sorted(allowlist.orgs()):
        log_step(f"cleanup {slug(run_id)} in {org}")
        failures += _clean_repos(org, run_id, dry_run, left)
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
    left: list[str] = []
    try:
        code = cleanup(args.run_id, dry_run=args.dry_run, left=left)
    except (RuntimeError, ValueError) as exc:
        log_err(str(exc))
        return 1
    if left:
        # A re-run only helps if the token was the problem and has since been swapped;
        # the way out of a run that already died is the delete, by hand, spelt out. The
        # template goes to everyone because a public log may not name a repo - a submission
        # repo is `<slug>-<handle>` - and the filled-in commands follow the same rule as
        # every other per-person line.
        log(f"  or delete the {len(left)} repo(s) by hand:")
        log("    gh api --method DELETE repos/<org>/<repo>")
        for repo in left:
            log_person(f"    gh api --method DELETE repos/{repo}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
