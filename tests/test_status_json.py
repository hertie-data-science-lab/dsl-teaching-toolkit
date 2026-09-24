"""status.json (`dsl.status/1`): the lifecycle model rendered from facts, and the writer.

The render is pure, so every test here builds the facts a gather would have read - a
parsed schedule, the faults the digest parsers produce, a listing - and asserts on the
document. The writer and the hook are driven with the reads stubbed at the consumer.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dsl_course import (
    grades,
    roster,
    schedule,
    schemas,
    status,
    status_json,
    sync_faculty,
)
from dsl_course.faults import ConfigFault, FaultKind, header_fault
from dsl_course.gh_contents import load_yaml_lines
from dsl_course.ops.request import validate
from tests.conftest import repo_row

COURSE = "hertie-dsl-demo-course-e1234"
SEMESTER = "hertie-dsl-demo-f2026"
BERLIN = ZoneInfo("Europe/Berlin")
# Wednesday 23 Sep 2026, 11:00 in Berlin - the contract example's "now".
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

SCHEDULE = """\
timezone: Europe/Berlin
semester_start: 2026-09-07
semester_end: 2026-12-18
releases:
  s3:
    event_datetime: 2026-09-24T10:00
    title: Trees
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/03_trees
  s5:
    event_datetime: 2026-10-08T10:00
    title: Trees and ensembles
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/05_trees
assignments:
  assignment-2:
    course_source_repo: assignment-2-f2026
    title: Regression
    handout_datetime: 2026-09-15T10:00
    due_datetime: 2026-09-27T23:59
  assignment-3:
    course_source_repo: assignment-3-f2026
    handout_datetime: 2026-10-20T10:00
    due_datetime: 2026-11-01T23:59
