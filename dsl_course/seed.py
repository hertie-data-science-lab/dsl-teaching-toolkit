"""dsl-course seed -- render + place the run-from-repo faculty & instructors workflows.

The Release / Provision actions live INSIDE course content (and assignment-template)
repos, so faculty & instructors trigger them from the repo they're working in. The repo the workflow
runs in is the default SOURCE; the action pushes into a chosen cohort org/repo.

The cohort org input is a GitHub `choice` dropdown. GitHub can't populate a dropdown
live, so its options are rendered into the YAML from the cohort registry and
refreshed on demand: `refresh` reads the course org's .github/cohort-courses-pages.yml
`cohorts:` list (maintained by `bootstrap --cohort --course X`, or by hand) and re-pushes
the content actions to every course repo. No cron, no app.

This module is the placement + CLI layer; the three jobs it used to also do live next to
it, and are imported from there (see `__all__` for the few names still reached for as
`seed.<name>`):

- workflows_render - the workflow YAML templates and every render_* function;
- discovery       - the cohort registry and all live org/repo/section/session discovery;
- profile_readme  - the org landing page + `.github` repo README.

CLI:
  refresh --course-org X   re-render the content actions into every course repo with
                           fresh cohort/course-source-repo/assignment dropdowns, converge
                           each materials repo's SYSTEM-owned files (maintainer guide,
                           syllabus example) and its seeded stubs, rebuild
                           the org profile README, and re-push each registered cohort's
                           welcome workflows + classroom-config SYSTEM-owned files (the
                           schema README, the dispatchers, the schedule validator) and
                           `*.sample` worked examples. (Run by the Bootstrap-cohort
                           workflow, and by Refresh actions - on demand and on its nightly
                           cron, which is how an org converges on central `release`
                           without anyone pressing anything.)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .discovery import (
    COHORTS_PATH,
    discover_assignments,
    discover_cohort_repos,
    discover_cohorts,
    discover_content_repos,
    discover_release_sources,
    discover_sessions,
    register_cohort,
    unregister_cohort,
)
from .profile_readme import update_profile_readme
from .roster import CONFIG_REPO
from .utils import (
    get_file_content,
    gh,
    log,
    log_err,
    log_ok,
    log_step,
    org_exists,
    put_file,
    put_files,
    refresh_stubs,
    repo_is_archived,
    term_tag,
)
from .welcome import (
    refresh_classroom_samples,
    refresh_classroom_system_files,
    refresh_cohort_pointer,
    refresh_welcome_workflows,
)
from .workflows_render import (
    render_bootstrap_cohort,
    render_central_release,
    render_distribute_grades,
    render_generate_syllabus,
    render_grade_assignment,
    render_new_assignment,
    render_new_materials,
    render_provision,
    render_publish_site,
    render_refresh,
    render_release,
    render_render_grades,
    render_scheduler,
    render_send_codes,
    render_status,
    render_sync_gradebooks,
    render_sync_membership,
    render_sync_site,
    system_owned,
)

# What the rest of the package reaches for as `seed.<name>`: this module's own jobs, plus
# the handful of discovery/profile names its callers (site, scaffold, bootstrap_course,
# sync_faculty, sync_membership) grew up importing from here. Everything else the split
# moved out is imported from its owning module (workflows_render, discovery,
# profile_readme, central) - so should new code be.
__all__ = [  # noqa: RUF022 - grouped by owning module, sorting would lose the grouping
    # placement + CLI (this module's own job)
    "seed_github_workflows",
    "_push_workflows",
    # discovery.py
    "COHORTS_PATH",
    "discover_assignments",
    "discover_cohort_repos",
    "discover_cohorts",
    "discover_content_repos",
    "discover_release_sources",
    "discover_sessions",
    "register_cohort",
    # profile_readme.py
    "update_profile_readme",
]

# The run-from-repo workflows _push_workflows places in every content repo.
WORKFLOWS = (
    ".github/workflows/release-materials.yml",
    ".github/workflows/release-assignment.yml",
)

# Retired in favour of the consolidated Release materials workflow (whose course_source_path
# takes any folder or file, which is all Release code ever did) - removed from content repos
# seeded before that change, so no repo keeps a workflow whose CLI no longer exists.
RETIRED_WORKFLOWS = (".github/workflows/release-code.yml",)

# The heartbeat file, in the course org's `.github` repo - the repo every seeded cron runs
# from. See _write_heartbeat.
HEARTBEAT_PATH = ".github/.last-refresh"

# The cohort orgs the last refresh could not see, one per line, beside the heartbeat in the
# course org's `.github` repo. One 404 is not proof an org is gone (see _live_cohorts), so
# the verdict has to survive until the next nightly run - and a file in the repo the cron
# already writes to is the only state this toolkit has that does.
MISSES_PATH = ".github/.missing-cohorts"


def _write_heartbeat(course_org: str) -> int:
    """Stamp today's date into the `.github` repo, so its schedules stay alive.

    GitHub disables a repo's scheduled workflows after 60 days with no repository activity,
    and a refresh is deliberately silent when nothing changed (put_file compares blob shas
    and skips identical files). So a course org that is simply quiet for two months has
    every cron switched off at once - including Refresh actions, the one that would have
    self-healed it. Nothing then recovers without a human running a workflow they have no
    reason to know about.

    The content is the DATE alone: a second run on the same day writes an identical blob,
    which put_file skips, so this is at most one commit a day and never fills the repo with
    churn. Returns 1 on a failed write, so a heartbeat that isn't landing counts into
    refresh's failures rather than letting the org drift quietly towards the 60-day cliff."""
    today = datetime.now(timezone.utc).date().isoformat()
    if put_file(
        course_org,
        ".github",
        HEARTBEAT_PATH,
        f"{today}\n".encode(),
        f"chore: refresh heartbeat {today}",
    ):
        return 0
    log_err(
        f"could not write {HEARTBEAT_PATH} in {course_org}/.github - without it this org's "
        "scheduled workflows are disabled after 60 quiet days"
    )
    return 1


