"""dsl-course deploy -- publish path(s) from a course-org source repo into a cohort-org
repo, additively + idempotently:

    source/<repo>/<course_source_path>          (a folder - e.g. lectures/02_intro - or a file)
            |  copy that path
            v
    cohort/<cohort_dest_repo>/<cohort_dest_path>       (private + students read; accumulates over time)

The copy lands on the dest's `upstream` branch and is MERGED into the branch students
read (see `UPSTREAM_BRANCH`).

`deploy_many` is the batch core AND the single executor of every release in the system:
it clones each unique source repo and each unique dest repo ONCE per run and applies every
copy against those working trees, so a scheduler run releasing 27 paths from one source
clones it once, not 27 times. Both callers arrive here - the hourly scheduler
(scheduler._run_releases, straight from each `deploy:` entry in schedule.yml) and the
manual "Release materials" workflow (via `main` below, whose five inputs are deliberately the
same five fields as a `deploy:` entry).

The workflow's `course_source_path`/`cohort_dest_path` are comma-separated PARALLEL lists
paired by index (parse_path_pairs) - one Deploy per pair, one deploy_many call for the batch.

Usage:
    python3 -m dsl_course.deploy \\
        --source-org COURSE --course-source-repo course-materials-f2026 \\
        --cohort-org COHORT --cohort-dest-repo materials \\
        --course-source-path "lectures/02_intro,labs/02_lab" [--cohort-dest-path "week02/lecture,week02/lab"]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import pulls, site
from .access import COURSE_TEAM_ACCESS, grant_faculty, grant_read_teams
from .course import (
    FACULTY_ONLY_HEADING,
    INSTRUCTORS_TEAM,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
    UPSTREAM_BRANCH,
    is_repo_root,
)
from .fs import copy_tree, union_deny
from .gh_contents import is_untouched_stub
from .ghcli import GIT_ENV, clone, git
from .log import log, log_err, log_ok, log_step, log_withheld
from .releaseignore import RELEASEIGNORE, deny_for, excludes
from .repos import (
    allow_forking,
    create_repo,
    default_branch,
    is_never_material,
    repo_is_archived,
)
from .schedule import Deploy
from .schedule_plan import deploy_dest

# Never copied, at any depth: a `.git` landing in the dest overwrites its git metadata and
# redirects the release's own push into the SOURCE repo. That is a mechanical fact about
# copying a repo into a repo, not a judgement about content - clutter that is never course
# material is the separate `repos.NEVER_MATERIAL`, which `_copy_ignore` applies alongside
# this.
NEVER_COPIED = frozenset({".git"})

# Additionally skipped when the WHOLE repo is released (`course_source_path: /`), and only
# at the repo root: `.github` holds the Release workflows and their bot-token wiring,
# MAINTAINING.md is the maintainer guide, the syllabus sample is the filled example faculty
# copy from, and the sessions block is what the Generate syllabus workflow builds for them to
# paste. Each is written by this toolkit describing itself as never released, so each is
# named here - and named FROM `course`, not re-spelled, so the exclusion cannot lapse the
# next time one is renamed. Naming any of these paths explicitly still releases it: that is
# what "give me everything" means, not a ban.
ROOT_RELEASE_EXCLUDED = frozenset(
    {".github", "MAINTAINING.md", SYLLABUS_SAMPLE_FILE, SYLLABUS_SESSIONS_FILE}
)

# Root documents this toolkit seeds as stubs for faculty to write over. Released once
# written; withheld while still ours, because shipping either as-is publishes faculty
# instructions and empty tables to students as their course overview or their syllabus.
#
# The syllabus joined this the moment the site began PINNING it on the landing page: an
# unwritten stub would otherwise be the most prominent link on the course's front page.
WITHHELD_ROOT_STUBS = ("README.md", "SYLLABUS.md")

UNEDITED_README_MARKERS = ("**Replace this placeholder.**", FACULTY_ONLY_HEADING)


def _warn_withheld_stub(source_org: str, repo: str, path: str) -> None:
    """Say what was withheld and how to fix it - visibly, but WITHOUT failing the release.

    Withholding an unwritten stub is this guard working, not a fault: the release did
    exactly what it should. Counting it as an error reddened a run whose every other copy
    shipped, and - because the hourly scheduler drives the same `deploy_many` - would have
    reddened the Scheduled release cron every hour, forever, for any course that never
    rewrote its README. A permanently red cron is how real failures stop being noticed.

    So it takes the channel this codebase already uses for "true, worth seeing, not a
    failure" (see `templates/classroom-config/validate-schedule.yml`): a `::warning::`
    annotation on a green run, which touches no exit code."""
    log_withheld(
        f"{source_org}/{repo}/{path} was NOT released - it is still the scaffold stub, "
        "written for faculty rather than students. Everything else in this release "
        "shipped. Write it for students, then release again."
    )


def _warn_ignored_source(source_org: str, repo: str, path: str) -> None:
    """Say that a release's own source path is excluded by a `.releaseignore`.

    Faculty naming a path outright is a clear ask, so answering it with nothing at all
    would be the wrong kind of quiet - `git add <ignored-path>` refuses out loud too
    rather than adding the file. It takes the ::warning::-on-a-green-run channel instead
    of an error for the reason in `_warn_withheld_stub`: the hourly scheduler runs through
    the same `deploy_many`."""
    log_withheld(
        f"{source_org}/{repo}/{path} was NOT released - a `{RELEASEIGNORE}` excludes it. "
        "Everything else in this release shipped. Drop the pattern that matches it, or "
        "release a path that is not excluded."
    )


def _is_withheld_stub(path: str, text: str) -> bool:
    """Whether a copy is one of the root stubs this toolkit seeds, still unwritten.

    The ROOT file only - `path` must be exactly one of `WITHHELD_ROOT_STUBS`, not merely end
    in it. A `README.md` inside a session folder is the faculty's own writing about that
    session, and these stubs only ever exist at the repo root.

    Two tests, because the two files are marked differently: `SYLLABUS.md` carries the
    `dsl-stub:` mark every seeded stub now carries, while the README predates it and is
    recognised by its own placeholder text - both markers required there, so a real overview
    that happens to quote the stub still ships."""
    name = path.strip("/")
    if name not in WITHHELD_ROOT_STUBS:
        return False
    if name == "README.md":
        return all(marker in text for marker in UNEDITED_README_MARKERS)
    return is_untouched_stub(text)


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


def _copy_ignore(
    whole_repo_root: Path | None, extra_root_skips: frozenset[str] = frozenset()
):
    """A copytree `ignore` filter: NEVER_COPIED and `repos.NEVER_MATERIAL` at every depth,
    plus ROOT_RELEASE_EXCLUDED and `extra_root_skips` at `whole_repo_root` when a whole
    repo is being released (None for a subpath copy).

    The two every-depth rules answer different questions and are kept apart on purpose.
    NEVER_COPIED is about this copy working at all; NEVER_MATERIAL is about what a release
    means - a session folder is copied wholesale, so a `.gitkeep` holding it open and the
    `.DS_Store` a file manager left in it ship to students as course material unless
    something drops them here.

    Root-anchored deliberately, rather than `shutil.ignore_patterns`, which matches by
    basename at every level of the walk - that would also drop a `labs/.github/`, which is
    the faculty member's own content and nothing to do with the release plumbing.

    `extra_root_skips` is decided per release rather than by contract - currently a README
    still carrying the scaffold placeholder. Skipping the COPY rather than deleting the
    result afterwards is what keeps a withheld file from touching the destination: a
    delete-after-copy stages a deletion of whatever the cohort repo already had there."""

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in NEVER_COPIED or is_never_material(n)}
        if whole_repo_root is not None and Path(dirpath) == whole_repo_root:
            skip |= {n for n in names if n in ROOT_RELEASE_EXCLUDED | extra_root_skips}
        return skip

    return ignore


class Dest(NamedTuple):
    """One prepared release destination: the clone every copy for it lands in, the branch
    students read, and whether the repo had NO COMMITS when it was cloned.

    `unborn` is carried rather than re-probed because it answers both of the questions the
    merge phase would otherwise ask git again: an empty clone has no HEAD until this run
    commits one, and no local `base` branch at all."""

    dir: Path
    base: str
    unborn: bool


def _has_ref(dd: Path, ref: str) -> bool:
    """Whether `ref` resolves in a dest clone."""
    return git("-C", str(dd), "rev-parse", "--verify", "--quiet", ref)[0] == 0


def _checkout_upstream(cohort_org: str, repo: str, dd: Path) -> Dest | None:
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

    A cohort released into before any of this existed gets an `upstream` holding exactly
    what it was last released."""
    code, out = git(
        "-C", str(dd), "rev-parse", "--abbrev-ref", "--verify", "--quiet", "HEAD"
    )
    unborn = code != 0
    base = (
        out.strip()
        if code == 0 and out.strip()
        else default_branch(cohort_org, repo, fallback="main")
    )
    if base == UPSTREAM_BRANCH:
        # `upstream` as the DEFAULT branch collapses the two ends of the merge into one,
        # and every check below then reads as "already merged": the copy would be
        # committed, `merge-base --is-ancestor upstream upstream` would pass, and the
        # release would report "nothing new to release" having pushed nothing at all.
        # Refused out loud, because silently releasing nothing for ever is the one
        # outcome worse than a red run (maintainers.md names this branch toolkit-owned).
        log_err(
            f"  {cohort_org}/{repo}: `{UPSTREAM_BRANCH}` is this repo's DEFAULT branch. "
            f"It is the toolkit's own branch - make something else the default, and "
            f"releases can be merged into it again."
        )
        return None
    remote = f"origin/{UPSTREAM_BRANCH}"
    if _has_ref(dd, remote):
        code, out = git(
            "-C", str(dd), *GIT_ENV, "checkout", "-B", UPSTREAM_BRANCH, remote
        )
    else:
        code, out = git("-C", str(dd), *GIT_ENV, "checkout", "-b", UPSTREAM_BRANCH)
    if code != 0:
        log_err(
            f"  {cohort_org}/{repo}: could not check out `{UPSTREAM_BRANCH}` - "
            f"{out[:200]}"
        )
        return None
    return Dest(dd, base, unborn)


