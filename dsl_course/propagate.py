"""dsl-course propagate -- carry a cohort's edits to released material back into the
course org, as a pull request. The reverse of `deploy`:

    cohort/<cohort_dest_repo>/<cohort_dest_path>     what the cohort actually has now
            |  copy that path back
            v
    course/<course_source_repo>/<course_source_path> on branch `from-<cohort-org>`
            |  one pull request per source repo
            v
    course/<course_source_repo>/<its default branch>  faculty merge, cherry-pick or close

The course org is the source of truth, and a release copies one way. Since a release lands
on `upstream` and is MERGED into the branch students read (`deploy.UPSTREAM_BRANCH`), an
instructor's correction typed into the cohort repo now survives the next release - but it
lives only in that cohort, and next year's cohort is cut from the course org. This is what
carries it home.

WHAT IT PROPOSES, and nothing more. It writes to a branch of its own and opens a pull
request; a human decides what of it belongs in the course org. So it is deliberately
generous about what it copies and deliberately explicit about what it does not:

- Every deploy the cohort's plan has already FIRED, in the plan's own order, one commit
  per released path. A copy that has not shipped yet has no cohort-side edit to carry.
- DELETIONS ARE NOT PROPAGATED. A file the cohort deleted stays in the course repo, and
  the pull request names it: deleting from a course repo on the strength of a cohort's
  working copy is not a decision this should take unattended.
- The branch is REGENERATED from the course repo's default branch on every run and
  force-pushed, so a run always proposes what the cohort has now rather than accumulating.
  The pull request is reused and its body rewritten (`pulls.upsert_pr`), so a re-run is
  one conversation and not a second one.

Nothing here names a person. The cohort repo is readable by the whole cohort and its
history has student pull requests in it; what is propagated is a path, and the pull
request says so in paths.

Usage:
    python3 -m dsl_course.propagate --course-org COURSE --cohort-org COHORT [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import pulls, schedule
from .deploy import _copy_ignore, _resolve_within
from .fs import Deny, copy_tree
from .ghcli import GIT_ENV, clone, git
from .log import log, log_err, log_ok, log_step
from .schedule import Deploy
from .schedule_plan import deploy_dest

# The branch every run of this rewrites, in the COURSE repo. Named after the cohort it
# carries, because a course org releases into several at once and each one's edits are a
# separate conversation with faculty. Toolkit-owned: it is regenerated from the default
# branch on every run, so anything committed onto it by hand is lost at the next tick.
BRANCH_PREFIX = "from-"


def branch_for(cohort_org: str) -> str:
    """The branch a cohort's edits are proposed on. One spelling, because the push, the
    pull request lookup and the body's own explanation all have to name the same ref."""
    return f"{BRANCH_PREFIX}{cohort_org}"


class Carried(NamedTuple):
    """One propagated path: where it came from and where it landed in the course repo.

    Both halves, because they are usually the same string and occasionally not - a
    `cohort_dest_path` that renames the folder is exactly the case a reader of the pull
    request needs spelled out."""

    cohort: str  # `<repo>/<path>` in the cohort org
    course: str  # the path inside the course source repo

    def line(self) -> str:
        return f"- `{self.cohort}` -> `{self.course or '(repo root)'}`"


def due_deploys(cohort_org: str, now: datetime) -> list[Deploy]:
    """Every copy this cohort's plan has already fired, in the plan's own order.

    The plan's releases are sorted by `event_datetime` and each entry's `deploy:` list is
    in the order it was written, so this is session order - which is the order the commits
    are made in, so the branch reads like the term did.

    Assignment handouts are not here: an assignment is handed out from a TEMPLATE repo
    into per-student repos, and a student repo is somebody's work, not a released path
    with a course-side original to carry an edit back to."""
    return [d for r in schedule.load(cohort_org).releases for d in r.due_deploys(now)]


def _deny(path: Path, clone_root: Path) -> Deny:
    """The copy filter for one end of a propagate.

    `deploy._copy_ignore` applies its root-only exclusions at the clone root when a WHOLE
    repo is being copied, and nowhere for a subpath copy - the same rule in both
    directions, so what a release refuses to ship is what a propagate refuses to carry
    back. The `.github` a cohort repo has holds nothing but toolkit workflows, and
    MAINTAINING.md is the course org describing itself."""
    return _copy_ignore(path if path == clone_root else None)


