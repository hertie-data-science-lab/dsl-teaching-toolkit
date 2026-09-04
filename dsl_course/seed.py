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
it, and every caller imports them from their owning module:

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
                           cron, which is how an org converges on its central ref
                           without anyone pressing anything.)
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from . import scaffold
from .access import converge_faculty_access, converge_topics
from .central import MissingCentralRef
from .course import COHORT_TEAMS, CONFIG_REPO, FACULTY_TEAMS, term_tag
from .discovery import (
    central_ref_for,
    discover_assignments,
    discover_cohorts,
    discover_content_repos,
    list_org_repos,
    org_tier,
    student_repo_names,
    unregister_cohort,
)
from .gh_contents import get_file_content, put_file, put_files
from .gh_teams import converge_org_settings, create_role_teams
from .ghcli import bot_token, gh
from .log import log, log_err, log_ok, log_step
from .profile_readme import update_profile_readme
from .repos import converge_descriptions, org_exists
from .welcome import (
    refresh_classroom_samples,
    refresh_classroom_system_files,
    refresh_cohort_pointer,
    refresh_welcome_workflows,
)
from .workflows_place import push_content_workflows
from .workflows_render import (
    for_placement,
    render_bootstrap_cohort,
    render_central_release,
    render_collect_submissions,
    render_distribute_grades,
    render_generate_syllabus,
    render_new_assignment,
    render_new_materials,
    render_provision,
    render_publish_site,
    render_refresh,
    render_scheduler,
    render_send_codes,
    render_status,
    render_sync_membership,
    render_sync_site,
)

# The heartbeat file, in the course org's `.github` repo - the repo every seeded cron runs
# from. See _write_heartbeat.
HEARTBEAT_PATH = ".github/.last-refresh"

# The MISS LEDGER: `<cohort> <first missed at>` per line, beside the heartbeat in the
# course org's `.github` repo - the only cross-run state this toolkit has. Unregistering a
# cohort takes two misses at least MISS_GRACE_HOURS apart (see _live_cohorts), so the
# first verdict has to survive to the next run, and the grace period has to be measured in
# WALL time: two manual runs ten minutes apart are also two consecutive refreshes.
MISSES_PATH = ".github/.missing-cohorts"
MISS_GRACE_HOURS = 20


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


def _read_misses(course_org: str) -> dict[str, str]:
    """`{cohort: when it was FIRST missed}` from the previous refreshes - see MISSES_PATH.

    A line carrying no timestamp - a ledger written before they were recorded - maps to
    "", which reads as "too recent to act on" and costs one more grace period. That is the
    safe direction: never an unregistration."""
    content = get_file_content(course_org, ".github", MISSES_PATH)
    out: dict[str, str] = {}
    for line in (content or "").splitlines():
        cohort, _, first_seen = line.strip().partition(" ")
        if cohort:
            out[cohort.casefold()] = first_seen.strip()
    return out


def _write_misses(
    course_org: str, misses: dict[str, str], previous: dict[str, str]
) -> None:
    """Record this run's misses, if they differ from the last run's.

    Each line is `<cohort> <first missed at>`, and a cohort still missing keeps the
    ORIGINAL timestamp - re-stamping it every night would restart the grace period every
    night and nothing would ever be unregistered.

    Best effort: a failed write only costs a second grace period (the next miss reads as
    a first one), which is the safe direction - never an unregistration."""
    if misses == previous:
        return
    put_file(
        course_org,
        ".github",
        MISSES_PATH,
        ("".join(f"{c} {at}\n" for c, at in sorted(misses.items()))).encode(),
        "chore: record cohort orgs this refresh could not see",
    )


