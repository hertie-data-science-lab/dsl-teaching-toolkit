"""dsl-course teardown -- close a finished cohort out.

Runs on the cohort's own `archive.date`, fired by the scheduler - and only for a cohort
whose `schedule.yml` writes an `archive:` block (its `date:` defaults to `semester_end` +
60 days). Archiving is opt-in, so the Archive cohort button is for closing one out early,
and the only way to close out a cohort that wrote no block. Six steps, in this order, and
the order is the whole design:

0. PROPAGATE: offer the cohort's edits to its released material back to the course org as
   a pull request (`dsl_course.propagate`). First, because it is the only step that READS
   the cohort - after step 4 every repo in it is read-only - and it is the last chance to
   carry a correction home. It never blocks the seal: a cohort is closed whether or not
   faculty ever wanted its edits.
1. close the toolkit's own open notices in `classroom-config` - the digest issues, the
   cadence alarm and the "archives on <date>" notice - saying the cohort is now archived,
   since nothing will ever close them afterwards and an archived repo takes no issue
   write;
2. one last website sync, so the deployed site shows the archived state rather than the
   state of the term's last release;
3. ARCHIVE every repo in the org - students' work first, then `welcome` (the way IN, so a
   finished term cannot still be joined), then the released content, the website and the
   cohort's own `.github`;
4. write the teardown record into the cohort's private `classroom-config`;
5. archive `classroom-config` itself, LAST.

NOBODY IS REVOKED. An archived repo is read-only for everyone, so freezing IS the
withdrawal of write access - and a student keeps read access to their own work, which is
the point of closing a cohort rather than deleting it. Membership and teams are left
exactly as they are.

Seal last, because an archived `classroom-config` is what every course-side sweep reads as
"this cohort is finished, leave it frozen" (`discovery.cohort_is_live`) - so until that
step lands the cohort is still a live one, and a run that died half way is resumed simply
by running it again. Every step is idempotent: a repo already archived is passed over, and
a cohort whose `classroom-config` is archived is already closed out and does nothing at
all.

NOTHING IS EVER DELETED. The bot holds no `delete_repo` scope, and every step here is
reversible by hand: un-archiving a repo from its own Settings page brings it back exactly
as it was.

`--dry-run` is the default and prints counts only - always, whatever the date, because
the counts are how somebody decides whether to ask for a close-out. Only a REAL run
refuses until the cohort's `archive.date` has arrived; `--force` is a person saying it in
as many words, which is what an early close-out - and a cohort that never asked to be
archived - needs.

Usage:
    python3 -m dsl_course.teardown --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.teardown --course-org COURSE --cohort-org COHORT --no-dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from typing import NamedTuple

from . import cadence, config_digest, propagate, schedule, site, source_digest
from .course import CONFIG_REPO, UPSTREAM_BRANCH, pages_repo
from .discovery import (
    ASSIGNMENT_TEMPLATE_TOPIC,
    classify_repos,
    is_student_repo,
    list_org_repos,
)
from .gh_contents import get_file_content, put_file, read_csv
from .grades import COHORT_CSV_NAME
from .issues import close_issues_titled, open_titles
from .log import log, log_err, log_ok, log_person, log_step
from .repos import archive_repo

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

# The comment left on every notice this closes. Its own sentence, because the issues being
# closed are about things nobody can now fix - a roster row the parser could not use, a
# release whose source was never staged - and closing them silently would read as "fixed".
_CLOSING_COMMENT = (
    "This cohort has been archived and is read-only. Nothing in it can be changed now, so "
    "this notice is closed unresolved rather than fixed. The teardown record is in "
    f"`{RECORD_PATH}`."
)


# What every archive notice's title begins with, whatever date it names. The date is what
# makes the two ends able to spell one title identically; the prefix is what makes the
# ones for OTHER dates findable when this closes the cohort out.
ARCHIVE_NOTICE_PREFIX = "Cohort archives on "


def archive_notice_title(when: date) -> str:
    """The title of the "this cohort is about to freeze" issue.

    Here rather than in the scheduler that OPENS it, because the two ends have to spell it
    identically - `issues` addresses an issue by its exact title - and teardown is the end
    that can never be skipped. The date is in the title so that moving `archive.date` opens
    a notice about the new one rather than silently editing the old one's body."""
    return f"{ARCHIVE_NOTICE_PREFIX}{when}"


