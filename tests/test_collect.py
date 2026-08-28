"""collect pure cores -- the grading-spec parse, the junit -> result.json contract, the
summary glyphs, and the deadline-snapshot logic. The gh/git/subprocess wiring is
deliberately not tested (testing strategy: cover the pure logic, not the fan-out), except
where a snapshot decision IS the logic - which commit gets graded is an academic-integrity
answer, so the pin's every branch is pinned down here with git/gh stubbed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dsl_course import collect, grades
from dsl_course.roster import Student
from dsl_course.schedule import Schedule

SHA = "a" * 40
OTHER_SHA = "b" * 40


@pytest.fixture(autouse=True)
def _grading_deps_present(monkeypatch):
    """Report every grading dependency as installed, by default.

    `_run_tests` refuses to spawn a grader it knows is not importable, and most tests here
    stub the subprocess boundary rather than really running one - so a dev box without
    `nbconvert` on it must not read as a broken runner. A test about the probe itself
    re-patches `find_spec` in its own body and wins. The verdict is cached for the life of
    a grading run, so it is cleared either side."""
    collect._grader_dep_missing.cache_clear()
    monkeypatch.setattr(collect.importlib.util, "find_spec", lambda name: object())
    yield
    collect._grader_dep_missing.cache_clear()


def test_parse_grading_spec_defaults_and_overrides():
    assert collect.parse_grading_spec("") == {
        "type": "individual",
        "autograde": True,
        "tests": "tests",
    }
    # A retired key (`format`, `max_auto`) in a template written before they went is
    # ignored like any other extra, never carried into the spec.
    spec = collect.parse_grading_spec(
        "type: group\nformat: notebook\nautograde: false\nmax_auto: 20\ntests: solution/tests\n"
    )
    assert spec == {"type": "group", "autograde": False, "tests": "solution/tests"}


def test_score_from_junit_counts_only_clean_passes():
    xml = """<testsuite>
      <testcase name="t_pass"/>
      <testcase name="t_fail"><failure>boom</failure></testcase>
      <testcase name="t_err"><error>kaboom</error></testcase>
      <testcase name="t_skip"><skipped/></testcase>
    </testsuite>"""
    result = collect.score_from_junit(xml)
    assert result["max"] == 4 and result["score"] == 1
    passed = {c["name"]: c["passed"] for c in result["tests"]}
    assert passed == {"t_pass": True, "t_fail": False, "t_err": False, "t_skip": False}


def test_score_from_junit_handles_testsuites_root():
    xml = '<testsuites><testsuite><testcase name="a"/></testsuite></testsuites>'
    result = collect.score_from_junit(xml)
    assert result == {"score": 1, "max": 1, "tests": [{"name": "a", "passed": True}]}


def test_the_public_log_never_names_a_submission_repo(monkeypatch, capsys):
    # Every grading log line is world-readable (the workflows run in the course org's
    # PUBLIC .github), and a submission repo is `<slug>-<handle>`. The tag is stable so a
    # marker can match it to the private archive, and carries no handle.
    ref = collect.target_ref("assignment-1-ada-l")
    assert ref == collect.target_ref("assignment-1-ada-l")
    assert ref.startswith("#") and len(ref) == 8
    assert "ada" not in ref
    # The clone-failure paths log the tag, not the repo.
    monkeypatch.setattr(collect, "gh", lambda *a, **k: (1, "clone failed"))
    monkeypatch.setattr(collect, "repo_missing", lambda *a: True)
    collect._grade_target("COHORT", "assignment-1-ada-l", {}, None, "2026-09-08")
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert "ada-l" not in out and ref in out


def test_today_in_cohort_tz_follows_the_schedule_timezone():
    # The fallback grading pin must anchor to the COHORT's timezone, not the (UTC)
    # Actions runner: +14 and -11 are always different calendar days, so a single
    # runner-local date() cannot be right for both.
    east = collect._today_in_cohort_tz(Schedule(timezone="Pacific/Kiritimati"))
    west = collect._today_in_cohort_tz(Schedule(timezone="Pacific/Niue"))
    assert east != west
    assert east == datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()


def test_today_in_cohort_tz_defaults_to_berlin():
    berlin = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    assert collect._today_in_cohort_tz(Schedule()) == berlin  # no timezone declared
    assert collect._today_in_cohort_tz(Schedule(timezone="Nowhere/Fake")) == berlin


# ----------------------------------------------------------------- snapshot CSV (pure)


def test_snapshot_csv_round_trips_and_keeps_a_blank_sha():
    # A blank sha is a RECORD, not a gap: "nothing had been pushed by the deadline". Drop
    # it and grading falls back to the student-datable pin for exactly the repos where a
    # backdated commit would be most valuable.
    rows = [
        ("assignment-1-ben", "", "2026-10-16T00:04:12+00:00"),
        ("assignment-1-anna", SHA, "2026-10-16T00:04:12+00:00"),
    ]
    text = collect.dump_snapshots(rows)
    assert text.splitlines()[0] == "repo,sha,recorded_at"
    assert text.splitlines()[1].startswith("assignment-1-anna,")  # repo-sorted, stable
    assert collect.parse_snapshots(text) == {
        "assignment-1-anna": SHA,
        "assignment-1-ben": "",
    }


def test_parse_snapshots_skips_rows_without_a_repo():
    text = "repo,sha,recorded_at\n,deadbeef,2026-10-16T00:00:00+00:00\n"
    assert collect.parse_snapshots(text) == {}


def test_snapshot_path_lives_under_snapshots():
    assert collect.snapshot_path("assignment-1") == "snapshots/assignment-1.csv"


@pytest.mark.parametrize(
    "deadline,tz,expected",
    [
        # A bare date is the end of that day WHERE THE STUDENTS ARE. Read as end-of-day
        # UTC, "the 13th" ran until 01:59 on the 14th in Berlin summer time - two hours of
        # late work graded as on time.
        ("2026-10-13", "Europe/Berlin", "2026-10-13T21:59:59Z"),
        ("2026-10-13", "America/New_York", "2026-10-14T03:59:59Z"),
        ("2026-10-13", "UTC", "2026-10-13T23:59:59Z"),
        # A naive datetime is a local one, for the same reason.
        ("2026-10-15T12:00:00", "Europe/Berlin", "2026-10-15T10:00:00Z"),
        # An explicit offset already names an instant - only re-expressed, never moved.
        ("2026-10-15T23:59:59+02:00", "America/New_York", "2026-10-15T21:59:59Z"),
        # No zone given: the schedule's own default, exactly as _today_in_cohort_tz uses.
        ("2026-10-13", None, "2026-10-13T21:59:59Z"),
    ],
)
def test_until_param_is_a_utc_z_stamp_of_the_cohorts_own_deadline(
    deadline, tz, expected
):
    # A `+HH:MM` offset in a query string would be read as a space, silently shifting the
    # cutoff by hours - so the API cutoff is always normalised to UTC Z.
    assert collect._until_param(deadline, tz) == expected


def test_an_unparseable_deadline_still_raises_rather_than_matching_nothing():
    with pytest.raises(ValueError):
        collect.local_deadline("last thursday", "Europe/Berlin")


# ------------------------------------------------------------------------------ the pin


def _git_stub(rev_list_sha: str = "", sha_in_clone: bool = True):
    """A fake `git` recording its calls: `cat-file -e` answers whether the snapshot sha is
    in the clone, `rev-list` answers the date-based fallback."""
    calls: list[tuple[str, ...]] = []

    def fake_git(*args, **kwargs):
        calls.append(args)
        if "cat-file" in args:
            return (0 if sha_in_clone else 1, "")
        if "rev-list" in args:
            return (0, rev_list_sha) if rev_list_sha else (1, "")
        return (0, "")

    return fake_git, calls


def test_pin_commit_prefers_the_snapshot_sha_and_never_looks_at_dates(monkeypatch):
    fake_git, calls = _git_stub(rev_list_sha=OTHER_SHA)
    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-15T23:59:59+02:00", SHA) == SHA
    assert any("checkout" in c and SHA in c for c in calls)
    # the whole point: the client-supplied committer date is never consulted
    assert not any("rev-list" in c for c in calls)


def test_pin_commit_blank_snapshot_is_a_recorded_non_submission(monkeypatch):
    # "" means the server saw no commit by the deadline. Falling back to rev-list here
    # would re-open the hole: a later push backdated before the deadline would grade.
    fake_git, calls = _git_stub(rev_list_sha=OTHER_SHA)
    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-15T23:59:59+02:00", "") is None
    assert calls == []


def test_pin_commit_fails_when_the_snapshot_sha_is_gone_after_a_rewrite(monkeypatch):
    # A force-push after the deadline rewrote history and the pinned commit can't be
    # fetched back. Falling back to the committer-date pin here would grade the rewritten
    # history - turning a detected tamper into a successful one. So the target FAILS (None),
    # a recovery fetch is attempted, and the client-datable rev-list is never consulted.
    fake_git, calls = _git_stub(rev_list_sha=OTHER_SHA, sha_in_clone=False)
    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-15T23:59", SHA) is None
    assert any("fetch" in c for c in calls)  # tried to recover the frozen commit
    assert not any("rev-list" in c for c in calls)  # never the date fallback


def test_pin_commit_recovers_the_snapshot_sha_via_fetch(monkeypatch):
    # A rewrite orphans the pinned commit but it survives server-side until GC; fetching it
    # by sha lets us grade exactly what was frozen, not the rewritten history.
    seen = {"cat_file": 0}
    calls: list[tuple[str, ...]] = []

    def fake_git(*args, **kwargs):
        calls.append(args)
        if "cat-file" in args:
            seen["cat_file"] += 1
            return (1, "") if seen["cat_file"] == 1 else (0, "")  # gone, then fetched
        return (0, "")

    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-15T23:59", SHA) == SHA
    assert any("fetch" in c and SHA in c for c in calls)
    assert any("checkout" in c and SHA in c for c in calls)


def test_pin_commit_without_a_snapshot_uses_the_date_pin(monkeypatch):
    fake_git, calls = _git_stub(rev_list_sha=OTHER_SHA)
    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-13") == OTHER_SHA
    before = [a for c in calls for a in c if a.startswith("--before=")]
    assert before == ["--before=2026-10-13 23:59:59"]  # bare date -> end of day


def test_pin_commit_no_commit_at_all_is_none(monkeypatch):
    fake_git, _calls = _git_stub(rev_list_sha="")
    monkeypatch.setattr(collect, "git", fake_git)
    assert collect._pin_commit(Path("/repo"), "2026-10-13") is None


# -------------------------------------------------------- notebook -> importable script

_JUNIT = '<testsuite><testcase name="test_solve"/></testsuite>'


def _fake_nbconvert(monkeypatch, written_suffix: str | None):
    """Stub the sandboxed subprocess boundary (`_run_limited`) `_run_tests` crosses.
    `written_suffix` is the extension nbconvert is pretended to have chosen for its script
    output (None = it wrote nothing at all); pytest always drops a passing junit report. The
    stub returns True (the process exited on its own) - the killpg/timeout path is False."""

    def fake_run_limited(argv, *, cwd, env, timeout):
        if "nbconvert" in argv and written_suffix is not None:
            nb = Path(argv[-1])
            (nb.parent / (nb.stem + written_suffix)).write_text(
                "def solve(xs):\n    return xs\n"
            )
        if "pytest" in argv:
            report = next(a for a in argv if a.startswith("--junitxml="))
            Path(report.split("=", 1)[1]).write_text(_JUNIT)
        return True

    monkeypatch.setattr(collect, "_run_limited", fake_run_limited)


@pytest.mark.parametrize("suffix", [".txt", ""])
def test_run_tests_renames_a_non_py_nbconvert_output(
    monkeypatch, tmp_path, capsys, suffix
):
    # nbconvert takes the output extension from metadata.language_info.file_extension, so a
    # notebook with empty metadata (or only a kernelspec) converts to starter.txt - or to a
    # bare `starter` - and the hidden tests' `from starter import ...` fails for EVERY
    # submission. The score would be a silent 0/n, so the output is renamed back to .py.
    _fake_nbconvert(monkeypatch, suffix)
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.ipynb").write_text("{}")
    tests = tmp_path / "hidden"
    tests.mkdir()
    (tests / "test_x.py").write_text("from starter import solve\n")

    result = collect._run_tests(work, tests)

    assert (work / "starter.py").read_text().startswith("def solve")
    assert not (work / f"starter{suffix}").exists()  # renamed, not copied
    assert result == {
        "score": 1,
        "max": 1,
        "tests": [{"name": "test_solve", "passed": True}],
    }
    # The rename is not silent - but names no file: the notebook is student-named and the
    # log is public.
    out = capsys.readouterr().out
    assert "renamed the stray output" in out and "starter" not in out


def test_run_tests_leaves_a_correct_py_conversion_alone(monkeypatch, tmp_path):
    # The happy path must not be disturbed: a notebook declaring `file_extension: ".py"`
    # already converts to starter.py, and a stray same-stem .txt is not the script.
    _fake_nbconvert(monkeypatch, ".py")
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.ipynb").write_text("{}")
    (work / "starter.txt").write_text("notes, not code")
    tests = tmp_path / "hidden"
    tests.mkdir()

    assert collect._run_tests(work, tests)["score"] == 1
    assert (work / "starter.py").read_text().startswith("def solve")
    assert (work / "starter.txt").read_text() == "notes, not code"  # untouched


def test_run_tests_converts_a_notebook_the_template_never_declared(
    monkeypatch, tmp_path
):
    # Conversion follows what the submission HOLDS, not a `format:` the template declared:
    # a student who worked in a notebook is graded, where the old py/notebook switch
    # imported nothing and scored them zero.
    _fake_nbconvert(monkeypatch, ".py")
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.ipynb").write_text("{}")
    tests = tmp_path / "hidden"
    tests.mkdir()

    assert collect._run_tests(work, tests)["score"] == 1
    assert (work / "starter.py").exists()


def test_run_tests_converts_nothing_when_the_submission_holds_no_notebook(
    monkeypatch, tmp_path
):
    _fake_nbconvert(monkeypatch, ".txt")  # would fire if the walk found an .ipynb
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.py").write_text("def solve():\n    return 1\n")
    tests = tmp_path / "hidden"
    tests.mkdir()

    assert collect._run_tests(work, tests)["score"] == 1
    assert not (work / "starter.txt").exists()


def test_run_tests_abandons_the_submission_on_the_first_convert_timeout(
    monkeypatch, tmp_path, capsys
):
    # Tolerating a timeout PER notebook multiplies the budget: 100 hanging .ipynb would cost
    # 100 x RUN_TIMEOUT, blow the 6h Actions cap and kill the job before the fire-once sentinel
    # is written - so the next hourly tick regrades the same submission, for ever. The first
    # timeout abandons the submission instead; the caller records the usual zero.
    calls: list[list[str]] = []

    def always_times_out(argv, *, cwd, env, timeout):
        calls.append(argv)
        return False

    monkeypatch.setattr(collect, "_run_limited", always_times_out)
    work = tmp_path / "sub"
    work.mkdir()
    for stem in ("a", "b", "c"):
        (work / f"{stem}.ipynb").write_text("{}")
    tests = tmp_path / "hidden"
    tests.mkdir()

    assert collect._run_tests(work, tests) is None
    assert len(calls) == 1  # bailed on the FIRST timeout, not once per notebook
    assert "abandoning this submission" in capsys.readouterr().err


@pytest.mark.parametrize(
    "submission, module",
    [("starter.py", "pytest"), ("starter.ipynb", "nbconvert")],
)
def test_run_tests_names_a_grading_dependency_the_runner_does_not_have(
    monkeypatch, tmp_path, capsys, submission, module
):
    # `_run_limited` sends the child's output to DEVNULL and calls ANY exit code a completed
    # run, so `python -m pytest` dying on "No module named pytest" was indistinguishable
    # from a failing submission: a grading-failed zero for every target, a red cron with no
    # sentinel, and the same again every hour for the rest of the term. Name the fault.
    monkeypatch.setattr(collect.importlib.util, "find_spec", lambda name: None)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        collect,
        "_run_limited",
        lambda argv, **kw: spawned.append(argv) or True,
    )
    work = tmp_path / "sub"
    work.mkdir()
    (work / submission).write_text("{}")
    tests = tmp_path / "hidden"
    tests.mkdir()

    assert collect._run_tests(work, tests) is None
    assert spawned == [], f"{module} was invoked although it is not importable"
    assert f"`{module}` is not installed" in capsys.readouterr().err


def test_stray_conversion_ignores_a_same_stem_directory(tmp_path):
    (tmp_path / "starter").mkdir()  # extensionless candidate that is not a file
    assert collect._stray_conversion(tmp_path / "starter.ipynb") is None


# ------------------------------------------------------------------- target discovery


_STUDENTS = [
    Student("a@x", "Anna", "anna-adams", ""),
    Student("b@x", "Ben", "ben-baker", ""),
    Student("c@x", "Not yet", "", ""),  # enrolled, not onboarded
]
_TEAMS = {"assignment-4-project": {"team-y": ["carla"], "team-x": ["anna-adams"]}}


def test_submission_targets_individual_skips_unonboarded(monkeypatch):
    monkeypatch.setattr(collect.roster, "load", lambda org: _STUDENTS)
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    assert collect.submission_targets("Cohort", "assignment-1", False) == [
        ("assignment-1-anna-adams", "anna-adams", ["anna-adams"]),
        ("assignment-1-ben-baker", "ben-baker", ["ben-baker"]),
    ]


def test_submission_targets_individual_ignores_teams_csv(monkeypatch):
    # Replaces the old "infer group from teams.csv" test: that inference is REMOVED. A student
    # can grow teams.csv by opening a "Join team" issue naming an INDIVIDUAL assignment, so
    # with is_group=False submission_targets must never consult teams.csv - the faculty-owned
    # schedule/grading.yml decides the kind upstream (resolve_is_group). Here a team row exists
    # for the slug, yet the individual (one-repo-per-student) targets are returned regardless.
    monkeypatch.setattr(collect.teams, "load", lambda org: _TEAMS)
    monkeypatch.setattr(collect.roster, "load", lambda org: _STUDENTS)
    assert collect.submission_targets("Cohort", "assignment-4-project", False) == [
        ("assignment-4-project-anna-adams", "anna-adams", ["anna-adams"]),
        ("assignment-4-project-ben-baker", "ben-baker", ["ben-baker"]),
    ]


def test_submission_targets_group_without_teams_is_empty(monkeypatch):
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    assert collect.submission_targets("Cohort", "assignment-4-project", True) == []


# -------------------------------------------------------------------- taking a snapshot


@pytest.mark.parametrize(
    "response,expected",
    [
        ((0, SHA), SHA),
        ((0, ""), ""),  # repo exists, no commit that early -> recorded non-submission
        # 404: the repo ISN'T THERE - distinct from empty, so an all-absent set can be skipped
        ((1, "gh: Not Found (HTTP 404)"), collect._REPO_ABSENT),
        (
            (1, "Git Repository is empty (HTTP 409)"),
            "",
        ),  # exists but empty -> freeze as zero
        ((1, "server error (HTTP 500)"), None),  # transient -> the caller must retry
    ],
)
def test_snapshot_sha_maps_api_outcomes(monkeypatch, response, expected):
    monkeypatch.setattr(collect, "gh", lambda *a, **k: response)
    assert (
        collect._snapshot_sha("Cohort", "assignment-1-anna", "2026-10-13") == expected
    )


def test_snapshot_sha_asks_the_api_for_one_commit_before_a_utc_cutoff(monkeypatch):
    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(collect, "gh", lambda *a, **k: seen.append(a) or (0, SHA))
    collect._snapshot_sha("Cohort", "assignment-1-anna", "2026-10-15T23:59:59+02:00")
    args = seen[0]
    assert "repos/Cohort/assignment-1-anna/commits" in args
    assert "until=2026-10-15T21:59:59Z" in args and "per_page=1" in args


def _stub_snapshot_write(monkeypatch, shas: dict[str, str | None], existing=None):
    """Wire snapshot_assignment onto stubs; returns the (path, text) writes it makes."""
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(collect, "load_snapshots", lambda org, slug: existing)
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group=False, teams_key=None: [
            (r, r.split("-")[-1], []) for r in shas
        ],
    )
    monkeypatch.setattr(
        collect, "_snapshot_sha", lambda org, repo, deadline: shas[repo]
    )
    monkeypatch.setattr(
        collect,
        "put_file",
        lambda org, repo, path, content, msg: (
            written.append((path, content.decode())) or True
        ),
    )
    return written


def test_snapshot_assignment_records_one_row_per_repo(monkeypatch):
    written = _stub_snapshot_write(
        monkeypatch, {"assignment-1-anna": SHA, "assignment-1-ben": ""}
    )
    assert collect.snapshot_assignment(
        "Cohort", "assignment-1", "2026-10-15T23:59:59+02:00", is_group=False
    )
    ((path, text),) = written
    assert path == "snapshots/assignment-1.csv"
    assert collect.parse_snapshots(text) == {
        "assignment-1-anna": SHA,
        "assignment-1-ben": "",
    }
    # recorded_at is the SERVER's clock, not anything the schedule or a student supplied
    stamps = {row.split(",")[2] for row in text.splitlines()[1:]}
    assert len(stamps) == 1 and stamps.pop().endswith("+00:00")


def test_snapshot_assignment_never_overwrites_an_existing_snapshot(monkeypatch):
    # Write-once is the whole guarantee: a later run must not be able to move the pin.
    def boom(*args, **kwargs):
        raise AssertionError("an existing snapshot must never be re-taken")

    monkeypatch.setattr(collect, "load_snapshots", lambda org, slug: {"r": SHA})
    monkeypatch.setattr(collect, "_snapshot_sha", boom)
    monkeypatch.setattr(collect, "put_file", boom)
    assert (
        collect.snapshot_assignment(
            "Cohort", "assignment-1", "2026-10-15T23:59", is_group=False
        )
        is True
    )


def test_snapshot_assignment_writes_nothing_when_a_lookup_fails(monkeypatch):
    # A transient API failure must not be frozen into a never-rewritten record: abandon
    # the whole file so the next hourly tick rebuilds it.
    written = _stub_snapshot_write(
        monkeypatch, {"assignment-1-anna": SHA, "assignment-1-ben": None}
    )
    assert (
        collect.snapshot_assignment(
            "Cohort", "assignment-1", "2026-10-15T23:59", is_group=False
        )
        is False
    )
    assert written == []


def test_snapshot_assignment_with_no_targets_yet_writes_nothing_and_is_not_an_error(
    monkeypatch, capsys
):
    # An assignment nobody can submit to yet (nobody onboarded, no teams, not handed out)
    # must be a no-op, not an hourly failure - AND the snapshot is write-once, so an empty
    # one would pin it to "nothing submitted" for ever. Nothing written, green, retried.
    def boom(*args, **kwargs):
        raise AssertionError("an empty snapshot must never be frozen")

    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(collect, "load_snapshots", lambda org, slug: None)
    monkeypatch.setattr(collect, "submission_targets", lambda *a, **k: [])
    monkeypatch.setattr(collect, "put_file", boom)
    assert (
        collect.snapshot_assignment(
            "Cohort", "assignment-1", "2026-10-15T23:59", is_group=False
        )
        is True
    )
    assert "nothing to freeze yet" in capsys.readouterr().out


def test_load_snapshots_distinguishes_a_missing_file_from_blank_shas(monkeypatch):
    monkeypatch.setattr(collect, "get_file_content", lambda *a: None)
    assert collect.load_snapshots("Cohort", "assignment-1") is None
    monkeypatch.setattr(
        collect,
        "get_file_content",
        lambda *a: "repo,sha,recorded_at\nr,,2026-10-16T00:00:00+00:00\n",
    )
    assert collect.load_snapshots("Cohort", "assignment-1") == {"r": ""}


# --------------------------------------------------------- collect() threads it through


def _clone_writing(grading: str):
    """A `gh` whose `repo clone` of the template's solution branch is faked into a real
    directory carrying `grading`."""

    def fake_gh(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            dest = Path(args[3])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "grading.yml").write_text(grading)
            (dest / "tests").mkdir(exist_ok=True)
        return (0, "")

    return fake_gh


_fake_solution_clone = _clone_writing("autograde: true\nmax_auto: 2\n")


def _captured_writes(monkeypatch) -> list[tuple[str, str]]:
    """The (path, text) writes collect makes into classroom-config."""
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        collect,
        "put_file",
        lambda org, repo, path, content, msg: (
            written.append((path, content.decode())) or True
        ),
    )
    return written


def _stub_collect(monkeypatch, snapshots):
    monkeypatch.setattr(collect, "gh", _fake_solution_clone)
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group=None, teams_key=None: [
            (f"{slug}-{h}", h, [h]) for h in ("anna", "ben", "cara")
        ],
    )
    monkeypatch.setattr(collect, "load_snapshots", lambda org, slug: snapshots)
    monkeypatch.setattr(collect, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(collect, "get_file_content", lambda *a: "")
    seen: dict[str, str | None] = {}

    def fake_grade(cohort_org, repo, spec, tests_src, deadline, snapshot=None):
        seen[repo] = snapshot
        return {"score": 1, "max": 2, "tests": []}

    monkeypatch.setattr(collect, "_grade_target", fake_grade)
    return seen


def test_collect_passes_each_repos_own_snapshot_entry_to_grading(monkeypatch):
    # The wiring bug worth a test: loading the snapshot but grading the wrong commit.
    seen = _stub_collect(
        monkeypatch, {"assignment-1-anna": SHA, "assignment-1-ben": ""}
    )
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    assert seen["assignment-1-anna"] == SHA
    assert seen["assignment-1-ben"] == ""  # recorded non-submission, graded as such
    # cara is ABSENT from the snapshot (a repo present at grading but not in the freeze).
    # That must NOT silently drop to the student-controlled committer-date pin - it is scored
    # zero, so _grade_target is never called for it.
    assert "assignment-1-cara" not in seen


def test_collect_without_a_snapshot_grades_on_dates_and_says_so(monkeypatch, capsys):
    seen = _stub_collect(monkeypatch, None)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    assert set(seen.values()) == {None}
    err = capsys.readouterr().err
    assert "snapshots/assignment-1.csv" in err and "students control" in err


def test_collect_resolves_the_cohort_type_from_the_entry_not_the_cohort_name(
    monkeypatch,
):
    # schedule.yml is keyed on the SLUG; a `cohort_dest_repo` makes the cohort-side name
    # differ from that key, so looking the entry up by name finds nothing and a declared
    # group assignment quietly grades one repo per student. Resolve it by
    # course_source_repo, exactly as assignment_is_group does.
    from dsl_course.schedule import AssignmentEntry

    entry = AssignmentEntry(
        course_source_repo="assignment-4-project-f2026",
        cohort_dest_repo="group-project",
        due_datetime=datetime(2026, 11, 15, tzinfo=ZoneInfo("Europe/Berlin")),
        type="group",
    )
    _stub_collect(monkeypatch, None)
    monkeypatch.setattr(
        collect.schedule, "load", lambda org: Schedule(assignments={"project": entry})
    )
    kinds: list[bool | None] = []
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group=None, teams_key=None: (
            kinds.append(is_group) or [(f"{slug}-team-x", "team-x", ["anna", "ben"])]
        ),
    )
    written = _captured_writes(monkeypatch)

    assert collect.collect("Course", "assignment-4-project-f2026", "Cohort") == 0

    assert kinds == [True]  # graded per TEAM, as the cohort declared
    # every cohort-side artefact keys on the cohort name, and the per-target archive on the
    # target's own key (the loop variable no longer shadows the schedule key)
    assert ("autograde/group-project/team-x.json") in [p for p, _t in written]
    assert "grades/group-project.csv" in [p for p, _t in written]
    # The team's count goes to `autograde_score` on every member's row - NOT to
    # `team_score`, which is the marker's shared mark and must reach them unclaimed.
    (csv_text,) = [t for p, t in written if p == "grades/group-project.csv"]
    rows = {r.github_handle: r for r in grades.parse_grades(csv_text)}
    assert set(rows) == {"anna", "ben"}
    for r in rows.values():
        assert r.team == "team-x"
        assert r.autograde_score != ""
        assert r.team_score == ""


def test_collect_records_a_skip_when_the_template_has_no_solution_branch(monkeypatch):
    # Fire-once: no marker means the scheduler re-clones this template and re-decides the
    # same skip on every hourly tick, for ever. Hand-marked assignments are common.
    monkeypatch.setattr(collect, "gh", lambda *a, **k: (1, "no such branch"))
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    ((path, text),) = written
    assert path == "autograde/assignment-1/_skipped.json"
    assert collect.SOLUTION_BRANCH in text  # the record says why


def test_collect_records_a_skip_when_autograde_is_disabled(monkeypatch):
    monkeypatch.setattr(collect, "gh", _clone_writing("autograde: false\n"))
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    ((path, text),) = written
    assert path == "autograde/assignment-1/_skipped.json"
    assert "autograde: false" in text


def test_collect_dry_run_records_no_skip(monkeypatch):
    # A dry run must not fire the marker - that would silence the real run that follows.
    monkeypatch.setattr(collect, "gh", _clone_writing("autograde: false\n"))
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort", dry_run=True) == 0
    assert written == []


def test_collect_with_nothing_gradable_records_a_skip_and_succeeds(monkeypatch, capsys):
    # Every target WAS examined and none of them yielded a grade (here: a group assignment
    # whose team has no members). The snapshot is frozen, so an hourly retry would see
    # exactly this and go red every hour - record the skip and stay green.
    _stub_collect(monkeypatch, {"assignment-1-team-x": ""})
    monkeypatch.setattr(collect, "gh", _clone_writing("type: group\nautograde: true\n"))
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group=None, teams_key=None: [
            (f"{slug}-team-x", "team-x", [])
        ],
    )
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    (skip,) = [(p, t) for p, t in written if p.endswith(collect.SKIP_RECORD)]
    assert skip[0] == "autograde/assignment-1/_skipped.json"
    assert "nothing gradable" in skip[1]
    assert "nothing gradable" in capsys.readouterr().out  # and it is not silent


def test_collect_with_every_repo_unreadable_fails_and_records_nothing(
    monkeypatch, capsys
):
    # Infrastructure, not a verdict: repos not generated yet, or the API having a bad
    # afternoon. Recording a permanent "not machine-graded" on the strength of an outage
    # would lose the scores for good, so the run goes red with nothing written and the
    # next hourly tick retries. This is the one empty case that is NOT a skip.
    _stub_collect(
        monkeypatch, None
    )  # no snapshot: every repo goes through the clone path
    monkeypatch.setattr(collect, "_grade_target", lambda *a, **k: None)
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert written == []  # above all: no _skipped.json
    assert "could be read" in capsys.readouterr().err


def test_collect_with_every_target_failing_to_grade_records_nothing(
    monkeypatch, capsys
):
    # Every repo cloned fine and every grading run broke the same way - a bad runner image, a
    # missing dependency, an rlimit the host won't satisfy. Recording that would write a whole
    # cohort of write-once zeros and then lock them in behind the fire-once sentinel, so it is
    # treated like the unreachable case: nothing written, red run, next tick retries.
    _stub_collect(monkeypatch, None)
    monkeypatch.setattr(
        collect,
        "_grade_target",
        lambda *a, **k: collect._zero_result(collect.GRADE_FAILED_NOTE),
    )
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert written == []  # no grades CSV, no archives, above all no _graded.json
    assert "runner-wide failure" in capsys.readouterr().err


def test_collect_records_a_cohort_of_genuine_non_submissions(monkeypatch):
    # The guard above keys on the failed-to-run note ALONE. A cohort that simply didn't submit
    # is a real verdict: the zeros are recorded and the assignment IS marked machine-graded,
    # or nobody's deadline would ever land.
    _stub_collect(monkeypatch, None)
    monkeypatch.setattr(
        collect,
        "_grade_target",
        lambda *a, **k: collect._zero_result("no submission on/before 2026-11-15"),
    )
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    assert "autograde/assignment-1/_graded.json" in [p for p, _t in written]


def test_template_is_group_reads_the_solution_branch_grading_yml(monkeypatch):
    seen = {}

    def fake_get(org, repo, path, ref=""):
        seen.update(org=org, repo=repo, path=path, ref=ref)
        return "type: group\nformat: py\n"

    monkeypatch.setattr(collect, "get_file_content", fake_get)
    assert collect.template_is_group("Course-Org", "assignment-4-project-f2026")
    assert seen == {
        "org": "Course-Org",
        "repo": "assignment-4-project-f2026",
        "path": collect.GRADING_FILE,
        "ref": collect.SOLUTION_BRANCH,
    }


def test_template_is_group_defaults_to_individual_without_grading_yml(monkeypatch):
    # No solution branch / no grading.yml -> the contents fetch misses -> individual.
    monkeypatch.setattr(collect, "get_file_content", lambda *a, **k: None)
    assert not collect.template_is_group("Course-Org", "assignment-1-f2026")


def test_assignment_is_group_prefers_the_cohort_schedule(monkeypatch):
    # schedule.yml's assignments.<slug>.type wins; grading.yml is only the fallback.
    from dsl_course.schedule import AssignmentEntry, Schedule

    entry = AssignmentEntry(
        course_source_repo="assignment-4-project-f2026",
        due_datetime=datetime(2026, 11, 15, tzinfo=ZoneInfo("Europe/Berlin")),
    )
    sched = Schedule(assignments={"assignment-4-project": entry})
    monkeypatch.setattr(collect.schedule, "load", lambda org: sched)
    calls = []
    monkeypatch.setattr(
        collect,
        "template_is_group",
        lambda org, template: calls.append(template) or True,
    )
    # no cohort declaration -> falls through to grading.yml
    entry.type = None
    assert collect.assignment_is_group(
        "Course", "Cohort-f2026", "assignment-4-project-f2026"
    )
    assert calls == ["assignment-4-project-f2026"]
    # cohort says individual -> grading.yml is NOT consulted
    entry.type = "individual"
    calls.clear()
    assert not collect.assignment_is_group(
        "Course", "Cohort-f2026", "assignment-4-project-f2026"
    )
    assert calls == []
    # cohort says group -> group, regardless of the template
    entry.type = "group"
    assert collect.assignment_is_group(
        "Course", "Cohort-f2026", "assignment-4-project-f2026"
    )


# ---------------------------------------------------------- autograde sandbox (fix 1)
# These run REAL pytest against a REAL submission: the only way to prove that nothing a
# student committed can move their machine score. Each tampered checkout must yield exactly
# the honest score - the student's own code, judged by the hidden tests, and nothing else.


def _sandbox(tmp_path, starter_body: str):
    """A checked-out submission + a hidden-tests dir. The student's `solve` is WRONG (returns
    0), so the honest score is 0/2; a successful tamper would raise it."""
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.py").write_text(starter_body)
    tests = tmp_path / "hidden"
    tests.mkdir()
    (tests / "test_solve.py").write_text(
        "from starter import solve\n"
        "def test_one():\n    assert solve(1) == 2\n"
        "def test_two():\n    assert solve(3) == 4\n"
    )
    return work, tests


def test_run_tests_honest_baseline(tmp_path):
    # Ground truth: the student's wrong code scores 0/2 with no tampering in play.
    work, tests = _sandbox(tmp_path, "def solve(x):\n    return 0\n")
    assert collect._run_tests(work, tests) == {
        "score": 0,
        "max": 2,
        "tests": [
            {"name": "test_one", "passed": False},
            {"name": "test_two", "passed": False},
        ],
    }


def test_run_tests_ignores_a_committed_report_xml(tmp_path):
    # A pre-baked report claiming full marks must never be scored: the real report is written
    # OUTSIDE the checkout, so the committed one is irrelevant.
    work, tests = _sandbox(tmp_path, "def solve(x):\n    return 0\n")
    (work / "report.xml").write_text(
        '<testsuite><testcase name="a"/><testcase name="b"/></testsuite>'
    )
    result = collect._run_tests(work, tests)
    assert result["score"] == 0 and result["max"] == 2  # honest, not the forged 2/2


def test_run_tests_ignores_a_committed_conftest(tmp_path):
    # A conftest.py that would suppress collection (0 tests -> 0/0) must not be honored: the
    # score stays the honest 0/2, proving the student's conftest never loaded.
    work, tests = _sandbox(tmp_path, "def solve(x):\n    return 0\n")
    (work / "conftest.py").write_text(
        "def pytest_ignore_collect(collection_path, config):\n    return True\n"
    )
    result = collect._run_tests(work, tests)
    assert result["max"] == 2  # both hidden tests still collected
    assert result["score"] == 0


def test_run_tests_ignores_a_committed_sitecustomize(tmp_path):
    # sitecustomize.py runs at interpreter startup from any sys.path dir. Here it would inject
    # a fake `starter` whose solve is CORRECT (0/2 -> 2/2). Stripped before the run, it never
    # loads, so the student's real (wrong) code is what gets scored.
    work, tests = _sandbox(tmp_path, "def solve(x):\n    return 0\n")
    (work / "sitecustomize.py").write_text(
        "import sys, types\n"
        "m = types.ModuleType('starter')\n"
        "m.solve = lambda x: x + 1\n"
        "sys.modules['starter'] = m\n"
    )
    result = collect._run_tests(work, tests)
    assert result["score"] == 0 and result["max"] == 2  # the tamper did not inflate it


def test_run_tests_student_module_cannot_shadow_a_stdlib_import(tmp_path):
    # A student `operator.py` in the checkout must not shadow the stdlib module a hidden test
    # imports - that would let them force every assertion True. The submission is appended to
    # sys.path AFTER the stdlib, so the real module wins the import and the wrong solve (0)
    # still scores 0; if it were prepended, operator.eq would return True and forge a 1/1.
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.py").write_text("def solve(x):\n    return 0\n")
    (work / "operator.py").write_text(
        "def eq(a, b):\n    return True\n"
    )  # the forge attempt
    tests = tmp_path / "hidden"
    tests.mkdir()
    (tests / "test_solve.py").write_text(
        "import operator\n"
        "from starter import solve\n"
        "def test_one():\n    assert operator.eq(solve(1), 2)\n"
    )
    result = collect._run_tests(work, tests)
    assert result["score"] == 0 and result["max"] == 1  # the real stdlib operator won


def test_run_tests_removes_the_git_credential_before_student_code_runs(tmp_path):
    # The clone's .git/config persists the bot credential; env-stripping doesn't reach it, so
    # it must be gone from the tree before student code can read it.
    work, tests = _sandbox(tmp_path, "def solve(x):\n    return x + 1\n")
    (work / ".git").mkdir()
    (work / ".git" / "config").write_text(
        "[http]\n  extraheader = AUTHORIZATION: bearer x\n"
    )
    collect._run_tests(work, tests)
    assert not (work / ".git").exists()


# ------------------------------------------------------- fire-once ordering (fix 2, 6)


def test_collect_leaves_no_marker_when_the_run_dies_mid_loop(monkeypatch):
    # If the run dies part-way through grading, the fire-once marker (the autograde/<slug>/
    # archives) must be ABSENT so the next tick regrades - not left present over an unwritten
    # grades CSV, which would silently un-grade everyone.
    _stub_collect(monkeypatch, {"assignment-1-anna": SHA, "assignment-1-ben": SHA})
    n = {"calls": 0}

    def dying_grade(*a, **k):
        n["calls"] += 1
        if n["calls"] == 2:
            raise RuntimeError("the clone host fell over mid-run")
        return {"score": 1, "max": 2, "tests": []}

    monkeypatch.setattr(collect, "_grade_target", dying_grade)
    written = _captured_writes(monkeypatch)
    with pytest.raises(RuntimeError):
        collect.collect("Course", "assignment-1-f2026", "Cohort")
    # the first target graded fine, but NOTHING was written - no archive, no grades CSV
    assert written == []


def test_collect_holds_the_marker_when_some_repos_are_unreachable(monkeypatch):
    # Partial outage: some repos graded, one couldn't be read. The scores are recorded
    # (write-once), but the marker is held back and the run returns non-zero, so the next tick
    # retries the missing one rather than treating the assignment as fully machine-graded.
    _stub_collect(monkeypatch, None)

    def grade(cohort_org, repo, *a, **k):
        return None if repo.endswith("cara") else {"score": 1, "max": 2, "tests": []}

    monkeypatch.setattr(collect, "_grade_target", grade)
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    paths = [p for p, _ in written]
    assert "grades/assignment-1.csv" in paths  # the reachable scores are recorded
    assert not any(p.startswith("autograde/") for p in paths)  # marker held back


# ------------------------------------------------------ snapshot integrity (fix 3B, 4b)


def test_snapshot_assignment_requires_and_passes_is_group_through(monkeypatch):
    # snapshot_assignment no longer resolves group-vs-individual itself (the removed
    # `_snapshot_is_group` did, weakly): the caller resolves it once via resolve_is_group and
    # passes it in - and the argument is REQUIRED (keyword-only), so a forgetful future caller
    # can't silently freeze individual repos for a group assignment. Never inferred from
    # student-writable teams.csv (see test_submission_targets_*_ignores_*).
    monkeypatch.setattr(collect, "load_snapshots", lambda org, slug: None)
    seen: list[bool] = []
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group, teams_key=None: seen.append(is_group) or [],
    )
    collect.snapshot_assignment("Cohort", "assignment-1", "2026-11-15", is_group=False)
    collect.snapshot_assignment("Cohort", "assignment-1", "2026-11-15", is_group=True)
    assert seen == [False, True]  # exactly what each caller passed
    with pytest.raises(
        TypeError
    ):  # omitting it is a caller bug, caught at the signature
        collect.snapshot_assignment("Cohort", "assignment-1", "2026-11-15")


def test_snapshot_assignment_skips_when_every_repo_is_absent(monkeypatch, capsys):
    # Every target is ABSENT (404: not generated yet, a handout typo). Freezing this write-once
    # snapshot would pin the whole assignment to "nobody submitted" for ever; write nothing and
    # let a later tick take it once the repos exist.
    _stub_snapshot_write(
        monkeypatch,
        {
            "assignment-1-anna": collect._REPO_ABSENT,
            "assignment-1-ben": collect._REPO_ABSENT,
        },
    )

    def boom(*a, **k):
        raise AssertionError("an all-absent snapshot must never be frozen")

    monkeypatch.setattr(collect, "put_file", boom)
    assert (
        collect.snapshot_assignment(
            "Cohort", "assignment-1", "2026-10-15T23:59", is_group=False
        )
        is True
    )
    assert "every target repo is absent" in capsys.readouterr().out


def test_snapshot_assignment_freezes_reachable_empty_repos_as_zero(monkeypatch):
    # Repos EXIST but nobody committed on time - a real "nobody submitted". Freeze it (blank
    # shas recorded) to CLOSE the backdating window, rather than leaving it open for a later
    # push backdated before the deadline. Distinct from all-absent, which is skipped above.
    written = _stub_snapshot_write(
        monkeypatch, {"assignment-1-anna": "", "assignment-1-ben": ""}
    )
    assert (
        collect.snapshot_assignment(
            "Cohort", "assignment-1", "2026-10-15T23:59", is_group=False
        )
        is True
    )
    assert len(written) == 1  # the snapshot WAS frozen
    _path, text = written[0]
    assert "assignment-1-anna," in text and "assignment-1-ben," in text


# ------------------------------------------------ auditors + deadline guard (fix 7, 9)


def test_submission_targets_individual_excludes_auditors(monkeypatch):
    # Auditors deliberately have no submission repo; listing one makes it an unclonable
    # phantom target. submission_targets must apply the enrolled filter, like assign/grades.
    students = [
        Student("a@x", "Anna", "anna-adams", "", "", "enrolled"),
        Student("e@x", "Eve", "eve-e", "", "", "auditor"),
    ]
    monkeypatch.setattr(collect.roster, "load", lambda org: students)
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    assert collect.submission_targets("Cohort", "assignment-1", False) == [
        ("assignment-1-anna-adams", "anna-adams", ["anna-adams"]),
    ]


def test_collect_refuses_an_unparseable_deadline(monkeypatch, capsys):
    # An unparseable --deadline would reach git's approxidate and silently match NO commits,
    # zeroing the whole cohort. Validate up front and fail loudly instead.
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    assert (
        collect.collect(
            "Course", "assignment-1-f2026", "Cohort", deadline="next friday"
        )
        == 1
    )
    assert "not an ISO date" in capsys.readouterr().err


# ---------------------------------------------- resource limits + group-kill (fix 1)
# The highest-value fix: a memory/fork bomb (or an infinite loop) in ONE submission must be
# contained, never abort the whole job. A subprocess.run(timeout=) SIGKILLs only the direct
# child; the graded pytest runs in its own process GROUP under POSIX rlimits, and a wall-clock
# breach kills the whole group and returns None (couldn't grade) - fast, never a hang.


def test_apply_rlimits_lowers_the_childs_cap(monkeypatch):
    # Prove the preexec_fn actually applies our module-level caps to the graded child, WITHOUT
    # depending on a platform enforcing RLIMIT_DATA (macOS does not). RLIMIT_CPU is settable
    # everywhere: dial it to a distinctive value and read it back from inside the child.
    monkeypatch.setattr(collect, "RLIMIT_CPU_SECONDS", 123)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource; print(resource.getrlimit(resource.RLIMIT_CPU)[0])",
        ],
        preexec_fn=collect._apply_rlimits,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.stdout.strip() == "123"  # our cap reached the child's process


def test_run_limited_kills_the_whole_process_group_on_timeout(monkeypatch, tmp_path):
    # On a wall-clock breach the ENTIRE process group is SIGKILLed (not just the direct child a
    # fork bomb would orphan), and the helper returns False fast rather than waiting the sleep
    # out. The spy calls the real killpg so the process is genuinely reaped (no hang, no zombie).
    real_killpg = os.killpg
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        collect.os,
        "killpg",
        lambda pg, sig: calls.append((pg, sig)) or real_killpg(pg, sig),
    )
    t0 = time.monotonic()
    completed = collect._run_limited(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(tmp_path),
        env=dict(os.environ),
        timeout=1,
    )
    assert completed is False
    assert time.monotonic() - t0 < 15  # killed near the 1s budget, not waited out
    assert calls and calls[0][1] == signal.SIGKILL  # the whole group, hard


def test_run_tests_returns_none_on_a_wall_clock_timeout_without_hanging(
    monkeypatch, tmp_path, capsys
):
    # The self-perpetuating DoS core: a submission whose hidden-test run never returns (here an
    # infinite loop) must be contained. With a short wall-clock budget the graded pytest group
    # is killed and _run_tests returns None - no report, no score, fast. _grade_target then
    # scores it a recorded zero ("grading failed to run"), so the run COMPLETES and the marker
    # can land - the assignment is no longer stuck regrading the same bomb every hour for ever.
    monkeypatch.setattr(collect, "RUN_TIMEOUT", 2)
    work = tmp_path / "sub"
    work.mkdir()
    (work / "starter.py").write_text("x = 1\n")
    tests = tmp_path / "hidden"
    tests.mkdir()
    (tests / "test_hang.py").write_text(
        "def test_spin():\n    while True:\n        pass\n"
    )
    t0 = time.monotonic()
    assert collect._run_tests(work, tests) is None
    assert time.monotonic() - t0 < 30  # killed near the 2s budget, nowhere near a hang
    assert "timed out" in capsys.readouterr().err


# ------------------------------------------------ symlink-cycle strip walk (fix 2)


def test_strip_student_test_rigging_survives_a_symlink_cycle(tmp_path):
    # A committed symlink cycle (a->b, b->a) makes Path.rglob loop for ever - and the strip
    # runs BEFORE any subprocess timeout, so a followed cycle hangs the whole job. os.walk with
    # followlinks=False never traverses it, so the walk still terminates and the real rigging
    # (a committed conftest.py) is stripped.
    work = tmp_path / "sub"
    work.mkdir()
    (work / "conftest.py").write_text(
        "def pytest_ignore_collect(*a, **k):\n    return True\n"
    )
    (work / "a").symlink_to(work / "b")
    (work / "b").symlink_to(work / "a")  # a <-> b cycle
    t0 = time.monotonic()
    collect._strip_student_test_rigging(work)
    assert time.monotonic() - t0 < 10  # terminated, did not loop the cycle
    assert not (work / "conftest.py").exists()  # rigging still stripped


# --------------------------------------------------- single group resolver (fix 3)


@pytest.mark.parametrize(
    "force,schedule_type,template_group,expected",
    [
        (True, None, None, True),  # force (button / --group) wins
        (True, "individual", False, True),  # ... over everything below it
        (False, "group", False, True),  # cohort schedule beats the template
        (False, "individual", True, False),
        (False, None, True, True),  # template grading.yml is the fallback
        (False, None, False, False),
        (False, None, None, False),  # nothing declared -> individual
    ],
)
def test_resolve_is_group_precedence(force, schedule_type, template_group, expected):
    assert (
        collect.resolve_is_group(
            force=force, schedule_type=schedule_type, template_group=template_group
        )
        is expected
    )


# ------------------------------------------- explicit fire-once sentinel (fix 4)


def test_has_autograde_results_checks_the_records_not_bare_directory(monkeypatch):
    # The marker is the _graded.json sentinel OR the _skipped.json record - NEVER bare
    # autograde/<slug>/ existence, which an aborted run can leave populated but un-sentineled.
    def only(record: str):
        return lambda *args: (0, "") if args[-1].endswith(record) else (1, "not found")

    monkeypatch.setattr(collect, "gh", only("_graded.json"))
    assert collect.has_autograde_results("Cohort", "assignment-1")  # a completed run
    monkeypatch.setattr(collect, "gh", only("_skipped.json"))
    assert collect.has_autograde_results("Cohort", "assignment-1")  # a recorded skip
    # a populated directory with neither record present is NOT graded (the old bug)
    monkeypatch.setattr(collect, "gh", lambda *a: (1, "not found"))
    assert not collect.has_autograde_results("Cohort", "assignment-1")


def test_collect_writes_the_graded_sentinel_as_the_last_autograde_write(monkeypatch):
    # A successful run ends by writing autograde/<slug>/_graded.json - AFTER every per-target
    # archive - so the fire-once marker is decoupled from any single archive write.
    _stub_collect(monkeypatch, None)
    written = _captured_writes(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 0
    paths = [p for p, _ in written]
    assert "autograde/assignment-1/_graded.json" in paths
    autograde = [p for p in paths if p.startswith("autograde/")]
    assert (
        autograde[-1] == "autograde/assignment-1/_graded.json"
    )  # the LAST marker write


def test_collect_withholds_the_sentinel_when_an_archive_write_fails(monkeypatch):
    # A failed archive write reds the run and WITHHOLDS the sentinel, so the assignment stays
    # eligible for a retry - the recorded scores (write-once) are untouched, the marker is not
    # set, and the next tick rewrites the missing archive plus the sentinel.
    _stub_collect(monkeypatch, None)
    written: list[str] = []

    def failing_put(org, repo, path, content, msg):
        written.append(path)
        return not path.endswith("ben.json")  # one archive write fails

    monkeypatch.setattr(collect, "put_file", failing_put)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert "grades/assignment-1.csv" in written  # scores still durably recorded
    assert "autograde/assignment-1/_graded.json" not in written  # marker withheld


def test_a_zero_is_recorded_only_when_github_says_the_repo_is_gone(monkeypatch):
    # `repo_exists` reads ANY failure as absent. A clone hiccup followed by one 5xx on the
    # probe used to write a permanent, write-once zero for a student who had submitted.
    monkeypatch.setattr(collect, "gh", lambda *a, **k: (1, "clone failed"))
    monkeypatch.setattr(collect, "repo_missing", lambda *a: False)  # a 5xx: cannot tell
    assert collect._grade_target("K", "a1-ada", {}, None, "2026-09-08") is None
    monkeypatch.setattr(collect, "repo_missing", lambda *a: True)  # GitHub says 404
    result = collect._grade_target("K", "a1-ada", {}, None, "2026-09-08")
    assert result["score"] == 0 and "does not exist" in result["note"]


# ---------- teams.csv is keyed on the SCHEDULE KEY, submission repos on the cohort name


def _roster_of(monkeypatch, *rows: str):
    """The cohort roster `submission_targets` vets teams.csv against."""
    monkeypatch.setattr(
        collect.roster,
        "load",
        lambda org: collect.roster.parse(
            "hertie_email,name,github_handle,github_id,enrol_code,role\n"
            + "".join(r + "\n" for r in rows)
        ),
    )


def test_submission_targets_vets_teams_csv_against_the_roster(monkeypatch, capsys):
    # teams.csv is student-writable, and a handle in it earned a row of its OWN in the
    # grades CSV - the file faculty mark from and `render` fans out into gradebooks. A
    # typo, an invented name or an auditor must not appear there at all. Same allowlist
    # `assign.provision_all` vets a group handout through.
    _roster_of(
        monkeypatch,
        "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled",
        "eve@uni.edu,Eve,eve-e,43,dsl-xyz,auditor",
        "cy@uni.edu,Cy,,,dsl-ghi,enrolled",  # not onboarded
    )
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    monkeypatch.setattr(
        collect.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["Ada-L", "stranger-x", "eve-e", "cy"]},
    )
    targets = collect.submission_targets("Cohort", "assignment-4", True)
    # the roster's casing wins; everyone else is dropped
    assert targets == [("assignment-4-team-1", "team-1", ["ada-l"])]
    err = capsys.readouterr().err
    assert "3 handle(s) in teams.csv" in err
    assert "stranger-x" not in err, "a student's typing must not reach a public log"


def test_submission_targets_looks_teams_up_by_the_schedule_key(monkeypatch):
    # `cohort_dest_repo` makes the cohort-side name differ from the schedule key. teams.csv
    # carries the key (the Join-team form writes what schedule.yml declares), so looking up
    # by the name found no teams and the whole group assignment silently had nothing to
    # grade - while the repos it should have graded existed under the name.
    asked: list[str] = []
    _roster_of(monkeypatch, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    monkeypatch.setattr(
        collect.teams,
        "teams_for",
        lambda rows, slug: (
            asked.append(slug)
            or ({"team-1": ["ada-l"]} if slug == "regression" else {})
        ),
    )
    targets = collect.submission_targets(
        "Cohort", "wk3-regression", True, teams_key="regression"
    )
    assert asked == ["regression"]
    assert targets == [("wk3-regression-team-1", "team-1", ["ada-l"])]


def test_submission_targets_defaults_the_teams_key_to_the_name(monkeypatch):
    _roster_of(monkeypatch, "ada@uni.edu,Ada,ada-l,42,dsl-abc,enrolled")
    monkeypatch.setattr(collect.teams, "load", lambda org: {})
    monkeypatch.setattr(
        collect.teams,
        "teams_for",
        lambda rows, slug: {"team-1": ["ada-l"]} if slug == "assignment-4" else {},
    )
    assert collect.submission_targets("Cohort", "assignment-4", True) == [
        ("assignment-4-team-1", "team-1", ["ada-l"])
    ]


# ------------- a skip that was not recorded is not a skip (the marker write is checked)


def _failing_put_file(monkeypatch):
    monkeypatch.setattr(collect, "put_file", lambda *a, **k: False)


def test_an_unwritten_autograde_false_marker_goes_red_rather_than_green(
    monkeypatch, capsys
):
    # The `_skipped.json` record IS the skip: without it the cron re-clones the template
    # and re-decides the identical skip on every hourly tick, for ever. Returning 0 on a
    # failed write reported that as done.
    monkeypatch.setattr(collect, "gh", _clone_writing("autograde: false\n"))
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    _failing_put_file(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert "could not record the skip" in capsys.readouterr().err


def test_an_unwritten_no_solution_branch_marker_goes_red(monkeypatch, capsys):
    # No `solution` branch means hand-marked, recorded once. A failed record means the
    # same clone attempt, and the same decision, every hour.
    monkeypatch.setattr(collect, "gh", lambda *a, **k: (1, "no such branch"))
    monkeypatch.setattr(collect.schedule, "load", lambda org: Schedule())
    _failing_put_file(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert "could not record the skip" in capsys.readouterr().err


def test_an_unwritten_nothing_gradable_marker_goes_red(monkeypatch, capsys):
    _stub_collect(monkeypatch, {"assignment-1-team-x": ""})
    monkeypatch.setattr(collect, "gh", _clone_writing("type: group\nautograde: true\n"))
    monkeypatch.setattr(
        collect,
        "submission_targets",
        lambda org, slug, is_group=None, teams_key=None: [
            (f"{slug}-team-x", "team-x", [])
        ],
    )
    _failing_put_file(monkeypatch)
    assert collect.collect("Course", "assignment-1-f2026", "Cohort") == 1
    assert "could not record the skip" in capsys.readouterr().err
