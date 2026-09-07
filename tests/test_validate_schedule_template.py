"""validate-schedule.yml -- what the cohort's own commit-time check does that no other
workflow can.

The operational properties every shipped workflow shares are asserted in
test_shipped_workflows.py. What is unique here is the routing of three DIFFERENT verdicts
that all arrive in one run: an entry the parser dropped (faculty's file, red X plus an
issue), a source the course org has not got (faculty's file, a comment on the push and
nothing else), and a course org the toolkit could not read at all (infrastructure, red X
plus a mail to the maintainer). Sending any of them down another's channel is how a fault
gets the wrong name and closes itself without being fixed.

The comment-building shell is EXECUTED against a real `--check-sources` report, because
the property under test lives in a grep and a sed: which rungs it keeps.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from dsl_course.schedule import (
    SOURCE_WARN_WINDOW,
    Severity,
    SourceFault,
)

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=BERLIN)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "classroom-config" / "validate-schedule.yml"
RAW = TEMPLATE.read_text()
JOB = yaml.safe_load(RAW)["jobs"]["validate"]


def _step(name_starts: str) -> dict:
    return next(s for s in JOB["steps"] if s.get("name", "").startswith(name_starts))


def test_an_unreadable_course_org_is_a_failure_not_a_skipped_check():
    # A cohort's dsl-course.yml is SYSTEM-OWNED and always names `course:`, so an empty
    # answer is an API error or a token that lost its scope. Swallowed, the source check
    # silently stopped running and every push looked clean.
    resolve = _step("Resolve the course org")
    assert "continue-on-error" not in resolve
    validate = _step("Validate schedule.yml")
    assert "::error::could not read this cohort's dsl-course.yml" in validate["run"]
    # ...and it says whose problem it is. Faculty reading a red X on their own push will
    # otherwise go looking through a file that is perfectly fine.
    assert "not a fault in your file" in validate["run"]
    assert "::notice::" not in validate["run"]


def test_only_the_infrastructure_failure_emails_the_maintainer():
    # A dropped entry is faculty's own YAML and resolves the course org perfectly well, so
    # it must never reach this step - the red X and the assigned issue are its channel.
    mail = _step("Email the maintainer")
    assert mail["if"] == (
        "failure() && steps.course.outputs.org == '' "
        "&& github.event_name != 'workflow_dispatch'"
    )
    assert "--run-failed" in mail["run"]
    assert "--log-failed" in mail["run"]
    # It can only mail if it carries the transport and knows where to send.
    assert "GRAPH_CLIENT_CERT" in mail["env"]
    assert mail["env"]["DSL_MAINTAINER_EMAIL"] == "${{ secrets.DSL_MAINTAINER_EMAIL }}"


def test_the_commit_comment_only_fires_on_a_push():
    # On a pull request there is no commit anybody is watching, and a dispatch is somebody
    # standing at the run.
    step = _step("Comment on the push")
    assert step["if"].startswith("github.event_name == 'push'")


def test_the_commit_comment_is_gated_on_the_rung_the_engine_reported():
    # Re-deriving a severity by grepping the report is what this replaces: the pattern
    # matched some rungs and not others, silently. `sources_worst` is written by the
    # process that knows it.
    step = _step("Comment on the push")
    for rung in (Severity.WARNING, Severity.URGENT, Severity.CRITICAL, Severity.MISSED):
        assert f"sources_worst == '{rung}'" in step["if"], rung
    # An advisory is a term written in August. Commenting on those teaches faculty to
    # scroll past this.
    assert "'advisory'" not in step["if"]
    assert "'none'" not in step["if"]


def test_a_commit_comment_needs_contents_write():
    # The endpoint is repos/.../commits/<sha>/comments, which lives under Contents rather
    # than Issues - declared at read, the intent this block records would be wrong.
    doc = yaml.safe_load(RAW)
    assert doc["permissions"] == {"contents": "write", "issues": "write"}


def _report(now: datetime, faults: list[SourceFault]) -> str:
    """The `--check-sources` block of a real CLI report, built the way `main` builds it."""
    lines = [f"  {len(faults)} SOURCE(S) NOT IN Course-Org YET:"]
    lines += [
        f"    [{f.severity(now)}] {f.line()}"
        for f in sorted(faults, key=lambda f: -f.severity(now))
    ]
    return "\n".join(
        [*lines, "", "  A source you have not written yet looks like this"]
    )


def _run_comment_step(report: str) -> str:
    """Execute the comment step's script with `gh` faked, and return the body it built."""
    step = _step("Comment on the push")
    script = step["run"].replace(
        'gh api "repos/$REPO/commits/$SHA/comments" -f body="$body" >/dev/null',
        'printf "%s" "$body"',
    )
    return subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "REPORT": report,
            "COHORT": "C-f2026",
        },
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _fault(where: str, offset: timedelta, lineno: int) -> SourceFault:
    return SourceFault(
        where,
        "Course-Org/cm/lectures/02 does not exist",
        NOW + offset,
        field="course_source_path",
        lineno=lineno,
        repo="cm",
        path="lectures/02",
    )


def test_the_comment_keeps_the_imminent_faults_and_drops_the_distant_ones():
    body = _run_comment_step(
        _report(
            NOW,
            [
                _fault("releases.lecture_02", timedelta(hours=3), 131),
                _fault("releases.lecture_03", timedelta(hours=20), 140),
                _fault("releases.lecture_09", timedelta(days=60), 300),
            ],
        )
    )
    assert "planned release(s) inside" in body
    assert "releases.lecture_02" in body
    assert "releases.lecture_03" in body
    assert "releases.lecture_09" not in body  # the digest issue holds that one


def test_the_comment_counts_what_it_actually_listed():
    body = _run_comment_step(
        _report(
            NOW,
            [
                _fault("releases.a", timedelta(hours=3), 10),
                _fault("releases.b", timedelta(days=60), 20),
            ],
        )
    )
    assert body.startswith("This push leaves 1 planned release(s) inside ")
    assert f"inside {int(SOURCE_WARN_WINDOW.total_seconds() // 3600)}h" in body


def test_the_comment_says_where_the_durable_record_is():
    body = _run_comment_step(
        _report(NOW, [_fault("releases.a", timedelta(hours=3), 10)])
    )
    assert "You will get one email about each as its deadline nears." in body
    assert "C-f2026/classroom-config/issues" in body


def test_a_report_with_nothing_imminent_writes_no_comment():
    assert (
        _run_comment_step(_report(NOW, [_fault("releases.a", timedelta(days=60), 10)]))
        == ""
    )


def test_each_listed_row_is_the_engines_own_line_with_only_the_rung_stripped():
    # The shell must not re-arrange a sentence: `SourceFault.line()` is dash-separated in
    # this order precisely so the comment can reuse it whole.
    fault = _fault("releases.lecture_02", timedelta(hours=3), 131)
    body = _run_comment_step(_report(NOW, [fault]))
    assert f"- {fault.line()}" in body
