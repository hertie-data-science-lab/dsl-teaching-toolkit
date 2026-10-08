"""dsl-course site -- regenerate a course/semester website from the live org structure.

Two sites, two audiences, one set of Jekyll templates (`templates/site/`):

- **semester site** (`<semester>.github.io`, `sync_site`) - student-facing, the 0.9.0 site
  (decision 0035 rule 1): the schedule, a tab per row kind, the assignment pages, All
  Materials and Your Profile, under a banner to the student console, which carries the
  same materials. Its file links point at the semester's PRIVATE content repos, so they
  404 for non-members (the gate is deliberate). Regenerates `_lectures/`, `_assignments/`,
  `_events/` from the release state. Releases call it; the Sync site action runs it on
  demand.

- **course site** (`<course-org>.github.io`) - PUBLIC open courseware, opt-in, built in
  `public_site`; `public-sync` here is its CLI. It hosts the shared files rather than
  linking into the private repos - see that module.

Both hand their plan to `site_repo`, which applies it; pushing the site repo redeploys it.

Usage:
    python3 -m dsl_course.site sync --course-org TEST-HERTIE-COURSE \\
        --semester-org TEST-HERTIE-SEMESTER-f2026
    python3 -m dsl_course.site public-sync --course-org TEST-HERTIE-COURSE [--daily]
"""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from textwrap import indent

import yaml
from pathspec import GitIgnoreSpec

from . import policy, schedule, status, teams
from .course import (
    CONFIG_REPO,
    CUTOFF_SENTENCE,
    INSTRUCTORS_FILE,
    SELF_SELECT,
    identifier,
    late_rule,
    pages_repo,
    row_name,
    semester_label,
    semester_of,
    session_number,
    shape_note,
    shared_repo,
    submission_repo,
)
from .discovery import (
    SEMESTERS_PATH,
    discover_release_sources,
    discover_semesters,
    handed_out_assignments,
    join_issue_url,
    list_org_repos,
    live_semesters,
    semester_content_repos,
    semester_is_live,
)
from .gh_contents import get_file_content, repo_tree
from .ghcli import clone
from .grades import load_grading_spec, spoken_day, team_cap, total_points
from .log import CLIParser, log, log_err, log_step, log_withheld
from .materials import ASSETS_KIND, publishable
from .materials import read as read_materials
from .opencourse import read as read_opencourse
from .public_site import publish as publish_public_site
from .readings import demote_headings
from .releaseignore import listed
from .releaseignore import parse as parse_patterns
from .repos import (
    default_branch,
)
from .schedule_plan import (
    Aliases,
    PlannedRow,
    declared_syllabus,
    deploy_dest,
    deploy_section,
    offplan_folders,
    planned_rows,
    site_rows,
)
from .site_repo import (
    DECK_EXTENSIONS,
    RENDERED_EXTENSIONS,
    SITE_FILES_DIR,
    Hosted,
    Link,
    SitePlan,
    block,
    console_yaml,
    file_ext,
    file_link,
    gh_url,
    iso_when,
    kinds_yaml,
    landed_links,
    link_extensions,
    links_block,
    liquid_raw,
    nav_yaml,
    people_yaml,
    q,
    retired_kind_pages,
    row_file,
    row_tabs,
    site_readme,
    site_templates,
    slug,
    sync_site_repo,
    theme_pages,
    yaml_file,
)


def _semester_start(semester_org: str) -> date:
    """Best-effort semester start from a fYYYY / sYYYY tag (for schedule ordering)."""
    tag = semester_of(semester_org)
    if tag:
        return date(int(tag[1:]), 9 if tag[0] == "f" else 2, 1)
    return date(2026, 1, 1)


def _instructors_meta(semester_org: str) -> tuple[dict, str]:
    """A semester's `instructors.yml` and its path. The old `people.yml` is never read
    (decision 0012): a semester that has not migrated gets the instructors-team cards."""
    return yaml_file(semester_org, CONFIG_REPO, INSTRUCTORS_FILE), INSTRUCTORS_FILE


def _semester_label(semester_org: str) -> str:
    """fYYYY -> 'Fall YYYY', sYYYY -> 'Spring YYYY' (for site.course_semester)."""
    return semester_label(semester_of(semester_org)) or ""


@cache
def _repo_tree(org: str, repo: str) -> tuple[str, tuple[str, ...]]:
    """(default branch, every blob path in it) for a repo - one recursive tree fetch,
    memoised for the run. A semester site asks for the files of EVERY released session, and
    they nearly all live in the same repo, so without the memo the identical tree got
    fetched once per session. Paths come back sorted, so callers filtering them keep a
    stable diff.

    Unbounded cache: this is a one-shot CLI process, and the trees it reads are the
    handful of repos one semester released into.

    The fetch itself is gh_contents.repo_tree (shared with discovery's directory-side twin, so
    the absent-vs-failed discrimination is written once): a genuinely absent/empty tree is
    `()` and the caller simply finds no files, while any other failure RAISES rather than
    reporting an empty tree - swallowed, it republished the site with every material link
    stripped."""
    branch = default_branch(org, repo, fallback="main")
    return branch, repo_tree(org, repo, branch, "blob")


# ---------------------------------------------------------------- publicly hosted copies

# A course's declaration of what its semester sites may host, in the source repo faculty
# edit (one file per course, applying to every semester of it).
PUBLISH_FILE = "publish.yml"

# GitHub refuses a file over 100 MB on a push, so one carried into the site repo fails the
# sync's own push rather than the release that put it in the materials repo.
_MAX_PUBLIC_FILE_BYTES = 100 * 1024 * 1024


@cache
def _publish_policy(course_org: str, source_repo: str) -> GitIgnoreSpec | None:
    """What `publish.yml` in `source_repo` declares public, or None when it declares
    nothing.

    The policy is COURSE-level and lives in the source repo faculty actually edit, not in
    each semester's copy. Memoised for the run because `--all-semesters` asks the same
    course the same question once per semester. Patterns go through the parser faculty's
    `.releaseignore` goes through, so one syntax covers both directions of the question.

    A file that is absent or empty is "nothing public", and the mirror may then delete
    what an earlier sync copied. A file that does not PARSE, or whose `public:` is not a
    list of patterns, stops the sync and reports: read as "nothing public" it would
    unpublish a whole course's rendered decks over a typo, on a green run. The second is
    a `RuntimeError`, the faculty-fixable config fault every caller of `sync_site` (a
    hand-out, the CLI) reports in one line and survives."""
    declared = yaml_file(course_org, source_repo, PUBLISH_FILE).get("public")
    if declared is None:
        return None
    if not isinstance(declared, list) or not all(isinstance(x, str) for x in declared):
        raise RuntimeError(
            f"{course_org}/{source_repo}/{PUBLISH_FILE}: `public:` must be a list of "
            "patterns"
        )
    return parse_patterns("\n".join(declared)) if declared else None


def _publish_policies(
    course_org: str, sched: schedule.Schedule, content_repos: list[str]
) -> dict[str, tuple[GitIgnoreSpec, ...]]:
    """Each semester content repo the schedule releases into, mapped to the policies of
    the source repos that feed it.

    Keyed on the DESTINATION, because that is the repo whose files the site links and
    whose bytes the mirror copies. Several sources may feed one destination, so a path is
    public if ANY of their policies says so. A repo with no policies at all is still a key:
    that is the instruction to delete what an earlier sync copied for it.

    The schedule's declared destinations, not discovery's findings: this decides what gets
    CLONED and copied into a public site repo, so it reads a faculty declaration rather
    than a heuristic over an org listing (the same argument `_indexable_repos` makes)."""
    sources: dict[str, set[str]] = {}
    for release in sched.releases:
        for d in release.deploy:
            if d.semester_dest_repo in content_repos and d.course_source_repo:
                sources.setdefault(d.semester_dest_repo, set()).add(
                    d.course_source_repo
                )
    return {
        repo: tuple(
            spec
            for source in sorted(source_repos)
            if (spec := _publish_policy(course_org, source)) is not None
        )
        for repo, source_repos in sources.items()
    }


# A destination repo's copies as (path in the semester copy, path in the course source)
# pairs, one per deploy into it (`_deploy_sources`).
Renames = dict[str, tuple[tuple[str, str], ...]]


def _deploy_sources(sched: schedule.Schedule) -> Renames:
    """Each semester repo the schedule releases into, mapped to where each copy into it
    came from: `(semester_dest_path, course_source_path)`, the destination as
    `deploy_dest` resolves it. What turns a path in the semester copy back into the
    path faculty wrote a pattern against, when a deploy renamed it on the way."""
    out: dict[str, list[tuple[str, str]]] = {}
    for release in sched.releases:
        for d in release.deploy:
            pair = (deploy_dest(d), d.course_source_path.strip("/"))
            out.setdefault(d.semester_dest_repo, []).append(pair)
    return {repo: tuple(pairs) for repo, pairs in out.items()}


