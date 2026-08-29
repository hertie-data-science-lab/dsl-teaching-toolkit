"""Repositories: whether one exists, whether it is archived, and what a 422 from a
create actually means."""

from __future__ import annotations

import pytest

from dsl_course import repos


def test_repo_is_archived_reads_the_flag_and_assumes_live_when_it_cannot(monkeypatch):
    # This gates whether the nightly refresh skips a cohort, so the failure default is the
    # whole point: an unreadable repo must read as LIVE. Guessing "archived" on a transient
    # error would silently stop converging a running cohort with nothing in the log to say
    # so; guessing "live" costs a loud 403 from the write itself, which is the right alarm.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, '{"archived": true}'))
    assert repos.repo_is_archived("Cohort-f2025", "classroom-config") is True
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, '{"archived": false}'))
    assert repos.repo_is_archived("Cohort-f2026", "classroom-config") is False
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 - bad gateway"))
    assert repos.repo_is_archived("Cohort-f2026", "classroom-config") is False


def test_one_repo_read_answers_every_question_about_it(monkeypatch):
    # Four questions about ONE object; a sweep asks several of them about the same repo,
    # and each used to be its own `GET repos/{org}/{name}`.
    reads = []
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (
            reads.append(a)
            or (0, '{"default_branch": "trunk", "private": false, "archived": true}')
        ),
    )
    assert repos.repo_exists("Org", "r")
    assert repos.repo_is_archived("Org", "r") is True
    assert repos.repo_is_private("Org", "r") is False
    assert repos.default_branch("Org", "r") == "trunk"
    assert len(reads) == 1


def test_a_failed_repo_read_is_retried_not_pinned_for_the_run(monkeypatch):
    # functools.cache does not memoise a raise, which is what keeps a 502 on one question
    # from answering every later question about that repo for the rest of the process.
    answers = [(1, "gh: HTTP 502 bad gateway"), (0, '{"default_branch": "main"}')]
    monkeypatch.setattr(repos, "gh", lambda *a, **k: answers.pop(0))
    assert repos.default_branch("Org", "r", fallback="main") == "main"
    assert repos.default_branch("Org", "r") == "main"
    assert answers == []


def test_default_branch_raises_for_a_writer_and_falls_back_for_a_reader(monkeypatch):
    # A writer would otherwise aim a commit at a branch that may not exist; a reader that
    # would just find nothing asks for the guess explicitly.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 bad gateway"))
    assert repos.default_branch("Org", "r", fallback="main") == "main"
    with pytest.raises(RuntimeError, match="default branch"):
        repos.default_branch("Org", "r")


def test_create_repo_only_treats_a_genuine_name_clash_422_as_success(monkeypatch):
    # A bare `"422" in out` swallowed an invalid-name/policy 422 as success, so the caller
    # then wrote into a repo that was never created. Only the name-clash message is success.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    assert repos.create_repo("Org", "good") is True
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (
            1,
            "HTTP 422: Validation Failed - name already exists on this account",
        ),
    )
    assert repos.create_repo("Org", "dup") is True
    monkeypatch.setattr(
        repos,
        "gh",
        lambda *a, **k: (1, "HTTP 422: Validation Failed - name is invalid"),
    )
    assert repos.create_repo("Org", "bad name") is False


def test_repo_missing_is_true_only_on_a_404(monkeypatch):
    # `repo_exists` is optimistic (any failure = absent) because it answers a create-if-
    # missing question. `repo_missing` answers "may I record something permanent on the
    # strength of absence?" - so a 5xx or a rate limit is neither present nor absent.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.repo_missing("O", "r")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "HTTP 502 bad gateway"))
    assert not repos.repo_missing("O", "r")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, "{}"))
    assert not repos.repo_missing("O", "r")


def test_is_collaborator_asks_for_direct_grants_only(monkeypatch):
    # `GET /collaborators/{login}` 204s for anyone who can reach the repo AT ALL - through
    # a team, or by being an org owner - so it answers "has access", not "is a direct
    # collaborator". Its one caller revokes on the answer, and only a direct grant is
    # revocable: reading team access as a direct grant reported revokes that removed
    # nothing, on repos named after a handle nobody had ever been granted directly.
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        repos, "gh", lambda *a, **k: seen.append(a) or (0, "Ada-L\nhertie-dsl-bot\n")
    )
    assert repos.is_collaborator("Cohort", "assignment-1-ada-l", "ada-l") is True
    assert any("affiliation=direct" in a for a in seen[0])
    assert repos.is_collaborator("Cohort", "assignment-1-ada-l", "zoe-z") is False

    # An unreadable answer is neither - the caller must not revoke on a rate limit.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 bad gateway"))
    assert repos.is_collaborator("Cohort", "assignment-1-ada-l", "ada-l") is None
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.is_collaborator("Cohort", "gone", "ada-l") is False


def test_pending_invitations_picks_out_one_logins_ids(monkeypatch):
    # GitHub logins are case-insensitive, and the listing carries everyone's invitations -
    # cancelling the wrong one revokes a colleague's access to a repo they still need.
    listing = "12\tZoe-Z\n13\tada-l\n14\tzoe-z\n"
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, listing))
    assert repos.pending_invitations("O", "r", "zoe-z") == ["12", "14"]
    assert repos.pending_invitations("O", "r", "nobody") == []


def test_an_unreadable_invitation_listing_is_none_not_empty(monkeypatch):
    # The caller is about to revoke: a rate limit must never read as "nothing to cancel".
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "HTTP 403: rate limited"))
    assert repos.pending_invitations("O", "r", "zoe-z") is None
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: Not Found (HTTP 404)"))
    assert repos.pending_invitations("O", "r", "zoe-z") == []
