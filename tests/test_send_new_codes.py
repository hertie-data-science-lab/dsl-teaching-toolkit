"""`enrol_codes --resend-unjoined`: the console's "send new codes". Every roster row
without a handle gets a NEW code (the old one stops working) and an email; an address that
cannot be one is skipped and counted, and no address ever reaches the log."""

from __future__ import annotations

import json
import re

import pytest

from dsl_course import enrol_codes, mailer, roster

HEADER = "hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\n"
ROSTER = (
    HEADER
    + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,2026-08-31T09:00:00+00:00\n"
    + "bob@uni.edu,Bob,enrolled,bobgh,7,dsl-bbb222,2026-08-31T09:00:00+00:00\n"
    + "not-an-address,Cy,enrolled,,,dsl-ccc333,\n"
    + "dee@uni.edu,Dee,auditor,,,,\n"
)
ADDRESSES = ("ada@uni.edu", "bob@uni.edu", "not-an-address", "dee@uni.edu")


def _drive(monkeypatch, *, dry_run: bool, transport: bool = True, sends=None):
    for name in mailer.GRAPH_ENV:
        if transport:
            monkeypatch.setenv(name, "set")
        else:
            monkeypatch.delenv(name, raising=False)
    written: list[str] = []
    sent: list = []
    monkeypatch.setattr(
        enrol_codes,
        "get_file_with_sha",
        lambda org, repo, path: (written[-1] if written else ROSTER, "sha"),
    )

    def put_file(org, repo, path, content, message, expected_sha=None):
        written.append(content.decode())
        return True

    monkeypatch.setattr(enrol_codes, "put_file", put_file)
    monkeypatch.setattr(enrol_codes, "course_name_for_semester", lambda org: "ML")
    monkeypatch.setattr(enrol_codes, "join_issue_url", lambda org: "https://w")

    def send_bulk(messages, dry_run=False, sample=None):
        sent.extend(messages)
        out = [m[0] for m in messages]
        return out if sends is None else out[:sends]

    monkeypatch.setattr(enrol_codes.mailer, "send_bulk", send_bulk)
    outcome, _counts = enrol_codes.resend_unjoined("SEMESTER", dry_run=dry_run)
    return outcome, sent, written


def _done(out: str) -> dict:
    (line,) = [ln for ln in out.splitlines() if "Done - " in ln]
    return json.loads(re.search(r"Done - (\{.*?\})", line).group(1))


def _no_address_in(out: str) -> None:
    for address in ADDRESSES:
        assert address not in out, address


def test_the_preview_counts_and_changes_nothing(monkeypatch, capsys):
    outcome, sent, written = _drive(monkeypatch, dry_run=True)
    out = capsys.readouterr()
    assert outcome is enrol_codes.Outcome.NOTHING_TO_SEND
    assert sent == [] and written == []
    assert _done(out.out) == {"students": 2, "skipped": 1, "sent": 0}
    _no_address_in(out.out + out.err)


def test_every_unjoined_row_gets_a_new_code_and_an_email(monkeypatch, capsys):
    outcome, sent, written = _drive(monkeypatch, dry_run=False)
    out = capsys.readouterr()
    assert outcome is enrol_codes.Outcome.SENT
    final = {s.hertie_email: s for s in roster.parse(written[-1])}
    # The old code is gone for Ada, Dee has her first; Bob joined and keeps his; the row
    # with no usable address is left exactly as it was.
    assert final["ada@uni.edu"].enrol_code not in ("", "dsl-aaa111")
    assert final["dee@uni.edu"].enrol_code
    assert final["bob@uni.edu"].enrol_code == "dsl-bbb222"
    assert final["not-an-address"].enrol_code == "dsl-ccc333"
    # Mailed what the roster now holds, with the warning that the old code is dead.
    assert sorted(to for to, _s, _b in sent) == ["ada@uni.edu", "dee@uni.edu"]
    for to, _subject, body in sent:
        assert final[to].enrol_code in body and "no longer works" in body
        assert (
            final[to].code_sent_at
            and final[to].code_sent_at != "2026-08-31T09:00:00+00:00"
        )
    assert _done(out.out) == {"students": 2, "skipped": 1, "sent": 2}
    _no_address_in(out.out + out.err)


def test_a_mail_that_did_not_go_releases_its_claim(monkeypatch, capsys):
    outcome, _sent, written = _drive(monkeypatch, dry_run=False, sends=1)
    out = capsys.readouterr()
    assert outcome is enrol_codes.Outcome.FAILED
    stamps = [s.code_sent_at for s in roster.parse(written[-1]) if not s.onboarded]
    # One row carries the new stamp; the unsent one is blank, so the next roster push
    # mails its new code.
    assert "" in stamps
    _no_address_in(out.out + out.err)


def test_without_a_transport_no_code_changes(monkeypatch):
    outcome, sent, written = _drive(monkeypatch, dry_run=False, transport=False)
    assert outcome is enrol_codes.Outcome.NO_TRANSPORT
    assert sent == [] and written == []


def test_a_preview_of_the_ordinary_send_sends_nothing(monkeypatch, capsys):
    # The roster send has no preview of its own, so previewing it (the default) sends
    # nothing at all: only --no-preview acts.
    monkeypatch.setattr(
        enrol_codes, "run", lambda org: pytest.fail("a preview must not send")
    )
    monkeypatch.setattr("sys.argv", ["enrol_codes", "--semester-org", "C", "--preview"])
    assert enrol_codes.main() == 0
    assert "nothing sent" in capsys.readouterr().out


def test_everyone_joining_mid_run_is_nothing_to_send(monkeypatch, capsys):
    """The code write loses a race to the Join issues of every target, and its retry lands
    on a roster where all of them have joined: nobody needs mail, and that is not a
    failure to stamp."""
    for name in mailer.GRAPH_ENV:
        monkeypatch.setenv(name, "set")
    joined = ROSTER.replace(
        "ada@uni.edu,Ada,enrolled,,", "ada@uni.edu,Ada,enrolled,adagh,"
    ).replace("dee@uni.edu,Dee,auditor,,", "dee@uni.edu,Dee,auditor,deegh,")
    reads = iter([(ROSTER, "sha1"), (joined, "sha2"), (joined, "sha2")])
    monkeypatch.setattr(enrol_codes, "get_file_with_sha", lambda *a: next(reads))
    puts: list[str | None] = []

    def put_file(org, repo, path, content, message, expected_sha=None):
        puts.append(expected_sha)
        return expected_sha == "sha2"

    monkeypatch.setattr(enrol_codes, "put_file", put_file)
    monkeypatch.setattr(enrol_codes, "course_name_for_semester", lambda org: "ML")
    monkeypatch.setattr(enrol_codes, "join_issue_url", lambda org: "https://w")
    monkeypatch.setattr(
        enrol_codes.mailer, "send_bulk", lambda *a, **k: pytest.fail("mailed")
    )
    outcome, counts = enrol_codes.resend_unjoined("SEMESTER", dry_run=False)
    assert outcome is enrol_codes.Outcome.NOTHING_TO_SEND
    assert not enrol_codes.reds_the_run(outcome)
    assert counts["students"] == 0
