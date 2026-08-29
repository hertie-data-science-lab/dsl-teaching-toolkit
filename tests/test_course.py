"""The course domain vocabulary: session-folder discovery, term tags, date windows,
and the group-vs-individual precedence. Pure functions with no I/O, so these
assertions are the whole contract."""

from __future__ import annotations

from dsl_course import course


def test_session_number_extracts_ordinal_prefix():
    assert course.session_number("00_intro") == 0
    assert course.session_number("07_finals-review") == 7
    assert course.session_number("13_other") == 13
    assert course.session_number("3_regression") == 3
    assert course.session_number("no-prefix-here") is None


def test_find_session_dir_plain_and_padded(tmp_path):
    section = tmp_path / "lectures"
    section.mkdir()
    (section / "00_intro").mkdir()
    (section / "03_regression").mkdir()  # zero-padded
    (section / "13_other").mkdir()  # must not match session "3"
    assert course.find_session_dir(section, "3").name == "03_regression"
    assert course.find_session_dir(section, "13").name == "13_other"
    assert course.find_session_dir(section, "9") is None


def test_find_session_dir_missing_section_returns_none(tmp_path):
    assert course.find_session_dir(tmp_path / "does-not-exist", "1") is None


def test_discover_sections_only_counts_dirs_with_ordinal_subdirs(tmp_path):
    (tmp_path / "lectures" / "00_intro").mkdir(parents=True)
    (tmp_path / "labs" / "03_regression").mkdir(parents=True)
    (tmp_path / "readings").mkdir()  # no ordinal subdirs -> not a section
    (tmp_path / "SYLLABUS.md").write_text("x")  # a file, not a dir
    assert course.discover_sections(tmp_path) == ["labs", "lectures"]


def test_discover_sections_missing_root_returns_empty(tmp_path):
    assert course.discover_sections(tmp_path / "nope") == []


def test_active_today_accepts_date_objects_as_bounds():
    # An unquoted `start: 2026-09-01` in people.yml parses to a datetime.date, not a
    # string; `today < start` used to raise TypeError: str < date.
    from datetime import date, datetime

    assert course.active_today(date(2026, 9, 1), None, "2026-10-01") is True
    assert course.active_today(date(2026, 11, 1), None, "2026-10-01") is False
    assert course.active_today(None, date(2026, 9, 30), "2026-10-01") is False
    assert course.active_today(None, date(2026, 12, 31), "2026-10-01") is True
    # a full datetime (date subclass) is sliced back to its date portion
    assert course.active_today(datetime(2026, 9, 1, 12, 0), None, "2026-10-01") is True
    # strings still work exactly as before
    assert course.active_today("2026-09-01", "2026-12-31", "2026-10-01") is True


def test_term_tag_is_case_insensitive_and_lowercased():
    assert course.term_tag("course-materials-F2026") == "f2026"
    assert course.term_tag("Stats-s2030") == "s2030"
    assert course.term_tag("no-tag-here") is None


def test_pages_repo_lowercases_the_org():
    assert course.pages_repo("Hertie-DSL-F2026") == "hertie-dsl-f2026.github.io"


def test_assignment_slug_drops_only_a_trailing_cohort_suffix():
    assert course.assignment_slug("assignment-1-f2026") == "assignment-1"
    assert course.assignment_slug("assignment-1") == "assignment-1"


def test_resolve_is_group_precedence():
    # force wins over everything
    assert (
        course.resolve_is_group(
            force=True, schedule_type="individual", template_group=False
        )
        is True
    )
    # else the cohort's declaration
    assert (
        course.resolve_is_group(
            force=False, schedule_type="group", template_group=False
        )
        is True
    )
    # else the template's design-time type
    assert (
        course.resolve_is_group(force=False, schedule_type=None, template_group=True)
        is True
    )
    # else individual
    assert (
        course.resolve_is_group(force=False, schedule_type=None, template_group=None)
        is False
    )
