"""The syllabus weekly plan: schedule.yml + readings entries -> a block written between
markers in the syllabus (decision 0031 rule 9). These pin the mapping (which entry names a
session, and which readings entries sit under it), the heading nesting, and that a write
touches nothing outside the markers.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dsl_course import syllabus
from dsl_course.materials import PLAN_END, PLAN_START, Declared
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
    "readings/01_week-1/READINGS.md",
    "readings/01_week-1/blitzstein.pdf",
)


@pytest.fixture
def wired(monkeypatch):
    sched = Schedule(
        releases=[
            # A week ahead of its lecture, silent, as the demo ships them.
            Release(
                "readings-1",
                datetime(2026, 8, 25, 9, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "readings/01_week-1", "materials", None)],
                show_on_site=False,
            ),
            Release(
                "lecture-2",
                datetime(2026, 9, 8, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/02_x", "materials", None)],
                title="Random Variables",
                details="Distributions, expectation\nand variance.",
            ),
            Release(
                "lecture-1",
                datetime(2026, 9, 1, 10, 0, tzinfo=BERLIN),
                deploy=[Deploy("cm", "lectures/01_intro", "materials", None)],
                title="Probability Theory",
                details="Sample spaces and Bayes' rule.",
            ),
        ]
    )
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: sched)
    monkeypatch.setattr(syllabus, "read_materials", lambda org, repo: Declared())
    monkeypatch.setattr(syllabus, "default_branch", lambda o, r: "main")
    monkeypatch.setattr(syllabus, "repo_tree", lambda o, r, b, k: TREE)
    monkeypatch.setattr(
        syllabus,
        "get_file_content",
        lambda o, r, p: READING if p.endswith("READINGS.md") else "",
    )
    return lambda: syllabus.build("Course", "Semester-f2026")[0]


def test_sessions_come_out_in_order_with_their_declared_names(wired):
    out = wired()
    assert out.index("### Session 1: Probability Theory") < out.index(
        "### Session 2: Random Variables"
    )
    # The block carries no heading of its own: the syllabus's `## Weekly plan` is it.
    assert out.startswith("### Session 1: Probability Theory")


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
    # No readings entry falls before session 2; it must not vanish from the syllabus.
    out = wired()
    assert "### Session 2: Random Variables" in out
    assert out.count("Required Readings") == 1


def test_uploaded_files_are_named_alongside_the_overlay(wired):
    # The overlay's prose leads, and the files follow by NAME - never their bytes. Listing
    # them is what stops a session whose readings are PDFs coming out as a bare heading.
    out = wired()
    assert "- Blitzstein & Hwang, ch. 1-2." in out  # the prose
    assert "- blitzstein.pdf" in out  # ...and the file beside it
    assert out.index("Blitzstein & Hwang") < out.index("- blitzstein.pdf")
    # The overlay is never ALSO listed as a file - its content is already on the page.
    assert "- READINGS.md" not in out


def test_a_pdf_only_session_is_not_invisible(monkeypatch, wired):
    # The bug: a session whose readings are files and no prose used to render as a heading
    # with NOTHING under it - the one destination where uploading a reading and writing no
    # citations left the session blank. Overrides only what differs from `wired`, so a new
    # dependency in `build` cannot leave this test wired half the old way.
    monkeypatch.setattr(
        syllabus,
        "repo_tree",
        lambda o, r, b, k: ("lectures/01_intro/deck.html", "readings/01_week-1/x.pdf"),
    )
    monkeypatch.setattr(syllabus, "get_file_content", lambda o, r, p: "")
    out = wired()
    assert "- x.pdf" in out
    assert "### Session 1: Probability Theory" in out


BLOCK = "### Session 1\n\nNew.\n"


def test_place_appends_the_marked_block_under_a_weekly_plan_heading():
    out = syllabus.place("# My syllabus\n\nGrading: 40/60.\n", BLOCK)
    assert out == (
        "# My syllabus\n\nGrading: 40/60.\n\n## Weekly plan\n\n"
        f"{PLAN_START}\n### Session 1\n\nNew.\n{PLAN_END}\n"
    )
    assert syllabus.place("", BLOCK).startswith(f"## Weekly plan\n\n{PLAN_START}\n")


def test_place_replaces_only_between_the_markers_wherever_they_were_moved():
    text = (
        "# My syllabus\n\n## Sessions\n\n"
        f"{PLAN_START}\nold plan\n{PLAN_END}\n\n## Grading\n\n40/60, my words.\n"
    )
    out = syllabus.place(text, BLOCK)
    assert out == text.replace("old plan", "### Session 1\n\nNew.")
    # Written again: the same file, nothing appended.
    assert syllabus.place(out, BLOCK) == out


def test_place_appends_when_the_markers_are_out_of_order_or_half_there():
    for text in (f"{PLAN_END}\nx\n{PLAN_START}\n", f"# S\n{PLAN_START}\nx\n"):
        out = syllabus.place(text, BLOCK)
        assert out.startswith(text.rstrip("\n"))
        assert out.endswith(f"## Weekly plan\n\n{PLAN_START}\n{BLOCK}{PLAN_END}\n")


SYLLABUS_TEXT = "# Syllabus\n\nWritten by the course team.\n"


def _writable(monkeypatch, text: str | None = SYLLABUS_TEXT, ok: bool = True) -> dict:
    """Stub the syllabus read and write; returns `{path: what was written}`, and
    `"sha"`, the sha the write was conditioned on."""
    written: dict = {}
    monkeypatch.setattr(
        syllabus,
        "get_file_with_sha",
        lambda o, r, p: None if text is None else (text, "abc123"),
    )

    def put(o, r, p, c, m, expected_sha=None):
        written.update({p: c.decode(), "sha": expected_sha, "message": m})
        return ok

    monkeypatch.setattr(syllabus, "put_file", put)
    return written


def test_the_cli_succeeds_on_a_real_schedule(monkeypatch, capsys, wired):
    # The regression this pins: `main` counted sessions by searching its own finished
    # markdown for a marker the formatter had since changed, so the count was always zero
    # and the button ALWAYS reported "no dated sessions". Only the failure path was tested.
    written = _writable(monkeypatch)
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--course-org",
            "C",
            "--semester-org",
            "H",
            "--course-source-repo",
            "cm",
            "--no-preview",
        ],
    )
    assert syllabus.main() == 0
    assert "2 session(s)" in capsys.readouterr().out
    assert "### Session 1: Probability Theory" in written["SYLLABUS.md"]


def _argv(monkeypatch, *extra):
    monkeypatch.setattr(
        "sys.argv",
        ["x", "--course-org", "C", "--semester-org", "H", "--course-source-repo", "cm"]
        + list(extra),
    )


def test_preview_and_write_both_hand_the_block_to_the_outcome(monkeypatch, wired):
    # MA1: the console panel shows the generated list; it used to live in the run log only.
    written = _writable(monkeypatch)
    _argv(monkeypatch)
    preview = syllabus.main()
    assert preview == 0 and written == {}
    assert preview.block.startswith("### Session 1")
    assert preview.counts == {"sessions": 2}
    assert preview.text == "Built the weekly plan: 2 sessions; nothing was written."
    _argv(monkeypatch, "--no-preview")
    wrote = syllabus.main()
    assert wrote == 0 and wrote.block == preview.block
    assert wrote.text == "Wrote the weekly plan (2 sessions) into cm/SYLLABUS.md."


def test_write_goes_between_the_markers_of_the_declared_syllabus(monkeypatch, wired):
    # Decision 0031 rule 9: the faculty's own words stay; only the marked block changes,
    # and the write is refused if the file moved on since it was read.
    monkeypatch.setattr(
        syllabus, "read_materials", lambda o, r: Declared("E1282.md", {}, True)
    )
    written = _writable(monkeypatch)
    _argv(monkeypatch, "--no-preview")
    out = syllabus.main()
    assert out == 0
    text = written["E1282.md"]
    assert text.startswith(SYLLABUS_TEXT.rstrip("\n"))
    assert text == syllabus.place(SYLLABUS_TEXT, out.block)
    assert written["sha"] == "abc123"
    assert written["message"] == "docs: write the weekly plan into E1282.md"
    # The file the console chose wins over the declared one.
    written.clear()
    _argv(monkeypatch, "--no-preview", "--syllabus", "Other.md")
    assert syllabus.main() == 0 and "Other.md" in written


def test_a_syllabus_that_is_not_markdown_or_not_there_is_never_written(
    monkeypatch, wired
):
    written = _writable(monkeypatch)
    _argv(monkeypatch, "--no-preview", "--syllabus", "E1282.pdf")
    out = syllabus.main()
    assert out == 1 and out.block and written == {}
    assert [r["code"] for r in out.reasons] == ["NOT_MARKDOWN"]
    written = _writable(monkeypatch, text=None)
    _argv(monkeypatch, "--no-preview")
    out = syllabus.main()
    assert out == 1 and written == {}
    assert out.text == "There is no cm/SYLLABUS.md yet. Write the syllabus first."


def test_a_failed_write_still_shows_the_block(monkeypatch, wired):
    _writable(monkeypatch, ok=False)
    _argv(monkeypatch, "--no-preview")
    out = syllabus.main()
    assert out == 1 and out.block
    assert [r["code"] for r in out.reasons] == ["WRITE_FAILED"]


def test_a_titleless_entry_does_not_blank_a_session_the_site_names(wired, monkeypatch):
    # A "Course opens" entry dated before every lecture is no session of its own.
    sched = syllabus.schedule.load("Semester-f2026")
    sched.releases.append(
        Release(
            "course-intro",
            datetime(2026, 8, 20, 9, 0, tzinfo=BERLIN),
            deploy=[Deploy("cm", "SYLLABUS.md", "materials", None)],
            show_on_site=False,
        )
    )
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: sched)
    out = wired()
    assert "### Session 1: Probability Theory" in out
    assert [line for line in out.splitlines() if line.startswith("### Session")] == [
        "### Session 1: Probability Theory",
        "### Session 2: Random Variables",
    ]


def _with(monkeypatch, *releases, tree=TREE):
    sched = syllabus.schedule.load("Semester-f2026")
    sched.releases.extend(releases)
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: sched)
    monkeypatch.setattr(syllabus, "repo_tree", lambda o, r, b, k: tree)


def test_readings_come_from_any_repo_and_any_folder_of_the_readings_kind(
    monkeypatch, wired
):
    _with(
        monkeypatch,
        Release(
            "papers-2",
            datetime(2026, 9, 5, 9, 0, tzinfo=BERLIN),
            deploy=[Deploy("nlp-literature", "week-2/vaswani.pdf", "readings", None)],
            kind="readings",
        ),
        tree=(*TREE, "week-2/vaswani.pdf"),
    )
    out = wired()
    # Between lecture 1 (1 Sep) and lecture 2 (8 Sep): listed under session 2.
    assert out.index("### Session 2") < out.index("- vaswani.pdf")


def test_readings_after_the_last_lecture_close_the_list(monkeypatch, wired):
    _with(
        monkeypatch,
        Release(
            "further",
            datetime(2026, 12, 1, 9, 0, tzinfo=BERLIN),
            deploy=[Deploy("cm", "literature/week-12", "materials", None)],
        ),
        tree=(*TREE, "literature/week-12/extra.pdf"),
    )
    out = wired()
    assert out.index("### Further readings") < out.index("- extra.pdf")


def test_a_schedule_with_no_sessions_is_an_error_not_an_empty_file(monkeypatch, capsys):
    monkeypatch.setattr(syllabus.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(syllabus, "default_branch", lambda o, r: "main")
    monkeypatch.setattr(syllabus, "repo_tree", lambda o, r, b, k: ())
    monkeypatch.setattr(
        "sys.argv",
        ["x", "--course-org", "C", "--semester-org", "H", "--course-source-repo", "cm"],
    )
    assert syllabus.main() == 1
    assert "names no dated sessions" in capsys.readouterr().err
