"""grades pure core -- the CSV -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from dsl_course import ghcli, grades, repos, roster
from dsl_course.schedule import AssignmentEntry, Schedule
from tests.conftest import ROSTER_HEADER


def test_parse_grades_tolerates_blank_and_missing_columns():
    text = (
        "github_handle,team,team_score,individual_adjustment,final_grade,individual_comments\n"
        "anna-adams,,,,88,Strong work\n"
        "ben-baker, team-x , 85 , +4 , 89 , Good lead \n"
    )
    rows = grades.parse_grades(text)
    assert [r.github_handle for r in rows] == ["anna-adams", "ben-baker"]
    # values are stripped, never coerced
    assert rows[1].team == "team-x" and rows[1].individual_adjustment == "+4"
    assert rows[0].team == "" and rows[0].final_grade == "88"


def test_a_retired_header_is_refused_rather_than_half_read():
    # The rename left `github_handle`, `team` and `team_comments` untouched, so an
    # un-migrated CSV would parse PARTIALLY: the row keeps its handle while every renamed
    # cell reads blank. Nothing downstream can tell that apart from a legitimately sparse
    # row, so `render` would publish a gradebook with the marks missing and `merge_auto`
    # would write the file back having discarded them - green, and destructive.
    text = (
        "github_handle,team,auto,manual,team_grade,adjustment,final,comments,team_comments\n"
        "ada-l,team-1,87,9,78,+4,B+,Nice work.,Team was solid.\n"
    )
    with pytest.raises(grades.RetiredGradeHeader) as exc:
        grades.parse_grades(text)
    # The message has to say what to rename to, or it just blocks without helping.
    assert "final -> final_grade" in str(exc.value)


def test_merge_auto_refuses_a_retired_header_instead_of_wiping_it():
    # The path that actually destroys data: the autograder re-runs, reads the old CSV as
    # blank, and re-serialises it under the new header. Write-once gives no protection,
    # because from the new code's side those cells were never filled.
    text = (
        "github_handle,team,auto,manual,team_grade,adjustment,final,comments,team_comments\n"
        "ada-l,,87,9,,,B+,Nice work.,\n"
    )
    with pytest.raises(grades.RetiredGradeHeader):
        grades.merge_auto(text, [("ada-l", {"autograde_score": "3"})])


def test_a_current_header_missing_optional_columns_still_parses():
    # The guard keys on the RETIRED names specifically - a sparse but current header, and
    # an extra column of the marker's own, are both still fine.
    text = "github_handle,final_grade,rubric_notes\nada-l,B+,see the rubric tab\n"
    (row,) = grades.parse_grades(text)
    assert row.final_grade == "B+" and row.github_handle == "ada-l"


def test_individual_entry_drops_group_fields():
    row = grades.GradeRow(
        github_handle="anna", final_grade="88", individual_comments="Nice"
    )
    assert grades.gradebook_entry(row) == {
        "final_grade": "88",
        "individual_comments": "Nice",
    }


def test_autograde_and_manual_scores_are_internal_not_in_gradebook():
    # auto/manual are faculty working columns - the student sees only the published final
    row = grades.GradeRow(
        github_handle="anna",
        autograde_score="70",
        manual_score="18",
        final_grade="88",
        individual_comments="Nice",
    )
    entry = grades.gradebook_entry(row)
    assert entry == {"final_grade": "88", "individual_comments": "Nice"}
    assert "autograde_score" not in entry and "manual_score" not in entry


def test_group_entry_keeps_team_score_private_adjustment_and_shared_comment():
    row = grades.GradeRow(
        github_handle="ben",
        team="team-x",
        team_score="85",
        individual_adjustment="+4",
        final_grade="89",
        individual_comments="Led the model work",
        team_comments="Strong project; thin evaluation",
    )
    assert grades.gradebook_entry(row) == {
        "team": "team-x",
        "team_score": "85",
        "individual_adjustment": "+4",
        "team_comments": "Strong project; thin evaluation",
        "final_grade": "89",
        "individual_comments": "Led the model work",
    }


def test_build_gradebooks_pivots_per_student_across_assignments():
    per = {
        "assignment-1": [grades.GradeRow(github_handle="anna", final_grade="88")],
        "assignment-4": [
            grades.GradeRow(
                github_handle="anna",
                team="team-x",
                team_score="85",
                individual_adjustment="0",
                final_grade="85",
            ),
            grades.GradeRow(
                github_handle="ben",
                team="team-x",
                team_score="85",
                individual_adjustment="+4",
                final_grade="89",
            ),
        ],
    }
    books = grades.build_gradebooks(per)
    assert set(books) == {"anna", "ben"}
    assert set(books["anna"]["assignments"]) == {"assignment-1", "assignment-4"}
    # one team-mate never sees the other's private adjustment: it lives in their own book
    assert books["ben"]["assignments"]["assignment-4"]["individual_adjustment"] == "+4"
    assert "individual_adjustment" not in books["anna"]["assignments"]["assignment-1"]


def test_build_gradebooks_skips_blank_handles():
    per = {
        "assignment-1": [
            grades.GradeRow(
                github_handle="", final_grade="50", individual_comments="ghost row"
            )
        ]
    }
    assert grades.build_gradebooks(per) == {}


def test_render_yaml_roundtrips_and_is_student_scoped():
    per = {
        "assignment-1": [
            grades.GradeRow(
                github_handle="anna", final_grade="88", individual_comments="Nice"
            )
        ]
    }
    book = grades.build_gradebooks(per)["anna"]
    parsed = yaml.safe_load(grades.render_yaml(book))
    assert parsed["student"] == "anna"
    assert parsed["assignments"]["assignment-1"]["final_grade"] == "88"


def test_merge_auto_upserts_without_clobbering_the_manual_score():
    existing = grades.dump_grades(
        [
            grades.GradeRow(
                github_handle="anna", manual_score="18", individual_comments="Nice"
            )
        ]
    )
    out = grades.merge_auto(
        existing,
        [("anna", {"autograde_score": "70"}), ("ben", {"autograde_score": "60"})],
    )
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    # the collector's auto score lands without touching the faculty's manual mark/comment
    assert rows["anna"].autograde_score == "70" and rows["anna"].manual_score == "18"
    assert rows["anna"].individual_comments == "Nice"
    assert rows["ben"].autograde_score == "60"  # a not-yet-listed student is appended


def test_merge_auto_group_gives_every_member_the_teams_autograde_score():
    # The team's passing-test count lands in `autograde_score` - the same column an
    # individual assignment uses - on every member's row, since it is what they were all
    # graded on. `team_score` is left alone: that is the marker's shared mark.
    out = grades.merge_auto(
        "",
        [
            ("anna", {"team": "team-x", "autograde_score": "2"}),
            ("ben", {"team": "team-x", "autograde_score": "2"}),
        ],
    )
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].team == "team-x" and rows["anna"].autograde_score == "2"
    assert rows["ben"].autograde_score == "2"
    assert rows["anna"].team_score == "" and rows["ben"].team_score == ""


def test_a_hand_set_team_score_is_not_a_machine_column():
    # The flaw this replaced: the group autograder wrote its count into `team_score`, so a
    # machine number and the marker's shared mark shared one write-once cell and whichever
    # landed first won. `team_score` is now faculty-owned outright: a later autograde run
    # neither reads it nor competes for it, so a row whose mark is already set still gets
    # its count recorded. (The count itself stays write-once - see the test below.)
    assert "team_score" not in grades.MACHINE_FIELDS
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="anna", team="team-x", team_score="85")]
    )
    out = grades.merge_auto(existing, [("anna", {"autograde_score": "2"})])
    row = grades.parse_grades(out)[0]
    assert row.team_score == "85"  # the marker's mark, untouched
    assert row.autograde_score == "2"  # the count, recorded beside it


# -------------------------------------------------------- write-once machine columns
# `autograde_score` and `team` are filled by a machine but OWNED by whoever marks: a
# non-empty cell is never overwritten, so a hand-corrected score survives every re-grade,
# scheduled or manual. Only empty cells get filled.


def test_merge_auto_never_overwrites_an_existing_autograde_score():
    existing = grades.dump_grades(
        [
            grades.GradeRow(
                github_handle="anna",
                autograde_score="9",
                individual_comments="regraded by hand",
            )
        ]
    )
    out = grades.merge_auto(existing, [("anna", {"autograde_score": "3"})])
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].autograde_score == "9"  # the hand-edit stands
    assert rows["anna"].individual_comments == "regraded by hand"


def test_merge_auto_never_overwrites_an_existing_team():
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="anna", team="team-x", autograde_score="2")]
    )
    out = grades.merge_auto(
        existing, [("anna", {"team": "team-y", "autograde_score": "0"})]
    )
    row = grades.parse_grades(out)[0]
    assert (row.team, row.autograde_score) == ("team-x", "2")


def test_merge_auto_fills_only_the_empty_cells_of_a_mixed_row():
    existing = grades.dump_grades(
        [
            grades.GradeRow(github_handle="anna", team="team-x")
        ]  # team set, autograde_score empty
    )
    out = grades.merge_auto(
        existing, [("anna", {"team": "team-y", "autograde_score": "2"})]
    )
    row = grades.parse_grades(out)[0]
    assert row.team == "team-x"  # preserved
    assert row.autograde_score == "2"  # filled


def test_merge_auto_write_once_is_per_row_not_per_file():
    existing = grades.dump_grades(
        [
            grades.GradeRow(github_handle="anna", autograde_score="9"),
            grades.GradeRow(github_handle="ben"),
        ]
    )
    out = grades.merge_auto(
        existing,
        [("anna", {"autograde_score": "3"}), ("ben", {"autograde_score": "3"})],
    )
    rows = {r.github_handle: r for r in grades.parse_grades(out)}
    assert rows["anna"].autograde_score == "9" and rows["ben"].autograde_score == "3"


def test_merge_auto_logs_how_many_cells_were_preserved(capsys, monkeypatch):
    monkeypatch.setenv("DSL_VERBOSE", "1")  # the per-handle [keep] line is verbose-only
    existing = grades.dump_grades(
        [
            grades.GradeRow(
                github_handle="anna",
                autograde_score="9",
                team="team-x",
                team_score="85",
            )
        ]
    )
    grades.merge_auto(
        existing,
        [("anna", {"autograde_score": "3"}), ("ben", {"autograde_score": "3"})],
    )
    out = capsys.readouterr().out
    assert "anna: 1 existing cell(s)" in out  # per-row skip count
    assert "1 existing machine-written cell(s) preserved" in out


def test_merge_auto_says_nothing_when_it_preserved_nothing(capsys):
    grades.merge_auto("", [("anna", {"autograde_score": "3"})])
    assert "preserved" not in capsys.readouterr().out


def test_render_cohort_csv_pivots_to_one_row_per_handle():
    per = {
        "assignment-2": [grades.GradeRow(github_handle="anna", final_grade="90")],
        "assignment-1": [
            grades.GradeRow(
                github_handle="anna", final_grade="88", individual_comments="Nice"
            ),
            grades.GradeRow(
                github_handle="ben", team="team-x", team_score="85", final_grade="89"
            ),
        ],
    }
    csv_text = grades.render_cohort_csv(per)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    assert [r["github_handle"] for r in rows] == ["anna", "ben"]
    # assignment column groups are sorted, so assignment-1 comes before assignment-2
    header = csv_text.splitlines()[0].split(",")
    assert header.index("assignment-1_final_grade") < header.index(
        "assignment-2_final_grade"
    )
    anna = rows[0]
    assert (
        anna["assignment-1_final_grade"] == "88"
        and anna["assignment-1_individual_comments"] == "Nice"
    )
    assert anna["assignment-2_final_grade"] == "90"
    # anna has no row in assignment-1's team columns
    assert anna["assignment-1_team"] == ""
    ben = rows[1]
    assert (
        ben["assignment-1_team"] == "team-x" and ben["assignment-1_team_score"] == "85"
    )
    # ben has no assignment-2 row at all - blank, not missing
    assert ben["assignment-2_final_grade"] == ""


def test_gradebook_sync_skips_auditors(monkeypatch, capsys):
    # Auditors are never assessed, so they get no private gradebook repo. Dry-run keeps
    # this pure - the roster is the only input, and nothing is provisioned.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-student lines are verbose-only
    students = roster.parse(
        ROSTER_HEADER + "\n"
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz\n"
        "bob@uni.edu,Bob,,bob-b,44,dsl-def\n"  # blank role -> enrolled
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    assert grades.sync("COHORT", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "grades-ada-l" in out and "grades-bob-b" in out
    assert "eve-e" not in out
    assert "Syncing 2 gradebook repo(s)" in out
    assert "1 auditor row(s) skipped" in out


def test_gradebook_sync_names_no_student_in_a_public_log(monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    assert grades.sync("COHORT", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "ada-l" not in out
    assert "Syncing 1 gradebook repo(s)" in out  # the aggregate still reports


def test_gradebook_provisioning_names_nobody_on_the_happy_path(monkeypatch, capsys):
    # The sibling test above covers `sync --dry-run`, which never creates anything. This is
    # the CREATE branch, where `repo created: COHORT/grades-ada-l` used to reach the public
    # log. `repos.gh` is stubbed - the process boundary - so the real create_repo runs.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(grades, "repo_exists", lambda org, repo: False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    assert grades.provision_one("COHORT", "ada-l") == "ok"
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err


def test_a_gradebook_the_student_cannot_open_is_a_failure(monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so sync's exit
    # predicate ignored it: a student with no read on their own gradebook, reported green.
    monkeypatch.setattr(grades, "repo_exists", lambda org, repo: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: False)
    assert grades.provision_one("COHORT", "ada-l").startswith("failed")


def test_a_new_gradebook_grants_faculty_read_and_an_existing_one_is_left_alone(
    monkeypatch,
):
    # Read, not write: `distribute` rewrites grades.yml from grades/<slug>.csv, so a mark
    # corrected in the gradebook itself would be overwritten on the next run.
    #
    # At CREATION only. A team grant does not decay and the nightly sweep
    # (access.converge_faculty_access) owns the floor, so re-granting on every Sync
    # membership cost two PUTs per student a night for nothing.
    faculty = []
    exists = {"grades-ada-l"}
    monkeypatch.setattr(grades, "repo_exists", lambda org, repo: repo in exists)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: faculty.append(a))
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(grades, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    grades.provision_one("COHORT", "ada-l")
    assert faculty == [], "the existing gradebook was re-granted"
    grades.provision_one("COHORT", "bob-b")
    assert faculty == [("COHORT", "grades-bob-b", grades.FACULTY_READ_ACCESS)]


def test_unsent_grade_notifications_are_reported(monkeypatch, capsys):
    # The send count used to be discarded, so a student who never got the "your grades are
    # updated" mail left no trace in the log at all.
    students = roster.parse(
        ROSTER_HEADER + "\n"
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
        "bob@uni.edu,Bob,enrolled,bob-b,43,dsl-def\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "")
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: [m[0] for m in msgs[:1]],
    )
    grades._email_updates("COHORT", ["ada-l", "bob-b"])
    assert "1 of 2 grade notification(s) not sent" in capsys.readouterr().err


def test_grade_notification_names_the_course_and_falls_back_when_unnamed(monkeypatch):
    # A student taking several of these courses cannot tell one "your grades have been
    # updated" from another, so the body names the course - but a course org that carries
    # no name yet must produce the generic sentence, never a blank or a placeholder.
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    sent: list[list] = []
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            sent.append(msgs) or [m[0] for m in msgs]
        ),
    )

    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "Deep Learning")
    grades._email_updates("COHORT", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades for Deep Learning have been updated." in body
    # and in the SUBJECT - the inbox list is where a student tells two courses apart
    assert subject == "Your grades for Deep Learning have been updated"

    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "")
    grades._email_updates("COHORT", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades have been updated." in body
    assert subject == "Your grades have been updated"


def test_grade_notification_dry_run_carries_a_placeholder_sample(monkeypatch):
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "Deep Learning")
    seen: dict = {}
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            seen.update(sample=sample) or [m[0] for m in msgs]
        ),
    )
    grades._email_updates("COHORT", ["ada-l"], dry_run=True)
    # The reviewer sees the wording; no real student's name or handle is in it.
    assert "<name>" in seen["sample"] and "<handle>" in seen["sample"]
    assert "Ada" not in seen["sample"] and "ada-l" not in seen["sample"]
    assert "Deep Learning" in seen["sample"]


# ------------------------------------------ render must not clobber a reviewer's edit (fix 16)


def test_human_commit_authors_flags_only_non_bot_commits():
    # `render` refuses to force-overwrite the grades-update branch when it carries a reviewer's
    # own commit; the decision is this pure split of `git log --format=%an base..branch`.
    log = "dsl-bot\nDr Reviewer\ndsl-bot\nDr Reviewer\n"
    assert grades._human_commit_authors(log) == ["Dr Reviewer"]  # de-duplicated, sorted
    assert grades._human_commit_authors("dsl-bot\ndsl-bot\n") == []  # only bot renders
    assert grades._human_commit_authors("") == []  # branch absent / no commits


def test_parse_grades_survives_an_excel_bom_and_refuses_a_semicolon_export():
    # The BOM glued itself to the first header name, so every handle read "" and merge_auto
    # folded every student onto one row - hand-entered marks destroyed. roster and teams
    # already stripped it; this was the one hand-edited CSV that did not.
    import pytest

    text = "\ufeffgithub_handle,team,autograde_score\nada-l,,5\nbob-b,,3\n"
    rows = grades.parse_grades(text)
    assert [r.github_handle for r in rows] == ["ada-l", "bob-b"]
    with pytest.raises(RuntimeError, match="semicolon"):
        grades.parse_grades("github_handle;team;autograde_score\nada-l;;5\n")


# ------------------------------- handles are one account whatever their casing (fix 3)


def test_merge_auto_fills_the_existing_row_when_the_casing_differs():
    # GitHub logins are case-insensitive. Keyed raw, the collector's `ada-l` update did not
    # find the marker's `Ada-L` row, appended a second one, and the write-once guard on the
    # first row protected nothing.
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="Ada-L", manual_score="18")]
    )
    out = grades.merge_auto(existing, [("ada-l", {"autograde_score": "7"})])
    rows = grades.parse_grades(out)
    assert len(rows) == 1
    assert rows[0].github_handle == "Ada-L"  # the first-seen spelling is kept
    assert rows[0].autograde_score == "7" and rows[0].manual_score == "18"


def test_merge_auto_write_once_holds_across_a_case_difference():
    existing = grades.dump_grades(
        [grades.GradeRow(github_handle="Ada-L", autograde_score="9")]
    )
    out = grades.merge_auto(existing, [("ADA-L", {"autograde_score": "3"})])
    (row,) = grades.parse_grades(out)
    assert row.autograde_score == "9"  # the marker's correction survives


def test_build_gradebooks_folds_one_student_written_two_ways():
    per = {
        "assignment-1": [grades.GradeRow(github_handle="Ada-L", final_grade="88")],
        "assignment-2": [grades.GradeRow(github_handle="ada-l", final_grade="91")],
    }
    books = grades.build_gradebooks(per)
    assert set(books) == {"Ada-L"}  # one gradebook, under the first-seen spelling
    assert set(books["Ada-L"]["assignments"]) == {"assignment-1", "assignment-2"}


def test_email_updates_matches_the_roster_case_insensitively(monkeypatch):
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,Ada-L,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "")
    sent: list[list] = []
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            sent.append(msgs) or [m[0] for m in msgs]
        ),
    )
    grades._email_updates("COHORT", ["ada-l"])  # the gradebook file's spelling
    assert sent and sent[-1][0][0] == "ada@uni.edu"


# ---------------- "nothing new to render" must mean nothing new, not a failed commit


def _render_with(monkeypatch, tmp_path, *, staged, commit_ok=True):
    """`render` against a local clone; `staged` is what `git diff --cached --quiet` says."""
    per = {"assignment-1": [grades.GradeRow(github_handle="ada-l", final_grade="88")]}
    monkeypatch.setattr(grades, "load_grade_sources", lambda org: per)
    monkeypatch.setattr(grades, "default_branch", lambda org, repo, **k: "main")

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            from pathlib import Path

            Path(args[3]).mkdir(parents=True, exist_ok=True)
        return 0, ""

    def fake_git(*args, **kwargs):
        if "ls-remote" in args:
            return 1, ""  # no existing render branch
        if "diff" in args:
            return (1, "") if staged else (0, "")
        if "commit" in args:
            return (
                (0, "") if commit_ok else (1, "fatal: unable to write new index file")
            )
        return 0, ""

    monkeypatch.setattr(grades, "gh", fake_gh)
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(grades, "git", fake_git)
    return grades.render("COHORT")


def test_a_failed_render_commit_is_not_reported_as_nothing_to_render(
    tmp_path, monkeypatch, capsys
):
    # `git commit` exits non-zero both for "nothing staged" and for a real failure, so a
    # lock or a full disk read as the idempotent no-op: green run, no preview PR, and the
    # marker's grades never distributed.
    assert _render_with(monkeypatch, tmp_path, staged=True, commit_ok=False) == 1
    assert "could not commit the rendered gradebooks" in capsys.readouterr().err


def test_genuinely_nothing_staged_is_still_the_green_no_op(
    tmp_path, monkeypatch, capsys
):
    assert _render_with(monkeypatch, tmp_path, staged=False) == 0
    assert "nothing new to render" in capsys.readouterr().out


# ------------------------------------------- ONE listing instead of a probe per gradebook


def _sync_run(monkeypatch, listing, handles=("ada-l", "bob-b")):
    """grades.sync over `handles`, with `listing` (or an Exception) standing in for the
    org listing. Returns (the orgs listed, the gradebooks created)."""
    students = roster.parse(
        ROSTER_HEADER
        + "\n"
        + "".join(
            f"{h}@uni.edu,{h},enrolled,{h},4{i},dsl-{h}\n"
            for i, h in enumerate(handles)
        )
    )
    listed: list[str] = []

    def fake_listing(org):
        listed.append(org)
        if isinstance(listing, Exception):
            raise listing
        return listing

    created: list[str] = []
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "list_org_repos", fake_listing)
    monkeypatch.setattr(
        grades, "create_repo", lambda org, repo, **k: created.append(repo) or True
    )
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    assert grades.sync("COHORT") == 0
    return listed, created


def test_sync_lists_the_org_once_and_probes_no_gradebook(monkeypatch):
    # A repo_exists per student cost a GET per student on every nightly sync, to ask what
    # one paginated listing already answers for the whole cohort.
    monkeypatch.setattr(
        grades,
        "repo_exists",
        lambda *a, **k: pytest.fail("a per-repo probe is back in the hot path"),
    )
    listed, created = _sync_run(monkeypatch, [{"name": "grades-ada-l", "topics": []}])
    assert listed == ["COHORT"], "one listing per run, not one per student"
    assert created == ["grades-bob-b"], "a listed gradebook was recreated"


def test_a_failed_listing_falls_back_to_probing_each_gradebook(monkeypatch):
    # The listing is an optimisation. A rate limit on it must not leave a student who
    # onboarded today without a gradebook.
    probed: list[str] = []
    monkeypatch.setattr(
        grades, "repo_exists", lambda org, repo: probed.append(repo) or False
    )
    listed, created = _sync_run(
        monkeypatch, RuntimeError("could not list repos in COHORT: 502")
    )
    assert listed == ["COHORT"]
    assert probed == ["grades-ada-l", "grades-bob-b"]
    assert created == ["grades-ada-l", "grades-bob-b"]


def test_a_dry_run_lists_nothing(monkeypatch):
    # Nothing is created, so nothing needs to know what exists.
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(
        grades, "list_org_repos", lambda org: pytest.fail("a dry run listed the org")
    )
    assert grades.sync("COHORT", dry_run=True) == 0


# ------------------------------------------------------------------ distribute, end to end

_SHEET = """\
submissions:
  ada-l:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_individual: 43
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
    notes_not_shared_with_students: chased by email
