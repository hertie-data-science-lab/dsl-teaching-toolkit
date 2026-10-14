"""Distributing marks -- the sheet -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from dsl_course import (
    course,
    discovery,
    distribute,
    ghcli,
    gradebook,
    grades,
    grading_sheet,
    grading_spec,
    issues,
    receipts,
    roster,
    settings,
    status_json,
)
from dsl_course.schedule import AssignmentEntry, Schedule
from tests.conftest import ROSTER_HEADER, repo_row, split_grading


def test_unsent_grade_notifications_are_reported(monkeypatch, capsys):
    # The send count used to be discarded, so a student who never got the "your grades are
    # updated" mail left no trace in the log at all.
    students = roster.parse(
        ROSTER_HEADER + "\n"
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
        "bob@uni.edu,Bob,enrolled,bob-b,43,dsl-def\n"
    )
    monkeypatch.setattr(distribute.roster, "load", lambda org: students)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "")
    monkeypatch.setattr(
        distribute.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: [m[0] for m in msgs[:1]],
    )
    distribute._email_updates("SEMESTER", ["ada-l", "bob-b"])
    assert "1 of 2 grade notification(s) not sent" in capsys.readouterr().err


def test_grade_notification_names_the_course_and_falls_back_when_unnamed(monkeypatch):
    # A student taking several of these courses cannot tell one "your grades have been
    # updated" from another, so the body names the course - but a course org that carries
    # no name yet must produce the generic sentence, never a blank or a placeholder.
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(distribute.roster, "load", lambda org: students)
    sent: list[list] = []
    monkeypatch.setattr(
        distribute.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            sent.append(msgs) or [m[0] for m in msgs]
        ),
    )

    monkeypatch.setattr(
        discovery, "course_name_for_semester", lambda org: "Deep Learning"
    )
    distribute._email_updates("SEMESTER", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades for the Deep Learning course have been updated." in body
    # and in the SUBJECT - the inbox list is where a student tells two courses apart
    assert subject == "Your grades for Deep Learning have been updated"

    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "")
    distribute._email_updates("SEMESTER", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades for the course have been updated." in body
    assert subject == "Your grades have been updated"


def test_grade_notification_dry_run_carries_a_placeholder_sample(monkeypatch):
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(distribute.roster, "load", lambda org: students)
    monkeypatch.setattr(
        discovery, "course_name_for_semester", lambda org: "Deep Learning"
    )
    seen: dict = {}
    monkeypatch.setattr(
        distribute.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            seen.update(sample=sample) or [m[0] for m in msgs]
        ),
    )
    distribute._email_updates("SEMESTER", ["ada-l"], dry_run=True)
    # The reviewer sees the wording; no real student's name or handle is in it.
    assert "<name>" in seen["sample"] and "<handle>" in seen["sample"]
    assert "Ada" not in seen["sample"] and "ada-l" not in seen["sample"]
    assert "Deep Learning" in seen["sample"]


# ------------------------------------------ render must not clobber a reviewer's edit (fix 16)


# ------------------------------- handles are one account whatever their casing (fix 3)


def test_email_updates_matches_the_roster_case_insensitively(monkeypatch):
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,Ada-L,42,dsl-abc\n"
    )
    monkeypatch.setattr(distribute.roster, "load", lambda org: students)
    monkeypatch.setattr(discovery, "course_name_for_semester", lambda org: "")
    sent: list[list] = []
    monkeypatch.setattr(
        distribute.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            sent.append(msgs) or [m[0] for m in msgs]
        ),
    )
    distribute._email_updates("SEMESTER", ["ada-l"])  # the gradebook file's spelling
    assert sent and sent[-1][0][0] == "ada@uni.edu"


# ------------------------------------------------------------------ distribute, end to end

_SHEET = """\
submissions:
  ada-l:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_individual: 43
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
    notes_not_shared_with_students: chased by email
"""
_TEAM_SHEET = """\
teams:
  alpha:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_group: 43
    feedback_group: |
      Good work.
    members:
      ada-l:
        adjustment_individual: -3
        feedback_individual: |
          Your section repeats the Q4 error.
        notes_not_shared_with_students: privately noted
