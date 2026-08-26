"""Render the run-from-repo and org-level faculty & instructors workflows as YAML text.

Pure text rendering - no network, no filesystem: every function here takes the already
discovered dropdown contents (see discovery) and returns the workflow YAML. seed places
the results.

The templates are deliberately f-strings rather than a programmatic YAML builder: the
rendered files carry human-facing comments (faculty read them in the repo, next to where
they run them) and a deliberate key order, both of which a yaml.dump round-trip would destroy.
Shared boilerplate that repeats verbatim between renderers is extracted into the small
constants/helpers below (the check-team gate, the checkout+python job preamble, the
dropdown builders); the prose and ordering stay per-workflow.

The Release materials workflow's inputs are deliberately the SAME five fields as a
schedule.yml `deploy:` entry (course_source_repo, course_source_path, cohort_dest_repo,
cohort_dest_path, plus the cohort org) - one vocabulary for the scheduled and the manual
path, so what faculty learn on the workflow reads straight across into the schedule.
Nothing about the workflow is discovered from the source repo any more: `course_source_path`
is free text (a folder, a file, or a comma-separated list), so it needs no per-section
checkbox and no session dropdown, and both variants stay well under GitHub's 10-input
workflow_dispatch cap.
"""

from __future__ import annotations

import re

from .central import CENTRAL, CENTRAL_REF

# Third-party actions pinned to full commit SHAs. Every job below runs with an org-owner
# PAT in its env, so a mutable tag (`@v4`) is a standing invitation: whoever can move the
# tag runs code in that environment. The trailing comment is the human-readable version -
# bump both together.
_CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0"
_SETUP_PYTHON = (
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5.6.0"
)

# Every workflow in an org is re-rendered from this module by Refresh actions, nightly, so
# a hand-edit to one survives at most a day. Stamped on at the write sites (seed.py) rather
# than inside each renderer's own header, so a new renderer cannot ship without it.
SYSTEM_OWNED_BANNER = (
    "# SYSTEM-OWNED - do not edit, edits here are overwritten. This workflow is\n"
    "# re-rendered from the central DSL teaching toolkit by 'Refresh actions' (nightly).\n"
)


def system_owned(rendered: str) -> str:
    """Prefix a rendered workflow with the ownership banner."""
    return SYSTEM_OWNED_BANNER + rendered


# Workflow-level permissions for every rendered workflow. Each one authenticates with
# secrets.DSL_BOT_TOKEN and never needs the ambient GITHUB_TOKEN - not even to check out,
# because the central repo is public. So the ambient token is dropped to zero scopes.
# Emitted together with the `jobs:` key it precedes, so no renderer can grow a jobs block
# without one.
_PERMISSIONS_JOBS = """permissions: {}

jobs:
"""

# `seed refresh` converges a whole org - dozens of Contents-API writes across every content
# repo, .github, and every registered cohort - so two runs at once race each other into sha
# conflicts. The nightly refresh is therefore serialised AGAINST ITSELF, and nothing else
# joins the group.
#
# Deliberately NOT shared with the workflows that end in a refresh (New materials, New
# assignment, Bootstrap cohort). Actions concurrency has no `queue:`: a group holds exactly
# ONE pending run, so a third arrival CANCELS the second - `cancel-in-progress: false`
# notwithstanding. Putting an operator's click in a group with a nightly cron therefore
# means a click that silently does nothing, which is the worse failure: the operator is
# told the run started and never learns it was dropped. A workflow racing the nightly refresh
# can at worst take a put_file 409 - visible, red, and healed by the next converge.
_SEED_REFRESH_CONCURRENCY = """concurrency:
  group: seed-refresh
  cancel-in-progress: false
"""

# The scheduler runs hourly and can outlive its slot (it clones and grades submissions
# across every cohort), so it can overlap itself. Its actions are fire-once, guarded by
# markers written as they complete - so two concurrent passes can double-release or
# double-grade whatever the first has not yet marked. Queue instead.
_SCHEDULED_RELEASE_CONCURRENCY = """concurrency:
  group: scheduled-release
  cancel-in-progress: false
"""

_CHECK_TEAM = """  check-team:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Verify the user may run actions for THIS repo
        env:
          GH_TOKEN: ${{ secrets.DSL_BOT_TOKEN }}
          ACTOR: ${{ github.actor }}
          REPO: ${{ github.repository }}
        run: |
          # Faculty & instructors have write+ on the course repos; students never do (and triggering a
          # workflow_dispatch already requires write), so repo permission is the gate.
          perm=$(gh api "repos/$REPO/collaborators/$ACTOR/permission" --jq '.permission' 2>/tmp/gherr || true)
          case "$perm" in admin|write|maintain) exit 0 ;; esac
          echo "::error::@$ACTOR lacks write on $REPO (permission='$perm'). gh api said:"
          cat /tmp/gherr || true
          exit 1
"""

