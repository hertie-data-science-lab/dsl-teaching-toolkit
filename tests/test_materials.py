"""materials: the topic that marks a materials repo, the semester's syllabus, and folder
kinds (the console's exported aliases and migrate's 0.10 reading kept)."""

from __future__ import annotations

from dsl_course import materials


def test_the_semester_names_its_syllabus_and_absent_is_none():
    # Decision 0039: `semester.yml` `student_site.syllabus`; no materials.yml is read.
    meta = {"student_site": {"syllabus": "/E1282_syllabus.pdf"}}
    assert materials.semester_syllabus(meta) == "E1282_syllabus.pdf"
    assert materials.semester_syllabus({}) == ""
    assert materials.semester_syllabus(None) == ""
    assert materials.semester_syllabus({"student_site": "x"}) == ""


def test_a_folders_kind_is_the_institutions_and_nothing_else():
    # Ruling of 14 Oct 2026: kinds from institution.yml only - a plural or a key, case
    # aside; any other folder is supporting files, whatever a materials.yml said.
    assert materials.folder_kind("Labs/") == "lab"
    assert materials.folder_kind("lecture") == "lecture"
    assert materials.folder_kind("tutorials") == materials.ASSETS_KIND
    assert materials.folder_kind("data") == materials.ASSETS_KIND


def test_the_topic_makes_a_materials_repo_not_the_name():
    assert materials.is_materials_repo(
        {"name": "nlp-labs", "topics": ["dsl-materials"]}
    )
    assert not materials.is_materials_repo(
        {"name": "course-materials-f2026", "topics": []}
    )


def test_folder_keys_and_aliases_match_whatever_the_case():
    assert materials.infer_kind("Quiz", {"quiz": "exam"}) == "exam"
    assert materials.alias_kind("LABS") == "lab"
    assert materials.alias_kind("code") is None


def test_supporting_files_folders_are_assets_by_name():
    # Decision 0026 rule 3: released, never shown. Decision 0031 rule 10: an unknown name
    # is supporting files too, not a lecture.
    for name in ("data", "img", "images", "src", "assets", "figures", "fig", "Static"):
        assert materials.alias_kind(name) == materials.ASSETS_KIND, name
    assert materials.ASSETS_KIND in materials.policy.content_kinds()
    assert materials.DEFAULT_KIND == materials.ASSETS_KIND
    assert materials.alias_kind("code") is None
    assert materials.infer_kind("code") == materials.ASSETS_KIND
    assert materials.infer_kind("Code", {"code": "lecture"}) == "lecture"
    for name, kind in {
        "lecture": "lecture",
        "Lectures": "lecture",
        "lab": "lab",
        "labs": "lab",
        "tutorials": "lab",
        "reading": "readings",
        "readings": "readings",
        "literature": "readings",
    }.items():
        assert materials.infer_kind(name) == kind, name


def test_a_folder_path_names_its_kind_and_the_repo_declaration_wins():
    # The 0.10 inference migrate replays once (`migrate.old_materials` reads the map).
    # A built-in name one level down names its kind; two levels down it does not.
    assert materials.alias_kind("students/Labs") == "lab"
    assert materials.alias_kind("course/2026/labs") is None
    # A declared path is honoured at any depth.
    assert (
        materials.alias_kind("Course/2026/Labs", {"course/2026/labs": "lab"}) == "lab"
    )
    # The repo's own declaration wins over a built-in name, at every depth.
    assert materials.alias_kind("labs", {"labs": "readings"}) == "readings"
    assert materials.alias_kind("students/labs", {"students/labs": "readings"}) == (
        "readings"
    )
    assert materials.infer_kind("readings", {"readings": "assets"}) == "assets"
