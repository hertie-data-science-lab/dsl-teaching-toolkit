"""assign -- the slug transform, and WHO gets a repo. Auditors are read-only: handing one
an assignment repo (and, downstream, a grade) is the failure this guards. Exercised through
the dry-run path, which is pure: it reads a local roster and prints the planned units
without touching gh/git.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from dsl_course import assign, collect, grades
from dsl_course.schedule import Schedule
from tests.conftest import ROSTER_HEADER

HEADER = ROSTER_HEADER


def _roster_file(tmp_path, *rows: str):
    path = tmp_path / "students.csv"
    path.write_text("\n".join((HEADER, *rows)) + "\n")
    return str(path)


@pytest.fixture(autouse=True)
def _no_cohort_schedule(monkeypatch):
    """provision_all resolves the cohort-side name from schedule.yml; these tests exercise
    the provisioning mechanics, not the lookup, and must never reach for the network."""
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())


@pytest.fixture(autouse=True)
def _empty_cohort_listing(monkeypatch):
    """provision_all takes ONE repo listing of the cohort and answers "does this repo
    exist?" out of it. An empty org is the uninteresting answer for the tests below; the
    ones about the listing itself set their own after this fixture and win."""
    monkeypatch.setattr(assign, "list_org_repos", lambda org: [])


@pytest.fixture(autouse=True)
def feedback_issues(monkeypatch):
    """The Feedback issue each new submission repo gets, recorded as `(repo, body)`.

    Its own tests live in tests/test_grades.py; the assignment tests care only that one is
    opened, on the create path, with this assignment's facts in it."""
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(
        assign,
        "load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(),
    )
    monkeypatch.setattr(
        assign.grades,
        "ensure_feedback_issue",
        lambda org, repo, body, dry_run=False: opened.append((repo, body)) or 1,
    )
    return opened


@pytest.fixture(autouse=True)
def _team_lock_is_current(monkeypatch):
    """The Join-team form's mirror, refreshed beside `record_handout`. Its content has its
    own tests (tests/test_grades.py); a handout test only needs it not to reach the API."""
    monkeypatch.setattr(assign.grades, "write_team_lock", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def sheet_writes(monkeypatch):
    """provision_all creates the assignment's grading sheet once the handout has landed.

    Recorded rather than written: what the sheet CONTAINS has its own tests
    (tests/test_collect.py); what matters here is that a handout produces one, keyed on the
    same units the repos were made for."""
    written: list[dict] = []
    monkeypatch.setattr(
        assign,
        "sync_sheet",
        lambda course, cohort, sched, key, slug, template, **kw: (
            written.append({"key": key, "slug": slug, "template": template, **kw})
            or True
        ),
    )
    return written


def test_the_grading_sheet_is_created_at_handout_with_one_row_per_student(
    tmp_path, monkeypatch, sheet_writes
):
    # The sheet arrives WITH the repos, complete and blank. Created later - at the first
    # submission, say - a missing row would be indistinguishable from an unmarked one, and
    # a grader could not tell who had yet to hand in.
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "ben@uni.edu,Ben,enrolled,ben-k,43,dsl-def",
    )
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )

    ((sheet,),) = (sheet_writes,)
    assert (sheet["key"], sheet["slug"]) == ("assignment-1", "assignment-1")
    assert sheet["template"] == "assignment-1-f2026"
    assert sheet["is_group"] is False
    # One unit per onboarded student, keyed on the handle - the same key
    # `collect.submission_targets` uses, so every later refresh writes the same rows.
    assert sheet["units"] == [("ada-l", ["ada-l"]), ("ben-k", ["ben-k"])]


def test_a_handout_that_provisioned_nothing_does_not_rewrite_the_sheet(
    tmp_path, monkeypatch, sheet_writes
):
    # `due_releases` is cumulative, so the scheduler re-fires every handed-out assignment
    # on every tick and almost every one of those provisions nothing. Such a pass derives
    # nothing either, so writing the sheet could only undo what the refresh pass - which
    # runs EARLIER in the same tick - had just put in it.
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "skipped")
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )

    assert sheet_writes == []


def test_the_handout_sheet_for_a_group_assignment_is_keyed_on_the_team_name(
    tmp_path, monkeypatch, sheet_writes
):
    # NOT on the GitHub team slug the repo grant uses: teams.csv writes the name, and the
    # name is what `submission_targets` (and therefore every refresh) keys the sheet on.
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "ben@uni.edu,Ben,enrolled,ben-k,43,dsl-def",
    )
    monkeypatch.setattr(
        assign.teams, "load", lambda org: {"assignment-1": {"alpha": ["ada-l"]}}
    )
    monkeypatch.setattr(
        assign.sync_teams,
        "vet_groups",
        lambda groups, participants: [
            (team, members, []) for team, members in groups.items()
        ],
    )
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=True
    )

    ((sheet,),) = (sheet_writes,)
    assert sheet["is_group"] is True
    assert sheet["units"] == [("alpha", ["ada-l"])]


def test_an_unusable_solution_branch_does_not_block_provisioning(
    tmp_path, monkeypatch, capsys
):
    # A scheduled handout re-runs every tick, so aborting on a bad solution branch meant NO
    # student who onboarded after solution_datetime ever got a repo. The repos must be
    # handed out regardless; an unusable solution only reddens the run.
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    monkeypatch.setattr(assign, "fetch_solution", lambda *a, **k: None)
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    provisioned = []
    monkeypatch.setattr(
        assign,
        "provision_one",
        lambda *a, **k: provisioned.append(a[3]) or "created",
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    recorded = []
    monkeypatch.setattr(
        assign, "record_solution_released", lambda *a, **k: recorded.append(a)
    )

    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        solution=True,
        group=False,
    )
    err = capsys.readouterr().err
    assert provisioned == ["assignment-1-ada-l"], (
        "the abort is back - nobody provisioned"
    )
    assert rc == 1, "an unusable solution must still redden the run"
    assert "provisioning continues without it" in err
    assert recorded == [], "a failed solution must not be recorded as released"


def test_a_handout_that_skipped_every_repo_syncs_no_site(tmp_path, monkeypatch):
    # The scheduler re-fires every handed-out release on every hourly tick (that is what
    # gets a late onboarder their repo), so nearly every tick provisions nothing at all.
    # Syncing anyway re-rendered a whole cohort website once an hour for the rest of the
    # term - and the `changed` half of the answer is what lets the SCHEDULER decide the
    # same thing for the tick as a whole.
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)
    synced: list[tuple] = []
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a: synced.append(a))

    def run():
        return assign.provision_all(
            "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
        )

    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "skipped")
    assert run() == (0, False)
    assert synced == [], "a pass that changed nothing re-rendered the site"

    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")
    assert run() == (0, True)
    assert synced == [("COURSE", "COHORT")]


def _marker_run(
    tmp_path,
    monkeypatch,
    *,
    status="created",
    units=1,
    site_raises=False,
    record_ok=True,
):
    """provision_all with the network stubbed. Returns (rc, what was recorded)."""
    rows = [f"s{i}@uni.edu,S{i},enrolled,sh{i},{i},dsl-{i}" for i in range(units)]
    path = _roster_file(tmp_path, *rows)
    monkeypatch.setattr(assign, "fetch_solution", lambda *a, **k: tmp_path / "sol")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: status)
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())
    monkeypatch.setattr("dsl_course.schedule.entry_for_repo", lambda *a, **k: None)

    def sync(*a, **k):
        if site_raises:
            raise RuntimeError("tree read failed")

    monkeypatch.setattr("dsl_course.site.sync_site", sync)
    recorded = []
    monkeypatch.setattr(
        assign,
        "record_solution_released",
        lambda *a, **k: recorded.append(a) or record_ok,
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        solution=True,
        group=False,
    )
    return rc, recorded


