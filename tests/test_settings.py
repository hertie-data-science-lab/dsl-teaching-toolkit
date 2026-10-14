"""The run-settings cascade: institution -> course -> semester -> assignment, nearest wins,
the late pair paired at whichever layer states it, and every source named."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dsl_course import (
    faults,
    gh_contents,
    grading_spec,
    policy,
    schedule,
    settings,
)
from dsl_course.gh_contents import load_yaml_lines
from tests.conftest import run_settings

# The real reader, before conftest answers it with "nothing written" for every test.
RUN_SETTINGS_OF = schedule.run_settings_of

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
    run_settings(monkeypatch, lambda org: ASSIGNMENTS_YML)


def test_with_nothing_declared_every_value_is_the_institution_s():
    got = settings.effective_all(
        "Sem", inline=schedule.run_settings_of("Sem", "a1"), course_org="Course"
    )
    ours = policy.defaults()
    assert got["max_team_size"] == (ours["max_team_size"], "institution")
    assert got["late_window_days"] == (ours["late_window_days"], "institution")
    assert got["late_penalty_per_day"] == (ours["late_penalty_per_day"], "institution")
    assert got["team_formation"] == (ours["team_formation"], "institution")
    assert got["visibility"] == (ours["visibility"], "institution")


def test_the_course_layer_answers_before_the_institution(course):
    assert settings.effective(
        "Sem",
        inline=schedule.run_settings_of("Sem", "a1"),
        key="late_window_days",
        course_org="C",
    ) == (
        7,
        "course",
    )
    assert settings.effective(
        "Sem",
        inline=schedule.run_settings_of("Sem", "a1"),
        key="visibility",
        course_org="C",
    ) == (
        "public",
        "course",
    )
    assert settings.effective(
        "Sem",
        inline=schedule.run_settings_of("Sem", "a1"),
        key="max_team_size",
        course_org="C",
    )[1] == ("institution")


def test_assignments_yml_is_the_semester_and_assignment_layers(course, semester):
    a1 = settings.effective_all(
        "Sem", inline=schedule.run_settings_of("Sem", "a1"), course_org="C"
    )
    assert a1["max_team_size"] == (4, "semester")
    a2 = settings.effective_all(
        "Sem", inline=schedule.run_settings_of("Sem", "a2"), course_org="C"
    )
    assert a2["max_team_size"] == (2, "assignment")
    assert a2["visibility"] == ("public", "course")


def test_the_late_pair_stays_paired_at_the_layer_that_states_it(course, semester):
    # The semester defaults state the window alone: that IS its rule, penalty none - the
    # course's 20% is not borrowed for the missing half.
    a1 = settings.effective_all(
        "Sem", inline=schedule.run_settings_of("Sem", "a1"), course_org="C"
    )
    assert a1["late_window_days"] == (5, "semester")
    assert a1["late_penalty_per_day"] == (None, "semester")
    # a2's own block states the penalty alone.
    a2 = settings.effective_all(
        "Sem", inline=schedule.run_settings_of("Sem", "a2"), course_org="C"
    )
    assert a2["late_penalty_per_day"] == ("5%", "assignment")
    assert a2["late_window_days"] == (None, "assignment")


def test_no_assignments_yml_means_both_of_its_layers_are_empty(course):
    assert settings.semester_defaults("Sem") == {}
    assert settings.effective(
        "Sem",
        inline=schedule.run_settings_of("Sem", "a2"),
        key="max_team_size",
        course_org="C",
    )[1] == ("institution")


def test_a_semester_read_that_fails_raises_and_is_not_cached(monkeypatch):
    run_settings(monkeypatch, _READ_ASSIGNMENTS)
    answers = [RuntimeError("could not read Sem/semester-config/assignments.yml: 403")]

    def read(org, repo, path, ref=""):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(gh_contents, "get_file_content", read)
    with pytest.raises(RuntimeError):
        settings.semester_defaults("Sem")
    # A later read that succeeds is read, not the failure remembered as "nothing".
    answers.append("defaults:\n  max_team_size: 4\n")
    assert settings.semester_defaults("Sem") == {"max_team_size": 4}


def test_an_absent_assignments_yml_is_the_empty_layer(monkeypatch):
    run_settings(monkeypatch, _READ_ASSIGNMENTS)
    monkeypatch.setattr(gh_contents, "get_file_content", lambda *a, **k: None)
    assert settings.semester_defaults("Sem") == {}


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
    run_settings(
        monkeypatch,
        lambda org: "defaults:\n  max_team_size: lots\n",
    )
    assert settings.effective(
        "Sem",
        inline=schedule.run_settings_of("Sem", "a1"),
        key="max_team_size",
        course_org="C",
    ) == (
        policy.defaults()["max_team_size"],
        "institution",
    )


@pytest.mark.parametrize("key", settings.RUN_KEYS)
def test_a_run_key_in_a_template_is_refused_by_its_own_code(monkeypatch, key):
    # A template states what the assignment IS; how a semester runs it is not its to say.
    with pytest.raises(faults.RunSettingInTemplate) as caught:
        grading_spec.parse_grading_spec(f"type: group\n{key}: 3\n")
    assert caught.value.old == key
    assert caught.value.code == faults.RUN_SETTING_IN_TEMPLATE
    assert str(caught.value) == (
        f"grading_config.yml: `{key}` is a run setting: set it on the assignment entry "
        f"in schedule.yml, not in grading_config.yml"
    )
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, template: f"{key}: 3\n"
    )
    assert grading_spec.load_grading_spec("C", "t").not_migrated


def test_load_grading_spec_carries_effective_values_and_their_sources(
    monkeypatch, course, semester
):
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, template: "type: group\n"
    )
    spec = grading_spec.load_grading_spec(
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
        grading_spec, "_grading_text", lambda org, template: "submit_via: external\n"
    )
    assert (
        grading_spec.load_grading_spec("C", "assignment-1-f2026").visibility
        == "private"
    )


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
    run_settings(
        monkeypatch,
        lambda org: "assignments:\n  a1:\n    visibility: public\n",
    )
    template = "type: individual\nsubmit_via: assignment_repo\n"
    handed_out = [{"visibility": "public"}] * 3
    faults, spec = grading_spec.grading_spec_faults(
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
    run_settings(monkeypatch, lambda org: f"defaults:\n  {key}: {typo}\n")
    assert settings.effective(
        "Sem", inline=schedule.run_settings_of("Sem", "a1"), key=key, course_org="C"
    ) == (
        course_value,
        "course",
    )


@pytest.mark.parametrize(
    ("key", "inline", "typo"),
    [
        ("visibility", "assignment_repo_visibility", "publik"),
        ("team_formation", "team_formation", "x"),
    ],
)
def test_an_entry_setting_typo_leaves_the_semester_default_to_answer(
    monkeypatch, key, inline, typo
):
    good = "public" if key == "visibility" else "assigned"
    monkeypatch.setattr(
        settings, "_assignments_text", lambda org: f"defaults:\n  {key}: {good}\n"
    )
    entry = (
        f"assignments:\n  a1:\n    template: t\n    number: 1\n"
        f"    due_datetime: 2026-10-13\n    {inline}: {typo}\n"
    )
    # The entry's settings come off the parsed plan (`schedule.run_settings_of`).
    monkeypatch.setattr(schedule, "_schedule_text", lambda org: entry)
    monkeypatch.setattr(schedule, "run_settings_of", RUN_SETTINGS_OF)
    monkeypatch.setattr(
        grading_spec, "_grading_text", lambda org, template: "type: group\n"
    )
    spec = grading_spec.load_grading_spec("C", "t", semester_org="Sem", slug="a1")
    assert getattr(spec, key) == good and dict(spec.sources)[key] == "semester"
    # ...and the refused value is a fault on the schedule entry, at its line.
    sched = schedule.parse(
        load_yaml_lines(
            "assignments:\n  a1:\n    template: t\n    number: 1\n"
            f"    due_datetime: 2026-10-13\n    {inline}: {typo}\n"
        )
    )
    (fault,) = sched.faults
    assert (fault.file, fault.where, fault.field, fault.lineno) == (
        "schedule.yml",
        "assignments.a1",
        inline,
        6,
    )


# ------------------------------------------------------------ assignments.yml, read


def test_the_assignments_block_of_assignments_yml_is_legacy_and_read_for_migrate():
    # Decision 0038 rule 9: each assignment's settings are written on its schedule.yml
    # entry; a per-assignment block here is NOT_MIGRATED, read only for the migration.
    got = settings.parse_instance(
        "defaults:\n  max_team_size: 4\n"
        "assignments:\n  a2:\n    late_window_days: 0\n"
        "    semester_dest_repo: homework-2\n"
    )
    assert got.defaults == {"max_team_size": 4}
    assert got.blocks == {"a2": {"late_window_days": 0}}
    assert got.dests == {"a2": "homework-2"}
    (fault,) = got.faults
    assert (fault.where, fault.code) == ("assignments", faults.NOT_MIGRATED)


def test_an_entrys_inline_settings_are_read_through_their_readers():
    values = settings.inline_settings(
        {
            "late_window_days": 3,
            "assignment_repo_visibility": "public",
            "template": "t",
            "due_datetime": "2026-10-13",
        },
        "assignments.a1",
        dropped := [],
    )
    assert (values, dropped) == ({"late_window_days": 3, "visibility": "public"}, [])
    assert settings.inline_blocks({"a1": {"max_team_size": 4}, "a2": "x"}) == {
        "a1": {"max_team_size": 4}
    }


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("defaults:\n  semester_dest_repo: x\n", "semester_dest_repo"),
        ("extra:\n  a: 1\n", "extra"),
    ],
)
def test_every_line_assignments_yml_cannot_use_is_a_fault(text, field):
    (fault,) = settings.parse_instance(text).faults
    assert fault.file == settings.ASSIGNMENTS_FILE and fault.field == field


def test_an_assignments_yml_that_is_not_yaml_stops_the_cascade(monkeypatch):
    run_settings(monkeypatch, lambda org: "defaults: [\n")
    read = settings.instance("Sem")
    assert read.unparseable and read.faults[0].file == settings.ASSIGNMENTS_FILE
    # Fail closed: nothing hands out or grades on defaults the semester did not choose.
    with pytest.raises(faults.Unusable):
        settings.semester_defaults("Sem")


def test_the_schedule_carries_assignments_yml_s_faults(monkeypatch):
    run_settings(monkeypatch, lambda org: "defaults:\n  max_team_size: 0\n")
    monkeypatch.setattr(
        schedule,
        "_schedule_text",
        lambda org: (
            "assignments:\n  a1:\n    template: t1\n    number: 1\n"
            "    due_datetime: 2026-10-13\n"
        ),
    )
    sched = schedule.load("Sem")
    assert set(sched.assignments) == {"a1"}
    got = [(f.file, f.where, f.field) for f in sched.faults]
    assert got == [(settings.ASSIGNMENTS_FILE, "defaults", "max_team_size")]
    assert len(sched.dropped) == 1
