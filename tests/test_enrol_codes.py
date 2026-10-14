"""enrol-codes + mailer pure cores: code assignment must fill only blanks and stay unique
(a clash would let one student claim another's row), the message must carry the code, and
the roster must round-trip with the new enrol_code column.

The Graph certificate credential IS tested: the client assertion is the whole of our
authentication, it is built by hand here rather than by a library, and it is unverifiable
against the real tenant until IT uploads the public half - so a self-signed throwaway
keypair stands in for the real one.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from dsl_course import discovery, enrol_codes, gh_contents, mailer, roster
from dsl_course.gh_contents import read_csv
from tests.conftest import ROSTER_HEADER, roster_files


def _student(email="a@x.edu", name="Ada", code="", handle=""):
    return roster.Student(email, name, handle, "", code)


def test_assign_codes_fills_blanks_only_and_is_unique():
    students = [_student(code=""), _student(code="dsl-keep"), _student(code="")]
    # deterministic generator so the test can assert behaviour, not randomness
    seq = iter(
        ["dsl-aaa", "dsl-keep", "dsl-bbb"]
    )  # second clashes with existing -> skipped
    added = enrol_codes.assign_codes(students, gen=lambda: next(seq))
    assert added == 2
    assert students[1].enrol_code == "dsl-keep"  # existing untouched
    codes = [s.enrol_code for s in students]
    assert len(set(codes)) == 3 and "" not in codes  # all filled, all unique


def test_make_code_shape():
    code = enrol_codes.make_code()
    assert code.startswith("dsl-") and len(code) == 10
    suffix = code[4:]  # the random part (the "dsl-" prefix legitimately contains 'l')
    assert not (
        set(suffix) & set("0o1il")
    )  # ambiguous chars excluded from the random part


def test_code_message_contains_code_and_targets_university_email():
    s = _student(email="ada@uni.edu", name="Ada", code="dsl-xyz123")
    to, _subject, body = enrol_codes.code_message(
        s, "https://github.com/org/join/issues"
    )
    assert to == "ada@uni.edu"
    assert "dsl-xyz123" in body and "join" in body


def test_code_message_names_the_course_and_falls_back_when_unnamed():
    # The code arrives in a student's inbox alongside other courses' mail, so the opening
    # line names this one - but a course org with no name yet must read as plain English,
    # never as a blank or a literal placeholder.
    s = _student(email="ada@uni.edu", name="Ada", code="dsl-xyz123")
    url = "https://github.com/org/join/issues"
    _to, subject, named = enrol_codes.code_message(s, url, "Deep Learning")
    assert "To join the Deep Learning course on GitHub" in named
    assert subject == "Your enrolment code for Deep Learning"
    _to, subject, unnamed = enrol_codes.code_message(s, url)
    assert "To join the course on GitHub" in unnamed
    assert subject == "Your course enrolment code"


def test_mailer_dry_run_previews_without_config(capsys):
    msgs = [("ada@x.edu", "Subj", "Hello Ada, your code is dsl-abc123")]
    # no transport env needed for a dry-run preview
    assert mailer.send_bulk(msgs, dry_run=True) == ["ada@x.edu"]
    # The workflow log is PUBLIC: never the body (name + live enrol code), never the
    # address in full.
    out = capsys.readouterr().out
    assert "dsl-abc123" not in out and "Ada" not in out and "ada@x.edu" not in out
    # Not even the mask: `a***@x.edu` beside a roster is a name. A count and the subject.
    assert "a***@" not in out
    assert "would send -> 1 recipient(s): Subj" in out


def test_a_real_send_never_prints_the_sample(capsys, monkeypatch):
    monkeypatch.setattr(mailer, "graph_config_from_env", lambda: None)
    mailer.send_bulk([("ada@x.edu", "Subj", "Body")], sample="SHOULD NOT APPEAR")
    captured = capsys.readouterr()
    assert "SHOULD NOT APPEAR" not in captured.out + captured.err


def test_mask_email_keeps_one_character_and_the_domain():
    assert mailer.mask_email("katarzyna.nowak@students.hertie-school.org") == (
        "k***@students.hertie-school.org"
    )
    assert mailer.mask_email("nodomain") == "n***"


def test_graph_config_from_env_needs_all_four(monkeypatch):
    for k in (
        "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_ID",
        "GRAPH_CLIENT_CERT",
        "GRAPH_SENDER",
    ):
        monkeypatch.delenv(k, raising=False)
    assert mailer.graph_config_from_env() is None
    monkeypatch.setenv("GRAPH_TENANT_ID", "t")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "c")
    monkeypatch.setenv("GRAPH_CLIENT_CERT", "pem")
    assert mailer.graph_config_from_env() is None  # sender still missing
    monkeypatch.setenv("GRAPH_SENDER", "bot@x.edu")
    cfg = mailer.graph_config_from_env()
    assert cfg and cfg.sender == "bot@x.edu" and cfg.tenant_id == "t"


# ------------------------------------------------- the certificate credential (no secret)


def _self_signed_pem(days: int = 730, with_key: bool = True) -> str:
    """A throwaway cert (+ key) in the one-secret PEM layout `mailer` expects.

    Generated in-test rather than committed as a fixture: a committed private key - even a
    disposable one - is the kind of thing that gets found by a scanner and mistaken for a
    live credential. `with_key=False` gives the certificate alone, standing in for a
    half-pasted secret."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-mailer")])
    now = datetime.now(tz=UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    if not with_key:
        return cert_pem
    return (
        cert_pem
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    )


def _decode_segment(segment: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_client_assertion_is_signed_by_the_key_and_names_the_certificate():
    pem = _self_signed_pem()
    cfg = mailer.GraphConfig("tenant-1", "client-1", pem, "bot@x.edu")
    header_seg, claims_seg, sig_seg = mailer._client_assertion(cfg).split(".")

    header = _decode_segment(header_seg)
    assert header["alg"] == "RS256" and header["typ"] == "JWT"
    # Entra finds the uploaded public half by this thumbprint; if it doesn't match the
    # certificate byte-for-byte the token request fails with an opaque error.
    cert = x509.load_pem_x509_certificate(pem.encode())
    assert header["x5t"] == mailer._b64url(cert.fingerprint(hashes.SHA1()))

    claims = _decode_segment(claims_seg)
    # aud must be the tenant's own token endpoint - a mismatch here is silently rejected.
    assert claims["aud"] == (
        "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    )
    assert claims["iss"] == claims["sub"] == "client-1"
    assert 0 < claims["exp"] - claims["nbf"] <= 600  # short-lived, single-use
    assert claims["jti"]

    # The signature must verify against the certificate's public key, over the exact
    # `header.claims` bytes - this is the whole proof the client secret used to provide.
    sig = base64.urlsafe_b64decode(sig_seg + "=" * (-len(sig_seg) % 4))
    cert.public_key().verify(
        sig,
        f"{header_seg}.{claims_seg}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_assertions_are_unique_per_request():
    # jti is a replay guard; a reused one would let a captured assertion be replayed.
    cfg = mailer.GraphConfig("t", "c", _self_signed_pem(), "bot@x.edu")
    first = _decode_segment(mailer._client_assertion(cfg).split(".")[1])
    second = _decode_segment(mailer._client_assertion(cfg).split(".")[1])
    assert first["jti"] != second["jti"]


def test_an_expiring_certificate_is_flagged_loudly(capsys):
    # A certificate lapses silently: enrolment codes and grade notices just stop. The warning
    # is the only signal anyone gets, so it must fire BEFORE expiry, not on failure.
    cfg = mailer.GraphConfig("t", "c", _self_signed_pem(days=5), "bot@x.edu")
    mailer._client_assertion(cfg)
    assert "expires in" in capsys.readouterr().err  # log_err -> stderr


@pytest.mark.parametrize(
    ("pem", "expected"),
    [
        ("not a pem at all", "no readable PEM certificate"),
        # Certificate present, key missing - the classic half-pasted secret.
        (_self_signed_pem(with_key=False), "no usable PEM"),
    ],
)
def test_a_malformed_cert_secret_fails_with_one_actionable_line(pem, expected):
    # This runs in an Actions log, where a cryptography traceback tells faculty nothing.
    cfg = mailer.GraphConfig("t", "c", pem, "bot@x.edu")
    with pytest.raises(RuntimeError, match=expected):
        mailer._client_assertion(cfg)


def test_a_failed_graph_token_is_not_reported_as_nothing_to_send(monkeypatch):
    # "0 sent" is the same number an empty batch produces, so a dead token used to look
    # like a quiet no-op. Nothing was sent AND nothing could be: that is a failure.
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: None)
    cfg = mailer.GraphConfig("t", "c", "pem", "bot@x.edu")
    with pytest.raises(RuntimeError, match="token request failed"):
        mailer._send_via_graph(cfg, [("a@x.edu", "Subj", "Body")])


# --------------------------- codes are written to .system/roster.csv, never students.csv


HEADER = ROSTER_HEADER + "\n"
SYS = roster.SYSTEM_PATH
STUDENTS = roster.STUDENTS_PATH
STALE = HEADER + "ada@uni.edu,Ada,enrolled,,,\nbob@uni.edu,Bob,enrolled,,,\n"
# What the roster file looks like after a Join issue bound Ada's handle mid-run.
FRESH = roster_files(HEADER + "ada@uni.edu,Ada,enrolled,ada-l,42,\n")[SYS]


def _codes():
    return {"ada@uni.edu": "dsl-aaa111", "bob@uni.edu": "dsl-bbb222"}


def _system(text: str) -> dict[str, dict]:
    return {r["email"]: r for r in read_csv(text, ("email",), SYS)}


def _files(monkeypatch, files: dict[str, str], *, writes_ok=True) -> list[str]:
    """Serve `files` (path -> text) as semester-config, writes landing back in it. Returns
    the texts written, in order."""
    written: list[str] = []

    def with_sha(org, repo, path):
        return (files[path], f"sha-{len(written)}") if path in files else None

    monkeypatch.setattr(enrol_codes, "get_file_content", lambda o, r, p: files.get(p))
    monkeypatch.setattr(enrol_codes, "get_file_with_sha", with_sha)
    monkeypatch.setattr(gh_contents, "get_file_with_sha", with_sha)

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        if not writes_ok:
            return False
        assert path == SYS, "the send writes only the engine's roster file"
        files[path] = content.decode()
        written.append(files[path])
        return True

    monkeypatch.setattr(gh_contents, "put_file", fake_put_file)
    return written


def test_a_refused_write_is_retried_against_the_fresh_roster(monkeypatch):
    # A Join issue bound Ada's handle while this run was minting codes: the retry is
    # re-applied onto the fresh file, so the binding survives.
    written: list[str] = []
    attempts = {"n": 0}

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        attempts["n"] += 1
        written.append(content.decode())
        return expected_sha == "fresh"

    monkeypatch.setattr(gh_contents, "put_file", fake_put_file)
    monkeypatch.setattr(
        gh_contents, "get_file_with_sha", lambda org, repo, path: (FRESH, "fresh")
    )
    assert (
        enrol_codes.write_column(
            "SEMESTER", None, "", "enrol_code", _codes(), "roster: assign"
        )
        == written[-1]
    )
    assert attempts["n"] == 2
    rows = _system(written[-1])
    assert rows["ada@uni.edu"]["github_handle"] == "ada-l"
    assert rows["ada@uni.edu"]["enrol_code"] == "dsl-aaa111"
    assert rows["bob@uni.edu"]["enrol_code"] == "dsl-bbb222"


def test_the_retry_gives_up_after_a_bounded_number_of_attempts(monkeypatch):
    monkeypatch.setattr(gh_contents, "put_file", lambda *a, **k: False)
    monkeypatch.setattr(
        gh_contents, "get_file_with_sha", lambda org, repo, path: (FRESH, "fresh")
    )
    assert (
        enrol_codes.write_column(
            "SEMESTER", None, "", "enrol_code", _codes(), "roster: assign"
        )
        is None
    )


def test_a_code_that_arrived_in_between_is_left_alone(monkeypatch):
    theirs = roster_files(HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-theirs\n")[SYS]
    written: list[str] = []

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        written.append(content.decode())
        return expected_sha == "fresh"

    monkeypatch.setattr(gh_contents, "put_file", fake_put_file)
    monkeypatch.setattr(
        gh_contents, "get_file_with_sha", lambda org, repo, path: (theirs, "fresh")
    )
    assert enrol_codes.write_column(
        "SEMESTER", None, "", "enrol_code", _codes(), "roster: assign"
    )
    assert "dsl-theirs" in written[-1] and "dsl-aaa111" not in written[-1]


def test_the_emails_carry_the_code_the_roster_actually_holds(monkeypatch):
    # Another run filled Ada's code first, so our write was refused and the retry left
    # `dsl-theirs` in place. Emailing the code THIS run generated in memory gave Ada one
    # that enrols nobody.
    files = roster_files(STALE)
    del files[SYS]
    theirs = roster_files(HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-theirs\n")[SYS]
    monkeypatch.setattr(enrol_codes, "get_file_content", lambda o, r, p: files.get(p))
    reads = [None, (theirs, "fresh")]

    def with_sha(org, repo, path):
        return reads.pop(0) if len(reads) > 1 else reads[0]

    monkeypatch.setattr(enrol_codes, "get_file_with_sha", with_sha)
    monkeypatch.setattr(gh_contents, "get_file_with_sha", with_sha)

    def fake_put_file(org, repo, path, content, msg, expected_sha=None):
        if expected_sha != "fresh":
            return False
        reads[0] = (content.decode(), "fresh")
        return True

    monkeypatch.setattr(gh_contents, "put_file", fake_put_file)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "Test")
    _transport(monkeypatch, True)
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        enrol_codes.mailer,
        "send_bulk",
        lambda messages, dry_run=False, sample=None: (
            sent.extend(messages) or [m[0] for m in messages]
        ),
    )
    assert enrol_codes.run("SEMESTER") is enrol_codes.Outcome.SENT
    ada = next(body for to, _subject, body in sent if to == "ada@uni.edu")
    assert "dsl-theirs" in ada


def test_students_csv_is_never_written_and_its_extra_columns_never_read(monkeypatch):
    files = {
        STUDENTS: "student_id,email,name,role,notes\n1,ada@uni.edu,Ada,audit,keen\n",
    }
    before = files[STUDENTS]
    written = _files(monkeypatch, files)
    _transport(monkeypatch, True)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "Test")
    monkeypatch.setattr(
        enrol_codes.mailer, "send_bulk", lambda m, **k: [x[0] for x in m]
    )
    assert enrol_codes.run("SEMESTER") is enrol_codes.Outcome.SENT
    assert files[STUDENTS] == before
    assert list(_system(written[-1])) == ["ada@uni.edu"]


# ------------------------------------------------ run(): who gets mailed, and who does not


def _transport(monkeypatch, configured: bool) -> None:
    """Set (or clear) every GRAPH_* secret. `run` asks BEFORE it stamps anything, so a
    test that wants a send at all has to have a transport for it to go out on."""
    for name in mailer.GRAPH_ENV:
        if configured:
            monkeypatch.setenv(name, "set")
        else:
            monkeypatch.delenv(name, raising=False)


def _run_with(monkeypatch, roster_text, *, sends=None, writes_ok=True, transport=True):
    """Drive `run` against one combined fixture (`roster_files`). Returns (outcome,
    messages sent, roster-file texts written)."""
    _transport(monkeypatch, transport)
    # A combined fixture is split; anything else (a header nothing can read) as it is.
    combined = roster_text.startswith(ROSTER_HEADER)
    files = roster_files(roster_text) if combined else {STUDENTS: roster_text}
    sent: list[tuple[str, str, str]] = []
    written = _files(monkeypatch, files, writes_ok=writes_ok)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "Test")
    monkeypatch.setattr(
        enrol_codes.mailer,
        "send_bulk",
        lambda messages, dry_run=False, sample=None: (
            sent.extend(messages)
            or [m[0] for m in messages][: len(messages) if sends is None else sends]
        ),
    )
    outcome = enrol_codes.run("SEMESTER")
    return outcome, sent, written


def test_a_student_already_marked_sent_is_not_emailed_again(monkeypatch):
    text = (
        HEADER
        + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,2026-08-31T09:00:00+00:00\n"
        + "bob@uni.edu,Bob,enrolled,,,dsl-bbb222,\n"
    )
    outcome, sent, _ = _run_with(monkeypatch, text)
    assert outcome is enrol_codes.Outcome.SENT
    assert [to for to, _s, _b in sent] == ["bob@uni.edu"]


def test_the_sent_marker_is_written_only_for_recipients_that_went_out(monkeypatch):
    text = (
        HEADER
        + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
        + "bob@uni.edu,Bob,enrolled,,,dsl-bbb222,\n"
    )
    outcome, _sent, written = _run_with(monkeypatch, text, sends=1)
    assert outcome is enrol_codes.Outcome.FAILED
    stamped = {
        e: bool(r["code_sent_at"].strip()) for e, r in _system(written[-1]).items()
    }
    assert stamped == {"ada@uni.edu": True, "bob@uni.edu": False}


def test_a_batch_that_went_nowhere_for_want_of_secrets_reads_as_a_missing_transport(
    monkeypatch,
):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
    outcome, sent, written = _run_with(monkeypatch, text, sends=0, transport=False)
    assert outcome is enrol_codes.Outcome.NO_TRANSPORT
    assert sent == [] and written == []


def test_a_wholly_rejected_batch_on_a_live_transport_is_still_a_failure(monkeypatch):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
    outcome, _sent, written = _run_with(monkeypatch, text, sends=0)
    assert outcome is enrol_codes.Outcome.FAILED
    assert _system(written[-1])["ada@uni.edu"]["code_sent_at"].strip() == ""


def test_a_roster_that_cannot_be_stamped_mails_nobody_however_often_it_is_run(
    monkeypatch, capsys
):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
    for tick in range(3):
        outcome, sent, written = _run_with(monkeypatch, text, writes_ok=False)
        assert outcome is enrol_codes.Outcome.FAILED, tick
        assert sent == [] and written == [], tick
    err = capsys.readouterr().err
    assert "nothing emailed, so re-running is safe" in err
    assert "ada@uni.edu" not in err


def test_a_transport_that_raises_gives_the_claim_back_before_it_propagates(monkeypatch):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
    _transport(monkeypatch, True)
    written = _files(monkeypatch, roster_files(text))
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "Test")
    monkeypatch.setattr(
        enrol_codes.mailer,
        "send_bulk",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("token request failed")),
    )
    with pytest.raises(RuntimeError):
        enrol_codes.run("SEMESTER")
    assert _system(written[-1])["ada@uni.edu"]["code_sent_at"].strip() == ""


