"""The Feedback issue and its submission receipts.

One issue per submission repo, opened at handout, never a second one; additive receipt
comments that a re-run cannot duplicate. Both halves are promises made to a student who
reads that thread rather than the docs, so the bodies are pinned to the wording the
mock-up specifies and the lookup is pinned against every way it could open a duplicate.
"""

from __future__ import annotations

import pytest

from dsl_course import course, ghcli, grades, repos
from dsl_course.grades import SheetSpec

DUE_LONG = "Sunday 4 October 2026, 23:59 (Europe/Berlin)"
CUTOFF_LONG = "Sunday 11 October 2026, 23:59 (Europe/Berlin)"
MARK = course.FEEDBACK_ISSUE_MARKS[0]
SHA = "a1b2c3d4e5f6" + "0" * 28

SUBMIT_PARAGRAPH = (
    "Push your work to this repository as normal; the last commit to `main` before the "
    "deadline is what we grade. A submission receipt is posted here at the deadline and "
    "after any late push, and your feedback and grade follow as a comment once marking "
    "is complete."
)


def spec(**kw) -> SheetSpec:
    return SheetSpec(
        slug="assignment-1",
        title="Neural networks from scratch",
        is_group=kw.pop("is_group", False),
        late_window_days=kw.pop("late_window_days", 7),
        late_penalty_per_day=kw.pop("late_penalty_per_day", "10%"),
        due_long=DUE_LONG,
        cutoff_long=CUTOFF_LONG,
        **kw,
    )


# ------------------------------------------------------------------------- the bodies


def test_the_issue_body_for_an_individual_repo():
    body = grades.feedback_body(spec())
    assert body.splitlines() == [
        MARK,
        "**Due:** Sunday 4 October 2026, 23:59 (Europe/Berlin)",
        (
            "**Late work:** accepted until Sunday 11 October 2026, 23:59 "
            "(Europe/Berlin), at 10% of your grade per day started."
        ),
        "",
        SUBMIT_PARAGRAPH,
    ]


def test_the_issue_body_for_a_team_repo_names_the_team_and_asks_for_contributions():
    body = grades.feedback_body(
        spec(is_group=True), "team-alpha", ["ada-l", "ben-k", "chen-w"]
    )
    assert (
        "**Team:** team-alpha (@ada-l, @ben-k, @chen-w) - fill in CONTRIBUTIONS.md "
        "before the deadline." in body.splitlines()
    )


def test_the_issue_body_for_work_handed_in_off_github_promises_no_receipts():
    # There is no push to acknowledge, so the body must not tell a student to expect one.
    body = grades.feedback_body(spec(submit_external=True))
    assert body.splitlines() == [
        MARK,
        "**Due:** Sunday 4 October 2026, 23:59 (Europe/Berlin)",
        "",
        (
            "This assignment is submitted outside GitHub (see the brief). This "
            "repository holds the brief and your feedback."
        ),
    ]
    assert "receipt" not in body


def test_the_body_always_opens_with_a_mark_the_lookup_can_find():
    # The mark is how a second Feedback issue is prevented in a repo whose label was
    # removed by hand. It is a chain, never edited - so the FIRST is what we write.
    for body in (
        grades.feedback_body(spec()),
        grades.feedback_body(spec(submit_external=True)),
    ):
        assert body.startswith(f"{MARK}\n")


# ------------------------------------------------------------------------ the receipts


def test_the_three_receipt_bodies_read_as_the_mock_up_specifies():
    late = "Late work is accepted until 11 October 2026, 23:59 (10% per day)."
    assert course.receipt_body(
        course.RECEIPT_DUE,
        sha=SHA,
        pushed_display="Saturday 3 October 2026, 22:14",
        days_late=0,
        late_line=late,
    ) == (
        "**Submission recorded** · `a1b2c3d` · pushed Saturday 3 October 2026, 22:14 · "
        "on time\n"
        "Late work is accepted until 11 October 2026, 23:59 (10% per day). A further "
        "push replaces this.\n"
    )
    assert course.receipt_body(
        course.RECEIPT_UPDATED,
        sha=SHA,
        pushed_display="Tuesday 6 October 2026, 09:30",
        days_late=2,
        penalty_display="-20%",
    ) == (
        "**Submission updated** · `a1b2c3d` · pushed Tuesday 6 October 2026, 09:30 · "
        "2 days late (-20%)\n"
    )
    assert course.receipt_body(course.RECEIPT_FROZEN, sha=SHA, days_late=2) == (
        "**Frozen for grading** · `a1b2c3d` · 2 days late. No further pushes count.\n"
    )


