"""`legacy.py`: the one module that knows old names (decision 0042). Every row is read by
a parser or by `migrate`; a row nothing reads is a row to delete."""

from __future__ import annotations

import re
from pathlib import Path

from dsl_course import legacy, schemas

PACKAGE = Path(legacy.__file__).parent
# The surface constant each row's `where` is, by its name in `legacy`.
SURFACES = {
    value: name
    for name, value in vars(legacy).items()
    if name.isupper() and not name.startswith("_") and isinstance(value, str)
}


def _readers() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name != "legacy.py"
    )


def test_every_row_is_read_by_a_parser_or_by_migrate():
    text = _readers()
    for row in legacy.ROWS:
        name = SURFACES.get(row.where)
        assert name, f"{row.old}: its `where` is not one of legacy's surface constants"
        assert re.search(rf"\blegacy\.{name}\b", text), (
            f"nothing reads legacy.{name} (row {row.old!r}): delete the row"
        )


def test_every_row_says_where_and_since():
    for row in legacy.ROWS:
        assert row.old and row.where and re.fullmatch(r"\d+\.\d+\.\d+", row.since), row


def test_a_parser_finds_an_old_name_only_where_it_was_spelt():
    row = legacy.flag("all_cohorts", legacy.MEMBERSHIP_DISPATCH)
    assert row is not None and row.new == "all_semesters"
    assert legacy.flag("all_cohorts", legacy.SETTINGS) is None
    assert legacy.renames(legacy.SETTINGS) == {"format": "formats"}
    assert legacy.old_name("semester-config", legacy.SEMESTER_ORG) == "classroom-config"


def test_names_json_carries_the_rows_for_the_console():
    rows = schemas.names_json()["legacy"]
    assert [r["old"] for r in rows] == [r.old for r in legacy.ROWS]
    assert set(rows[0]) == {"old", "new", "where", "since", "fault_code", "cost"}
