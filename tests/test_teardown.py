"""Cohort teardown -- what gets frozen, in what order, and what is left resumable.

The order is the feature: an archived repo takes no collaborator change, so a revoke that
does not happen BEFORE the freeze can never happen at all, and the sealed `classroom-config`
is the marker the nightly refresh reads as "this cohort is finished". Both are asserted here
against a recorded call sequence rather than trusted to a comment.
"""

from __future__ import annotations

from datetime import date

import pytest

from dsl_course import schedule, teardown
from tests.conftest import repo_row

COHORT = "hertie-dsl-demo-f2026"

# A cohort mid-teardown: two individual repos, one group repo, two gradebooks, one already
# frozen, plus the infra and the template that must never be counted as somebody's work.
LISTING = [
    repo_row("classroom-config"),
    repo_row("welcome"),
    repo_row(".github", topics=["dsl-cohort"]),
    repo_row(f"{COHORT}.github.io", visibility="public"),
    repo_row("assignment-1", isTemplate=True, topics=["assignment-template"]),
    repo_row("assignment-2-project", isTemplate=True, topics=["assignment-template"]),
    repo_row("assignment-1-ada-l", topics=["submission"]),
    repo_row("assignment-1-bob-b", topics=["submission"]),
    repo_row("assignment-2-project-team-x", topics=["submission"]),
    repo_row("grades-ada-l"),
    repo_row("grades-bob-b", archived=True),
]


def _sched(end: date | None) -> schedule.Schedule:
    return schedule.Schedule(semester_end=end)


@pytest.fixture
def org(monkeypatch):
    """A cohort org whose every write is recorded rather than made.

    Returns the call log: `("revoke", repo)`, `("archive", repo)`, `("put", path)` in the
    order they were attempted, which is what the ordering tests read."""
    calls: list[tuple[str, str]] = []
    # No rendered workflow sets DSL_VERBOSE, so no test may inherit it from the shell
    # either - the per-person lines are exactly what the public-log assertion is about.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        teardown, "list_org_repos", lambda o: [dict(r) for r in LISTING]
    )
    # A term that ENDED, and a date that stays in the past: `close_out` reads the wall
    # clock, so a fixture pinned to this year's December would start refusing in December.
    monkeypatch.setattr(teardown.schedule, "load", lambda o: _sched(date(2025, 12, 18)))
    monkeypatch.setattr(
        teardown,
        "get_file_content",
        lambda o, r, p: "hertie_email,name,github_handle\na@uni.edu,Ada,ada-l\n",
    )
    monkeypatch.setattr(
        teardown,
        "revoke_repo_grants",
        lambda o, repo, login, dry_run=False: (
            calls.append(("dry-revoke" if dry_run else "revoke", repo)),
            (1, 0),
        )[1],
    )
    monkeypatch.setattr(
        teardown,
        "archive_repo",
        lambda o, repo, person=False: (calls.append(("archive", repo)), True)[1],
    )
    monkeypatch.setattr(
        teardown,
        "put_file",
        lambda o, r, path, content, message: (calls.append(("put", path)), True)[1],
    )
    return calls


# --------------------------------------------------------------- what counts as a target


def test_only_the_repos_holding_a_students_work_or_marks_are_frozen():
    # Infra, the public site and the assignment TEMPLATE are not anybody's record: the
    # template holds the brief every student was given, and freezing it into the teardown
    # record as a student repo would misdescribe what was frozen.
    assert [t.repo for t in teardown.targets(LISTING)] == [
        "assignment-1-ada-l",
        "assignment-1-bob-b",
        "assignment-2-project-team-x",
        "grades-ada-l",
        "grades-bob-b",
    ]


def test_a_submission_repo_whose_topic_never_landed_still_counts():
    # The `submission` topic is stamped in a separate call after the create. Discovery's
    # rule is topic OR name, and teardown must not depend on one PATCH having landed.
    listing = [
        repo_row("assignment-1", isTemplate=True),
        repo_row("assignment-1-ada-l"),
    ]
    assert [t.repo for t in teardown.targets(listing)] == ["assignment-1-ada-l"]


