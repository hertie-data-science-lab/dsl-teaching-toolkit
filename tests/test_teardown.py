"""Cohort teardown -- what gets frozen, in what order, and what is left resumable.

The order is the feature. Everything that READS the cohort has to happen before anything
in it is frozen (the propagate, the notices, the last site sync), and the sealed
`classroom-config` is the marker every course-side sweep reads as "this cohort is
finished" - so it goes last, and a run that died half way is resumed by running it again.
Both are asserted here against a recorded call sequence rather than trusted to a comment.
"""

from __future__ import annotations

from datetime import date

import pytest

from dsl_course import config_digest, propagate, schedule, source_digest, teardown
from tests.conftest import repo_row

COHORT = "hertie-dsl-demo-f2026"
COURSE = "hertie-dsl-demo-course-e1234"
PR_URL = "https://github.com/hertie-dsl-demo-course-e1234/cm/pull/7"

# A cohort at the end of term: two individual repos, one group repo, two gradebooks, one
# already frozen, the released materials, the website, and the infra.
LISTING = [
    repo_row("classroom-config"),
    repo_row("welcome"),
    repo_row(".github", topics=["dsl-cohort"]),
    repo_row(f"{COHORT}.github.io", visibility="public"),
    repo_row("materials"),
    repo_row("assignment-1", isTemplate=True, topics=["assignment-template"]),
    repo_row("assignment-2-project", isTemplate=True, topics=["assignment-template"]),
    repo_row("assignment-1-ada-l", topics=["submission"]),
    repo_row("assignment-1-bob-b", topics=["submission"]),
    repo_row("assignment-2-project-team-x", topics=["submission"]),
    repo_row("grades-ada-l"),
    repo_row("grades-bob-b", archived=True),
]

# A date that stays in the past, so a fixture pinned to this year does not start refusing
# once the year catches up with it.
DUE = date(2026, 3, 1)


def _sched(archive: date | None, end: date | None = date(2025, 12, 18)):
    return schedule.Schedule(semester_end=end, archive_date=archive)


