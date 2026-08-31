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

from dsl_course import enrol_codes, mailer, roster
from dsl_course.gh_contents import read_csv
from tests.conftest import ROSTER_HEADER


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
        s, "https://github.com/org/welcome/issues"
    )
    assert to == "ada@uni.edu"
    assert "dsl-xyz123" in body and "welcome" in body


def test_code_message_names_the_course_and_falls_back_when_unnamed():
    # The code arrives in a student's inbox alongside other courses' mail, so the opening
    # line names this one - but a course org with no name yet must read as plain English,
    # never as a blank or a literal placeholder.
    s = _student(email="ada@uni.edu", name="Ada", code="dsl-xyz123")
    url = "https://github.com/org/welcome/issues"
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
    assert "a***@x.edu" in out and "Subj" in out


def test_dry_run_prints_one_placeholder_sample_and_no_real_body(capsys):
    # The wording is the one thing a masked recipient list cannot show a reviewer, so a
    # dry run prints ONE body - rendered from placeholders, never a student's own.
    msgs = [
        ("ada@x.edu", "Subj", "Hello Ada, your code is dsl-abc123"),
        ("bo@x.edu", "Subj", "Hello Bo, your code is dsl-def456"),
    ]
    sample = enrol_codes.sample_body("https://github.com/org/welcome/issues")
    assert mailer.send_bulk(msgs, dry_run=True, sample=sample) == [
        "ada@x.edu",
        "bo@x.edu",
    ]
    out = capsys.readouterr().out
    assert out.count(mailer.SAMPLE_HEADER) == 1
    assert "<code>" in out and "<name>" in out
    assert "dsl-abc123" not in out and "Ada" not in out and "Bo" not in out


def test_a_real_send_never_prints_the_sample(capsys, monkeypatch):
    monkeypatch.setattr(mailer, "graph_config_from_env", lambda: None)
    mailer.send_bulk([("ada@x.edu", "Subj", "Body")], sample="SHOULD NOT APPEAR")
    captured = capsys.readouterr()
    assert "SHOULD NOT APPEAR" not in captured.out + captured.err


def test_sample_body_names_the_course_like_a_real_message():
    named = enrol_codes.sample_body(
        "https://github.com/org/welcome/issues", "Deep Learning"
    )
    assert "To join the Deep Learning course on GitHub" in named
    assert "<code>" in named and "<name>" in named


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


# --------------------------------- writing codes must not rewrite the roster (fix 17)


def test_fill_enrol_codes_preserves_unknown_columns_and_raw_role():
    # The old whole-file round-trip re-serialised only roster.FIELDS, dropping
    # every column the engine does not read - a faculty-added `notes`, and the retired
    # `student_id`/`section` a deployed cohort's roster still carries - and normalising
    # `role`. A surgical cell edit must leave every other column and each cell's raw text
    # exactly as written.
    import csv
    import io

    text = (
        "student_id,hertie_email,name,github_handle,github_id,section,enrol_code,role,notes\n"
        "1,ada@uni.edu,Ada,ada-l,42,A,,audit,keen\n"
        "2,bob@uni.edu,Bob,bob-b,43,B,dsl-keep,enrolled,\n"
    )
    out = enrol_codes.fill_column_in_csv(text, "enrol_code", {0: "dsl-new"})
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["enrol_code"] == "dsl-new"  # the blank cell is filled
    assert rows[0]["role"] == "audit"  # raw text, NOT normalised to enrolled/auditor
    assert rows[0]["notes"] == "keen"  # the unknown column survives
    assert rows[0]["student_id"] == "1"  # so do the columns the engine no longer reads
    assert rows[0]["section"] == "A"
    assert rows[1]["enrol_code"] == "dsl-keep"  # an existing code is never overwritten
    assert "notes" in out.splitlines()[0]  # header keeps the extra column


