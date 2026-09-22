"""The seeded welcome workflows/forms must be valid YAML - a typo breaks a cohort's
bootstrap (they're put_file'd verbatim into the welcome repo). github-script bodies are
YAML literal-block strings, so safe_load parses the workflow without running any JS.

The JS itself can't be executed here (no node in CI, and github-script has no npm
deps), so what's asserted instead is the Python <-> JS contract: the embedded scripts
parse the CSVs with real quote-aware helpers rather than line.split(','), and every
column they address by name really exists in roster.FIELDS / teams.FIELDS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from dsl_course import course, roster, teams, welcome

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
    """The github-script body of a workflow's single step, AS SEEDED - through the same
    reader the seeding uses, so the shared helper block is spliced in the way a cohort
    receives it rather than left as its marker."""
    doc = yaml.safe_load(welcome.welcome_workflow(f"welcome/{rel}"))
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
    # Both workflows write the same roster/teams CSVs, and both get their reader/writer
    # from ONE file spliced in at seeding time - so they cannot drift. Asserted on the
    # SEEDED text, because the splice (and its indentation) is what a cohort receives.
    onboard, formation = (
        csv_helpers(script_of(rel, job)) for rel, job in sorted(CSV_WORKFLOWS.items())
    )
    assert onboard == formation
    assert onboard.strip() in welcome.template(welcome.SHARED_SCRIPT)


@pytest.mark.parametrize("rel,job", sorted(CSV_WORKFLOWS.items()))
def test_an_unspliced_workflow_is_still_valid_yaml(rel, job):
    # The marker is a JS comment inside the block scalar, so the file on disk parses (and
    # reads as JavaScript) whether or not anything has spliced into it - a template that
    # only becomes well-formed at seeding time is one nobody can review.
    doc = yaml.safe_load((WELCOME / rel).read_text())
    script = doc["jobs"][job]["steps"][0]["with"]["script"]
    assert welcome.SHARED_SCRIPT_MARK in script
    assert "const parseCsv" not in script  # the copy is gone, not duplicated


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


def test_the_form_reads_the_lock_file_and_nothing_else():
    # The two answers this workflow needs - may a team form, and how big - live in the
    # template's grading_config.yml, in the course org, which this token cannot reach. It
    # used to scrape them out of the cohort's schedule.yml, which no longer carries them
    # at all: every request would be accepted, at a default cap, for any slug.
    script = script_of("team-formation.yml", "form-team")
    assert "assignments.lock.yml" in script and "path: LOCK" in script
    # Nothing reads the cohort's schedule any more, and nothing interprets a `type:`.
    assert "path: 'schedule.yml'" not in script
    assert "declaredType" not in script
    # No cap of its own and no default cap of its own: both come from the file.
    assert "MAX_TEAM_SIZE" not in script and "DEFAULT_TEAM_SIZE" not in script


_JSC = shutil.which("osascript")
# Spelled once and applied as `@needs_js`: every test below that runs the shipped
# JavaScript needs an engine, and a reason repeated per test is a reason to get wrong.
needs_js = pytest.mark.skipif(_JSC is None, reason="no JavaScript engine on this host")

_LOCK_FILE = """\
# SYSTEM-OWNED - do not edit.
assignments:
  solo:
    team_formation: none
    max_team_size: 5
  project:
    team_formation: self_select
    max_team_size: 3
  allocated:
    team_formation: assigned
    max_team_size: 4