"""


_GRADING_YML = (
    "title: Neural networks\nlate_window_days: 7\nlate_penalty_per_day: 10%\n"
)
# `pass`, two days late: a score no penalty can be applied to, under one that would have
# applied to a number.
_HELD_SHEET = _SHEET.replace("score_individual: 43", "score_individual: pass").replace(
    "days_late: 0", "days_late: 2"
)
_HELD_TEAM_SHEET = _TEAM_SHEET.replace("score_group: 43", "score_group: pass").replace(
    "days_late: 0", "days_late: 2"
)
ROSTER_ADA = "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"


# Far enough either side of any clock a run of this suite can be on. `distribute` now
# compares the due date with the moment it runs (`_undue_marks`), so a fixed date would
# make the tests that are not about that mean one thing this term and another the next.
_DUE_AHEAD = datetime(2099, 10, 4, 23, 59, tzinfo=timezone.utc)
_DUE_PASSED = datetime(2000, 10, 4, 23, 59, tzinfo=timezone.utc)


def _schedule_with(*slugs: str, due: datetime = _DUE_PASSED) -> Schedule:
    return Schedule(
        assignments={
            slug: AssignmentEntry(
                course_source_repo=f"{slug}-f2026",
                due_datetime=due,
            )
            for slug in slugs
        },
        # `schedule.load` records the semester, which is what its assignments' run
        # settings (assignments.yml) resolve against.
        org="SEMESTER",
    )


class _ListedPrivate(dict):
    """A semester listing that answers "private" for every repo asked of it.

    What `distribute` really sees on a `github` + `private` semester - the shape every test
    here but a handful is about - without each of them having to spell a row per unit. The
    ones that ARE about the listing pass their own, and `listed=None` is the listing that
    could not be read at all."""

    def get(self, name, default=None):
        return super().get(name) or repo_row(name)


_ANY_PRIVATE = _ListedPrivate()


def _fake_issues(monkeypatch, store: list[dict]) -> None:
    """`issues`' two `gh` calls answered from `store`, one dict per issue, so a lookup by
    title, a create, an edit and a close all act on the same list."""

    def listing(*args):
        state = args[args.index("--state") + 1]
        return [
            {k: i[k] for k in ("number", "body", "title", "state")}
            for i in store
            if state == "all" or i["state"] == state.upper()
        ]

    def write(*args):
        verb, flags = args[1], dict(zip(args[2::2], args[3::2], strict=False))
        if verb == "create":
            number = len(store) + 1
            store.append(
                {
                    "number": number,
                    "title": flags["--title"],
                    "body": flags["--body"],
                    "state": "OPEN",
                    "comments": [],
                }
            )
            return 0, f"https://github.com/SEMESTER/semester-config/issues/{number}"
        issue = next(i for i in store if str(i["number"]) == args[2])
        rest = dict(zip(args[3::2], args[4::2], strict=False))
        if verb == "edit":
            issue["body"] = rest["--body"]
        elif verb == "close":
            issue["state"] = "CLOSED"
            issue["comments"].append(rest.get("--comment"))
        return 0, ""

    monkeypatch.setattr(issues, "gh_json", listing)
    monkeypatch.setattr(issues, "gh", write)


def _distribute(
    monkeypatch,
    tmp_path,
    *,
    sheets: dict[str, str] | None = None,
    grading: str = _GRADING_YML,
    distributed: str | None = None,
    notified: str | None = None,
    stale_gradebooks: tuple[str, ...] = (),
    sent: int = 1,
    notify: bool = True,
    dry_run: bool = False,
    roster_rows: str | None = ROSTER_ADA,
    issue: int | None = 7,
    found_issue: int | None = None,
    put_files_ok: bool = True,
    course_name=lambda org: "",
    listed: dict[str, dict] | None = _ANY_PRIVATE,
    due: datetime = _DUE_PASSED,
    exported: str | None = None,
    exported_at: str = gradebook.SEMESTER_CSV_NAME,
    preview_issues: list[dict] | None = None,
    assignment: str | None = None,
    include_feedback: bool = False,
    intent_ok: bool = True,
    send_error: Exception | None = None,
) -> dict:
    """`distribute` over a local semester-config clone, writing to nothing.

    Returns every effect it had: the gradebook commits, the semester-config commit and
    the mail batches - which between them are every channel a mark reaches a student by -
    plus `comments` and `issues`, which are TRIPWIRES. Nothing is posted into a submission
    repo any more, so those two stay empty in every test here; `issue` and `found_issue`
    are what a lookup WOULD answer, so a run that went near a thread would show up rather
    than pass for want of a stub.

    `preview` is semester-config's issue list as GitHub would hold it after the run - pass
    `preview_issues` to start from one, or to share it between two runs. `exported` is the
    registrar export the last real run left behind."""
    cfg = tmp_path / "cfg"
    (cfg / grading_sheet.SHEETS_DIR).mkdir(parents=True)
    sheets = {"assignment-1": _SHEET} if sheets is None else sheets
    for slug, text in sheets.items():
        (cfg / grading_sheet.SHEETS_DIR / f"{slug}.yml").write_text(text)
    if distributed is not None:
        (cfg / gradebook.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / gradebook.DISTRIBUTED_PATH).write_text(distributed)
    if notified is not None:
        (cfg / gradebook.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / gradebook.NOTIFIED_PATH).write_text(notified)
    for name in stale_gradebooks:
        (cfg / gradebook.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / gradebook.GRADEBOOK_DIR / name).write_text("student: someone\n")
    if exported is not None:
        (cfg / exported_at).parent.mkdir(parents=True, exist_ok=True)
        (cfg / exported_at).write_text(exported)
    store = [] if preview_issues is None else preview_issues
    _fake_issues(monkeypatch, store)

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            copytree(cfg, Path(args[3]))
            return 0, ""
        return 0, ""

    effects: dict = {
        "comments": [],
        "gradebooks": [],
        "config": [],
        "intent": [],
        "outbox": [],
        "issues": [],
        "gradebook_calls": [],
        "preview": store,
    }
    monkeypatch.setattr(receipts, "gh", fake_gh)
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(
        distribute,
        "ensure_gradebooks",
        lambda org, dry_run=False, existing=None, **kw: (
            effects["gradebook_calls"].append(kw) or 0
        ),
    )
    # The semester listing distribute takes to check what each repo is: there, and private.
    # None is "could not be read", which is a listing nothing may be CREATED on the
    # strength of (`receipts.receipts_thread_policy`).
    monkeypatch.setattr(distribute, "listing_by_name", lambda org: listed)
    monkeypatch.setattr(distribute, "course_org_for_semester", lambda org: "COURSE")
    template_text, semester_text = split_grading(grading)
    monkeypatch.setattr(grading_spec, "_grading_text", lambda org, tpl: template_text)
    monkeypatch.setattr(settings, "_assignments_text", lambda org: semester_text)
    monkeypatch.setattr(
        distribute.schedule,
        "load",
        lambda org: _schedule_with(*(sheets or {"assignment-1": ""}), due=due),
    )
    monkeypatch.setattr(
        receipts,
        "ensure_receipts_issue",
        lambda org, repo, body, dry_run=False: (
            effects["issues"].append((repo, body)) or issue
        ),
    )
    monkeypatch.setattr(
        distribute,
        "post_marked_comment",
        lambda org, repo, no, body, marker, dry_run=False: (
            effects["comments"].append((repo, body, marker)) or True
        ),
    )

    def fake_put_files(
        org, repo, files, message, *, delete=(), create_only=False, person=False
    ):
        target = "config" if repo == course.CONFIG_REPO else "gradebooks"
        if target == "config" and message.startswith(distribute.INTENT_MESSAGE):
            # The record of the emails about to go, committed before they are sent.
            effects["intent"].append({k: v.decode() for k, v in files.items()})
            return intent_ok
        if target == "gradebooks":
            # The repo is named after the student, so the write has to be marked as one:
            # without it, a gradebook GitHub could not read published its own name.
            assert person, "a gradebook write must be a person write"
        effects[target].append(
            (repo, {k: v.decode() for k, v in files.items()}, tuple(delete))
        )
        # A callable lets a test answer per write - which is what a lost ref race is.
        return put_files_ok(files) if callable(put_files_ok) else put_files_ok

    monkeypatch.setattr(distribute, "put_files", fake_put_files)
    students = (
        None if roster_rows is None else roster.parse(ROSTER_HEADER + roster_rows)
    )
    monkeypatch.setattr(distribute.roster, "load", lambda org: students)
    monkeypatch.setattr(discovery, "course_name_for_semester", course_name)

    def fake_send_bulk(msgs, dry_run=False, sample=None):
        if send_error is not None:
            raise send_error
        effects["outbox"].append(msgs)
        return [m[0] for m in msgs[:sent]]

    monkeypatch.setattr(distribute.mailer, "send_bulk", fake_send_bulk)
    try:
        effects["rc"] = distribute.distribute(
            "SEMESTER",
            notify=notify,
            dry_run=dry_run,
            assignment=assignment,
            include_feedback=include_feedback,
        )
    except Exception as exc:
        if send_error is None:
            raise
        effects["raised"] = exc
    return effects


def test_a_real_run_reaches_every_channel_and_posts_no_comment(tmp_path, monkeypatch):
    # THE shape of a run: three channels, and the submission repo is not one of them.
    # `found_issue` says there IS a thread in that repo, so nothing here is silent for
    # want of one - the run simply does not go near it.
    out = _distribute(monkeypatch, tmp_path, found_issue=7)
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    # ONE commit per gradebook, holding both files, so the page never disagrees with the
    # data beside it
    ((gb_repo, files, _delete),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l"
    assert set(files) == {"grades.yml", "README.md"}
    assert "student: ada-l" in files["grades.yml"]
    assert "| Assignment 1 · Neural networks | 43 |" in files["README.md"]
    # the registrar export and the record, in one semester-config commit
    ((_cfg, cfg_files, _d),) = out["config"]
    assert set(cfg_files) == {gradebook.SEMESTER_CSV_NAME, gradebook.DISTRIBUTED_PATH}
    assert "ada@uni.edu,Ada,ada-l,43" in cfg_files[gradebook.SEMESTER_CSV_NAME]
    # and the email
    assert [m[0] for batch in out["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_scoped_return_sends_that_assignment_beside_those_already_returned(
    tmp_path, monkeypatch
):
    # a1 comes due and is complete; a2 is marked in part and not returned; a0 went out
    # last week (the registrar export has its column). The run returns a1 only, and every
    # gradebook still shows a0: nothing already given is taken away.
    half = _SHEET.replace("score_individual: 43", "score_individual: 20")
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-0": _SHEET, "assignment-1": _SHEET, "assignment-2": half},
        exported=(
            "hertie_email,name,github_handle,assignment-0\nada@uni.edu,Ada,ada-l,43\n"
        ),
        assignment="assignment-1",
    )
    assert out["rc"] == 0
    ((_repo, files, _d),) = out["gradebooks"]
    assert (
        "assignment-0" in files["grades.yml"] and "assignment-1" in files["grades.yml"]
    )
    assert "assignment-2" not in files["grades.yml"]
    ((_cfg, cfg_files, _d2),) = out["config"]
    header = cfg_files[gradebook.SEMESTER_CSV_NAME].splitlines()[0]
    assert header.endswith("assignment-0,assignment-1")
    # distribute never writes a sheet, scoped or not.
    assert not [p for p in cfg_files if p.startswith(grading_sheet.SHEETS_DIR)]


_UNMARKED = _SHEET.replace("score_individual: 43", "score_individual:").replace(
    "feedback_individual: |\n      Clean derivation.\n", "feedback_individual:\n"
)


@pytest.mark.parametrize(
    ("exported", "distributed"),
    [
        # re-saved from Excel, `;`-delimited: present and unreadable
        (
            "hertie_email;name;github_handle;assignment-0\nada@uni.edu;Ada;ada-l;43\n",
            None,
        ),
        # gone while gradebooks were already written
        (
            None,
            gradebook.dump_distributed(
                {("ada-l", "", gradebook.CHANNEL_GRADEBOOK): ("x", "2026-10-01", "")}
            ),
        ),
    ],
)
def test_a_scoped_return_that_cannot_tell_what_went_out_sends_nothing(
    tmp_path, monkeypatch, exported, distributed
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-0": _SHEET, "assignment-1": _SHEET},
        exported=exported,
        distributed=distributed,
        assignment="assignment-1",
    )
    assert out["rc"] == 1
    assert out["gradebooks"] == [] and out["config"] == [] and out["outbox"] == []


def test_a_column_of_submission_facts_alone_is_not_returned(tmp_path, monkeypatch):
    # a2's sheet has only submission facts: its export column is empty, so a scoped
    # return of a1 must not send a2 along.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _SHEET.replace("43", "20")},
        exported="hertie_email,name,github_handle,assignment-2\nada@uni.edu,Ada,ada-l,\n",
        assignment="assignment-1",
    )
    assert out["rc"] == 0
    ((_repo, files, _d),) = out["gradebooks"]
    assert "assignment-2" not in files["grades.yml"]


def test_a_scoped_return_of_an_assignment_with_no_sheet_sends_nothing(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, assignment="nope")
    assert out["rc"] == 1 and out["gradebooks"] == [] and out["outbox"] == []


def test_the_done_line_is_the_spec_counts_in_the_spec_order(
    tmp_path, monkeypatch, capsys
):
    # `Done - {...}` is a JSON dump of the counts dict, so the dict's key order is what a
    # grader reads. Nothing asserted it, and it had drifted out of the order the spec
    # prints. `unknown` is the one key the spec does not name - marks for handles nobody
    # enrolled - and it is kept.
    _distribute(monkeypatch, tmp_path)
    done = next(
        line for line in capsys.readouterr().out.splitlines() if "Done - " in line
    )
    counts = json.loads(done.split("Done - ", 1)[1])
    assert list(counts) == [
        "gradebooks",
        "emails",
        "held",
        "unknown",
        "failed",
    ]


def test_nothing_a_student_may_not_see_reaches_them(tmp_path, monkeypatch):
    # The leak this design exists to close: the grader's private notes, and one member's
    # adjustment. There is no team-wide channel for either to reach now - a group mark
    # goes to each member's own gradebook and nowhere a team-mate reads.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["comments"] == []
    ((gb_repo, files, _d),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l"  # the member's OWN repo, read by nobody else
    for page in (files["grades.yml"], files["README.md"]):
        assert "privately noted" not in page  # the grader's note
        assert "-3" not in page  # the adjustment behind the final grade
    # Their own feedback is theirs to read, and it is here rather than anywhere shared.
    assert "Your section repeats the Q4 error." in files["README.md"]


def test_a_score_no_penalty_fits_is_held_rather_than_sent(
    tmp_path, monkeypatch, capsys
):
    # "penalty -20% · Final grade: pass" is not a grade, it is a contradiction a student
    # would read before anyone noticed. Nothing goes out for it until a person settles it.
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _HELD_SHEET})
    assert out["rc"] == 0
    assert out["comments"] == []
    assert out["gradebooks"] == []
    assert out["outbox"] == []
    printed = capsys.readouterr().out
    assert '"held": 1' in printed
    assert "ada-l" not in printed  # the public log counts, it never names


def test_a_typo_in_one_question_cell_is_held_not_silently_dropped(
    tmp_path, monkeypatch, capsys
):
    # `Q1: 14/15` used to total to nothing: the student's grade vanished and their
    # feedback was posted anyway, with the dry run counting the row as unmarked.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 14/15\n      Q2: 10"
    )
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet})
    assert out["rc"] == 0
    assert (out["comments"], out["gradebooks"], out["outbox"]) == ([], [], [])
    printed = capsys.readouterr().out
    assert '"held": 1' in printed
    assert "ada-l" not in printed


def test_a_non_numeric_adjustment_is_held_not_read_as_zero(tmp_path, monkeypatch):
    # `−3` is what a word processor produces. It was read as no adjustment at all, so the
    # grader believed a penalty had been waived and the student was penalised anyway.
    sheet = _SHEET.replace("adjustment_individual:", "adjustment_individual: \u22123")
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet})
    assert (out["comments"], out["gradebooks"]) == ([], [])


def test_a_question_the_assignment_does_not_declare_is_held_and_never_summed(
    tmp_path, monkeypatch
):
    # A stray `Q5: 10` made 53 out of a 50-point assignment, with no maximum beside it.
    sheet = _SHEET.replace(
        "score_individual: 43",
        "score_individual:\n      Q1: 15\n      Q2: 10\n      Q5: 10",
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, grading=grading
    )
    assert (out["comments"], out["gradebooks"]) == ([], [])
    assert grading_sheet.score_total(
        {"Q1": "15", "Q2": "10", "Q5": "10"}, {"Q1": "15", "Q2": "10"}
    ) == Decimal(25)


def test_a_handle_in_two_teams_is_held_rather_than_taking_the_last_one(
    tmp_path, monkeypatch
):
    # The later team's view silently won the gradebook. Which team a student is in is not
    # something to guess at.
    sheet = _TEAM_SHEET + (
        "  beta:\n"
        "    score_group: 20\n"
        "    feedback_group: |\n      Thin.\n"
        "    members:\n"
        "      ada-l:\n"
        "        adjustment_individual:\n"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["gradebooks"] == []


def test_a_run_writes_every_sheet_the_semester_has_into_every_gradebook(
    tmp_path, monkeypatch
):
    # THE defect a live showcase found, back when a run could be scoped to one slug: the
    # gradebook was rendered from the selected sheet alone, so each scoped run left that
    # one section and deleted every other - four in a row, and assignment-1's distributed
    # grade was gone. The button offers no slug now, and a gradebook is the whole of what
    # a student has been given: every sheet in the semester, and a registrar's column each.
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": _SHEET, "assignment-2": _SHEET}
    )
    ((_repo, book, _detail),) = out["gradebooks"]
    assert "assignment-1:" in book["grades.yml"]
    assert "assignment-2:" in book["grades.yml"]
    ((_cfg_repo, cfg, _e),) = out["config"]
    assert (
        cfg[gradebook.SEMESTER_CSV_NAME]
        .splitlines()[0]
        .endswith("assignment-1,assignment-2")
    )


def test_a_semester_with_no_sheet_yet_distributes_nothing(
    tmp_path, monkeypatch, capsys
):
    # The sheet is the only source of marks, so an empty folder is "nothing has been
    # handed out yet" - reported, and nothing written, rather than an empty run.
    out = _distribute(monkeypatch, tmp_path, sheets={})
    assert out["rc"] == 1
    assert (out["comments"], out["gradebooks"], out["config"]) == ([], [], [])
    assert f"no {grading_sheet.SHEETS_DIR}/ in SEMESTER" in capsys.readouterr().err


def test_the_dry_run_counts_units_with_questions_still_unmarked(
    tmp_path, monkeypatch, capsys
):
    # A partly filled map totals to what has been typed, and a real run sends it - which is
    # the grader's call to make, so the count is what they are given to make it with.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=grading,
        dry_run=True,
    )
    assert "1 unit(s) have unmarked questions" in capsys.readouterr().out


def test_a_half_marked_map_is_still_sent_on_a_real_run(tmp_path, monkeypatch):
    # Not held: a grader releasing Q1 early is a decision, not a typo.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, grading=grading
    )
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "15" in files["README.md"]


def test_the_dry_run_says_what_each_hold_is(tmp_path, monkeypatch, capsys):
    # "1 held" tells a grader to go looking without saying what for, and every one of
    # these is something they typed and can fix in a minute.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 14/15\n      Q2: 10"
    )
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet}, dry_run=True)
    printed = capsys.readouterr().out
    assert "1 held for a hand decision" in printed
    assert "1 with a non-numeric value in a per-question map" in printed


def test_a_held_team_score_holds_every_members_gradebook(tmp_path, monkeypatch):
    # A team's result is derived from the team block rather than from a member's view, so
    # a score nobody can act on holds every member of it, not just the one who was typed.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _HELD_TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["rc"] == 0
    assert out["gradebooks"] == []


def test_holding_one_mark_leaves_the_students_other_marks_alone(tmp_path, monkeypatch):
    # The hold is per assignment: everything else a grader has settled still goes out.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _HELD_SHEET},
    )
    assert out["rc"] == 0
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "assignment-1" in files["grades.yml"]
    assert "assignment-2" not in files["grades.yml"]


_TYPO_SHEET = _SHEET.replace(
    "score_individual: 43", "score_individual:\n      Q1: 14/15\n      Q2: 10"
)


def test_a_hold_on_a_mark_already_returned_takes_nothing_away(tmp_path, monkeypatch):
    # Ada was sent 43 for assignment-1. A grader then types `14/15` into it. The hold
    # used to rewrite her gradebook WITHOUT assignment-1, blank its export cell and email
    # her that her grades had changed. Now nothing of hers moves until it is settled -
    # including assignment-2, so its automatic return is not recorded as done.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, before, _d),) = first["config"]
    sheets = {"assignment-1": _TYPO_SHEET, "assignment-2": _SHEET}
    out = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets=sheets,
        distributed=before[gradebook.DISTRIBUTED_PATH],
        exported=before[gradebook.SEMESTER_CSV_NAME],
        assignment="assignment-2",
    )
    assert out["rc"] == 0
    assert (out["gradebooks"], out["outbox"]) == ([], [])
    ((_cfg, after, _d),) = out["config"]
    assert (
        after[gradebook.SEMESTER_CSV_NAME].splitlines()[1] == "ada@uni.edu,Ada,ada-l,43"
    )
    assert distribute.marks_return_record("assignment-2") not in after


def test_a_hold_with_no_export_to_check_it_against_sends_nothing(tmp_path, monkeypatch):
    # Ada was sent 43, then the export was deleted by hand. With no record of what went
    # out, a held mark cannot be told from a new one, and treating it as new would take
    # her 43 away: nothing goes out until the hold is settled.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, before, _d),) = first["config"]
    out = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": _TYPO_SHEET, "assignment-2": _SHEET},
        distributed=before[gradebook.DISTRIBUTED_PATH],
    )
    assert out["rc"] == 1
    assert (out["gradebooks"], out["outbox"], out["config"]) == ([], [], [])


def test_a_hold_on_a_mark_never_sent_still_sends_the_rest(tmp_path, monkeypatch):
    # The other side of the rule above: an export that never carried assignment-1 for
    # Ada means the held mark is new, and her settled assignment-2 goes out.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TYPO_SHEET, "assignment-2": _SHEET},
        exported="hertie_email,name,github_handle\nada@uni.edu,Ada,ada-l\n",
    )
    ((_repo, files, _d),) = out["gradebooks"]
    assert "assignment-2" in files["grades.yml"]
    assert "assignment-1" not in files["grades.yml"]


def test_a_hold_finds_a_handle_spelt_another_way_on_another_sheet(
    tmp_path, monkeypatch
):
    # The gradebook is keyed on the first spelling (`Ada-L` on assignment-1); the typo is
    # on assignment-2 under `ada-l`. The lookup missed, and `14/15` was sent.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": _SHEET.replace("ada-l:", "Ada-L:"),
            "assignment-2": _TYPO_SHEET,
        },
    )
    ((_repo, files, _d),) = out["gradebooks"]
    assert "assignment-1" in files["grades.yml"]
    assert "assignment-2" not in files["grades.yml"]


def test_one_student_in_two_teams_under_two_spellings_is_held(tmp_path, monkeypatch):
    sheet = _TEAM_SHEET + (
        "  beta:\n"
        "    score_group: 20\n"
        "    members:\n"
        "      Ada-L:\n"
        "        adjustment_individual:\n"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["gradebooks"] == []


@pytest.mark.parametrize(
    "members",
    ["members: [ada-l]", "members: ada-l", "members:\n      ada-l: TODO"],
)
def test_a_team_whose_members_are_not_a_mapping_sends_nothing(
    tmp_path, monkeypatch, capsys, members
):
    # Each of these crashed Return marks with an AttributeError and no line to fix.
    sheet = _TEAM_SHEET.split("    members:")[0] + f"    {members}\n"
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["rc"] == 1
    assert (out["gradebooks"], out["config"], out["outbox"]) == ([], [], [])
    assert "not one entry per student or team - nothing sent" in capsys.readouterr().err


def test_the_dry_run_sample_email_is_the_one_that_would_be_sent(tmp_path, monkeypatch):
    # The subject is the half a student reads first, and the course name is what tells one
    # of these apart from another - so a preview that showed neither was reviewing text
    # nobody would ever receive.
    out = _distribute(
        monkeypatch,
        tmp_path,
        dry_run=True,
        course_name=lambda org: "Deep Learning",
    )
    ((issue),) = out["preview"]
    assert "Subject: Your grades for Deep Learning have been updated" in issue["body"]
    assert (
        "Your grades for the Deep Learning course have been updated. View them"
        in issue["body"]
    )
    assert "grades-<handle>" in issue["body"]  # a placeholder, never a student


def test_an_unreadable_course_name_still_previews_the_email(tmp_path, monkeypatch):
    # Same fallback the send has: the name is a nicety, the notification is not.
    def boom(org):
        raise RuntimeError("no dsl-course.yml")

    out = _distribute(monkeypatch, tmp_path, dry_run=True, course_name=boom)
    assert out["rc"] == 0
    assert "Subject: Your grades have been updated" in out["preview"][0]["body"]


def test_the_dry_run_says_who_gets_what_in_a_private_issue_and_logs_none_of_it(
    tmp_path, monkeypatch, capsys
):
    # The log is a PUBLIC repo's; the issue is in the private semester-config. So the
    # names and marks go to the one, and only counts and the issue's address to the other.
    monkeypatch.setenv("DSL_VERBOSE", "")
    rows = ROSTER_ADA + "bob@uni.edu,Bob Byte,enrolled,bob-b,43,dsl-def\n"
    sheet = _SHEET + _SHEET.split("submissions:\n", 1)[1].replace("ada-l", "bob-b")
    sheet = sheet.replace("score_individual: 43", "score_individual: 38", 1)
    held = _HELD_SHEET.replace("ada-l", "bob-b")
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet, "assignment-2": held},
        roster_rows=rows,
        exported="hertie_email,name,github_handle,assignment-1\n"
        "ada@uni.edu,Ada,ada-l,36\nbob@uni.edu,Bob Byte,bob-b,43\n",
        dry_run=True,
    )
    assert out["rc"] == 0
    assert (out["gradebooks"], out["config"], out["outbox"]) == ([], [], [])
    ((issue),) = out["preview"]
    assert issue["title"] == distribute.PREVIEW_TITLE and issue["state"] == "OPEN"
    body = issue["body"]
    # ada's grade changed; bob's did not (he has never been told it, so he is still
    # emailed), and his other mark is held
    assert (
        "### ⚠️ Fix these first - held back, not sent (1)\n"
        "- **assignment-2** · `bob-b` (Bob Byte) was late, and their mark is not a "
        "number a late penalty can come off. Fix it in "
        "`grading_sheets/assignment-2.yml`.\n" in body
    )
    assert (
        "### Grades that would change (1)\n"
        "- **assignment-1** · `ada-l` (Ada) · 38 (was 36)\n" in body
    )
    assert (
        "### Students who would be emailed (2)\n"
        "- `ada-l` (Ada) - their first grades email.\n"
        "  Their gradebook shows: assignment-1 38\n"
        "- `bob-b` (Bob Byte) - their first grades email.\n"
        "  Their gradebook shows: assignment-1 43\n" in body
    )
    printed = "".join(capsys.readouterr())
    assert (
        "Who gets what: https://github.com/SEMESTER/semester-config/issues/1" in printed
    )
    for private in ("ada-l", "bob-b", "Ada", "Bob", "38", "36"):
        assert private not in printed


def test_the_preview_says_a_late_penalty_without_a_late_policy_is_ignored(
    tmp_path, monkeypatch
):
    late = _SHEET.replace("days_late: 0", "days_late: 2").replace(
        "    score_individual: 43\n",
        "    late_penalty: 15%\n    score_individual: 43\n",
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": late},
        # A late window with no penalty: the pair is set together, so no late rule.
        grading="title: Neural networks\nlate_window_days: 7\n",
        dry_run=True,
    )
    body = out["preview"][0]["body"]
    assert (
        "- **assignment-1** has no late policy, so the `late_penalty` on 1 unit is "
        "ignored: nothing comes off." in body
    )
    assert "### ⚠️ Fix these first - held back, not sent (0)" in body
    assert "- **assignment-1** · `ada-l` (Ada) · 43 (new)" in body


def test_a_second_dry_run_rewrites_the_preview_rather_than_opening_another(
    tmp_path, monkeypatch
):
    store: list[dict] = []
    _distribute(monkeypatch, tmp_path, dry_run=True, preview_issues=store)
    corrected = _SHEET.replace("score_individual: 43", "score_individual: 45")
    _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": corrected},
        dry_run=True,
        preview_issues=store,
    )
    ((issue),) = store
    body = issue["body"]
    assert "- **assignment-1** · `ada-l` (Ada) · 45 (new)" in body
    # The first run's line is gone, not kept beside the new one. Matched on the line, not
    # on a bare "43", which any run id or date in the body can carry.
    assert "`ada-l` (Ada) · 43" not in body
    assert body.count("`ada-l` (Ada) ·") == 1


def test_the_preview_counts_unmarked_questions_per_student(tmp_path, monkeypatch):
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=grading,
        dry_run=True,
    )
    assert (
        "### Not marked yet (1)\n- **assignment-1** · 1 student: `ada-l` (Q2 blank)\n"
        in out["preview"][0]["body"]
    )


def test_a_student_with_no_mark_is_not_in_the_preview_as_emailed(tmp_path, monkeypatch):
    # The send skips a book with nothing a grader wrote, and the preview says what the
    # send would do.
    sheet = _SHEET.replace("score_individual: 43", "score_individual:").replace(
        "    feedback_individual: |\n      Clean derivation.\n", ""
    )
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, dry_run=True
    )
    body = out["preview"][0]["body"]
    assert "- **assignment-1** · 1 student: `ada-l` (no mark yet)" in body
    assert (
        "### Students who would be emailed (0)\n"
        "Nothing - nobody has a new mark to be told about." in body
    )
    assert "Subject:" not in body


def test_a_preview_too_long_for_one_issue_says_how_many_it_left_out(
    tmp_path, monkeypatch
):
    # GitHub refuses a body over its cap, and a refused body is no preview at all.
    monkeypatch.setattr(distribute, "_ISSUE_BODY_CAP", 2_000)
    handles = [f"s{n:03d}" for n in range(60)]
    rows = (
        "".join(
            f"\n{h}@uni.edu,Student {h},enrolled,{h},{n},c{n}"
            for n, h in enumerate(handles)
        )
        + "\n"
    )
    block = _SHEET.split("submissions:\n", 1)[1]
    sheet = "submissions:\n" + "".join(block.replace("ada-l", h) for h in handles)
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        roster_rows=rows,
        dry_run=True,
    )
    body = out["preview"][0]["body"]
    assert len(body) <= 2_000
    shown = body.count("- **assignment-1** · `s")
    assert 0 < shown < 60
    more = "more not shown - the list is longer than one issue can hold._"
    assert f"_{60 - shown} {more}" in body
    # every heading survives the cut, the emailed ones are all counted, and the email
    # itself is still there to review
    assert "### Students who would be emailed (60)\n_60 " + more in body
    assert body.endswith("</details>")


def _after_one_real_run(monkeypatch, tmp_path, sheet: str, **kwargs) -> dict:
    """A dry run over `sheet` in a semester whose last real run sent `_SHEET`: the record
    and the registrar export are the ones that run left behind."""
    first = _distribute(monkeypatch, tmp_path / "real")
    ((_cfg, cfg_files, _e),) = first["config"]
    return _distribute(
        monkeypatch,
        tmp_path / "dry",
        sheets={"assignment-1": sheet},
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
        exported=cfg_files[gradebook.SEMESTER_CSV_NAME],
        dry_run=True,
        **kwargs,
    )


def test_the_preview_opens_by_saying_nothing_was_sent_and_when(tmp_path, monkeypatch):
    body = _distribute(monkeypatch, tmp_path, dry_run=True)["preview"][0]["body"]
    first, second, third = body.splitlines()[:3]
    assert re.fullmatch(
        r"\*\*Nothing has been sent\.\*\* Preview: \d{1,2} [A-Z][a-z]{2} \d\d:\d\d UTC\.",
        first,
    )
    assert second == (
        "This is what running Distribute grades for real (with `preview` unticked) "
        "would do now."
    )
    assert third == "Each preview replaces this text; the real run closes this issue."


def test_a_preview_with_nothing_to_report_keeps_every_heading(tmp_path, monkeypatch):
    body = _after_one_real_run(monkeypatch, tmp_path, _SHEET)["preview"][0]["body"]
    for heading, nothing in (
        (
            "### ⚠️ Fix these first - held back, not sent (0)",
            "Nothing - no mark is held back.",
        ),
        ("### Not marked yet (0)", "Nothing - every row in every sheet has a mark."),
        (
            "### Grades that would change (0)",
            "Nothing new or changed since grades were last sent.",
        ),
        (
            "### Students who would be emailed (0)",
            "Nothing - nobody has a new mark to be told about.",
        ),
    ):
        assert f"{heading}\n{nothing}" in body
    assert "<details>" not in body


def test_a_silent_preview_says_why_nobody_is_emailed(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, dry_run=True, notify=False)
    assert (
        "### Students who would be emailed (0)\n"
        "Nothing - `notify` is unticked, so nobody is emailed."
    ) in out["preview"][0]["body"]


@pytest.mark.parametrize(
    ("sheet", "why"),
    [
        (
            _SHEET.replace("score_individual: 43", "score_individual: 45"),
            "a grade changed",
        ),
        (_SHEET.replace("Clean derivation.", "Clean; see Q3."), "feedback changed"),
    ],
    ids=["grade", "feedback"],
)
def test_the_preview_says_why_each_student_would_be_emailed(
    tmp_path, monkeypatch, sheet, why
):
    body = _after_one_real_run(monkeypatch, tmp_path, sheet)["preview"][0]["body"]
    grade = "45" if why == "a grade changed" else "43"
    assert (
        f"### Students who would be emailed (1)\n- `ada-l` (Ada) - {why}.\n"
        f"  Their gradebook shows: assignment-1 {grade}\n" in body
    )
    changes = "(1)\n- **assignment-1** · `ada-l` (Ada) · 45 (was 43)"
    assert (changes in body) == (why == "a grade changed")
    assert "<summary>The email they would get</summary>" in body


def test_a_handle_in_two_teams_is_a_fix_the_preview_names(tmp_path, monkeypatch):
    sheet = _TEAM_SHEET + (
        "  beta:\n"
        "    score_group: 20\n"
        "    members:\n"
        "      ada-l:\n"
        "        adjustment_individual:\n"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
        dry_run=True,
    )
    assert (
        "- **assignment-1** · `ada-l` (Ada) is in more than one team. Fix it in "
        "`grading_sheets/assignment-1.yml`." in out["preview"][0]["body"]
    )


def test_the_preview_reads_as_plain_english(tmp_path, monkeypatch):
    # Faculty read this, not the pipeline: no "(s)", and none of the toolkit's own words.
    rows = ROSTER_ADA + "bob@uni.edu,Bob Byte,enrolled,bob-b,43,dsl-def\n"
    sheet = _SHEET + _SHEET.split("submissions:\n", 1)[1].replace("ada-l", "bob-b")
    half = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": sheet,
            "assignment-2": _HELD_SHEET.replace("ada-l", "bob-b"),
            "assignment-3": half,
        },
        grading=_GRADING_YML,
        roster_rows=rows,
        dry_run=True,
    )
    body = out["preview"][0]["body"]
    for word in ("(s)", "unit", "derived", "digest", "@ada-l", "@bob-b"):
        assert word not in body


def test_the_dry_run_only_says_a_column_is_new_when_it_is(
    tmp_path, monkeypatch, capsys
):
    _after_one_real_run(monkeypatch, tmp_path, _SHEET)
    assert "would gain column" not in capsys.readouterr().out


def test_a_real_run_closes_the_preview_with_a_line_saying_so(tmp_path, monkeypatch):
    store: list[dict] = []
    _distribute(monkeypatch, tmp_path, dry_run=True, preview_issues=store)
    out = _distribute(monkeypatch, tmp_path / "real", preview_issues=store)
    assert out["rc"] == 0
    ((issue),) = store
    assert issue["state"] == "CLOSED"
    assert issue["comments"] == [distribute.PREVIEW_SENT]


def test_a_real_run_with_no_preview_open_is_fine(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path)
    assert out["rc"] == 0
    assert out["preview"] == []


def test_the_dry_run_counts_what_it_would_hold(tmp_path, monkeypatch, capsys):
    _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": _HELD_SHEET}, dry_run=True
    )
    printed = capsys.readouterr().out
    assert "assignment-1: 1 student(s) · 0 final grade(s) derived" in printed
    assert "1 held for a hand decision" in printed
    assert "would update 0 gradebook(s) and email 0 student(s)" in printed


def test_a_dry_run_writes_nothing_posts_nothing_and_sends_nothing(
    tmp_path, monkeypatch, capsys
):
    out = _distribute(monkeypatch, tmp_path, dry_run=True)
    assert out["rc"] == 0
    assert (out["comments"], out["gradebooks"], out["config"], out["issues"]) == (
        [],
        [],
        [],
        [],
    )
    assert out["outbox"] == []
    printed = capsys.readouterr().out
    assert "assignment-1: 1 student(s) · 1 final grade(s) derived" in printed
    assert "0 held for a hand decision" in printed
    assert f"{gradebook.SEMESTER_CSV_NAME}: would gain column assignment-1" in printed
    assert "would update 1 gradebook(s) and email 1 student(s)" in printed


def test_a_sheet_that_does_not_parse_sends_nothing_at_all(tmp_path, monkeypatch):
    # Not "everything except that one": a partial distribution has to be reconciled
    # student by student, where a refused one is fixed in the file and pressed again.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": "submissions:\n  bad: [\n"},
    )
    assert out["rc"] == 1
    assert out["comments"] == [] and out["gradebooks"] == []
    assert out["config"] == [] and out["outbox"] == []


def test_the_record_survives_one_lost_race_with_the_cron(tmp_path, monkeypatch, capsys):
    # The quarter-hourly scheduler commits into the same repo all through the term, and the
    # ref update is not forced. Losing that race cost the RECORD, not a file: comments and
    # gradebooks are guarded by their own content, the emails only by `distributed.csv`, so
    # the next run mailed the whole semester again.
    tries = {"n": 0}

    def refuse_the_first_record(files):
        if gradebook.DISTRIBUTED_PATH not in files:
            return True  # the gradebook writes are not in the race
        tries["n"] += 1
        return tries["n"] > 1

    out = _distribute(monkeypatch, tmp_path, put_files_ok=refuse_the_first_record)
    assert tries["n"] == 2  # refused once, rebuilt against a fresh head, landed
    assert out["rc"] == 0
    assert "rebuilding the record commit" in capsys.readouterr().out


def test_a_record_that_cannot_be_written_twice_still_reds_the_run(
    tmp_path, monkeypatch
):
    # Two attempts, not a ladder: a second loss is a fault to report.
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1


def test_a_re_run_says_nothing_twice(tmp_path, monkeypatch):
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
    )
    assert again["comments"] == []
    assert again["gradebooks"] == []
    assert again["outbox"] == []
    assert again["rc"] == 0


def test_a_corrected_grade_reaches_that_student_and_only_them(tmp_path, monkeypatch):
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    corrected = _SHEET.replace("score_individual: 43", "score_individual: 45")
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": corrected},
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
    )
    ((_repo, files, _delete),) = again["gradebooks"]  # exactly one gradebook
    assert "45" in files["README.md"]
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


@pytest.mark.parametrize(
    "grading",
    [_GRADING_YML, _GRADING_YML + "submit_via: external\n"],
    ids=["assignment_repo", "external"],
)
def test_a_student_is_emailed_on_the_run_that_brings_their_first_mark(
    tmp_path, monkeypatch, grading
):
    # A row with nothing typed in it still renders a gradebook - the points available, a
    # submission time - but "your grades have been updated" would send a student to read
    # nothing. So no mail, and no record of one: the run that brings the mark tells them.
    unmarked = _SHEET.replace("score_individual: 43", "score_individual:").replace(
        "feedback_individual: |\n      Clean derivation.\n", "feedback_individual:\n"
    )
    first = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": unmarked}, grading=grading
    )
    assert first["rc"] == 0
    assert [repo for repo, _files, _d in first["gradebooks"]] == ["grades-ada-l"]
    assert first["outbox"] == []
    ((_cfg, cfg_files, _d),) = first["config"]
    record = gradebook.parse_distributed(cfg_files[gradebook.DISTRIBUTED_PATH])
    assert ("ada-l", "", gradebook.CHANNEL_EMAIL) not in record

    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": _SHEET},
        grading=grading,
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_reworded_gradebook_page_is_committed_and_nobody_is_emailed(
    tmp_path, monkeypatch
):
    # The two channels are keyed on different things on purpose. The COMMIT is keyed on
    # the whole book, so a change the toolkit makes to the page's own standing text lands
    # in every gradebook; the EMAIL is keyed on what a grader wrote, so "there is something
    # new to read" still means a mark moved. On one hash, adding a sentence to the README re-mailed
    # every student in every live semester to tell them nothing.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    page = gradebook.render_readme
    monkeypatch.setattr(
        distribute,
        "render_readme",
        lambda *a, **k: (
            page(*a, **k) + "\n## Keeping your work\nA new standing section.\n"
        ),
    )
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
    )
    ((_repo, files, _delete),) = again["gradebooks"]
    assert "A new standing section." in files["README.md"]
    assert again["outbox"] == []


_EMAIL = ("ada-l", "", gradebook.CHANNEL_EMAIL)


def _first_run(monkeypatch, tmp_path, **kwargs) -> tuple[str, str, str]:
    """`(the record, the versioned email digest in it, its grades.yml's LEGACY digest)`
    after one real run - what a semester holds once Ada has been told."""
    first = _distribute(monkeypatch, tmp_path, **kwargs)
    ((_repo, book, _d),) = first["gradebooks"]
    ((_cfg, cfg_files, _e),) = first["config"]
    record = cfg_files[gradebook.DISTRIBUTED_PATH]
    told = gradebook.parse_distributed(record)[_EMAIL][0]
    return record, told, gradebook.content_hash(book["grades.yml"])


def _told_as(record: str, digest: str) -> str:
    """`record` with Ada's email row holding `digest` - the row an older run wrote."""
    rows = gradebook.parse_distributed(record)
    rows[_EMAIL] = (digest, "2026-09-01T00:00:00", "")
    return gradebook.dump_distributed(rows)


