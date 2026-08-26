"""roster pure core -- the `role` column splits the cohort into full participants and
read-only auditors, and it is the newest column, so the parse must stay tolerant: rosters
seeded before it existed have no `role` cell at all and must keep working (blank =
enrolled). Getting this wrong either locks a student out or hands an auditor an assignment
repo. No network here.
"""

from __future__ import annotations

import csv
from pathlib import Path

from dsl_course import roster

HEADER = "hertie_email,name,github_handle,github_id,enrol_code,role"


def test_role_defaults_to_enrolled_when_the_column_is_absent():
    # a roster written before `role` existed - not one cell to read. It also still
    # carries the retired `student_id`/`section` columns, which the parser must ignore
    # rather than trip over.
    text = (
        "student_id,hertie_email,name,github_handle,github_id,section\n"
        "1,ada@uni.edu,Ada,ada-l,42,A\n"
    )
    (student,) = roster.parse(text)
    assert student.role == roster.ROLE_ENROLLED
    assert student.is_enrolled and not student.is_auditor


def test_blank_role_cell_is_enrolled():
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,ada-l,42,dsl-abc,\n")
    assert student.role == roster.ROLE_ENROLLED and student.is_enrolled


def test_auditor_role_is_recognised_case_and_space_insensitively():
    text = f"{HEADER}\neve@uni.edu,Eve,eve-e,43,dsl-xyz, Auditor \n"
    (student,) = roster.parse(text)
    assert student.role == roster.ROLE_AUDITOR
    assert student.is_auditor and not student.is_enrolled


def test_unknown_role_falls_back_to_enrolled_and_warns(capsys):
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,ada-l,42,dsl-abc,guest\n")
    assert student.role == roster.ROLE_ENROLLED
    assert "guest" in capsys.readouterr().err  # a typo must be visible, not silent


def test_parse_tolerates_a_utf8_bom_from_excel():
    # Excel exports a UTF-8 BOM; left in, csv.DictReader reads the first header as
    # "﻿hertie_email" and every `hertie_email` lookup misses - and that column is the
    # enrolment match key, so every row silently drops out of the roster.
    text = "﻿" + f"{HEADER}\nada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n"
    (student,) = roster.parse(text)
    assert student.hertie_email == "ada@uni.edu"
    assert student.github_handle == "ada-l"
    assert student.is_enrolled


def test_role_survives_a_dump_parse_roundtrip():
    students = roster.parse(
        f"{HEADER}\n"
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n"
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor\n"
    )
    reparsed = roster.parse(roster.dump(students))
    assert [s.role for s in reparsed] == [roster.ROLE_ENROLLED, roster.ROLE_AUDITOR]


def test_dump_writes_an_explicit_role_for_a_roster_that_lacked_the_column():
    # enrol-codes rewrites the whole file, so a legacy roster GAINS the column with the
    # default spelled out - never a blank the next reader has to interpret.
    students = roster.parse(
        "student_id,hertie_email,name,github_handle,github_id,section\n"
        "1,ada@uni.edu,Ada,,,A\n"
    )
    text = roster.dump(students)
    assert text.splitlines()[0].endswith(",role")
    assert text.splitlines()[1].endswith(",enrolled")


def test_enrolled_and_auditors_partition_the_roster():
    students = roster.parse(
        f"{HEADER}\n"
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n"
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor\n"
        "bob@uni.edu,Bob,,,dsl-def,\n"
    )
    assert [s.name for s in roster.enrolled(students)] == ["Ada", "Bob"]
    assert [s.name for s in students if s.is_auditor] == ["Eve"]


def test_example_dataset_roster_declares_roles_and_ships_an_auditor():
    # the shipped demo dataset is what faculty copy - it must show the role column in use
    path = (
        Path(__file__).resolve().parents[1]
        / "example-course"
        / "cohort-org"
        / "students.csv"
    )
    students = roster.load_path(str(path))
    assert [s.name for s in students if s.is_auditor] == ["Eve Evans"]
    # enough enrolled students to fill the dataset's three project teams
    assert len(roster.enrolled(students)) >= 10
    # ...and the raw file still leaves one `role` cell blank, so the dataset demonstrates
    # that the column is optional (normalise_role turns blank into `enrolled` on load,
    # so this can only be checked against the CSV text)
    rows = list(csv.DictReader(path.read_text().splitlines()))
    assert any(r["role"] == "" for r in rows)
