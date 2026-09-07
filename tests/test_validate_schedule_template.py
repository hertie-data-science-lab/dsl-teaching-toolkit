"""validate-schedule.yml -- what the cohort's own commit-time check does that no other
workflow can.

The operational properties every shipped workflow shares are asserted in
test_shipped_workflows.py. What is unique here is the routing of three DIFFERENT verdicts
that all arrive in one run: an entry the parser dropped (faculty's file, red X plus an
issue), a source the course org has not got (faculty's file, a comment on the push and
nothing else), and a course org the toolkit could not read at all (infrastructure, red X
and an annotation that asks for the maintainer). Sending any of them down another's channel
is how a fault gets the wrong name and closes itself without being fixed.

The comment's TEXT belongs to the engine (`schedule.source_comment`), which is where it is
asserted; what is asserted here is that the workflow posts it rather than rebuilding it -
the step used to grep the report for a rung prefix, which matched some rungs and not
others, and counted an already-fired entry among the ones still to come.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from conftest import source_fault

from dsl_course import mailer, schedule
from dsl_course.schedule import SOURCE_WARN_WINDOW, hours

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


def test_nothing_in_the_cohort_template_pretends_it_can_send_mail():
    # A cohort org carries DSL_BOT_TOKEN and nothing else. GRAPH_* and
    # DSL_MAINTAINER_EMAIL are set on COURSE orgs and nothing propagates them down, so a
    # mail step here would resolve to empty secrets and send to nobody - while reading, in
    # the file and in the docs, as a channel that works. The infrastructure verdict is
    # delivered by the red X and by an annotation that asks for the maintainer by name.
    wired = {k for s in JOB["steps"] for k in s.get("env", {})}
    assert wired.isdisjoint({*mailer.GRAPH_ENV, mailer.MAINTAINER_ENV})
    assert "dsl_course.notify" not in RAW
    assert "Tell the maintainer." in _step("Validate schedule.yml")["run"]


def test_the_commit_comment_only_fires_on_a_push():
    # On a pull request there is no commit anybody is watching, and a dispatch is somebody
    # standing at the run.
    step = _step("Comment on the push")
    assert step["if"].startswith("github.event_name == 'push'")


def test_the_commit_comment_is_gated_on_one_answer_from_the_engine():
    # It used to enumerate the rung names an `if:` should fire on - a list a new rung falls
    # out of silently. One boolean, written by the process that knows.
    step = _step("Comment on the push")
    assert "steps.validate.outputs.sources_notify == 'true'" in step["if"]
    assert "sources_worst" not in RAW


def test_the_workflow_posts_the_comment_rather_than_building_it():
    # A shell re-arranging a sentence is a shell that gets it wrong on the one line that
    # matters, and this one dropped the rungs its pattern did not list.
    validate, comment = _step("Validate schedule.yml"), _step("Comment on the push")
    assert "--comment-file comment.txt" in validate["run"]
    assert "cat comment.txt" in comment["run"]
    assert "grep" not in comment["run"] and "sed" not in comment["run"]
    # The engine wrote it into the checkout the validate step ran in.
    assert comment["working-directory"] == "central"


def test_the_comment_says_where_the_durable_record_is():
    # The one thing the engine cannot know: which repo this workflow is running in.
    comment = _step("Comment on the push")
    assert "The generated GitHub record issue is" in comment["run"]
    assert "classroom-config/issues" in comment["run"]


def test_a_commit_comment_needs_contents_write():
    # The endpoint is repos/.../commits/<sha>/comments, which lives under Contents rather
    # than Issues - declared at read, the intent this block records would be wrong.
    doc = yaml.safe_load(RAW)
    assert doc["permissions"] == {"contents": "write", "issues": "write"}


# ------------------------------------------------------- the text the step is handed


def _fault(where: str, offset: timedelta, lineno: int):
    return source_fault(
        where,
        "Course-Org/cm/lectures/02 does not exist",
        NOW + offset,
        lineno=lineno,
        repo="cm",
        path="lectures/02",
    )


def test_the_comment_keeps_the_imminent_faults_and_drops_the_distant_ones():
    body = schedule.source_comment(
        [
            _fault("releases.lecture_02", timedelta(hours=3), 131),
            _fault("releases.lecture_03", timedelta(hours=20), 140),
            _fault("releases.lecture_09", timedelta(days=60), 300),
        ],
        NOW,
    )
    assert body.startswith(
        f"This push leaves 2 planned release(s) inside {hours(SOURCE_WARN_WINDOW)}h"
    )
    assert "releases.lecture_02" in body
    assert "releases.lecture_03" in body
    assert "releases.lecture_09" not in body  # the digest issue holds that one


def test_an_entry_that_has_already_fired_is_not_counted_as_one_still_to_come():
    # "inside 24h" is a promise about the future, and a release that has already shipped
    # nothing needs a different sentence - and its own line.
    body = schedule.source_comment(
        [
            _fault("releases.lecture_01", -timedelta(hours=2), 120),
            _fault("releases.lecture_02", timedelta(hours=3), 131),
        ],
        NOW,
    )
    assert "This push leaves 1 planned release(s) inside" in body
    assert "1 planned release(s) have already fired with nothing to ship:" in body
    assert body.index("releases.lecture_01") > body.index("releases.lecture_02")


def test_a_plan_with_nothing_imminent_writes_no_comment():
    assert (
        schedule.source_comment([_fault("releases.a", timedelta(days=60), 10)], NOW)
        == ""
    )
    assert schedule.source_comment([], NOW) == ""


def test_each_listed_row_is_the_engines_own_line():
    # `SourceFault.line()` is dash-separated in this order precisely so every surface can
    # reuse it whole rather than re-arranging it.
    fault = _fault("releases.lecture_02", timedelta(hours=3), 131)
    assert f"- {fault.line()}" in schedule.source_comment([fault], NOW)


def test_the_comment_tells_the_pusher_what_happens_next():
    body = schedule.source_comment([_fault("releases.a", timedelta(hours=3), 10)], NOW)
    assert body.endswith("You will get one email about each as its deadline nears.")


def test_the_run_summary_is_the_engines_report_not_a_second_rendering():
    # The workflow appends the CLI's stdout verbatim; asserting the report here rather
    # than re-implementing it is what stops the two drifting.
    fault = _fault("releases.a", timedelta(hours=3), 10)
    report = schedule.source_report([fault], NOW, "Course-Org")
    assert report.startswith("  1 SOURCE(S) NOT IN Course-Org YET:")
    assert f"    [critical] {fault.line()}" in report
    assert schedule.source_report([], NOW, "Course-Org") == (
        "  every source in the plan exists in Course-Org"
    )