def _carried(root: Path, deny: Deny) -> set[str]:
    """Every file under `root` this copy would carry, as posix paths relative to it.

    Only used to work out what a propagate is NOT doing: the difference between the two
    ends is the set of files the cohort no longer has, which the pull request names rather
    than deletes. Filtered by the same `deny` the copy uses, or a whole-repo propagate
    would report the course org's own `.github` as something the cohort had deleted."""
    if root.is_file():
        return {root.name}
    found: set[str] = set()
    for dirpath, dirnames, filenames in root.walk():
        skip = deny(str(dirpath), dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in skip]
        found |= {
            (dirpath / f).relative_to(root).as_posix()
            for f in filenames
            if f not in skip
        }
    return found


def _base_branch(sd: Path) -> str | None:
    """The course repo's own default branch, off the clone `gh repo clone` checked out.

    None for a repo with no commits at all: there is nothing to branch from and nothing a
    cohort could have been released from either."""
    code, out = git(
        "-C", str(sd), "rev-parse", "--abbrev-ref", "--verify", "--quiet", "HEAD"
    )
    return out.strip() if code == 0 and out.strip() else None


@dataclass
class Source:
    """One course source repo, cloned and put on the propagate branch, and the account of
    what this run did to it - which is what its pull request body is written from."""

    dir: Path
    base: str
    carried: list[Carried] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # what the cohort no longer has


def _prepare_source(
    course_org: str, repo: str, branch: str, root: Path
) -> Source | None:
    """Clone one course source repo and cut `branch` from its default branch.

    `-B`, so a branch left by a previous run is thrown away rather than added to: this
    proposes what the cohort has NOW, and a branch that accumulated would keep re-offering
    an edit faculty had already declined."""
    sd = root / "course" / repo
    if not clone(course_org, repo, sd):
        log_err(f"could not clone source {course_org}/{repo}")
        return None
    base = _base_branch(sd)
    if base is None:
        log_err(f"{course_org}/{repo} has no commits - nothing to propagate into")
        return None
    code, out = git("-C", str(sd), *GIT_ENV, "checkout", "-B", branch, base)
    if code != 0:
        log_err(f"could not cut `{branch}` in {course_org}/{repo} - {out[:200]}")
        return None
    return Source(sd, base)


def _body(cohort_org: str, source: Source, branch: str) -> str:
    """The pull request's body: what was carried, what was deliberately left, what to do.

    Rewritten on every run (`refresh_body`), because it is a summary of what the branch
    now holds and the branch is regenerated every run - a body describing a previous run's
    branch is worse than none."""
    kept = (
        "\n\nThese paths are in this repo but no longer in the cohort's copy. "
        "**Deletions are not propagated** - they are listed here, not removed:\n"
        + "\n".join(f"- `{p}`" for p in source.kept)
        if source.kept
        else ""
    )
    return (
        f"`{cohort_org}` has edits to material this repo released to it. This branch "
        f"carries them back, one commit per released path, in the order the term ran.\n\n"
        f"{chr(10).join(c.line() for c in source.carried)}"
        f"{kept}\n\n"
        f"Merge it, cherry-pick the commits you want, or close it. `{branch}` is cut "
        f"fresh from `{source.base}` and force-pushed on every run, so closing this "
        f"settles nothing: the next run proposes whatever the cohort has then.\n"
    )


def _commit(sd: Path, message: str) -> bool:
    """Stage and commit one propagated path. False when nothing changed.

    `-f`, for the reason `deploy` gives: a course repo's own `.gitignore` would otherwise
    silently drop a file the cohort force-added, and the pull request would claim to carry
    a path it left behind."""
    git("-C", str(sd), *GIT_ENV, "add", "-A", "-f")
    if git("-C", str(sd), "diff", "--cached", "--quiet")[0] == 0:
        return False
    code, out = git(
        "-C", str(sd), *GIT_ENV, "commit", "-q", "--no-verify", "-m", message
    )
    if code != 0:
        log_err(f"  commit failed - {out[:200]}")
        return False
    return True


def _open_pr(
    course_org: str, repo: str, cohort_org: str, source: Source, branch: str
) -> int:
    """Push the branch and keep ONE pull request for it. Returns the error count.

    `--force-with-lease` rather than `--force`: the lease is against the ref this run
    cloned, so a branch somebody moved in the meantime refuses rather than being
    overwritten - the same branch is the only thing two concurrent runs could collide on."""
    slug = f"{course_org}/{repo}"
    code, out = git(
        "-C",
        str(source.dir),
        *GIT_ENV,
        "push",
        "-q",
        "--force-with-lease",
        "origin",
        branch,
    )
    if code != 0:
        log_err(f"  {slug}: could not push `{branch}` - {out[:200]}")
        return 1
    opened = pulls.upsert_pr(
        slug,
        head=branch,
        base=source.base,
        title=f"Cohort edits from {cohort_org}",
        body=_body(cohort_org, source, branch),
        refresh_body=True,
    )
    if opened.url:
        log_ok(f"  {slug}: {len(source.carried)} path(s) proposed - {opened.url}")
    return opened.errors


