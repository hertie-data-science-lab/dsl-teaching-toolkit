"""The gradebook repos: provisioning them, what a student reads in them, and the log lines
a gradebook write may carry (a repo named after a student is never named in a public log).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from dsl_course import (
    gh_contents,
    gradebook,
    grading_sheet,
    repos,
)
from tests.conftest import ROSTER_HEADER, parse_roster

# ------------------------------------------------------ provisioning the gradebooks


def test_ensure_gradebooks_skips_auditors(monkeypatch, capsys):
    # Auditors are never assessed, so they get no private gradebook repo. Dry-run keeps
    # this pure - the roster is the only input, and nothing is provisioned.
    monkeypatch.setenv("DSL_VERBOSE", "1")  # per-student lines are verbose-only
    students = parse_roster(
        ROSTER_HEADER + "\n"
        "ada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
        "eve@uni.edu,Eve,auditor,eve-e,43,dsl-xyz\n"
        "bob@uni.edu,Bob,,bob-b,44,dsl-def\n"  # blank role -> enrolled
    )
    monkeypatch.setattr(gradebook.roster, "load", lambda org: students)
    assert gradebook.ensure_gradebooks("SEMESTER", dry_run=True) == 0
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
    monkeypatch.setattr(gradebook.roster, "load", lambda org: [])
    assert gradebook.ensure_gradebooks("SEMESTER") == 0
    monkeypatch.setattr(gradebook.roster, "load", lambda org: None)
    assert gradebook.ensure_gradebooks("SEMESTER") == 0
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
        gradebook.roster, "load", lambda org: parse_roster(ROSTER_HEADER + rows + "\n")
    )
    made: list[str] = []
    monkeypatch.setattr(gradebook, "listing_by_name", lambda org: {})
    # A clock that jumps a minute per gradebook, so the third student is already past a
    # 2-minute budget. `time.monotonic` is read once before the loop and once per student.
    ticks = iter(range(0, 6000, 60))
    monkeypatch.setattr(gradebook.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        gradebook,
        "provision_one",
        lambda org, handle, existing=None, collaborators=None: (
            made.append(handle) or "ok"
        ),
    )
    assert gradebook.ensure_gradebooks("SEMESTER", budget_minutes=2) == 0
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
        gradebook.roster, "load", lambda org: parse_roster(ROSTER_HEADER + rows + "\n")
    )
    made: list[str] = []
    monkeypatch.setattr(gradebook, "listing_by_name", lambda org: {})
    monkeypatch.setattr(
        gradebook,
        "provision_one",
        lambda org, handle, existing=None, collaborators=None: (
            made.append(handle) or "skipped"
        ),
    )
    assert gradebook.ensure_gradebooks("SEMESTER") == 0
    assert len(made) == 200
    assert "on the next run" not in capsys.readouterr().out


def test_ensure_gradebooks_names_no_student_in_a_public_log(monkeypatch, capsys):
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    students = parse_roster(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(gradebook.roster, "load", lambda org: students)
    assert gradebook.ensure_gradebooks("SEMESTER", dry_run=True) == 0
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
    monkeypatch.setattr(gradebook, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    assert gradebook.provision_one("SEMESTER", "ada-l") == "ok"
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
    monkeypatch.setattr(gradebook, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: False)
    assert gradebook.provision_one("SEMESTER", "ada-l").startswith("failed")


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
    monkeypatch.setattr(gradebook, "grant_faculty", lambda *a, **k: faculty.append(a))
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "set_repo_topics", lambda *a, **k: True)
    gradebook.provision_one("SEMESTER", "ada-l")
    assert faculty == [], "the existing gradebook was re-granted"
    gradebook.provision_one("SEMESTER", "bob-b")
    assert faculty == [("SEMESTER", "grades-bob-b", gradebook.FACULTY_READ_ACCESS)]


def test_an_existing_gradebook_missing_its_topic_is_retagged(monkeypatch):
    # The stamp is a separate PUT after the create, so a gradebook whose PUT failed - or
    # that predates the topic - stayed untagged until the nightly sweep. Untagged means
    # discovery reads `grades-<handle>` as a semester repo, handle and all.
    tagged = []
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        gradebook, "set_repo_topics", lambda o, r, t, **k: tagged.append((r, t)) or True
    )
    gradebook.provision_one(
        "SEMESTER",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": ["keep-me"]}},
    )
    # additive: the PUT replaces the whole list, so what it already carried comes back
    assert tagged == [("grades-ada-l", ["gradebook", "keep-me"])]


def test_an_already_tagged_gradebook_costs_no_call(monkeypatch):
    # The listing carries `topics`, so the common case - every gradebook tagged - is free.
    tagged = []
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        gradebook, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    gradebook.provision_one(
        "SEMESTER",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": ["gradebook"]}},
    )
    assert tagged == []


def test_an_archived_gradebook_is_left_frozen(monkeypatch):
    # An archived repo is read-only, so the PUT 403s, and a finished semester is meant to
    # stay frozen. `access.converge_topics` passes over archived repos for the same reason.
    tagged = []
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(
        gradebook, "set_repo_topics", lambda o, r, t, **k: tagged.append(r) or True
    )
    gradebook.provision_one(
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
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "set_repo_topics", lambda *a, **k: False)
    gradebook.provision_one(
        "SEMESTER",
        "ada-l",
        existing={"grades-ada-l": {"name": "grades-ada-l", "topics": []}},
    )
    captured = capsys.readouterr()
    assert "carries no `gradebook` topic" in captured.err
    assert "landing page" in captured.err
    assert "ada-l" not in captured.out + captured.err


# ---------------- "nothing new to render" must mean nothing new, not a failed commit


# ------------------------------------------- ONE listing instead of a probe per gradebook


def _ensure_run(monkeypatch, listing, handles=("ada-l", "bob-b")):
    """`ensure_gradebooks` over `handles`, with `listing` (or None for one that could not
    be read) standing in for the org listing. Returns (the orgs listed, the gradebooks
    created)."""
    students = parse_roster(
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
    monkeypatch.setattr(gradebook.roster, "load", lambda org: students)
    monkeypatch.setattr(gradebook, "listing_by_name", fake_listing)
    monkeypatch.setattr(
        gradebook, "create_repo", lambda org, repo, **k: created.append(repo) or True
    )
    monkeypatch.setattr(gradebook, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    assert gradebook.ensure_gradebooks("SEMESTER") == 0
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
    students = parse_roster(
        ROSTER_HEADER + "\nada@uni.edu,Ada,enrolled,ada-l,42,dsl-abc\n"
    )
    monkeypatch.setattr(gradebook.roster, "load", lambda org: students)
    monkeypatch.setattr(
        gradebook,
        "listing_by_name",
        lambda org: pytest.fail("a dry run listed the org"),
    )
    assert gradebook.ensure_gradebooks("SEMESTER", dry_run=True) == 0


def test_a_gradebook_this_run_creates_goes_into_the_listing_it_was_handed(monkeypatch):
    # The handout provisions the gradebooks off the tick's rows, so a gradebook made here
    # has to be in them: the next release of the same tick reads those rows, and would
    # otherwise create it again and count GitHub's refusal as a failure.
    monkeypatch.setattr(gradebook, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "set_repo_topics", lambda *a, **k: True)
    monkeypatch.setattr(gradebook, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(gradebook, "add_collaborator", lambda *a, **k: True)
    existing: dict[str, dict] = {}
    assert gradebook.provision_one("SEMESTER", "ada-l", existing) == "ok"
    row = existing["grades-ada-l"]
    assert (row["name"], row["visibility"]) == ("grades-ada-l", "private")
    assert (row["isTemplate"], row["archived"], row["topics"]) == (False, False, [])
    assert row["pushed_at"]
    # And the second pass over those rows makes nothing.
    monkeypatch.setattr(
        gradebook, "create_repo", lambda *a, **k: pytest.fail("made twice")
    )
    assert gradebook.provision_one("SEMESTER", "ada-l", existing) == "skipped"


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
    assert gradebook.spoken_day(datetime(2026, 10, day)) == f"{spoken} Oct"


def test_a_teams_per_question_feedback_reaches_every_member():
    spec = grading_sheet.SheetSpec(
        slug="a1", title="A1", is_group=True, questions={"Q1": "5", "Q2": "5"}
    )
    block = {
        "feedback_group": "Good.",
        "members": {"ada-l": {}, "ben-k": {}},
        "score_group": {"Q1": "4", "Q2": "5"},
        grading_sheet.QUESTION_FEEDBACK_KEY: {"Q2": "Neat proof.", "Q1": None},
    }
    for handle in ("ada-l", "ben-k"):
        view = gradebook.student_view(spec, "alpha", block, handle)
        assert view[grading_sheet.QUESTION_FEEDBACK_KEY] == {"Q2": "Neat proof."}


def test_a_teams_per_question_feedback_sits_in_the_shared_quote():
    view = {
        "final_grade": "9",
        "team": "alpha",
        "team_feedback": "Good.",
        grading_sheet.QUESTION_FEEDBACK_KEY: {"Q2": "Neat proof."},
    }
    section = gradebook._readme_section("A1", view)
    assert (
        "> **Team feedback (shared with alpha):** Good.\n>\n> - **Q2:** Neat proof."
        in section
    )


def test_a_gradebook_whose_student_already_reads_it_is_not_granted_again(monkeypatch):
    # The nightly sync visits every gradebook; the GraphQL listing says who is already a
    # read collaborator, so the PUT is made only for the rest: a pending invite (not
    # listed) and a student who holds MORE than read, which the PUT puts back.
    added = []
    monkeypatch.setattr(
        gradebook,
        "add_collaborator",
        lambda org, repo, handle, **k: added.append(handle) or True,
    )
    monkeypatch.setattr(gradebook, "_tag_gradebook", lambda *a, **k: None)
    handles = ("ada", "bo", "cy")
    existing = {f"grades-{h}": {"name": f"grades-{h}"} for h in handles}
    held = {
        "grades-ada": {"ada": "READ"},
        "grades-bo": {},
        "grades-cy": {"cy": "WRITE"},
    }
    for handle in handles:
        assert gradebook.provision_one("S", handle, existing, held) == "skipped"
    assert added == ["bo", "cy"]


def test_per_question_feedback_keeps_the_declared_order_and_a_mistyped_name():
    spec = grading_sheet.SheetSpec(
        slug="a1", title="A1", is_group=False, questions={"Q1": "5", "Q2": "5"}
    )
    said = gradebook.question_feedback(spec, {"Q3": "typo", "Q2": "b", "Q1": "a"})
    assert list(said) == ["Q1", "Q2", "Q3"]