"""

# The contract's example document, verbatim (contracts.md section 3). The render must
# carry every key it carries, at every level, so WP1's exported schema and this module
# describe one shape.
CONTRACT_EXAMPLE = {
    "schema": "dsl.status/1",
    "inputs": {
        "schedule.yml": "<blob sha>",
        "instructors.yml": "...",
        "students.csv": "...",
        "teams.csv": "...",
        "grading_sheets": "<sha of the directory tree>",
        "course/dsl-course.yml": "...",
        "assignments.lock.yml": "...",
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
            "text": "Session 5 cites folder lectures/05_trees, which is not in "
            "course-materials-f2026.",
            "stops": "The release on Thu 8 Oct will be skipped.",
            "fix": {
                "repo": "hertie-dsl-demo-f2026/classroom-config",
                "path": "schedule.yml",
                "line": 41,
                "screen": "schedule",
                "entry": "s5",
            },
        },
    ],
    "this_week": [
        {
            "when": "2026-09-24T10:00:00+02:00",
            "type": "release",
            "ref": "s3",
            "title": "Session 3: Trees",
            "state": "planned",
        },
    ],
    "releases": [
        {
            "id": "s5",
            "when": "2026-10-08T10:00:00+02:00",
            "type": "lecture",
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
            "late_until": "2026-10-07T23:59:00+02:00",
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
            "summary": "Released Session 3: 7 files to materials.",
            "finished": "2026-09-23T09:01:10Z",
        }
    ],
}


def _sched(text: str = SCHEDULE) -> schedule.Schedule:
    return schedule.parse(load_yaml_lines(text))


def _student(handle: str = "", sent: str = "2026-09-01T10:00:00Z") -> roster.Student:
    return roster.Student("s@x.edu", "S", handle, "", code_sent_at=sent)


def _people(email: str | None = "prof@x.edu") -> dict:
    entry = {"github_handle": "prof", **({"email": email} if email else {})}
    return sync_faculty.parse_faculty_from_meta({"people": {"instructors": [entry]}})


def _course(**over) -> status_json.CourseFacts:
    facts = status_json.CourseFacts(
        org=COURSE,
        meta={
            "course_name": "Machine Learning",
            "course_code": "E1234",
            "course_description": "Learning from data.",
            "people": {
                "course_admins": [{"github_handle": "admin1", "email": "a@x.edu"}]
            },
        },
        github_paths={
            "dsl-course.yml": "c0ffee",
            "semesters.yml": "facade",
            ".github/workflows/scheduled-release.yml": "beef",
        },
        registry=[SEMESTER],
        materials=[
            status_json.MaterialsFacts("course-materials-f2026", "# Syllabus", True)
        ],
        templates=[
            status_json.TemplateFacts("assignment-2-f2026", "# Regression"),
            status_json.TemplateFacts("assignment-3-f2026", "# Trees"),
        ],
        public_site=True,
    )
    for key, value in over.items():
        setattr(facts, key, value)
    return facts


def _semester(**over) -> status_json.SemesterFacts:
    site = f"{SEMESTER}.github.io"
    listing = {
        name: repo_row(name)
        for name in ("classroom-config", "welcome", site, "materials")
    }
    facts = status_json.SemesterFacts(
        org=SEMESTER,
        listing=listing,
        config_paths={
            "schedule.yml": "5c4ed",
            "instructors.yml": "9e091",
            "students.csv": "57ude",
            "teams.csv": "7ea45",
            "grading_sheets": "5hee7",
            "assignments.lock.yml": "10c4",
        },
        sched=_sched(),
        people=_people(),
        students=[_student("ada"), _student("")],
        dest_paths={"materials": {"lectures", "lectures/03_trees"}},
        site_home="# Welcome to Machine Learning",
        site_last_update=datetime(2026, 9, 22, 6, 2, tzinfo=UTC),
        config_last_update=datetime(2026, 9, 21, 6, 0, tzinfo=UTC),
        staff_synced=True,
    )
    for key, value in over.items():
        setattr(facts, key, value)
    return facts


def _missing_s5() -> ConfigFault:
    """What `schedule.source_faults` files for a release folder nobody has written."""
    return ConfigFault(
        "releases.s5",
        f"{COURSE}/course-materials-f2026/lectures/05_trees does not exist",
        datetime(2026, 10, 8, 10, 0, tzinfo=BERLIN),
        field="course_source_path",
        kind=FaultKind.MISSING_PATH,
        file="schedule.yml",
        lineno=41,
        repo="course-materials-f2026",
        path="lectures/05_trees",
    )


def _autograde_sometimes() -> ConfigFault:
    """What `grades.grading_spec_faults` files for an unreadable value."""
    return ConfigFault(
        "assignments.assignment-3",
        "`autograde: sometimes` is not yes or no",
        datetime(2026, 11, 1, 23, 59, tzinfo=BERLIN),
        field="autograde",
        lineno=7,
        file=grades.GRADING_FILE,
        in_repo="assignment-3-f2026",
        in_org=COURSE,
        ref="solution",
    )


def _contract_scenario() -> tuple[status_json.CourseFacts, status_json.SemesterFacts]:
    course = _course()
    course.templates[1].faults = [_autograde_sometimes()]
    semester = _semester(
        schedule_faults=[_missing_s5()],
        roster_faults=[header_fault("students.csv", ["github_handle"])],
        template_faults=[_autograde_sometimes()],
    )
    return course, semester


def _render(course=None, semester=None, now=NOW) -> dict:
    return status_json.render_semester(
        course or _course(), semester or _semester(), now
    )


def _keys(doc, prefix="") -> set[str]:
    """Every key path in a document, lists folded to their first element."""
    out: set[str] = set()
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.add(f"{prefix}{k}")
            out |= _keys(v, f"{prefix}{k}.")
    elif isinstance(doc, list) and doc:
        out |= _keys(doc[0], f"{prefix}[].")
    return out


# ---------------------------------------------------------------------------- the model


def test_contract_example_validates():
    # WP1 exports the JSON Schema; until it lands, the contract's own example is the
    # shape. Every key path the example carries, the render carries too (the render may
    # add: `app_installed`, `copies`, `fix.ref`).
    doc = _render(*_contract_scenario())
    doc["operations"] = [CONTRACT_EXAMPLE["operations"][0]]  # none in the fixture
    missing = _keys(CONTRACT_EXAMPLE) - _keys(doc)
    assert missing == set()
    assert doc["schema"] == "dsl.status/1"


def test_a_healthy_semester_has_every_setup_stage_done_and_no_problems():
    doc = _render(
        semester=_semester(students=[_student("ada"), _student("bob")]),
    )
    assert doc["problems"] == []
    assert doc["course"]["stages"] == dict.fromkeys(status_json.COURSE_STAGES, "done")
    assert doc["course"]["ready"] is True
    stages = doc["semester"]["stages"]
    assert {
        k: stages[k] for k in ("K1", "K2", "K3", "K4", "K5", "K6")
    } == dict.fromkeys(("K1", "K2", "K3", "K4", "K5", "K6"), "done")
    # Archiving is the end of the term, not a setup step a healthy semester has done.
    assert stages["K7"] == "todo"
    assert doc["semester"]["live"] is True
    assert doc["semester"]["label"] == "Fall 2026"
    assert (doc["semester"]["week"], doc["semester"]["weeks"]) == (3, 15)


def test_the_contract_example_marks_k4_k5_and_c5_and_lists_three_problems():
    doc = _render(*_contract_scenario())
    assert doc["semester"]["stages"]["K4"] == "problem"
    assert doc["semester"]["stages"]["K5"] == "problem"
    assert doc["course"]["stages"]["C5"] == "problem"
    assert doc["course"]["ready"] is False
    assert [p["id"] for p in doc["problems"]] == [
        "schedule:s5:SOURCE_MISSING",
        "roster:header:ROSTER",
        "template:assignment-3:GRADING_CONFIG",
    ]
    source, _, template = doc["problems"]
    assert source["stops"] == "The release on Thu 8 Oct will be skipped."
    assert source["fix"] == {
        "repo": f"{SEMESTER}/classroom-config",
        "path": "schedule.yml",
        "line": 41,
        "screen": "schedule",
        "entry": "s5",
    }
    # A course-side fault, shown on the semester it will affect and tagged with where the
    # fix lives - on the template's solution branch.
    assert (template["scope"], template["stage"]) == ("course", "C5")
    assert template["fix"]["repo"] == f"{COURSE}/assignment-3-f2026"
    assert template["fix"]["ref"] == "solution"
    assert template["stops"] == "Marking uses the toolkit's default for that value."
    release = next(r for r in doc["releases"] if r["id"] == "s5")
    assert release["state"] == "will_be_skipped"
    assignment = next(a for a in doc["assignments"] if a["slug"] == "assignment-3")
    assert assignment["problem"] is True


def test_two_faults_on_one_entry_are_two_problems():
    second = _missing_s5()
    second.path = "lectures/05_forests"
    doc = _render(semester=_semester(schedule_faults=[_missing_s5(), second]))
    assert [p["id"] for p in doc["problems"]] == [
        "schedule:s5:SOURCE_MISSING",
        "schedule:s5:SOURCE_MISSING:2",
    ]


def test_a_source_whose_moment_passed_was_skipped_and_its_release_is_late():
    doc = _render(
        semester=_semester(schedule_faults=[_missing_s5()]),
        now=datetime(2026, 10, 9, 9, 0, tzinfo=UTC),
    )
    assert doc["problems"][0]["stops"] == "The release on Thu 8 Oct was skipped."
    assert next(r for r in doc["releases"] if r["id"] == "s5")["state"] == "late"


def test_release_states_follow_the_destination():
    doc = _render(now=datetime(2026, 9, 25, 9, 0, tzinfo=UTC))
    states = {r["id"]: r["state"] for r in doc["releases"]}
    # s3's folder is in materials; s5 is still to come.
    assert states == {"s3": "released", "s5": "planned"}
    s3 = next(r for r in doc["releases"] if r["id"] == "s3")
    assert (s3["type"], s3["dest"]) == (
        "lecture",
        {"repo": "materials", "path": "lectures/03_trees"},
    )


def test_an_archived_semester_is_not_live_and_k7_is_done():
    semester = _semester()
    semester.listing["classroom-config"] = repo_row("classroom-config", archived=True)
    doc = _render(semester=semester)
    assert doc["semester"]["live"] is False
    assert doc["semester"]["stages"]["K7"] == "done"


def test_this_week_runs_from_local_midnight_for_seven_days():
    sched = _sched(
        """\