def _prepare_dest(cohort_org: str, repo: str, root: Path) -> Dest | None:
    """Create, grant, clone and put ONE dest repo on `UPSTREAM_BRANCH`. None when it could
    not be prepared - the copies for that dest are impossible either way, and each failure
    has already said so once.

    The grants are here rather than at creation because they CONVERGE: a dest made before
    one of them existed has to get it too, and the release is the only thing that visits a
    dest regularly."""
    create_repo(
        cohort_org,
        repo,
        private=True,
        description="Released lectures, labs, readings, & other materials",
    )
    grant_read_teams(cohort_org, repo)
    # Write, because an edit made here is now DURABLE: the release lands on `upstream`
    # and is merged in, so a correction typed into the cohort repo survives the next tick
    # instead of being copied over. It was read for exactly as long as it was not.
    #
    # The course org is still the source of truth - a fix made here reaches next term
    # only when somebody carries it back. The floor (`access.faculty_floor`) stays at
    # read: the sweep never demotes, and this grant runs on every release, so the two
    # agree.
    grant_faculty(cohort_org, repo, COURSE_TEAM_ACCESS, missing_is_note=True)
    # Students are told to fork the materials and work in their own copy, and a PRIVATE
    # repo is forkable only if BOTH its org and it say so. Converged on every release,
    # not only at creation: the dests that predate this need it too.
    allow_forking(cohort_org, repo)
    dd = root / "out" / repo
    if not clone(cohort_org, repo, dd):
        log_err(f"could not clone dest {cohort_org}/{repo}")
        return None
    # A dest that would not go onto `upstream` is as unusable as one that would not
    # clone: releasing onto the base branch instead is exactly what the merge exists to
    # stop.
    return _checkout_upstream(cohort_org, repo, dd)


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

    Branch names only. This repo is readable by the whole cohort, so nothing about WHO
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
        f"`{base}` is untouched, so the cohort still reads what it read before. Resolve "
        f"the conflict here and merge - keeping this repo's version, the released one, "
        f"or a mix of the two. Merging is what settles it: further releases add to "
        f"`{UPSTREAM_BRANCH}` and re-use this pull request, and closing it unresolved "
        f"only means the next release opens the same question again.\n"
    )


