"""grades pure core -- the CSV -> per-student gradebook pivot is the bit that must be
right (a wrong row silently emails a student someone else's mark). The gh/git fan-out is
deliberately not mocked, per the testing strategy. No network here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import pytest
import yaml

from dsl_course import (
    course,
    gh_contents,
    ghcli,
    grades,
    issues,
    policy,
    repos,
    roster,
    settings,
)
from dsl_course.schedule import AssignmentEntry, Schedule
from tests.conftest import ROSTER_HEADER, repo_row

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
    assert grades.ensure_gradebooks("SEMESTER", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "grades-ada-l" in out and "grades-bob-b" in out
    assert "eve-e" not in out
    assert "Syncing 2 gradebook repo(s)" in out
    assert "1 auditor row(s) skipped" in out


def test_a_semester_with_no_roster_rows_yet_is_a_skip_not_a_failure(
    monkeypatch, capsys
):
    # This runs on every nightly Sync membership. An empty roster is a freshly bootstrapped
    # semester and a missing one is a content fault the roster's own digest already reports,
    # so neither may redden a run in an org the maintainer cannot fix it in.
    monkeypatch.setattr(grades.roster, "load", lambda org: [])
    assert grades.ensure_gradebooks("SEMESTER") == 0
    monkeypatch.setattr(grades.roster, "load", lambda org: None)
    assert grades.ensure_gradebooks("SEMESTER") == 0
    assert capsys.readouterr().err == ""


def test_one_run_stops_at_its_deadline_and_says_how_many_are_left(monkeypatch, capsys):
    # Four API calls per new gradebook, on a nightly job with a 30-minute bound: a large
    # semester's FIRST night would spend all of it here, and a job that times out has
    # recorded nothing about where it got to. What has to be bounded is the WALL CLOCK, so
    # the budget is a deadline rather than a guess at how many creations fit in one.
    # Nothing is lost - the next run starts from the rest - so it stays green and counts.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    rows = "".join(f"\ns{n}@uni.edu,S{n},enrolled,s{n},{n},dsl-{n}" for n in range(5))
    monkeypatch.setattr(
        grades.roster, "load", lambda org: roster.parse(ROSTER_HEADER + rows + "\n")
    )
    made: list[str] = []
    monkeypatch.setattr(grades, "listing_by_name", lambda org: {})
    # A clock that jumps a minute per gradebook, so the third student is already past a
    # 2-minute budget. `time.monotonic` is read once before the loop and once per student.
    ticks = iter(range(0, 6000, 60))
    monkeypatch.setattr(grades.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        grades,
        "provision_one",
        lambda org, handle, existing=None: made.append(handle) or "ok",
    )
    assert grades.ensure_gradebooks("SEMESTER", budget_minutes=2) == 0
    assert made == ["s0", "s1"]
    out = capsys.readouterr().out
    assert "3 more gradebook(s) on the next run" in out
    assert "2 created here" in out  # `ok` is provision_one's word for a creation
    assert "s2" not in out  # a count, never a handle


def test_the_deadline_does_not_stop_a_run_that_is_keeping_up(monkeypatch, capsys):
    # A semester whose gradebooks all exist costs almost nothing per student, so the budget
    # must never be what decides that some of them wait for tomorrow.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    rows = "".join(f"\ns{n}@uni.edu,S{n},enrolled,s{n},{n},dsl-{n}" for n in range(200))
    monkeypatch.setattr(
        grades.roster, "load", lambda org: roster.parse(ROSTER_HEADER + rows + "\n")
    )
    made: list[str] = []
    monkeypatch.setattr(grades, "listing_by_name", lambda org: {})
    monkeypatch.setattr(
        grades,
        "provision_one",
        lambda org, handle, existing=None: made.append(handle) or "skipped",
    )
    assert grades.ensure_gradebooks("SEMESTER") == 0
    assert len(made) == 200
    assert "on the next run" not in capsys.readouterr().out


def test_ensure_gradebooks_names_no_student_in_a_public_log(monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    assert grades.ensure_gradebooks("SEMESTER", dry_run=True) == 0
    out = capsys.readouterr().out
    assert "ada-l" not in out
    assert "Syncing 1 gradebook repo(s)" in out  # the aggregate still reports


def test_gradebook_provisioning_names_nobody_on_the_happy_path(monkeypatch, capsys):
    # The sibling test above covers `sync --dry-run`, which never creates anything. This is
    # the CREATE branch, where `repo created: SEMESTER/grades-ada-l` used to reach the public
    # log. `repos.gh` is stubbed - the process boundary - so the real create_repo runs.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr("dsl_course.discovery.repo_exists", lambda org, repo: False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (0, ""))
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    assert grades.provision_one("SEMESTER", "ada-l") == "ok"
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
    # repo name lives: `could not commit to SEMESTER/grades-ada-l` on a bad day publishes the
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
        "SEMESTER",
        "grades-ada-l",
        {"grades.yml": b"x\n"},
        "grades: update",
        person=True,
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
    # Both are called once per SUBMISSION repo now (the Submission receipts issue's label, and the
    # student's own grant), so both name a `<slug>-<handle>` repo on failure.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.ensure_label(
        "SEMESTER",
        "assignment-1-ada-l",
        "dsl-feedback",
        color="ededed",
        description="d",
        person=True,
    )
    assert not repos.add_collaborator(
        "SEMESTER", "assignment-1-ada-l", "ada-l", person=True
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err
    assert captured.err.count("SEMESTER") == 2  # the fault, and where to look


def test_no_topic_stamp_or_offboarding_failure_names_a_student_repo(
    monkeypatch, capsys
):
    # The other five repos.py failure lines that carry `{org}/{repo}` on a path a student
    # repo reaches: the topic stamp both sweeps make, and the four calls that take a
    # vanished handle's access away.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.set_repo_topics(
        "SEMESTER", "grades-ada-l", ["gradebook"], person=True
    )
    assert (
        repos.is_collaborator("SEMESTER", "assignment-1-ada-l", "ada-l", person=True)
        is None
    )
    assert not repos.remove_collaborator(
        "SEMESTER", "assignment-1-ada-l", "ada-l", person=True
    )
    assert (
        repos.pending_invitations(
            "SEMESTER", "assignment-1-ada-l", "ada-l", person=True
        )
        is None
    )
    assert not repos.cancel_invitation(
        "SEMESTER", "assignment-1-ada-l", "777", person=True
    )
    captured = capsys.readouterr()
    assert "ada-l" not in captured.out + captured.err
    assert captured.err.count("SEMESTER") == 5  # each fault, and where to look


def test_the_verbose_log_still_says_which_repo_it_was(monkeypatch, capsys):
    # The name is not thrown away, it is moved: a maintainer running the CLI locally with
    # DSL_VERBOSE=1 still gets the repo, and so does the private semester-config archive.
    monkeypatch.setenv("DSL_VERBOSE", "1")
    monkeypatch.setattr(repos, "gh", lambda *a, **k: (1, "boom"))
    assert not repos.add_collaborator(
        "SEMESTER", "assignment-1-ada-l", "ada-l", person=True
    )
    assert "assignment-1-ada-l" in capsys.readouterr().out


def test_a_gradebook_the_student_cannot_open_is_a_failure(monkeypatch):
    # The old "created-no-collaborator" status doesn't start with "failed", so sync's exit
    # predicate ignored it: a student with no read on their own gradebook, reported green.
    monkeypatch.setattr("dsl_course.discovery.repo_exists", lambda org, repo: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: False)
    assert grades.provision_one("SEMESTER", "ada-l").startswith("failed")


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
    monkeypatch.setattr(
        "dsl_course.discovery.repo_exists", lambda org, repo: repo in exists
    )
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: faculty.append(a))
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(grades, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    grades.provision_one("SEMESTER", "ada-l")
    assert faculty == [], "the existing gradebook was re-granted"
    grades.provision_one("SEMESTER", "bob-b")
    assert faculty == [("SEMESTER", "grades-bob-b", grades.FACULTY_READ_ACCESS)]


def test_an_existing_gradebook_missing_its_topic_is_retagged(monkeypatch):
    # The stamp is a separate PUT after the create, so a gradebook whose PUT failed - or
    # that predates the topic - stayed untagged until the nightly sweep. Untagged means
    # discovery reads `grades-<handle>` as a semester repo, handle and all.
    tagged = []
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        grades, "set_repo_topics", lambda o, r, t, **k: tagged.append((r, t)) or True
    )
    grades.provision_one(
        "SEMESTER",
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
        "SEMESTER",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": ["gradebook"]}},
    )
    assert tagged == []


def test_an_archived_gradebook_is_left_frozen(monkeypatch):
    # An archived repo is read-only, so the PUT 403s, and a finished semester is meant to
    # stay frozen. `access.converge_topics` passes over archived repos for the same reason.
    tagged = []
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        grades, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    grades.provision_one(
        "SEMESTER",
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
        "SEMESTER",
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
    monkeypatch.setattr(grades, "course_name_for_semester", lambda org: "")
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: [m[0] for m in msgs[:1]],
    )
    grades._email_updates("SEMESTER", ["ada-l", "bob-b"])
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

    monkeypatch.setattr(grades, "course_name_for_semester", lambda org: "Deep Learning")
    grades._email_updates("SEMESTER", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades for the Deep Learning course have been updated." in body
    # and in the SUBJECT - the inbox list is where a student tells two courses apart
    assert subject == "Your grades for Deep Learning have been updated"

    monkeypatch.setattr(grades, "course_name_for_semester", lambda org: "")
    grades._email_updates("SEMESTER", ["ada-l"])
    _to, subject, body = sent[-1][0]
    assert "Your grades for the course have been updated." in body
    assert subject == "Your grades have been updated"


def test_grade_notification_dry_run_carries_a_placeholder_sample(monkeypatch):
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "course_name_for_semester", lambda org: "Deep Learning")
    seen: dict = {}
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            seen.update(sample=sample) or [m[0] for m in msgs]
        ),
    )
    grades._email_updates("SEMESTER", ["ada-l"], dry_run=True)
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
    monkeypatch.setattr(grades, "course_name_for_semester", lambda org: "")
    sent: list[list] = []
    monkeypatch.setattr(
        grades.mailer,
        "send_bulk",
        lambda msgs, dry_run=False, sample=None: (
            sent.append(msgs) or [m[0] for m in msgs]
        ),
    )
    grades._email_updates("SEMESTER", ["ada-l"])  # the gradebook file's spelling
    assert sent and sent[-1][0][0] == "ada@uni.edu"


# ---------------- "nothing new to render" must mean nothing new, not a failed commit


# ------------------------------------------- ONE listing instead of a probe per gradebook


def _ensure_run(monkeypatch, listing, handles=("ada-l", "bob-b")):
    """`ensure_gradebooks` over `handles`, with `listing` (or None for one that could not
    be read) standing in for the org listing. Returns (the orgs listed, the gradebooks
    created)."""
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
        # None is `discovery.listing_by_name`'s answer when the listing could not be read.
        return None if listing is None else {r["name"]: r for r in listing}

    created: list[str] = []
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(grades, "listing_by_name", fake_listing)
    monkeypatch.setattr(
        grades, "create_repo", lambda org, repo, **k: created.append(repo) or True
    )
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    assert grades.ensure_gradebooks("SEMESTER") == 0
    return listed, created


def test_ensure_gradebooks_lists_the_org_once_and_probes_no_gradebook(monkeypatch):
    # A repo_exists per student cost a GET per student on every nightly sync, to ask what
    # one paginated listing already answers for the whole semester.
    monkeypatch.setattr(
        "dsl_course.discovery.repo_exists",
        lambda *a, **k: pytest.fail("a per-repo probe is back in the hot path"),
    )
    listed, created = _ensure_run(monkeypatch, [{"name": "grades-ada-l", "topics": []}])
    assert listed == ["SEMESTER"], "one listing per run, not one per student"
    assert created == ["grades-bob-b"], "a listed gradebook was recreated"


def test_a_failed_listing_falls_back_to_probing_each_gradebook(monkeypatch):
    # The listing is an optimisation. A rate limit on it must not leave a student who
    # onboarded today without a gradebook.
    probed: list[str] = []
    monkeypatch.setattr(
        "dsl_course.discovery.repo_exists",
        lambda org, repo: probed.append(repo) or False,
    )
    listed, created = _ensure_run(monkeypatch, None)
    assert listed == ["SEMESTER"]
    assert probed == ["grades-ada-l", "grades-bob-b"]
    assert created == ["grades-ada-l", "grades-bob-b"]


def test_a_dry_run_lists_nothing(monkeypatch):
    # Nothing is created, so nothing needs to know what exists.
    students = roster.parse(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(grades.roster, "load", lambda org: students)
    monkeypatch.setattr(
        grades, "listing_by_name", lambda org: pytest.fail("a dry run listed the org")
    )
    assert grades.ensure_gradebooks("SEMESTER", dry_run=True) == 0


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


def _split_grading(text: str | None) -> tuple[str | None, str | None]:
    """A test's old-style grading config split where the keys live now: the template's
    `grading_config.yml`, and the run settings as the semester's `assignments.yml`
    `defaults:` (decision 0009)."""
    if text is None:
        return None, None
    data = yaml.safe_load(text) or {}
    run = {k: data.pop(k) for k in settings.RUN_KEYS if k in data}
    template = yaml.safe_dump(data) if data else ""
    return template, (yaml.safe_dump({"defaults": run}) if run else None)


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


# Far enough either side of any clock a run of this suite can be on. `distribute` now
# compares the due date with the moment it runs (`_undue_marks`), so a fixed date would
# make the tests that are not about that mean one thing this term and another the next.
_DUE_AHEAD = datetime(2099, 10, 4, 23, 59, tzinfo=timezone.utc)
_DUE_PASSED = datetime(2000, 10, 4, 23, 59, tzinfo=timezone.utc)


def _schedule_with(*slugs: str, due: datetime = _DUE_PASSED) -> Schedule:
    return Schedule(
        assignments={
            slug: AssignmentEntry(
                course_source_repo=f"{slug}-f2026",
                due_datetime=due,
            )
            for slug in slugs
        },
        # `schedule.load` records the semester, which is what its assignments' run
        # settings (assignments.yml) resolve against.
        org="SEMESTER",
    )


class _ListedPrivate(dict):
    """A semester listing that answers "private" for every repo asked of it.

    What `distribute` really sees on a `github` + `private` semester - the shape every test
    here but a handful is about - without each of them having to spell a row per unit. The
    ones that ARE about the listing pass their own, and `listed=None` is the listing that
    could not be read at all."""

    def get(self, name, default=None):
        return super().get(name) or repo_row(name)


_ANY_PRIVATE = _ListedPrivate()


def _fake_issues(monkeypatch, store: list[dict]) -> None:
    """`issues`' two `gh` calls answered from `store`, one dict per issue, so a lookup by
    title, a create, an edit and a close all act on the same list."""

    def listing(*args):
        state = args[args.index("--state") + 1]
        return [
            {k: i[k] for k in ("number", "body", "title", "state")}
            for i in store
            if state == "all" or i["state"] == state.upper()
        ]

    def write(*args):
        verb, flags = args[1], dict(zip(args[2::2], args[3::2], strict=False))
        if verb == "create":
            number = len(store) + 1
            store.append(
                {
                    "number": number,
                    "title": flags["--title"],
                    "body": flags["--body"],
                    "state": "OPEN",
                    "comments": [],
                }
            )
            return 0, f"https://github.com/SEMESTER/semester-config/issues/{number}"
        issue = next(i for i in store if str(i["number"]) == args[2])
        rest = dict(zip(args[3::2], args[4::2], strict=False))
        if verb == "edit":
            issue["body"] = rest["--body"]
        elif verb == "close":
            issue["state"] = "CLOSED"
            issue["comments"].append(rest.get("--comment"))
        return 0, ""

    monkeypatch.setattr(issues, "gh_json", listing)
    monkeypatch.setattr(issues, "gh", write)


def _distribute(
    monkeypatch,
    tmp_path,
    *,
    sheets: dict[str, str] | None = None,
    grading: str = _GRADING_YML,
    distributed: str | None = None,
    notified: str | None = None,
    stale_gradebooks: tuple[str, ...] = (),
    sent: int = 1,
    notify: bool = True,
    dry_run: bool = False,
    roster_rows: str | None = ROSTER_ADA,
    issue: int | None = 7,
    found_issue: int | None = None,
    put_files_ok: bool = True,
    course_name=lambda org: "",
    listed: dict[str, dict] | None = _ANY_PRIVATE,
    due: datetime = _DUE_PASSED,
    exported: str | None = None,
    preview_issues: list[dict] | None = None,
    assignment: str | None = None,
    include_feedback: bool = False,
    intent_ok: bool = True,
    send_error: Exception | None = None,
) -> dict:
    """`distribute` over a local semester-config clone, writing to nothing.

    Returns every effect it had: the gradebook commits, the semester-config commit and
    the mail batches - which between them are every channel a mark reaches a student by -
    plus `comments` and `issues`, which are TRIPWIRES. Nothing is posted into a submission
    repo any more, so those two stay empty in every test here; `issue` and `found_issue`
    are what a lookup WOULD answer, so a run that went near a thread would show up rather
    than pass for want of a stub.

    `preview` is semester-config's issue list as GitHub would hold it after the run - pass
    `preview_issues` to start from one, or to share it between two runs. `exported` is the
    registrar export the last real run left behind."""
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
    if exported is not None:
        (cfg / grades.SEMESTER_CSV_NAME).parent.mkdir(parents=True, exist_ok=True)
        (cfg / grades.SEMESTER_CSV_NAME).write_text(exported)
    store = [] if preview_issues is None else preview_issues
    _fake_issues(monkeypatch, store)

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            copytree(cfg, Path(args[3]))
            return 0, ""
        return 0, ""

    effects: dict = {
        "comments": [],
        "gradebooks": [],
        "config": [],
        "intent": [],
        "outbox": [],
        "issues": [],
        "gradebook_calls": [],
        "preview": store,
    }
    monkeypatch.setattr(grades, "gh", fake_gh)
    monkeypatch.setattr(ghcli, "gh", fake_gh)
    monkeypatch.setattr(
        grades,
        "ensure_gradebooks",
        lambda org, dry_run=False, existing=None, **kw: (
            effects["gradebook_calls"].append(kw) or 0
        ),
    )
    # The semester listing distribute takes to check what each repo is: there, and private.
    # None is "could not be read", which is a listing nothing may be CREATED on the
    # strength of (`grades.receipts_thread_policy`).
    monkeypatch.setattr(grades, "listing_by_name", lambda org: listed)
    monkeypatch.setattr(grades, "course_org_for_semester", lambda org: "COURSE")
    template_text, semester_text = _split_grading(grading)
    monkeypatch.setattr(grades, "_grading_text", lambda org, tpl: template_text)
    monkeypatch.setattr(settings, "_assignments_text", lambda org: semester_text)
    monkeypatch.setattr(
        grades.schedule,
        "load",
        lambda org: _schedule_with(*(sheets or {"assignment-1": ""}), due=due),
    )
    monkeypatch.setattr(
        grades,
        "ensure_receipts_issue",
        lambda org, repo, body, dry_run=False, create=True: (
            (effects["issues"].append((repo, body)) or issue) if create else found_issue
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
        if target == "config" and message.startswith(grades.INTENT_MESSAGE):
            # The record of the emails about to go, committed before they are sent.
            effects["intent"].append({k: v.decode() for k, v in files.items()})
            return intent_ok
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
    monkeypatch.setattr(grades, "course_name_for_semester", course_name)

    def fake_send_bulk(msgs, dry_run=False, sample=None):
        if send_error is not None:
            raise send_error
        effects["outbox"].append(msgs)
        return [m[0] for m in msgs[:sent]]

    monkeypatch.setattr(grades.mailer, "send_bulk", fake_send_bulk)
    try:
        effects["rc"] = grades.distribute(
            "SEMESTER",
            notify=notify,
            dry_run=dry_run,
            assignment=assignment,
            include_feedback=include_feedback,
        )
    except Exception as exc:
        if send_error is None:
            raise
        effects["raised"] = exc
    return effects


def test_a_real_run_reaches_every_channel_and_posts_no_comment(tmp_path, monkeypatch):
    # THE shape of a run: three channels, and the submission repo is not one of them.
    # `found_issue` says there IS a thread in that repo, so nothing here is silent for
    # want of one - the run simply does not go near it.
    out = _distribute(monkeypatch, tmp_path, found_issue=7)
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    # ONE commit per gradebook, holding both files, so the page never disagrees with the
    # data beside it
    ((gb_repo, files, _delete),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l"
    assert set(files) == {"grades.yml", "README.md"}
    assert "student: ada-l" in files["grades.yml"]
    assert "| Assignment 1 · Neural networks | 43 |" in files["README.md"]
    # the registrar export and the record, in one semester-config commit
    ((_cfg, cfg_files, _d),) = out["config"]
    assert set(cfg_files) == {grades.SEMESTER_CSV_NAME, grades.DISTRIBUTED_PATH}
    assert "ada@uni.edu,Ada,ada-l,43" in cfg_files[grades.SEMESTER_CSV_NAME]
    # and the email
    assert [m[0] for batch in out["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_scoped_return_sends_that_assignment_beside_those_already_returned(
    tmp_path, monkeypatch
):
    # a1 comes due and is complete; a2 is marked in part and not returned; a0 went out
    # last week (the registrar export has its column). The run returns a1 only, and every
    # gradebook still shows a0: nothing already given is taken away.
    half = _SHEET.replace("score_individual: 43", "score_individual: 20")
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-0": _SHEET, "assignment-1": _SHEET, "assignment-2": half},
        exported=(
            "hertie_email,name,github_handle,assignment-0\nada@uni.edu,Ada,ada-l,43\n"
        ),
        assignment="assignment-1",
    )
    assert out["rc"] == 0
    ((_repo, files, _d),) = out["gradebooks"]
    assert (
        "assignment-0" in files["grades.yml"] and "assignment-1" in files["grades.yml"]
    )
    assert "assignment-2" not in files["grades.yml"]
    ((_cfg, cfg_files, _d2),) = out["config"]
    header = cfg_files[grades.SEMESTER_CSV_NAME].splitlines()[0]
    assert header.endswith("assignment-0,assignment-1")
    # distribute never writes a sheet, scoped or not.
    assert not [p for p in cfg_files if p.startswith(grades.SHEETS_DIR)]


_UNMARKED = _SHEET.replace("score_individual: 43", "score_individual:").replace(
    "feedback_individual: |\n      Clean derivation.\n", "feedback_individual:\n"
)


@pytest.mark.parametrize(
    ("exported", "distributed"),
    [
        # re-saved from Excel, `;`-delimited: present and unreadable
        (
            "hertie_email;name;github_handle;assignment-0\nada@uni.edu;Ada;ada-l;43\n",
            None,
        ),
        # gone while gradebooks were already written
        (
            None,
            grades.dump_distributed(
                {("ada-l", "", grades.CHANNEL_GRADEBOOK): ("x", "2026-10-01", "")}
            ),
        ),
    ],
)
def test_a_scoped_return_that_cannot_tell_what_went_out_sends_nothing(
    tmp_path, monkeypatch, exported, distributed
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-0": _SHEET, "assignment-1": _SHEET},
        exported=exported,
        distributed=distributed,
        assignment="assignment-1",
    )
    assert out["rc"] == 1
    assert out["gradebooks"] == [] and out["config"] == [] and out["outbox"] == []


def test_a_column_of_submission_facts_alone_is_not_returned(tmp_path, monkeypatch):
    # a2's sheet has only submission facts: its export column is empty, so a scoped
    # return of a1 must not send a2 along.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _SHEET.replace("43", "20")},
        exported="hertie_email,name,github_handle,assignment-2\nada@uni.edu,Ada,ada-l,\n",
        assignment="assignment-1",
    )
    assert out["rc"] == 0
    ((_repo, files, _d),) = out["gradebooks"]
    assert "assignment-2" not in files["grades.yml"]


def test_a_scoped_return_of_an_assignment_with_no_sheet_sends_nothing(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, assignment="nope")
    assert out["rc"] == 1 and out["gradebooks"] == [] and out["outbox"] == []


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
        "gradebooks",
        "emails",
        "held",
        "unknown",
        "failed",
    ]


def test_nothing_a_student_may_not_see_reaches_them(tmp_path, monkeypatch):
    # The leak this design exists to close: the grader's private notes, and one member's
    # adjustment. There is no team-wide channel for either to reach now - a group mark
    # goes to each member's own gradebook and nowhere a team-mate reads.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["comments"] == []
    ((gb_repo, files, _d),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l"  # the member's OWN repo, read by nobody else
    for page in (files["grades.yml"], files["README.md"]):
        assert "privately noted" not in page  # the grader's note
        assert "-3" not in page  # the adjustment behind the final grade
    # Their own feedback is theirs to read, and it is here rather than anywhere shared.
    assert "Your section repeats the Q4 error." in files["README.md"]


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


def test_a_run_writes_every_sheet_the_semester_has_into_every_gradebook(
    tmp_path, monkeypatch
):
    # THE defect a live showcase found, back when a run could be scoped to one slug: the
    # gradebook was rendered from the selected sheet alone, so each scoped run left that
    # one section and deleted every other - four in a row, and assignment-1's distributed
    # grade was gone. The button offers no slug now, and a gradebook is the whole of what
    # a student has been given: every sheet in the semester, and a registrar's column each.
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": _SHEET, "assignment-2": _SHEET}
    )
    ((_repo, book, _detail),) = out["gradebooks"]
    assert "assignment-1:" in book["grades.yml"]
    assert "assignment-2:" in book["grades.yml"]
    ((_cfg_repo, cfg, _e),) = out["config"]
    assert (
        cfg[grades.SEMESTER_CSV_NAME]
        .splitlines()[0]
        .endswith("assignment-1,assignment-2")
    )


def test_a_semester_with_no_sheet_yet_distributes_nothing(
    tmp_path, monkeypatch, capsys
):
    # The sheet is the only source of marks, so an empty folder is "nothing has been
    # handed out yet" - reported, and nothing written, rather than an empty run.
    out = _distribute(monkeypatch, tmp_path, sheets={})
    assert out["rc"] == 1
    assert (out["comments"], out["gradebooks"], out["config"]) == ([], [], [])
    assert f"no {grades.SHEETS_DIR}/ in SEMESTER" in capsys.readouterr().err


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
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "15" in files["README.md"]


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


def test_a_held_team_score_holds_every_members_gradebook(tmp_path, monkeypatch):
    # A team's result is derived from the team block rather than from a member's view, so
    # a score nobody can act on holds every member of it, not just the one who was typed.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _HELD_TEAM_SHEET},
        grading=_GRADING_YML + "type: group\n",
    )
    assert out["rc"] == 0
    assert out["gradebooks"] == []


def test_holding_one_mark_leaves_the_students_other_marks_alone(tmp_path, monkeypatch):
    # The hold is per assignment: everything else a grader has settled still goes out.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _SHEET, "assignment-2": _HELD_SHEET},
    )
    assert out["rc"] == 0
    ((_repo, files, _delete),) = out["gradebooks"]
    assert "assignment-1" in files["grades.yml"]
    assert "assignment-2" not in files["grades.yml"]


def test_the_dry_run_sample_email_is_the_one_that_would_be_sent(tmp_path, monkeypatch):
    # The subject is the half a student reads first, and the course name is what tells one
    # of these apart from another - so a preview that showed neither was reviewing text
    # nobody would ever receive.
    out = _distribute(
        monkeypatch,
        tmp_path,
        dry_run=True,
        course_name=lambda org: "Deep Learning",
    )
    ((issue),) = out["preview"]
    assert "Subject: Your grades for Deep Learning have been updated" in issue["body"]
    assert (
        "Your grades for the Deep Learning course have been updated. View them"
        in issue["body"]
    )
    assert "grades-<handle>" in issue["body"]  # a placeholder, never a student


def test_an_unreadable_course_name_still_previews_the_email(tmp_path, monkeypatch):
    # Same fallback the send has: the name is a nicety, the notification is not.
    def boom(org):
        raise RuntimeError("no dsl-course.yml")

    out = _distribute(monkeypatch, tmp_path, dry_run=True, course_name=boom)
    assert out["rc"] == 0
    assert "Subject: Your grades have been updated" in out["preview"][0]["body"]


def test_the_dry_run_says_who_gets_what_in_a_private_issue_and_logs_none_of_it(
    tmp_path, monkeypatch, capsys
):
    # The log is a PUBLIC repo's; the issue is in the private semester-config. So the
    # names and marks go to the one, and only counts and the issue's address to the other.
    monkeypatch.setenv("DSL_VERBOSE", "")
    rows = ROSTER_ADA + "bob@uni.edu,Bob Byte,enrolled,bob-b,43,dsl-def\n"
    sheet = _SHEET + _SHEET.split("submissions:\n", 1)[1].replace("ada-l", "bob-b")
    sheet = sheet.replace("score_individual: 43", "score_individual: 38", 1)
    held = _HELD_SHEET.replace("ada-l", "bob-b")
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet, "assignment-2": held},
        roster_rows=rows,
        exported="hertie_email,name,github_handle,assignment-1\n"
        "ada@uni.edu,Ada,ada-l,36\nbob@uni.edu,Bob Byte,bob-b,43\n",
        dry_run=True,
    )
    assert out["rc"] == 0
    assert (out["gradebooks"], out["config"], out["outbox"]) == ([], [], [])
    ((issue),) = out["preview"]
    assert issue["title"] == grades.PREVIEW_TITLE and issue["state"] == "OPEN"
    body = issue["body"]
    # ada's grade changed; bob's did not (he has never been told it, so he is still
    # emailed), and his other mark is held
    assert (
        "### ⚠️ Fix these first - held back, not sent (1)\n"
        "- **assignment-2** · `bob-b` (Bob Byte) was late, and their mark is not a "
        "number a late penalty can come off. Fix it in "
        "`grading_sheets/assignment-2.yml`.\n" in body
    )
    assert (
        "### Grades that would change (1)\n"
        "- **assignment-1** · `ada-l` (Ada) · 38 (was 36)\n" in body
    )
    assert (
        "### Students who would be emailed (2)\n"
        "- `ada-l` (Ada) - their first grades email.\n"
        "  Their gradebook shows: assignment-1 38\n"
        "- `bob-b` (Bob Byte) - their first grades email.\n"
        "  Their gradebook shows: assignment-1 43\n" in body
    )
    printed = "".join(capsys.readouterr())
    assert (
        "Who gets what: https://github.com/SEMESTER/semester-config/issues/1" in printed
    )
    for private in ("ada-l", "bob-b", "Ada", "Bob", "38", "36"):
        assert private not in printed


def test_a_second_dry_run_rewrites_the_preview_rather_than_opening_another(
    tmp_path, monkeypatch
):
    store: list[dict] = []
    _distribute(monkeypatch, tmp_path, dry_run=True, preview_issues=store)
    corrected = _SHEET.replace("score_individual: 43", "score_individual: 45")
    _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": corrected},
        dry_run=True,
        preview_issues=store,
    )
    ((issue),) = store
    assert "- **assignment-1** · `ada-l` (Ada) · 45 (new)" in issue["body"]
    assert "43" not in issue["body"]


def test_the_preview_counts_unmarked_questions_per_student(tmp_path, monkeypatch):
    sheet = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    grading = _GRADING_YML + "questions:\n  Q1: 15\n  Q2: 10\n"
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=grading,
        dry_run=True,
    )
    assert (
        "### Not marked yet (1)\n- **assignment-1** · 1 student: `ada-l` (Q2 blank)\n"
        in out["preview"][0]["body"]
    )


def test_a_student_with_no_mark_is_not_in_the_preview_as_emailed(tmp_path, monkeypatch):
    # The send skips a book with nothing a grader wrote, and the preview says what the
    # send would do.
    sheet = _SHEET.replace("score_individual: 43", "score_individual:").replace(
        "    feedback_individual: |\n      Clean derivation.\n", ""
    )
    out = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": sheet}, dry_run=True
    )
    body = out["preview"][0]["body"]
    assert "- **assignment-1** · 1 student: `ada-l` (no mark yet)" in body
    assert (
        "### Students who would be emailed (0)\n"
        "Nothing - nobody has a new mark to be told about." in body
    )
    assert "Subject:" not in body


def test_a_preview_too_long_for_one_issue_says_how_many_it_left_out(
    tmp_path, monkeypatch
):
    # GitHub refuses a body over its cap, and a refused body is no preview at all.
    monkeypatch.setattr(grades, "_ISSUE_BODY_CAP", 2_000)
    handles = [f"s{n:03d}" for n in range(60)]
    rows = (
        "".join(
            f"\n{h}@uni.edu,Student {h},enrolled,{h},{n},c{n}"
            for n, h in enumerate(handles)
        )
        + "\n"
    )
    block = _SHEET.split("submissions:\n", 1)[1]
    sheet = "submissions:\n" + "".join(block.replace("ada-l", h) for h in handles)
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        roster_rows=rows,
        dry_run=True,
    )
    body = out["preview"][0]["body"]
    assert len(body) <= 2_000
    shown = body.count("- **assignment-1** · `s")
    assert 0 < shown < 60
    more = "more not shown - the list is longer than one issue can hold._"
    assert f"_{60 - shown} {more}" in body
    # every heading survives the cut, the emailed ones are all counted, and the email
    # itself is still there to review
    assert "### Students who would be emailed (60)\n_60 " + more in body
    assert body.endswith("</details>")


def _after_one_real_run(monkeypatch, tmp_path, sheet: str, **kwargs) -> dict:
    """A dry run over `sheet` in a semester whose last real run sent `_SHEET`: the record
    and the registrar export are the ones that run left behind."""
    first = _distribute(monkeypatch, tmp_path / "real")
    ((_cfg, cfg_files, _e),) = first["config"]
    return _distribute(
        monkeypatch,
        tmp_path / "dry",
        sheets={"assignment-1": sheet},
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
        exported=cfg_files[grades.SEMESTER_CSV_NAME],
        dry_run=True,
        **kwargs,
    )


def test_the_preview_opens_by_saying_nothing_was_sent_and_when(tmp_path, monkeypatch):
    body = _distribute(monkeypatch, tmp_path, dry_run=True)["preview"][0]["body"]
    first, second, third = body.splitlines()[:3]
    assert re.fullmatch(
        r"\*\*Nothing has been sent\.\*\* Preview: \d{1,2} [A-Z][a-z]{2} \d\d:\d\d UTC\.",
        first,
    )
    assert second == (
        "This is what running Distribute grades for real (with `preview` unticked) "
        "would do now."
    )
    assert third == "Each preview replaces this text; the real run closes this issue."


def test_a_preview_with_nothing_to_report_keeps_every_heading(tmp_path, monkeypatch):
    body = _after_one_real_run(monkeypatch, tmp_path, _SHEET)["preview"][0]["body"]
    for heading, nothing in (
        (
            "### ⚠️ Fix these first - held back, not sent (0)",
            "Nothing - no mark is held back.",
        ),
        ("### Not marked yet (0)", "Nothing - every row in every sheet has a mark."),
        (
            "### Grades that would change (0)",
            "Nothing new or changed since grades were last sent.",
        ),
        (
            "### Students who would be emailed (0)",
            "Nothing - nobody has a new mark to be told about.",
        ),
    ):
        assert f"{heading}\n{nothing}" in body
    assert "<details>" not in body


def test_a_silent_preview_says_why_nobody_is_emailed(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path, dry_run=True, notify=False)
    assert (
        "### Students who would be emailed (0)\n"
        "Nothing - `notify` is unticked, so nobody is emailed."
    ) in out["preview"][0]["body"]


@pytest.mark.parametrize(
    ("sheet", "why"),
    [
        (
            _SHEET.replace("score_individual: 43", "score_individual: 45"),
            "a grade changed",
        ),
        (_SHEET.replace("Clean derivation.", "Clean; see Q3."), "feedback changed"),
    ],
    ids=["grade", "feedback"],
)
def test_the_preview_says_why_each_student_would_be_emailed(
    tmp_path, monkeypatch, sheet, why
):
    body = _after_one_real_run(monkeypatch, tmp_path, sheet)["preview"][0]["body"]
    grade = "45" if why == "a grade changed" else "43"
    assert (
        f"### Students who would be emailed (1)\n- `ada-l` (Ada) - {why}.\n"
        f"  Their gradebook shows: assignment-1 {grade}\n" in body
    )
    changes = "(1)\n- **assignment-1** · `ada-l` (Ada) · 45 (was 43)"
    assert (changes in body) == (why == "a grade changed")
    assert "<summary>The email they would get</summary>" in body


def test_a_handle_in_two_teams_is_a_fix_the_preview_names(tmp_path, monkeypatch):
    sheet = _TEAM_SHEET + (
        "  beta:\n"
        "    score_group: 20\n"
        "    members:\n"
        "      ada-l:\n"
        "        adjustment_individual:\n"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": sheet},
        grading=_GRADING_YML + "type: group\n",
        dry_run=True,
    )
    assert (
        "- **assignment-1** · `ada-l` (Ada) is in more than one team. Fix it in "
        "`grading_sheets/assignment-1.yml`." in out["preview"][0]["body"]
    )


def test_the_preview_reads_as_plain_english(tmp_path, monkeypatch):
    # Faculty read this, not the pipeline: no "(s)", and none of the toolkit's own words.
    rows = ROSTER_ADA + "bob@uni.edu,Bob Byte,enrolled,bob-b,43,dsl-def\n"
    sheet = _SHEET + _SHEET.split("submissions:\n", 1)[1].replace("ada-l", "bob-b")
    half = _SHEET.replace(
        "score_individual: 43", "score_individual:\n      Q1: 15\n      Q2:"
    )
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={
            "assignment-1": sheet,
            "assignment-2": _HELD_SHEET.replace("ada-l", "bob-b"),
            "assignment-3": half,
        },
        grading=_GRADING_YML,
        roster_rows=rows,
        dry_run=True,
    )
    body = out["preview"][0]["body"]
    for word in ("(s)", "unit", "derived", "digest", "@ada-l", "@bob-b"):
        assert word not in body


def test_the_dry_run_only_says_a_column_is_new_when_it_is(
    tmp_path, monkeypatch, capsys
):
    _after_one_real_run(monkeypatch, tmp_path, _SHEET)
    assert "would gain column" not in capsys.readouterr().out


def test_a_real_run_closes_the_preview_with_a_line_saying_so(tmp_path, monkeypatch):
    store: list[dict] = []
    _distribute(monkeypatch, tmp_path, dry_run=True, preview_issues=store)
    out = _distribute(monkeypatch, tmp_path / "real", preview_issues=store)
    assert out["rc"] == 0
    ((issue),) = store
    assert issue["state"] == "CLOSED"
    assert issue["comments"] == [grades.PREVIEW_SENT]


def test_a_real_run_with_no_preview_open_is_fine(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path)
    assert out["rc"] == 0
    assert out["preview"] == []


def test_the_dry_run_counts_what_it_would_hold(tmp_path, monkeypatch, capsys):
    _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": _HELD_SHEET}, dry_run=True
    )
    printed = capsys.readouterr().out
    assert "assignment-1: 1 student(s) · 0 final grade(s) derived" in printed
    assert "1 held for a hand decision" in printed
    assert "would update 0 gradebook(s) and email 0 student(s)" in printed


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
    assert f"{grades.SEMESTER_CSV_NAME}: would gain column assignment-1" in printed
    assert "would update 1 gradebook(s) and email 1 student(s)" in printed


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
    # the next run mailed the whole semester again.
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
    ((_repo, files, _delete),) = again["gradebooks"]  # exactly one gradebook
    assert "45" in files["README.md"]
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


@pytest.mark.parametrize(
    "grading",
    [_GRADING_YML, _GRADING_YML + "submit_via: external\n"],
    ids=["assignment_repo", "external"],
)
def test_a_student_is_emailed_on_the_run_that_brings_their_first_mark(
    tmp_path, monkeypatch, grading
):
    # A row with nothing typed in it still renders a gradebook - the points available, a
    # submission time - but "your grades have been updated" would send a student to read
    # nothing. So no mail, and no record of one: the run that brings the mark tells them.
    unmarked = _SHEET.replace("score_individual: 43", "score_individual:").replace(
        "feedback_individual: |\n      Clean derivation.\n", "feedback_individual:\n"
    )
    first = _distribute(
        monkeypatch, tmp_path, sheets={"assignment-1": unmarked}, grading=grading
    )
    assert first["rc"] == 0
    assert [repo for repo, _files, _d in first["gradebooks"]] == ["grades-ada-l"]
    assert first["outbox"] == []
    ((_cfg, cfg_files, _d),) = first["config"]
    record = grades.parse_distributed(cfg_files[grades.DISTRIBUTED_PATH])
    assert ("ada-l", "", grades.CHANNEL_EMAIL) not in record

    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": _SHEET},
        grading=grading,
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_reworded_gradebook_page_is_committed_and_nobody_is_emailed(
    tmp_path, monkeypatch
):
    # The two channels are keyed on different things on purpose. The COMMIT is keyed on
    # the whole book, so a change the toolkit makes to the page's own standing text lands
    # in every gradebook; the EMAIL is keyed on what a grader wrote, so "there is something
    # new to read" still means a mark moved. On one hash, adding a sentence to the README re-mailed
    # every student in every live semester to tell them nothing.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    page = grades.render_readme
    monkeypatch.setattr(
        grades,
        "render_readme",
        lambda *a, **k: (
            page(*a, **k) + "\n## Keeping your work\nA new standing section.\n"
        ),
    )
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
    )
    ((_repo, files, _delete),) = again["gradebooks"]
    assert "A new standing section." in files["README.md"]
    assert again["outbox"] == []


def test_a_record_written_before_the_split_is_carried_over_not_re_mailed(
    tmp_path, monkeypatch, capsys
):
    # What a live semester's `distributed.csv` holds: rows written when BOTH channels were
    # keyed on the whole book, so the email row carries the same digest the gradebook row
    # does. The columns do not change - the row is carried over in place, and a re-run on
    # an unchanged semester mails nobody.
    first = _distribute(monkeypatch, tmp_path)
    ((_cfg, cfg_files, _d),) = first["config"]
    record = grades.parse_distributed(cfg_files[grades.DISTRIBUTED_PATH])
    whole = record[("ada-l", "", grades.CHANNEL_GRADEBOOK)][0]
    record[("ada-l", "", grades.CHANNEL_EMAIL)] = (whole, "2026-09-01T00:00:00", "")
    page = grades.render_readme
    monkeypatch.setattr(
        grades, "render_readme", lambda *a, **k: page(*a, **k) + "\nstanding text\n"
    )
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=grades.dump_distributed(record),
    )
    assert again["outbox"] == []
    assert "already knew what their gradebook said" in capsys.readouterr().out
    ((_cfg2, files, _d2),) = again["config"]
    carried = grades.parse_distributed(files[grades.DISTRIBUTED_PATH])
    # ...and the row now keys on the marks alone, so the next real mark does mail.
    email = ("ada-l", "", grades.CHANNEL_EMAIL)
    assert (
        carried[email][0]
        == grades.parse_distributed(cfg_files[grades.DISTRIBUTED_PATH])[email][0]
    )


_EMAIL = ("ada-l", "", grades.CHANNEL_EMAIL)


def _first_run(monkeypatch, tmp_path, **kwargs) -> tuple[str, str, str]:
    """`(the record, the versioned email digest in it, its grades.yml's LEGACY digest)`
    after one real run - what a semester holds once Ada has been told."""
    first = _distribute(monkeypatch, tmp_path, **kwargs)
    ((_repo, book, _d),) = first["gradebooks"]
    ((_cfg, cfg_files, _e),) = first["config"]
    record = cfg_files[grades.DISTRIBUTED_PATH]
    told = grades.parse_distributed(record)[_EMAIL][0]
    return record, told, grades.content_hash(book["grades.yml"])


def _told_as(record: str, digest: str) -> str:
    """`record` with Ada's email row holding `digest` - the row an older run wrote."""
    rows = grades.parse_distributed(record)
    rows[_EMAIL] = (digest, "2026-09-01T00:00:00", "")
    return grades.dump_distributed(rows)


