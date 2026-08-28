"""dsl-course deploy -- publish path(s) from a course-org source repo into a cohort-org
repo, additively + idempotently:

    source/<repo>/<course_source_path>          (a folder - e.g. lectures/02_intro - or a file)
            |  copy that path
            v
    cohort/<cohort_dest_repo>/<cohort_dest_path>       (private + students read; accumulates over time)

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

from .schedule import Deploy
from .utils import (
    FACULTY_ONLY_HEADING,
    GIT_ENV,
    SYLLABUS_SAMPLE_FILE,
    SYLLABUS_SESSIONS_FILE,
    create_repo,
    gh,
    git,
    grant_faculty_read_access,
    grant_read_teams,
    is_untouched_stub,
    log,
    log_err,
    log_ok,
    log_step,
)

_GIT_ENV = GIT_ENV


# Never copied, at any depth: a `.git` landing in the dest overwrites its git metadata and
# redirects the release's own push into the SOURCE repo.
NEVER_COPIED = frozenset({".git"})

# Additionally skipped when the WHOLE repo is released (`course_source_path: /`), and only
# at the repo root: `.github` holds the Release workflows and their bot-token wiring,
# MAINTAINING.md is the maintainer guide, the syllabus sample is the filled example faculty
# copy from, and the sessions block is what the Generate syllabus workflow builds for them to
# paste. Each is written by this toolkit describing itself as never released, so each is
# named here - and named FROM `utils`, not re-spelled, so the exclusion cannot lapse the
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
    fix = (
        f"{source_org}/{repo}/{path} was NOT released - it is still the scaffold stub, "
        "written for faculty rather than students. Everything else in this release "
        "shipped. Write it for students, then release again."
    )
    log(f"  (withheld) {fix}")
    # Straight to stderr as a workflow command, so the run summary carries it too.
    print(f"::warning::{fix}", file=sys.stderr)


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

    `""`, `/` and `.` all name the root of `base` - for a source path that is the "release
    everything" spelling. A `..` path resolving outside the clone is refused: no reading of
    it is a release, and it is caught before any file is touched."""
    cleaned = rel.strip("/")
    base_r = base.resolve()
    target = (base / cleaned).resolve() if cleaned else base_r
    return target if target.is_relative_to(base_r) else None


def _copy_ignore(
    whole_repo_root: Path | None, extra_root_skips: frozenset[str] = frozenset()
):
    """A copytree `ignore` filter: NEVER_COPIED at every depth, plus ROOT_RELEASE_EXCLUDED
    and `extra_root_skips` at `whole_repo_root` when a whole repo is being released (None
    for a subpath copy).

    Root-anchored deliberately, rather than `shutil.ignore_patterns`, which matches by
    basename at every level of the walk - that would also drop a `labs/.github/`, which is
    the faculty member's own content and nothing to do with the release plumbing.

    `extra_root_skips` is decided per release rather than by contract - currently a README
    still carrying the scaffold placeholder. Skipping the COPY rather than deleting the
    result afterwards is what keeps a withheld file from touching the destination: a
    delete-after-copy stages a deletion of whatever the cohort repo already had there."""

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n in NEVER_COPIED}
        if whole_repo_root is not None and Path(dirpath) == whole_repo_root:
            skip |= {n for n in names if n in ROOT_RELEASE_EXCLUDED | extra_root_skips}
        return skip

    return ignore


def deploy_many(
    source_org: str,
    cohort_org: str,
    deploys: list[Deploy],
    sync: bool = True,
) -> tuple[int, bool]:
    """Apply a batch of Deploy copies, cloning each unique source and dest repo ONCE.

    Every deploy's `course_source_path` is copied from its (course-org) `course_source_repo`
    into its (cohort-org) `cohort_dest_repo` at `cohort_dest_path` (default: mirror
    `course_source_path`). Each touched
    dest repo gets a single commit+push covering all its copies; a dest with no net change
    is left alone (idempotent). Returns `(errors, changed)` - `errors` counts copies that
    could not be applied, `changed` is True if anything was actually pushed. `sync` runs a
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
            if gh("repo", "clone", f"{source_org}/{repo}", str(sd), "--", "-q")[0] != 0:
                log_err(f"could not clone source {source_org}/{repo}")
            else:
                src_dirs[repo] = sd

        # 2. clone (create if needed) each unique dest repo once (cohort org)
        dest_dirs: dict[str, Path] = {}
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
            grant_faculty_read_access(cohort_org, repo)
            dd = root / "out" / repo
            if gh("repo", "clone", f"{cohort_org}/{repo}", str(dd), "--", "-q")[0] != 0:
                log_err(f"could not clone dest {cohort_org}/{repo}")
            else:
                dest_dirs[repo] = dd

        # A deploy whose source or dest failed to clone is one impossible copy - count it
        # ONCE, per deploy, not once per failed clone (both failing is still one copy lost).
        errors += sum(
            1
            for d in deploys
            if d.course_source_repo not in src_dirs
            or d.cohort_dest_repo not in dest_dirs
        )

        # 3. apply every copy against the already-cloned trees
        touched: set[str] = set()
        for d in deploys:
            if (
                d.course_source_repo not in src_dirs
                or d.cohort_dest_repo not in dest_dirs
            ):
                continue  # its source/dest failed to clone (already counted)
            # A root cohort_dest_path means the dest repo's root, exactly as a root
            # course_source_path means the source repo's - no mirror-the-source fallback.
            dest_rel = (d.cohort_dest_path or d.course_source_path).strip("/")
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
                    # symlinks=True copies each link AS a link. Following them, a symlink
                    # pointing at nothing raised shutil.Error and a directory symlink
                    # pointing at its own parent recursed - and this runs under the hourly
                    # cron, so one such path in one materials repo aborted the whole
                    # cohort's release, every hour, until someone noticed.
                    shutil.copytree(
                        srcp,
                        destp,
                        dirs_exist_ok=True,
                        symlinks=True,
                        ignore=_copy_ignore(
                            srcp if srcp == src_root else None, withheld
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

        # 4. one commit + push per touched dest (skip if it has no net change)
        for repo in sorted(touched):
            dd = dest_dirs[repo]
            # -f: what was copied IS the release. A whole-repo release brings the source's
            # own `.gitignore` along, and without -f `git add` would then silently drop any
            # file the source force-added past it (lecture PDFs under a `*.pdf` rule are the
            # usual case) - reporting the release as shipped while those files never left.
            git("-C", str(dd), *_GIT_ENV, "add", "-A", "-f")
            # Distinguish "nothing staged" (genuinely nothing new to release - the
            # idempotent no-op) from a real commit failure (disk, lock, hook): git commit
            # exits non-zero for BOTH, so a failed commit would otherwise be reported as
            # "nothing new to release" and silently lost.
            if git("-C", str(dd), "diff", "--cached", "--quiet")[0] == 0:
                log_ok(f"  {repo}: nothing new to release")
                continue
            code, out = git(
                "-C",
                str(dd),
                *_GIT_ENV,
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
            if git("-C", str(dd), *_GIT_ENV, "push", "-q", "origin", "HEAD")[0] != 0:
                log_err(f"  {repo}: push failed")
                errors += 1
                continue
            log_ok(f"  {repo}: released")
            changed = True

    if sync and changed:
        from . import site

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
