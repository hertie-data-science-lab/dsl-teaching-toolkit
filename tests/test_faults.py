"""faults -- the one type every hand-edited config file's faults arrive as.

What is load-bearing here is the SPLIT between the two clocks. A source the plan cites
climbs a ladder as its moment approaches and an undated one can never escalate; a line the
toolkit cannot read is at the notify bar the moment it exists, because waiting changes
nothing about it. Both are the same dataclass, so the ladder and the citation are one
implementation - and the tests below are what stops the two halves quietly swapping.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import source_fault

from dsl_course import faults
from dsl_course.faults import ConfigFault, FaultKind, Severity

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=BERLIN)


def _immediate(**kw) -> ConfigFault:
    """A students.csv row nobody can enrol from - the shape every immediate fault has."""
    return ConfigFault(
        "row 4",
        "the `role` column says `audit` (expected enrolled or auditor)",
        file="students.csv",
        field="role",
        lineno=4,
        **kw,
    )


# --------------------------------------------------------------- the two clocks


def test_an_immediate_fault_is_at_the_notify_bar_from_the_moment_it_exists():
    """No date pins it, so there is no ladder to climb and nothing to wait for."""
    fault = _immediate()
    assert fault.fires is None
    assert fault.severity(NOW) is Severity.WARNING
    assert fault.severity(NOW + timedelta(days=30)) is Severity.WARNING
    assert fault.severity(NOW) >= faults.NOTIFY_FROM


def test_an_undated_source_stays_advisory():
    """The opposite case, and the reason the flat rung cannot simply be "no date means
    WARNING": a `tbc` entry names materials nobody has written for a session nobody has
    dated, which is the normal state of a term planned in August."""
    assert source_fault(fires=None).severity(NOW) is Severity.ADVISORY


@pytest.mark.parametrize(
    ("left", "rung"),
    [
        (timedelta(days=3), Severity.ADVISORY),
        (timedelta(hours=20), Severity.WARNING),
        (timedelta(hours=10), Severity.URGENT),
        (timedelta(hours=3), Severity.CRITICAL),
        (timedelta(hours=-1), Severity.MISSED),
    ],
)
def test_a_dated_fault_climbs_the_ladder(left, rung):
    assert source_fault(fires=NOW + left).severity(NOW) is rung


def test_the_ceiling_holds_a_withheld_source_at_warning():
    fault = source_fault(
        fires=NOW - timedelta(hours=1),
        kind=FaultKind.WITHHELD,
        ceiling=Severity.WARNING,
    )
    assert fault.severity(NOW) is Severity.WARNING


# ------------------------------------------------------------------- identity


def test_the_key_names_the_entry_and_the_field():
    assert _immediate().key == "row 4.role"


def test_two_deploys_under_one_entry_are_two_faults():
    """The path is part of a source fault's identity, so neither inherits the other's
    recorded rung in the digest's state."""
    a = source_fault(path="lectures/02")
    b = source_fault(path="lectures/03")
    assert a.key != b.key


# ---------------------------------------------------------- where to go and fix it


def test_a_fault_cites_its_own_file_and_links_the_line():
    fault = _immediate()
    assert fault.at == "students.csv:4"
    assert (
        fault.link("Cohort-f2026")
        == "https://github.com/Cohort-f2026/classroom-config/blob/main/students.csv#L4"
    )
    assert fault.cite("Cohort-f2026").startswith("[`students.csv:4`](https://")


def test_a_line_that_is_not_known_is_cited_without_a_link():
    fault = ConfigFault("header", "unreadable", file="teams.csv")
    assert fault.at == "teams.csv"
    assert fault.link("Cohort-f2026") is None
    assert fault.cite("Cohort-f2026") == "`teams.csv`"


def test_an_immediate_fault_falls_back_to_its_files_fix_sentence():
    assert _immediate().fix() == faults.FIX["students.csv"]


def test_a_parser_that_knows_the_row_says_so_instead():
    assert _immediate(fix_text="fix row 4").fix() == "fix row 4"


def test_a_source_fault_reads_its_fix_off_the_kind_and_the_rung():
    fault = source_fault(field="course_source_path", repo="cm", path="lectures/02")
    coming = fault.fix("Course-Org", Severity.URGENT)
    fired = fault.fix("Course-Org", Severity.MISSED)
    assert "Course-Org/cm" in coming
    assert "the next 15-minute tick releases them" in fired


def test_the_consequence_is_per_file():
    assert "nobody new is enrolled" in _immediate().consequence
    assert ConfigFault("x", "y", file="nothing.txt").consequence == ""


def test_a_csv_fault_names_the_row_and_never_a_cell():
    assert faults.csv_row(4) == "row 4"
    assert faults.csv_row(None) == "row"
