"""enrol-codes + mailer pure cores: code assignment must fill only blanks and stay unique
(a clash would let one student claim another's row), the message must carry the code, and
the roster must round-trip with the new enrol_code column. SMTP send is wiring, not tested.
"""

from __future__ import annotations

import pytest

from dsl_course import enrol_codes, mailer, roster


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


def test_roster_dump_roundtrips_with_enrol_code():
    students = [
        _student(email="ada@uni.edu", name="Ada", code="dsl-abc", handle="ada-l")
    ]
    reparsed = roster.parse(roster.dump(students))
    assert reparsed[0].enrol_code == "dsl-abc"
    assert reparsed[0].hertie_email == "ada@uni.edu"
    assert reparsed[0].onboarded is True


def test_mailer_dry_run_previews_without_config(capsys):
    msgs = [("ada@x.edu", "Subj", "Hello Ada, your code is dsl-abc123")]
    # no SMTP env needed for a dry-run preview
    assert mailer.send_bulk(msgs, dry_run=True) == 1
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
    assert mailer.send_bulk(msgs, dry_run=True, sample=sample) == 2
    out = capsys.readouterr().out
    assert out.count(mailer.SAMPLE_HEADER) == 1
    assert "<code>" in out and "<name>" in out
    assert "dsl-abc123" not in out and "Ada" not in out and "Bo" not in out


def test_a_real_send_never_prints_the_sample(capsys, monkeypatch):
    monkeypatch.setattr(mailer, "graph_config_from_env", lambda: None)
    monkeypatch.setattr(mailer, "smtp_config_from_env", lambda: None)
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


def test_smtp_config_from_env_needs_all_three(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.smtp_config_from_env() is None
    monkeypatch.setenv("SMTP_HOST", "smtp.x")
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    cfg = mailer.smtp_config_from_env()
    assert cfg and cfg.port == 587 and cfg.from_addr == "u"  # defaults applied


def test_graph_config_from_env_needs_all_four(monkeypatch):
    for k in (
        "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_ID",
        "GRAPH_CLIENT_SECRET",
        "GRAPH_SENDER",
    ):
        monkeypatch.delenv(k, raising=False)
    assert mailer.graph_config_from_env() is None
    monkeypatch.setenv("GRAPH_TENANT_ID", "t")
    monkeypatch.setenv("GRAPH_CLIENT_ID", "c")
    monkeypatch.setenv("GRAPH_CLIENT_SECRET", "s")
    assert mailer.graph_config_from_env() is None  # sender still missing
    monkeypatch.setenv("GRAPH_SENDER", "bot@x.edu")
    cfg = mailer.graph_config_from_env()
    assert cfg and cfg.sender == "bot@x.edu" and cfg.tenant_id == "t"


def test_a_failed_graph_token_is_not_reported_as_nothing_to_send(monkeypatch):
    # "0 sent" is the same number an empty batch produces, so a dead token used to look
    # like a quiet no-op. Nothing was sent AND nothing could be: that is a failure.
    monkeypatch.setattr(mailer, "_graph_token", lambda cfg: None)
    cfg = mailer.GraphConfig("t", "c", "s", "bot@x.edu")
    with pytest.raises(RuntimeError, match="token request failed"):
        mailer._send_via_graph(cfg, [("a@x.edu", "Subj", "Body")])


# --------------------------------- writing codes must not rewrite the roster (fix 17)


def test_fill_enrol_codes_preserves_unknown_columns_and_raw_role():
    # The old round-trip through roster.dump re-serialised only roster.FIELDS, dropping
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
    out = enrol_codes.fill_enrol_codes_in_csv(text, {0: "dsl-new"})
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["enrol_code"] == "dsl-new"  # the blank cell is filled
    assert rows[0]["role"] == "audit"  # raw text, NOT normalised to enrolled/auditor
    assert rows[0]["notes"] == "keen"  # the unknown column survives
    assert rows[0]["student_id"] == "1"  # so do the columns the engine no longer reads
    assert rows[0]["section"] == "A"
    assert rows[1]["enrol_code"] == "dsl-keep"  # an existing code is never overwritten
    assert "notes" in out.splitlines()[0]  # header keeps the extra column


def test_fill_enrol_codes_appends_the_column_when_the_roster_predates_it():
    import csv
    import io

    text = "student_id,name,github_handle\n1,Ada,ada-l\n"
    out = enrol_codes.fill_enrol_codes_in_csv(text, {0: "dsl-new"})
    rows = list(csv.DictReader(io.StringIO(out)))
    assert rows[0]["enrol_code"] == "dsl-new"
    assert out.splitlines()[0].endswith("enrol_code")  # added at the end
