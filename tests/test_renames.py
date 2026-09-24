"""Decision 0012's renames, in flight: every new spelling is the one written, and every
old one is still READ for one release (with a note naming the new one where a file carries
it). One section per rename, in the order of `maintainers.md`'s "Renames in flight"."""

from __future__ import annotations

import json
import sys

import pytest

from dsl_course import bootstrap_course, discovery, list_orgs, schedule, status
from dsl_course.ops.request import RequestError, parse_request

# ------------------------------------------------------------------ semester (cohort)


def _gh(topic: str) -> dict:
    return {"name": ".github", "topics": [topic]}


@pytest.mark.parametrize("topic", ["dsl-semester", "dsl-cohort"])
def test_both_semester_topics_place_an_org_as_a_semester(topic):
    assert discovery.org_tier([_gh(topic)]) == "semester"


def test_the_semester_inventory_searches_the_old_topic_too(monkeypatch):
    searched: list[str] = []
    monkeypatch.setattr(
        list_orgs,
        "_tagged_orgs",
        lambda topic: (
            searched.append(topic) or {"dsl-cohort": ["Old-Sem"]}.get(topic, [])
        ),
    )
    monkeypatch.setattr(list_orgs, "_metadata_or_none", lambda org: {"course": "C"})
    assert [s["org"] for s in list_orgs.discover_semester_orgs()] == ["Old-Sem"]
    assert searched == ["dsl-semester", "dsl-cohort"]


def _registry(monkeypatch, files: dict[str, str]) -> list[tuple[str, bytes]]:
    written: list[tuple[str, bytes]] = []
    monkeypatch.setattr(
        discovery, "get_file_content", lambda org, repo, path: files.get(path)
    )
    monkeypatch.setattr(
        discovery,
        "put_file",
        lambda org, repo, path, body, msg: written.append((path, body)) or True,
    )
    return written


def test_the_old_registry_file_and_key_are_read_while_the_new_file_is_absent(
    monkeypatch,
):
    _registry(monkeypatch, {"cohort-courses-pages.yml": "cohorts:\n- Sem-f2026\n"})
    assert discovery.discover_semesters("Course") == ["Sem-f2026"]


def test_the_new_registry_file_wins_over_the_old_one(monkeypatch):
    _registry(
        monkeypatch,
        {
            "semesters.yml": "semesters:\n- New-f2026\n",
            "cohort-courses-pages.yml": "cohorts:\n- Old-f2025\n",
        },
    )
    assert discovery.discover_semesters("Course") == ["New-f2026"]


def test_registering_writes_the_new_registry_file_only(monkeypatch):
    written = _registry(monkeypatch, {"cohort-courses-pages.yml": "cohorts: [A]\n"})
    assert discovery.register_semester("Course", "B")
    assert [(p, b.decode()) for p, b in written] == [
        ("semesters.yml", "semesters:\n- A\n- B\n")
    ]


def test_the_refresh_bridge_copies_the_old_registry_once(monkeypatch):
    written = _registry(monkeypatch, {"cohort-courses-pages.yml": "cohorts: [A]\n"})
    assert discovery.migrate_semester_registry("Course")
    assert [p for p, _ in written] == ["semesters.yml"]
    written = _registry(monkeypatch, {"semesters.yml": "semesters: [A]\n"})
    assert discovery.migrate_semester_registry("Course")
    assert written == []


@pytest.mark.parametrize("key", ["semester_dest_repo", "cohort_dest_repo"])
def test_an_assignment_dest_repo_is_read_under_either_name(key):
    sched = schedule.parse(
        {
            "assignments": {
                "hw": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    key: "homework-1",
                }
            }
        }
    )
    assert sched.assignments["hw"].semester_dest_repo == "homework-1"
    moved = [d for d in sched.dropped if "cohort_dest_repo" in d]
    assert bool(moved) == (key == "cohort_dest_repo")
    assert all("renamed to `semester_dest_repo:`" in d for d in moved)


def test_a_deploy_dest_is_read_under_its_old_names_with_a_note():
    sched = schedule.parse(
        {
            "releases": {
                "lecture-1": {
                    "event_datetime": "2026-09-08T09:00",
                    "deploy": [
                        {
                            "course_source_repo": "course-materials-f2026",
                            "course_source_path": "lectures/01",
                            "cohort_dest_repo": "slides",
                            "cohort_dest_path": "week-1",
                        }
                    ],
                }
            }
        }
    )
    (deploy,) = sched.releases[0].deploy
    assert (deploy.semester_dest_repo, deploy.semester_dest_path) == (
        "slides",
        "week-1",
    )
    assert sum("renamed to `semester_dest_" in d for d in sched.dropped) == 2


def test_an_entry_setting_both_dest_names_keeps_the_new_one():
    sched = schedule.parse(
        {
            "assignments": {
                "hw": {
                    "course_source_repo": "a-f2026",
                    "due_datetime": "2026-10-13",
                    "semester_dest_repo": "new",
                    "cohort_dest_repo": "old",
                }
            }
        }
    )
    assert sched.assignments["hw"].semester_dest_repo == "new"
    assert any("which is also set - ignored" in d for d in sched.dropped)


def _request(**over) -> str:
    raw = {
        "schema": "dsl.request/1",
        "op": "release.now",
        "actor": "prof",
        "course_org": "Course",
        "args": {"entry": "s5"},
        "preview": True,
    }
    raw.update(over)
    return json.dumps(raw)


def test_a_console_request_naming_the_old_cohort_org_is_read():
    assert parse_request(_request(cohort_org="Sem-f2026")).semester_org == "Sem-f2026"
    assert parse_request(_request(semester_org="Sem-f2026")).semester_org == "Sem-f2026"


def test_a_request_naming_both_semester_spellings_is_refused():
    with pytest.raises(RequestError):
        parse_request(_request(cohort_org="A", semester_org="B"))


def test_old_request_arg_names_reach_the_op_under_the_new_ones():
    req = parse_request(
        _request(semester_org="Sem", args={"entry": "s5", "cohort_dest_repo": "slides"})
    )
    assert req.args == {"entry": "s5", "semester_dest_repo": "slides"}
    req = parse_request(
        _request(op="materials.create", args={"tag": "f2026"}, preview=False)
    )
    assert req.args == {"semester": "f2026"}


@pytest.mark.parametrize("flag", ["--semester-org", "--cohort-org"])
def test_a_cli_takes_the_semester_org_under_either_flag(monkeypatch, flag):
    seen: list[tuple] = []
    monkeypatch.setattr(status, "refresh", lambda *a: seen.append(a) or 0)
    monkeypatch.setattr(
        sys, "argv", ["status", "--course-org", "C", flag, "Sem", "--write"]
    )
    assert status.main() == 0
    assert seen == [("C", "Sem")]


def test_bootstrap_reads_the_old_cohort_defaults_block(monkeypatch):
    monkeypatch.setattr(
        bootstrap_course,
        "org_meta",
        lambda org: {"cohort_defaults": {"timezone": "Europe/London"}},
    )
    assert bootstrap_course.course_semester_defaults("C")["timezone"] == "Europe/London"
