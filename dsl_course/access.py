"""Who can read and write each repo: the team-permission grants, the faculty floor a
sweep converges every repo back up to, and the machinery topics that go with them.
"""

from __future__ import annotations

import json

from .course import (
    AUDITORS_TEAM,
    COURSE_ADMIN_TEAM,
    GRADEBOOK_PREFIX,
    INSTRUCTORS_TEAM,
    STUDENTS_TEAM,
)
from .discovery import classify_repos
from .gh_teams import create_team
from .ghcli import gh, is_missing_resource
from .log import log, log_err, log_ok
from .repos import Converged, set_repo_topics


def grant_team_repo_access(
    org: str, team: str, repo: str, permission: str, *, missing_is_note: bool = False
) -> bool:
    """Grant a team a permission level on one repo (idempotent).

    `missing_is_note`: a team that does not exist yet is logged as a note, not an error -
    an org can be released into before its teams exist, and the next release or sync
    fixes it. Any OTHER failure (a 5xx, a rate limit) stays an error either way; it used
    to read as "team not found" on the read-teams path, which hid real outages."""
    code, out = gh(
        "api",
        "-X",
        "PUT",
        f"orgs/{org}/teams/{team}/repos/{org}/{repo}",
        "-f",
        f"permission={permission}",
    )
    if code == 0:
        return True
    if missing_is_note and is_missing_resource(out):
        log(f"  ({team} team not found - create it first)")
        return False
    log_err(f"  ! could not grant {team} {permission} on {org}/{repo}: {out[:120]}")
    return False


# The course-org faculty teams that get standing access to course repos: instructors run
# releases day-to-day (write), course-admin manage (admin). Applied to `.github` at bootstrap
# and to every scaffolded materials/assignment repo, so faculty & instructors can push content without an
# owner hand-granting each new repo.
COURSE_TEAM_ACCESS = {INSTRUCTORS_TEAM: "push", COURSE_ADMIN_TEAM: "admin"}

# Faculty access to a repo they should READ but not edit: the RELEASED copy of materials,
# and a student's gradebook. Both have a source of truth elsewhere, so a hand edit here is
# not durable and looks like one that stuck:
#   - a re-release copies over the released copy (`copytree(dirs_exist_ok=True)`), so a
#     correction belongs in the course org's materials repo, then re-release
#   - `distribute` rewrites a gradebook's grades.yml from
#     `classroom-config/grades/<slug>.csv`, so a mark belongs in that CSV
# A submission repo is read for the same reason: marking happens in
# `classroom-config/grades/<slug>.csv`, and by then the deadline snapshot has frozen its
# HEAD and the autograder has run off that snapshot, so a commit there would reach no
# gradebook and form no part of the record.
#
# What keeps WRITE is where faculty actually author: `classroom-config` (the grading CSVs,
# schedule.yml, people.yml, the roster), `welcome/README.md` (the students' front door,
# seeded create-only so faculty may reword it), and `.github` - that one because GitHub
# requires write on a repo to trigger a workflow_dispatch at all, which is what every
# faculty button is.
#
# `course-admin` stays admin throughout - it is the cohort's owner of last resort, and read
# access cannot fix a broken repo.
FACULTY_READ_ACCESS = {INSTRUCTORS_TEAM: "pull", COURSE_ADMIN_TEAM: "admin"}

# The cohort repos faculty AUTHOR in - the only cohort repos that get write. Everything else
# in a cohort org has its source of truth elsewhere and takes FACULTY_READ_ACCESS. `.github`
# is here because GitHub requires write on a repo to trigger a workflow_dispatch at all.
COHORT_WRITE_REPOS = frozenset({".github", "welcome", "classroom-config"})

# GitHub's repo permissions, weakest first, in the vocabulary a PUT takes (`permission=`).
# A team-repos LISTING answers in a different one (`role_name`: read/write/...) - which is
# why the sweep reads the listing's `permissions` booleans instead; their keys are these.
_PERM_RANK = {"pull": 1, "triage": 2, "push": 3, "maintain": 4, "admin": 5}