def test_a_missing_submission_still_gets_a_receipt():
    # Silence at the deadline is indistinguishable from a toolkit that broke. The student
    # is told what was recorded and how long they still have.
    late = "Late work is accepted until 11 October 2026, 23:59 (10% per day)."
    assert course.receipt_body(course.RECEIPT_DUE, late_line=late) == (
        "**No submission recorded** at the deadline. Late work is accepted until "
        "11 October 2026, 23:59 (10% per day).\n"
    )


def test_one_day_late_is_singular():
    assert "1 day late" in course.receipt_body(
        course.RECEIPT_FROZEN, sha=SHA, days_late=1
    )


def test_the_penalty_shown_is_the_days_times_the_rate():
    assert grades.penalty_display(spec(), 2) == "-20%"
    assert grades.penalty_display(spec(), 0) == ""
    assert grades.penalty_display(spec(late_penalty_per_day=None), 2) == ""


def test_the_marker_keys_on_the_commit_as_well_as_the_event():
    # Per COMMIT, so a re-run over the same pin says nothing while a genuinely new push
    # still earns its own receipt.
    assert course.receipt_marker(SHA, "due") == f"<!-- dsl-receipt:{SHA}:due -->"
    assert course.receipt_marker("", "due") == "<!-- dsl-receipt:none:due -->"


# ------------------------------------------------------------------------- the lookup


def _gh(monkeypatch, answers):
    """Answer `gh` from `answers` (matched on a substring of the joined argv), recording
    every call. An unmatched call is a test bug, not a blank answer."""
    calls: list[tuple[str, ...]] = []

    def fake(*args, **kwargs):
        calls.append(args)
        argv = " ".join(args)
        for needle, reply in answers.items():
            if needle in argv:
                return reply
        raise AssertionError(f"unstubbed gh call: {argv}")

    monkeypatch.setattr(grades, "gh", fake)
    return calls


def test_the_issue_is_found_by_its_label_in_one_call(monkeypatch):
    calls = _gh(monkeypatch, {"labels=dsl-feedback": (0, "7\topen")})
    assert grades.find_feedback_issue("Cohort", "assignment-1-ada-l") == (7, "open")
    assert len(calls) == 1  # the cheapest rung answered; no listing, no search


def test_an_unlabelled_issue_is_still_found_by_its_body_mark(monkeypatch):
    # A label a faculty member removed by hand must not make the toolkit open a second
    # issue on top of the thread the student has been reading.
    _gh(
        monkeypatch,
        {
            "labels=dsl-feedback": (0, ""),
            "state=all&per_page=50": (
                0,
                "3\tclosed\t\tSomething else\n9\topen\tmark\tNotes",
            ),
        },
    )
    assert grades.find_feedback_issue("Cohort", "assignment-1-ada-l") == (9, "open")


def test_an_issue_with_neither_label_nor_mark_is_found_by_its_exact_title(monkeypatch):
    _gh(
        monkeypatch,
        {
            "labels=dsl-feedback": (0, ""),
            "state=all&per_page=50": (
                0,
                "4\topen\t\tFeedback please\n5\topen\t\tFeedback",
            ),
        },
    )
    assert grades.find_feedback_issue("Cohort", "assignment-1-ada-l") == (5, "open")


def test_a_pull_request_is_never_mistaken_for_the_feedback_issue(monkeypatch):
    # This endpoint returns PRs as issues. Commenting a grade onto a student's pull
    # request would put it somewhere the toolkit never looks again.
    calls = _gh(monkeypatch, {"issues?": (0, "")})
    grades.find_feedback_issue("Cohort", "assignment-1-ada-l")
    assert all("select(.pull_request == null)" in " ".join(c) for c in calls)


def test_the_lookup_uses_the_list_endpoint_never_the_search_index(monkeypatch):
    # `gh issue list --search` lags by minutes, so a lookup that came back empty meant
    # "opened a second one" rather than "not there".
    calls = _gh(monkeypatch, {"issues?": (0, "")})
    grades.find_feedback_issue("Cohort", "assignment-1-ada-l")
    assert not any("--search" in " ".join(c) or "issue" == c[0] for c in calls)