def test_a_claim_that_cannot_be_released_names_the_exact_stamp_to_clear(
    monkeypatch, capsys
):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
    _transport(monkeypatch, True)
    files = roster_files(text)
    written = _files(monkeypatch, files)
    real_put = gh_contents.put_file

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        if "release" in message:
            return False
        return real_put(org, repo, path, content, message, expected_sha)

    monkeypatch.setattr(gh_contents, "put_file", fake_put_file)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "Test")
    monkeypatch.setattr(enrol_codes.mailer, "send_bulk", lambda *a, **k: [])
    assert enrol_codes.run("SEMESTER") is enrol_codes.Outcome.FAILED
    err = capsys.readouterr().err
    stamp = _system(written[-1])["ada@uni.edu"]["code_sent_at"]
    assert f"code_sent_at={stamp}" in err
    assert "by hand" in err
    assert "ada@uni.edu" not in err


def test_two_roster_rows_sharing_an_email_get_one_code_email(monkeypatch):
    text = HEADER + "ada@uni.edu,Ada,enrolled,,,\nada@uni.edu,Ada,enrolled,,,\n"
    _rc, sent, written = _run_with(monkeypatch, text)
    assert [to for to, _s, _b in sent] == ["ada@uni.edu"]
    assert list(_system(written[-1])) == ["ada@uni.edu"]


