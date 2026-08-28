"""dsl-course assign -- provision per-student assignment repos from a template repo.

Generates ONE private repo per onboarded ENROLLED student from an assignment TEMPLATE repo
(e.g. assignment-1-f2026) in the course org, using GitHub's native template-generate,
then adds the student as a collaborator (maintain). The template carries its own
starter code + autograder workflow, which every generated repo inherits. Students
never use a CLI. Roster rows with `role=auditor` are skipped - auditors are read-only.
Idempotent: existing repos are left alone.

    course/<template>  (private, is_template)
            |  generate (native)
            v
    cohort/<slug>-<handle>   (private; student = collaborator)
    where <slug> is the template name minus a trailing -fYYYY / -sYYYY.

With --type group it instead makes ONE repo per team, `cohort/<slug>-<team>`, and grants the
GitHub Team materialised from classroom-config/teams.csv (see dsl_course.sync_teams) - so
membership changes propagate to access. Grades are never written here; they go to each
student's private gradebook repo (see dsl_course.grades), so a possibly-public team repo
never carries marks.

Usage:
    python3 -m dsl_course.assign \\
        --master-org TEST-HERTIE-COURSE --course-source-repo assignment-1-f2026 \\
        --cohort-org TEST-HERTIE-COHORT-f2026
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

from . import roster, sync_teams, teams
from .course import CONFIG_REPO, SOLUTION_BRANCH, assignment_slug
from .discovery import ASSIGNMENT_TEMPLATE_TOPIC
from .utils import (
    GIT_ENV,
    add_collaborator,
    generate_from_template,
    gh,
    git,
    grant_faculty_read_access,
    grant_team_repo_access,
    log,
    log_err,
    log_ok,
    log_skip,
    log_step,
    log_verbose,
    put_file,
    repo_exists,
    set_repo_topics,
)

# Fire-once sentinel for the SCHEDULED solution push, in classroom-config. Needed because
# `due_releases` is cumulative by design - a handout release re-fires every tick so a late
# onboarder still gets their repo - and while re-probing a repo is cheap, push_solution
# CLONES every student repo. Without this marker a passed `solution_datetime` means a clone
# per student per hour for the rest of the term.
#
# A marker rather than a time window (`solution_datetime <= now < +1h`): a missed tick -
# an outage, a queued runner, a rate limit - would silently mean the solution never ships
# at all, and nothing would ever notice. Deleting the file re-releases it.
SOLUTION_RECORD_DIR = "solutions"
SOLUTION_DIR = "solution"
_GIT_ENV = GIT_ENV


def _wait_for_content(
    org: str, repo: str, attempts: int = 12, delay: float = 1.5
) -> bool:
    """Poll until a freshly template-generated repo has content.

    GitHub's template-generate is asynchronous: a just-created repo can briefly be empty,
    and using it as a generate *source* (the next stage) then fails with `... is empty`.
    Returns True once the repo's root has files."""
    for _ in range(attempts):
        code, out = gh("api", f"repos/{org}/{repo}/contents", "--jq", "length")
        if code == 0 and out.strip().isdigit() and int(out.strip()) > 0:
            return True
        time.sleep(delay)
    return False


