"""The `gh` and `git` subprocess wrappers: their return-pair contract, the messages a
failure leaves in an Actions log, and the timeouts that stop a hung call running for
six hours."""

from __future__ import annotations

import time

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


# --------------------------------------- writes are paced under GitHub's secondary limit


def _fake_clock(monkeypatch):
    """Drive the governor with a clock a test controls: sleeping ADVANCES it, so a real
    minute is never spent. Returns the list of slept-for durations."""
    at = [0.0]
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        at[0] += seconds

    monkeypatch.setattr(ghcli, "_now", lambda: at[0])
    monkeypatch.setattr(ghcli, "_sleep", sleep)
    return slept


def test_a_burst_of_writes_waits_rather_than_tripping_the_secondary_limit(monkeypatch):
    # The first handout tick issues several writes per student in one process. GitHub caps
    # content-creating requests at ~80/min, and the retry ladder (30+60+120s) is spent
    # before that clears - so past ~60 students the rest of the cohort simply failed.
    slept = _fake_clock(monkeypatch)
    write = ("api", "--method", "PUT", "repos/O/R/contents/f")
    for _ in range(ghcli.WRITES_PER_MINUTE):
        ghcli._pace_writes(write)
    assert slept == [], "the first minute's budget must not be delayed"
    ghcli._pace_writes(write)
    assert slept == [60.0], "the write over the cap did not wait for the window to roll"
    # the burst has aged out of the 60-second window; only the paced write is still in it
    assert list(ghcli._write_times) == [60.0]


def test_reads_are_never_paced(monkeypatch):
    # Reads draw on the 5,000/hour budget, not the content-creation cap, and a listing
    # slowed to 70/minute would make every discovery pass crawl.
    slept = _fake_clock(monkeypatch)
    for _ in range(ghcli.WRITES_PER_MINUTE * 2):
        ghcli._pace_writes(("api", "repos/O/R"))
    assert slept == [] and not ghcli._write_times


@pytest.mark.parametrize(
    "args,mutating",
    [
        (("api", "--method", "POST", "orgs/O/repos"), True),
        (("api", "-X", "delete", "repos/O/R/collaborators/x"), True),
        (("api", "--method", "PATCH", "repos/O/R"), True),
        (("api", "--paginate", "repos/O/R/collaborators"), False),
        (("repo", "clone", "O/R"), False),
    ],
)
def test_which_argv_shapes_count_as_a_write(args, mutating):
    assert ghcli._is_mutating(args) is mutating


# ------------------------------------ a transient GitHub fault on a READ rides the ladder