def test_a_legacy_email_row_under_a_changed_mark_mails_once(tmp_path, monkeypatch):
    # A legacy row (a hash of the whole grades.yml) is not the marks digest, so it is
    # mailed about - once, and recorded under the new digest.
    record, _told, legacy = _first_run(monkeypatch, tmp_path)
    corrected = {
        "assignment-1": _SHEET.replace("score_individual: 43", "score_individual: 45")
    }
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets=corrected,
        distributed=_told_as(record, legacy),
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]
    ((_cfg, files, _d),) = again["config"]
    now = gradebook.parse_distributed(files[gradebook.DISTRIBUTED_PATH])[_EMAIL][0]
    assert now.startswith(distribute.MARKS_DIGEST_PREFIX)
    third = _distribute(
        monkeypatch,
        tmp_path / "third",
        sheets=corrected,
        distributed=files[gradebook.DISTRIBUTED_PATH],
    )
    assert third["outbox"] == []


def _respelt(monkeypatch) -> None:
    # What PR #289 did to the Submitted column: the same moment, spelt `3rd Oct`.
    shown = gradebook._submitted_display
    monkeypatch.setattr(
        gradebook,
        "_submitted_display",
        lambda *a, **k: shown(*a, **k).replace("3 Oct", "3rd Oct"),
    )