def test_rows_sharing_a_code_are_not_mailed_it(monkeypatch, capsys):
    text = (
        HEADER
        + "ada@uni.edu,Ada,enrolled,,,dsl-aaa111,\n"
        + "eve@uni.edu,Eve,enrolled,,,dsl-aaa111,\n"
        + "bob@uni.edu,Bob,enrolled,,,dsl-bbb222,\n"
    )
    outcome, sent, _written = _run_with(monkeypatch, text)
    assert outcome is enrol_codes.Outcome.SENT
    assert [to for to, _s, _b in sent] == ["bob@uni.edu"]
    err = capsys.readouterr().err
    assert "2 student(s) share an enrol_code" in err
    assert "ada@uni.edu" not in err and "eve@uni.edu" not in err


def test_a_semicolon_roster_sends_nothing_and_leaves_the_run_green(monkeypatch, capsys):
    semicolons = "email;name;role\nada@uni.edu;Ada;enrolled\n"
    outcome, sent, written = _run_with(monkeypatch, semicolons)
    assert outcome is enrol_codes.Outcome.UNUSABLE_ROSTER
    assert not enrol_codes.reds_the_run(outcome)
    assert sent == [] and written == []
    err = capsys.readouterr().err
    assert "header lacks email" in err
    assert "ada@uni.edu" not in err


