"""dsl-course status -- a per-cohort checklist of every faculty & instructors input location.

Faculty & instructors currently touch several distinct files across 2 orgs to run a cohort: course
identity, course admins, and classroom-config's roster/teams/grades/schedule.yml (which
now carries the release plan too)/people.yml. This module answers one glance-able question -
what's configured, what's still missing, and where do I go to fix it - by reusing
each source's existing loader rather than re-deriving anything. Read-only; it
changes no state.

Row IDs (B1, C2, ...) are internal stable identifiers for the checklist rows - they key the
REQUIRED set and the JSON output, and are not tied to any numbering in the docs.

Usage:
    python3 -m dsl_course.status --course-org COURSE --cohort-org COHORT
    python3 -m dsl_course.status --course-org COURSE --cohort-org COHORT --format json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import date

import yaml

from . import grades, roster, schedule, sync_faculty, teams
from .central import CENTRAL_REF, resolve_central_ref
from .discovery import org_meta
from .log import log_err
from .repos import default_branch

ITEMS = ("B1", "B6", "B7", "C2", "C3", "C4", "C5", "C6", "C7")
# Rows whose input is marked `[required]` in docs/DEPLOYMENT-CHECKLIST.md;
# everything else is optional
# (synthesised/skipped when absent), so an absent optional item is "optional", not
# "missing" - the status view shouldn't cry wolf over things that never block the pipeline.
REQUIRED = {"B1", "C2"}


# --------------------------------------------------------------------------- pure core


def _edit_url(org: str, repo: str, path: str, branch: str, exists: bool) -> str:
    if exists:
        return f"https://github.com/{org}/{repo}/edit/{branch}/{path}"
    return f"https://github.com/{org}/{repo}/new/{branch}?filename={path}"


def _row(
    item_id: str,
    label: str,
    org: str,
    repo: str,
    path: str,
    branch: str,
    present: bool,
    detail: str,
) -> dict:
    status = "ok" if present else ("missing" if item_id in REQUIRED else "optional")
    return {
        "label": label,
        "org": org,
        "repo": repo,
        "path": path,
        "status": status,
        "detail": detail,
        "edit_url": _edit_url(org, repo, path, branch, present),
    }


def render_markdown(course_org: str, cohort_org: str, data: dict[str, dict]) -> str:
    """One markdown table, in `docs/DEPLOYMENT-CHECKLIST.md`'s B/C order, each
    row linking straight to the file to fix if something's missing."""
    icon = {"ok": "OK", "missing": "MISSING", "optional": "not set (optional)"}
    lines = [
        f"## Status: {cohort_org} (course: {course_org})",
        "",
        "| Item | Status | Detail | |",
        "| --- | --- | --- | --- |",
    ]
    for item_id in ITEMS:
        row = data[item_id]
        link_text = "edit" if row["status"] == "ok" else "add"
        lines.append(
            f"| {row['label']} | {icon[row['status']]} | {row['detail'] or '-'} "
            f"| [{link_text}]({row['edit_url']}) |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- gh/git wiring


def collect(course_org: str, cohort_org: str) -> dict[str, dict]:
    """One status row per faculty & instructors input location for `cohort_org`. Read-only."""
    # load_yaml_config raises a clear error on malformed YAML (caught in main()) instead
    # of handing a traceback to the operator who runs status to find the broken input.
    course_meta = org_meta(course_org)

    # Every course-org row lives in .github; every cohort row lives in
    # classroom-config - resolve each default branch once, not once per row.
    course_branch = default_branch(course_org, ".github", fallback="main")
    cohort_branch = default_branch(cohort_org, schedule.CONFIG_REPO, fallback="main")

    data: dict[str, dict] = {}

    course_name = course_meta.get("course_name") or course_meta.get("org_name") or ""
    data["B1"] = _row(
        "B1",
        "Course identity",
        course_org,
        ".github",
        "dsl-course.yml",
        course_branch,
        bool(course_name),
        course_name,
    )

    # Access is granted by github_handle alone (sync_faculty's actual criterion) -
    # site_repo._people_from_meta requires a display `name` too (it's for website cards),
    # so it undercounts here. Reuse the already-fetched course_raw. course-admin only
    # - a course-level `instructors`/`teaching_assistants` entry is a legitimate,
    # display-only website card (see the People section in
    # docs/DEPLOYMENT-CHECKLIST.md), not access, so it must not inflate
    # this count.
    has_people_block = isinstance(course_meta.get("people"), dict)
    course_faculty = sync_faculty.parse_faculty_from_meta(course_meta)
    course_desired = sync_faculty.desired_team_members(
        course_faculty, date.today().isoformat()
    )
    n_admins = len(course_desired.get("course-admin", set()))
    data["B6"] = _row(
        "B6",
        "Course admins",
        course_org,
        ".github",
        "dsl-course.yml",
        course_branch,
        has_people_block,
        # No people: block does NOT "fall back to GitHub teams" - sync_faculty reconciles
        # course-admin with prune=True whenever dsl-course.yml is present, so an absent
        # block reconciles the team to empty.
        f"{n_admins} active"
        if has_people_block
        else "no people: block - Sync empties course-admin",
    )

    # Which toolkit tier this cohort's workflows run. Declared on the COURSE org and
    # inherited here, so it belongs in the B (course-org) block even though the checklist
    # is per-cohort - and an org whose tier is the default should read as such rather than
    # as an unset input someone forgot.
    declared = course_meta.get("central_ref")
    ref = resolve_central_ref(declared, source=f"{course_org}/.github/dsl-course.yml")
    data["B7"] = _row(
        "B7",
        "Toolkit tier",
        course_org,
        ".github",
        "dsl-course.yml",
        course_branch,
        declared is not None,
        ref if declared is not None else f"{CENTRAL_REF} (default)",
    )

    students = roster.load(cohort_org) or []
    onboarded = sum(s.onboarded for s in students)
    data["C2"] = _row(
        "C2",
        "Roster",
        cohort_org,
        roster.CONFIG_REPO,
        roster.ROSTER_PATH,
        cohort_branch,
        bool(students),
        f"{len(students)} student(s), {onboarded} onboarded" if students else "",
    )

    grade_sources = grades.load_grade_sources(cohort_org)
    data["C3"] = _row(
        "C3",
        "Grades",
        cohort_org,
        grades.CONFIG_REPO,
        grades.GRADES_DIR,
        cohort_branch,
        bool(grade_sources),
        f"{len(grade_sources)} assignment(s)" if grade_sources else "",
    )

    team_data = teams.load(cohort_org)
    n_teams = sum(len(t) for t in team_data.values())
    data["C4"] = _row(
        "C4",
        "Teams",
        cohort_org,
        teams.CONFIG_REPO,
        teams.TEAMS_PATH,
        cohort_branch,
        bool(team_data),
        f"{n_teams} team(s) across {len(team_data)} assignment(s)" if team_data else "",
    )

    sched = schedule.load(cohort_org)
    # A dropped entry is the one schedule fault a count alone hides: the numbers below
    # look plausible, they are just quietly short of what faculty wrote. Say so on both
    # schedule rows, since a drop in either block lands in whichever row reads it.
    dropped = (
        f" - WARNING: {len(sched.dropped)} entry/ies DROPPED, see the run log"
        if sched.dropped
        else ""
    )

    n_actions = sum(len(r.deploy) + bool(r.assignment) for r in sched.releases)
    data["C5"] = _row(
        "C5",
        f"Release plan ({schedule.SCHEDULE_PATH} -> releases)",
        cohort_org,
        schedule.CONFIG_REPO,
        schedule.SCHEDULE_PATH,
        cohort_branch,
        bool(sched.releases),
        f"{len(sched.releases)} scheduled release(s), {n_actions} action(s){dropped}"
        if sched.releases
        else dropped.lstrip(" -"),
    )

    has_due_dates = bool(sched.semester_start or sched.assignments or sched.events)
    data["C6"] = _row(
        "C6",
        f"Due dates & events ({schedule.SCHEDULE_PATH})",
        cohort_org,
        schedule.CONFIG_REPO,
        schedule.SCHEDULE_PATH,
        cohort_branch,
        has_due_dates,
        f"start={sched.semester_start}, {len(sched.assignments)} due date(s), "
        f"{len(sched.events)} event(s){dropped}"
        if has_due_dates
        else dropped.lstrip(" -"),
    )

    # load_cohort_faculty returns None when people.yml is absent - an empty desired set
    # for this read-only status view (no team to count).
    cohort_faculty = sync_faculty.load_cohort_faculty(cohort_org) or {}
    cohort_desired = sync_faculty.desired_team_members(
        cohort_faculty, date.today().isoformat()
    )
    n_instructors = len(cohort_desired.get("instructors", set()))
    data["C7"] = _row(
        "C7",
        f"Instructors/TAs ({sync_faculty.COHORT_PEOPLE_PATH})",
        cohort_org,
        sync_faculty.COHORT_CONFIG_REPO,
        sync_faculty.COHORT_PEOPLE_PATH,
        cohort_branch,
        bool(n_instructors),
        f"{n_instructors} active" if n_instructors else "",
    )

    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument("--format", choices=["md", "json"], default="md")
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        # collect()'s dependencies (schedule.load, roster.load, sync_faculty...) log
        # informational lines to stdout, some naming people.yml entries. Both modes keep
        # them off stdout: --format json promises parseable output, and the workflow
        # appends the markdown to $GITHUB_STEP_SUMMARY of a PUBLIC repo.
        with contextlib.redirect_stdout(io.StringIO()):
            data = collect(args.course_org, args.cohort_org)
        if args.format == "json":
            print(json.dumps(data, indent=2))
        else:
            print(render_markdown(args.course_org, args.cohort_org, data))
    except (RuntimeError, yaml.YAMLError) as exc:
        # A read helper that couldn't reach the API raises RuntimeError; a malformed
        # config file raises yaml.YAMLError. Either way an operator wants a one-line
        # "this is broken" in the log, not a traceback - and the run still goes red.
        log_err(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