def is_archive_notice(title: str) -> bool:
    """Whether an open issue's title is one of this toolkit's archive notices.

    Public, and beside `archive_notice_title` for the same reason: two ends have to agree
    about it. This one closes every dated notice at the seal, and the scheduler closes a
    notice whose date has been moved or taken away while the cohort is still live
    (`scheduler._stale_archive_notices`).

    The prefix AND a date that parses, rather than the prefix alone: `issues` matches by
    exact title precisely so that an issue a human filed quoting one is never adopted, and
    a prefix match on its own would close "Cohort archives on the last day of what?"
    written by an instructor. Nothing this toolkit opens spells the date any other way."""
    if not title.startswith(ARCHIVE_NOTICE_PREFIX):
        return False
    try:
        date.fromisoformat(title[len(ARCHIVE_NOTICE_PREFIX) :])
    except ValueError:
        return False
    return True


class Closed(NamedTuple):
    """What one run froze, in the shape both the summary line and the record read."""

    frozen: list[str]  # every repo this run archived
    already: list[str]  # every repo that was archived before it started
    errors: int


def archive_due(sched: schedule.Schedule, today: date) -> bool:
    """Whether this cohort's archive date has arrived.

    A cohort that writes no `archive:` block has no such date and is never due: archiving
    is opt-in, so closing it out is `--force`, which is a person taking the decision
    instead. Where the block is written, the sixty-day grace after `semester_end` is the
    whole point (`schedule.ARCHIVE_GRACE`), because a term goes on being pushed to for
    weeks after its last class."""
    return sched.archive_date is not None and sched.archive_date <= today


def _is_template(repo: dict) -> bool:
    """Whether a listing row is an assignment TEMPLATE rather than somebody's work."""
    return bool(repo.get("isTemplate")) or ASSIGNMENT_TEMPLATE_TOPIC in (
        repo.get("topics") or []
    )


def freeze_order(cohort_org: str, repos: list[dict]) -> list[dict]:
    """Every repo in the org except `classroom-config`, in the order they are frozen.

    Students' work first, because it is the cohort's record and the reason any of this is
    reversible rather than a delete. `welcome` next: its Join issues are how a student
    enrols themselves, and an open one on a finished cohort writes into a
    `classroom-config` that is about to be sealed - a red run instead of a place. Then the
    released content, then the website (after step 2's final sync), and the cohort's own
    `.github` last of the live repos, because it holds the dispatchers that wake everything
    else.

    `classroom-config` is not here at all: it is the marker, and the caller freezes it
    after the record is written."""
    derived = classify_repos(repos)
    site_repo = pages_repo(cohort_org)

    def rank(repo: dict) -> int:
        name = repo["name"]
        if is_student_repo(repo, derived) or _is_template(repo):
            return 0
        if name == WELCOME_REPO:
            return 1
        if name == site_repo:
            return 3
        return 4 if name == ".github" else 2

    return sorted(
        (r for r in repos if r["name"] != CONFIG_REPO),
        key=lambda r: (rank(r), r["name"]),
    )


def registrar_summary(cohort_org: str) -> str:
    """One plain-text line for the record: what the registrar export holds, or that there
    is none. A ROW COUNT, never a row - this string is printed to the run log too, and the
    log of a course org's `.github` is world-readable."""
    text = get_file_content(cohort_org, CONFIG_REPO, COHORT_CSV_NAME)
    if text is None:
        return f"{COHORT_CSV_NAME} is NOT here - no grade was ever distributed"
    rows = len(list(read_csv(text, (), COHORT_CSV_NAME)))
    return f"{COHORT_CSV_NAME}, {rows} student row(s)"