def test_the_marker_is_not_written_when_a_solution_push_failed(tmp_path, monkeypatch):
    # The worst failure this feature can have: the marker is fire-once, so recording a
    # release whose pushes failed means the student NEVER receives the solution and no
    # later tick ever retries. provision_one must report the failure, not just log it.
    rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-solution")
    assert recorded == []
    assert rc == 1


def test_the_marker_IS_written_when_the_site_sync_fails(tmp_path, monkeypatch):
    # A site-sync failure says nothing about whether the solution shipped - and it is
    # PERSISTENT (a malformed people.yml raises every run), so withholding the marker for
    # it would re-clone every student repo every hour for the rest of the term.
    rc, recorded = _marker_run(tmp_path, monkeypatch, site_raises=True)
    assert recorded == [("COHORT", "assignment-1", 1)]
    assert rc == 1  # the run still goes red for the site


def test_the_marker_is_not_written_when_a_repo_could_not_be_created(
    tmp_path, monkeypatch
):
    # `failed-create` is returned BEFORE the solution push is even attempted, so the unit
    # never received it - but the marker is fire-once, so writing it here meant that
    # student never got the solution and no later tick retried. Only the failures that
    # happen after a successful push may be written over.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-create")
    assert recorded == []


def test_the_marker_IS_written_when_a_handle_is_dead(tmp_path, monkeypatch):
    # Same reasoning: one unusable student handle is persistent and unrelated to the push.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, status="failed-no-collaborator")
    assert recorded == [("COHORT", "assignment-1", 1)]


def test_the_marker_IS_written_when_a_feedback_issue_would_not_open(
    tmp_path, monkeypatch
):
    # Same reasoning again: the issue is opened AFTER the solution push and the fault is
    # persistent, so withholding the fire-once marker for it would re-clone every
    # submission repo every hour for the rest of the term.
    _rc, recorded = _marker_run(
        tmp_path, monkeypatch, status="failed-no-feedback-issue"
    )
    assert recorded == [("COHORT", "assignment-1", 1)]
    assert "failed-no-feedback-issue" not in assign._SOLUTION_NOT_PUSHED


def test_a_feedback_issue_that_would_not_open_reds_the_handout(tmp_path, monkeypatch):
    # The issue is opened on the CREATE path only - the cron re-fires every release every
    # tick, so an existing repo is never re-probed. A repo that misses its one chance used
    # to log a line and report `ok`, so a whole cohort could be handed out green with
    # nowhere for its receipts, feedback or grades to be posted.
    rc, _recorded = _marker_run(
        tmp_path, monkeypatch, status="failed-no-feedback-issue"
    )
    assert rc == 1


def test_the_new_repo_status_says_its_feedback_issue_never_opened(
    monkeypatch, feedback_issues
):
    _provision_one_env(monkeypatch)
    monkeypatch.setattr(
        assign.grades, "ensure_feedback_issue", lambda *a, **k: grades.LOOKUP_FAILED
    )
    assert (
        assign.provision_one(
            "COURSE",
            "assignment-1",
            "COHORT",
            "assignment-1-ada-l",
            ["ada-l"],
            "assignment-1",
            existing={},
            feedback_body="BODY",
        )
        == "failed-no-feedback-issue"
    )


def test_a_failed_solution_push_still_wins_over_a_missing_feedback_issue(
    monkeypatch, tmp_path
):
    # The fire-once solution marker is written off these statuses, so the one fault that
    # must never be masked is the push that did not happen.
    _provision_one_env(monkeypatch)
    monkeypatch.setattr(
        assign.grades, "ensure_feedback_issue", lambda *a, **k: grades.LOOKUP_FAILED
    )
    monkeypatch.setattr(assign, "_wait_for_content", lambda *a, **k: True)
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    assert (
        assign.provision_one(
            "COURSE",
            "assignment-1",
            "COHORT",
            "assignment-1-ada-l",
            ["ada-l"],
            "assignment-1",
            tmp_path,
            existing={},
            feedback_body="BODY",
        )
        == "failed-solution"
    )


def test_the_marker_is_not_written_when_there_is_nobody_to_push_to(
    tmp_path, monkeypatch
):
    # Nobody onboarded yet -> no repos, so nothing was pushed. Recording it would mean
    # every student who onboards afterwards never receives the solution.
    _rc, recorded = _marker_run(tmp_path, monkeypatch, units=0)
    assert recorded == []


def test_an_unwritten_solution_marker_goes_red(tmp_path, monkeypatch, capsys):
    # The marker is what stops the next tick re-cloning every submission repo to re-push a
    # solution they already have. A write that failed was discarded, so the run went green
    # and the re-clone recurred every hour for the rest of the term.
    rc, recorded = _marker_run(tmp_path, monkeypatch, record_ok=False)
    assert recorded == [("COHORT", "assignment-1", 1)]  # it was attempted
    assert rc == 1
    assert "fire-once record could not be written" in capsys.readouterr().err


def test_a_recorded_solution_release_stays_green(tmp_path, monkeypatch):
    rc, recorded = _marker_run(tmp_path, monkeypatch)
    assert recorded == [("COHORT", "assignment-1", 1)] and rc == 0


def _template_tree(monkeypatch, files: dict[str, str]):
    """Stub the tree + blob reads `withhold_from_template` does; record the deletes."""
    deleted: list[tuple[str, ...]] = []
    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(assign, "repo_tree", lambda *a, **k: tuple(files))
    monkeypatch.setattr(
        assign, "get_file_content", lambda org, repo, path, **k: files.get(path)
    )
    monkeypatch.setattr(
        assign,
        "put_files",
        lambda org, repo, w, msg, delete=(), **k: deleted.append(delete) or True,
    )
    return deleted


def test_the_cohort_assignment_template_is_filtered_before_it_is_a_template(
    monkeypatch,
):
    # The handout is a SERVER-SIDE copy (generate_from_template), so there is no clone and
    # no copytree hook - the filter has to be a delete, and it has to land before
    # `is_template` is set, because that is the moment student repos start coming off it.
    deleted = _template_tree(
        monkeypatch,
        {
            ".releaseignore": "rubric-draft.md\nnotes/\n",
            "starter.py": "",
            "rubric-draft.md": "not for students",
            "notes/mine.md": "",
        },
    )
    assert assign.withhold_from_template("COHORT", "a1")
    # The ignore file goes too - it withholds itself.
    assert deleted == [(".releaseignore", "notes/mine.md", "rubric-draft.md")]


def test_a_template_with_no_releaseignore_is_left_alone(monkeypatch):
    deleted = _template_tree(monkeypatch, {"starter.py": "", "README.md": ""})
    assert assign.withhold_from_template("COHORT", "a1")
    assert deleted == []


def test_an_empty_template_tree_stops_the_handout(monkeypatch, capsys):
    # The likeliest failure, and it used to fail OPEN: `default_branch` GUESSES `main` when
    # it cannot read the repo, `repo_tree` answers () for a 404 rather than raising, so a
    # template on a differently-named branch yielded "nothing to withhold" and was handed
    # to every student unfiltered. `_wait_for_content` has just confirmed the repo HAS
    # content, so an empty tree is a contradiction - and a contradiction is "could not
    # tell", not "nothing".
    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(assign, "repo_tree", lambda *a, **k: ())
    assert not assign.withhold_from_template("COHORT", "a1")
    assert "handout stopped" in capsys.readouterr().err