def _new_facts(monkeypatch) -> dict[str, str]:
    # A later push: a new submission time and a day late, under a course with no late
    # penalty, so the mark itself does not move.
    return {
        "assignment-1": _SHEET.replace("2026-10-03T22:14", "2026-10-05T09:00").replace(
            "days_late: 0", "days_late: 1"
        )
    }


@pytest.mark.parametrize("change", [_new_facts, _respelt], ids=["facts", "respelt"])
def test_a_changed_fact_rewrites_the_gradebook_and_mails_nobody(
    tmp_path, monkeypatch, change
):
    grading = "title: Neural networks\nlate_penalty_per_day: 0%\n"
    record, _told, _legacy = _first_run(monkeypatch, tmp_path, grading=grading)
    sheets = change(monkeypatch)
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets=sheets,
        grading=grading,
        distributed=record,
    )
    ((_repo, book, _d),) = again["gradebooks"]  # the page does move...
    assert ("5 Oct" if sheets else "3rd Oct") in book["README.md"]
    assert again["outbox"] == []  # ...and nobody is told to go and read it


def test_changed_feedback_under_a_versioned_row_mails(tmp_path, monkeypatch):
    record, _told, _legacy = _first_run(monkeypatch, tmp_path)
    reworded = _SHEET.replace("Clean derivation.", "Clean derivation; see Q3.")
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": reworded},
        distributed=record,
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_the_dry_run_says_what_the_real_run_would_send(tmp_path, monkeypatch, capsys):
    # The preview's "emailed" is the same decision the send takes, so a moved fact
    # previews as nobody mailed - and a dry run records nothing.
    grading = "title: Neural networks\nlate_penalty_per_day: 0%\n"
    record, _told, _legacy = _first_run(monkeypatch, tmp_path, grading=grading)
    out = _distribute(
        monkeypatch,
        tmp_path / "dry",
        sheets=_new_facts(monkeypatch),
        grading=grading,
        distributed=record,
        dry_run=True,
    )
    assert out["config"] == [] and out["outbox"] == []
    body = out["preview"][0]["body"]
    assert "### Students who would be emailed (0)" in body and "Subject:" not in body
    assert "would update 1 gradebook(s)" in capsys.readouterr().out


