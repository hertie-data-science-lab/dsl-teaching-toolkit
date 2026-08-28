"""The seeded welcome workflows/forms must be valid YAML - a typo breaks a cohort's
bootstrap (they're put_file'd verbatim into the welcome repo). github-script bodies are
YAML literal-block strings, so safe_load parses the workflow without running any JS.

The JS itself can't be executed here (no node in CI, and github-script has no npm
deps), so what's asserted instead is the Python <-> JS contract: the embedded scripts
parse the CSVs with real quote-aware helpers rather than line.split(','), and every
column they address by name really exists in roster.FIELDS / teams.FIELDS.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from dsl_course import roster, teams

WELCOME = Path(__file__).resolve().parents[1] / "templates" / "welcome"
TEMPLATES = [
    "onboard.yml",
    "team-formation.yml",
    "ISSUE_TEMPLATE/01-join-course.yml",
    "ISSUE_TEMPLATE/02-join-team.yml",
]
# The two workflows carrying an embedded github-script CSV reader/writer.
CSV_WORKFLOWS = {"onboard.yml": "onboard", "team-formation.yml": "form-team"}


def script_of(rel: str, job: str) -> str:
    """The github-script body of a workflow's single step."""
    doc = yaml.safe_load((WELCOME / rel).read_text())
    (step,) = doc["jobs"][job]["steps"]
    return step["with"]["script"]


def code_of(script: str) -> str:
    """The script minus whole-line `//` comments (which discuss the old naive parse)."""
    return "\n".join(
        ln for ln in script.splitlines() if not ln.strip().startswith("//")
    )


def retry_loop(code: str) -> str:
    """The read-modify-write retry loop only - `for (let attempt` through its matching
    closing brace - so an assertion about the loop can't be satisfied (or broken) by code
    that merely happens to sit after it."""
    start = code.index("for (let attempt")
    depth = 0
    for j in range(code.index("{", start), len(code)):
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        if depth == 0:
            return code[start : j + 1]
    raise AssertionError("unbalanced braces in the retry loop")


def csv_helpers(script: str) -> str:
    """The parseCsv/csvCell/serialiseCsv block, for cross-workflow drift checks."""
    start = script.index("const parseCsv")
    end = script.index("\n", script.index("const serialiseCsv"))
    return script[start:end]


@pytest.mark.parametrize("rel", TEMPLATES)
def test_welcome_template_is_valid_yaml(rel):
    doc = yaml.safe_load((WELCOME / rel).read_text())
    assert isinstance(doc, dict) and doc.get("name")


def test_workflows_are_gated_on_the_forms_labels():
    # Titles are fixed defaults the workflows rewrite after the fact, so routing keys on
    # the label each issue form applies - the one thing a student can't mistype.
    doc = yaml.safe_load((WELCOME / "team-formation.yml").read_text())
    job = doc["jobs"]["form-team"]
    assert "'team-formation'" in job["if"] and "labels" in job["if"]
    onboard = yaml.safe_load((WELCOME / "onboard.yml").read_text())["jobs"]["onboard"]
    assert "'onboarding'" in onboard["if"] and "labels" in onboard["if"]
    form = yaml.safe_load((WELCOME / "ISSUE_TEMPLATE/01-join-course.yml").read_text())
    team_form = yaml.safe_load(
        (WELCOME / "ISSUE_TEMPLATE/02-join-team.yml").read_text()
    )
    assert form["labels"] == ["onboarding"]
    assert team_form["labels"] == ["team-formation"]
    # writes to the private roster repo, not a public one
    assert "classroom-config" in (WELCOME / "team-formation.yml").read_text()


@pytest.mark.parametrize("rel,job", sorted(CSV_WORKFLOWS.items()))
def test_csv_is_parsed_with_quote_aware_helpers_not_split(rel, job):
    # A quoted field containing a comma (a name like "Doe, Jane") is legal CSV that
    # Python's csv module writes and reads happily; line.split(',') would shift every
    # column right of it and silently write github_handle/github_id into wrong cells.
    script = script_of(rel, job)
    assert "const parseCsv" in script
    assert "const serialiseCsv" in script
    code = code_of(script)
    assert "split(',')" not in code, "naive comma split still parses a CSV row"
    assert "split('\\n')" not in code, "CSV is still split into lines before parsing"
    # Escaped quotes ("" -> ") on read, and QUOTE_MINIMAL-equivalent quoting on write.
    assert "'\"\"'" in script
    assert '/[",\\r\\n]/' in script


def test_csv_helpers_do_not_drift_between_workflows():
    # Both workflows write the same roster/teams CSVs, so the two hand-rolled copies
    # (no shared module - these files ship verbatim) must stay byte-identical.
    onboard, formation = (
        csv_helpers(script_of(rel, job)) for rel, job in sorted(CSV_WORKFLOWS.items())
    )
    assert onboard == formation