def _read_misses(course_org: str) -> set[str]:
    """The cohorts the PREVIOUS refresh could not see - see MISSES_PATH."""
    content = get_file_content(course_org, ".github", MISSES_PATH)
    return {
        line.strip().casefold() for line in (content or "").splitlines() if line.strip()
    }


def _write_misses(course_org: str, misses: set[str], previous: set[str]) -> None:
    """Record this run's misses, if they differ from the last run's.

    Best effort: a failed write only costs a second grace period (the next miss reads as
    a first one), which is the safe direction - never an unregistration."""
    if {m.casefold() for m in misses} == previous:
        return
    put_file(
        course_org,
        ".github",
        MISSES_PATH,
        ("".join(f"{m}\n" for m in sorted(misses))).encode(),
        "chore: record cohort orgs this refresh could not see",
    )


def _live_cohorts(course_org: str) -> list[str]:
    """The registry, converged: every registered cohort whose org still exists, with any
    that has been missing for two consecutive refreshes dropped from the registry on the
    way past.

    A cohort ORG DELETED after it was registered 404s on every write, which would red the
    nightly cron forever. Detect a genuinely-gone org by probing the ORG ITSELF: probing
    one of its repos instead wrongly skipped a live org that had only lost its
    classroom-config repo - which is a real problem that must fail loud in
    refresh_classroom_samples, not be pruned away.

    The registry is CONVERGED rather than merely annotated: this used to log "prune it by
    hand", which nobody does, so a deleted org stayed registered and every nightly sync in
    every tool went on trying it. Removal is safe precisely because the org is proven gone
    - see `unregister_cohort` for why the ADD side stays manual.

    TWO consecutive misses, though, not one. GitHub answers 404 - not 403 - for an org the
    TOKEN cannot see, so a bot dropped from one org, or a rotated token never re-invited
    to it, is indistinguishable from a deleted org: one bad night would have silently
    unregistered a live cohort from every nightly sync, and nothing re-adds it. The first
    miss is loud and costs the cohort only that night's refresh; MISSES_PATH carries the
    verdict to the next run, and a cohort that answers again clears it.

    `org_exists` raises rather than guessing, and the safe reading of "could not tell" here
    is LIVE: the cohort is refreshed as usual and fails loudly on its own if something is
    really wrong, rather than being unregistered on a rate limit or a 502."""
    registered = discover_cohorts(course_org)
    previous = _read_misses(course_org)
    live: list[str] = []
    missing: set[str] = set()
    for cohort in registered:
        try:
            gone = not org_exists(cohort)
        except RuntimeError as exc:
            log(f"  [warn] could not probe {cohort}, treating it as live: {exc}")
            gone = False
        if not gone:
            live.append(cohort)
            continue
        if cohort.casefold() not in previous:
            missing.add(cohort)
            log_err(
                f"{cohort} did not answer - it is either deleted or no longer visible to "
                f"this token. Left registered and skipped for tonight; if it is still "
                f"missing at the next refresh it will be unregistered from {course_org}."
            )
            continue
        log(f"  [skip] {cohort} (missing for a second consecutive refresh)")
        unregister_cohort(course_org, cohort)
        # The cohort's own `instructors-<tag>` team lives in the COURSE org, so deleting
        # the cohort org does not take it with it - and once unregistered, sync_faculty
        # never looks at it again. Say so here: before the prune this showed up as a
        # nightly sync_faculty failure, and silently trading that for an orphaned team
        # holding push on this org's repos would be a worse deal than the noise.
        log(
            f"  [note] {course_org}/instructors-{term_tag(cohort) or cohort} may now be "
            f"an orphaned team with push access - delete it by hand if so"
        )
    _write_misses(course_org, missing, previous)
    return live


