"""What a student is shown: the allowlisted view, the private gradebook, the feedback
comments and the registrar's export.

The promise these tests hold to is a privacy one, and it has three parts: a student sees
their own final grade and their own feedback; they never see the grader's notes or any
other `info:` fact; and no member of a team ever sees another member's marks, feedback or
adjustment. Most of what follows is written as sentinels - a unique string in every field
of every person - because that is the only way to assert the absence of a leak rather
than the presence of the fields we happened to think of.
"""

from __future__ import annotations

import pytest

from dsl_course import roster
from dsl_course.grades import (
    NOTES_KEY,
    STUDENT_VIEW_KEYS,
    GradeRow,
    SheetSpec,
    TeamResult,
    _cell,
    build_gradebooks,
    individual_issue_body,
    load_sheets,
    needs_hand_decision,
    render_readme,
    render_registrar_csv,
    student_view,
    team_issue_body,
    team_result,
)

TITLE = "Neural networks from scratch"
QUESTIONS = {"Q1": "15", "Q2": "15", "Q3": "10", "Q4": "10"}
TITLES = {"assignment-1": TITLE}
# The sentinel cast below is keyed by a slug with no digits in it, so that a bare "-1"
# in an assertion is an adjustment and never the tail of "assignment-1".
SENTINEL_SLUG = "neural-nets"
SENTINEL_TITLES = {SENTINEL_SLUG: TITLE}


def group_spec(**kw) -> SheetSpec:
    return SheetSpec(
        slug="assignment-1",
        title=TITLE,
        is_group=True,
        questions=QUESTIONS,
        late_window_days=7,
        late_penalty_per_day="10%",
        due_display="Sun 4 Oct 2026 23:59",
        cutoff_display="Sun 11 Oct 2026 23:59",
        **kw,
    )


def individual_spec(**kw) -> SheetSpec:
    return SheetSpec(
        slug="assignment-1",
        title="Introduce Yourself",
        is_group=False,
        due_display="Sun 20 Sep 2026 23:59",
        **kw,
    )


# --------------------------------------------------------------- the leak test's cast
#
# One team, three members, and a unique string in every field anybody owns. The scores
# are chosen so that no member's final grade is the team's total or another member's
# grade, and so that no question's mark reads as a substring of anything legitimately on
# the page: 43 - 1 = 42, 43 - 3 = 40, 43 + 2 = 45.

MEMBERS = ("ada-l", "ben-k", "chen-w")
ADJUSTMENTS = {"ada-l": -1, "ben-k": -3, "chen-w": +2}
FINALS = {"ada-l": "42", "ben-k": "40", "chen-w": "45"}
TEAM_TOTAL = "43"
QUESTION_MARKS = ("14", "13", "10", "6")


def sentinel_sheet() -> dict:
    return {
        "teams": {
            "team-alpha": {
                "info": {
                    "submitted": "2026-10-03T09:05+02:00",
                    "days_late": 0,
                    "contributions": "CONTRIBUTIONS-SENTINEL",
                    "autograde": "AUTOGRADE-SENTINEL",
                },
                "score_group": {"Q1": 14, "Q2": 13, "Q3": 10, "Q4": 6},
                "feedback_group": "TEAM-FEEDBACK-SENTINEL",
                "members": {
                    handle: {
                        "adjustment_individual": ADJUSTMENTS[handle],
                        "feedback_individual": f"FEEDBACK-{handle}-SENTINEL",
                        NOTES_KEY: f"NOTES-{handle}-SENTINEL",
                    }
                    for handle in MEMBERS
                },
            }
        }
    }


def sentinel_books() -> dict:
    return build_gradebooks({SENTINEL_SLUG: (group_spec(), sentinel_sheet())})


def sentinel_students() -> list[roster.Student]:
    return [
        roster.Student(
            hertie_email=f"{handle}@students.hertie-school.org",
            name=handle.upper(),
            github_handle=handle,
            github_id="",
        )
        for handle in MEMBERS
    ]


def everything_a_student_reads() -> dict[str, str]:
    """Every artefact this module hands to a student or a registrar, as text, keyed by
    what it is - so a failure names the file the sentinel escaped into."""
    books = sentinel_books()
    sheet = sentinel_sheet()
    pages = {
        f"{handle} gradebook README": render_readme(
            handle, books[handle], SENTINEL_TITLES
        )
        for handle in MEMBERS
    }
    pages |= {f"{handle} grades.yml view": repr(books[handle]) for handle in MEMBERS}
    pages["registrar CSV"] = render_registrar_csv(sentinel_students(), books)
    pages["team comment"] = team_issue_body(
        TITLE, team_result(group_spec(), "team-alpha", sheet["teams"]["team-alpha"])
    )
    return pages