def test_onboard_addresses_roster_columns_declared_in_python():
    script = script_of("onboard.yml", "onboard")
    named = set(re.findall(r"indexOf\('([a-z_]+)'\)", script))
    assert named == {"github_handle", "github_id", "enrol_code", "role"}
    assert named <= set(roster.FIELDS)  # the contract with dsl_course.roster


def test_onboard_routes_auditors_to_the_auditors_team():
    # The role column decides the team: auditors are read-only (released materials, no
    # assignment repos), enrolled students go to `students`. Nothing else about the flow
    # differs, so the team slug must be a variable, not a hardcoded 'students'.
    script = script_of("onboard.yml", "onboard")
    code = code_of(script)
    assert f"=== '{roster.ROLE_AUDITOR}'" in code  # matches the Python spelling
    assert "'auditors' : 'students'" in code
    assert "team_slug: team" in code
    assert "team_slug: 'students'" not in code


def test_onboard_treats_a_missing_role_column_as_enrolled():
    # A cohort whose roster predates the column has no `role` header at all - it must
    # keep onboarding (blank/absent = enrolled, per roster.normalise_role), so `role` is
    # never part of the required-column guard.
    script = script_of("onboard.yml", "onboard")
    code = code_of(script)
    guard = re.search(r"if \((iHandle < 0[^)]*)\)", code).group(1)
    assert "iRole" not in guard, "role must not be a required roster column"
    # every read of the role cell is guarded on the column existing
    assert "iRole >= 0" in code


def test_onboard_never_downgrades_an_existing_org_admin():
    # An org OWNER filing a Join issue (a course admin testing the flow) must not be
    # re-invited as a plain `member`: that demotes them and strips access to every private
    # repo. The membership pre-check has to run BEFORE the grants and short-circuit them,
    # but AFTER the roster link-back (recording the handle/id is safe and useful).
    script = script_of("onboard.yml", "onboard")
    code = code_of(script)
    assert "orgs.getMembershipForUser" in code
    assert "e.status !== 404" in code, "not-a-member (404) must not abort onboarding"
    assert "orgRole === 'admin'" in code
    assert code.index("createOrUpdateFileContents") < code.index("getMembershipForUser")
    assert code.index("getMembershipForUser") < code.index("setMembershipForUser")
    assert code.index("orgRole === 'admin'") < code.index("setMembershipForUser")
    # the guard returns before the team write too, and says why nothing changed
    assert code.index("orgRole === 'admin'") < code.index("team_slug: team")
    assert "no access changes" in script


def test_team_formation_refuses_auditors_without_publishing_their_role():
    # Auditors are read-only: assignment release is roster-driven (enrolled rows only), so an
    # auditor recorded in teams.csv would be handed a group assignment repo anyway. They must
    # be refused on the same comment + needs-review path as every other rejection - and with
    # the SAME words a non-enrolee gets. This issue is public and permanent, so "your
    # enrolment doesn't include project work" published the author's role to anyone reading.
    script = script_of("team-formation.yml", "form-team")
    code = code_of(script)
    assert f"=== '{roster.ROLE_AUDITOR}'" in code  # matches the Python spelling
    refusal = re.search(
        r"if \(iRole >= 0 [^\n]*\n(?:.*\n)*?\s+'needs-review'\);", code
    ).group(0)
    assert "NOT_A_PARTICIPANT" in refusal, (
        "the auditor refusal has its own wording again"
    )
    assert "enrolment" not in refusal
    # ... and it is the same constant the not-on-the-roster path uses.
    assert len(re.findall(r"fail\(\s*NOT_A_PARTICIPANT", code)) == 2
    # refused before anything is written back to teams.csv
    assert code.index("iRole >= 0") < code.index("createOrUpdateFileContents")


def test_a_refused_team_name_gives_no_reason():
    # A team may not be named after a roster handle (a group repo is `<slug>-<team>` and a
    # per-student one `<slug>-<handle>`), but SAYING so turned the form into a membership
    # oracle: try a name, and the reply tells you whether that person is in this cohort.
    # Reserved names and handle collisions share one reason-free refusal.
    code = code_of(script_of("team-formation.yml", "form-team"))
    assert "named after a GitHub handle" not in code
    assert "handles.has(team)) return fail(NAME_TAKEN" in code
    # ... the same words the reserved-name refusal uses, so the two are indistinguishable.
    assert len(re.findall(r"fail\(\s*NAME_TAKEN", code)) == 2


def test_team_formation_treats_a_missing_role_column_as_enrolled():
    # A cohort whose roster predates the column has no `role` header at all - those students
    # must keep forming teams (blank/absent = enrolled, per roster.normalise_role), so `role`
    # is never part of the required-column guard.
    script = script_of("team-formation.yml", "form-team")
    code = code_of(script)
    guard = re.search(r"if \((iRosterHandle < 0[^)]*)\)", code).group(1)
    assert "iRole" not in guard, "role must not be a required roster column"
    assert "iRole >= 0" in code  # every read of the role cell is guarded on it existing


