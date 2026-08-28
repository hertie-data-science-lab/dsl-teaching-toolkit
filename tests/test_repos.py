"""Repositories: whether one exists, whether it is archived, and what a 422 from a
create actually means."""

from __future__ import annotations

from dsl_course import repos


def test_repo_is_archived_reads_the_flag_and_assumes_live_when_it_cannot(monkeypatch):
    # This gates whether the nightly refresh skips a cohort, so the failure default is the
    # whole point: an unreadable repo must read as LIVE. Guessing "archived" on a transient
    # error would silently stop converging a running cohort with nothing in the log to say
    # so; guessing "live" costs a loud 403 from the write itself, which is the right alarm.
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, "true\n"))
    assert repos.repo_is_archived("Cohort-f2025", "classroom-config") is True
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, "false\n"))
    assert repos.repo_is_archived("Cohort-f2026", "classroom-config") is False
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "gh: HTTP 502 - bad gateway"))
    assert repos.repo_is_archived("Cohort-f2026", "classroom-config") is False


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