def test_a_roster_with_no_header_row_names_no_cell_in_the_public_log(
    monkeypatch, capsys
):
    headerless = "ada@uni.edu,Ada,enrolled\nbob@uni.edu,Bob,enrolled\n"
    outcome, sent, written = _run_with(monkeypatch, headerless)
    assert outcome is enrol_codes.Outcome.UNUSABLE_ROSTER
    assert sent == [] and written == []
    err = capsys.readouterr().err
    assert "header lacks" in err
    for cell in ("ada@uni.edu", "Ada"):
        assert cell not in err


def test_an_absent_roster_and_a_missing_transport_still_red(monkeypatch):
    reds = set(enrol_codes.Outcome) - set(enrol_codes._GREEN)
    assert reds == {
        enrol_codes.Outcome.NO_ROSTER,
        enrol_codes.Outcome.NO_TRANSPORT,
        enrol_codes.Outcome.FAILED,
    }
    assert all(enrol_codes.reds_the_run(o) for o in reds)


def test_a_roster_with_only_a_header_is_green(monkeypatch, capsys):
    outcome, sent, written = _run_with(monkeypatch, HEADER)
    assert outcome is enrol_codes.Outcome.EMPTY_ROSTER
    assert not enrol_codes.reds_the_run(outcome)
    assert sent == [] and written == []
    assert "no rows yet" in capsys.readouterr().err


