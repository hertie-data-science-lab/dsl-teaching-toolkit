"""source_digest: one self-updating issue per cohort, whose BODY is state and whose
COMMENTS are events. The whole point is notification volume - a term written up front has
dozens of missing sources, all normal, so anything that emails per fault or per tick buries
the one that matters. These tests pin the three moments a human is meant to hear about
(appears, escalates, clears) and the silence in between.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from dsl_course import source_digest as sd
from dsl_course.schedule import SourceFault

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=BERLIN)


def _f(where: str, offset: timedelta | None, field: str = "course_source_path"):
    return SourceFault(
        where,
        "`cm/x` does not exist yet",
        NOW + offset if offset else None,
        field=field,
    )


class _Gh:
    """A recording fake for ghcli.gh - every call captured, replies queued by subcommand."""

    def __init__(self, issues: list[dict] | None = None):
        self.issues = issues or []
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return 0, json.dumps(self.issues)
        return 0, ""

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]


@pytest.fixture
def gh(monkeypatch):
    def _make(issues=None):
        fake = _Gh(issues)
        monkeypatch.setattr(sd, "gh", fake)
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
    assert sd.read_state(body) == {"releases.a.course_source_path": "error"}


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
            _f("releases.near", timedelta(hours=3)),
            _f("releases.soon", timedelta(days=3)),
        ],
        NOW,
        "Course",
    )
    assert (
        body.index("### ERROR") < body.index("### WARNING") < body.index("### ADVISORY")
    )


# ------------------------------------------------------------------------ transitions


def test_a_new_advisory_is_not_news_but_a_new_warning_is():
    current = {"a.f": "advisory", "b.f": "warning", "c.f": "error"}
    appeared, escalated, cleared = sd.transitions({}, current)
    assert appeared == ["b.f", "c.f"]  # the advisory stays quiet
    assert (escalated, cleared) == ([], [])


def test_escalation_is_reported_but_standing_still_is_not():
    previous = {"a.f": "warning", "b.f": "warning"}
    current = {"a.f": "error", "b.f": "warning"}
    appeared, escalated, _ = sd.transitions(previous, current)
    assert escalated == ["a.f"]
    assert appeared == []  # `b` is unchanged - an hourly tick must not re-announce it


def test_clearing_is_always_news_however_quietly_it_arrived():
    # It left from `advisory`, which never earned an email going in - but "it is fixed"
    # is the message that lets someone stop worrying, so it is always reported.
    _, _, cleared = sd.transitions({"a.f": "advisory"}, {})
    assert cleared == ["a.f"]


def test_de_escalation_is_not_reported_as_a_change():
    # A date pushed back makes a fault less urgent. Nothing broke, so nobody is emailed.
    assert sd.transitions({"a.f": "error"}, {"a.f": "warning"}) == ([], [], [])


# ------------------------------------------------------------------------- sync + IO


def test_an_advisory_only_plan_opens_no_issue_at_all(gh):
    # Jan writes his whole term in August: 21 sources that do not exist yet, all of them
    # normal. Opening a ticket for that is the cry-wolf failure in a different channel.
    fake = gh([])
    assert sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=60))], NOW) == 0
    assert fake.did("issue", "create") == []
    assert fake.did("issue", "comment") == []


def test_the_first_warning_opens_the_issue(gh):
    fake = gh([])
    assert sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW) == 0
    created = fake.did("issue", "create")
    assert len(created) == 1
    assert sd.TITLE in created[0]
    # A brand-new issue notifies by being created; a comment on top would double up.
    assert fake.did("issue", "comment") == []


def test_a_quiet_tick_edits_the_body_and_says_nothing(gh):
    # The hourly cron re-runs with nothing changed. The body is refreshed (GitHub does not
    # email on a body edit) and NOT commented on - this is the noise control.
    body = sd.render_body([_f("releases.a", timedelta(days=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": body}])
    assert sd.sync("Cohort", "Course", [_f("releases.a", timedelta(days=3))], NOW) == 0
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


def test_an_escalation_comments_and_mentions_the_instructors(gh):
    was = sd.render_body([_f("releases.a", timedelta(days=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    assert sd.sync("Cohort", "Course", [_f("releases.a", timedelta(hours=3))], NOW) == 0
    comments = fake.did("issue", "comment")
    assert len(comments) == 1
    text = comments[0][comments[0].index("--body") + 1]
    assert "Escalated" in text and "now **error**" in text
    # An issue only emails people it mentions - without this the comment is as silent as
    # the run summary it exists to improve on.
    assert "cc @Cohort/instructors" in text


def test_the_last_fault_clearing_closes_the_issue(gh):
    was = sd.render_body([_f("releases.a", timedelta(hours=3))], NOW, "Course")
    fake = gh([{"number": 7, "title": sd.TITLE, "body": was}])
    assert sd.sync("Cohort", "Course", [], NOW) == 0
    closed = fake.did("issue", "close")
    assert len(closed) == 1 and "7" in closed[0]
    assert fake.did("issue", "edit") == []


def test_nothing_missing_and_no_issue_is_a_complete_no_op(gh):
    fake = gh([])
    assert sd.sync("Cohort", "Course", [], NOW) == 0
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
        )
        == 0
    )
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


def test_the_digest_lookup_asks_for_more_than_the_default_page(monkeypatch):
    # `gh issue list` returns 30 by default and the exact-title match below is client-side,
    # so a repo whose issue list buried ours past the 30th result looked as though no
    # digest existed - and every hourly run opened a fresh one.
    seen: list[tuple[str, ...]] = []

    def fake_gh(*args, **kwargs):
        seen.append(args)
        return 0, "[]"

    monkeypatch.setattr(sd, "gh", fake_gh)
    assert sd._open_issue("Cohort-f2026") is None
    (args,) = seen
    assert "--limit" in args and args[args.index("--limit") + 1] == "100"


def test_the_field_reference_points_at_the_tier_the_org_runs(monkeypatch):
    # The runbook describes the engine the org actually runs; a staging org sent to main's
    # docs reads a schema for code it does not have.
    body = sd.render_body(
        [_f("releases.a", timedelta(hours=2))], NOW, "Course", None, "staging"
    )
    assert "/blob/staging/docs/07-schedule-releases.md" in body
