"""roster pure core -- the `role` column splits the cohort into full participants and
read-only auditors, and it is the newest column, so the parse must stay tolerant: rosters
seeded before it existed have no `role` cell at all and must keep working (blank =
enrolled). Getting this wrong either locks a student out or hands an auditor an assignment
repo. No network here.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dsl_course import faults, roster
from tests.conftest import ROSTER_HEADER

HEADER = ROSTER_HEADER


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
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,,ada-l,42,dsl-abc\n")
    assert student.role == roster.ROLE_ENROLLED and student.is_enrolled


def test_auditor_role_is_recognised_case_and_space_insensitively():
    text = f"{HEADER}\neve@uni.edu,Eve, Auditor ,eve-e,43,dsl-xyz\n"
    (student,) = roster.parse(text)
    assert student.role == roster.ROLE_AUDITOR
    assert student.is_auditor and not student.is_enrolled


def test_unknown_role_falls_back_to_enrolled_and_warns(capsys):
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,guest,ada-l,42,dsl-abc\n")
    assert student.role == roster.ROLE_ENROLLED
    assert "guest" in capsys.readouterr().err  # a typo must be visible, not silent


def test_parse_tolerates_a_utf8_bom_from_excel():
    # Excel exports a UTF-8 BOM; left in, csv.DictReader reads the first header as
    # "﻿hertie_email" and every `hertie_email` lookup misses - and that column is the
    # enrolment match key, so every row silently drops out of the roster.
    text = "﻿" + f"{HEADER}\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    (student,) = roster.parse(text)
    assert student.hertie_email == "ada@uni.edu"
    assert student.github_handle == "ada-l"
    assert student.is_enrolled


def test_enrolled_and_auditors_partition_the_roster():
    students = roster.parse(
        f"{HEADER}\n"
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz\n"
        "bob@uni.edu,Bob,,,,dsl-def\n"
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


def test_a_semicolon_delimited_roster_is_refused_not_read_as_empty():
    # German-locale Excel saves `;`-CSV. DictReader then sees one header column and every
    # field reads "" - no error, an empty roster, and enrol_codes once wrote it back mangled
    # with exit 0. A header that cannot name the required columns is a hard error.
    text = "hertie_email;name;github_handle;github_id;enrol_code;role\na@x;A;ada;1;;\n"
    with pytest.raises(RuntimeError, match="semicolon"):
        roster.parse(text)


def test_the_roster_columns_run_instructor_filled_then_system_filled():
    # The order faculty see when they open students.csv: the three columns THEY fill,
    # then the four the engine fills. Every reader and writer here addresses cells by
    # name, so nothing breaks if this drifts - which is exactly why it needs pinning: the
    # seeded scaffold, the worked sample, the docs and onboard.yml's comment all quote
    # this order, and a silent reshuffle leaves faculty filling the wrong cells.
    assert roster.FIELDS == (
        "hertie_email",
        "name",
        "role",
        "github_handle",
        "github_id",
        "enrol_code",
        "code_sent_at",
    )


# ------------------------------------------------------------- faults a human must fix
#
# The roster's faults reach faculty through a digest issue and an email, and every cell of
# this file is personal data - a name, an address, a handle, an enrolment code. So the two
# properties asserted below are that the fault is FOUND, and that what it says is a row
# number and a column name and nothing else.


def _faults(text: str) -> list:
    found = []
    roster.parse(text, found)
    return found


def test_an_unreadable_header_is_recorded_as_well_as_raised():
    text = "hertie_email;name;github_handle\na@x;A;ada\n"
    found = []
    with pytest.raises(RuntimeError, match="semicolon"):
        roster.parse(text, found)
    (fault,) = found
    assert fault.file == "students.csv"
    assert fault.lineno == 1 and fault.where == "header"
    assert "hertie_email" in fault.field and "github_handle" in fault.field
    assert fault.fix() == faults.CSV_HEADER_FIX


def test_the_header_fault_never_names_what_it_found():
    """A students.csv whose header row was deleted has a NAME and an ADDRESS where the
    column names should be, and this text goes to an email and an issue."""
    text = "ada@uni.edu,Ada Lovelace,ada-l\n"
    found = []
    with pytest.raises(RuntimeError):
        roster.parse(text, found)
    assert "ada@uni.edu" not in found[0].what and "Ada" not in found[0].what


def test_an_unrecognised_role_is_a_fault_on_its_own_row():
    (fault,) = _faults(f"{HEADER}\nada@uni.edu,Ada,audit,ada-l,42,,\n")
    assert fault.where == "row 2" and fault.field == "role"
    assert fault.lineno == 2
    assert fault.fix() == "fix row 2 of students.csv"
    assert "audit" not in fault.what.replace("auditor", "")


def test_a_duplicate_handle_and_a_duplicate_address_are_both_found():
    text = (
        f"{HEADER}\n"
        "ada@uni.edu,Ada,,ada-l,42,,\n"
        "eve@uni.edu,Eve,,ADA-L,43,,\n"
        "ada@uni.edu,Ada Again,,zoe-z,44,,\n"
    )
    found = _faults(text)
    assert [(f.lineno, f.field) for f in found] == [
        (3, "github_handle"),
        (4, "hertie_email"),
    ]
    assert all("row 2" in f.what for f in found)


def test_a_row_fault_never_carries_a_cell():
    text = f"{HEADER}\nada@uni.edu,Ada Lovelace,audit,ada-l,42,dsl-secret,\n"
    (fault,) = _faults(text)
    for private in ("ada@uni.edu", "Ada Lovelace", "ada-l", "dsl-secret"):
        assert private not in fault.what
        assert private not in fault.fix()


def test_a_clean_roster_has_no_faults():
    assert _faults(f"{HEADER}\nada@uni.edu,Ada,,ada-l,42,,\n") == []


def test_a_caller_that_asks_for_nothing_gets_exactly_what_it_always_got():
    (student,) = roster.parse(f"{HEADER}\nada@uni.edu,Ada,audit,ada-l,42,,\n")
    assert student.role == roster.ROLE_ENROLLED
