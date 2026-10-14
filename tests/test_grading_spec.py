"""An assignment's definition (`grading_config.yml`) and the run settings it resolves to:
the late rule a silent file is graded by, the refusals, the per-question files, the title.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from dsl_course import (
    course,
    gradebook,
    grading_sheet,
    grading_spec,
    policy,
)
from tests.conftest import run_settings

# ------------------------------------------- the late-work rule a silent file is graded by


def test_a_file_that_says_nothing_about_late_work_gets_the_hertie_rule():
    # The default an assignment is graded by when neither it nor its course states one.
    # It was "nothing after the deadline", which no syllabus says: the school's own
    # sentence is 10% a day, and ten days is where that has taken the whole grade.
    spec = grading_spec.parse_grading_spec("title: Neural networks\n")
    assert (spec.late_window_days, spec.late_penalty_per_day) == (
        policy.defaults()["late_window_days"],
        policy.defaults()["late_penalty_per_day"],
    )
    assert (
        course.late_rule(spec.late_window_days, spec.late_penalty_per_day)
        == "10% per day, up to 10 days"
    )


def _semester_spec(monkeypatch, block: str) -> grading_spec.GradingSpec:
    """An assignment whose semester's assignments.yml gives it `block` (YAML lines)."""
    text = "assignments:\n  a1:\n" + "".join(f"    {ln}\n" for ln in block.splitlines())
    run_settings(monkeypatch, lambda org: text)
    return grading_spec.with_run_settings(
        grading_spec.parse_grading_spec(""), "C", "SEM", "a1"
    )


def test_an_explicit_zero_window_still_means_nothing_after_the_deadline(monkeypatch):
    # The one way to say it, and it must survive a default that now fills the silence.
    spec = _semester_spec(monkeypatch, "late_window_days: 0\nlate_penalty_per_day: 10%")
    assert spec.late_window_days == 0
    assert (
        course.late_rule(spec.late_window_days, spec.late_penalty_per_day)
        == "not accepted after the deadline"
    )


def test_an_explicit_late_rule_wins(monkeypatch):
    spec = _semester_spec(monkeypatch, "late_window_days: 3\nlate_penalty_per_day: 25%")
    assert (spec.late_window_days, spec.late_penalty_per_day) == (3, "25%")


@pytest.mark.parametrize(
    ("config", "window", "penalty"),
    [
        ("late_window_days: 3", 3, None),
        ("late_penalty_per_day: 25%", None, "25%"),
        ("late_window_days: 0", 0, None),
    ],
    ids=["window-alone", "penalty-alone", "zero-alone"],
)
def test_half_a_late_rule_is_never_completed_from_the_default(
    monkeypatch, config, window, penalty
):
    # A semester that names one of the two has stated its own rule - three days late
    # accepted free, or a rate with nothing collected to spend it on - and finishing the
    # sentence out of the syllabus would grade a semester by words nobody wrote.
    spec = _semester_spec(monkeypatch, config)
    assert (spec.late_window_days, spec.late_penalty_per_day) == (window, penalty)


@pytest.mark.parametrize("key", ["late_window_days", "visibility", "max_team_size"])
def test_a_run_setting_in_the_template_is_refused(key):
    # Refused WHOLE: read without it, the assignment would run on the semester's defaults
    # instead of the rule the file wrote, and say nothing (decision 0009).
    with pytest.raises(grading_spec.RunSettingInTemplate) as raised:
        grading_spec.parse_grading_spec(f"title: A1\n{key}: 3\n")
    assert raised.value.old == key and "schedule.yml" in str(raised.value)


def test_a_run_setting_in_the_template_files_its_own_fault_code():
    faults, spec = grading_spec.grading_spec_faults(
        "a1", "a1", "C", "late_window_days: 3\n", None
    )
    assert spec is None
    assert [f.code for f in faults] == ["RUN_SETTING_IN_TEMPLATE"]
    assert "move `late_window_days:` to the assignment's entry in schedule.yml" in (
        faults[0].fix()
    )


def test_a_question_may_name_the_file_it_is_marked_from():
    spec = grading_spec.parse_grading_spec(
        "formats: [ipynb, latex]\n"
        "questions:\n"
        "  Q1: 10\n"
        "  Q2: {points: 5, file: starter.tex}\n"
    )
    assert spec.format == "ipynb"  # the runnable one
    assert spec.questions == {"Q1": "10", "Q2": "5"}
    assert spec.question_files == {"Q2": "starter.tex"}
    assert spec.dropped == ()


