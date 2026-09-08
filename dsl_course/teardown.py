"""dsl-course teardown -- close a finished cohort out.

Run once, at the end of term, after the last grades have gone out. Five steps, in this
order, and the order is the whole design:

1. revoke each student's DIRECT collaborator grant - and any invitation they have not
   accepted yet - on the submission repos and gradebooks named after them;
2. ARCHIVE those repos: GitHub's read-only freeze;
3. archive `welcome`, the way IN to the cohort, so a finished term cannot still be joined;
4. write the teardown record into the cohort's private `classroom-config`;
5. archive `classroom-config` itself, LAST.

Revoke before freeze, because an archived repo takes no collaborator change: a repo frozen
before it is revoked keeps that grant for as long as it stays frozen. Seal last, because an
archived `classroom-config` is already what `seed._live_cohorts` reads as "this cohort is
finished, leave it frozen" - so until that step lands the cohort is still a live one, and a
run that died half way is resumed simply by running it again. Every step is idempotent: a
repo already archived is passed over, a grant already revoked is a no-op, and a cohort whose
`classroom-config` is archived is already closed out and does nothing at all.

NOTHING IS EVER DELETED. The bot holds no `delete_repo` scope, and every step here is
reversible by hand: un-archiving a repo from its own Settings page brings it back exactly as
it was, and `students.csv` - still in the sealed record - is what re-grants the access.

`--dry-run` is the default and prints counts only. A real run refuses unless the cohort's
`schedule.yml` declares a `semester_end` that has passed; `--force` is a person saying it
in as many words, which is what a cohort with no term dates needs.

Usage:
    python3 -m dsl_course.teardown --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.teardown --cohort-org hertie-dsl-demo-f2026 --no-dry-run
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, datetime, timezone
from typing import NamedTuple

from . import schedule
from .course import CONFIG_REPO, GRADEBOOK_PREFIX, submission_suffix
from .discovery import (
    ASSIGNMENT_TEMPLATE_TOPIC,
    classify_repos,
    is_student_repo,
    list_org_repos,
)
from .gh_contents import get_file_content, put_file
from .grades import COHORT_CSV_NAME
from .log import log, log_err, log_ok, log_person, log_step
from .repos import archive_repo
from .sync_roster import revoke_repo_grants

# The record itself, in the private repo it describes. Its own directory rather than a root
# file, so the sealed repo shows at a glance which files are the term's working state and
# which one is the account of how it ended.
RECORD_PATH = "archive/teardown.md"

# The way IN to a cohort: the repo holding the Join course and Join team issues. Frozen
# with the rest, so a finished cohort cannot still be joined.
WELCOME_REPO = "welcome"

_RECORD_BANNER = (
    "<!-- SYSTEM-OWNED - do not edit. Written by `python3 -m dsl_course.teardown` "
    "when this cohort was closed out. -->"
)

# What the sealed repo holds, spelled out for whoever opens it years later with a retention
# question rather than a teaching one. Every path here is written by some other part of the
# toolkit; this list is the only place that says, in one breath, that together they ARE the
# cohort's record of assessment.
_RETENTION_NOTE = f"""This repository is the cohort's private record and is now read-only.
It holds the roster (`students.csv`), the project teams (`teams.csv`), the teaching team
(`people.yml`), the term plan (`schedule.yml`), every grading sheet (`grading_sheets/`),
the autograde detail (`autograde/`), what was sent to whom (`gradebook/distributed.csv`)
and the registrar export (`{COHORT_CSV_NAME}`). Together those are this cohort's record of
assessment: delete the repository when your institution's retention period for that record
expires, and the archived student repos with it."""


class Target(NamedTuple):
    """One repo a teardown freezes: its name, whether it is frozen already, and the login
    it is NAMED after (`""` when it is named after something else - see `named_login`)."""

    repo: str
    archived: bool
    login: str


class Closed(NamedTuple):
    """What one run did, in the shape both the summary line and the record read."""

    frozen: list[str]  # every submission repo and gradebook now archived
    withdrawn: int  # direct grants + pending invitations taken back
    errors: int


def term_ended(sched: schedule.Schedule, today: date) -> bool:
    """Whether the cohort's declared term is over.

    A cohort with no `semester_end:` is NOT over. The key is optional everywhere else - the
    site synthesises a term end 15 weeks after the start when it is missing - but
    synthesising one HERE would freeze a live cohort's work off a guess. Such a cohort needs
    `--force`, which is a person taking the decision instead."""
    return sched.semester_end is not None and sched.semester_end < today


def _is_template(repo: dict) -> bool:
    """Whether a listing row is an assignment TEMPLATE rather than somebody's work."""
    return bool(repo.get("isTemplate")) or ASSIGNMENT_TEMPLATE_TOPIC in (
        repo.get("topics") or []
    )


