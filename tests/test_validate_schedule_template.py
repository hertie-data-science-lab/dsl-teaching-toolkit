"""validate-schedule.yml -- what the cohort's own commit-time check does that no other
workflow can.

The operational properties every shipped workflow shares are asserted in
test_shipped_workflows.py. What is unique here is the routing of three DIFFERENT verdicts
that all arrive in one run: an entry the parser dropped (faculty's file, a red X and an
annotation - the RECORD is the engine's digest issue, not anything written here), a source
the course org has not got (faculty's file, a comment on the push and nothing else), and a
course org the toolkit could not read at all (infrastructure, red X and an annotation that
asks for the maintainer). Sending any of them down another's channel is how a fault gets
the wrong name and closes itself without being fixed.

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

from dsl_course import discovery, mailer, schedule, source_digest
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
    fail = _step("Fail the run because the course org")
    assert fail["if"] == "steps.course.outputs.org == ''"
    assert "::error::could not read this cohort's dsl-course.yml" in fail["run"]
    assert "exit 1" in fail["run"]
    # ...and it says whose problem it is. Faculty reading a red X on their own push will
    # otherwise go looking through a file that is perfectly fine.
    assert "not a fault in your file" in fail["run"]
    assert "::notice::" not in RAW


def test_the_course_org_is_read_by_the_real_loader_not_a_grep():
    # `grep '^course:' | cut -d: -f2- | xargs` - what the dispatchers must use, having
    # neither Python nor the central checkout - keeps the quotes on a quoted value and
    # keeps a trailing `# comment`. Either resolves to an org that does not exist, and the
    # source check then reads as infrastructure-broken on a file that is fine. This job
    # has both by the time it asks, so it asks the same YAML parse every CLI makes.
    resolve = _step("Resolve the course org")
    assert "grep" not in resolve["run"]
    assert "course_org_for_cohort" in resolve["run"]
    # ...and the name it invokes is a real one, in the checkout it runs from.
    assert callable(discovery.course_org_for_cohort)
    assert resolve["working-directory"] == "central"


def test_only_the_course_org_reaches_the_step_output():
    # The answer is captured off STDOUT, and `ghcli.gh` prints its retry notices there
    # before a successful retry. A second line writes a step-output line with no `=`,
    # GitHub rejects the whole file, and the step fails - which skips every step after it,
    # the parse verdict included. One rate-limited read would cost the dropped-entry
    # channel entirely. `scheduler.main`'s --list-cohorts carries the same guard.
    assert "redirect_stdout" in _step("Resolve the course org")["run"]


def test_the_parse_is_reported_even_when_the_course_org_cannot_be_read():
    # Two verdicts, two channels. Failing the validate step for a missing org skipped
    # every step after it - and those steps have no status function in their `if:`, so a
    # push that dropped an entry went unreported because of an unrelated API blip.
    validate = _step("Validate schedule.yml")
    assert "--validate" in validate["run"]
    assert validate["run"].rstrip().endswith("exit 0")
    # The source check is what is conditional, not the parse.
    assert 'if [ -n "$COURSE_ORG" ]; then' in validate["run"]
    assert "--check-sources" in validate["run"]
    # ...and the org failure is the LAST step, after the red X the dropped entry earns.
    names = [s.get("name", "") for s in JOB["steps"]]
    assert names[-1].startswith("Fail the run because the course org")


def test_nothing_in_the_cohort_template_pretends_it_can_send_mail():
    # A cohort org carries DSL_BOT_TOKEN and nothing else. GRAPH_* and
    # DSL_MAINTAINER_EMAIL are set on COURSE orgs and nothing propagates them down, so a
    # mail step here would resolve to empty secrets and send to nobody - while reading, in
    # the file and in the docs, as a channel that works. The infrastructure verdict is
    # delivered by the red X and by an annotation that asks for the maintainer by name.
    wired = {k for s in JOB["steps"] for k in s.get("env", {})}
    assert wired.isdisjoint({*mailer.GRAPH_ENV, mailer.MAINTAINER_ENV})
    assert "dsl_course.notify" not in RAW
    assert "Tell the maintainer." in _step("Fail the run because the course org")["run"]


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


def test_the_comment_links_the_digest_issue_rather_than_a_search_for_it():
    # The one thing the engine cannot know: which repo this workflow is running in, and
    # which issue in it holds the record. A search link was what it used to say - the
    # reader had to find the issue themselves, on a query that also matches the parser's.
    comment = _step("Comment on the push")
    assert "The generated GitHub record issue is" in comment["run"]
    assert "gh issue list" in comment["run"]
    # `--search` is a word match; the title is compared client-side, and read from the
    # environment rather than spliced into the jq program.
    assert "select(.title == env.TITLE)" in comment["run"]
    # ...and only when nothing is open does it fall back to a search.
    assert "issues?q=" in comment["run"]


def test_the_digest_title_comes_from_the_engine_that_writes_the_issue():
    # Every lookup of that issue matches the title EXACTLY, so a copy in this template
    # stops finding it the day the wording changes - silently, on the one line meant to
    # point at it.
    assert "python3 -m dsl_course.source_digest --title" in _step("Comment")["run"]
    assert source_digest.TITLE not in RAW


def test_a_commit_comment_needs_contents_write():
    # The endpoint is repos/.../commits/<sha>/comments, which lives under Contents rather
    # than Issues - declared at read, the intent this block records would be wrong.
    doc = yaml.safe_load(RAW)
    assert doc["permissions"] == {"contents": "write"}


def test_nothing_here_writes_an_issue_any_more():
    # This workflow used to open, comment on and close the dropped-entry issue in a block
    # of shell that searched by title and assigned the pusher - which could not tell a
    # fault that ESCALATED from one that was merely still there, and re-notified on every
    # push. The engine owns that issue now (it adopts the same title), so the only writes
    # left here are the red X, the annotations and the commit comment.
    assert "gh issue create" not in RAW
    assert "gh issue close" not in RAW
    assert "gh issue comment" not in RAW
    # Nothing here SEARCHES for that issue either. (The only title this template looks up
    # is the sources digest's, and it asks the engine for it - see the test above.)
    assert f'"{source_digest.ABSORBED} in:title"' not in RAW
    # The red X and the annotation stay - they are the half only a push can give.
    assert "::error file=schedule.yml::" in RAW
    assert "--annotate" in RAW


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
    body = schedule.source_comment([fault], NOW, "Cohort-f2026")
    assert f"- {fault.line(fault.cite('Cohort-f2026'))}" in body


def test_the_citation_is_a_link_at_the_line_that_needs_editing():
    # A commit comment is markdown, and the whole point of saying this on the push is that
    # the fix is one click away rather than a scroll through a file written in August.
    fault = _fault("releases.lecture_02", timedelta(hours=3), 131)
    body = schedule.source_comment([fault], NOW, "Cohort-f2026")
    assert (
        "[`schedule.yml:131`](https://github.com/Cohort-f2026/classroom-config/blob/main"
        "/schedule.yml#L131)"
    ) in body
    # No cohort to build a URL from - run by hand, off a runner - and it is plain code.
    assert "](" not in schedule.source_comment([fault], NOW)


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