def test_an_unreadable_ignore_blob_stops_the_handout(monkeypatch, capsys):
    # The tree read was guarded; the per-blob reads were not, and `get_file_content` raises
    # on any non-404 failure. Unhandled, that escaped through provision_all into the hourly
    # scheduler and took every later cohort's release with it.
    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(assign, "repo_tree", lambda *a, **k: (".releaseignore", "x.py"))

    def boom(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(assign, "get_file_content", boom)
    assert not assign.withhold_from_template("COHORT", "a1")
    assert "handout stopped" in capsys.readouterr().err


def test_the_withheld_count_matches_what_is_actually_deleted(monkeypatch, capsys):
    # `put_files` can only delete BLOBS, so a tree fetch that also returned directories
    # made the reported count larger than the work done: one withheld folder of one file
    # read as "withheld 2 path(s)". Asking for blobs only keeps the two in step.
    kinds: list[str] = []

    def fake_tree(org, repo, branch, kind=""):
        kinds.append(kind)
        return ("notes/mine.md", ".releaseignore")

    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(assign, "repo_tree", fake_tree)
    monkeypatch.setattr(assign, "get_file_content", lambda *a, **k: "notes/\n")
    deleted: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        assign,
        "put_files",
        lambda o, r, w, m, delete=(), **k: deleted.append(delete) or True,
    )
    assert assign.withhold_from_template("COHORT", "a1")
    assert kinds == ["blob"]
    assert deleted == [(".releaseignore", "notes/mine.md")]
    assert "withheld 2 path(s)" in capsys.readouterr().out


def test_an_unreadable_template_tree_stops_the_handout(monkeypatch, capsys):
    # Fails CLOSED, unlike most reads in this codebase: "could not tell" is not "nothing to
    # withhold", and answers published to a cohort cannot be taken back. A transient
    # failure costs a re-run, which the hourly tick does anyway.
    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")

    def boom(*a, **k):
        raise RuntimeError("502")

    monkeypatch.setattr(assign, "repo_tree", boom)
    assert not assign.withhold_from_template("COHORT", "a1")
    assert "handout stopped" in capsys.readouterr().err


def test_a_failed_withhold_stops_the_handout_too(monkeypatch, capsys):
    monkeypatch.setattr(assign, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(
        assign, "repo_tree", lambda *a, **k: (".releaseignore", "x.key")
    )
    monkeypatch.setattr(assign, "get_file_content", lambda *a, **k: "*.key\n")
    monkeypatch.setattr(assign, "put_files", lambda *a, **k: False)
    assert not assign.withhold_from_template("COHORT", "a1")
    assert "handout stopped" in capsys.readouterr().err


def test_ensure_cohort_template_refuses_when_the_filter_fails(monkeypatch):
    # Wiring: a failed filter must abort ensure_cohort_template rather than fall through to
    # `is_template`, which would hand the unfiltered template to every student.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: False)
    patched = []
    monkeypatch.setattr(assign, "gh", lambda *a, **k: patched.append(a) or (0, ""))
    assert assign.ensure_cohort_template("C", "t", "COHORT", "a1") is None
    assert patched == []


def test_push_solution_withholds_what_the_templates_releaseignore_excludes(
    tmp_path, monkeypatch
):
    # The solution push is the third outbound copy from staging, and used to be a bare
    # `shutil.copytree` with no filter at all - so a stray key or a scratch notebook beside
    # the model answer went to every student with it. The patterns anchor at the template
    # CLONE root, which is the `root` half of the Solution.
    root = tmp_path / "t"
    (root / "solution").mkdir(parents=True)
    (root / ".releaseignore").write_text("*.key\nscratch/\n")
    (root / "solution" / "answers.ipynb").write_text("the model answer")
    (root / "solution" / "grader.key").write_text("SECRET")
    (root / "solution" / "scratch").mkdir()
    (root / "solution" / "scratch" / "wip.py").write_text("half an idea")

    def fake_clone(org, repo, wd):
        wd.mkdir(parents=True, exist_ok=True)
        return True

    monkeypatch.setattr(assign, "clone", fake_clone)

    pushed: list[str] = []

    def fake_git(*args):
        if "add" in args:
            wd = Path(args[args.index("-C") + 1])
            pushed.extend(
                p.relative_to(wd).as_posix() for p in wd.rglob("*") if p.is_file()
            )
        return (0, "")

    monkeypatch.setattr(assign, "git", fake_git)
    assert assign.push_solution("COHORT", "a1-ada", root / "solution")
    assert pushed == ["solution/answers.ipynb"]


def test_a_failed_solution_push_reaches_the_returned_status(tmp_path, monkeypatch):
    # The root of it: provision_one used to log the failure and return "ok" anyway, so
    # provision_all could not tell. Both the group and individual paths must report it.
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    individual = assign.provision_one(
        "C",
        "t",
        "COHORT",
        "r",
        ["ada"],
        "assignment-1",
        sol_dir=tmp_path,
    )
    group = assign.provision_one(
        "C",
        "t",
        "COHORT",
        "r",
        ["ada"],
        "assignment-1",
        sol_dir=tmp_path,
        team="t-a",
    )
    assert individual == "failed-solution"
    assert group == "failed-solution"


def test_provisioning_skips_auditors(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz",
        "bob@uni.edu,Bob,,bob-b,44,dsl-def",  # blank role -> enrolled
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "assignment-1-ada-l" in out and "assignment-1-bob-b" in out
    assert "eve-e" not in out  # the auditor gets no repo
    assert "1 auditor row(s) skipped" in out
    assert "2 student(s)" in out


def test_provisioning_still_works_for_a_roster_without_a_role_column(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    path = tmp_path / "students.csv"
    path.write_text(
        "student_id,hertie_email,name,github_handle,github_id,section\n"
        "1,ada@uni.edu,Ada,ada-l,42,A\n"
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=str(path),
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "assignment-1-ada-l" in out
    assert "auditor row(s) skipped" not in out


def test_a_dry_run_names_no_student_in_a_public_log(tmp_path, capsys, monkeypatch):
    # The Release assignment workflow runs in the course org's PUBLIC .github, so its log
    # must not publish who is enrolled. The counts a faculty member reads still appear.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "bob@uni.edu,Bob,enrolled,bob-b,43,dsl-def",
    )
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "ada-l" not in out and "bob-b" not in out
    assert "2 student(s)" in out  # the aggregate a faculty member actually reads


def test_not_yet_onboarded_rows_are_still_skipped_separately(tmp_path, capsys):
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "bob@uni.edu,Bob,enrolled,,,dsl-def",  # no handle yet
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz",
    )
    assign.provision_all(
        "COURSE",
        "assignment-1-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert "1 not-yet-onboarded row(s) skipped" in out
    assert "1 auditor row(s) skipped" in out


def test_group_none_infers_per_team_from_the_templates_grading_yml(
    tmp_path, capsys, monkeypatch
):
    # group=None (the default - scheduler and untick'd button alike) asks the template's
    # own grading_config.yml: `type: group` provisions per TEAM without anyone
    # force-ticking.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(type="group"),
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "bob-b"], "team-2": ["cid-c"]},
    )
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "bob@uni.edu,Bob,enrolled,bob-b,43,dsl-def",
        "cid@uni.edu,Cid,enrolled,cid-c,44,dsl-ghi",
    )
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "provisioning per team" in out
    assert "assignment-4-project-team-1" in out
    assert "assignment-4-project-team-2" in out
    assert "2 team(s)" in out


def test_group_false_forces_individual_even_for_a_group_template(
    tmp_path, capsys, monkeypatch
):
    # An explicit False beats the assignment's own `type: group` - the caller decided.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(type="group"),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-4-project-f2026",
        "COHORT",
        roster_path=path,
        group=False,
        dry_run=True,
    )
    assert rc == 0
    assert "assignment-4-project-ada-l" in capsys.readouterr().out


# ------------------------------------ what counts as a failed handout (the exit code)


@pytest.fixture
def _provisioned(monkeypatch):
    """A repo that creates cleanly, so provision_one exercises the access half. (An
    EXISTING repo with nothing due returns before any access call - see below.)"""
    monkeypatch.setattr(assign, "repo_exists", lambda org, repo: False)
    monkeypatch.setattr(assign, "generate_from_template", lambda **k: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)


def test_a_repo_no_student_can_open_is_a_failed_handout(_provisioned, monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so a repo
    # nobody can see never reached provision_all's exit predicate: the release went green
    # while the student had nothing to submit into.
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: False)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
    )
    assert status.startswith("failed")