def _merge_and_push(
    cohort_org: str,
    repo: str,
    dest: Dest,
    touched: set[str],
    *,
    committed: bool,
) -> tuple[int, bool]:
    """Merge one dest's `UPSTREAM_BRANCH` into the branch students read and push both.

    `(errors, base_moved)` - `base_moved` only when the branch students read actually
    moved, so a release held at a pull request does not report itself as a change.

    `committed` is whether the caller's commit-on-`upstream` ran this time. With
    `dest.unborn` it answers both of the questions a `rev-parse` used to: a clone that was
    empty and stayed empty has no HEAD to merge from, and one that was empty has no local
    `base` branch to merge into."""
    dd = dest.dir
    if dest.unborn and not committed:
        return 0, False  # an empty dest nothing could be copied into
    if dest.unborn:
        # A dest this run created: there is nothing to merge into, so the branch students
        # read simply starts where the released copy now is. No `-f`: `base` cannot exist
        # here, and a git that says otherwise is a fact to fail on rather than to
        # overwrite the branch students read with.
        if git("-C", str(dd), *GIT_ENV, "branch", dest.base, UPSTREAM_BRANCH)[0] != 0:
            log_err(f"  {repo}: could not start `{dest.base}`")
            return 1, False
    elif (
        git("-C", str(dd), "merge-base", "--is-ancestor", UPSTREAM_BRANCH, dest.base)[0]
        == 0
    ):
        # Everything released is already in the branch students read - the idempotent
        # no-op, and the only reason to say so is that somebody pressed the button and is
        # watching for a line about this repo.
        if repo in touched:
            log_ok(f"  {repo}: nothing new to release")
        return 0, False
    else:
        code, out = git("-C", str(dd), *GIT_ENV, "checkout", dest.base)
        if code != 0:
            log_err(f"  {repo}: could not check out `{dest.base}` - {out[:200]}")
            return 1, False
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
        if code != 0:
            # The cohort has edited what this release also changed. Leave `base` exactly
            # as students last read it, ship the release to `upstream` anyway so nothing
            # is lost, and put the decision in front of the instructors as ONE standing
            # pull request.
            git("-C", str(dd), *GIT_ENV, "merge", "--abort")
            # Only when this run added to `upstream`: a re-offered merge has nothing new
            # on it, so the local branch is already what the remote holds.
            if committed and _push(dd, UPSTREAM_BRANCH) != 0:
                log_err(f"  {repo}: push failed")
                return 1, False
            opened = pulls.upsert_pr(
                f"{cohort_org}/{repo}",
                head=UPSTREAM_BRANCH,
                base=dest.base,
                title=f"Release: merge `{UPSTREAM_BRANCH}` into `{dest.base}`",
                body=_conflict_body(dest.base),
                reviewer=f"{cohort_org}/{INSTRUCTORS_TEAM}",
            )
            if opened.url:
                log(f"  {repo}: held for review - {opened.url}")
            return opened.errors, False
    if _push(dd, UPSTREAM_BRANCH, dest.base) != 0:
        log_err(f"  {repo}: push failed")
        return 1, False
    log_ok(f"  {repo}: released")
    return 0, True