class _Canned:
    """One canned `subprocess.run` result."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _canned_gh(monkeypatch, *results: _Canned) -> list[list[str]]:
    """Serve `results` to successive `gh` calls (the last one repeats), with the ladder's
    backoff spent instantly. Returns the argv of every call made."""
    import subprocess

    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return calls


def test_a_read_that_hits_a_500_is_retried(monkeypatch, capsys):
    # Three real `Scheduled release` runs died on one contents-API 500. The tick fires 96
    # times a day, and a red one files a public "is failing" issue and mails course-admin -
    # so a fault GitHub fixes by itself a second later must not end the run.
    calls = _canned_gh(
        monkeypatch,
        _Canned(1, "", "gh: Internal Server Error (HTTP 500)"),
        _Canned(0, "{}", ""),
    )
    assert ghcli.gh("api", "repos/O/R/contents/f") == (0, "{}")
    assert len(calls) == 2
    assert "[wait] transient http 500, retry 1/3" in capsys.readouterr().out


def test_a_read_truncated_mid_json_is_retried(monkeypatch):
    # The other shape the failed runs took: the response body simply ended early.
    calls = _canned_gh(
        monkeypatch,
        _Canned(1, "", "unexpected end of JSON input"),
        _Canned(0, "[]", ""),
    )
    assert ghcli.gh_json("api", "repos/O/R/contents/f") == []
    assert len(calls) == 2


def test_a_write_that_hits_a_500_is_never_retried(monkeypatch):
    # A POST that came back 5xx may still have APPLIED; repeating it would apply it twice.
    # Straight to the ladder, so the write governor's shared window stays untouched.
    calls = _canned_gh(
        monkeypatch, _Canned(1, "", "gh: Internal Server Error (HTTP 500)")
    )
    code, _, err = ghcli._run_gh(("api", "--method", "POST", "orgs/O/repos"), None, 3)
    assert code == 1 and "HTTP 500" in err
    assert len(calls) == 1


def test_a_rate_limit_is_still_retried(monkeypatch, capsys):
    # The ladder's original reason, and its wording, unchanged by the transient markers.
    calls = _canned_gh(
        monkeypatch,
        _Canned(1, "", "You have exceeded a secondary rate limit"),
        _Canned(0, "ok", ""),
    )
    assert ghcli.gh("api", "repos/O/R") == (0, "ok")
    assert len(calls) == 2
    assert "[wait] rate-limited, retry 1/3" in capsys.readouterr().out


def test_an_ordinary_failure_is_not_retried(monkeypatch):
    # The markers must stay narrow: a 404 or a 403 is the answer, not a fault to sit out.
    calls = _canned_gh(monkeypatch, _Canned(1, "", "gh: Not Found (HTTP 404)"))
    assert ghcli.gh("api", "repos/O/R")[0] == 1
    assert len(calls) == 1


def test_clone_carries_a_branch_when_one_is_asked_for(monkeypatch):
    # The solution branch is cloned in two places, which each spelled the argv out by hand;
    # a clone that quietly lands on the default branch reads as "no solution here".
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(ghcli, "gh", lambda *a, **k: seen.append(a) or (0, ""))
    assert ghcli.clone("O", "R", "/tmp/x", branch="solution")
    assert seen[-1] == ("repo", "clone", "O/R", "/tmp/x", "--", "-q", "-b", "solution")
    ghcli.clone("O", "R", "/tmp/x")
    assert seen[-1] == ("repo", "clone", "O/R", "/tmp/x", "--", "-q")


# ------------------------------------------------- the opt-in org allowlist (tests/e2e)


class _Ran(list):
    """The argvs that reached the process boundary, plus canned stdout keyed by a
    substring of the argv (a list cannot carry the replies dict on its own)."""

    def __init__(self):
        super().__init__()
        self.replies: dict[str, str] = {}


@pytest.fixture
def ran(monkeypatch):
    """Every subprocess argv the wrappers actually reached, with a canned reply.

    Stubbing `subprocess.run` is what the repo's no-live-gh rule asks for everywhere else;
    here it is also the only honest fake, because ghcli IS the transport - the thing under
    test is which argvs get as far as the process boundary."""
    import subprocess

    seen = _Ran()

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))

        class R:
            returncode = 0
            stdout = next(
                (v for k, v in seen.replies.items() if k in " ".join(cmd)), ""
            )
            stderr = ""

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_an_unset_allowlist_changes_nothing(monkeypatch, ran):
    monkeypatch.delenv("DSL_ORG_ALLOWLIST", raising=False)
    assert ghcli.gh("api", "--method", "DELETE", "repos/anyone/anything")[0] == 0
    assert ran[-1][:2] == ["gh", "api"]


def test_a_write_to_an_allowed_org_goes_through(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-course , demo-f2026")
    assert (
        ghcli.gh("api", "--method", "PUT", "repos/demo-f2026/x/contents/a.md")[0] == 0
    )
    assert ran, "the call never reached the process boundary"


def test_a_write_outside_the_allowlist_raises(monkeypatch, ran):
    # NOT a failure pair: `utils.repo_exists` reads any failure as absence, so a refusal
    # returned that way would be heard as "not there yet" and the caller would create it.
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    with pytest.raises(RuntimeError, match="demo-f2026"):
        ghcli.gh("api", "--method", "DELETE", "repos/hertie-ml-26/live-repo")
    assert ran == [], "the refused call must not reach the process boundary"


def test_a_write_that_names_no_org_is_refused_too(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    with pytest.raises(RuntimeError, match="no org at all"):
        ghcli.gh("api", "--method", "POST", "graphql")
    assert ran == []


def test_the_destination_of_a_template_generate_is_checked(monkeypatch, ran):
    # The PATH names the template's org; the org the new repo lands in is a `--field`.
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-course")
    with pytest.raises(RuntimeError, match="hertie-ml-26"):
        ghcli.gh(
            "api",
            "--method",
            "POST",
            "repos/demo-course/a-template/generate",
            "--field",
            "owner=hertie-ml-26",
            "--field",
            "name=a-1",
        )


def test_reads_are_never_fenced(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    assert ghcli.gh("api", "repos/hertie-ml-26/live-repo")[0] == 0
    assert ran, "a read outside the allowlist must still run"


def test_a_push_to_an_allowed_remote_goes_through(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    ran.replies["remote get-url"] = "https://github.com/demo-f2026/a-1-jane\n"
    assert ghcli.git("-C", "/tmp/wd", "push", "-q", "origin", "HEAD")[0] == 0
    assert ran[-1][-2:] == ["origin", "HEAD"]


def test_a_push_to_a_remote_outside_the_allowlist_is_refused(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    ran.replies["remote get-url"] = "git@github.com:hertie-ml-26/materials.git\n"
    with pytest.raises(RuntimeError, match="hertie-ml-26"):
        ghcli.git("-C", "/tmp/wd", "push", "-q", "origin", "HEAD")
    assert [c for c in ran if "push" in c] == []


def test_a_push_whose_remote_cannot_be_read_is_refused(monkeypatch):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    monkeypatch.setattr(
        ghcli, "git", ghcli.git
    )  # the real one; only the URL read fails
    import subprocess

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = "fatal: no such remote"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="unreadable remote"):
        ghcli.git("-C", "/tmp/wd", "push", "-q", "origin", "HEAD")


def test_other_git_commands_are_untouched(monkeypatch, ran):
    monkeypatch.setenv("DSL_ORG_ALLOWLIST", "demo-f2026")
    assert (
        ghcli.git("-C", "/tmp/wd", "clone", "https://github.com/hertie-ml-26/m")[0] == 0
    )
    assert ran, "the fence is about writes, not about reading somebody else's repo"