# ------------------------------------------------------------------------- the leaks


@pytest.mark.parametrize("owner", MEMBERS)
def test_no_member_of_a_team_is_shown_another_members_work(owner):
    # The whole reason a group result is split into a shared team score and a private
    # final grade: a team repo is read by the whole team, a gradebook by one person.
    books = sentinel_books()
    for other in MEMBERS:
        if other == owner:
            continue
        page = render_readme(other, books[other], SENTINEL_TITLES)
        page += repr(books[other])
        assert f"FEEDBACK-{owner}-SENTINEL" not in page
        assert f"NOTES-{owner}-SENTINEL" not in page


def test_the_graders_own_notes_never_reach_anything_a_student_reads():
    # `notes_not_shared_with_students` says what it is for in its own name; `autograde`
    # and `contributions` are toolkit facts kept for the grader, not marks.
    for name, page in everything_a_student_reads().items():
        for sentinel in ("NOTES-", "AUTOGRADE-SENTINEL", "CONTRIBUTIONS-SENTINEL"):
            assert sentinel not in page, f"{sentinel} reached the {name}"


def test_the_team_comment_carries_no_member_field_at_all():
    # A team repo grants the whole team `maintain`, so this comment is read by everyone.
    # TeamResult is what makes the leak impossible rather than merely absent.
    sheet = sentinel_sheet()
    body = team_issue_body(
        TITLE, team_result(group_spec(), "team-alpha", sheet["teams"]["team-alpha"])
    )
    for handle in MEMBERS:
        assert f"FEEDBACK-{handle}-SENTINEL" not in body
        assert FINALS[handle] not in body
    assert not [f for f in TeamResult.__dataclass_fields__ if "member" in f]
    # ... and each member's own feedback still reaches their own gradebook.
    books = sentinel_books()
    for handle in MEMBERS:
        assert f"FEEDBACK-{handle}-SENTINEL" in render_readme(
            handle, books[handle], SENTINEL_TITLES
        )
    assert "TEAM-FEEDBACK-SENTINEL" in body


def test_a_gradebook_shows_the_final_grade_and_never_the_parts_it_came_from():
    # The maintainer's call: the team's score and the member's own adjustment are the
    # grader's working, not the student's result. Reading your own deduction beside the
    # mark your team-mates share is the conversation this workflow exists to avoid.
    for name, page in everything_a_student_reads().items():
        if name == "team comment":
            continue  # the team's score IS the team's to see, in the team's own repo
        assert TEAM_TOTAL not in page, f"the team score reached the {name}"
        for mark in QUESTION_MARKS:
            assert mark not in page, f"a question's mark reached the {name}"
        for adjustment in ("-1", "-3", "+2"):
            assert adjustment not in page, f"an adjustment reached the {name}"


def test_a_view_holds_nothing_the_allowlist_does_not_name():
    # The allowlist has to hold for keys nobody has thought of yet: a fact the toolkit
    # starts recording, a column a grader invents for their own use.
    sheet = sentinel_sheet()
    block = sheet["teams"]["team-alpha"]
    block["info"]["completion"] = "COMPLETION-SENTINEL"
    block["moderation_round_2"] = "INVENTED-SENTINEL"
    block["members"]["ada-l"]["second_marker"] = "SECOND-MARKER-SENTINEL"
    view = student_view(group_spec(), "team-alpha", block, "ada-l")
    assert set(view) <= set(STUDENT_VIEW_KEYS)
    for sentinel in ("COMPLETION-", "INVENTED-", "SECOND-MARKER-"):
        assert sentinel not in repr(view)


# -------------------------------------------------------------------- the derivation


def test_a_group_members_grade_is_the_team_total_plus_their_own_adjustment():
    sheet = sentinel_sheet()
    view = student_view(
        group_spec(), "team-alpha", sheet["teams"]["team-alpha"], "ben-k"
    )
    assert view["final_grade"] == "40"  # 43 - 3
    assert view["max_points"] == "50"
    assert view["team"] == "team-alpha"
    assert "score" not in view  # a group's score is the team's, not the member's