def test_a_legacy_email_row_for_this_very_grades_yml_is_upgraded_not_re_mailed(
    tmp_path, monkeypatch, capsys
):
    # Every live semester's email rows are hashes of the whole grades.yml. One that still
    # matches it is a student who was told exactly this: carried over to the versioned
    # digest in the run's own record commit, and nobody is mailed for the change of scheme.
    record, told, legacy = _first_run(monkeypatch, tmp_path)
    assert told.startswith(grades.MARKS_DIGEST_PREFIX)
    assert not legacy.startswith(grades.MARKS_DIGEST_PREFIX)
    capsys.readouterr()
    again = _distribute(
        monkeypatch, tmp_path / "again", distributed=_told_as(record, legacy)
    )
    assert again["outbox"] == []
    assert again["gradebooks"] == []
    printed = capsys.readouterr().out
    assert "1 student(s) already knew what their gradebook said" in printed
    assert "ada-l" not in printed
    ((_cfg, files, _d),) = again["config"]
    assert grades.parse_distributed(files[grades.DISTRIBUTED_PATH])[_EMAIL][0] == told

    # ...and the run after that has nothing left to do: no mail, no row moved.
    third = _distribute(
        monkeypatch, tmp_path / "third", distributed=files[grades.DISTRIBUTED_PATH]
    )
    assert (third["outbox"], third["gradebooks"]) == ([], [])
    ((_cfg3, files3, _d3),) = third["config"]
    assert files3[grades.DISTRIBUTED_PATH] == files[grades.DISTRIBUTED_PATH]
    assert "already knew" not in capsys.readouterr().out


