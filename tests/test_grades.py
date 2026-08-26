"""grades pure core -- the CSV -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

import csv
import io

import yaml

from dsl_course import grades, roster


def test_parse_grades_tolerates_blank_and_missing_columns():
    text = (
        "github_handle,team,team_grade,adjustment,final,comments\n"
        "anna-adams,,,,88,Strong work\n"
        "ben-baker, team-x , 85 , +4 , 89 , Good lead \n"
    )
    rows = grades.parse_grades(text)
    assert [r.github_handle for r in rows] == ["anna-adams", "ben-baker"]
    # values are stripped, never coerced
    assert rows[1].team == "team-x" and rows[1].adjustment == "+4"
    assert rows[0].team == "" and rows[0].final == "88"


def test_individual_entry_drops_group_fields():
    row = grades.GradeRow(github_handle="anna", final="88", comments="Nice")
    assert grades.gradebook_entry(row) == {"final": "88", "comments": "Nice"}


def test_auto_and_manual_are_internal_not_in_gradebook():
    # auto/manual are faculty working columns - the student sees only the published final
    row = grades.GradeRow(
        github_handle="anna", auto="70", manual="18", final="88", comments="Nice"
    )
    entry = grades.gradebook_entry(row)
    assert entry == {"final": "88", "comments": "Nice"}
    assert "auto" not in entry and "manual" not in entry


def test_group_entry_keeps_team_grade_private_adjustment_and_shared_comment():
    row = grades.GradeRow(
        github_handle="ben",
        team="team-x",
        team_grade="85",
        adjustment="+4",
        final="89",
        comments="Led the model work",
        team_comments="Strong project; thin evaluation",
    )
    assert grades.gradebook_entry(row) == {
        "team": "team-x",
        "team_grade": "85",
        "adjustment": "+4",
        "team_comments": "Strong project; thin evaluation",
        "final": "89",
        "comments": "Led the model work",
    }


def test_build_gradebooks_pivots_per_student_across_assignments():
    per = {
        "assignment-1": [grades.GradeRow(github_handle="anna", final="88")],
        "assignment-4": [
            grades.GradeRow(
                github_handle="anna",
                team="team-x",
                team_grade="85",
                adjustment="0",
                final="85",
            ),
            grades.GradeRow(
                github_handle="ben",
                team="team-x",
                team_grade="85",
                adjustment="+4",
                final="89",
            ),
        ],
    }
    books = grades.build_gradebooks(per)
    assert set(books) == {"anna", "ben"}
    assert set(books["anna"]["assignments"]) == {"assignment-1", "assignment-4"}
    # one team-mate never sees the other's private adjustment: it lives in their own book
    assert books["ben"]["assignments"]["assignment-4"]["adjustment"] == "+4"
    assert "adjustment" not in books["anna"]["assignments"]["assignment-1"]


def test_build_gradebooks_skips_blank_handles():
    per = {
        "assignment-1": [
            grades.GradeRow(github_handle="", final="50", comments="ghost row")
        ]
    }
    assert grades.build_gradebooks(per) == {}


def test_render_yaml_roundtrips_and_is_student_scoped():
    per = {
        "assignment-1": [
            grades.GradeRow(github_handle="anna", final="88", comments="Nice")
        ]
    }
    book = grades.build_gradebooks(per)["anna"]
    parsed = yaml.safe_load(grades.render_yaml(book))
    assert parsed["student"] == "anna"
    assert parsed["assignments"]["assignment-1"]["final"] == "88"


def test_merge_auto_upserts_without_clobbering_manual():
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="anna", manual="18", comments="Nice")]
    )
    out = grades.merge_auto(
        existing, [("anna", {"auto": "70"}), ("ben", {"auto": "60"})]
    )
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    # the collector's auto score lands without touching the faculty's manual mark/comment
    assert rows["anna"].auto == "70" and rows["anna"].manual == "18"
    assert rows["anna"].comments == "Nice"
    assert rows["ben"].auto == "60"  # a not-yet-listed student is appended


def test_merge_auto_group_sets_team_grade_per_member():
    out = grades.merge_auto(
        "",
        [
            ("anna", {"team": "team-x", "team_grade": "85"}),
            ("ben", {"team": "team-x", "team_grade": "85"}),
        ],
    )
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].team == "team-x" and rows["anna"].team_grade == "85"
    assert rows["ben"].team_grade == "85"


# -------------------------------------------------------- write-once machine columns
# `auto`, `team` and `team_grade` are filled by a machine but OWNED by whoever marks: a
# non-empty cell is never overwritten, so a hand-corrected score survives every re-grade,
# scheduled or manual. Only empty cells get filled.


def test_merge_auto_never_overwrites_an_existing_auto_score():
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="anna", auto="9", comments="regraded by hand")]
    )
    out = grades.merge_auto(existing, [("anna", {"auto": "3"})])
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].auto == "9"  # the hand-edit stands
    assert rows["anna"].comments == "regraded by hand"


def test_merge_auto_never_overwrites_existing_team_columns():
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="anna", team="team-x", team_grade="85")]
    )
    out = grades.merge_auto(
        existing, [("anna", {"team": "team-y", "team_grade": "40"})]
    )
    row = grades.parse_grades(out)[0]
    assert (row.team, row.team_grade) == ("team-x", "85")


def test_merge_auto_fills_only_the_empty_cells_of_a_mixed_row():
    existing = grades.dump_grades(
        [
            grades.GradeRow(github_handle="anna", team="team-x")
        ]  # team set, team_grade empty
    )
    out = grades.merge_auto(
        existing, [("anna", {"team": "team-y", "team_grade": "85"})]
    )
    row = grades.parse_grades(out)[0]
    assert row.team == "team-x"  # preserved
    assert row.team_grade == "85"  # filled


def test_merge_auto_write_once_is_per_row_not_per_file():
    existing = grades.dump_grades(
        [
            grades.GradeRow(github_handle="anna", auto="9"),
            grades.GradeRow(github_handle="ben"),
        ]
    )
    out = grades.merge_auto(existing, [("anna", {"auto": "3"}), ("ben", {"auto": "3"})])
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].auto == "9" and rows["ben"].auto == "3"


def test_merge_auto_logs_how_many_cells_were_preserved(capsys):
    existing = grades.dump_grades(
        [
            grades.GradeRow(
                github_handle="anna", auto="9", team="team-x", team_grade="85"
            )
        ]
    )
    grades.merge_auto(existing, [("anna", {"auto": "3"}), ("ben", {"auto": "3"})])
    out = capsys.readouterr().out
    assert "anna: 1 existing cell(s)" in out  # per-row skip count
    assert "1 existing machine-written cell(s) preserved" in out


def test_merge_auto_says_nothing_when_it_preserved_nothing(capsys):
    grades.merge_auto("", [("anna", {"auto": "3"})])
    assert "preserved" not in capsys.readouterr().out


def test_render_cohort_csv_pivots_to_one_row_per_handle():
    per = {
        "assignment-2": [grades.GradeRow(github_handle="anna", final="90")],
        "assignment-1": [
            grades.GradeRow(github_handle="anna", final="88", comments="Nice"),
            grades.GradeRow(
                github_handle="ben", team="team-x", team_grade="85", final="89"
            ),
        ],
    }
    csv_text = grades.render_cohort_csv(per)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert [r["github_handle"] for r in rows] == ["anna", "ben"]
    # assignment column groups are sorted, so assignment-1 comes before assignment-2
    header = csv_text.splitlines()[0].split(",")
    assert header.index("assignment-1_final") < header.index("assignment-2_final")
    anna = rows[0]
    assert (
        anna["assignment-1_final"] == "88" and anna["assignment-1_comments"] == "Nice"
    )
    assert anna["assignment-2_final"] == "90"
    # anna has no row in assignment-1's team columns
    assert anna["assignment-1_team"] == ""
    ben = rows[1]
    assert (
        ben["assignment-1_team"] == "team-x" and ben["assignment-1_team_grade"] == "85"
    )
    # ben has no assignment-2 row at all - blank, not missing
    assert ben["assignment-2_final"] == ""


def test_gradebook_sync_skips_auditors(monkeypatch, capsys):
    # Auditors are never assessed, so they get no private gradebook repo. Dry-run keeps
    # this pure - the roster is the only input, and nothing is provisioned.
    students = roster.parse(
        "hertie_email,name,github_handle,github_id,enrol_code,role\n"
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n"
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor\n"
        "bob@uni.edu,Bob,bob-b,44,dsl-def,\n"  # blank role -> enrolled
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    assert grades.sync("COHORT", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "grades-ada-l" in out and "grades-bob-b" in out
    assert "eve-e" not in out
    assert "Syncing 2 gradebook repo(s)" in out
    assert "1 auditor row(s) skipped" in out


def test_a_gradebook_the_student_cannot_open_is_a_failure(monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so sync's exit
    # predicate ignored it: a student with no read on their own gradebook, reported green.
    monkeypatch.setattr(grades, "repo_exists", lambda org, repo: True)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: False)
    assert grades.provision_one("COHORT", "ada-l").startswith("failed")


def test_unsent_grade_notifications_are_reported(monkeypatch, capsys):
    # The send count used to be discarded, so a student who never got the "your grades are
    # updated" mail left no trace in the log at all.
    students = roster.parse(
        "hertie_email,name,github_handle,github_id,enrol_code,role\n"
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled\n"
        "bob@uni.edu,Bob,bob-b,43,dsl-def,enrolled\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades.mailer, "send_bulk", lambda msgs, dry_run=False: 1)
    grades._email_updates("COHORT", ["ada-l", "bob-b"])
    assert "1 of 2 grade notification(s) not sent" in capsys.readouterr().err


# ------------------------------------------ render must not clobber a reviewer's edit (fix 16)


def test_human_commit_authors_flags_only_non_bot_commits():
    # `render` refuses to force-overwrite the grades-update branch when it carries a reviewer's
    # own commit; the decision is this pure split of `git log --format=%an base..branch`.
    log = "dsl-bot\nDr Reviewer\ndsl-bot\nDr Reviewer\n"
    assert grades._human_commit_authors(log) == ["Dr Reviewer"]  # de-duplicated, sorted
    assert grades._human_commit_authors("dsl-bot\ndsl-bot\n") == []  # only bot renders
    assert grades._human_commit_authors("") == []  # branch absent / no commits