# Job time budgets. Every job is bounded (an unbounded one that hangs holds a runner for
# GitHub's 6-hour default), but the bound has to fit the work: a timeout that fires on a
# healthy run is an outage, not a safety net.
#
# 30 is the ordinary budget - a handful of API calls, or one repo cloned.
# 60 covers Bootstrap cohort: it creates and configures a whole org, then converges it.
# 120 covers the two jobs that grade: collect budgets 300s PER submission subprocess and
#     walks a cohort serially, and the hourly scheduler does that for every cohort (its own
#     concurrency comment says a pass can outlive its slot).
_TIMEOUT_DEFAULT = 30
_TIMEOUT_BOOTSTRAP = 60
_TIMEOUT_GRADING = 120


# The head of any job that runs toolkit code: a runner, the central repo checked out at
# CENTRAL_REF, Python, and the deps. Used bare by the UNGATED jobs (cron /
# repository_dispatch / push paths have no actor to gate on), and behind the check-team
# gate via _run_preamble by every workflow. Ends after `pip install`, so a renderer appends
# its own `      - name: ...` step directly. The timeout is the ONE thing that varies, so
# it is a parameter rather than a second copy of the preamble.
def _ungated_preamble(minutes: int = _TIMEOUT_DEFAULT) -> str:
    return f"""    runs-on: ubuntu-latest
    timeout-minutes: {minutes}
    steps:
      - uses: {_CHECKOUT}
        with:
          repository: {CENTRAL}
          ref: {CENTRAL_REF}
      - uses: {_SETUP_PYTHON}
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
"""


def _run_preamble(minutes: int = _TIMEOUT_DEFAULT) -> str:
    return f"    needs: check-team\n{_ungated_preamble(minutes)}"


# SMTP secrets, wired into the env of the workflows that send email (enrolment codes, grade
# notifications). A plain string (not the f-string body) so the GitHub `${{ }}` is literal.
_MAIL_ENV = """\
          GRAPH_TENANT_ID: ${{ secrets.GRAPH_TENANT_ID }}
          GRAPH_CLIENT_ID: ${{ secrets.GRAPH_CLIENT_ID }}
          GRAPH_CLIENT_SECRET: ${{ secrets.GRAPH_CLIENT_SECRET }}
          GRAPH_SENDER: ${{ secrets.GRAPH_SENDER }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          SMTP_FROM: ${{ secrets.SMTP_FROM }}"""


# Unattended-run visibility, appended to the job of every workflow that has a cron.
# GitHub emails a scheduled-run failure notice to whoever last committed the cron file -
# which is always the bot - so nobody is told. A failing cron is then invisible until
# someone notices that the thing it maintains stopped happening, which for a nightly
# convergence job can be weeks. These two steps keep exactly ONE issue in the repo the
# workflow runs in tracking the current state: opened (or commented on, if already open) by
# a failure, closed by the next green run. Deduped by title search, gh CLI only, using the
# DSL_BOT_TOKEN the job already carries (workflow-level `permissions: {}` means the ambient
# token could not do it).
#
# A workflow_dispatch failure is skipped: someone is watching that run, and a deliberate
# manual test going red should not file a ticket. A manual SUCCESS still closes the issue,
# because re-running by hand is exactly how a human confirms the fix - which is why
# _CRON_CLOSE is also appended on its own to the manually-runnable job of the workflows
# whose notice lives in a schedule-gated one.
#
# The issue title is the workflow's OWN name, taken from the ambient `github.workflow`, so
# each workflow keeps its own issue (a shared title would let one recovery close another's
# open failure) with no per-renderer string to keep in step with the `name:` above it.
_CRON_CLOSE = """      - name: Close the failure issue once a run succeeds
        if: success()
        env:
          GH_TOKEN: ${{ secrets.DSL_BOT_TOKEN }}
          WORKFLOW: ${{ github.workflow }}
          REPO: ${{ github.repository }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          title="$WORKFLOW is failing"
          for n in $(gh issue list --repo "$REPO" --state open --search "$title in:title" --json number --jq '.[].number'); do
            gh issue close "$n" --repo "$REPO" --comment "Recovered: $RUN_URL"
          done
"""

# `cancelled()`, not just `failure()`: a job killed by its own `timeout-minutes` is
# CANCELLED, and a cron that reliably runs out of time is exactly the silent failure this
# exists to surface.
_CRON_NOTICE = (
    """      - name: Report an unattended failure as an issue
        if: (failure() || cancelled()) && github.event_name != 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ secrets.DSL_BOT_TOKEN }}
          WORKFLOW: ${{ github.workflow }}
          REPO: ${{ github.repository }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          title="$WORKFLOW is failing"
          # A filed issue emails only the repo's watchers, which in practice is nobody.
          # The mention makes a red cron land in the admins' inbox; course-admin, not
          # instructors, because broken infrastructure is not the teaching staff's problem.
          body=$(printf 'The unattended run failed or was cancelled: %s\\n\\nNothing retries it before the next scheduled run. This issue closes itself once a run succeeds.\\n\\ncc @%s/course-admin\\n' "$RUN_URL" "${REPO%%/*}")
          # The step runs under `bash -e`, so an unguarded capture would abort the step on a
          # transient search failure - before the `gh issue create` that is the whole point.
          # No dedupe hit just means we file a fresh issue.
          existing=$(gh issue list --repo "$REPO" --state open --search "$title in:title" --json number --jq '.[0].number') || true
          if [ -n "$existing" ]; then
            gh issue comment "$existing" --repo "$REPO" --body "$body"
          else
            gh issue create --repo "$REPO" --title "$title" --body "$body"
          fi
"""
    + _CRON_CLOSE
)