def test_a_legacy_email_row_under_a_changed_mark_mails_once(tmp_path, monkeypatch):
    # A legacy row that no longer describes the book is what the old code would have
    # mailed about, so it is mailed about - once, and recorded under the new digest.
    record, _told, legacy = _first_run(monkeypatch, tmp_path)
    corrected = {
        "assignment-1": _SHEET.replace("score_individual: 43", "score_individual: 45")
    }
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets=corrected,
        distributed=_told_as(record, legacy),
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]
    ((_cfg, files, _d),) = again["config"]
    now = grades.parse_distributed(files[grades.DISTRIBUTED_PATH])[_EMAIL][0]
    assert now.startswith(grades.MARKS_DIGEST_PREFIX)
    third = _distribute(
        monkeypatch,
        tmp_path / "third",
        sheets=corrected,
        distributed=files[grades.DISTRIBUTED_PATH],
    )
    assert third["outbox"] == []


def _respelt(monkeypatch) -> None:
    # What PR #289 did to the Submitted column: the same moment, spelt `3rd Oct`.
    shown = grades._submitted_display
    monkeypatch.setattr(
        grades,
        "_submitted_display",
        lambda *a, **k: shown(*a, **k).replace("3 Oct", "3rd Oct"),
    )


def _new_facts(monkeypatch) -> dict[str, str]:
    # A later push: a new submission time and a day late, under a course with no late
    # penalty, so the mark itself does not move.
    return {
        "assignment-1": _SHEET.replace("2026-10-03T22:14", "2026-10-05T09:00").replace(
            "days_late: 0", "days_late: 1"
        )
    }