def propagate(
    course_org: str,
    cohort_org: str,
    now: datetime | None = None,
    dry_run: bool = False,
) -> int:
    """Propose `cohort_org`'s edits to its released material back into `course_org`.
    Returns the error count.

    A cohort with nothing due, or with nothing changed, opens nothing and says so in one
    line: this runs from a button and from the teardown, and a pull request per run saying
    "no change" would be the fastest way to make faculty stop reading them."""
    now = now or datetime.now(timezone.utc)
    deploys = due_deploys(cohort_org, now)
    log_step(
        f"Propagating {cohort_org} -> {course_org}"
        f"{' (dry run)' if dry_run else ''}: {len(deploys)} released path(s)"
    )
    if not deploys:
        log_ok(f"nothing has been released to {cohort_org} yet - nothing to carry back")
        return 0
    if dry_run:
        for d in deploys:
            log(
                f"  DRY-RUN  {cohort_org}/{d.cohort_dest_repo}/"
                f"{deploy_dest(d) or '(repo root)'} -> {course_org}/"
                f"{d.course_source_repo}/{d.course_source_path}"
            )
        return 0

    branch = branch_for(cohort_org)
    errors = 0
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        dests: dict[str, Path] = {}
        for repo in sorted({d.cohort_dest_repo for d in deploys}):
            dd = root / "cohort" / repo
            if clone(cohort_org, repo, dd):
                dests[repo] = dd
            else:
                log_err(f"could not clone {cohort_org}/{repo}")
        sources: dict[str, Source] = {}
        for repo in sorted({d.course_source_repo for d in deploys}):
            prepared = _prepare_source(course_org, repo, branch, root)
            if prepared is not None:
                sources[repo] = prepared
        # One end missing is one copy lost, counted once per deploy exactly as the release
        # counts it - both ends failing is still one path not carried.
        errors += sum(
            1
            for d in deploys
            if d.cohort_dest_repo not in dests or d.course_source_repo not in sources
        )

        for d in deploys:
            if d.cohort_dest_repo not in dests or d.course_source_repo not in sources:
                continue
            source = sources[d.course_source_repo]
            cohort_root = dests[d.cohort_dest_repo]
            cohort_rel = deploy_dest(d)
            srcp = _resolve_within(cohort_root, cohort_rel)
            destp = _resolve_within(source.dir, d.course_source_path)
            if srcp is None or destp is None:
                log_err(
                    f"unsafe path pair `{cohort_rel}` -> `{d.course_source_path}` for "
                    f"{d.course_source_repo} - skipped."
                )
                errors += 1
                continue
            where = f"{d.cohort_dest_repo}/{cohort_rel or '(repo root)'}"
            if not srcp.exists():
                # Released into a path the cohort no longer has, or never had: a note, not
                # a failure. A plan entry can be edited after it fired, and a cohort repo
                # is faculty's to reorganise.
                log(
                    f"  [note] {cohort_org}/{where} is not there - nothing to carry back"
                )
                continue
            deny = _deny(srcp, cohort_root)
            gone = sorted(
                _carried(destp, _deny(destp, source.dir)) - _carried(srcp, deny)
                if destp.exists()
                else ()
            )
            source.kept.extend(
                f"{d.course_source_path.strip('/')}/{p}".lstrip("/") for p in gone
            )
            try:
                if srcp.is_dir():
                    copy_tree(srcp, destp, deny)
                else:
                    destp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(srcp, destp)
            except (shutil.Error, OSError) as exc:
                log_err(f"could not copy `{where}` from {cohort_org}: {exc}")
                errors += 1
                continue
            course_rel = d.course_source_path.strip("/")
            if _commit(
                source.dir,
                f"propagate: {course_rel or 'the repo root'} from {cohort_org}",
            ):
                source.carried.append(Carried(where, course_rel))

        for repo in sorted(sources):
            source = sources[repo]
            if not source.carried:
                log_ok(f"  {course_org}/{repo}: no cohort edits to carry back")
                continue
            errors += _open_pr(course_org, repo, cohort_org, source, branch)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--course-org", required=True, help="Course org (the target)")
    parser.add_argument("--cohort-org", required=True, help="Cohort org (the source)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the cohort -> course path pairs and exit, cloning nothing.",
    )
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        errors = propagate(args.course_org, args.cohort_org, dry_run=args.dry_run)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