def test_the_registrar_export_is_written_only_on_a_real_run(tmp_path, monkeypatch):
    assert _distribute(monkeypatch, tmp_path, dry_run=True)["config"] == []
    ((_cfg, files, _d),) = _distribute(monkeypatch, tmp_path / "real")["config"]
    csv_text = files[gradebook.SEMESTER_CSV_NAME]
    assert csv_text.splitlines()[0] == "hertie_email,name,github_handle,assignment-1"


def test_the_migration_off_notified_csv_happens_in_one_commit(tmp_path, monkeypatch):
    # The old marker and the dead per-student YAML go in the SAME commit that writes the
    # new record, so no reader ever sees both and has to choose.
    out = _distribute(
        monkeypatch,
        tmp_path,
        notified=(
            "github_handle,grades_sha,notified_at\n"
            "ada-l,anoldsha,2026-08-31T09:00:00+00:00\n"
        ),
        stale_gradebooks=("ada-l.yml",),
        notify=False,  # so the carried-over row is visible, not overwritten
    )
    ((_cfg, files, delete),) = out["config"]
    assert gradebook.DISTRIBUTED_PATH in files
    assert set(delete) == {
        gradebook.NOTIFIED_PATH,
        f"{gradebook.GRADEBOOK_DIR}/ada-l.yml",
    }
    # The email rows carried over, so nobody is re-told about a book they already know
    # about beyond the one re-hash this migration costs.
    assert "ada-l,,email,anoldsha," in files[gradebook.DISTRIBUTED_PATH]


