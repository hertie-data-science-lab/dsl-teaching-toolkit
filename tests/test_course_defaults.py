"""Course-level defaults in `dsl-course.yml`: `assignment_defaults:` answers the New
assignment boxes left at the course-default choice, and `cohort_defaults:` shapes the
`schedule.yml` Bootstrap cohort seeds."""

from __future__ import annotations

from datetime import date

import yaml

from dsl_course import bootstrap_course, course, grades, scaffold, schedule
from dsl_course.central import pin_central_ref
from dsl_course.welcome import template

SENTINEL = course.COURSE_DEFAULT_CHOICE


# ------------------------------------------------------------- assignment_defaults


def test_the_four_new_assignment_boxes_can_be_set_course_wide():
    block = {
        "format": "py",
        "submit_via": "external",
        "team_formation": "assigned",
        "visibility": "public",
    }
    assert grades.parse_assignment_defaults(block) == block


def test_a_course_default_outside_the_vocabulary_is_refused_out_loud(capsys):
    got = grades.parse_assignment_defaults({"visibility": "everyone"})
    assert got == {"visibility": "private"}
    assert "visibility" in capsys.readouterr().err


def test_the_legacy_submit_via_word_reads_as_its_new_name():
    assert grades.parse_assignment_defaults({"submit_via": "github"}) == {
        "submit_via": "assignment_repo"
    }


def test_a_box_left_at_the_course_default_takes_the_courses_value():
    answers = {
        "format": SENTINEL,
        "team_formation": SENTINEL,
        "submit_via": SENTINEL,
        "visibility": "public",
    }
    got = scaffold.resolve_answers(
        answers, {"format": "rmd", "submit_via": "external", "visibility": "private"}
    )
    # A box somebody chose is kept; one they left falls to the course, then the toolkit.
    assert got == {
        "format": "rmd",
        "team_formation": "self_select",
        "submit_via": "external",
        "visibility": "public",
    }


def test_with_no_course_defaults_the_toolkit_answers_as_the_form_used_to():
    answers = dict.fromkeys(
        ("format", "team_formation", "submit_via", "visibility"), SENTINEL
    )
    assert scaffold.resolve_answers(answers, {}) == {
        "format": "ipynb",
        "team_formation": "self_select",
        "submit_via": "assignment_repo",
        "visibility": "private",
    }


def _run_new_assignment(monkeypatch, argv: list[str], defaults: dict) -> dict:
    seen: dict = {}

    def fake_scaffold(org, number, tag, formats, kind, **kw):
        seen.update(formats=formats, kind=kind, **kw)
        return 0

    monkeypatch.setattr(scaffold, "course_assignment_defaults", lambda org: defaults)
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
            "--tag",
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
            "format": "py",
            "submit_via": "shared_dropbox_repo",
            "team_formation": "assigned",
        },
    )
    assert seen["formats"] == ["py"]
    assert seen["submit_via"] == "shared_dropbox_repo"
    assert seen["team_formation"] == "assigned"
    assert seen["visibility"] == "private"


def test_an_answer_on_the_form_beats_the_course_default(monkeypatch):
    seen = _run_new_assignment(
        monkeypatch,
        ["--format", "qmd", "--visibility", "public"],
        {"format": "py", "visibility": "private"},
    )
    assert seen["formats"] == ["qmd"] and seen["visibility"] == "public"


# ------------------------------------------------------------- cohort_defaults


def test_cohort_defaults_parse_a_zone_and_an_archive_switch():
    got = schedule.parse_cohort_defaults(
        {"timezone": "America/New_York", "archive": {"auto": True, "grace_days": 30}}
    )
    assert got == {
        "timezone": "America/New_York",
        "archive": {"auto": True, "grace_days": 30},
    }


def test_unusable_cohort_defaults_are_dropped_with_a_warning(capsys):
    got = schedule.parse_cohort_defaults(
        {"timezone": "Mars/Olympus", "archive": {"auto": "maybe"}, "colour": "red"}
    )
    assert got == {}
    err = capsys.readouterr().err
    assert "Mars/Olympus" in err and "auto" in err and "colour" in err


def _seeded(defaults: dict) -> str:
    """The schedule.yml Bootstrap cohort writes, through the real render path."""
    return bootstrap_course._scaffold_text(
        "schedule.yml", "classroom-config/schedule.yml", "main", "f2026", 2026, defaults
    ).decode()


def _parsed(text: str, **extra) -> schedule.Schedule:
    meta = yaml.safe_load(text) or {}
    meta.update(extra)
    return schedule.parse(meta)


def test_no_cohort_defaults_seed_todays_skeleton():
    plain = pin_central_ref(template("classroom-config/schedule.yml"), "main").format(
        tag="f2026", year=2026, year_next=2027
    )
    assert _seeded({}) == plain
    assert "\narchive:\n" in _seeded({})


def test_a_course_timezone_is_seeded_live():
    sched = _parsed(_seeded({"timezone": "America/New_York"}))
    assert sched.timezone == "America/New_York" and sched.dropped == []


def test_auto_archive_off_seeds_no_archive_block():
    sched = _parsed(
        _seeded({"archive": {"auto": False, "grace_days": None}}),
        semester_end="2026-12-18",
    )
    assert sched.archive is None


def test_auto_archive_on_counts_the_courses_grace_days():
    sched = _parsed(
        _seeded({"archive": {"auto": True, "grace_days": 30}}),
        semester_end="2026-12-18",
    )
    assert sched.archive.when == date(2027, 1, 17)
    assert sched.dropped == []


def test_an_unusable_grace_days_is_flagged_and_the_sixty_days_stand():
    sched = schedule.parse(
        {"semester_end": "2026-12-18", "archive": {"grace_days": "a month"}}
    )
    assert sched.archive.when == date(2026, 12, 18) + schedule.ARCHIVE_GRACE
    (drop,) = sched.dropped
    assert drop.startswith("archive.grace_days")


def test_the_course_template_documents_both_blocks():
    text = template("course/dsl-course.yml")
    assert "cohort_defaults" in text
    for key in ("format", "submit_via", "team_formation", "visibility"):
        assert key in text