def render_record(
    cohort_org: str,
    closed: Closed,
    *,
    sealed_on: date,
    archive_date: date | None,
    registrar: str,
    propagated: str,
) -> str:
    """The teardown record, as it is written into the private `classroom-config`.

    It names the repos it froze. That is the point of it and it is safe: this file is
    written into the cohort's PRIVATE record, beside the roster those names are drawn from,
    and never into a log or a page. Nothing in this function is ever printed."""
    due = (
        f"Its archive date was **{archive_date}**."
        if archive_date
        else "`schedule.yml` declares no archive date - closed out with `--force`."
    )
    frozen = sorted(closed.frozen + closed.already)
    rows = "\n".join(f"- `{name}`" for name in frozen) or "- (none)"
    return f"""{_RECORD_BANNER}
# Teardown record - {cohort_org}

Closed out on **{sealed_on}** (UTC). {due}

| What | Detail |
| --- | --- |
| Repositories frozen | {len(frozen)} ({len(closed.already)} already were) |
| Cohort edits carried back | {propagated} |
| Registrar export | {registrar} |

**Nobody was revoked.** An archived repository is read-only for everyone, so freezing IS
the withdrawal of write access - and everyone who could read this cohort still can, which
is the point of closing it rather than deleting it. Org membership and teams are untouched.

**Nothing was deleted.** Archiving is GitHub's reversible read-only freeze: un-archive a
repo from its own Settings page and it comes back exactly as it was, and `students.csv` is
what re-grants a student their access if one has to be reopened.

{_RETENTION_NOTE}

## Frozen

{rows}
"""


def _carry_back(course_org: str, cohort_org: str, dry_run: bool) -> tuple[str, int]:
    """Step 0: offer this cohort's edits back to the course org. `(what the record says,
    errors)`.

    Nothing here can stop the cohort being sealed. A course org that never wanted the
    cohort's edits is still entitled to a closed cohort, and leaving one half-frozen
    because a clone failed would be the worst of both. The failure is counted, so the run
    goes red and a person can re-run it - which is safe, because everything else is
    idempotent - and it is written into the record either way.

    Swallows everything for the same reason: this is the one step that talks to a second
    org, so it has twice as many ways to raise.

    A cohort repo the propagate did not READ is the one outcome that is neither an error
    there nor a success here. `propagate` skips a dest whose `upstream` is ahead - a
    standing conflict pull request - and names it in the pull request body; but with
    every due dest behind there is no pull request, so it came back as
    `Propagated(0, ())` and this wrote "nothing had been edited" over a cohort whose
    edits were never looked at, into a record that is then sealed read-only. So it is
    named in the record, with what to do about it, and counted - the seal still lands
    (nothing here can hold it back), the run reds, and the edits are still there to
    recover before anybody un-archives the org."""
    if not course_org:
        log(
            "  [skip] no --course-org given, so this cohort's edits are NOT being "
            "carried back. Run Propagate cohort edits before un-archiving anything."
        )
        return "not offered - no course org was named", 0
    try:
        done = propagate.propagate(course_org, cohort_org, dry_run=dry_run)
    except Exception as exc:
        log_err(
            f"could not carry {cohort_org}'s edits back to {course_org} "
            f"({type(exc).__name__}): {exc}"
        )
        return "FAILED - the cohort was sealed without carrying them back", 1
    said: list[str] = []
    if done.errors:
        said.append(f"{done.errors} path(s) could not be carried back")
    if done.urls:
        said.append(", ".join(done.urls))
    if done.behind:
        said.append(
            f"NOT read: {', '.join(f'`{r}`' for r in done.behind)} - behind its latest "
            f"release. Merge the open `{UPSTREAM_BRANCH}` pull request there and re-run "
            f"Propagate cohort edits before un-archiving anything"
        )
    unread = 1 if done.behind else 0
    return "; ".join(said) or "nothing had been edited", done.errors + unread