def test_the_dead_per_student_yaml_goes_whether_or_not_this_is_the_migration(
    tmp_path, monkeypatch
):
    # A semester that reached `distributed.csv` without ever having had a `notified.csv` was
    # never on the migration path, so its `.system/gradebook/*.yml` was left in place for the rest
    # of the term - a stale copy of a grade beside the repo that holds the real one.
    out = _distribute(
        monkeypatch,
        tmp_path,
        stale_gradebooks=("ada-l.yml", "bo-b.yml"),
    )
    ((_cfg, files, delete),) = out["config"]
    assert set(delete) == {
        f"{gradebook.GRADEBOOK_DIR}/ada-l.yml",
        f"{gradebook.GRADEBOOK_DIR}/bo-b.yml",
    }
    # `distributed.csv` lives in the same folder and is the one file still read there.
    assert gradebook.DISTRIBUTED_PATH in files


_EXTERNAL_GRADING = _GRADING_YML + "submit_via: external\n"

# The four shapes with no Submission receipts issue of their own. They used to be the interesting
# half of distribute - each one a thread it must not post into - and they are ordinary
# now: a mark goes to the gradebook whatever the shape, so there is one thing to prove.
_NO_THREAD_SHAPES = (
    "visibility: public\n",
    "visibility: student_choice\n",
    "submit_via: shared_dropbox_repo\n",
    "submit_via: external\n",
)


@pytest.mark.parametrize("shape", _NO_THREAD_SHAPES)
def test_every_shape_sends_its_mark_to_the_gradebook_and_nowhere_else(
    tmp_path, monkeypatch, shape
):
    # `found_issue` is what a lookup would answer, and `listed` carries the repo as
    # private: every excuse for the run to touch a thread is present, and it touches none.
    out = _distribute(
        monkeypatch,
        tmp_path,
        grading=_GRADING_YML + shape,
        found_issue=7,
        listed={"assignment-1-ada-l": repo_row("assignment-1-ada-l")},
    )
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    ((gb_repo, files, _delete),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l" and "43" in files["README.md"]


def test_a_repo_the_listing_calls_public_is_no_longer_a_reason_to_hold_anything(
    tmp_path, monkeypatch
):
    # A `visibility:` edited after handout used to decide whether a mark could be posted
    # at all. It decides nothing here: the mark was never going to a repo.
    out = _distribute(
        monkeypatch,
        tmp_path,
        found_issue=7,
        listed={
            "assignment-1-ada-l": repo_row("assignment-1-ada-l", visibility="public")
        },
    )
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


def test_a_listing_that_could_not_be_read_still_sends_every_mark(tmp_path, monkeypatch):
    # An API blip used to cost a semester its feedback for a tick. The listing answers one
    # question now - has this student a gradebook yet - and `ensure_gradebooks` handles
    # not being told.
    out = _distribute(monkeypatch, tmp_path, listed=None)
    assert out["rc"] == 0
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


def test_a_record_written_while_marks_were_commented_still_reads(tmp_path, monkeypatch):
    # A live semester's `distributed.csv` carries `issue` rows from before marks moved to
    # the gradebook alone. They are read, they keep their columns, and nothing acts on
    # them - the run must not trip over a channel it no longer writes.
    recorded = (
        "target,assignment,channel,content_hash,distributed_at,issue\n"
        "ada-l,assignment-1,issue,anoldhash,2026-10-05T00:00:00,7\n"
    )
    out = _distribute(monkeypatch, tmp_path, distributed=recorded, found_issue=7)
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    ((_cfg, files, _d),) = out["config"]
    written = gradebook.parse_distributed(files[gradebook.DISTRIBUTED_PATH])
    # The old row is still there, unchanged, and the gradebook row is new beside it.
    assert written[("ada-l", "assignment-1", gradebook.CHANNEL_ISSUE)] == (
        "anoldhash",
        "2026-10-05T00:00:00",
        "7",
    )
    assert ("ada-l", "", gradebook.CHANNEL_GRADEBOOK) in written


# -------------------------------------------- marks sent before the due date has passed

# A unit the toolkit has looked at and found nothing for yet: `info:` is there, because
# the sheet is created at handout, and empty, because nothing derives it before the due
# date.
_UNDERIVED_SHEET = """\
submissions:
  ada-l:
    info:
      submitted:
      days_late:
    score_individual: 43
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
"""


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_mark_sent_before_the_due_date_is_counted_and_said_out_loud(
    tmp_path, monkeypatch, capsys, dry_run
):
    # The gradebook row reads `not submitted` while the work is sitting in the repo,
    # because nothing fills `info:` until the due date has passed. A grader may mean to
    # send it, so this is a warning and a count, in both runs, and never a refusal.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNDERIVED_SHEET},
        dry_run=dry_run,
        due=_DUE_AHEAD,
    )
    printed = capsys.readouterr().out
    assert (
        "WARNING: 1 mark(s) sent before the due date - the submission facts "
        "are not derived yet" in printed
    )
    assert out["rc"] == 0  # counted, not blocked


def test_nothing_is_warned_about_once_the_due_date_has_gone_by(
    tmp_path, monkeypatch, capsys
):
    # Past the due date a blank `submitted` is a fact about the student, not about the
    # clock: they handed nothing in, and saying "the facts are not derived yet" would
    # send a grader looking for a bug in the toolkit.
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _UNDERIVED_SHEET})
    assert "before the due date" not in capsys.readouterr().out


def test_an_assignment_that_collects_nothing_is_never_warned_about(
    tmp_path, monkeypatch, capsys
):
    # `external` has no commit to time at any moment, so its blank `submitted` is the
    # shape of the assignment and the gradebook says `external` rather than `not
    # submitted`. A due date still ahead changes nothing about it.
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNDERIVED_SHEET},
        grading=_EXTERNAL_GRADING,
        due=_DUE_AHEAD,
    )
    assert "before the due date" not in capsys.readouterr().out


def test_distribute_provisions_the_gradebooks_with_no_time_budget(
    tmp_path, monkeypatch
):
    # The nightly sync is the ONE caller that bounds `ensure_gradebooks`. This run is
    # about to write a mark into every one of these repos, so one it stopped short of
    # would be a 404 per student in a public log, not a batch for tomorrow.
    assert _distribute(monkeypatch, tmp_path)["gradebook_calls"] == [{}]


# A handle no roster row claims. A sheet is hand-typed, and the demo org carried six of
# these: `ensure_gradebooks` had provisioned nothing for them, so every one was a 404
# naming `grades-<handle>` in a PUBLIC log.
_UNKNOWN_IN_THE_SHEET = (
    _SHEET
    + """\
  zed-z:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_individual: 30
"""
)
# A second assignment nobody on the roster is in, so two unknown marks are counted.
_ONLY_A_STRANGER = _SHEET.replace("ada-l:", "mallory-m:")


def test_marks_for_handles_not_on_the_roster_are_dropped_not_pushed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": _UNKNOWN_IN_THE_SHEET,
            "assignment-2": _ONLY_A_STRANGER,
        },
    )
    assert out["rc"] == 0
    # One gradebook, for the one student the roster has - not three.
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]
    printed = capsys.readouterr()
    assert '"unknown": 2' in printed.out
    for stranger in ("zed-z", "mallory-m"):
        assert stranger not in printed.out + printed.err


