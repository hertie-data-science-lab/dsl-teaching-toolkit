"""readings: the shared rule for a session's reading folder.

The heading shift is what nests a faculty-written list under the heading the page or the
syllabus puts above it, so a file in the Hertie syllabus shape does not outrank its own
session.
"""

from __future__ import annotations

from dsl_course import readings


def test_demote_headings_nests_a_reading_list_under_the_session_heading():
    # A reading file written in the Hertie syllabus shape: `# Session N readings`, then
    # `## Required Readings` / `## Optional Readings`. At their written levels those
    # outrank the page's own <h2>Session N, which is backwards.
    src = "# Session 1 readings\n\n## Required Readings\n\n- Gill (2015)\n\n## Optional Readings\n"
    out = readings.demote_headings(src)
    assert "### Session 1 readings" in out
    assert "#### Required Readings" in out and "#### Optional Readings" in out


def test_demote_headings_leaves_code_and_prose_alone():
    # A `#` comment inside a fence is not a heading, and deepening it would rewrite the
    # example the faculty member wrote.
    src = "# Real heading\n\n```bash\n# install first\ncd x\n```\n\n#hashtag not a heading\n"
    out = readings.demote_headings(src)
    assert "### Real heading" in out
    assert "\n# install first\n" in out
    assert "#hashtag not a heading" in out


def test_demote_headings_clamps_at_six():
    assert readings.demote_headings("##### deep").startswith("###### deep")
    assert readings.demote_headings("###### deepest").startswith("###### deepest")