def ensure_cohort_template(
    master_org: str, template: str, cohort_org: str, slug: str
) -> str | None:
    """Stage 1: freeze a cohort-level template repo (named `<slug>`) from the course
    template, so the cohort has its own copy and per-student repos generate from it
    (the role Classroom 50's classroom template used to play). Returns the cohort
    template name, or None on failure. Idempotent."""
    if repo_exists(cohort_org, slug):
        log_skip(f"cohort template {cohort_org}/{slug}")
    elif not generate_from_template(
        template_org=master_org,
        template_name=template,
        owner=cohort_org,
        name=slug,
        private=True,
        description=f"{slug} - cohort assignment template",
    ):
        return None
    else:
        log_ok(f"created cohort template {cohort_org}/{slug}")
    # Whether just created OR pre-existing, ENSURE it is both populated and flagged as a
    # template - not only on the create path. A prior run that timed out in
    # `_wait_for_content` left the repo existing but with `is_template` never set; the old
    # exists-short-circuit then returned the slug without re-checking, so every later handout
    # failed with a misleading "<slug> is not a template" error. Both steps are idempotent,
    # so a retry HEALS a half-created template rather than being wedged by it. For a healthy
    # template `_wait_for_content` returns on its first poll, so the cost is one API call.
    if not _wait_for_content(cohort_org, slug):
        log_err(
            f"  ! cohort template {cohort_org}/{slug} did not populate in time "
            f"(template-generate is async) - re-run the release"
        )
        return None
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{cohort_org}/{slug}",
        "-F",
        "is_template=true",
    )
    if code != 0:
        log_err(
            f"  ! could not set is_template on {cohort_org}/{slug} - per-student repos "
            f"generate FROM it, so this must succeed: {out[:160]}"
        )
        return None
    # The topic is not decoration: discovery.discover_handed_out_assignments reads it back
    # as the record that this assignment went out, and the site withholds the brief until
    # it does. So a failure here is said out loud with its consequence attached rather than
    # dropped - the hand-out itself succeeded, and failing it now would be worse.
    if not set_repo_topics(cohort_org, slug, [slug, ASSIGNMENT_TEMPLATE_TOPIC]):
        log_err(
            f"  ! {cohort_org}/{slug} carries no `{ASSIGNMENT_TEMPLATE_TOPIC}` topic. That "
            f"topic is what the cohort site reads as the record that {slug} was handed "
            f"out, so its brief stays withheld there until the topic is set by hand (or "
            f"its `handout_datetime` passes)."
        )
    return slug


def fetch_solution(master_org: str, template: str, dest: Path) -> Path | None:
    """Clone the template's `solution` branch and return its solution/ dir, or None.

    Solutions live on a non-default branch so native template-generate (default branch
    only) never copies them into student repos - they're pushed separately, on demand."""
    code, _ = gh(
        "repo",
        "clone",
        f"{master_org}/{template}",
        str(dest),
        "--",
        "-q",
        "-b",
        SOLUTION_BRANCH,
    )
    if code != 0:
        log_err(
            f"  ! no `{SOLUTION_BRANCH}` branch on {master_org}/{template} - "
            f"nothing to push (add the solution there first)"
        )
        return None
    sol = dest / SOLUTION_DIR
    if not sol.is_dir():
        # The branch exists but holds no `solution/` folder - the model answer was committed
        # at the branch root, or the folder was renamed. Silent before, which made the
        # caller's failure look like a missing branch.
        log_err(
            f"  ! {master_org}/{template}'s `{SOLUTION_BRANCH}` branch has no "
            f"`{SOLUTION_DIR}/` folder - nothing to push"
        )
        return None
    return sol


def push_solution(cohort_org: str, repo: str, sol_dir: Path) -> bool:
    """Push the solution/ folder into an existing student repo (idempotent overwrite)."""
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "r"
        if gh("repo", "clone", f"{cohort_org}/{repo}", str(wd), "--", "-q")[0] != 0:
            return False
        shutil.copytree(sol_dir, wd / SOLUTION_DIR, dirs_exist_ok=True)
        git("-C", str(wd), *_GIT_ENV, "add", "-A")
        code, _ = git(
            "-C",
            str(wd),
            *_GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            "add solution",
        )
        if code != 0:
            return True  # already present, nothing new
        return git("-C", str(wd), *_GIT_ENV, "push", "-q", "origin", "HEAD")[0] == 0


