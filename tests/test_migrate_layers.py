"""The 0.11.0 migration's config-layers fold (decision 0039), on copies of the live orgs'
files: the demo, NLP and Maths courses and their running semesters. The copies are
`tests/fixtures/config-layers/<org>/` (staff addresses replaced; no roster: the roster
here is three made-up rows). Each fold must stop on nothing, lose nothing, and read clean
with the engine's own readers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dsl_course import migrate, opencourse, roster, settings, sync_faculty

FIXTURES = Path(__file__).parent / "fixtures" / "config-layers"
COURSES = {
    "hertie-dsl-demo-course-e1234": "hertie-dsl-demo-f2026",
    "hertie-maths-data-science-C23": "hertie-maths-data-science-f2026",
    "hertie-nlp-e1282": "hertie-nlp-f2026",
}


def _read(org: str, name: str) -> str | None:
    path = FIXTURES / org / name
    return path.read_text(encoding="utf-8") if path.exists() else None


def _course(org: str) -> tuple[str | None, str | None, str]:
    """The course's old files as the keys step leaves them, and the course.yml folded
    out of them."""
    meta = migrate.course_config_keys(_read(org, "dsl-course.yml"))
    oc = _read(org, "opencourse.yml")
    new, _notes, stops = migrate.course_layer(meta, oc)
    assert stops == [], stops
    return meta, oc, new


@pytest.mark.parametrize("org", sorted(COURSES))
def test_each_live_course_folds_into_course_yml_and_loses_nothing(org):
    meta, oc, new = _course(org)
    assert migrate.course_layer_kept(meta, oc, new) == []
    parsed = yaml.safe_load(new)
    old = yaml.safe_load(meta)
    assert parsed["course"]["name"] == old["course_name"]
    assert parsed["course"]["code"] == old["course_code"]
    assert parsed["people"]["course_admins"] == old["people"]["course_admins"]
    # Through the engine's readers: the defaults, the open site.
    settings.parse_course_defaults(parsed["defaults"])
    opencourse.parse(opencourse.block(parsed))
    assert settings.parse_course_defaults(parsed["defaults"]) == {
        k: v
        for k, v in settings.parse_course_defaults(
            old.get("assignment_defaults")
        ).items()
    }


def test_the_demo_course_keeps_its_tier_and_console_and_drops_its_cards():
    meta, oc, new = _course("hertie-dsl-demo-course-e1234")
    parsed = yaml.safe_load(new)
    assert parsed["course"]["toolkit_version_ref"] == "main"
    assert parsed["course"]["student_console"] is True
    assert parsed["open_course_site"]["enabled"] is True
    assert parsed["defaults"]["max_team_size"] == 4
    # Decision 0039 rule 3: no course-level instructors or TAs; the plan says so.
    assert "instructors" not in parsed["people"]
    _, notes, _ = migrate.course_layer(meta, oc)
    assert any("course-level `people.instructors`" in n for n in notes)
    assert any("course-level `people.teaching_assistants`" in n for n in notes)


def test_the_maths_course_keeps_its_set_aside_list():
    meta, _oc, new = _course("hertie-maths-data-science-C23")
    assert yaml.safe_load(new)["set_aside"] == yaml.safe_load(meta)["set_aside"]


def test_a_course_key_with_no_new_home_stops_the_fold():
    _new, _notes, stops = migrate.course_layer("course_name: X\nmystery: 1\n", None)
    assert stops and "`mystery:` has no place in course.yml" in stops[0]


@pytest.mark.parametrize(("course", "semester"), sorted(COURSES.items()))
def test_each_live_semester_folds_into_semester_yml_and_loses_nothing(course, semester):
    _, _, course_text = _course(course)
    instructors = _read(semester, "instructors.yml")
    # As the schedule shape step leaves it: the per-assignment blocks are on their entries.
    assignments = _read(semester, "assignments.yml")
    assignments = migrate._without_block(assignments, "assignments") or assignments
    inherited = [
        (
            "course",
            settings.parse_course_defaults(yaml.safe_load(course_text)["defaults"]),
        ),
        ("institution", settings.institution_defaults()),
    ]
    carried = migrate.carried_defaults(
        inherited[0][1], settings.parse_legacy_assignments(assignments).defaults
    )
    new, stops = migrate.semester_layer(
        semester, "release", instructors, assignments, "", inherited, carried
    )
    assert stops == [], stops
    assert migrate.semester_layer_kept(instructors, assignments, "", new, carried) == []
    read = settings.parse_instance(new)
    assert read.faults == ()
    found: list = []
    faculty = sync_faculty.parse_faculty_from_meta(yaml.safe_load(new), found)
    old = yaml.safe_load(instructors)["instructors"]
    # Everyone with a handle is granted access, exactly as from instructors.yml.
    assert sum(len(v) for v in faculty.values()) == len(
        [p for p in old if p.get("github_handle")]
    )
    # Faculty's own lines are kept as written.
    for line in instructors.split("\n"):
        if line.startswith("    ") and line.strip() and "github_handle" in line:
            assert line in new


def test_the_demo_semester_states_its_own_defaults_live_and_the_rest_commented():
    _, _, course_text = _course("hertie-dsl-demo-course-e1234")
    assignments = migrate._without_block(
        _read("hertie-dsl-demo-f2026", "assignments.yml"), "assignments"
    )
    inherited = [
        (
            "course",
            settings.parse_course_defaults(yaml.safe_load(course_text)["defaults"]),
        ),
        ("institution", settings.institution_defaults()),
    ]
    new, _ = migrate.semester_layer(
        "hertie-dsl-demo-f2026",
        "release",
        _read("hertie-dsl-demo-f2026", "instructors.yml"),
        assignments,
        "SYLLABUS.pdf",
        inherited,
    )
    defaults = yaml.safe_load(new)["defaults"]
    assert defaults == {"max_team_size": 3, "assignment_repo_visibility": "private"}
    # Inherited keys are comments naming the value and its layer (decision 0039 rule 2).
    assert "# late_window_days: 10" in new and "(course)" in new
    assert yaml.safe_load(new)["student_site"] == {"syllabus": "SYLLABUS.pdf"}


def test_per_assignment_blocks_left_in_assignments_yml_stop_the_fold():
    _, stops = migrate.semester_layer(
        "x-f2026",
        "release",
        None,
        "assignments:\n  a1:\n    max_team_size: 3\n",
        "",
        [("institution", settings.institution_defaults())],
    )
    assert stops and "schedule shape step" in stops[0]


# A made-up roster in the 0.10 shape: never a real one.
OLD_STUDENTS = (
    "hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at\n"
    "ana@example.org,Ana Lopez,enrolled,ana-l,101,dsl-aaaaaa,2026-08-20T09:00:00Z\n"
    "ben@example.org,Ben Okafor,enrolled,benok,102,dsl-bbbbbb,2026-08-20T09:00:00Z\n"
    "cara@example.org,Cara Ng,auditor,,,dsl-cccccc,\n"
)
OLD_TEAMS = (
    "assignment,team,github_handle\nproject,geneva,ana-l\nproject,geneva,benok\n"
)


def test_a_three_row_roster_splits_and_keeps_every_value():
    cols = {"project": "4"}
    students, system = roster.split_legacy(OLD_STUDENTS, OLD_TEAMS, cols)
    assert migrate.roster_kept(OLD_STUDENTS, OLD_TEAMS, students, system, cols) == []
    assert students.splitlines()[0] == "email,name,role,team:4"
    assert system.splitlines()[0] == ",".join(roster.SYSTEM_FIELDS)
    faults: list = []
    parsed = roster.parse(students, faults)
    assert faults == [] and len(parsed) == 3


def test_a_roster_value_the_split_would_lose_is_counted_never_quoted():
    cols = {"project": "4"}
    students, system = roster.split_legacy(OLD_STUDENTS, OLD_TEAMS, cols)
    lost = migrate.roster_kept(
        OLD_STUDENTS, OLD_TEAMS, students.replace("Ana Lopez", ""), system, cols
    )
    assert lost == ["1 roster value(s) would not survive the split"]
    assert "Ana" not in lost[0]


@pytest.mark.parametrize(
    ("course", "semester"),
    [
        ("hertie-maths-data-science-C23", "hertie-maths-data-science-f2026"),
        ("hertie-nlp-e1282", "hertie-nlp-f2026"),
    ],
)
def test_a_semester_that_ran_on_the_institutions_team_formation_keeps_it(
    course, semester
):
    # Neither the course nor the semester states `team_formation`: 0.10 answered
    # `self_select` from the institution, which 0.11 no longer does (absent everywhere =
    # no team, decision 0039). Maths' assignment-2 forms teams on it, so the fold writes
    # it into the semester's own defaults, live.
    _, _, course_text = _course(course)
    course_defaults = settings.parse_course_defaults(
        yaml.safe_load(course_text)["defaults"]
    )
    assignments = _read(semester, "assignments.yml")
    assignments = migrate._without_block(assignments, "assignments") or assignments
    old = settings.parse_legacy_assignments(assignments).defaults
    carried = migrate.carried_defaults(course_defaults, old)
    assert carried == {"team_formation": "self_select"}
    inherited = [
        ("course", course_defaults),
        ("institution", settings.institution_defaults()),
    ]
    new, stops = migrate.semester_layer(
        semester,
        "release",
        _read(semester, "instructors.yml"),
        assignments,
        "",
        inherited,
        carried,
    )
    assert stops == []
    assert yaml.safe_load(new)["defaults"]["team_formation"] == "self_select"
    # ... and the cascade answers it from the semester, as 0.10 did from the institution.
    read = settings.parse_instance(new)
    stack = [("semester", read.defaults), *inherited]
    assert settings.resolve("team_formation", stack) == ("self_select", "semester")


def test_nothing_is_carried_where_the_course_or_semester_states_a_formation():
    assert migrate.carried_defaults({"team_formation": "assigned"}, {}) == {}
    assert migrate.carried_defaults({}, {"team_formation": "assigned"}) == {}


def test_a_materials_yml_syllabus_folds_into_the_semesters_student_site(monkeypatch):
    # No live org declares one (ruling of 14 Oct 2026): a synthetic copy. The 0.10 file
    # is read once, by the migration; its kinds feed only the schedule-shape inference.
    files = {
        "materials.yml": "syllabus: /E1282_syllabus.pdf\nkinds:\n  Tutorials: lab\n"
    }
    monkeypatch.setattr(
        migrate, "get_file_content", lambda org, repo, path: files.get(path)
    )
    syllabus, kinds = migrate.old_materials("C", "course-materials-f2026")
    assert (syllabus, kinds) == ("E1282_syllabus.pdf", {"tutorials": "lab"})
    inherited = [("institution", settings.institution_defaults())]
    new, stops = migrate.semester_layer(
        "x-f2026", "release", None, None, syllabus, inherited
    )
    assert stops == []
    assert yaml.safe_load(new)["student_site"] == {"syllabus": "E1282_syllabus.pdf"}
    assert migrate.semester_layer_kept(None, None, syllabus, new) == []
    files.clear()
    assert migrate.old_materials("C", "course-materials-f2026") == ("", {})


# ------------------------------------------------- decision 0040: the selection folds


def test_publish_yml_folds_into_the_semesters_host_exactly():
    # `public:` hosted only what it named: `kinds: []`, its patterns (and `!` lines) as
    # `include:`. No file anywhere = nothing written: the default applies.
    texts = {
        "course-materials-f2026": 'public:\n  - "lectures/**/*.html"\n  - "!lectures/09_*/**"\n',
        "course-code": None,
    }
    host, stops = migrate.publish_host(texts)
    assert stops == []
    assert host == {"kinds": [], "include": ["lectures/**/*.html", "!lectures/09_*/**"]}
    assert migrate.publish_host({"m": None}) == (None, [])
    assert migrate.publish_host({"m": "public: lectures\n"})[1]


def test_the_folded_host_lands_in_semester_yml_and_reads_back():
    inherited = [("institution", settings.institution_defaults())]
    host = {"kinds": [], "include": ["lectures/**/*.html"]}
    new, stops = migrate.semester_layer(
        "Org-f2026", "release", None, None, "SYLLABUS.md", inherited, None, host
    )
    assert stops == []
    site = yaml.safe_load(new)["student_site"]
    assert site == {"syllabus": "SYLLABUS.md", "host": host}
    assert migrate.semester_layer_kept(None, None, "SYLLABUS.md", new, None, host) == []
    assert migrate.semester_layer_kept(None, None, "SYLLABUS.md", new, None, None)


@pytest.mark.parametrize(
    ("old", "publish"),
    [
        (
            {"enabled": True, "source_repo": "m"},
            {
                "kinds": ["lectures", "labs", "exams", "drop-ins", "events"],
                "exclude": [],
                "reading_lists": True,
            },
        ),
        (
            {
                "source_repo": "m",
                "readings_mode": "actual-readings",
                "include_lectures": False,
                "withhold": ["labs/"],
            },
            {"kinds": ["readings"], "exclude": ["labs/"], "reading_lists": False},
        ),
        (
            {"source_repo": "m", "readings_mode": "none"},
            {
                "kinds": ["lectures", "labs", "exams", "drop-ins", "events"],
                "exclude": [],
                "reading_lists": False,
            },
        ),
    ],
)
def test_the_old_open_site_keys_fold_into_publish(old, publish):
    folded = migrate.fold_open_site(old)
    assert folded["publish"] == publish
    assert not set(folded) & {"readings_mode", "include_lectures", "withhold"}
    opencourse.parse(folded)  # reads clean
    # Already folded: left alone.
    assert migrate.fold_open_site(folded) == folded
