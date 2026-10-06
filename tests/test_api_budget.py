"""The `[budget]` lines every engine CLI run prints at its start and end (`ghcli`), read
off the `X-RateLimit-*` headers of one `GET /user` through a stubbed `ghcli.gh`."""

from __future__ import annotations

import io
import json
import sys

import pytest

from dsl_course import console, ghcli, log, notify

# 2026-09-26 11:50:32Z
RESET = 1790423432


def _headers(remaining: int, used: int, *, reset: int = RESET, eol: str = "\n") -> str:
    lines = [
        "HTTP/2.0 200 OK",
        "X-Ratelimit-Limit: 5000",
        f"X-Ratelimit-Remaining: {remaining}",
        f"X-Ratelimit-Reset: {reset}",
        "X-Ratelimit-Resource: core",
        f"X-Ratelimit-Used: {used}",
        "",
        '{"login":"dsl-bot-account","id":1}',
    ]
    return eol.join(lines)


FORBIDDEN = (
    "HTTP/2.0 403 Forbidden\r\n"
    "X-Ratelimit-Limit: 5000\r\n"
    "X-Ratelimit-Remaining: 0\r\n"
    f"X-Ratelimit-Reset: {RESET}\r\n"
    "X-Ratelimit-Used: 5000\r\n"
    "\r\n"
    '{"message":"API rate limit exceeded for user ID 1.","status":"403"}\n'
    "gh: API rate limit exceeded for user ID 1. (HTTP 403)"
)


@pytest.fixture
def answers(monkeypatch):
    """Queue what `gh api --include user` answers, and record every call made."""
    queue: list[tuple[int, str]] = []
    calls: list[tuple] = []

    def fake(*args, **kwargs):
        calls.append(args)
        return queue.pop(0)

    monkeypatch.setattr(ghcli, "gh", fake)
    return queue, calls


@pytest.fixture
def at_exit(monkeypatch):
    held: list[tuple] = []
    monkeypatch.setattr(ghcli, "_at_exit", lambda fn, *a: held.append((fn, a)))
    return held


def test_headers_parse_with_lf_and_crlf():
    for eol in ("\n", "\r\n"):
        budget = ghcli.parse_budget(_headers(4996, 4, eol=eol))
        assert budget == ghcli.Budget("dsl-bot-account", 5000, 4996, 4, RESET)
        assert budget.resets == "11:50"


def test_a_missing_header_is_no_budget():
    text = _headers(4996, 4).replace("X-Ratelimit-Used: 4\n", "")
    assert ghcli.parse_budget(text) is None
    assert (
        ghcli.parse_budget("gh: To get started with GitHub CLI, run gh auth login")
        is None
    )


def test_a_403_body_still_carries_the_budget():
    budget = ghcli.parse_budget(FORBIDDEN)
    assert (budget.remaining, budget.used, budget.login) == (0, 5000, "this token")


def test_start_line_and_end_line(answers, at_exit, capsys):
    queue, calls = answers
    queue.append((0, _headers(4900, 100)))
    ghcli.budget_at_start()
    assert capsys.readouterr().err == (
        "  [budget] GitHub API as dsl-bot-account: 4900 of 5000 left this hour, "
        "resets 11:50Z\n"
    )
    assert calls == [("api", "--include", "--method", "GET", "user")]
    [(end, args)] = at_exit
    # The run made 42 calls; the end read is the 43rd `used` counts.
    queue.append((0, _headers(4857, 143)))
    end(*args)
    assert capsys.readouterr().err == (
        "  [budget] this run used 42 calls; 4857 left, resets 11:50Z\n"
    )


def test_end_line_across_a_reset_is_a_floor():
    start = ghcli.Budget("bot", 5000, 4900, 100, RESET)
    end = ghcli.Budget("bot", 5000, 4990, 10, RESET + 3600)
    assert ghcli.budget_end_line(start, end) == (
        "  [budget] this run used at least 9 calls (the hour reset during the run); "
        "4990 left, resets 12:50Z"
    )


def test_a_run_that_made_no_calls_used_none():
    start = ghcli.Budget("bot", 5000, 4900, 100, RESET)
    end = ghcli.Budget("bot", 5000, 4899, 101, RESET)
    assert "used 0 calls;" in ghcli.budget_end_line(start, end)


@pytest.mark.parametrize(
    ("remaining", "tag"), [(1000, "  [budget]"), (999, "  [warn]"), (100, "  [warn]")]
)
def test_below_1000_the_start_line_warns(answers, at_exit, capsys, remaining, tag):
    answers[0].append((0, _headers(remaining, 5000 - remaining)))
    ghcli.budget_at_start()
    assert capsys.readouterr().err.startswith(f"{tag} GitHub API as ")
    assert len(at_exit) == 1