def test_a_team_missing_members_is_not_named_in_a_public_log(
    _provisioned, monkeypatch, capsys
):
    # The repo is named after the TEAM, so this line published who is grouped with whom -
    # from a release that runs in the course org's PUBLIC `.github`.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    assert (
        assign.provision_one(
            "COURSE",
            "assignment-1",
            "COHORT",
            "assignment-1-wizards",
            ["ada-l", "bob-b"],
            "assignment-1",
            team="assignment-1-wizards",
        )
        == "failed-team-members"
    )
    captured = capsys.readouterr()
    assert "wizards" not in captured.out + captured.err
    # The fault itself still reaches faculty; the status above is what gets counted.
    assert "a team is missing member(s)" in captured.err


def test_the_verbose_log_still_says_which_team_it_was(
    _provisioned, monkeypatch, capsys
):
    monkeypatch.setenv("DSL_VERBOSE", "1")
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-wizards",
        ["ada-l", "bob-b"],
        "assignment-1",
        team="assignment-1-wizards",
    )
    # The detail line specifically - `provision_one` narrates the CREATE through
    # log_person too, so a bare repo-name assertion here would pass without this fix.
    assert (
        "team assignment-1-wizards is missing member(s) - they cannot see "
        "COHORT/assignment-1-wizards" in capsys.readouterr().out
    )


def test_a_group_repo_reports_the_teams_own_failures(_provisioned, monkeypatch):
    # ensure_team's result used to be discarded, so a team that couldn't take its members
    # (they see nothing - access is via the team) still reported "ok".
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-wizards",
        ["ada-l", "bob-b"],
        "assignment-1",
        team="assignment-1-wizards",
    )
    assert status.startswith("failed")

    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    status = assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-wizards",
        ["ada-l", "bob-b"],
        "assignment-1",
        team="assignment-1-wizards",
    )
    assert status == "ok"


# ------------------------------------- group provisioning honours the roster allowlist


def test_group_provisioning_filters_teams_csv_through_the_roster_allowlist(
    tmp_path, capsys, monkeypatch
):
    # teams.csv is student-writable (the welcome "Join team" issue appends rows). A handle
    # not on the roster - a typo, or a stranger's login - must be excluded, never invited
    # into the private org with maintain on a repo. An auditor's handle is excluded too.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-repo lines are verbose-only
    monkeypatch.setattr(
        "dsl_course.assign.load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(type="group"),
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "stranger-x", "Eve-E"]},
    )
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz",  # an auditor, not a team member
    )
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    captured = capsys.readouterr()
    assert rc == 0
    # The DRY-RUN provisioning lines name their members as `@handle`.
    assert "@ada-l" in captured.out  # the one valid, enrolled, onboarded handle
    assert "@stranger-x" not in captured.out  # never provisioned
    assert "@eve-e" not in captured.out  # the auditor's handle is not a team member
    # Both rejections are reported - but the HANDLES a student typed go to the verbose
    # channel (this workflow's log is world-readable) and the actionable error is a count.
    assert "stranger-x" in captured.out and "Eve-E" in captured.out
    assert "2 handle(s) in teams.csv" in captured.err
    assert "stranger-x" not in captured.err and "Eve-E" not in captured.err


def test_a_rejected_teams_csv_handle_is_not_published_in_the_workflow_log(
    tmp_path, capsys, monkeypatch
):
    # No rendered workflow sets DSL_VERBOSE, so this is what a course org's PUBLIC Actions
    # log actually shows: a count a faculty member can act on, and no student's typing.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        "dsl_course.assign.load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(type="group"),
    )
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l", "stranger-x"]},
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, dry_run=True
    )
    captured = capsys.readouterr()
    assert "1 handle(s) in teams.csv" in captured.err
    assert "stranger-x" not in captured.err + captured.out


# ------------------------------------------------ half-created cohort template healing


def test_ensure_cohort_template_repairs_a_half_created_template(monkeypatch):
    # A prior run left the repo existing but never set is_template (a _wait_for_content
    # timeout). The exists-path must still verify content and re-PATCH is_template
    # (idempotent), healing it instead of failing every later handout with "not a template".
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args, **kwargs):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(assign, "gh", fake_gh)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        == "assignment-1"
    )
    assert any("PATCH" in a for a in calls) and any(
        "is_template=true" in a for a in calls
    )


def test_ensure_cohort_template_stamps_the_topic_the_site_gates_on(monkeypatch):
    # discovery.discover_handed_out_assignments reads this topic back as the record that
    # the assignment went out, and site._assignment_entry withholds the brief until it
    # does - so dropping the stamp silently blanks every brief on every cohort site.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    stamped: list[tuple] = []
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: stamped.append(a) or True)
    assign.ensure_cohort_template(
        "COURSE", "assignment-1-f2026", "COHORT", "homework-1"
    )
    # stamped on the COHORT-side repo, under the name the site looks it up by
    assert stamped == [("COHORT", "homework-1", ["homework-1", "assignment-template"])]


def test_ensure_cohort_template_says_what_a_failed_topic_stamp_costs(monkeypatch):
    # The hand-out itself succeeded, so this must not fail the run - but a silent drop
    # leaves the site withholding a brief the students already hold.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: False)
    errs: list[str] = []
    monkeypatch.setattr(assign, "log_err", errs.append)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        == "assignment-1"
    )
    assert "assignment-template" in errs[0] and "withheld" in errs[0]


def test_ensure_cohort_template_fails_loudly_when_is_template_patch_fails(monkeypatch):
    # The is_template PATCH result was discarded; now a failed PATCH returns None so the run
    # goes red rather than fanning out from a repo that isn't actually a template.
    monkeypatch.setattr(assign, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (1, "403 Forbidden"))
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1"
        )
        is None
    )


# ----------------------------------- handout recorded under the schedule key + site guard


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("tree read failed"),
        # A config file with one bad indent raises yaml.YAMLError, which is NOT a
        # RuntimeError - it used to walk through the guard and misreport the handout.
        yaml.parser.ParserError(None, None, "bad indent", None),
    ],
)
def test_provision_all_records_handout_under_schedule_key_and_survives_site_failure(
    tmp_path, monkeypatch, exc
):
    # record_handout keys on the schedule KEY; with a cohort_dest_repo set, the cohort-side
    # name differs, and passing the name appended a bogus block. And site.sync_site now RAISES
    # on a genuine read failure - one such failure must be logged + counted, never a traceback
    # that misreports the whole handout.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dsl_course.schedule import AssignmentEntry, Schedule

    entry = AssignmentEntry(
        course_source_repo="assignment-4-project-f2026",
        cohort_dest_repo="group-project",
        due_datetime=datetime(2026, 11, 15, tzinfo=ZoneInfo("Europe/Berlin")),
    )
    monkeypatch.setattr(
        "dsl_course.schedule.load",
        lambda org: Schedule(assignments={"project": entry}),
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "dsl_course.schedule.record_handout",
        lambda org, slug, *a: captured.__setitem__("key", slug),
    )
    monkeypatch.setattr(assign, "ensure_cohort_template", lambda *a: "group-project")
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")

    from dsl_course import site

    def boom_site(*a, **k):
        raise exc

    monkeypatch.setattr(site, "sync_site", boom_site)
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    rc, _changed = assign.provision_all(
        "COURSE", "assignment-4-project-f2026", "COHORT", roster_path=path, group=False
    )
    assert captured["key"] == "project"  # the schedule key, not "group-project"
    assert rc == 1  # the site failure was counted, not raised as a traceback


