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

from . import mailer
from .central import CENTRAL, CENTRAL_REF_PLACEHOLDER, pin_central_ref
from .course import MATERIALS_REPO_PREFIX, term_tag

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


def for_placement(rendered: str, central_ref: str) -> str:
    """Make a rendered workflow ready to write into an org: stamp the ownership banner,
    and pin the central ref THIS org runs the engine from.

    Both happen at the write site rather than inside each renderer, for the same reason: a
    new renderer cannot ship without either, and `central_ref` is a required argument, so
    a caller that places a workflow cannot forget to say which ref it is placing it at.
    `discovery.central_ref_for` is where that ref comes from; `central.pin_central_ref`
    refuses a ref the central repo does not have."""
    return SYSTEM_OWNED_BANNER + pin_central_ref(rendered, central_ref)


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

# The scheduler's concurrency sits on its JOBS, never on the workflow: a workflow-level
# group is one queue for every action in every cohort, which is how a two-hour grading pass
# held up a release that was due meanwhile. Each job declares its own instead.
#
# Releases are fire-once, guarded by markers written as they complete, so two concurrent
# passes can double-release whatever the first has not yet marked - hence still a queue of
# one. A manual DRY-RUN writes nothing and therefore needs no serialisation, and joining the
# queue is exactly how an operator's preview gets silently dropped (Actions holds ONE
# pending run per group, so a third arrival cancels the second whatever
# `cancel-in-progress` says): it gets a group of its own, per run.
_RELEASE_CONCURRENCY = """    concurrency:
      group: ${{ inputs.dry_run == true && github.run_id || 'scheduled-release' }}
      cancel-in-progress: false
"""

# Grading is queued PER COHORT: the fire-once autograde marker is a cohort-side file, so two
# passes over the same cohort can double-grade, while a pass over another cohort shares
# nothing with it and must never wait. A dry-run leg is per run AND per cohort, for the same
# reason the release job's is: it writes nothing, so it needs no queue, and a queue is what
# would silently drop an operator's preview.
_AUTOGRADE_CONCURRENCY = """    concurrency:
      group: ${{ inputs.dry_run == true && format('{0}-{1}', github.run_id, matrix.cohort) || format('scheduled-autograde-{0}', matrix.cohort) }}
      cancel-in-progress: false
"""


def _concurrency(name: str) -> str:
    """One run of this workflow at a time, per repo - the group every WRITER declares.

    These workflows converge shared state: the Contents API (grades, gradebooks, the
    roster), a force-pushed render branch, an org's team membership, a site repo. Two
    overlapping runs race each other into sha conflicts and half-written state, and the
    triggers that fire them are exactly the ones that bunch up (a push, a
    repository_dispatch per cohort edit, a daily cron, an operator's click).

    `cancel-in-progress: false`, so a run already doing work is never killed part-way.
    Actions has no `queue:` - a group holds one pending run, and a third arrival drops the
    second - but every one of these is an idempotent CONVERGE, so the run that does
    survive reaches the same end state as the one it replaced. That is not true of
    `seed-refresh`, whose group is deliberately not shared with the buttons that end in a
    refresh (see _SEED_REFRESH_CONCURRENCY), nor of `scheduled-release`, whose fire-once
    actions the manual grader must therefore never join.

    The group is repo-scoped by name as well as by GitHub's own per-repository scoping, so
    it reads unambiguously in a log line that carries no repo."""
    return (
        "concurrency:\n"
        f"  group: ${{{{ github.repository }}}}-{name}\n"
        "  cancel-in-progress: false\n"
    )


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
# 60 covers the two jobs that write across MANY repos in series: Bootstrap cohort, which
#     creates and configures a whole org and then converges it, and the scheduler's release
#     job, whose `assignment` handout provisions one repo per student for every cohort at
#     once - so two cohorts handing out on the same tick outlast the ordinary budget.
# 120 covers the two jobs that grade: collect budgets 300s PER submission subprocess and
#     walks a cohort serially - the manual Grade assignment button, and the scheduler's
#     autograde job, which is one matrix leg per cohort.
_TIMEOUT_DEFAULT = 30
_TIMEOUT_MANY_REPOS = 60
_TIMEOUT_GRADING = 120


