"""dsl-course sync-faculty -- materialise course-admin (course-wide) and
instructors/TAs (per-cohort) team membership.

Two independent flows, split by role rather than by "stability":

- `course_admins` - genuinely course-wide (course director / permanent admin, needs
  admin rights everywhere). Declared ONCE on the persistent COURSE org's
  `.github/dsl-course.yml` `people:` block - the SSOT, reconciled into the course
  org's own `course-admin` team AND mirrored into every cohort org's own
  `course-admin` team. Unchanged from the original course-org-SSOT design.
- `instructors`/`teaching_assistants` - genuinely cohort-scoped (most cohorts have
  different lecturers/TAs). Declared PER COHORT, in that cohort's own
  `classroom-config/people.yml` (see `load_cohort_faculty`) - reconciled into that
  cohort's own `instructors` team, AND synced UP into a parallel, tag-scoped
  `instructors-<tag>` team on the COURSE org (push access on just that tag's
  content repos, PLUS the central `.github` repo so its members can also use the
  central dispatch workflows), so a cohort's own people can push materials without a
  course-level declaration. No merge/union across cohorts - each cohort's tag gets
  its own team, so there's no "which cohort wins" ambiguity and no
  accumulate-forever list.

Each person entry requires `github_handle` (the only field that grants access);
`start`/`end` (optional ISO dates) bound when they're active, giving auto-rotation
with no manual removal step. Every reconcile here is FULL (add + remove) - a lapsed
`end` date or a deleted entry revokes access on the next sync, same as an edit to
students.csv/teams.csv.

Usage:
    python3 -m dsl_course.sync_faculty --course-org hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from .course import CONFIG_REPO, active_today, term_tag
from .discovery import (
    discover_assignments,
    discover_cohorts,
    discover_content_repos,
)
from .utils import (
    create_team,
    grant_team_repo_access,
    is_valid_github_username,
    load_yaml_config,
    log,
    log_err,
    log_ok,
    log_step,
    reconcile_team_members,
)

ROLE_TEAM = {
    "instructors": "instructors",
    "teaching_assistants": "instructors",
    "course_admins": "course-admin",
}
COHORT_PEOPLE_PATH = "people.yml"


# --------------------------------------------------------------------------- pure core


def parse_faculty_from_meta(meta: dict) -> dict[str, list[dict]]:
    """Parse an already-loaded config mapping's `people:` block (course org's
    dsl-course.yml, or a cohort's people.yml - same schema) for the roles in ROLE_TEAM.
    Only entries with a `github_handle` grant access; a named entry without one is a
    legitimate display-only card (noted, not an error), anything else is junk (flagged)."""
    people = meta.get("people")
    if not isinstance(people, dict):
        return {}
    faculty: dict[str, list[dict]] = {}
    for role in ROLE_TEAM:
        entries = []
        for p in people.get(role) or []:
            if isinstance(p, dict) and p.get("github_handle"):
                entries.append(p)
            elif isinstance(p, dict) and p.get("name"):
                log(
                    f"  ({role} entry '{p['name']}' has no github_handle - "
                    f"display-only, no access granted)"
                )
            else:
                log_err(f"  ! skipping {role} entry with no github_handle: {p!r}")
        faculty[role] = entries
    return faculty


def desired_team_members(
    faculty: dict[str, list[dict]], today: str
) -> dict[str, set[str]]:
    """Flatten active entries (per `today`, an ISO date string) via ROLE_TEAM ->
    {'instructors': {handles}, 'course-admin': {handles}}."""
    desired: dict[str, set[str]] = {team: set() for team in set(ROLE_TEAM.values())}
    for role, entries in faculty.items():
        team = ROLE_TEAM[role]
        for p in entries:
            if not active_today(p.get("start"), p.get("end"), today):
                continue
            handle = p["github_handle"]
            # Adding a faculty handle to a team also INVITES it to the org, so a typo'd
            # handle would invite a stranger with push on `.github`. There is no roster to
            # intersect faculty against, so charset-validate at minimum and skip anything
            # that can't be a real GitHub username rather than inviting it.
            handle = str(handle)  # an unquoted YAML handle may parse to int/bool
            if not is_valid_github_username(handle):
                log_err(
                    f"  ! {role} github_handle {handle!r} is not a valid GitHub "
                    f"username - skipping (not inviting)"
                )
                continue
            desired[team].add(handle)
    return desired


def _desired_for(faculty: dict[str, list[dict]], team: str, today: str) -> set[str]:
    """This team's desired active handles from a parsed faculty dict."""
    return desired_team_members(faculty, today).get(team, set())


