"""dsl-course propagate -- carry a semester's edits to released material back into the
course org, as a pull request. The reverse of `deploy`:

    semester/<semester_dest_repo>/<semester_dest_path>     what the semester actually has now
            |  copy that path back
            v
    course/<course_source_repo>/<course_source_path> on branch `from-<semester-org>`
            |  one pull request per source repo
            v
    course/<course_source_repo>/<its default branch>  faculty merge, cherry-pick or close

The course org is the source of truth, and a release copies one way. Since a release lands
on `upstream` and is MERGED into the branch students read (`course.UPSTREAM_BRANCH`), an
instructor's correction typed into the semester repo now survives the next release - but it
lives only in that semester, and next year's semester is cut from the course org. This is what
carries it home.

WHAT IT PROPOSES, and nothing more. It writes to a branch of its own and opens a pull
request; a human decides what of it belongs in the course org. So it is deliberately
generous about what it copies and deliberately explicit about what it does not:

- Every deploy the semester's plan has already FIRED, in the plan's own order, one commit
  per released path. A copy that has not shipped yet has no semester-side edit to carry.
- DELETIONS ARE NOT PROPAGATED. A file the semester deleted stays in the course repo, and
  the pull request names it: deleting from a course repo on the strength of a semester's
  working copy is not a decision this should take unattended.
- A semester repo BEHIND its latest release is not read at all. Its `upstream` holds a
  release the branch students read has not merged - a held conflict pull request - so
  copying that branch back would offer the course org its own newer content as a semester
  edit, and merging it would revert the fix. Those paths are named, not carried.
- A full run REGENERATES the branch from the course repo's default branch and
  force-pushes it, so it proposes what the semester has now rather than accumulating.
  The pull request is reused and its body rewritten (`pulls.upsert_pr`), so a re-run is
  one conversation and not a second one.
- `--paths` RESTRICTS a run to some semester-side files or folders, each inside a released
  copy - the release record's, so a hand release counts - and mapped back through it to
  its course source. Per-path runs ACCUMULATE: while the pull request a per-path run left
  is open, the next adds its paths to that branch (`_per_path_lines`). A full run, or a
  per-path run after one, starts the branch again.

Nothing here names a person. The semester repo is readable by the whole semester and its
history has student pull requests in it; what is propagated is a path, and the pull
request says so in paths.

Usage:
    python3 -m dsl_course.propagate --course-org COURSE --semester-org SEMESTER
        [--paths 'materials/lectures/01/lab.md,extras/labs/02'] [--preview]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from . import pulls, release_record, schedule
from .course import PROPOSAL_BRANCH_PREFIX, UPSTREAM_BRANCH, is_repo_root
from .deploy import _resolve_within
from .fs import Deny, copy_tree
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
    plural,
)
from .records import SYSTEM_DIR
from .release_record import Key
from .schedule import Deploy
from .schedule_plan import deploy_dest, within
from .selection import ROOT_RELEASE_EXCLUDED, is_clutter


def branch_for(semester_org: str) -> str:
    """The branch a semester's edits are proposed on, in the COURSE repo.

    One spelling, because the push, the pull request lookup and the body's own explanation
    all have to name the same ref. Named after the semester it carries, because a course org
    releases into several at once and each one's edits are a separate conversation with
    faculty. Toolkit-owned: it is regenerated from the default branch on every run, so
    anything committed onto it by hand is lost at the next tick."""
    return f"{PROPOSAL_BRANCH_PREFIX}{semester_org}"


def _rel(path: str) -> str:
    """A plan path as this module spells it: `""` for every "release everything" spelling.

    `course.is_repo_root` owns which those are (`""`, `/` and `.` are all written by
    faculty), and without it a plan that says `.` reaches a commit subject, a pull request
    body and a `kept` path as a bare dot."""
    return "" if is_repo_root(path) else path.strip("/")


class Propagated(NamedTuple):
    """What one run did: the error count its callers fold into their own, the pull
    request it left open on each source repo, and the semester repos it did not read.

    The URLs are here for the teardown, which runs this as its first step and then seals
    the semester: the record it writes into the sealed repo is the only place that can
    still say where the semester's last edits went.

    `behind` is here for the same reader. A dest whose `upstream` is ahead is skipped and
    named in the pull request body, which is enough for a button somebody pressed - but
    when EVERY due dest is behind there is no pull request to name them in, and a record
    that then said "nothing had been edited" would be sealed into a repo nobody can
    correct. Repo names only: a dest is a materials repo, never a student's."""

    errors: int = 0
    urls: tuple[str, ...] = ()
    behind: tuple[str, ...] = ()
    # How many released paths were looked at - what a preview reports.
    checked: int = 0