def _source_paths(path: str, renames: tuple[tuple[str, str], ...]) -> set[str]:
    """Where `path` of a semester copy came from in the course source, through every
    deploy that landed it (a copy may land inside another, so there can be several)."""
    out = set()
    for dest, source in renames:
        if not dest:
            rest = path
        elif path == dest:
            rest = ""
        elif path.startswith(f"{dest}/"):
            rest = path[len(dest) + 1 :]
        else:
            continue
        out.add("/".join(p for p in (source, rest) if p))
    return out


def _bundle_prefix(path: str) -> str:
    """The `<stem>_files/` directory a rendered deck keeps its assets in, beside it."""
    return f"{path.rsplit('.', 1)[0]}_files/"


def _matched(
    paths: tuple[str, ...],
    specs: tuple[GitIgnoreSpec, ...],
    allowed: Callable[[str], bool],
) -> set[str]:
    """The paths a policy makes public and `allowed` lets through: rendered formats, plus
    each matched deck's bundle (`_public_selection`)."""
    matched = {
        path
        for path in paths
        if file_ext(path) in RENDERED_EXTENSIONS
        and any(spec.check_file(path).include for spec in specs)
        and allowed(path)
    }
    for path in list(matched):
        if file_ext(path) in DECK_EXTENSIONS:
            prefix = _bundle_prefix(path)
            matched |= {a for a in paths if a.startswith(prefix) and allowed(a)}
    return matched


def _prune(served: Path, keep: set[str]) -> None:
    """Delete every file under `served` that is not in `keep`, and the directories that
    leaves empty."""
    if not served.is_dir():
        return
    for path in sorted(served.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            if not any(path.iterdir()):
                path.rmdir()
        elif path.relative_to(served).as_posix() not in keep:
            path.unlink()
    if not any(served.iterdir()):
        served.rmdir()


def _public_selection(
    src: Path,
    repo: str,
    paths: tuple[str, ...],
    specs: tuple[GitIgnoreSpec, ...],
    withhold: tuple[str, ...] = (),
    renames: tuple[tuple[str, str], ...] = (),
) -> frozenset[str]:
    """Which paths of `repo` the site can host, out of its released tree.

    What a policy matches - last match wins WITHIN one policy, which is what makes
    `!lectures/09_*/**` carve a session back out - and only where a link will open the
    copy: a file a browser renders (`RENDERED_EXTENSIONS`, html/htm/pdf), plus the
    `<stem>_files/` bundle beside each matched deck, without which it loads with no
    figures and no styles. A matched `.ipynb`, `.md` or `.csv` is not copied: GitHub
    already renders it, and nothing would link the copy.

    Two deny filters gate every candidate, bundles included, and cannot be written
    around: the denylist (`materials.publishable`), and `opencourse.yml`'s `withhold`
    (`withhold`) - a file kept off the open-courseware site is never hosted publicly here
    either. `withhold` is written against SOURCE repo paths, so it is matched against the
    path in this copy AND every source path a deploy landed it from (`renames`, this
    repo's `_deploy_sources`): a deploy that renames a withheld folder hosts nothing.

    A file GitHub would refuse on a push is dropped with a warning rather than failing the
    sync. So is anything that is not a regular file - `lstat`, so a symlink is judged as
    the link it is rather than as what it points at - and anything the clone does not
    have. Judged over the same tree the links are built from (`_repo_tree`); the clone is
    only where the bytes and the sizes come from."""

    ignore = listed(withhold) if withhold else None

    def withheld(path: str) -> bool:
        # Every name above a released blob is a directory, and the blob itself is not.
        return any(
            ignore.excludes(p, lambda rel, p=p: rel != p)
            for p in {path, *_source_paths(path, renames)}
        )

    def allowed(path: str) -> bool:
        return publishable(path) and not (ignore and withheld(path))

    keep = set()
    for path in _matched(paths, specs, allowed):
        try:
            st = (src / path).lstat()
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if st.st_size > _MAX_PUBLIC_FILE_BYTES:
            log_withheld(
                f"{repo}/{path} from the semester site's public copies: it is over "
                f"{_MAX_PUBLIC_FILE_BYTES // (1024 * 1024)} MB, which GitHub refuses"
            )
            continue
        keep.add(path)
    return frozenset(keep)


def _mirror_public(
    site_wd: Path,
    semester_org: str,
    policies: dict[str, tuple[GitIgnoreSpec, ...]],
    withhold: tuple[str, ...] | None = (),
    renames: Renames | None = None,
) -> Hosted:
    """Copy every publicly declared file of this semester's content repos into the site's
    own `files/<repo>/` tree, and say what actually landed there.

    The copy source is the RELEASED semester repo, never the course-org source: one
    release boundary for the whole toolkit, so a `.releaseignore` that held a file back
    from the semester holds it back from the public site too. The clone is shallow.

    Deleted and rebuilt per repo on every sync, which is what makes unpublishing work:
    removing a pattern removes the copy. Only ever AFTER a successful clone - a site
    republished with every rendered deck deleted because one clone failed is a worse
    outage than a copy one sync stale. A `files/<repo>/` whose repo is no longer a key of
    `policies` (the plan stopped releasing into it) goes too.

    `withhold` is `opencourse.yml`'s list and `renames` the plan's `_deploy_sources`
    (`_public_selection`). `withhold=None` is an `opencourse.yml` that could not be read:
    then only the deletion half runs - stale repos, repos with nothing declared, and every
    copy the policy no longer matches go, with no clone - and nothing is copied or linked,
    because what the file would withhold is unknown.

    Logs name repos and paths only: `policies` covers the release plan's declared
    destinations, never a student's repo."""
    hosted: dict[str, frozenset[str]] = {}
    root = site_wd / SITE_FILES_DIR
    if root.is_dir():
        for stale in sorted(root.iterdir()):
            if stale.name not in policies:
                if stale.is_dir():
                    shutil.rmtree(stale)
                else:
                    stale.unlink()
    for repo in sorted(policies):
        served = root / repo
        if not policies[repo]:
            # Nothing declared public: an earlier sync's copy has to go.
            if served.exists():
                shutil.rmtree(served)
            continue
        _branch, paths = _repo_tree(semester_org, repo)
        if withhold is None:
            # A superset of what a readable file would keep: whatever is outside it was
            # unpublished, and has to go now rather than when the file is fixed.
            _prune(served, _matched(paths, policies[repo], publishable))
            continue
        with tempfile.TemporaryDirectory() as work:
            src = Path(work) / repo
            if not clone(semester_org, repo, src, shallow=True):
                log_err(
                    f"could not clone {semester_org}/{repo} - its public copies on the "
                    "site are left as the last sync made them"
                )
                continue
            keep = _public_selection(
                src,
                repo,
                paths,
                policies[repo],
                withhold,
                (renames or {}).get(repo, ()),
            )
            if served.exists():
                shutil.rmtree(served)
            if not keep:
                continue
            for rel in keep:
                dest = served / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src / rel, dest)
            hosted[repo] = keep
            log(f"  hosting {len(keep)} public file(s) from {repo}")
    return hosted


@dataclass
class _Landed:
    """What one copy of a row has landed in the semester, found in its repo's tree: the
    links the row shows, and (on a readings row) the reading-list overlays it inlines."""

    repo: str
    section: str
    links: list[Link]
    overlays: list[str] = field(default_factory=list)
    # A single file at the repo's root - a course document (the syllabus, a README),
    # which is what the home page shows rather than a row.
    root_file: bool = False


def _landed(
    semester_org: str,
    deploy: schedule.Deploy,
    allow: frozenset[str],
    readings: bool,
    aliases: Aliases,
    hosted: Hosted,
) -> _Landed | None:
    """What `deploy` has landed, or None while nothing has (`site_repo.landed_links`),
    read off the destination repo's memoised tree (`_repo_tree`), so a released folder
    whose name carries no ordinal is as linked as one that does. Its section is the one
    the status gives it: through its source repo's `aliases`. A file the site hosts a
    copy of (`hosted`) links that copy too."""
    repo, path = deploy.semester_dest_repo, deploy_dest(deploy)
    branch, blobs = _repo_tree(semester_org, repo)
    found = landed_links(
        semester_org, repo, branch, blobs, path, allow, readings, hosted
    )
    if found is None:
        return None
    links, overlays = found
    root_file = bool(path) and path in blobs and "/" not in path
    section = deploy_section(deploy, aliases(deploy.course_source_repo))
    return _Landed(repo, section, links, overlays, root_file)


