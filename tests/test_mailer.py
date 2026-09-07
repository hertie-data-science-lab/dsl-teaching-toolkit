"""mailer -- the transport's failure handling, which is where a whole cohort's mail is
lost quietly. The HTTP POST itself is stubbed (`_post`), so nothing here reaches Graph or
Graph; everything asserted is what the module does with the answer it gets.
"""

from __future__ import annotations

import json

import pytest

from dsl_course import mailer

CFG = mailer.GraphConfig("tenant", "client", "secret", "bot@x.edu")
ONE = mailer.Message("ada@x.edu", "Subj", "Body")


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Retries must be exercised, not waited out.

    The clock advances by whatever was slept, so pacing-to-a-deadline and the batch
    budget are measured against the time the module thinks has passed."""
    slept: list[float] = []
    clock = [0.0]

    def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(mailer.time, "sleep", fake_sleep)
    monkeypatch.setattr(mailer.time, "monotonic", lambda: clock[0])
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


def _payloads(monkeypatch) -> list[dict]:
    """The `message` object of every sendMail this batch put on the wire.

    The other stub keeps only the URL, and what these tests are about is the BODY: which
    content type it declared and who was on the Cc line."""
    seen: list[dict] = []

    def fake_post(url, data, headers):
        seen.append(json.loads(data)["message"])
        return 202, b"", {}

    monkeypatch.setattr(mailer, "_post", fake_post)
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    monkeypatch.setattr(mailer, "graph_config_from_env", lambda: CFG)
    return seen


# ------------------------------------------------------- throttling and transient 5xx


def test_a_throttled_send_is_retried_and_succeeds(monkeypatch, _no_sleeping):
    calls = _replies(monkeypatch, [(429, {"Retry-After": "2"}), (202, {})])
    assert mailer._graph_send_one(CFG, "tok", ONE) is True
    assert len(calls) == 2
    assert _no_sleeping == [2.0]  # the server's own wait, honoured


def test_a_transient_5xx_is_retried(monkeypatch, _no_sleeping):
    calls = _replies(monkeypatch, [(503, {}), (200, {})])
    assert mailer._graph_send_one(CFG, "tok", ONE) is True
    assert len(calls) == 2
    assert _no_sleeping == [mailer._RETRY_AFTER_DEFAULT]  # no header -> the default


def test_retries_are_capped_and_the_failure_is_reported(monkeypatch, capsys):
    calls = _replies(monkeypatch, [(429, {}), (429, {}), (429, {})])
    assert mailer._graph_send_one(CFG, "tok", ONE) is False
    assert len(calls) == mailer._MAX_SEND_ATTEMPTS
    err = capsys.readouterr().err
    assert "failed (429)" in err
    assert "ada@x.edu" not in err  # the public log only ever sees the mask
    assert "a***@x.edu" in err


def test_a_permanent_failure_is_not_retried(monkeypatch):
    calls = _replies(monkeypatch, [(400, {})])
    assert mailer._graph_send_one(CFG, "tok", ONE) is False
    assert len(calls) == 1  # a malformed recipient will not fix itself


def test_one_throttled_recipient_does_not_stop_the_batch(monkeypatch, capsys):
    # Three attempts for ada, then bo goes through: the batch carries on, and the return
    # says WHO actually landed.
    _replies(monkeypatch, [(429, {})] * 3 + [(202, {})])
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    sent = mailer._send_via_graph(
        CFG, [ONE, mailer.Message("bo@x.edu", "Subj", "Body")]
    )
    assert sent == ["bo@x.edu"]  # ada gave up after three attempts; bo still went out
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


# ------------------------------------------------------------- config from the environment


def _graph_env(monkeypatch, **overrides):
    values = {
        "GRAPH_TENANT_ID": "tenant",
        "GRAPH_CLIENT_ID": "client",
        # Only its multi-line shape matters here; the real PEM parsing is tested where
        # a real self-signed pair is built, in test_enrol_codes.
        "GRAPH_CLIENT_CERT": "cert-line-one\ncert-line-two\n",
        "GRAPH_SENDER": "bot@x.edu",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_graph_secrets_are_stripped_before_they_reach_a_url(monkeypatch):
    # `gh secret set < file` leaves a trailing newline. tenant_id goes straight into the
    # token URL, where that newline raised http.client.InvalidURL - uncaught, into a public
    # log, AFTER the enrolment codes had already been committed.
    _graph_env(monkeypatch, GRAPH_TENANT_ID="tenant\n", GRAPH_SENDER=" bot@x.edu ")
    cfg = mailer.graph_config_from_env()
    assert cfg and cfg.tenant_id == "tenant" and cfg.sender == "bot@x.edu"


def test_a_multi_line_certificate_is_not_mistaken_for_a_mangled_secret(monkeypatch):
    # The PEM is multi-line by construction; only the single-line values are vetted.
    _graph_env(monkeypatch)
    assert mailer.graph_config_from_env() is not None


def test_nothing_configured_is_silent(monkeypatch, capsys):
    # An offline dry-run preview is a documented feature and must not shout.
    for key in mailer.GRAPH_ENV:
        monkeypatch.delenv(key, raising=False)
    assert mailer.graph_config_from_env() is None
    assert capsys.readouterr().err == ""


def test_a_half_configured_graph_names_the_missing_variables(monkeypatch, capsys):
    # Actions masks the values, so the old blanket "no transport configured" could not tell
    # "nothing is set" from "one name is misspelt".
    _graph_env(monkeypatch)
    monkeypatch.delenv("GRAPH_SENDER")
    assert mailer.graph_config_from_env() is None
    err = capsys.readouterr().err
    assert "GRAPH_SENDER" in err and "GRAPH_TENANT_ID" not in err


def test_a_secret_containing_whitespace_is_refused_by_name_not_by_value(
    monkeypatch, capsys
):
    _graph_env(monkeypatch, GRAPH_TENANT_ID="ten ant")
    assert mailer.graph_config_from_env() is None
    err = capsys.readouterr().err
    assert "GRAPH_TENANT_ID" in err and "ten ant" not in err


def test_a_mangled_request_is_a_transport_failure_not_a_traceback(monkeypatch):
    def boom(req):
        raise mailer.http.client.InvalidURL("URL can't contain control characters")

    monkeypatch.setattr(mailer.urllib.request, "urlopen", boom)
    status, raw, headers = mailer._post("https://x/y", b"", {})
    assert status == 0 and headers == {}
    assert b"control characters" in raw


# ------------------------------------------------------------ where fault mail goes


def test_the_maintainer_address_prefers_its_own_variable(monkeypatch):
    # DSL_MAINTAINER_EMAIL is the toolkit maintainer; GRAPH_SENDER is the shared mailbox
    # the toolkit sends AS. When both are set the maintainer wins - the fallback exists
    # only so that an org nobody propagated the address to still mails somebody.
    _graph_env(monkeypatch)
    monkeypatch.setenv(mailer.MAINTAINER_ENV, "maintainer@x.edu")
    assert mailer.maintainer_address() == "maintainer@x.edu"


@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "blank", "spaces"])
def test_without_it_fault_mail_falls_back_to_the_send_mailbox(monkeypatch, value):
    # An empty org secret reads as an empty env value, and mailing "" mails nobody.
    _graph_env(monkeypatch)
    if value is None:
        monkeypatch.delenv(mailer.MAINTAINER_ENV, raising=False)
    else:
        monkeypatch.setenv(mailer.MAINTAINER_ENV, value)
    assert mailer.maintainer_address() == "bot@x.edu"


def test_no_address_anywhere_is_none_rather_than_an_empty_string(monkeypatch):
    # The caller says "mail not configured" once and carries on; "" would be handed to
    # Graph as a recipient and fail per message instead.
    for key in (*mailer.GRAPH_ENV, mailer.MAINTAINER_ENV):
        monkeypatch.delenv(key, raising=False)
    assert mailer.maintainer_address() is None


# ------------------------------------------------------------------- pacing and the budget


def test_the_batch_is_paced_below_the_graph_rate_limit(monkeypatch, _no_sleeping):
    # ~30/min per mailbox. Sent back-to-back, a full cohort starts 429-ing around message
    # 30 and then pays the retry ladder per recipient, serially, against a 30-minute job.
    _replies(monkeypatch, [(202, {})] * 3)
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    messages = [mailer.Message(f"s{i}@x.edu", "Subj", "Body") for i in range(3)]
    assert mailer._send_via_graph(CFG, messages) == [m.to for m in messages]
    assert _no_sleeping == [mailer._SEND_INTERVAL] * 2  # n-1: the first is not delayed
    assert mailer.time.monotonic() == 2 * mailer._SEND_INTERVAL


def test_a_batch_that_runs_out_of_budget_stops_and_says_re_run(
    monkeypatch, _no_sleeping, capsys
):
    # A batch KILLED at the job timeout takes the caller's sent-marker write with it, and
    # the re-run then re-mails everyone who already got one. Stopping early is resumable.
    _replies(monkeypatch, [(202, {})] * 3)
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    # Budget shorter than one slot: message two still goes (nothing has elapsed when it
    # is checked), message three finds it spent.
    monkeypatch.setattr(mailer, "_BATCH_BUDGET", mailer._SEND_INTERVAL / 2)
    messages = [mailer.Message(f"s{i}@x.edu", "Subj", "Body") for i in range(3)]
    assert mailer._send_via_graph(CFG, messages) == ["s0@x.edu", "s1@x.edu"]
    assert (
        "stopped after 2 of 3 message(s) - re-run to continue"
        in capsys.readouterr().err
    )


# --------------------------------------------------------------------------- the preflight


def test_a_dry_run_proves_the_credential_after_it_prints_the_preview(
    monkeypatch, capsys
):
    # The preview is what faculty came for; a broken credential must not cost them it.
    _graph_env(monkeypatch)
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: None)
    with pytest.raises(RuntimeError):
        mailer.send_bulk([ONE], dry_run=True)
    assert "would send -> a***@x.edu" in capsys.readouterr().out


def test_a_good_credential_says_which_mailbox_it_would_send_as(monkeypatch, capsys):
    _graph_env(monkeypatch)
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: "tok")
    assert mailer.send_bulk([ONE], dry_run=True) == ["ada@x.edu"]
    assert "would send as bot@x.edu" in capsys.readouterr().out


def test_a_dry_run_with_no_transport_configured_still_previews_offline(
    monkeypatch, capsys
):
    for key in mailer.GRAPH_ENV:
        monkeypatch.delenv(key, raising=False)
    assert mailer.send_bulk([ONE], dry_run=True) == ["ada@x.edu"]
    captured = capsys.readouterr()
    assert "would send -> a***@x.edu" in captured.out
    assert "proves nothing about a send" in captured.err


# ------------------------------------------------------- HTML bodies and a real Cc


def test_a_body_is_plain_text_unless_the_caller_asks_for_html(monkeypatch):
    # The two roster senders are plain text by design: a grade notification marked up as
    # HTML would render a student's own `<` as markup.
    seen = _payloads(monkeypatch)
    mailer.send_bulk([ONE])
    assert seen[0]["body"]["contentType"] == "Text"


def test_the_html_flag_reaches_graph_as_the_content_type(monkeypatch):
    # The fault mails carry marked-up labels, and a body sent as Text renders them as
    # literal `<b>` - which is worse than not marking them up at all.
    seen = _payloads(monkeypatch)
    mailer.send_bulk([ONE], html=True)
    assert seen[0]["body"]["contentType"] == "HTML"


def test_a_cc_is_a_real_cc_line_not_another_recipient(monkeypatch):
    # Who ELSE was told is the whole point of copying them: a TA's fault mail copies the
    # instructors, and fanning that out as more To recipients hides it from everybody.
    seen = _payloads(monkeypatch)
    mailer.send_bulk([ONE._replace(cc=("boss@x.edu",))])
    assert seen[0]["ccRecipients"] == [{"emailAddress": {"address": "boss@x.edu"}}]


def test_no_cc_means_no_cc_field_at_all(monkeypatch):
    seen = _payloads(monkeypatch)
    mailer.send_bulk([ONE])
    assert "ccRecipients" not in seen[0]


def test_a_recipient_on_its_own_cc_line_is_not_sent_two_copies(monkeypatch):
    # The fallback To set is the whole teaching team, and the instructors are also the Cc.
    seen = _payloads(monkeypatch)
    mailer.send_bulk([ONE._replace(cc=("ADA@x.edu", "boss@x.edu"))])
    assert seen[0]["ccRecipients"] == [{"emailAddress": {"address": "boss@x.edu"}}]


# --------------------------------------------------- one message, a group of recipients


def test_a_group_of_recipients_is_one_send_not_one_each(monkeypatch):
    # A fault mail is the same text for everybody on the line, and a copy per recipient
    # pays the rate limiter's 2.5s slot for each - so the whole group is one POST, with
    # every address on `toRecipients`.
    seen = _payloads(monkeypatch)
    sent = mailer.send_bulk([mailer.Message(("ada@x.edu", "bo@x.edu"), "Subj", "Body")])
    assert len(seen) == 1
    assert seen[0]["toRecipients"] == [
        {"emailAddress": {"address": "ada@x.edu"}},
        {"emailAddress": {"address": "bo@x.edu"}},
    ]
    # Addresses, not a count: the caller records who was mailed so a re-run does not mail
    # them again.
    assert sent == ["ada@x.edu", "bo@x.edu"]


def test_a_plain_three_tuple_is_still_a_message(monkeypatch):
    # The two roster senders build one per student and copy nobody; asking them to name a
    # group of one would be ceremony for its own sake.
    seen = _payloads(monkeypatch)
    assert mailer.send_bulk([("ada@x.edu", "Subj", "Body")]) == ["ada@x.edu"]
    assert seen[0]["toRecipients"] == [{"emailAddress": {"address": "ada@x.edu"}}]


def test_a_group_is_masked_as_a_whole_in_the_run_log(monkeypatch, capsys):
    # Every workflow runs in a PUBLIC repo, so its log is world-readable.
    _payloads(monkeypatch)
    mailer.send_bulk([mailer.Message(("ada@x.edu", "bo@x.edu"), "Subj", "Body")])
    out = capsys.readouterr().out
    assert "sent -> a***@x.edu, b***@x.edu" in out
    assert "ada@" not in out


def test_a_dry_run_counts_the_copies_without_naming_them(monkeypatch, capsys):
    # A Cc list is other people's addresses, and this preview prints into a public log.
    mailer.send_bulk([ONE._replace(cc=("boss@x.edu",))], dry_run=True)
    out = capsys.readouterr().out
    assert "...copying 1 address(es)" in out
    assert "boss@x.edu" not in out