def test_both_assignment_arms_grant_faculty_read(_provisioned, monkeypatch):
    # A cohort org is default_repository_permission=none, so a team grant is the WHOLE of
    # a non-owner instructor's access - and submission repos granted only the student. The
    # group arm RETURNS inside itself, so the grant must sit before the split or every team
    # project repo would go on granting nobody but the team. READ, not write: marking
    # happens in classroom-config/grading_sheets/<slug>.yml, after the snapshot froze HEAD.
    faculty = []
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: faculty.append(a))
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    assign.provision_one("COURSE", "a1", "COHORT", "a1-ada-l", ["ada-l"], "a1")
    assign.provision_one(
        "COURSE",
        "a1",
        "COHORT",
        "a1-wizards",
        ["ada-l", "bob-b"],
        "a1",
        team="a1-wizards",
    )
    read = assign.FACULTY_READ_ACCESS
    assert faculty == [("COHORT", "a1-ada-l", read), ("COHORT", "a1-wizards", read)]


def test_the_scheduler_leaves_an_existing_repo_alone_but_the_button_repairs_it(
    monkeypatch,
):
    # The scheduler re-runs every handed-out release hourly. Re-granting access to an
    # existing repo on every tick cost 2-4 API calls per student per assignment for the
    # rest of term. The hourly path (touch_existing=False) skips it; the manual Release
    # assignment button keeps re-granting the STUDENT, so re-running it still repairs a
    # student's access. The faculty grant is not re-run on either path - the nightly sweep
    # owns that floor. With a solution to push, the push happens either way.
    calls = []
    monkeypatch.setattr(assign, "repo_exists", lambda org, repo: True)
    for name in (
        "add_collaborator",
        "grant_team_repo_access",
        "grant_faculty",
    ):
        monkeypatch.setattr(
            assign, name, lambda *a, _n=name, **k: calls.append(_n) or True
        )
    monkeypatch.setattr(
        assign.sync_teams, "ensure_team", lambda *a, **k: calls.append("team") or True
    )
    hourly = {"touch_existing": False}
    assert assign.provision_one("C", "t", "K", "a1-ada", ["ada"], "a1", **hourly) == (
        "skipped"
    )
    assert assign.provision_one(
        "C", "t", "K", "a1-w", ["ada"], "a1", team="a1-w", **hourly
    ) == ("skipped")
    assert calls == []
    # the button (default) re-grants the student, and only the student
    assert assign.provision_one("C", "t", "K", "a1-ada", ["ada"], "a1") == "skipped"
    assert calls == ["add_collaborator"]
    pushed = []
    monkeypatch.setattr(assign, "push_solution", lambda *a: pushed.append(a) or True)
    assert assign.provision_one(
        "C",
        "t",
        "K",
        "a1-ada",
        ["ada"],
        "a1",
        sol_dir=Path("s"),
        **hourly,
    ) == ("skipped")
    assert len(pushed) == 1


# ------------- teams.csv is keyed on the SCHEDULE KEY, repos on the cohort-side name


def _scheduled(monkeypatch, key: str, dest: str, source: str):
    """A cohort schedule with ONE assignment whose cohort-side name differs from its key."""
    from datetime import datetime, timezone

    from dsl_course.schedule import AssignmentEntry

    entry = AssignmentEntry(
        due_datetime=datetime(2026, 11, 1, tzinfo=timezone.utc),
        course_source_repo=source,
        cohort_dest_repo=dest,
    )
    monkeypatch.setattr(
        "dsl_course.schedule.load", lambda org: Schedule(assignments={key: entry})
    )
    # ... and a group assignment, which only the template's grading_config.yml can say.
    monkeypatch.setattr(
        "dsl_course.assign.load_grading_spec",
        lambda org, template: collect.grades.GradingSpec(type="group"),
    )


def _two_on_one_template(monkeypatch):
    """A plan where two entries hand out from one template, each naming its own repos."""
    from datetime import datetime, timezone

    from dsl_course.schedule import AssignmentEntry

    def entry(dest):
        return AssignmentEntry(
            due_datetime=datetime(2026, 11, 1, tzinfo=timezone.utc),
            course_source_repo="assignment-2-f2026",
            cohort_dest_repo=dest,
        )

    monkeypatch.setattr(
        "dsl_course.schedule.load",
        lambda org: Schedule(
            assignments={
                "assignment-2": entry("assignment-2"),
                "assignment-2-resit": entry("assignment-2-resit"),
            }
        ),
    )


def test_a_handout_refuses_to_choose_between_two_entries_on_one_template(
    tmp_path, capsys, monkeypatch
):
    # Both entries are real assignments with their own repos and their own marks. Picking
    # the first would hand the resit's brief to the whole cohort under the wrong name, and
    # a handout is not a thing you can take back.
    _two_on_one_template(monkeypatch)
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    rc, changed = assign.provision_all(
        "COURSE", "assignment-2-f2026", "COHORT", roster_path=path, dry_run=True
    )
    assert (rc, changed) == (1, False)
    err = capsys.readouterr().err
    assert "assignment-2-resit" in err and "say which" in err


def test_a_handout_told_which_entry_names_that_entrys_repos(
    tmp_path, capsys, monkeypatch
):
    _two_on_one_template(monkeypatch)
    monkeypatch.setenv("DSL_VERBOSE", "1")
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    rc, _changed = assign.provision_all(
        "COURSE",
        "assignment-2-f2026",
        "COHORT",
        roster_path=path,
        dry_run=True,
        slug="assignment-2-resit",
    )
    assert rc == 0
    assert "assignment-2-resit-ada-l" in capsys.readouterr().out


def test_group_handout_looks_teams_up_by_key_and_names_repos_by_dest(
    tmp_path, capsys, monkeypatch
):
    # With `cohort_dest_repo` set the two names diverge. teams.csv is keyed on the SCHEDULE
    # KEY (the Join-team form validates the slug against `assignments:` and writes it), so
    # looking teams up by the cohort-side name found none at all - the handout failed with
    # "no teams" while the CSV was full.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    _scheduled(monkeypatch, "regression", "wk3-regression", "wk3-regression-f2026")
    asked: list[str] = []
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams,
        "teams_for",
        lambda rows, slug: (
            asked.append(slug)
            or ({"team-1": ["ada-l"]} if slug == "regression" else {})
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    rc, _changed = assign.provision_all(
        "COURSE", "wk3-regression-f2026", "COHORT", roster_path=path, dry_run=True
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert asked == ["regression"]  # keyed on the schedule key, not the dest repo
    assert "COHORT/wk3-regression-team-1" in out  # the repo keeps the cohort-side name


def test_the_granted_team_slug_matches_the_one_sync_teams_reconciles(
    tmp_path, monkeypatch
):
    # sync_teams.desired_teams derives its slug from the teams.csv key, so a handout that
    # derived its own from the cohort-side name granted `wk3-regression-team-1` while Sync
    # membership kept reconciling `regression-team-1`: two teams, and the members were in
    # the one with no repo.
    from dsl_course import sync_teams

    _scheduled(monkeypatch, "regression", "wk3-regression", "wk3-regression-f2026")
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {"unused": {}})
    monkeypatch.setattr(
        assign.teams, "teams_for", lambda rows, slug: {"team-1": ["ada-l"]}
    )
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "wk3-regression"
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    granted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        assign,
        "provision_one",
        lambda *a, **k: granted.append((a[3], k["team"])) or "ok",
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    assign.provision_all("COURSE", "wk3-regression-f2026", "COHORT", roster_path=path)
    assert granted == [("wk3-regression-team-1", "regression-team-1")]
    # ... which is exactly what the membership sync materialises from the same CSV.
    assert sync_teams.desired_teams({"regression": {"team-1": ["ada-l"]}}) == {
        "regression-team-1": {"ada-l"}
    }


def test_a_group_handout_with_no_teams_yet_waits_on_the_cron_and_fails_on_the_button(
    tmp_path, monkeypatch, capsys
):
    # Teams form when students click 'Join team', days after the handout datetime. The
    # hourly cron counted the empty CSV as a failure and went red every tick until the
    # first team formed; an operator pressing the button still needs to be told.
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {})
    monkeypatch.setattr(assign.teams, "teams_for", lambda rows, slug: {})

    def boom(*a, **k):
        raise AssertionError("nothing may be provisioned without a team")

    monkeypatch.setattr(assign, "ensure_cohort_template", boom)
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")

    def run(**kw):
        return assign.provision_all(
            "COURSE", "project-f2026", "COHORT", roster_path=path, group=True, **kw
        )

    assert run(scheduled=True) == (0, False)
    out = capsys.readouterr().out
    assert "[wait] no teams" in out and "the first team forms" in out
    assert run() == (1, False)
    err = capsys.readouterr().err
    assert "no teams for" in err and "students self-select" in err