def test_the_late_penalty_is_taken_before_the_adjustment_is_added():
    # 20 x (1 - 10% x 2) = 16, then +4 - the waived penalty - is 20. The other order
    # would penalise the waiver itself.
    block = {
        "info": {"submitted": "2026-10-06T09:30+02:00", "days_late": 2},
        "score_group": {"Q1": 12, "Q2": 8},
        "members": {"eli-r": {"adjustment_individual": 4}},
    }
    view = student_view(group_spec(), "team-beta", block, "eli-r")
    assert view["final_grade"] == "20"
    assert view["penalty"] == "-20%"
    assert view["days_late"] == 2
    assert view["submitted"] == "6 Oct 09:30"


def test_a_final_grade_is_never_below_zero():
    block = {"score_individual": 5, "adjustment_individual": -20}
    view = student_view(individual_spec(), "ada-l", block, "ada-l")
    assert view["final_grade"] == "0"


def test_a_score_no_arithmetic_applies_to_is_passed_through_as_typed():
    block = {"info": {"days_late": 2}, "score_individual": "pass"}
    view = student_view(
        individual_spec(late_penalty_per_day="10%", late_window_days=7),
        "ada-l",
        block,
        "ada-l",
    )
    assert view["final_grade"] == "pass"
    assert view["penalty"] == "-20%"
    assert needs_hand_decision(view), "a penalty on `pass` must reach a person"


@pytest.mark.parametrize(
    "block",
    [
        {"info": {"days_late": 0}, "score_individual": "pass"},  # nothing to apply
        {"info": {"days_late": 2}, "score_individual": 20},  # a number: just arithmetic
    ],
)
def test_a_mark_nobody_has_to_decide_is_not_flagged(block):
    spec = individual_spec(late_penalty_per_day="10%", late_window_days=7)
    assert not needs_hand_decision(student_view(spec, "ada-l", block, "ada-l"))


def test_an_individual_keeps_their_own_per_question_breakdown():
    spec = individual_spec(questions={"Q1": "10", "Q2": "10"})
    block = {"score_individual": {"Q1": 9, "Q2": None}}
    view = student_view(spec, "ada-l", block, "ada-l")
    assert view["score"] == {"Q1": 9}  # the unmarked question is not a mark of nothing
    assert view["final_grade"] == "9"
    assert view["max_points"] == "20"


# ------------------------------------------------------------------- the gradebook


def spec_example_book() -> dict:
    """The mock-up's own example (spec §7): Ben K. in team-alpha, 43 - 3 = 40.

    The team feedback is stored with the line breaks the grader typed, and the README
    keeps them - the mock-up wraps that same paragraph at one width in the sheet and
    another in the README, and only the grader's own wrapping is a fact."""
    sheet = {
        "teams": {
            "team-alpha": {
                "info": {"submitted": "2026-10-03T22:14+02:00", "days_late": 0},
                "score_group": {"Q1": 14, "Q2": 13, "Q3": 10, "Q4": 6},
                "feedback_group": (
                    "Clean derivation in Q1-Q3. Q4 confuses the\n"
                    "marginal with the conditional. Plots are excellent.\n"
                ),
                "members": {
                    "ada-l": {},
                    "ben-k": {
                        "adjustment_individual": -3,
                        "feedback_individual": (
                            "Your section of the write-up repeats the Q4 error; see the "
                            "team feedback.\n"
                        ),
                        NOTES_KEY: "contributions list says Ben did the write-up",
                    },
                },
            }
        }
    }
    return build_gradebooks({"assignment-1": (group_spec(), sheet)})


def test_the_gradebook_readme_is_exactly_the_page_the_spec_shows():
    assert render_readme("ben-k", spec_example_book()["ben-k"], TITLES) == (
        "This gradebook is private to you. It is regenerated each time grades are "
        "distributed; do not edit it.\n"
        "\n"
        "| Assignment | Final grade | Submitted | Late | Team |\n"
        "|---|---|---|---|---|\n"
        "| Neural networks from scratch | 40 / 50 | 3 Oct 22:14 | on time | team-alpha |\n"
        "\n"
        "## Neural networks from scratch\n"
        "**Final grade:** 40 / 50\n"
        "\n"
        "Your section of the write-up repeats the Q4 error; see the team feedback.\n"
        "\n"
        "> **Team feedback (shared with team-alpha):** Clean derivation in Q1-Q3. Q4 "
        "confuses the\n"
        "> marginal with the conditional. Plots are excellent.\n"
    )


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        # Handed in off GitHub: there was never a commit to time.
        (individual_spec(submit_external=True), "external"),
        # A GitHub assignment whose repo nobody pushed to. Calling this one "external"
        # told a student who had missed the deadline that their work was handed in
        # somewhere else - and told their grader the same.
        (individual_spec(), "not submitted"),
    ],
    ids=["external", "nothing pushed"],
)
def test_the_submitted_column_tells_the_two_kinds_of_blank_apart(spec, expected):
    sheet = {"submissions": {"ada-l": {"info": {}, "score_individual": 9}}}
    books = build_gradebooks({"assignment-1": (spec, sheet)})
    row = render_readme("ada-l", books["ada-l"], {}).splitlines()[4]
    assert row == f"| assignment-1 | 9 | {expected} |  |  |"