def _close_notices(cohort_org: str, dry_run: bool) -> int:
    """Step 1: close the toolkit's own open issues in `classroom-config`.

    Every one of them asks somebody to go and fix a file in a repo that is about to be
    read-only, and nothing will ever close them afterwards - an archived repo takes no
    issue write either, so this is the last moment. Closed with a comment, because closing
    them silently would read as "fixed".

    EVERY title the toolkit can leave open in this repo, which is the six hand-edited-file
    digests, the schedule's own source digest, the cadence alarm and the archive notice -
    those, and nothing else, are what write here (`cadence.report_cohort` is the one that
    is not a digest). A title missed here stands open inside a frozen repo for ever, since
    the sweep that would have closed it never runs on a closed-out cohort again.

    The archive notice is the one whose title MOVES: it names a date, and `archive.date`
    can be moved after one is open, which opens a second notice rather than editing the
    first (`archive_notice_title` says why). So every dated notice standing in the repo is
    closed, not just the one for today's date - the earlier one would otherwise be sealed
    in, open, contradicting the record."""
    repo = f"{cohort_org}/{CONFIG_REPO}"
    titles = [d.title for d in config_digest.COHORT_DIGESTS] + [
        source_digest.TITLE,
        cadence.LATE_TITLE,
    ]
    if dry_run:
        log(
            f"    DRY-RUN close any of {len(titles)} toolkit notice(s) still open, and "
            f"every `{ARCHIVE_NOTICE_PREFIX.strip()} <date>` notice with them"
        )
        return 0
    errors = 0
    try:
        titles += sorted(t for t in open_titles(repo) if is_archive_notice(t))
    except RuntimeError as exc:
        # A listing that could not be read is not "no notice is open", and the rest of
        # them are still worth closing while the repo takes writes.
        log_err(str(exc))
        errors = 1
    return errors + sum(
        close_issues_titled(repo, title, _CLOSING_COMMENT) for title in titles
    )


def _final_sync(course_org: str, cohort_org: str, dry_run: bool) -> int:
    """Step 2: one last website sync, before the site repo is frozen.

    The deployed site carries the cohort's own "Cohort archived" schedule row
    (`site._archive_entry`), and without this the last thing it ever says is whatever the
    term's final release left there. Swallows everything: a site that is one sync behind is
    not a reason to leave a cohort half-closed."""
    if not course_org:
        return 0
    if dry_run:
        log(f"    DRY-RUN sync {cohort_org}'s website one last time")
        return 0
    try:
        if site.sync_site(course_org, cohort_org) != 0:
            log_err(f"{cohort_org}'s final website sync was incomplete")
            return 1
    except Exception as exc:
        log_err(f"{cohort_org}'s final website sync failed: {exc}")
        return 1
    return 0


def _freeze(cohort_org: str, listing: list[dict], dry_run: bool) -> Closed:
    """Step 3: archive every repo in the org but `classroom-config`, in `freeze_order`.

    Per-repo lines go through `log_person`, because a `<slug>-<handle>` on stdout of a
    workflow running in the course org's PUBLIC `.github` publishes who was in the
    cohort."""
    frozen: list[str] = []
    already: list[str] = []
    errors = 0
    for repo in freeze_order(cohort_org, listing):
        name = repo["name"]
        if repo.get("archived"):
            already.append(name)
            continue
        if dry_run:
            log_person(f"    DRY-RUN archive {cohort_org}/{name}")
            frozen.append(name)
        elif archive_repo(cohort_org, name, person=True):
            log_person(f"  [ok] archived {cohort_org}/{name}")
            frozen.append(name)
        else:
            errors += 1
    return Closed(frozen, already, errors)