def _live_cohorts(course_org: str) -> tuple[list[str], int]:
    """The registry, converged: every registered cohort whose org still exists, with any
    that has been missing for two refreshes at least MISS_GRACE_HOURS apart dropped from
    the registry on the way past. Returns `(live cohorts, how many were unregistered)`.

    The count goes into the refresh's failure total: nothing re-adds a cohort, and every
    nightly sync in every tool stops looking at that org, so a run that removed one is not
    an ordinary green night.

    TWO misses, on two different days, because GitHub answers 404 - not 403 - for an org
    the TOKEN cannot see: a bot dropped from one org, or a rotated token never re-invited,
    is indistinguishable from a deleted org, and one bad night would silently unregister a
    live cohort. The first miss is loud, costs that cohort only that night's refresh, and
    is carried to the next run in MISSES_PATH; a cohort that answers again clears it.

    Liveness is probed on the ORG itself, never one of its repos - a live org that has only
    lost its classroom-config must fail loud in refresh_classroom_samples, not be pruned
    away. `org_exists` raises rather than guessing, and "could not tell" reads as LIVE.
    """
    registered = discover_cohorts(course_org)
    previous = _read_misses(course_org)
    now = datetime.now(timezone.utc)
    live: list[str] = []
    missing: dict[str, str] = {}
    unregistered = 0
    for cohort in registered:
        try:
            gone = not org_exists(cohort)
        except RuntimeError as exc:
            log(f"  [warn] could not probe {cohort}, treating it as live: {exc}")
            gone = False
        if not gone:
            live.append(cohort)
            continue
        first_seen = previous.get(cohort.casefold(), "")
        try:
            since = now - datetime.fromisoformat(first_seen)
        except ValueError:
            since = timedelta(0)  # never recorded, or unparseable - start the clock now
        if since < timedelta(hours=MISS_GRACE_HOURS):
            missing[cohort.casefold()] = first_seen or now.isoformat(timespec="seconds")
            log_err(
                f"{cohort} did not answer - it is either deleted or no longer visible to "
                f"this token. Left registered and skipped for tonight; if it is still "
                f"missing at a refresh more than {MISS_GRACE_HOURS}h from the first miss "
                f"it will be unregistered from {course_org}."
            )
            continue
        log(
            f"  [skip] {cohort} (missing since {first_seen} - unregistering it from "
            f"{course_org})"
        )
        unregister_cohort(course_org, cohort)
        unregistered += 1
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
    return live, unregistered


