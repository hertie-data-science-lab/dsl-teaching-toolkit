"""The per-assignment grading sheet: its shape, its merge, its text, and the arithmetic
derived from it.

The sheet is the ONE place a grader types, and the merge is what makes that safe: the
toolkit re-derives its own `info:` on every tick, and never touches anything else. Most of
what is asserted here is a promise made to a person - your marks survive, your deletions
survive, the maxima beside your blanks are the current ones - so the tests are written as
those promises rather than as a schema check.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from dsl_course.grades import (
    INFO_COMMENT,
    NOTES_KEY,
    SheetSpec,
    SheetUnreadable,
    dump_sheet,
    final_grade,
    merge_sheet,
    new_sheet,
    parse_sheet,
    penalty_rate,
    score_total,
    sheet_path,
)

QUESTIONS = {"Q1": "15", "Q2": "10"}
PERSON_KEYS = ["adjustment_individual", "feedback_individual", NOTES_KEY]


def individual(**kw) -> SheetSpec:
    return SheetSpec(
        slug="assignment-1",
        title="Introduce Yourself",
        is_group=False,
        due_display="Sun 4 Oct 2026 23:59",
        **kw,
    )


def group(**kw) -> SheetSpec:
    return SheetSpec(
        slug="assignment-1",
        title="Neural networks from scratch",
        is_group=True,
        due_display="Sun 4 Oct 2026 23:59",
        cutoff_display="Sun 11 Oct 2026 23:59",
        late_window_days=7,
        late_penalty_per_day="10%",
        **kw,
    )


SOLO = [("ada-l", ["ada-l"]), ("ben-k", ["ben-k"])]
TEAMS = [("team-alpha", ["ada-l", "ben-k"]), ("team-beta", ["chen-w"])]


# ------------------------------------------------------------------ a brand-new sheet


def test_sheet_path_lives_under_grading_sheets():
    assert sheet_path("assignment-1") == "grading_sheets/assignment-1.yml"


def test_a_fresh_individual_sheet_has_a_blank_block_per_student():
    # Every row present from HANDOUT, not from the first mark: a grader can start the
    # moment anything is in, and a student with no block is visibly missing rather than
    # silently ungraded.
    sheet = new_sheet(individual(), SOLO)
    assert list(sheet) == ["submissions"]
    assert list(sheet["submissions"]) == ["ada-l", "ben-k"]
    assert sheet["submissions"]["ada-l"] == {
        "info": {"submitted": None, "days_late": None},
        "score_individual": None,
        "adjustment_individual": None,
        "feedback_individual": None,
        NOTES_KEY: None,
    }


def test_a_fresh_group_sheet_nests_members_inside_each_team():
    sheet = new_sheet(group(), TEAMS)
    assert list(sheet) == ["teams"]
    assert sheet["teams"]["team-alpha"] == {
        "info": {"submitted": None, "days_late": None, "contributions": None},
        "score_group": None,
        "feedback_group": None,
        "members": {
            "ada-l": dict.fromkeys(PERSON_KEYS),
            "ben-k": dict.fromkeys(PERSON_KEYS),
        },
    }


def test_an_externally_submitted_assignment_has_no_info_block_at_all():
    # There is no commit to time, so an `info:` block full of blanks would read as a
    # toolkit that tried to fill one and failed. `adjustment_individual` still exists -
    # the override is the grader's, not the machine's.
    block = new_sheet(individual(submit_external=True), SOLO)["submissions"]["ada-l"]
    assert "info" not in block
    assert list(block) == ["score_individual", *PERSON_KEYS]


def test_declared_questions_become_a_blank_skeleton_and_nothing_else_does():
    assert new_sheet(group(questions=QUESTIONS), TEAMS)["teams"]["team-alpha"][
        "score_group"
    ] == {"Q1": None, "Q2": None}
    assert new_sheet(group(), TEAMS)["teams"]["team-alpha"]["score_group"] is None


def test_autograde_adds_its_info_field_only_when_the_assignment_autogrades():
    assert (
        "autograde"
        in new_sheet(individual(autograde=True), SOLO)["submissions"]["ada-l"]["info"]
    )
    assert (
        "autograde" not in new_sheet(individual(), SOLO)["submissions"]["ada-l"]["info"]
    )


# ------------------------------------------------------------------------- the merge


def _marked(spec: SheetSpec, units) -> dict:
    """A sheet a grader has worked in: marks, multi-line feedback, notes, an override."""
    sheet = new_sheet(spec, units)
    block = sheet[spec.container_key][units[0][0]]
    block[spec.score_key] = {"Q1": 14, "Q2": 6} if spec.questions else 14
    block[spec.feedback_key] = "Clean derivation in Q1.\nQ2 confuses the marginal.\n"
    person = block["members"]["ada-l"] if spec.is_group else block
    person["adjustment_individual"] = -3
    person["feedback_individual"] = (
        "Your section repeats the Q2 error.\nSee the team.\n"
    )
    person[NOTES_KEY] = "extension granted by email"
    return sheet


def test_the_merge_keeps_every_word_the_grader_typed():
    spec = group(questions=QUESTIONS)
    marked = _marked(spec, TEAMS)
    before = dump_sheet(marked, spec, "OPEN")
    merged = merge_sheet(marked, spec, TEAMS, {})
    team = merged["teams"]["team-alpha"]
    assert team["score_group"] == {"Q1": 14, "Q2": 6}
    assert (
        team["feedback_group"] == "Clean derivation in Q1.\nQ2 confuses the marginal.\n"
    )
    assert team["members"]["ada-l"] == {
        "adjustment_individual": -3,
        "feedback_individual": "Your section repeats the Q2 error.\nSee the team.\n",
        NOTES_KEY: "extension granted by email",
    }
    # And the file itself is unchanged, paragraph breaks and all - the write path compares
    # blob shas, so a merge that reflowed feedback would commit a diff on every tick.
    assert dump_sheet(merged, spec, "OPEN") == before


def test_the_merge_re_derives_info_until_the_sheet_is_frozen():
    # A late push moves `submitted` and `days_late` under marks that are already there:
    # `info:` is the toolkit's, and the whole block is replaced with what it knows now.
    spec = group()
    sheet = merge_sheet(
        new_sheet(spec, TEAMS),
        spec,
        TEAMS,
        {"team-alpha": {"submitted": "2026-10-06T09:30+02:00", "days_late": 2}},
    )
    assert sheet["teams"]["team-alpha"]["info"] == {
        "submitted": "2026-10-06T09:30+02:00",
        "days_late": 2,
        "contributions": None,
    }
    # A pass that LOOKED and found nothing pinned does blank it: `{}` for that unit is a
    # derivation, and its answer is "no submission".
    looked = merge_sheet(sheet, spec, TEAMS, {"team-alpha": {}})
    assert looked["teams"]["team-alpha"]["info"]["submitted"] is None


def test_a_write_that_derived_nothing_leaves_the_recorded_facts_alone():
    # The handout is re-fired by the scheduler on EVERY tick (`due_releases` is
    # cumulative), and it derives nothing - it has no pins and no targets. Blanking `info:`
    # on those writes wiped the refresh's work four times an hour, and permanently once the
    # cutoff had passed and nothing re-derived it.
    spec = group()
    sheet = merge_sheet(
        new_sheet(spec, TEAMS),
        spec,
        TEAMS,
        {"team-alpha": {"submitted": "2026-10-06T09:30+02:00", "days_late": 2}},
    )
    again = merge_sheet(sheet, spec, TEAMS, {})
    assert again["teams"]["team-alpha"]["info"] == sheet["teams"]["team-alpha"]["info"]


def test_a_frozen_sheet_keeps_the_info_the_cutoff_recorded():
    # The freeze is the point: after it, the recorded facts stand even when the toolkit is
    # asked again with nothing (or with something different) to say.
    spec = group()
    frozen_sheet = merge_sheet(
        new_sheet(spec, TEAMS), spec, TEAMS, {"team-alpha": {"days_late": 2}}
    )
    after = merge_sheet(
        frozen_sheet, spec, TEAMS, {"team-alpha": {"days_late": 9}}, frozen=True
    )
    assert after["teams"]["team-alpha"]["info"]["days_late"] == 2


def test_the_merge_keeps_an_invented_key_and_a_withdrawn_unit():
    # Two ways a real sheet stops matching the schema: a grader adds a column of their own,
    # and a student withdraws. Neither is ours to delete - the second holds marks.
    spec = individual()
    sheet = new_sheet(spec, SOLO)
    sheet["submissions"]["ada-l"]["moderation"] = "second-marked by TA"
    merged = merge_sheet(sheet, spec, [("ben-k", ["ben-k"])], {})
    assert merged["submissions"]["ada-l"]["moderation"] == "second-marked by TA"
    assert list(merged["submissions"]) == [
        "ben-k",
        "ada-l",
    ]  # units first, then leftovers


def test_the_merge_does_not_re_add_a_human_key_the_grader_deleted():
    # A file that grows a key back on every tick is one nobody can tidy. Only `info:` is
    # re-derived; a deleted human key stays deleted.
    spec = individual()
    sheet = new_sheet(spec, SOLO)
    del sheet["submissions"]["ada-l"][NOTES_KEY]
    assert NOTES_KEY not in merge_sheet(sheet, spec, SOLO, {})["submissions"]["ada-l"]


def test_a_late_onboarder_gets_a_fresh_block_at_the_end():
    spec = individual()
    units = [*SOLO, ("chen-w", ["chen-w"])]
    merged = merge_sheet(new_sheet(spec, SOLO), spec, units, {})
    assert list(merged["submissions"]) == ["ada-l", "ben-k", "chen-w"]
    assert merged["submissions"]["chen-w"]["score_individual"] is None


def test_a_member_who_joins_a_team_later_gets_a_blank_row():
    # The unit is the team, but the marks are per member: a student who joins team-alpha
    # after the sheet was written must still have somewhere to be marked.
    spec = group()
    marked = _marked(spec, TEAMS)
    grown = [("team-alpha", ["ada-l", "ben-k", "chen-w"]), TEAMS[1]]
    members = merge_sheet(marked, spec, grown, {})["teams"]["team-alpha"]["members"]
    assert list(members) == ["ada-l", "ben-k", "chen-w"]
    assert members["ada-l"]["adjustment_individual"] == -3  # untouched
    assert members["chen-w"] == dict.fromkeys(PERSON_KEYS)


def test_a_unit_seen_for_the_first_time_arrives_with_the_facts_already_derived():
    # A late onboarder, or the very first write of a sheet during the late window. Created
    # blank and filled "next tick", their row would sit empty for fifteen minutes with
    # nothing to distinguish it from a non-submission.
    spec = individual()
    merged = merge_sheet(
        None,
        spec,
        SOLO,
        {"ada-l": {"submitted": "2026-10-03T22:14+02:00", "days_late": 0}},
    )
    assert merged["submissions"]["ada-l"]["info"] == {
        "submitted": "2026-10-03T22:14+02:00",
        "days_late": 0,
    }
    assert merged["submissions"]["ben-k"]["info"] == {
        "submitted": None,
        "days_late": None,
    }


def test_merging_into_nothing_is_the_same_as_a_fresh_sheet():
    spec = individual()
    assert merge_sheet(None, spec, SOLO, {}) == new_sheet(spec, SOLO)


# ------------------------------------------------------------------------ the text


def test_the_header_carries_the_status_line_and_what_the_toolkit_fills():
    spec = group(questions=QUESTIONS)
    text = dump_sheet(
        new_sheet(spec, TEAMS), spec, "OPEN - 1 of 2 teams have submitted"
    )
    assert "# Status: OPEN - 1 of 2 teams have submitted" in text
    assert (
        "GRADING SHEET · assignment-1 · Neural networks from scratch · INSTRUCTOR-OWNED"
        in text
    )
    assert "From grading_config.yml / schedule.yml (edit THERE, not here):" in text
    assert "group assignment · 25 points (Q1 15, Q2 10) · autograde off" in text
    assert (
        "due Sun 4 Oct 2026 23:59 · late work to Sun 11 Oct 2026 23:59 at 10%/day"
        in text
    )
    assert (
        "Auto-filled by the toolkit (you never type these): every `info:` block."
        in text
    )


@pytest.mark.parametrize(
    "spec,units,expected",
    [
        (
            group(),
            TEAMS,
            (
                "score_group, feedback_group, adjustment_individual, "
                "feedback_individual, notes_not_shared_with_students."
            ),
        ),
        (
            individual(),
            SOLO,
            (
                "score_individual, adjustment_individual, feedback_individual, "
                "notes_not_shared_with_students."
            ),
        ),
    ],
    ids=["group", "individual"],
)
def test_the_header_names_the_human_keys_of_this_shape(spec, units, expected):
    # The sheet is self-documenting or it is not documented: this line is the only place a
    # grader is told which fields are theirs, and the two shapes do not share a field list.
    text = dump_sheet(new_sheet(spec, units), spec, "OPEN")
    assert f"You fill in: {expected}" in " ".join(
        line.lstrip("# ") for line in text.splitlines() if line.startswith("#")
    )


def test_an_external_sheet_says_the_toolkit_fills_nothing():
    spec = individual(submit_external=True)
    text = dump_sheet(new_sheet(spec, SOLO), spec, "FROZEN 20 Sep 2026 23:59")
    assert "Auto-filled by the toolkit: nothing." in text
    assert "submitted outside GitHub" in text


def test_the_maxima_and_the_info_notice_are_re_emitted_beside_the_fields():
    # Both are comments, not data - so they are re-derived on every write and stay true
    # when grading_config.yml changes under a sheet that is already half marked.
    spec = group(questions=QUESTIONS)
    lines = dump_sheet(new_sheet(spec, TEAMS), spec, "OPEN").splitlines()
    assert any(
        line.strip().startswith("Q1:") and line.endswith("# /15") for line in lines
    )
    assert any(
        line.strip().startswith("Q2:") and line.endswith("# /10") for line in lines
    )
    assert all(
        line.endswith(f"# {INFO_COMMENT}")
        for line in lines
        if line.strip() == "info:" or line.lstrip().startswith("info:")
    )
    assert sum(1 for line in lines if f"# {INFO_COMMENT}" in line) == 2  # one per team


def test_a_score_the_grader_replaced_with_a_scalar_keeps_no_stale_maxima():
    # `questions` are declared, but this team was marked as one overall number. The `# /N`
    # comments belong to the skeleton, not to the key, so none is emitted here.
    spec = group(questions=QUESTIONS)
    sheet = new_sheet(spec, TEAMS)
    sheet["teams"]["team-alpha"]["score_group"] = 21
    text = dump_sheet(sheet, spec, "OPEN")
    assert "score_group: 21" in text
    assert text.count("# /15") == 1  # team-beta's skeleton only


def test_blank_cells_are_written_as_bare_keys_never_as_null():
    # The file is typed into by hand: `null` reads as a value to delete before marking.
    text = dump_sheet(new_sheet(individual(), SOLO), individual(), "OPEN")
    assert "score_individual:\n" in text
    assert "null" not in text


def test_multi_line_feedback_is_written_as_a_literal_block():
    spec = individual()
    sheet = new_sheet(spec, SOLO)
    sheet["submissions"]["ada-l"]["feedback_individual"] = "First line.\nSecond line.\n"
    text = dump_sheet(sheet, spec, "OPEN")
    assert "feedback_individual: |\n      First line.\n      Second line.\n" in text


def test_the_dump_never_uses_flow_style_or_folds_a_long_line():
    spec = group(questions=QUESTIONS)
    sheet = new_sheet(spec, TEAMS)
    sheet["teams"]["team-alpha"]["feedback_group"] = "word " * 60
    text = dump_sheet(sheet, spec, "OPEN")
    assert not [c for c in "{}[]" if c in text]
    assert (
        max(len(line) for line in text.splitlines() if not line.startswith("#")) > 200
    )


@pytest.mark.parametrize(
    "spec,units",
    [(individual(), SOLO), (group(questions=QUESTIONS), TEAMS)],
    ids=["individual", "group"],
)
def test_the_sheet_round_trips_and_is_byte_identical_on_a_second_write(spec, units):
    sheet = _marked(spec, units) if spec.is_group else new_sheet(spec, units)
    text = dump_sheet(sheet, spec, "OPEN - 1 of 2 have submitted")
    assert parse_sheet(text) == sheet
    assert dump_sheet(parse_sheet(text), spec, "OPEN - 1 of 2 have submitted") == text


def test_an_empty_file_parses_as_an_empty_sheet():
    assert parse_sheet("") == {}


@pytest.mark.parametrize(
    "text",
    [
        "submissions:\n  ada-l:\n   score_individual: 3\n  bad: [\n",  # mid-edit
        "- ada-l\n- ben-k\n",  # not a mapping at all
    ],
)
def test_a_sheet_that_does_not_parse_is_refused_never_read_as_empty(text):
    # The one file a grader types into by hand, in a repo they edit in the browser. Read
    # as `{}` it would look like an assignment with no rows, and the next tick would
    # rebuild it blank over their marking - so the parse says so instead.
    with pytest.raises(SheetUnreadable):
        parse_sheet(text)


def test_a_unit_the_grader_turned_into_something_else_is_kept_verbatim():
    # `team-alpha: TODO` used to be an AttributeError out of the quarter-hourly cron.
    spec = group()
    merged = merge_sheet({"teams": {"team-alpha": "TODO"}}, spec, TEAMS, {})
    assert merged["teams"]["team-alpha"] == "TODO"
    assert merged["teams"]["team-beta"]["score_group"] is None


def test_the_merge_keeps_a_key_that_lost_its_container():
    # A block that lost its indentation lands beside `teams:`, not inside it. Dropping it
    # would delete that team's marks on the next tick, silently and for good.
    stray = {"team-gamma": {"score_group": 41}}
    merged = merge_sheet({"teams": {}, **stray}, group(), TEAMS, {})
    assert merged["team-gamma"] == stray["team-gamma"]
    assert next(iter(merged)) == "teams"


def test_a_container_that_is_not_a_mapping_is_refused_rather_than_rebuilt():
    with pytest.raises(SheetUnreadable):
        merge_sheet({"teams": "team-alpha"}, group(), TEAMS, {})


# --------------------------------------------------------------------- the arithmetic


@pytest.mark.parametrize(
    "score,expected",
    [
        (14, Decimal(14)),
        ("14.5", Decimal("14.5")),
        ({"Q1": 14, "Q2": 6}, Decimal(20)),
        ({"Q1": 14, "Q2": None}, Decimal(14)),  # part-marked still totals
        ({"Q1": None, "Q2": ""}, None),  # nothing marked yet
        ({"Q1": 14, "Q2": "see me"}, None),  # one guess spoils the total
        (None, None),
        ("pass", None),
        ("A-", None),
    ],
)
def test_score_total_adds_only_what_it_can_add(score, expected):
    assert score_total(score) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("10%", Decimal("0.10")),
        ("0.1", Decimal("0.10")),
        ("5.5%", Decimal("0.055")),
        ("0%", Decimal(0)),
        ("10", None),  # bare: neither 1000% nor silently 10%
        ("1", None),
        ("ten percent", None),
        ("", None),
        (None, None),
    ],
)
def test_penalty_rate_reads_a_percentage_or_a_fraction_and_refuses_a_bare_number(
    text, expected
):
    assert penalty_rate(text) == expected


def test_final_grade_applies_the_penalty_then_the_adjustment():
    # The order is the whole point: `adjustment_individual` is absolute points applied
    # LAST, so a waived penalty is written as the points it was worth.
    assert final_grade(20, penalty_rate("10%"), 2, None) == Decimal(16)
    assert final_grade(20, penalty_rate("10%"), 2, "+4") == Decimal(20)
    assert final_grade(43, penalty_rate("10%"), 0, -3) == Decimal(40)


def test_final_grade_is_floored_at_zero():
    assert final_grade(20, penalty_rate("10%"), 20, None) == Decimal(0)
    assert final_grade(5, None, None, "-40") == Decimal(0)


def test_final_grade_needs_no_late_policy():
    assert final_grade(37, None, None, None) == Decimal(37)
    assert final_grade(37, penalty_rate("10%"), None, None) == Decimal(37)


@pytest.mark.parametrize("typed", ["nan", "NaN", "Infinity", "-inf"])
def test_a_score_that_is_not_a_FINITE_number_gets_no_arithmetic(typed):
    # `Decimal` accepts these and then RAISES on the comparison that floors the grade, so
    # one of them typed into a score cell took the whole distribution down.
    assert score_total(typed) is None
    assert final_grade(typed, penalty_rate("10%"), 2, None) is None


def test_a_non_numeric_grade_gets_no_arithmetic():
    # `pass`, `A-` and a marker's note are distributed verbatim; None is how the caller is
    # told to pass them through rather than publish a computed number nobody typed.
    assert final_grade("pass", penalty_rate("10%"), 2, None) is None
    assert final_grade(None, None, None, "+4") is None
