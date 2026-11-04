"""dsl-course deploy -- publish path(s) from a course-org source repo into a semester-org
repo, additively:

    source/<repo>/<course_source_path>          (a folder - e.g. lectures/02_intro - or a file)
            |  copy that path
            v
    semester/<semester_dest_repo>/<semester_dest_path>       (private + students read; accumulates over time)

The copy lands on the dest's `upstream` branch and is MERGED into the branch students
read (see `UPSTREAM_BRANCH`).

`deploy_many` is the batch core AND the single executor of every release in the system:
it clones each unique source repo and each unique dest repo ONCE per run and applies every
copy against those working trees, so a scheduler run releasing 27 paths from one source
clones it once, not 27 times. Both callers arrive here - the hourly scheduler
(scheduler._run_releases, straight from each `deploy:` entry in schedule.yml) and the
manual "Release materials" workflow (via `main` below, whose five inputs are deliberately the
same five fields as a `deploy:` entry).

The workflow's four inputs are comma-separated PARALLEL lists paired by index
(parse_deploy_lists): the paths pair one to one, and a repo list of one item applies to
every pair - so one hand release may draw on several source repos and land in several
destination repos, the semester's `materials_repo` when it names none. One Deploy per
pair, one deploy_many call for the batch.

`--entry KEY` releases one schedule.yml entry instead: every copy it lists, from every
source repo it draws on, in ONE deploy_many call with one site sync.

A release is ONE copy at its time (decision 0043): `deploy_many` reports, per copy, what
it copied and whether it landed, and the caller records each landed copy in the
semester's release record (`release_record`). The scheduler ships a recorded copy again
only when the semester autosyncs released edits; this CLI always copies - it is the
deliberate path (Release early / now / again, or a hand release) - and records every copy
of `--entry`, and every schedule copy a release of paths covers.

Usage:
    python3 -m dsl_course.deploy \\
        --course-org COURSE --course-source-repo course-materials-f2026 \\
        --semester-org SEMESTER --semester-dest-repo materials \\
        --course-source-path "lectures/02_intro,labs/02_lab" [--semester-dest-path "week02/lecture,week02/lab"]
    python3 -m dsl_course.deploy --course-org COURSE --semester-org SEMESTER --entry lecture-2
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from . import divergence, pulls, release_record, schedule, site
from .access import (
    COURSE_TEAM_ACCESS,
    grant_faculty,
    grant_read_teams,
    repo_team_permissions,
)
from .course import (
    INSTRUCTORS_TEAM,
    UPSTREAM_BRANCH,
    is_repo_root,
)
from .discovery import reserved_destination
from .fs import copy_tree
from .gh_contents import (
    WITHHELD_ROOT_STUBS,
    blob_sha,
    is_withheld_stub,
)
from .ghcli import GIT_ENV, clone, git
from .log import (
    CLIParser,
    Summary,
    add_preview_flag,
    comma_items,
    log,
    log_err,
    log_ok,
    log_step,
    log_withheld,
    plural,
)
from .releaseignore import RELEASEIGNORE, excludes
from .repos import (
    allow_forking,
    create_repo,
    default_branch,
    repo_is_archived,
)
from .schedule import DEFAULT_DEST_REPO, Deploy
from .schedule_plan import (
    covered_copies,
    deploy_dest,
    outermost,
    refuse_unnumbered,
    unnumbered_release,
)
from .selection import HARD, RELEASE, part_groups, release_ignore, release_use
from .selection import deny as selection_deny


def _warn_withheld_stub(course_org: str, repo: str, path: str) -> None:
    """Say what was withheld and how to fix it - visibly, but WITHOUT failing the release.

    Withholding an unwritten stub is this guard working, not a fault: the release did
    exactly what it should. Counting it as an error reddened a run whose every other copy
    shipped, and - because the hourly scheduler drives the same `deploy_many` - would have
    reddened the Scheduled release cron every hour, forever, for any course that never
    rewrote its README. A permanently red cron is how real failures stop being noticed.

    So it takes the channel this codebase already uses for "true, worth seeing, not a
    failure" (see `templates/semester-config/validate-schedule.yml`): a `::warning::`
    annotation on a green run, which touches no exit code."""
    log_withheld(
        f"{course_org}/{repo}/{path} was NOT released - it is still the scaffold stub, "
        "written for faculty rather than students. Everything else in this release "
        "shipped. Write it for students, then release again."
    )


def _warn_ignored_source(course_org: str, repo: str, path: str) -> None:
    """Say that a release's own source path is excluded by a `.releaseignore`.

    Faculty naming a path outright is a clear ask, so answering it with nothing at all
    would be the wrong kind of quiet - `git add <ignored-path>` refuses out loud too
    rather than adding the file. It takes the ::warning::-on-a-green-run channel instead
    of an error for the reason in `_warn_withheld_stub`: the hourly scheduler runs through
    the same `deploy_many`."""
    log_withheld(
        f"{course_org}/{repo}/{path} was NOT released - a `{RELEASEIGNORE}` excludes it. "
        "Everything else in this release shipped. Drop the pattern that matches it, or "
        "release a path that is not excluded."
    )


def _warn_never_released(course_org: str, repo: str, path: str) -> None:
    """Say that a release's own source path is on the institution's never-released list
    (`selection.DENYLIST_HARD`: secrets, git metadata, grading files, `.system/`).

    Refused out loud for `_warn_ignored_source`'s reason, on its channel. Unlike that one
    there is nothing for faculty to drop: no pattern releases these (decision B)."""
    log_withheld(
        f"{course_org}/{repo}/{path} was NOT released - never released: "
        f"{path.strip('/')} is on the institution's list. Everything else in this "
        "release shipped."
    )


def _resolve_within(base: Path, rel: str) -> Path | None:
    """Resolve `rel` under the clone `base`, or None if it escapes it.

    The "release everything" spellings all name the root of `base` - `course.is_repo_root`
    owns which they are, because the schedule validator has to skip the same ones. A `..`
    path resolving outside the clone is refused: no reading of it is a release, and it is
    caught before any file is touched."""
    base_r = base.resolve()
    if is_repo_root(rel):
        return base_r
    target = (base / rel.strip("/")).resolve()
    return target if target.is_relative_to(base_r) else None


class Dest(NamedTuple):
    """One prepared release destination: the clone every copy for it lands in, the branch
    students read, whether the repo had NO COMMITS when it was cloned, and whether the
    REMOTE already carried `UPSTREAM_BRANCH`.

    `unborn` is carried rather than re-probed because it answers both of the questions the
    merge phase would otherwise ask git again: an empty clone has no HEAD until this run
    commits one, and no local `base` branch at all.

    `upstream_on_remote` cannot be re-derived by then at all: the clone has a local
    `upstream` either way once `_checkout_upstream` has run, so only the code that put it
    there knows whether the branch was fetched or invented - and a branch this run
    invented has to be pushed even on the path that pushes nothing else."""

    dir: Path
    base: str
    unborn: bool
    upstream_on_remote: bool


def _has_ref(dd: Path, ref: str) -> bool:
    """Whether `ref` resolves in a dest clone."""
    return git("-C", str(dd), "rev-parse", "--verify", "--quiet", ref)[0] == 0


def _checkout_upstream(semester_org: str, repo: str, dd: Path) -> Dest | None:
    """Put a dest clone on `UPSTREAM_BRANCH` and describe it (see `Dest`).

    None when the checkout itself failed, or when the dest's default branch IS
    `UPSTREAM_BRANCH`, which the caller treats exactly like a dest
    that would not clone: the copies for that dest are impossible, and it is dropped
    here rather than left to fail later. The return codes used to be discarded, so a
    refused checkout left the clone on its BASE branch - the release then copied onto
    the branch students read, and the merge phase reported two errors about a branch
    that was never cut.

    The base is read off the clone's own HEAD - `clone` checks out whatever the repo calls
    its default - rather than assumed to be `main`: a dest created by hand may call it
    anything, and releasing onto a branch nobody reads is silent. A clone with NO COMMITS
    is the exception: its HEAD names whatever the local git would have called a first
    branch, which has nothing to do with what the repo says, so the repo is asked instead
    and `main` is the last resort - guessing wrong there only names the branch this first
    release creates. One probe answers both halves: `--abbrev-ref` prints the branch, and
    an unborn HEAD is what fails `--verify`.

    A semester released into before any of this existed gets an `upstream` holding exactly
    what it was last released - cut here, and reported back as not yet on the remote (see
    `Dest`), because the merge phase is the only thing that can push it."""
    code, out = git(
        "-C", str(dd), "rev-parse", "--abbrev-ref", "--verify", "--quiet", "HEAD"
    )
    unborn = code != 0
    base = (
        out.strip()
        if code == 0 and out.strip()
        else default_branch(semester_org, repo, fallback="main")
    )
    if base == UPSTREAM_BRANCH:
        # `upstream` as the DEFAULT branch collapses the two ends of the merge into one,
        # and every check below then reads as "already merged": the copy would be
        # committed, `merge-base --is-ancestor upstream upstream` would pass, and the
        # release would report "nothing new to release" having pushed nothing at all.
        # Refused out loud, because silently releasing nothing for ever is the one
        # outcome worse than a red run (maintainers.md names this branch toolkit-owned).
        log_err(
            f"  {semester_org}/{repo}: `{UPSTREAM_BRANCH}` is this repo's DEFAULT branch. "
            f"It is the toolkit's own branch - make something else the default, and "
            f"releases can be merged into it again."
        )
        return None
    remote = f"origin/{UPSTREAM_BRANCH}"
    on_remote = _has_ref(dd, remote)
    if on_remote:
        code, out = git(
            "-C", str(dd), *GIT_ENV, "checkout", "-B", UPSTREAM_BRANCH, remote
        )
    else:
        code, out = git("-C", str(dd), *GIT_ENV, "checkout", "-b", UPSTREAM_BRANCH)
    if code != 0:
        log_err(
            f"  {semester_org}/{repo}: could not check out `{UPSTREAM_BRANCH}` - "
            f"{out[:200]}"
        )
        return None
    return Dest(dd, base, unborn, on_remote)


def _prepare_dest(semester_org: str, repo: str, root: Path) -> Dest | None:
    """Create, grant, clone and put ONE dest repo on `UPSTREAM_BRANCH`. None when it could
    not be prepared - the copies for that dest are impossible either way, and each failure
    has already said so once.

    The grants are here rather than at creation because they CONVERGE: a dest made before
    one of them existed has to get it too, and the release is the only thing that visits a
    dest regularly."""
    create_repo(
        semester_org,
        repo,
        private=True,
        description="Released lectures, labs, readings, & other materials",
    )
    # Visited on every tick, where the dest almost always holds every grant below
    # already: ONE listing of its teams says which of them would change nothing.
    held = repo_team_permissions(semester_org, repo)
    grant_read_teams(semester_org, repo, held)
    # Write, because an edit made here is now DURABLE: the release lands on `upstream`
    # and is merged in, so a correction typed into the semester repo survives the next tick
    # instead of being copied over. It was read for exactly as long as it was not.
    #
    # The course org is still the source of truth - a fix made here reaches next term
    # only when somebody carries it back. The floor (`access.faculty_floor`) stays at
    # read: the sweep never demotes, and this grant runs on every release, so the two
    # agree.
    grant_faculty(
        semester_org, repo, COURSE_TEAM_ACCESS, missing_is_note=True, held=held
    )
    # Students are told to fork the materials and work in their own copy, and a PRIVATE
    # repo is forkable only if BOTH its org and it say so. Converged on every release,
    # not only at creation: the dests that predate this need it too.
    allow_forking(semester_org, repo)
    dd = root / "out" / repo
    if not clone(semester_org, repo, dd):
        log_err(f"could not clone dest {semester_org}/{repo}")
        return None
    # A dest that would not go onto `upstream` is as unusable as one that would not
    # clone: releasing onto the base branch instead is exactly what the merge exists to
    # stop.
    return _checkout_upstream(semester_org, repo, dd)


def _push(dd: Path, *branches: str) -> int:
    """Push branches of a dest clone by NAME, not `HEAD`: a release moves two of them and
    which one is checked out changes with the path taken.

    `--atomic`, so a release never lands half a pair: `upstream` without the branch
    students read leaves the next run merging a branch that never arrived, and the branch
    students read without `upstream` loses the record of what was released."""
    code, _ = git(
        "-C", str(dd), *GIT_ENV, "push", "-q", "--atomic", "origin", *branches
    )
    return code


def _conflict_body(base: str) -> str:
    """The body of the pull request a release opens when its merge conflicts.

    Branch names only. This repo is readable by the whole semester, so nothing about WHO
    edited what belongs here - and no file list either: this pull request's own Files tab
    is that list, and GitHub keeps it current as later releases add to the branch.

    It says to MERGE rather than offering a close, because a close does not settle
    anything: `pulls.find_pr` looks for an OPEN pull request, so the next release finds
    none, and the conflict it is still holding opens a second one. Merging is what ends
    the hold - whichever version the resolution keeps."""
    return (
        f"This release could not be merged into `{base}`: the released copy and this "
        f"repo have both changed the same lines.\n\n"
        f"`{UPSTREAM_BRANCH}` holds everything the course org has released so far, and "
        f"grows with every release.\n\n"
        f"`{base}` is untouched, so the semester still reads what it read before. Resolve "
        f"the conflict here and merge - keeping this repo's version, the released one, "
        f"or a mix of the two. Merging is what settles it: further releases add to "
        f"`{UPSTREAM_BRANCH}` and re-use this pull request, and closing it unresolved "
        f"only means the next release opens the same question again.\n"
    )


def _merge_conflicted(dd: Path, out: str) -> bool:
    """Whether a failed `git merge` failed on CONTENT - the release and the semester having
    changed the same lines - rather than never having started.

    Two tests, because either can be the one that answers: git announces every content
    clash with a `CONFLICT` line, and the index it leaves behind names the unmerged paths.
    Anything else (`refusing to merge unrelated histories`, a dirty tree, a ref that is not
    there) is a merge git REFUSED, and reading that as a conflict is how a dest stops being
    released to: the release is pushed to `upstream`, a pull request is opened about a
    disagreement nobody has, no error is counted, and the run goes green again every
    quarter of an hour for ever with nothing landing on the branch students read."""
    if "CONFLICT" in out:
        return True
    code, unmerged = git("-C", str(dd), "diff", "--name-only", "--diff-filter=U")
    return code == 0 and bool(unmerged.strip())


# What became of one dest's release (`_merge_and_push`): the branch students read moved;
# it already held everything released (the idempotent no-op); the merge conflicted and is
# held at a pull request; or it failed. A copy into a RELEASED or UNCHANGED dest has
# landed - students have it - and is what the release record records.
RELEASED, UNCHANGED, HELD, FAILED = "released", "unchanged", "held", "failed"
LANDED = frozenset({RELEASED, UNCHANGED})


class Replacement(NamedTuple):
    """What a `--replace` release overwrites in one dest: the paths copied into it and the
    files `--remove-deleted` took off `upstream` there."""

    paths: list[str]
    dropped: list[str]


def _replace_paths(dd: Path, base: str, replace: Replacement) -> int:
    """Take the released version of exactly `replace.paths` onto `base`, as one commit:
    what a `--replace` release does before its merge - the hard overwrite.

    One `git checkout upstream -- <path>...` of the outermost paths `upstream` has (a copy
    nested in another is already inside it), then `git rm` of `replace.dropped`. A file the
    semester ADDED under a path is not on `upstream`, so it stays - this discards edits,
    not additions. Answers -1 on a git failure, else whether a commit was made (1) or not
    (0). The merge that follows then has nothing to conflict on at these paths: both
    sides agree."""
    code, out = git("-C", str(dd), *GIT_ENV, "checkout", "-q", base)
    if code != 0:
        log_err(f"  could not check out `{base}` - {out[:200]}")
        return -1
    paths = outermost(replace.paths)
    # `.` for the root, alone after `outermost`: listed, it is present when `upstream`
    # has anything at all. A path not there had nothing of it released (everything
    # excluded).
    specs = [p or "." for p in paths]
    code, out = git(
        "-C", str(dd), "ls-tree", "-z", "--name-only", UPSTREAM_BRANCH, "--", *specs
    )
    if code != 0:
        log_err(f"  could not list `{UPSTREAM_BRANCH}` - {out[:200]}")
        return -1
    listed = {n for n in out.split("\0") if n}
    present = [s for s in specs if s in listed or (s == "." and listed)]
    if present:
        code, out = git(
            "-C", str(dd), *GIT_ENV, "checkout", UPSTREAM_BRANCH, "--", *present
        )
        if code != 0:
            log_err(f"  could not take {', '.join(present)} - {out[:200]}")
            return -1
    if replace.dropped:
        git(
            "-C",
            str(dd),
            *GIT_ENV,
            "rm",
            "-q",
            "--ignore-unmatch",
            "--",
            *replace.dropped,
        )
    if git("-C", str(dd), "diff", "--cached", "--quiet")[0] == 0:
        return 0
    named = ", ".join(p or "(repo root)" for p in paths)
    code, out = git(
        "-C",
        str(dd),
        *GIT_ENV,
        "commit",
        "-q",
        "--no-verify",
        "-m",
        f"release: replace {named} with the course version",
    )
    if code != 0:
        log_err(f"  commit failed - {out[:200]}")
        return -1
    return 1


def _merge_and_push(
    semester_org: str,
    repo: str,
    dest: Dest,
    touched: set[str],
    *,
    committed: bool,
    replace: Replacement | None = None,
) -> tuple[int, str]:
    """Merge one dest's `UPSTREAM_BRANCH` into the branch students read and push both.

    `replace` - a `--replace` release - first takes the released version
    of those paths onto the branch students read (`_replace_paths`), discarding the
    semester's edits to them, so the merge cannot conflict there. The merge then runs as
    usual for everything else `upstream` holds; if THAT conflicts (a pull request already
    standing for other paths), the replaced paths still ship and the pull request stays.

    `(errors, outcome)` - `RELEASED` only when the branch students read actually moved,
    so a release held at a pull request (`HELD`) does not report itself as a change.

    `committed` is whether the caller's commit-on-`upstream` ran this time. With
    `dest.unborn` it answers both of the questions a `rev-parse` used to: a clone that was
    empty and stayed empty has no HEAD to merge from, and one that was empty has no local
    `base` branch to merge into."""
    dd = dest.dir
    if dest.unborn and not committed:
        return 0, UNCHANGED  # an empty dest nothing could be copied into
    replaced = False
    if replace and dest.upstream_on_remote:
        # A dest with no `upstream` yet - empty, or never released to by merge - has
        # nothing to replace: its first release lands on top of `base` anyway.
        made = _replace_paths(dd, dest.base, replace)
        if made < 0:
            log_err(f"  {repo}: could not replace the released paths")
            return 1, FAILED
        replaced = made == 1
    if dest.unborn:
        # A dest this run created: there is nothing to merge into, so the branch students
        # read simply starts where the released copy now is. No `-f`: `base` cannot exist
        # here, and a git that says otherwise is a fact to fail on rather than to
        # overwrite the branch students read with.
        if git("-C", str(dd), *GIT_ENV, "branch", dest.base, UPSTREAM_BRANCH)[0] != 0:
            log_err(f"  {repo}: could not start `{dest.base}`")
            return 1, FAILED
    elif (
        git("-C", str(dd), "merge-base", "--is-ancestor", UPSTREAM_BRANCH, dest.base)[0]
        == 0
        and not replaced
    ):
        # Everything released is already in the branch students read - the idempotent
        # no-op, and the only reason to say so is that somebody pressed the button and is
        # watching for a line about this repo.
        if not dest.upstream_on_remote:
            # A branch this run cut still has to reach the remote. Every other path
            # pushes it as half of the pair below; this one returns before them, and on a
            # semester whose every due release had already landed - every real org on the
            # day it upgrades - that left the remote with no `upstream` at all. The next
            # tick then re-cut it from whatever the base held BY THEN, copied the source
            # over it, committed and fast-forwarded the base onto that: an instructor's
            # first edit to a released file silently reverted, by the merge that exists to
            # keep it.
            if _push(dd, UPSTREAM_BRANCH) != 0:
                log_err(f"  {repo}: could not push `{UPSTREAM_BRANCH}`")
                return 1, FAILED
            log_ok(
                f"  {repo}: `{UPSTREAM_BRANCH}` created - semester edits are kept now"
            )
        if repo in touched:
            log_ok(f"  {repo}: nothing new to release")
        return 0, UNCHANGED
    else:
        code, out = git("-C", str(dd), *GIT_ENV, "checkout", dest.base)
        if code != 0:
            log_err(f"  {repo}: could not check out `{dest.base}` - {out[:200]}")
            return 1, FAILED
        code, out = git(
            "-C",
            str(dd),
            *GIT_ENV,
            "merge",
            "--no-edit",
            "-m",
            f"release: merge {UPSTREAM_BRANCH} into {dest.base}",
            UPSTREAM_BRANCH,
        )
        if code != 0 and not _merge_conflicted(dd, out):
            # git refused the merge rather than failing to resolve it. `--abort` in case
            # one is half-started, and then say so: there is no decision to put to the
            # instructors here, and the conflict path would report success for ever.
            git("-C", str(dd), *GIT_ENV, "merge", "--abort")
            first = out.splitlines()[0] if out.strip() else "git said nothing"
            log_err(
                f"  {repo}: could not merge `{UPSTREAM_BRANCH}` into `{dest.base}` - "
                f"{first[:200]}"
            )
            return 1, FAILED
        if code != 0:
            # The semester has edited what this release also changed. Leave `base` exactly
            # as students last read it, ship the release to `upstream` anyway so nothing
            # is lost, and put the decision in front of the instructors as ONE standing
            # pull request.
            git("-C", str(dd), *GIT_ENV, "merge", "--abort")
            # Only when this run added to `upstream`: a re-offered merge has nothing new
            # on it, so the local branch is already what the remote holds. A replace
            # moved the branch students read as well, and it ships with it.
            pushed = (UPSTREAM_BRANCH, dest.base) if replaced else (UPSTREAM_BRANCH,)
            if (committed or replaced) and _push(dd, *pushed) != 0:
                log_err(f"  {repo}: push failed")
                return 1, FAILED
            opened = pulls.upsert_pr(
                f"{semester_org}/{repo}",
                head=UPSTREAM_BRANCH,
                base=dest.base,
                title=f"Release: merge `{UPSTREAM_BRANCH}` into `{dest.base}`",
                body=_conflict_body(dest.base),
                reviewer=f"{semester_org}/{INSTRUCTORS_TEAM}",
            )
            if opened.url:
                log(f"  {repo}: held for review - {opened.url}")
            if replaced:
                log_ok(f"  {repo}: replaced with the course version")
                return opened.errors, RELEASED
            return opened.errors, HELD
    if _push(dd, UPSTREAM_BRANCH, dest.base) != 0:
        log_err(f"  {repo}: push failed")
        return 1, FAILED
    log_ok(
        f"  {repo}: {'replaced with the course version' if replaced else 'released'}"
    )
    return 0, RELEASED


class Copied(NamedTuple):
    """One copy `deploy_many` made: the Deploy, the source commit it was copied from, its
    manifest (each copied file's path relative to the copy - a single file by its
    destination name - and blob sha: what `.releaseignore`, the denylist and a withheld
    stub left), its dest's outcome (`_merge_and_push`), and the files it removed from
    the destination (`deploy_many`'s `remove`)."""

    deploy: Deploy
    commit: str
    files: dict[str, str]
    outcome: str
    removed: tuple[str, ...] = ()

    @property
    def landed(self) -> bool:
        """Students have it: its dest merged (or already held it), and it copied
        something - a copy everything in it was excluded from has not shipped."""
        return bool(self.files) and self.outcome in LANDED


class Batch(NamedTuple):
    """What `deploy_many` did: `errors` counts copies that could not be applied, `changed`
    is whether any branch students read moved, `copies` every copy it applied."""

    errors: int
    changed: bool
    copies: list[Copied]


def _blob_of(path: Path) -> str:
    """The git blob sha of a file as committed: a symlink is its target's text."""
    if path.is_symlink():
        return blob_sha(str(path.readlink()).encode())
    return blob_sha(path.read_bytes())


def _copy_listed(srcp: Path, destp: Path, deny) -> dict[str, str]:
    """`copy_tree`, answering the manifest of what it copied: `deny` is asked about every
    folder the copy walks, and what it lets through is what lands."""
    files: dict[str, str] = {}

    def ignore(folder: str, names: list[str]) -> set[str]:
        skip = deny(folder, names)
        for name in names:
            p = Path(folder) / name
            if name not in skip and (p.is_symlink() or not p.is_dir()):
                files[p.relative_to(srcp).as_posix()] = _blob_of(p)
        return skip

    copy_tree(srcp, destp, ignore)
    return files


def _remove_dropped(
    destp: Path, files: dict[str, str], paths: Iterable[str]
) -> list[str]:
    """Delete from a folder copy's destination each of `paths` (relative to the copy) that
    the copy no longer carries - never one in its fresh manifest `files`, never one outside
    the destination. The path itself is unlinked, never what a symlink there points at.
    Answers the paths removed, which the commit on `upstream` stages."""
    removed = []
    base = destp.resolve()
    for rel in sorted(set(paths)):
        parts = rel.split("/")
        if rel in files or not rel or rel.startswith("/") or ".." in parts:
            continue
        target = destp / rel
        if not target.parent.resolve().is_relative_to(base):
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
            removed.append(rel)
    return removed


def deploy_many(
    course_org: str,
    semester_org: str,
    deploys: list[Deploy],
    sync: bool = True,
    remove: Mapping[release_record.Key, Collection[str]] | None = None,
    replace: bool = False,
) -> Batch:
    """Apply a batch of Deploy copies, cloning each unique source and dest repo ONCE.

    Every deploy's `course_source_path` is copied from its (course-org) `course_source_repo`
    into its (semester-org) `semester_dest_repo` at `semester_dest_path` (default: mirror
    `course_source_path`). Each touched dest repo gets a single commit on `UPSTREAM_BRANCH`
    covering all its copies, which is then merged into the branch students read; a dest with
    no net change is left alone (idempotent). Returns a `Batch`: `changed` is True if the
    branch students read moved (a release held at a pull request has NOT changed it), and
    `copies` says, per copy applied, what it copied and its dest's outcome - a copy that
    could not be applied, or was withheld whole, is not among them. `sync` runs a single
    website sync at the end when `changed` (callers batching several release kinds pass
    sync=False and sync once themselves).

    `remove` (decision 0043 rule 6, Release again's "also remove"): per copy, by its record
    key, paths relative to the copy to delete from its destination before the merge - each
    only when the copy no longer carries it (`_remove_dropped`), so a file the course still
    releases is never removed. A folder copy only.

    `replace` (the hard overwrite): instead of merging the semester's edits to the copied
    paths with the release, take the released version of exactly those paths onto the
    branch students read (`_replace_paths`); every other path is merged as usual."""
    deploys = [d for d in deploys if d]
    if not deploys:
        return Batch(0, False, [])

    errors = 0
    changed = False
    # Per copy applied: (deploy, source commit, manifest); its outcome comes from its dest.
    applied: list[tuple[Deploy, str, dict[str, str], tuple[str, ...]]] = []
    outcomes: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)

        # 1. clone each unique source repo once (course org)
        src_dirs: dict[str, Path] = {}
        commits: dict[str, str] = {}
        for repo in sorted({d.course_source_repo for d in deploys}):
            sd = root / "src" / repo
            if not clone(course_org, repo, sd):
                log_err(f"could not clone source {course_org}/{repo}")
            else:
                src_dirs[repo] = sd
                commits[repo] = git("-C", str(sd), "rev-parse", "HEAD")[1].strip()
        # Each source's `.releaseignore` files, read once for every copy out of it: the
        # copy's own path is judged by them (`excludes`), and its folder walk too.
        ignores = {repo: release_ignore(sd.resolve()) for repo, sd in src_dirs.items()}

        # 2. clone (create if needed) each unique dest repo once (semester org)
        dests: dict[str, Dest] = {}
        archived: set[str] = set()
        for repo in sorted({d.semester_dest_repo for d in deploys}):
            if repo_is_archived(semester_org, repo):
                # A closed semester. Everything below - the grants, the commit, the push -
                # 403s on an archived repo, so a schedule that still names one would red
                # this cron for the rest of time. Being finished is a state somebody
                # chose, so it is a line, not an error. (`repo_is_archived` fails open, so
                # a flag that could not be read releases as usual and the write itself is
                # the alarm.)
                log(f"  [skip] {semester_org}/{repo} is archived")
                archived.add(repo)
                continue
            dest = _prepare_dest(semester_org, repo, root)
            if dest is not None:
                dests[repo] = dest

        # A deploy into an ARCHIVED dest is not a failure and not a copy: it was skipped
        # on purpose, and counting it would red every run of a course that has closed one
        # of its semesters. Dropping it here leaves everything below counting failures.
        deploys = [d for d in deploys if d.semester_dest_repo not in archived]

        # A deploy whose source or dest could not be prepared - the clone, or the dest's
        # `upstream` checkout - is one impossible copy. Count it ONCE, per deploy, not
        # once per failure (both ends failing is still one copy lost).
        errors += sum(
            1
            for d in deploys
            if d.course_source_repo not in src_dirs or d.semester_dest_repo not in dests
        )

        # 3. apply every copy against the already-cloned trees
        touched: set[str] = set()
        # Per dest, for `replace` only: what it overwrites there.
        replaced_paths: dict[str, Replacement] = {}
        for d in deploys:
            if (
                d.course_source_repo not in src_dirs
                or d.semester_dest_repo not in dests
            ):
                continue  # its source/dest failed to clone (already counted)
            # A root semester_dest_path means the dest repo's root, exactly as a root
            # course_source_path means the source repo's - no mirror-the-source fallback.
            # The rule is stated once (schedule_plan.deploy_dest) because the site reads
            # the same rule to work out which schedule row a release lands in; two copies
            # is how a release and its row come to disagree about where it went.
            dest_rel = deploy_dest(d)
            src_root = src_dirs[d.course_source_repo].resolve()
            srcp = _resolve_within(src_root, d.course_source_path)
            if srcp is None:
                log_err(
                    f"unsafe course_source_path `{d.course_source_path}` for "
                    f"{course_org}/{d.course_source_repo} - it escapes the clone. skipped."
                )
                errors += 1
                continue
            destp = _resolve_within(dests[d.semester_dest_repo].dir, dest_rel)
            if destp is None:
                log_err(
                    f"unsafe semester_dest_path `{dest_rel}` for "
                    f"{semester_org}/{d.semester_dest_repo} - skipped."
                )
                errors += 1
                continue
            if not srcp.exists():
                log_err(
                    f"`{d.course_source_path}` not found in "
                    f"{course_org}/{d.course_source_repo} - skipped."
                )
                errors += 1
                continue
            ignore = ignores[d.course_source_repo]
            if srcp != src_root and HARD in part_groups(
                srcp.relative_to(src_root).as_posix()
            ):
                # Never released, by any route (decision B): a release naming a `.env`, a
                # grading file or `.system/` - or a path inside one - copies nothing.
                _warn_never_released(
                    course_org, d.course_source_repo, d.course_source_path
                )
                continue
            if excludes(src_root, srcp, ignore):
                # The source path is itself excluded - so is everything under it, and no
                # `!` deeper down can re-include any of it. Nothing else was asked for, so
                # this copy is a no-op rather than a partial one. (A content name -
                # `solutions/`, `tests/` - named outright DOES ship: the default only
                # stops a folder copy sweeping one up - `selection.deny`.)
                _warn_ignored_source(
                    course_org, d.course_source_repo, d.course_source_path
                )
                continue
            try:
                if srcp.is_dir():
                    # A WHOLE-REPO release carries the root README along, which is how the
                    # placeholder actually reached students. Checked on the SOURCE and
                    # skipped before the copy, never deleted after it: faculty who fixed a
                    # leaked placeholder by editing the semester repo's own README would
                    # otherwise have that fix staged as a deletion by the next release -
                    # while the log said everything else shipped.
                    #
                    # Whole-repo only: a copy of one section picks up that section's own
                    # `README.md`, which is faculty writing about the section, not the stub.
                    withheld = frozenset()
                    if srcp == src_root:
                        for stub in WITHHELD_ROOT_STUBS:
                            f = srcp / stub
                            if f.is_file() and is_withheld_stub(
                                stub, f.read_text(encoding="utf-8", errors="replace")
                            ):
                                withheld |= {stub}
                                _warn_withheld_stub(
                                    course_org, d.course_source_repo, stub
                                )
                    # The selection's layers (decision 0040): the institution denylist,
                    # faculty's `.releaseignore`, this release's own exclusions. Anchored
                    # at the CLONE root even for a subpath copy, as a root `.gitignore`.
                    files = _copy_listed(
                        srcp,
                        destp,
                        selection_deny(
                            src_root,
                            use=release_use(srcp == src_root, withheld),
                            reincludable=RELEASE,
                            ignore=ignore,
                        ),
                    )
                elif is_withheld_stub(
                    d.course_source_path,
                    srcp.read_text(encoding="utf-8", errors="replace"),
                ):
                    # Named outright rather than swept up by a whole-repo release: nothing
                    # else was asked for, so this copy is simply a no-op.
                    _warn_withheld_stub(
                        course_org, d.course_source_repo, d.course_source_path
                    )
                    continue
                else:
                    destp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(srcp, destp)
                    files = {destp.name: _blob_of(srcp)}
            except (shutil.Error, OSError) as exc:
                # One unreadable path is ONE failed copy, counted like any other - not an
                # exception out of deploy_many that takes every other release with it.
                log_err(
                    f"could not copy `{d.course_source_path}` from "
                    f"{course_org}/{d.course_source_repo}: {exc}"
                )
                errors += 1
                continue
            log_ok(f"+ {d.semester_dest_repo}/{dest_rel or '(repo root)'}")
            removed: list[str] = []
            if remove and destp.is_dir():
                removed = _remove_dropped(
                    destp, files, remove.get(release_record.key_of(d), ())
                )
                for rel in removed:
                    log_ok(
                        f"- {d.semester_dest_repo}/{dest_rel}/{rel}".replace("//", "/")
                    )
            touched.add(d.semester_dest_repo)
            if replace:
                at = replaced_paths.setdefault(
                    d.semester_dest_repo, Replacement([], [])
                )
                at.paths.append(dest_rel)
                at.dropped.extend(f"{dest_rel}/{rel}".lstrip("/") for rel in removed)
            applied.append((d, commits[d.course_source_repo], files, tuple(removed)))

        # 4. one commit on `upstream` per dest, then merge it into the branch students
        # read. Every CLONED dest, not just the ones this run copied into: a run whose
        # merge conflicted leaves `upstream` ahead with a pull request standing, and the
        # next run has to re-offer that merge (and re-find the same pull request) even
        # when it had nothing new of its own to add.
        for repo in sorted(dests):
            dest = dests[repo]
            dd = dest.dir
            # -f: what was copied IS the release. A whole-repo release brings the source's
            # own `.gitignore` along, and without -f `git add` would then silently drop any
            # file the source force-added past it (lecture PDFs under a `*.pdf` rule are the
            # usual case) - reporting the release as shipped while those files never left.
            git("-C", str(dd), *GIT_ENV, "add", "-A", "-f")
            # Distinguish "nothing staged" (genuinely nothing new to release - the
            # idempotent no-op) from a real commit failure (disk, lock, hook): git commit
            # exits non-zero for BOTH, so a failed commit would otherwise be reported as
            # "nothing new to release" and silently lost.
            committed = False
            if git("-C", str(dd), "diff", "--cached", "--quiet")[0] != 0:
                code, out = git(
                    "-C",
                    str(dd),
                    *GIT_ENV,
                    "commit",
                    "-q",
                    "--no-verify",
                    "-m",
                    f"release: sync materials into {repo}",
                )
                if code != 0:
                    log_err(f"  {repo}: commit failed - {out[:200]}")
                    errors += 1
                    outcomes[repo] = FAILED
                    continue
                committed = True
            merge_errors, outcomes[repo] = _merge_and_push(
                semester_org,
                repo,
                dest,
                touched,
                committed=committed,
                replace=replaced_paths.get(repo),
            )
            errors += merge_errors
            changed = changed or outcomes[repo] == RELEASED

    if sync and changed:
        errors += _sync_site(course_org, semester_org)
    copies = [
        Copied(d, commit, files, outcomes.get(d.semester_dest_repo, FAILED), removed)
        for d, commit, files, removed in applied
    ]
    return Batch(errors, changed, copies)