def due_deploys(sched: schedule.Schedule, now: datetime) -> list[Deploy]:
    """Every copy this semester's plan has already fired, in the plan's own order.

    The plan's releases are sorted by `event_datetime` and each entry's `deploy:` list is
    in the order it was written, so this is session order - which is the order the commits
    are made in, so the branch reads like the term did.

    Assignment handouts are not here: an assignment is handed out from a TEMPLATE repo
    into per-student repos, and a student repo is somebody's work, not a released path
    with a course-side original to carry an edit back to."""
    return [d for r in sched.releases for d in r.due_deploys(now)]


def _carry_of(copies: dict[Key, dict], repo: str, path: str) -> Deploy | None:
    """The one-path copy that carries `repo`/`path` back, mapped through the recorded copy
    that holds it (`release_record`), or None when no copy into `repo` does.

    The deepest holder wins, and of two equally deep ones the later shipped: that is the
    copy whose content the semester's file last came from."""
    held = [
        (k, rest)
        for k in copies
        if k[2] == repo and (rest := within(k[3], path)) is not None
    ]
    if not held:
        return None
    k, rest = max(
        held,
        key=lambda h: (len(h[0][3]), copies[h[0]].get("last_shipped_at") or ""),
    )
    course = "/".join(p for p in (k[1], rest) if p)
    # `/` for the repo root, because a blank dest path means "mirror the source path".
    return Deploy(k[0], course or "/", repo, path or "/")


def select_paths(
    copies: dict[Key, dict], paths: list[str], default_repo: str
) -> list[Deploy]:
    """The copies a per-path run carries: one per named SEMESTER-side path, in the order
    named, each mapped through the copy the release record says shipped it - a scheduled
    release or one made by hand (`release_record`).

    A path is `<dest repo>/<path>`, or just `<path>` in the semester's `default_repo`
    (its `materials_repo`); a leading segment naming a released dest repo is read as the
    repo first. A path inside no released copy - not released, not due yet, or spelt with
    a `.`/`..` segment - is a ValueError naming it: carrying "something near it" back
    would propose an edit nobody asked for."""
    dest_repos = {k[2] for k in copies}
    out: dict[tuple[str, str], Deploy] = {}
    for raw in paths:
        parts = [p for p in raw.strip().split("/") if p]
        found = None
        if parts and not {".", ".."} & set(parts):
            if parts[0] in dest_repos:
                found = _carry_of(copies, parts[0], "/".join(parts[1:]))
            found = found or _carry_of(copies, default_repo, "/".join(parts))
        if found is None:
            raise ValueError(
                f"`{raw.strip()}` is not inside anything already released to this "
                f"semester - name a file or folder of a released copy"
            )
        out.setdefault(release_record.key_of(found)[2:], found)
    return list(out.values())


def _deny(path: Path, clone_root: Path) -> Deny:
    """The copy filter for one end of a propagate: `.git` and clutter at every depth, and
    for a WHOLE repo the root's plumbing (`selection.ROOT_RELEASE_EXCLUDED` and `.system/`),
    so what a release refuses to ship as plumbing a propagate refuses to carry back. The
    `.github` a semester repo has holds nothing but toolkit workflows, and `.system/` is
    the course org describing itself.

    Deliberately NOT the rest of the release's layers (`selection`): the denylist and a
    source's `.releaseignore` say what students may not see, and a file the semester ADDED
    at such a path is a proposal in a pull request a human reads, not a leak."""
    root = path if path == clone_root else None
    plumbing = {*ROOT_RELEASE_EXCLUDED, SYSTEM_DIR}

    def ignore(dirpath: str, names: list[str]) -> set[str]:
        skip = {n for n in names if n == ".git" or is_clutter(n)}
        if root is not None and Path(dirpath) == root:
            skip |= {n for n in names if n in plumbing}
        return skip

    return ignore


