"""status's table-rendering given already-collected data, plus one walk of the real
`collect()` with every loader it reads stubbed - the row-building code between them is
where a constant that moved modules goes unnoticed."""

from __future__ import annotations

import json
from datetime import date

from dsl_course import (
    config_digest,
    grades,
    mailer,
    roster,
    schedule,
    source_digest,
    status,
    sync_faculty,
    teams,
)

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


def _stub_every_read(monkeypatch, standing=None):
    """Answer each loader `collect()` reads with "this cohort is empty", so the real
    row-building runs end to end with no gh. `conftest._no_live_gh` catches any read
    this misses."""
    monkeypatch.setattr(status, "org_meta", lambda org: {"course_name": "Course"})
    monkeypatch.setattr(status, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(roster, "load", lambda org: [])
    monkeypatch.setattr(grades, "sheet_slugs", lambda org: [])
    monkeypatch.setattr(teams, "load", lambda org: {})
    monkeypatch.setattr(schedule, "load", lambda org: schedule.Schedule())
    monkeypatch.setattr(sync_faculty, "load_cohort_faculty", lambda org: None)
    monkeypatch.setattr(status, "open_titles", lambda repo: set(standing or ()))


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


def test_c7_says_how_many_of_the_teaching_team_cannot_be_notified(monkeypatch):
    # `email:` is required but never withheld access, so an entry missing one looks
    # perfectly healthy everywhere else. Counts, not handles: this table is appended to
    # the step summary of a PUBLIC repo.
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(
        sync_faculty,
        "load_cohort_faculty",
        lambda org: {
            "instructors": [{"github_handle": "janedoe", "email": "jane@x.org"}],
            "teaching_assistants": [{"github_handle": "nomail"}],
        },
    )
    row = status.collect("Course", "Cohort-f2026")["C7"]
    assert row["detail"] == "2 active - WARNING: 1 without email, see the run log"
    assert "nomail" not in row["detail"]


def test_c7_is_quiet_when_every_entry_can_be_notified(monkeypatch):
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(
        sync_faculty,
        "load_cohort_faculty",
        lambda org: {
            "instructors": [{"github_handle": "janedoe", "email": "jane@x.org"}]
        },
    )
    assert status.collect("Course", "Cohort-f2026")["C7"]["detail"] == "1 active"


# ------------------------------------------------------------- B8, the mail transport
# The codes send runs unattended off a roster push, so nobody watches it discover that the
# org has no transport to mail on. Nothing in status checked the GRAPH_* secrets: an org
# with them missing (or half-set, the commoner mistake) mailed nobody and every surface
# said ok. This row is where that is readable before anyone waits on an email.


def _transport_row(monkeypatch, **secrets):
    # DSL_MAINTAINER_EMAIL rides in the same workflow env and is reported in the same row,
    # so it defaults to ABSENT here: a value exported in the shell that runs the tests
    # must not be what decides this row.
    for name in (*mailer.GRAPH_ENV, mailer.MAINTAINER_ENV):
        value = secrets.get(name, None if name == mailer.MAINTAINER_ENV else "set")
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
    # The table is appended to $GITHUB_STEP_SUMMARY of a PUBLIC repo. Both addresses are
    # real inboxes, and the maintainer's is a person's.
    row = _transport_row(
        monkeypatch,
        GRAPH_SENDER="mailbox@example.org",
        DSL_MAINTAINER_EMAIL="maintainer@example.org",
    )
    assert "mailbox@example.org" not in row["detail"]
    assert "maintainer@example.org" not in row["detail"]


def test_b8_says_whether_this_org_can_mail_a_fault_home(monkeypatch):
    # Propagated by bootstrap, and by nothing else - an org that never got it is otherwise
    # indistinguishable from one that did, until a fault goes to the shared mailbox.
    row = _transport_row(monkeypatch, DSL_MAINTAINER_EMAIL="maintainer@example.org")
    assert row["status"] == "ok"
    assert f"{mailer.MAINTAINER_ENV} set" in row["detail"]


def test_b8_stays_ok_when_only_the_maintainer_address_is_missing(monkeypatch):
    # Not a fault: mailer.maintainer_address falls back to GRAPH_SENDER, and a row that
    # went MISSING over an optional address would train faculty to ignore it.
    row = _transport_row(monkeypatch)
    assert row["status"] == "ok"
    assert f"{mailer.MAINTAINER_ENV} unset" in row["detail"]
    assert "GRAPH_SENDER" in row["detail"]


# ------------------------------------------- the rows about faults, not about inputs
#
# C8 and C9 are the only rows that do not describe an input file: they say which of this
# cohort's digest issues are standing. They exist so the table a faculty member opens
# agrees with the mail already in their inbox - every other row can read perfectly while
# a roster nobody can parse sits in an open issue.


def test_c8_and_c9_are_clean_when_no_digest_issue_is_open(monkeypatch):
    _stub_every_read(monkeypatch)
    data = status.collect("Course", "Cohort-f2026")
    assert data["C8"]["status"] == "ok"
    assert data["C8"]["detail"] == "no source-fault issue open"
    assert data["C9"]["status"] == "ok"
    assert data["C9"]["detail"] == "config faults: 0 open"


def test_c8_reports_the_release_plans_own_digest(monkeypatch):
    _stub_every_read(monkeypatch, standing={source_digest.TITLE})
    row = status.collect("Course", "Cohort-f2026")["C8"]
    assert row["status"] == status.ATTENTION
    assert row["detail"] == "the release plan cites sources nobody has staged"
    assert row["edit_url"] == "https://github.com/Cohort-f2026/classroom-config/issues"


def test_c9_names_the_files_whose_digest_issues_are_standing(monkeypatch):
    _stub_every_read(
        monkeypatch,
        standing={config_digest.ROSTER.title, config_digest.TEAMS.title},
    )
    row = status.collect("Course", "Cohort-f2026")["C9"]
    assert row["status"] == status.ATTENTION
    assert row["detail"] == "config faults: 2 open - students.csv, teams.csv"


def test_c9_covers_every_digest_a_cohort_can_have(monkeypatch):
    # A seventh digest added without a line here would be a file this table never
    # mentions, in the one place a reader goes to ask what is wrong.
    _stub_every_read(
        monkeypatch, standing={d.title for d in config_digest.COHORT_DIGESTS}
    )
    row = status.collect("Course", "Cohort-f2026")["C9"]
    assert f"{len(config_digest.COHORT_DIGESTS)} open" in row["detail"]


def test_the_schedule_digest_is_c8s_alone_and_never_counted_twice(monkeypatch):
    # schedule.yml has its own row because its faults keep the other clock. It must not
    # also appear in C9, or one broken file would read as two.
    _stub_every_read(monkeypatch, standing={source_digest.TITLE})
    data = status.collect("Course", "Cohort-f2026")
    assert data["C9"]["detail"] == "config faults: 0 open"


def test_a_fault_row_links_the_issue_list_in_both_states(monkeypatch):
    md = status.render_markdown(
        "Course",
        "Cohort-f2026",
        _data(C9={**_ROW, "status": status.ATTENTION, "link_text": "open"}),
    )
    assert "ATTENTION" in md and "[open](https://x/edit)" in md


def test_c3_points_at_the_grading_sheets_folder(monkeypatch):
    # It said `grades/`, a path retired in 2026-09 - so the one link a grader would
    # follow from this table opened a file-creation form for a folder nothing reads.
    _stub_every_read(monkeypatch)
    row = status.collect("Course", "Cohort-f2026")["C3"]
    assert row["path"] == "grading_sheets/"
    assert "grading_sheets/" in row["label"] and "grading_sheets/" in row["edit_url"]


def test_c6_says_when_the_whole_cohort_freezes(monkeypatch):
    # The one date in schedule.yml that acts on every repo in the org, and the one nobody
    # would otherwise think to check before the day it fires.
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(
        schedule,
        "load",
        lambda org: schedule.Schedule(
            semester_end=date(2026, 12, 18), archive_date=date(2027, 2, 16)
        ),
    )
    assert (
        "archives 2027-02-16"
        in status.collect("Course", "Cohort-f2026")["C6"]["detail"]
    )


def test_c6_says_so_when_nothing_will_ever_archive_the_cohort(monkeypatch):
    _stub_every_read(monkeypatch)
    monkeypatch.setattr(
        schedule, "load", lambda org: schedule.Schedule(semester_start=date(2026, 9, 7))
    )
    detail = status.collect("Course", "Cohort-f2026")["C6"]["detail"]
    assert "no archive date (set semester_end or archive.date)" in detail
