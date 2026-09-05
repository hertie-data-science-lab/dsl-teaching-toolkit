"""The test student's own hands: a real clone, commit and push with the student's token.

The submission has to arrive the way a student's does - a push from a clone, authored by
the student - because that is what the snapshot pins and what `CONTRIBUTIONS.md` and the
late arithmetic later read. Faking it through the Contents API with the bot token would
test the harness rather than the pipeline.

The token is a fine-grained PAT (Contents R/W on the demo cohort org) and it appears in
the remote URL, so every failure message here goes through `_redact` first.
"""

from __future__ import annotations

import os
from pathlib import Path

from dsl_course import ghcli

HANDLE_ENV = "DSL_E2E_STUDENT"
TOKEN_ENV = "DSL_E2E_STUDENT_TOKEN"


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
        "-c",
        "core.hooksPath=/dev/null",
    ]
    _git("clone", "--depth", "1", url, str(dest), token=token)
    target = dest / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _git("-C", str(dest), "add", path, token=token)
    _git("-C", str(dest), *identity, "commit", "-m", message, token=token)
    _git("-C", str(dest), "push", "-q", "origin", "HEAD", token=token)
    return _git("-C", str(dest), "rev-parse", "HEAD", token=token).strip()