def test_fill_column_appends_the_column_when_the_roster_predates_it():
    import csv
    import io

    # A roster predating the column, but still a roster: the required columns are what
    # `roster.parse` requires, and only `enrol_code` is optional here.
    text = "hertie_email,name,github_handle\nada@uni.edu,Ada,ada-l\n"
    out = enrol_codes.fill_column_in_csv(text, "enrol_code", {0: "dsl-new"})
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["enrol_code"] == "dsl-new"
    assert out.splitlines()[0].endswith("enrol_code")  # added at the end


def test_a_semicolon_export_is_refused_rather_than_written_back_mangled():
    # A German-locale Excel saves `;`-delimited CSV. DictReader sees ONE header column and
    # every field reads "", so this path used to bolt an `enrol_code` column onto the
    # single mangled column and commit that over the roster - exit 0, nobody told. These
    # two readers bypassed `roster.parse`, which has refused it all along.
    text = "hertie_email;name;github_handle\nada@uni.edu;Ada;ada-l\n"
    for call in (
        lambda: enrol_codes.fill_column_in_csv(text, "enrol_code", {0: "dsl-new"}),
        lambda: enrol_codes.rows_for_values(text, [(0, "ada@uni.edu", "dsl-new")]),
    ):
        with pytest.raises(RuntimeError, match="comma-separated"):
            call()


# ------------------- a code write must not revert a Join binding that landed meanwhile


HEADER = ROSTER_HEADER + "\n"
STALE = HEADER + "ada@uni.edu,Ada,,,,enrolled\nbob@uni.edu,Bob,,,,enrolled\n"
# What the roster looks like after a Join issue bound Ada's handle mid-run.
FRESH = HEADER + "ada@uni.edu,Ada,ada-l,42,,enrolled\nbob@uni.edu,Bob,,,,enrolled\n"


def _codes():
    return [(0, "ada@uni.edu", "dsl-aaa111"), (1, "bob@uni.edu", "dsl-bbb222")]


def test_a_refused_write_is_retried_against_the_fresh_roster(monkeypatch):
    # The failure: put_file re-read the sha at write time, so this run's stale copy - with
    # no handle for Ada - overwrote the Join binding, and Ada could not be provisioned.
    written: list[str] = []
    attempts = {"n": 0}

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        attempts["n"] += 1
        written.append(content.decode())
        return expected_sha == "fresh"  # only the up-to-date sha is accepted

    monkeypatch.setattr(enrol_codes, "put_file", fake_put_file)
    monkeypatch.setattr(
        enrol_codes, "get_file_with_sha", lambda org, repo, path: (FRESH, "fresh")
    )
    assert (
        enrol_codes.write_column(
            "COHORT", STALE, "stale", "enrol_code", _codes(), "roster: assign"
        )
        == written[-1]
    )
    assert attempts["n"] == 2
    # The retry carries Ada's handle - it was never this run's to remove - and both codes.
    assert "ada-l" in written[-1]
    assert "dsl-aaa111" in written[-1] and "dsl-bbb222" in written[-1]


def test_the_retry_gives_up_after_a_bounded_number_of_attempts(monkeypatch):
    monkeypatch.setattr(enrol_codes, "put_file", lambda *a, **k: False)
    monkeypatch.setattr(
        enrol_codes, "get_file_with_sha", lambda org, repo, path: (FRESH, "fresh")
    )
    assert (
        enrol_codes.write_column(
            "COHORT", STALE, "stale", "enrol_code", _codes(), "roster: assign"
        )
        is None
    )


def test_a_code_that_arrived_in_between_is_left_alone(monkeypatch):
    # Another run (or a faculty edit) already filled Ada's cell. Ours must not replace it.
    theirs = (
        HEADER + "ada@uni.edu,Ada,,,dsl-theirs,enrolled\nbob@uni.edu,Bob,,,,enrolled\n"
    )
    written: list[str] = []

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        written.append(content.decode())
        return expected_sha == "fresh"

    monkeypatch.setattr(enrol_codes, "put_file", fake_put_file)
    monkeypatch.setattr(
        enrol_codes, "get_file_with_sha", lambda org, repo, path: (theirs, "fresh")
    )
    assert enrol_codes.write_column(
        "COHORT", STALE, "stale", "enrol_code", _codes(), "roster: assign"
    )
    assert "dsl-theirs" in written[-1] and "dsl-aaa111" not in written[-1]


