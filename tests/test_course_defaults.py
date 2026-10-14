"""Course-level defaults in `course.yml`: `assignment_defaults:` answers the New
assignment boxes left at the course-default choice; the `schedule.yml` Bootstrap semester
seeds names the institution's defaults and copies none of them."""

from __future__ import annotations

import json
from datetime import date

import pytest
import yaml

from dsl_course import (
    bootstrap_course,
    course,
    legacy,
    policy,
    scaffold,
    schedule,
    settings,
)
from dsl_course.ops.registry import REGISTRY
from dsl_course.ops.request import RequestError, parse_request

SENTINEL = course.COURSE_DEFAULT_CHOICE


# ------------------------------------------------------------- course.yml defaults


def test_the_course_defaults_block_reads_its_keys_and_the_submit_box():
    block = {
        "submit_via": "external",
        "team_formation": "assigned",
        "assignment_repo_visibility": "public",
        "materials_repo": "handouts",
    }
    assert settings.parse_course_defaults(block) == {
        "submit_via": "external",
        "team_formation": "assigned",
        "visibility": "public",
        "materials_repo": "handouts",
    }


def test_formats_left_the_cascade(capsys):
    # Decision 0039 rule 2: the New assignment wizard asks; a course cannot set it.
    assert settings.parse_course_defaults({"formats": "py"}) == {}
    assert "formats" in capsys.readouterr().err
    assert scaffold.resolve_answers({"formats": SENTINEL}, {"formats": ("py",)}) == {
        "formats": "ipynb"
    }


def test_a_course_default_outside_the_vocabulary_is_refused_out_loud(capsys):
    # Refused, so the course states nothing and the institution's value applies.
    got = settings.parse_course_defaults({"assignment_repo_visibility": "everyone"})
    assert got == {}
    assert "visibility" in capsys.readouterr().err


def test_the_legacy_submit_via_word_reads_as_its_new_name():
    assert settings.parse_course_defaults({"submit_via": "github"}) == {
        "submit_via": "assignment_repo"
    }


def test_a_box_left_at_the_course_default_takes_the_courses_value():
    answers = {
        "formats": SENTINEL,
        "team_formation": SENTINEL,
        "submit_via": SENTINEL,
        "visibility": "public",
    }
    got = scaffold.resolve_answers(
        answers, {"formats": "rmd", "submit_via": "external", "visibility": "private"}
    )
    # A box somebody chose is kept; one they left falls to the course, then the toolkit
    # (formats to the toolkit at once: it left the cascade).
    assert got == {
        "formats": "ipynb",
        "team_formation": "self_select",
        "submit_via": "external",
        "visibility": "public",
    }


def test_with_no_course_defaults_the_toolkit_answers_as_the_form_used_to():
    answers = dict.fromkeys(
        ("formats", "team_formation", "submit_via", "visibility"), SENTINEL
    )
    assert scaffold.resolve_answers(answers, {}) == {
        "formats": "ipynb",
        "team_formation": "self_select",
        "submit_via": "assignment_repo",
        "visibility": "private",
    }


def _run_new_assignment(monkeypatch, argv: list[str], defaults: dict) -> dict:
    seen: dict = {}

    def fake_scaffold(org, name, formats, kind, **kw):
        seen.update(formats=formats, kind=kind, **kw)
        return 0

    monkeypatch.setattr(settings, "course_defaults", lambda org: defaults)
    monkeypatch.setattr(scaffold, "scaffold_assignment", fake_scaffold)
    monkeypatch.setattr(
        "sys.argv",
        [
            "scaffold",
            "assignment",
            "--org",
            "Org",
            "--name",
            "Trees",
            *argv,
        ],
    )
    assert scaffold.main() == 0
    return seen


def test_the_button_untouched_scaffolds_with_the_courses_defaults(monkeypatch):
    seen = _run_new_assignment(
        monkeypatch,
        [],
        {
            "formats": "py",
            "submit_via": "shared_dropbox_repo",
            "team_formation": "assigned",
        },
    )
    assert seen["formats"] == ["ipynb"]
    assert seen["submit_via"] == "shared_dropbox_repo"
    # No run box at all: how a semester runs it is that semester's assignments.yml.
    assert "team_formation" not in seen and "visibility" not in seen


