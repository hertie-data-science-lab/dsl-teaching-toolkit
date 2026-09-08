"""grades pure core -- the CSV -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from dsl_course import gh_contents, ghcli, grades, repos, roster
from dsl_course.schedule import AssignmentEntry, Schedule
from tests.conftest import ROSTER_HEADER

# ------------------------------------------------------ provisioning the gradebooks


def test_ensure_gradebooks_skips_auditors(monkeypatch, capsys):
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
    assert grades.ensure_gradebooks("COHORT", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "grades-ada-l" in out and "grades-bob-b" in out
    assert "eve-e" not in out
    assert "Syncing 2 gradebook repo(s)" in out
    assert "1 auditor row(s) skipped" in out


def test_ensure_gradebooks_names_no_student_in_a_public_log(monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    assert grades.ensure_gradebooks("COHORT", dry_run=True) == 0
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
        "branch-404",  # the gradebook repo is not there at all
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
    if break_it == "branch-404":
        # The one that happened: `grades-<handle>` was never provisioned, GET /repos 404s,
        # and `default_branch` raises NAMING the repo. Stubbed at the process boundary so
        # the real repos.default_branch runs and writes its own message.
        monkeypatch.setattr(
            repos, "gh", lambda *a, **k: (1, '{"message":"Not Found"} (HTTP 404)')
        )
    elif break_it == "tree":
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


def test_no_topic_stamp_or_offboarding_failure_names_a_student_repo(
    monkeypatch, capsys
):
    # The other five repos.py failure lines that carry `{org}/{repo}` on a path a student
    # repo reaches: the topic stamp both sweeps make, and the four calls that take a
    # vanished handle's access away.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.set_repo_topics(
        "COHORT", "grades-ada-l", ["gradebook"], person=True
    )
    assert (
        repos.is_collaborator("COHORT", "assignment-1-ada-l", "ada-l", person=True)
        is None
    )
    assert not repos.remove_collaborator(
        "COHORT", "assignment-1-ada-l", "ada-l", person=True
    )
    assert (
        repos.pending_invitations("COHORT", "assignment-1-ada-l", "ada-l", person=True)
        is None
    )
    assert not repos.cancel_invitation(
        "COHORT", "assignment-1-ada-l", "777", person=True
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err
    assert captured.err.count("COHORT") == 5  # each fault, and where to look


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
    # Read, not write: `distribute` rewrites grades.yml from the grading sheet, so a mark
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


def test_an_existing_gradebook_missing_its_topic_is_retagged(monkeypatch):
    # The stamp is a separate PUT after the create, so a gradebook whose PUT failed - or
    # that predates the topic - stayed untagged until the nightly sweep. Untagged means
    # discovery reads `grades-<handle>` as a cohort repo, handle and all.
    tagged = []
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        grades, "set_repo_topics", lambda o, r, t, **k: tagged.append((r, t)) or True
    )
    grades.provision_one(
        "COHORT",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": ["keep-me"]}},
    )
    # additive: the PUT replaces the whole list, so what it already carried comes back
    assert tagged == [("grades-ada-l", ["gradebook", "keep-me"])]


def test_an_already_tagged_gradebook_costs_no_call(monkeypatch):
    # The listing carries `topics`, so the common case - every gradebook tagged - is free.
    tagged = []
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        grades, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    grades.provision_one(
        "COHORT",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": ["gradebook"]}},
    )
    assert tagged == []


def test_an_archived_gradebook_is_left_frozen(monkeypatch):
    # An archived repo is read-only, so the PUT 403s, and a finished cohort is meant to
    # stay frozen. `access.converge_topics` passes over archived repos for the same reason.
    tagged = []
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        grades, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    grades.provision_one(
        "COHORT",
        "ada-l",
        existing={
            "grades-ada-l": {"name": "grades-ada-l", "topics": [], "archived": True}
        },
    )
    assert tagged == []


def test_a_failed_gradebook_tag_is_reported_with_its_consequence(monkeypatch, capsys):
    # The return used to be discarded. The consequence - `grades-<handle>` is a candidate
    # for the public landing page - is what a reader needs, and the line names nobody.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: False)
    grades.provision_one(
        "COHORT",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": []}},
    )
    captured = capsys.readouterr()
    assert "carries no `gradebook` topic" in captured.err
    assert "landing page" in captured.err
    assert "ada-l" not in captured.out + captured.err


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


def _ensure_run(monkeypatch, listing, handles=("ada-l", "bob-b")):
    """`ensure_gradebooks` over `handles`, with `listing` (or an Exception) standing in
    for the org listing. Returns (the orgs listed, the gradebooks created)."""
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
    assert grades.ensure_gradebooks("COHORT") == 0
    return listed, created


def test_ensure_gradebooks_lists_the_org_once_and_probes_no_gradebook(monkeypatch):
    # A repo_exists per student cost a GET per student on every nightly sync, to ask what
    # one paginated listing already answers for the whole cohort.
    monkeypatch.setattr(
        grades,
        "repo_exists",
        lambda *a, **k: pytest.fail("a per-repo probe is back in the hot path"),
    )
    listed, created = _ensure_run(monkeypatch, [{"name": "grades-ada-l", "topics": []}])
    assert listed == ["COHORT"], "one listing per run, not one per student"
    assert created == ["grades-bob-b"], "a listed gradebook was recreated"


def test_a_failed_listing_falls_back_to_probing_each_gradebook(monkeypatch):
    # The listing is an optimisation. A rate limit on it must not leave a student who
    # onboarded today without a gradebook.
    probed: list[str] = []
    monkeypatch.setattr(
        grades, "repo_exists", lambda org, repo: probed.append(repo) or False
    )
    listed, created = _ensure_run(
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
    assert grades.ensure_gradebooks("COHORT", dry_run=True) == 0


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

    def fake_put_files(
        org, repo, files, message, *, delete=(), create_only=False, person=False
    ):
        target = "config" if repo == grades.CONFIG_REPO else "gradebooks"
        if target == "gradebooks":
            # The repo is named after the student, so the write has to be marked as one:
            # without it, a gradebook GitHub could not read published its own name.
            assert person, "a gradebook write must be a person write"
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


def test_the_done_line_is_the_spec_counts_in_the_spec_order(
    tmp_path, monkeypatch, capsys
):
    # `Done - {...}` is a JSON dump of the counts dict, so the dict's key order is what a
    # grader reads. Nothing asserted it, and it had drifted out of the order the spec
    # prints. `unknown` is the one key the spec does not name - marks for handles nobody
    # enrolled - and it is kept.
    _distribute(monkeypatch, tmp_path)
    done = next(
        line for line in capsys.readouterr().out.splitlines() if "Done - " in line
    )
    counts = json.loads(done.split("Done - ", 1)[1])
    assert list(counts) == [
        "comments",
        "gradebooks",
        "emails",
        "skipped",
        "held",
        "unknown",
        "failed",
    ]


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


def test_a_cohort_with_no_sheet_yet_distributes_nothing(tmp_path, monkeypatch, capsys):
    # The sheet is the only source of marks, so an empty folder is "nothing has been
    # handed out yet" - reported, and nothing written, rather than an empty run.
    out = _distribute(monkeypatch, tmp_path, sheets={})
    assert out["rc"] == 1
    assert (out["comments"], out["gradebooks"], out["config"]) == ([], [], [])
    assert f"no {grades.SHEETS_DIR}/ in COHORT" in capsys.readouterr().err


def test_a_slug_no_sheet_matches_distributes_nothing(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-9")
    assert out["rc"] == 1
    assert (out["comments"], out["gradebooks"], out["config"]) == ([], [], [])
    assert "no grading sheet for `assignment-9`" in capsys.readouterr().err


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


def test_the_dead_per_student_yaml_goes_whether_or_not_this_is_the_migration(
    tmp_path, monkeypatch
):
    # A cohort that reached `distributed.csv` without ever having had a `notified.csv` was
    # never on the migration path, so its `gradebook/*.yml` was left in place for the rest
    # of the term - a stale copy of a grade beside the repo that holds the real one.
    out = _distribute(
        monkeypatch,
        tmp_path,
        stale_gradebooks=("ada-l.yml", "bo-b.yml"),
    )
    ((_cfg, files, delete),) = out["config"]
    assert set(delete) == {
        f"{grades.GRADEBOOK_DIR}/ada-l.yml",
        f"{grades.GRADEBOOK_DIR}/bo-b.yml",
    }
    # `distributed.csv` lives in the same folder and is the one file still read there.
    assert grades.DISTRIBUTED_PATH in files


def test_a_missing_submission_repo_is_a_counted_skip(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, issue=None)
    assert out["comments"] == []
    assert out["rc"] == 0  # a student who never onboarded is not a failure
    assert '"skipped": 1' in capsys.readouterr().out


# A handle no roster row claims. A sheet is hand-typed, and the demo org carried six of
# these: `ensure_gradebooks` had provisioned nothing for them, so every one was a 404
# naming `grades-<handle>` in a PUBLIC log.
_UNKNOWN_IN_THE_SHEET = (
    _SHEET
    + """\
  zed-z:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    score_individual: 30