def _sync_site(course_org: str, semester_org: str) -> int:
    """The website sync after a release, as an error count. site.sync_site RAISES on a
    genuine tree/team read failure - one semester's site-sync failure must be logged and
    counted (making the release non-zero), not an unhandled traceback that aborts the
    batch."""
    try:
        if site.sync_site(course_org, semester_org) != 0:
            log_err("site sync incomplete after release")
            return 1
    except Exception as exc:
        log_err(f"site sync failed after release: {exc}")
        return 1
    return 0


def _per_pair(repos: list[str], n: int, what: str) -> list[str]:
    """`repos` spread over `n` path pairs: one repo applies to every pair, otherwise one
    per pair - the same loud mismatch as the paths'."""
    if len(repos) == 1:
        return repos * n
    if len(repos) != n:
        raise ValueError(
            f"{len(repos)} {what}s but {n} course_source_paths - give one {what} per "
            f"course_source_path (paired in order), or one {what} for them all"
        )
    return repos


def parse_deploy_lists(
    source_repos: str,
    source_paths: str,
    dest_repos: str = "",
    dest_paths: str = "",
    default_dest: str = DEFAULT_DEST_REPO,
) -> list[Deploy]:
    """The Release materials workflow's four comma-separated lists as one Deploy per
    source path, paired by index.

    A blank `dest_paths` mirrors every source path (`None` dest, exactly what an omitted
    `semester_dest_path:` means in schedule.yml). Otherwise the counts MUST match: unlike
    the schedule (which drops what it can't pair, on an unattended cron), a workflow run
    has an operator watching it, so a mismatch is a loud ValueError naming both counts
    rather than a silently short release. A repo list of one applies to every path; a
    blank `dest_repos` is `default_dest` (the semester's `materials_repo`) for every
    path. Surrounding whitespace is stripped and empty items (a trailing comma) are
    ignored throughout."""
    paths = comma_items(source_paths)
    if not paths:
        raise ValueError("--course-source-path is empty")
    landings: list[str | None] = comma_items(dest_paths) or [None] * len(paths)
    if len(landings) != len(paths):
        raise ValueError(
            f"{len(paths)} course_source_paths but {len(landings)} semester_dest_paths - "
            f"give one semester_dest_path per course_source_path (paired in order), or "
            f"leave semester_dest_path blank to mirror every course_source_path"
        )
    sources = comma_items(source_repos)
    if not sources:
        raise ValueError("--course-source-repo is empty")
    sources = _per_pair(sources, len(paths), "course_source_repo")
    dests = _per_pair(
        comma_items(dest_repos) or [default_dest], len(paths), "semester_dest_repo"
    )
    return [
        Deploy(repo, src, dest_repo, dest)
        for repo, src, dest_repo, dest in zip(sources, paths, dests, landings)
    ]