def _choice(options: list[str]) -> str:
    opts = options or ["(none-yet)"]
    return "\n".join(f"          - {o}" for o in opts)


# A trailing academic year in an org/repo name - the naming convention's term marker
# (`...-f2026`, `course-materials-f2026`). Anchored to 19xx/20xx so a course code that
# merely ends in four digits (`...-e1234`) is not mistaken for a year.
_TERM_YEAR = re.compile(r"((?:19|20)\d{2})\D*$")


def _newest(options: list[str]) -> str | None:
    """The option carrying the latest term year, or None when none of them carries one
    (in which case GitHub's own "first option is selected" behaviour stands). Faculty
    almost always want the cohort/materials repo they are teaching now, and the dropdowns
    are sorted alphabetically, so without this the oldest cohort is pre-selected."""
    dated = [(m.group(1), o) for o in options if (m := _TERM_YEAR.search(o))]
    return max(dated)[1] if dated else None


def _choice_input(
    name: str, description: str, options: list[str], default: str | None = None
) -> str:
    """A required dropdown input. Pre-selected on `default` if given, otherwise on the
    latest term year (see _newest) - every org/repo dropdown in every workflow, so a faculty
    member never has to scroll past last year's cohort to reach this year's."""
    default = default or _newest(options)
    return (
        f'      {name}:\n        description: "{description}"\n'
        "        required: true\n        type: choice\n"
        + (f'        default: "{default}"\n' if default else "")
        + f"        options:\n{_choice(options)}"
    )


# The five Release materials inputs read top to bottom as the release itself: what to copy
# (1, 2), then where it lands (3, 4, 5). They are numbered in the UI because GitHub renders
# workflow_dispatch inputs as a flat list of boxes with no grouping. The input NAMES are
# still a schedule.yml `deploy:` entry's keys exactly - the mapping is the key itself, not
# the label, so the descriptions stay plain English rather than echoing the snake_case.
# course_source_path and cohort_dest_path are comma-separated PARALLEL lists paired by
# index (see deploy.parse_path_pairs); a blank cohort_dest_path mirrors every
# course_source_path, exactly as an omitted `cohort_dest_path:` does in the schedule.
#
# Only cohort_dest_path is optional, and it ships EMPTY rather than pre-filled - a
# `default:` on a free-text box is submitted verbatim, so pre-filling one reads as a value
# the faculty member chose. cohort_dest_repo is REQUIRED on the workflow (the schedule's
# `deploy:` may still omit it and take deploy's `materials` fallback): naming the repo a
# release lands in is the one decision worth forcing, since a typo'd or defaulted target
# quietly creates a second materials repo the cohort never sees.
_COURSE_SOURCE_REPO_DESC = "1. repo to release from in the course org"

_COURSE_SOURCE_PATH_INPUT = """\
      course_source_path:
        description: "2. within-repo folder/file path to copy from (or comma-separated list)"
        required: true"""

_COHORT_DEST_INPUTS = """\
      cohort_dest_repo:
        description: "4. repo to release to in the cohort org; created if missing"
        required: true
      cohort_dest_path:
        description: "5. within-repo destination path (blank mirrors box 2's path(s)); created if missing"
        required: false"""


def _render_release(header: str, cohort_orgs: list[str], source_repo_input: str) -> str:
    """The Release materials workflow, shared by both variants. Its five inputs ARE a
    schedule.yml `deploy:` entry (plus the cohort org): the same names, the same meaning -
    and the same executor, deploy.deploy_many, so a batch of paths clones each repo once
    whether it arrives from the cron or from this workflow. Only the `course_source_repo`
    widget differs between variants (a dropdown centrally, a pre-filled string inside a
    content repo), which is why it is passed in."""
    return f"""name: Release materials
{header}
on:
  workflow_dispatch:
    inputs:
{source_repo_input}
{_COURSE_SOURCE_PATH_INPUT}
{_choice_input("cohort_org", "3. target cohort org", cohort_orgs)}
{_COHORT_DEST_INPUTS}

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  release:
{_run_preamble()}      - name: Release
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          SRC_ORG: ${{{{ github.repository_owner }}}}
          COURSE_SOURCE_REPO: ${{{{ inputs.course_source_repo }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          COURSE_SOURCE_PATH: ${{{{ inputs.course_source_path }}}}
          COHORT_DEST_REPO: ${{{{ inputs.cohort_dest_repo }}}}
          COHORT_DEST_PATH: ${{{{ inputs.cohort_dest_path }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.deploy --source-org "$SRC_ORG" \\
            --course-source-repo "$COURSE_SOURCE_REPO" --cohort-org "$COHORT_ORG" \\
            --course-source-path "$COURSE_SOURCE_PATH" --cohort-dest-repo "$COHORT_DEST_REPO" \\
            --cohort-dest-path "$COHORT_DEST_PATH"
"""