def _row_landed(
    semester_org: str,
    deploys: tuple[schedule.Deploy, ...],
    allow: frozenset[str],
    live_repos: frozenset[str],
    readings: bool,
    aliases: Aliases,
    hosted: Hosted,
) -> list[_Landed]:
    """Everything these copies have landed, in plan order. A copy into a repo the semester
    does not have yet has landed nothing, and a copy that lands INSIDE another copy of the
    same row (a lab's `solutions/`) is already listed by that one."""
    dests = [(d.semester_dest_repo, deploy_dest(d)) for d in deploys]

    def inside(repo: str, path: str) -> bool:
        return any(
            r == repo and p != path and (not p or path.startswith(f"{p}/"))
            for r, p in dests
        )

    out = []
    for deploy, (repo, path) in zip(deploys, dests, strict=True):
        if repo not in live_repos or inside(repo, path):
            continue
        landed = _landed(semester_org, deploy, allow, readings, aliases, hosted)
        if landed is not None:
            out.append(landed)
    return out


def _reading_list(semester_org: str, landed: list[_Landed]) -> str:
    """The prose a readings row inlines: the text of every overlay its copies landed,
    headings demoted to nest under the row's.

    Prose ONLY: every other file is already a download beside it. Reads the released
    SEMESTER copy, so a reading list appears on the same gate as every other material.
    `get_file_content` raises on anything but a 404 - a rate-limited read must not
    republish the row with the reading list silently emptied."""
    parts = []
    for item in landed:
        for path in item.overlays:
            text = (get_file_content(semester_org, item.repo, path) or "").strip()
            if text:
                parts.append(demote_headings(text))
    return "\n\n".join(parts)


def _indexable_repos(
    sched: schedule.Schedule, release_sources: list[tuple[str, str, str, int]]
) -> set[str]:
    """Which semester repos the site's rows, All Materials index and syllabus lookup are
    allowed to read.

    A POSITIVE allowlist, deliberately. `discover_semester_repos` works by exclusion - a repo
    is content unless it carries an infra topic - and that topic is written once, on
    creation, with its result ignored (`assign.py`), so a submission repo whose tag failed
    is content forever. That was survivable while a repo only reached the site by holding
    `NN_` session folders; the off-plan rows read whole trees, and the site repo they write
    into is PUBLIC, so the same slip would publish a student's private folder names.

    Two positive signals, both faculty declarations: a repo the release plan names as a
    destination, and a repo discovery actually found a released session in (which covers a
    manual release into a repo the plan never mentions). Non-ordinal material - a root
    `SYLLABUS.md`, a flat `datasets/` - is still read, because the signal is the REPO, not
    the folder shape inside it."""
    planned = {d.semester_dest_repo for r in sched.releases for d in r.deploy}
    return planned | {repo for repo, _sub, _folder, _n in release_sources}


def _section_boundary(repo: str, path: str) -> tuple[str, str]:
    """(section, the prefix of `path` that names it) for one released blob already known
    to hold a "/" - a root file is handled separately by the caller.

    The ordinal decides only the LEVEL a section is read at, never whether a file shows up:
    a repo whose top-level directories are session folders (`01_intro/...`) IS one section,
    the repo's own name; a repo holding `lectures/`, `labs/`, `datasets/` gives one section
    EACH, named after the top directory, which is stripped so its own children become the
    section's nodes."""
    head, _sep, _rest = path.partition("/")
    if session_number(head) is not None:
        return repo, ""
    return head, f"{head}/"


@dataclass
class _IndexEntry:
    """One node of the All Materials index - a file, or a directory nesting its own
    children to whatever depth the release actually has. `files` is 1 for a file and the
    total under a directory."""

    name: str
    is_dir: bool
    # A directory's link never has a hosted copy: what it opens is a GitHub listing.
    link: Link
    files: int = 0
    entries: dict[str, _IndexEntry] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """A directory keeps its trailing slash so it is obviously not a file."""
        return f"{self.name}/" if self.is_dir else self.name

    @property
    def children(self) -> list[_IndexEntry]:
        """This node's own entries, sorted for display."""
        return _sorted_entries(self.entries)


def _sorted_entries(entries: dict[str, _IndexEntry]) -> list[_IndexEntry]:
    """One level of the All Materials tree, directories before files, both alphabetically:
    this is a directory listing, where the structure is what a reader scans."""
    return sorted(entries.values(), key=lambda e: (not e.is_dir, e.name.lower()))


def _insert_released_path(
    root: dict[str, _IndexEntry],
    semester_org: str,
    repo: str,
    branch: str,
    full_path: str,
    prefix: str,
    hosted: Hosted,
) -> None:
    """Add one released blob into the nested tree rooted at `root`, creating every
    ancestor directory it needs and counting the file into each one's `files`. Nested as
    deep as the release is, unlike a row's links (`site_repo.shape_links`), which fold a
    subfolder into a count: this is the one page that shows the whole shape."""
    parts = full_path[len(prefix) :].split("/")
    node = root
    entry_path = prefix.rstrip("/")
    for i, part in enumerate(parts):
        is_dir = i < len(parts) - 1
        entry_path = f"{entry_path}/{part}" if entry_path else part
        entry = node.get(part)
        if entry is None:
            link = (
                Link(part, gh_url(semester_org, repo, branch, "tree", entry_path))
                if is_dir
                else file_link(semester_org, repo, branch, entry_path, part, hosted)
            )
            entry = node[part] = _IndexEntry(part, is_dir, link)
        entry.files += 1
        node = entry.entries


def _emit_entries(entries: list[_IndexEntry], indent_: str) -> list[str]:
    """YAML lines for one level of the All Materials tree, `indent_` growing with every
    level it recurses into."""
    lines: list[str] = []
    for e in entries:
        lines.append(f'{indent_}- name: "{q(e.label)}"')
        lines.append(f"{indent_}  url: {e.link.url}")
        # Written only where there is one, exactly as `links_block` writes it.
        if e.link.view_url:
            lines.append(f"{indent_}  view_url: {e.link.view_url}")
        if e.is_dir:
            lines.append(f"{indent_}  files: {e.files}")
            lines.append(f"{indent_}  entries:")
            lines.extend(_emit_entries(e.children, indent_ + "    "))
    return lines


def _materials_index(
    semester_org: str,
    content_repos: list[str],
    hosted: Hosted,
    syllabus: Link | None = None,
) -> str:
    """`_data/materials.yml` - the syllabus the home page pins, and every file released to
    this semester, nested exactly as its repo has it, for the All Materials tab.

    The catch-all. Every other page is curated: a row exists because the schedule named
    it. This is the complete index, so it answers a student's "what do I have?" and a
    teaching team's "did my file actually ship?" - including material no row covers.

    Filtered by `materials.publishable`: a released `solution/`, `grading_config.yml` or
    hidden `tests/` is not course material, and this index lists everything a release
    happened to carry; the never-material names (`.gitkeep`) go with them.

    Root files come out separately as `documents:`, deduped by NAME: a course-level
    document released into three content repos is one document, not three sections.

    `repos:` names `content_repos` themselves, in order: Your Profile forks and clones the
    first, and `open_in.html` offers `online` / `local` for that repo's files."""
    found: dict[str, dict[str, _IndexEntry]] = {}
    docs: dict[str, _IndexEntry] = {}
    for repo in sorted(content_repos):
        branch, paths = _repo_tree(semester_org, repo)
        for path in paths:
            if not publishable(path):
                continue
            if "/" not in path:
                doc = file_link(semester_org, repo, branch, path, path, hosted)
                docs.setdefault(path, _IndexEntry(path, False, doc, files=1))
                continue
            section, prefix = _section_boundary(repo, path)
            _insert_released_path(
                found.setdefault(section, {}),
                semester_org,
                repo,
                branch,
                path,
                prefix,
                hosted,
            )
    rows_out: list[str] = []
    for section in sorted(found):
        entries = _sorted_entries(found[section])
        rows_out.append(f'  - name: "{q(section)}"')
        rows_out.append(f"    files: {sum(e.files for e in entries)}")
        rows_out.append("    entries:")
        rows_out.extend(_emit_entries(entries, "      "))
    doc_rows = _emit_entries(sorted(docs.values(), key=lambda e: e.name.lower()), "  ")
    header = (
        "# Generated by `python3 -m dsl_course.site sync` - the syllabus and every released\n"
        "# file, nested as its repo has it. Edit nothing here; it is rewritten on every sync.\n"
    ) + (
        # ONE key: the home page pins a single link, so what it opens is the hosted copy
        # where there is one and the GitHub blob otherwise.
        f"syllabus: {syllabus.view_url or syllabus.url}\n" if syllabus else ""
    )
    repos = ", ".join(f'"{q(r)}"' for r in sorted(content_repos))
    header += f"repos: [{repos}]\n"
    body = "documents:\n" + "\n".join(doc_rows) + "\n" if doc_rows else ""
    body += "sections:\n" + "\n".join(rows_out) if rows_out else "sections: []"
    return header + body + "\n"


