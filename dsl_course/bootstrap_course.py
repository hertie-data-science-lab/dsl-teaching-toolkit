"""bootstrap-course -- one-time setup for a new course org.

Sets up org-level infrastructure that persists across semesters:
- DSL_BOT_TOKEN secret (required for all workflows)
- Faculty teams (instructors, course-admin); cohort bootstrap adds students + auditors
- Org settings (2FA enforcement, Pages default branch)
- Profile README (.github repo with description)
- Org-level workflows in .github (sync-membership, bootstrap-cohort, refresh-actions)
- Central faculty & instructors workflows seeded into .github (Release materials/assignment +
  Sync membership/Bootstrap-cohort/Refresh); the run-from-repo copies are equipped by Refresh

With --cohort, instead tightens the org and seeds the student-facing welcome (onboard)
and classroom-config (roster) repos.

Usage:
    python3 -m dsl_course.bootstrap_course --org hertie-dsl-demo-course-e1234
    python3 -m dsl_course.bootstrap_course --org hertie-dsl-demo-f2026 --cohort
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import scaffold, seed, site, sync_faculty
from .discovery import COHORT_TOPIC, COURSE_HUB_TOPIC
from .utils import (
    COHORT_WRITE_REPOS,
    COURSE_TEAM_ACCESS,
    create_repo,
    create_team,
    gh,
    grant_team_repo_access,
    log,
    log_err,
    log_ok,
    log_step,
    put_file,
    repo_exists,
    repo_is_private,
    seed_files_if_absent,
    seed_if_absent,
    set_repo_topics,
)
from .welcome import (
    CLASSROOM_SCAFFOLDS,
    refresh_classroom_samples,
    refresh_classroom_system_files,
    refresh_welcome_workflows,
    template,
)


def _profile_topics(is_cohort: bool, course_code: str = "") -> list[str]:
    """Topics for an org's .github repo. list_orgs.py enumerates COURSE orgs by
    dsl-course-hub, so a cohort org must NOT carry it (it would show up in the
    course-org inventory as a course); cohorts get dsl-cohort, a human-facing marker."""
    if is_cohort:
        return [COHORT_TOPIC]
    topics = [COURSE_HUB_TOPIC]
    if course_code:
        topics.append(f"course-{course_code.lower()}")
    return topics


# ---------------------------------------------------------------------------------------
# Seeded content: USER-owned vs SYSTEM-owned
#
# Bootstrap (both "Bootstrap course" and "Bootstrap cohort") is re-run on EXISTING orgs as
# the documented idempotent-repair path - e.g. to apply new team grants or refresh
# workflows mid-semester. So every write it makes has to be re-run-safe. `create_repo` is
# NOT a first-run guard: it treats an already-existing repo as success (utils.create_repo
# returns True on the 422 "name already exists"), so an `if create_repo(...)` block runs on
# every re-run. The guard has to be per FILE, and it depends on who owns the file:
#
#   USER-owned - content faculty edit, or that the running system writes live state into.
#   In a cohort: classroom-config/{students.csv, teams.csv, schedule.yml, people.yml,
#   grades/** (except *.sample)} and welcome/README.md (the student landing page). On a
#   course org: .github/dsl-course.yml (the faculty/course_admins SSOT). Seed these ONLY
#   when absent (utils.seed_if_absent) - rewriting them on a re-run destroys live enrolment
#   state (roster rows, enrol codes, onboarded handles) and the faculty's schedule.
#
#   SYSTEM-owned - machinery and documentation this repo generates and must be able to fix
#   in place: everything under `.github/` in the seeded repos (welcome/onboard.yml,
#   welcome/team-formation.yml, the ISSUE_TEMPLATE join forms those workflows parse - they
#   must stay in lockstep with them - and classroom-config's dispatch-sync*.yml), a
#   cohort's `.github/dsl-course.yml` (a wholly generated course pointer with no
#   faculty-authored content), classroom-config's README.md (the schema contract - it went
#   stale as USER-owned), and every `*.sample` (worked examples the engine never ingests;
#   activation = copying rows into the real file, so refreshing them is safe).
#   These are written unconditionally on every run so fixes propagate, exactly like
#   seed.seed_github_workflows.
#
# Every user-editable classroom-config file ships as a PAIR under one rule: `<file>` is a
# minimal commented scaffold (USER-owned, seeded once) and `<file>.sample` is a filled,
# realistic example (SYSTEM-owned, always converged). The samples are injected from
# example-course/cohort-org/ rather than authored a second time - see
# welcome.CLASSROOM_SAMPLES.
# ---------------------------------------------------------------------------------------


def _cohort_tag(org: str) -> tuple[str, int]:
    """This cohort's year tag (fYYYY/sYYYY) and year, derived from the org-name suffix.

    Renders the seeded scaffolds' examples (schedule.yml repo names/dates, people.yml
    dates) copy-paste-correct for THIS cohort. A name with no tag suffix falls back to
    f2026-shaped examples - purely cosmetic, everything rendered from this is commented."""
    m = re.search(r"[-_]([fs])(\d{4})$", org, re.IGNORECASE)
    if not m:
        return "f2026", 2026
    return f"{m.group(1).lower()}{m.group(2)}", int(m.group(2))


def set_org_secret(org: str, secret_name: str, secret_value: str) -> bool:
    """Create or update an org secret, scoped to the infra repos that need it.

    The token must reach the **public** `.github` (faculty & instructors workflows), `welcome`
    (onboarding), and `classroom-config` (its dispatch-sync workflow cross-repo
    triggers Sync membership in `.github`). gh defaults org-secret visibility to
    `private`, which excludes public repos - so the seeded workflows there run with
    an empty `secrets.DSL_BOT_TOKEN` and fail with "set the GH_TOKEN environment
    variable". Scope it explicitly to the infra repos that exist, which also keeps
    this org-admin credential out of student-facing/content repos (`visibility=all`
    would expose it to every workflow in the org) - classroom-config is already
    private/faculty-only, the same trust tier as `.github`.

    The value goes over stdin - `gh secret set` reads it from there whenever `--body` is
    omitted - never argv, so it is not visible in `ps` to anyone on the runner."""
    infra = [
        r for r in (".github", "welcome", "classroom-config") if repo_exists(org, r)
    ] or [".github"]
    code, out = gh(
        "secret",
        "set",
        secret_name,
        "--org",
        org,
        "--visibility",
        "selected",
        "--repos",
        ",".join(infra),
        stdin=secret_value,
    )
    if code != 0:
        log_err(f"failed to set org secret {secret_name}: {out[:200]}")
        return False
    log_ok(f"org secret set: {secret_name} (selected: {', '.join(infra)})")

    # Free-plan delivery gap: an org secret with `selected` visibility is never
    # delivered to a PRIVATE repo (only public ones receive it). classroom-config is
    # private, so its dispatch workflows would read an empty `secrets.DSL_BOT_TOKEN`.
    # Mirror the value as a repo-level secret on each private infra repo so it lands.
    # A failed mirror is a failed write, not a cosmetic one: the org-secret call alone
    # succeeding still leaves classroom-config's dispatch workflows reading an empty
    # DSL_BOT_TOKEN, which is exactly the Free-plan gap this mirror exists to close.
    mirror_failures = 0
    for r in infra:
        if not repo_is_private(org, r):
            continue
        rc, rout = gh(
            "secret", "set", secret_name, "--repo", f"{org}/{r}", stdin=secret_value
        )
        if rc == 0:
            log_ok(f"repo secret set (private infra): {org}/{r}")
        else:
            log_err(f"failed to set repo secret on {org}/{r}: {rout[:200]}")
            mirror_failures += 1
    return mirror_failures == 0


# Faculty role teams - created in EVERY org (course + cohort): instructors run the workflows
# and push content (write); course-admin manage the org (admin).
FACULTY_TEAMS = [
    ("instructors", "Instructors and TAs", "closed"),
    ("course-admin", "Course administrators - DSL team", "closed"),
]
# Cohort-only role teams: enrolled students + read-only auditors. The persistent course org
# never gets these - it holds unreleased materials, model solutions, and hidden tests, so
# students/auditors must not be near it. Auditors are read-only: assignment release is
# roster-driven (onboarded students only), so auditors never receive assignment repos.
COHORT_TEAMS = [
    ("students", "Enrolled students", "closed"),
    (
        "auditors",
        "Auditors - read-only (released materials only, no assignments)",
        "closed",
    ),
]


def create_default_teams(org: str) -> None:
    """Create the faculty role teams (FACULTY_TEAMS) - in both course and cohort orgs. The
    cohort-only teams (students, auditors) are created separately by create_cohort_teams."""
    log_step("Creating faculty teams")
    for slug, desc, privacy in FACULTY_TEAMS:
        create_team(org, slug, desc, privacy=privacy)


def create_cohort_teams(org: str) -> None:
    """Create the cohort-only role teams (COHORT_TEAMS): enrolled students + read-only
    auditors. Called at cohort bootstrap only - never on the persistent course org."""
    log_step("Creating cohort teams (students, auditors)")
    for slug, desc, privacy in COHORT_TEAMS:
        create_team(org, slug, desc, privacy=privacy)


# The course-org teams that may run the seeded workflows, and their grant on `.github`:
# `instructors` run releases day-to-day (write); `course-admin` manage the org (admin).
# Access is per-course - only this course's teaching team goes in these teams. The central
# hertie-data-science-lab faculty/admin teams are a SEPARATE concern (who may bootstrap an
# org at all - the central action's gate); they are deliberately NOT mirrored in here.
BUTTON_TEAMS = COURSE_TEAM_ACCESS


def grant_button_access(org: str) -> None:
    """Give the course-org teams write/admin on `.github`, so faculty & instructors in them can see +
    run the seeded workflow_dispatch workflows. GitHub only shows the 'Run workflow' button
    to write+ users, so without this only the org owner can run the workflows - the seeded
    check-team gate (repo permission) then enforces it at run time too."""
    log_step("Granting course-org teams workflow access (.github)")
    for team, perm in BUTTON_TEAMS.items():
        if grant_team_repo_access(org, team, ".github", perm):
            log_ok(f"  {team} -> {perm} on {org}/.github")


# The COHORT infra repos the faculty teams need the same standing grant on as `.github`.
# A cohort org is tightened to default_repository_permission=none, so without these grants
# only org OWNERS can touch either repo - yet the whole faculty workflow lives in them:
# `classroom-config` is what instructors edit (schedule.yml, students.csv, teams.csv,
# people.yml) and read (grades/), and `welcome` is where they triage `needs-review`
# onboarding issues. Course orgs have neither repo, so this is cohort-only. Single-sourced
# with the nightly sweep's floor (utils.COHORT_WRITE_REPOS), so the two cannot disagree.
COHORT_FACULTY_REPOS = sorted(COHORT_WRITE_REPOS - {".github"})


def grant_cohort_faculty_access(org: str) -> None:
    """Give this cohort's faculty teams their standing access (COURSE_TEAM_ACCESS:
    instructors write, course-admin admin) on the cohort infra repos - `.github` is
    granted separately by grant_button_access, in both org kinds.

    Idempotent, and deliberately outside the `if create_repo(...)` seeding blocks in
    setup_cohort_extras, so re-running "Bootstrap cohort" on an org bootstrapped before
    this existed repairs the missing grants."""
    log_step("Granting cohort faculty access (welcome, classroom-config)")
    for repo in COHORT_FACULTY_REPOS:
        for team, perm in COURSE_TEAM_ACCESS.items():
            if grant_team_repo_access(org, team, repo, perm):
                log_ok(f"  {team} -> {perm} on {org}/{repo}")


def _parse_handles(handles: str) -> list[str]:
    return [h.strip() for h in handles.replace(",", " ").split() if h.strip()]


def add_course_admins(org: str, handles: str) -> None:
    """Add this course's admin(s) to its `course-admin` team (per-course, so nobody is
    added to a course they don't run). `handles` is a comma/space-separated list of GitHub
    logins; each gets an org invite they accept once (membership shows `pending` until
    then). Instructors/TAs are added later to the `instructors` team via the Teams page.

    This is a direct, immediate team invite ONLY - it does not persist anywhere. On the
    course org, `_course_metadata` also seeds these same handles into
    `dsl-course.yml`'s `people.course_admins` (the SSOT `sync_faculty` reconciles
    against), so the next sync doesn't undo this invite by pruning them right back out
    for not being declared. On a cohort org there's no SSOT to write to (course_admins
    stays exclusively course-level) - this invite is real but only until the next sync
    mirrors the course org's actual roster over it."""
    logins = _parse_handles(handles)
    if not logins:
        return
    log_step(f"Adding {len(logins)} admin(s) to {org}/course-admin")
    for login in logins:
        code, out = gh(
            "api",
            "-X",
            "PUT",
            f"orgs/{org}/teams/course-admin/memberships/{login}",
            "-f",
            "role=member",
            "--jq",
            ".state",
        )
        if code == 0:
            log_ok(f"  {login}: {out.strip() or 'added'}")
        else:
            log_err(f"  ! could not add {login}: {out[:120]}")