def test_a_frozen_repo_is_carried_as_already_archived():
    frozen = {t.repo: t.archived for t in teardown.targets(LISTING)}
    assert frozen["grades-bob-b"] is True
    assert frozen["grades-ada-l"] is False


@pytest.mark.parametrize(
    ("repo", "template", "login"),
    [
        ("assignment-1-ada-l", "assignment-1", "ada-l"),
        ("grades-ada-l", None, "ada-l"),
        # A GROUP repo's suffix is a team name. It is returned, and the probe behind it
        # finds nothing, because a team is not a collaborator on its own repo.
        ("assignment-2-project-team-x", "assignment-2-project", "team-x"),
        # Recognised only by its topic, with no template left in the org to split on.
        ("assignment-9-orphan", None, ""),
    ],
)
def test_the_login_a_repo_is_named_after(repo, template, login):
    assert teardown.named_login(repo, template) == login


# ------------------------------------------------------------------------ the refusal


@pytest.mark.parametrize(
    ("end", "ended"),
    [
        (date(2026, 12, 18), True),
        (date(2027, 1, 1), False),
        (date(2026, 12, 31), False),  # today itself is not "over"
        (None, False),  # nothing declared is never evidence the term ended
    ],
)
def test_term_ended_reads_only_a_declared_semester_end(end, ended):
    assert teardown.term_ended(_sched(end), date(2026, 12, 31)) is ended


def test_a_live_term_refuses_and_touches_nothing(org, monkeypatch):
    monkeypatch.setattr(teardown.schedule, "load", lambda o: _sched(date(2099, 1, 1)))
    assert teardown.close_out(COHORT, dry_run=False) == 1
    assert org == []


def test_force_closes_a_cohort_that_never_declared_a_term_end(org, monkeypatch):
    monkeypatch.setattr(teardown.schedule, "load", lambda o: _sched(None))
    assert teardown.close_out(COHORT, dry_run=False) == 1
    assert org == []
    assert teardown.close_out(COHORT, dry_run=False, force=True) == 0
    assert ("archive", "classroom-config") in org


def test_a_cohort_with_no_classroom_config_is_refused(org, monkeypatch):
    monkeypatch.setattr(teardown, "list_org_repos", lambda o: [repo_row("welcome")])
    assert teardown.close_out(COHORT, dry_run=False) == 1
    assert org == []


def test_an_already_sealed_cohort_is_a_no_op(org, monkeypatch):
    # Idempotence at the top: the sealed classroom-config IS the "this cohort is finished"
    # marker, so a second click does nothing rather than re-freezing a frozen org.
    listing = [dict(r) for r in LISTING]
    listing[0]["archived"] = True
    monkeypatch.setattr(teardown, "list_org_repos", lambda o: listing)
    assert teardown.close_out(COHORT, dry_run=False) == 0
    assert org == []


# ------------------------------------------------------------------------- the ordering


def test_every_repo_is_revoked_before_it_is_frozen(org):
    # An archived repo takes no collaborator change, so a repo frozen first keeps its
    # grant for as long as it stays frozen.
    assert teardown.close_out(COHORT, dry_run=False) == 0
    for kind, repo in org:
        if kind == "archive" and repo != "classroom-config":
            assert ("revoke", repo) in org[: org.index((kind, repo))], repo


def test_the_private_record_is_sealed_last(org):
    assert teardown.close_out(COHORT, dry_run=False) == 0
    # The record is written, and only then is the repo holding it frozen - and that is the
    # very last thing the run does, so an interrupted run leaves a cohort the nightly
    # refresh still treats as live and this command still resumes.
    assert org[-2:] == [("put", teardown.RECORD_PATH), ("archive", "classroom-config")]


def test_a_frozen_repo_is_not_touched_again(org):
    assert teardown.close_out(COHORT, dry_run=False) == 0
    assert ("archive", "grades-bob-b") not in org
    assert ("revoke", "grades-bob-b") not in org


def test_the_classroom_config_is_never_treated_as_a_students_repo(org):
    assert teardown.close_out(COHORT, dry_run=False) == 0
    assert [r for k, r in org if k == "archive"].count("classroom-config") == 1


# ------------------------------------------------------------------- failure and dry run