"""
)
# A second assignment nobody on the roster is in, so two unknown marks are counted.
_ONLY_A_STRANGER = _SHEET.replace("ada-l:", "mallory-m:")


def test_marks_for_handles_not_on_the_roster_are_dropped_not_pushed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": _UNKNOWN_IN_THE_SHEET,
            "assignment-2": _ONLY_A_STRANGER,
        },
    )
    assert out["rc"] == 0
    # One gradebook, for the one student the roster has - not three.
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]
    assert [repo for repo, _b, _m in out["comments"]] == ["assignment-1-ada-l"]
    printed = capsys.readouterr()
    assert '"unknown": 2' in printed.out
    for stranger in ("zed-z", "mallory-m"):
        assert stranger not in printed.out + printed.err


def test_the_dry_run_says_how_many_marks_no_roster_row_claims(
    tmp_path, monkeypatch, capsys
):
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": _UNKNOWN_IN_THE_SHEET,
            "assignment-2": _ONLY_A_STRANGER,
        },
        dry_run=True,
    )
    assert (
        "2 mark(s) for handles not on the roster - ignored" in capsys.readouterr().out
    )


def test_the_verbose_log_still_says_which_handles_were_dropped(
    tmp_path, monkeypatch, capsys
):
    # Moved, not thrown away: a grader who typed a handle wrong has to be able to find it.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET})
    assert "zed-z is not an onboarded student" in capsys.readouterr().out


def test_an_unreadable_roster_drops_nobody(tmp_path, monkeypatch, capsys):
    # None means the file could not be READ. Reading it as "nobody is enrolled" would
    # withhold the whole cohort's grades on a transient failure.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET},
        roster_rows=None,
    )
    assert sorted(repo for repo, _f, _d in out["gradebooks"]) == [
        "grades-ada-l",
        "grades-zed-z",
    ]
    assert '"unknown": 0' in capsys.readouterr().out


def test_an_auditor_with_a_mark_typed_in_gets_no_gradebook(tmp_path, monkeypatch):
    # Auditors are never assessed and `ensure_gradebooks` makes them no repo, so a mark
    # typed against one has nowhere to go.
    out = _distribute(
        monkeypatch,
        tmp_path,
        roster_rows=ROSTER_ADA + "zed@uni.edu,Zed,auditor,zed-z,43,dsl-zzz\n",
        sheets={"assignment-1": _UNKNOWN_IN_THE_SHEET},
    )
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


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


def test_a_mark_no_roster_row_claims_is_counted_not_fatal(
    tmp_path, monkeypatch, capsys
):
    # The sheet marks ada-l and the roster has never heard of them: counted and dropped,
    # rather than pushed at a `grades-ada-l` that was never provisioned.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch, tmp_path, roster_rows="\nbo@uni.edu,Bo,enrolled,bo-b,7,dsl-x\n"
    )
    assert out["rc"] == 0
    assert out["gradebooks"] == [] and out["outbox"] == []
    printed = capsys.readouterr()
    assert '"unknown": 1' in printed.out
    assert "ada-l" not in printed.out + printed.err


def test_a_roster_row_with_no_email_is_counted_not_fatal(tmp_path, monkeypatch, capsys):
    # On the roster, so the grade is distributed - but there is no address to tell them at,
    # which is an ordinary state (a withdrawn student) and must not red every run from here
    # on.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    out = _distribute(
        monkeypatch, tmp_path, roster_rows="\n,Ada,enrolled,ada-l,42,dsl-abc\n"
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


# ------------------------------------------------- the team-formation lock file


def _sched(**assignments) -> Schedule:
    """A schedule of assignments whose only interesting field is which template they hand
    out from - the lock file reads nothing else off it."""
    return Schedule(
        assignments={
            key: AssignmentEntry(
                due_datetime=datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc),
                course_source_repo=template,
            )
            for key, template in assignments.items()
        }
    )


def _lock(
    monkeypatch, sched, configs: dict[str, str | None], defaults: str = ""
) -> str:
    """Render the lock file for `sched`, with each template's `grading_config.yml` given
    as text (None = the template has none) and the course's own defaults block as YAML."""
    monkeypatch.setattr(
        grades, "_grading_text", lambda org, template: configs.get(template)
    )
    monkeypatch.setattr(
        grades, "org_meta", lambda org: yaml.safe_load(defaults or "{}") or {}
    )
    return grades.team_lock_text(grades.team_lock_entries("COURSE", sched))