def test_run_reds_when_the_roster_is_missing(monkeypatch, capsys):
    _files(monkeypatch, {})
    outcome = enrol_codes.run("SEMESTER")
    assert outcome is enrol_codes.Outcome.NO_ROSTER
    assert enrol_codes.reds_the_run(outcome)
    assert "Could not find students.csv" in capsys.readouterr().err


# ------------------------- the dispatched path's trust boundary (--dispatched-by)


def _dispatched(monkeypatch, semester, registered, course="Course-Org"):
    """main() as the roster-push dispatcher invokes it. Returns (rc, semesters run for)."""
    ran: list[str] = []
    monkeypatch.setattr(enrol_codes, "discover_semesters", lambda org: registered)
    monkeypatch.setattr(
        enrol_codes, "run", lambda org: ran.append(org) or enrol_codes.Outcome.SENT
    )
    monkeypatch.setattr(enrol_codes.status, "refresh", lambda course, semester=None: 0)
    monkeypatch.setattr(
        "sys.argv",
        [
            "enrol_codes",
            "--semester-org",
            semester,
            "--dispatched-by",
            course,
            "--no-preview",
        ],
    )
    return enrol_codes.main(), ran


def test_a_dispatched_send_refreshes_the_semesters_status(monkeypatch):
    # The codes just sent are the roster's `codes_sent` count in status.json.
    refreshed: list = []
    _dispatched(monkeypatch, "Semester-f2026", ["Semester-f2026"])
    monkeypatch.setattr(
        enrol_codes.status, "refresh", lambda *a: refreshed.append(a) or 1
    )
    # A status that could not be written does not red the send.
    assert enrol_codes.main() == 0
    assert refreshed == [("Course-Org", "Semester-f2026")]


