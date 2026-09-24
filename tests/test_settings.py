"""The run-settings cascade: institution -> course -> semester -> assignment, nearest wins,
the late pair paired at whichever layer states it, and every source named."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dsl_course import grades, policy, settings

PACKAGE = Path(settings.__file__).parent

ASSIGNMENTS_YML = """\
defaults:
  max_team_size: 4
  late_window_days: 5
assignments:
  a2:
    max_team_size: 2
    late_penalty_per_day: 5%
"""


@pytest.fixture
def course(monkeypatch):
    """The course declares a window/penalty pair and a visibility."""
    meta = {
        "assignment_defaults": {
            "late_window_days": 7,
            "late_penalty_per_day": "20%",
            "visibility": "public",
        }
    }
    monkeypatch.setattr(settings, "org_meta", lambda org: meta)


@pytest.fixture
def semester(monkeypatch):
    monkeypatch.setattr(settings, "_assignments_text", lambda org: ASSIGNMENTS_YML)


def test_with_nothing_declared_every_value_is_the_institution_s():
    got = settings.effective_all("Sem", "a1", course_org="Course")
    ours = policy.defaults()
    assert got["max_team_size"] == (ours["max_team_size"], "institution")
    assert got["late_window_days"] == (ours["late_window_days"], "institution")
    assert got["late_penalty_per_day"] == (ours["late_penalty_per_day"], "institution")
    assert got["team_formation"] == (ours["team_formation"], "institution")
    assert got["visibility"] == (ours["visibility"], "institution")


def test_the_course_layer_answers_before_the_institution(course):
    assert settings.effective("Sem", "a1", "late_window_days", course_org="C") == (
        7,
        "course",
    )
    assert settings.effective("Sem", "a1", "visibility", course_org="C") == (
        "public",
        "course",
    )
    assert settings.effective("Sem", "a1", "max_team_size", course_org="C")[1] == (
        "institution"
    )


def test_assignments_yml_is_the_semester_and_assignment_layers(course, semester):
    a1 = settings.effective_all("Sem", "a1", course_org="C")
    assert a1["max_team_size"] == (4, "semester")
    a2 = settings.effective_all("Sem", "a2", course_org="C")
    assert a2["max_team_size"] == (2, "assignment")
    assert a2["visibility"] == ("public", "course")


def test_the_late_pair_stays_paired_at_the_layer_that_states_it(course, semester):
    # The semester defaults state the window alone: that IS its rule, penalty none - the
    # course's 20% is not borrowed for the missing half.
    a1 = settings.effective_all("Sem", "a1", course_org="C")
    assert a1["late_window_days"] == (5, "semester")
    assert a1["late_penalty_per_day"] == (None, "semester")
    # a2's own block states the penalty alone.
    a2 = settings.effective_all("Sem", "a2", course_org="C")
    assert a2["late_penalty_per_day"] == ("5%", "assignment")
    assert a2["late_window_days"] == (None, "assignment")


def test_no_assignments_yml_means_both_of_its_layers_are_empty(course):
    assert settings.semester_blocks("Sem") == ({}, {})
    assert settings.effective("Sem", "a2", "max_team_size", course_org="C")[1] == (
        "institution"
    )


def test_a_value_the_reader_refuses_leaves_the_next_layer_to_answer(monkeypatch):
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: "defaults:\n  max_team_size: lots\n",
    )
    assert settings.effective("Sem", "a1", "max_team_size", course_org="C") == (
        policy.defaults()["max_team_size"],
        "institution",
    )


def test_the_template_s_own_run_keys_sit_below_the_assignments_yml_block(semester):
    template = {"max_team_size": 3, "team_formation": "assigned"}
    got = settings.effective_all("Sem", "a2", course_org="C", template=template)
    assert got["max_team_size"] == (2, "assignment")
    assert got["team_formation"] == ("assigned", "assignment")


def test_load_grading_spec_carries_effective_values_and_their_sources(
    monkeypatch, course, semester
):
    monkeypatch.setattr(grades, "_grading_text", lambda org, template: "type: group\n")
    spec = grades.load_grading_spec(
        "C", "assignment-2-f2026", semester_org="Sem", slug="a2"
    )
    sources = dict(spec.sources)
    assert spec.max_team_size == 2 and sources["max_team_size"] == "assignment"
    assert spec.visibility == "public" and sources["visibility"] == "course"
    assert (spec.late_window_days, spec.late_penalty_per_day) == (None, "5%")


def test_a_course_visibility_says_nothing_about_an_assignment_with_no_repo(
    monkeypatch, course
):
    monkeypatch.setattr(
        grades, "_grading_text", lambda org, template: "submit_via: external\n"
    )
    assert grades.load_grading_spec("C", "assignment-1-f2026").visibility == "private"


def test_no_module_but_settings_reads_assignment_defaults():
    """The course layer has one reader. The literal key appears in code nowhere else."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "settings.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and node.value == "assignment_defaults":
                found.append(f"{path.name}:{node.lineno}")
    assert found == []