def seed_github_workflows(course_org: str, central_ref: str) -> int:
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
        ".github/workflows/collect-submissions.yml": render_collect_submissions(
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
        ".github/workflows/send-codes.yml": render_send_codes(),
        ".github/workflows/distribute-grades.yml": render_distribute_grades(cohorts),
        ".github/workflows/bootstrap-cohort.yml": render_bootstrap_cohort(),
        ".github/workflows/check-cohort-setup.yml": render_status(cohorts),
        ".github/workflows/refresh-actions.yml": render_refresh(),
        ".github/workflows/scheduled-release.yml": render_scheduler(),
    }
    log_step(
        f"Seeding org-level workflows into {course_org}/.github at central ref {central_ref}"
    )
    if not put_files(
        course_org,
        ".github",
        {
            path: for_placement(content, central_ref).encode()
            for path, content in files.items()
        },
        "ci: refresh org workflows",
        # Retired workflows - remove any copies already seeded into orgs bootstrapped before
        # the change, so faculty never see two workflows for one job. sync-enrolment/sync-teams
        # were consolidated into sync-membership.yml; status.yml was renamed to
        # check-cohort-setup.yml (same workflow, a name that says what it checks); and the
        # three grading buttons became two - Collect submissions refreshes the grading sheet,
        # Distribute grades sends what a grader typed into it, and the preview PR is gone.
        delete=(
            ".github/workflows/sync-enrolment.yml",
            ".github/workflows/sync-teams.yml",
            ".github/workflows/status.yml",
            ".github/workflows/grade-assignment.yml",
            ".github/workflows/sync-gradebooks.yml",
            ".github/workflows/render-grades.yml",
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

    `ghcli.bot_token` owns the "never a personal GH_TOKEN" refusal; its failure counts
    every repo as unpropagated rather than passing green, because until the nightly
    refresh self-heals an org its content repos still run the pre-fix new-assignment.yml
    (no DSL_BOT_TOKEN in env). The value goes over stdin - `gh secret set` reads it from
    there whenever `--body` is omitted - never argv, so it is not visible in `ps`."""
    token = bot_token("the DSL_BOT_TOKEN repo secret")
    if not token:
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


def _converge_org_metadata(org: str, repos: list[dict]) -> int:
    """Bring an org's repo descriptions, faculty-team access and machinery topics up to
    what the toolkit now says they should be - all three off ONE listing.

    Each of the three is set once, when a repo is created, and never revisited: a repo
    kind that predates its grant, an org bootstrapped before one existed, a description
    the toolkit has since reworded, a topic whose PATCH failed after the create. This is
    the convergence path for all of them, and it is the nightly sweep's job - it used to
    ride inside update_profile_readme, which meant a README renderer was quietly the only
    thing granting repo permissions in the estate.

    Costs no reads beyond the listing the caller passes in (which carries `description`,
    `topics` and `isTemplate`), and converge_descriptions mutates it in place, so the
    landing page rendered from the same listing shows the corrected wording in this run
    rather than the next.

    Returns the failure count that must reach the refresh's exit code."""
    tier = org_tier(repos)
    described = converge_descriptions(org, repos, tier)
    granted = converge_faculty_access(
        org, repos, tier, protected=student_repo_names(repos)
    )
    topics = converge_topics(org, repos, tier)
    changed = described.changed + granted.changed + topics.changed
    if changed:
        log_ok(f"{org}: {changed} repo(s) converged (descriptions, access, topics)")
    # Only a missing TOPIC reds the run. A topic is what keeps a student's submission repo
    # and a private gradebook off the landing page, out of the release targets and on the
    # faculty read floor, and nothing else revisits it. A reworded description is
    # documentation, and a failed access PUT is retried by the next sweep - both have
    # already logged their own line.
    return topics.failures


def _converge_org(
    org: str,
    central_ref: str,
    listing: list[dict] | None = None,
    is_cohort: bool = False,
) -> int:
    """Sweep one org's repo listing and re-render its landing pages from that SAME
    snapshot. Failure count.

    One listing for both: the sweep corrects the descriptions the landing page's table is
    built from, so the page is right in this run rather than one run behind.

    Run for the course org and for every live cohort. A cohort's own pages - the
    student-facing profile/README.md and the orientation in its `.github` - were written
    once at Bootstrap and then frozen, so every wording fix since reached the course org
    and no cohort. The `.github` README is SYSTEM-owned and rewritten outright; the
    student-facing landing page is INSTRUCTOR-owned, so only its marked repo table is
    refreshed (see profile_readme.splice_repo_table) - which is what keeps that table
    honest as repos are added, without flattening an instructor's wording around it.

    `listing` is that snapshot when the caller already holds one - a cohort's refresh reads
    its archived flag off the same listing rather than probing classroom-config for it.

    The org's own settings are converged here too (gh_teams.converge_org_settings). They
    used to be written only at bootstrap, so every org tightened after its own bootstrap
    kept GitHub's default of `read` for every member on every repo.

    `is_cohort` says this org is a cohort, which additionally converges the four role teams'
    PRIVACY (course.FACULTY_TEAMS + COHORT_TEAMS). Their privacy was asserted only by the
    team-creating call at bootstrap, so `students` and `auditors` stayed `closed` - their
    membership browsable by every student in the org - on every cohort created before they
    were made `secret`. One GET per role team per night; the read-before-PATCH inside
    create_team is what keeps that from being four writes."""
    if listing is None:
        listing = list_org_repos(org)
    return (
        converge_org_settings(org)
        + (create_role_teams(org, (*FACULTY_TEAMS, *COHORT_TEAMS)) if is_cohort else 0)
        + _converge_org_metadata(org, listing)
        + update_profile_readme(org, central_ref=central_ref, repos=listing)
    )


def refresh(course_org: str) -> int:
    """Refresh both layers: the run-from-repo content actions in every content repo,
    AND the central org-level workflows in .github; converge each materials repo's
    SYSTEM-owned files (maintainer guide, syllabus example) and its seeded stubs;
    repopulate dropdowns; converge each org's repo descriptions, faculty-team
    access and machinery topics (_converge_org_metadata) and rebuild its profile README
    off the same listing; re-push every registered cohort's welcome workflows, its
    classroom-config SYSTEM-owned files (README contract, dispatch-sync*.yml,
    validate-schedule.yml) and its `*.sample` worked examples (skipping cohorts whose
    repos are archived) - never its own config, which stays create-if-missing; (Free-plan
    workaround) propagate the token as a repo secret so private content repos can
    authenticate; and stamp the heartbeat that keeps this org's crons from being
    auto-disabled (_write_heartbeat).

    Non-zero if any file could not be written: this runs nightly on a cron, so a run that
    silently failed to converge an org would go unnoticed until someone ran a workflow
    that was never seeded."""
    # Converge the registry FIRST, so `cohorts` is the live list for everything below.
    # Every org-level workflow dropdown, the run-from-repo workflows in every content
    # repo and the profile README's cohort list are all rendered from it further down;
    # pruning after them wrote the dead org into all of them one last time and self-healed
    # a night later, which is the same "converges eventually, if someone waits" the prune
    # exists to end.
    # ONE read of this org's tier, threaded into everything below: the course org's
    # workflows, its content repos' workflows, and every cohort's classroom-config
    # validator all have to be pinned to the same ref, and a cohort inherits its course
    # org's (central_ref_for), so re-reading it per cohort could only ever disagree.
    central_ref = central_ref_for(course_org)
    cohorts, unregistered = _live_cohorts(course_org)
    targets = discover_content_repos(course_org)
    assignments = discover_assignments(
        course_org
    )  # org-wide; discover once, not per repo
    log_step(
        f"Refreshing {course_org} at central ref {central_ref}: {len(targets)} content "
        f"repo(s), cohorts {cohorts or 'none'}"
    )
    # An unregistration is never a silent success: see _live_cohorts.
    failures = unregistered
    # `central.pin_central_ref` refuses a ref the central repo does not have, and every
    # workflow write below goes through it. Caught ONCE, here: the first refusal counts a
    # single failure and skips every later workflow write, leaving the PREVIOUS rendering
    # exactly where it is - stale workflows that run beat current workflows that cannot
    # check anything out - while every other step of the refresh proceeds as usual.
    refused = False

    def render(step: Callable[[], int]) -> int:
        nonlocal refused
        if refused:
            return 0
        try:
            return step()
        except MissingCentralRef as exc:
            refused = True
            log_err(f"{exc} {course_org}'s workflows are NOT being re-rendered.")
            return 1

    for repo in sorted(targets):
        failures += render(
            lambda repo=repo: push_content_workflows(
                course_org, repo, cohorts, assignments, central_ref
            )
        )
        # A no-op on the code and dataset repos this sweep also returns; the gate is
        # inside, so no caller can forget it.
        failures += scaffold.refresh_materials_system_files(course_org, repo)
    failures += _propagate_repo_secret(course_org, targets)
    failures += render(lambda: seed_github_workflows(course_org, central_ref))
    failures += _write_heartbeat(course_org)
    failures += _converge_org(course_org, central_ref)
    # A cohort's onboarding workflows, classroom-config dispatchers and config samples are
    # seeded at Bootstrap cohort, and would otherwise stay frozen for the whole semester
    # while the engine they call - and the schemas the samples demonstrate - move on.
    log_step(
        f"Refreshing welcome workflows + classroom-config system files + samples "
        f"in {len(cohorts)} cohort org(s)"
    )
    for cohort in cohorts:
        # ONE listing of the cohort: the archived flag below, and the convergence sweep +
        # profile rebuild at the end of the loop, are all read off this same snapshot. The
        # flag used to be its own GET of classroom-config, a night after night probe for a
        # field the listing already carries.
        listing = list_org_repos(cohort)
        config_repo = next((r for r in listing if r["name"] == CONFIG_REPO), None)
        # A finished semester's cohort is archived, and an archived repo is read-only:
        # every write 403s, and the samples are new files so put_file's sha no-op can't
        # absorb it. A past cohort is meant to stay frozen anyway, so skip it whole rather
        # than turn the nightly cron red in every org that has ever finished a semester.
        # A cohort with no classroom-config at all is not archived, it is unfinished, and
        # the writes below are what give it one.
        if config_repo is not None and config_repo.get("archived"):
            log(f"  [skip] {cohort} (archived cohort - left frozen)")
            continue
        failures += refresh_welcome_workflows(cohort)
        # SYSTEM-owned files only (see welcome.CLASSROOM_SYSTEM_FILES): the cohort's own
        # students.csv/teams.csv/schedule.yml/people.yml are never touched here, or this
        # nightly cron would overwrite a live roster every night. Skipped whole when the
        # ref is missing: the set includes validate-schedule.yml, which is rendered at it.
        failures += render(
            lambda cohort=cohort: refresh_classroom_system_files(cohort, central_ref)
        )
        failures += refresh_classroom_samples(cohort)
        # The pointer its dispatchers read to find this course org. Also SYSTEM-owned and
        # also only ever written by Bootstrap cohort until now - same bug class.
        failures += refresh_cohort_pointer(cohort, course_org)
        failures += _converge_org(cohort, central_ref, listing, is_cohort=True)
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
