"""The syllabus sessions generator: schedule.yml + readings -> a paste-ready block.

Deliberately paste-ready rather than an in-place edit of SYLLABUS.md - see the module
docstring in dsl_course/syllabus.py. These pin the mapping (which entry names a session, and
what its readings are) and the heading nesting, which is the thing that looked wrong on the
site before it was fixed there too.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dsl_course import deploy, syllabus
from dsl_course.schedule import Deploy, Release, Schedule

BERLIN = ZoneInfo("Europe/Berlin")

READING = (
    "# Session 1 readings\n\n"
    "## Required Readings\n\n"
    "- Blitzstein & Hwang, ch. 1-2.\n\n"
    "## Optional Readings\n\n"
    "- Wasserman, s1.1-1.5.\n"
)
TREE = (
    "SYLLABUS.md",
    "lectures/01_intro/deck.html",
    "readings/01_week-1/reading.md",
    "readings/01_week-1/blitzstein.pdf",
)


@pytest.fixture
def wired(monkeypatch):
    sched = Schedule(
        releases=[
            Release(
                "lecture-2",
                datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "materials", None)],
                title="Random Variables",
                description="Distributions, expectation\nand variance.",
            ),
            Release(
                "lecture-1",
                datetime(2026, 9, 1, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/01_intro", "materials", None)],
                title="Probability Theory",
                description="Sample spaces and Bayes' rule.",
            ),
        ]
    )
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: sched)
    monkeypatch.setattr(syllabus, "default_branch", lambda o, r: "main")
    monkeypatch.setattr(syllabus, "repo_tree", lambda o, r, b, k: TREE)
    monkeypatch.setattr(
        syllabus,
        "get_file_content",
        lambda o, r, p: READING if p.endswith("reading.md") else "",
    )
    return lambda: syllabus.build("Course", "Cohort-f2026", "cm")[0]


def test_sessions_come_out_in_order_with_their_declared_names(wired):
    out = wired()
    assert out.index("### Session 1: Probability Theory") < out.index(
        "### Session 2: Random Variables"
    )
    # Ordered by session number, not by the order the plan happens to be written in.
    assert out.startswith("## Course sessions and readings")


def test_learning_objectives_are_folded_to_one_paragraph(wired):
    # The plan may hold them as a wrapped block; a syllabus wants a sentence.
    assert "*Learning objectives.* Distributions, expectation and variance." in wired()


def test_readings_nest_under_their_session_heading(wired):
    out = wired()
    # The reading file opens with its own `# Session 1 readings`, which unshifted would
    # outrank both the session heading above it and the section heading above that.
    assert "#### Session 1 readings" in out
    assert "##### Required Readings" in out and "##### Optional Readings" in out
    assert "\n# Session 1 readings" not in out


def test_a_session_without_readings_still_appears(wired):
    # Session 2 has no readings folder in TREE; it must not vanish from the syllabus.
    out = wired()
    assert "### Session 2: Random Variables" in out
    assert out.count("Required Readings") == 1


def test_only_the_citation_text_is_used_never_the_pdf(monkeypatch, wired):
    # `blitzstein.pdf` sits in the same folder. A syllabus quotes the list, not the file.
    assert "blitzstein.pdf" not in wired()


def test_the_generated_block_is_never_released_to_students():
    assert syllabus.SYLLABUS_SESSIONS_FILE in deploy.ROOT_RELEASE_EXCLUDED


def test_the_cli_succeeds_on_a_real_schedule(monkeypatch, capsys, wired):
    # The regression this pins: `main` counted sessions by searching its own finished
    # markdown for a marker the formatter had since changed, so the count was always zero
    # and the button ALWAYS reported "no dated sessions". Only the failure path was tested.
    written = {}
    monkeypatch.setattr(
        syllabus,
        "put_file",
        lambda o, r, p, c, m: written.update({p: c.decode()}) or True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--course-org",
            "C",
            "--cohort-org",
            "H",
            "--course-source-repo",
            "cm",
            "--write",
        ],
    )
    assert syllabus.main() == 0
    assert "2 session(s)" in capsys.readouterr().out
    assert (
        "### Session 1: Probability Theory" in written[syllabus.SYLLABUS_SESSIONS_FILE]
    )


def test_a_titleless_entry_does_not_blank_a_session_the_site_names(wired, monkeypatch):
    # Re-deriving the naming rule here took the title from the EARLIEST deploy touching a
    # session whether or not that entry declared one - so a readings-only or "Course opens"
    # entry silently blanked a session the website names. Reading `site._planned_sessions`
    # is what makes the two agree.
    sched = syllabus.schedule.load("Cohort-f2026")
    sched.releases.append(
        Release(
            "readings-push",
            datetime(2026, 8, 30, 9, 0, tzinfo=BERLIN),  # earliest of all
            deploy=[Deploy("cm", "readings/01_week-1", "materials", None)],
        )
    )
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: sched)
    assert "### Session 1: Probability Theory" in wired()


def test_a_schedule_with_no_sessions_is_an_error_not_an_empty_file(monkeypatch, capsys):
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(syllabus, "default_branch", lambda o, r: "main")
    monkeypatch.setattr(syllabus, "repo_tree", lambda o, r, b, k: ())
    monkeypatch.setattr(
        "sys.argv",
        ["x", "--course-org", "C", "--cohort-org", "H", "--course-source-repo", "cm"],
    )
    assert syllabus.main() == 1
    assert "names no dated sessions" in capsys.readouterr().err
