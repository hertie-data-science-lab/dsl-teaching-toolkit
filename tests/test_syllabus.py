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


BROKEN = {
    "start alone": f"# Syl\n{PLAN_START}\nImportant prose\n",
    "end alone": f"# Syl\nImportant prose\n{PLAN_END}\n",
    "out of order": f"# Syl\n{PLAN_END}\nImportant prose\n{PLAN_START}\n",
    "two starts": f"{PLAN_START}\nx\n{PLAN_START}\nImportant prose\n{PLAN_END}\n",
    "two ends": f"{PLAN_START}\nx\n{PLAN_END}\nImportant prose\n{PLAN_END}\n",
}


@pytest.mark.parametrize("name", BROKEN)
def test_place_refuses_markers_in_any_state_but_one_start_then_one_end(name):
    # The data loss this pins: a half-marked file used to get a block appended, and the
    # next write treated the stray marker as the block's edge and deleted the prose.
    assert syllabus.place(BROKEN[name], BLOCK) is None


def test_a_marker_inside_a_sentence_is_prose():
    text = f"# Syl\n\nWe keep the plan between {PLAN_START} and {PLAN_END} lines.\n"
    once = syllabus.place(text, BLOCK)
    assert once.startswith(text.rstrip("\n"))
    assert once.endswith(f"## Weekly plan\n\n{PLAN_START}\n{BLOCK}{PLAN_END}\n")
    # Written again: the block is found, the sentence untouched.
    assert syllabus.place(once, BLOCK) == once
    # Indented markers on their own lines are markers.
    spaced = f"# Syl\n  {PLAN_START}  \nold\n\t{PLAN_END}\nEnd.\n"
    assert (
        syllabus.place(spaced, BLOCK)
        == f"# Syl\n  {PLAN_START}\n{BLOCK}\t{PLAN_END}\nEnd.\n"
    )


def test_a_marker_line_in_the_plan_does_not_break_the_next_write():
    # The U5 repro: a reading list holding a marker line made the second Write
    # MARKERS_BROKEN (two ends), or cut the block short at the plan's own end marker.
    body = f"### Session 1\n\n{PLAN_END}\nRead ch. 1.\n  {PLAN_START}\n"
    once = syllabus.place("# Syl\n", body)
    assert syllabus.place(once, body) == once
    assert once.count(f"\n{PLAN_END}\n") == 1 and "Read ch. 1." in once
    # Still an HTML comment, so the rendered page reads the same.
    assert (
        "<!--  /dsl:weekly-plan -->" in once and "  <!--  dsl:weekly-plan -->" in once
    )
    replaced = syllabus.place(once, BLOCK)
    assert replaced == f"# Syl\n\n## Weekly plan\n\n{PLAN_START}\n{BLOCK}{PLAN_END}\n"


def test_a_fence_the_plan_leaves_open_is_closed_inside_the_block():
    # Left open, it would swallow the end marker on the next write.
    once = syllabus.place("# Syl\n", "### Session 1\n\n~~~~\ncode\n")
    assert once.endswith(f"~~~~\ncode\n~~~~\n{PLAN_END}\n")
    assert syllabus.place(once, BLOCK).endswith(f"{PLAN_START}\n{BLOCK}{PLAN_END}\n")


EXAMPLE = f"Paste this:\n\n```markdown\n{PLAN_START}\nyour plan\n{PLAN_END}\n```\n"


def test_markers_shown_in_a_fenced_example_are_not_the_block():
    once = syllabus.place(f"# Syl\n\n{EXAMPLE}", BLOCK)
    assert once.startswith(f"# Syl\n\n{EXAMPLE}\n## Weekly plan\n\n{PLAN_START}\n")
    assert syllabus.place(once, BLOCK) == once
    # A real block before the example is found; the example stays as written.
    text = f"{PLAN_START}\nold\n{PLAN_END}\n\n~~~\n{PLAN_END}\n```\n~~~\n"
    assert syllabus.place(text, BLOCK) == text.replace("old\n", BLOCK)
    # A lone marker in an example does not make the real pair broken.
    assert syllabus.place(f"```\n{PLAN_END}\n```\n{text}", BLOCK) is not None