def test_an_allocated_assignment_with_no_teams_names_the_teaching_team(
    tmp_path, monkeypatch, capsys
):
    # `team_formation: assigned` means the Join-team form refuses every request, so
    # telling this course to wait for students to self-select points them at a door that
    # is shut. Who fills teams.csv is the assignment's own declaration.
    monkeypatch.setattr(assign.teams, "load", lambda cohort_org: {})
    monkeypatch.setattr(assign.teams, "teams_for", lambda rows, slug: {})
    monkeypatch.setattr(
        assign,
        "load_grading_spec",
        lambda org, template: grades.GradingSpec(
            type="group", team_formation="assigned"
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")

    def run(**kw):
        return assign.provision_all(
            "COURSE", "project-f2026", "COHORT", roster_path=path, **kw
        )

    assert run(scheduled=True) == (0, False)
    assert "the teaching team writes them into teams.csv" in capsys.readouterr().out
    assert run() == (1, False)
    err = capsys.readouterr().err
    assert "team_formation: assigned" in err and "self-select" not in err


# --------------- a failed solution push outranks every other fault (the marker depends on it)


@pytest.mark.parametrize(
    "broken",
    ["grant_team_repo_access", "add_collaborator"],
)
def test_a_failed_solution_wins_over_a_failed_access_grant(
    tmp_path, monkeypatch, broken
):
    # provision_all writes the FIRE-ONCE solution marker off these statuses, so a repo that
    # reported `failed-no-access` / `failed-no-collaborator` had its missing solution
    # forgotten - and the marker guaranteed no later tick would ever retry it.
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: True)
    monkeypatch.setattr(assign, broken, lambda *a, **k: False)
    team = "t-a" if broken == "grant_team_repo_access" else None
    status = assign.provision_one(
        "C",
        "t",
        "COHORT",
        "r",
        ["ada"],
        "assignment-1",
        sol_dir=tmp_path,
        team=team,
    )
    assert status == "failed-solution"


def test_a_failed_solution_wins_over_a_team_missing_members(tmp_path, monkeypatch):
    monkeypatch.setattr(assign, "push_solution", lambda *a, **k: False)
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "grant_team_repo_access", lambda *a, **k: True)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", lambda *a, **k: False)
    status = assign.provision_one(
        "C",
        "t",
        "COHORT",
        "r",
        ["ada"],
        "assignment-1",
        sol_dir=tmp_path,
        team="t-a",
    )
    assert status == "failed-solution"


def test_a_team_of_rejected_handles_gets_no_repo_at_all(monkeypatch, capsys):
    # Every handle in the teams.csv row failed the roster allowlist, so the team is empty
    # and the repo can be granted to nobody. The check ran AFTER creation, so a typo'd team
    # left a private repo behind that no student could open, for the term.
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: False)

    def boom(*a, **k):
        raise AssertionError("a team with no vetted members must create nothing")

    monkeypatch.setattr(assign, "generate_from_template", boom)
    monkeypatch.setattr(assign.sync_teams, "ensure_team", boom)
    status = assign.provision_one(
        "C", "t", "COHORT", "a1-team-1", [], "assignment-1", team="assignment-1-team-1"
    )
    assert status == "failed-no-members"
    assert "no vetted members" in capsys.readouterr().err


def test_a_solution_waits_for_the_repo_this_run_created_to_populate(
    tmp_path, monkeypatch, capsys
):
    # template-generate is async. Pushing into a repo that is still empty clones a repo with
    # no branch, so the solution landed on the runner's own default branch - invisible to
    # the student and to grading, on a green run.
    monkeypatch.setattr(assign, "repo_exists", lambda *a, **k: False)
    monkeypatch.setattr(assign, "generate_from_template", lambda **k: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, repo: False)

    def boom(*a, **k):
        raise AssertionError("the solution must not be pushed into an empty repo")

    monkeypatch.setattr(assign, "push_solution", boom)
    status = assign.provision_one(
        "C", "t", "COHORT", "a1-ada", ["ada"], "assignment-1", sol_dir=tmp_path
    )
    assert (
        status == "failed-solution"
    )  # withholds the fire-once marker, so a tick retries
    assert "has not populated yet" in capsys.readouterr().err


# ------------------------------------------- ONE listing instead of a probe per repo


def _ready_template(name="assignment-1"):
    return {"name": name, "isTemplate": True, "topics": [name, "assignment-template"]}


def _listing_run(tmp_path, monkeypatch, listing):
    """provision_all over two students, with `listing` standing in for the org listing.
    Returns (the orgs listed, the repos generate_from_template was asked to create)."""
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "bob@uni.edu,Bob,enrolled,bob-b,43,dsl-def",
    )
    listed: list[str] = []

    def fake_listing(org):
        listed.append(org)
        if isinstance(listing, Exception):
            raise listing
        return listing

    created: list[str] = []
    monkeypatch.setattr(assign, "list_org_repos", fake_listing)
    monkeypatch.setattr(
        assign, "generate_from_template", lambda **k: created.append(k["name"]) or True
    )
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)
    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )
    return listed, created


def test_provision_all_lists_the_org_once_and_probes_no_repo(tmp_path, monkeypatch):
    # A `repo_exists` per unit cost a GET per student per assignment on EVERY hourly tick
    # (~1,200 an hour for a large cohort) where one paginated listing costs three.
    monkeypatch.setattr(
        assign,
        "repo_exists",
        lambda *a, **k: pytest.fail("a per-repo probe is back in the hot path"),
    )
    listed, created = _listing_run(
        tmp_path,
        monkeypatch,
        [_ready_template(), {"name": "assignment-1-ada-l", "topics": []}],
    )
    assert listed == ["COHORT"], "one listing per run, not one per repo"
    assert created == ["assignment-1-bob-b"], "a listed repo was regenerated"


def test_a_failed_listing_falls_back_to_probing_each_repo(tmp_path, monkeypatch):
    # The listing is an optimisation. A rate limit on it must not stop the students who
    # onboarded this hour from getting their repos.
    probed: list[str] = []
    monkeypatch.setattr(
        assign, "repo_exists", lambda org, name: probed.append(name) or False
    )
    listed, created = _listing_run(
        tmp_path, monkeypatch, RuntimeError("could not list repos in COHORT: 502")
    )
    assert listed == ["COHORT"]
    assert created == ["assignment-1", "assignment-1-ada-l", "assignment-1-bob-b"]
    assert probed == ["assignment-1", "assignment-1-ada-l", "assignment-1-bob-b"]