def test_the_emails_carry_the_code_the_roster_actually_holds(monkeypatch):
    # Another run filled Ada's cell first, so our write was refused and the retry left
    # `dsl-theirs` in place. Emailing the code THIS run generated in memory gave Ada one
    # that enrols nobody: the Join issue rejected her, with no sign anything went wrong.
    theirs = (
        HEADER + "ada@uni.edu,Ada,,,dsl-theirs,enrolled\nbob@uni.edu,Bob,,,,enrolled\n"
    )
    # First read is the stale roster; every read after it sees theirs - the retry's
    # re-read, and then the sent-marker's.
    reads = [(STALE, "stale"), (theirs, "fresh")]
    monkeypatch.setattr(
        enrol_codes,
        "get_file_with_sha",
        lambda org, repo, path: reads.pop(0) if len(reads) > 1 else reads[0],
    )
    monkeypatch.setattr(
        enrol_codes,
        "put_file",
        lambda org, repo, path, content, msg, expected_sha=None: (
            expected_sha == "fresh"
        ),
    )
    monkeypatch.setattr(enrol_codes, "course_name_for_cohort", lambda org: "Test")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        enrol_codes.mailer,
        "send_bulk",
        lambda messages, dry_run=False, sample=None: (
            sent.extend(messages) or [m[0] for m in messages]
        ),
    )
    assert enrol_codes.run("COHORT") == 0
    ada = next(body for to, _subject, body in sent if to == "ada@uni.edu")
    assert "dsl-theirs" in ada  # not the code this run generated for her in memory


def test_rows_are_relocated_by_email_not_by_their_original_index():
    # A row inserted above shifts every index below it; the email is what identifies the
    # student the code was generated for.
    shifted = HEADER + "zoe@uni.edu,Zoe,,,,enrolled\n" + STALE[len(HEADER) :]
    assert enrol_codes.rows_for_values(shifted, _codes()) == {
        1: "dsl-aaa111",
        2: "dsl-bbb222",
    }


def test_two_rows_sharing_an_email_each_keep_their_own_code():
    # A registrar export with the same address twice (a duplicate enrolment, a shared
    # departmental inbox) mapped BOTH codes onto the first of the two rows, so the pair
    # collapsed to one dict key and the second row was left with no code at all - silently,
    # on a green run. A non-unique email re-locates nothing; those rows keep their index.
    text = (
        HEADER
        + "ada@uni.edu,Ada,,,,enrolled\n"
        + "dept@uni.edu,First,,,,enrolled\n"
        + "dept@uni.edu,Second,,,,enrolled\n"
    )
    codes = [
        (0, "ada@uni.edu", "dsl-aaa111"),
        (1, "dept@uni.edu", "dsl-bbb222"),
        (2, "dept@uni.edu", "dsl-ccc333"),
    ]
    assert enrol_codes.rows_for_values(text, codes) == {
        0: "dsl-aaa111",
        1: "dsl-bbb222",
        2: "dsl-ccc333",
    }


def test_a_row_with_no_email_keeps_its_original_index():
    text = HEADER + ",Anonymous,,,,enrolled\n"
    assert enrol_codes.rows_for_values(text, [(0, "", "dsl-zzz")]) == {0: "dsl-zzz"}


# ------------------------------------------------ run(): who gets mailed, and who does not


