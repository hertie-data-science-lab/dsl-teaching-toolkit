"""The bot accepts its own org invitations: every pending one, all pages, and only those."""

from __future__ import annotations

import pytest

from dsl_course import invitations

REGISTERED = frozenset({"hertie-new-e1234", "hertie-new-f2026", "gone"})


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(invitations.org_registry, "course_orgs", lambda: REGISTERED)
    monkeypatch.setattr(invitations, "bot_login", lambda: "hertie-dsl-bot")


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


def test_an_org_the_registry_does_not_name_is_left_pending(monkeypatch, capsys):
    # Anyone can invite the bot. Accepting would put it in an org the fan-out could then
    # hand the bot token to; declining would stop a later registration from working.
    calls = _stub_gh(monkeypatch, "Stranger-Org\nHertie-New-E1234\n")
    assert invitations.accept_pending() == ["Hertie-New-E1234"]
    assert [c[3] for c in calls[1:]] == ["user/memberships/orgs/Hertie-New-E1234"]
    out = capsys.readouterr().out
    assert "invitation from Stranger-Org left pending: not registered" in out


def test_a_registry_that_cannot_be_read_accepts_nothing(monkeypatch):
    def unreadable():
        raise RuntimeError("could not read the course org registry orgs.yml")

    monkeypatch.setattr(invitations.org_registry, "course_orgs", unreadable)
    calls = _stub_gh(monkeypatch, "hertie-new-e1234\n")
    with pytest.raises(RuntimeError, match="orgs.yml"):
        invitations.accept_pending()
    assert calls == []


def test_a_registered_courses_own_semester_is_accepted(monkeypatch):
    # The course's admins vouch for its semesters: the wizard lists the org in the
    # course's semesters.yml before the bot is invited.
    calls = _stub_gh(monkeypatch, "hertie-new-s2027\n")
    assert invitations.accept_pending(
        "Hertie-New-E1234", ["hertie-new-f2026", "Hertie-New-S2027"]
    ) == ["hertie-new-s2027"]
    assert len(calls) == 2


def test_an_org_named_like_a_semester_but_listed_nowhere_is_left_pending(
    monkeypatch, capsys
):
    calls = _stub_gh(monkeypatch, "hertie-new-s2027\n")
    assert invitations.accept_pending("hertie-new-e1234", ["hertie-new-f2026"]) == []
    assert len(calls) == 1
    assert "hertie-new-s2027 left pending: not registered" in capsys.readouterr().out


def test_an_unregistered_course_cannot_vouch_for_semesters(monkeypatch):
    # Its semesters.yml is anyone's to write, like the topic.
    calls = _stub_gh(monkeypatch, "stranger-s2027\n")
    assert invitations.accept_pending("stranger-e1", ["stranger-s2027"]) == []
    assert len(calls) == 1


def test_a_maintainers_break_glass_token_accepts_nothing(monkeypatch, capsys):
    # The scheduler can be run from a laptop with a maintainer's token; that must never
    # join orgs as the maintainer.
    monkeypatch.setattr(invitations, "bot_login", lambda: "h-maintainer")
    calls = _stub_gh(monkeypatch, "hertie-new-e1234\n")
    assert invitations.accept_pending() == []
    assert calls == []
    assert "this token is h-maintainer, not hertie-dsl-bot" in capsys.readouterr().out
