"""The exported JSON Schemas: committed copies match a fresh export, every enum is the
engine's own constant, and the contract examples validate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dsl_course import (
    course,
    gradebook,
    grading_spec,
    policy,
    roster,
    schedule,
    schemas,
    settings,
    team_formation,
)
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
    assert _enum(spec, "properties", "submit_via") == list(course.SUBMIT_VIA)
    listed = spec["properties"]["formats"]["oneOf"][0]
    assert _enum(listed, "items") == list(course.FORMATS)
    # The template's keys: never a run setting, which is the semester's.
    assert set(spec["properties"]) == set(grading_spec.TEMPLATE_KEYS)
    # Decision 0028: an enum with no default - a template without it reads by its markers.
    assert _enum(spec, "properties", "starter") == list(course.STARTER_MODES)
    assert "default" not in spec["properties"]["starter"]
    assert (
        grading_spec.parse_grading_spec("starter: handwritten\n").starter
        == "handwritten"
    )
    assert grading_spec.parse_grading_spec("formats: [py]\n").starter is None
    bad = grading_spec.parse_grading_spec("starter: maybe\n")
    assert bad.starter is None and any("starter" in d for d in bad.dropped)
    assert not set(spec["properties"]) & set(settings.RUN_KEYS)
    # Decision 0039: one fixed `defaults:` key set at every level.
    semester = schemas.semester_schema()["properties"]
    assert set(semester) == set(settings.SEMESTER_BLOCKS)
    defaults = semester["defaults"]
    assert set(defaults["properties"]) == set(settings.DEFAULT_FILE_KEYS)
    assert _enum(defaults, "properties", "team_formation") == list(
        course.TEAM_FORMATIONS
    )
    assert _enum(defaults, "properties", "assignment_repo_visibility") == list(
        course.VISIBILITIES
    )
    institution = schemas.institution_schema()["properties"]["defaults"]
    assert list(institution["required"]) == list(policy.DEFAULT_KEYS)

    sched = schemas.schedule_schema()
    top = sched["properties"]
    assert set(top) == set(schedule.KNOWN_TOP_LEVEL)
    # Decision 0038: one block per content kind, each a map of entries.
    for block, kind in schedule.BLOCKS.items():
        entry = top[block]["additionalProperties"]
        known = schedule.KNOWN_READINGS if kind == "readings" else schedule.KNOWN_ENTRY
        assert set(entry["properties"]) == set(known), block
        files = entry["properties"]["files"]["items"]["properties"]
        assert set(files) == set(schedule.KNOWN_FILE)
    assert set(top["assignments"]["additionalProperties"]["properties"]) == set(
        schedule.KNOWN_ASSIGNMENT
    )
    # Decision 0038 amendment 2: the term's dates, one `semester:` block of typed entries.
    semester = top[schedule.SEMESTER_BLOCK]["additionalProperties"]
    assert set(semester["properties"]) == set(
        schedule.KNOWN_ARCHIVE | schedule.KNOWN_BREAK | schedule.KNOWN_SEMESTER_DATE
    )
    assert semester["properties"]["type"]["enum"] == list(schedule.SEMESTER_TYPES)

    assert schemas.students_schema()["columns"] == list(roster.FIELDS)
    assert schemas.roster_schema()["columns"] == list(roster.SYSTEM_FIELDS)

    defaults = schemas.course_schema()["properties"]["defaults"]["properties"]
    assert set(defaults) == {*settings.DEFAULT_FILE_KEYS, *settings.COURSE_ASKED_KEYS}


def test_op_arg_enums_are_the_engine_constants():
    create = REGISTRY["assignment.create"].args_schema["properties"]
    assert create["submit_via"]["enum"] == list(course.SUBMIT_VIA)
    assert create["type"]["enum"] == list(course.ASSIGNMENT_TYPES)
    # New assignment asks nothing a semester decides.
    assert not set(create) & set(settings.RUN_KEYS)
    # publish.yml is a hand-written opt-in: New materials repo asks nothing about it.
    materials = REGISTRY["materials.create"].args_schema["properties"]
    assert set(materials) == {"semester", "copy_from"}
    assert REGISTRY["course.publish_website"].args_schema["properties"] == {}


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
    "op": "release.entry",
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
    "started": "2026-09-23T09:00:03Z",
    "finished": "2026-09-23T09:01:10Z",
}

CONTRACT_STATUS = {
    "schema": "dsl.status/1",
    "inputs": {
        "schedule.yml": "a",
        "assignments.yml": "a2",
        "semester.yml": "b",
        "students.csv": "c",
        "teams.csv": "d",
        "grading_sheets": "e",
        "course/course.yml": "f",
        ".system/assignments.lock.yml": "g",
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
        "stage_need": {
            **dict.fromkeys(("C1", "C2", "C3"), "needed"),
            **dict.fromkeys(("C4", "C5", "C6"), "suggested"),
        },
        "materials": [{"repo": "course-materials-f2026", "state": "ready"}],
        "templates": [{"repo": "assignment-3-f2026", "state": "problem"}],
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
        },
        "stage_need": dict.fromkeys(("K1", "K2", "K3", "K4", "K5", "K6"), "needed"),
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
            "bites": "soon",
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
            "op": "release.entry",
            "conclusion": "done",
            "summary": "Released Session 3.",
            "finished": "2026-09-23T09:01:10Z",
        }
    ],
}


def test_the_contract_examples_validate():
    assert validate(CONTRACT_OUTCOME, schemas.outcome_schema()) == []
    assert validate(CONTRACT_STATUS, schemas.status_schema()) == []


def test_the_console_reads_the_release_engines_status():
    # One console, built from main, serves every org; a release-tier org's files were
    # written by the last-promoted engine. They must validate against the console's
    # copy, or a merge breaks every real org until the next Promote (maintainers.md,
    # "The status contract"). Refresh the fixture at each Promote, never to pass this.
    console = json.loads((COMMITTED / "status.schema.json").read_text())
    files = sorted((ROOT / "tests" / "fixtures" / "status-release").glob("*.json"))
    assert [f.name for f in files] == ["course.json", "semester.json"]
    for f in files:
        assert validate(json.loads(f.read_text()), console) == [], f.name


def test_the_console_copy_of_the_status_schema_is_open_and_the_engines_closed():
    # An older or newer key is ignored on read; the engine's own render stays exact.
    doc = {**CONTRACT_STATUS, "added_later": 1}
    doc["course"] = {**doc["course"], "added_later": 1}
    console = json.loads((COMMITTED / "status.schema.json").read_text())
    assert validate(doc, console) == []
    assert len(validate(doc, schemas.status_schema())) == 2


def test_the_example_files_validate():
    # The worked examples have every block their files have: dated course-lead cards,
    # a semester's people with their roles, and the defaults.
    course_org = ROOT / "example-course" / "course-org" / "course.yml"
    meta = yaml.safe_load(course_org.read_text())
    assert "start" in meta["people"]["course_admins"][0]
    assert validate(meta, schemas.course_schema()) == []
    semester_org = ROOT / "example-course" / "semester-org" / "semester.yml"
    assert (
        validate(yaml.safe_load(semester_org.read_text()), schemas.semester_schema())
        == []
    )
    assert validate(policy.load(), schemas.institution_schema()) == []


def test_contact_and_licence_are_optional_course_facts():
    # Forward-only course metadata: a file without them is valid, one with them too.
    schema = schemas.course_schema()
    base = {"course": {"name": "ML", "code": "E1"}}
    assert validate(base, schema) == []
    assert (
        validate(
            {
                "course": {**base["course"], "licence": "CC BY 4.0"},
                "open_course_site": {"contact": "ml@x.edu"},
            },
            schema,
        )
        == []
    )
    assert validate({**base, "open_course_site": {"contact": "nope"}}, schema) != []
    # The old flat keys are not course.yml's.
    assert validate({"course_name": "ML"}, schema) != []


def test_student_console_is_an_optional_course_flag():
    schema = schemas.course_schema()
    assert validate({"course": {"student_console": True}}, schema) == []
    assert validate({"course": {"student_console": "yes"}}, schema) != []


def test_the_validator_catches_what_the_schemas_forbid():
    bad = {**CONTRACT_OUTCOME, "conclusion": "maybe", "extra": 1}
    problems = validate(bad, schemas.outcome_schema())
    assert any("conclusion" in p for p in problems) and any(
        "extra" in p for p in problems
    )


def test_names_json_is_the_engines_own_names():
    # The console spells no repo name or path of its own: it reads this file.
    names = schemas.names_json()
    assert set(names) == {
        "config_repo", "join_repo", "system_dir", "course_file", "semester_file",
        "default_formats", "instructors_file", "assignments_file", "registry_file",
        "records", "join_markers", "opencourse_file", "site_config", "semester_topic",
        "legacy",
    }  # fmt: skip
    assert (names["course_file"], names["semester_file"]) == (
        course.COURSE_CONFIG,
        course.SEMESTER_FILE,
    )
    assert names["default_formats"] == list(course.DEFAULT_FORMATS)
    assert names["semester_topic"] == course.SEMESTER_TOPIC
    assert names["join_markers"] == {
        "join_course": course.JOIN_COURSE_MARKER,
        "join_team": course.JOIN_TEAM_MARKER,
    }
    assert names["records"]["student_status"] == ".system/student-status.json"
    assert (names["config_repo"], names["join_repo"]) == (
        course.CONFIG_REPO,
        course.JOIN_REPO,
    )
    assert names["records"]["status"] == ".system/status.json"
    assert names["records"]["distributed"] == gradebook.DISTRIBUTED_PATH
    assert names["records"]["lock"] == team_formation.TEAM_LOCK_PATH
    for kind in ("outcomes", "pointer", "snapshots", "autograde", "solutions",
                 "team_formation", "archive", "semester_gradebook"):  # fmt: skip
        assert names["records"][kind].startswith(names["system_dir"] + "/"), kind


def test_labels_json_names_every_engine_value_once():
    # The console carries no label of its own: a value added to the engine without words
    # fails here, and so does a label for a value the engine no longer has.
    labels = schemas.labels_json()
    assert labels["solution_warning"] == course.SOLUTION_WARNING
    for key, values in {
        "formats": course.FORMATS,
        "submit_via": course.SUBMIT_VIA,
        "visibility": course.VISIBILITIES,
        "team_formation": course.TEAM_FORMATIONS,
    }.items():
        assert list(labels[key]) == list(values), key
        for value, words in labels[key].items():
            assert set(words) == {"label", "help"} and words["label"], (key, value)


def test_the_seeded_files_validate():
    # What Bootstrap seeds, unedited, must save through the console, which validates the
    # whole file before every write: the schedule's bare archive `event_datetime:` and the
    # comment-only `defaults:` blocks all read as YAML null.
    from dsl_course import bootstrap_course

    sched = yaml.safe_load(
        bootstrap_course.semester_scaffold("x-f2026", "schedule.yml", "release")
    )
    assert sched["semester"]["archive"]["event_datetime"] is None
    assert validate(sched, schemas.schedule_schema()) == []
    for code, admins in (("E1", None), ("", ["ann"])):
        meta = yaml.safe_load(bootstrap_course.course_seed("ML", code, admins, "main"))
        assert validate(meta, schemas.course_schema()) == []
    seeded = yaml.safe_load(
        bootstrap_course.semester_scaffold("x-f2026", "semester.yml", "release")
    )
    assert seeded["defaults"] is None
    assert validate(seeded, schemas.semester_schema()) == []


def test_blank_schedule_keys_validate():
    # A live schedule's shape: rows carrying a `details:` slot nobody has filled yet (the
    # engine reads blank as absent), and the console's own cleared archive date.
    rows = {
        f"lecture-{i:02d}": {
            "session": i,
            "event_datetime": f"2026-09-{i:02d}",
            "details": None,
        }
        for i in range(1, 24)
    }

    def term(archive: dict) -> dict:
        return {
            "start": {"type": "start", "event_datetime": "2026-09-01"},
            "archive": {"type": "archive", **archive},
        }

    doc = {
        "semester": term({"event_datetime": None, "show_on_site": True}),
        "lectures": rows,
        "exams": {"midterm": {"details": None, "tbc": None}},
    }
    assert validate(doc, schemas.schedule_schema()) == []
    assert validate({**doc, "semester": term({})}, schemas.schedule_schema()) == []
    # Still a type check: a list where text belongs is refused.
    assert validate(
        {**doc, "semester": term({"details": ["a"]})}, schemas.schedule_schema()
    )