def _dest_link(semester_org: str, dest: str, live_repos: frozenset[str]) -> str:
    """A planned destination (`repo/path`) as markdown - a LINK when there is something to
    link to, plain code when there is not.

    The path itself does not exist yet, by definition: that is what "not released" means, so
    linking it would hand a student a 404. What can exist is the destination repo, and once
    it does, its tree is already in hand - so the link points at the deepest ancestor of the
    path that is really there. `live_repos` is the semester's existing repos, already
    discovered by the caller, so knowing this costs no extra API call."""
    repo, _, path = dest.partition("/")
    if repo not in live_repos:
        return f"`{dest}`"
    branch, blobs = _repo_tree(semester_org, repo)
    here = ""
    for part in path.split("/"):
        candidate = f"{here}/{part}" if here else part
        if not any(b == candidate or b.startswith(f"{candidate}/") for b in blobs):
            break
        here = candidate
    return f"[`{dest}`]({gh_url(semester_org, repo, branch, 'tree', here) if here else f'https://github.com/{semester_org}/{repo}'})"


def _details(text: str) -> str:
    """A row's `details:` front matter - the faculty prose that fills the schedule table's
    Details column, and, on a session row, the learning objectives its tab page repeats.

    ONE key, on every row type, feeding one column. It was `description:`, which meant the
    session blurb on a lecture row and the row's NAME on an exam, a special event, a term
    boundary and an assignment's due row - so the same word named two columns and the
    theme had to know which kind of row it was reading to know which. `title` is now the
    Title cell everywhere and this is the Details cell everywhere.

    A block scalar once it has a newline in it. The Hertie syllabus format writes these as
    a paragraph (sometimes two), and `q` folds every newline away, so a one-line scalar
    silently ran two paragraphs together. Empty stays absent rather than blank, so the
    theme can test for it.

    Always at column zero. A caller nesting it under a parent key - the `due_event:`
    sub-hash is the only one - shifts the whole thing with `textwrap.indent`, which is a
    uniform shift and so keeps both the block indicator and the quoted scalar valid."""
    if not text.strip():
        return ""
    if "\n" in text.strip():
        return block("details", text)
    return f'details: "{q(text)}"\n'


# The section label an attached readings entry's links are filed under, so the Readings
# tab can pick them off a lecture's row whatever the folder is called.
READINGS_LINKS = "readings"


@dataclass
class _Row:
    """Everything one `_lectures` file says, gathered before it is written."""

    kind: str
    subtitle: str = ""
    details: str = ""
    when: date | datetime | None = None  # None: an off-plan folder, on its tab only
    number: int | None = None
    tbc: bool = False
    off_schedule: bool = False
    landed: list[_Landed] = field(default_factory=list)
    readings: list[_Landed] = field(default_factory=list)
    readings_pending: bool = False
    dests: list[str] = field(default_factory=list)
    order: str = ""  # an off-plan row's place on its tab

    @property
    def tabs(self) -> list[str]:
        """The kind tabs that list this row (`site_repo.row_tabs`)."""
        return row_tabs(self.kind, bool(self.readings or self.readings_pending))


def _row_entry(
    semester_org: str, row: _Row, live_repos: frozenset[str] = frozenset()
) -> str:
    """One `_lectures` row, of any kind.

    `title` is the kind's label and the row's number ("Lecture 3", "Lab 9"; the label
    alone when unnumbered); `subtitle` and `details` are the entry's `title:` and
    `details:`, omitted when empty. `kind` names it. `tabs` are the kind tabs that list it: a lecture
    carrying its week's readings is on the Readings tab too, under the same name.

    Nothing landed (its own copies or its readings) is the not-yet-released row:
    `unreleased: true` and a line naming where the copies will land. `readings_pending`:
    readings attached to it that have not landed yet. `off_schedule`: a `show_on_site: false`
    row, left off the schedule and the Updates box. `undated`: an off-plan folder."""
    label = policy.kind_label(row.kind)
    title = f"{label} {row.number}" if row.number is not None else label
    # `row_name`, so an entry that declares `title: Lab 1` renders "Lab 1" and not
    # "Lab 1 / Lab 1" - faculty repeat the identifier as readily in the plan as in a README.
    subtitle, details = row_name(row.subtitle, title), row.details
    own = row.landed if row.kind == "readings" else []
    reading_list = _reading_list(semester_org, [*own, *row.readings])
    links = links_block(
        [(item.section, item.links) for item in row.landed]
        + [(READINGS_LINKS, item.links) for item in row.readings]
    )
    flags, body = "", ""
    if not row.landed and not row.readings:
        flags = "unreleased: true\n"
        where = ", ".join(_dest_link(semester_org, d, live_repos) for d in row.dests)
        # Italic, with the lead in bold: this is the one line on an unreleased row.
        # `.session-note` sets the gap above it (dsl-jekyll-theme's _layout.scss).
        body = (
            f"_**Materials for {title.lower()} are not yet released**"
            + (f" - they will appear in {where} when they are" if where else "")
            + "._"
        )
    # The plan's own `tbc:` - the DATE is provisional. Display-only: every copy on this
    # row still fires exactly when its entry says.
    if row.tbc:
        flags += "tbc: true\n"
    if row.off_schedule:
        flags += "off_schedule: true\n"
    if row.readings_pending:
        flags += "readings_pending: true\n"
    if row.when is None:
        flags += f'undated: true\norder: "{q(row.order)}"\n'
    return (
        f"---\n"
        f"kind: {row.kind}\n"
        + (f"number: {row.number}\n" if row.number is not None else "")
        + (f"date: {iso_when(row.when)}\n" if row.when is not None else "")
        + f'title: "{q(title)}"\n'
        + (f'subtitle: "{q(subtitle)}"\n' if subtitle else "")
        + _details(details)
        + f"tabs: [{', '.join(row.tabs)}]\n"
        + flags
        + (block("reading_list", reading_list) if reading_list else "")
        + f"{links}\n"
        f"---\n"
        f"{body}\n"
    )


def _row_filename(row: _Row, key: str, taken: dict[str, str]) -> str:
    """The row's file: `row_file` when numbered (`session-03.md`, `lab-09.md`), else
    named for its entry or folder; a number two entries share gets the second's key."""
    prefix = row_file(1, row.kind).rsplit("-", 1)[0]
    name = (
        row_file(row.number, row.kind)
        if row.number is not None
        else f"{prefix}-{slug(key)}.md"
    )
    if name in taken:
        name = f"{name[:-3]}-{slug(key)}.md"
    return name


def _offplan_rows(
    semester_org: str,
    rows: list[PlannedRow],
    allow: frozenset[str],
    live_repos: frozenset[str],
    aliases: Aliases,
    hosted: Hosted,
) -> list[tuple[str, _Row]]:
    """`(key, row)` for each off-plan folder (`schedule_plan.offplan_folders`; decision
    0013: it keeps a row, on its kind's tab): unnumbered, undated, named for its folder. A
    readings folder joins the lecture folder of the same `NN_` ordinal, as such folders
    always did; a supporting-files folder is no row (decision 0026); the rest are rows of
    their own."""

    def land(repo: str, folder: str, readings: bool) -> list[_Landed]:
        deploy = schedule.Deploy("", folder, repo)
        return _row_landed(
            semester_org, (deploy,), allow, live_repos, readings, aliases, hosted
        )

    folders = offplan_folders(
        (d for r in rows for d in r.deploys),
        {repo: _repo_tree(semester_org, repo)[1] for repo in live_repos},
    )
    lectures = {
        session_number(f.rsplit("/", 1)[-1]): (repo, f)
        for repo, f, kind in folders
        if kind == "lecture" and session_number(f.rsplit("/", 1)[-1]) is not None
    }
    joined: dict[tuple[str, str], list[_Landed]] = {}
    out = []
    for repo, folder, kind in folders:
        if kind == ASSETS_KIND:
            continue
        n = session_number(folder.rsplit("/", 1)[-1])
        if kind == "readings" and n in lectures:
            joined.setdefault(lectures[n], []).extend(land(repo, folder, True))
            continue
        name = re.sub(r"^0*\d+_", "", folder.rsplit("/", 1)[-1])
        row = _Row(
            kind,
            subtitle=name.replace("-", " ").replace("_", " ").strip().capitalize(),
            landed=land(repo, folder, kind == "readings"),
            order=f"{repo}/{folder}",
        )
        out.append(((repo, folder), row))
    for key, row in out:
        row.readings = joined.get(key, [])
    return [(folder.rsplit("/", 1)[-1], row) for (_repo, folder), row in out]


