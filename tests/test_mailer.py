"""mailer -- the transport's failure handling, which is where a whole cohort's mail is
lost quietly. The HTTP POST itself is stubbed (`_post`), so nothing here reaches Graph or
an SMTP server; everything asserted is what the module does with the answer it gets.
"""

from __future__ import annotations

import pytest

from dsl_course import mailer

CFG = mailer.GraphConfig("tenant", "client", "secret", "bot@x.edu")
ONE = ("ada@x.edu", "Subj", "Body")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Retries must be exercised, not waited out."""
    slept: list[float] = []
    monkeypatch.setattr(mailer.time, "sleep", lambda s: slept.append(s))
    return slept


def _replies(monkeypatch, answers):
    """Stub `_post` with a queue of (status, headers) answers; returns the calls made."""
    queue = list(answers)
    calls: list[str] = []

    def fake_post(url, data, headers):
        calls.append(url)
        status, response_headers = queue.pop(0) if queue else (500, {})
        # `_post` lower-cases what the server sent; the stub must too, or the header
        # lookup passes here for a reason the real transport does not share.
        return status, b"", {k.lower(): v for k, v in response_headers.items()}

    monkeypatch.setattr(mailer, "_post", fake_post)
    return calls


# ------------------------------------------------------- throttling and transient 5xx


def test_a_throttled_send_is_retried_and_succeeds(monkeypatch, _no_sleeping):
    calls = _replies(monkeypatch, [(429, {"Retry-After": "2"}), (202, {})])
    assert mailer._graph_send_one(CFG, "tok", *ONE) is True
    assert len(calls) == 2
    assert _no_sleeping == [2.0]  # the server's own wait, honoured


def test_a_transient_5xx_is_retried(monkeypatch, _no_sleeping):
    calls = _replies(monkeypatch, [(503, {}), (200, {})])
    assert mailer._graph_send_one(CFG, "tok", *ONE) is True
    assert len(calls) == 2
    assert _no_sleeping == [mailer._RETRY_AFTER_DEFAULT]  # no header -> the default


def test_retries_are_capped_and_the_failure_is_reported(monkeypatch, capsys):
    calls = _replies(monkeypatch, [(429, {}), (429, {}), (429, {})])
    assert mailer._graph_send_one(CFG, "tok", *ONE) is False
    assert len(calls) == mailer._MAX_SEND_ATTEMPTS
    err = capsys.readouterr().err
    assert "failed (429)" in err
    assert "ada@x.edu" not in err  # the public log only ever sees the mask
    assert "a***@x.edu" in err


def test_a_permanent_failure_is_not_retried(monkeypatch):
    calls = _replies(monkeypatch, [(400, {})])
    assert mailer._graph_send_one(CFG, "tok", *ONE) is False
    assert len(calls) == 1  # a malformed recipient will not fix itself


def test_one_throttled_recipient_does_not_stop_the_batch(monkeypatch, capsys):
    # Three attempts for ada, then bo goes through: the batch carries on, and the count
    # says how many actually landed.
    _replies(monkeypatch, [(429, {})] * 3 + [(202, {})])
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    sent = mailer._send_via_graph(CFG, [ONE, ("bo@x.edu", "Subj", "Body")])
    assert sent == 1  # ada gave up after three attempts; bo still went out
    captured = capsys.readouterr()
    assert "sent -> b***@x.edu" in captured.out
    assert "send to a***@x.edu failed (429)" in captured.err


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ({}, mailer._RETRY_AFTER_DEFAULT),
        ({"retry-after": "12"}, 12.0),
        ({"retry-after": "  7 "}, 7.0),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, mailer._RETRY_AFTER_DEFAULT),
        ({"retry-after": "-5"}, 0.0),
        ({"retry-after": "99999"}, mailer._RETRY_AFTER_CAP),
    ],
)
def test_retry_after_is_read_defaulted_and_capped(header, expected):
    assert mailer.retry_after_seconds(header) == expected


# ------------------------------------------------------------------------ SMTP config


def _smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.x")
    monkeypatch.setenv("SMTP_USER", "u@x.edu")
    monkeypatch.setenv("SMTP_PASSWORD", "p")


def test_a_blank_smtp_port_falls_back_to_the_default(monkeypatch):
    # An Actions `env:` block always DEFINES the variable, so an unconfigured SMTP_PORT
    # arrives as "" - and `int("")` was a traceback out of the mail step before a single
    # message was built.
    _smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "")
    cfg = mailer.smtp_config_from_env()
    assert cfg and cfg.port == mailer.DEFAULT_SMTP_PORT


def test_a_garbage_smtp_port_defaults_and_says_so(monkeypatch, capsys):
    _smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "five-eight-seven")
    cfg = mailer.smtp_config_from_env()
    assert cfg and cfg.port == mailer.DEFAULT_SMTP_PORT
    assert "SMTP_PORT is not a number" in capsys.readouterr().err


def test_a_real_smtp_port_is_honoured(monkeypatch):
    _smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_PORT", "2525")
    cfg = mailer.smtp_config_from_env()
    assert cfg and cfg.port == 2525


def test_a_blank_smtp_from_falls_back_to_the_user(monkeypatch):
    _smtp_env(monkeypatch)
    monkeypatch.setenv("SMTP_FROM", "")
    cfg = mailer.smtp_config_from_env()
    assert cfg and cfg.from_addr == "u@x.edu"
