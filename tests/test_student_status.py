"""student-status.json: the one PUBLIC file the student console reads a semester from.

The allow-list comes first. The file lives in the semester org's public `.github`, so a
render built from a semester full of personal data (a roster with names, emails and enrol
codes, teams with handles, an instructor who did not choose to show their email) must
carry none of it, and every key at every level must be one the module names.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from dsl_course import grades, roster, schedule, schemas, student_status
from dsl_course.gh_contents import load_yaml_lines
from dsl_course.materials import Declared
from dsl_course.schema_check import validate
from dsl_course.status_json import CourseFacts, SemesterFacts
from tests.conftest import repo_row

COURSE = "hertie-dsl-demo-course-e1234"
SEMESTER = "hertie-dsl-demo-f2026"
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)

SCHEDULE = """\
timezone: Europe/Berlin
semester_start: 2026-09-07
semester_end: 2026-12-18
archive:
  event_datetime: 2027-01-31
  show_on_site: false
releases:
  lecture_01:
    event_datetime: 2026-09-08T10:00
    kind: lecture
    title: Intro
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/01_intro
        semester_dest_repo: materials
  readings_01:
    event_datetime: 2026-09-01T10:00
    kind: readings
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: readings/01_intro
        semester_dest_repo: materials
  lecture_02:
    event_datetime: 2026-09-30T10:00
    kind: lecture
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/02_trees
        semester_dest_repo: materials
events:
  midterm:
    kind: exam
    event_datetime: 2026-10-28T09:00
    title: Midterm
assignments:
  assignment-1:
    course_source_repo: assignment-1-f2026
    handout_datetime: 2026-09-15T10:00
    due_datetime: 2026-09-27T23:59
  assignment-2-project:
    course_source_repo: assignment-2-project-f2026
    handout_datetime: 2026-09-20T10:00
    due_datetime: 2026-10-30T23:59
  assignment-3:
    course_source_repo: assignment-3-f2026
    handout_datetime: 2026-10-20T10:00
    due_datetime: 2026-11-01T23:59
  assignment-4:
    course_source_repo: assignment-4-f2026
    handout_datetime: 2026-10-20T10:00
    due_datetime: 2026-11-01T23:59
    show_on_site: false