def _site_rows(
    semester_org: str,
    rows: list[PlannedRow],
    allow: frozenset[str],
    live_repos: frozenset[str],
    aliases: Aliases,
    hosted: Hosted,
) -> tuple[dict[str, str], list[str]]:
    """The `_lectures` collection - one file per site row (`schedule_plan.site_rows`),
    plus the off-plan folders' rows - and the kind tabs it needs.

    Every row the schedule shows is written, released or not, so the whole term reads as
    a syllabus from the day it is written. A silent readings row is written only once
    something has landed, and never when all it landed is root files."""
    built: list[tuple[str, _Row]] = []
    for sr in site_rows(rows):
        r = sr.row
        landed = _row_landed(
            semester_org,
            r.deploys,
            allow,
            live_repos,
            r.kind == "readings",
            aliases,
            hosted,
        )
        if not r.shown and all(item.root_file for item in landed):
            continue
        readings = []
        pending = False
        for attached in sr.readings:
            got = _row_landed(
                semester_org, attached.deploys, allow, live_repos, True, aliases, hosted
            )
            readings += got
            pending = pending or not got
        row = _Row(
            r.kind,
            subtitle=r.subtitle,
            details=r.details,
            when=r.when,
            number=sr.number,
            tbc=r.tbc,
            off_schedule=not r.shown,
            landed=landed,
            readings=readings,
            readings_pending=pending,
            dests=r.dests,
        )
        built.append((r.key, row))
    built += _offplan_rows(semester_org, rows, allow, live_repos, aliases, hosted)
    out: dict[str, str] = {}
    tabs: dict[str, None] = {}
    for key, row in built:
        out[_row_filename(row, key, out)] = _row_entry(semester_org, row, live_repos)
        tabs |= dict.fromkeys(row.tabs)
    return out, list(tabs)


def _declared_syllabus(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    live_repos: frozenset[str],
    hosted: Hosted,
) -> Link | None:
    """The syllabus released to this semester (`schedule_plan.declared_syllabus`) as the
    home page's link, or None - the home page then shows no line. A syllabus the course
    publishes carries its hosted copy (`view_url`), which is what the pin opens."""
    found = declared_syllabus(
        sched,
        live_repos,
        lambda repo: _repo_tree(semester_org, repo)[1],
        lambda repo: read_materials(course_org, repo),
    )
    if found is None:
        return None
    repo, path = found
    branch, _blobs = _repo_tree(semester_org, repo)
    return file_link(semester_org, repo, branch, path, path.rsplit("/", 1)[-1], hosted)


def member_digest(semester_org: str, handle: str) -> str:
    """A team member as the public semester site carries them: SHA-256 of
    `<semester org>:<handle, lower-cased>`, hex. Never the handle itself - the page's
    script hashes its reader's saved handle the same way (_layouts/assignment.html,
    `memberKey`) to recognise their team, and nothing else can read one back. Salted with
    the org so one student's digest differs from semester to semester. `.lower()`, not
    `.casefold()`, because the browser side is `toLowerCase` and GitHub handles are ASCII."""
    return hashlib.sha256(f"{semester_org}:{handle.lower()}".encode()).hexdigest()


def _formed_teams(semester_org: str, key: str) -> list[tuple[str, list[str]]]:
    """`(team, member handles)` for every team formed for `key` so far, by name - the
    same reader (`teams.teams_for`) the student console's team list uses.

    Never fatal: teams.csv is student-written, and a row somebody broke must not take down
    the render of a semester's whole website. The callout still goes out; only the table
    is missing."""
    try:
        groups = teams.teams_for(teams.load(semester_org), key)
    except RuntimeError as exc:
        log_err(f"could not read {semester_org}'s teams for {key}: {exc}")
        return []
    return sorted((team, sorted(members)) for team, members in groups.items())


def _assignment_entry(
    course_org: str,
    semester_org: str,
    repo: str,
    when: date | datetime,
    handout: datetime | None = None,
    found: tuple[str, schedule.AssignmentEntry] | None = None,
    handed_out: frozenset[str] = frozenset(),
    now: datetime | None = None,
    sched: schedule.Schedule | None = None,
) -> str:
    """An assignment's page (`_layouts/assignment.html`), plus the two schedule rows it
    drives: the entry's own `date:` is the hand-out ("Assignment out") row and its
    `due_event:` sub-block the due row.

    The page's rule sentences are the same helpers the student console's assignment
    reads (`student_status.render_assignments`): `course.late_rule`, `CUTOFF_SENTENCE`,
    `course.shape_note`, `grades.total_points`, `grades.team_cap`, `teams.teams_for`.

    `when` is the due date (a real one from schedule.yml, or a synthesised fallback);
    `handout` the scheduled provisioning moment when there is one. A handout dates the
    released-row where it belongs - at hand-out, not at the deadline - while an
    unscheduled assignment keeps both rows on the due date (the only date known).

    `found` is this assignment's `(slug, entry)` from the plan, already resolved by the
    caller (two entries may cite one template, so a lookup here by repo would merge them),
    or None for one the plan does not name. It supplies the semester-side name exactly as
    assign.py / collect.py resolve it.

    An assignment NOT YET HANDED OUT is flagged `handout_pending: true` and its body says
    so: the plan is public from the day it is written, the payload arrives on hand-out, so
    its name comes from the template's `title:` alone until then (its README heading is
    the brief's, and waits). Handed out means `handed_out` holds its semester-side name (the
    frozen semester template exists) or its `handout` has passed.

    While a self-select group assignment's team-formation window is open
    (`schedule.formation_state`, the same answer the Join-team form's lock reads), the
    entry carries `team_join_url` / `team_join_cap` / `team_join_closes` / `team_salt`,
    and `teams:` once any has formed: each team's name, headcount, cap, its members as
    salted digests (`member_digest`, never a handle) and its repo's URL."""
    slug = schedule.semester_name(*found) if found else repo
    # An unscheduled assignment's synthesised fallback date is due end-of-day.
    due = iso_when(when, "23:59:00")
    released = iso_when(handout) if handout is not None else due
    pinned_out = handout is not None and handout <= (
        now or datetime.now(handout.tzinfo)
    )
    out = slug in handed_out or pinned_out
    spec = load_grading_spec(
        course_org,
        repo,
        semester_org=semester_org if found else "",
        slug=found[0] if found else "",
    )
    # The slug's own name is the row's IDENTIFIER, and it does not change at hand-out;
    # the template's `title:`, else (once out) its README heading, is its NAME.
    title = identifier(slug)
    subtitle = spec.title
    details = found[1].details if found else ""
    # Display-only: a deadline that says "(TBC)" still closes when it says.
    tbc_fm = "tbc: true\n" if found and found[1].tbc else ""
    tbc_due = indent(tbc_fm, "    ")
    external = spec.submit_external
    window, shuts = (
        schedule.formation_state(sched, found[0], now or datetime.now(UTC))
        if sched is not None and found is not None
        else ("closed", None)
    )
    forming = window == "open" and spec.team_formation_resolved == SELF_SELECT
    # Where the work goes, on the due row: the SHAPE in one word, and the address once
    # there is something at the other end of it.
    repo_lines = [f'submit_shape: "{spec.submit_shape}"']
    whose = "<your-team>" if spec.is_group else "<your-handle>"
    repo_name = ""
    if external:
        if out and spec.submit_url:
            repo_lines.append(f'submit_url: "{q(spec.submit_url)}"')
            repo_lines.append(f'submit_host: "{q(spec.submit_host)}"')
    elif spec.submit_shared:
        repo_name = shared_repo(slug)
        repo_lines.append(f'submit_path: "{whose}/"')
        if out:
            repo_lines.append(
                f'repo_url: "https://github.com/{semester_org}/{q(repo_name)}"'
            )
        repo_lines.append(f'repo_name: "{q(repo_name)}"')
    else:
        repo_name = submission_repo(slug, whose)
        if out:
            repo_lines.append(
                f'repo_url: "https://github.com/orgs/{semester_org}/repositories?q={slug}-"'
            )
        repo_lines.append(f'repo_name: "{q(repo_name)}"')
        # `repo_name` is a SHAPE to substitute a handle into (open_in.html), not a real
        # name - the drop box above is named exactly and never gets this flag.
        repo_lines.append("repo_name_is_shape: true")
    # The page's rule sentences: when the work is read, and what the deadline costs. Only
    # for a shape that collects commits - `external` pins no commit and counts no day.
    cutoff_fm = (
        f'cutoff_sentence: "{q(CUTOFF_SENTENCE)}"\n' if spec.collects_commits else ""
    )
    late_fm = (
        f'late_rule: "{q(late_rule(spec.late_window_days, spec.late_penalty_per_day))}"\n'
        if spec.collects_commits
        else ""
    )
    points = total_points(spec)
    points_fm = f'max_points: "{points}"\n' if points else ""
    # Who can read the repo, under the brief - only once the brief is out.
    note = shape_note(spec.submit_shape) if out else ""
    note_fm = f'shape_note: "{q(note)}"\n' if note else ""
    team_fm = ""
    if forming and shuts is not None:
        cap = team_cap(course_org, spec, semester_org, found[0])

        def team_entry(name: str, handles: list[str]) -> str:
            if external:
                url = ""
            elif spec.submit_shared:
                url = f"https://github.com/{semester_org}/{q(shared_repo(slug))}"
            else:
                url = f"https://github.com/{semester_org}/{q(submission_repo(slug, name))}"
            digests = ", ".join(f'"{member_digest(semester_org, h)}"' for h in handles)
            return (
                f'  - name: "{q(name)}"\n    members: {len(handles)}\n    cap: {cap}\n'
                f"    members_sha256: [{digests}]\n"
                + (f'    repo_url: "{url}"\n' if url else "")
            )

        listed = "".join(
            team_entry(name, handles)
            for name, handles in _formed_teams(semester_org, found[0])
        )
        closes = spoken_day(schedule.in_semester_zone(sched, shuts))
        team_fm = (
            f'team_join_url: "{join_issue_url(semester_org)}"\n'
            f'team_join_cap: "{cap}"\n'
            f'team_join_closes: "{closes}"\n'
            f'team_salt: "{q(semester_org)}"\n'
            + (f"teams:\n{listed}" if listed else "")
        )
    # Written at BOTH levels: the due row is a sub-hash the theme reaches through
    # `map: "due_event"`, so it cannot see its parent's fields.
    repo_fm = "".join(f"{ln}\n" for ln in repo_lines)
    repo_due = "".join(f"    {ln}\n" for ln in repo_lines)
    if out:
        readme = get_file_content(course_org, repo, "README.md") or ""
        if not subtitle:
            heading = next(
                (ln[2:] for ln in readme.splitlines() if ln.startswith("# ")), ""
            )
            subtitle = row_name(heading, title)
        brief = "\n".join(
            ln for ln in readme.splitlines() if not ln.startswith("# ")
        ).strip()
        flags = ""
        # The page's body is the brief, and nothing else.
        body = liquid_raw(brief or "Assignment brief.")
    else:
        flags = "handout_pending: true\n"
        # Word for word the shape of an unreleased session's line (`_row_entry`).
        born = "private" if spec.visibility_is_students else spec.visibility
        if external:
            coming = "the brief appears here when it is"
        elif spec.submit_shared:
            coming = f"the `{repo_name}` drop box appears when it is"
        else:
            coming = f"your {born} `{repo_name}` repo appears when it is"
        body = f"_**{title} is not yet released** - {coming}._"
    sub_fm = f'subtitle: "{q(subtitle)}"\n' if subtitle else ""
    sub_due = f'    subtitle: "{q(subtitle)}"\n' if subtitle else ""
    # Both rows carry the plan's `details:`: the due row is a sub-hash the theme reaches
    # through `map: "due_event"`, so it cannot see its parent's.
    details_fm = _details(details)
    details_due = indent(details_fm, "    ")
    return (
        f"---\n"
        f"kind: assignment\n"
        f"date: {released}\n"
        f'title: "{q(title)}"\n'
        f"{sub_fm}"
        f"{details_fm}"
        f"{tbc_fm}"
        f"{flags}"
        f"{repo_fm}"
        f"{cutoff_fm}"
        f"{late_fm}"
        f"{points_fm}"
        f"{note_fm}"
        f"{team_fm}"
        f"due_event:\n"
        f"    kind: due\n"
        f"    date: {due}\n"
        f'    title: "{q(title)}"\n'
        f"{sub_due}"
        f"{details_due}"
        f"{tbc_due}"
        f"{repo_due}"
        f"---\n"
        f"{body}\n"
    )