@pytest.mark.parametrize("change", [_new_facts, _respelt], ids=["facts", "respelt"])
def test_a_changed_fact_rewrites_the_gradebook_and_mails_nobody(
    tmp_path, monkeypatch, change
):
    grading = "title: Neural networks\nlate_penalty_per_day: 0%\n"
    record, _told, _legacy = _first_run(monkeypatch, tmp_path, grading=grading)
    sheets = change(monkeypatch)
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets=sheets,
        grading=grading,
        distributed=record,
    )
    ((_repo, book, _d),) = again["gradebooks"]  # the page does move...
    assert ("5 Oct" if sheets else "3rd Oct") in book["README.md"]
    assert again["outbox"] == []  # ...and nobody is told to go and read it


def test_changed_feedback_under_a_versioned_row_mails(tmp_path, monkeypatch):
    record, _told, _legacy = _first_run(monkeypatch, tmp_path)
    reworded = _SHEET.replace("Clean derivation.", "Clean derivation; see Q3.")
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        sheets={"assignment-1": reworded},
        distributed=record,
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


@pytest.mark.parametrize("case", ["legacy", "facts"])
def test_the_dry_run_says_what_the_real_run_would_send(
    tmp_path, monkeypatch, capsys, case
):
    # The preview's "emailed" is the same decision the send takes, so a carried-over row
    # and a moved fact both preview as nobody mailed - and a dry run records nothing.
    grading = "title: Neural networks\nlate_penalty_per_day: 0%\n"
    record, _told, legacy = _first_run(monkeypatch, tmp_path, grading=grading)
    out = _distribute(
        monkeypatch,
        tmp_path / "dry",
        sheets=_new_facts(monkeypatch) if case == "facts" else None,
        grading=grading,
        distributed=_told_as(record, legacy) if case == "legacy" else record,
        dry_run=True,
    )
    assert out["config"] == [] and out["outbox"] == []
    body = out["preview"][0]["body"]
    assert "### Students who would be emailed (0)" in body and "Subject:" not in body
    printed = capsys.readouterr().out
    assert f"would update {1 if case == 'facts' else 0} gradebook(s)" in printed