def test_team_formation_addresses_columns_declared_in_python():
    script = script_of("team-formation.yml", "form-team")
    named = set(re.findall(r"indexOf\('([a-z_]+)'\)", script))
    assert named == set(teams.FIELDS) | {"github_handle", "role"}
    assert named <= set(teams.FIELDS) | set(roster.FIELDS)
    # The header it writes on first use must match teams.FIELDS exactly, in order.
    literal = re.search(r"const FIELDS = \[(.*?)\];", script).group(1)
    assert tuple(re.findall(r"'([a-z_]+)'", literal)) == teams.FIELDS


def test_forms_have_no_confirmation_checkbox_and_fixed_titles():
    # The forms ask only for what the workflows parse; the title is a fixed default the
    # workflows later rewrite from the author + fields.
    for rel, fixed in (
        ("ISSUE_TEMPLATE/01-join-course.yml", "Join course"),
        ("ISSUE_TEMPLATE/02-join-team.yml", "Join team"),
    ):
        doc = yaml.safe_load((WELCOME / rel).read_text())
        assert doc["title"] == fixed
        assert all(b.get("type") != "checkboxes" for b in doc["body"]), rel


@pytest.mark.parametrize("rel", sorted(CSV_WORKFLOWS))
def test_onboarding_concurrency_is_scoped_per_issue(rel):
    # A repo-wide group with cancel-in-progress: false looks like serialisation but isn't:
    # GitHub keeps exactly ONE pending run per group, so on a first-day burst of Join
    # issues the third arrival CANCELS the second - and a cancelled run posts no comment
    # and adds no label, so those students are dropped in silence. Scoping the group to the
    # issue lets the burst run in parallel; the CSV's `sha` + the retry below is what makes
    # the concurrent writes safe (a stale sha is a 409, never a lost update).
    doc = yaml.safe_load((WELCOME / rel).read_text())
    assert "github.event.issue.number" in doc["concurrency"]["group"]
    assert doc["concurrency"]["cancel-in-progress"] is False


@pytest.mark.parametrize("rel", sorted(CSV_WORKFLOWS))
def test_onboarding_workflows_are_minimally_scoped(rel):
    # Bounded jobs and sha-pinned actions are swept over every shipped workflow in
    # test_shipped_workflows.py; what is UNIQUE to these two is the exact scope. The
    # ambient token comments on, labels and closes the issue in THIS repo and gets nothing
    # else: the CSV they write lives in classroom-config, which only DSL_BOT_TOKEN reaches,
    # so `contents:` here would be scope with no purpose.
    doc = yaml.safe_load((WELCOME / rel).read_text())
    assert doc["permissions"] == {"issues": "write"}


@pytest.mark.parametrize("rel,job", sorted(CSV_WORKFLOWS.items()))
def test_the_csv_write_retries_and_ends_in_a_comment_not_a_stack_trace(rel, job):
    # Many issues are in flight at once now that they run in parallel, and each is a
    # read-modify-write of the same file. A stale sha is a 409; a first-day burst can also
    # draw a 403 naming GitHub's SECONDARY rate limit. Both are retried, with JITTER (a
    # burst retrying in lockstep just collides again on the same schedule).
    code = code_of(script_of(rel, job))
    assert "e.status === 409" in code
    assert "secondary rate limit" in code
    assert "Math.random()" in code, "backoff must be jittered"
    assert "ATTEMPTS = 8" in code
    # Exhaustion is a RESULT, not a crash: a bare throw leaves a red run with no comment
    # and no label, so the student is dropped in silence and nobody triages it.
    loop = retry_loop(code)
    assert "throw e" not in loop, "a terminal path must comment + label, not throw"
    assert "attempt === ATTEMPTS) return fail(" in loop


def test_onboard_retries_the_roster_write_on_a_conflict():
    # The write RE-READS and re-applies rather than giving up (or, worse, writing a stale
    # table back) - and everything read afterwards comes off that fresh snapshot.
    code = code_of(script_of("onboard.yml", "onboard"))
    assert code.count("await readRoster()") >= 2, "a retry must re-read, not re-send"
    # Only this student's row is touched, keyed on their unique code, so a retry can never
    # undo the row a competing run committed in between.
    assert "rows.find(r => r[iCode]" in code
    loop = retry_loop(code)
    # Two issues quoting the same code race here: the "already bound to another handle"
    # guard must be RE-TAKEN on the re-read row, or the loser overwrites the winner.
    assert "boundElsewhere(matched)" in loop
    assert loop.index("boundElsewhere(matched)") < loop.index(
        "matched[iHandle] = handle"
    )
    # ...and the role read further down addresses the fresh row, not the first snapshot's.
    assert "(matched[iRole] || '')" in code
    assert "(row[iRole] || '')" not in code


