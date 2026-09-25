"""The run-settings cascade: institution -> course -> semester -> assignment, nearest wins,
the late pair paired at whichever layer states it, and every source named."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dsl_course import grades, policy, settings

PACKAGE = Path(settings.__file__).parent
# The real read, before conftest's autouse fixture answers it with "no file".
_READ_ASSIGNMENTS = settings._assignments_text

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


def test_a_semester_read_that_fails_raises_and_is_not_cached(monkeypatch):
    monkeypatch.setattr(settings, "_assignments_text", _READ_ASSIGNMENTS)
    answers = [RuntimeError("could not read Sem/semester-config/assignments.yml: 403")]

    def read(org, repo, path, ref=""):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(settings, "get_file_content", read)
    with pytest.raises(RuntimeError):
        settings.semester_blocks("Sem")
    # A later read that succeeds is read, not the failure remembered as "nothing".
    answers.append("defaults:\n  max_team_size: 4\n")
    assert settings.semester_blocks("Sem") == ({"max_team_size": 4}, {})


def test_an_absent_assignments_yml_is_the_empty_layer(monkeypatch):
    monkeypatch.setattr(settings, "_assignments_text", _READ_ASSIGNMENTS)
    monkeypatch.setattr(settings, "get_file_content", lambda *a, **k: None)
    assert settings.semester_blocks("Sem") == ({}, {})


def test_a_course_read_that_fails_raises_and_is_not_cached(monkeypatch):
    answers: list = [RuntimeError("could not read C/.github/dsl-course.yml: 502")]

    def meta(org):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(settings, "org_meta", meta)
    with pytest.raises(RuntimeError):
        settings.course_defaults("C")
    answers.append({"assignment_defaults": {"max_team_size": 3}})
    assert settings.course_defaults("C") == {"max_team_size": 3}


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


def _names_the_block(node: ast.AST) -> bool:
    """`"assignment_defaults"`, or the constant that spells it, however imported."""
    if isinstance(node, ast.Constant):
        return node.value == settings.ASSIGNMENT_DEFAULTS_KEY
    if isinstance(node, ast.Name):
        return node.id == "ASSIGNMENT_DEFAULTS_KEY"
    return isinstance(node, ast.Attribute) and node.attr == "ASSIGNMENT_DEFAULTS_KEY"


def _block_reads(tree: ast.AST) -> list[int]:
    """The lines that READ the block out of a mapping: `meta[key]` or `meta.get(key)`
    (and `pop`/`setdefault`), by the literal or by the constant."""
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and _names_the_block(node.slice)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "pop", "setdefault")
                and node.args
                and _names_the_block(node.args[0])
            )
        ):
            found.append(node.lineno)
    return found


# `settings` is the course layer's one reader; `migrate` reads the block only to strip
# and rewrite it.
_MAY_READ_THE_BLOCK = {"settings.py", "migrate.py"}


def test_no_module_but_settings_reads_assignment_defaults():
    """The course layer has one reader: no other module reads the block, whether by the
    literal key or by the constant."""
    found = [
        f"{path.name}:{line}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name not in _MAY_READ_THE_BLOCK
        for line in _block_reads(ast.parse(path.read_text()))
    ]
    assert found == []


def test_the_read_check_sees_a_read_by_the_constant():
    tree = ast.parse(
        "from .settings import ASSIGNMENT_DEFAULTS_KEY\n"
        "x = meta.get(ASSIGNMENT_DEFAULTS_KEY)\n"
        "y = meta['assignment_defaults']\n"
        "z = {ASSIGNMENT_DEFAULTS_KEY: 1}\n"
    )
    assert _block_reads(tree) == [2, 3]


def test_the_digest_checks_the_spec_the_handout_resolves(monkeypatch):
    """`assignments.yml` makes a1 public on a template that says nothing about visibility:
    the digest reads the same answer the handout does."""
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: "assignments:\n  a1:\n    visibility: public\n",
    )
    template = "type: individual\nsubmit_via: assignment_repo\n"
    handed_out = [{"visibility": "public"}] * 3
    faults, spec = grades.grading_spec_faults(
        "a1",
        "assignment-1-f2026",
        "C",
        template,
        None,
        handed_out,
        releases_solution=True,
        semester_org="Sem",
    )
    assert spec.visibility == "public" and dict(spec.sources)["visibility"] == (
        "assignment"
    )
    texts = [f.what for f in faults]
    assert not any("does not describe the repos" in t for t in texts)
    assert any("`solution_datetime:`" in t for t in texts)


@pytest.mark.parametrize(
    ("key", "typo", "course_value"),
    [("visibility", "publik", "public"), ("team_formation", "asigned", "assigned")],
)
def test_a_closed_vocabulary_typo_leaves_the_next_layer_to_answer(
    monkeypatch, key, typo, course_value
):
    monkeypatch.setattr(
        settings, "org_meta", lambda org: {"assignment_defaults": {key: course_value}}
    )
    monkeypatch.setattr(
        settings, "_assignments_text", lambda org: f"defaults:\n  {key}: {typo}\n"
    )
    assert settings.effective("Sem", "a1", key, course_org="C") == (
        course_value,
        "course",
    )


@pytest.mark.parametrize(
    ("key", "typo"), [("visibility", "publik"), ("team_formation", "x")]
)
def test_a_template_typo_leaves_the_cascade_to_answer(monkeypatch, key, typo):
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: (
            f"defaults:\n  {key}: {'public' if key == 'visibility' else 'assigned'}\n"
        ),
    )
    monkeypatch.setattr(
        grades, "_grading_text", lambda org, template: f"type: group\n{key}: {typo}\n"
    )
    spec = grades.load_grading_spec("C", "t", semester_org="Sem", slug="a1")
    assert dict(spec.sources)[key] == "semester"
    assert any(d.field == key for d in spec.dropped)