def test_the_console_s_new_assignment_leaves_unanswered_boxes_to_the_cascade():
    request = parse_request(
        json.dumps(
            {
                "schema": "dsl.request/1",
                "op": "assignment.create",
                "actor": "prof",
                "course_org": "Course",
                "args": {"name": "Trees"},
                "preview": False,
            }
        )
    )
    argv = REGISTRY["assignment.create"].argv(request)
    for flag in ("--formats", "--submit-via"):
        assert argv[argv.index(flag) + 1] == SENTINEL
    assert "--team-formation" not in argv and "--visibility" not in argv


def _create(args: dict) -> str:
    return json.dumps(
        {
            "schema": "dsl.request/1",
            "op": "assignment.create",
            "actor": "prof",
            "course_org": "Course",
            "args": args,
            "preview": False,
        }
    )


def test_new_assignment_takes_a_name_and_nothing_a_template_does_not_have():
    # Decision 0014: a template has no number and no semester, and is never copied by
    # the engine. The args schema refuses each like any unknown arg.
    request = parse_request(_create({"name": "Trees"}))
    argv = REGISTRY["assignment.create"].argv(request)
    assert argv[:5] == ["assignment", "--org", "Course", "--name", "Trees"]
    for stray in ({"number": "3"}, {"semester": "f2026"}, {"copy_from": "a-f2025"}):
        with pytest.raises(RequestError) as exc:
            parse_request(_create({"name": "Trees", **stray}))
        assert exc.value.code == "BAD_ARGS"


def test_an_answer_on_the_form_beats_the_course_default(monkeypatch):
    seen = _run_new_assignment(
        monkeypatch,
        ["--formats", "qmd", "--submit-via", "external"],
        {"formats": "py", "submit_via": "assignment_repo"},
    )
    assert seen["formats"] == ["qmd"] and seen["submit_via"] == "external"


# ------------------------------------------------ what a new semester is seeded with


def _seeded() -> str:
    """The schedule.yml Bootstrap semester writes, through the real render path."""
    return bootstrap_course._scaffold_text(
        "schedule.yml", "semester-config/schedule.yml", "main", "f2026", 2026
    ).decode()


def test_the_seeded_skeleton_names_the_institution_s_defaults_and_sets_none():
    text = _seeded()
    ours = policy.defaults()
    assert f"# timezone: {ours['timezone']}" in text
    assert f"semester_end + {ours['archive_grace_days']} days" in text
    sched = schedule.parse(yaml.safe_load(text) or {})
    # Nothing copied into the semester: absent means the institution's default.
    assert sched.timezone == schedule.DEFAULT_TZ == ours["timezone"]
    assert "\narchive:\n" in text


def test_an_unusable_grace_days_is_flagged_and_the_sixty_days_stand():
    sched = schedule.parse(
        {"semester_end": "2026-12-18", "archive": {"grace_days": "a month"}}
    )
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    (drop,) = sched.dropped
    assert drop.startswith("archive.grace_days")


def test_the_course_seed_offers_every_default_and_nothing_retired():
    text = bootstrap_course.course_seed("C", "E1")
    for key in legacy.renames(legacy.COURSE_FILE_KEYS):
        assert f"\n{key}:" not in text
    seeded = yaml.safe_load(text)
    # Every default commented: the course layer is optional, absent = the institution's.
    assert seeded["defaults"] is None
    for key in settings.DEFAULT_FILE_KEYS:
        assert f"  # {key}:" in text


def test_the_console_s_starter_choice_reaches_the_scaffold():
    argv = REGISTRY["assignment.create"].argv(
        parse_request(_create({"name": "Trees", "starter": "handwritten"}))
    )
    assert argv[argv.index("--starter") + 1] == "handwritten"
    left_out = REGISTRY["assignment.create"].argv(parse_request(_create({"name": "T"})))
    assert "--starter" not in left_out
    with pytest.raises(RequestError):
        parse_request(_create({"name": "Trees", "starter": "copied"}))


def test_the_scaffolded_grading_config_says_how_the_starter_is_written():
    def starter(**k):
        text = scaffold._grading_config(
            title="T",
            kind="individual",
            submit_via="assignment_repo",
            formats=["py"],
            **k,
        )
        return yaml.safe_load(text)["starter"]

    # Rule 1: tests on, derived; tests off, hand-written; an explicit answer wins.
    assert starter(autograde=True) == "derived"
    assert starter(autograde=False) == "handwritten"
    assert starter(autograde=False, starter="derived") == "derived"