def _assignment_dates(
    found: tuple[str, schedule.AssignmentEntry] | None, fallback: date
) -> tuple[date | datetime, datetime | None]:
    """(due, handout) for an assignment, off the plan entry the caller resolved. An
    assignment the plan does not name is due on `fallback` and has no handout; a scheduled
    one has a handout only when the plan pins (or the manual release workflow recorded)
    one."""
    if found is None:
        return fallback, None
    return found[1].due_datetime, found[1].handout_datetime


def _pretty(label: str) -> str:
    """A schedule label as a display name, for an entry that declared no title."""
    return label.replace("-", " ").replace("_", " ").title()


def _event_row(
    kind: str,
    title: str,
    when: date | datetime,
    tbc: bool = False,
    dateless: bool = False,
    details: str = "",
) -> str:
    """A display-only schedule row - `kind='exam'` for the red exam row the theme styles
    (schedule_row_exam.html), `kind='special_event'` for the generic one
    (schedule_row_special_event.html): a clinic, a guest lecture, a review session.
    Nothing is released; the site simply shows it. ONE renderer, because the two rows
    differ in the word and in nothing else - the templates differ, the front matter does
    not, and two copies of it is how a key added to one row type misses the other.

    `when` is a datetime when schedule.yml gave the entry a real start time, or a bare
    date (a whole-day entry, or the synthesised mid/end-of-semester exam) - which keeps
    the 09:00 placeholder.

    The name goes in `title` and the prose in `details`, which are the schedule's Title
    and Details columns. Both used to go through `description`, which meant the row's NAME
    here and a session's blurb on a lecture row - so one word named two columns and
    whoever read it had to know which kind of row it was on to know which. One meaning per
    column, and one word per meaning: Event says what kind of row this is, Title which one
    it is, Details what there is to say about it. An exam's Details cell used to be the
    fixed sentence "Details to be confirmed.", written into every exam of every semester
    whether or not anything was outstanding - a toolkit opinion about a room and a format
    it knows nothing about, and one nobody could delete by editing their schedule. There
    is no default now: a row says what its `details:` says, or nothing.

    TBC: an undated entry (`event_datetime: tbc`) still needs a sortable `date:` for the
    theme, so the caller passes end-of-term as `when` plus `dateless=True` - the theme
    then prints "TBC" instead of the placeholder. A dated entry with `tbc=True` keeps its
    date and gains a "(TBC)" marker."""
    flags = ""
    if tbc or dateless:
        flags = "tbc: true\n" + ("dateless: true\n" if dateless else "")
    return (
        f"---\n"
        f"kind: {kind}\n"
        f"date: {iso_when(when)}\n"
        f"{flags}"
        f'title: "{q(title)}"\n'
        f"{_details(details)}"
        f"---\n"
    )


def _event_entry(event: schedule.Event, fallback: date) -> str:
    """One `events:` row, rendered as the type it declared - an exam or a special event.
    An event with no title of its own falls back to its prettified label, its `details:`
    fills the row's Details cell, and an undated one (`event_datetime: tbc`) sorts at
    `fallback` (end of term) as a dateless row.

    `show_on_site: false` is the caller's business, not this function's: an event hidden
    from the schedule is one this is never called for."""
    return _event_row(
        "exam" if event.kind == "exam" else "special_event",
        event.title or _pretty(event.label),
        event.when if event.when is not None else fallback,
        event.tbc,
        event.when is None,
        event.details,
    )