@pytest.fixture
def org(monkeypatch):
    """A cohort org whose every write is recorded rather than made.

    Returns the call log: `("propagate", cohort)`, `("close", title)`, `("sync", cohort)`,
    `("archive", repo)`, `("put", path)` in the order they were attempted, which is what
    the ordering tests read."""
    calls: list[tuple[str, str]] = []
    # No rendered workflow sets DSL_VERBOSE, so no test may inherit it from the shell
    # either - the per-person lines are exactly what the public-log assertion is about.
    monkeypatch.delenv("DSL_VERBOSE", raising=False)
    monkeypatch.setattr(
        teardown, "list_org_repos", lambda o: [dict(r) for r in LISTING]
    )
    monkeypatch.setattr(teardown.schedule, "load", lambda o: _sched(DUE))
    monkeypatch.setattr(
        teardown,
        "get_file_content",
        lambda o, r, p: "hertie_email,name,github_handle\na@uni.edu,Ada,ada-l\n",
    )
    monkeypatch.setattr(
        teardown.propagate,
        "propagate",
        lambda course, cohort, dry_run=False: (
            calls.append(("propagate", cohort)),
            propagate.Propagated(0, (PR_URL,)),
        )[1],
    )
    monkeypatch.setattr(
        teardown,
        "close_issues_titled",
        lambda repo, title, comment=None: (calls.append(("close", title)), 0)[1],
    )
    monkeypatch.setattr(
        teardown.site,
        "sync_site",
        lambda course, cohort: (calls.append(("sync", cohort)), 0)[1],
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


def _archived(calls) -> list[str]:
    return [r for kind, r in calls if kind == "archive"]


# ------------------------------------------------------------------------ the date gate


@pytest.mark.parametrize(
    ("archive", "due"),
    [
        (date(2026, 2, 16), True),
        (date(2026, 3, 1), True),  # the day itself IS due
        (date(2026, 3, 2), False),
        (None, False),  # nothing to freeze against
    ],
)
def test_archive_due_reads_only_the_archive_date(archive, due):
    assert teardown.archive_due(_sched(archive), date(2026, 3, 1)) is due


def test_a_passed_semester_end_no_longer_opens_the_gate(org, monkeypatch):
    # The sixty-day grace after the term is the whole point: the courses this was measured
    # against went on being pushed to for weeks after their last class.
    monkeypatch.setattr(
        teardown.schedule, "load", lambda o: _sched(date(2099, 1, 1), date(2020, 1, 1))
    )
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert org == []


def test_force_closes_a_cohort_that_has_no_archive_date_at_all(org, monkeypatch):
    monkeypatch.setattr(teardown.schedule, "load", lambda o: _sched(None, None))
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert org == []
    assert teardown.close_out(COURSE, COHORT, dry_run=False, force=True) == 0
    assert ("archive", "classroom-config") in org


def test_a_cohort_with_no_classroom_config_is_refused(org, monkeypatch):
    monkeypatch.setattr(teardown, "list_org_repos", lambda o: [repo_row("welcome")])
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert org == []


def test_an_already_sealed_cohort_is_a_no_op(org, monkeypatch):
    # Idempotence at the top: the sealed classroom-config IS the "this cohort is finished"
    # marker, so a second click does nothing rather than re-freezing a frozen org.
    listing = [dict(r) for r in LISTING]
    listing[0]["archived"] = True
    monkeypatch.setattr(teardown, "list_org_repos", lambda o: listing)
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    assert org == []


# ------------------------------------------------------------------------- the ordering


def test_everything_that_reads_the_cohort_happens_before_anything_is_frozen(org):
    # After the freeze every repo in the org is read-only: the propagate cannot clone what
    # it needs to, the notices cannot be closed, and the site cannot be rebuilt.
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    first_freeze = next(i for i, (kind, _) in enumerate(org) if kind == "archive")
    kinds = [kind for kind, _ in org[:first_freeze]]
    assert kinds[0] == "propagate"
    assert "close" in kinds and "sync" in kinds


def test_the_site_is_synced_before_the_site_repo_is_frozen(org):
    # The deployed site carries the "Cohort archived" row, and this is the last sync that
    # can ship it.
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    assert org.index(("sync", COHORT)) < org.index(("archive", f"{COHORT}.github.io"))


def test_every_repo_in_the_org_is_frozen_in_order_and_the_marker_goes_last(org):
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    assert _archived(org) == [
        # students' work and the briefs they were set, first: they are the record
        "assignment-1",
        "assignment-1-ada-l",
        "assignment-1-bob-b",
        "assignment-2-project",
        "assignment-2-project-team-x",
        "grades-ada-l",
        # then the way IN, so a finished term cannot still be joined
        "welcome",
        # then the released content, the website, and the cohort's own dispatchers
        "materials",
        f"{COHORT}.github.io",
        ".github",
        # and the marker last of all
        "classroom-config",
    ]


def test_the_private_record_is_sealed_last(org):
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    # The record is written, and only then is the repo holding it frozen - and that is the
    # very last thing the run does, so an interrupted run leaves a cohort every sweep still
    # treats as live and this command still resumes.
    assert org[-2:] == [("put", teardown.RECORD_PATH), ("archive", "classroom-config")]


def test_a_frozen_repo_is_not_touched_again(org):
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    assert "grades-bob-b" not in _archived(org)


# --------------------------------------------------------------------- the notices closed


def test_every_notice_the_toolkit_can_have_open_is_closed(org):
    # Each one asks somebody to fix a file in a repo that is about to be read-only, and
    # nothing will ever close them afterwards - an archived repo takes no issue write.
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    closed = {t for kind, t in org if kind == "close"}
    assert {d.title for d in config_digest.COHORT_DIGESTS} <= closed
    assert source_digest.TITLE in closed
    assert teardown.archive_notice_title(DUE) in closed


def test_a_notice_that_will_not_close_still_lets_the_cohort_seal(org, monkeypatch):
    # An issue left open inside a frozen repo is untidy; a cohort left half-closed is not.
    monkeypatch.setattr(teardown, "close_issues_titled", lambda *a, **k: 1)
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert ("archive", "classroom-config") in org


# ------------------------------------------------------------------------ step 0, propagate


def test_the_propagate_runs_first_and_its_url_reaches_the_record(org):
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
    assert org[0] == ("propagate", COHORT)


def test_a_failed_propagate_reds_the_run_but_never_blocks_the_seal(
    org, monkeypatch, capsys
):
    # A course org that never wanted the cohort's edits is still entitled to a closed
    # cohort, and a half-frozen org is the worst of both.
    def boom(course, cohort, dry_run=False):
        raise RuntimeError("gh: HTTP 502")

    monkeypatch.setattr(teardown.propagate, "propagate", boom)
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert ("archive", "classroom-config") in org
    assert "could not carry" in capsys.readouterr().err


def test_without_a_course_org_the_cohort_still_closes(org, monkeypatch):
    # The CLI's --course-org is optional, and the record says what was skipped.
    def boom(*a, **k):
        raise AssertionError("nothing to propagate into")

    monkeypatch.setattr(teardown.propagate, "propagate", boom)
    monkeypatch.setattr(teardown.site, "sync_site", boom)
    assert teardown.close_out("", COHORT, dry_run=False) == 0
    assert ("archive", "classroom-config") in org


# ------------------------------------------------------------------------ failure and dry run


def test_a_repo_that_will_not_freeze_blocks_the_seal(org, monkeypatch):
    # The one failure the marker would lie about: a sealed classroom-config over a repo
    # that is still live tells every sweep the cohort is finished when it is not.
    def archive(o, repo, person=False):
        org.append(("archive", repo))
        return repo != "materials"

    monkeypatch.setattr(teardown, "archive_repo", archive)
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert ("archive", "classroom-config") not in org
    assert ("put", teardown.RECORD_PATH) not in org


def test_an_unwritable_record_leaves_the_cohort_live(org, monkeypatch):
    monkeypatch.setattr(teardown, "put_file", lambda *a, **k: False)
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 1
    assert ("archive", "classroom-config") not in org


def test_a_dry_run_freezes_nothing_and_writes_nothing(org):
    # It still PROPAGATES - in dry-run - because the counts it prints have to be real.
    assert teardown.close_out(COURSE, COHORT) == 0
    assert org == [("propagate", COHORT)]


# ------------------------------------------------------------------------- what is said


def test_no_log_line_names_a_student_or_their_repo(org, capsys):
    # Every faculty workflow runs in the course org's PUBLIC .github, so one
    # `<slug>-<handle>` line there publishes who was in the cohort. DSL_VERBOSE is unset
    # here, exactly as it is in every rendered workflow.
    assert teardown.close_out(COURSE, COHORT, dry_run=False) == 0
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
        teardown.Closed(["assignment-1-ada-l"], ["grades-ada-l"], 0),
        sealed_on=date(2027, 2, 16),
        archive_date=date(2027, 2, 16),
        registrar="cohort-gradebook.csv, 30 student row(s)",
        propagated=PR_URL,
    )
    assert text.startswith("<!-- SYSTEM-OWNED")
    assert "`assignment-1-ada-l`" in text and "`grades-ada-l`" in text
    assert "| Repositories frozen | 2 (1 already were) |" in text
    assert PR_URL in text
    assert "cohort-gradebook.csv, 30 student row(s)" in text
    assert "2027-02-16" in text
    assert "Nobody was revoked." in text and "Nothing was deleted." in text
    assert "retention period" in text


def test_a_forced_record_says_no_archive_date_was_ever_declared():
    text = teardown.render_record(
        COHORT,
        teardown.Closed([], [], 0),
        sealed_on=date(2027, 1, 5),
        archive_date=None,
        registrar="cohort-gradebook.csv, 0 student row(s)",
        propagated="nothing had been edited",
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