def _push_workflows(
    org: str,
    repo: str,
    cohort_orgs: list[str],
    assignments: list[str],
) -> int:
    """Place the run-from-repo workflows in one content repo, as ONE commit.

    Both workflows are re-rendered from the same inputs and change together (a new cohort
    org, a new assignment template, an edit to the template here), so writing them file by
    file put a pair of near-identical `ci: ... wrapper` commits into a repo faculty
    actually read, for what is one logical change. put_files makes it one commit - and
    folds the retired-workflow removal into it, so retiring a workflow costs no commit of its
    own either.

    Returns 1 if that commit didn't land, so refresh can report a run that didn't
    converge. It is all-or-nothing: put_files moves the branch once, at the end."""
    if not put_files(
        org,
        repo,
        {
            WORKFLOWS[0]: system_owned(render_release(cohort_orgs, repo)).encode(),
            WORKFLOWS[1]: system_owned(
                render_provision(cohort_orgs, assignments)
            ).encode(),
        },
        "ci: refresh release workflows",
        delete=RETIRED_WORKFLOWS,
    ):
        log_err(f"release workflows not written to {org}/{repo}")
        return 1
    log_ok(f"workflows -> {org}/{repo}")
    return 0


def seed_github_workflows(course_org: str) -> int:
    """Seed/refresh the org-level workflows into the course org's .github repo: the
    CENTRAL Release materials (course-source-repo dropdown), Release assignment, plus Sync
    membership / Bootstrap cohort / Refresh.

    All of them land as ONE commit (and the retired ones go in the same commit). They are
    rendered from one set of inputs by shared helpers, so in practice they change together:
    an edit to the run preamble or to a dropdown helper re-renders every one of them, and
    file-by-file writes turned each such edit into a wall of near-identical
    `ci: <file>.yml` commits in the repo whose history faculty actually browse.

    Returns 1 if that commit didn't land - a workflow that didn't land is exactly the thing a
    green run must not hide."""
    cohorts = discover_cohorts(course_org)
    source_repos = discover_content_repos(course_org)
    assignments = discover_assignments(course_org)
    files = {
        ".github/workflows/release-materials.yml": render_central_release(
            source_repos, cohorts
        ),
        ".github/workflows/release-assignment.yml": render_provision(
            cohorts, assignments
        ),
        ".github/workflows/grade-assignment.yml": render_grade_assignment(
            cohorts, assignments
        ),
        ".github/workflows/new-materials.yml": render_new_materials(),
        ".github/workflows/generate-syllabus.yml": render_generate_syllabus(
            source_repos, cohorts
        ),
        ".github/workflows/new-assignment.yml": render_new_assignment(),
        ".github/workflows/sync-site.yml": render_sync_site(cohorts),
        ".github/workflows/publish-site.yml": render_publish_site(source_repos),
        ".github/workflows/sync-membership.yml": render_sync_membership(cohorts),
        ".github/workflows/send-codes.yml": render_send_codes(cohorts),
        ".github/workflows/sync-gradebooks.yml": render_sync_gradebooks(cohorts),
        ".github/workflows/render-grades.yml": render_render_grades(cohorts),
        ".github/workflows/distribute-grades.yml": render_distribute_grades(cohorts),
        ".github/workflows/bootstrap-cohort.yml": render_bootstrap_cohort(),
        ".github/workflows/check-cohort-setup.yml": render_status(cohorts),
        ".github/workflows/refresh-actions.yml": render_refresh(),
        ".github/workflows/scheduled-release.yml": render_scheduler(),
    }
    log_step(f"Seeding org-level workflows into {course_org}/.github")
    if not put_files(
        course_org,
        ".github",
        {path: system_owned(content).encode() for path, content in files.items()},
        "ci: refresh org workflows",
        # Retired workflows - remove any copies already seeded into orgs bootstrapped before
        # the change, so faculty never see two workflows for one job. sync-enrolment/sync-teams
        # were consolidated into sync-membership.yml; status.yml was renamed to
        # check-cohort-setup.yml (same workflow, a name that says what it checks).
        delete=(
            ".github/workflows/sync-enrolment.yml",
            ".github/workflows/sync-teams.yml",
            ".github/workflows/status.yml",
        ),
    ):
        log_err(f"org workflows not written to {course_org}/.github")
        return 1
    log_ok(f".github <- {len(files)} org-level workflow(s)")
    return 0


