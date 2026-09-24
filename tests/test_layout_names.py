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
    records,
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
        discovery, "gh", lambda *a, **k: (0, f"{course.OLD_SEMESTER_TOPIC}\n")
    )
    assert bootstrap_course.refuses_unmigrated("sem-f2026") is True
    monkeypatch.setattr(
        discovery, "gh", lambda *a, **k: (0, f"{course.SEMESTER_TOPIC}\n")
    )
    assert bootstrap_course.refuses_unmigrated("sem-f2026") is False


# ------------------------------------------------------------------ .system/ (item 3)


def test_the_pointer_lives_in_the_config_repo_and_the_dispatchers_read_it_there():
    files = welcome.config_system_files("main")
    pointer = f"{CONFIG_REPO}/contents/{records.path('pointer')}"
    for path, body in files.items():
        if path.startswith(".github/workflows/dispatch-"):
            assert pointer in body.decode(), path
            assert "__POINTER__" not in body.decode(), path


def test_a_semester_is_found_through_the_pointer_in_its_config_repo(monkeypatch):
    reads = []

    def load(org, repo, path):
        reads.append((org, repo, path))
        return {"course": "Course-Org", "org": org}

    monkeypatch.setattr(discovery, "load_yaml_config", load)
    assert discovery.course_org_for_semester("Sem-f2026") == "Course-Org"
    assert reads == [("Sem-f2026", CONFIG_REPO, ".system/dsl-course.yml")]


def test_a_semester_archived_before_the_rename_is_never_written_into(monkeypatch):
    # No semester-config under its new name, and the old topic: frozen or unmigrated.
    monkeypatch.setattr(discovery, "repo_missing", lambda org, name: True)
    monkeypatch.setattr(
        discovery, "gh", lambda *a, **k: (0, f"{course.OLD_SEMESTER_TOPIC}\n")
    )
    assert discovery.semester_is_live("Old-f2025") is False
    monkeypatch.setattr(
        discovery, "gh", lambda *a, **k: (0, f"{course.SEMESTER_TOPIC}\n")
    )
    assert discovery.semester_is_live("New-f2026") is True


def test_the_refresh_skips_a_frozen_pre_rename_semester_without_a_fault(
    monkeypatch, capsys
):
    from test_bootstrap_seeding import _stub_refresh

    from dsl_course import seed

    _stub_refresh(monkeypatch)
    frozen = [
        {"name": ".github", "topics": [course.OLD_SEMESTER_TOPIC], "archived": True},
        {"name": OLD_CONFIG_REPO, "topics": [], "archived": True},
    ]
    monkeypatch.setattr(
        seed, "list_org_repos", lambda org: frozen if org == "Semester-f2026" else []
    )
    assert seed.refresh("Course-Org") == 0
    out = capsys.readouterr()
    assert "Semester-f2026 (archived semester - left frozen)" in out.out
    assert "NOT_MIGRATED" not in out.out + out.err


def test_the_record_paths_all_sit_under_one_folder():
    for kind in records.RECORDS:
        assert records.path(kind).startswith(f"{records.SYSTEM_DIR}/"), kind
    assert records.path("autograde", "a1", "_graded.json") == (
        ".system/autograde/a1/_graded.json"
    )