def named_login(repo: str, template: str | None) -> str:
    """The login `repo` is NAMED after - the `<handle>` in `<slug>-<handle>` and in
    `grades-<handle>` - or `""` when it is named after something else.

    The narrowest possible target for a revoke, and deliberately so: a GROUP repo's suffix
    is a TEAM name, and a team is not a collaborator on its own repo, so the probe finds
    nothing there and takes nothing away. A repo recognised only by its `submission` topic,
    whose template is no longer in the org, is `""` for the same reason - it is frozen, but
    nobody's access is guessed at from a name that no rule explains."""
    if template:
        return submission_suffix(repo, template)
    if repo.startswith(GRADEBOOK_PREFIX):
        return repo.removeprefix(GRADEBOOK_PREFIX)
    return ""


def targets(repos: list[dict]) -> list[Target]:
    """Every repo in a cohort LISTING that holds a student's work or their marks.

    `discovery.is_student_repo` is the shared rule - the `submission` / `gradebook` topic OR
    the name, so a repo whose topic stamp never landed still counts - minus the cohort-side
    assignment TEMPLATES it also covers. A template holds the brief every student was given,
    which is nobody's personal record, and listing one in the teardown record as a student's
    repo would be a plain lie about what was frozen."""
    derived = classify_repos(repos)
    return sorted(
        Target(
            r["name"],
            bool(r.get("archived")),
            named_login(r["name"], derived.get(r["name"])),
        )
        for r in repos
        if is_student_repo(r, derived) and not _is_template(r)
    )


def registrar_summary(cohort_org: str) -> str:
    """One plain-text line for the record: what the registrar export holds, or that there
    is none. A ROW COUNT, never a row - this string is printed to the run log too, and the
    log of a course org's `.github` is world-readable."""
    text = get_file_content(cohort_org, CONFIG_REPO, COHORT_CSV_NAME)
    if text is None:
        return f"{COHORT_CSV_NAME} is NOT here - no grade was ever distributed"
    rows = max(len(list(csv.reader(io.StringIO(text)))) - 1, 0)
    return f"{COHORT_CSV_NAME}, {rows} student row(s)"


def render_record(
    cohort_org: str,
    closed: Closed,
    *,
    sealed_on: date,
    semester_end: date | None,
    registrar: str,
    welcome: str,
) -> str:
    """The teardown record, as it is written into the private `classroom-config`.

    It names the repos it froze. That is the point of it and it is safe: this file is
    written into the cohort's PRIVATE record, beside the roster those names are drawn from,
    and never into a log or a page. Nothing in this function is ever printed."""
    term = (
        f"Term ended **{semester_end}**."
        if semester_end
        else "`schedule.yml` declares no `semester_end` - closed out with `--force`."
    )
    rows = "\n".join(f"- `{name}`" for name in closed.frozen) or "- (none)"
    return f"""{_RECORD_BANNER}
# Teardown record - {cohort_org}

Closed out on **{sealed_on}** (UTC). {term}

| What | Count |
| --- | --- |
| Submission repos and gradebooks archived | {len(closed.frozen)} |
| Direct grants and invitations withdrawn | {closed.withdrawn} |
| Enrolment repo (`{WELCOME_REPO}`) | {welcome} |
| Registrar export | {registrar} |

**Nothing was deleted.** Archiving is GitHub's reversible read-only freeze: un-archive a
repo from its own Settings page and it comes back exactly as it was, and `students.csv` is
what re-grants a student their access if one has to be reopened.

{_RETENTION_NOTE}

## Frozen

{rows}
"""


def _freeze(cohort_org: str, live: list[Target], dry_run: bool) -> Closed:
    """Revoke then archive, one repo at a time. See the module docstring for the order.

    A repo whose access could NOT be settled is deliberately left LIVE: freezing it would
    make the revoke impossible until somebody un-archives it by hand, so the run reds and
    the next one picks that repo up."""
    frozen: list[str] = []
    withdrawn = errors = 0
    for target in live:
        if target.login:
            gained, failed = revoke_repo_grants(
                cohort_org, target.repo, target.login, dry_run=dry_run
            )
            withdrawn += gained
            if failed:
                errors += failed
                log_person(
                    f"  ! {cohort_org}/{target.repo} left live - its access could not "
                    f"be settled, so freezing it would strand the grant"
                )
                continue
        if dry_run:
            log_person(f"    DRY-RUN archive {cohort_org}/{target.repo}")
            frozen.append(target.repo)
        elif archive_repo(cohort_org, target.repo, person=True):
            log_person(f"  [ok] archived {cohort_org}/{target.repo}")
            frozen.append(target.repo)
        else:
            errors += 1
    return Closed(frozen, withdrawn, errors)