def _run_with(monkeypatch, roster_text, *, sends=None, writes_ok=True, dry_run=False):
    """Drive `run` against one roster. Returns (rc, messages sent, roster texts written)."""
    written: list[str] = []
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        enrol_codes,
        "get_file_with_sha",
        lambda org, repo, path: (written[-1] if written else roster_text, "sha"),
    )

    def fake_put_file(org, repo, path, content, message, expected_sha=None):
        if not writes_ok:
            return False
        written.append(content.decode())
        return True

    monkeypatch.setattr(enrol_codes, "put_file", fake_put_file)
    monkeypatch.setattr(enrol_codes, "course_name_for_cohort", lambda org: "Test")
    monkeypatch.setattr(
        enrol_codes.mailer,
        "send_bulk",
        lambda messages, dry_run=False, sample=None: (
            sent.extend(messages)
            or [m[0] for m in messages][: len(messages) if sends is None else sends]
        ),
    )
    rc = enrol_codes.run("COHORT", dry_run=dry_run)
    return rc, sent, written


def test_a_student_already_marked_sent_is_not_emailed_again(monkeypatch):
    # The predicate used to be the state of the ROSTER, not the state of the SEND: a
    # student who had their code but had not yet opened a Join issue was re-mailed on
    # every run, including runs that assigned no new codes at all.
    text = (
        HEADER
        + "ada@uni.edu,Ada,,,dsl-aaa111,enrolled,2026-08-31T09:00:00+00:00\n"
        + "bob@uni.edu,Bob,,,dsl-bbb222,enrolled,\n"
    )
    rc, sent, _ = _run_with(monkeypatch, text)
    assert rc == 0
    assert [to for to, _s, _b in sent] == ["bob@uni.edu"]


def test_the_sent_marker_is_written_only_for_recipients_that_went_out(monkeypatch):
    # A marker written off a COUNT would stamp the wrong rows and the students whose mail
    # failed would never be retried.
    text = (
        HEADER
        + "ada@uni.edu,Ada,,,dsl-aaa111,enrolled,\n"
        + "bob@uni.edu,Bob,,,dsl-bbb222,enrolled,\n"
    )
    rc, _sent, written = _run_with(monkeypatch, text, sends=1)
    assert rc == 1  # one of two did not go out
    rows = list(read_csv(written[-1], roster.REQUIRED_FIELDS, roster.ROSTER_PATH))
    stamped = {r["hertie_email"]: bool(r["code_sent_at"].strip()) for r in rows}
    assert stamped == {"ada@uni.edu": True, "bob@uni.edu": False}


def test_a_marker_write_failure_reds_the_run_and_says_re_running_re_emails(
    monkeypatch, capsys
):
    # The one failure that must never be swallowed: the students HAVE their codes and
    # nothing on disk says so, so the next run mails them all over again.
    text = HEADER + "ada@uni.edu,Ada,,,dsl-aaa111,enrolled,\n"
    rc, _sent, _written = _run_with(monkeypatch, text, writes_ok=False)
    assert rc == 1
    err = capsys.readouterr().err
    assert "re-running WILL email them again" in err
    assert "ada@uni.edu" not in err  # the log is public


def test_two_roster_rows_sharing_an_email_get_one_code_email(monkeypatch):
    # A registrar re-enrolment or a shared inbox. Each row keeps its OWN code, but the
    # person behind the address received two emails carrying two different codes, only
    # one of which binds - with nothing to say which.
    text = (
        HEADER
        + "ada@uni.edu,Ada,,,dsl-aaa111,enrolled,\n"
        + "ada@uni.edu,Ada,,,dsl-bbb222,enrolled,\n"
    )
    _rc, sent, _written = _run_with(monkeypatch, text)
    assert [to for to, _s, _b in sent] == ["ada@uni.edu"]


def test_a_dry_run_writes_nothing_and_marks_nobody(monkeypatch):
    text = HEADER + "ada@uni.edu,Ada,,,,enrolled,\n"
    rc, sent, written = _run_with(monkeypatch, text, dry_run=True)
    assert rc == 0 and written == []
    assert [to for to, _s, _b in sent] == ["ada@uni.edu"]


def test_run_reds_when_the_roster_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(enrol_codes, "get_file_with_sha", lambda org, repo, path: None)
    assert enrol_codes.run("COHORT") == 1
    assert "Could not find students.csv" in capsys.readouterr().err