def test_a_cohort_template_the_listing_shows_ready_is_left_alone(monkeypatch):
    # The repair is three writes per handed-out assignment; running it on a template the
    # listing already shows as frozen, flagged and topiced re-wrote correct state hourly.
    monkeypatch.setattr(
        assign,
        "_wait_for_content",
        lambda *a: pytest.fail("the hourly re-probe is back"),
    )
    monkeypatch.setattr(
        assign,
        "gh",
        lambda *a, **k: pytest.fail("the hourly is_template PATCH is back"),
    )
    monkeypatch.setattr(
        assign,
        "set_repo_topics",
        lambda *a: pytest.fail("the hourly topics PUT is back"),
    )
    assert (
        assign.ensure_cohort_template(
            "COURSE",
            "assignment-1-f2026",
            "COHORT",
            "assignment-1",
            [_ready_template()],
        )
        == "assignment-1"
    )


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "assignment-1", "isTemplate": False, "topics": ["assignment-1"]},
        {"name": "assignment-1", "isTemplate": True, "topics": []},
    ],
)
def test_a_half_created_cohort_template_is_still_repaired_from_the_listing(
    monkeypatch, entry
):
    # A run that timed out in _wait_for_content leaves the repo existing but unflagged or
    # untopiced. The listing must not read that as "ready" - every later handout would
    # fail with a misleading "not a template", or the site would withhold the brief.
    monkeypatch.setattr(assign, "_wait_for_content", lambda org, name: True)
    monkeypatch.setattr(assign, "withhold_from_template", lambda *a: True)
    patched: list[tuple] = []
    monkeypatch.setattr(assign, "gh", lambda *a, **k: patched.append(a) or (0, ""))
    stamped: list[tuple] = []
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a: stamped.append(a) or True)
    assert (
        assign.ensure_cohort_template(
            "COURSE", "assignment-1-f2026", "COHORT", "assignment-1", [entry]
        )
        == "assignment-1"
    )
    assert any("is_template=true" in a for a in patched)
    assert stamped == [
        ("COHORT", "assignment-1", ["assignment-1", "assignment-template"])
    ]


def test_wait_for_content_does_not_sleep_after_its_last_poll(monkeypatch):
    # The delay is there to space the polls out. Sleeping after the final failed one only
    # adds `delay` to a wait that has already given up.
    slept: list[float] = []
    monkeypatch.setattr(assign.time, "sleep", lambda d: slept.append(d))
    monkeypatch.setattr(assign, "gh", lambda *a, **k: (0, "0"))
    assert assign._wait_for_content("COHORT", "a1", attempts=3, delay=1.5) is False
    assert slept == [1.5, 1.5]


def test_a_solution_holding_a_symlink_still_pushes(tmp_path, monkeypatch):
    # A plain copytree FOLLOWS links: one pointing at nothing raises and one pointing at
    # its own parent recurses, so a single such file aborted the handout - hourly.
    sol = tmp_path / "solution"
    sol.mkdir()
    (sol / "answers.md").write_text("the answers")
    (sol / "stale.md").symlink_to("nowhere.md")
    monkeypatch.setattr(
        assign,
        "clone",
        lambda org, repo, dest, branch=None: Path(dest).mkdir(parents=True) or True,
    )
    monkeypatch.setattr(assign, "git", lambda *a, **k: (0, ""))
    assert assign.push_solution("COHORT", "a1-ada", sol) is True


# ---------------------------------------------------------------- the Feedback issue


def _provision_one_env(monkeypatch):
    monkeypatch.setattr(assign, "generate_from_template", lambda **k: True)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(assign, "grant_faculty", lambda *a, **k: True)
    monkeypatch.setattr(assign, "add_collaborator", lambda *a, **k: True)


def test_a_new_submission_repo_gets_its_feedback_issue(monkeypatch, feedback_issues):
    # It is where every receipt and, eventually, the grade appears, so the student is told
    # at handout where to look rather than being surprised by a comment weeks later.
    _provision_one_env(monkeypatch)
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        existing={},
        feedback_body="BODY",
    )
    assert feedback_issues == [("assignment-1-ada-l", "BODY")]


def test_an_existing_repo_is_never_probed_for_its_feedback_issue(
    monkeypatch, feedback_issues
):
    # The scheduler re-runs every handed-out release on every hourly tick. Probing here
    # would be one issue listing per student per tick for the rest of the term, for an
    # issue that does not go away - and the refresh pass opens a missing one lazily.
    _provision_one_env(monkeypatch)
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        touch_existing=True,
        existing={"assignment-1-ada-l": {"name": "assignment-1-ada-l", "topics": []}},
        feedback_body="BODY",
    )
    assert feedback_issues == []


def test_an_existing_submission_repo_missing_its_topic_is_retagged(monkeypatch):
    # The stamp is a separate PUT after the create, so a repo whose PUT failed - or that
    # predates the topic - stayed untagged until the nightly sweep. Untagged means
    # discovery reads it as a cohort repo, and its NAME carries a student's handle.
    tagged = []
    _provision_one_env(monkeypatch)
    monkeypatch.setattr(
        assign, "set_repo_topics", lambda o, r, t, **k: tagged.append((r, t)) or True
    )
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        touch_existing=False,
        existing={
            "assignment-1-ada-l": {"name": "assignment-1-ada-l", "topics": ["keep-me"]}
        },
    )
    # additive: the PUT replaces the whole list, so what the repo already carried is
    # written back with what was missing
    assert tagged == [("assignment-1-ada-l", ["assignment-1", "keep-me", "submission"])]


def test_an_already_tagged_submission_repo_costs_no_call(monkeypatch):
    # The listing carries `topics`, so the common case - every repo tagged - is free. A
    # PUT per student per hourly tick for the rest of the term would not be.
    tagged = []
    _provision_one_env(monkeypatch)
    monkeypatch.setattr(
        assign, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        touch_existing=False,
        existing={
            "assignment-1-ada-l": {
                "name": "assignment-1-ada-l",
                "topics": ["assignment-1", "submission"],
            }
        },
    )
    assert tagged == []


def test_an_archived_submission_repo_is_left_frozen(monkeypatch):
    # A finished semester's cohort is archived, and an archived repo is read-only: the PUT
    # 403s. The scheduler re-runs every handed-out release on every tick, so an untagged
    # archived repo would buy a failed write and a public error line every hour, forever.
    tagged = []
    _provision_one_env(monkeypatch)
    monkeypatch.setattr(
        assign, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        touch_existing=False,
        existing={
            "assignment-1-ada-l": {
                "name": "assignment-1-ada-l",
                "topics": [],
                "archived": True,
            }
        },
    )
    assert tagged == []


def test_a_failed_submission_tag_is_reported_with_its_consequence(monkeypatch, capsys):
    # The return used to be discarded. The consequence - the repo NAME, which carries a
    # handle, is a candidate for the public landing page - is what a reader needs, and the
    # line itself names nobody.
    _provision_one_env(monkeypatch)
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(assign, "set_repo_topics", lambda *a, **k: False)
    assign.provision_one(
        "COURSE",
        "assignment-1",
        "COHORT",
        "assignment-1-ada-l",
        ["ada-l"],
        "assignment-1",
        existing={},
    )
    captured = capsys.readouterr()
    assert "carries no `submission` topic" in captured.err
    assert "landing page" in captured.err
    assert "ada-l" not in captured.out + captured.err


def test_the_handout_composes_one_feedback_body_per_team(
    tmp_path, monkeypatch, feedback_issues
):
    # A team's body names its members, so it cannot be composed once for the assignment.
    path = _roster_file(
        tmp_path,
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc",
        "ben@uni.edu,Ben,enrolled,ben-k,43,dsl-def",
    )
    monkeypatch.setattr(
        assign.teams,
        "load",
        lambda org: {"assignment-1": {"alpha": ["ada-l"], "beta": ["ben-k"]}},
    )
    monkeypatch.setattr(
        assign.sync_teams,
        "vet_groups",
        lambda groups, participants: [
            (team, members, []) for team, members in groups.items()
        ],
    )
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    bodies: list[str] = []
    monkeypatch.setattr(
        assign,
        "provision_one",
        lambda *a, **k: bodies.append(k["feedback_body"]) or "ok",
    )
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=True
    )

    assert ["@ada-l" in b for b in bodies] == [True, False]
    assert ["@ben-k" in b for b in bodies] == [False, True]


def test_the_handout_refreshes_the_team_formation_lock(tmp_path, monkeypatch):
    # A handout is the last moment the schedule and the template's definition can have
    # moved before students are looking at the assignment, and the Join-team form cannot
    # read either one. The lock is written with the schedule this run already loaded.
    sched = Schedule()
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: sched)
    locked: list[tuple] = []
    monkeypatch.setattr(
        assign.grades,
        "write_team_lock",
        lambda cohort_org, course_org, sched: (
            locked.append((course_org, cohort_org, sched)) or True
        ),
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "ok")
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )
    assert locked == [("COURSE", "COHORT", sched)]