def _propagate_repo_secret(course_org: str, repos: list[str]) -> int:
    """On GitHub Free, org secrets don't reach PRIVATE repos - so set DSL_BOT_TOKEN as a
    repo secret on each content repo (from the token this run already holds), letting
    their run-from-repo workflows authenticate. Returns the number of repos the secret
    could NOT be set on: a repo left with an empty DSL_BOT_TOKEN runs its Release workflows
    with no auth and fails weeks later when faculty run them, so a failure here must
    count into refresh's exit code rather than pass silently.

    Only DSL_BOT_TOKEN is published. A maintainer running `seed refresh` by hand usually
    has their PERSONAL GH_TOKEN exported; publishing that as the shared repo secret would
    leak their PAT into every content repo, so if only GH_TOKEN is set we refuse. The
    refusal counts every repo as unpropagated rather than passing green: until the nightly
    refresh self-heals an org, its content repos still run the pre-fix new-assignment.yml
    (no DSL_BOT_TOKEN in env), so a green refusal is a live workflow path left with no auth.
    The value goes over stdin - `gh secret set` reads it from there whenever `--body` is
    omitted - never argv, so it is not visible in `ps`."""
    token = os.environ.get("DSL_BOT_TOKEN")
    if not token:
        if os.environ.get("GH_TOKEN"):
            log_err(
                "DSL_BOT_TOKEN not set (only GH_TOKEN is) - refusing to publish a personal "
                "token as the DSL_BOT_TOKEN repo secret; set DSL_BOT_TOKEN to propagate it."
            )
        else:
            log_err(
                "DSL_BOT_TOKEN not set - cannot propagate the repo secret to "
                f"{len(repos)} content repo(s)."
            )
        return len(repos)
    failures = 0
    for repo in repos:
        code, out = gh(
            "secret",
            "set",
            "DSL_BOT_TOKEN",
            "--repo",
            f"{course_org}/{repo}",
            stdin=token,
        )
        if code == 0:
            log_ok(f"repo secret -> {repo}")
        else:
            log_err(f"could not set DSL_BOT_TOKEN on {course_org}/{repo}: {out[:120]}")
            failures += 1
    return failures


def _refresh_stubs(course_org: str, repo: str) -> int:
    """Bring a content repo's seeded STUBS up to date, without creating any.

    The scaffold's stubs improve over time - the syllabus grew the standard Hertie sections,
    the reading list grew Required/Optional headings - and were create-only, so a course
    scaffolded a month ago kept whatever the toolkit first shipped and every later
    improvement reached new repos only. They now carry a mark, so a stub can be refreshed
    while it is still ours and is never touched once faculty write over it
    (`utils.refresh_stubs`).

    `create=False` is the whole reason this is safe to run over EVERY content repo:
    `discover_content_repos` returns the code and dataset repos too, and seeding a syllabus
    into `lecture-code-f2026` would be nonsense. Creating stays the scaffold's job, because
    only the scaffold knows what kind of repo it just made; this only improves what is
    already there.

    Two reads per stub per repo, so a handful of calls per org per night."""
    # Local import: `scaffold` imports this module, so a module-level one is a cycle. Same
    # shape as `scheduler`'s import of `deploy`.
    from . import scaffold

    # `course-materials-f2026` -> `f2026`, which is all the stubs interpolate. A repo with
    # no term tag is not one the materials scaffold made, and rewriting its stub would head
    # the file `#  syllabus` - so it is left alone rather than refreshed into nonsense.
    tag = term_tag(repo)
    if tag is None:
        return 0
    return refresh_stubs(
        course_org,
        repo,
        scaffold.refreshable_stubs(tag),
        "docs: refresh the scaffold stubs",
        retire=scaffold.RETIRED_STUBS,
    )


