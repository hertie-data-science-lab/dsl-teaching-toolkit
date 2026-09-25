"""Course-level defaults in `dsl-course.yml`: `assignment_defaults:` answers the New
assignment boxes left at the course-default choice; the `schedule.yml` Bootstrap semester
seeds names the institution's defaults and copies none of them."""

from __future__ import annotations

import json
from datetime import date

import yaml

from dsl_course import bootstrap_course, course, policy, scaffold, schedule, settings
from dsl_course.ops.registry import REGISTRY
from dsl_course.ops.request import parse_request
from dsl_course.welcome import template

SENTINEL = course.COURSE_DEFAULT_CHOICE


# ------------------------------------------------------------- assignment_defaults


def test_the_four_new_assignment_boxes_can_be_set_course_wide():
    block = {
        "formats": "py",
        "submit_via": "external",
        "team_formation": "assigned",
        "visibility": "public",
    }
    assert settings.parse_assignment_defaults(block) == block


def test_a_course_default_outside_the_vocabulary_is_refused_out_loud(capsys):
    # Refused, so the course states nothing and the institution's value applies.
    got = settings.parse_assignment_defaults({"visibility": "everyone"})
    assert got == {}
    assert "visibility" in capsys.readouterr().err


def test_a_course_default_format_is_a_list_of_starters_as_the_box_takes(capsys):
    got = settings.parse_assignment_defaults({"formats": "ipynb, py"})
    assert got == {"formats": "ipynb,py"}
    # An unusable answer is dropped, so the toolkit's ipynb applies - never `none`.
    for bad in ("ipnb", "none,py", ""):
        assert settings.parse_assignment_defaults({"formats": bad}) == {}
        assert "format" in capsys.readouterr().err
    assert scaffold.resolve_answers({"formats": SENTINEL}, {}) == {"formats": "ipynb"}


def test_the_legacy_submit_via_word_reads_as_its_new_name():
    assert settings.parse_assignment_defaults({"submit_via": "github"}) == {
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
    # A box somebody chose is kept; one they left falls to the course, then the toolkit.
    assert got == {
        "formats": "rmd",
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

    def fake_scaffold(org, number, tag, formats, kind, **kw):
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
            "--number",
            "1",
            "--semester",
            "f2026",
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
    assert seen["formats"] == ["py"]
    assert seen["submit_via"] == "shared_dropbox_repo"
    # The two run boxes are passed on unanswered: the file writes them commented and the
    # cascade answers them at read time.
    assert seen["team_formation"] == SENTINEL
    assert seen["visibility"] == SENTINEL


def test_the_console_s_new_assignment_leaves_unanswered_boxes_to_the_cascade():
    request = parse_request(
        json.dumps(
            {
                "schema": "dsl.request/1",
                "op": "assignment.create",
                "actor": "prof",
                "course_org": "Course",
                "args": {"number": "1", "semester": "f2026"},
                "preview": False,
            }
        )
    )
    argv = REGISTRY["assignment.create"].argv(request)
    for flag in ("--formats", "--team-formation", "--submit-via", "--visibility"):
        assert argv[argv.index(flag) + 1] == SENTINEL


def test_an_answer_on_the_form_beats_the_course_default(monkeypatch):
    seen = _run_new_assignment(
        monkeypatch,
        ["--formats", "qmd", "--visibility", "public"],
        {"formats": "py", "visibility": "private"},
    )
    assert seen["formats"] == ["qmd"] and seen["visibility"] == "public"


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
    assert f"semester_end + {ours['archive']['grace_days']} days" in text
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


def test_the_course_template_offers_the_course_layer_and_nothing_retired():
    text = template("course/dsl-course.yml")
    for key in course.RETIRED_COURSE_KEYS:
        assert f"\n{key}:" not in text and f"# {key}:" not in text
    block = yaml.safe_load(text.format(course_name="C", course_code="E1"))
    # Every line commented: the course layer is optional, absent = the institution's.
    assert block[settings.ASSIGNMENT_DEFAULTS_KEY] is None
    for key in ("formats", "submit_via", "team_formation", "visibility"):
        assert key in text