def test_the_lock_file_carries_two_scalars_per_schedule_assignment(monkeypatch):
    # The Join-team form runs in a PUBLIC repo under a token deliberately scoped away from
    # the course org's templates, so it cannot read grading_config.yml. This file is the
    # mirror it reads instead, and it must answer for every assignment in the schedule.
    text = _lock(
        monkeypatch,
        _sched(a1="assignment-1-f2026", project="assignment-4-project-f2026"),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": (
                "type: group\nteam_formation: self_select\nmax_team_size: 3\n"
            ),
        },
    )
    assert yaml.safe_load(text) == {
        "assignments": {
            "a1": {"team_formation": "none", "max_team_size": 5},
            "project": {"team_formation": "self_select", "max_team_size": 3},
        }
    }
    # SYSTEM-OWNED, stamped at the write site - it is rewritten on every sync.
    assert text.startswith("# SYSTEM-OWNED")


def test_an_assigned_group_assignment_is_locked_as_assigned(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\nteam_formation: assigned\n"},
    )
    assert (
        yaml.safe_load(text)["assignments"]["project"]["team_formation"] == "assigned"
    )


def test_a_template_that_does_not_exist_yet_is_locked_to_none(monkeypatch, capsys):
    # Maths a2-a4 today: the schedule names a template nobody has created. Guessing
    # `group` would let any student mint a real GitHub team, granted `maintain` on the
    # repo, under a name of their choosing - so the lock refuses and says what that costs.
    text = _lock(
        monkeypatch, _sched(a2="assignment-2-f2026"), {"assignment-2-f2026": None}
    )
    assert yaml.safe_load(text)["assignments"]["a2"]["team_formation"] == "none"
    err = capsys.readouterr().err
    assert "no grading_config.yml yet" in err and "no team can be formed" in err