def _freeze_welcome(
    cohort_org: str, listing: list[dict], dry_run: bool
) -> tuple[str, int]:
    """Archive the cohort's `welcome` repo - the way IN to the cohort. Returns
    `(what the record says about it, errors)`.

    Its Join course and Join team issues are how a student enrols themselves, and an open
    one on a finished cohort is an enrolment into a term that is over: the handler writes
    into `classroom-config`, which by then is sealed and cannot take it, so the student gets
    a red run instead of a place. Frozen AFTER the student repos and BEFORE the record is
    sealed, for the same reason as everything else here - the marker moves last.

    Not a per-person repo, so a failure names it in the public log like any other piece of
    infrastructure, and reds the run: a cohort left joinable is not closed."""
    row = next((r for r in listing if r["name"] == WELCOME_REPO), None)
    if row is None:
        return "not in this org", 0
    if row.get("archived"):
        return "already frozen", 0
    if dry_run:
        log(f"    DRY-RUN archive {cohort_org}/{WELCOME_REPO}")
        return "frozen with them", 0
    if archive_repo(cohort_org, WELCOME_REPO):
        log_ok(f"archived {cohort_org}/{WELCOME_REPO}")
        return "frozen with them", 0
    return "NOT frozen", 1


def close_out(cohort_org: str, dry_run: bool = True, force: bool = False) -> int:
    """Close `cohort_org` out. 0 when it is closed (or already was), 1 on any failure.

    Counts only in the log: every faculty workflow runs in the course org's PUBLIC
    `.github`, and one `<slug>-<handle>` line there publishes who was in the cohort. The
    per-repo detail goes through `log_person` and into the private record."""
    log_step(f"Closing out {cohort_org}{' (dry run)' if dry_run else ''}")
    listing = list_org_repos(cohort_org)
    config = next((r for r in listing if r["name"] == CONFIG_REPO), None)
    if config is None:
        log_err(
            f"{cohort_org} has no {CONFIG_REPO} - it is not a bootstrapped cohort org, "
            f"and there is no private record to seal"
        )
        return 1
    if config.get("archived"):
        log_ok(f"{cohort_org} is already closed out ({CONFIG_REPO} is archived)")
        return 0

    sched = schedule.load(cohort_org)
    if not term_ended(sched, datetime.now(timezone.utc).date()) and not force:
        declared = (
            f"declares semester_end {sched.semester_end}"
            if sched.semester_end
            else "declares no semester_end"
        )
        log_err(
            f"{cohort_org}'s term is not over: {CONFIG_REPO}/{schedule.SCHEDULE_PATH} "
            f"{declared}. Close the cohort out after the term ends, or re-run with "
            f"--force to close it out anyway."
        )
        return 1

    found = targets(listing)
    live = [t for t in found if not t.archived]
    already = [t.repo for t in found if t.archived]
    registrar = registrar_summary(cohort_org)
    log(
        f"  {len(found)} submission repo(s) and gradebook(s): {len(live)} to freeze, "
        f"{len(already)} already frozen"
    )
    log(f"  registrar export: {registrar}")

    closed = _freeze(cohort_org, live, dry_run)
    frozen = sorted(closed.frozen + already)
    welcome, welcome_errors = _freeze_welcome(cohort_org, listing, dry_run)
    errors = closed.errors + welcome_errors
    if errors:
        log_err(
            f"{errors} step(s) failed - {cohort_org} is NOT sealed. Fix the cause "
            f"and run this again; it resumes from wherever it stopped."
        )
        return 1
    if dry_run:
        log_ok(
            f"dry run: {len(closed.frozen)} repo(s) would be frozen, {closed.withdrawn} "
            f"grant(s)/invite(s) withdrawn, {WELCOME_REPO} {welcome}, then "
            f"{CONFIG_REPO} sealed"
        )
        return 0

    record = render_record(
        cohort_org,
        Closed(frozen, closed.withdrawn, 0),
        sealed_on=datetime.now(timezone.utc).date(),
        semester_end=sched.semester_end,
        registrar=registrar,
        welcome=welcome,
    )
    if not put_file(
        cohort_org,
        CONFIG_REPO,
        RECORD_PATH,
        record.encode(),
        "docs: record the cohort teardown",
    ):
        log_err(
            f"the teardown record was not written - {CONFIG_REPO} is left LIVE rather "
            f"than sealed over a missing record. Re-run to finish."
        )
        return 1
    if not archive_repo(cohort_org, CONFIG_REPO):
        log_err(
            f"the record is written but {cohort_org}/{CONFIG_REPO} is NOT sealed, so the "
            f"nightly refresh still treats this cohort as live. Re-run to finish."
        )
        return 1
    log_ok(
        f"{cohort_org} closed out: {len(frozen)} repo(s) frozen, {closed.withdrawn} "
        f"grant(s)/invite(s) withdrawn, {WELCOME_REPO} {welcome}, {CONFIG_REPO} sealed"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    # Default ON, like every other write button: the rendered workflow passes --dry-run /
    # --no-dry-run explicitly, so a bare local invocation cannot freeze a cohort by accident.
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print the counts; freeze nothing, revoke nothing (default).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Close out even though the term is not over (or has no declared end).",
    )
    args = parser.parse_args()

    # A read helper that could not reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return close_out(args.cohort_org, dry_run=args.dry_run, force=args.force)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
