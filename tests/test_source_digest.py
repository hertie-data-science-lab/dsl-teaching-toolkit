"""source_digest: one self-updating issue per cohort, whose BODY is state and whose
COMMENTS are events. The whole point is notification volume - a term written up front has
dozens of missing sources, all normal, so anything that emails per fault or per tick buries
the one that matters. These tests pin the three moments a human is meant to hear about
(appears, escalates, clears) and the silence in between.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dsl_course import issues
from dsl_course import source_digest as sd
from dsl_course.schedule import SourceFault

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=BERLIN)
# What `gh issue create` prints, and the only place the number of a just-opened issue
# comes from.
CREATED_URL = "https://github.com/Cohort/classroom-config/issues/12"


def _f(
    where: str,
    offset: timedelta | None,
    field: str = "course_source_path",
    lineno: int | None = None,
):
    return SourceFault(
        where,
        "`cm/x` does not exist yet",
        NOW + offset if offset else None,
        field=field,
        lineno=lineno,
    )


class _Gh:
    """A recording fake for ghcli.gh - every call captured, replies queued by subcommand.

    The issue LISTING goes through `gh_json` (it parses stdout alone, so a gh advisory on
    stderr cannot spoil it), so that one is served by `json` below."""

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return 0, f"{CREATED_URL}\n" if args[:2] == ("issue", "create") else ""

    def json(self, *args, **kwargs):
        self.calls.append(args)
        return self.rows

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]


@pytest.fixture
def gh(monkeypatch):
    def _make(rows=None):
        fake = _Gh(rows)
        # The issue plumbing lives in `dsl_course.issues` now (`find_issue`/`upsert_issue`/
        # `close_issues_titled`); the digest's own logic is unchanged, so this is the same
        # recording fake one module further down.
        monkeypatch.setattr(issues, "gh", fake)
        monkeypatch.setattr(issues, "gh_json", fake.json)
        # The body's field-reference link is pinned to the tier the course org runs; that
        # read is not what any of these tests is about.
        monkeypatch.setattr(sd, "central_ref_for", lambda org: "release")
        return fake

    return _make


# ------------------------------------------------------------------ state round-trip


def test_the_body_carries_its_own_previous_state():
    # No committed state file and no database - the issue IS the record, so the digest
    # can tell "still broken" from "just got worse" with nothing but what it last wrote.
    body = sd.render_body([_f("releases.a", timedelta(hours=2))], NOW, "Course")
    assert sd.read_state(body) == {"releases.a.course_source_path": "critical"}


def test_a_body_this_module_did_not_write_reads_as_no_state():
    assert sd.read_state("someone typed this by hand") == {}
    assert sd.read_state("") == {}
    assert sd.read_state("<!-- dsl-source-state: not json -->") == {}


def test_the_state_marker_is_invisible_in_the_rendered_issue():
    body = sd.render_body([_f("releases.a", timedelta(days=30))], NOW, "Course")
    assert body.count("<!-- dsl-source-state:") == 1
    assert body.strip().endswith("-->")  # last line, out of the reader's way


def test_the_body_names_the_field_to_edit_not_just_the_entry():
    body = sd.render_body(
        [_f("assignments.a1", None, field="course_source_repo")], NOW, "Course"
    )
    assert "**`assignments.a1`** -> `course_source_repo`" in body
    assert "no date (tbc)" in body


def test_rungs_are_rendered_loudest_first():
    body = sd.render_body(
        [
            _f("releases.far", timedelta(days=40)),
            _f("releases.fired", -timedelta(hours=1)),
            _f("releases.tomorrow", timedelta(hours=3)),
            _f("releases.near", timedelta(hours=30)),
            _f("releases.soon", timedelta(days=3)),
        ],
        NOW,
        "Course",
    )
    assert (
        body.index("### MISSED")
        < body.index("### CRITICAL")
        < body.index("### URGENT")
        < body.index("### WARNING")
        < body.index("### ADVISORY")
    )


def test_every_rung_says_what_it_means():
    # The rung is the only thing that tells a reader how much of their day this deserves,
    # and the top one is not "deploys soon" - it has already failed to ship.
    body = sd.render_body(
        [
            _f("releases.fired", -timedelta(hours=1)),
            _f("releases.tomorrow", timedelta(hours=3)),
            _f("releases.near", timedelta(hours=30)),
            _f("releases.soon", timedelta(days=3)),
            _f("releases.far", timedelta(days=40)),
        ],
        NOW,
        "Course",
    )
    assert "Fired with nothing staged - the copy did not ship." in body
    assert "**Fires within 24h.**" in body
    assert "**Fires within 48h.**" in body
    assert "**Fires within 7 days.**" in body
    assert "Further out - listed so the picture is complete" in body


def test_the_body_links_at_the_line_to_edit():
    # `releases.lecture_02` still leaves faculty scrolling a file they wrote in August.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3), lineno=36)], NOW, "Course", "Cohort"
    )
    assert (
        "([schedule.yml:36](https://github.com/Cohort/classroom-config/blob/main/"
        "schedule.yml#L36))"
    ) in body


def test_a_fault_whose_line_is_unknown_is_listed_without_one():
    # The scan returns None for a line it cannot find, and a broken link is worse than no
    # link - the fault itself still has to be reported.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=3))], NOW, "Course", "Cohort"
    )
    assert "**`releases.a`** -> `course_source_path`  " in body
    assert "schedule.yml#L" not in body


# ------------------------------------------------------------------------ transitions


def test_a_new_advisory_is_not_news_but_a_new_warning_is():
    current = {"a.f": "advisory", "b.f": "warning", "c.f": "critical"}
    t = sd.transitions({}, current)
    assert t.appeared == ["b.f", "c.f"]  # the advisory stays quiet
    assert (t.escalated, t.cleared) == ([], [])


def test_transitions_carry_the_rung_each_fault_landed_on():
    # Who is told depends on the rung, not on the fact that something changed - so the
    # notifier reads it from here rather than re-deriving it from the faults.
    t = sd.transitions({"a.f": "warning"}, {"a.f": "missed", "b.f": "advisory"})
    assert t.escalated == ["a.f"]
    assert t.rung == {"a.f": sd.Severity.MISSED, "b.f": sd.Severity.ADVISORY}


def test_no_transition_at_all_is_falsey():
    assert not sd.transitions({"a.f": "warning"}, {"a.f": "warning"})
    assert sd.transitions({}, {"a.f": "warning"})


def test_escalation_is_reported_but_standing_still_is_not():
    previous = {"a.f": "warning", "b.f": "warning"}
    current = {"a.f": "urgent", "b.f": "warning"}
    t = sd.transitions(previous, current)
    assert t.escalated == ["a.f"]
    assert t.appeared == []  # `b` is unchanged - an hourly tick must not re-announce it


def test_clearing_is_always_news_however_quietly_it_arrived():
    # It left from `advisory`, which never earned an email going in - but "it is fixed"
    # is the message that lets someone stop worrying, so it is always reported.
    assert sd.transitions({"a.f": "advisory"}, {}).cleared == ["a.f"]


def test_de_escalation_is_not_reported_as_a_change():
    # A date pushed back makes a fault less urgent. Nothing broke, so nobody is emailed.
    t = sd.transitions({"a.f": "critical"}, {"a.f": "warning"})
    assert (t.appeared, t.escalated, t.cleared) == ([], [], [])


# ------------------------------------------------------------------------- sync + IO


def test_an_advisory_only_plan_opens_no_issue_at_all(gh):
    # Jan writes his whole term in August: 21 sources that do not exist yet, all of them
    # normal. Opening a ticket for that is the cry-wolf failure in a different channel.
    fake = gh([])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=60))], NOW)
    assert out.errors == 0
    assert fake.did("issue", "create") == []
    assert fake.did("issue", "comment") == []


def test_the_first_warning_opens_the_issue(gh):
    fake = gh([])
    assert (
        sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW).errors
        == 0
    )
    created = fake.did("issue", "create")
    assert len(created) == 1
    assert sd.TITLE in created[0]
    # A brand-new issue notifies by being created; a comment on top would double up.
    assert fake.did("issue", "comment") == []


def test_the_tick_that_opens_the_issue_still_reports_its_url(gh):
    # The first-appeared mail goes out on exactly this tick, and the number of an issue
    # just opened is printed by `gh issue create` and nowhere else - so without it the
    # mail could not link the record it was summarising.
    gh([])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW)
    assert out.issue_url == CREATED_URL


def test_a_quiet_tick_edits_the_body_and_says_nothing(gh):
    # The hourly cron re-runs with nothing changed. The body is refreshed (GitHub does not
    # email on a body edit) and NOT commented on - this is the noise control.
    body = sd.render_body([_f("releases.a", timedelta(days=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": body}])
    out = sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW)
    assert out.errors == 0 and not out.transitions
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


def test_an_escalation_comments_and_mentions_the_instructors(gh):
    was = sd.render_body([_f("releases.a", timedelta(days=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    out = sd.sync(
        "Cohort", "Course", [_f("releases.a", timedelta(hours=3), lineno=36)], NOW
    )
    assert out.errors == 0
    comments = fake.did("issue", "comment")
    assert len(comments) == 1
    text = comments[0][comments[0].index("--body") + 1]
    assert "Escalated" in text and "now **critical**" in text
    # The fix is one click from the notification, not a scroll through the file.
    assert "schedule.yml#L36" in text
    # An issue only emails people it mentions - without this the comment is as silent as
    # the run summary it exists to improve on.
    assert "cc @Cohort/instructors" in text


def test_sync_reports_the_transitions_and_the_issue_to_link_to(gh):
    # What the notifier mails on top of the @mention: the rung each key crossed, the
    # issue that holds the detail, and the faults themselves.
    was = sd.render_body([_f("releases.a", timedelta(days=3))], NOW, "Course")
    gh([{"number": 7, "title": sd.TITLE, "body": was}])
    fault = _f("releases.a", timedelta(hours=3), lineno=36)
    out = sd.sync("Cohort", "Course", [fault], NOW)
    assert out.transitions.escalated == ["releases.a.course_source_path"]
    assert out.transitions.rung == {
        "releases.a.course_source_path": sd.Severity.CRITICAL
    }
    assert out.issue_url == "https://github.com/Cohort/classroom-config/issues/7"
    assert out.faults_by_key == {"releases.a.course_source_path": fault}


def test_the_last_fault_clearing_closes_the_issue(gh):
    was = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0
    closed = fake.did("issue", "close")
    assert len(closed) == 1 and "7" in closed[0]
    assert fake.did("issue", "edit") == []
    # "It is fixed" is a transition too - the notifier says so on the same channel.
    assert out.transitions.cleared == ["releases.a.course_source_path"]


def test_nothing_missing_and_no_issue_is_a_complete_no_op(gh):
    fake = gh([])
    out = sd.sync("Cohort", "Course", [], NOW)
    assert out.errors == 0 and out.transitions is None
    assert fake.did("issue", "create") == fake.did("issue", "close") == []


def test_an_issue_a_human_filed_is_never_adopted_and_rewritten(gh):
    # `--search` is full-text, so someone quoting the title in their own issue would come
    # back in the results. Rewriting their issue out from under them would be worse than
    # opening a second one.
    fake = gh([{"number": 3, "title": "re: " + sd.TITLE, "body": "my notes"}])
    sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW)
    assert fake.did("issue", "edit") == []
    assert len(fake.did("issue", "create")) == 1


def test_dry_run_touches_nothing(gh):
    fake = gh([])
    assert (
        sd.sync(
            "Cohort",
            "Course",
            [_f("releases.a", timedelta(hours=3))],
            NOW,
            dry_run=True,
        ).errors
        == 0
    )
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


def test_the_field_reference_points_at_the_tier_the_org_runs(monkeypatch):
    # The runbook describes the engine the org actually runs; a trunk org sent to release's
    # docs reads a schema for code it does not have.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=2))], NOW, "Course", "Cohort", None, "main"
    )
    assert "/blob/main/docs/07-schedule-releases.md" in body