timezone: Europe/Berlin
events:
  e0:
    title: starts the week
    event_datetime: 2026-09-23T00:00
  e1:
    title: last minute of it
    event_datetime: 2026-09-29T23:59
  e2:
    title: the next week
    event_datetime: 2026-09-30T00:00
  e3:
    title: yesterday
    event_datetime: 2026-09-22T23:59
"""
    )
    rows = status_json.this_week(sched, [], [], NOW)
    assert [r["ref"] for r in rows] == ["e0", "e1"]
    assert rows[0]["when"] == "2026-09-23T00:00:00+02:00"


def test_this_week_lists_releases_and_due_dates_in_order():
    doc = _render()
    assert [(r["type"], r["ref"]) for r in doc["this_week"]] == [
        ("release", "s3"),
        ("due", "assignment-2"),
    ]


@pytest.mark.parametrize(
    ("now", "units", "expected"),
    [
        (datetime(2026, 9, 14, 12, 0, tzinfo=UTC), 0, "declared"),
        (datetime(2026, 9, 20, 12, 0, tzinfo=UTC), 2, "open"),
        # Due 27 Sep 23:59 Berlin; the late window (10 days by default) runs to 7 Oct.
        (datetime(2026, 9, 27, 21, 58, tzinfo=UTC), 2, "open"),
        (datetime(2026, 9, 27, 21, 59, tzinfo=UTC), 2, "late_window"),
        (datetime(2026, 10, 7, 21, 58, tzinfo=UTC), 2, "late_window"),
        (datetime(2026, 10, 7, 21, 59, tzinfo=UTC), 2, "marking"),
    ],
)
def test_an_assignment_moves_through_due_and_the_late_cutoff(now, units, expected):
    listing = _semester().listing | {
        f"assignment-2-{h}": repo_row(f"assignment-2-{h}")
        for h in ("ada", "bob")[:units]
    }
    listing["assignment-2"] = repo_row(
        "assignment-2", isTemplate=True, topics=["assignment-template"]
    )
    doc = _render(semester=_semester(listing=listing), now=now)
    row = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
    assert (row["state"], row["units"]) == (expected, units)
    assert row["late_until"] == "2026-10-07T23:59:00+02:00"


def test_a_group_assignment_with_no_copies_after_hand_out_is_forming_teams():
    group = grades.GradingSpec(type="group", team_formation="self_select")
    doc = _render(semester=_semester(specs={"assignment-2": group}))
    row = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
    assert (row["state"], row["teams"]) == ("teams_forming", 0)
    assigned = grades.GradingSpec(type="group", team_formation="assigned")
    doc = _render(semester=_semester(specs={"assignment-2": assigned}))
    row = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
    assert row["state"] == "blocked"


def test_marks_are_counted_off_the_sheet_and_returned_once_every_unit_is():
    sheet = {
        "submissions": {
            "ada": {"info": {"submitted": "2026-09-27"}, "score_individual": 8},
            "bob": {"info": {"submitted": None}, "score_individual": None},
        }
    }
    later = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    doc = _render(semester=_semester(sheets={"assignment-2": sheet}), now=later)
    row = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
    assert (row["marks"], row["submissions"], row["state"]) == (
        {"filled": 1, "total": 2},
        1,
        "marking",
    )
    sheet["submissions"]["bob"]["score_individual"] = 5
    marked = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    back = {"ada": later, "bob": later}

    def state(returned_at, changed=marked):
        doc = _render(
            semester=_semester(
                sheets={"assignment-2": sheet},
                returned_at=returned_at,
                sheet_changed={"assignment-2": changed},
            ),
            now=later,
        )
        row = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
        return row["returned"], row["state"]

    assert state(back) == (True, "returned")
    # Bob's gradebook was never written: not every unit has its marks back.
    assert state({"ada": later}) == (False, "marking")
    # A mark changed on the sheet after the return is not returned yet.
    assert state(back, changed=later + timedelta(hours=1)) == (False, "marking")


def test_returned_is_read_off_the_gradebook_rows_distribute_writes(monkeypatch):
    text = (
        "target,assignment,channel,content_hash,distributed_at,issue\n"
        "Ada,,gradebook,abc,2026-10-12T09:00:00+00:00,\n"
        "bob,,email,m:1,2026-10-12T09:00:00+00:00,\n"
    )
    monkeypatch.setattr(status_json, "get_file_content", lambda *a, **k: text)
    assert status_json._returned_at(SEMESTER) == {
        "ada": datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    }


def test_a_staff_entry_without_an_email_is_a_counted_problem():
    doc = _render(semester=_semester(people=_people(email=None)))
    assert doc["semester"]["stages"]["K3"] == "problem"
    (problem,) = doc["problems"]
    assert problem["id"] == "people:staff:NO_EMAIL"
    assert "prof" not in json.dumps(problem)


def test_inputs_carry_shas_and_no_timestamp_is_recorded_for_the_write():
    doc = _render()
    assert doc["inputs"] == {
        "schedule.yml": "5c4ed",
        "instructors.yml": "9e091",
        "students.csv": "57ude",
        "teams.csv": "7ea45",
        "grading_sheets": "5hee7",
        "course/dsl-course.yml": "c0ffee",
        "assignments.lock.yml": "10c4",
    }
    stamp = re.compile(
        r"generated|written|timestamp|updated_at|rendered|as_of", re.IGNORECASE
    )
    assert not [k for k in _keys(doc) if stamp.search(k)]
    # The scheduler's heartbeat moves every tick; in the file it would commit every 15 min.
    assert "automation" not in doc
    # Two renders of one state are one blob, which is what makes the write a no-op.
    assert status_json.dumps(_render()) == status_json.dumps(_render())


def test_the_course_file_carries_no_handle_email_or_student_repo():
    course = _course(
        faults=[
            ConfigFault(
                "people.course_admins[0]",
                "no usable `email:` - access is still granted, but no fault reaches "
                "this person",
                file="dsl-course.yml",
                field="email",
                in_repo=".github",
                lineno=12,
            )
        ]
    )
    doc = status_json.render_course_file(course, NOW)
    text = json.dumps(doc)
    assert "admin1" not in text and "@x.edu" not in text
    assert set(doc) == {"schema", "inputs", "course", "problems"}
    assert doc["course"]["stages"]["C3"] == "problem"
    assert doc["inputs"] == {
        "dsl-course.yml": "c0ffee",
        "semesters.yml": "facade",
    }


def test_semester_weeks_before_during_and_after_the_semester():
    start, end = date(2026, 9, 7), date(2026, 12, 18)
    assert status_json.semester_weeks(start, end, date(2026, 9, 1)) == (0, 15)
    assert status_json.semester_weeks(start, end, date(2026, 9, 7)) == (1, 15)
    assert status_json.semester_weeks(start, end, date(2027, 1, 5)) == (15, 15)
    assert status_json.semester_weeks(None, end, date(2026, 9, 7)) == (None, None)


# ---------------------------------------------------------------------------- writer


def _stub_write(monkeypatch, doc: dict, results=(True,)):
    puts: list[tuple] = []
    answers = iter(results)
    monkeypatch.setattr(status, "_document", lambda course, semester: doc)
    monkeypatch.setattr(
        status,
        "put_file",
        lambda org, repo, path, content, msg: (
            puts.append((org, repo, path, content)) or next(answers)
        ),
    )
    return puts


def test_write_puts_the_semester_file_into_classroom_config(monkeypatch):
    doc = _render()
    puts = _stub_write(monkeypatch, doc)
    assert status.write(COURSE, SEMESTER) == 0
    assert puts == [
        (SEMESTER, "classroom-config", ".dsl/status.json", status_json.dumps(doc))
    ]


def test_write_puts_the_course_file_into_dot_github(monkeypatch):
    doc = status_json.render_course_file(_course(), NOW)
    puts = _stub_write(monkeypatch, doc)
    assert status.write(COURSE) == 0
    assert [(o, r, p) for o, r, p, _ in puts] == [
        (COURSE, ".github", ".dsl/status.json")
    ]


def test_write_tries_twice_for_the_dispatchers_race(monkeypatch):
    puts = _stub_write(monkeypatch, _render(), results=(False, True))
    assert status.write(COURSE, SEMESTER) == 0
    assert len(puts) == 2


def test_write_refuses_a_semester_the_registry_does_not_list(monkeypatch):
    puts = _stub_write(monkeypatch, _render(course=_course(registry=["someone-else"])))
    assert status.write(COURSE, SEMESTER) == 1
    assert puts == []


def test_write_leaves_an_archived_semester_frozen(monkeypatch):
    semester = _semester()
    semester.listing["classroom-config"] = repo_row("classroom-config", archived=True)
    puts = _stub_write(monkeypatch, _render(semester=semester))
    assert status.write(COURSE, SEMESTER) == 0
    assert puts == []


def test_write_after_op_refreshes_the_semester_then_the_course(monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        status,
        "write",
        lambda course, semester=None: seen.append((course, semester)) or 0,
    )
    request = {
        "schema": "dsl.request/1",
        "op": "release.now",
        "course_org": COURSE,
        "semester_org": SEMESTER,
    }
    assert status.write_after_op(request) == 0
    assert seen == [(COURSE, SEMESTER), (COURSE, None)]
    seen.clear()
    assert status.write_after_op({"op": "assignment.create", "course_org": COURSE}) == 0
    assert seen == [(COURSE, None)]


def test_write_after_op_never_raises(monkeypatch):
    def boom(course, semester=None):
        raise RuntimeError("could not list repos")

    monkeypatch.setattr(status, "write", boom)
    assert status.write_after_op({"course_org": COURSE, "semester_org": SEMESTER}) == 2
    assert status.write_after_op({}) == 1


def test_json_v1_prints_the_document(monkeypatch, capsys):
    monkeypatch.setattr(status, "_document", lambda course, semester: {"schema": "x"})
    monkeypatch.setattr("sys.argv", ["status", "--course-org", COURSE, "--json-v1"])
    assert status.main() == 0
    assert json.loads(capsys.readouterr().out) == {"schema": "x"}


def test_the_checklist_still_needs_a_semester(monkeypatch):
    monkeypatch.setattr("sys.argv", ["status", "--course-org", COURSE])
    with pytest.raises(SystemExit):
        status.main()


# ---------------------------------------------------------------------------- gather


def test_collect_semester_walks_every_read_end_to_end(monkeypatch):
    # The wiring between the loaders and the facts: every read answered from memory, the
    # rest of the pipeline real. `conftest._no_live_gh` catches any read this misses.
    files = {
        (SEMESTER, "classroom-config", "schedule.yml"): SCHEDULE,
        (SEMESTER, "classroom-config", "grading_sheets/assignment-2.yml"): (
            "submissions:\n  ada:\n    score_individual: 7\n"
        ),
        (SEMESTER, "classroom-config", ".dsl/outcomes/release.now.json"): json.dumps(
            CONTRACT_EXAMPLE["operations"][0]
        ),
        (COURSE, "course-materials-f2026", "SYLLABUS.md"): "# Syllabus",
        (COURSE, "course-materials-f2026", "publish.yml"): "public: lectures\n",
        (COURSE, "assignment-2-f2026", "README.md"): "# Regression",
        (COURSE, "assignment-2-f2026", "grading_config.yml"): "autograde: sometimes\n",
        (SEMESTER, f"{SEMESTER}.github.io", "index.md"): "# Welcome",
    }

    def content(org, repo, path, ref=""):
        return files.get((org, repo, path))

    listings = {
        COURSE: [
            repo_row(".github"),
            repo_row("course-materials-f2026"),
            repo_row("assignment-2-f2026", isTemplate=True),
        ],
        SEMESTER: [
            repo_row(n)
            for n in (
                "classroom-config",
                "welcome",
                f"{SEMESTER}.github.io",
                "materials",
            )
        ],
    }
    for module in (status_json, schedule, grades):
        monkeypatch.setattr(module, "get_file_content", content)
    schedule._schedule_text.cache_clear()
    monkeypatch.setattr(status_json, "list_org_repos", lambda org: listings[org])
    monkeypatch.setattr(status_json, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(
        status_json,
        "repo_path_shas",
        lambda org, repo, branch: (
            {"dsl-course.yml": "c0ffee"}
            if repo == ".github"
            else {"schedule.yml": "5c4ed", ".dsl/outcomes/release.now.json": "0u7"}
        ),
    )
    monkeypatch.setattr(
        status_json,
        "org_meta",
        lambda org: {"course_name": "Machine Learning", "course_code": "E1234"},
    )
    monkeypatch.setattr(
        status_json.sync_faculty, "read_course_config", lambda org, faults: {}
    )
    monkeypatch.setattr(
        status_json, "read_semester_registry", lambda org, faults: [SEMESTER]
    )
    monkeypatch.setattr(
        schedule,
        "source_repo_paths",
        lambda org, repo: {"lectures/03_trees", "lectures/05_trees"},
    )
    monkeypatch.setattr(
        status_json.sync_faculty, "read_semester_people", lambda org, faults: _people()
    )
    monkeypatch.setattr(
        status_json.roster, "load", lambda org, faults=None: [_student("ada")]
    )
    monkeypatch.setattr(
        status_json.teams, "load", lambda org, faults=None, known=None: {}
    )
    monkeypatch.setattr(
        status_json, "repo_tree", lambda org, repo, branch: ("lectures",)
    )
    monkeypatch.setattr(status_json, "gh", lambda *a: (0, "2026-09-22T06:02:00Z"))
    monkeypatch.setattr(status_json, "get_team_members", lambda org, team: {"prof"})
    monkeypatch.setattr(grades, "_org_settings_faults", lambda org: [])

    doc = status_json.collect_semester(COURSE, SEMESTER, NOW)
    assert doc["semester"]["org"] == SEMESTER
    assert doc["inputs"]["schedule.yml"] == "5c4ed"
    assert doc["inputs"]["course/dsl-course.yml"] == "c0ffee"
    assert doc["staff"] == {"instructors": 1, "tas": 0, "synced": True}
    assert doc["operations"][0]["op"] == "release.now"
    a2 = next(a for a in doc["assignments"] if a["slug"] == "assignment-2")
    assert a2["marks"] == {"filled": 1, "total": 1}
    # The template's unreadable value, seen from the semester that cites it and from the
    # course: one fault, both scopes' problem lists.
    assert "template:assignment-2:GRADING_CONFIG" in [p["id"] for p in doc["problems"]]
    course = status_json.collect_course(COURSE, NOW)
    assert course["course"]["templates"][0]["state"] == "problem"


def test_every_render_validates_against_the_exported_schema():
    # WP1's `console/schemas/status.schema.json` is what the console reads the file by.
    healthy = _render(semester=_semester(students=[_student("ada"), _student("bob")]))
    archived = _semester(sched=_sched("timezone: Europe/Berlin\n"), people=None)
    archived.listing["classroom-config"] = repo_row("classroom-config", archived=True)
    for doc in (
        _render(*_contract_scenario()),
        healthy,
        _render(semester=archived),
        status_json.render_course_file(_course(), NOW),
    ):
        assert validate(doc, schemas.status_schema()) == []


# ------------------------------------------------------------------ problem sentences

# The demo's group project sheet: one student in two teams.
DUPLICATE_SHEET = """\
teams:
  red:
    members:
      ada: {}
      bob: {}
    score: 5
  blue:
    members:
      ada: {}