# course_admins are declared ONCE on the persistent COURSE org - the single source of truth
# for admin access, reconciled into this org's own `course-admin` GitHub team AND mirrored
# into every cohort org's own `course-admin` team. `github_handle` is the only required
# field (it's what actually grants access); `start`/`end` are optional ISO dates - omit
# either for open-ended, or set both to bound access to one window (auto-rotates, no manual
# removal needed).
#
# TAs are never declared here (they change every cohort); instructors appear here only as
# OPTIONAL open-courseware display cards (templates/course/people-cards.yml - the schema
# site._people_from_meta reads for the course-site headshots). A cohort's real teaching team
# - GitHub access AND cohort-site cards - is declared per cohort in that cohort's own
# classroom-config/people.yml (seeded alongside schedule.yml at Bootstrap cohort).
#
# The preamble (people-header.yml) and card scaffold (people-cards.yml) are shared by both
# variants below - fully-commented default and --admins-seeded - so the two can't drift.
def _course_admins_block(admins: list[str] | None) -> str:
    """The `people.course_admins` block for a freshly-seeded dsl-course.yml. With no
    admins given, ships fully commented out (today's default, uncomment-what-you-want
    UX). Given admins (from bootstrap's --admins), seeds them LIVE (uncommented) - so
    they're declared in the SSOT from day one, not just given a one-time direct team
    invite (add_course_admins) that the next sync would otherwise revert for not
    being declared here."""
    header = template("course/people-header.yml")
    cards = template("course/people-cards.yml")
    if not admins:
        return f"{header}\n{template('course/people-commented.yml')}\n{cards}"
    entries = "\n".join(
        f'    - github_handle: "{a}"    # grants the `course-admin` team'
        for a in admins
    )
    return f"{header}people:\n  course_admins:\n{entries}\n\n{cards}"