def test_a_repo_whose_access_cannot_be_read_is_left_live_and_reds_the_run(
    org, monkeypatch
):
    # Freezing it would strand the grant behind a read-only repo, so it stays live, the
    # run reds, and nothing is sealed - the next run picks that repo up.
    monkeypatch.setattr(
        teardown,
        "revoke_repo_grants",
        lambda o, repo, login, dry_run=False: (
            (0, 1) if repo == "assignment-1-ada-l" else (1, 0)
        ),
    )
    assert teardown.close_out(COHORT, dry_run=False) == 1
    assert ("archive", "assignment-1-ada-l") not in org
    assert ("archive", "classroom-config") not in org
    assert ("put", teardown.RECORD_PATH) not in org


def test_an_unwritable_record_leaves_the_cohort_live(org, monkeypatch):
    monkeypatch.setattr(teardown, "put_file", lambda *a, **k: False)
    assert teardown.close_out(COHORT, dry_run=False) == 1
    assert ("archive", "classroom-config") not in org


def test_a_dry_run_writes_nothing_at_all(org):
    # It still PROBES - the counts it prints have to be the real ones - but every probe
    # is itself a dry run, and nothing is frozen, written or sealed.
    assert teardown.close_out(COHORT) == 0
    assert org == [
        ("dry-revoke", "assignment-1-ada-l"),
        ("dry-revoke", "assignment-1-bob-b"),
        ("dry-revoke", "assignment-2-project-team-x"),
        ("dry-revoke", "grades-ada-l"),
    ]


# ------------------------------------------------------------------------- what is said


def test_no_log_line_names_a_student_or_their_repo(org, capsys):
    # Every faculty workflow runs in the course org's PUBLIC .github, so one
    # `<slug>-<handle>` line there publishes who was in the cohort. DSL_VERBOSE is unset
    # here, exactly as it is in every rendered workflow.
    assert teardown.close_out(COHORT, dry_run=False) == 0
    printed = capsys.readouterr()
    said = printed.out + printed.err
    for secret in ("ada-l", "bob-b", "team-x", "grades-", "assignment-1-"):
        assert secret not in said, said


def test_the_record_names_what_was_frozen_and_why_it_is_being_kept():
    # The opposite rule, and for the opposite reason: this file is written into the
    # PRIVATE classroom-config beside the roster those names come from, and it is the
    # cohort's account of its own retention.
    text = teardown.render_record(
        COHORT,
        teardown.Closed(["assignment-1-ada-l", "grades-ada-l"], 3, 0),
        sealed_on=date(2027, 1, 5),
        semester_end=date(2026, 12, 18),
        registrar="cohort-gradebook.csv, 30 student row(s)",
    )
    assert text.startswith("<!-- SYSTEM-OWNED")
    assert "`assignment-1-ada-l`" in text and "`grades-ada-l`" in text
    assert "| Direct grants and invitations withdrawn | 3 |" in text
    assert "cohort-gradebook.csv, 30 student row(s)" in text
    assert "2026-12-18" in text and "2027-01-05" in text
    assert "Nothing was deleted." in text
    assert "retention period" in text


def test_a_forced_record_says_the_term_end_was_never_declared():
    text = teardown.render_record(
        COHORT,
        teardown.Closed([], 0, 0),
        sealed_on=date(2027, 1, 5),
        semester_end=None,
        registrar="cohort-gradebook.csv, 0 student row(s)",
    )
    assert "`--force`" in text


def test_the_registrar_export_is_summarised_by_its_row_count(monkeypatch):
    # A COUNT, never a row: this line is printed to the run log as well as written into
    # the record, and the log is world-readable.
    monkeypatch.setattr(
        teardown,
        "get_file_content",
        lambda o, r, p: "hertie_email,name,github_handle,a1\na@uni.edu,Ada,ada-l,80\n",
    )
    summary = teardown.registrar_summary(COHORT)
    assert summary == "cohort-gradebook.csv, 1 student row(s)"
    assert "ada-l" not in summary


def test_a_cohort_that_never_distributed_a_grade_says_so(monkeypatch):
    monkeypatch.setattr(teardown, "get_file_content", lambda o, r, p: None)
    assert "NOT here" in teardown.registrar_summary(COHORT)