@pytest.mark.parametrize("answer", [(0, _headers(99, 4901)), (1, FORBIDDEN)])
def test_below_100_the_run_stops_before_it_starts(answers, at_exit, capsys, answer):
    queue, calls = answers
    queue.append(answer)
    with pytest.raises(SystemExit) as stopped:
        ghcli.budget_at_start()
    assert stopped.value.code != 0
    remaining = ghcli.parse_budget(answer[1]).remaining
    assert capsys.readouterr().err == (
        f"  [err] GitHub API budget exhausted: {remaining} of 5000 left, "
        "resets 11:50Z - this run stops before it starts\n"
    )
    assert len(calls) == 1 and at_exit == []


def test_no_token_prints_nothing(answers, at_exit, capsys):
    answers[0].append((4, "To get started with GitHub CLI, please run:  gh auth login"))
    ghcli.budget_at_start()
    assert capsys.readouterr() == ("", "")
    assert at_exit == []


def test_no_gh_binary_prints_nothing(monkeypatch, at_exit, capsys):
    def missing(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(ghcli, "gh", missing)
    ghcli.budget_at_start()
    assert capsys.readouterr() == ("", "")


def test_the_hook_runs_once_after_a_parse_and_never_on_help(monkeypatch):
    ran = []
    monkeypatch.setattr(log, "_cli_started", False)
    monkeypatch.setattr(log, "_start_hooks", [ran.append])
    parser = log.CLIParser(prog="x")
    parser.add_argument("--n")
    with pytest.raises(SystemExit):
        parser.parse_args(["--help"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--bogus"])
    assert ran == []
    parser.parse_args(["--n", "1"])
    parser.parse_args(["--n", "2"])
    assert ran == [parser]


# ------------------------------------------------ the real chain, parse to `gh`


@pytest.fixture
def first_parse(monkeypatch, at_exit):
    """This process has not parsed a command line yet, so the real hooks run."""
    monkeypatch.setattr(log, "_cli_started", False)
    return at_exit


def test_a_parse_reads_the_budget_with_one_get_user(answers, first_parse, capsys):
    queue, calls = answers
    queue.append((0, _headers(4900, 100)))
    parser = log.CLIParser(prog="x")
    parser.add_argument("--n")
    parser.parse_args(["--n", "1"])
    parser.parse_args(["--n", "2"])
    assert calls == [("api", "--include", "--method", "GET", "user")]
    assert "  [budget] GitHub API as " in capsys.readouterr().err


def test_a_low_budget_stops_a_cli_at_its_parse(answers, first_parse):
    answers[0].append((1, FORBIDDEN))
    parser = log.CLIParser(prog="x")
    with pytest.raises(SystemExit):
        parser.parse_args([])


@pytest.fixture
def mail(monkeypatch):
    sent = []
    monkeypatch.setattr(
        notify.mailer, "send_bulk", lambda msgs: sent.extend(msgs) or msgs
    )
    monkeypatch.setattr(notify.mailer, "maintainer_address", lambda: "maint@x.edu")
    monkeypatch.setattr(sys, "stdin", io.StringIO("the failed step's log"))
    return sent


RUN_FAILED = [
    "run-failed",
    "--course-org",
    "course",
    "--workflow",
    "Autograde",
    "--run-url",
    "https://run/1",
]


def test_notify_run_failed_mails_with_no_budget_left(
    answers, first_parse, mail, monkeypatch, capsys
):
    # The mail goes through Graph, and a failed run is when the budget may be gone.
    answers[0].append((1, FORBIDDEN))
    monkeypatch.setattr(sys, "argv", ["dsl_course.notify", *RUN_FAILED])
    assert notify.main() == 0
    assert len(mail) == 1
    assert (
        "  [warn] GitHub API as this token: 0 of 5000 left" in capsys.readouterr().err
    )


def test_a_cli_the_console_runs_in_process_does_not_read_it_again(
    answers, first_parse, mail, monkeypatch
):
    queue, calls = answers
    queue.append((0, _headers(4900, 100)))
    ran = []

    def run(raw):
        # What `console.run` does with a dispatch op, minus the request handling.
        ran.append(console.run_cli("notify", RUN_FAILED))
        return 0

    monkeypatch.setattr(console, "run", run)
    monkeypatch.setattr(
        sys, "argv", ["dsl_course.console", "--request", json.dumps({"op": "x"})]
    )
    assert console.main() == 0
    assert ran == [(0, None, False)] and len(mail) == 1
    assert calls == [("api", "--include", "--method", "GET", "user")]


def test_ghcli_registers_the_budget_line():
    assert ghcli._budget_hook in log._start_hooks