def test_the_dry_run_says_how_many_marks_no_roster_row_claims(
    tmp_path, monkeypatch, capsys
):
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": _UNKNOWN_IN_THE_SHEET,
            "assignment-2": _ONLY_A_STRANGER,
        },
        dry_run=True,
    )
    assert (
        "2 mark(s) for handles not on the roster - ignored" in capsys.readouterr().out
    )


def test_the_verbose_log_still_says_which_handles_were_dropped(
    tmp_path, monkeypatch, capsys
):
    # Moved, not thrown away: a grader who typed a handle wrong has to be able to find it.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET})
    assert "zed-z is not an onboarded student" in capsys.readouterr().out


def test_an_unreadable_roster_drops_nobody(tmp_path, monkeypatch, capsys):
    # None means the file could not be READ. Reading it as "nobody is enrolled" would
    # withhold the whole semester's grades on a transient failure.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET},
        roster_rows=None,
    )
    assert sorted(repo for repo, _f, _d in out["gradebooks"]) == [
        "grades-ada-l",
        "grades-zed-z",
    ]
    assert '"unknown": 0' in capsys.readouterr().out


def test_an_auditor_with_a_mark_typed_in_gets_no_gradebook(tmp_path, monkeypatch):
    # Auditors are never assessed and `ensure_gradebooks` makes them no repo, so a mark
    # typed against one has nowhere to go.
    out = _distribute(
        monkeypatch,
        tmp_path,
        roster_rows=ROSTER_ADA + "zed@uni.edu,Zed,auditor,zed-z,43,dsl-zzz\n",
        sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET},
    )
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


def test_the_public_log_carries_counts_and_no_student(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    _distribute(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert '"gradebooks": 1' in out
    assert "ada-l" not in out and "ada@uni.edu" not in out


def test_an_unsent_notification_reddens_the_run_and_is_retried(tmp_path, monkeypatch):
    # The grades are out by this point, so nothing is undone - but a student who never got
    # the mail does not know to look, and the record must not claim they were told.
    first = _distribute(monkeypatch, tmp_path, sent=0)
    assert first["rc"] == 1
    ((_cfg, files, _d),) = first["config"]
    assert ",email," not in files[gradebook.DISTRIBUTED_PATH]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=files[gradebook.DISTRIBUTED_PATH],
        sent=1,
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_gradebook_that_failed_to_write_is_not_emailed_about(tmp_path, monkeypatch):
    # "Your grades have been updated" over a push that did not land tells a student to go
    # and read a page that has not changed - and records the telling, so the run that
    # finally lands the page says nothing at all.
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1
    assert out["outbox"] == []


def test_no_notify_sends_nothing_and_stays_green(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, notify=False)
    assert out["outbox"] == [] and out["rc"] == 0


def test_distribute_reds_when_the_record_could_not_be_written(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1


def test_a_mark_no_roster_row_claims_is_counted_not_fatal(
    tmp_path, monkeypatch, capsys
):
    # The sheet marks ada-l and the roster has never heard of them: counted and dropped,
    # rather than pushed at a `grades-ada-l` that was never provisioned.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch, tmp_path, roster_rows="\nbo@uni.edu,Bo,enrolled,bo-b,7,dsl-x\n"
    )
    assert out["rc"] == 0
    assert out["gradebooks"] == [] and out["outbox"] == []
    printed = capsys.readouterr()
    assert '"unknown": 1' in printed.out
    assert "ada-l" not in printed.out + printed.err


def test_a_roster_row_with_no_email_is_counted_not_fatal(tmp_path, monkeypatch, capsys):
    # On the roster, so the grade is distributed - but there is no address to tell them at,
    # which is an ordinary state (a withdrawn student) and must not red every run from here
    # on.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch, tmp_path, roster_rows="\n,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    assert out["rc"] == 0
    err = capsys.readouterr().err
    assert "no roster row with an email" in err and "ada-l" not in err


def test_a_dry_run_reds_when_the_roster_cannot_be_read(tmp_path, monkeypatch):
    # `ensure_gradebooks` skips an unreadable roster (the nightly sync runs it too), so
    # distribute says so itself - on the dry run as well as on the real one.
    assert _distribute(monkeypatch, tmp_path, roster_rows=None, dry_run=True)["rc"] == 1


def test_distribute_reds_when_the_roster_cannot_be_read(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, roster_rows=None)
    assert out["rc"] == 1
    assert "could not be read" in capsys.readouterr().err
    # And the registrar export is LEFT ALONE. It is one row per enrolled student, so
    # regenerating it from a roster nobody could read would commit a header line over the
    # file a registrar transcribes grades from.
    ((_repo, files, _delete),) = out["config"]
    assert gradebook.SEMESTER_CSV_NAME not in files
    assert gradebook.DISTRIBUTED_PATH in files


def test_distribute_reds_on_a_header_only_roster_and_leaves_the_export(
    tmp_path, monkeypatch, capsys
):
    # `ensure_gradebooks` passes over an EMPTY roster as well as an unreadable one (a
    # freshly bootstrapped semester is not a failure), so by the time marks exist distribute
    # has to say so itself - and the export is one row per enrolled student, so rebuilding
    # it from no rows would commit a header line over the file a registrar transcribes
    # grades from.
    out = _distribute(monkeypatch, tmp_path, roster_rows="")
    assert out["rc"] == 1
    ((_repo, files, _delete),) = out["config"]
    assert gradebook.SEMESTER_CSV_NAME not in files
    assert gradebook.DISTRIBUTED_PATH in files


def test_a_dry_run_reds_on_a_header_only_roster_too(tmp_path, monkeypatch):
    assert _distribute(monkeypatch, tmp_path, roster_rows="", dry_run=True)["rc"] == 1


def test_a_scoped_real_return_records_that_assignment_as_returned(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-1")
    ((_cfg, cfg_files, _d),) = out["config"]
    assert distribute.marks_return_record("assignment-1") in cfg_files


def test_a_return_marks_dispatch_sends_only_what_is_due_and_marked(monkeypatch):
    # The payload is written by whoever holds a bot token: nothing in it is trusted.
    monkeypatch.setattr(distribute, "discover_semesters", lambda org: ["Sem"])
    monkeypatch.setattr(
        distribute.schedule, "load", lambda org: _schedule_with("a1", "a2")
    )
    monkeypatch.setattr(distribute, "marks_due", lambda c, s, sched, now: ([], ["a1"]))
    assert distribute.dispatch_refusal("C", "sem", "a1")[0] == ""
    assert "not due" in distribute.dispatch_refusal("C", "Sem", "a2")[0]
    assert "not a semester" in distribute.dispatch_refusal("C", "Other", "a1")[0]


def test_a_dispatched_return_reads_the_schedule_once(monkeypatch):
    # The dispatch check and the return both need the plan: the check hands on what it
    # read, so one run reads schedule.yml once.
    loads: list[str] = []
    sched = _schedule_with("a1")
    monkeypatch.setattr(distribute, "discover_semesters", lambda org: ["Sem"])
    monkeypatch.setattr(
        distribute.schedule, "load", lambda org: loads.append(org) or sched
    )
    monkeypatch.setattr(distribute, "marks_due", lambda c, s, sched, now: ([], ["a1"]))
    given: dict = {}
    monkeypatch.setattr(grades, "distribute", lambda org, **kw: given.update(kw) or 0)
    monkeypatch.setattr(
        sys,
        "argv",
        ["grades", "distribute", "--semester-org", "Sem", "--assignment", "a1"]
        + ["--dispatched-by", "C"],
    )
    assert grades.main() == 0
    assert loads == ["Sem"] and given["sched"] is sched


# ------------------------------------------------ the record written before the emails


def test_the_emails_are_recorded_before_they_are_sent(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path)
    ((intent),) = out["intent"]
    assert ",email," in intent[gradebook.DISTRIBUTED_PATH]
    assert [m[0] for batch in out["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_lost_final_record_re_mails_nobody(tmp_path, monkeypatch):
    # The register's case: the mail went and `distributed.csv` would not land. Every
    # following run (and the automatic return, every quarter-hour) mailed everyone again.
    first = _distribute(
        monkeypatch,
        tmp_path,
        put_files_ok=lambda files: gradebook.DISTRIBUTED_PATH not in files,
    )
    assert first["rc"] == 1 and first["outbox"]
    ((intent),) = first["intent"]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=intent[gradebook.DISTRIBUTED_PATH],
    )
    assert again["outbox"] == []


def test_no_email_goes_when_the_record_before_it_cannot_be_written(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, intent_ok=False)
    assert out["rc"] == 1
    assert out["outbox"] == [] and out["config"] == []


_TWO_SHEET = (
    _SHEET
    + """\
  ben-k:
    score_individual: 40
    adjustment_individual:
    feedback_individual:
    notes_not_shared_with_students:
"""
)
_TWO_ROSTER = ROSTER_ADA + "ben@uni.edu,Ben,enrolled,ben-k,43,dsl-abd\n"


def test_one_refused_address_does_not_hold_back_the_returned_record(
    tmp_path, monkeypatch
):
    # A refused address in a class that was mailed would otherwise keep the automatic
    # return asking, and failing, every quarter-hour; its row stays untold, so the next
    # Return marks run retries it.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TWO_SHEET},
        roster_rows=_TWO_ROSTER,
        assignment="assignment-1",
        sent=1,
    )
    assert out["rc"] == 1
    ((_cfg, cfg_files, _d),) = out["config"]
    assert distribute.marks_return_record("assignment-1") in cfg_files
    record = cfg_files[gradebook.DISTRIBUTED_PATH]
    assert "ada-l,,email," in record and "ben-k,,email," not in record


def test_no_email_sent_at_all_leaves_the_assignment_to_be_returned_again(
    tmp_path, monkeypatch
):
    # Every send failed (no mail configured, a token fault): the automatic return asks
    # again, and the rows are reset, so it cannot mail anybody twice.
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-1", sent=0)
    assert out["rc"] == 1
    ((_cfg, cfg_files, _d),) = out["config"]
    assert distribute.marks_return_record("assignment-1") not in cfg_files
    assert ",email," not in cfg_files[gradebook.DISTRIBUTED_PATH]


def test_a_send_that_raises_resets_the_recorded_rows(tmp_path, monkeypatch):
    # `_send_via_graph` raises on a token failure. Before, the rows recorded before the
    # mail stayed "told": the next run said "No new marks to return" and nobody was
    # ever mailed.
    first = _distribute(
        monkeypatch,
        tmp_path,
        assignment="assignment-1",
        send_error=RuntimeError("Graph token refused"),
    )
    assert "raised" in first
    ((intent),) = first["intent"]
    assert ",email," in intent[gradebook.DISTRIBUTED_PATH]
    ((_cfg, cfg_files, _d),) = first["config"]
    assert ",email," not in cfg_files[gradebook.DISTRIBUTED_PATH]
    assert distribute.marks_return_record("assignment-1") not in cfg_files
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
        exported=cfg_files[gradebook.SEMESTER_CSV_NAME],
        assignment="assignment-1",
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_scoped_return_that_died_after_the_first_record_is_not_wedged(
    tmp_path, monkeypatch
):
    # The first record carries the registrar export: a scoped run refuses while
    # `distributed.csv` has rows and the export is missing.
    first = _distribute(
        monkeypatch,
        tmp_path,
        assignment="assignment-1",
        put_files_ok=lambda files: gradebook.DISTRIBUTED_PATH not in files,
    )
    ((intent),) = first["intent"]
    assert gradebook.SEMESTER_CSV_NAME in intent
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=intent[gradebook.DISTRIBUTED_PATH],
        exported=intent[gradebook.SEMESTER_CSV_NAME],
        assignment="assignment-1",
    )
    assert again["rc"] == 0
    ((_cfg, cfg_files, _d),) = again["config"]
    assert distribute.marks_return_record("assignment-1") in cfg_files


def test_a_student_with_no_roster_email_is_not_recorded_before_the_mail(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        roster_rows="\n,Ada,enrolled,ada-l,42,dsl-abc\n",
    )
    assert out["gradebooks"]  # marked and written ...
    assert out["intent"] == [] and out["outbox"] == []  # ... and nobody to tell


# ------------------------------------------------------------- per-question feedback

_QUESTIONS_GRADING = _GRADING_YML + "questions:\n  Q1: 30\n  Q2: 20\n"
_QUESTION_SHEET = """\
submissions:
  ada-l:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
    notes_not_shared_with_students: chased by email
    score_individual:
      Q1: 25
      Q2: 18
    feedback_per_question:
      Q1:
      Q2: |
        The bound is loose.
        Tighten it with the second lemma.
"""


def test_per_question_feedback_reaches_the_gradebook_under_each_question(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _QUESTION_SHEET},
        grading=_QUESTIONS_GRADING,
    )
    ((_repo, files, _d),) = out["gradebooks"]
    book = yaml.safe_load(files["grades.yml"])["assignments"]["assignment-1"]
    # a blank cell is not sent
    assert book[grading_sheet.QUESTION_FEEDBACK_KEY] == {
        "Q2": "The bound is loose.\nTighten it with the second lemma."
    }
    readme = files["README.md"]
    assert "Clean derivation." in readme
    assert (
        "- **Q2:** The bound is loose.\n  Tighten it with the second lemma." in readme
    )
    assert "**Q1:**" not in readme


def test_the_marks_email_lists_overall_then_per_question_feedback(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _QUESTION_SHEET},
        grading=_QUESTIONS_GRADING,
        include_feedback=True,
    )
    ((message,),) = out["outbox"]
    body = message[2]
    assert body.index("Clean derivation.") < body.index("Q2: The bound is loose.")