def test_the_registrar_export_is_written_only_on_a_real_run(tmp_path, monkeypatch):
    assert _distribute(monkeypatch, tmp_path, dry_run=True)["config"] == []
    ((_cfg, files, _d),) = _distribute(monkeypatch, tmp_path / "real")["config"]
    csv_text = files[grades.SEMESTER_CSV_NAME]
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
    # A semester that reached `distributed.csv` without ever having had a `notified.csv` was
    # never on the migration path, so its `.system/gradebook/*.yml` was left in place for the rest
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


_EXTERNAL_GRADING = _GRADING_YML + "submit_via: external\n"

# The four shapes with no Submission receipts issue of their own. They used to be the interesting
# half of distribute - each one a thread it must not post into - and they are ordinary
# now: a mark goes to the gradebook whatever the shape, so there is one thing to prove.
_NO_THREAD_SHAPES = (
    "visibility: public\n",
    "visibility: student_choice\n",
    "submit_via: shared_dropbox_repo\n",
    "submit_via: external\n",
)


@pytest.mark.parametrize("shape", _NO_THREAD_SHAPES)
def test_every_shape_sends_its_mark_to_the_gradebook_and_nowhere_else(
    tmp_path, monkeypatch, shape
):
    # `found_issue` is what a lookup would answer, and `listed` carries the repo as
    # private: every excuse for the run to touch a thread is present, and it touches none.
    out = _distribute(
        monkeypatch,
        tmp_path,
        grading=_GRADING_YML + shape,
        found_issue=7,
        listed={"assignment-1-ada-l": repo_row("assignment-1-ada-l")},
    )
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    ((gb_repo, files, _delete),) = out["gradebooks"]
    assert gb_repo == "grades-ada-l" and "43" in files["README.md"]


def test_a_repo_the_listing_calls_public_is_no_longer_a_reason_to_hold_anything(
    tmp_path, monkeypatch
):
    # A `visibility:` edited after handout used to decide whether a mark could be posted
    # at all. It decides nothing here: the mark was never going to a repo.
    out = _distribute(
        monkeypatch,
        tmp_path,
        found_issue=7,
        listed={
            "assignment-1-ada-l": repo_row("assignment-1-ada-l", visibility="public")
        },
    )
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


def test_a_listing_that_could_not_be_read_still_sends_every_mark(tmp_path, monkeypatch):
    # An API blip used to cost a semester its feedback for a tick. The listing answers one
    # question now - has this student a gradebook yet - and `ensure_gradebooks` handles
    # not being told.
    out = _distribute(monkeypatch, tmp_path, listed=None)
    assert out["rc"] == 0
    assert [repo for repo, _f, _d in out["gradebooks"]] == ["grades-ada-l"]


def test_a_record_written_while_marks_were_commented_still_reads(tmp_path, monkeypatch):
    # A live semester's `distributed.csv` carries `issue` rows from before marks moved to
    # the gradebook alone. They are read, they keep their columns, and nothing acts on
    # them - the run must not trip over a channel it no longer writes.
    recorded = (
        "target,assignment,channel,content_hash,distributed_at,issue\n"
        "ada-l,assignment-1,issue,anoldhash,2026-10-05T00:00:00,7\n"
    )
    out = _distribute(monkeypatch, tmp_path, distributed=recorded, found_issue=7)
    assert out["rc"] == 0
    assert out["comments"] == [] and out["issues"] == []
    ((_cfg, files, _d),) = out["config"]
    written = grades.parse_distributed(files[grades.DISTRIBUTED_PATH])
    # The old row is still there, unchanged, and the gradebook row is new beside it.
    assert written[("ada-l", "assignment-1", grades.CHANNEL_ISSUE)] == (
        "anoldhash",
        "2026-10-05T00:00:00",
        "7",
    )
    assert ("ada-l", "", grades.CHANNEL_GRADEBOOK) in written


# -------------------------------------------- marks sent before the due date has passed

# A unit the toolkit has looked at and found nothing for yet: `info:` is there, because
# the sheet is created at handout, and empty, because nothing derives it before the due
# date.
_UNDERIVED_SHEET = """\
submissions:
  ada-l:
    info:
      submitted:
      days_late:
    score_individual: 43
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
"""


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_mark_sent_before_the_due_date_is_counted_and_said_out_loud(
    tmp_path, monkeypatch, capsys, dry_run
):
    # The gradebook row reads `not submitted` while the work is sitting in the repo,
    # because nothing fills `info:` until the due date has passed. A grader may mean to
    # send it, so this is a warning and a count, in both runs, and never a refusal.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNDERIVED_SHEET},
        dry_run=dry_run,
        due=_DUE_AHEAD,
    )
    printed = capsys.readouterr().out
    assert (
        "WARNING: 1 mark(s) sent before the due date - the submission facts "
        "are not derived yet" in printed
    )
    assert out["rc"] == 0  # counted, not blocked


