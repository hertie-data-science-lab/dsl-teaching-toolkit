"""roster pure core -- students.csv (faculty's) joined on `email` with `.system/roster.csv`
(the engine's), decision 0039 rule 4. The `role` column splits the semester into full
participants and read-only auditors; blank = enrolled. Getting this wrong either locks a
student out or hands an auditor an assignment repo. No network here.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dsl_course import faults, roster
from tests.conftest import ROSTER_HEADER, roster_files

HEADER = "email,name,role"
SYSTEM_HEADER = ",".join(roster.SYSTEM_FIELDS)


def test_role_defaults_to_enrolled_when_the_column_is_absent():
    # extra faculty columns (`student_id`, `section`) are theirs and never tripped over.
    (student,) = roster.parse("student_id,email,name,section\n1,ada@uni.edu,Ada,A\n")
    assert student.role == roster.ROLE_ENROLLED
    assert student.is_enrolled and not student.is_auditor


def test_blank_role_cell_is_enrolled():
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,\n")
    assert student.role == roster.ROLE_ENROLLED and student.is_enrolled


def test_auditor_role_is_recognised_case_and_space_insensitively():
    (student,) = roster.parse(f"{HEADER}\neve@uni.edu,Eve, Auditor \n")
    assert student.role == roster.ROLE_AUDITOR
    assert student.is_auditor and not student.is_enrolled


def test_unknown_role_falls_back_to_enrolled_and_warns(capsys):
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,guest\n")
    assert student.role == roster.ROLE_ENROLLED
    assert "row 2: unrecognised role" in capsys.readouterr().err


def test_a_shifted_role_cell_is_never_echoed_to_the_log(capsys):
    # an unquoted comma in a name shifts the cells, so `role` holds part of the name. The
    # warning goes to PUBLIC run logs: the row number and the allowed values, never the cell.
    roster.parse(f"{HEADER}\nada@uni.edu,Lovelace, Ada\n")
    err = capsys.readouterr().err
    assert "row 2" in err and "expected enrolled or auditor" in err
    assert "Ada" not in err and "ada" not in err


def test_the_engine_columns_are_joined_on_the_email_casefolded():
    system = f"{SYSTEM_HEADER}\nADA@uni.edu,ada-l,42,dsl-abc,2026-09-01\n"
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,\n", system)
    assert (student.github_handle, student.github_id, student.enrol_code) == (
        "ada-l",
        "42",
        "dsl-abc",
    )
    assert student.onboarded and student.code_sent_at == "2026-09-01"


def test_no_roster_file_is_nobody_onboarded_yet():
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,\n", None)
    assert not student.onboarded and not student.enrol_code


def test_team_cells_are_read_by_column_suffix_casefolded():
    (student,) = roster.parse(
        "email,name,role,team:4,team:6\nada@uni.edu,Ada,,Geneva,\n"
    )
    assert student.teams == {"4": "geneva"}


def test_parse_tolerates_a_utf8_bom_from_excel():
    (student,) = roster.parse("﻿" + f"{HEADER}\nada@uni.edu,Ada,enrolled\n")
    assert student.email == "ada@uni.edu" and student.is_enrolled


def test_enrolled_and_auditors_partition_the_roster():
    students = roster.parse(
        f"{HEADER}\nada@uni.edu,Ada,enrolled\neve@uni.edu,Eve,auditor\nbob@uni.edu,Bob,\n"
    )
    assert [s.name for s in roster.enrolled(students)] == ["Ada", "Bob"]
    assert [s.name for s in students if s.is_auditor] == ["Eve"]


def test_by_email_and_by_handle_resolve_one_student():
    students = roster.parse(
        f"{HEADER}\nada@uni.edu,Ada,\nbob@uni.edu,Bob,\n",
        f"{SYSTEM_HEADER}\nada@uni.edu,Ada-L,42,,\n",
    )
    assert roster.by_email(students)["ADA@uni.edu".casefold()].name == "Ada"
    assert roster.by_handle(students)["ada-l"].email == "ada@uni.edu"
    assert "bob" not in str(roster.by_handle(students))


def test_example_dataset_roster_declares_roles_and_ships_an_auditor():
    path = (
        Path(__file__).resolve().parents[1]
        / "example-course"
        / "semester-org"
        / "students.csv"
    )
    students = roster.load_path(str(path))
    assert [s.name for s in students if s.is_auditor] == ["Eve Evans"]
    assert len(roster.enrolled(students)) >= 10
    rows = list(csv.DictReader(path.read_text().splitlines()))
    assert any(r["role"] == "" for r in rows)
    # the three project teams of assignment 4, in its team column
    assert {s.teams.get("4") for s in students} == {
        "team-alpha",
        "team-beta",
        "team-gamma",
        None,
    }


def test_a_semicolon_delimited_roster_is_refused_not_read_as_empty():
    with pytest.raises(RuntimeError, match="semicolon"):
        roster.parse("email;name;role\na@x;A;\n")


def test_the_roster_columns():
    # The seeded scaffold, the worked sample, the docs and onboard.yml all quote these.
    assert roster.FIELDS == ("email", "name", "role")
    assert roster.SYSTEM_FIELDS == (
        "email",
        "github_handle",
        "github_id",
        "enrol_code",
        "code_sent_at",
    )
    assert roster.SYSTEM_PATH == ".system/roster.csv"


# ------------------------------------------------------------- faults a human must fix


def _faults(text: str, system: str | None = None) -> list:
    found = []
    roster.parse(text, system, found)
    return found


def test_an_unreadable_header_is_recorded_as_well_as_raised():
    found = []
    with pytest.raises(RuntimeError, match="semicolon"):
        roster.parse("email;name\na@x;A\n", None, found)
    (fault,) = found
    assert fault.file == "students.csv"
    assert fault.lineno == 1 and fault.where == "header"
    assert "email" in fault.field
    assert fault.fix() == faults.CSV_HEADER_FIX


def test_the_header_fault_never_names_what_it_found():
    found = []
    with pytest.raises(RuntimeError):
        roster.parse("ada@uni.edu,Ada Lovelace,ada-l\n", None, found)
    assert "ada@uni.edu" not in found[0].what and "Ada" not in found[0].what


def test_an_unrecognised_role_is_a_fault_on_its_own_row():
    (fault,) = _faults(f"{HEADER}\nada@uni.edu,Ada,audit\n")
    assert fault.where == "row 2" and fault.field == "role" and fault.lineno == 2
    assert fault.fix() == "fix row 2 of students.csv"
    assert "audit" not in fault.what.replace("auditor", "")


def test_a_duplicate_address_and_a_duplicate_handle_are_both_found():
    text = f"{HEADER}\nada@uni.edu,Ada,\neve@uni.edu,Eve,\nADA@uni.edu,Ada Again,\n"
    system = f"{SYSTEM_HEADER}\nada@uni.edu,ada-l,42,,\neve@uni.edu,ADA-L,43,,\n"
    found = _faults(text, system)
    assert sorted((f.file, f.lineno, f.field) for f in found) == [
        (roster.SYSTEM_PATH, 3, "github_handle"),
        ("students.csv", 4, "email"),
    ]
    assert all("row 2" in f.what for f in found)


def test_a_duplicate_enrol_code_is_found_without_naming_it():
    system = (
        f"{SYSTEM_HEADER}\nada@uni.edu,,,dsl-abc234,\neve@uni.edu,,,DSL-ABC234,\n"
        "bob@uni.edu,,,dsl-zzz999,\n"
    )
    text = f"{HEADER}\nada@uni.edu,Ada,\neve@uni.edu,Eve,\nbob@uni.edu,Bob,\n"
    (fault,) = _faults(text, system)
    assert (fault.lineno, fault.field) == (3, "enrol_code")
    assert "row 2" in fault.what and "abc234" not in fault.what.casefold()
    assert roster.shared_codes(roster.parse(text, system)) == {"dsl-abc234"}


def test_a_row_fault_never_carries_a_cell():
    (fault,) = _faults(f"{HEADER}\nada@uni.edu,Ada Lovelace,audit\n")
    for private in ("ada@uni.edu", "Ada Lovelace"):
        assert private not in fault.what and private not in fault.fix()


def test_a_clean_roster_has_no_faults():
    assert _faults(f"{HEADER}\nada@uni.edu,Ada,\n") == []


def test_an_absent_roster_is_recorded_as_a_fault_not_just_logged(monkeypatch):
    monkeypatch.setattr(roster, "_students_text", lambda org: None)
    found: list = []
    assert roster.load("Semester-f2026", found) is None
    (fault,) = found
    assert fault.file == roster.STUDENTS_PATH and fault.lineno is None
    assert "missing" in fault.what and "no student is enrolled" in fault.what


def test_a_caller_that_wants_no_faults_still_just_gets_none(monkeypatch):
    monkeypatch.setattr(roster, "_students_text", lambda org: None)
    assert roster.load("Semester-f2026") is None


# ------------------------------------------------------------- writing roster.csv


def test_fill_system_appends_a_row_and_never_overwrites_a_held_value():
    text = roster.fill_system(None, "enrol_code", {"ada@uni.edu": "dsl-aaa"})
    text = roster.fill_system(
        text, "enrol_code", {"ADA@uni.edu": "dsl-bbb", "bob@uni.edu": "dsl-ccc"}
    )
    rows = list(csv.DictReader(text.splitlines()))
    assert [(r["email"], r["enrol_code"]) for r in rows] == [
        ("ada@uni.edu", "dsl-aaa"),
        ("bob@uni.edu", "dsl-ccc"),
    ]


def test_fill_system_replaces_only_its_own_value_and_adds_no_row_for_one():
    text = f"{SYSTEM_HEADER}\nada@uni.edu,,,dsl-a,STAMP\n"
    out = roster.fill_system(
        text, "code_sent_at", {"ada@uni.edu": "", "bob@uni.edu": ""}, replacing="STAMP"
    )
    rows = list(csv.DictReader(out.splitlines()))
    assert [(r["email"], r["code_sent_at"]) for r in rows] == [("ada@uni.edu", "")]


# ------------------------------------------------------------- the 0.11.0 migration


def test_split_legacy_moves_the_engine_columns_and_folds_teams_csv():
    old = (
        "hertie_email,name,role,github_handle,github_id,enrol_code,code_sent_at,notes\n"
        'ada@uni.edu,"Doe, Ada",,ada-l,1,dsl-aaaaaa,2026-01-01,n1\n'
        "eve@uni.edu,Eve,auditor,,,dsl-bbbbbb,,\n"
        "bob@uni.edu,Bob,,bob-b,3,,,\n"
        "zoe@uni.edu,Zoe,,,,,,\n"
    )
    teams = (
        "assignment,team,github_handle\n"
        "Assignment-4,Alpha,ADA-L\n"
        "assignment-4,beta,bob-b\n"
        "project,x,bob-b\n"
        "bonus,y,bob-b\n"
        "assignment-4,ghost,nobody\n"
    )
    students, system = roster.split_legacy(
        old, teams, {"assignment-4": 4, "project": 6}
    )
    rows = list(csv.DictReader(students.splitlines()))
    assert list(rows[0]) == [
        "email",
        "name",
        "role",
        "notes",
        "team:4",
        "team:6",
        "team:bonus",
    ]
    assert rows[0] == {
        "email": "ada@uni.edu",
        "name": "Doe, Ada",
        "role": "",
        "notes": "n1",
        "team:4": "alpha",
        "team:6": "",
        "team:bonus": "",
    }
    assert (rows[2]["team:4"], rows[2]["team:6"], rows[2]["team:bonus"]) == (
        "beta",
        "x",
        "y",
    )
    sys_rows = list(csv.DictReader(system.splitlines()))
    assert [r["email"] for r in sys_rows] == [
        "ada@uni.edu",
        "eve@uni.edu",
        "bob@uni.edu",
    ]
    assert sys_rows[0] == {
        "email": "ada@uni.edu",
        "github_handle": "ada-l",
        "github_id": "1",
        "enrol_code": "dsl-aaaaaa",
        "code_sent_at": "2026-01-01",
    }
    # and the two files read back as the students they were
    parsed = roster.parse(students, system)
    assert [s.github_handle for s in parsed] == ["ada-l", "", "bob-b", ""]
    assert parsed[0].teams == {"4": "alpha"}


def test_split_legacy_without_a_teams_csv():
    files = roster_files(f"{ROSTER_HEADER}\nada@uni.edu,Ada,,ada-l,42,dsl-abc\n")
    assert files[roster.STUDENTS_PATH].splitlines()[0] == "email,name,role"