def test_a_dispatched_send_refuses_a_semester_this_course_org_does_not_own(
    monkeypatch, capsys
):
    # A client_payload is written by whoever holds a SEMESTER's bot token - a lower trust
    # tier than the course org - so naming someone else's semester would have this run
    # generate codes into that semester's roster and email its students. The registry is the
    # authority, and a name absent from it is refused before anything is read.
    rc, ran = _dispatched(monkeypatch, "Someone-Elses-f2026", ["Semester-f2026"])
    assert (rc, ran) == (1, [])
    err = capsys.readouterr().err
    assert "Someone-Elses-f2026 is not registered under Course-Org" in err
    assert "Semester-f2026" in err  # what IS registered, so the fix is obvious


def test_an_empty_registry_authorises_no_dispatched_semester(monkeypatch, capsys):
    # The bug this exists to prevent: short-circuiting on an empty registry let a course
    # org that had never registered a semester accept any org name a dispatch cared to name.
    rc, ran = _dispatched(monkeypatch, "Semester-f2026", [])
    assert (rc, ran) == (1, [])
    assert "lists nothing" in capsys.readouterr().err


def test_a_dispatched_send_accepts_a_registered_semester_whatever_its_casing(
    monkeypatch,
):
    # GitHub org names are case-insensitive, and the registry's spelling need not match
    # the dispatch's - refusing on case alone would break the automatic path for real.
    assert _dispatched(monkeypatch, "semester-F2026", ["Semester-f2026"]) == (
        0,
        ["semester-F2026"],
    )