def test_nothing_is_warned_about_once_the_due_date_has_gone_by(
    tmp_path, monkeypatch, capsys
):
    # Past the due date a blank `submitted` is a fact about the student, not about the
    # clock: they handed nothing in, and saying "the facts are not derived yet" would
    # send a grader looking for a bug in the toolkit.
    _distribute(monkeypatch, tmp_path, sheets={"assignment-1": _UNDERIVED_SHEET})
    assert "before the due date" not in capsys.readouterr().out


def test_an_assignment_that_collects_nothing_is_never_warned_about(
    tmp_path, monkeypatch, capsys
):
    # `external` has no commit to time at any moment, so its blank `submitted` is the
    # shape of the assignment and the gradebook says `external` rather than `not
    # submitted`. A due date still ahead changes nothing about it.
    _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _UNDERIVED_SHEET},
        grading=_EXTERNAL_GRADING,
        due=_DUE_AHEAD,
    )
    assert "before the due date" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "shape, listed, want",
    [
        # We could not look at all: find the thread the student was told to read, open
        # nothing on an org nobody could list.
        ({}, None, grades.THREAD_FIND),
        # There, and private: the one case a Submission receipts issue may be OPENED in.
        ({}, {"assignment-1-ada": "private"}, grades.THREAD_CREATE),
        # There, and not private. Nothing about a student's marking goes where the world
        # can read it - not even into a thread we used while it was still private.
        ({}, {"assignment-1-ada": "public"}, grades.THREAD_NONE),
        ({"visibility": "public"}, {"assignment-1-ada": "public"}, grades.THREAD_NONE),
        # There and private, but a shape with no Submission receipts issue of its own: the thread a
        # semester handed out before these shapes existed still gets its comment.
        ({"visibility": "public"}, {"assignment-1-ada": "private"}, grades.THREAD_FIND),
        (
            {"visibility": "student_choice"},
            {"assignment-1-ada": "private"},
            grades.THREAD_FIND,
        ),
        (
            {"submit_via": "external"},
            {"assignment-1-ada": "private"},
            grades.THREAD_FIND,
        ),
        # Not in the listing. A `github` repo may have been created since it was taken,
        # where a shape that creates no repos has none to find, and probing each would be
        # an issues call per student for an answer already known.
        ({}, {"grades-ada": "private"}, grades.THREAD_FIND),
        ({"submit_via": "external"}, {"grades-ada": "private"}, grades.THREAD_NONE),
    ],
)
def test_the_feedback_thread_policy_answers_every_shape(shape, listed, want):
    # ONE question - may this run touch the issue here, and may it open one? - answered in
    # one place, for the submission receipts, which are the whole of what is posted into
    # a submission repo.
    spec = grades.SheetSpec(slug="assignment-1", title="A1", is_group=False, **shape)
    rows = (
        None
        if listed is None
        else {name: repo_row(name, visibility=v) for name, v in listed.items()}
    )
    assert grades.receipts_thread_policy(spec, rows, "assignment-1-ada") == want


def test_a_spec_read_off_the_sheet_alone_never_opens_an_issue():
    # No schedule entry, so no definition: `submit_via` and `visibility` are this spec's
    # defaults rather than anybody's decision, and the one thing a guess may not do is
    # open an issue in a student's repo. Its marks reach the gradebook either way.
    spec = grades._spec_from_sheet("assignment-1", {"submissions": {}})
    rows = {"assignment-1-ada": repo_row("assignment-1-ada")}
    assert (
        grades.receipts_thread_policy(spec, rows, "assignment-1-ada")
        is grades.THREAD_FIND
    )


def test_distribute_provisions_the_gradebooks_with_no_time_budget(
    tmp_path, monkeypatch
):
    # The nightly sync is the ONE caller that bounds `ensure_gradebooks`. This run is
    # about to write a mark into every one of these repos, so one it stopped short of
    # would be a 404 per student in a public log, not a batch for tomorrow.
    assert _distribute(monkeypatch, tmp_path)["gradebook_calls"] == [{}]