def faculty_floor(
    repo: str, tier: str | None, protected: frozenset[str] = frozenset()
) -> dict[str, str]:
    """The faculty teams' MINIMUM grant on `repo`: write where faculty AUTHOR (every repo
    of a course org, the COHORT_WRITE_REPOS of a cohort), read everywhere else.

    `tier` is `discovery.org_tier`, and None - a listing that cannot place the org - reads
    as a cohort: only a listing that positively says "course" earns the write-everywhere
    floor. `protected` names the per-student repos (discovery.student_repo_names), which
    take the READ floor whatever the tier says - so a mis-told tier can under-grant a
    course org, but can never hand instructors push on a student's submission or
    gradebook."""
    if repo in protected:
        return FACULTY_READ_ACCESS
    if tier == "course" or repo in COHORT_WRITE_REPOS:
        return COURSE_TEAM_ACCESS
    return FACULTY_READ_ACCESS


def grant_faculty(
    org: str, repo: str, access: dict[str, str], *, missing_is_note: bool = False
) -> None:
    """Give the faculty teams `access` - COURSE_TEAM_ACCESS where they author,
    FACULTY_READ_ACCESS where the source of truth is elsewhere - on one repo, at the point
    it is created.

    `missing_is_note` for the per-student hot path (every gradebook, every submission
    repo): a cohort whose faculty teams are not there yet must not print two errors per
    student, and `converge_faculty_access` repairs the grant on the next sweep."""
    for team, perm in access.items():
        grant_team_repo_access(org, team, repo, perm, missing_is_note=missing_is_note)


def grant_tagged_team_access(course_org: str, repo: str, tag: str) -> None:
    """Give this tag's cohort-declared instructors team (`instructors-<tag>`) push
    access on `repo` - scoped to just that tag's own content, unlike the standing
    COURSE_TEAM_ACCESS grant every repo gets. No course-admin-<tag> variant: admin
    access stays on the single, course-wide `course-admin` team.

    Ensures the team exists first (idempotent) - callable in either order, whether
    a tag's content repo is scaffolded before or after its cohort first declares
    instructors."""
    team = f"{INSTRUCTORS_TEAM}-{tag}"
    create_team(course_org, team, f"Instructors for {tag} (cohort-declared)")
    grant_team_repo_access(course_org, team, repo, "push")


# The cohort-org role teams that get read on released content.
READ_TEAMS = (STUDENTS_TEAM, AUDITORS_TEAM)


def grant_read_teams(cohort_org: str, repo: str) -> None:
    """Give both cohort role teams read on a released repo.

    Auditors see exactly what enrolled students see once it's released - the split is
    assignments and grades, not content - so every release grant covers both teams. A
    missing team is a note, not an error: an org can be released into before its teams
    exist, and the next release (or Sync membership) fixes it."""
    for team in READ_TEAMS:
        if grant_team_repo_access(cohort_org, team, repo, "pull", missing_is_note=True):
            log_ok(f"{team} team -> read")


def _strongest_permission(permissions: dict) -> str | None:
    """The strongest TRUE flag of a listing's cumulative `permissions` object, in the PUT
    vocabulary. None when no flag we rank is set - the caller leaves that repo alone."""
    held = [p for p, on in permissions.items() if on and p in _PERM_RANK]
    return max(held, key=_PERM_RANK.__getitem__) if held else None


def team_repo_access(org: str, team: str) -> dict[str, str | None] | None:
    """`{repo: permission}` for every repo `team` holds - ONE paginated read, PUT vocabulary.

    None when the team does not exist (a 404): an org can be swept before its teams are
    created, and the next sweep picks it up. Not `{}` - that reads as "holds nothing", and
    the caller would then PUT on every repo in the org and 404 on each. Any other failure
    RAISES, on the same rule as every other listing here.

    Read from the `permissions` booleans, never `.role_name`: the listing's role names
    (`read`/`write`) are not the PUT vocabulary (`pull`/`push`), and ranking one in the
    other's table once read every instructor's write as "below read" and demoted them. A
    repo maps to None when its object sets no flag we rank; the caller must skip it."""
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{org}/teams/{team}/repos?per_page=100",
        "--jq",
        ".[] | {name, permissions: (.permissions // {})}",
    )
    if code != 0:
        if is_missing_resource(out):
            return None
        raise RuntimeError(f"could not read {org}/{team}'s repos: {out[:200]}")
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"unparseable repo listing for {org}/{team}: {out[:200]}"
        ) from exc
    return {r["name"]: _strongest_permission(r["permissions"]) for r in rows}


