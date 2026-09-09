"""dsl-course deploy -- publish path(s) from a course-org source repo into a cohort-org
repo, additively + idempotently:

    source/<repo>/<course_source_path>          (a folder - e.g. lectures/02_intro - or a file)
            |  copy that path
            v
    cohort/<cohort_dest_repo>/<cohort_dest_path>       (private + students read; accumulates over time)

The copy lands on the dest's `upstream` branch, which is then MERGED into the branch
students read - so an edit made in the cohort repo survives the next release instead of
being copied over, and a release that cannot be merged cleanly stops at a pull request
with the branch students read untouched (see UPSTREAM_BRANCH).

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

from . import pulls, site
from .access import (
    FACULTY_READ_ACCESS,
    INSTRUCTORS_TEAM,
    grant_faculty,
    grant_read_teams,
)
from .course import (
    FACULTY_ONLY_HEADING,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
    is_repo_root,
)
from .fs import copy_tree, union_deny
from .gh_contents import is_untouched_stub
from .ghcli import GIT_ENV, clone, git
from .log import log, log_err, log_ok, log_step, log_withheld
from .releaseignore import RELEASEIGNORE, deny_for, excludes
from .repos import create_repo, default_branch, is_never_material
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

# The branch a release actually lands on. What students read is the dest's DEFAULT branch,
# which the release then `git merge`s this into - so an edit made in the cohort repo is
# merged with the next release instead of copied over, which is what made every cohort-side
# fix vanish within the quarter hour. A copy that cannot be merged cleanly stops at a pull
# request and leaves the default branch exactly as students last saw it.
#
# Toolkit-owned and invisible to everything else: `deploy_many` is the only writer, and
# every reader in the package (site, discovery, status) resolves the default branch.
UPSTREAM_BRANCH = "upstream"


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


def _checkout_upstream(cohort_org: str, repo: str, dd: Path) -> str:
    """Put a dest clone on `UPSTREAM_BRANCH` and return the branch the release merges INTO.

    The base is read off the clone's own HEAD - `clone` checks out whatever the repo calls
    its default - rather than assumed to be `main`: a dest created by hand may call it
    anything, and releasing onto a branch nobody reads is silent. A clone with NO COMMITS
    is the exception: its HEAD names whatever the local git would have called a first
    branch, which has nothing to do with what the repo says, so the repo is asked instead
    and `main` is the last resort - guessing wrong there only names the branch this first
    release creates.

    `upstream` is taken from the remote when it is there and cut from the base when it is
    not, so a cohort released into before this existed gets a branch holding exactly what
    it was last released."""
    code, out = git("-C", str(dd), "symbolic-ref", "--short", "HEAD")
    unborn = git("-C", str(dd), "rev-parse", "--verify", "--quiet", "HEAD")[0] != 0
    base = (
        out.strip()
        if code == 0 and out.strip() and not unborn
        else default_branch(cohort_org, repo, fallback="main")
    )
    remote = f"origin/{UPSTREAM_BRANCH}"
    if git("-C", str(dd), "rev-parse", "--verify", "--quiet", remote)[0] == 0:
        git("-C", str(dd), *GIT_ENV, "checkout", "-B", UPSTREAM_BRANCH, remote)
    else:
        git("-C", str(dd), *GIT_ENV, "checkout", "-b", UPSTREAM_BRANCH)
    return base


def _push(dd: Path, branch: str) -> int:
    """Push one branch of a dest clone by NAME, not `HEAD`: a release now moves two of
    them and which one is checked out changes with the path taken."""
    return git("-C", str(dd), *GIT_ENV, "push", "-q", "origin", branch)[0]


def _conflict_body(base: str, paths: list[str]) -> str:
    """The body of the pull request a release opens when its merge conflicts.

    Paths and branch names only. This repo is readable by the whole cohort, so nothing
    about WHO edited what belongs here - the diff says that to whoever opens it, in the
    one place GitHub already shows it."""
    released = "\n".join(f"- `{p}`" for p in paths) or "- (nothing new this run)"
    return (
        f"This release could not be merged into `{base}`: the released copy and this "
        f"repo have both changed the same lines.\n\n"
        f"`{UPSTREAM_BRANCH}` holds what the course org released, covering:\n\n"
        f"{released}\n\n"
        f"`{base}` is untouched, so the cohort still reads what it read before. Resolve "
        f"the conflict here and merge, or close this pull request to keep this repo's "
        f"version. Either way the next release adds to `{UPSTREAM_BRANCH}`, and this "
        f"pull request follows it rather than a second one being opened.\n"
    )


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
        dest_dirs: dict[str, Path] = {}
        bases: dict[str, str] = {}
        for repo in sorted({d.cohort_dest_repo for d in deploys}):
            create_repo(
                cohort_org,
                repo,
                private=True,
                description="Released lectures, labs, readings, & other materials",
            )
            grant_read_teams(cohort_org, repo)
            # Read, not write: this is the RELEASED copy, and a re-release copies over it
            # (`copytree(dirs_exist_ok=True)`), so an edit made here would vanish. A
            # correction belongs in the course org's materials repo, then re-release.
            grant_faculty(cohort_org, repo, FACULTY_READ_ACCESS, missing_is_note=True)
            dd = root / "out" / repo
            if not clone(cohort_org, repo, dd):
                log_err(f"could not clone dest {cohort_org}/{repo}")
            else:
                dest_dirs[repo] = dd
                bases[repo] = _checkout_upstream(cohort_org, repo, dd)

        # A deploy whose source or dest failed to clone is one impossible copy - count it
        # ONCE, per deploy, not once per failed clone (both failing is still one copy lost).
        errors += sum(
            1
            for d in deploys
            if d.course_source_repo not in src_dirs
            or d.cohort_dest_repo not in dest_dirs
        )

        # 3. apply every copy against the already-cloned trees
        # (the paths, not just the repo names: the pull request a conflicted merge opens
        # says which released paths are in the branch it is proposing)
        released: dict[str, list[str]] = {}
        for d in deploys:
            if (
                d.course_source_repo not in src_dirs
                or d.cohort_dest_repo not in dest_dirs
            ):
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
            destp = _resolve_within(dest_dirs[d.cohort_dest_repo], dest_rel)
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
            released.setdefault(d.cohort_dest_repo, []).append(
                dest_rel or "(repo root)"
            )

        # 4. one commit on `upstream` per dest, then merge it into the branch students
        # read. Every CLONED dest, not just the ones this run copied into: a run whose
        # merge conflicted leaves `upstream` ahead with a pull request standing, and the
        # next run has to re-offer that merge (and re-find the same pull request) even
        # when it had nothing new of its own to add.
        for repo in sorted(dest_dirs):
            dd = dest_dirs[repo]
            base = bases[repo]
            # -f: what was copied IS the release. A whole-repo release brings the source's
            # own `.gitignore` along, and without -f `git add` would then silently drop any
            # file the source force-added past it (lecture PDFs under a `*.pdf` rule are the
            # usual case) - reporting the release as shipped while those files never left.
            git("-C", str(dd), *GIT_ENV, "add", "-A", "-f")
            # Distinguish "nothing staged" (genuinely nothing new to release - the
            # idempotent no-op) from a real commit failure (disk, lock, hook): git commit
            # exits non-zero for BOTH, so a failed commit would otherwise be reported as
            # "nothing new to release" and silently lost.
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
            if git("-C", str(dd), "rev-parse", "--verify", "--quiet", "HEAD")[0] != 0:
                continue  # an empty dest nothing could be copied into
            if git("-C", str(dd), "rev-parse", "--verify", "--quiet", base)[0] != 0:
                # A dest this run created: there is nothing to merge into, so the branch
                # students read simply starts where the released copy now is.
                if git("-C", str(dd), *GIT_ENV, "branch", "-f", base, UPSTREAM_BRANCH)[
                    0
                ]:
                    log_err(f"  {repo}: could not start `{base}`")
                    errors += 1
                    continue
            elif (
                git(
                    "-C", str(dd), "merge-base", "--is-ancestor", UPSTREAM_BRANCH, base
                )[0]
                == 0
            ):
                # Everything released is already in the branch students read - the
                # idempotent no-op, and the only reason to say so is that somebody pressed
                # the button and is watching for a line about this repo.
                if repo in released:
                    log_ok(f"  {repo}: nothing new to release")
                continue
            else:
                code, out = git("-C", str(dd), *GIT_ENV, "checkout", base)
                if code != 0:
                    log_err(f"  {repo}: could not check out `{base}` - {out[:200]}")
                    errors += 1
                    continue
                code, out = git(
                    "-C",
                    str(dd),
                    *GIT_ENV,
                    "merge",
                    "--no-edit",
                    "-m",
                    f"release: merge {UPSTREAM_BRANCH} into {base}",
                    UPSTREAM_BRANCH,
                )
                if code != 0:
                    # The cohort has edited what this release also changed. Leave `base`
                    # exactly as students last read it, ship the release to `upstream`
                    # anyway so nothing is lost, and put the decision in front of the
                    # instructors as ONE standing pull request.
                    git("-C", str(dd), *GIT_ENV, "merge", "--abort")
                    if _push(dd, UPSTREAM_BRANCH) != 0:
                        log_err(f"  {repo}: push failed")
                        errors += 1
                        continue
                    opened = pulls.upsert_pr(
                        f"{cohort_org}/{repo}",
                        head=UPSTREAM_BRANCH,
                        base=base,
                        title=f"Release: merge `{UPSTREAM_BRANCH}` into `{base}`",
                        body=_conflict_body(base, released.get(repo, [])),
                        reviewer=f"{cohort_org}/{INSTRUCTORS_TEAM}",
                    )
                    errors += opened.errors
                    if opened.url:
                        log(f"  {repo}: held for review - {opened.url}")
                    continue
            # `upstream` first: it is the record of what was released, and a base pushed
            # without it would leave the next run merging a branch that never arrived.
            if _push(dd, UPSTREAM_BRANCH) != 0 or _push(dd, base) != 0:
                log_err(f"  {repo}: push failed")
                errors += 1
                continue
            log_ok(f"  {repo}: released")
            changed = True

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
