"""The 0.11.0 schedule shape (decision 0038) and its migration, proved on the three live
semesters: the demo, NLP and Maths `schedule.yml` as they stood on 14 Oct 2026 (comments
removed), migrated by `migrate.schedule_shape` and rendered by the site sync, give the rows
the 0.10 engine rendered (frozen in `<name>-rows-0.10.json`) - but for what 0038 and its
rulings change: no stub exams, the readings chip, a READINGS.md listed as a file (its
text is the entry's `reading_list:` now), a lab's second group a slot of its lab (with
its files), and every row file named by its key."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from dsl_course import migrate, schedule
from dsl_course.gh_contents import load_yaml_lines
from tests import test_site
from tests.test_site import _plan

FIX = Path(__file__).parent / "fixtures" / "schedule-shape"
NAMES = ("demo", "nlp", "maths")


def _overlay(path: str) -> str:
    """What every READINGS.md in these fixtures says (the goldens were rendered so)."""
    return f"## Readings\n\nThe list in `{path}`.\n"


def _tree(name: str) -> tuple[str, ...]:
    return tuple(p for p in (FIX / f"{name}-tree.txt").read_text().split("\n") if p)


def _migrated(name: str) -> tuple[str, list[str], list[str]]:
    tree = set(_tree(name))

    def reading_list(repo: str, path: str) -> str:
        overlay = f"{path.strip('/')}/READINGS.md"
        return _overlay(overlay) if overlay in tree else ""

    text, _instance, notes, stops = migrate.schedule_shape(
        (FIX / f"{name}-schedule-0.10.yml").read_text(), None, reading_list=reading_list
    )
    return text, notes, stops


def _render(monkeypatch, tmp_path, sched) -> dict:
    monkeypatch.setattr(
        test_site.site, "load_grading_spec", lambda *a, **k: test_site._spec("")
    )
    monkeypatch.setattr(test_site.site, "read_opencourse", lambda org: None)
    monkeypatch.setattr(test_site.site_repo, "acting_login", lambda: None)
    plan = _plan(monkeypatch, tmp_path, sched, trees={"materials": _TREE})
    return {
        "lectures": plan.collections["_lectures"],
        "events": plan.collections["_events"],
        "assignments": sorted(plan.collections["_assignments"]),
    }


_TREE: tuple[str, ...] = ()


def _front(text: str) -> dict:
    return yaml.safe_load(text.split("---\n")[1])


_READINGS_LINK = re.compile(
    r'    - url: [^\n]*/READINGS\.md\n      name: "READINGS\.md"\n      section: "[a-z]+"\n'
)


def _decided(text: str) -> str:
    """A 0.11 row less what decision 0038 and its rulings add: the readings chip, and a
    READINGS.md listed as a file (its text is the entry's `reading_list:` now)."""
    text = text.replace("readings_chip: true\n", "")
    text = _READINGS_LINK.sub("", text)
    return text.replace("links:\n---", "links: []\n---")


def _no_files(text: str) -> str:
    """A row less what says whether its files are out: how a 0.10 second lab group (a
    session with nothing to ship) and its 0.11 slot (the lab's own files) are compared."""
    text = re.sub(r"links:\n(    [^\n]*\n|      [^\n]*\n)*", "", text)
    text = re.sub(r"(no_materials|unreleased): true\n|links: \[\]\n", "", text)
    return text.split("---\n")[1]


@pytest.mark.parametrize("name", NAMES)
def test_a_live_semester_migrates_and_renders_the_rows_it_did(
    name, monkeypatch, tmp_path
):
    global _TREE
    text, notes, stops = _migrated(name)
    assert (notes, stops) == ([], [])
    sched = schedule.parse(load_yaml_lines(text))
    assert sched.dropped == [] and sched.faults == []
    _TREE = _tree(name)
    new = _render(monkeypatch, tmp_path, sched)
    old = json.loads((FIX / f"{name}-rows-0.10.json").read_text())

    # Every row file is named by its entry's key now; the rows themselves are the same.
    assert len(new["lectures"]) == len(old["lectures"])
    # 0.10 named a second group's row after its lab's (`lab-01-lab-01-group-2.md`).
    groups = {
        f: t
        for f, t in old["lectures"].items()
        if (m := re.fullmatch(r"([a-z]+-\d+)-.+\.md", f))
        and f"{m.group(1)}.md" in old["lectures"]
    }
    slots = {
        f: t
        for f, t in new["lectures"].items()
        if (m := re.fullmatch(r"(.+)-\d+\.md", f))
        and f"{m.group(1)}.md" in new["lectures"]
    }
    # A lab's second group: a slot of its lab - the same row, now with the lab's files.
    assert sorted(_no_files(t) for t in groups.values()) == sorted(
        _no_files(_decided(t)) for t in slots.values()
    )
    rest_old = sorted(t for f, t in old["lectures"].items() if f not in groups)
    rest_new = sorted(_decided(t) for f, t in new["lectures"].items() if f not in slots)
    assert rest_new == rest_old

    # No exam made up for a semester that names none (9b ruling 3); the rest as before.
    stubs = {"midterm.md", "final.md"} if name == "nlp" else set()
    assert set(old["events"]) - set(new["events"]) == stubs
    assert {f: t for f, t in old["events"].items() if f not in stubs} == new["events"]
    assert new["assignments"] == old["assignments"]


def test_the_migration_writes_session_number_only_where_it_differs_and_keeps_keys():
    text, _notes, _stops = _migrated("demo")
    data = yaml.safe_load(text)
    assert "releases" not in data
    # `events:` is the block of the `other` kind now: the special events alone.
    assert set(data["events"]) == {"guest-lecture", "reading-week"}
    assert data["lectures"]["lecture-01"]["session"] == 1
    assert "number" not in data["lectures"]["lecture-01"]
    # Readings the site joined to lecture 1 name its unit.
    assert data["readings"]["readings-01"]["session"] == 1
    # A drop-in numbered 1 is in no unit: its number stays its own.
    assert data["drop-ins"]["lecture-12"] == {
        "number": 1,
        "title": "Tutorial presentations",
        "details": "Students present their tutorial topic to the group.",
        "event_datetime": "2026-10-20T10:00",
    }
    # Supporting files (kind inferred once, today's way).
    assert set(data["assets"]) == {"course-intro"}
    copy = data["lectures"]["lecture-09"]["files"][0]
    assert copy == {
        "from_course_repo": "lecture-code-f2026",
        "from_path": "dldemo/uncertainty.py",
        "to_path": "code/dldemo/uncertainty.py",
    }
    # Assignments keep their keys; a key that is not `assignment-<n>` keeps its repos.
    project = data["assignments"]["assignment-3-project"]
    assert (project["number"], project["repo_name"]) == (8, "assignment-3-project")
    assert "repo_name" not in data["assignments"]["assignment-1"]
    assert data["assignments"]["assignment-1"]["template"] == "assignment-1-f2026"


def test_a_second_lab_group_becomes_a_slot_and_keeps_the_labs_ship_time():
    text, _notes, _stops = _migrated("nlp")
    lab = yaml.safe_load(text)["labs"]["lab-01"]
    assert lab["event_datetime"] == [
        "2026-09-02T08:00",
        {
            "at": "2026-09-03T18:00",
            "details": "Lab group 2 - Thursday 18:00-20:00, room 2.32",
        },
    ]
    assert lab["details"] == "Lab group 1 - Wednesday 08:00-10:00, room 2.30"
    assert lab["files"][0]["release_datetime"] == "2026-09-01T20:00"


def test_the_assignments_yml_blocks_move_onto_their_entries():
    old = (FIX / "demo-schedule-0.10.yml").read_text()
    instance = (
        "defaults:\n  visibility: private\n\nassignments:\n"
        "  assignment-4:\n    visibility: public\n    late_window_days: 1\n"
        "    late_penalty_per_day: 10%\n"
    )
    text, after, _notes, stops = migrate.schedule_shape(old, instance)
    assert stops == []
    entry = yaml.safe_load(text)["assignments"]["assignment-4"]
    assert entry["assignment_repo_visibility"] == "public"
    assert (entry["late_window_days"], entry["late_penalty_per_day"]) == (1, "10%")
    assert after == "defaults:\n  visibility: private\n"
    sched = schedule.parse(load_yaml_lines(text))
    assert sched.assignments["assignment-4"].settings["visibility"] == "public"