"""


def _problems(faults: list[ConfigFault]) -> list[dict]:
    return [status_json.problem_from_fault(f, SEMESTER, NOW) for f in faults]


def _many_faults() -> list[ConfigFault]:
    """A fault from every hand-edited file, through the parsers that file them."""
    faults: list[ConfigFault] = [_missing_s5(), _autograde_sometimes()]
    faults += grades.sheet_faults("assignment-3-project", DUPLICATE_SHEET)
    faults += grades.sheet_faults("assignment-4", "teams: [\n")
    spec_faults, _ = grades.grading_spec_faults(
        "assignment-3", "assignment-3-f2026", COURSE, "a: [\n", None
    )
    faults += spec_faults
    sync_faculty.parse_faculty_from_meta(
        load_yaml_lines(
            "people:\n  instructors:\n    - github_handle: 'not valid!'\n"
            "      email: p@x.edu\n  teaching_assistants:\n    - github_handle: ta1\n"
        ),
        faults,
    )
    faults += _sched(
        "timezone: Europe/Berlin\nreleases:\n  s1:\n    event_datetime: someday\n"
        "    deploy:\n      - course_source_repo: x\n        course_source_path: y\n"
    ).faults
    faults.append(header_fault("students.csv", ["github_handle"]))
    return faults


def test_a_held_mark_is_one_plain_sentence_with_its_fault_code_in_the_id():
    (problem,) = _problems(grades.sheet_faults("assignment-3-project", DUPLICATE_SHEET))
    assert problem["id"] == "sheet:assignment-3-project:GRADING_SHEETS"
    assert problem["stage"] == "marking"
    assert problem["text"] == (
        "Line 9 of the assignment-3-project marking sheet lists a student who is also "
        "in another team or entry, so marks for both are held."
    )
    assert problem["stops"] == (
        "That sheet is not updated, and none of its marks are returned."
    )
    assert problem["fix"]["line"] == 9 and problem["fix"]["entry"] == (
        "assignment-3-project"
    )


def test_no_problem_sentence_carries_markdown_or_a_null_stage():
    problems = _problems(_many_faults())
    assert len(problems) >= 8
    for p in problems:
        assert p["stage"], p["id"]
        for key in ("text", "stops"):
            assert "`" not in p[key] and "**" not in p[key], p[key]
            assert p[key].endswith("."), p[key]
            assert " - " not in p[key], p[key]


def test_a_derived_sentence_names_its_subject_in_the_consoles_words():
    by_id = {p["id"]: p for p in _problems(_many_faults())}
    ta = by_id["people:people.teaching_assistants-0:PEOPLE"]
    assert ta["text"] == "Teaching assistant 1 in instructors.yml: no usable email."
    assert ta["stops"] == (
        "Access is still granted, but no notification reaches this person."
    )
    assert by_id["schedule:s1:SCHEDULE"]["text"].startswith("Schedule entry s1: ")
    texts = [p["text"] for p in by_id.values()]
    assert (
        "The assignment-3 template's settings file is not valid YAML, so the "
        "assignment is marked on the toolkit's defaults."
    ) in texts


def test_the_vocabulary_pass_keeps_the_file_names_a_fix_edits():
    assert (
        status_json.plain_words(
            "the `grading_config.yml` grading value is not read by the dry run"
        )
        == "the grading_config.yml marking value is not read by the preview"
    )
    assert status_json.plain_words("an onboarded handle") == "a joined handle"


# ------------------------------------------------------------------ semester-only template faults


def _visibility_mismatch() -> ConfigFault:
    """What `grades.grading_spec_faults` files for a private assignment whose repos
    this semester handed out public."""
    rows = [
        repo_row("assignment-1-ada", visibility="public"),
        repo_row("assignment-1-bob", visibility="private"),
    ]
    faults, _ = grades.grading_spec_faults(
        "assignment-1",
        "assignment-1-f2026",
        COURSE,
        "visibility: private\n",
        None,
        handed_out=rows,
    )
    (fault,) = faults
    return fault


def test_a_visibility_mismatch_is_the_semesters_problem_not_the_courses():
    fault = _visibility_mismatch()
    assert fault.per_semester
    doc = _render(semester=_semester(template_faults=[fault]))
    (problem,) = doc["problems"]
    assert (problem["scope"], problem["stage"]) == ("semester", "open")
    assert problem["id"] == "template:assignment-1:GRADING_CONFIG"
    assert problem["text"] == (
        "assignment-1's settings say its repos are private, but 1 of 2 handed out in "
        "this semester are not."
    )
    assert problem["stops"] == (
        "Those repos stay as they are, and the pages the toolkit writes describe them "
        "wrongly."
    )
    # The fix is still the template's line; the course's own stage does not move.
    assert problem["fix"]["repo"] == f"{COURSE}/assignment-1-f2026"
    assert doc["course"]["stages"]["C5"] == "done"
    assert all(t["state"] == "ready" for t in doc["course"]["templates"])
    assert "problem" not in doc["semester"]["stages"].values()


def test_the_course_file_never_lists_a_semester_only_problem():
    # The course's own gather reads each template with nothing handed out to compare
    # it against, so the fault does not arise there at all.
    faults, _ = grades.grading_spec_faults(
        "assignment-1", "assignment-1-f2026", COURSE, "visibility: private\n", None
    )
    assert faults == []
    course = status_json.render_course_file(_course(), NOW)
    assert course["problems"] == [] and course["course"]["stages"]["C5"] == "done"


def test_the_org_settings_problem_is_the_semesters_setup(monkeypatch):
    monkeypatch.setattr(
        grades.gh_teams,
        "org_settings",
        lambda org: {grades.gh_teams.MEMBERS_CAN_DELETE: True},
    )
    (fault,) = grades._org_settings_faults(SEMESTER)
    doc = _render(semester=_semester(template_faults=[fault]))
    (problem,) = doc["problems"]
    assert (problem["scope"], problem["stage"]) == ("semester", "K2")
    assert problem["id"] == "org:org-settings:ORG_SETTINGS"
    assert problem["stops"] == "A student can delete or move their own submission."
    assert problem["fix"]["url"] == (
        f"https://github.com/organizations/{SEMESTER}/settings/member_privileges"
    )
    assert problem["fix"]["repo"] == f"{SEMESTER}/classroom-config"
    assert doc["semester"]["stages"]["K2"] == "problem"
    assert doc["course"]["stages"]["C5"] == "done"
    assert validate(doc, schemas.status_schema()) == []


def test_a_template_that_does_not_parse_stays_the_courses():
    faults, _ = grades.grading_spec_faults(
        "assignment-3", "assignment-3-f2026", COURSE, "a: [\n", None
    )
    course = _course()
    course.templates[1].faults = faults
    doc = _render(course, _semester(template_faults=faults))
    (problem,) = doc["problems"]
    assert (problem["scope"], problem["stage"]) == ("course", "C5")
    assert doc["course"]["stages"]["C5"] == "problem"
    public = status_json.render_course_file(course, NOW)
    assert [p["id"] for p in public["problems"]] == [problem["id"]]


# ------------------------------------------------------------------ why a stage is not done


def test_a_stage_that_is_not_done_says_why():
    # The demo: one TA and no instructor, and last term's materials repo unwritten.
    ta_only = sync_faculty.parse_faculty_from_meta(
        {
            "people": {
                "teaching_assistants": [{"github_handle": "ta", "email": "t@x.edu"}]
            }
        }
    )
    course = _course(
        materials=[
            status_json.MaterialsFacts(
                "course-materials-f2025", "<!-- dsl-stub: syllabus -->", True
            ),
            status_json.MaterialsFacts("course-materials-f2026", "# Syllabus", True),
        ]
    )
    doc = _render(course, _semester(people=ta_only))
    assert doc["semester"]["stages"]["K3"] == "todo"
    assert doc["semester"]["stage_why"]["K3"] == (
        "No instructor is declared in instructors.yml yet."
    )
    assert doc["course"]["stages"]["C4"] == "todo"
    assert doc["course"]["stage_why"]["C4"] == (
        "course-materials-f2025's SYLLABUS.md is still the placeholder."
    )
    # Done stages carry no sentence; every other one does.
    for block in (doc["course"], doc["semester"]):
        assert set(block["stage_why"]) == {
            s for s, state in block["stages"].items() if state != "done"
        }
    assert validate(doc, schemas.status_schema()) == []


def test_a_problem_or_a_prerequisite_is_the_why():
    doc = _render(*_contract_scenario())
    assert doc["semester"]["stage_why"]["K4"] == "1 problem needs fixing."
    half = _semester(people=None)
    del half.listing["welcome"]
    doc = _render(semester=half)
    assert doc["semester"]["stage_why"]["K2"] == "The semester has no welcome repo yet."
    assert doc["semester"]["stages"]["K3"] == "blocked"
    assert (
        doc["semester"]["stage_why"]["K3"] == "Waiting for the semester to be set up."
    )
    doc = _render(semester=_semester(listing={}))
    assert doc["semester"]["stage_why"]["K2"] == "Waiting for the semester org."


def test_the_archive_stage_says_when():
    doc = _render()
    assert doc["semester"]["stage_why"]["K7"] == (
        "Not archived yet; the schedule sets no archive date."
    )


def test_the_course_file_carries_its_whys_too():
    course = _course(public_site=False)
    public = status_json.render_course_file(course, NOW)
    assert public["course"]["stage_why"] == {
        "C6": "There is no public website; it is optional."
    }


# ------------------------------------------------------------------ dates for the console's clock


def _client_state(row: dict, now: datetime) -> str:
    """What the console derives from an assignment row it already holds, with no
    rewrite of status.json: open -> late window -> marking on the row's own dates."""
    if row["state"] not in ("open", "late_window", "marking"):
        return row["state"]
    due = datetime.fromisoformat(row["due"])
    late_until = datetime.fromisoformat(row["late_until"])
    if now >= late_until:
        return "marking"
    return "late_window" if now >= due else "open"