def test_a_mark_on_a_repo_nothing_was_pushed_to_says_that_in_the_comment():
    # A 0 with no explanation is the one grade a student writes in about.
    sheet = {"submissions": {"ada-l": {"info": {}, "score_individual": 0}}}
    view = build_gradebooks({"assignment-1": (individual_spec(), sheet)})["ada-l"][
        "assignment-1"
    ]
    assert individual_issue_body("Introduce Yourself", view) == (
        "### Feedback · Introduce Yourself\n"
        "**Grade:** 0\n"
        "\n"
        "No submission was recorded.\n"
    )


def test_an_assignment_handed_in_off_github_is_never_called_unsubmitted():
    spec = individual_spec(submit_external=True)
    sheet = {"submissions": {"ada-l": {"score_individual": 9}}}
    view = build_gradebooks({"assignment-1": (spec, sheet)})["ada-l"]["assignment-1"]
    body = individual_issue_body("Introduce Yourself", view)
    assert "No submission was recorded." not in body
    assert "submitted external" not in body  # nor does it read as a timestamp


def test_one_row_per_assignment_sorted_by_slug():
    spec = individual_spec()
    books = build_gradebooks(
        {
            "assignment-2": (spec, {"submissions": {"ada-l": {"score_individual": 8}}}),
            "assignment-1": (spec, {"submissions": {"ada-l": {"score_individual": 9}}}),
        }
    )
    readme = render_readme("ada-l", books["ada-l"], {})
    assert readme.index("| assignment-1 ") < readme.index("| assignment-2 ")
    assert readme.index("## assignment-1") < readme.index("## assignment-2")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a | b", "a \\| b"),
        ("two\nlines", "two lines"),
        ("  padded  ", "padded"),
        (None, ""),
        (0, "0"),
    ],
)
def test_a_cell_can_never_break_the_table(value, expected):
    # Feedback is free text: a marking rubric with a pipe in it, or a paragraph, would
    # otherwise close the column early or end the row.
    assert _cell(value) == expected


# ---------------------------------------------------------------------- the registrar


def registrar_roster() -> list[roster.Student]:
    return [
        roster.Student("zoe@students.hertie-school.org", "Zoe Z.", "zoe-z", ""),
        roster.Student("Ada@students.hertie-school.org", "Ada L.", "ada-l", ""),
        roster.Student("ben@students.hertie-school.org", "Ben K.", "ben-k", ""),
        roster.Student(
            "aud@students.hertie-school.org",
            "Aud I.",
            "aud-i",
            "",
            role=roster.ROLE_AUDITOR,
        ),
    ]


def test_the_registrar_csv_has_a_row_for_every_enrolled_student_and_no_auditor():
    # The ungraded are rows too: a missing row reads as somebody who left the course,
    # and this is the file a mark is transcribed from.
    csv_text = render_registrar_csv(registrar_roster(), spec_example_book())
    assert csv_text == (
        "hertie_email,name,github_handle,assignment-1\r\n"
        "Ada@students.hertie-school.org,Ada L.,ada-l,43\r\n"
        "ben@students.hertie-school.org,Ben K.,ben-k,40\r\n"
        "zoe@students.hertie-school.org,Zoe Z.,zoe-z,\r\n"
    )


def test_the_registrar_csv_has_one_column_per_assignment_sorted():
    spec = individual_spec()
    books = build_gradebooks(
        {
            "assignment-2": (spec, {"submissions": {"ada-l": {"score_individual": 8}}}),
            "assignment-1": (spec, {"submissions": {"ada-l": {"score_individual": 9}}}),
        }
    )
    lines = render_registrar_csv(registrar_roster(), books).splitlines()
    assert lines[0] == "hertie_email,name,github_handle,assignment-1,assignment-2"
    assert lines[1] == "Ada@students.hertie-school.org,Ada L.,ada-l,9,8"