def provision_one(
    master_org: str,
    template: str,
    cohort_org: str,
    repo: str,
    handles: list[str],
    slug: str,
    sol_dir: Path | None = None,
    team: str | None = None,
    touch_existing: bool = True,
) -> str:
    """Generate one submission repo and grant its members access.

    `touch_existing=False` (the hourly scheduler): a repo that already exists, with no
    solution due, is left exactly as it is - no access re-grant, no team reconcile. The
    manual Release assignment button keeps the default and so remains the way a faculty
    member repairs one student's access by re-running it.

    Individual assignments pass a single-element `handles` list (a team of one) and no
    `team`, so each member is added as a collaborator. Group assignments also pass the
    GitHub Team slug: the team is materialised from `handles` and granted on the repo, so
    membership changes propagate to access (and members get @mentions + a team space)."""
    existed = repo_exists(cohort_org, repo)
    if existed:
        log_verbose(f"  [skip] repo {cohort_org}/{repo}")
        if sol_dir is None and not touch_existing:
            # Nothing is due for this repo. The scheduler re-runs every handed-out release
            # on every hourly tick, so re-granting access here cost 2-4 API calls per
            # student per assignment for the rest of the term (1,440+/hour for a large
            # cohort) - mostly writes, against a 5,000/hour budget shared by every cron.
            # Faculty access repairs are the nightly sweep's job (converge_faculty_access),
            # a team's late joiners arrive through Sync membership, and a student's access
            # is repaired by re-running the Release assignment button (touch_existing).
            return "skipped"
    elif not generate_from_template(
        template_org=master_org,
        template_name=template,
        owner=cohort_org,
        name=repo,
        private=True,
        description=f"{slug} - submission repo",
    ):
        return "failed-create"
    else:
        log_verbose(f"  [ok] created {cohort_org}/{repo}")
        if not set_repo_topics(cohort_org, repo, [slug, "submission"]):
            # Not named: this log is public. The nightly sweep converges the topic.
            log_err(
                "  ! a submission repo is untagged - the nightly sweep converges it"
            )

    solution_failed = False
    if sol_dir is not None:
        if push_solution(cohort_org, repo, sol_dir):
            log_verbose("  [ok]   + solution pushed")
        else:
            # Reported in the RETURN value, not just the log: provision_all writes a
            # fire-once marker off these statuses, so a push that only logged its failure
            # meant the marker was written anyway - the student never received the
            # solution, and the marker guaranteed no later tick would retry.
            log_err("  ! could not push solution")
            solution_failed = True

    # Before the group/individual split, because the group arm RETURNS inside itself: a
    # call after it reaches individual assignments only, and every team project repo would
    # have gone on granting nobody but the team. This repo used to grant no faculty at all,
    # so an instructor who was not an org OWNER could not open the work they had to mark.
    #
    # READ, not write. Marking happens in `classroom-config/grades/<slug>.csv` (docs/10),
    # and by the time anyone marks, the deadline snapshot has already frozen this repo's
    # HEAD and the autograder has run off that snapshot - so a commit here would reach no
    # gradebook and form no part of the record. Faculty need to SEE the work, not edit it.
    grant_faculty_read_access(cohort_org, repo)
    if team is not None:
        # Group: materialise the team from its members and grant it on the repo, so
        # post-sync membership edits propagate to access (vs. one-off collaborator grants).
        # A team that couldn't take all its members grants access to nobody missing, so
        # its result counts towards this repo's status rather than being discarded.
        team_ok = sync_teams.ensure_team(cohort_org, team, set(handles), prune=False)
        access_ok = grant_team_repo_access(cohort_org, team, repo, "maintain")
        if access_ok:
            log_verbose(f"  [ok]   + team {team} (maintain)")
        if not team_ok:
            log_err(f"  ! team {team} is missing member(s) - they cannot see {repo}")
        if not handles:
            # Every member of this team was rejected by the roster allowlist upstream, so
            # the team is empty and the repo is granted to nobody. The individual arm calls
            # that failed-no-collaborator; a group of nobody is the same handout failure,
            # and reporting "ok" left a repo no student could open looking successful.
            log_err(f"  ! team {team} has no vetted members - nobody can open {repo}")
        # A failed solution push WINS over every other fault here. provision_all writes the
        # FIRE-ONCE solution marker off these statuses, so a repo that reported any other
        # failure had its missing solution forgotten - and the marker guaranteed no later
        # tick would retry. Every other fault below is persistent and unrelated to the
        # push; only this one must reach `failed-solution`.
        if solution_failed:
            return "failed-solution"
        if not access_ok:
            return "failed-no-access"
        if not team_ok:
            return "failed-team-members"
        if not handles:
            return "failed-no-members"
        return "skipped" if existed else "ok"

    # Ordering hazard (individual path): granting a repo collaborator BEFORE the student has
    # accepted their org invite records them as an OUTSIDE collaborator, which can make a
    # later team-based add 422 forever. The individual flow is collaborator-based by design
    # (see the module docstring - groups are the team-based path), and onboarding normally
    # accepts the org invite first, so this stays a direct grant; the group path already
    # routes access through the team to avoid the wedge.
    added = 0
    for handle in handles:
        if add_collaborator(cohort_org, repo, handle, permission="maintain"):
            log_verbose(f"  [ok]   + @{handle} (maintain)")
            added += 1
        else:
            log_err(f"  ! could not add @{handle} (not a real account?)")
    # Same precedence as the group arm above: a failed solution push wins, because it is
    # the only fault the fire-once marker must not be written over.
    if solution_failed:
        return "failed-solution"
    if added == 0:
        # A repo nobody can open is a failed handout - "failed" is what the exit code
        # keys on (see provision_all), so the run goes red rather than quietly ok.
        return "failed-no-collaborator"
    return "skipped" if existed else "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master-org", required=True, help="Course org (template source)"
    )
    parser.add_argument(
        "--course-source-repo",
        dest="template",
        required=True,
        help="COURSE-org repo to hand out from (e.g. assignment-1-f2026)",
    )
    parser.add_argument("--cohort-org", required=True, help="Cohort org (target)")
    parser.add_argument(
        "--roster",
        default=None,
        help="Local students.csv (default: cohort classroom-config)",
    )
    parser.add_argument(
        "--solution",
        action="store_true",
        help="Also push the solution (template's `solution` branch) into each student repo",
    )
    parser.add_argument(
        "--type",
        dest="kind",
        choices=["auto", "individual", "group"],
        default="auto",
        help="individual = one repo per student; group = one per team (from "
        "classroom-config/teams.csv); auto = whatever schedule.yml / the template's "
        "grading.yml declare (default: individual).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    kind = args.kind
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return provision_all(
            args.master_org,
            args.template,
            args.cohort_org,
            roster_path=args.roster,
            solution=args.solution,
            group={"auto": None, "individual": False, "group": True}[kind],
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


def solution_record_path(slug: str) -> str:
    """Where the fire-once record for `slug`'s solution release lives."""
    return f"{SOLUTION_RECORD_DIR}/{slug}.json"


def solution_released(cohort_org: str, slug: str) -> bool:
    """Whether the model solution for `slug` has already been pushed to this cohort.

    Read by the scheduler, so a passed `solution_datetime` fires exactly once. The manual
    Release assignment path does NOT consult it - an operator ticking include_solution is
    asking for it now, and push_solution is an idempotent overwrite anyway."""
    code, _ = gh(
        "api",
        f"repos/{cohort_org}/{CONFIG_REPO}/contents/{solution_record_path(slug)}",
        "--jq",
        ".sha",
    )
    return code == 0


def record_solution_released(cohort_org: str, slug: str, repos: int) -> bool:
    """Write the fire-once record, so no later tick re-pushes the solution.

    Written only after a run in which every solution push succeeded - a partial push must
    re-run, or the students it missed would never receive the solution at all. Returns
    whether the record actually landed: a marker that did not is what makes every later
    tick re-clone every submission repo to re-push a solution they already have."""
    return put_file(
        cohort_org,
        CONFIG_REPO,
        solution_record_path(slug),
        json.dumps(
            {"assignment": slug, "repos": repos, "released": "by dsl-course"}, indent=2
        ).encode()
        + b"\n",
        f"chore: record the model solution release for {slug}",
    )


def provision_all(
    master_org: str,
    template: str,
    cohort_org: str,
    roster_path: str | None = None,
    solution: bool = False,
    group: bool | None = None,
    dry_run: bool = False,
    touch_existing: bool = True,
) -> int:
    """Freeze the cohort template, then provision a repo per unit (student, or team).

    Callable directly (e.g. by the scheduler) as well as from the CLI. `group=None`
    (the default) reads the template's own declaration - `type: group` in the
    grading.yml on its solution branch; pass True to force per-team for a template
    that doesn't declare it."""
    if master_org == cohort_org:
        log_err("master-org and cohort-org must differ.")
        return 1
    if group is None:
        from .collect import assignment_is_group

        # schedule.yml's assignments.<slug>.type wins; grading.yml is the fallback.
        group = assignment_is_group(master_org, cohort_org, template)
        if group:
            log("  (declared `type: group` - provisioning per team)")

    students = roster.load_path(roster_path) if roster_path else roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - nobody to provision for.")
        return 1
    # Auditors are read-only - they see released materials, never an assignment repo.
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    # TWO names, and they are not interchangeable.
    #   `slug`: the cohort-side NAME - `cohort_dest_repo`, else the schedule key, else (for
    #     a handout of an unscheduled template) the template name minus its tag. Every repo
    #     made here, and every snapshot/autograde/grades artefact, is named after it.
    #   `key`: the SCHEDULE KEY. teams.csv is keyed on it - the welcome Join-team form
    #     validates the assignment against `assignments:` in schedule.yml and writes that
    #     key - and `sync_teams.desired_teams` derives its GitHub team slugs from it.
    # They differ exactly when `cohort_dest_repo` is set. Keying the lookup or the team slug
    # on the name then meant no teams found at all, or a team granted on the repo under a
    # slug that Sync membership reconciles a DIFFERENT team for.
    from . import schedule

    found = schedule.entry_for_repo(schedule.load(cohort_org), template)
    key = found[0] if found else assignment_slug(template)
    slug = schedule.cohort_name(*found) if found else key

    # A provisioning unit is (repo_name, [member handles], team slug). Individual = one per
    # student (a team of one); group = one per team from teams.csv, keyed on `key`.
    if group:
        groups = teams.teams_for(teams.load(cohort_org), key)
        if not groups:
            log_err(
                f"no teams for `{key}` in {cohort_org}/classroom-config/teams.csv - "
                f"students self-select via the welcome 'Join team' issue, or seed the CSV."
            )
            return 1
        # teams.csv is student-writable (the welcome "Join team" issue appends rows), so its
        # handles must pass the SAME roster allowlist sync_teams applies: only enrolled,
        # onboarded roster handles - never a typo or a stranger's login that would be INVITED
        # into the private cohort org (and granted `maintain` on a repo) by ensure_team.
        # Compared casefold (GitHub logins are case-insensitive); the roster's casing wins.
        allowed_by_fold = {
            h.casefold(): h for h in sync_teams.known_handles(participants)
        }
        units = []
        for team, members in sorted(groups.items()):
            vetted, rejected = sync_teams.vet_handles(members, allowed_by_fold)
            for m in rejected:
                log_err(
                    f"{m} in teams.csv ({key}/{team}) is not an enrolled, onboarded "
                    f"roster handle - excluding it (would invite an arbitrary account "
                    f"into {cohort_org})"
                )
            units.append((f"{slug}-{team}", vetted, sync_teams.team_slug(key, team)))
        what = f"{len(units)} team(s)"
    else:
        units = [
            (f"{slug}-{s.github_handle}", [s.github_handle], None) for s in onboarded
        ]
        what = f"{len(units)} student(s)"

    log_step(
        f"Releasing {slug} to {cohort_org}: freeze cohort template, then provision "
        f"{what}{' + solution' if solution else ''}"
    )
    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, no assignment repos)")

    if dry_run:
        log(f"    DRY-RUN  cohort template {cohort_org}/{slug}")
        for repo, handles, team in units:
            via = f" (team {team})" if team else ""
            log_verbose(
                f"    DRY-RUN  {cohort_org}/{repo}{via}  <- {', '.join('@' + h for h in handles)}"
            )
        return 0

    # Stage 1: freeze the cohort-level template.
    cohort_template = ensure_cohort_template(master_org, template, cohort_org, slug)
    if cohort_template is None:
        log_err("could not create the cohort assignment template.")
        return 1

    with tempfile.TemporaryDirectory() as soldir:
        # Solution still comes from the COURSE template's solution branch.
        sol_dir = None
        solution_unavailable = False
        if solution:
            sol_dir = fetch_solution(master_org, template, Path(soldir) / "t")
            if sol_dir is None:
                # NOT fatal. Stage 2 below is what gets students their repos at all, and a
                # scheduled handout re-runs every tick - so returning here would mean a
                # template whose solution branch is missing, renamed, or holding the model
                # answer outside `solution/` stops provisioning for every student who
                # onboards from that moment on, with the solution request as the only
                # cause. Hand out the repos, report the failure, ship no solution.
                log_err(
                    "  ! no usable solution to push - provisioning continues without it"
                )
                solution_unavailable = True

        # Stage 2: fan out one repo per unit (student, or team) FROM the cohort template.
        results: dict[str, int] = {}
        for repo, handles, team in units:
            log_verbose(f"-> {repo}")
            status = provision_one(
                cohort_org,
                cohort_template,
                cohort_org,
                repo,
                handles,
                slug,
                sol_dir,
                team=team,
                touch_existing=touch_existing,
            )
            results[status] = results.get(status, 0) + 1

    log_ok(f"Done - {json.dumps(results)}")
    # Record the handout moment back into the cohort's schedule.yml (write-once - a
    # handout the schedule already carries is never touched). The schedule is the primary
    # route AND the one record of when each assignment went out; a manual workflow run
    # fills the field the dispatcher didn't. record_handout keys on the schedule KEY, not the
    # cohort-side name: when `cohort_dest_repo` is set the two differ, and passing the name
    # made it miss the real entry and append a bogus duplicate block (dropping its due date).
    from . import schedule

    schedule.record_handout(cohort_org, key)
    from . import site

    # site.sync_site now RAISES on a genuine tree/team read failure (post-PR2), and a config
    # file that doesn't parse raises yaml.YAMLError - which is NOT a RuntimeError. The repos
    # are already handed out by this point, so neither failure may abort the run with a
    # traceback and misreport the whole handout as failed: log it, count it (so the run goes
    # red and the next Sync site / tick refreshes the site), and return normally.
    site_failed = False
    try:
        # A tick that created or changed nothing has nothing to show the site: skipping the
        # sync here is what stops every handed-out assignment re-rendering the site hourly.
        if any(k != "skipped" for k in results):
            site.sync_site(master_org, cohort_org)
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(
            f"site sync failed after provisioning {slug} - the repos are handed out; the "
            f"site refreshes on the next Sync site or scheduler tick: {exc}"
        )
        site_failed = True
    failed = site_failed or any(k.startswith("failed") for k in results)
    # Record the release only when every solution push in this run landed, and only when
    # there was at least one repo to push into. Deliberately NOT gated on `failed`:
    #   - a site-sync failure, or one dead student handle, says nothing about whether the
    #     solution shipped - and both are PERSISTENT, so withholding the marker for them
    #     would re-clone every student repo every hour for the rest of the term, which is
    #     the exact cost this marker exists to prevent;
    #   - `units == []` (nobody onboarded yet) means nothing was pushed at all, so
    #     recording it would mean everyone who onboards later never gets the solution.
    # `failed-solution` is what a failed push reports, so it is read here directly.
    solution_pushed = (
        solution
        and not solution_unavailable
        and bool(units)
        and not results.get("failed-solution")
    )
    if solution_pushed and not record_solution_released(cohort_org, slug, len(units)):
        log_err(
            f"the solution for {slug} shipped, but its fire-once record could not be "
            f"written to {cohort_org}/classroom-config - until it is, every hourly tick "
            f"re-clones every submission repo to push a solution they already have"
        )
        failed = True
    return 1 if failed or solution_unavailable else 0


if __name__ == "__main__":
    sys.exit(main())