def test_every_assignment_and_release_carries_the_moments_the_console_needs():
    doc = _render(*_contract_scenario())
    for row in doc["assignments"]:
        for key in ("handout", "due", "late_until", "solution_shown"):
            assert key in row, (row["slug"], key)
        for key in ("handout", "due", "late_until"):
            if row[key] is not None:
                assert datetime.fromisoformat(row[key]).tzinfo is not None
    assert all("when" in r for r in doc["releases"])
    assert validate(doc, schemas.status_schema()) == []
    # The exported schema holds the writer to it: a row without them does not validate.
    bare = json.loads(json.dumps(doc))
    del bare["assignments"][0]["late_until"]
    del bare["releases"][0]["when"]
    assert len(validate(bare, schemas.status_schema())) == 2


def test_the_console_can_move_open_to_late_window_without_a_rewrite():
    # Rendered on Wednesday 23 Sep: assignment-2 is open, due Sunday 27 Sep.
    course, semester = _contract_scenario()
    semester.listing |= {
        "assignment-2-ada": repo_row("assignment-2-ada"),
        "assignment-2": repo_row(
            "assignment-2", isTemplate=True, topics=["assignment-template"]
        ),
    }
    written = next(
        a
        for a in _render(course, semester)["assignments"]
        if a["slug"] == "assignment-2"
    )
    assert written["state"] == "open"
    due = datetime.fromisoformat(written["due"])
    late_until = datetime.fromisoformat(written["late_until"])
    assert due < late_until
    for later in (due - timedelta(minutes=1), due, late_until):
        engine = next(
            a
            for a in _render(course, semester, now=later)["assignments"]
            if a["slug"] == "assignment-2"
        )
        assert _client_state(written, later) == engine["state"]


