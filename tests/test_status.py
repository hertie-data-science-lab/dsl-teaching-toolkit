"""status.render_markdown pure core - collect()'s gh/git wiring is left live per the
testing strategy; this only pins the table-rendering given already-collected data."""

from __future__ import annotations

from dsl_course import status

_ROW = {
    "label": "x",
    "org": "o",
    "repo": "r",
    "path": "p",
    "status": "ok",
    "detail": "1 thing",
    "edit_url": "https://x/edit",
}


def _data(**overrides) -> dict:
    data = {item_id: dict(_ROW) for item_id in status.ITEMS}
    for item_id, fields in overrides.items():
        data[item_id].update(fields)
    return data


def test_render_markdown_covers_every_item_in_order():
    md = status.render_markdown("Course", "Cohort-f2026", _data())
    lines = [ln for ln in md.splitlines() if ln.startswith("| ") and "---" not in ln]
    # header row + one row per ITEMS, in ITEMS order
    assert len(lines) == 1 + len(status.ITEMS)
    assert "C7" not in md  # row IDs aren't printed, only labels


def test_render_markdown_c7_instructors_row_present_with_edit_link():
    md = status.render_markdown(
        "Course",
        "Cohort-f2026",
        _data(
            C7={
                "label": "Instructors/TAs (people.yml)",
                "org": "Cohort-f2026",
                "repo": "classroom-config",
                "path": "people.yml",
                "status": "ok",
                "detail": "2 active",
                "edit_url": "https://x/edit/people.yml",
            }
        ),
    )
    assert "Instructors/TAs (people.yml)" in md
    assert "2 active" in md
    assert "[edit](https://x/edit/people.yml)" in md


def test_render_markdown_missing_status_uses_add_link_text():
    md = status.render_markdown(
        "Course", "Cohort-f2026", _data(C7={**_ROW, "status": "missing"})
    )
    assert "[add](https://x/edit)" in md


def test_markdown_mode_keeps_loader_chatter_off_stdout(monkeypatch, capsys):
    # The workflow appends stdout to $GITHUB_STEP_SUMMARY of a PUBLIC repo, and the
    # loaders log lines that can name people.yml entries. Only the rendered table may
    # reach stdout, in both formats.
    from dsl_course import status

    def chatty_collect(course, cohort):
        print("  (instructor entry 'Jane Doe' has no github_handle)")
        return {}

    monkeypatch.setattr(status, "collect", chatty_collect)
    monkeypatch.setattr(status, "render_markdown", lambda *a: "# table")
    monkeypatch.setattr(
        "sys.argv", ["status", "--course-org", "C", "--cohort-org", "K"]
    )
    assert status.main() == 0
    out = capsys.readouterr().out
    assert "Jane Doe" not in out and "# table" in out