def test_a_tick_that_handed_nothing_out_does_not_rewrite_the_lock(
    tmp_path, monkeypatch
):
    # `due_releases` is cumulative, so the quarter-hourly scheduler re-fires every
    # handed-out assignment for the rest of the term. A pass whose every repo was skipped
    # handed nothing out, and nothing it mirrors can have moved with it - writing anyway
    # costs one contents read per assignment per tick to find the file unchanged.
    monkeypatch.setattr("dsl_course.schedule.load", lambda org: Schedule())
    locked: list[tuple] = []
    monkeypatch.setattr(
        assign.grades,
        "write_team_lock",
        lambda **kw: locked.append(kw) or True,
    )
    path = _roster_file(tmp_path, "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc")
    monkeypatch.setattr(
        assign, "ensure_cohort_template", lambda *a, **k: "assignment-1"
    )
    monkeypatch.setattr(assign, "provision_one", lambda *a, **k: "skipped")
    monkeypatch.setattr("dsl_course.schedule.record_handout", lambda *a, **k: None)
    monkeypatch.setattr("dsl_course.site.sync_site", lambda *a, **k: None)

    assign.provision_all(
        "COURSE", "assignment-1-f2026", "COHORT", roster_path=path, group=False
    )
    assert locked == []


# ------------------------------- patching an assignment that is already in student hands

FIXED = b"print('fixed')\n"
AS_HANDED_OUT = b"print('broken')\n"


def _cohort(monkeypatch, live: dict[str, dict[str, bytes]], corrected=None):
    """A cohort holding one frozen hand-out and three submission repos.

    `live` is `{repo: {path: content}}` - what each repo has right now, which is how the
    "did the student change this?" question is answered. Returns the commits the run makes.
    """
    corrected = {"starter.py": FIXED} if corrected is None else corrected
    commits: list[tuple[str, dict[str, bytes]]] = []

    monkeypatch.setattr(assign, "template_files", lambda org, tmpl, path: corrected)
    monkeypatch.setattr(assign, "default_branch", lambda org, repo, **k: "main")
    monkeypatch.setattr(
        assign,
        "repo_blob_shas",
        lambda org, repo, branch: {
            path: assign.blob_sha(body) for path, body in live[repo].items()
        },
    )
    monkeypatch.setattr(
        assign,
        "list_org_repos",
        lambda org: [
            {"name": "assignment-1", "isTemplate": True, "archived": False},
            *(
                {"name": name, "isTemplate": False, "archived": False}
                for name in live
                if name != "assignment-1"
            ),
        ],
    )

    def fake_put_files(org, repo, files, message, *, person=False, **k):
        commits.append((repo, files))
        live[repo].update(files)
        return True

    monkeypatch.setattr(assign, "put_files", fake_put_files)
    monkeypatch.setattr(
        assign.grades, "find_feedback_issue", lambda org, repo: (7, "open")
    )
    monkeypatch.setattr(
        assign.grades, "post_marked_comment", lambda *a, **k: notes.append(a) or True
    )
    return commits


notes: list = []


@pytest.fixture(autouse=True)
def _clear_notes():
    notes.clear()


def _run(**kw):
    return assign.patch_released(
        "COURSE", "assignment-1-f2026", "COHORT", "starter.py", **kw
    )


def test_the_correction_reaches_every_untouched_submission_repo(monkeypatch):
    commits = _cohort(
        monkeypatch,
        {
            "assignment-1": {"starter.py": AS_HANDED_OUT},
            "assignment-1-ada": {"starter.py": AS_HANDED_OUT},
            "assignment-1-bob": {"starter.py": AS_HANDED_OUT},
        },
    )
    assert _run(dry_run=False) == 0
    # The frozen hand-out is patched too, or tomorrow's onboarder gets the broken file.
    assert sorted(repo for repo, _ in commits) == [
        "assignment-1",
        "assignment-1-ada",
        "assignment-1-bob",
    ]
    assert all(files == {"starter.py": FIXED} for _, files in commits)


def test_a_file_the_student_has_changed_is_kept_unless_overwrite_says_otherwise(
    monkeypatch,
):
    live = {
        "assignment-1": {"starter.py": AS_HANDED_OUT},
        "assignment-1-ada": {"starter.py": b"print('my own work')\n"},
    }
    commits = _cohort(monkeypatch, live)
    assert _run(dry_run=False) == 0
    assert [repo for repo, _ in commits] == ["assignment-1"]
    assert live["assignment-1-ada"]["starter.py"] == b"print('my own work')\n"


def test_overwrite_replaces_the_students_own_version(monkeypatch):
    live = {
        "assignment-1": {"starter.py": AS_HANDED_OUT},
        "assignment-1-ada": {"starter.py": b"print('my own work')\n"},
    }
    _cohort(monkeypatch, live)
    assert _run(dry_run=False, overwrite=True) == 0
    assert live["assignment-1-ada"]["starter.py"] == FIXED


def test_a_repo_already_carrying_the_correction_costs_no_commit(monkeypatch):
    commits = _cohort(
        monkeypatch,
        {
            "assignment-1": {"starter.py": FIXED},
            "assignment-1-ada": {"starter.py": FIXED},
        },
    )
    assert _run(dry_run=False) == 0
    assert commits == []
    assert notes == []  # and says nothing a second time


def test_each_patched_repo_gets_one_note_on_its_feedback_issue(monkeypatch):
    _cohort(
        monkeypatch,
        {
            "assignment-1": {"starter.py": AS_HANDED_OUT},
            "assignment-1-ada": {"starter.py": AS_HANDED_OUT},
        },
    )
    assert _run(dry_run=False) == 0
    assert len(notes) == 1
    org, repo, issue, body, marker = notes[0]
    assert (org, repo, issue) == ("COHORT", "assignment-1-ada", 7)
    assert "`starter.py`" in body and "pull before you continue" in body
    assert marker.startswith("<!-- dsl-patch:")


def test_a_dry_run_writes_nothing_and_says_nothing(monkeypatch):
    commits = _cohort(
        monkeypatch,
        {
            "assignment-1": {"starter.py": AS_HANDED_OUT},
            "assignment-1-ada": {"starter.py": AS_HANDED_OUT},
        },
    )
    assert _run(dry_run=True) == 0
    assert commits == [] and notes == []


def test_the_public_log_never_names_a_submission_repo(monkeypatch, capsys):
    _cohort(
        monkeypatch,
        {
            "assignment-1": {"starter.py": AS_HANDED_OUT},
            "assignment-1-ada": {"starter.py": AS_HANDED_OUT},
        },
    )
    _run(dry_run=False)
    out = capsys.readouterr().out
    assert "assignment-1-ada" not in out


def test_a_path_the_template_has_not_got_is_refused(monkeypatch, capsys):
    _cohort(monkeypatch, {"assignment-1": {}}, corrected={})
    assert _run(dry_run=False) == 1
    assert "nothing to patch" in capsys.readouterr().err


def test_the_note_reads_as_a_sentence_however_many_files_it_names():
    one = assign.patch_note(["starter.ipynb"], date(2026, 10, 14))
    assert one == (
        "The teaching team updated `starter.ipynb` in this repository on 2026-10-14; "
        "pull before you continue. Your own commits are untouched."
    )
    several = assign.patch_note(["b.py", "a.py"], date(2026, 10, 14))
    assert several == (
        "The teaching team updated 2 files - `a.py`, `b.py` - in this repository on "
        "2026-10-14; pull before you continue. Your own commits are untouched."
    )


def test_the_marker_changes_when_the_correction_does():
    first = assign.patch_marker({"starter.py": AS_HANDED_OUT})
    assert first != assign.patch_marker({"starter.py": FIXED})
    assert first == assign.patch_marker({"starter.py": AS_HANDED_OUT})