def render_release(cohort_orgs: list[str], repo: str) -> str:
    """Run-from-repo copy: `course_source_repo` is a free-text field pre-filled with `repo`
    (the repo this workflow is being seeded into), so the common case needs no thought
    but a different source repo in the same org can still be typed in."""
    source_repo_input = (
        f'      course_source_repo:\n        description: "{_COURSE_SOURCE_REPO_DESC}"\n'
        f'        required: true\n        default: "{repo}"'
    )
    return _render_release(
        header=(
            "\n# Run from a course content repo: course_source_repo is pre-filled with THIS"
            " repo (editable).\n# Copies the given path(s) into the cohort org."
            " course_source_path and\n# cohort_dest_path are comma-separated parallel lists"
            " paired by index - leave\n# cohort_dest_path blank to mirror course_source_path."
            " These are exactly a schedule.yml\n# `deploy:` entry's fields.\n# The cohort"
            " dropdown is refreshed by the 'Refresh actions' workflow.\n"
        ),
        cohort_orgs=cohort_orgs,
        source_repo_input=source_repo_input,
    )


def render_central_release(source_repos: list[str], cohort_orgs: list[str]) -> str:
    """Central copy that lives in .github: `course_source_repo` is a dropdown of the course
    org's content repos (discovery.discover_content_repos), since this workflow lives
    outside any one of them. Otherwise identical to the run-from-repo workflow."""
    source_repo_input = _choice_input(
        "course_source_repo", _COURSE_SOURCE_REPO_DESC, source_repos
    )
    return _render_release(
        header=(
            "\n# Central copy: pick the SOURCE repo in this course org, then the path(s) to"
            " copy into\n# the cohort org. course_source_path and cohort_dest_path are"
            " comma-separated parallel lists paired\n# by index - leave cohort_dest_path"
            " blank to mirror course_source_path. These are exactly a\n# schedule.yml"
            " `deploy:` entry's fields.\n# Dropdowns are refreshed by the 'Refresh actions'"
            " workflow.\n"
        ),
        cohort_orgs=cohort_orgs,
        source_repo_input=source_repo_input,
    )


def _assignment_input(assignments: list[str]) -> str:
    """The course-org repo to hand out from - a dropdown of discovered assignment
    templates, or free-text. Named as in schedule.yml: `course_source_repo`."""
    if assignments:
        return _choice_input(
            "course_source_repo", "Course-org repo to hand out from", assignments
        )
    return (
        '      course_source_repo:\n        description: "Course-org repo to hand out from (e.g. assignment-1-f2026)"\n'
        "        required: true"
    )


def render_provision(
    cohort_orgs: list[str], assignments: list[str] | None = None
) -> str:
    return f"""name: Release assignment

# Generates one private repo per onboarded student from the chosen assignment template
# repo (native template-generate). The assignment dropdown lists the course org's
# assignment-* template repos; refresh repopulates it.

on:
  workflow_dispatch:
    inputs:
{_choice_input("cohort_org", "Target cohort org", cohort_orgs)}
{_assignment_input(assignments or [])}
      include_solution:
        description: "Also push the solution (from the template's solution branch) into each student repo"
        type: boolean
        default: false
      type:
        description: "individual (one repo per student) or group (one per team from teams.csv). auto = whatever schedule.yml / the template's grading.yml declare (default: individual)"
        required: true
        type: choice
        default: auto
        options:
          - auto
          - individual
          - group
      dry_run:
        description: "Preview only - list the repos that WOULD be created, don't create them"
        type: boolean
        default: false

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  provision:
{_run_preamble()}      - name: Provision
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          MASTER_ORG: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          COURSE_SOURCE_REPO: ${{{{ inputs.course_source_repo }}}}
          INC_SOL: ${{{{ inputs.include_solution }}}}
          TYPE: ${{{{ inputs.type }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          gh auth setup-git
          args=(--master-org "$MASTER_ORG" --course-source-repo "$COURSE_SOURCE_REPO" --cohort-org "$COHORT_ORG")
          [ "$INC_SOL" = "true" ] && args+=(--solution)
          args+=(--type "$TYPE")
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.assign "${{args[@]}}"
"""