# Cron minutes, chosen ONCE here because two rules govern every schedule below and neither
# is visible from a single cron line.
#
# 1. Never minute 0, 15, 30 or 45. GitHub delivers `schedule` events best-effort and drops
#    the most contended minutes first - those four are where everyone puts their crons. On
#    `0 * * * *` the seeded scheduler was delivered 6 of 24 ticks a day, identically across
#    all four course orgs, so a release pinned to a class time landed hours late. Odd
#    off-peak minutes cost nothing and are the single biggest win available here.
# 2. The daily jobs form a CHAIN and must not share a minute. Refresh actions converges
#    workflows and secrets first; Sync membership mirrors the teams that Sync site then
#    reads for gating. Sync membership and Sync site were both `0 6 * * *` in one repo
#    under one token, i.e. racing, which is how a site could sync against teams that had
#    not been written yet.
#
#   05:27  Refresh actions          converge workflows + secrets
#   05:58  Publish course website   public open-courseware site
#   06:13  Sync membership          teams from people.yml
#   06:41  Sync site                cohort sites (reads those teams)
#
# Spacing is nominal only - a late delivery can still overlap - so it is the per-workflow
# `concurrency` groups, not these minutes, that keep a workflow from overlapping ITSELF.
# The minutes buy ordering in the common case and keep the token's budget unbunched.


# The head of any job that runs toolkit code: a runner, the central repo checked out at
# the org's central ref (left as a placeholder here, pinned by for_placement), Python, and
# the deps. Used bare by the UNGATED jobs (cron /
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
          ref: {CENTRAL_REF_PLACEHOLDER}
      - uses: {_SETUP_PYTHON}
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
"""


def _run_preamble(minutes: int = _TIMEOUT_DEFAULT) -> str:
    return f"    needs: check-team\n{_ungated_preamble(minutes)}"


# Mail secrets, wired into the env of the workflows that send email (enrolment codes and
# grade notifications) - and of Check cohort setup, which does not send but REPORTS
# whether a send could: its mail-transport row reads the very same variables, and without
# them it would report "unset" on every org whatever the truth.
# A plain string (not the f-string body) so the GitHub `${{ }}` is literal.
# Derived from the names the mailer actually reads, so a rename cannot leave an org
# silently unconfigured. GRAPH_CLIENT_CERT holds certificate + private key in one
# multi-line secret; there is no GRAPH_CLIENT_SECRET.
_MAIL_ENV = "\n".join(
    f"          {name}: ${{{{ secrets.{name} }}}}" for name in mailer.GRAPH_ENV
)

# Fail CLOSED: only an explicit `false` sends. Any other value - "True", "1", a blank from
# a renamed input - previews. Distribute grades is the one button left that emails a whole
# cohort; the other dry-run gates in this module are not send paths and keep the simpler
# spelling.
_DRY_RUN_GATE = (
    '          if [ "$DRY_RUN" = "false" ]; '
    "then args+=(--no-dry-run); else args+=(--dry-run); fi"
)


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
#
# A workflow whose unattended jobs run CONCURRENTLY has to go further, and `scope` is how:
# the scheduler releases and grades in two jobs (grading once per cohort), which fail
# independently, and on a shared title the green one closes the red one's issue - then the
# next tick files it again, cc-ing course-admin four times an hour about the one fault. A
# scoped job appends its own suffix and so keeps its own issue. The default, no suffix, is
# the title every org already has open, so those still self-close.
#
# Which is why both lookups below match the title EXACTLY, client-side. `--search` is a
# WORD match: "<workflow> is failing" also hits "<workflow> (autograde <cohort>) is
# failing", so the release job's close loop would close every grading leg's open issue on
# every green tick, the failing leg would refile with a fresh cc, and the 6h throttle would
# never engage. The search stays as the cheap server-side narrowing; `select(.title == ..)`
# is the guard. `source_digest._open_issue` matches client-side for the same reason.
_SCOPE = "__CRON_ISSUE_SCOPE__"  # replaced per job; see _fill_scope
_SCOPE_ENV = "__CRON_SCOPE_ENV__"  # any env the scope's shell fragment reads

_CRON_CLOSE_TEMPLATE = (
    """      - name: Close the failure issue once a run succeeds
        if: success()
        env:
          GH_TOKEN: ${{ secrets.DSL_BOT_TOKEN }}
          WORKFLOW: ${{ github.workflow }}
          REPO: ${{ github.repository }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
"""
    + _SCOPE_ENV
    + """        run: |
          title="$WORKFLOW"""
    + _SCOPE
    + """ is failing"
          for n in $(gh issue list --repo "$REPO" --state open --search "$title in:title" --json number,title --jq ".[] | select(.title == \\"$title\\") | .number"); do
            gh issue close "$n" --repo "$REPO" --comment "Recovered: $RUN_URL"
          done
"""
)