def close_out(
    course_org: str,
    cohort_org: str,
    dry_run: bool = True,
    force: bool = False,
    today: date | None = None,
) -> int:
    """Close `cohort_org` out. 0 when it is closed (or already was), 1 on any failure.

    `course_org` may be `""`: the cohort is still closed, but its edits are not offered
    back and its website is not synced one last time, and the record says so.

    `today` is the date the archive-date gate is decided against, and the CALLER's clock
    where it has one: the scheduler decides the cohort is due off its own `now` (which
    `--now` can set), and re-deriving the date here meant a run that had decided to
    archive then refused to. Defaults to today, which is what the CLI wants. The record's
    `sealed_on` is the real clock either way - it is when the freeze actually happened.

    Only a failed FREEZE holds the seal back, because that is the one failure the marker
    would lie about: a `classroom-config` archived over a repo that is still live tells
    every sweep the cohort is finished when it is not. Everything else - the propagate, the
    notices, the final sync - counts towards the exit code and is left for a re-run, which
    is safe because every step here is idempotent.

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
    if not archive_due(sched, today or datetime.now(timezone.utc).date()) and not force:
        declared = (
            f"archives on {sched.archive_date}"
            if sched.archive_date
            else "names no archive date - it has no `archive:` block, or none with a "
            "date anything can be derived from"
        )
        not_due = (
            f"{cohort_org} is not due to be archived: {CONFIG_REPO}/"
            f"{schedule.SCHEDULE_PATH} {declared}."
        )
        if not dry_run:
            log_err(
                f"{not_due} Wait for that date, change it, or re-run with --force to "
                f"close the cohort out now."
            )
            return 1
        # A dry run freezes nothing, and printing the counts is how somebody decides
        # whether to ask for a close-out at all - so refusing to print them until the
        # date has passed answers the question only once it no longer needs asking.
        # It goes on, and says what the real run would need.
        log(f"  {not_due} A real run would need --force.")

    registrar = registrar_summary(cohort_org)
    log(f"  registrar export: {registrar}")
    propagated, errors = _carry_back(course_org, cohort_org, dry_run)
    errors += _close_notices(cohort_org, dry_run)
    errors += _final_sync(course_org, cohort_org, dry_run)

    closed = _freeze(cohort_org, listing, dry_run)
    if closed.errors:
        log_err(
            f"{closed.errors} repo(s) could not be frozen - {cohort_org} is NOT sealed. "
            f"Fix the cause and run this again; it resumes from wherever it stopped."
        )
        return 1
    if dry_run:
        log_ok(
            f"dry run: {len(closed.frozen)} repo(s) would be frozen "
            f"({len(closed.already)} already are), then {CONFIG_REPO} sealed"
        )
        return 1 if errors else 0

    record = render_record(
        cohort_org,
        closed,
        sealed_on=datetime.now(timezone.utc).date(),
        archive_date=sched.archive_date,
        registrar=registrar,
        propagated=propagated,
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
            f"the record is written but {cohort_org}/{CONFIG_REPO} is NOT sealed, so "
            f"every nightly sweep still treats this cohort as live. Re-run to finish."
        )
        return 1
    log_ok(
        f"{cohort_org} closed out: {len(closed.frozen) + len(closed.already)} repo(s) "
        f"frozen, {CONFIG_REPO} sealed"
    )
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--course-org",
        default="",
        help="Course org - where this cohort's edits are offered back before it is "
        "frozen, and where its website is synced from. Omit and both are skipped.",
    )
    parser.add_argument("--cohort-org", required=True)
    # Default ON, like every other write button: the rendered workflow passes --dry-run /
    # --no-dry-run explicitly, so a bare local invocation cannot freeze a cohort by accident.
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print the counts; freeze nothing (default).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Close out even though the archive date has not arrived (or is not set).",
    )
    args = parser.parse_args()

    # A read helper that could not reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        return close_out(
            args.course_org,
            args.cohort_org,
            dry_run=args.dry_run,
            force=args.force,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