@pytest.mark.parametrize(
    "entry", ["{points: 5, file: ../secret.tex}", "{points: 5, file: /etc/x}"]
)
def test_a_question_file_outside_the_submission_is_refused(entry):
    spec = grading_spec.parse_grading_spec(f"questions:\n  Q1: 10\n  Q2: {entry}\n")
    assert spec.question_files is None
    assert spec.questions == {"Q1": "10", "Q2": "5"}  # still marked, from the runnable
    assert any("not a file inside the submission" in line for line in spec.dropped)


def test_an_unknown_key_on_a_question_is_named_and_ignored():
    spec = grading_spec.parse_grading_spec("questions:\n  Q1: {points: 5, weight: 2}\n")
    assert spec.questions == {"Q1": "5"}
    assert any("`weight:`" in line for line in spec.dropped)


@pytest.mark.parametrize(
    ("config", "window", "penalty"),
    [
        ("late_penalty_per_day: 0", None, "0"),
        ("late_window_days: 3\nlate_penalty_per_day: 0", 3, "0"),
        ("late_window_days: 0\nlate_penalty_per_day: 0", 0, "0"),
    ],
    ids=["zero-penalty-alone", "zero-penalty-with-window", "both-zero"],
)
def test_an_explicit_zero_penalty_is_never_the_institution_default(
    monkeypatch, config, window, penalty
):
    # A bare `0` was read as blank, so the layer dropped out and the institution's
    # 10% a day for 10 days was deducted from every late mark and emailed.
    spec = _semester_spec(monkeypatch, config)
    assert (spec.late_window_days, spec.late_penalty_per_day) == (window, penalty)
    assert dict(spec.sources)["late_penalty_per_day"] == "assignment"
    assert grading_sheet.penalty_rate(spec.late_penalty_per_day) == 0


def test_one_penalty_reader_gives_the_rate_and_the_fault_together():
    # The setting's parse, the grade arithmetic and the fault text read the key through
    # one function, so `0` and `10%` mean the same thing to all three.

    from dsl_course.setting_readers import read_penalty

    assert read_penalty("10%") == (Decimal("0.1"), "")
    assert read_penalty("0.1") == (Decimal("0.1"), "")
    assert read_penalty(0) == (Decimal(0), "")
    assert read_penalty(None) == read_penalty("  ") == (None, "")
    for raw, fault in (("10", "bare"), ("-5%", "negative"), ("x", "unwritten")):
        assert read_penalty(raw) == (None, fault)
    assert read_penalty("150%") == (None, "over")


def test_a_whole_number_of_days_is_read_by_one_rule():
    # `late_window_days` and the schedule's day counts share the parse: a negative or a
    # YAML `true` is not a number of days, and the late window warns rather than
    # silently reading it as 0.
    from dsl_course.setting_readers import _whole_days, whole_days

    assert [whole_days(v) for v in (3, "7", 0)] == [3, 7, 0]
    assert [whole_days(v) for v in (-1, True, "x", None)] == [None] * 4
    dropped: list[str] = []
    assert _whole_days(-1, "where", dropped) is None and len(dropped) == 1


@pytest.mark.parametrize(
    ("declared", "readme", "want", "label"),
    [
        # The seeded placeholder names nothing: the brief's heading does.
        (
            "Assignment 1",
            "# Assignment 1 - Fraud\nBody",
            "Assignment 1 - Fraud",
            "Assignment 1 · Fraud",
        ),
        # A real title beats the heading.
        ("Churn model", "# Fraud\nBody", "Churn model", "Assignment 1 · Churn model"),
        # No heading: the placeholder stands, and the label is the identifier alone.
        ("Assignment 1", "Body only", "Assignment 1", "Assignment 1"),
    ],
)
def test_a_placeholder_title_gives_way_to_the_readme_heading(
    monkeypatch, declared, readme, want, label
):
    monkeypatch.setattr(grading_spec, "get_file_content", lambda *a, **k: readme)
    spec = grading_spec.parse_grading_spec(f"title: {declared}\n")
    got = grading_spec.assignment_title(
        "COURSE", "assignment-1-f2026", spec, "assignment-1"
    )
    assert got == want
    assert gradebook._readme_label("assignment-1", got) == label