# `cancelled()`, not just `failure()`: a job killed by its own `timeout-minutes` is
# CANCELLED, and a cron that reliably runs out of time is exactly the silent failure this
# exists to surface.
_CRON_NOTICE_TEMPLATE = (
    """      - name: Report an unattended failure as an issue
        if: (failure() || cancelled()) && github.event_name != 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ secrets.DSL_BOT_TOKEN }}
          WORKFLOW: ${{ github.workflow }}
          REPO: ${{ github.repository }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
"""
    + _SCOPE_ENV
    + """        run: |
          title="$WORKFLOW"""
    + _SCOPE
    + """ is failing"
          note=$(printf 'The unattended run failed or was cancelled: %s\\n\\nNothing retries it before the next scheduled run. This issue closes itself once a run succeeds.\\n' "$RUN_URL")
          # A filed issue emails only the repo's watchers, which in practice is nobody, so
          # the FIRST report mentions the org's admins; course-admin, not instructors,
          # because broken infrastructure is not the teaching staff's problem.
          body=$(printf '%s\\ncc @%s/course-admin\\n' "$note" "${REPO%%/*}")
          # The step runs under `bash -e`, so an unguarded capture would abort the step on a
          # transient search failure - before the `gh issue create` that is the whole point.
          # No dedupe hit just means we file a fresh issue.
          existing=$(gh issue list --repo "$REPO" --state open --search "$title in:title" --json number,title,updatedAt --jq "map(select(.title == \\"$title\\"))[0] | select(.) | \\"\\(.number) \\(.updatedAt)\\"") || true
          if [ -z "$existing" ]; then
            gh issue create --repo "$REPO" --title "$title" --body "$body"
            exit 0
          fi
          # Already open. The scheduler fails on EVERY tick while a fault stands, and a
          # comment per run buried the thread and mentioned course-admin dozens of times a
          # day about one fault. So comment only once the thread has been quiet for six hours,
          # and without the cc - whoever it reached the first time is already subscribed.
          # An unparseable timestamp reads as epoch, i.e. "long overdue": the point of the
          # issue is that somebody hears about the failure.
          last=$(date -u -d "${existing#* }" +%s 2>/dev/null || echo 0)
          if [ $(( $(date -u +%s) - last )) -lt 21600 ]; then
            echo "already reported within the last 6h - see issue ${existing%% *}"
            exit 0
          fi
          gh issue comment "${existing%% *}" --repo "$REPO" --body "$note"
"""
    + _CRON_CLOSE_TEMPLATE
)


def _fill_scope(template: str, scope: str = "", scope_env: str = "") -> str:
    """Bind one job's issue scope into a reporting template.

    `scope` is a SHELL fragment, so it may name an env var (`$COHORT`) and `scope_env` is
    where that var comes from - a value must never reach a run block as a `${{ }}`
    expression, which GitHub substitutes before the shell parses the line."""
    return template.replace(_SCOPE, f" ({scope})" if scope else "").replace(
        _SCOPE_ENV, scope_env
    )


