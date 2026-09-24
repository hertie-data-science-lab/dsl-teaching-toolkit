"""The course domain vocabulary: session-folder discovery, term tags, date windows,
and the group-vs-individual precedence. Pure functions with no I/O, so these
assertions are the whole contract."""

from __future__ import annotations

from dsl_course import course, discovery


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


def test_is_repo_root_knows_every_whole_repo_spelling():
    # Faculty write all of these for "release everything", and two readers act on the
    # answer: deploy._resolve_within resolves them to the clone root, and
    # schedule.source_faults skips them. Encoded twice, the pair drifted - the validator
    # reported "path does not exist" against a line the release ships in full.
    for spelling in ("", "/", ".", "./", "//", " . "):
        assert course.is_repo_root(spelling), spelling
    for inside in ("labs", "/labs", "labs/", "./labs", "..", ".hidden"):
        assert not course.is_repo_root(inside), inside


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
    assert course.semester_of("course-materials-F2026") == "f2026"
    assert course.semester_of("Stats-s2030") == "s2030"
    assert course.semester_of("no-tag-here") is None


def test_pages_repo_lowercases_the_org():
    assert course.pages_repo("Hertie-DSL-F2026") == "hertie-dsl-f2026.github.io"


def test_assignment_slug_drops_only_a_trailing_semester_suffix():
    assert course.assignment_slug("assignment-1-f2026") == "assignment-1"
    assert course.assignment_slug("assignment-1") == "assignment-1"


def test_resolve_is_group_precedence():
    # force wins over the assignment's own declaration
    assert course.resolve_is_group(force=True, template_type="individual") is True
    # else grading_config.yml's `type:` - the only other rung there is
    assert course.resolve_is_group(force=False, template_type="group") is True
    assert course.resolve_is_group(force=False, template_type="individual") is False
    # else individual
    assert course.resolve_is_group(force=False, template_type=None) is False
    assert course.resolve_is_group(force=False, template_type="") is False


def test_the_shared_drop_box_is_named_off_the_template_and_carries_no_handle():
    # `<slug>-submissions`, never the bare slug (that is the frozen semester TEMPLATE), and
    # never a `<slug>-<handle>`: it is the one submission-repo name a public log may print.
    assert course.shared_repo("assignment-3") == "assignment-3-submissions"
    # And it is a name `classify_repos` reads off the template, which is what earns it the
    # faculty read floor and the public-page exclusion with no rule of its own.
    derived = discovery.classify_repos(
        [
            {"name": "assignment-3", "isTemplate": True},
            {"name": course.shared_repo("assignment-3"), "isTemplate": False},
        ]
    )
    assert derived[course.shared_repo("assignment-3")] == "assignment-3"


def test_the_late_rule_reads_as_one_sentence_for_every_way_it_can_be_declared():
    # What follows "Late work: " on an assignment's page. A window with a rate, a window
    # without one - late but unpenalised, which is a real course policy - and no window at
    # all, which is the strictest of the three and now the one a course has to ask for.
    assert course.late_rule(7, "10%") == "10% per day, up to 7 days"
    assert course.late_rule(7, None) == "accepted up to 7 days late"
    assert course.late_rule(None, "10%") == "not accepted after the deadline"
    assert course.late_rule(0, None) == "not accepted after the deadline"
    # The sentence a semester reads when nobody has declared anything: the toolkit's own
    # default is the Hertie syllabus rule, not silence (`grades.parse_grading_spec`).
    assert (
        course.late_rule(
            course.DEFAULT_LATE_WINDOW_DAYS, course.DEFAULT_LATE_PENALTY_PER_DAY
        )
        == "10% per day, up to 10 days"
    )
    # The rate is quoted as it was TYPED. `grades._penalty` is what decides whether a
    # spelling is usable at all, at the parse, and a second reading of it here would be a
    # second answer to the same question.
    assert course.late_rule(7, "0.1") == "0.1 per day, up to 7 days"


def test_every_shape_that_hands_out_a_repo_owes_a_note():
    # The note answers "who else can read the repo I was just handed?", which is asked of
    # every repo - including the ordinary private one, whose silence read as an oversight
    # rather than as reassurance. The shape that hands out no repo owes none.
    assert course.shape_note("external") == ""
    assert set(course.SHAPE_NOTES) == {
        "assignment-repo-private",
        "assignment-repo-public",
        "assignment-repo-student-choice",
        "shared-dropbox-repo",
    }
    # Every key is a shape the vocabulary actually derives, not a word typed twice.
    derivable = {
        course.submit_shape(via, vis)
        for via in course.SUBMIT_VIA
        for vis in course.VISIBILITIES
    }
    assert set(course.SHAPE_NOTES) <= derivable


def test_every_note_opens_the_same_way_and_says_what_it_is_about():
    # `NB:` on all four: the box is an aside beside the brief, not a step in it, and one
    # note that opened differently would read as an instruction.
    for shape, note in course.SHAPE_NOTES.items():
        assert note.startswith("NB: "), shape
        assert note.endswith("."), shape
        # A plain `>`, never an HTML entity: this text is a YAML scalar and a GitHub repo
        # description as well as page copy, and the one consumer that needs markup escapes
        # it where it renders.
        assert "&gt;" not in note, shape
        # And none of them says what is marked: that is the cutoff sentence's job, and the
        # About line joins the two, so a note that carried it would say it twice.
        assert course.CUTOFF_SENTENCE not in note, shape