def test_a_failed_refresh_names_no_repo(monkeypatch, capsys):
    def boom(course, semester):
        raise RuntimeError("gh: Not Found (repos/x/assignment-3-octocat)")

    monkeypatch.setattr(status, "_document", boom)
    assert status.refresh(COURSE, SEMESTER) == 1
    out = capsys.readouterr()
    assert "octocat" not in out.out + out.err
    assert "RuntimeError" in out.out + out.err


def test_write_reads_the_roster_as_it_is_now(monkeypatch):
    texts = iter(["before the send", "after the send"])
    monkeypatch.setattr(roster, "get_file_content", lambda *a, **k: next(texts))
    assert roster._roster_text(SEMESTER) == "before the send"
    seen = []

    def document(course, semester):
        seen.append(roster._roster_text(semester))
        return _render()

    monkeypatch.setattr(status, "_document", document)
    monkeypatch.setattr(status, "put_file", lambda *a, **k: True)
    status.write(COURSE, SEMESTER)
    assert seen == ["after the send"]


def test_check_setup_still_reports_a_write_that_did_not_land(monkeypatch):
    # cohort.check runs `status --write`: its Outcome must say failed, unlike the
    # nightly refresh, which only warns.
    _stub_write(monkeypatch, _render(), results=(False, False))
    monkeypatch.setattr(
        "sys.argv",
        ["status", "--course-org", COURSE, "--semester-org", SEMESTER, "--write"],
    )
    assert status.main() == 1