def test_a_gradebook_this_run_creates_goes_into_the_listing_it_was_handed(monkeypatch):
    # The handout provisions the gradebooks off the tick's rows, so a gradebook made here
    # has to be in them: the next release of the same tick reads those rows, and would
    # otherwise create it again and count GitHub's refusal as a failure.
    monkeypatch.setattr(grades, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(grades, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(grades, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(grades, "add_collaborator", lambda *a, **k: True)
    existing: dict[str, dict] = {}
    assert grades.provision_one("SEMESTER", "ada-l", existing) == "ok"
    row = existing["grades-ada-l"]
    assert (row["name"], row["visibility"]) == ("grades-ada-l", "private")
    assert (row["isTemplate"], row["archived"], row["topics"]) == (False, False, [])
    assert row["pushed_at"]
    # And the second pass over those rows makes nothing.
    monkeypatch.setattr(
        grades, "create_repo", lambda *a, **k: pytest.fail("made twice")
    )
    assert grades.provision_one("SEMESTER", "ada-l", existing) == "skipped"


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
    # withhold the whole semester's grades on a transient failure.
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
    assert '"gradebooks": 1' in out
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


def test_a_dry_run_reds_when_the_roster_cannot_be_read(tmp_path, monkeypatch):
    # `ensure_gradebooks` skips an unreadable roster (the nightly sync runs it too), so
    # distribute says so itself - on the dry run as well as on the real one.
    assert _distribute(monkeypatch, tmp_path, roster_rows=None, dry_run=True)["rc"] == 1


def test_distribute_reds_when_the_roster_cannot_be_read(tmp_path, monkeypatch, capsys):
    out = _distribute(monkeypatch, tmp_path, roster_rows=None)
    assert out["rc"] == 1
    assert "could not be read" in capsys.readouterr().err
    # And the registrar export is LEFT ALONE. It is one row per enrolled student, so
    # regenerating it from a roster nobody could read would commit a header line over the
    # file a registrar transcribes grades from.
    ((_repo, files, _delete),) = out["config"]
    assert grades.SEMESTER_CSV_NAME not in files
    assert grades.DISTRIBUTED_PATH in files


def test_distribute_reds_on_a_header_only_roster_and_leaves_the_export(
    tmp_path, monkeypatch, capsys
):
    # `ensure_gradebooks` passes over an EMPTY roster as well as an unreadable one (a
    # freshly bootstrapped semester is not a failure), so by the time marks exist distribute
    # has to say so itself - and the export is one row per enrolled student, so rebuilding
    # it from no rows would commit a header line over the file a registrar transcribes
    # grades from.
    out = _distribute(monkeypatch, tmp_path, roster_rows="")
    assert out["rc"] == 1
    ((_repo, files, _delete),) = out["config"]
    assert grades.SEMESTER_CSV_NAME not in files
    assert grades.DISTRIBUTED_PATH in files


def test_a_dry_run_reds_on_a_header_only_roster_too(tmp_path, monkeypatch):
    assert _distribute(monkeypatch, tmp_path, roster_rows="", dry_run=True)["rc"] == 1


# ------------------------------------------------- the team-formation lock file


_DUE = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)
# The late cutoff the window closes at: the due date plus the institution's 10 days.
_CUTOFF = _DUE + timedelta(days=10)
_HANDOUT = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


def _sched(*, handout: datetime | None = None, **assignments) -> Schedule:
    """A schedule of assignments whose only interesting fields are which template they
    hand out from and, for the formation window, when. `handout=None` is the manual
    hand-out every test that is not about the window wants."""
    return Schedule(
        assignments={
            key: AssignmentEntry(
                due_datetime=_DUE,
                course_source_repo=template,
                handout_datetime=handout,
            )
            for key, template in assignments.items()
        }
    )


def _lock(
    monkeypatch,
    sched,
    configs: dict[str, str | None],
    defaults: str = "",
    now: datetime | None = None,
) -> str:
    """Render the lock file for `sched`, with each template's `grading_config.yml` given
    as text (None = the template has none) and the course's own defaults block as YAML."""
    # Each template's run settings are its schedule key's block in the semester's
    # assignments.yml now (decision 0009); the rest stays the template's.
    split = {t: _split_grading(text) for t, text in configs.items()}
    blocks = {
        key: yaml.safe_load(split[e.course_source_repo][1])["defaults"]
        for key, e in sched.assignments.items()
        if (split.get(e.course_source_repo) or (None, None))[1]
    }
    sched.org = sched.org or "SEMESTER"
    monkeypatch.setattr(
        grades,
        "_grading_text",
        lambda org, template: (split.get(template) or (None,))[0],
    )
    monkeypatch.setattr(
        settings,
        "_assignments_text",
        lambda org: yaml.safe_dump({"assignments": blocks}) if blocks else None,
    )
    monkeypatch.setattr(
        settings, "org_meta", lambda org: yaml.safe_load(defaults or "{}") or {}
    )
    return grades.team_lock_text(grades.team_lock_entries("COURSE", sched, now))


def test_the_lock_file_carries_a_scalar_block_per_schedule_assignment(monkeypatch):
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
            "a1": {
                "team_formation": "none",
                "max_team_size": 5,
                "team_formation_window": "none",
                # No window, so no day for a refusal about one to name.
                "team_formation_closes": None,
                "team_formation_page": None,
            },
            "project": {
                "team_formation": "self_select",
                "max_team_size": 3,
                # No handout_datetime on `_sched`, so the window never opens.
                "team_formation_window": "closed",
                "team_formation_closes": _CUTOFF.date(),
                # No `pages` handed in, so none to link - the key is still written.
                "team_formation_page": None,
            },
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


def test_a_semester_with_no_assignments_still_gets_a_readable_lock_file(monkeypatch):
    # The form refuses every slug rather than 404ing on the read and reporting a fault.
    text = _lock(monkeypatch, _sched(), {})
    assert yaml.safe_load(text) == {"assignments": {}}


def test_a_self_select_window_is_pending_then_open_then_closed(
    monkeypatch,
):
    # The three answers off one schedule, so the boundaries are read from the same dates a
    # semester really carries: handout 20 Sep, due 4 Oct, late cutoff 14 Oct.
    def window(now: datetime) -> str:
        text = _lock(
            monkeypatch,
            _sched(project="assignment-4-project-f2026", handout=_HANDOUT),
            {"assignment-4-project-f2026": "type: group\n"},
            now=now,
        )
        return yaml.safe_load(text)["assignments"]["project"]["team_formation_window"]

    # `pending` and `closed` are kept apart because the form says different things about
    # them: before the handout the close date is still in the FUTURE, so the shut wording
    # would tell a September student the door closed in October.
    assert window(_HANDOUT - timedelta(seconds=1)) == "pending"
    assert window(_HANDOUT) == "open"  # the handout instant itself is inside
    assert window(_CUTOFF - timedelta(seconds=1)) == "open"
    assert window(_CUTOFF) == "closed"  # the cutoff is not: the snapshot has frozen


def test_a_self_select_assignment_nobody_has_dated_never_opens(monkeypatch):
    # `handout_datetime` unset = handed out by hand at a moment nobody wrote down, so
    # there is no hour from which "form your team now" would be true.
    text = _lock(
        monkeypatch,
        _sched(project="assignment-4-project-f2026"),
        {"assignment-4-project-f2026": "type: group\n"},
        now=_HANDOUT,
    )
    assert (
        yaml.safe_load(text)["assignments"]["project"]["team_formation_window"]
        == "closed"
    )


def test_an_assignment_the_form_refuses_anyway_has_no_window(monkeypatch):
    # `assigned` and `individual` are refused on the team_formation scalar alone, so the
    # window says nothing - whatever the dates would make of it.
    text = _lock(
        monkeypatch,
        _sched(
            a1="assignment-1-f2026",
            project="assignment-4-project-f2026",
            handout=_HANDOUT,
        ),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": "type: group\nteam_formation: assigned\n",
        },
        now=_HANDOUT,
    )
    windows = yaml.safe_load(text)["assignments"]
    assert windows["a1"]["team_formation_window"] == "none"
    assert windows["project"]["team_formation_window"] == "none"


def test_the_close_date_is_the_pins_day_and_the_line_is_there_even_when_it_is_empty(
    monkeypatch,
):
    # The only reader is a refusal a student reads ("closed on 14 Oct"), so the scalar is
    # the cutoff's DAY, not its moment. The line is written whatever the value: the form and
    # `welcome.open_formation_slugs` both line-scan this file, and a key that appears only
    # sometimes is a second shape for them to get right.
    text = _lock(
        monkeypatch,
        _sched(
            a1="assignment-1-f2026",
            project="assignment-4-project-f2026",
            handout=_HANDOUT,
        ),
        {
            "assignment-1-f2026": "type: individual\n",
            "assignment-4-project-f2026": "type: group\n",
        },
        now=_HANDOUT,
    )
    assert "    team_formation_closes: 2026-10-14\n" in text
    assert "    team_formation_closes:\n" in text  # the individual one, empty
    entries = yaml.safe_load(text)["assignments"]
    assert entries["project"]["team_formation_closes"] == _CUTOFF.date()
    assert entries["a1"]["team_formation_closes"] is None


def test_a_template_with_no_spec_has_no_window_either(monkeypatch):
    text = _lock(
        monkeypatch,
        _sched(a2="assignment-2-f2026", handout=_HANDOUT),
        {"assignment-2-f2026": None},
        now=_HANDOUT,
    )
    assert yaml.safe_load(text)["assignments"]["a2"]["team_formation_window"] == "none"


def _writes(monkeypatch, existing, ok: bool = True) -> list[dict]:
    """Stub everything the lock write touches and collect what it puts. `existing` is what
    `get_file_with_sha` answers: `(text, sha)`, None for a 404, or an exception to raise."""
    monkeypatch.setattr(grades, "_grading_text", lambda org, t, **_: "type: group\n")
    monkeypatch.setattr(settings, "org_meta", lambda org: {})
    monkeypatch.setattr(grades, "repo_is_archived", lambda org, repo, **_: False)
    # The course org's templates, which number the assignment pages the lock links.
    monkeypatch.setattr(
        grades.schedule,
        "discover_assignments",
        lambda org: ["assignment-4-project-f2026"],
    )

    def read(org, repo, path):
        if isinstance(existing, Exception):
            raise existing
        return existing

    monkeypatch.setattr(grades, "get_file_with_sha", read)
    puts: list[dict] = []

    def put(org, repo, path, content, msg, expected_sha=None):
        puts.append({"org": org, "path": path, "content": content, "sha": expected_sha})
        return ok

    monkeypatch.setattr(grades, "put_file", put)
    return puts


def test_the_lock_file_is_written_once_and_is_free_when_nothing_changed(monkeypatch):
    # put_file blob-compares, so writing it from the membership sync, the handout and the
    # nightly refresh costs a read apiece and no commit at all on an unchanged semester.
    puts = _writes(monkeypatch, None)
    assert grades.sync_team_lock(
        "COURSE", "SEMESTER", _sched(project="assignment-4-project-f2026")
    ).ok
    (put,) = puts
    assert (put["org"], put["path"]) == ("SEMESTER", ".system/assignments.lock.yml")
    assert yaml.safe_load(put["content"])["assignments"]["project"] == {
        "team_formation": "self_select",
        "max_team_size": 5,
        "team_formation_window": "closed",
        "team_formation_closes": _CUTOFF.date(),
        # The assignment's page on the semester site, which lists the teams - numbered and
        # named exactly as the site names it, so the refusal that links it cannot drift.
        "team_formation_page": "https://semester.github.io/assignments/01-project.html",
    }


def test_the_lock_links_no_page_for_an_individual_assignment_and_lists_nothing(
    monkeypatch,
):
    # The page is for the refusal of a Join, which only a self-select assignment gets - and
    # a plan with none of those does not pay a listing of the course org for it.
    puts = _writes(monkeypatch, None)
    monkeypatch.setattr(grades, "_grading_text", lambda org, t: "type: individual\n")

    def boom(org):
        raise AssertionError("no self-select assignment, so no listing")

    monkeypatch.setattr(grades.schedule, "discover_assignments", boom)
    assert grades.sync_team_lock("COURSE", "SEMESTER", _sched(a1="assignment-1")).ok
    assert "    team_formation_page:\n" in puts[0]["content"].decode()


def test_the_lock_write_says_whether_the_file_actually_moved(monkeypatch):
    # `changed` is the blob compare handed back, so a caller that renders off this file
    # knows when to re-render without paying a second read to find out.
    puts = _writes(monkeypatch, None)
    sched = _sched(project="assignment-4-project-f2026")
    assert grades.sync_team_lock("COURSE", "SEMESTER", sched) == (True, True)
    live = puts[0]["content"]

    puts = _writes(monkeypatch, ("text does not matter", gh_contents.blob_sha(live)))
    assert grades.sync_team_lock("COURSE", "SEMESTER", sched) == (True, False)
    # The sha the content was read at goes to put_file - both the safe read-modify-write
    # and, when it already matches, the no-op short circuit.
    assert puts[0]["sha"] == gh_contents.blob_sha(live)

    puts = _writes(monkeypatch, ("something else", "0" * 40))
    assert grades.sync_team_lock("COURSE", "SEMESTER", sched) == (True, True)


def test_a_read_that_failed_still_writes_and_reports_a_change(monkeypatch):
    # Not a 404 - the file may well be there and unreadable. `changed` is a render HINT,
    # so one spurious re-render beats a missed one, and the write goes ahead with no sha.
    puts = _writes(monkeypatch, RuntimeError("could not read"))
    assert grades.sync_team_lock(
        "COURSE", "SEMESTER", _sched(project="assignment-4-project-f2026")
    ) == (True, True)
    assert puts[0]["sha"] is None


def test_a_failed_write_is_never_reported_as_a_change(monkeypatch):
    puts = _writes(monkeypatch, None, ok=False)
    assert grades.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")) == (False, False)
    assert len(puts) == 1


def test_a_lock_file_that_could_not_be_written_says_what_that_costs(
    monkeypatch, capsys
):
    _writes(monkeypatch, None, ok=False)
    assert not grades.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")).ok
    assert "the Join-team form reads it" in capsys.readouterr().err


def test_a_closed_out_semester_is_left_alone(monkeypatch, capsys):
    # `teardown` archives semester-config last, and an archived repo is read-only. The
    # membership sync fans out over the semester REGISTRY, which teardown does not touch, so
    # without this every finished semester would 403 the daily sync red for good.
    def boom(*args, **kwargs):
        raise AssertionError("a sealed semester-config takes no write")

    monkeypatch.setattr(grades, "repo_is_archived", lambda org, repo: True)
    monkeypatch.setattr(grades, "put_file", boom)
    monkeypatch.setattr(grades, "_grading_text", boom)
    assert grades.sync_team_lock("COURSE", "SEMESTER", _sched(p="t")).ok
    assert "semester archived" in capsys.readouterr().out


def test_no_log_line_from_the_lock_file_names_a_person(monkeypatch, capsys):
    # It runs in the course org's PUBLIC .github. Only slugs and template names may appear.
    monkeypatch.setattr(grades, "put_file", lambda *a, **k: True)
    _lock(monkeypatch, _sched(a2="assignment-2-f2026"), {"assignment-2-f2026": None})
    printed = capsys.readouterr()
    assert "@" not in printed.out + printed.err


# ------------------------------------------- the late-work rule a silent file is graded by


def test_a_file_that_says_nothing_about_late_work_gets_the_hertie_rule():
    # The default an assignment is graded by when neither it nor its course states one.
    # It was "nothing after the deadline", which no syllabus says: the school's own
    # sentence is 10% a day, and ten days is where that has taken the whole grade.
    spec = grades.parse_grading_spec("title: Neural networks\n")
    assert (spec.late_window_days, spec.late_penalty_per_day) == (
        policy.defaults()["late_window_days"],
        policy.defaults()["late_penalty_per_day"],
    )
    assert (
        course.late_rule(spec.late_window_days, spec.late_penalty_per_day)
        == "10% per day, up to 10 days"
    )


def _semester_spec(monkeypatch, block: str) -> grades.GradingSpec:
    """An assignment whose semester's assignments.yml gives it `block` (YAML lines)."""
    text = "assignments:\n  a1:\n" + "".join(f"    {ln}\n" for ln in block.splitlines())
    monkeypatch.setattr(settings, "_assignments_text", lambda org: text)
    return grades.with_run_settings(grades.parse_grading_spec(""), "C", "SEM", "a1")


def test_an_explicit_zero_window_still_means_nothing_after_the_deadline(monkeypatch):
    # The one way to say it, and it must survive a default that now fills the silence.
    spec = _semester_spec(monkeypatch, "late_window_days: 0\nlate_penalty_per_day: 10%")
    assert spec.late_window_days == 0
    assert (
        course.late_rule(spec.late_window_days, spec.late_penalty_per_day)
        == "not accepted after the deadline"
    )


def test_an_explicit_late_rule_wins(monkeypatch):
    spec = _semester_spec(monkeypatch, "late_window_days: 3\nlate_penalty_per_day: 25%")
    assert (spec.late_window_days, spec.late_penalty_per_day) == (3, "25%")


@pytest.mark.parametrize(
    ("config", "window", "penalty"),
    [
        ("late_window_days: 3", 3, None),
        ("late_penalty_per_day: 25%", None, "25%"),
        ("late_window_days: 0", 0, None),
    ],
    ids=["window-alone", "penalty-alone", "zero-alone"],
)
def test_half_a_late_rule_is_never_completed_from_the_default(
    monkeypatch, config, window, penalty
):
    # A semester that names one of the two has stated its own rule - three days late
    # accepted free, or a rate with nothing collected to spend it on - and finishing the
    # sentence out of the syllabus would grade a semester by words nobody wrote.
    spec = _semester_spec(monkeypatch, config)
    assert (spec.late_window_days, spec.late_penalty_per_day) == (window, penalty)


@pytest.mark.parametrize("key", ["late_window_days", "visibility", "max_team_size"])
def test_a_run_setting_in_the_template_is_not_migrated(key):
    # Refused WHOLE: read without it, the assignment would run on the semester's defaults
    # instead of the rule the file wrote, and say nothing (decision 0009).
    with pytest.raises(grades.NotMigrated) as raised:
        grades.parse_grading_spec(f"title: A1\n{key}: 3\n")
    assert raised.value.old == key and "assignments.yml" in str(raised.value)


@pytest.mark.parametrize(
    ("day", "spoken"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (31, "31st"),
    ],
)
def test_a_day_is_spoken_with_its_ordinal(day, spoken):
    # ONE spelling for the mail, the site and the form's JavaScript copy -
    # and 11th-13th are the three a `day % 10` rule alone gets wrong.
    assert grades.spoken_day(datetime(2026, 10, day)) == f"{spoken} Oct"


def test_a_scoped_real_return_records_that_assignment_as_returned(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-1")
    ((_cfg, cfg_files, _d),) = out["config"]
    assert grades.marks_return_record("assignment-1") in cfg_files


def test_a_return_marks_dispatch_sends_only_what_is_due_and_marked(monkeypatch):
    # The payload is written by whoever holds a bot token: nothing in it is trusted.
    monkeypatch.setattr(grades, "discover_semesters", lambda org: ["Sem"])
    monkeypatch.setattr(grades.schedule, "load", lambda org: _schedule_with("a1", "a2"))
    monkeypatch.setattr(grades, "marks_due", lambda c, s, sched, now: ([], ["a1"]))
    assert grades.dispatch_refusal("C", "sem", "a1") == ""
    assert "not due" in grades.dispatch_refusal("C", "Sem", "a2")
    assert "not a semester" in grades.dispatch_refusal("C", "Other", "a1")


# ------------------------------------------------ the record written before the emails


def test_the_emails_are_recorded_before_they_are_sent(tmp_path, monkeypatch):
    out = _distribute(monkeypatch, tmp_path)
    ((intent),) = out["intent"]
    assert ",email," in intent[grades.DISTRIBUTED_PATH]
    assert [m[0] for batch in out["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_lost_final_record_re_mails_nobody(tmp_path, monkeypatch):
    # The register's case: the mail went and `distributed.csv` would not land. Every
    # following run (and the automatic return, every quarter-hour) mailed everyone again.
    first = _distribute(
        monkeypatch,
        tmp_path,
        put_files_ok=lambda files: grades.DISTRIBUTED_PATH not in files,
    )
    assert first["rc"] == 1 and first["outbox"]
    ((intent),) = first["intent"]
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=intent[grades.DISTRIBUTED_PATH],
    )
    assert again["outbox"] == []


def test_no_email_goes_when_the_record_before_it_cannot_be_written(
    tmp_path, monkeypatch
):
    out = _distribute(monkeypatch, tmp_path, intent_ok=False)
    assert out["rc"] == 1
    assert out["outbox"] == [] and out["config"] == []


_TWO_SHEET = (
    _SHEET
    + """\
  ben-k:
    score_individual: 40
    adjustment_individual:
    feedback_individual:
    notes_not_shared_with_students:
"""
)
_TWO_ROSTER = ROSTER_ADA + "ben@uni.edu,Ben,enrolled,ben-k,43,dsl-abd\n"


def test_one_refused_address_does_not_hold_back_the_returned_record(
    tmp_path, monkeypatch
):
    # A refused address in a class that was mailed would otherwise keep the automatic
    # return asking, and failing, every quarter-hour; its row stays untold, so the next
    # Return marks run retries it.
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _TWO_SHEET},
        roster_rows=_TWO_ROSTER,
        assignment="assignment-1",
        sent=1,
    )
    assert out["rc"] == 1
    ((_cfg, cfg_files, _d),) = out["config"]
    assert grades.marks_return_record("assignment-1") in cfg_files
    record = cfg_files[grades.DISTRIBUTED_PATH]
    assert "ada-l,,email," in record and "ben-k,,email," not in record


def test_no_email_sent_at_all_leaves_the_assignment_to_be_returned_again(
    tmp_path, monkeypatch
):
    # Every send failed (no mail configured, a token fault): the automatic return asks
    # again, and the rows are reset, so it cannot mail anybody twice.
    out = _distribute(monkeypatch, tmp_path, assignment="assignment-1", sent=0)
    assert out["rc"] == 1
    ((_cfg, cfg_files, _d),) = out["config"]
    assert grades.marks_return_record("assignment-1") not in cfg_files
    assert ",email," not in cfg_files[grades.DISTRIBUTED_PATH]


def test_a_send_that_raises_resets_the_recorded_rows(tmp_path, monkeypatch):
    # `_send_via_graph` raises on a token failure. Before, the rows recorded before the
    # mail stayed "told": the next run said "No new marks to return" and nobody was
    # ever mailed.
    first = _distribute(
        monkeypatch,
        tmp_path,
        assignment="assignment-1",
        send_error=RuntimeError("Graph token refused"),
    )
    assert "raised" in first
    ((intent),) = first["intent"]
    assert ",email," in intent[grades.DISTRIBUTED_PATH]
    ((_cfg, cfg_files, _d),) = first["config"]
    assert ",email," not in cfg_files[grades.DISTRIBUTED_PATH]
    assert grades.marks_return_record("assignment-1") not in cfg_files
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=cfg_files[grades.DISTRIBUTED_PATH],
        exported=cfg_files[grades.SEMESTER_CSV_NAME],
        assignment="assignment-1",
    )
    assert [m[0] for batch in again["outbox"] for m in batch] == ["ada@uni.edu"]