def _archive_entry(archive: schedule.ArchiveRow, today: date) -> str:
    """The archive row: when this semester is frozen read-only.

    Takes the parsed block whole (`schedule.ArchiveRow`) rather than five of its fields,
    for the same reason `_row_entry` takes its `PlannedRow`: the block's keys are the
    row's keys, and re-declaring them here is a second place for the next one to be
    added - and a second place for a default to be written. `archive.when` is a date by
    the time this is called; the caller does not render a block nothing can date.

    A `special_event` rather than a type of its own, because it IS one - a dated thing
    that happens to the semester and releases nothing - and the theme already colours that
    row. Inventing a fourth row type would mean shipping a theme change for one line.

    `archive.title` ("Semester archived" unless the semester renames it) fills the row's
    Title cell like every other row's.

    `archive.details` is the WHOLE of what either
    surface says - there is no default sentence, because the toolkit does not know what a
    freeze means for a given semester's students and a wrong reassurance is worse than none.
    Without one the row still renders as its title and date, and is not announced at all
    (below). The seeded skeleton carries a sentence ready to uncomment.

    It goes in `details:` like every other row's. It used to be written as the page BODY
    instead, because the Updates box captured its bullet out of an entry's `content` - and
    the cost was that this one sentence had to be folded onto a single line and fenced,
    making it the only `details:` in the vocabulary that could not run to a paragraph.
    `announcements.html` reads `details` first and falls back to `content`, so the box
    keeps its bullet and the exception goes.

    `archive.tbc` marks a provisional freeze date, exactly as it does on an event: the
    date the scheduler acts on is `archive.when` either way.

    The sentence naturally names the day, and a day typed into it twice goes stale the
    moment `archive.when` moves or is left to its default - so `{date}` in it is filled
    in here with the date this row itself carries, spelled the way the row dates the
    freeze (`YYYY-MM-DD`; the front matter adds only the placeholder clock time that
    `hide_time` suppresses). A plain replacement rather than `str.format`, because this is
    faculty prose: any other brace in it is left exactly as typed, and front matter is
    data rather than a Liquid template, so no brace in it can run.

    `announce` opts the row into the home page's Updates box for the last
    `schedule.ARCHIVE_NOTICE` before the date, and the sentence is what that box prints -
    so it is written only when there IS one, and only while the freeze is still ahead. The
    box captures each bullet INSIDE its `limit: 7` loop and drops an empty one afterwards
    (`templates/site/_includes/announcements.html`), so a row announced with nothing to
    say spent the newest of seven slots on nothing - and, sorting by its own future date,
    the top one - for the whole fortnight. Past the date there is nothing to announce
    either: the freeze has happened. It is a flag rather than a rendering decision because
    the collection is cleared and rewritten on every sync, so once the window closes the
    flag is simply not written again - there is nothing to take back."""
    when = archive.when
    dated = (
        archive.details.replace("{date}", when.isoformat()) if archive.details else ""
    )
    flags = "hide_time: true\n" + ("tbc: true\n" if archive.tbc else "")
    if dated and today <= when and when - today <= schedule.ARCHIVE_NOTICE:
        flags += "announce: true\n"
    return (
        f"---\n"
        f"kind: special_event\n"
        f"date: {iso_when(when)}\n"
        f"{flags}"
        f'title: "{q(archive.title)}"\n'
        f"{_details(dated)}"
        f"---\n"
    )


def _term_date_entry(name: str, when: date) -> str:
    """A semester-boundary row (the theme's schedule_row_term_date.html).

    `name` ("Semester starts" / "Semester ends") is the row's TITLE. It used to be written to
    `name:`, which the theme prints in the Event column, beside a `description: ""` that
    left the Title cell permanently blank - so the one row type on the schedule read its
    name out of a different column from every other. The Event column now says "Semester",
    which is the kind, and this says which one. `hide_time` suppresses the placeholder
    clock time - a term boundary is a whole day, not a 09:00 appointment."""
    return (
        f"---\n"
        f"kind: term_date\n"
        f"date: {iso_when(when)}\n"
        f"hide_time: true\n"
        f'title: "{q(name)}"\n'
        f"---\n"
    )


def sync_site(course_org: str, semester_org: str) -> int:
    """Regenerate the semester's student-facing site from the live org state: the term's
    rows by kind (released ones linked into the private content repos, planned ones marked
    not-yet-released), the assignment pages with their hand-out and due rows, the
    display-only rows of the schedule (exams, special events and term dates), the All
    Materials index, the hosted copies the course publishes, the home text and the
    announcements, under a banner to the student console (decision 0035 rule 1)."""

    def build(site_wd: Path) -> SitePlan:
        # ONE listing of the semester answers both questions this build asks of it: which
        # repos hold released content, and which assignments have actually gone out.
        # Taken here rather than memoised in `discovery`, because a memo would serve a
        # listing from before assign.py created the template it is syncing the site for.
        semester_repos = list_org_repos(semester_org)
        content_repos = semester_content_repos(semester_repos)
        release_sources = discover_release_sources(semester_org, content_repos)
        # Which of them this semester has actually been given - what gates their briefs. Read
        # from the semester org rather than inferred from the plan, since the manual workflow
        # hands out with no `handout_datetime` pinned at all.
        handed_out = handed_out_assignments(semester_repos)

        # Course identity comes from the course org metadata, semester from the semester tag.
        meta = yaml_file(course_org, ".github", "dsl-course.yml")
        # Schedule is semester-specific (it varies by year), so it comes from the semester's
        # own semester-config/schedule.yml. So do this semester's instructors/TAs - read
        # from its own semester-config/instructors.yml below, NOT the course org (whose
        # dsl-course.yml carries only the multi-year instructor cards).
        sched = schedule.load(semester_org)
        # Every datetime on `sched` is already the semester's wall clock (the parser converts
        # a written offset into the semester timezone), so the renderers below just print it.
        start = sched.semester_start or _semester_start(semester_org)
        # Every assignment of the plan, numbered as every link to one numbers it
        # (`schedule.assignment_pages`, hidden ones included).
        pages = schedule.assignment_pages(sched)

        def shown(hit: tuple[str, schedule.AssignmentEntry] | None) -> bool:
            """Does this assignment appear on the site at all?

            `show_on_site: false` drops it from the SITE and from nothing else: it is
            handed out, snapshotted and graded exactly as written. Both its schedule rows
            go with it (the due row is a sub-hash of its page), as does its Assignments-tab
            entry - the same all-or-nothing the site has for a silent release, because the
            theme has no way to render one of an entry's rows and not the other.

            One discovered from the course org but hidden in the plan is hidden: the plan
            is where faculty say what the site shows. One the plan never mentions has
            nowhere to say otherwise, so it shows."""
            return hit is None or hit[1].show_on_site

        # What a row LINKS, out of everything it released - the default
        # folder-shaped listing unless this course declared an extension allowlist.
        allow = link_extensions(meta)
        # The repos this semester actually releases into - the only ones the index and
        # the syllabus lookup may read (see `_indexable_repos`).
        indexable = sorted(
            set(content_repos) & _indexable_repos(sched, release_sources)
        )
        # The rows: one per `releases:` entry, in date order, kind declared or inferred
        # once from where its first copy lands (the source repo's `materials.yml` aliases,
        # else the built-in ones).
        # An off-plan folder's copy names no source repo, and so no aliases.
        kinds = lambda repo: read_materials(course_org, repo).kinds if repo else None
        planned = planned_rows(sched, kinds)
        # The repos the release plan names or a released session was found in: never a
        # student's repo, whose folder names would reach this public site.
        live = frozenset(indexable)
        # What this course declares PUBLIC, per release destination (`publish.yml` in the
        # source repo the plan names). Copied before a single row is rendered, so every
        # page that links a hosted copy links one that exists.
        # `opencourse.yml`'s `withhold` is a deny filter here too: what the course keeps
        # off its open-courseware site is never hosted publicly on this one. Read only
        # when something is declared public, and a file that cannot be read stops the
        # copying (the last sync's copies stand, linked from nothing), never the sync -
        # nor the deletions: an unpublished file still leaves `files/`.
        policies = _publish_policies(course_org, sched, content_repos)
        hosted: Hosted = {}
        if any(policies.values()):
            try:
                opencourse = read_opencourse(course_org)
            except RuntimeError as exc:
                log_err(
                    f"{course_org}/.github/opencourse.yml could not be read, so no copy "
                    f"is hosted on this sync: {exc}"
                )
                _mirror_public(site_wd, semester_org, policies, None)
            else:
                hosted = _mirror_public(
                    site_wd,
                    semester_org,
                    policies,
                    opencourse.withhold if opencourse else (),
                    _deploy_sources(sched),
                )
        else:
            hosted = _mirror_public(site_wd, semester_org, policies)
        rows, present = _site_rows(semester_org, planned, allow, live, kinds, hosted)
        log_step(
            f"Syncing {semester_org}/{pages_repo(semester_org)}: {len(rows)} row(s) "
            f"({sum('unreleased: true' in text for text in rows.values())} not released "
            f"yet), {sum(1 for page in pages if shown(page.hit))} assignment(s)"
        )

        config = {}
        if meta.get("course_name"):
            config["course_name"] = str(meta["course_name"])
        if _semester_label(semester_org):
            config["course_semester"] = _semester_label(semester_org)
        if meta.get("course_code"):
            config["course_code"] = str(meta["course_code"])
        # The site's blurb. Declared once in the course org's dsl-course.yml and pushed to
        # every semester site; left as the site repo has it when the course doesn't declare
        # one. Written as a single line whatever the source shape (see q).
        if meta.get("course_description"):
            config["course_description"] = str(meta["course_description"])
        # The footer's GitHub link (the site's only click-back). This is the SEMESTER site,
        # so it links the semester org - where this year's materials and the students' own
        # repos live - never the course org (faculty-side) or the template's default.
        config["github_org"] = semester_org

        # The display-only half of the schedule. `events:` rows render as what they
        # declared (exam or special event); an undated (TBC) one sorts at end-of-term.
        end = sched.semester_end or start + timedelta(weeks=15)
        # Enumerated over EVERY event and filtered after, not before: the ordinal is the
        # entry's position in the plan, so hiding one leaves the rest of the term's event
        # pages at the filenames - and so the URLs - they already have.
        event_entries = {
            f"{i + 1:02d}-{slug(e.label)}.md": _event_entry(e, end)
            for i, e in enumerate(sched.events)
            if e.show_on_site
        }
        # Every course has exams, so a schedule that names none still gets stub mid/end
        # dates of a ~15-week semester (bounded by semester_end when set) - a placeholder
        # faculty replace, rather than a schedule page with no exams on it at all.
        #
        # Counted over EVERY event, hidden ones included: a semester that wrote its exams and
        # then took them off the site has said what its exams are, and answering that with
        # two invented ones would put back exactly what it asked to remove.
        if not any(e.kind == "exam" for e in sched.events):
            event_entries |= {
                "midterm.md": _event_row(
                    "exam", "MidTerm Exam", start + timedelta(weeks=8)
                ),
                "final.md": _event_row("exam", "Final Exam", end),
            }
        # When the whole semester is frozen read-only. Off the same date the scheduler
        # acts on, so what students are told and what happens are one date.
        if sched.archive and sched.archive.when and sched.archive.show_on_site:
            event_entries["semester-archived.md"] = _archive_entry(
                sched.archive, date.today()
            )
        # "Marks expected": an assignment's `marks_return_datetime`, shown only where its
        # entry says `show_on_site: true` in so many words - the date is internal by
        # default (decision 0009).
        for key, entry in sched.assignments.items():
            if entry.marks_return_datetime is not None and entry.marks_return_on_site:
                event_entries[f"marks-{slug(key)}.md"] = _event_row(
                    "special_event",
                    f"Marks expected: {identifier(schedule.semester_name(key, entry))}",
                    entry.marks_return_datetime,
                    entry.tbc,
                    False,
                    "",
                )
        # The term's own boundaries, when the schedule pins them.
        if sched.semester_start:
            event_entries["term-start.md"] = _term_date_entry(
                "Semester starts", sched.semester_start
            )
        if sched.semester_end:
            event_entries["term-end.md"] = _term_date_entry(
                "Semester ends", sched.semester_end
            )

        instructors_meta, instructors_path = _instructors_meta(semester_org)
        return SitePlan(
            config=config,
            # People: this semester's own semester-config/instructors.yml (instructors AND TAs -
            # the per-semester teaching team; schema in
            # templates/semester-config/instructors.yml), else its instructors team.
            files={
                "README.md": site_readme(semester_org, semester=True),
                "_data/people.yml": people_yaml(
                    semester_org,
                    instructors_meta,
                    edit_at=f"{semester_org}/semester-config/{instructors_path}",
                    semester=True,
                ),
                "_data/nav.yml": nav_yaml(semester=True, kinds=present),
                "_data/kinds.yml": kinds_yaml(),
                # The catch-all index behind the All Materials tab, across the repos
                # faculty actually release into, and the syllabus the home page pins.
                "_data/materials.yml": _materials_index(
                    semester_org,
                    indexable,
                    hosted,
                    _declared_syllabus(course_org, semester_org, sched, live, hosted),
                ),
                # The banner's link: this semester in the student console.
                "_data/console.yml": console_yaml(semester_org),
                **theme_pages(semester=True, kinds=present),
                # The course-specific layouts, includes and stylesheet - shipped
                # from templates/site/, not from the shared theme, so a change to
                # how a session renders is tested against the generator that
                # writes its front matter before any site sees it.
                **site_templates(),
            },
            # Assignment handout/due dates come from schedule.yml when set (keyed on the
            # assignment slug), else a synthesised fortnightly cadence.
            collections={
                "_lectures": rows,
                # Named by the number and the semester-side name (the name alone
                # without a number), so every assignment keeps its URL for the whole
                # term. A pending one is a placeholder rather than an absence - see
                # `_assignment_entry`. A hidden one is SKIPPED: numbers are explicit, so
                # hiding one moves nobody else's URL.
                "_assignments": {
                    f"{page.stem}.md": _assignment_entry(
                        course_org,
                        semester_org,
                        page.repo,
                        *_assignment_dates(
                            page.hit, start + timedelta(days=(page.number or 0) * 14)
                        ),
                        found=page.hit,
                        handed_out=handed_out,
                        sched=sched,
                    )
                    for page in pages
                    if shown(page.hit)
                },
                "_events": event_entries,
            },
            # The tab of a kind this semester has no rows of goes.
            retire=retired_kind_pages(present, site_wd),
            commit="site: sync from org structure",
            title="Student site",
        )

    # `--all-semesters` loops this in one process, and the rows read EVERY release
    # destination's tree, not just the session-bearing ones - so the memo would pin a few
    # hundred KB per repo for the whole run. Cleared on ENTRY rather than on the way out:
    # most semesters in a daily cron are already up to date and return early, so an exit-path
    # clear ran on the rare path and never on the common one. Keys include the org, so this
    # is purely about memory, never staleness.
    _repo_tree.cache_clear()
    # Cleared for memory, like the tree memo above: the key names the course org.
    _publish_policy.cache_clear()
    return sync_site_repo(semester_org, build)