def _course_metadata(
    org: str,
    org_name: str,
    course_name: str,
    course_code: str,
    admins: list[str] | None = None,
) -> str:
    """dsl-course.yml for the persistent COURSE org: identity + course_admins (the
    single source of truth for course-wide admin access, mirrored into every cohort
    org's own course-admin team by sync_faculty). Instructors/TAs and the schedule
    both stay per-cohort instead (they change year to year and, for instructors/TAs,
    usually the people too)."""
    identity = template("course/dsl-course.yml").format(
        org=org,
        org_name=org_name,
        course_name=course_name,
        course_code=course_code or "",
    )
    return identity + _course_admins_block(admins)


def _cohort_metadata(org: str, course: str) -> str:
    """dsl-course.yml for a COHORT org's .github repo: a pointer back to its persistent
    course org. This is the single source the cohort's classroom-config dispatchers
    (dispatch-sync / dispatch-sync-site) read to find where to fire Sync membership /
    Sync site - so without it those auto-triggers can't resolve the course org."""
    return template("cohort/dsl-course.yml").format(course=course, org=org)


def create_profile_repo(
    org: str,
    org_name: str,
    course_name: str,
    course_code: str = "",
    *,
    is_cohort: bool = False,
    admins: list[str] | None = None,
) -> None:
    """Create the .github profile repo with README, and (course orgs only) course
    metadata.

    Also tags the repo with `dsl-course-hub` so `list_orgs.py` can discover it.

    The course org's dsl-course.yml carries identity + the faculty roster. A cohort org
    instead gets a tiny `.github/dsl-course.yml` pointer back to its course org (written
    in main()'s cohort wiring via _cohort_metadata, once --course is known) - the
    classroom-config dispatchers read its `course:` line. Its schedule lives in
    classroom-config/schedule.yml. `admins` (course org only) seeds dsl-course.yml's
    people.course_admins live from the start - see _course_admins_block.
    """
    log_step("Setting up .github profile repo")
    # Opposite instructions to the same reader, so the description says which: a cohort
    # org's `.github` is machine-owned scaffolding, a course org's is where faculty work -
    # it holds dsl-course.yml and every workflow they run.
    if not create_repo(
        org,
        ".github",
        private=False,
        description=(
            "[do not touch]: Org profile and configuration"
            if is_cohort
            else "[control panel]: Org profile & configuration"
        ),
    ):
        return

    if not is_cohort:
        # Course metadata - canonical machine-readable source for discovery tooling, and
        # the SSOT faculty edit (people.course_admins / instructor cards), so it is
        # USER-owned: seeded once, never rewritten by a later repair run.
        # (The org-overview profile/README.md is generated at the end of bootstrap,
        # once all repos exist, by seed.update_profile_readme - see main.)
        metadata = _course_metadata(org, org_name, course_name, course_code, admins)
        seed_if_absent(
            org,
            ".github",
            "dsl-course.yml",
            metadata.encode(),
            "init: course metadata for DSL discovery tooling",
        )

    set_repo_topics(org, ".github", _profile_topics(is_cohort, course_code))

    log_ok(".github profile repo initialised")