def deploy_many(
    source_org: str,
    cohort_org: str,
    deploys: list[Deploy],
    sync: bool = True,
) -> tuple[int, bool]:
    """Apply a batch of Deploy copies, cloning each unique source and dest repo ONCE.

    Every deploy's `course_source_path` is copied from its (course-org) `course_source_repo`
    into its (cohort-org) `cohort_dest_repo` at `cohort_dest_path` (default: mirror
    `course_source_path`). Each touched dest repo gets a single commit on `UPSTREAM_BRANCH`
    covering all its copies, which is then merged into the branch students read; a dest with
    no net change is left alone (idempotent). Returns `(errors, changed)` - `errors` counts
    copies that could not be applied, `changed` is True if the branch students read moved
    (a release held at a pull request has NOT changed it). `sync` runs a
    single website sync at the end when `changed` (callers batching several release kinds
    pass sync=False and sync once themselves)."""
    deploys = [d for d in deploys if d]
    if not deploys:
        return 0, False

    errors = 0
    changed = False
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)

        # 1. clone each unique source repo once (course org)
        src_dirs: dict[str, Path] = {}
        for repo in sorted({d.course_source_repo for d in deploys}):
            sd = root / "src" / repo
            if not clone(source_org, repo, sd):
                log_err(f"could not clone source {source_org}/{repo}")
            else:
                src_dirs[repo] = sd

        # 2. clone (create if needed) each unique dest repo once (cohort org)
        dests: dict[str, Dest] = {}
        archived: set[str] = set()
        for repo in sorted({d.cohort_dest_repo for d in deploys}):
            if repo_is_archived(cohort_org, repo):
                # A closed cohort. Everything below - the grants, the commit, the push -
                # 403s on an archived repo, so a schedule that still names one would red
                # this cron for the rest of time. Being finished is a state somebody
                # chose, so it is a line, not an error. (`repo_is_archived` fails open, so
                # a flag that could not be read releases as usual and the write itself is
                # the alarm.)
                log(f"  [skip] {cohort_org}/{repo} is archived")
                archived.add(repo)
                continue
            dest = _prepare_dest(cohort_org, repo, root)
            if dest is not None:
                dests[repo] = dest

        # A deploy into an ARCHIVED dest is not a failure and not a copy: it was skipped
        # on purpose, and counting it would red every run of a course that has closed one
        # of its cohorts. Dropping it here leaves everything below counting failures.
        deploys = [d for d in deploys if d.cohort_dest_repo not in archived]

        # A deploy whose source or dest could not be prepared - the clone, or the dest's
        # `upstream` checkout - is one impossible copy. Count it ONCE, per deploy, not
        # once per failure (both ends failing is still one copy lost).
        errors += sum(
            1
            for d in deploys
            if d.course_source_repo not in src_dirs or d.cohort_dest_repo not in dests
        )

        # 3. apply every copy against the already-cloned trees
        touched: set[str] = set()
        for d in deploys:
            if d.course_source_repo not in src_dirs or d.cohort_dest_repo not in dests:
                continue  # its source/dest failed to clone (already counted)
            # A root cohort_dest_path means the dest repo's root, exactly as a root
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
                    f"{source_org}/{d.course_source_repo} - it escapes the clone. skipped."
                )
                errors += 1
                continue
            destp = _resolve_within(dests[d.cohort_dest_repo].dir, dest_rel)
            if destp is None:
                log_err(
                    f"unsafe cohort_dest_path `{dest_rel}` for "
                    f"{cohort_org}/{d.cohort_dest_repo} - skipped."
                )
                errors += 1
                continue
            if not srcp.exists():
                log_err(
                    f"`{d.course_source_path}` not found in "
                    f"{source_org}/{d.course_source_repo} - skipped."
                )
                errors += 1
                continue
            if excludes(src_root, srcp):
                # The source path is itself excluded - so is everything under it, and no
                # `!` deeper down can re-include any of it. Nothing else was asked for, so
                # this copy is a no-op rather than a partial one.
                _warn_ignored_source(
                    source_org, d.course_source_repo, d.course_source_path
                )
                continue
            try:
                if srcp.is_dir():
                    # A WHOLE-REPO release carries the root README along, which is how the
                    # placeholder actually reached students. Checked on the SOURCE and
                    # skipped before the copy, never deleted after it: faculty who fixed a
                    # leaked placeholder by editing the cohort repo's own README would
                    # otherwise have that fix staged as a deletion by the next release -
                    # while the log said everything else shipped.
                    #
                    # Whole-repo only: a copy of one section picks up that section's own
                    # `README.md`, which is faculty writing about the section, not the stub.
                    withheld = frozenset()
                    if srcp == src_root:
                        for stub in WITHHELD_ROOT_STUBS:
                            f = srcp / stub
                            if f.is_file() and _is_withheld_stub(
                                stub, f.read_text(encoding="utf-8", errors="replace")
                            ):
                                withheld |= {stub}
                                _warn_withheld_stub(
                                    source_org, d.course_source_repo, stub
                                )
                    copy_tree(
                        srcp,
                        destp,
                        union_deny(
                            # Two filters unioned, not one list: the toolkit's exclusions
                            # are a contract it keeps with itself, `.releaseignore` is
                            # faculty's. The patterns anchor at the CLONE root even for a
                            # subpath copy, as a root `.gitignore` would.
                            _copy_ignore(srcp if srcp == src_root else None, withheld),
                            deny_for(src_root),
                        ),
                    )
                elif _is_withheld_stub(
                    d.course_source_path,
                    srcp.read_text(encoding="utf-8", errors="replace"),
                ):
                    # Named outright rather than swept up by a whole-repo release: nothing
                    # else was asked for, so this copy is simply a no-op.
                    _warn_withheld_stub(
                        source_org, d.course_source_repo, d.course_source_path
                    )
                    continue
                else:
                    destp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(srcp, destp)
            except (shutil.Error, OSError) as exc:
                # One unreadable path is ONE failed copy, counted like any other - not an
                # exception out of deploy_many that takes every other release with it.
                log_err(
                    f"could not copy `{d.course_source_path}` from "
                    f"{source_org}/{d.course_source_repo}: {exc}"
                )
                errors += 1
                continue
            log_ok(f"+ {d.cohort_dest_repo}/{dest_rel or '(repo root)'}")
            touched.add(d.cohort_dest_repo)

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
                    continue
                committed = True
            merge_errors, base_moved = _merge_and_push(
                cohort_org, repo, dest, touched, committed=committed
            )
            errors += merge_errors
            changed = changed or base_moved

    if sync and changed:
        # site.sync_site RAISES on a genuine tree/team read failure - one cohort's
        # site-sync failure must be logged and counted (making the release non-zero), not
        # an unhandled traceback that aborts the batch.
        try:
            if site.sync_site(source_org, cohort_org) != 0:
                log_err("site sync incomplete after release")
                errors += 1
        except Exception as exc:
            log_err(f"site sync failed after release: {exc}")
            errors += 1
    return errors, changed