def render_grade_assignment(
    cohort_orgs: list[str], assignments: list[str] | None = None
) -> str:
    """Faculty-side autograder workflow: run hidden tests after the deadline, record scores."""
    return f"""name: Grade assignment

# Faculty-side autograder, by hand. The hourly cron already grades each assignment ONCE at
# its grading deadline - this workflow is for a deliberate re-grade. Clones each submission as
# of the cohort schedule's grading deadline (`grading_datetime`, else `due_datetime` -
# SSOT, no input here), runs the HIDDEN tests from the template's solution branch, archives
# result.json, and fills the machine score into the private grades CSV (faculty & instructors
# then add manual marks; Render + Distribute send them).
#
# WRITE-ONCE: an `autograde_score`/`team` cell that already holds a value is never
# overwritten, so re-running does NOT refresh scores - it only fills cells still empty. For a
# fresh machine score, clear those cells first (and delete classroom-config/autograde/<slug>/
# to let the cron regrade). Nothing is written to student repos. dry_run lists what would be
# graded.

on:
  workflow_dispatch:
    inputs:
{_choice_input("cohort_org", "Cohort org (submissions)", cohort_orgs)}
{_assignment_input(assignments or [])}
      group:
        description: "Group assignment - grade one repo per team"
        type: boolean
        default: false
      dry_run:
        description: "Preview only - list the repos that WOULD be graded"
        type: boolean
        default: false

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  grade:
{_run_preamble(_TIMEOUT_GRADING)}      - name: Grade
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          MASTER_ORG: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          COURSE_SOURCE_REPO: ${{{{ inputs.course_source_repo }}}}
          GROUP: ${{{{ inputs.group }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          gh auth setup-git
          pip install --quiet pytest nbconvert
          args=(--master-org "$MASTER_ORG" --course-source-repo "$COURSE_SOURCE_REPO" --cohort-org "$COHORT_ORG")
          [ "$GROUP" = "true" ] && args+=(--group)
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.collect "${{args[@]}}"
"""


_FACULTY_ONLY = "(faculty only)"


def render_sync_membership(cohort_orgs: list[str]) -> str:
    """Consolidated roster + project-teams + faculty sync (replaces the old separate
    Sync enrolment / Sync teams workflows).

    Faculty always reconciles - split by role: course_admins (from THIS org's
    declared `people:` block) into the course org + every cohort's own course-admin
    team; and, for whichever cohort is in scope, that cohort's own instructors/TAs
    (from its classroom-config/people.yml) into its own instructors team + a
    course-org instructors-<tag> team. Roster (students.csv) + project teams
    (teams.csv) additionally reconcile for whichever cohort is in scope. Fully
    automatic, including removals (no --prune flag - config is the live truth):

    - push to this file's own dsl-course.yml -> course_admins only (no single cohort
      implied - but still applied to every cohort's own course-admin team)
    - repository_dispatch (from a cohort's classroom-config dispatcher on push to its
      students.csv/teams.csv/people.yml) -> course_admins + that one cohort's
      instructors/TAs
    - daily cron -> course_admins + EVERY registered cohort (roster/teams/instructors,
      catching any start/end date rotation with no edit that day, and any drift
      generally)
    - workflow_dispatch -> manual escape hatch, gated by check-team (the other three
      trigger types skip that gate, same as the existing scheduler workflow already
      does for cron)
    """
    return f"""name: Sync membership

on:
  push:
    branches: [main]
    paths:
      - dsl-course.yml
  repository_dispatch:
    types: [sync-membership]
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs, optional=True)}

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  sync-dispatch:
{_run_preamble()}      - name: Sync membership
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
        run: |
          args=(--course-org "$COURSE")
          [ "$COHORT_ORG" != "{_FACULTY_ONLY}" ] && args+=(--cohort-org "$COHORT_ORG")
          python3 -m dsl_course.sync_membership "${{args[@]}}"
{_CRON_CLOSE}
  sync-auto:
    if: github.event_name != 'workflow_dispatch'
{_ungated_preamble()}      - name: Sync membership
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          EVENT: ${{{{ github.event_name }}}}
          DISPATCH_COHORT: ${{{{ github.event.client_payload.cohort_org }}}}
        run: |
          args=(--course-org "$COURSE")
          case "$EVENT" in
            schedule) args+=(--all-cohorts) ;;
            repository_dispatch) [ -n "$DISPATCH_COHORT" ] && args+=(--cohort-org "$DISPATCH_COHORT") ;;
          esac
          python3 -m dsl_course.sync_membership "${{args[@]}}"
{_CRON_NOTICE}"""


def _cohort_dropdown(cohort_orgs: list[str], optional: bool = False) -> str:
    """The plain cohort dropdown. `optional` prepends the faculty-only sentinel and pins
    the default to it (opting IN to a cohort must stay a deliberate choice); otherwise the
    latest cohort is pre-selected."""
    options = ([_FACULTY_ONLY] + cohort_orgs) if optional else cohort_orgs
    return _choice_input(
        "cohort_org", "Cohort org", options, default=_FACULTY_ONLY if optional else None
    )


def render_sync_gradebooks(cohort_orgs: list[str]) -> str:
    """Provision one private grades-<handle> repo per onboarded student (idempotent)."""
    return f"""name: Sync gradebooks

# Ensures every onboarded student has a PRIVATE grades-<handle> repo (student = read) -
# the single home for all their grades. Idempotent; safe to re-run after new enrolments.

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}
      dry_run:
        description: "Preview only - list the gradebook repos that WOULD be created"
        type: boolean
        default: false

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  sync-gradebooks:
{_run_preamble()}      - name: Sync gradebooks
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          args=(--cohort-org "$COHORT_ORG")
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.grades sync "${{args[@]}}"
"""


def render_render_grades(cohort_orgs: list[str]) -> str:
    """Build per-student gradebook YAML from the grade CSVs and open the preview PR."""
    return f"""name: Render grades (preview)

# Reads classroom-config/grades/<assignment>.csv, builds one gradebook/<handle>.yml per
# student, and opens ONE pull request in classroom-config. THAT PR IS THE PREVIEW: review
# every student's grades in the diff, then merge to distribute (Distribute grades).

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  render-grades:
{_run_preamble()}      - name: Render grades
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.grades render --cohort-org "$COHORT_ORG"
"""