def _entry_of(sched: schedule.Schedule, label: str) -> schedule.Release | str:
    """The schedule entry `label`, or the refusal to say instead."""
    found = next((r for r in sched.releases if r.label == label), None)
    if found is None:
        return f"{label} is not an entry in this semester's schedule.yml."
    return found


def _record(
    semester_org: str,
    sched: schedule.Schedule,
    entry: schedule.Release | None,
    copies: list[Copied],
) -> int:
    """Record every copy that landed (`release_record`): each copy of `entry`, or - for a
    release of paths - each schedule copy those paths made (`covered_copies`), whatever
    its planned moment: a hand release ahead of schedule IS that copy's release (decision
    0043 rule 1), so the scheduler does not copy it again at its time. A path the
    schedule does not copy stays unrecorded. A deliberate release, so each recorded
    copy's manifest and `last_shipped_at` are refreshed. A copy recorded here for the
    first time that beat its plan, or had none, has its ship time written into
    schedule.yml (`schedule.record_release_times`); one the record already had is a
    Release again and writes nothing. Returns the error count."""
    landed = [c for c in copies if c.landed]
    if not landed:
        return 0
    try:
        record = release_record.load(semester_org)
    except (RuntimeError, ValueError) as exc:
        log_err(f"could not read {release_record.PATH} ({exc}) - not recorded")
        return 1
    before = set(record.copies)
    first: dict[release_record.Key, tuple[schedule.Release, Deploy, datetime]] = {}
    at = datetime.now(UTC)
    for c in landed:
        if entry is not None:
            made = [(entry, c.deploy, c.files)]
        else:
            made = [
                (release, planned, files)
                for release, planned, sub in covered_copies(sched, c.deploy)
                if (files := release_record.inside(c.files, sub))
            ]
        for release, d, files in made:
            if (k := release_record.key_of(d)) not in before:
                first.setdefault(k, (release, d, at))
            # Only Release again's "also remove" removes anything (`deploy_many`).
            record.ship(
                release.label, d, c.commit, files, at, refresh=True, removed=c.removed
            )
    if not record.save(semester_org):
        log_err(f"could not write {release_record.PATH} - this release is not recorded")
        return 1
    schedule.record_release_times(semester_org, sched.timezone, list(first.values()))
    return 0