def _items(spec: str) -> list[str]:
    """Split a comma-separated input into stripped, non-empty items - so
    "a, b," is ["a", "b"] and "" is []."""
    return [item.strip() for item in spec.split(",") if item.strip()]


def parse_path_pairs(
    source_paths: str, dest_paths: str = ""
) -> list[tuple[str, str | None]]:
    """Pair the Release materials workflow's two comma-separated lists by index.

    A blank `dest_paths` mirrors every source path (`None` dest, exactly what an omitted
    `cohort_dest_path:` means in schedule.yml). Otherwise the counts MUST match: unlike the
    schedule (which drops what it can't pair, on an unattended cron), a workflow run has an
    operator watching it, so a mismatch is a loud ValueError naming both counts rather
    than a silently short release. Surrounding whitespace is stripped and empty items
    (a trailing comma) are ignored on both sides."""
    sources = _items(source_paths)
    if not sources:
        raise ValueError("--course-source-path is empty")
    dests = _items(dest_paths)
    if not dests:
        return [(s, None) for s in sources]
    if len(dests) != len(sources):
        raise ValueError(
            f"{len(sources)} course_source_paths but {len(dests)} cohort_dest_paths - give "
            f"one cohort_dest_path per course_source_path (paired in order), or leave "
            f"cohort_dest_path blank to mirror every course_source_path"
        )
    return list(zip(sources, dests))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-org", required=True, help="Course org (source)")
    parser.add_argument(
        "--course-source-repo", required=True, help="Source repo holding the path(s)"
    )
    parser.add_argument("--cohort-org", required=True, help="Cohort org (target)")
    parser.add_argument(
        "--cohort-dest-repo",
        default="materials",
        help="Target repo in the cohort org, created if missing (default: materials)",
    )
    parser.add_argument(
        "--course-source-path",
        required=True,
        help="Source path(s) to release - a folder/file, or a comma-separated list",
    )
    parser.add_argument(
        "--cohort-dest-path",
        default="",
        help="Destination path(s), paired with --course-source-path by index "
        "(default: mirror each --course-source-path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved source -> dest path pairs and exit without cloning or "
        "copying anything (the cheapest check that a release will land where you expect).",
    )
    args = parser.parse_args()

    dest_repo = args.cohort_dest_repo.strip() or "materials"
    if (args.source_org, args.course_source_repo) == (args.cohort_org, dest_repo):
        log_err("source and target must differ.")
        return 1
    try:
        pairs = parse_path_pairs(args.course_source_path, args.cohort_dest_path)
    except ValueError as e:
        log_err(f"{e}.")
        return 1

    if args.dry_run:
        log_step(
            f"DRY-RUN release {len(pairs)} path(s) from "
            f"{args.source_org}/{args.course_source_repo} -> {args.cohort_org}/{dest_repo}"
        )
        # The cheap structural checks need no clone, so catch them here: a source path that
        # strips to the repo root (drags the source's own .git/.github over the dest), or one
        # that contains `..` (rejected at run for resolving to the root or escaping the clone).
        # (The full clone-relative escape-check stays at copy time in deploy_many.)
        unsafe = False
        for src, dest in pairs:
            # The root is a legal path now (it means "everything"), so only an escaping
            # path is still unsafe - that half of the check survives unchanged.
            if ".." in src.strip("/").split("/"):
                log(
                    f"  UNSAFE  {args.course_source_repo}/{src}: escapes the clone - "
                    f"release a path inside the repo"
                )
                unsafe = True
                continue
            # Mirror deploy_many's own destination rule exactly (a root path means the dest
            # repo's ROOT, with no mirror-the-source fallback) - a dry-run that models the
            # release differently from the release is worse than no dry-run.
            landing = (dest or src).strip("/")
            log(
                f"  DRY-RUN  {args.course_source_repo}/{src} -> "
                f"{dest_repo}/{landing or '(repo root)'}"
            )
        return 1 if unsafe else 0

    log_step(
        f"Releasing {len(pairs)} path(s) from {args.source_org}/{args.course_source_repo} -> "
        f"{args.cohort_org}/{dest_repo}"
    )
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        errors, _ = deploy_many(
            args.source_org,
            args.cohort_org,
            [
                Deploy(args.course_source_repo, src, dest_repo, dest)
                for src, dest in pairs
            ],
        )
    except RuntimeError as e:
        log_err(str(e))
        return 1
    if errors:
        return 1
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