def render_distribute_grades(cohort_orgs: list[str]) -> str:
    """Fan the merged gradebook/<handle>.yml files out into each private grades-<handle>."""
    return f"""name: Distribute grades

# Run AFTER merging the Render grades preview PR. Copies each merged gradebook/<handle>.yml
# into that student's private grades-<handle> repo and (unless silenced) emails them a
# notification to their university inbox. Needs the GRAPH_* (or SMTP_*) secrets for the email.

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}
      dry_run:
        description: "Preview the grade emails - push nothing, send nothing"
        type: boolean
        default: true
      silent:
        description: "Skip the email notification (just push the grades)"
        type: boolean
        default: false

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  distribute-grades:
{_run_preamble()}      - name: Distribute grades
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
          SILENT: ${{{{ inputs.silent }}}}
{_MAIL_ENV}
        run: |
          args=(--cohort-org "$COHORT_ORG")
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          [ "$SILENT" = "true" ] && args+=(--no-notify)
          python3 -m dsl_course.grades distribute "${{args[@]}}"
"""


def render_send_codes(cohort_orgs: list[str]) -> str:
    """Generate a non-PII enrolment code per student and email each their code over SMTP."""
    return f"""name: Send enrolment codes

# Generates a random enrolment code per student (into classroom-config/students.csv) and
# emails each not-yet-onboarded student their code to their university inbox. Students paste
# the code into the welcome Join issue - no personal data in the public repo. dry_run
# previews the codes + emails without writing or sending. Needs the GRAPH_* (or SMTP_*) secrets.

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}
      dry_run:
        description: "Preview the codes + emails - write nothing, send nothing"
        type: boolean
        default: true

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  send-codes:
{_run_preamble()}      - name: Send enrolment codes
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
{_MAIL_ENV}
        run: |
          args=(--cohort-org "$COHORT_ORG")
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.enrol_codes "${{args[@]}}"
"""


def render_bootstrap_cohort() -> str:
    """Configure a (pre-created, empty) cohort org from the course org: welcome +
    classroom-config + tightened perms, register it, and refresh the dropdowns."""
    return f"""name: Bootstrap cohort

# You create the empty cohort org in the web UI first (GitHub has no org-creation API)
# and add the bot as an owner. Then run this with that org's name.

on:
  workflow_dispatch:
    inputs:
      cohort_org:
        description: "Empty cohort org you've already created (bot must be an owner)"
        required: true

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  bootstrap-cohort:
{_run_preamble(_TIMEOUT_BOOTSTRAP)}      - name: Bootstrap + register + refresh
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          DSL_BOT_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT: ${{{{ inputs.cohort_org }}}}
        run: |
          python3 -m dsl_course.bootstrap_course --org "$COHORT" --org-name "$COHORT" \\
            --cohort --course "$COURSE" --propagate-secret
          python3 -m dsl_course.seed refresh --course-org "$COURSE"
"""


def render_scheduler() -> str:
    """Hourly cron that snapshots + autogrades each passed grading deadline and releases
    whatever each cohort's schedule says is now due, across every registered cohort. No
    check-team gate: scheduled runs have no actor, and every action is either idempotent or
    fire-once (manual dispatch still needs write)."""
    return f"""name: Scheduled release

# Reads each cohort's classroom-config/schedule.yml and, every hour: freezes the submission
# snapshot for each assignment whose grading deadline has passed, autogrades that assignment
# ONCE (marker: classroom-config/autograde/<slug>/ - delete it to re-grade), then fires every
# `releases:` release whose `when` datetime has arrived. Releases are idempotent, so
# re-releasing on the next hour is a no-op; grading is not re-run. On the cron it releases for
# real; manual runs default to dry-run.
# Hourly so a `when` time-of-day is honoured to the hour (GitHub cron is UTC and best-effort).

on:
  schedule:
    - cron: "0 * * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Preview only - list what WOULD open, release nothing"
        type: boolean
        default: true

{_SCHEDULED_RELEASE_CONCURRENCY}
{_PERMISSIONS_JOBS}  scheduled-release:
{_ungated_preamble(_TIMEOUT_GRADING)}      - name: Run scheduler
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          gh auth setup-git
          args=(--course-org "$COURSE" --all-cohorts)
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.scheduler "${{args[@]}}"
{_CRON_NOTICE}"""


def render_status(cohort_orgs: list[str]) -> str:
    """Per-cohort checklist of every faculty & instructors input location - identity, people,
    schedule + release plan, roster, teams, grades - with the current value and a
    direct edit link for anything missing. Read-only; changes nothing."""
    return f"""name: Check cohort setup

# A per-cohort glance view of everything configured (and everything still missing),
# with direct links to fix it. Read-only - this workflow changes nothing.

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  status:
{_run_preamble()}      - name: Check cohort setup
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
        run: |
          python3 -m dsl_course.status --course-org "$COURSE" --cohort-org "$COHORT_ORG" >> "$GITHUB_STEP_SUMMARY"
"""


