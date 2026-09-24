"""The test student's own hands: a real clone, commit and push with the student's token.

The submission has to arrive the way a student's does - a push from a clone, authored by
the student - because that is what the snapshot pins and what `CONTRIBUTIONS.md` and the
late arithmetic later read. Faking it through the Contents API with the bot token would
test the harness rather than the pipeline.

The token is a fine-grained PAT on the demo semester org and it appears in the remote URL,
so every failure message here goes through `_redact` first. Contents R/W is what the push
needs; `set_visibility` also needs Administration: write, because one of the five shapes
this harness drives is the one whose repo the STUDENT is admin of and may publish.

Every git call here runs with hooks off. A student's laptop has no repo hooks; the
MAINTAINER'S has whatever their dotfiles install, and a `pre-push` that lints the working
tree fires inside this throwaway clone and fails the run on the maintainer's own house
style rather than on anything the pipeline did.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from dsl_course import ghcli, repos

HANDLE_ENV = "DSL_E2E_STUDENT"
TOKEN_ENV = "DSL_E2E_STUDENT_TOKEN"

# Every variable `gh` will take a token out of. Both are set, and `gh` prefers the first,
# so nothing downstream can fall back to the maintainer's own token on a machine that
# happens to export the other one.
_TOKEN_VARS = ("GH_TOKEN", "GITHUB_TOKEN")


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set - see this suite's module docstring")
    return value


def handle() -> str:
    """The GitHub handle of the account standing in for a student."""
    return _env(HANDLE_ENV)


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***")


# Hooks off, in git's two spellings: `--config` for the repo `clone` is about to create
# (which also covers a hooks template copied into it), and the pre-command `-c` for every
# call afterwards (which also covers a global `core.hooksPath`).
HOOKS_SETTING = "core.hooksPath=/dev/null"
HOOKS_OFF = ("-c", HOOKS_SETTING)


def _git(*args: str, token: str) -> str:
    code, out = ghcli.git(*args)
    if code != 0:
        raise RuntimeError(f"`git {args[0]}` failed: {_redact(out, token)[:300]}")
    return out


def push_file(repo: str, dest: Path, path: str, content: str, message: str) -> str:
    """Clone `<owner>/<repo>` into `dest`, write `path`, commit and push. Returns the sha.

    The commit carries the STUDENT's identity, not `ghcli.GIT_ENV`'s bot one: a submission
    authored by dsl-bot would be indistinguishable from the handout commit."""
    token = _env(TOKEN_ENV)
    who = handle()
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    identity = [
        "-c",
        f"user.name={who}",
        "-c",
        f"user.email={who}@users.noreply.github.com",
        *HOOKS_OFF,
    ]
    _git(
        "clone", "--config", HOOKS_SETTING, "--depth", "1", url, str(dest), token=token
    )
    target = dest / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git("-C", str(dest), *HOOKS_OFF, "add", path, token=token)
    _git(
        "-C", str(dest), *identity, "commit", "--no-verify", "-m", message, token=token
    )
    _git(
        "-C",
        str(dest),
        *HOOKS_OFF,
        "push",
        "--no-verify",
        "-q",
        "origin",
        "HEAD",
        token=token,
    )
    return _git("-C", str(dest), *HOOKS_OFF, "rev-parse", "HEAD", token=token).strip()


@contextmanager
def acting() -> Iterator[None]:
    """Run the `gh` calls inside this block as the STUDENT rather than as the bot.

    `ghcli` shells out to `gh`, which inherits this process's environment, so swapping the
    token variables for the length of a call is all it takes - and it keeps the call
    itself on `ghcli`, which is where the org fence and the write pacer live.

    Restored in a `finally`, to the exact previous state including "was not set at all":
    everything after this block is the maintainer's run again, and a leaked student token
    would silently downgrade every write that follows."""
    token = _env(TOKEN_ENV)
    before = {name: os.environ.get(name) for name in _TOKEN_VARS}
    os.environ.update(dict.fromkeys(_TOKEN_VARS, token))
    try:
        yield
    finally:
        for name, was in before.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was


def set_visibility(org: str, repo: str, visibility: str) -> bool:
    """Flip `repo` public or private AS THE STUDENT - the `student_choice` promise, made
    and broken from the student's own side.

    The point of driving it with their token and not the maintainer's: what the assignment
    page tells them is that the repo is THEIRS to publish, and what the scheduler promises
    the rest of the semester is that publishing it before the grading cutoff does not last.
    A flip made with an org-owner token would prove the second and none of the first.

    Needs Administration: write on the semester org in the student's fine-grained PAT, on
    top of the Contents R/W the push needs - see this suite's module docstring."""
    with acting():
        return repos.set_visibility(org, repo, visibility, person=True)