def _cohort_roles_only(faculty: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """A cohort's people.yml declares instructors/TAs only - course_admins stays
    exclusively course-level, so drop it even if someone puts it there."""
    return {
        role: entries for role, entries in faculty.items() if role != "course_admins"
    }


def _matches_tag(repo: str, tag: str) -> bool:
    """Whether `repo` belongs to this year's tag (e.g. `course-materials-f2026`,
    `assignment-1-f2026` both match `f2026`)."""
    return repo.endswith(f"-{tag}")


def _tag_repos(content_repos: list[str], assignments: list[str], tag: str) -> list[str]:
    """Repos matching `tag` from the course org's already-discovered content/
    assignment repos, plus the central `.github` repo - what `instructors-<tag>`
    needs push access to so its members can use both the run-from-repo and central
    dispatch workflows (`.github` is cross-cohort infrastructure, not itself
    tag-scoped)."""
    matching = [r for r in content_repos if _matches_tag(r, tag)] + [
        r for r in assignments if _matches_tag(r, tag)
    ]
    return [".github"] + matching


# ---------------------------------------------------------------------- gh/git wiring


def load_faculty(course_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse the course org's `.github/dsl-course.yml` `people:` block -
    course_admins only in practice; instructors/TAs are declared per cohort
    (see `load_cohort_faculty`), but any stray entries here are still parsed
    (and reconciled) the same way `parse_faculty_from_meta` always has.

    Returns None when dsl-course.yml is genuinely ABSENT - the caller must then NOT prune
    (an absent config is not an empty desired set). A present-but-empty `people:` block
    parses to {} and legitimately empties the team."""
    meta = load_yaml_config(course_org, ".github", "dsl-course.yml")
    if meta is None:
        return None
    return parse_faculty_from_meta(meta)


def load_cohort_faculty(cohort_org: str) -> dict[str, list[dict]] | None:
    """Fetch + parse this cohort's own classroom-config/people.yml - instructors/TAs
    only (no course_admins key here; that role stays exclusively course-level).

    Returns None when people.yml is genuinely ABSENT (do not prune); a present-but-empty
    people block parses to {} and legitimately empties the team."""
    meta = load_yaml_config(cohort_org, CONFIG_REPO, COHORT_PEOPLE_PATH)
    if meta is None:
        return None
    return _cohort_roles_only(parse_faculty_from_meta(meta))


def sync_course_admins(
    course_org: str, cohorts: list[str], dry_run: bool = False
) -> int:
    """course_admins: declared once on the course org, mirrored unchanged into the
    course org itself and every cohort's own course-admin team."""
    faculty = load_faculty(course_org)
    if faculty is None:
        # ABSENT dsl-course.yml: parsing it as "" would yield an empty desired set and a
        # pruning reconcile would strip course-admin from EVERY org. Refuse to prune.
        log_err(
            f"course identity {course_org}/.github/dsl-course.yml is absent - refusing "
            f"to reconcile course-admin (an absent config would prune every admin from "
            f"{1 + len(cohorts)} org(s)); skipping"
        )
        return 1
    desired = _desired_for(faculty, "course-admin", date.today().isoformat())
    errors = 0
    for org in [course_org] + cohorts:
        errors += reconcile_team_members(
            org, "course-admin", desired, prune=True, dry_run=dry_run
        )
    return errors


def sync_cohort_instructors(
    course_org: str,
    cohort_org: str,
    content_repos: list[str],
    assignments: list[str],
    dry_run: bool = False,
) -> int:
    """instructors/TAs: declared in this cohort's own classroom-config/people.yml,
    reconciled into that cohort's own `instructors` team AND a parallel, tag-scoped
    `instructors-<tag>` team on the course org - no merge with any other cohort.
    `content_repos`/`assignments` are the course org's discovered repos, passed in
    (rather than re-discovered here) so a multi-cohort `sync()` fetches them once,
    not once per cohort."""
    faculty = load_cohort_faculty(cohort_org)
    if faculty is None:
        # ABSENT people.yml: reconciling an empty desired set with prune=True would strip
        # this cohort's instructors team (and its course-org tag team). Refuse to prune.
        log_err(
            f"cohort people config {cohort_org}/{CONFIG_REPO}/"
            f"{COHORT_PEOPLE_PATH} is absent - refusing to reconcile instructors (an "
            f"absent config would prune every instructor); skipping"
        )
        return 1
    desired = _desired_for(faculty, "instructors", date.today().isoformat())
    errors = reconcile_team_members(
        cohort_org, "instructors", desired, prune=True, dry_run=dry_run
    )

    tag = term_tag(cohort_org)
    if tag is None:
        return errors
    team = f"instructors-{tag}"
    if not dry_run:
        if not create_team(
            course_org, team, f"Instructors for {tag} (cohort-declared)"
        ):
            # The team could not be created; granting it repo access and reconciling its
            # membership would all fail against a non-existent team (triple-counting the
            # one root failure and firing doomed API calls). Report it once and stop here.
            return errors + 1
        for repo in _tag_repos(content_repos, assignments, tag):
            if not grant_team_repo_access(course_org, team, repo, "push"):
                errors += 1
    errors += reconcile_team_members(
        course_org, team, desired, prune=True, dry_run=dry_run
    )
    return errors


def sync(
    course_org: str, cohorts: list[str] | None = None, dry_run: bool = False
) -> int:
    """Reconcile course_admins (course org + `cohorts`, every registered cohort if
    not given) and, for each of those same cohorts, that cohort's own
    instructors/TAs (its own team + its course-org tag team). Pass an explicit
    single-item list to scope to just one cohort, e.g. a freshly bootstrapped one,
    without re-touching every other cohort."""
    targets = discover_cohorts(course_org) if cohorts is None else cohorts
    log_step(
        f"Materialising faculty access: course-admin across {1 + len(targets)} "
        f"org(s), instructors across {len(targets)} cohort(s)"
    )
    errors = sync_course_admins(course_org, targets, dry_run=dry_run)
    # Fetched once, not once per cohort - discover_content_repos/discover_assignments
    # depend only on course_org, not on which cohort is being processed.
    content_repos = discover_content_repos(course_org)
    assignments = discover_assignments(course_org)
    for cohort_org in targets:
        errors += sync_cohort_instructors(
            course_org, cohort_org, content_repos, assignments, dry_run=dry_run
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    errors = sync(args.course_org, dry_run=args.dry_run)
    if errors:
        log_err(f"{errors} errors during sync")
        return 1
    log_ok("Sync complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