def test_the_preview_counts_feedback_on_an_undeclared_question(
    tmp_path, monkeypatch, capsys
):
    sheet = _QUESTION_SHEET.replace("      Q1:\n", "      Q9: stray\n")
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_QUESTIONS_GRADING,
        dry_run=True,
    )
    assert "WARNING: 1 unit(s) give feedback on a question" in capsys.readouterr().out


def test_a_held_mark_keeps_the_assignment_to_be_returned_again(tmp_path, monkeypatch):
    # A typed-but-held mark (a Unicode minus) must not let the once-only record land:
    # the automatic return would skip the assignment for good and that student would
    # never get their mark. The rerun after the fix sends only what had not gone out.
    held = _TWO_SHEET.replace(
        "score_individual: 40\n    adjustment_individual:\n",
        "score_individual: 40\n    adjustment_individual: −3\n",
    )
    assert held != _TWO_SHEET
    first = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": held},
        roster_rows=_TWO_ROSTER,
        assignment="assignment-1",
    )
    ((_cfg, cfg_files, _d),) = first["config"]
    assert distribute.marks_return_record("assignment-1") not in cfg_files
    assert [m[0] for batch in first["outbox"] for m in batch] == ["ada@uni.edu"]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": _TWO_SHEET},
        roster_rows=_TWO_ROSTER,
        distributed=cfg_files[gradebook.DISTRIBUTED_PATH],
        exported=cfg_files[gradebook.SEMESTER_CSV_NAME],
        assignment="assignment-1",
    )
    assert [repo for repo, _f, _d in again["gradebooks"]] == ["grades-ben-k"]
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ben@uni.edu"]
    ((_cfg, cfg_files, _d),) = again["config"]
    assert distribute.marks_return_record("assignment-1") in cfg_files


def test_a_refused_definition_returns_no_marks(tmp_path, monkeypatch):
    # A NOT_MIGRATED grading_config.yml reads as a default spec: individual, and the
    # institution's late rule. Nothing goes out off it, and nothing is recorded returned.
    monkeypatch.setattr(
        grading_spec, "assignment_title", lambda org, repo, spec, name: name
    )
    out = _distribute(
        monkeypatch, tmp_path, grading="format: notebook\n", assignment="assignment-1"
    )
    assert out["rc"] == 1
    assert out["gradebooks"] == [] and out["config"] == [] and out["outbox"] == []


def test_a_refused_definition_is_not_due_for_return(monkeypatch):
    sched = Schedule(
        assignments={
            "assignment-1": AssignmentEntry(
                course_source_repo="assignment-1-f2026",
                due_datetime=_DUE_PASSED,
                marks_return_datetime=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        },
        org="SEMESTER",
    )
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, tpl: "format: notebook\n"
    )

    def read(org, repo, path, **k):
        return None if "marks" in path else _SHEET

    # The sheet and its marker (`marks_due`), and the template's README (the title).
    monkeypatch.setattr(distribute, "get_file_content", read)
    monkeypatch.setattr(grading_spec, "get_file_content", read)
    faults, ready = distribute.marks_due(
        "COURSE", "SEMESTER", sched, datetime(2026, 10, 5, tzinfo=timezone.utc)
    )
    assert ready == [] and faults == []


def _book_row(record: str, handle: str) -> tuple[str, str, str]:
    return gradebook.parse_distributed(record)[
        (handle, "", gradebook.CHANNEL_GRADEBOOK)
    ]


def test_a_sheet_edit_that_moves_no_mark_keeps_the_marks_returned(
    tmp_path, monkeypatch
):
    # A comment committed after the return rewrites no gradebook, so the record's moment
    # stayed behind the sheet's last commit and the console read `marking` for good. The
    # next run re-stamps it - and sends nothing.
    record, _told, _legacy = _first_run(monkeypatch, tmp_path)
    old = _book_row(record, "ada-l")
    edited = datetime.now(timezone.utc) + timedelta(seconds=-1)
    monkeypatch.setattr(distribute, "git", lambda *a, cwd=None: (0, edited.isoformat()))
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": "# a grader's comment\n" + _SHEET},
        distributed=record.replace(old[1], "2026-10-04T00:00:00+00:00"),
    )
    assert again["gradebooks"] == [] and again["outbox"] == []
    ((_cfg, cfg_files, _d),) = again["config"]
    digest, at, _issue = _book_row(cfg_files[gradebook.DISTRIBUTED_PATH], "ada-l")
    assert digest == old[0] and datetime.fromisoformat(at) >= edited.replace(
        microsecond=0
    )
    assert status_json.marks_returned(
        grading_sheet.parse_sheet(_SHEET),
        grading_sheet.SheetSpec(slug="assignment-1", title="", is_group=False),
        {"ada-l": datetime.fromisoformat(at)},
        edited.replace(microsecond=0),
    )


def test_a_held_mark_is_never_re_stamped_as_returned(tmp_path, monkeypatch):
    # Ben's assignment-1 book is unchanged, but his assignment-2 mark is held: re-stamping
    # him would make the console read assignment-2 as returned.
    first = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TWO_SHEET},
        roster_rows=_TWO_ROSTER,
    )
    ((_cfg, cfg_files, _d),) = first["config"]
    stale = "2026-10-04T00:00:00+00:00"
    rows = gradebook.parse_distributed(cfg_files[gradebook.DISTRIBUTED_PATH])
    rows = {k: (v[0], stale, v[2]) for k, v in rows.items()}
    held = _TWO_SHEET.replace(
        "score_individual: 40\n    adjustment_individual:\n",
        "score_individual: 40\n    adjustment_individual: \u22123\n",
    )
    monkeypatch.setattr(
        distribute, "git", lambda *a, cwd=None: (0, "2026-10-05T00:00:00+00:00")
    )
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": _TWO_SHEET, "assignment-2": held},
        roster_rows=_TWO_ROSTER,
        distributed=gradebook.dump_distributed(rows),
        exported=cfg_files[gradebook.SEMESTER_CSV_NAME],
    )
    assert [repo for repo, _f, _d in again["gradebooks"]] == ["grades-ada-l"]
    ((_cfg, cfg_files, _d),) = again["config"]
    assert _book_row(cfg_files[gradebook.DISTRIBUTED_PATH], "ben-k")[1] == stale