# The unscoped pair, for the workflows with a single unattended job.
_CRON_NOTICE = _fill_scope(_CRON_NOTICE_TEMPLATE)
_CRON_CLOSE = _fill_scope(_CRON_CLOSE_TEMPLATE)

# The scheduler's grading job reports PER COHORT: its matrix legs run in parallel, so on a
# shared title a green cohort would close a red cohort's open issue. A cohort org name is
# not per-person data, so it may be said out loud in a public repo's issue title.
_AUTOGRADE_NOTICE = _fill_scope(
    _CRON_NOTICE_TEMPLATE,
    "autograde $COHORT",
    "          COHORT: ${{ matrix.cohort }}\n",
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


def _newest_materials(options: list[str]) -> str | None:
    """The `course-materials-*` option carrying the latest term tag, or None when the
    dropdown holds none. Spring precedes autumn within a year, so the tag is ordered as
    (year, autumn?) rather than alphabetically."""
    dated = [
        (tag[1:], tag[0] == "f", o)
        for o in options
        if o.startswith(MATERIALS_REPO_PREFIX) and (tag := term_tag(o))
    ]
    return max(dated)[2] if dated else None


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
# cohort_dest_path ships EMPTY rather than pre-filled - a `default:` on a free-text box is
# submitted verbatim, so pre-filling a PATH reads as a value the faculty member chose.
# cohort_dest_repo is the opposite case and carries `materials`, the same default an omitted
# `cohort_dest_repo:` takes in the schedule: `materials` is not a guess at intent, it is the
# answer the system supplies either way, so showing it teaches the default instead of hiding
# it. This box used to be required-and-blank on the theory that naming the destination was
# worth forcing - but a mandatory free-text field IS the typo surface that theory feared, and
# it made the button contradict the schedule for no gain. A cleared box is still safe:
# deploy.main resolves `cohort_dest_repo.strip() or "materials"`.
_COURSE_SOURCE_REPO_DESC = "1. repo to release from in the course org"

_COURSE_SOURCE_PATH_INPUT = """\
      course_source_path:
        description: "2. within-repo folder/file path to copy from (or comma-separated list)"
        required: true"""

_COHORT_DEST_INPUTS = """\
      cohort_dest_repo:
        description: "4. repo to release to in the cohort org; created if missing"
        default: "materials"
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

{_concurrency("release-materials")}
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
        description: "individual (one repo per student) or group (one per team from teams.csv). auto = whatever schedule.yml / the template's grading_config.yml declare (default: individual)"
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

{_concurrency("release-assignment")}
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


def render_collect_submissions(
    cohort_orgs: list[str], assignments: list[str] | None = None
) -> str:
    """Refresh one assignment's grading sheet on demand, between cron ticks."""
    return f"""name: Collect submissions

# Brings an assignment's grading sheet up to date NOW instead of waiting for the next
# quarter-hour tick: it re-reads each submission repo's latest commit, refills the `info:`
# block (submitted, days late, contributions) and posts any submission receipt still owed.
#
# It never freezes anything. The pin is frozen once, by the cron, at the assignment's
# grading deadline - so a grader can press this as often as they like without moving what
# anyone is marked on. Nothing is written to a student repo except that receipt.

on:
  workflow_dispatch:
    inputs:
{_choice_input("cohort_org", "Cohort org (submissions)", cohort_orgs)}
{_assignment_input(assignments or [])}
      dry_run:
        description: "Preview only - show what WOULD be refreshed"
        type: boolean
        default: false

{_concurrency("collect-submissions")}
{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  collect-submissions:
{_run_preamble(_TIMEOUT_GRADING)}      - name: Collect submissions
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          MASTER_ORG: ${{{{ github.repository_owner }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          COURSE_SOURCE_REPO: ${{{{ inputs.course_source_repo }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          args=(--master-org "$MASTER_ORG" --course-source-repo "$COURSE_SOURCE_REPO" --cohort-org "$COHORT_ORG" --refresh-only)
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
    - cron: "13 6 * * *"
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs, optional=True)}

{_concurrency("sync-membership")}
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


def render_distribute_grades(cohort_orgs: list[str]) -> str:
    """Send every mark a grader has written where it has to go."""
    return f"""name: Distribute grades

# Reads the grading sheets in classroom-config and sends what they hold: a feedback comment
# on each submission repo's Feedback issue, each student's private grades-<handle> repo, the
# registrar export, and (unless silenced) an email saying there is something new to read.
# Nothing is said twice - a re-run after one correction reaches one student.
# `assignment` narrows the run to one slug; blank is every sheet in the cohort, which is
# right at the end of term and wrong in the middle of one.
# Dry run first; it writes nothing and prints the counts. Needs the GRAPH_* secrets to mail.

on:
  workflow_dispatch:
    inputs:
{_cohort_dropdown(cohort_orgs)}
      assignment:
        description: "One assignment slug - leave blank for every sheet in the cohort"
        type: string
        required: false
      dry_run:
        description: "Preview the grade emails - push nothing, send nothing"
        type: boolean
        default: true
      silent:
        description: "Skip the email notification (just push the grades)"
        type: boolean
        default: false

{_concurrency("distribute-grades")}
{_PERMISSIONS_JOBS}{_CHECK_TEAM}
  distribute-grades:
{_run_preamble(_TIMEOUT_GRADING)}      - name: Distribute grades
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COHORT_ORG: ${{{{ inputs.cohort_org }}}}
          ASSIGNMENT: ${{{{ inputs.assignment }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
          SILENT: ${{{{ inputs.silent }}}}
{_MAIL_ENV}
        run: |
          args=(--cohort-org "$COHORT_ORG")
          [ -n "$ASSIGNMENT" ] && args+=(--assignment "$ASSIGNMENT")
{_DRY_RUN_GATE}
          [ "$SILENT" = "true" ] && args+=(--no-notify)
          python3 -m dsl_course.grades distribute "${{args[@]}}"
"""


# Which cohort a Send-codes run is FOR: the roster dispatcher's payload, its only
# trigger. Used to scope the concurrency group PER COHORT - the state two runs race each
# other over is one cohort's students.csv, and a repo-wide group would have a roster push
# in one cohort drop a queued send in another (Actions holds one pending run per group and
# a third arrival cancels the second).
_SEND_CODES_COHORT = "${{ github.event.client_payload.cohort_org }}"


def render_send_codes() -> str:
    """Generate a non-PII enrolment code per student and email each their code.

    One way in, and it is not a person: a push to a cohort's students.csv, which its
    classroom-config dispatcher turns into a `send-codes` repository_dispatch. So the job
    is UNGATED - a dispatch has no actor to check - and it sends for real, because the
    whole point is that a roster edit reaches the new students' inboxes without a click.
    Same routing as Sync membership's automatic path.

    It carries `--dispatched-by`, which refuses a cohort this course org does not own: a
    `client_payload` is written by whoever holds a cohort's bot token, a lower trust tier
    than the course org (see enrol_codes.refuse_unregistered).
    """
    return f"""name: Send enrolment codes

# Generates a random enrolment code per student (into classroom-config/students.csv) and
# emails each not-yet-onboarded student their code to their hertie email address. Students
# paste the code into the welcome Join course issue - no personal data in the public repo.
# Needs the GRAPH_* secrets.
#
# There is no button: a push to a cohort's students.csv is what fires this (its
# classroom-config dispatch-send-codes.yml dispatches `send-codes`), so the roster is the
# only thing anyone edits. Re-running is safe - a row is mailed only while its
# `code_sent_at` is blank - so a re-send is a fresh push to the roster.

on:
  repository_dispatch:
    types: [send-codes]

{_concurrency("send-codes-" + _SEND_CODES_COHORT)}
{_PERMISSIONS_JOBS}  send-codes:
{_ungated_preamble()}      - name: Send enrolment codes for the cohort that pushed its roster
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          DISPATCH_COHORT: ${{{{ github.event.client_payload.cohort_org }}}}
{_MAIL_ENV}
        run: |
          # A payload with no cohort names nothing to send for. Fail loudly rather than
          # let an empty --cohort-org reach the CLI and be refused for the wrong reason.
          if [ -z "$DISPATCH_COHORT" ]; then
            echo "::error::the send-codes dispatch carried no client_payload.cohort_org - nothing to send."
            exit 1
          fi
          # --dispatched-by names the course org whose registry authorises this cohort:
          # the payload comes from a cohort's bot token, so the cohort it names is
          # untrusted input.
          python3 -m dsl_course.enrol_codes --cohort-org "$DISPATCH_COHORT" \\
            --dispatched-by "$COURSE"
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
{_run_preamble(_TIMEOUT_MANY_REPOS)}      - name: Bootstrap + register + refresh
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
    """Quarter-hourly cron - and an external dispatch - that releases whatever each cohort's
    schedule says is now due and grades each passed deadline, across every registered cohort.
    Two jobs, so neither waits on the other. No check-team gate: an unattended run has no
    actor, and every action is either idempotent or fire-once (manual dispatch still needs
    write)."""
    return f"""name: Scheduled release

# Reads each cohort's classroom-config/schedule.yml and, on every tick: freezes the submission
# snapshot for each assignment whose grading deadline has passed, fires every `releases:`
# release whose `when` datetime has arrived, and autogrades each frozen assignment ONCE
# (marker: classroom-config/autograde/<slug>/ - delete it to re-grade). Releases are
# idempotent, so re-releasing on the next tick is a no-op; grading is not re-run. Unattended
# it releases for real; manual runs default to dry-run.
#
# TWO DRIVERS, because GitHub delivers `schedule` best-effort and in practice delivers very
# little of it: measured 2-7% of the fires below, with gaps up to 13h. So this is also fired
# by `repository_dispatch` from an external dispatcher (every 15 minutes, off-box), and each
# driver covers the other's outage. Both arrive here as an ordinary run; nothing downstream
# cares which, because every action is dated and fire-once. The off-peak minutes stay as they
# are - see the cron-minute rules above for why `0 * * * *` was delivered 6 times a day, not
# 24 - and an idle tick is ~30s of reads, so the cost of arriving twice is negligible.
#
# TWO JOBS, because a grading pass can run for two hours and must not hold up a release due
# meanwhile. `release` walks every cohort (fast: dated copies and repo provisioning);
# `autograde` is one matrix leg per cohort, each queued only against itself, and runs even
# when the release job failed - one cohort's fault is nobody else's wait.

on:
  schedule:
    - cron: "7,22,37,52 * * * *"
  repository_dispatch:
    types: [scheduled-release]
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Preview only - list what WOULD open, release nothing"
        type: boolean
        default: true

{_PERMISSIONS_JOBS}  release:
{_RELEASE_CONCURRENCY}    outputs:
      cohorts: ${{{{ steps.cohorts.outputs.cohorts }}}}
{_ungated_preamble(_TIMEOUT_MANY_REPOS)}      - name: List the cohorts to grade
        id: cohorts
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
        run: |
          # FIRST, so the grading matrix is populated whatever the release pass does: a step
          # that fails skips the ones after it, and a release fault in one cohort must not
          # cancel grading in the others. Assigned, not echoed inline: under `bash -e` a
          # failed substitution inside an echo would write an empty output on a GREEN step,
          # and grading would then be skipped for the whole course, silently.
          cohorts=$(python3 -m dsl_course.scheduler --course-org "$COURSE" --list-cohorts)
          echo "cohorts=$cohorts" >> "$GITHUB_OUTPUT"
      - name: Release what is due, in every cohort
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
          EVENT: ${{{{ github.event_name }}}}
          DRIVER: ${{{{ github.event.client_payload.driver }}}}
        run: |
          gh auth setup-git
          # Which driver delivered this tick. The run history is the only record of that,
          # and it is what says whether both drivers are alive (a dispatch names its
          # sender in client_payload.driver; the cron has nobody to name).
          echo "delivered by event=$EVENT driver=${{DRIVER:-none}}"
          args=(--course-org "$COURSE" --all-cohorts --skip-autograde)
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.scheduler "${{args[@]}}"
{_CRON_NOTICE}  autograde:
    needs: [release]
    # always(), because grading is gated on the durable snapshot marker, not on this run's
    # release pass: a red release must not silently skip a cohort's grading. The output test
    # is the empty-matrix guard - GitHub errors on a matrix with no vectors, and a course org
    # with no cohorts registered yet is a normal state, not a failure.
    if: always() && needs.release.outputs.cohorts != '' && needs.release.outputs.cohorts != '[]'
    strategy:
      # One cohort's grading failure must not cancel the others' - they share nothing.
      fail-fast: false
      matrix:
        cohort: ${{{{ fromJSON(needs.release.outputs.cohorts) }}}}
{_AUTOGRADE_CONCURRENCY}{_ungated_preamble(_TIMEOUT_GRADING)}      - name: Autograde every passed deadline
        env:
          GH_TOKEN: ${{{{ secrets.DSL_BOT_TOKEN }}}}
          COURSE: ${{{{ github.repository_owner }}}}
          COHORT: ${{{{ matrix.cohort }}}}
          DRY_RUN: ${{{{ inputs.dry_run }}}}
        run: |
          gh auth setup-git
          args=(--course-org "$COURSE" --cohort-org "$COHORT" --autograde-only)
          [ "$DRY_RUN" = "true" ] && args+=(--dry-run)
          python3 -m dsl_course.scheduler "${{args[@]}}"
{_AUTOGRADE_NOTICE}"""


def render_status(cohort_orgs: list[str]) -> str:
    """Per-cohort checklist of every faculty & instructors input location - identity, people,
    schedule + release plan, roster, teams, grades - with the current value and a
    direct edit link for anything missing. Read-only; changes nothing."""
    return f"""name: Check cohort setup

# A per-cohort glance view of everything configured (and everything still missing),
# with direct links to fix it. Read-only - this workflow changes nothing.
# It carries the GRAPH_* secrets to REPORT on them (present/absent only - no value is
# printed), never to send: this is where a missing mail transport is meant to be caught,
# before a roster push tries to mail a cohort its codes.

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
{_MAIL_ENV}
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
# always checked out from this org's central ref - so an org left alone drifts, until a stale
# workflow calls engine code that has since moved. This re-seeds the org daily, so every org
# converges on central within 24h with nobody pressing anything. The refresh is idempotent
# and skips files whose content is unchanged, so a night with no central changes is silent.
#
# It is also how a promotion reaches an org: after the tier branch moves, the next run here
# re-renders every workflow at the org's ref. Run it by hand to pick a promotion up now.

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

    format/type land in the solution branch's grading_config.yml (and shape the starter/hidden
    tests), so the choice made on this workflow is the one the grader later obeys - the
    grading_config.yml vocabulary is picked here, not hand-edited in afterwards."""
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
    - cron: "41 6 * * *"
  workflow_dispatch:
    inputs:
{_choice_input("cohort_org", "Cohort whose site to regenerate from the org structure", cohort_orgs)}

{_concurrency("sync-site")}
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
    # A run REPLACES what the site serves, so the default must be the repo the site was
    # published from - the latest `course-materials-*`. `_newest` alone picked the
    # alphabetically last option of the newest year (`lecture-code-f2026`), and a faculty
    # member clicking Run with the defaults wiped a live site's materials.
    default = _newest_materials(source_repos) or (
        source_repos[0] if source_repos else None
    )
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
    - cron: "58 5 * * *"
  workflow_dispatch:
    inputs:
{_choice_input("source_repo", "Materials repo to publish - the site is REBUILT from it; defaults to the latest course-materials-* repo", source_repos, default)}
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

{_concurrency("publish-course-website")}
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