def set_org_settings(org: str) -> None:
    """Set org-level settings: 2FA, Pages, base permissions."""
    log_step("Configuring org settings")

    # Require 2FA for all members (best practice for course orgs)
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"orgs/{org}",
        "--field",
        "two_factor_requirement_enabled=true",
    )
    # Set default Pages branch to main (if not present, Pages will use default on first enable)
    # Note: pages_build_type is set per-repo, not org-wide
    if code == 0:
        log_ok("org settings configured (2FA enforced)")
    else:
        log_err(f"could not enable 2FA: {out[:100]}")


def validate_secret_presence(org: str, secret_name: str) -> bool:
    """Check if an org secret exists (non-destructive check)."""
    # gh api doesn't expose secret listing without auth headers, so we check by trying
    # to read the secret value (which will 404 if it doesn't exist)
    code, _ = gh("api", f"orgs/{org}/actions/secrets/{secret_name}")
    exists = code == 0
    if exists:
        log_ok(f"org secret found: {secret_name}")
    else:
        log_err(f"org secret missing: {secret_name}")
    return exists


def setup_cohort_extras(org: str) -> int:
    """Cohort-only: tighten the org and seed the student-facing repos.

    Layered on top of the common bootstrap when --cohort is passed:
    - safe-by-default permissions (members get no repo access unless granted);
    - public `welcome` repo with the Join issue form + onboard workflow;
    - private `classroom-config` repo with a starter students.csv;
    - the faculty teams' standing grant on both of those repos.
    The `materials` repo is created on the first release, so it's not made here.

    Safe to re-run on a LIVE cohort: the USER-owned classroom-config files (roster,
    schedule, people, grades) are only ever created, never rewritten, while the
    SYSTEM-owned workflows refresh. See the ownership note at the top of this file.

    Returns the number of student-facing workflow/sample writes that failed, so a cohort
    left half-seeded (onboarding workflow or config samples never landed) reds the
    bootstrap rather than reporting success.
    """
    log_step("Cohort setup: tighten org + seed welcome/classroom-config")

    failures = 0
    create_cohort_teams(org)

    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"orgs/{org}",
        "--field",
        "default_repository_permission=none",
        "--field",
        "members_can_create_repositories=false",
    )
    if code == 0:
        log_ok(
            "org tightened (default_repository_permission=none, no member repo creation)"
        )
    else:
        # Without this PATCH the cohort stays open (members get default repo access) - a
        # real misconfiguration, so it must red the bootstrap, not just log and pass.
        failures += 1
        log_err(f"could not tighten org settings: {out[:120]}")

    # NB: this block (and the classroom-config one below) runs on EVERY bootstrap, re-runs
    # included - create_repo reports an existing repo as success. That is deliberate for
    # SYSTEM-owned files (they refresh so fixes reach running cohorts); USER-owned files are
    # protected per-file by utils.seed_if_absent. See the ownership note at the top of this file.
    # A failed create_repo (post-PR1, a genuine failure, not the idempotent 422) leaves the
    # cohort with no student-facing front door, so it must red the bootstrap and skip the
    # seeding rather than the create's False being silently dropped by a bare `if`.
    if not create_repo(
        org,
        "welcome",
        private=False,
        description="Course front door - open a Join issue to enrol",
    ):
        failures += 1
        log_err(
            f"could not create the welcome repo in {org} - students have no front door"
        )
    else:
        welcome_failures = refresh_welcome_workflows(org)
        if welcome_failures:
            failures += welcome_failures
            log_err(
                f"the welcome repo in {org} is not fully seeded - re-run Bootstrap "
                f"cohort (or wait for the nightly Refresh) once the cause is cleared"
            )
        # The landing page a student sees on this public repo: what to do, and how. Its
        # link back to the issue chooser is org-specific, so the template carries `{org}`.
        # USER-owned (it is the cohort's front door, and faculty may reword it), so
        # create-only - a repair re-run must not clobber their edits.
        if not seed_if_absent(
            org,
            "welcome",
            "README.md",
            template("welcome/README.md").format(org=org).encode(),
            "docs: seed welcome README (how to join)",
        ):
            failures += 1

    # A failed create_repo here leaves the cohort with no roster/schedule/dispatcher repo -
    # membership sync never triggers - so count it and skip the seeding rather than drop the
    # False on a bare `if`.
    if not create_repo(
        org,
        "classroom-config",
        private=True,
        # Instructors and course-admin hold it and nobody else does: the cohort org sets
        # default_repository_permission=none, so a student is not a reader of this by
        # default - it does not appear in their repo list at all.
        description=(
            "[visible to instructors only]: Everything you configure for this cohort "
            "is here - student roster, teams, term schedule, and marking. Students "
            "never see it, and no PII leaves this repo."
        ),
    ):
        failures += 1
        log_err(f"could not create the classroom-config repo in {org}")
    else:
        # USER-owned files are create-only: this repo holds the cohort's LIVE state - the
        # roster with enrol codes and onboarded handles, the schedule the scheduler
        # releases from, this cohort's people.yml, and returned grades. Re-running
        # "Bootstrap cohort" mid-semester must leave all of it exactly as faculty (and the
        # onboarding/enrol-code/grade flows) left it.
        tag, year = _cohort_tag(org)
        # The scaffolds: minimal, mostly-commented skeletons faculty fill in. Header-only
        # CSVs carry the full schema (roster.FIELDS / teams.FIELDS); the YAML scaffolds
        # carry structure + one-line field notes. Every filled example lives in the
        # `.sample` twin seeded below, so none of these has to double as documentation.
        # Rendering is uniform - the CSV scaffolds carry no `{placeholders}`, so one
        # `.format` over the whole table keeps the YAML examples tag-aware (this cohort's
        # fYYYY/sYYYY, so they are copy-paste-correct) without a per-file special case.
        # One commit for the set: seeding a cohort's config is a single act, and writing it
        # file by file put a burst of near-identical `init:`/`docs: seed` commits at the top
        # of a repo faculty then work in by hand. Create-only is unchanged and still per
        # file - a re-run that finds five of six present writes only the sixth.
        if not seed_files_if_absent(
            org,
            "classroom-config",
            {
                path: template(rel)
                .format(tag=tag, year=year, year_next=year + 1)
                .encode()
                for path, rel in CLASSROOM_SCAFFOLDS.items()
            }
            | {"grades/.gitkeep": b""},
            "init: classroom-config scaffolds (roster, teams, schedule, people, grades)",
        ):
            failures += 1
        # SYSTEM-owned documentation, refreshed on every run so it never goes stale: a
        # `.sample` twin for every file in the worked example cohort. Samples keep the
        # `.sample` suffix so the engine (sync_membership, sync_teams, grade sync) never
        # ingests them - only the real names; activation = copying rows into the real file.
        sample_failures = refresh_classroom_samples(org)
        if sample_failures:
            failures += sample_failures
            log_err(
                f"the classroom-config samples in {org} are not fully seeded - re-run "
                f"Bootstrap cohort (or wait for the nightly Refresh)"
            )
        # SYSTEM-owned contract + dispatchers: refreshed on every run so fixes reach
        # running cohorts - and, since they live in welcome.py, on every nightly
        # seed.refresh too, so a cohort no longer waits for someone to run this by hand.
        # A failed dispatcher write means membership/site sync never triggers, so count it.
        failures += refresh_classroom_system_files(org)

    # Faculty access on the two repos just seeded - unconditional (not inside the
    # create_repo blocks above), so a re-run repairs an org that predates this.
    grant_cohort_faculty_access(org)

    # Public, auto-deployed cohort website (from course-website-template).
    failures += scaffold.scaffold_site(org)

    return failures