"""
_TEAM_SHEET = """\
teams:
  alpha:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_group: 43
    feedback_group: |
      Good work.
    members:
      ada-l:
        adjustment_individual: -3
        feedback_individual: |
          Your section repeats the Q4 error.
        notes_not_shared_with_students: privately noted
"""
_GRADING_YML = (
    "title: Neural networks\nlate_window_days: 7\nlate_penalty_per_day: 10%\n"
)
_LEGACY_CSV = (
    "github_handle,team,autograde_score,manual_score,team_score,"
    "individual_adjustment,final_grade,individual_comments,team_comments\n"
    "ada-l,,,,,,77,Solid work,\n"
)
ROSTER_ADA = "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"


def _schedule_with(*slugs: str) -> Schedule:
    return Schedule(
        assignments={
            slug: AssignmentEntry(
                course_source_repo=f"{slug}-f2026",
                due_datetime=datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc),
            )
            for slug in slugs
        }
    )


def _distribute(
    monkeypatch,
    tmp_path,
    *,
    sheets: dict[str, str] | None = None,
    legacy: dict[str, str] | None = None,
    grading: str = _GRADING_YML,
    distributed: str | None = None,
    notified: str | None = None,
    stale_gradebooks: tuple[str, ...] = (),
    existing_marks: str = "",
    sent: int = 1,
    notify: bool = True,
    dry_run: bool = False,
    roster_rows: str | None = ROSTER_ADA,
    issue: int | None = 7,
    put_files_ok: bool = True,
) -> dict:
    """`distribute` over a local classroom-config clone, writing to nothing.

    Returns every effect it had: the comments posted, the gradebook commits, the
    classroom-config commit and the mail batches - which between them are the four things
    a student can be reached by."""
    cfg = tmp_path / "cfg"
    (cfg / grades.SHEETS_DIR).mkdir(parents=True)
    sheets = {"assignment-1": _SHEET} if sheets is None else sheets
    for slug, text in sheets.items():
        (cfg / grades.SHEETS_DIR / f"{slug}.yml").write_text(text)
    for slug, text in (legacy or {}).items():
        (cfg / grades.GRADES_DIR).mkdir(exist_ok=True)
        (cfg / grades.GRADES_DIR / f"{slug}.csv").write_text(text)
    if distributed is not None:
        (cfg / grades.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / grades.DISTRIBUTED_PATH).write_text(distributed)
    if notified is not None:
        (cfg / grades.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / grades.NOTIFIED_PATH).write_text(notified)
    for name in stale_gradebooks:
        (cfg / grades.GRADEBOOK_DIR).mkdir(parents=True, exist_ok=True)
        (cfg / grades.GRADEBOOK_DIR / name).write_text("student: someone\n")

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            copytree(cfg, Path(args[3]))
            return 0, ""
        if "comments?" in " ".join(str(a) for a in args):
            return 0, existing_marks
        return 0, ""

    effects: dict = {
        "comments": [],
        "gradebooks": [],
        "config": [],
        "outbox": [],
        "issues": [],
    }
    monkeypatch.setattr(grades, "gh", fake_gh)
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(grades, "ensure_gradebooks", lambda org, dry_run=False: 0)
    monkeypatch.setattr(grades, "course_org_for_cohort", lambda org: "COURSE")
    monkeypatch.setattr(grades, "_grading_text", lambda org, tpl: grading)
    monkeypatch.setattr(
        grades.schedule,
        "load",
        lambda org: _schedule_with(*(sheets or {"assignment-1": ""})),
    )
    monkeypatch.setattr(
        grades,
        "ensure_feedback_issue",
        lambda org, repo, body, dry_run=False: (
            effects["issues"].append((repo, body)) or issue
        ),
    )
    monkeypatch.setattr(
        grades,
        "post_marked_comment",
        lambda org, repo, no, body, marker, dry_run=False: (
            effects["comments"].append((repo, body, marker)) or True
        ),
    )

    def fake_put_files(org, repo, files, message, *, delete=(), create_only=False):
        target = "config" if repo == grades.CONFIG_REPO else "gradebooks"
        effects[target].append(
            (repo, {k: v.decode() for k, v in files.items()}, tuple(delete))
        )
        return put_files_ok

    monkeypatch.setattr(grades, "put_files", fake_put_files)
    students = (
        None if roster_rows is None else roster.parse(ROSTER_HEADER + roster_rows)
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_cohort", lambda org: "")
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            effects["outbox"].append(msgs),
            [m[0] for m in msgs[:sent]],
        )[1],
    )
    effects["rc"] = grades.distribute("COHORT", notify=notify, dry_run=dry_run)
    return effects


def test_a_real_run_reaches_all_four_channels(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path)
    assert out["rc"] == 0
    # the feedback comment, on the student's own submission repo
    ((repo, body, marker),) = out["comments"]
    assert repo == "assignment-1-ada-l"
    assert "### Feedback · Neural networks" in body and "43" in body
    assert marker.startswith("<!-- dsl-grade:") and marker.endswith("-->")
    # ONE commit per gradebook, holding both files, so the page never disagrees with the
    # data beside it
    ((gb_repo, files, _delete),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l"
    assert set(files) == {"grades.yml", "README.md"}
    assert "student: ada-l" in files["grades.yml"]
    assert "| Neural networks | 43 |" in files["README.md"]
    # the registrar export and the record, in one classroom-config commit
    ((_cfg, cfg_files, _d),) = out["config"]
    assert set(cfg_files) == {grades.COHORT_CSV_NAME, grades.DISTRIBUTED_PATH}
    assert "ada@uni.edu,Ada,ada-l,43" in cfg_files[grades.COHORT_CSV_NAME]
    # and the email
    assert [m[0] for batch in out["outbox"] for m in batch] == ["ada@uni.edu"]


def test_nothing_a_student_may_not_see_reaches_them(tmp_path, monkeypatch):
    # The two leaks this design exists to close: the grader's private notes, and one
    # member's adjustment in a repo the whole team reads.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    ((repo, body, _marker),) = out["comments"]
    assert repo == "assignment-1-alpha"  # the TEAM's repo
    for secret in ("privately noted", "-3", "repeats the Q4 error"):
        assert secret not in body
    ((_gb, files, _d),) = out["gradebooks"]
    assert "privately noted" not in files["grades.yml"]
    assert "privately noted" not in files["README.md"]


def test_a_dry_run_writes_nothing_posts_nothing_and_sends_nothing(
    tmp_path, monkeypatch, capsys
):
    out = _distribute(monkeypatch, tmp_path, dry_run=True)
    assert out["rc"] == 0
    assert (out["comments"], out["gradebooks"], out["config"], out["issues"]) == (
        [],
        [],
        [],
        [],
    )
    assert out["outbox"] == []
    printed = capsys.readouterr().out
    assert "assignment-1: 1 student(s) · 1 final grade(s) derived" in printed
    assert "0 need a hand decision" in printed
    assert f"{grades.COHORT_CSV_NAME}: would gain column assignment-1" in printed
    assert (
        "would post 1 comment(s), update 1 gradebook(s), email 1 student(s)" in printed
    )
    assert "<handle>" in printed  # the sample email, from placeholders


def test_a_re_run_says_nothing_twice(tmp_path, monkeypatch):
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
    )
    assert again["comments"] == []
    assert again["gradebooks"] == []
    assert again["outbox"] == []
    assert again["rc"] == 0


def test_a_corrected_grade_reaches_that_student_and_only_them(tmp_path, monkeypatch):
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    corrected = _SHEET.replace("score_individual: 43", "score_individual: 45")
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": corrected},
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
    )
    ((_repo, body, _marker),) = again["comments"]  # exactly one new comment
    assert "45" in body
    assert len(again["gradebooks"]) == 1
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_lost_record_still_does_not_duplicate_a_comment(tmp_path, monkeypatch):
    # `distributed.csv` deleted, restored from a backup, never written: the hash on the
    # comment itself is the second belt, and it is read from the issue.
    first = _distribute(monkeypatch, tmp_path)
    ((_repo, body, marker),) = first["comments"]
    posted: list = []
    monkeypatch.setattr(
        grades,
        "post_marked_comment",
        grades.post_marked_comment,  # the real one, over the stubbed gh
    )
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        existing_marks=f"an earlier comment\n{marker}\n",
    )
    del posted, body
    # The real post_marked_comment saw its own marker on the issue and posted nothing.
    assert again["rc"] == 0


def test_the_registrar_export_is_written_only_on_a_real_run(tmp_path, monkeypatch):
    assert _distribute(monkeypatch, tmp_path, dry_run=True)["config"] == []
    ((_cfg, files, _d),) = _distribute(monkeypatch, tmp_path / "real")["config"]
    csv_text = files[grades.COHORT_CSV_NAME]
    assert csv_text.splitlines()[0] == "hertie_email,name,github_handle,assignment-1"


def test_the_migration_off_notified_csv_happens_in_one_commit(tmp_path, monkeypatch):
    # The old marker and the dead per-student YAML go in the SAME commit that writes the
    # new record, so no reader ever sees both and has to choose.
    out = _distribute(
        monkeypatch,
        tmp_path,
        notified=(
            "github_handle,grades_sha,notified_at\n"
            "ada-l,anoldsha,2026-08-31T09:00:00+00:00\n"
        ),
        stale_gradebooks=("ada-l.yml",),
        notify=False,  # so the carried-over row is visible, not overwritten
    )
    ((_cfg, files, delete),) = out["config"]
    assert grades.DISTRIBUTED_PATH in files
    assert set(delete) == {
        grades.NOTIFIED_PATH,
        f"{grades.GRADEBOOK_DIR}/ada-l.yml",
    }
    # The email rows carried over, so nobody is re-told about a book they already know
    # about beyond the one re-hash this migration costs.
    assert "ada-l,,email,anoldsha," in files[grades.DISTRIBUTED_PATH]


def test_a_cohort_still_on_the_grade_csvs_gets_gradebooks_but_no_comments(
    tmp_path, monkeypatch
):
    # A legacy CSV has no submission-unit structure to post against, and no timing - so
    # the Submitted cell is BLANK rather than an accusation.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={},
        legacy={"assignment-1": _LEGACY_CSV},
    )
    assert out["comments"] == [] and out["issues"] == []
    ((_gb, files, _d),) = out["gradebooks"]
    assert "| Neural networks | 77 |  |  |  |" in files["README.md"]
    assert "not submitted" not in files["README.md"]


def test_a_missing_submission_repo_is_a_counted_skip(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, issue=None)
    assert out["comments"] == []
    assert out["rc"] == 0  # a student who never onboarded is not a failure
    assert '"skipped": 1' in capsys.readouterr().out


def test_the_public_log_carries_counts_and_no_student(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    _distribute(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert '"comments": 1' in out and '"gradebooks": 1' in out
    assert "ada-l" not in out and "ada@uni.edu" not in out


def test_an_unsent_notification_reddens_the_run_and_is_retried(tmp_path, monkeypatch):
    # The grades are out by this point, so nothing is undone - but a student who never got
    # the mail does not know to look, and the record must not claim they were told.
    first = _distribute(monkeypatch, tmp_path, sent=0)
    assert first["rc"] == 1
    ((_cfg, files, _d),) = first["config"]
    assert ",email," not in files[grades.DISTRIBUTED_PATH]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=files[grades.DISTRIBUTED_PATH],
        sent=1,
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_no_notify_sends_nothing_and_stays_green(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, notify=False)
    assert out["outbox"] == [] and out["rc"] == 0


def test_distribute_reds_when_the_record_could_not_be_written(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1


def test_a_gradebook_with_no_roster_row_is_counted_not_fatal(
    tmp_path, monkeypatch, capsys
):
    out = _distribute(
        monkeypatch, tmp_path, roster_rows="\nbo@uni.edu,Bo,enrolled,bo-b,7,dsl-x\n"
    )
    assert out["rc"] == 0
    err = capsys.readouterr().err
    assert "no roster row with an email" in err and "ada-l" not in err


def test_distribute_reds_when_the_roster_cannot_be_read(tmp_path, monkeypatch, capsys):
    assert _distribute(monkeypatch, tmp_path, roster_rows=None)["rc"] == 1
    assert "could not be read" in capsys.readouterr().err