def _carried(root: Path, deny: Deny) -> set[str]:
    """Every file under `root` this copy would carry, as posix paths relative to it.

    Only used to work out what a propagate is NOT doing: the difference between the two
    ends is the set of files the semester's copy does not have - deleted there, or never
    released to it at all - which the pull request names rather than deletes. Filtered by
    the same `deny` the copy uses, or a whole-repo propagate
    would report the course org's own `.github` as something the semester had deleted."""
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
    semester could have been released from either."""
    code, out = git(
        "-C", str(sd), "rev-parse", "--abbrev-ref", "--verify", "--quiet", "HEAD"
    )
    return out.strip() if code == 0 and out.strip() else None


def _behind_its_release(dd: Path) -> bool:
    """Whether a semester clone's default branch is missing what was last released to it.

    A release lands on `deploy.UPSTREAM_BRANCH` and is MERGED into the branch students
    read, so a merge that conflicted - or a push that failed, or the quarter hour between
    a course commit and the release carrying it - leaves `upstream` ahead of that branch
    with a pull request standing. Propagating from it then reads the course org's own
    newer content back as a semester edit, and merging what this proposes reverts the
    course's fix. The teardown propagates before it seals, so a conflict pull request open
    on archive day is exactly that case.

    A dest with NO `upstream` has never had a merge-based release - a semester released into
    before that branch existed - so there is nothing it could be behind: up to date."""
    remote = f"origin/{UPSTREAM_BRANCH}"
    if git("-C", str(dd), "rev-parse", "--verify", "--quiet", remote)[0] != 0:
        return False
    return git("-C", str(dd), "merge-base", "--is-ancestor", remote, "HEAD")[0] != 0


@dataclass
class Source:
    """One course source repo, cloned and put on the propagate branch, and the account of
    what this run did to it - which is what its pull request body is written from."""

    dir: Path
    base: str
    # One rendered `- `<semester repo>/<path>` -> `<course path>`` line per commit made.
    # Both ends, because they are usually the same string and occasionally not - a
    # `semester_dest_path` that renames the folder is exactly the case a reader of the pull
    # request needs spelled out.
    carried: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)  # what the semester's copy lacks
    behind: list[str] = field(default_factory=list)  # dest paths a stale dest holds
    # The semester repos those commits were read from, so the body can name them rather
    # than say `materials` at a course org whose plan releases into three repos. Added
    # beside `carried`, so a source with commits always has at least one.
    dests: set[str] = field(default_factory=set)


# The trailer a per-path run's commit carries, naming the path pair it carried: what
# tells the next per-path run the branch is one to add to, and what that run's body lists.
PATH_TRAILER = "Propagate-Path"


def _carried_line(where: str, course_rel: str) -> str:
    """One `Source.carried` line: the semester path and the course path it went to."""
    return f"- `{where}` -> `{course_rel or '(repo root)'}`"


def _per_path_lines(sd: Path, slug: str, base: str, branch: str) -> list[str] | None:
    """The carried lines of the per-path branch a previous run left under an open pull
    request, oldest first - or None when there is nothing to add to: no such branch, no
    open pull request onto `base` for it (closed, merged or retargeted: settled), or a
    commit on it that is not a per-path one (a full run's branch is regenerated)."""
    remote = f"refs/remotes/origin/{branch}"
    if git("-C", str(sd), "rev-parse", "--verify", "-q", remote)[0] != 0:
        return None
    pr = pulls.find_pr(slug, branch)
    if pr is None or pr.base != base:
        return None
    code, out = git(
        "-C", str(sd), "log", "--reverse", "--format=%B%x00", f"{base}..{remote}"
    )
    if code != 0:
        return None
    lines = []
    for message in (m for m in out.split("\0") if m.strip()):
        pairs = [
            v.strip().split(" -> ", 1)
            for line in message.splitlines()
            if (v := line.removeprefix(f"{PATH_TRAILER}: ")) != line
        ]
        if not pairs or any(len(pair) != 2 for pair in pairs):
            return None
        lines += [_carried_line(w, c) for w, c in pairs]
    return lines or None


def _prepare_source(
    course_org: str, repo: str, branch: str, root: Path, per_path: bool
) -> Source | None:
    """Clone one course source repo and put it on `branch`.

    A full run cuts `branch` fresh from the default branch (`-B`), so a branch left by a
    previous run is thrown away rather than added to: this proposes what the semester has
    NOW, and a branch that accumulated would keep re-offering an edit faculty had already
    declined. A `per_path` run ADDS to a per-path branch still open for review
    (`_per_path_lines`), so pressing the button on two files in turn proposes both."""
    sd = root / "course" / repo
    if not clone(course_org, repo, sd):
        log_err(f"could not clone source {course_org}/{repo}")
        return None
    base = _base_branch(sd)
    if base is None:
        log_err(f"{course_org}/{repo} has no commits - nothing to propagate into")
        return None
    earlier = (
        _per_path_lines(sd, f"{course_org}/{repo}", base, branch) if per_path else None
    )
    start = f"origin/{branch}" if earlier else base
    code, out = git("-C", str(sd), *GIT_ENV, "checkout", "-B", branch, start)
    if code != 0:
        log_err(f"could not cut `{branch}` in {course_org}/{repo} - {out[:200]}")
        return None
    source = Source(sd, base)
    for line in earlier or ():
        source.carried.append(line)
        # The semester repo is the first segment of the line's semester path.
        source.dests.add(line.split("`", 2)[1].split("/", 1)[0])
    return source


def _semester_repos(dests: set[str]) -> str:
    """How the body names the semester repos it read the edits out of.

    A semester's dest is usually `materials` and occasionally is not - a plan may release
    into several repos, and a body that said "its materials repo" at one of those would
    be describing a repo the reader has to go and correct."""
    names = [f"`{r}`" for r in sorted(dests)]
    joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"its {joined} repo{'' if len(names) == 1 else 's'}"


def _body(semester_org: str, source: Source, branch: str, carries: str) -> str:
    """The pull request's body: what was carried, what was deliberately left, what to do.

    Rewritten on every run (`refresh_body`), because it is a summary of what the branch
    now holds - a full run regenerates the branch and a per-path run adds to it, and a
    body describing a previous run's branch is worse than none. `carries` is the
    sentence saying which kind of run it was (`_carries`)."""
    kept = (
        "\n\nThese paths are in this repo but not in the semester's copy - never released "
        "there (withheld by a `.releaseignore`, or still an unwritten stub), or deleted "
        "there. **Not propagated** either way - they are listed here, not removed:\n"
        + "\n".join(f"- `{p}`" for p in source.kept)
        if source.kept
        else ""
    )
    behind = (
        f"\n\n**Not carried** - these semester repos are behind their latest release. "
        f"Merge the open `{UPSTREAM_BRANCH}` -> default-branch pull request in the "
        f"semester repo first, then run this again:\n"
        + "\n".join(f"- `{p}`" for p in source.behind)
        if source.behind
        else ""
    )
    return (
        f"This pull request is opened automatically by the **Propagate semester edits** "
        f"workflow.\n\n"
        f"The semester org `{semester_org}`, bootstrapped from this course org, has edits to "
        f"content in {_semester_repos(source.dests)} that this repo released to it (the "
        f"result of student pull requests, or instructors' own pushes). {carries}:\n\n"
        f"{chr(10).join(source.carried)}"
        f"{kept}{behind}\n\n"
        f"Merge it, cherry-pick the commits you want, or close it.\n"
        f"- To take every edit from the semester: merge this pull request.\n"
        f"- To take specific edits: cherry-pick the commits you want (one per path) "
        f"onto `{source.base}`, or edit this branch and then merge it.\n"
        f"- To reject every edit: close it. Nothing is changed in this repo.\n\n"
        f"A full run cuts `{branch}` fresh from `{source.base}` and force-pushes it, so "
        f"this pull request then shows that run only; a run for some paths adds them to "
        f"a branch an earlier one left open. Closing it settles nothing: the next run "
        f"proposes whatever the semester has then.\n"
    )


def _carries(restricted: bool) -> str:
    """What a pull request body says the run carried: the paths it was asked for, or
    every released path."""
    if restricted:
        return "Only the paths asked for are carried back, one commit each"
    return (
        "This branch carries them back, one commit per released path, in the "
        "order the semester ran"
    )


def _commit(sd: Path, message: str) -> bool:
    """Stage and commit one propagated path. False when nothing changed.

    `-f`, for the reason `deploy` gives: a course repo's own `.gitignore` would otherwise
    silently drop a file the semester force-added, and the pull request would claim to carry
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
    course_org: str,
    repo: str,
    semester_org: str,
    source: Source,
    branch: str,
    carries: str,
) -> Propagated:
    """Push the branch and keep ONE pull request for it.

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
        return Propagated(1)
    opened = pulls.upsert_pr(
        slug,
        head=branch,
        base=source.base,
        title=f"Semester edits from {semester_org}",
        body=_body(semester_org, source, branch, carries),
        refresh_body=True,
    )
    if opened.url:
        log_ok(f"  {slug}: {len(source.carried)} path(s) proposed - {opened.url}")
    return Propagated(opened.errors, (opened.url,) if opened.url else ())


def propagate(
    course_org: str,
    semester_org: str,
    now: datetime | None = None,
    dry_run: bool = False,
    paths: list[str] | None = None,
) -> Propagated:
    """Propose `semester_org`'s edits to its released material back into `course_org`.

    `paths` restricts the run to those semester-side files or folders, mapped through the
    release record (`select_paths`), so a copy released by hand carries back too; one
    outside every released copy refuses the whole run, previewed or not. A full run
    carries every copy the plan has fired (`due_deploys`).

    A semester with nothing due, or with nothing changed, opens nothing and says so in one
    line: this runs from a button and from the teardown, and a pull request per run saying
    "no change" would be the fastest way to make faculty stop reading them."""
    now = now or datetime.now(timezone.utc)
    sched = schedule.load(semester_org)
    if not paths:
        deploys = due_deploys(sched, now)
    else:
        try:
            copies = release_record.load(semester_org).copies
            deploys = select_paths(copies, paths, sched.materials_repo)
        except ValueError as exc:
            log_err(f"{exc}.")
            return Propagated(1)
    log_step(
        f"Propagating {semester_org} -> {course_org}"
        f"{' (preview)' if dry_run else ''}: {len(deploys)} "
        f"{'chosen' if paths else 'released'} path(s)"
    )
    if not deploys:
        log_ok(
            f"nothing has been released to {semester_org} yet - nothing to carry back"
        )
        return Propagated()
    if dry_run:
        for d in deploys:
            log(
                f"  PREVIEW  {semester_org}/{d.semester_dest_repo}/"
                f"{_rel(deploy_dest(d)) or '(repo root)'} -> {course_org}/"
                f"{d.course_source_repo}/{_rel(d.course_source_path) or '(repo root)'}"
            )
        return Propagated(checked=len(deploys))

    branch = branch_for(semester_org)
    carries = _carries(bool(paths))
    errors = 0
    urls: list[str] = []
    with tempfile.TemporaryDirectory() as work:
        # RESOLVED, exactly as `deploy` resolves its own clone root: `_resolve_within`
        # returns a resolved path, and `_deny` decides "is this the whole repo?" by
        # comparing the two. A temporary directory that sits behind a symlink - every
        # macOS `/var/folders/...` does - makes that comparison false for a whole-repo
        # copy, and the root-only exclusions then do not apply: the semester's `.github`
        # would be carried over the COURSE org's own workflows, and every path a release
        # withholds at the root would be reported as something the semester had deleted.
        root = Path(work).resolve()
        dests: dict[str, Path] = {}
        behind: set[str] = set()
        for repo in sorted({d.semester_dest_repo for d in deploys}):
            dd = root / "semester" / repo
            if not clone(semester_org, repo, dd):
                log_err(f"could not clone {semester_org}/{repo}")
                continue
            if _behind_its_release(dd):
                behind.add(repo)
                log(
                    f"  [skip] {semester_org}/{repo} is behind its latest release - "
                    f"nothing is carried back from it until the open "
                    f"`{UPSTREAM_BRANCH}` -> `{_base_branch(dd) or 'default branch'}` "
                    f"pull request there is merged"
                )
                continue
            dests[repo] = dd
        sources: dict[str, Source] = {}
        for repo in sorted({d.course_source_repo for d in deploys}):
            prepared = _prepare_source(course_org, repo, branch, root, bool(paths))
            if prepared is not None:
                sources[repo] = prepared
        for d in deploys:
            source = sources.get(d.course_source_repo)
            semester_rel = _rel(deploy_dest(d))
            course_rel = _rel(d.course_source_path)
            where = f"{d.semester_dest_repo}/{semester_rel or '(repo root)'}"
            if d.semester_dest_repo in behind:
                # Not an error and not a note: the semester's copy is intact, it is simply
                # not the copy to read yet. Named in the body, because a pull request
                # silently missing a dest's paths reads as "the semester changed nothing".
                if source is not None:
                    source.behind.append(where)
                continue
            if d.semester_dest_repo not in dests or source is None:
                # One end missing is one copy lost, counted once per deploy exactly as the
                # release counts it - both ends failing is still one path not carried.
                errors += 1
                continue
            semester_root = dests[d.semester_dest_repo]
            srcp = _resolve_within(semester_root, semester_rel)
            destp = _resolve_within(source.dir, course_rel)
            if srcp is None or destp is None:
                log_err(
                    f"unsafe path pair `{semester_rel}` -> `{course_rel}` for "
                    f"{d.course_source_repo} - skipped."
                )
                errors += 1
                continue
            if not srcp.exists():
                # Released into a path the semester no longer has, or never had: a note, not
                # a failure. A plan entry can be edited after it fired, and a semester repo
                # is faculty's to reorganise.
                log(
                    f"  [note] {semester_org}/{where} is not there - nothing to carry back"
                )
                continue
            deny = _deny(srcp, semester_root)
            gone = (
                sorted(_carried(destp, _deny(destp, source.dir)) - _carried(srcp, deny))
                if destp.exists()
                else []
            )
            source.kept.extend(f"{course_rel}/{p}".lstrip("/") for p in gone)
            try:
                if srcp.is_dir():
                    copy_tree(srcp, destp, deny)
                else:
                    destp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(srcp, destp)
            except (shutil.Error, OSError) as exc:
                log_err(f"could not copy `{where}` from {semester_org}: {exc}")
                errors += 1
                continue
            message = f"propagate: {course_rel or 'the repo root'} from {semester_org}"
            if paths:
                message += (
                    f"\n\n{PATH_TRAILER}: {where} -> {course_rel or '(repo root)'}"
                )
            if _commit(source.dir, message):
                line = _carried_line(where, course_rel)
                if line not in source.carried:
                    source.carried.append(line)
                source.dests.add(d.semester_dest_repo)

        for repo in sorted(sources):
            source = sources[repo]
            if not source.carried:
                log_ok(f"  {course_org}/{repo}: no semester edits to carry back")
                continue
            opened = _open_pr(course_org, repo, semester_org, source, branch, carries)
            errors += opened.errors
            urls += list(opened.urls)
    return Propagated(errors, tuple(urls), tuple(sorted(behind)), len(deploys))


def propagate_summary(done: Propagated, dry_run: bool) -> Summary:
    """Keep for future terms' sentence. Repo counts only; a source repo is a materials
    repo, never a student's."""
    if dry_run:
        return Summary(
            f"Preview: {plural(done.checked, 'released item')} would be checked for "
            f"edits to keep for future terms.",
            {"checked": done.checked},
        )
    counts = {"pull_requests": len(done.urls), "checked": done.checked}
    if not done.urls:
        return Summary(
            "No semester edits to keep for future terms.",
            counts,
            conclusion="nothing_to_do",
        )
    return Summary(
        f"Kept for future terms: {plural(len(done.urls), 'pull request')} opened on "
        f"the course's materials.",
        counts,
    )


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("--course-org", required=True, help="Course org (the target)")
    parser.add_argument(
        "--semester-org",
        required=True,
        help="Semester org (the source)",
    )
    # Default ON, like every other button whose real run reaches into another org:
    # the rendered workflow passes --preview / --no-preview explicitly, so a bare local
    # invocation cannot force-push a branch and open pull requests by accident.
    add_preview_flag(
        parser,
        "Print the semester -> course path pairs and exit, cloning nothing (default).",
    )
    parser.add_argument(
        "--paths",
        default="",
        help="Only these semester-side paths, comma-separated: `<dest repo>/<path>`, or "
        "`<path>` in the semester's materials repo. Each must lie inside a released copy.",
    )
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        done = propagate(
            args.course_org,
            args.semester_org,
            dry_run=args.preview,
            paths=comma_items(args.paths),
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1
    if done.errors:
        return 1
    return propagate_summary(done, args.preview)


if __name__ == "__main__":
    sys.exit(main())