def test_the_course_default_cap_fills_in_for_an_assignment_that_names_none(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\n"},
        defaults="assignment_defaults:\n  max_team_size: 4\n",
    )
    assert yaml.safe_load(text)["assignments"]["project"]["max_team_size"] == 4


def test_a_cohort_with_no_assignments_still_gets_a_readable_lock_file(monkeypatch):
    # The form refuses every slug rather than 404ing on the read and reporting a fault.
    text = _lock(monkeypatch, _sched(), {})
    assert yaml.safe_load(text) == {"assignments": {}}


def test_the_lock_file_is_written_once_and_is_free_when_nothing_changed(monkeypatch):
    # put_file blob-compares, so writing it from the membership sync, the handout and the
    # nightly refresh costs a read apiece and no commit at all on an unchanged cohort.
    written: list[tuple[str, str, bytes]] = []
    monkeypatch.setattr(grades, "_grading_text", lambda org, t: "type: group\n")
    monkeypatch.setattr(grades, "org_meta", lambda org: {})
    monkeypatch.setattr(grades, "repo_is_archived", lambda org, repo: False)
    monkeypatch.setattr(
        grades,
        "put_file",
        lambda org, repo, path, content, msg: (
            written.append((org, path, content)) or True
        ),
    )
    assert grades.write_team_lock(
        "COURSE", "COHORT", _sched(project="assignment-4-project-f2026")
    )
    ((org, path, content),) = written
    assert (org, path) == ("COHORT", "assignments.lock.yml")
    assert yaml.safe_load(content)["assignments"]["project"] == {
        "team_formation": "self_select",
        "max_team_size": 5,
    }


def test_a_lock_file_that_could_not_be_written_says_what_that_costs(
    monkeypatch, capsys
):
    monkeypatch.setattr(grades, "_grading_text", lambda org, t: "type: group\n")
    monkeypatch.setattr(grades, "org_meta", lambda org: {})
    monkeypatch.setattr(grades, "repo_is_archived", lambda org, repo: False)
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: False)
    assert not grades.write_team_lock("COURSE", "COHORT", _sched(p="t"))
    assert "the Join-team form reads it" in capsys.readouterr().err


def test_a_closed_out_cohort_is_left_alone(monkeypatch, capsys):
    # `teardown` archives classroom-config last, and an archived repo is read-only. The
    # membership sync fans out over the cohort REGISTRY, which teardown does not touch, so
    # without this every finished cohort would 403 the daily sync red for good.
    def boom(*args, **kwargs):
        raise AssertionError("a sealed classroom-config takes no write")

    monkeypatch.setattr(grades, "repo_is_archived", lambda org, repo: True)
    monkeypatch.setattr(grades, "put_file", boom)
    monkeypatch.setattr(grades, "_grading_text", boom)
    assert grades.write_team_lock("COURSE", "COHORT", _sched(p="t"))
    assert "cohort closed out" in capsys.readouterr().out


def test_no_log_line_from_the_lock_file_names_a_person(monkeypatch, capsys):
    # It runs in the course org's PUBLIC .github. Only slugs and template names may appear.
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    _lock(monkeypatch, _sched(a2="assignment-2-f2026"), {"assignment-2-f2026": None})
    printed = capsys.readouterr()
    assert "@" not in printed.out + printed.err