def refresh(course_org: str) -> int:
    """Refresh both layers: the run-from-repo content actions in every content repo,
    AND the central org-level workflows in .github; converge each materials repo's
    SYSTEM-owned files (maintainer guide, syllabus example) and its seeded stubs;
    repopulate dropdowns; rebuild the org profile README; re-push every registered cohort's welcome workflows, its
    classroom-config SYSTEM-owned files (README contract, dispatch-sync*.yml,
    validate-schedule.yml) and its `*.sample` worked examples (skipping cohorts whose
    repos are archived) - never its own config, which stays create-if-missing; (Free-plan
    workaround) propagate the token as a repo secret so private content repos can
    authenticate; and stamp the heartbeat that keeps this org's crons from being
    auto-disabled (_write_heartbeat).

    Non-zero if any file could not be written: this runs nightly on a cron, so a run that
    silently failed to converge an org would go unnoticed until someone ran a workflow
    that was never seeded."""
    # Local import: `scaffold` imports this module, so a module-level one is a cycle.
    from . import scaffold

    # Converge the registry FIRST, so `cohorts` is the live list for everything below.
    # Every org-level workflow dropdown, the run-from-repo workflows in every content
    # repo and the profile README's cohort list are all rendered from it further down;
    # pruning after them wrote the dead org into all of them one last time and self-healed
    # a night later, which is the same "converges eventually, if someone waits" the prune
    # exists to end.
    cohorts = _live_cohorts(course_org)
    targets = discover_content_repos(course_org)
    assignments = discover_assignments(
        course_org
    )  # org-wide; discover once, not per repo
    log_step(
        f"Refreshing {len(targets)} content repo(s) in {course_org} with cohorts {cohorts or 'none'}"
    )
    failures = 0
    for repo in sorted(targets):
        failures += _push_workflows(course_org, repo, cohorts, assignments)
        failures += _refresh_stubs(course_org, repo)
        # A no-op on the code and dataset repos this sweep also returns; the gate is
        # inside, so no caller can forget it.
        failures += scaffold.refresh_materials_system_files(course_org, repo)
    failures += _propagate_repo_secret(course_org, targets)
    failures += seed_github_workflows(course_org)
    failures += _write_heartbeat(course_org)
    failures += update_profile_readme(course_org)
    # A cohort's onboarding workflows, classroom-config dispatchers and config samples are
    # seeded at Bootstrap cohort, and would otherwise stay frozen for the whole semester
    # while the engine they call - and the schemas the samples demonstrate - move on.
    log_step(
        f"Refreshing welcome workflows + classroom-config system files + samples "
        f"in {len(cohorts)} cohort org(s)"
    )
    for cohort in cohorts:
        # A finished semester's cohort is archived, and an archived repo is read-only:
        # every write 403s, and the samples are new files so put_file's sha no-op can't
        # absorb it. A past cohort is meant to stay frozen anyway, so skip it whole rather
        # than turn the nightly cron red in every org that has ever finished a semester.
        # repo_is_archived assumes LIVE on a transient read failure, so a live cohort's
        # refresh is never silently skipped.
        if repo_is_archived(cohort, CONFIG_REPO):
            log(f"  [skip] {cohort} (archived cohort - left frozen)")
            continue
        failures += refresh_welcome_workflows(cohort)
        # SYSTEM-owned files only (see welcome.CLASSROOM_SYSTEM_FILES): the cohort's own
        # students.csv/teams.csv/schedule.yml/people.yml are never touched here, or this
        # nightly cron would overwrite a live roster every night.
        failures += refresh_classroom_system_files(cohort)
        failures += refresh_classroom_samples(cohort)
        # The pointer its dispatchers read to find this course org. Also SYSTEM-owned and
        # also only ever written by Bootstrap cohort until now - same bug class.
        failures += refresh_cohort_pointer(cohort, course_org)
        # The cohort's OWN landing pages - the student-facing profile/README.md and the
        # orientation in its .github repo. Only the COURSE org's pair was ever refreshed
        # (below the content-repo sweep above): a cohort's were written once at Bootstrap
        # and then frozen for the life of the org, so every wording fix since reached the
        # course org and no cohort. The .github README is SYSTEM-owned and rewritten
        # outright; the student-facing landing page is INSTRUCTOR-owned, so only its
        # marked repo table is refreshed (see profile_readme.splice_repo_table) - which is
        # what keeps that table honest as repos are added, without flattening an
        # instructor's wording around it.
        failures += update_profile_readme(cohort)
    if failures:
        log_err(f"refresh incomplete: {failures} file(s) could not be written")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("refresh")
    pr.add_argument("--course-org", required=True)
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return refresh(args.course_org)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