def _named(org: str, names: list[str], what: str) -> tuple[str, str]:
    """One repo by name, or several by count - the Summary's subject or object - and the
    log's words for it: `org/repo`, or "2 source repos in org"."""
    unique = list(dict.fromkeys(names))
    if len(unique) == 1:
        return unique[0], f"{org}/{unique[0]}"
    named = plural(len(unique), what)
    return named, f"{named} in {org}"


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("--course-org", required=True, help="Course org (source)")
    parser.add_argument(
        "--course-source-repo",
        help="Source repo holding the path(s), or a comma-separated list paired with "
        "--course-source-path by index (one repo applies to every path)",
    )
    parser.add_argument("--semester-org", required=True, help="Semester org (target)")
    parser.add_argument(
        "--semester-dest-repo",
        default="",
        help="Target repo in the semester org, created if missing, or a "
        "comma-separated list paired with --course-source-path by index (one repo "
        "applies to every path; default: the semester's materials_repo)",
    )
    parser.add_argument(
        "--course-source-path",
        help="Source path(s) to release - a folder/file, or a comma-separated list",
    )
    parser.add_argument(
        "--semester-dest-path",
        default="",
        help="Destination path(s), paired with --course-source-path by index "
        "(default: mirror each --course-source-path)",
    )
    parser.add_argument(
        "--entry",
        default="",
        help="A schedule.yml releases key: release every copy it lists, from every "
        "source repo, in one batch with one site sync (instead of the four fields above)",
    )
    parser.add_argument(
        "--remove-deleted",
        action="store_true",
        help="With --entry: also remove from each copy's destination the files the "
        "release record lists that the course copy no longer has",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Take the course version of exactly the copied paths onto the branch "
        "students read, discarding the semester's edits to them, instead of merging",
    )
    add_preview_flag(
        parser,
        "Print the resolved source -> dest path pairs and exit without cloning or copying anything (default).",
    )
    args = parser.parse_args()

    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        sched = schedule.load(args.semester_org)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    if args.remove_deleted and not args.entry:
        log_err("--remove-deleted goes with --entry.")
        return 1
    if args.entry:
        entry = _entry_of(sched, args.entry)
        if isinstance(entry, str):
            log_err(entry)
            return 1
        deploys = list(entry.deploy)
    else:
        if not (args.course_source_repo and args.course_source_path):
            log_err("give --entry, or --course-source-repo and --course-source-path.")
            return 1
        try:
            deploys = parse_deploy_lists(
                args.course_source_repo,
                args.course_source_path,
                args.semester_dest_repo,
                args.semester_dest_path,
                sched.materials_repo,
            )
        except ValueError as e:
            log_err(f"{e}.")
            return 1
    # A release never lands in a repo the semester uses for something else: its config,
    # site or enrolment page, a gradebook, a student's repo (`reserved_destination`).
    for dest in dict.fromkeys(d.semester_dest_repo for d in deploys):
        if reserved := reserved_destination(dest, sched.assignments):
            log_err(
                f"{dest} cannot take a release: that name is reserved for {reserved}."
            )
            return 1
    if any(
        (args.course_org, d.course_source_repo)
        == (args.semester_org, d.semester_dest_repo)
        for d in deploys
    ):
        log_err("source and target must differ.")
        return 1
    # A copy the plan names under an entry that needs a number and has none is refused,
    # as the scheduled release skips it (decision 0020 rule 3). Off the plan it goes.
    for repo in dict.fromkeys(d.course_source_repo for d in deploys):
        refusal = unnumbered_release(
            sched,
            repo,
            [d.course_source_path for d in deploys if d.course_source_repo == repo],
        )
        if refusal:
            return refuse_unnumbered(refusal)

    sources, from_words = _named(
        args.course_org, [d.course_source_repo for d in deploys], "source repo"
    )
    dests, to_words = _named(
        args.semester_org, [d.semester_dest_repo for d in deploys], "repo"
    )
    route = f"from {from_words} -> {to_words}"
    what = plural(len(deploys), "item")
    if args.preview:
        log_step(f"PREVIEW release {len(deploys)} path(s) {route}")
        # The cheap structural checks need no clone, so catch them here: a source path that
        # contains `..` (rejected at run for resolving to the root or escaping the clone).
        # (The full clone-relative escape-check stays at copy time in deploy_many.)
        unsafe = False
        for d in deploys:
            src = d.course_source_path
            # The root is a legal path now (it means "everything"), so only an escaping
            # path is still unsafe - that half of the check survives unchanged.
            if ".." in src.strip("/").split("/"):
                log(
                    f"  UNSAFE  {d.course_source_repo}/{src}: escapes the clone - "
                    f"release a path inside the repo"
                )
                unsafe = True
                continue
            # Mirror deploy_many's own destination rule exactly (a root path means the dest
            # repo's ROOT, with no mirror-the-source fallback) - a dry-run that models the
            # release differently from the release is worse than no dry-run.
            landing = (d.semester_dest_path or src).strip("/")
            log(
                f"  PREVIEW  {d.course_source_repo}/{src} -> "
                f"{d.semester_dest_repo}/{landing or '(repo root)'}"
            )
        if unsafe:
            return 1
        return Summary(
            f"Preview: {what} from {sources} would be released to {dests}.",
            {"items": len(deploys)},
        )

    log_step(f"Releasing {len(deploys)} path(s) {route}")
    remove = None
    if args.remove_deleted:
        # Every file the record lists for each copy: `deploy_many` removes only those the
        # copy no longer carries.
        try:
            record = release_record.load(args.semester_org)
        except (RuntimeError, ValueError) as exc:
            log_err(f"could not read {release_record.PATH} ({exc}) - nothing removed")
            return 1
        # Never a path another copy of the plan lands at inside this one (a lab's
        # `solutions/`): those files are that copy's.
        landings = [
            (d.semester_dest_repo, deploy_dest(d))
            for r in sched.releases
            for d in r.deploy
        ]
        remove = {}
        for k in map(release_record.key_of, deploys):
            if k not in record.copies:
                continue
            nested = divergence.nested_in((k[2], k[3]), landings)
            remove[k] = list(
                divergence.outside(record.copies[k].get("files") or {}, nested)
            )
    # ONE batch, whatever the entry draws from: each repo clones once and the site syncs
    # once, as the scheduler's own release pass does - after the record is written, so
    # the render dates the rows this release shipped (`released_at`).
    try:
        errors, changed, copies = deploy_many(
            args.course_org,
            args.semester_org,
            deploys,
            sync=False,
            remove=remove,
            replace=args.replace,
        )
    except RuntimeError as e:
        log_err(str(e))
        return 1
    errors += _record(args.semester_org, sched, entry if args.entry else None, copies)
    if changed:
        errors += _sync_site(args.course_org, args.semester_org)
    if errors:
        return 1
    log("done")
    if not changed:
        return Summary(
            f"Nothing new to release: {what} from {sources} "
            f"{'is' if len(deploys) == 1 else 'are'} already in {dests}.",
            {"items": len(deploys)},
            conclusion="nothing_to_do",
        )
    verb, prep, tail = (
        ("Replaced", "in", " with the course version")
        if args.replace
        else ("Released", "to", "")
    )
    return Summary(
        f"{verb} {what} from {sources} {prep} {dests}{tail}.",
        {"items": len(deploys)},
    )


if __name__ == "__main__":
    sys.exit(main())