def main() -> int:
    parser = CLIParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("sync")
    ps.add_argument("--course-org", required=True)
    ps.add_argument(
        "--semester-org",
        default=None,
        help="One semester; omit with --all-semesters",
    )
    ps.add_argument(
        "--all-semesters",
        action="store_true",
        help="Sync every registered semester (a course-level change, e.g. dsl-course.yml)",
    )
    pp = sub.add_parser("public-sync")
    pp.add_argument("--course-org", required=True)
    pp.add_argument(
        "--daily",
        action="store_true",
        help="The daily update: a course whose opencourse.yml is absent or off is a "
        "quiet no-op rather than a refusal",
    )
    args = parser.parse_args()
    if args.cmd != "public-sync" and not (args.all_semesters or args.semester_org):
        log_err("pass --semester-org or --all-semesters.")
        return 1
    # A read helper that couldn't reach the API raises RuntimeError; a config file with
    # one bad indent raises yaml.YAMLError out of load_yaml_config (instructors.yml is
    # web-editable, so faculty author that fault directly). In an Actions log a one-line
    # error beats a traceback either way, and the run still goes red.
    try:
        if args.cmd == "public-sync":
            return publish_public_site(args.course_org, daily=args.daily)
        if args.all_semesters:
            rc = 0
            for semester in live_semesters(args.course_org):
                # One semester's raised failure (an unreachable API, a instructors.yml that
                # doesn't parse) must not skip every LATER semester's site on the 06:00
                # cron - log it, mark the batch failed, and carry on. The same per-semester
                # isolation PR #151/#146 applied to the nightly refresh and the scheduler.
                try:
                    rc |= sync_site(args.course_org, semester)
                except Exception as exc:
                    log_err(
                        f"site sync for {semester} failed ({type(exc).__name__}): {exc}"
                    )
                    rc |= 1  # accumulate, don't clobber prior semesters' status bits
            return rc
        # --semester-org arrives on the automatic path straight from a repository_dispatch's
        # `client_payload.semester_org`, written by whoever holds a semester's DSL_BOT_TOKEN - a
        # lower trust tier than the course org. Naming SOMEONE ELSE'S semester would rebuild
        # that semester's site from this dispatch. The registry is the authority on which
        # semesters this course org owns, so an unregistered name is refused. Checked here
        # rather than inside sync_site, because every internal caller (a release, the
        # scheduler, the --all-semesters loop above) already passes a semester it read FROM the
        # registry - only the CLI takes one from outside. Casefold: GitHub org names are
        # case-insensitive.
        # An EMPTY registry authorises nothing. It used to short-circuit the whole check,
        # so a course org that had never registered a semester - or whose registry failed to
        # parse to anything - accepted any org name a dispatch cared to name.
        registered = discover_semesters(args.course_org)
        if args.semester_org.casefold() not in {c.casefold() for c in registered}:
            listed = ", ".join(sorted(registered)) or "nothing"
            log_err(
                f"{args.semester_org} is not registered under {args.course_org} "
                f"({SEMESTERS_PATH} lists {listed}) - refusing to sync its site."
            )
            return 1
        # Registered but closed out: the site repo is frozen with the rest of the semester
        # and its last sync was the one teardown ran before freezing it.
        if not semester_is_live(args.semester_org):
            return 0
        rc = sync_site(args.course_org, args.semester_org)
        # The site's last update is part of the semester's status. Not counted.
        status.refresh(args.course_org, args.semester_org)
        return rc
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