def test_the_issue_is_opened_once_with_its_label(monkeypatch):
    calls = _gh(monkeypatch, {"issues?": (0, ""), "--method POST": (0, "12\n")})
    labelled: list[str] = []
    monkeypatch.setattr(
        grades,
        "ensure_label",
        lambda org, repo, name, *, color, description: labelled.append(name) or True,
    )
    assert grades.ensure_feedback_issue("Cohort", "assignment-1-ada-l", "body") == 12
    assert labelled == [
        course.FEEDBACK_ISSUE_LABEL
    ]  # before the create, or GitHub drops it
    create = [c for c in calls if "POST" in c]
    assert len(create) == 1
    assert f"labels[]={course.FEEDBACK_ISSUE_LABEL}" in create[0]


def test_an_existing_issue_is_never_opened_again(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a repo must never get a second Feedback issue")

    _gh(monkeypatch, {"labels=dsl-feedback": (0, "7\topen")})
    monkeypatch.setattr(grades, "ensure_label", boom)
    assert grades.ensure_feedback_issue("Cohort", "assignment-1-ada-l", "body") == 7


def test_a_closed_issue_is_reopened_rather_than_replaced(monkeypatch):
    # A student who closes theirs must still get their receipts and their grade, in the
    # thread they were pointed at.
    calls = _gh(
        monkeypatch,
        {"labels=dsl-feedback": (0, "7\tclosed"), "--method PATCH": (0, "")},
    )
    assert grades.ensure_feedback_issue("Cohort", "assignment-1-ada-l", "body") == 7
    ((patch,),) = ([c for c in calls if "PATCH" in c],)
    assert "state=open" in patch and "issues/7" in " ".join(patch)


def test_a_dry_run_opens_nothing(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a dry run must not write")

    _gh(monkeypatch, {"issues?": (0, "")})
    monkeypatch.setattr(grades, "ensure_label", boom)
    assert (
        grades.ensure_feedback_issue("Cohort", "assignment-1-ada-l", "body", True)
        is None
    )


# ------------------------------------------------------------------- posting a receipt


def test_a_receipt_is_posted_once_and_carries_its_marker(monkeypatch):
    marker = course.receipt_marker(SHA, "due")
    calls = _gh(monkeypatch, {"comments?": (0, ""), "--method POST": (0, "")})
    assert grades.post_receipt("Cohort", "assignment-1-ada-l", 7, "hello", marker)
    ((post,),) = ([c for c in calls if "POST" in c],)
    assert f"body=hello\n{marker}\n" in post


def test_a_re_run_over_the_same_commit_posts_nothing(monkeypatch):
    # The refresh runs four times an hour for the length of the late window; a student
    # must not collect four identical receipts for one push.
    marker = course.receipt_marker(SHA, "due")

    def boom(*a, **k):
        raise AssertionError("a receipt already on the issue must not be re-posted")

    calls = _gh(monkeypatch, {"comments?": (0, f"an earlier comment\n{marker}\n")})
    assert grades.post_receipt("Cohort", "assignment-1-ada-l", 7, "hello", marker)
    assert not [c for c in calls if "POST" in c]


def test_a_comment_read_that_failed_posts_nothing(monkeypatch):
    # Unable to tell whether the receipt is already there, the safe answer is silence: a
    # duplicate receipt confuses a student, a missed one is repaired by the next tick.
    calls = _gh(monkeypatch, {"comments?": (1, "HTTP 500")})
    assert not grades.post_receipt("Cohort", "assignment-1-ada-l", 7, "hello", "m")
    assert not [c for c in calls if "POST" in c]


@pytest.mark.parametrize(
    "call",
    [
        lambda: grades.ensure_feedback_issue("C", "r", "body"),
        lambda: grades.post_receipt("C", "r", 7, "b", "m"),
        lambda: repos.ensure_label("C", "r", "l", color="fff", description="d"),
    ],
    ids=["create-issue", "post-receipt", "ensure-label"],
)
def test_every_write_is_spelt_so_the_pacer_can_see_it(monkeypatch, call):
    # `ghcli._is_mutating` reads the argv for `--method`/`-X`. A POST inferred by gh from
    # the presence of `--field` is a POST the write governor never counts.
    calls: list[tuple[str, ...]] = []

    def fake(*args, **kwargs):
        calls.append(args)
        return (0, "12") if "--method" in args else (0, "")

    monkeypatch.setattr(grades, "gh", fake)
    monkeypatch.setattr(repos, "gh", fake)
    monkeypatch.setattr(grades, "ensure_label", lambda *a, **k: True)
    call()
    writes = [c for c in calls if any(f in c for f in ("--field", "-f"))]
    assert writes, "the call under test made no write at all"
    assert all(ghcli._is_mutating(c) for c in writes)