def test_markers_inside_a_list_item_keep_their_indentation():
    text = f"- Sessions:\n\n  {PLAN_START}\n  old\n  {PLAN_END}\n- Grading\n"
    out = syllabus.place(text, BLOCK)
    assert out == f"- Sessions:\n\n  {PLAN_START}\n{BLOCK}  {PLAN_END}\n- Grading\n"
    assert syllabus.place(out, BLOCK) == out


def test_an_end_marker_on_the_last_line_stays_the_last_line():
    assert syllabus.place(f"{PLAN_START}\nold\n{PLAN_END}", BLOCK) == (
        f"{PLAN_START}\n{BLOCK}{PLAN_END}"
    )


def test_place_keeps_the_files_own_line_endings():
    text = f"# Syl\r\n\r\n{PLAN_START}\r\nold\r\n{PLAN_END}\r\nEnd.\r\n"
    out = syllabus.place(text, "### Session 1\n\nNew.\n")
    assert (
        out
        == f"# Syl\r\n\r\n{PLAN_START}\r\n### Session 1\r\n\r\nNew.\r\n{PLAN_END}\r\nEnd.\r\n"
    )
    assert "\n" not in out.replace("\r\n", "")
    appended = syllabus.place("# Syl\r\nText.\r\n", BLOCK)
    assert appended == (
        f"# Syl\r\nText.\r\n\r\n## Weekly plan\r\n\r\n{PLAN_START}\r\n"
        f"### Session 1\r\n\r\nNew.\r\n{PLAN_END}\r\n"
    )


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


@pytest.mark.parametrize("name", BROKEN)
def test_a_write_over_broken_markers_is_refused_and_writes_nothing(
    monkeypatch, wired, name
):
    # The second Write of the reported repro, and every other broken state.
    written = _writable(monkeypatch, text=BROKEN[name])
    _argv(monkeypatch, "--no-preview")
    out = syllabus.main()
    assert out == 1 and written == {} and out.block
    assert [r["code"] for r in out.reasons] == ["MARKERS_BROKEN"]
    assert out.text.startswith(
        "cm/SYLLABUS.md has the weekly-plan markers out of place"
    )


def test_two_writes_keep_the_faculty_text(monkeypatch, wired):
    text = "# Syl\n\nImportant prose.\n"
    for _ in range(2):
        written = _writable(monkeypatch, text=text)
        _argv(monkeypatch, "--no-preview")
        assert syllabus.main() == 0
        text = written["SYLLABUS.md"]
    assert text.startswith("# Syl\n\nImportant prose.\n\n## Weekly plan\n")
    assert text.count(PLAN_START) == text.count(PLAN_END) == 1


def test_a_syllabus_too_large_or_not_utf8_is_refused(monkeypatch, wired):
    # Over 1 MB the Contents API sends no content: "" with a real sha is not empty.
    written = _writable(monkeypatch, text="")
    _argv(monkeypatch, "--no-preview")
    out = syllabus.main()
    assert out == 1 and written == {}
    assert [r["code"] for r in out.reasons] == ["TOO_LARGE"]
    # A truly empty file is written into.
    monkeypatch.setattr(
        syllabus, "get_file_with_sha", lambda o, r, p: ("", syllabus.blob_sha(b""))
    )
    assert syllabus.main() == 0 and written["SYLLABUS.md"].startswith("## Weekly plan")

    def latin1(o, r, p):
        raise UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid continuation byte")

    monkeypatch.setattr(syllabus, "get_file_with_sha", latin1)
    out = syllabus.main()
    assert out == 1
    assert [r["code"] for r in out.reasons] == ["NOT_UTF8"]


def test_a_broken_materials_yml_is_a_refusal_not_a_traceback(monkeypatch, wired):
    def unusable(o, r):
        raise syllabus.Unusable("cm/materials.yml is not valid YAML")

    monkeypatch.setattr(syllabus, "read_materials", unusable)
    written = _writable(monkeypatch)
    for extra in ((), ("--no-preview",)):
        _argv(monkeypatch, *extra)
        out = syllabus.main()
        assert out == 1 and written == {}
        assert [r["code"] for r in out.reasons] == ["MATERIALS_UNUSABLE"]
        assert (
            out.text == "cm/materials.yml is not valid YAML. Fix it and run this again."
        )


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
