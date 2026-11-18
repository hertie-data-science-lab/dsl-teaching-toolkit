"""Bootstrap copies the four GRAPH_* mail transport secrets onto a new course org.

Centrally they are hertie-data-science-lab org secrets shared with the toolkit repo;
`.github/workflows/bootstrap-org.yml` passes them into the run's env, and
`--propagate-secret` copies each one present onto the course org through `set_org_secret`
(over stdin). Absent ones are skipped by name; no value is ever logged.
"""

from __future__ import annotations

import pytest

from dsl_course import bootstrap_course as bc
from dsl_course import mailer
from tests.conftest import stub_bootstrap

# Multi-line like the real certificate + key PEM (no PEM armour, so the pre-commit secrets
# hook does not flag a fixture).
CERT = "cert-line-1\ncert-line-2\nkey-line-3\n"
VALUES = {
    "GRAPH_TENANT_ID": "tenant-0000",
    "GRAPH_CLIENT_ID": "client-1111",
    "GRAPH_SENDER": "mailbox@example.org",
    "GRAPH_CLIENT_CERT": CERT,
}


@pytest.fixture
def published(monkeypatch):
    calls: list = []
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: calls.append(a) or True)
    return calls


def _assert_no_value_logged(capsys) -> None:
    out = capsys.readouterr()
    for value in VALUES.values():
        for line in value.splitlines():
            assert line not in out.out
            assert line not in out.err


def test_all_four_are_set_with_their_exact_values(monkeypatch, published, capsys):
    for name, value in VALUES.items():
        monkeypatch.setenv(name, value)

    assert bc.propagate_mail_secrets("Course-Org") == 0
    assert published == [("Course-Org", n, VALUES[n]) for n in mailer.GRAPH_ENV]
    # The PEM keeps its newlines: the mailer needs the certificate and key intact.
    assert ("Course-Org", "GRAPH_CLIENT_CERT", CERT) in published
    _assert_no_value_logged(capsys)


def test_an_absent_one_is_skipped_by_name(monkeypatch, published, capsys):
    for name, value in VALUES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GRAPH_SENDER", "  ")

    assert bc.propagate_mail_secrets("Course-Org") == 0
    assert [a[1] for a in published] == [
        n for n in mailer.GRAPH_ENV if n != "GRAPH_SENDER"
    ]
    out = capsys.readouterr().out
    assert "[skip] GRAPH_SENDER not in this run's env" in out
    for value in VALUES.values():
        assert value.splitlines()[0] not in out


def test_none_present_is_one_line_and_never_a_failure(monkeypatch, published, capsys):
    for name in mailer.GRAPH_ENV:
        monkeypatch.delenv(name, raising=False)

    assert bc.propagate_mail_secrets("Course-Org") == 0
    assert published == []
    out = capsys.readouterr().out
    assert out.count("[skip]") == 1
    assert "mail transport not propagated" in out


def test_a_failed_write_counts(monkeypatch, capsys):
    for name, value in VALUES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        bc, "set_org_secret", lambda org, name, value: name != "GRAPH_CLIENT_CERT"
    )

    assert bc.propagate_mail_secrets("Course-Org") == 1


def test_the_cert_goes_over_stdin_with_its_newlines(monkeypatch, capsys):
    # The real setter, with only `gh` and the repo probes stubbed: the PEM must reach
    # `gh secret set` as stdin, byte for byte, and never as an argv element.
    monkeypatch.setattr(bc, "repo_missing", lambda org, r: r != ".github")
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: False)
    calls: list = []
    monkeypatch.setattr(bc, "gh", lambda *a, **k: calls.append((a, k)) or (0, ""))

    assert bc.set_org_secret("Course-Org", "GRAPH_CLIENT_CERT", CERT) is True
    ((args, kwargs),) = calls
    assert kwargs["stdin"] == CERT
    assert not any("-line-" in a or a.startswith("--body") for a in args)
    _assert_no_value_logged(capsys)


def _run_bootstrap(monkeypatch, *extra: str) -> None:
    stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    for name, value in VALUES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "sys.argv",
        ["bootstrap_course", "--org", "Course-Org", "--propagate-secret", *extra],
    )
    bc.main()


def test_course_bootstrap_propagates_them(monkeypatch, published, capsys):
    _run_bootstrap(monkeypatch)

    names = [a[1] for a in published]
    assert names[0] == "DSL_BOT_TOKEN"
    assert set(mailer.GRAPH_ENV) <= set(names)
    _assert_no_value_logged(capsys)


def test_semester_bootstrap_never_gets_them(monkeypatch, published, capsys):
    # The Console carries GRAPH_* in its env and bootstraps semesters with
    # --propagate-secret: a semester must still get the bot token and nothing of the mail.
    _run_bootstrap(monkeypatch, "--semester", "--course", "Course-Parent")

    names = [a[1] for a in published]
    assert "DSL_BOT_TOKEN" in names
    assert not set(mailer.GRAPH_ENV) & set(names)
