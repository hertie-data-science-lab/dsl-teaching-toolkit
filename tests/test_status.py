"""status's table-rendering given already-collected data, plus one walk of the real
`collect()` with every loader it reads stubbed - the row-building code between them is
where a constant that moved modules goes unnoticed."""

from __future__ import annotations

import json

from dsl_course import grades, mailer, roster, schedule, status, sync_faculty, teams

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


# ------------------------------------------------------------- B8, the mail transport
# The codes send runs unattended off a roster push, so nobody watches it discover that the
# org has no transport to mail on. Nothing in status checked the GRAPH_* secrets: an org
# with them missing (or half-set, the commoner mistake) mailed nobody and every surface
# said ok. This row is where that is readable before anyone waits on an email.


def _transport_row(monkeypatch, **secrets):
    for name in mailer.GRAPH_ENV:
        value = secrets.get(name, "set")
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    _stub_every_read(monkeypatch)
    return status.collect("Course", "Cohort-f2026")["B8"]


def test_b8_reads_the_transport_off_the_names_the_mailer_actually_uses(monkeypatch):
    row = _transport_row(monkeypatch)
    assert row["status"] == "ok"
    assert row["org"] == "Course"  # the secrets are the COURSE org's, like B1/B6/B7
    assert "settings/secrets/actions" in row["edit_url"]


def test_b8_flags_an_org_with_no_mail_transport_at_all(monkeypatch):
    row = _transport_row(monkeypatch, **dict.fromkeys(mailer.GRAPH_ENV))
    assert row["status"] != "ok"
    assert "no GRAPH_* secrets set" in row["detail"]
    assert "Send codes and Distribute grades mail nobody" in row["detail"]


def test_b8_names_the_half_of_a_half_configured_transport_that_is_missing(monkeypatch):
    # The commoner mistake, and the one a blanket "not configured" cannot be debugged
    # from: Actions masks the values, so the NAMES are the only thing that helps.
    row = _transport_row(monkeypatch, GRAPH_CLIENT_CERT=None, GRAPH_SENDER="")
    assert row["status"] != "ok"
    assert "GRAPH_CLIENT_CERT" in row["detail"] and "GRAPH_SENDER" in row["detail"]
    assert "GRAPH_TENANT_ID" not in row["detail"]


def test_b8_never_prints_a_secret_value(monkeypatch):
    # The table is appended to $GITHUB_STEP_SUMMARY of a PUBLIC repo.
    row = _transport_row(monkeypatch, GRAPH_SENDER="mailbox@example.org")
    assert "mailbox@example.org" not in row["detail"]
