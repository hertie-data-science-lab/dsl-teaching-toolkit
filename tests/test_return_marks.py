"""Distribute grades' two optional return-marks channels: `--receipt-note` posts one line
on each returned unit's receipts issue, and `--include-feedback` puts the feedback text
into the email. Both off by default."""

from __future__ import annotations

import re

from conftest import workflow_inputs, workflow_jobs

from dsl_course import course, grades, roster, workflows_render

COHORT = "Cohort-f2026"
SPEC = grades.SheetSpec(slug="assignment-1", title="Regression", is_group=False)
EXTERNAL = grades.SheetSpec(
    slug="assignment-2", title="Kaggle", is_group=False, submit_via="external"
)


def _sheet(*handles: str, marked: bool = True) -> dict:
    return {
        "submissions": {
            h: {
                "score_individual": 8 if marked else None,
                "feedback_individual": "Good.",
            }
            for h in handles
        }
    }


def _books(specs, sheets):
    return grades.build_gradebooks({s: (specs[s], sheets[s]) for s in sheets})


# ------------------------------------------------------------- which units get the note


def test_the_note_goes_to_every_unit_whose_marks_went_out():
    specs = {"assignment-1": SPEC}
    sheets = {"assignment-1": _sheet("ada", "bo")}
    books = _books(specs, sheets)
    units = grades._returned_units(specs, sheets, books, {"ada": "x"})
    # Only `ada`'s gradebook was written this run, so only her repo is told.
    assert [repo for _spec, repo in units] == ["assignment-1-ada"]


def test_an_unmarked_unit_or_an_off_github_shape_gets_no_note():
    specs = {"assignment-1": SPEC, "assignment-2": EXTERNAL}
    sheets = {
        "assignment-1": _sheet("ada", marked=False),
        "assignment-2": _sheet("ada"),
    }
    books = _books(specs, sheets)
    assert grades._returned_units(specs, sheets, books, {"ada": "x"}) == []


# ------------------------------------------------------------- posting it once


class _Thread:
    """One receipts issue's comments, behind the `gh` calls `post_marked_comment` makes."""

    def __init__(self, bodies: list[str]):
        self.bodies = bodies
        self.posted: list[str] = []

    def __call__(self, *args, **_kw):
        if "--method" in args and "POST" in args:
            body = next(a for a in args if a.startswith("body="))[len("body=") :]
            self.bodies.append(body)
            self.posted.append(body)
            return 0, ""
        return 0, "\n".join(self.bodies)


def test_a_rerun_posts_the_note_once(monkeypatch):
    thread = _Thread([])
    monkeypatch.setattr(grades, "gh", thread)
    monkeypatch.setattr(grades, "find_receipts_issue", lambda org, repo: (7, "open"))
    units = [(SPEC, "assignment-1-ada")]
    listed = {"assignment-1-ada": {"visibility": "private", "private": True}}
    assert grades._post_returned_notes(COHORT, units, listed) == 1
    assert grades._post_returned_notes(COHORT, units, listed) == 1
    (posted,) = thread.posted
    assert course.MARKS_RETURNED_NOTE in posted
    assert course.marks_returned_marker("assignment-1") in posted


def test_no_note_where_the_repo_has_no_receipts_issue(monkeypatch):
    thread = _Thread([])
    monkeypatch.setattr(grades, "gh", thread)
    monkeypatch.setattr(grades, "find_receipts_issue", lambda org, repo: None)
    assert grades._post_returned_notes(COHORT, [(SPEC, "assignment-1-ada")], None) == 0
    assert thread.posted == []


def test_the_note_is_never_read_as_a_submission_receipt():
    # Every receipt carries `course.receipt_marker(sha, event)`; the note's mark must not
    # fit that shape, or anything counting receipts would count a note.
    receipt = re.compile(
        re.escape(course.receipt_marker("SHA", "EVENT"))
        .replace("SHA", "[^:]*")
        .replace("EVENT", r"\w+")
    )
    assert receipt.fullmatch(course.receipt_marker("abc123", course.RECEIPT_DUE))
    assert not receipt.search(course.marks_returned_marker("assignment-1"))
    assert "dsl-receipt:" not in course.marks_returned_marker("assignment-1")


# ------------------------------------------------------------- feedback in the email


def _students():
    return [
        roster.Student(
            hertie_email="ada@x.edu",
            name="Ada",
            github_handle="ada",
            github_id="1",
        )
    ]


def _sent(monkeypatch) -> list:
    sent: list = []
    monkeypatch.setattr(grades.roster, "load", lambda org: _students())
    monkeypatch.setattr(grades, "_course_name", lambda org: "ML")

    def send(messages, dry_run=False, sample=None, **_kw):
        sent.extend(messages)
        return [m[0] for m in messages]

    monkeypatch.setattr(grades.mailer, "send_bulk", send)
    return sent


def test_feedback_goes_into_the_email_only_when_asked(monkeypatch):
    sent = _sent(monkeypatch)
    grades._email_updates(COHORT, ["ada"], feedback={"ada": "Regression:\nGood."})
    grades._email_updates(COHORT, ["ada"])
    with_feedback, without = (m[2] for m in sent)
    assert "Regression:\nGood." in with_feedback
    assert "Good." not in without


def test_feedback_text_is_one_paragraph_per_assignment():
    book = {
        "assignment-1": {"feedback": "Good."},
        "assignment-2": {"final_grade": 7},
        "assignment-3": {"feedback": "Fine.", "team_feedback": "Team did well."},
    }
    text = grades.feedback_text(book, {"assignment-1": "Regression"})
    assert text == "Regression:\nGood.\n\nassignment-3:\nFine.\n\nTeam did well."


def test_the_preview_sample_carries_a_placeholder_not_feedback():
    assert "<feedback>" in grades.sample_body(COHORT, "ML", feedback=True)
    assert "<feedback>" not in grades.sample_body(COHORT, "ML")


# ------------------------------------------------------------- the button


def test_the_button_offers_both_channels_off_by_default():
    rendered = workflows_render.render_distribute_grades([COHORT])
    inputs = workflow_inputs(rendered)
    for name in ("receipt_note", "include_feedback"):
        assert inputs[name]["type"] == "boolean" and inputs[name]["default"] is False
    run = workflow_jobs(rendered)["distribute-grades"]["steps"][-1]["run"]
    assert '[ "$RECEIPT_NOTE" = "true" ] && args+=(--receipt-note)' in run
    assert '[ "$INCLUDE_FEEDBACK" = "true" ] && args+=(--include-feedback)' in run