# ----------------------------------------------------------------- the two comments


def test_the_team_comment_is_exactly_the_text_the_spec_shows():
    sheet = {
        "teams": {
            "team-alpha": {
                "info": {"submitted": "2026-10-03T22:14+02:00", "days_late": 0},
                "score_group": {"Q1": 14, "Q2": 13, "Q3": 10, "Q4": 6},
                "feedback_group": (
                    "Clean derivation in Q1-Q3. Q4 confuses the marginal with the "
                    "conditional. Plots are excellent.\n"
                ),
                "members": {"ada-l": {}},
            }
        }
    }
    body = team_issue_body(
        TITLE, team_result(group_spec(), "team-alpha", sheet["teams"]["team-alpha"])
    )
    assert body == (
        "### Feedback · Neural networks from scratch\n"
        "**Team score:** 43 / 50 (Q1 14, Q2 13, Q3 10, Q4 6) · submitted on time\n"
        "\n"
        "Clean derivation in Q1-Q3. Q4 confuses the marginal with the conditional. "
        "Plots are excellent.\n"
        "\n"
        "Your own final grade and personal feedback are in your private gradebook: "
        "`grades-<your handle>`.\n"
    )


def test_the_individual_comment_is_exactly_the_text_the_spec_shows():
    spec = individual_spec(submit_external=True)
    block = {
        "score_individual": 9,
        "feedback_individual": (
            "Clear mapping of the screening-test example to Bayes' rule. Slightly over "
            "two minutes.\n"
        ),
        NOTES_KEY: "not for them",
    }
    view = student_view(spec, "ada-l", block, "ada-l")
    assert individual_issue_body("Introduce Yourself", view) == (
        "### Feedback · Introduce Yourself\n"
        "**Grade:** 9\n"
        "\n"
        "Clear mapping of the screening-test example to Bayes' rule. Slightly over two "
        "minutes.\n"
    )


def test_a_late_individual_comment_shows_the_arithmetic_done_to_the_work():
    spec = individual_spec(
        questions={"Q1": "50"}, late_window_days=7, late_penalty_per_day="10%"
    )
    block = {"info": {"days_late": 2}, "score_individual": 20}
    view = student_view(spec, "ada-l", block, "ada-l")
    assert individual_issue_body("Introduce Yourself", view).splitlines()[1] == (
        "**Score:** 20 / 50 · 2 days late · penalty -20% · **Final grade:** 16 / 50"
    )


# ------------------------------------------------------- the sources marks come from


def test_a_cohort_still_marking_in_the_legacy_csv_distributes_the_same_way():
    rows = [
        GradeRow(
            github_handle="ben-k",
            team="team-alpha",
            team_score="43",
            individual_adjustment="-3",
            final_grade="40",
            individual_comments="See the team feedback.",
            team_comments="Clean derivation.",
        )
    ]
    view = build_gradebooks({"assignment-1": rows})["ben-k"]["assignment-1"]
    assert view == {
        "final_grade": "40",
        "feedback": "See the team feedback.",
        "team": "team-alpha",
        "team_feedback": "Clean derivation.",
    }
    # The CSV's working columns have no student-visible home any more.
    assert "43" not in render_readme("ben-k", {"assignment-1": view}, TITLES)


def test_a_student_with_nothing_in_the_sheet_yet_has_no_gradebook_entry():
    sheet = {"submissions": {"ada-l": {"score_individual": None}, "ben-k": {}}}
    assert build_gradebooks({"assignment-1": (individual_spec(), sheet)}) == {}


def test_load_sheets_reads_every_sheet_in_a_classroom_config_checkout(tmp_path):
    folder = tmp_path / "grading_sheets"
    folder.mkdir()
    (folder / "assignment-1.yml").write_text(
        "submissions:\n  ada-l:\n    score_individual: 9\n"
    )
    (folder / "assignment-2.yml").write_text("")
    (folder / "notes.txt").write_text("not a sheet")
    sheets = load_sheets(tmp_path)
    assert sheets == {
        "assignment-1": {"submissions": {"ada-l": {"score_individual": 9}}},
        "assignment-2": {},
    }


def test_load_sheets_is_empty_where_no_assignment_has_been_handed_out(tmp_path):
    assert load_sheets(tmp_path) == {}