"""

# Everything personal the semester holds. None of it may reach the public file.
PII = (
    "octocat",
    "hubot",
    "monalisa",
    "jane.doe@hertie-school.org",
    "Jane Doe",
    "dsl-ab3k9m",
    "hidden@hertie-school.org",
    "priv-ta",
)

INSTRUCTORS = {
    "instructors": [
        {
            "github_handle": "prof-x",
            "role": "instructor",
            "name": "Prof X",
            "title": "Professor",
            "email": "shown@hertie-school.org",
            "show_email": True,
            "photo": "/_images/pp/x.jpg",
            "url": "https://example.org/x",
        },
        {
            "github_handle": "priv-ta",
            "role": "teaching_assistant",
            "name": "Tee A",
            "email": "hidden@hertie-school.org",
        },
    ]
}

README_1 = "# Linear regression\n\nFit a line.\n"
README_3 = "# Secret heading\n\nNot out yet.\n"


def _sched() -> schedule.Schedule:
    return schedule.parse(load_yaml_lines(SCHEDULE))


def _facts() -> SemesterFacts:
    facts = SemesterFacts(org=SEMESTER, sched=_sched())
    facts.listing = {
        name: repo_row(name)
        for name in (
            "semester-config",
            "materials",
            f"{SEMESTER}.github.io",
            # handed out: the semester-side template exists
            "assignment-1",
            "assignment-1-octocat",
        )
    }
    facts.listing["assignment-1"] = repo_row(
        "assignment-1", topics=["assignment-template"]
    )
    facts.students = [
        roster.Student(
            "jane.doe@hertie-school.org", "Jane Doe", "octocat", "", "dsl-ab3k9m"
        )
    ]
    facts.teams = {"assignment-2-project": {"red-team": ["octocat", "hubot"]}}
    group = dataclasses.replace(
        grades.GradingSpec(),
        type="group",
        team_formation="self_select",
        max_team_size=3,
        questions={"Q1": "4", "Q2": "6"},
    )
    facts.specs = {
        "assignment-1": dataclasses.replace(grades.GradingSpec(), title=""),
        "assignment-2-project": group,
        "assignment-3": grades.GradingSpec(),
    }
    facts.dest_paths = {
        "materials": {
            "lectures/01_intro/slides.pdf",
            "lectures/01_intro/code/a.py",
            "lectures/01_intro/code/b.py",
            "lectures/01_intro/solution/answers.py",
            "readings/01_intro/READINGS.md",
            "readings/01_intro/paper.pdf",
            "SYLLABUS.md",
        }
    }
    facts.site_home = "---\nlayout: home\n---\n# {{ site.course_name }}\n\nWelcome.\n"
    return facts


def _extra(facts: SemesterFacts) -> student_status.StudentFacts:
    return student_status.StudentFacts(
        handed_out=frozenset({"assignment-1"}),
        readmes={
            "assignment-1": README_1,
            "assignment-2-project": "# Project\n\nBuild it.",
        },
        caps={"assignment-2-project": 3},
        overlays={
            ("materials", "readings/01_intro/READINGS.md"): "# Read\n\nChapter 1."
        },
        syllabus=("materials", "SYLLABUS.md"),
        cards=student_status.people_cards(INSTRUCTORS, semester=True),
        home=facts.site_home or "",
        announcements=[{"when": "2026-09-20", "title": "Room change", "details": "B1"}],
    )


def _course() -> CourseFacts:
    return CourseFacts(org=COURSE, meta={"course_name": "Deep Learning"})


def _render(now: datetime = NOW) -> dict:
    facts = _facts()
    return student_status.render(_course(), facts, _extra(facts), now)


def _keys(value: object, where: str = "") -> set[str]:
    """Every key path in `value`, list positions folded (`rows[].links[].url`)."""
    out: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{where}.{k}" if where else k
            out.add(path)
            if where == "kinds":
                out |= {f"kinds.*.{kk}" for kk in v}
                continue
            out |= _keys(v, path)
    elif isinstance(value, list):
        for item in value:
            out |= _keys(item, f"{where}[]")
    return out


ALLOWED = (
    set(student_status.TOP_KEYS)
    | {f"rows[].{k}" for k in student_status.ROW_KEYS}
    | {f"rows[].links[].{k}" for k in student_status.LINK_KEYS}
    | {f"rows[].readings[].{k}" for k in student_status.LINK_KEYS}
    | {f"assignments[].{k}" for k in student_status.ASSIGNMENT_KEYS}
    | {f"assignments[].team_formation.{k}" for k in student_status.TEAM_FORMATION_KEYS}
    | {f"assignments[].teams[].{k}" for k in student_status.TEAM_KEYS}
    | {f"instructors[].{k}" for k in student_status.INSTRUCTOR_KEYS}
    | {f"announcements[].{k}" for k in student_status.ANNOUNCEMENT_KEYS}
    | {f"syllabus.{k}" for k in student_status.SYLLABUS_KEYS}
    | {f"materials_index[].{k}" for k in student_status.INDEX_KEYS}
    | {f"kinds.*.{k}" for k in student_status.KIND_KEYS}
    | {f"kinds.{k['key']}" for k in student_status.policy.kinds()}
)


# ---------------------------------------------------------------- the allow-list


def test_every_key_at_every_level_is_on_the_allow_list():
    doc = _render()
    assert _keys(doc) - ALLOWED == set()


def test_the_exported_schema_is_closed_at_every_level_and_the_render_fits_it():
    doc = _render()
    schema = student_status.json_schema()
    assert validate(doc, schema) == []
    for extra in (
        {"roster": []},
        {"rows": [{**doc["rows"][0], "handle": "x"}]},
        {"assignments": [{**doc["assignments"][0], "members": ["x"]}]},
        {"instructors": [{**doc["instructors"][0], "github_handle": "x"}]},
    ):
        assert validate({**doc, **extra}, schema), extra


def test_no_personal_datum_of_the_semester_reaches_the_file():
    text = student_status.dumps(_render()).decode()
    for needle in PII:
        assert needle not in text


def test_a_team_is_its_name_its_headcount_and_its_cap():
    (project,) = [
        a for a in _render()["assignments"] if a["slug"] == "assignment-2-project"
    ]
    assert project["teams"] == [{"name": "red-team", "members": 2, "cap": 3}]
    assert project["team_formation"]["max_team_size"] == 3
    assert project["group"] is True


def test_an_email_only_where_the_instructor_chose_to_show_it():
    cards = _render()["instructors"]
    assert [(c["name"], c["role"], c["email"]) for c in cards] == [
        ("Prof X", "instructor", "shown@hertie-school.org"),
        ("Tee A", "teaching_assistant", ""),
    ]
    # A site-hosted photo resolves against the semester's site.
    assert cards[0]["picture"] == f"https://{SEMESTER}.github.io/_images/pp/x.jpg"


def test_the_brief_its_name_and_the_shape_note_wait_for_the_hand_out():
    by = {a["slug"]: a for a in _render()["assignments"]}
    out, pending = by["assignment-1"], by["assignment-3"]
    assert out["handed_out"] and out["brief"] == "Fit a line."
    assert out["subtitle"] == "Linear regression"
    assert out["shape_note"] and out["cutoff_sentence"] and out["late_rule"]
    assert not pending["handed_out"]
    assert pending["brief"] == pending["subtitle"] == pending["shape_note"] == ""
    assert "Secret heading" not in student_status.dumps(_render()).decode()


def test_a_hidden_assignment_is_on_no_public_surface():
    doc = _render()
    assert "assignment-4" not in {a["slug"] for a in doc["assignments"]}
    assert not [r for r in doc["rows"] if r["assignment"] == "assignment-4"]


def test_the_engine_says_the_cutoff_and_the_timezone():
    (a1,) = [a for a in _render()["assignments"] if a["slug"] == "assignment-1"]
    assert a1["grading_cutoff_datetime"].startswith("2026-10-07T23:59")
    assert _render()["timezone"] == "Europe/Berlin"


def test_the_archive_date_is_there_whatever_show_on_site_says():
    doc = _render()
    assert doc["archive_datetime"] == "2027-01-31"
    assert "semester-archived" not in {r["id"] for r in doc["rows"]}


# ---------------------------------------------------------------- rows


def test_a_released_row_links_its_files_and_carries_its_readings():
    rows = {r["id"]: r for r in _render()["rows"]}
    lec = rows["lecture_01"]
    assert (lec["kind"], lec["number"], lec["title"], lec["subtitle"]) == (
        "lecture",
        1,
        "Lecture 1",
        "Intro",
    )
    names = [link["name"] for link in lec["links"]]
    assert names == ["slides.pdf", "code/ (2 files)", "paper.pdf"]
    # the denylisted solution never shows; the reading list is prose, not a link
    assert "answers.py" not in str(lec)
    assert [r["path"] for r in lec["readings"]] == ["readings/01_intro/paper.pdf"]
    assert lec["reading_list"] == "### Read\n\nChapter 1."
    assert lec["released"] and not lec["readings_pending"]
    assert lec["links"][0]["url"] == (
        f"https://github.com/{SEMESTER}/materials/blob/HEAD/lectures/01_intro/slides.pdf"
    )


def test_an_unreleased_row_is_on_the_calendar_with_nothing_to_open():
    rows = {r["id"]: r for r in _render()["rows"]}
    assert rows["lecture_02"]["released"] is False
    assert rows["lecture_02"]["links"] == []
    assert rows["lecture_02"]["number"] == 2


def test_hand_out_due_exam_and_term_rows():
    rows = {r["id"]: r for r in _render()["rows"]}
    assert rows["assignment-1:handout"]["kind"] == "assignment"
    assert rows["assignment-1:due"]["assignment"] == "assignment-1"
    assert rows["midterm"]["kind"] == "exam"
    assert rows["term-start"]["all_day"] and rows["term-start"]["kind"] == "term_date"
    ids = [r["id"] for r in _render()["rows"]]
    assert ids.index("term-start") < ids.index("lecture_01") < ids.index("term-end")


def test_kinds_come_from_the_policy():
    kinds = _render()["kinds"]
    assert kinds["lecture"]["label"] == "Lecture"
    assert set(kinds["lecture"]) == {"label", "colour", "background"}


def test_home_text_fills_the_course_keys_and_drops_other_liquid():
    doc = _render()
    assert doc["home_markdown"] == "# Deep Learning\n\nWelcome."
    assert (
        student_status.home_markdown(
            "{% if site.course_code %}Code {{ site.course_code }}{% endif %}{% raw %}x",
            {},
        )
        == "x"
    )


def test_materials_index_and_syllabus():
    doc = _render()
    assert doc["materials_repos"] == ["materials"]
    (index,) = doc["materials_index"]
    assert "lectures/01_intro/solution/answers.py" not in index["paths"]
    assert "SYLLABUS.md" in index["paths"]
    assert doc["syllabus"] == {"repo": "materials", "path": "SYLLABUS.md"}


def test_the_schema_is_exported_with_the_others():
    exported = schemas.all_schemas()["student-status.schema.json"]
    assert exported["properties"] == student_status.json_schema()["properties"]
    assert exported["additionalProperties"] is False


# ---------------------------------------------------------------- gather


def test_gather_reads_briefs_only_for_what_is_out(monkeypatch):
    reads: list[tuple] = []

    def content(org, repo, path, **_):
        reads.append((org, repo, path))
        return {
            "README.md": README_1,
            "readings/01_intro/READINGS.md": "prose",
            "_announcements/2026-09-20-room.md": "---\ndate: 2026-09-20\ntitle: Room\n---\nB1\n",
        }.get(path, "")

    monkeypatch.setattr(student_status, "get_file_content", content)
    monkeypatch.setattr(
        student_status,
        "repo_tree",
        lambda *a, **k: ("_announcements/2026-09-20-room.md", "index.md"),
    )
    monkeypatch.setattr(student_status, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(student_status, "yaml_file", lambda *a: INSTRUCTORS)
    monkeypatch.setattr(student_status, "read_materials", lambda org, repo: Declared())
    monkeypatch.setattr(student_status.grades, "team_cap", lambda *a, **k: 3)
    facts = _facts()
    extra = student_status.gather(_course(), facts, NOW)
    readme_reads = [r for r in reads if r[2] == "README.md"]
    # assignment-1 is out; assignment-2-project's hand-out has passed; 3 is to come
    assert sorted(r[1] for r in readme_reads) == [
        "assignment-1-f2026",
        "assignment-2-project-f2026",
    ]
    assert extra.announcements == [
        {"when": "2026-09-20", "title": "Room", "details": "B1"}
    ]
    assert extra.overlays == {("materials", "readings/01_intro/READINGS.md"): "prose"}
    assert extra.syllabus == ("materials", "SYLLABUS.md")
    assert extra.caps == {"assignment-2-project": 3}


@pytest.mark.parametrize("points,expected", [("10", 10), ("7.5", 7.5), ("", None)])
def test_points_are_numbers(points, expected):
    assert student_status._number(points) == expected