"""


def _lock_answers(lock_yml: str | None, slugs: list[str]) -> list[dict | None]:
    """Run the SHIPPED `lockEntry` scanner over `lock_yml` in JavaScriptCore, with its one
    await stubbed out, and return its answer for each slug. `None` for the file being
    absent (the reader's `undefined`, which JSON.stringify drops to null either way).
    Skipped where there is no JS engine (Linux CI); the text guards run everywhere."""
    script = script_of("team-formation.yml", "form-team")
    start = script.index("const LOCK =")
    end = script.index("\n  };", script.index("return formation === null")) + 5
    block = (
        script[start:end]
        .replace("async (slug)", "(slug)")
        .replace("await github", "github")
    )
    missing = lock_yml is None
    harness = (
        f"const LOCKFILE = {json.dumps(lock_yml or '')};\n"
        "const org = 'O', CONFIG = 'c';\n"
        "const Buffer = { from: (s, e) => ({ toString: () => LOCKFILE }) };\n"
        "const github = { rest: { repos: { getContent: () => { "
        f"if ({json.dumps(missing)}) {{ const e = new Error('nope'); e.status = 404; throw e; }} "
        "return { data: { content: '' } }; } } } };\n"
        f"{block}\n"
        f"JSON.stringify([{', '.join(f'lockEntry({json.dumps(s)})' for s in slugs)}]);\n"
    )
    run = subprocess.run(
        [_JSC, "-l", "JavaScript", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@needs_js
def test_the_scanner_reads_every_scalar_for_every_shape():
    # `_LOCK_FILE` predates the formation window, which is what a cohort last synced by an
    # older toolkit looks like: the two window scalars come back unset, and the workflow
    # reads that as open rather than shut (see the behavioural test further down).
    assert _lock_answers(_LOCK_FILE, ["solo", "project", "allocated", "invented"]) == [
        {"formation": "none", "cap": 5, "window": None, "closes": ""},
        {"formation": "self_select", "cap": 3, "window": None, "closes": ""},
        {"formation": "assigned", "cap": 4, "window": None, "closes": ""},
        None,  # a slug the lock file does not carry
    ]


@needs_js
def test_an_absent_lock_file_is_told_apart_from_an_unknown_slug():
    # Different messages: one is a maintainer's problem, the other a typo in the form.
    (answer,) = _lock_answers(None, ["project"])
    assert answer is None
    assert _lock_answers("assignments:\n  {}\n", ["project"]) == [None]


@needs_js
def test_the_scanner_is_not_confused_by_the_files_own_header():
    # Every line of the header is a comment, and the file always carries one.
    from dsl_course import grades

    real = grades.team_lock_text({"project": ("self_select", 3, "open", "2026-10-04")})
    assert _lock_answers(real, ["project"]) == [
        {"formation": "self_select", "cap": 3, "window": "open", "closes": "2026-10-04"}
    ]


@needs_js
def test_an_empty_close_date_is_still_a_line_the_scanner_reads():
    # The writer emits `team_formation_closes:` with nothing after it rather than dropping
    # the line, so the shape the scanner sees never changes; it has to come back as the
    # empty string, not as the next entry's date or as a parse miss.
    from dsl_course import grades

    real = grades.team_lock_text(
        {
            "early": ("self_select", 3, "closed", ""),
            "later": ("self_select", 4, "open", "2026-11-30"),
        }
    )
    assert _lock_answers(real, ["early", "later"]) == [
        {"formation": "self_select", "cap": 3, "window": "closed", "closes": ""},
        {
            "formation": "self_select",
            "cap": 4,
            "window": "open",
            "closes": "2026-11-30",
        },
    ]


def test_only_self_selection_may_form_a_team():
    # The behavioural test above needs a JS engine; these hold the line in CI. `none` and
    # `assigned` each get their own refusal, and anything else - a value written by a
    # newer toolkit than this workflow - falls to the "assigned by the instructor" arm
    # rather than through the guard.
    code = code_of(script_of("team-formation.yml", "form-team"))
    assert "lock.formation === 'none'" in code
    assert "lock.formation !== 'self_select'" in code


def test_each_refusal_says_which_kind_of_assignment_it_is():
    script = script_of("team-formation.yml", "form-team")
    assert "is an individual assignment - no teams." in script
    assert "are assigned by the instructor." in script
    assert "(the cap for \\`${assignment}\\` is ${cap})" in script


def test_every_code_in_the_body_is_redacted_not_only_the_one_that_binds():
    # The redaction re-used the STRICT form match, so a code pasted anywhere the match
    # could not bind from - the wrong field, an extra line - stayed live in a public issue.
    code = code_of(script_of("onboard.yml", "onboard"))
    m = re.search(r"\.replace\(/(.*?)/gi, 'dsl-\[redacted\]'\)", code)
    assert m, "the loose redaction is gone"
    pattern = re.compile(m.group(1), re.IGNORECASE)
    body = "Enrolment code\n\nDSL-ABC234\n\nnote: my other one was dsl-zzz999\n"
    assert pattern.sub("dsl-[redacted]", body).count("dsl-[redacted]") == 2
    assert "abc234" not in pattern.sub("dsl-[redacted]", body).lower()


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
    monkeypatch.setattr(welcome, "ensure_label", lambda *a, **k: True)
    monkeypatch.setattr(welcome, "get_file_content", lambda *a, **k: None)
    welcome.refresh_welcome_workflows("Org")
    assert ".github/ISSUE_TEMPLATE/config.yml" in seen


def test_refresh_seeds_exactly_the_routing_labels_the_forms_declare(monkeypatch):
    # GitHub silently drops a label an issue form declares when the repo doesn't have it,
    # and both workflows gate on theirs - so an unseeded label meant every Join issue was
    # `skipped`: no redaction, no comment, no needs-review, and a green run. The refresh
    # owes the repo the labels, spelt exactly as the forms request them.
    from dsl_course import welcome

    declared = set()
    for rel in ("01-join-course.yml", "02-join-team.yml"):
        declared.update(
            yaml.safe_load((WELCOME / "ISSUE_TEMPLATE" / rel).read_text())["labels"]
        )
    seeded = {name for name, _, _ in welcome.WELCOME_LABELS}
    # Every label a form declares must be seeded, or that form's issues are skipped.
    assert declared <= seeded
    # `team-list` is seeded but declared by no form ON PURPOSE: it marks the list this
    # workflow writes, and keeping it off the routing label is what stops the list
    # triggering a run that answers it in public.
    assert seeded - declared == {"team-list"}

    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: True)
    monkeypatch.setattr(welcome, "get_file_content", lambda *a, **k: None)
    created = []
    monkeypatch.setattr(
        welcome,
        "ensure_label",
        lambda org, repo, name, **k: created.append((org, repo, name)) or True,
    )
    assert welcome.refresh_welcome_workflows("Org") == 0
    assert created == [
        ("Org", "welcome", name) for name, _, _ in welcome.WELCOME_LABELS
    ]


def test_refresh_reds_when_a_routing_label_cannot_be_created(monkeypatch, capsys):
    # The files landing while the label didn't is the exact bug this guards against: the
    # workflows exist but never run. A failed label must red the refresh, not log-and-go.
    from dsl_course import welcome

    monkeypatch.setattr(welcome, "put_files", lambda *a, **k: True)
    monkeypatch.setattr(welcome, "ensure_label", lambda *a, **k: False)
    monkeypatch.setattr(welcome, "get_file_content", lambda *a, **k: None)
    assert welcome.refresh_welcome_workflows("Org") == len(welcome.WELCOME_LABELS)
    assert "up to date" not in capsys.readouterr().out


def test_onboard_throttles_a_student_before_it_touches_the_roster():
    # `welcome` is public and anyone can open an issue in it, and each one costs a private
    # roster read plus an org invite and a team write on the bot token. A student who keeps
    # opening new Join issues instead of reading the reply on the last one pays that on
    # repeat. The count must therefore happen BEFORE the roster read and before the bot
    # token is used at all.
    code = code_of(script_of("onboard.yml", "onboard"))
    throttle = code.index("listForRepo")
    assert "creator: handle" in code and "labels: 'needs-review'" in code
    # The THRESHOLD, as a number and not as a prefix: `>= 30` contains `>= 3`, and a
    # throttle that only fires on the thirtieth open Join issue is no throttle at all.
    assert re.search(r"unresolved\.length >= 3\b", code)
    assert "unresolved Join course issues - contact the teaching team" in code
    assert throttle < code.index("await readRoster()")
    assert throttle < code.index("process.env.HAS_BOT")


def test_the_form_refuses_exactly_the_slugs_the_reconcile_reserves():
    # teams.csv is STUDENT-written, and `team_slug("course", "admin")` is `course-admin` -
    # the faculty team with admin on every repo in the cohort. The form is the gate; the
    # reconcile (teams.is_reserved_slug) is the backstop for a row that reached the
    # CSV another way. Two lists in two languages, so they are pinned to each other here:
    # a role team added to course.ROLE_TEAMS and not to the form is a slug a student can
    # claim, and the reconcile would then add them to it and prune the real members.
    code = code_of(script_of("team-formation.yml", "form-team"))
    declared = re.search(r"const RESERVED = new Set\(\[([^\]]*)\]\)", code)
    assert declared, "the form no longer declares a RESERVED set"
    assert set(re.findall(r"'([^']+)'", declared.group(1))) == set(
        teams.RESERVED_TEAM_SLUGS
    )
    # ...and the `instructors-<tag>` prefix rule, which is a startswith on both sides.
    assert f"startsWith('{course.INSTRUCTORS_TEAM}-')" in code


# --- The whole form decision, run as shipped ----------------------------------------
#
# `_lock_answers` runs one helper out of the script; these run the SCRIPT, over real
# `team_lock_text` output and real CSVs, against stubs for the four API calls it makes.
# The create/join rules are the reason: each of them is a sentence about what the file
# says, what the form said and what gets written - which reading a regex cannot settle.
_ROSTER = ",".join(roster.FIELDS) + "\n"
_TEAMS_HEADER = ",".join(teams.FIELDS) + "\n"


def _roster_of(*handles: str) -> str:
    """students.csv carrying nothing but the handles, addressed by column name."""
    rows = [
        ",".join(h if f == "github_handle" else "" for f in roster.FIELDS)
        for h in handles
    ]
    return _ROSTER + "".join(f"{row}\n" for row in rows)


def _form_body(assignment: str, action: str | None, team: str) -> str:
    """An issue body as GitHub renders the Join-team form - a heading per field, the
    answer under it. `action=None` is an issue opened from a cached copy of the form that
    predates the field."""
    fields = [("Assignment", assignment), ("Action", action), ("Team", team)]
    return "\n".join(f"### {label}\n\n{value}\n" for label, value in fields if value)


def _run_form(
    lock: str,
    teams_csv: str,
    body: str,
    handle: str = "stu",
    roster: tuple[str, ...] | None = None,
    issues: list[dict] | None = None,
    issues_break: bool = False,
) -> dict:
    """Run the SHIPPED team-formation script over these files and this issue, and return
    what it did: `comments`, `labels`, `writes` (each teams.csv it committed), `states`,
    `created` / `edited` (the public team list it opened or rewrote), `pinned` and
    `issues` - the repo's open issues afterwards, to hand to the NEXT run.

    `issues` seeds the welcome repo's open issues, so a second join meets the list the
    first one left behind. `issues_break` makes every read of them fail, which is the
    join that must still be reported as done.

    `await`/`async` are taken out so the stubs can answer synchronously - the same trick
    `_lock_answers` plays on one helper, over the whole script. Nothing else is rewritten,
    so what runs is the JavaScript a cohort receives."""
    code = re.sub(
        r"\basync\s+",
        "",
        re.sub(r"\bawait\s+", "", script_of("team-formation.yml", "form-team")),
    )
    files = {
        "assignments.lock.yml": lock,
        "teams.csv": teams_csv,
        "students.csv": _roster_of(*(roster or ("ann", "bob", handle))),
    }
    issue = {"number": 7, "user": {"login": handle}, "body": body}
    harness = (
        f"const FILES = {json.dumps(files)};\n"
        f"const ISSUE = {json.dumps(issue)};\n"
        f"const ISSUES = {json.dumps(issues or [])};\n"
        f"const BREAK_ISSUES = {json.dumps(issues_break)};\n"
        "const OUT = { comments: [], labels: [], writes: [], states: [],"
        " created: [], edited: [], pinned: [], issues: ISSUES };\n"
        # base64 both ways is a no-op here, so the stubs hold plain text.
        "const Buffer = { from: (s, e) => ({ toString: () => s }) };\n"
        "const process = { env: { HAS_BOT: 'true' } };\n"
        "const setTimeout = (fn, ms) => fn();\n"
        "const core = { setFailed: (m) => {}, warning: (m) => {} };\n"
        "const context = { repo: { owner: 'cohort', repo: 'welcome' },"
        " payload: { issue: ISSUE } };\n"
        "const github = {\n"
        "  paginate: (fn, a) => fn(a),\n"
        "  graphql: (q, v) => { OUT.pinned.push(v.id); return {}; },\n"
        "  rest: {\n"
        "  repos: {\n"
        "    getContent: (a) => { if (!(a.path in FILES)) {"
        " const e = new Error('absent'); e.status = 404; throw e; }\n"
        "      return { data: { content: FILES[a.path], sha: 'sha' } }; },\n"
        "    createOrUpdateFileContents: (a) => { FILES[a.path] = a.content;"
        " OUT.writes.push(a.content); return {}; },\n"
        "  },\n"
        "  issues: {\n"
        "    createComment: (a) => { OUT.comments.push(a.body); return {}; },\n"
        "    addLabels: (a) => { OUT.labels.push(a.labels[0]); return {}; },\n"
        "    listForRepo: (a) => { if (BREAK_ISSUES) {"
        " const e = new Error('no'); e.status = 403; throw e; }\n"
        "      return ISSUES.filter(i => !a.labels || (i.labels || []).indexOf(a.labels) >= 0); },\n"
        "    create: (a) => { const made = { number: 100 + ISSUES.length,"
        " node_id: `N${100 + ISSUES.length}`, title: a.title, body: a.body,"
        " labels: a.labels || [] };\n"
        "      ISSUES.push(made); OUT.created.push(made); return { data: made }; },\n"
        "    update: (a) => { const it = ISSUES.filter(i => i.number === a.issue_number)[0];\n"
        "      if (a.body !== undefined) { if (it) it.body = a.body;"
        " OUT.edited.push({ number: a.issue_number, body: a.body }); }\n"
        "      if (a.state) OUT.states.push(a.state); return {}; },\n"
        "  },\n"
        "} };\n"
        "(function () {\n" + code + "\n})();\n"
        "JSON.stringify(OUT);\n"
    )
    run = subprocess.run(
        [_JSC, "-l", "JavaScript", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _lock_for(window: str, closes: str = "2026-10-04", cap: int = 4) -> str:
    from dsl_course import grades

    return grades.team_lock_text({"assignment-2": ("self_select", cap, window, closes)})


# team-alpha has two of its four seats taken.
_TEAMS_CSV = _TEAMS_HEADER + (
    "assignment-2,team-alpha,ann\nassignment-2,team-alpha,bob\n"
)


@needs_js
def test_a_shut_window_refuses_and_says_which_day_it_shut():
    out = _run_form(
        _lock_for("closed"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    assert out["writes"] == [], "a request outside the window was recorded anyway"
    assert out["labels"] == ["needs-review"]
    assert "team formation for `assignment-2` closed on 4 Oct." in out["comments"][0]


@needs_js
def test_a_window_that_has_not_opened_yet_never_says_it_closed():
    # The close date is in the FUTURE while the window is pending, so the shut wording
    # would tell a student in September that the door closed in October. The two states
    # are kept apart in the lock precisely so this sentence can differ.
    out = _run_form(
        _lock_for("pending"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    assert out["writes"] == [], "a request before the window opened was recorded anyway"
    assert out["labels"] == ["needs-review"]
    said = out["comments"][0]
    assert "is not open yet" in said
    assert "closed on" not in said, "a pending window must not claim it has shut"
    assert "runs until 4 Oct" in said


@needs_js
def test_a_lock_written_before_the_window_existed_still_forms_teams():
    # THE fail-open case. A cohort whose last sync predates the window carries no
    # `team_formation_window:` scalar at all; reading that as closed would take team
    # formation away from every such cohort at once, silently, until someone noticed.
    out = _run_form(
        "assignments:\n  assignment-2:\n"
        "    team_formation: self_select\n    max_team_size: 4\n",
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    assert out["labels"] == ["team-recorded"]
    assert "assignment-2,team-alpha,stu" in out["writes"][0]


@needs_js
def test_creating_a_team_whose_name_is_taken_is_refused_not_joined():
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Create a new team", "team-alpha"),
    )
    assert out["writes"] == []
    assert (
        "**team-alpha** already exists for `assignment-2` (2/4)" in out["comments"][0]
    )
    assert "Join an existing team" in out["comments"][0]


@needs_js
def test_joining_a_mistyped_name_names_the_team_that_was_meant():
    # The bug this whole field exists for: `teamalpha` for `team-alpha` used to open a
    # second, half-empty team, and nobody found out until the release provisioned both.
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "TeamAlpha"),
    )
    assert out["writes"] == []
    assert "no team **teamalpha** for `assignment-2`" in out["comments"][0]
    assert "Did you mean **team-alpha** (2/4)?" in out["comments"][0]


@needs_js
def test_joining_a_name_nothing_resembles_is_refused_with_somewhere_to_look():
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-zeta"),
    )
    assert out["writes"] == []
    assert "no team **team-zeta** for `assignment-2`" in out["comments"][0]
    assert "Did you mean" not in out["comments"][0]
    assert "Create a new team" in out["comments"][0]


@needs_js
@pytest.mark.parametrize(
    "action,team",
    [("Join an existing team", "team-alpha"), ("Create a new team", "team-beta")],
    ids=["join", "create"],
)
def test_both_actions_still_end_in_a_row_in_teams_csv(action, team):
    out = _run_form(
        _lock_for("open"), _TEAMS_CSV, _form_body("assignment-2", action, team)
    )
    assert out["labels"] == ["team-recorded"] and out["states"] == ["closed"]
    assert out["writes"][0].endswith(f"assignment-2,{team},stu\n")


@needs_js
def test_an_issue_from_a_form_with_no_action_field_is_answered_as_it_always_was():
    # A student's browser can hold a cached copy of the form from before the field. Such
    # an issue must not be refused for saying nothing: it falls back to the old implicit
    # rule - join the team if it is there, start it if it is not.
    joined = _run_form(
        _lock_for("open"), _TEAMS_CSV, _form_body("assignment-2", None, "team-alpha")
    )
    assert joined["writes"][0].endswith("assignment-2,team-alpha,stu\n")
    started = _run_form(
        _lock_for("open"), _TEAMS_CSV, _form_body("assignment-2", None, "team-gamma")
    )
    assert started["writes"][0].endswith("assignment-2,team-gamma,stu\n")


# --- The public team list ------------------------------------------------------------
#
# teams.csv is private, so the Join-team form's "type its name exactly" only ever worked
# for people who had already agreed a team face to face. These run the shipped script and
# read the issue it actually writes.


def _teams_list(out: dict) -> str:
    """The body of the `Teams for ...` issue this run opened or rewrote."""
    wrote = out["created"] + out["edited"]
    assert len(wrote) == 1, f"expected one team-list write, got {wrote}"
    return wrote[0]["body"]


@needs_js
def test_a_first_join_opens_the_public_team_list():
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    (made,) = out["created"]
    assert made["title"] == "Teams for assignment-2"
    # Its OWN label, never the routing one: the next run finds it again in one listing,
    # and a list wearing `team-formation` would trigger the very workflow that wrote it.
    assert made["labels"] == ["team-list"]
    assert out["pinned"] == [made["node_id"]], "the form calls it pinned; it must be"
    assert made["body"].splitlines()[0] == (
        "Up to 4 people per team. Team formation closes on 4 Oct."
    )
    assert "- **team-alpha** - 3/4, room for 1" in made["body"]


@needs_js
def test_a_list_for_a_window_with_no_close_date_says_only_the_cap():
    out = _run_form(
        _lock_for("open", closes=""),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    assert _teams_list(out).splitlines()[0] == "Up to 4 people per team."
    assert "closes" not in _teams_list(out)


@needs_js
def test_a_second_join_rewrites_the_same_list_rather_than_opening_another():
    first = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Create a new team", "team-beta"),
    )
    # The file the first join left behind is what the second one reads.
    second = _run_form(
        _lock_for("open"),
        first["writes"][-1],
        _form_body("assignment-2", "Join an existing team", "team-beta"),
        handle="cara",
        roster=("ann", "bob", "stu", "cara"),
        issues=first["issues"],
    )
    assert second["created"] == [], "a second list was opened for the same assignment"
    (edit,) = second["edited"]
    assert edit["number"] == first["created"][0]["number"]
    assert second["pinned"] == [], "an issue that already exists is not pinned again"
    assert "- **team-alpha** - 2/4, room for 2" in edit["body"]
    assert "- **team-beta** - 2/4, room for 2" in edit["body"]


@needs_js
def test_a_full_team_is_listed_as_full():
    out = _run_form(
        _lock_for("open", cap=3),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
    )
    assert "- **team-alpha** - 3/3, full" in _teams_list(out)
    assert "room for" not in _teams_list(out)


@needs_js
def test_the_list_is_rebuilt_from_the_rows_not_incremented():
    # THE property that makes a lost update harmless. The concurrency group is per ISSUE
    # on purpose, so two joins can reach the upsert at once and one edit can be lost. A
    # body rendered from the rows this run wrote is right again on the very next join;
    # a count carried forward would stay wrong for the rest of the term. The list handed
    # in here is deliberately WRONG, and the run must not build on it.
    stale = {
        "number": 42,
        "title": "Teams for assignment-2",
        "labels": ["team-list"],
        "body": "Up to 4 people per team.\n\n- **team-alpha** - 1/4, room for 3",
    }
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
        issues=[stale],
    )
    (edit,) = out["edited"]
    assert edit["number"] == 42
    assert "- **team-alpha** - 3/4, room for 1" in edit["body"]
    assert "1/4" not in edit["body"], "the stale count survived the rewrite"


@needs_js
def test_the_list_carries_no_handle_from_the_roster():
    # THE regression guard. `welcome` is PUBLIC and this issue is the one thing in it the
    # whole cohort is pointed at. A team name is student-chosen and public by
    # construction; who is in it is not - no handle, no name, no email, no
    # `<slug>-<handle>` repo name, ever.
    people = ("mariechen", "jbloggs", "hbaker")
    csv = _TEAMS_HEADER + (
        "assignment-2,team-alpha,mariechen\nassignment-2,team-alpha,jbloggs\n"
    )
    out = _run_form(
        _lock_for("open"),
        csv,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
        handle="hbaker",
        roster=people,
    )
    written = _teams_list(out).lower()
    for who in people:
        assert who not in written, f"the public team list names {who}"
    assert f"assignment-2-{people[2]}" not in written
    # ...and it does carry what it is for.
    assert "team-alpha" in written and "3/4" in written


@needs_js
def test_a_team_list_that_cannot_be_written_still_reports_the_join_as_done():
    # The membership is already in teams.csv by the time the list is touched. A student
    # must never be told their join failed because a cosmetic list could not be updated -
    # and `needs-review` must not be raised for something the next join fixes by itself.
    out = _run_form(
        _lock_for("open"),
        _TEAMS_CSV,
        _form_body("assignment-2", "Join an existing team", "team-alpha"),
        issues_break=True,
    )
    assert out["writes"][0].endswith("assignment-2,team-alpha,stu\n")
    assert out["labels"] == ["team-recorded"] and out["states"] == ["closed"]
    assert out["created"] == [] and out["edited"] == []
    assert "you're in team **team-alpha**" in out["comments"][0]


def test_the_workflow_does_not_answer_its_own_team_list():
    # The list is opened with DSL_BOT_TOKEN, a PAT - whose issues, unlike the ambient
    # token's, DO start workflow runs. Were it to carry the ROUTING label, opening it
    # would answer it in public with "I can't find you on the course enrolment roster",
    # on the one issue the whole cohort is pointed at. The two labels are what make that
    # unreachable, so this pins them apart at both ends rather than trusting a title.
    doc = yaml.safe_load((WELCOME / "team-formation.yml").read_text())
    guard = doc["jobs"]["form-team"]["if"]
    routes_on = yaml.safe_load(
        (WELCOME / "ISSUE_TEMPLATE/02-join-team.yml").read_text()
    )["labels"]
    assert routes_on == ["team-formation"]
    assert f"'{routes_on[0]}'" in guard

    script = script_of("team-formation.yml", "form-team")
    # The label the list is LOOKED UP by, and the one it is CREATED with, must be the same
    # label - or a second list is opened on every join - and it must not be the routing
    # one, or opening it starts a run against itself.
    (listed,) = set(re.findall(r"labels: '([\w-]+)', per_page", script))
    assert f"labels: ['{listed}']" in script, (
        "the list is created and looked up under different labels"
    )
    assert listed != routes_on[0]


def test_the_join_team_form_points_at_a_list_that_exists():
    # The form promises a PINNED issue by that title; the workflow has to be the thing
    # that makes both halves of that sentence true.
    form = (WELCOME / "ISSUE_TEMPLATE/02-join-team.yml").read_text()
    assert "pinned **Teams for \\<assignment\\>** issue" in form
    script = script_of("team-formation.yml", "form-team")
    assert "const TEAMS_ISSUE_PREFIX = 'Teams for '" in script
    assert "pinIssue" in script


# --- The generated Assignment field --------------------------------------------------


def test_a_cohort_with_nothing_open_keeps_the_free_text_assignment_field():
    # A dropdown needs at least one option, and a form GitHub refuses to render is worse
    # than an awkward one: it takes the Join-team route away from the cohort entirely.
    assert welcome.join_team_form([]) == welcome.template(welcome.JOIN_TEAM_FORM)
    assert welcome.join_team_form([]) == welcome.join_team_form(())


def test_the_open_assignments_become_a_dropdown_the_workflow_can_still_parse():
    form = welcome.join_team_form(["assignment-2", "assignment-4-project"])
    doc = yaml.safe_load(form)
    fields = {b["id"]: b for b in doc["body"] if "id" in b}
    assert [b.get("id", b["type"]) for b in doc["body"]] == [
        "markdown",
        "assignment",
        "action",
        "team",
    ]
    assert fields["assignment"]["type"] == "dropdown"
    assert fields["assignment"]["attributes"]["options"] == [
        "assignment-2",
        "assignment-4-project",
    ]
    assert fields["assignment"]["validations"]["required"] is True
    # The action field is never generated - two fixed options, the same in every cohort.
    assert fields["action"]["attributes"]["options"] == [
        "Join an existing team",
        "Create a new team",
    ]


def test_the_splice_replaces_the_field_and_leaves_the_rest_of_the_form_alone():
    form = welcome.join_team_form(["assignment-2"])
    assert welcome.ASSIGNMENT_FIELD_START in form
    assert welcome.ASSIGNMENT_FIELD_END in form
    assert "type: input\n    id: assignment" not in form  # the fallback is gone
    # Everything outside the markers is the reviewed template, to the byte.
    template = welcome.template(welcome.JOIN_TEAM_FORM)
    head = template.index(welcome.ASSIGNMENT_FIELD_START)
    tail = template.index(welcome.ASSIGNMENT_FIELD_END) + len(
        welcome.ASSIGNMENT_FIELD_END
    )
    assert form.startswith(template[:head]) and form.endswith(template[tail:])


def test_only_the_assignments_whose_window_is_open_are_offered():
    # Offering a slug the workflow would refuse is inviting a student to be refused, and
    # the refusal arrives minutes later as a comment.
    from dsl_course import grades

    lock = grades.team_lock_text(
        {
            "a1": ("none", 5, "none", ""),
            "a2": ("self_select", 4, "closed", "2026-09-01"),
            "a3": ("self_select", 4, "open", "2026-10-04"),
            "a4": ("assigned", 4, "none", ""),
        }
    )
    assert welcome.open_formation_slugs(lock) == ["a3"]
    assert welcome.open_formation_slugs("assignments:\n  {}\n") == []


def test_a_cohort_whose_lock_cannot_be_read_gets_the_free_text_form(monkeypatch):
    # Never a crash and never an empty dropdown: a cohort seeded before the file existed,
    # one whose sync has not run, and a read that failed all land on the same fallback.
    for answer in (None, ""):
        monkeypatch.setattr(
            welcome, "get_file_content", lambda *a, _answer=answer, **k: _answer
        )
        assert welcome._open_formation_slugs("Org") == []

    def boom(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(welcome, "get_file_content", boom)
    assert welcome._open_formation_slugs("Org") == []