def test_a_scoped_return_that_died_after_the_first_record_is_not_wedged(
    tmp_path, monkeypatch
):
    # The first record carries the registrar export: a scoped run refuses while
    # `distributed.csv` has rows and the export is missing.
    first = _distribute(
        monkeypatch,
        tmp_path,
        assignment="assignment-1",
        put_files_ok=lambda files: grades.DISTRIBUTED_PATH not in files,
    )
    ((intent),) = first["intent"]
    assert grades.SEMESTER_CSV_NAME in intent
    again = _distribute(
        monkeypatch,
        tmp_path / "again",
        distributed=intent[grades.DISTRIBUTED_PATH],
        exported=intent[grades.SEMESTER_CSV_NAME],
        assignment="assignment-1",
    )
    assert again["rc"] == 0
    ((_cfg, cfg_files, _d),) = again["config"]
    assert grades.marks_return_record("assignment-1") in cfg_files


def test_a_student_with_no_roster_email_is_not_recorded_before_the_mail(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        roster_rows="\n,Ada,enrolled,ada-l,42,dsl-abc\n",
    )
    assert out["gradebooks"]  # marked and written ...
    assert out["intent"] == [] and out["outbox"] == []  # ... and nobody to tell


# ------------------------------------------------------------- per-question feedback

_QUESTIONS_GRADING = _GRADING_YML + "questions:\n  Q1: 30\n  Q2: 20\n"
_QUESTION_SHEET = """\
submissions:
  ada-l:
    info:
      submitted: '2026-10-03T22:14+02:00'
      days_late: 0
    adjustment_individual:
    feedback_individual: |
      Clean derivation.
    notes_not_shared_with_students: chased by email
    score_individual:
      Q1: 25
      Q2: 18
    feedback_per_question:
      Q1:
      Q2: |
        The bound is loose.
        Tighten it with the second lemma.
"""


def test_per_question_feedback_reaches_the_gradebook_under_each_question(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _QUESTION_SHEET},
        grading=_QUESTIONS_GRADING,
    )
    ((_repo, files, _d),) = out["gradebooks"]
    book = yaml.safe_load(files["grades.yml"])["assignments"]["assignment-1"]
    # a blank cell is not sent
    assert book[grades.QUESTION_FEEDBACK_KEY] == {
        "Q2": "The bound is loose.\nTighten it with the second lemma."
    }
    readme = files["README.md"]
    assert "Clean derivation." in readme
    assert (
        "- **Q2:** The bound is loose.\n  Tighten it with the second lemma." in readme
    )
    assert "**Q1:**" not in readme


def test_the_marks_email_lists_overall_then_per_question_feedback(
    tmp_path, monkeypatch
):
    out = _distribute(
        monkeypatch,
        tmp_path,
        sheets={"assignment-1": _QUESTION_SHEET},
        grading=_QUESTIONS_GRADING,
        include_feedback=True,
    )
    ((message,),) = out["outbox"]
    body = message[2]
    assert body.index("Clean derivation.") < body.index("Q2: The bound is loose.")


def test_a_teams_per_question_feedback_reaches_every_member():
    spec = grades.SheetSpec(
        slug="a1", title="A1", is_group=True, questions={"Q1": "5", "Q2": "5"}
    )
    block = {
        "feedback_group": "Good.",
        "members": {"ada-l": {}, "ben-k": {}},
        "score_group": {"Q1": "4", "Q2": "5"},
        grades.QUESTION_FEEDBACK_KEY: {"Q2": "Neat proof.", "Q1": None},
    }
    for handle in ("ada-l", "ben-k"):
        view = grades.student_view(spec, "alpha", block, handle)
        assert view[grades.QUESTION_FEEDBACK_KEY] == {"Q2": "Neat proof."}


def test_per_question_feedback_keeps_the_declared_order_and_a_mistyped_name():
    spec = grades.SheetSpec(
        slug="a1", title="A1", is_group=False, questions={"Q1": "5", "Q2": "5"}
    )
    said = grades.question_feedback(spec, {"Q3": "typo", "Q2": "b", "Q1": "a"})
    assert list(said) == ["Q1", "Q2", "Q3"]


def test_a_question_may_name_the_file_it_is_marked_from():
    spec = grades.parse_grading_spec(
        "formats: [ipynb, latex]\n"
        "questions:\n"
        "  Q1: 10\n"
        "  Q2: {points: 5, file: starter.tex}\n"
    )
    assert spec.format == "ipynb"  # the runnable one
    assert spec.questions == {"Q1": "10", "Q2": "5"}
    assert spec.question_files == {"Q2": "starter.tex"}
    assert spec.dropped == ()


@pytest.mark.parametrize(
    "entry", ["{points: 5, file: ../secret.tex}", "{points: 5, file: /etc/x}"]
)
def test_a_question_file_outside_the_submission_is_refused(entry):
    spec = grades.parse_grading_spec(f"questions:\n  Q1: 10\n  Q2: {entry}\n")
    assert spec.question_files is None
    assert spec.questions == {"Q1": "10", "Q2": "5"}  # still marked, from the runnable
    assert any("not a file inside the submission" in line for line in spec.dropped)


def test_an_unknown_key_on_a_question_is_named_and_ignored():
    spec = grades.parse_grading_spec("questions:\n  Q1: {points: 5, weight: 2}\n")
    assert spec.questions == {"Q1": "5"}
    assert any("`weight:`" in line for line in spec.dropped)