def test_a_hand_run_without_dispatched_by_never_consults_the_registry(monkeypatch):
    # A maintainer naming a semester on the command line has no untrusted input to check, so
    # it pays for no registry read. Every production send passes --dispatched-by.
    def boom(org):
        raise AssertionError("a hand run must not read the semester registry")

    ran: list[str] = []
    monkeypatch.setattr(enrol_codes, "discover_semesters", boom)
    monkeypatch.setattr(
        enrol_codes, "run", lambda org: ran.append(org) or enrol_codes.Outcome.SENT
    )
    monkeypatch.setattr(
        "sys.argv", ["enrol_codes", "--semester-org", "Semester-f2026", "--no-preview"]
    )
    assert (enrol_codes.main(), ran) == (0, ["Semester-f2026"])


def test_a_closed_out_semester_is_sent_no_codes(monkeypatch):
    # Registered, so the trust-boundary check passes - but its semester-config is
    # read-only, so the `code_sent_at` write-back could not land, and nobody is being
    # enrolled into a term that is over.
    monkeypatch.setattr(enrol_codes, "semester_is_live", lambda org: False)

    def boom(org):
        raise AssertionError("a frozen semester must not be sent enrolment codes")

    monkeypatch.setattr(
        enrol_codes, "discover_semesters", lambda org: ["Semester-f2026"]
    )
    monkeypatch.setattr(enrol_codes, "run", boom)
    monkeypatch.setattr(
        "sys.argv",
        [
            "enrol_codes",
            "--semester-org",
            "Semester-f2026",
            "--dispatched-by",
            "Course-Org",
            "--no-preview",
        ],
    )
    assert enrol_codes.main() == 0
