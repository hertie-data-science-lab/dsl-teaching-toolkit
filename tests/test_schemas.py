"""The exported JSON Schemas: committed copies match a fresh export, every enum is the
engine's own constant, and the contract examples validate."""

from __future__ import annotations

from pathlib import Path

import pytest

from dsl_course import course, grades, roster, schedule, schemas, teams
from dsl_course.ops.registry import REGISTRY
from dsl_course.ops.request import validate

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "console" / "schemas"


def test_the_committed_schemas_are_a_fresh_export(tmp_path):
    schemas.write(tmp_path)
    fresh = {p.name: p.read_text() for p in tmp_path.iterdir()}
    committed = {p.name: p.read_text() for p in COMMITTED.iterdir()}
    assert fresh == committed, "run: python -m dsl_course.schemas --out console/schemas"


def _enum(schema: dict, *path: str) -> list:
    node = schema
    for key in path:
        node = node[key]
    return node["enum"]


def test_file_schema_enums_are_the_engine_constants():
    spec = schemas.grading_config_schema()
    assert _enum(spec, "properties", "type") == list(course.ASSIGNMENT_TYPES)
    assert _enum(spec, "properties", "team_formation") == list(course.TEAM_FORMATIONS)
    assert _enum(spec, "properties", "submit_via") == list(course.SUBMIT_VIA)
    assert _enum(spec, "properties", "visibility") == list(course.VISIBILITIES)
    listed = spec["properties"]["formats"]["oneOf"][0]
    assert _enum(listed, "items") == list(course.FORMATS)
    assert set(spec["properties"]) == set(grades.SPEC_KEYS)

    sched = schemas.schedule_schema()
    top = sched["properties"]
    assert set(top) == set(schedule.KNOWN_TOP_LEVEL)
    release = top["releases"]["additionalProperties"]
    assert set(release["properties"]) == set(schedule.KNOWN_RELEASE)
    assert set(release["properties"]["kind"]["enum"]) == set(schedule.KNOWN_ROW_KINDS)
    assert set(release["properties"]["deploy"]["items"]["properties"]) == set(
        schedule.KNOWN_DEPLOY
    )
    assert set(top["assignments"]["additionalProperties"]["properties"]) == set(
        schedule.KNOWN_ASSIGNMENT
    )
    assert set(top["events"]["additionalProperties"]["properties"]) == set(
        schedule.KNOWN_EVENT
    )
    assert set(top["archive"]["properties"]) == set(schedule.KNOWN_ARCHIVE)

    assert schemas.students_schema()["columns"] == list(roster.FIELDS)
    assert schemas.teams_schema()["columns"] == list(teams.FIELDS)

    defaults = schemas.dsl_course_schema()["properties"]["assignment_defaults"][
        "properties"
    ]
    assert set(grades.COURSE_DEFAULT_KEYS) <= set(defaults)


def test_op_arg_enums_are_the_engine_constants():
    create = REGISTRY["assignment.create"].args_schema["properties"]
    assert create["submit_via"]["enum"] == list(course.SUBMIT_VIA)
    assert create["visibility"]["enum"] == list(course.VISIBILITIES)
    assert create["type"]["enum"] == list(course.ASSIGNMENT_TYPES)
    assert create["team_formation"]["enum"] == list(course.TEAM_FORMATIONS)
    materials = REGISTRY["materials.create"].args_schema["properties"]
    assert materials["public_dirs"]["enum"] == list(course.PUBLIC_DIRS)
    assert materials["public_types"]["enum"] == list(course.PUBLIC_TYPES)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_op_has_a_schema_and_a_doc(name):
    op = REGISTRY[name]
    assert op.args_schema["type"] == "object"
    assert (ROOT / op.doc).is_file()
    assert op.doc.startswith("docs/")
    listed = {o["name"] for o in schemas.ops_json()["ops"]}
    assert name in listed


# contracts.md section 2 and 3, as written there.
CONTRACT_OUTCOME = {
    "schema": "dsl.outcome/1",
    "op": "release.now",
    "run_id": 4821,
    "actor": "prof",
    "preview": False,
    "conclusion": "done",
    "summary": "Released Session 3: 7 files to materials; the site row is live.",
    "counts": {"files": 7, "repos": 1},
    "reasons": [
        {
            "code": "SOURCE_MISSING",
            "text": "Folder lectures/05_trees was not found in course-materials-f2026.",
            "fix": {
                "repo": "hertie-dsl-demo-f2026/semester-config",
                "path": "schedule.yml",
                "line": 41,
                "screen": "schedule",
                "entry": "s5",
            },
        }
    ],
    "people": [{"handle": "octocat", "text": "No repo: not joined yet."}],
    "started": "2026-09-23T09:00:03Z",
    "finished": "2026-09-23T09:01:10Z",
}

