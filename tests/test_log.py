"""The verbose channel: a line naming a student may only appear when a human asks for
it, because every faculty workflow logs to a world-readable Actions log."""

from __future__ import annotations

from dsl_course import log

# ------------------------------- per-person lines stay out of a world-readable log


def test_log_person_is_silent_unless_dsl_verbose_is_set(capsys, monkeypatch):
    # Every faculty workflow runs in the course org's PUBLIC .github, so a line naming a
    # student may only appear when a human asks for it on their own machine.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    log.log_person("@ada-l")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("DSL_VERBOSE", "1")
    log.log_person("@ada-l")
    assert "@ada-l" in capsys.readouterr().out


def test_an_empty_dsl_verbose_is_not_verbose(capsys, monkeypatch):
    monkeypatch.setenv("DSL_VERBOSE", "")
    log.log_person("@ada-l")
    assert capsys.readouterr().out == ""