def seed_workflows(org: str) -> int:
    """Seed the org-level workflows into the course org's .github repo. The full set
    (central Release materials/assignment + Sync membership/Bootstrap-cohort/Refresh) is
    rendered by dsl_course.seed (single source of truth).

    Returns the number of writes that failed - a workflow that never landed (e.g. the token
    lost `workflow` scope) is exactly what a green bootstrap must not hide."""
    return seed.seed_github_workflows(org)


def preflight(org: str) -> bool:
    """Verify the org exists AND the bot can administer it before configuring anything.

    GitHub has NO API to create an organisation (github.com); it must be created in the
    web UI first, with the bot added as an Owner. A token that is only a *pending* or
    member-level org member can READ the org but every create call 403s - so check for an
    active Owner up front and stop with actionable instructions, rather than 403-ing
    through every step and falsely reporting success.
    """
    log_step(f"Preflight: checking org {org} + bot permissions")
    code, _ = gh("api", f"orgs/{org}", "--jq", ".login")
    if code != 0:
        log_err(f"org '{org}' not found or not accessible by this token.")
        log(
            "\nGitHub cannot create an organisation via API - create the empty org "
            "first, then re-run:\n"
            "  1. Create it:  https://github.com/organizations/new\n"
            "  2. Add the DSL bot account as an org Owner (so this automation can configure it).\n"
            f"  3. Re-run bootstrap with --org {org}.\n"
        )
        return False
    # The token must be an ACTIVE OWNER. A pending/invited or member-level token reads the
    # org fine but cannot create the .github repo, role teams, or org secret (all 403).
    bot = gh("api", "user", "--jq", ".login")[1].strip() or "the bot"
    code, membership = gh(
        "api", f"user/memberships/orgs/{org}", "--jq", '.state + "/" + .role'
    )
    membership = membership.strip() if code == 0 else "not a member"
    if membership != "active/admin":
        log_err(f"@{bot} cannot administer {org} (membership: {membership}).")
        log(
            f"\nThe bot must be an ACTIVE OWNER of {org} - creating the .github repo, role "
            f"teams, and org secret all require Owner. Fix by the matching case, then re-run:\n"
            f"  - 'pending/admin'  -> @{bot} was invited but hasn't accepted: sign in as "
            f"@{bot} and accept at https://github.com/orgs/{org}/invitation\n"
            f"  - 'not a member'   -> invite @{bot} to {org} as Owner, then accept as @{bot}\n"
            f"  - 'active/member'  -> promote @{bot} to Owner in the org's People page\n"
        )
        return False
    log_ok(f"org {org} accessible; @{bot} is an active owner")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True, help="Course org to bootstrap")
    parser.add_argument(
        "--org-name",
        default=None,
        help="Full org name for README (e.g. 'Deep Learning'). "
        "If not set, uses --org as-is.",
    )
    parser.add_argument(
        "--course-name",
        default=None,
        help="Course name for README (e.g. 'Deep Learning (GRAD-E1394)'). "
        "If not set, uses --org-name.",
    )
    parser.add_argument(
        "--course-code",
        default="",
        help="Hertie course code (e.g. 'GRAD-E1394'). Stored in "
        ".github/dsl-course.yml and set as a repo topic on .github.",
    )
    parser.add_argument(
        "--set-secret",
        default=None,
        help="Path to file containing DSL_BOT_TOKEN PAT. "
        "If provided, sets the org secret. Otherwise, validates presence only.",
    )
    parser.add_argument(
        "--cohort",
        action="store_true",
        help="Also do cohort student-facing setup: tighten the org and seed the "
        "welcome (onboard) + classroom-config (roster) repos.",
    )
    parser.add_argument(
        "--course",
        default=None,
        help="With --cohort: the parent course org. Registers this cohort in that "
        "course's .github/dsl-course.yml so it appears in the faculty & instructors dropdowns.",
    )
    parser.add_argument(
        "--propagate-secret",
        action="store_true",
        help="Set DSL_BOT_TOKEN on this org to the DSL_BOT_TOKEN env value "
        "(lets the central bootstrap auto-provision the token - no manual per-org step).",
    )
    parser.add_argument(
        "--admins",
        default="",
        help="GitHub handle(s) of this course's admin(s), comma/space-separated. Added to "
        "the course-admin team (admin on .github) so they can run the workflows - and, on "
        "a course-org bootstrap, declared in dsl-course.yml's SSOT so a later sync doesn't "
        "revert it. Each accepts an org invite once. Add instructors/TAs later via the "
        "org's Teams page.",
    )
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return _run(args)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