def render_refresh() -> str:
    """Repopulate dropdowns, re-seed content actions, propagate the repo secret, and
    rebuild the profile README across the course org - on demand, and nightly.

    No check-team gate: the cron has no actor to check, and manual dispatch already needs
    write on this repo, which is the same thing check-team verifies."""
    return f"""name: Refresh actions

# Every seeded workflow is frozen at the moment it was seeded, while the engine it calls is
# always checked out from central main - so an org left alone drifts, until a stale workflow
# calls engine code that has since moved. This re-seeds the org daily, so every org
# converges on central within 24h with nobody pressing anything. The refresh is idempotent
# and skips files whose content is unchanged, so a night with no central changes is silent.

on:
  schedule:
    - cron: "27 5 * * *"
  workflow_dispatch: {{}}

{_SEED_REFRESH_CONCURRENCY}
{_PERMISSIONS_JOBS}  refresh:
{_ungated_preamble()}      - name: Refresh
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          DSL_BOT_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
        run: |
          python3 -m dsl_course.seed refresh --course-org "$COURSE"
{_CRON_NOTICE}"""


def render_generate_syllabus(source_repos: list[str], cohort_orgs: list[str]) -> str:
    """Build the syllabus's session-by-session section from a cohort's schedule.yml.

    A workflow rather than a CLI habit, because the people who write syllabi are the people
    who use the Actions tab. It writes a companion file for them to paste from and never
    touches SYLLABUS.md itself - see dsl_course/syllabus.py for why."""
    return f"""name: Generate syllabus

# Writes the "Course sessions and readings" section of a syllabus - one block per session,
# with its title, its learning objectives and its reading list - from the cohort's
# classroom-config/schedule.yml and this repo's readings/ folders.
#
# It lands in SYLLABUS.sessions.md beside your syllabus, and is NEVER released to students.
# Paste what you want into SYLLABUS.md; a re-run overwrites the companion file, never your
# syllabus. Dropdowns are refreshed by the 'Refresh actions' workflow.

on:
  workflow_dispatch:
    inputs:
{_choice_input("course_source_repo", "Repo holding your syllabus and readings", source_repos)}
{_choice_input("cohort_org", "Cohort whose schedule.yml supplies the sessions", cohort_orgs)}
      write:
        description: "Commit the block to SYLLABUS.sessions.md (off = just print it)"
        type: boolean
        default: true

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  syllabus:
{_run_preamble()}      - name: Generate
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          SOURCE_REPO: ${{{{ inputs.course_source_repo }}}}
          WRITE: ${{{{ inputs.write }}}}
        run: |
          args=(--course-org "$COURSE" --cohort-org "$COHORT_ORG" --course-source-repo "$SOURCE_REPO")
          [ "$WRITE" = "true" ] && args+=(--write)
          python3 -m dsl_course.syllabus "${{args[@]}}"
"""


def render_new_materials() -> str:
    """Scaffold a correctly-structured course-materials-<tag> repo, then refresh."""
    return f"""name: New materials repo

on:
  workflow_dispatch:
    inputs:
      tag:
        description: "Year tag, e.g. f2026 or s2026 - creates course-materials-<tag>"
        required: true

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  scaffold:
{_run_preamble()}      - name: Scaffold materials
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          DSL_BOT_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          ORG: ${{{{ github.repository_owner }}}}
          TAG: ${{{{ inputs.tag }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.scaffold materials --org "$ORG" --tag "$TAG"
          python3 -m dsl_course.seed refresh --course-org "$ORG"
"""


def render_new_assignment() -> str:
    """Scaffold an assignment-N-<tag> template repo (main + solution branch), then refresh.

    format/type land in the solution branch's grading.yml (and shape the starter/hidden
    tests), so the choice made on this workflow is the one the grader later obeys - the
    grading.yml vocabulary is picked here, not hand-edited in afterwards."""
    return f"""name: New assignment

on:
  workflow_dispatch:
    inputs:
      number:
        description: "Assignment number (e.g. 1)"
        required: true
      tag:
        description: "Year tag, e.g. f2026 or s2026 - creates assignment-<number>-<tag>"
        required: true
      format:
        description: "Starter format - a .py script or a Jupyter notebook"
        required: true
        type: choice
        default: py
        options:
          - py
          - notebook
      type:
        description: "individual = one repo per student; group = one repo per team (teams.csv)"
        required: true
        type: choice
        default: individual
        options:
          - individual
          - group

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  scaffold:
{_run_preamble()}      - name: Scaffold assignment
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          DSL_BOT_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          ORG: ${{{{ github.repository_owner }}}}
          NUMBER: ${{{{ inputs.number }}}}
          TAG: ${{{{ inputs.tag }}}}
          FORMAT: ${{{{ inputs.format }}}}
          TYPE: ${{{{ inputs.type }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.scaffold assignment --org "$ORG" --number "$NUMBER" \\
            --tag "$TAG" --format "$FORMAT" --type "$TYPE"
          python3 -m dsl_course.seed refresh --course-org "$ORG"
"""


