"""Decision 0010: the semester's repos and files have ONE spelling each, declared once.

The engine knows only the new names (no dual reading); the old ones live in `course.py`
(the retired list) and `migrate.py` (the tool that renames a live org) and nowhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dsl_course import (
    access,
    bootstrap_course,
    course,
    discovery,
    profile_readme,
    repos,
    teardown,
    welcome,
)
from dsl_course.course import CONFIG_REPO, JOIN_REPO, OLD_CONFIG_REPO, OLD_JOIN_REPO

ROOT = Path(__file__).resolve().parents[1]
# The two files allowed to spell a retired name: the declaration and the migration.
SPELLS_OLD_NAMES = {"dsl_course/course.py", "dsl_course/migrate.py"}


def _shipped_files():
    for top in ("dsl_course", "templates"):
        for path in sorted((ROOT / top).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".yml", ".md", ".js", ".html"}:
                yield (
                    path.relative_to(ROOT).as_posix(),
                    path.read_text(encoding="utf-8"),
                )


# `welcome` is also the name of a module, so only its spellings AS A REPO count: quoted,
# back-ticked, or a path segment.
RETIRED_SPELLINGS = [
    rf"\b{OLD_CONFIG_REPO}\b",
    rf"[\"'`/>]{OLD_JOIN_REPO}[\"'`/<]",
]


@pytest.mark.parametrize("pattern", RETIRED_SPELLINGS)
def test_no_shipped_file_spells_a_retired_repo_name(pattern):
    found = [
        rel
        for rel, text in _shipped_files()
        if rel not in SPELLS_OLD_NAMES and re.search(pattern, text)
    ]
    assert found == []


def test_the_listing_sites_match_the_config_repo_by_its_constant():
    # Every name match on an org listing: an org listing returns ONLY the new name after a
    # rename, so a literal left behind here would silently stop matching.
    for name in (CONFIG_REPO, JOIN_REPO):
        assert name in discovery.INFRA_REPOS
        assert name in discovery.SEMESTER_ONLY_REPOS
        assert name in access.SEMESTER_WRITE_REPOS
        assert name in profile_readme._SEMESTER_ROWS
    assert discovery.join_issue_url("sem") == (
        f"https://github.com/sem/{JOIN_REPO}/issues/new/choose"
    )
    rows = [{"name": n, "topics": []} for n in (CONFIG_REPO, ".github", "x-repo")]
    assert CONFIG_REPO not in [r["name"] for r in teardown.freeze_order("o", rows)]


def test_the_shipped_scripts_carry_the_config_repo_substituted():
    for rel in ("join/onboard.yml", "join/team-formation.yml"):
        text = welcome.join_workflow(rel)
        assert f"const CONFIG = '{CONFIG_REPO}'" in text
        assert "__CONFIG_REPO__" not in text
    files = welcome.config_system_files("main")
    dispatcher = files[".github/workflows/dispatch-scheduled-release.yml"].decode()
    assert f"client_payload[driver]={CONFIG_REPO}" in dispatcher
    assert not any(b"__CONFIG_REPO__" in body for body in files.values())


@pytest.mark.parametrize("name", sorted(course.RETIRED_REPO_NAMES))
def test_a_retired_name_is_never_created(name, monkeypatch):
    calls = []
    monkeypatch.setattr(repos, "gh", lambda *a, **k: calls.append(a) or (0, ""))
    assert repos.create_repo("org", name) is False
    assert calls == []


def test_bootstrap_refuses_a_semester_the_migration_has_not_reached(monkeypatch):
    monkeypatch.setattr(
        bootstrap_course, "gh", lambda *a, **k: (0, f"{course.OLD_SEMESTER_TOPIC}\n")
    )
    assert bootstrap_course.refuses_unmigrated("sem-f2026") is True
    monkeypatch.setattr(
        bootstrap_course, "gh", lambda *a, **k: (0, f"{course.SEMESTER_TOPIC}\n")
    )
    assert bootstrap_course.refuses_unmigrated("sem-f2026") is False
