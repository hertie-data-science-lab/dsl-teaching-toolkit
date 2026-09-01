"""status's table-rendering given already-collected data, plus one walk of the real
`collect()` with every loader it reads stubbed - the row-building code between them is
where a constant that moved modules goes unnoticed."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from dsl_course import grades, roster, schedule, status, sync_faculty, teams

BERLIN = ZoneInfo("Europe/Berlin")

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


def _stub_every_read(monkeypatch):
    """Answer each loader `collect()` reads with "this cohort is empty", so the real
    row-building runs end to end with no gh. `conftest._no_live_gh` catches any read
    this misses."""
    monkeypatch.setattr(status, "org_meta", lambda org: {"course_name": "Course"})
    monkeypatch.setattr(status, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(roster, "load", lambda org: [])
    monkeypatch.setattr(grades, "load_grade_sources", lambda org: {})
    monkeypatch.setattr(teams, "load", lambda org: {})
    monkeypatch.setattr(schedule, "load", lambda org: schedule.Schedule())
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: None)


def test_main_walks_every_row_and_points_c7_at_classroom_config(monkeypatch, capsys):
    # Every row is built on the way to the table, so this is the only test that would
    # have caught `sync_faculty.COHORT_CONFIG_REPO` going stale in the module split -
    # an AttributeError that reached the demo org, not CI.
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        ["status", "--course-org", "C", "--cohort-org", "K", "--format", "json"],
    )
    assert status.main() == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == set(status.ITEMS)
    assert data["C7"]["repo"] == "classroom-config"
    assert data["C7"]["path"] == "people.yml"


# ------------------------------------------------------------- C8, the enrolment window
# The hourly cron mails enrolment codes for as long as an `enrolment:` window is open, and
# deliberately says nothing when it cannot: it would say it every hour, and by the time
# the window is open it is already nearly too late. This row is where the same facts are
# readable BEFORE it opens - so what it says about an empty roster is the whole point.


def _window(**kwargs) -> schedule.Enrolment:
    return schedule.Enrolment(
        opens=datetime(2026, 8, 24, 8, 0, tzinfo=BERLIN),
        closes=datetime(2026, 9, 21, 0, 0, tzinfo=BERLIN),
        **kwargs,
    )


def _collect_with(monkeypatch, sched, students):
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(schedule, "load", lambda org: sched)
    monkeypatch.setattr(roster, "load", lambda org: students)
    return status.collect("Course", "Cohort-f2026")


def test_c8_shows_the_window_bounds_and_points_at_scheduleyml(monkeypatch):
    row = _collect_with(
        monkeypatch,
        schedule.Schedule(enrolment=_window()),
        [roster.Student("a", "A", "h", "1")],
    )["C8"]
    assert row["status"] == "ok"
    assert row["repo"] == "classroom-config" and row["path"] == "schedule.yml"
    assert "2026-08-24T08:00" in row["detail"] and "2026-09-21T00:00" in row["detail"]
    assert "WARNING" not in row["detail"]


def test_c8_calls_out_a_window_that_will_open_over_an_empty_roster(monkeypatch):
    # The failure this row exists for: green, silent, and unrecoverable once the window
    # has run out.
    row = _collect_with(monkeypatch, schedule.Schedule(enrolment=_window()), [])["C8"]
    assert row["status"] == "ok"  # the window is declared; it is the roster that is not
    assert "students.csv is empty" in row["detail"]


def test_c8_tells_an_unreadable_roster_from_an_empty_one(monkeypatch):
    # Different fix: one wants a roster pasted in, the other wants somebody to look at why
    # classroom-config cannot be read.
    row = _collect_with(monkeypatch, schedule.Schedule(enrolment=_window()), None)["C8"]
    assert "students.csv cannot be read" in row["detail"]


def test_c8_is_optional_when_no_window_is_declared(monkeypatch):
    # Not every cohort automates enrolment - most still press the button.
    row = _collect_with(monkeypatch, schedule.Schedule(), [])["C8"]
    assert row["status"] == "optional"


def test_c8_says_so_when_the_enrolment_block_was_dropped(monkeypatch):
    # A block faculty wrote and the parser threw away leaves `enrolment` None, which would
    # otherwise render identically to never having written one.
    sched = schedule.Schedule(dropped=["enrolment: no valid `send_codes_datetime`"])
    row = _collect_with(monkeypatch, sched, [])["C8"]
    assert "DROPPED" in row["detail"]
