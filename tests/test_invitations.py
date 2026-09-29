"""The bot accepts its own org invitations: every pending one, all pages, and only those."""

from __future__ import annotations

import pytest

from dsl_course import invitations


def _stub_gh(monkeypatch, pending: str, fail: frozenset[str] = frozenset()):
    calls: list[tuple[str, ...]] = []

    def gh(*args: str, **_kw):
        calls.append(args)
        if "--paginate" in args:
            return 0, pending
        org = args[3].rsplit("/", 1)[1]
        return (1, "HTTP 404: Not Found") if org in fail else (0, "{}")

    monkeypatch.setattr(invitations, "gh", gh)
    return calls


def test_every_pending_invitation_on_every_page_is_accepted(monkeypatch, capsys):
    # `--paginate --jq` prints one login per line across every page.
    calls = _stub_gh(monkeypatch, "hertie-new-e1234\nhertie-new-f2026\n")
    assert invitations.accept_pending() == ["hertie-new-e1234", "hertie-new-f2026"]
    listing, *writes = calls
    assert "user/memberships/orgs?state=pending&per_page=100" in listing
    assert [w[3] for w in writes] == [
        "user/memberships/orgs/hertie-new-e1234",
        "user/memberships/orgs/hertie-new-f2026",
    ]
    assert all(w[:3] == ("api", "--method", "PATCH") for w in writes)
    assert all(w[-2:] == ("-f", "state=active") for w in writes)
    assert "accepted the invitation to hertie-new-e1234" in capsys.readouterr().out


def test_nothing_pending_writes_nothing(monkeypatch):
    calls = _stub_gh(monkeypatch, "")
    assert invitations.accept_pending() == []
    assert len(calls) == 1


def test_one_invitation_that_cannot_be_accepted_does_not_stop_the_rest(
    monkeypatch, capsys
):
    _stub_gh(monkeypatch, "gone\nhertie-new-e1234\n", fail=frozenset({"gone"}))
    assert invitations.accept_pending() == ["hertie-new-e1234"]
    assert "could not accept the invitation to gone" in capsys.readouterr().err


def test_a_listing_that_cannot_be_read_raises(monkeypatch):
    # "None pending" is an answer; a failed read must not pass for one.
    monkeypatch.setattr(
        invitations, "gh", lambda *a, **k: (1, "HTTP 401: Bad credentials")
    )
    with pytest.raises(RuntimeError, match="pending invitations"):
        invitations.accept_pending()