def test_onboard_re_matches_a_renamed_account_by_its_immutable_id():
    # A login is renameable; a GitHub id is not. Comparing only the handle, a student who
    # renamed their account got NO_MATCH on every re-run while the nightly reconcile pruned
    # their new login off every team - a break with no recovery short of a hand edit.
    # `boundElsewhere` therefore accepts a row whose `github_id` is this user's, and both
    # the pre-loop guard and the in-loop re-check go through it.
    code = code_of(script_of("onboard.yml", "onboard"))
    guard = re.search(r"const boundElsewhere = \(r\) =>(.*?);", code, re.DOTALL)
    assert guard, "the single-use guard is gone"
    assert re.search(r"r\[iId\].*!==\s*String\(userId\)", guard.group(1)), (
        "the guard does not exempt the same account under a new name"
    )
    assert "boundElsewhere(row)" in code, "the pre-loop check must use the same guard"
    assert code.index("const boundElsewhere") < code.index("boundElsewhere(row)")


def test_team_formation_retakes_the_cap_decision_on_every_attempt():
    code = code_of(script_of("team-formation.yml", "form-team"))
    loop = retry_loop(code)
    # The read, the duplicate-membership check and the size cap all live INSIDE the retry
    # loop: two students committing at the same moment must not both slip past a full team.
    for fragment in ("await readTeams()", "size >= cap", "createOrUpdateFileContents"):
        assert fragment in loop, fragment
    # 422 is the create-on-first-use race: our request carried no sha because the file did
    # not exist when we read it, but it does now.
    assert "e.status === 422" in code


def test_team_cap_is_read_from_schedule_yml_per_assignment():
    # The cap is instructor-set config (assignments.<slug>.max_team_size in the cohort's
    # schedule.yml), not a constant buried in the workflow.
    script = script_of("team-formation.yml", "form-team")
    assert "MAX_TEAM_SIZE" not in script
    assert "max_team_size" in script and "schedule.yml" in script
    assert "DEFAULT_TEAM_SIZE = 5" in script


def test_the_join_form_code_regex_matches_exactly_the_codes_we_mint():
    # The workflow REDACTS whatever the regex captured from the public body. A loose
    # capture (`[A-Za-z0-9-]+`) turned a Unicode hyphen into code="dsl", and split() on
    # that mangled the body. The strict shape is the one enrol_codes.make_code mints.
    from dsl_course import enrol_codes

    code = code_of(script_of("onboard.yml", "onboard"))
    m = re.search(r"match\(/Enrolment code(.*?)/i\)", code)
    pattern = re.compile("Enrolment code" + m.group(1), re.IGNORECASE)
    for _ in range(20):
        minted = enrol_codes.make_code()
        assert pattern.search(f"Enrolment code\n\n{minted}\n").group(1) == minted
    assert (
        pattern.search("Enrolment code\n\ndsl\u2011abc234\n") is None
    )  # Unicode hyphen
    assert pattern.search("Enrolment code\n\nabc234\n") is None


def test_blank_issues_are_disabled_so_every_issue_carries_a_routing_label(monkeypatch):
    # A blank issue has no `onboarding`/`team-formation` label, so neither workflow runs on
    # it: a code pasted there is never redacted and nobody is notified. The config must be
    # seeded (and refreshed) alongside the forms.
    from dsl_course import welcome

    cfg = yaml.safe_load((WELCOME / "ISSUE_TEMPLATE" / "config.yml").read_text())
    assert cfg["blank_issues_enabled"] is False
    seen: dict[str, bytes] = {}
    monkeypatch.setattr(
        welcome,
        "put_files",
        lambda org, repo, files, msg, **kw: seen.update(files) or True,
    )
    welcome.refresh_welcome_workflows("Org")
    assert ".github/ISSUE_TEMPLATE/config.yml" in seen


def test_onboard_throttles_a_student_before_it_touches_the_roster():
    # `welcome` is public and anyone can open an issue in it, and each one costs a private
    # roster read plus an org invite and a team write on the bot token. A student who keeps
    # opening new Join issues instead of reading the reply on the last one pays that on
    # repeat. The count must therefore happen BEFORE the roster read and before the bot
    # token is used at all.
    code = code_of(script_of("onboard.yml", "onboard"))
    throttle = code.index("listForRepo")
    assert "creator: handle" in code and "labels: 'needs-review'" in code
    assert "unresolved.length >= 3" in code
    assert "unresolved Join issues - contact the teaching team" in code
    assert throttle < code.index("await readRoster()")
    assert throttle < code.index("process.env.HAS_BOT")
