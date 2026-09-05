"""grades pure core -- the CSV -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import pytest

from dsl_course import gh_contents, ghcli, grades, repos, roster
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
    # row, so a transition cohort would distribute gradebooks with every mark silently
    # missing - green, and unnoticed until a student asked where their grade went.
    text = (
        "github_handle,team,auto,manual,team_grade,adjustment,final,comments,team_comments\n"
        "ada-l,team-1,87,9,78,+4,B+,Nice work.,Team was solid.\n"
    )
    with pytest.raises(grades.RetiredGradeHeader) as exc:
        grades.parse_grades(text)
    # The message has to say what to rename to, or it just blocks without helping.
    assert "final -> final_grade" in str(exc.value)


def test_a_current_header_missing_optional_columns_still_parses():
    # The guard keys on the RETIRED names specifically - a sparse but current header, and
    # an extra column of the marker's own, are both still fine.
    text = "github_handle,final_grade,rubric_notes\nada-l,B+,see the rubric tab\n"
    (row,) = grades.parse_grades(text)
    assert row.final_grade == "B+" and row.github_handle == "ada-l"


# -------------------------------------------------------- write-once machine columns
# `autograde_score` and `team` are filled by a machine but OWNED by whoever marks: a
# non-empty cell is never overwritten, so a hand-corrected score survives every re-grade,
# scheduled or manual. Only empty cells get filled.


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


@pytest.mark.parametrize(
    "break_it",
    [
        "tree",  # the repo could not be read before writing
        "build",  # POST /git/trees
        "commit",  # POST /git/commits
        "ref",  # PATCH the branch
    ],
)
def test_no_failure_branch_of_a_gradebook_write_names_the_student(
    break_it, monkeypatch, capsys
):
    # The green path was covered; the failure branches were not, and they are where the
    # repo name lives: `could not commit to COHORT/grades-ada-l` on a bad day publishes the
    # roster one student at a time, from a run in a PUBLIC .github repo.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    if break_it == "tree":
        monkeypatch.setattr(
            gh_contents,
            "default_branch",
            lambda org, repo, **k: (_ for _ in ()).throw(
                RuntimeError(f"could not read {org}/{repo}'s default branch: 500")
            ),
        )
    else:
        monkeypatch.setattr(
            gh_contents, "default_branch", lambda org, repo, **k: "main"
        )
        monkeypatch.setattr(gh_contents, "repo_blob_shas", lambda o, r, b: {})
        monkeypatch.setattr(gh_contents, "_head", lambda o, r, b: ("parent", "tree"))
        answers = {"trees": break_it != "build", "commits": break_it != "commit"}
        monkeypatch.setattr(
            gh_contents,
            "gh",
            lambda *a, **k: (
                (0, "sha") if answers.get(_endpoint(a), False) else (1, "boom")
            ),
        )
    assert not gh_contents.put_files(
        "COHORT", "grades-ada-l", {"grades.yml": b"x\n"}, "grades: update", person=True
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err
    assert captured.err.strip(), "the fault itself still has to be reported"


def _endpoint(args) -> str:
    """Which git-data call a stubbed `gh` was asked for."""
    joined = " ".join(str(a) for a in args)
    for name in ("trees", "commits", "refs"):
        if f"/git/{name}" in joined:
            return name
    return ""


def test_a_failed_label_or_collaborator_grant_names_nobody_publicly(
    monkeypatch, capsys
):
    # Both are called once per SUBMISSION repo now (the Feedback issue's label, and the
    # student's own grant), so both name a `<slug>-<handle>` repo on failure.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.ensure_label(
        "COHORT",
        "assignment-1-ada-l",
        "dsl-feedback",
        color="ededed",
        description="d",
        person=True,
    )
    assert not repos.add_collaborator(
        "COHORT", "assignment-1-ada-l", "ada-l", person=True
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err
    assert captured.err.count("COHORT") == 2  # the fault, and where to look


def test_the_verbose_log_still_says_which_repo_it_was(monkeypatch, capsys):
    # The name is not thrown away, it is moved: a maintainer running the CLI locally with
    # DSL_VERBOSE=1 still gets the repo, and so does the private classroom-config archive.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.add_collaborator(
        "COHORT", "assignment-1-ada-l", "ada-l", person=True
    )
    assert "assignment-1-ada-l" in capsys.readouterr().out


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
# `pass`, two days late: a score no penalty can be applied to, under one that would have
# applied to a number.
_HELD_SHEET = _SHEET.replace("score_individual: 43", "score_individual: pass").replace(
    "days_late: 0", "days_late: 2"
)
_HELD_TEAM_SHEET = _TEAM_SHEET.replace("score_group: 43", "score_group: pass").replace(
    "days_late: 0", "days_late: 2"
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
    course_name=lambda org: "",
    assignment: str = "",
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
        # A callable lets a test answer per write - which is what a lost ref race is.
        return put_files_ok(files) if callable(put_files_ok) else put_files_ok

    monkeypatch.setattr(grades, "put_files", fake_put_files)
    students = (
        None if roster_rows is None else roster.parse(ROSTER_HEADER + roster_rows)
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_cohort", course_name)
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            effects["outbox"].append(msgs),
            [m[0] for m in msgs[:sent]],
        )[1],
    )
    effects["rc"] = grades.distribute(
        "COHORT", notify=notify, dry_run=dry_run, assignment=assignment
    )
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


def test_a_score_no_penalty_fits_is_held_rather_than_sent(
    tmp_path, monkeypatch, capsys
):
    # "penalty -20% · Final grade: pass" is not a grade, it is a contradiction a student
    # would read before anyone noticed. Nothing goes out for it until a person settles it.
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _HELD_SHEET})
    assert out["rc"] == 0
    assert out["comments"] == []
    assert out["gradebooks"] == []
    assert out["outbox"] == []
    printed = capsys.readouterr().out
    assert '"held": 1' in printed
    assert "ada-l" not in printed  # the public log counts, it never names


def test_a_typo_in_one_question_cell_is_held_not_silently_dropped(
    tmp_path, monkeypatch, capsys
):
    # `Q1: 14/15` used to total to nothing: the student's grade vanished and their
    # feedback was posted anyway, with the dry run counting the row as unmarked.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 14/15\n      Q2: 10"
    )
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet})
    assert out["rc"] == 0
    assert (out["comments"], out["gradebooks"], out["outbox"]) == ([], [], [])
    printed = capsys.readouterr().out
    assert '"held": 1' in printed
    assert "ada-l" not in printed


def test_a_non_numeric_adjustment_is_held_not_read_as_zero(tmp_path, monkeypatch):
    # `−3` is what a word processor produces. It was read as no adjustment at all, so the
    # grader believed a penalty had been waived and the student was penalised anyway.
    sheet = _SHEET.replace("adjustment_individual:", "adjustment_individual: \u22123")
    out = _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet})
    assert (out["comments"], out["gradebooks"]) == ([], [])


def test_a_question_the_assignment_does_not_declare_is_held_and_never_summed(
    tmp_path, monkeypatch
):
    # A stray `Q5: 10` made 53 out of a 50-point assignment, with no maximum beside it.
    sheet = _SHEET.replace(
        "score_individual: 43",
        "score_individual:\n      Q1: 15\n      Q2: 10\n      Q5: 10",
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, grading=grading
    )
    assert (out["comments"], out["gradebooks"]) == ([], [])
    assert grades.score_total(
        {"Q1": "15", "Q2": "10", "Q5": "10"}, {"Q1": "15", "Q2": "10"}
    ) == Decimal(25)


def test_a_handle_in_two_teams_is_held_rather_than_taking_the_last_one(
    tmp_path, monkeypatch
):
    # The later team's view silently won the gradebook. Which team a student is in is not
    # something to guess at.
    sheet = _TEAM_SHEET + (
        "  beta:\n"
        "    score_group: 20\n"
        "    feedback_group: |\n      Thin.\n"
        "    members:\n"
        "      ada-l:\n"
        "        adjustment_individual:\n"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["gradebooks"] == []


def test_distribute_can_be_narrowed_to_one_assignment(tmp_path, monkeypatch):
    # a1's marks are ready while a2 is half typed in; the whole-repo run shipped both.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _SHEET},
        assignment="assignment-1",
    )
    assert out["rc"] == 0
    assert [repo for repo, _b, _m in out["comments"]] == ["assignment-1-ada-l"]
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "assignment-1" in files["grades.yml"]
    assert "assignment-2" not in files["grades.yml"]


def test_a_slug_no_sheet_matches_distributes_nothing(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-9")
    assert out["rc"] == 1
    assert (out["comments"], out["gradebooks"], out["config"]) == ([], [], [])
    assert "no grading sheet or grade CSV for `assignment-9`" in capsys.readouterr().err


def test_the_dry_run_counts_units_with_questions_still_unmarked(
    tmp_path, monkeypatch, capsys
):
    # A partly filled map totals to what has been typed, and a real run sends it - which is
    # the grader's call to make, so the count is what they are given to make it with.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=grading,
        dry_run=True,
    )
    assert "1 unit(s) have unmarked questions" in capsys.readouterr().out


def test_a_half_marked_map_is_still_sent_on_a_real_run(tmp_path, monkeypatch):
    # Not held: a grader releasing Q1 early is a decision, not a typo.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, grading=grading
    )
    ((_repo, body, _marker),) = out["comments"]
    assert "15" in body


def test_the_dry_run_says_what_each_hold_is(tmp_path, monkeypatch, capsys):
    # "1 held" tells a grader to go looking without saying what for, and every one of
    # these is something they typed and can fix in a minute.
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 14/15\n      Q2: 10"
    )
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": sheet}, dry_run=True)
    printed = capsys.readouterr().out
    assert "1 held for a hand decision" in printed
    assert "1 with a non-numeric value in a per-question map" in printed


def test_a_held_team_score_holds_the_teams_comment_too(tmp_path, monkeypatch):
    # A team's comment is built from the team block rather than from a member's view, so
    # taking the views out of the books is not on its own enough to keep it back.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _HELD_TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["rc"] == 0
    assert out["comments"] == []
    assert out["gradebooks"] == []


def test_holding_one_mark_leaves_the_students_other_marks_alone(tmp_path, monkeypatch):
    # The hold is per assignment: everything else a grader has settled still goes out.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _HELD_SHEET},
    )
    assert out["rc"] == 0
    assert [repo for repo, _body, _marker in out["comments"]] == ["assignment-1-ada-l"]
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "assignment-1" in files["grades.yml"]
    assert "assignment-2" not in files["grades.yml"]


def test_the_dry_run_sample_email_is_the_one_that_would_be_sent(
    tmp_path, monkeypatch, capsys
):
    # The subject is the half a student reads first, and the course name is what tells one
    # of these apart from another - so a preview that showed neither was reviewing text
    # nobody would ever receive.
    _distribute(
        monkeypatch,
        tmp_path,
        dry_run=True,
        course_name=lambda org: "Deep Learning",
    )
    printed = capsys.readouterr().out
    assert "    Subject: Your grades for Deep Learning have been updated" in printed
    assert "Your grades for Deep Learning have been updated. View them" in printed
    assert "grades-<handle>" in printed  # a placeholder, never a student


def test_an_unreadable_course_name_still_previews_the_email(
    tmp_path, monkeypatch, capsys
):
    # Same fallback the send has: the name is a nicety, the notification is not.
    def boom(org):
        raise RuntimeError("no dsl-course.yml")

    out = _distribute(monkeypatch, tmp_path, dry_run=True, course_name=boom)
    assert out["rc"] == 0
    assert "    Subject: Your grades have been updated" in capsys.readouterr().out


def test_the_dry_run_counts_what_it_would_hold(tmp_path, monkeypatch, capsys):
    _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": _HELD_SHEET}, dry_run=True
    )
    printed = capsys.readouterr().out
    assert "assignment-1: 1 student(s) · 0 final grade(s) derived" in printed
    assert "1 held for a hand decision" in printed
    assert (
        "would post 0 comment(s), update 0 gradebook(s), email 0 student(s)" in printed
    )


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
    assert "0 held for a hand decision" in printed
    assert f"{grades.COHORT_CSV_NAME}: would gain column assignment-1" in printed
    assert (
        "would post 1 comment(s), update 1 gradebook(s), email 1 student(s)" in printed
    )
    assert "<handle>" in printed  # the sample email, from placeholders


def test_a_team_issue_distribute_has_to_open_still_names_the_team(
    tmp_path, monkeypatch
):
    # Distribute is the last opener of a Feedback issue, and it knows the unit and its
    # members; it used to pass neither, so a team reached this way was never told which
    # team the repo belonged to.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    ((repo, body),) = out["issues"]
    assert repo == "assignment-1-alpha"
    assert (
        "**Team:** alpha (@ada-l) - fill in CONTRIBUTIONS.md before the deadline."
        in body.splitlines()
    )


def test_a_sheet_that_does_not_parse_sends_nothing_at_all(tmp_path, monkeypatch):
    # Not "everything except that one": a partial distribution has to be reconciled
    # student by student, where a refused one is fixed in the file and pressed again.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": "submissions:\n  bad: [\n"},
    )
    assert out["rc"] == 1
    assert out["comments"] == [] and out["gradebooks"] == []
    assert out["config"] == [] and out["outbox"] == []


def test_the_record_survives_one_lost_race_with_the_cron(tmp_path, monkeypatch, capsys):
    # The quarter-hourly scheduler commits into the same repo all through the term, and the
    # ref update is not forced. Losing that race cost the RECORD, not a file: comments and
    # gradebooks are guarded by their own content, the emails only by `distributed.csv`, so
    # the next run mailed the whole cohort again.
    tries = {"n": 0}

    def refuse_the_first_record(files):
        if grades.DISTRIBUTED_PATH not in files:
            return True  # the gradebook writes are not in the race
        tries["n"] += 1
        return tries["n"] > 1

    out = _distribute(monkeypatch, tmp_path, put_files_ok=refuse_the_first_record)
    assert tries["n"] == 2  # refused once, rebuilt against a fresh head, landed
    assert out["rc"] == 0
    assert "rebuilding the record commit" in capsys.readouterr().out


def test_a_record_that_cannot_be_written_twice_still_reds_the_run(
    tmp_path, monkeypatch
):
    # Two attempts, not a ladder: a second loss is a fault to report.
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1


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


def test_the_record_remembers_which_issue_the_comment_landed_on(tmp_path, monkeypatch):
    # A later run posts to the SAME thread without a listing to get wrong - and cannot open
    # a second one because a listing came back unreadable.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    assert cfg_files[grades.DISTRIBUTED_PATH].splitlines()[0].endswith(",issue")
    record = grades.parse_distributed(cfg_files[grades.DISTRIBUTED_PATH])
    assert record[("ada-l", "assignment-1", grades.CHANNEL_ISSUE)][2] == "7"

    corrected = _SHEET.replace("score_individual: 43", "score_individual: 45")
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": corrected},
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
        # A lookup would have to go through here. It does not run at all.
        issue=grades.LOOKUP_FAILED,
    )
    ((_repo, body, _marker),) = again["comments"]
    assert "45" in body
    assert again["issues"] == []


def test_an_unreadable_issue_lookup_posts_nothing_and_reds_the_run(
    tmp_path, monkeypatch, capsys
):
    # Nothing is opened and nothing is posted for that unit; the run goes red so somebody
    # looks, and the next one tries again.
    out = _distribute(monkeypatch, tmp_path, issue=grades.LOOKUP_FAILED)
    assert out["rc"] == 1
    assert out["comments"] == []
    printed = capsys.readouterr()
    assert '"failed": 1' in printed.out
    assert "ada-l" not in printed.out


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


def test_a_gradebook_that_failed_to_write_is_not_emailed_about(tmp_path, monkeypatch):
    # "Your grades have been updated" over a push that did not land tells a student to go
    # and read a page that has not changed - and records the telling, so the run that
    # finally lands the page says nothing at all.
    out = _distribute(monkeypatch, tmp_path, put_files_ok=False)
    assert out["rc"] == 1
    assert out["outbox"] == []


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
    out = _distribute(monkeypatch, tmp_path, roster_rows=None)
    assert out["rc"] == 1
    assert "could not be read" in capsys.readouterr().err
    # And the registrar export is LEFT ALONE. It is one row per enrolled student, so
    # regenerating it from a roster nobody could read would commit a header line over the
    # file a registrar transcribes grades from.
    ((_repo, files, _delete),) = out["config"]
    assert grades.COHORT_CSV_NAME not in files
    assert grades.DISTRIBUTED_PATH in files