def render_sync_site(cohort_orgs: list[str]) -> str:
    """Regenerate a cohort's website from the live org structure (released sessions +
    schedule.yml dates + assignment catalog). Auto-resyncs on any change the site sources
    from - not just on release:

    - push to this .github repo's dsl-course.yml -> re-sync EVERY cohort (the course name /
      instructor cards feed every cohort site).
    - repository_dispatch `sync-site` (fired by the cohort's classroom-config dispatcher on
      push to schedule.yml/people.yml) -> re-sync that one cohort (or all, if the payload
      names none).
    - daily cron -> re-sync every cohort (the catch-all: a direct edit to a released
      content repo can't fire a dispatch, because DSL_BOT_TOKEN is deliberately not
      scoped to content repos, so a daily pass reflects such edits within a day).
    - workflow_dispatch -> manual escape hatch, gated by check-team (single cohort).

    Releases also call site.sync_site directly (immediate). The push/dispatch/cron paths
    skip the check-team gate (no actor), same as Sync membership and the scheduler."""
    return f"""name: Sync site

on:
  push:
    branches: [main]
    paths:
      - dsl-course.yml
  repository_dispatch:
    types: [sync-site]
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:
    inputs:
{_choice_input("cohort_org", "Cohort whose site to regenerate from the org structure", cohort_orgs)}

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  sync:
{_run_preamble()}      - name: Sync site
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.site sync --course-org "$COURSE" --cohort-org "$COHORT_ORG"
{_CRON_CLOSE}
  sync-auto:
    if: github.event_name != 'workflow_dispatch'
{_ungated_preamble()}      - name: Sync site
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          EVENT: ${{{{ github.event_name }}}}
          DISPATCH_COHORT: ${{{{ github.event.client_payload.cohort_org }}}}
        run: |
          gh auth setup-git
          args=(--course-org "$COURSE")
          case "$EVENT" in
            push|schedule) args+=(--all-cohorts) ;;
            repository_dispatch)
              if [ -n "$DISPATCH_COHORT" ]; then
                args+=(--cohort-org "$DISPATCH_COHORT")
              else
                args+=(--all-cohorts)
              fi ;;
          esac
          python3 -m dsl_course.site sync "${{args[@]}}"
{_CRON_NOTICE}"""


def render_publish_site(source_repos: list[str]) -> str:
    """Build/refresh the PUBLIC course site <course-org>.github.io (open courseware).

    Opt-in: the first (manual) run scaffolds the site and persists its settings into the
    site repo; a daily cron then re-syncs from those settings, so a materials edit reaches
    the public site without another click. Hosts the chosen materials repo's lecture files
    in the public site (the source repos are private, so links would 404); readings are a
    text-only list or hosted files. The cron is a no-op for the (many) course orgs that
    never publish. Separate from the per-cohort student-gated sites; releases never touch
    it."""
    return f"""name: Publish course website

# Build/refresh the PUBLIC course site <course-org>.github.io (open courseware). The
# course materials repos are private, so this HOSTS the chosen repo's lecture files in
# the site (links would otherwise 404). Readings: 'reading-list' shows citations as text
# only; 'actual-readings' also hosts + links the files (you carry the copyright
# responsibility); 'none' skips them. Opt-in - the first manual run scaffolds the site and
# persists its settings into it; the daily cron then re-syncs from those settings (and does
# nothing at all for a course org that never published a site).

on:
  schedule:
    - cron: "30 5 * * *"
  workflow_dispatch:
    inputs:
{_choice_input("source_repo", "Source materials repo (in this course org) to publish", source_repos)}
      readings_mode:
        description: "Readings: reading-list (citations) / actual-readings (files) / none"
        required: true
        type: choice
        default: reading-list
        options:
          - reading-list
          - actual-readings
          - none
      include_lectures:
        description: "Publish lecture files (the point of the site)"
        type: boolean
        default: true

{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  publish:
{_run_preamble()}      - name: Publish course website
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE_ORG: ${{{{ github.repository_owner }}}}
          SOURCE_REPO: ${{{{ inputs.source_repo }}}}
          READINGS_MODE: ${{{{ inputs.readings_mode }}}}
          INC_LEC: ${{{{ inputs.include_lectures }}}}
        run: |
          gh auth setup-git
          args=(--course-org "$COURSE_ORG" --source-repo "$SOURCE_REPO" --readings-mode "$READINGS_MODE")
          [ "$INC_LEC" = "false" ] && args+=(--no-include-lectures)
          python3 -m dsl_course.site public-sync "${{args[@]}}"
{_CRON_CLOSE}
  resync:
    # The daily catch-up: no inputs, so public-sync re-runs the settings the last manual
    # publish persisted in the site repo. No site / no persisted settings -> quiet no-op.
    # Cron has no actor, so this path skips the check-team gate (as Sync site does).
    if: github.event_name == 'schedule'
{_ungated_preamble()}      - name: Re-sync course website
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE_ORG: ${{{{ github.repository_owner }}}}
        run: |
          gh auth setup-git
          python3 -m dsl_course.site public-sync --course-org "$COURSE_ORG"
{_CRON_NOTICE}"""