CONTRACT_STATUS = {
    "schema": "dsl.status/1",
    "inputs": {
        "schedule.yml": "a",
        "instructors.yml": "b",
        "students.csv": "c",
        "teams.csv": "d",
        "grading_sheets": "e",
        "course/dsl-course.yml": "f",
        "assignments.lock.yml": "g",
    },
    "course": {
        "org": "hertie-dsl-demo-course-e1234",
        "name": "Machine Learning",
        "code": "E1234",
        "stages": {
            "C1": "done",
            "C2": "done",
            "C3": "done",
            "C4": "done",
            "C5": "problem",
            "C6": "todo",
        },
        "ready": False,
        "materials": [{"repo": "course-materials-f2026", "state": "ready"}],
        "templates": [
            {"repo": "assignment-3-f2026", "slug": "assignment-3", "state": "problem"}
        ],
        "semesters": ["hertie-dsl-demo-f2026"],
    },
    "semester": {
        "org": "hertie-dsl-demo-f2026",
        "key": "f2026",
        "label": "Fall 2026",
        "timezone": "Europe/Berlin",
        "week": 3,
        "weeks": 15,
        "live": True,
        "stages": {
            "K1": "done",
            "K2": "done",
            "K3": "done",
            "K4": "problem",
            "K5": "problem",
            "K6": "done",
            "K7": "todo",
        },
        "archive_date": "2027-01-31",
    },
    "problems": [
        {
            "id": "schedule:s5:SOURCE_MISSING",
            "scope": "semester",
            "stage": "K4",
            "text": "Session 5 cites folder lectures/05_trees, which is not in course-materials-f2026.",
            "stops": "The release on Thu 8 Oct will be skipped.",
            "fix": {
                "repo": "hertie-dsl-demo-f2026/semester-config",
                "path": "schedule.yml",
                "line": 41,
                "screen": "schedule",
                "entry": "s5",
            },
        }
    ],
    "this_week": [
        {
            "when": "2026-09-24T10:00:00+02:00",
            "kind": "release",
            "ref": "s3",
            "title": "Session 3: Trees",
            "state": "planned",
        }
    ],
    "releases": [
        {
            "id": "s5",
            "when": "2026-10-08T10:00:00+02:00",
            "kind": "lecture",
            "title": "Trees and ensembles",
            "state": "will_be_skipped",
            "source": {"repo": "course-materials-f2026", "path": "lectures/05_trees"},
            "dest": {"repo": "materials", "path": "lectures/05_trees"},
            "show_on_site": True,
            "tbc": False,
        }
    ],
    "assignments": [
        {
            "slug": "assignment-2",
            "title": "Regression",
            "template": "assignment-2-f2026",
            "state": "open",
            "handout": "2026-09-15T10:00:00+02:00",
            "due": "2026-09-27T23:59:00+02:00",
            "grading_cutoff_datetime": "2026-10-07T23:59:00+02:00",
            "solution_shown": None,
            "units": 48,
            "submissions": 37,
            "teams": None,
            "marks": {"filled": 0, "total": 48},
            "returned": False,
            "problem": False,
        }
    ],
    "students": {"rows": 48, "codes_sent": 47, "joined": 41},
    "staff": {"instructors": 2, "tas": 1, "synced": True},
    "site": {
        "url": "https://hertie-dsl-demo-f2026.github.io",
        "last_update": "2026-09-21T06:02:00Z",
        "stale": True,
    },
    "operations": [
        {
            "run_id": 4821,
            "op": "release.now",
            "conclusion": "done",
            "summary": "Released Session 3.",
            "finished": "2026-09-23T09:01:10Z",
        }
    ],
}


def test_the_contract_examples_validate():
    assert validate(CONTRACT_OUTCOME, schemas.outcome_schema()) == []
    assert validate(CONTRACT_STATUS, schemas.status_schema()) == []


def test_the_validator_catches_what_the_schemas_forbid():
    bad = {**CONTRACT_OUTCOME, "conclusion": "maybe", "extra": 1}
    problems = validate(bad, schemas.outcome_schema())
    assert any("conclusion" in p for p in problems) and any(
        "extra" in p for p in problems
    )