def converge_faculty_access(
    org: str,
    repos: list[dict],
    tier: str | None,
    protected: frozenset[str] = frozenset(),
) -> Converged:
    """Raise the faculty teams to their floor (`faculty_floor`) on every live repo of `org`.

    A team grant is set when a repo is created and never revisited, so a repo kind that
    predates its grant, or an org bootstrapped before one existed, keeps whatever it
    started with. Both org kinds run at default_repository_permission=none, so that grant
    is the WHOLE of a non-owner's access; every live faculty member being an org owner is
    the only reason it went unnoticed. This is the convergence path.

    A FLOOR, never a level: a repo already granted higher is left alone. Fail closed: a
    grant this sweep cannot rank is skipped, never read as "nothing" and overwritten.
    `tier` and `protected` are `faculty_floor`'s. Archived repos are skipped (GitHub
    refuses the PUT).

    Cost: `2 * ceil(N/100)` GETs for a converged org; the FIRST sweep of an unconverged
    org is one PUT per missing grant (a 300-repo cohort: ~600 sequential PUTs, which may
    trip the secondary rate limit and crawl through gh()'s backoff - it self-heals, the
    next night finishes)."""
    changed = 0
    failures = 0
    live = [r["name"] for r in repos if not r.get("archived")]
    for team in COURSE_TEAM_ACCESS:
        try:
            have = team_repo_access(org, team)
        except RuntimeError as exc:
            log(f"  ({exc})")
            continue
        if have is None:
            log(f"  (no {team} team in {org} yet - faculty access not converged)")
            continue
        for name in live:
            floor = faculty_floor(name, tier, protected)[team]
            # "" is "the team does not hold this repo at all"; None is "it holds it at a
            # level this sweep cannot rank", which is left exactly as it is.
            current = have.get(name, "")
            if current is None:
                log(f"  ({team} holds {name} at a level this sweep cannot rank - left)")
                continue
            if current and _PERM_RANK[current] >= _PERM_RANK[floor]:
                continue
            if grant_team_repo_access(org, team, name, floor):
                log_ok(f"{team} -> {floor} on {name}")
                changed += 1
            else:
                failures += 1
    return Converged(changed, failures)


def converge_topics(org: str, repos: list[dict], tier: str | None) -> Converged:
    """Stamp the machinery topics missing from a COHORT org's per-student repos.

    `submission` (plus the template's own name) on `<template>-<handle>`, `gradebook` on
    `grades-<handle>` - exactly what assign.py and grades.py stamp at creation. That stamp
    is a separate PATCH after the create, so any repo whose stamp failed, or that predates
    the topic, is permanently untagged; nothing ever revisited it. Untagged matters: the
    topics are what keep a student's submission repo and a private gradebook off the org
    landing page, out of the release targets, and on the READ floor of the faculty sweep.
    Both readers have a name rule as a backstop for exactly that reason, but a backstop is
    not a reason to leave the record wrong.

    ADDITIVE, and only where something is missing: the PUT replaces the whole topic list,
    so whatever else a repo carries is read off the listing and written back with it, and
    a repo already carrying its topics costs no call at all. Only a listing that says
    "cohort" is swept: a course org has neither repo kind, and an unplaceable one is not
    worth a PATCH per repo on a guess.

    Costs no reads (the caller's listing carries `topics` and `isTemplate`) and raises
    nothing: set_repo_topics logs its own failure, and this counts it."""
    if tier != "cohort":
        return Converged()
    derived = classify_repos(repos)
    changed = 0
    failures = 0
    for repo in repos:
        if repo.get("archived"):
            continue
        name = repo["name"]
        template = derived[name]
        if template is not None:
            wanted = {template, "submission"}
        elif name.startswith(GRADEBOOK_PREFIX):
            wanted = {"gradebook"}
        else:
            continue
        have = set(repo.get("topics") or [])
        if wanted <= have:
            continue
        if set_repo_topics(org, name, sorted(have | wanted)):
            log_ok(f"topics converged on {name}")
            changed += 1
        else:
            failures += 1
    return Converged(changed, failures)