def _run(args: argparse.Namespace) -> int:
    """The bootstrap itself, in order: preflight, org settings, teams, repos, secret,
    profile README. Split from main so the whole sequence sits under one guard."""
    org_name = args.org_name or args.org
    course_name = args.course_name or org_name
    admin_logins = _parse_handles(args.admins)
    # Sub-steps that write machinery (workflows, welcome/config seeds, the cohort
    # registration, the faculty sync) each report a failure count; a half-configured org
    # must exit non-zero, so they are threaded here rather than logged and dropped.
    failures = 0

    log(f"Bootstrapping org: {args.org}")
    log(f"  Org name: {org_name}")
    log(f"  Course name: {course_name}")

    # 0. Preflight - the org must already exist (GitHub can't create one via API).
    if not preflight(args.org):
        return 1

    # 1. Org settings
    set_org_settings(args.org)

    # 2. Default teams
    create_default_teams(args.org)

    # 3. Profile repo (course org only - identity + faculty roster; a cohort org
    # gets no dsl-course.yml, its config all lives in classroom-config). --admins is
    # seeded into the SSOT here (course org only - see _course_admins_block) as well
    # as given a one-time direct team invite below (add_course_admins), so the next
    # sync doesn't undo that invite.
    create_profile_repo(
        args.org,
        org_name,
        course_name,
        args.course_code,
        is_cohort=args.cohort,
        admins=admin_logins if not args.cohort else None,
    )

    # 3b. Course vs cohort wiring.
    if args.cohort:
        # Cohort: student-facing welcome + roster + tightened perms.
        failures += setup_cohort_extras(args.org)
        if args.course:
            # Pointer back to the course org, in this cohort's .github/dsl-course.yml -
            # the classroom-config dispatchers read its `course:` line to know where to
            # fire Sync membership / Sync site. Without it those auto-triggers fail.
            #
            # SYSTEM-owned (see the ownership note at the top of this file): the file is
            # wholly generated from --org/--course and carries no faculty-authored content
            # (a cohort's identity lives in the course org's dsl-course.yml, its schedule in
            # classroom-config/schedule.yml), so refreshing it is what repairs a cohort
            # bootstrapped before this pointer existed. Unlike the COURSE org's
            # dsl-course.yml, which is the faculty SSOT and therefore create-only.
            # A failed write leaves the classroom-config dispatchers unable to resolve the
            # course org, so Sync membership / Sync site never fire - count it into the exit.
            if not put_file(
                args.org,
                ".github",
                "dsl-course.yml",
                _cohort_metadata(args.org, args.course).encode(),
                "ci: seed cohort -> course pointer (dispatchers read this)",
            ):
                failures += 1
                log_err(
                    f"could not seed the cohort -> course pointer in {args.org}/.github - "
                    f"the classroom-config dispatchers cannot resolve {args.course}"
                )
            # register_cohort returns False on a failed registry write. A cohort that is
            # invisible to discover_cohorts is invisible to every nightly sync, so a claimed
            # -but-unregistered cohort must red the bootstrap rather than proceed silently.
            if not seed.register_cohort(args.course, args.org):
                failures += 1
                log_err(
                    f"could not register {args.org} in {args.course}'s cohort registry - "
                    f"it will be missing from the faculty dropdowns and every nightly sync"
                )
            # Give this cohort the course's current, currently-active faculty roster
            # from day one (instructors/course-admin), rather than waiting for the
            # next push/cron sync. Scoped to just this cohort (cohorts=[args.org]) so
            # bootstrapping one more cohort doesn't re-touch every already-registered one.
            failures += sync_faculty.sync(args.course, cohorts=[args.org])
            # Populate + prune + wire the freshly-scaffolded site from the org structure.
            # This ONE sync is what replaces the website template's placeholders ("Fall
            # 2025", "Course Name (Code)") with this course's identity and the cohort's
            # inferred semester - an empty/commented schedule.yml is enough, dates are
            # synthesised. Without it a fresh cohort site shows the template until the
            # first successful "Sync site", which may be a while (or never).
            #
            # Best effort: Pages provisioning can lag right behind repo creation, and a
            # hiccup here must not fail a bootstrap that has already configured the org -
            # a bootstrap re-run or the "Sync site" workflow repairs it.
            try:
                if site.sync_site(args.course, args.org) != 0:
                    log_err(
                        f"initial site sync incomplete for {args.org} - re-run "
                        '"Sync site" (Pages may still have been provisioning).'
                    )
            except Exception as exc:
                log_err(
                    f"initial site sync failed for {args.org} ({exc}) - re-run "
                    '"Sync site"; the rest of the bootstrap is unaffected.'
                )
        else:
            log(
                f"  (no --course given - add {args.org} to its course org's "
                f".github/{seed.COHORTS_PATH} to show it in the faculty & instructors dropdowns)"
            )
    else:
        # Course: seed the org-level workflows (incl. the central Release actions) into .github.
        failures += seed_workflows(args.org)

    # 3c. Workflow access: grant this course's own instructors/course-admin teams write/admin
    # on .github (without it only the org owner can run the workflows), then seed the named
    # admin(s) into course-admin. Access is per-course - central DSL faculty/admin are a
    # separate concern (who may bootstrap), not auto-added here.
    grant_button_access(args.org)
    add_course_admins(args.org, args.admins)

    # 4. Secret (set or validate)
    if args.set_secret:
        try:
            with open(args.set_secret) as f:
                token = f.read().strip()
            # An empty/whitespace file used to write an EMPTY org secret and report
            # success - every seeded workflow then fails with "set the GH_TOKEN
            # environment variable" weeks later, with a green bootstrap behind it.
            if not token:
                log_err(f"secret file is empty: {args.set_secret}")
                failures += 1
            elif not set_org_secret(args.org, "DSL_BOT_TOKEN", token):
                failures += 1
        except (OSError, FileNotFoundError) as e:
            log_err(f"could not read secret file: {e}")
            return 1
    elif args.propagate_secret:
        # Copy the bot token onto this org so its seeded workflows can run. Lets the
        # central bootstrap auto-provision the secret - no per-course manual step.
        #
        # Only DSL_BOT_TOKEN is published. A maintainer running this by hand usually has
        # their PERSONAL GH_TOKEN exported; publishing that as the org secret would hand
        # their PAT to every workflow in the infra repos - a wider blast radius than the
        # repo secret seed.py already refuses to leak - so if only GH_TOKEN is set we
        # refuse, and the refusal counts as a failed step rather than a silent no-op.
        token = os.environ.get("DSL_BOT_TOKEN")
        if token:
            if not set_org_secret(args.org, "DSL_BOT_TOKEN", token):
                failures += 1
        elif os.environ.get("GH_TOKEN"):
            log_err(
                "DSL_BOT_TOKEN not set (only GH_TOKEN is) - refusing to publish a personal "
                "token as the DSL_BOT_TOKEN org secret; set DSL_BOT_TOKEN to propagate it."
            )
            failures += 1
        else:
            log_err("--propagate-secret set but no DSL_BOT_TOKEN in env")
            failures += 1
    else:
        # Validate the secret exists (it should have been set manually or by another bootstrap run)
        if not validate_secret_presence(args.org, "DSL_BOT_TOKEN"):
            log(
                "\nWARNING: DSL_BOT_TOKEN not set. "
                "Run bootstrap with --set-secret <path> to add it, "
                f"or set it manually at https://github.com/{args.org}/settings/secrets/actions"
            )

    # 5. Generate the org-overview README now that all repos exist (clickable index).
    seed.update_profile_readme(args.org, org_name, course_name)

    if admin_logins and not args.cohort:
        admins_step = (
            f"2. Course admins ({', '.join(admin_logins)}) are already declared in the "
            f"`people:` block of {args.org}/.github/dsl-course.yml - nothing to do here. "
            "Add more later by editing that file directly (not the Teams page - "
            '"Sync membership" reconciles the `course-admin` team FROM that file, so an '
            "undeclared manual addition gets reverted on the next sync). Instructors/TAs "
            "are declared per cohort instead, in that cohort's own "
            "classroom-config/people.yml (see step 4)."
        )
    else:
        admins_step = (
            f"2. Declare THIS course's course_admins in the `people:` block of "
            f'{args.org}/.github/dsl-course.yml, then push - "Sync membership" reconciles '
            "the `course-admin` team automatically (here and into every cohort's own "
            "course-admin team; no manual Teams-page edit needed). Instructors/TAs are "
            "declared per cohort instead, in that cohort's own classroom-config/people.yml "
            "(see step 4)."
        )
    log(f"""
============================================================
Course org bootstrap complete: {args.org}

DONE (automated):
============================================================
- Faculty teams: instructors, course-admin (students + auditors are created per cohort)
- Org settings: 2FA enforcement enabled
- .github profile repo with README
- Workflows in .github: Release materials, Release assignment, Sync membership,
  Bootstrap cohort, Refresh actions
- DSL_BOT_TOKEN secret validated (or set)
- Workflow access: instructors (write) + course-admin (admin) granted on .github; any
  --admins handles added to course-admin (they accept the org invite once, then the
  workflows appear in their Actions tab) and declared in dsl-course.yml's SSOT

NEXT STEPS (manual):
============================================================

1. Review org settings: https://github.com/{args.org}/settings

{admins_step}

3. Put content in the materials repo (any top-level dir with ordinal-prefixed
   subdirectories, e.g. lectures/01_.../, readings/01_.../) and create
   assignment-N-f2026 template repos, then run "Refresh actions" so they appear in the
   dropdowns. Run Release materials/assignment from inside the materials repo's Actions tab.

4. Add a cohort: create the empty cohort org, add the bot as owner, then run the
   "Bootstrap cohort" action here with its name (configures + registers + refreshes).

NB: cohort orgs are made the same way - create the empty org, add the bot as owner,
then run bootstrap with --cohort (seeds welcome + roster + tightens perms).
============================================================
""")

    if args.cohort:
        log(
            "COHORT extras done:\n"
            f"- org tightened (default_repository_permission=none)\n"
            f"- welcome repo (public): Join issue form + onboard workflow\n"
            f"- classroom-config repo (private): starter students.csv "
            f"(edit https://github.com/{args.org}/classroom-config/blob/HEAD/students.csv with registrar data), "
            f"plus schedule.yml and people.yml (this cohort's calendar/due-dates and "
            f"instructors/TAs - both seeded mostly-commented, uncomment what you want)\n"
            f"- faculty access: instructors (write) + course-admin (admin) on welcome and "
            f"classroom-config, so non-owner faculty can edit the roster/schedule and "
            f"triage onboarding issues\n"
        )

    if failures:
        log_err(
            f"bootstrap incomplete: {failures} configuration step(s) failed - re-run "
            f"once the cause is cleared (Actions log above has the failing write(s))"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
