"""The `gh` and `git` subprocess wrappers: their return-pair contract, the messages a
failure leaves in an Actions log, and the timeouts that stop a hung call running for
six hours."""

from __future__ import annotations

import pytest

from dsl_course import ghcli


def test_gh_always_returns_a_pair(monkeypatch):
    # The retry loop is gh's only return path, so a negative `retries` (no attempt at all)
    # used to fall off the end and hand back None - which every caller unpacks.
    code, out = ghcli.gh("api", "user", retries=-1)
    assert code != 0 and out


def test_gh_json_names_the_command_it_failed_to_run(monkeypatch):
    # This message is what a CLI prints in an Actions log instead of a traceback, so
    # "gh command failed" on its own leaves nothing to act on.
    import subprocess

    class Result:
        returncode = 1
        stdout = ""
        stderr = "HTTP 403: rate limited"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="`gh search repos topic:dsl-course-hub`"):
        ghcli.gh_json("search", "repos", "topic:dsl-course-hub")


# --------------------------------------------------- git must not hang for six hours


def test_a_hung_git_comes_back_as_an_ordinary_failure(monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs.get("timeout", 0))

    monkeypatch.setattr(subprocess, "run", timeout)
    code, out = ghcli.git("clone", "https://example.invalid/r")
    assert code == 1  # every caller's `!= 0` check reports it; no exception escapes
    assert "timed out" in out


def test_git_passes_a_timeout_to_the_subprocess(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)

        class R:
            returncode, stdout, stderr = 0, "", ""

        return R()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)
    ghcli.git("status")
    assert seen["timeout"] == ghcli.GIT_TIMEOUT_SECONDS


def test_gh_json_gets_the_same_timeout_and_retries_as_gh(monkeypatch):
    # It used to call subprocess directly, so the one call that reads across the whole
    # estate (list_orgs' topic search) was the only GitHub call here with no ceiling and
    # no backoff - free to hang a job for six hours, or to fail the weekly inventory on a
    # limit every other call rides out.
    import subprocess

    seen: dict = {}

    class Result:
        returncode = 0
        stdout = '[{"name": ".github"}]'
        stderr = ""

    def run(cmd, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(subprocess, "run", run)
    assert ghcli.gh_json("search", "repos", "topic:x") == [{"name": ".github"}]
    assert seen["timeout"] == ghcli.GH_TIMEOUT_SECONDS


def test_gh_json_parses_stdout_alone(monkeypatch):
    # gh writes advisories - a token nearing expiry, an update notice - to stderr, so the
    # joined pair `gh` hands back is not JSON and must never reach the parser.
    import subprocess

    class Result:
        returncode = 0
        stdout = "[]"
        stderr = "! gh version 2.0 is available\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Result())
    assert ghcli.gh_json("search", "repos", "topic:x") == []


# ------------------------------------------------- what "already there" looks like


@pytest.mark.parametrize(
    "out",
    [
        "HTTP 422: Validation Failed - name already exists on this account",
        '{"errors":[{"code":"already_exists"}]}',
        '{"errors":[{"message":"Name must be unique for this org"}]}',
        "gh: Conflict (HTTP 409)",
    ],
)
def test_every_spelling_of_already_there_reads_as_success(out):
    # One marker list for repo create, team create, PR create and Pages enable - each of
    # those endpoints words it differently, and each used to carry its own test.
    assert ghcli.is_already_exists(out)


@pytest.mark.parametrize(
    "out",
    [
        "HTTP 422: Validation Failed - name is invalid",
        "HTTP 403: organization plan does not allow this",
        "HTTP 404: Not Found",
    ],
)
def test_another_failure_is_not_read_as_already_there(out):
    # A bare `"422" in out` swallowed an invalid-name or policy 422 as success, and the
    # caller went on writing into a repo or team that was never created.
    assert not ghcli.is_already_exists(out)
