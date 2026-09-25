"""dsl-course site -- regenerate a course/semester website from the live org structure.

Two sites, two audiences, one set of Jekyll templates (`templates/site/`):

- **semester site** (`<semester>.github.io`, `sync_site`) - student-facing. Its lecture links
  point at the semester's PRIVATE content repos (wherever a release actually landed each
  section - see `discovery.discover_release_sources`), so they 404 for non-members (the gate is
  deliberate). Regenerates `_lectures/`, `_assignments/`, `_events/` from the release state.
  Releases call it; the Sync site action runs it on demand.

- **course site** (`<course-org>.github.io`) - PUBLIC open courseware, opt-in, built in
  `public_site`; `public-sync` here is its CLI. It hosts the shared files rather than
  linking into the private repos - see that module.

Both hand their plan to `site_repo`, which applies it; pushing the site repo redeploys it.

Usage:
    python3 -m dsl_course.site sync --course-org TEST-HERTIE-COURSE \\
        --semester-org TEST-HERTIE-SEMESTER-f2026
    python3 -m dsl_course.site public-sync --course-org TEST-HERTIE-COURSE \\
        --source-repo course-materials-f2026 --readings-mode reading-list
"""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from textwrap import indent
from urllib.parse import quote

import yaml

from . import policy, schedule, status, teams
from .course import (
    CONFIG_REPO,
    CUTOFF_SENTENCE,
    INSTRUCTORS_FILE,
    PUBLISH_FILE,
    SELF_SELECT,
    assignment_slug,
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
    discover_assignments,
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
from .materials import (
    DECK_EXTENSIONS,
    Feed,
    alias_kind,
    hosted_copy,
    publishable,
)
from .materials import read as read_materials
from .public_site import resync_public_site, sync_public_site
from .readings import demote_headings, is_reading_overlay
from .repos import (
    default_branch,
    has_never_material_component,
)
from .schedule_plan import (
    PlannedRow,
    declared_syllabus,
    deploy_dest,
    deploy_section,
    planned_rows,
    site_rows,
)
from .site_repo import (
    PUBLISH_CONFIG,
    Link,
    SitePlan,
    block,
    iso_when,
    kinds_yaml,
    links_block,
    liquid_raw,
    nav_yaml,
    people_yaml,
    q,
    retired_kind_pages,
    row_file,
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


def _ext(name: str) -> str:
    """A file name's extension, lowercased and without the dot ('' when it has none). Not
    `Path().suffix`, which would call the whole of `Makefile` an extension-less name but
    read `figure-1` in `figure-1.tar.gz` inconsistently with the allowlist faculty write."""
    return name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""


# ---------------------------------------------------------------- publicly hosted copies

# Where a semester site serves the public copies, under its root. `files/`, never `<repo>/`:
# `/materials/` is the All Materials page's own permalink, and a semester whose content repo
# is called `materials` would otherwise take that page's URL. Jekyll serves any path that
# does not begin with `_`, so nothing has to be declared for these to be published.
SITE_FILES_DIR = "files"

# What is worth linking a hosted copy for - a deck, plus pdf, on which the browser opens
# its own viewer. Everything else (`ipynb`, `md`, `csv`) GitHub already renders, so a
# second copy would only be a second place for it to go stale.
_RENDERED_EXTENSIONS = frozenset({*DECK_EXTENSIONS, "pdf"})

# GitHub refuses a file over 100 MB on a push, so one carried into the site repo fails the
# sync's own push rather than the release that put it in the materials repo.
_MAX_PUBLIC_FILE_BYTES = 100 * 1024 * 1024

# Semester repo -> the paths this run actually copied under `files/<repo>/`. The ONE record
# of what is hosted: the mirror hands it back and every renderer reads it, so a page cannot
# link a rendered copy that a size cap, a broken clone or a denylist stopped being made.
Hosted = dict[str, frozenset[str]]


@cache
def _publish_policy(course_org: str, source_repo: str) -> tuple[str, ...]:
    """What `publish.yml` in `source_repo` declares public: its `public:` patterns, empty
    when it declares nothing.

    The policy is COURSE-level and lives in the source repo faculty actually edit, not in
    each semester's copy: one file per course, applying to every semester of it. Its
    patterns match THAT repo's paths (`materials.hosted_copy` translates a renamed copy
    back). Memoised for the run because `--all-semesters` asks the same course the same
    question once per semester.

    A file that is absent or empty is "nothing public", said deliberately, and the mirror
    may then delete what an earlier sync copied. A file that does not PARSE, or whose
    `public:` is not a list of patterns, stops the sync and reports - the same rule
    `instructors.yml` follows next door, and for the same reason: read as "nothing public" it
    would unpublish a whole course's rendered decks over a typo, on a green run."""
    declared = yaml_file(course_org, source_repo, PUBLISH_FILE).get("public")
    if declared is None:
        return ()
    if not isinstance(declared, list) or not all(isinstance(x, str) for x in declared):
        raise ValueError(
            f"{course_org}/{source_repo}/{PUBLISH_FILE}: `public:` must be a list of "
            "patterns"
        )
    return tuple(declared)


def _publish_policies(
    course_org: str, sched: schedule.Schedule, content_repos: list[str]
) -> dict[str, tuple[Feed, ...]]:
    """Each semester content repo the schedule releases into, mapped to the source repos
    that feed it: each one's patterns and the (source, semester) path pairs of its copies.

    Keyed on the DESTINATION, because that is the repo whose files the site links and
    whose bytes the mirror copies. Several sources may feed one destination (`lectures/`
    from the materials repo, `datasets/` from another), so a path is public if the
    policy of the source it came from says so.

    Every source repo is a feed, patterns or none: a copy from a repo that hosts nothing
    still owns the folder it lands in (`materials.hosted_copy`). A destination whose feeds
    declare nothing is the instruction to delete what an earlier sync copied for it. A repo no release plan names is absent, because nothing
    was ever copied for it.

    The schedule's declared destinations, not discovery's findings: this decides what gets
    CLONED and copied into a public site repo, so it reads a faculty declaration rather
    than a heuristic over an org listing (the same argument `_indexable_repos` makes)."""
    pairs: dict[str, dict[str, set[tuple[str, str]]]] = {}
    for release in sched.releases:
        for d in release.deploy:
            if d.semester_dest_repo in content_repos:
                pairs.setdefault(d.semester_dest_repo, {}).setdefault(
                    d.course_source_repo, set()
                ).add((d.course_source_path, deploy_dest(d)))
    return {
        repo: tuple(
            Feed(_publish_policy(course_org, source), tuple(sorted(copies)))
            for source, copies in sorted(sources.items())
        )
        for repo, sources in pairs.items()
    }


def _view_url(semester_org: str, repo: str, path: str) -> str:
    """Where the semester site serves its own copy of one published file.

    Absolute, not site-relative: the templates prepend `site.baseurl` to anything without
    a scheme, and a link record now carries two destinations - so the one that is already
    a full URL on every other row stays a full URL here too."""
    return f"https://{pages_repo(semester_org)}/{SITE_FILES_DIR}/{repo}/{quote(path)}"


def _public_selection(
    src: Path, repo: str, paths: tuple[str, ...], feeds: tuple[Feed, ...]
) -> frozenset[str]:
    """Which paths of `repo` the site can host, out of its released tree.

    `materials.hosted_copy`: each path judged under the source path it was released from,
    by the policy of the repo it came from (last match wins, which is what makes
    `!lectures/09_*/**` carve a session back out), plus the asset folders beside each
    matched deck. The denylist gates every candidate, bundles included, and cannot be
    written around.

    A file GitHub would refuse on a push is dropped with a warning rather than failing the
    sync: one 200 MB recording in a materials repo would otherwise take a semester's whole
    site offline, and the file it is a copy of is still on GitHub. So is anything that is
    not a regular file - `lstat`, so a `notes.pdf -> ../solution/answers.pdf` is judged as
    the link it is rather than as what it points at - and anything the clone does not
    have, which is a tree and a clone that disagree, not a file to publish.

    Judged over the same tree the links are built from (`_repo_tree`); the clone is only
    where the bytes and the sizes come from."""
    keep = set()
    for path in hosted_copy(paths, feeds):
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
    site_wd: Path, semester_org: str, policies: dict[str, tuple[Feed, ...]]
) -> Hosted:
    """Copy every publicly declared file of this semester's content repos into the site's
    own `files/<repo>/` tree, and say what actually landed there.

    The copy source is the RELEASED semester repo, never the course-org source: one release
    boundary for the whole toolkit, so a `.releaseignore` that held a file back from the
    semester holds it back from the public site without publishing having to re-ask. The
    clone is shallow - this wants the files at HEAD, not a term of old blobs.

    Deleted and rebuilt per repo on every sync, which is what makes unpublishing work:
    removing a pattern removes the copy. Only ever AFTER a successful clone - a site that
    republished with every rendered deck deleted because one clone failed is a worse
    outage than a copy one sync stale. (The site repo's git history keeps the old bytes
    either way; purging those is done by hand, and the docs say so.)

    Logs name repos and paths only. A materials repo is course property, and nothing here
    reads a student's repo: `policies` covers the release plan's declared destinations."""
    hosted: Hosted = {}
    root = site_wd / SITE_FILES_DIR
    for repo in sorted(policies):
        served = root / repo
        if not any(feed.public for feed in policies[repo]):
            # Nothing declared public. No clone is needed to know it, and an earlier
            # sync's copy has to go.
            if served.exists():
                shutil.rmtree(served)
            continue
        _branch, paths = _repo_tree(semester_org, repo)
        with tempfile.TemporaryDirectory() as work:
            src = Path(work) / repo
            if not clone(semester_org, repo, src, shallow=True):
                log_err(
                    f"could not clone {semester_org}/{repo} - its public copies on the "
                    "site are left as the last sync made them"
                )
                continue
            keep = _public_selection(src, repo, paths, policies[repo])
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


def _gh_url(org: str, repo: str, branch: str, kind: str, path: str) -> str:
    """A GitHub `blob`/`tree` URL for a path in a repo. One template, three callers.

    The semester site's own script parses THIS shape out of the rendered page to offer a file
    in the student's own fork or clone (`templates/site/_includes/open_in.html`), so the
    two change together."""
    return f"https://github.com/{org}/{repo}/{kind}/{branch}/{quote(path)}"


def _file_link(
    semester_org: str, repo: str, branch: str, path: str, name: str, hosted: Hosted
) -> Link:
    """One released file as a link: the GitHub blob it always has, plus the site's own
    hosted copy when the mirror actually made one AND a browser would render it
    (`_RENDERED_EXTENSIONS`). The one place the two destinations are paired, and it reads
    what was COPIED rather than re-deciding what should have been - a page cannot link a
    copy that does not exist."""
    view = (
        _view_url(semester_org, repo, path)
        if _ext(path) in _RENDERED_EXTENSIONS and path in hosted.get(repo, ())
        else ""
    )
    return Link(name, _gh_url(semester_org, repo, branch, "blob", path), view)


# The link name for the escape hatch out of an allowlist: whatever the list does not
# name is still one click away, rather than invisible.
_BROWSE_ALL = "browse the folder"


def _link_extensions(meta: dict) -> frozenset[str]:
    """`site_link_extensions` from a course's `dsl-course.yml`, lowercased and dot-stripped.

    The OPTIONAL allowlist narrowing what a session row links (see `_shape_links`); absent
    or empty means the default folder-shaped listing. A bare string
    (`site_link_extensions: pdf, html`) is accepted alongside a list - it is the shape
    faculty reach for first, and refusing it would only produce a silently unfiltered site."""
    raw = meta.get("site_link_extensions") or []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    return frozenset(str(x).strip().lstrip(".").lower() for x in raw if str(x).strip())


def _shape_links(
    blobs: list[Link], tree_base: str, allow: frozenset[str]
) -> list[Link]:
    """The links a row actually SHOWS, out of every file one of its folders released.

    Release is recursive because a release copies a folder wholesale, and it must stay that way. DISPLAY must not be: a rendered Quarto/Rmd deck is
    one deliverable plus hundreds of assets (`libs/`, `pics/`, `<name>_files/`), and linking
    each of them put 1,641 links across 27 rows on a live semester site - burying the three
    files a student actually opens. Nothing here changes what ships, only what is listed.

    Two shapes, and neither leaves a released file unreachable from the page:

    - DEFAULT (`allow` empty) - the folder as GitHub shows it. A file at the session
      folder's root links to the file; each immediate subfolder gets ONE link to its tree,
      named with its file count. Nothing to configure, and a course that keeps handouts in
      `handouts/` reaches them in one more click rather than losing them.
    - ALLOWLIST (`site_link_extensions`) - only files with those extensions, at any depth,
      plus one "browse the folder" link, so a file the list does not name is still one
      click away instead of invisible.

    A rule about DOTS is still refused, for the reason it always was: `__pycache__/`,
    `.ipynb_checkpoints/` and `node_modules/` are all clutter and none of them starts with
    a dot, while `.Rprofile`, `.env.example` and a `.devcontainer/` are real course
    material such a rule would hide. What is filtered is narrower and can be written
    honestly - the short closed list of NAMES that are never course material in any course
    (`repos.NEVER_MATERIAL`), dropped before the counts are taken so a folder cannot be
    listed as "3 files" while showing two.

    The site applies it as well as the release, not instead: a semester site showed
    `labs/01_session-1/.gitkeep` and a `readings/.DS_Store` as materials, and neither had
    passed through a release copy that day or would ever pass through one again. Junk
    committed straight into a semester's own content repo never meets the release filter, so
    a release-only rule leaves it listed for the rest of the term.

    `blobs` is every file under the folder, named by path relative to it (`_landed`);
    `tree_base` is the folder's own GitHub tree URL. Order follows `blobs` (path
    sorted), files before folders, for a stable diff. A FOLDER link never carries a hosted
    copy: what it opens is a GitHub listing, and this site hosts files, not directories."""
    blobs = [b for b in blobs if not has_never_material_component(b.name)]
    if allow:
        return [b for b in blobs if _ext(b.name) in allow] + [
            Link(_BROWSE_ALL, tree_base)
        ]
    files = [b for b in blobs if "/" not in b.name]
    counts: dict[str, int] = {}
    for b in blobs:
        head, sep, _rest = b.name.partition("/")
        if sep:
            counts[head] = counts.get(head, 0) + 1
    folders = [
        Link(f"{d}/ ({n} file{'' if n == 1 else 's'})", f"{tree_base}/{quote(d)}")
        for d, n in counts.items()
    ]
    return files + folders


@dataclass
class _Landed:
    """What one copy of a row has landed in the semester, found in its repo's tree: the
    links the row shows, and (on a readings row) the reading-list overlays it inlines."""

    repo: str
    section: str
    links: list[Link]
    overlays: list[str] = field(default_factory=list)
    # A single file at the repo's root - a course document (the syllabus, a README),
    # which is what the home page and All Materials show rather than a row.
    root_file: bool = False


def _landed(
    semester_org: str,
    deploy: schedule.Deploy,
    allow: frozenset[str],
    hosted: Hosted,
    readings: bool,
) -> _Landed | None:
    """What `deploy` has landed, or None while nothing has: a file is one link, a folder
    is its files as GitHub shows it (`_shape_links`), the repo root the whole repo.

    Read off the destination repo's memoised tree (`_repo_tree`), so a released folder
    whose name carries no ordinal is as linked as one that does. `readings` takes the
    reading-list overlay (`READINGS.md`) out of the links: the row inlines its text."""
    repo, path = deploy.semester_dest_repo, deploy_dest(deploy)
    branch, blobs = _repo_tree(semester_org, repo)
    section = deploy_section(deploy)
    if path and path in blobs:
        name = path.rsplit("/", 1)[-1]
        if readings and is_reading_overlay(name):
            return _Landed(repo, section, [], [path])
        link = _file_link(semester_org, repo, branch, path, name, hosted)
        return _Landed(repo, section, [link], root_file="/" not in path)
    prefix = f"{path}/" if path else ""
    inside = [b for b in blobs if b.startswith(prefix)]
    if not inside:
        return None
    overlays = [b for b in inside if readings and is_reading_overlay(b)]
    files = [
        _file_link(semester_org, repo, branch, b, b[len(prefix) :], hosted)
        for b in inside
        if b not in overlays
    ]
    tree = (
        _gh_url(semester_org, repo, branch, "tree", path)
        if path
        else f"https://github.com/{semester_org}/{repo}/tree/{branch}"
    )
    return _Landed(repo, section, _shape_links(files, tree, allow), overlays)


def _row_landed(
    semester_org: str,
    deploys: tuple[schedule.Deploy, ...],
    allow: frozenset[str],
    hosted: Hosted,
    live_repos: frozenset[str],
    readings: bool,
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
        landed = _landed(semester_org, deploy, allow, hosted, readings)
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


def _section_boundary(repo: str, path: str) -> tuple[str, str]:
    """(section, the prefix of `path` that names it) for one released blob already known
    to hold a "/" - a root file is handled separately by the caller.

    The ordinal decides only the LEVEL a section is read at, never whether a file shows up:

    - a repo whose top-level directories are session folders (`01_intro/...`) IS one
      section, the repo's own name - the shape a semester gets from
      `semester_dest_repo: lectures`. Nothing is stripped, so a session folder is itself the
      first node the tree gets.
    - a repo holding `lectures/`, `labs/`, `datasets/` gives one section EACH, named after
      the top directory - the shape from a single `materials` repo. That directory name IS
      the prefix, stripped so its own children become the section's nodes.

    Both live semester shapes therefore land where a reader expects. Same reading as
    `deploy_section` - head-of-path, else the repo - one level down."""
    head, _sep, _rest = path.partition("/")
    if session_number(head) is not None:
        return repo, ""
    return head, f"{head}/"


@dataclass
class _IndexEntry:
    """One node of the All Materials index - a file, or a directory nesting its own
    children to whatever depth the release actually has. `files` is 1 for a file and the
    total under a directory, so a level's total is always `sum(e.files for e in level)`
    regardless of what it mixes."""

    name: str
    is_dir: bool
    # Both destinations in one record, the same one a session row carries. A directory's
    # never has a hosted copy: what it opens is a GitHub listing.
    link: Link
    files: int = 0
    entries: dict[str, _IndexEntry] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """How the row reads: a directory keeps its trailing slash so it is obviously not
        a file."""
        return f"{self.name}/" if self.is_dir else self.name

    @property
    def children(self) -> list[_IndexEntry]:
        """This node's own entries, sorted for display."""
        return _sorted_entries(self.entries)


def _sorted_entries(entries: dict[str, _IndexEntry]) -> list[_IndexEntry]:
    """One level of the All Materials tree, directories before files, both alphabetically:
    this is a directory listing, where the structure is what a reader scans - the ordering
    every level uses, from a section's own top down to its deepest file."""
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
    ancestor directory it needs and counting the file into each one's `files`.

    `full_path` is the blob's path in `repo`; `prefix` is the part `_section_boundary`
    already spent naming the section, so what remains is split and walked exactly as deep
    as the release actually is - a file three folders down nests three folders down, unlike
    a session row's links (`_shape_links`), which fold a subfolder into a count because
    this is the one page a reader opens to see the whole shape instead."""
    parts = full_path[len(prefix) :].split("/")
    node = root
    entry_path = prefix.rstrip("/")
    for i, part in enumerate(parts):
        is_dir = i < len(parts) - 1
        entry_path = f"{entry_path}/{part}" if entry_path else part
        entry = node.get(part)
        if entry is None:
            link = (
                Link(part, _gh_url(semester_org, repo, branch, "tree", entry_path))
                if is_dir
                else _file_link(semester_org, repo, branch, entry_path, part, hosted)
            )
            entry = node[part] = _IndexEntry(part, is_dir, link)
        entry.files += 1
        node = entry.entries


def _emit_entries(entries: list[_IndexEntry], indent: str) -> list[str]:
    """YAML lines for one level of the All Materials tree, `indent` growing with every
    level it recurses into - a file three folders down reads no differently than one at
    the top, just deeper in the page."""
    lines: list[str] = []
    for e in entries:
        lines.append(f'{indent}- name: "{q(e.label)}"')
        lines.append(f"{indent}  url: {e.link.url}")
        # Written only where there is one, exactly as `links_block` writes it: an index
        # with nothing public is byte-identical to the one every course has today.
        if e.link.view_url:
            lines.append(f"{indent}  view_url: {e.link.view_url}")
        if e.is_dir:
            lines.append(f"{indent}  files: {e.files}")
            lines.append(f"{indent}  entries:")
            lines.extend(_emit_entries(e.children, indent + "    "))
    return lines


def _indexable_repos(
    sched: schedule.Schedule, release_sources: list[tuple[str, str, str, int]]
) -> set[str]:
    """Which semester repos the All Materials index is allowed to read.

    A POSITIVE allowlist, deliberately. `discover_semester_repos` works by exclusion - a repo
    is content unless it carries an infra topic - and that topic is written once, on
    creation, with its result ignored (`assign.py`), so a submission repo whose tag failed
    is content forever. That was survivable while a repo only reached the site by holding
    `NN_` session folders; this index reads whole trees, and the site repo it writes into is
    PUBLIC, so the same slip would publish every path of a student's private work.

    Two positive signals, both faculty declarations: a repo the release plan names as a
    destination, and a repo discovery actually found a released session in (which covers a
    manual release into a repo the plan never mentions). Non-ordinal material - a root
    `SYLLABUS.md`, a flat `datasets/` - is still indexed, because the signal is the REPO,
    not the folder shape inside it."""
    planned = {d.semester_dest_repo for r in sched.releases for d in r.deploy}
    return planned | {repo for repo, _sub, _folder, _n in release_sources}


def _materials_index(
    semester_org: str,
    content_repos: list[str],
    hosted: Hosted,
    syllabus: Link | None = None,
) -> str:
    """`_data/materials.yml` - every file released to this semester, nested exactly as its
    repo has it, for the All Materials tab.

    The catch-all. Every other page is curated: a row exists because the schedule named a
    session, and its links are the files of that session. This is the complete index, so
    it answers the two questions the curated pages cannot - a student's "what do I have?"
    and a teaching team's "did my file actually ship?" - including material no session
    ordinal covers.

    Nested, not folded: contrast the session rows (`_shape_links`), which count a
    subfolder rather than open it because a deck's rendered assets would otherwise bury the
    three files a student opens. This index is the one page meant to show the whole shape
    of what shipped, so a directory carries its own children all the way down instead.
    Filtered by name only where `_shape_links` is - no rule about dots, because a dotfile
    can be course material and most real clutter is not dotted, but the closed list of
    names that are never course material anywhere (`repos.NEVER_MATERIAL`) applies here
    too. Showing the whole shape of what shipped is not a reason to tell a class that a
    `.gitkeep` is one of their materials.

    Directories lead, then files, both alphabetically, at every level: this is a directory
    listing, where the structure is what a reader scans - unlike a session row, which leads
    with the deliverables because there the files ARE the material.

    Root files come out separately as `documents:` rather than as sections of their own,
    because a course-level document is not a section: a README released into three content
    repos was appearing three times, once under each repo's heading."""
    found: dict[str, dict[str, _IndexEntry]] = {}
    # Course-level documents - the syllabus, the README - keyed by NAME, not by the repo
    # they happen to sit in. They used to take the repo as their section, so a README
    # released into three content repos showed up three times, once under each. Deduping by
    # name is not lossy: a root document reaches a semester by being released FROM one file in
    # the course materials repo, so the copies are the same document by construction.
    docs: dict[str, _IndexEntry] = {}
    for repo in sorted(content_repos):
        branch, paths = _repo_tree(semester_org, repo)
        for path in paths:
            # A released `solution/`, `grading_config.yml` or hidden `tests/` is not course
            # material, and this index is the one page that lists everything a release
            # happened to carry - so it was the shortest route from "someone released a
            # folder wholesale" to "the whole class has the answers".
            #
            # The second check is the harmless twin of the first and stays a separate
            # list: nothing on it leaks anything, it is what a machine drops in a folder
            # (see `repos.NEVER_MATERIAL`).
            if not publishable(path):
                continue
            if "/" not in path:
                doc = _file_link(semester_org, repo, branch, path, path, hosted)
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
    # Through the same emitter as every other node: a root document is a file entry that
    # happens to sit at the top level, and a second copy of "how a node is written" is how
    # the two come to disagree about a field.
    doc_rows = _emit_entries(sorted(docs.values(), key=lambda e: e.name.lower()), "  ")
    header = (
        "# Generated by `python3 -m dsl_course.site sync` - every released file, nested\n"
        "# as its repo has it. Edit nothing here; it is rewritten on every sync.\n"
    ) + (
        # ONE key: the home page pins a single link, so what it opens is the hosted copy
        # where there is one and the GitHub blob otherwise.
        f"syllabus: {syllabus.view_url or syllabus.url}\n" if syllabus else ""
    )
    # Stated, not reached: `sections: []` is the empty index, the same shape
    # `links_block` uses for a row with nothing to link.
    # Documents first, then the sections - the order the page renders them in.
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
    return f"[`{dest}`]({_gh_url(semester_org, repo, branch, 'tree', here) if here else f'https://github.com/{semester_org}/{repo}'})"


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


def _kind_label(kind: str) -> str:
    """The policy's label for a kind ("Lecture", "Drop-in"); the key when unknown."""
    return next((k["label"] for k in policy.kinds() if k["key"] == kind), kind)


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
    label = _kind_label(row.kind)
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
    tabs = [row.kind]
    if row.kind != "readings" and (row.readings or row.readings_pending):
        tabs.append("readings")
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
        + f"tabs: [{', '.join(tabs)}]\n"
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


def _offplan_folders(
    semester_org: str, rows: list[PlannedRow], live_repos: frozenset[str]
) -> list[tuple[str, str, str]]:
    """`(repo, folder path, kind)` for every released folder of a kind-named section
    (`lectures/`, `labs/`, `readings/`, ..., or a repo so named) that no entry copies:
    material released outside the plan (the manual workflow, a semester with no
    `releases:`). Decision 0013: it keeps a row, on its kind's tab."""
    copies = [(d.semester_dest_repo, deploy_dest(d)) for r in rows for d in r.deploys]

    def covered(repo: str, folder: str) -> bool:
        return any(
            r == repo
            and (
                not p
                or p == folder
                or folder.startswith(f"{p}/")
                or p.startswith(f"{folder}/")
            )
            for r, p in copies
        )

    found: dict[tuple[str, str], str] = {}
    for repo in sorted(live_repos):
        _branch, blobs = _repo_tree(semester_org, repo)
        for path in blobs:
            parts = path.split("/")
            if len(parts) >= 3 and (kind := alias_kind(parts[0])):
                folder = "/".join(parts[:2])
            elif len(parts) >= 2 and (kind := alias_kind(repo)):
                folder = parts[0]
            else:
                continue
            if not publishable(path) or covered(repo, folder):
                continue
            found.setdefault((repo, folder), kind)
    return [(repo, folder, kind) for (repo, folder), kind in found.items()]


def _offplan_rows(
    semester_org: str,
    rows: list[PlannedRow],
    allow: frozenset[str],
    hosted: Hosted,
    live_repos: frozenset[str],
) -> list[tuple[str, _Row]]:
    """`(key, row)` for each off-plan folder: unnumbered, undated, named for its folder. A
    readings folder joins the lecture folder of the same `NN_` ordinal, as such folders
    always did; the rest are rows of their own."""

    def land(repo: str, folder: str, readings: bool) -> list[_Landed]:
        deploy = schedule.Deploy("", folder, repo)
        return _row_landed(semester_org, (deploy,), allow, hosted, live_repos, readings)

    folders = _offplan_folders(semester_org, rows, live_repos)
    lectures = {
        session_number(f.rsplit("/", 1)[-1]): (repo, f)
        for repo, f, kind in folders
        if kind == "lecture" and session_number(f.rsplit("/", 1)[-1]) is not None
    }
    joined: dict[tuple[str, str], list[_Landed]] = {}
    out = []
    for repo, folder, kind in folders:
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
    hosted: Hosted,
    live_repos: frozenset[str],
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
            semester_org, r.deploys, allow, hosted, live_repos, r.kind == "readings"
        )
        if not r.shown and all(item.root_file for item in landed):
            continue
        readings = []
        pending = False
        for attached in sr.readings:
            got = _row_landed(
                semester_org, attached.deploys, allow, hosted, live_repos, True
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
    built += _offplan_rows(semester_org, rows, allow, hosted, live_repos)
    out: dict[str, str] = {}
    tabs: dict[str, None] = {}
    for key, row in built:
        out[_row_filename(row, key, out)] = _row_entry(semester_org, row, live_repos)
        tabs[row.kind] = None
        if row.kind != "readings" and (row.readings or row.readings_pending):
            tabs["readings"] = None
    return out, list(tabs)


def _declared_syllabus(
    course_org: str,
    semester_org: str,
    sched: schedule.Schedule,
    live_repos: frozenset[str],
    hosted: Hosted,
) -> Link | None:
    """The syllabus released to this semester (`schedule_plan.declared_syllabus`) as the
    home page's link, or None - the home page then shows no line."""
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
    return _file_link(semester_org, repo, branch, path, path.rsplit("/", 1)[-1], hosted)


def member_digest(semester_org: str, handle: str) -> str:
    """A team member as the public semester site carries them: SHA-256 of
    `<semester org>:<handle, lower-cased>`, hex. Never the handle itself - the page's script
    hashes its reader's saved handle the same way (_layouts/assignment.html, `memberKey`)
    to recognise their team, and nothing else can read one back. Salted with the org so one
    student's digest differs from semester to semester. `.lower()`, not `.casefold()`, because
    the browser side is `toLowerCase` and GitHub handles are ASCII."""
    return hashlib.sha256(f"{semester_org}:{handle.lower()}".encode()).hexdigest()


def _formed_teams(semester_org: str, key: str) -> list[tuple[str, list[str]]]:
    """`(team, member handles)` for every team formed for `key` so far, by name.

    The same reader `team_formation.open_windows` uses, on the same private file and keyed
    on the same SCHEDULE key - so the table the site prints and the fault the teaching team
    gets count one thing.

    Never fatal, and that is the point of catching here: teams.csv is student-written, and a
    row somebody broke must not take down the render of a semester's whole website. The
    callout above still goes out; only the table is missing."""
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
    """An assignment's page, plus the two schedule rows it drives: the entry's own
    `date:` is the "released!" row and its `due_event:` sub-block the due row.

    `when` is the due date (a real one from schedule.yml, or a synthesised fallback);
    `handout` the scheduled provisioning moment when there is one. A handout dates the
    released-row where it belongs - at hand-out, not at the deadline - while an
    unscheduled assignment keeps both rows on the due date (the only date known).

    `found` is this assignment's `(slug, entry)` from the plan, already resolved by the
    caller, or None for one the plan does not name. It supplies the semester-side repo name
    exactly as assign.py / collect.py resolve it (`semester_dest_repo` else the slug, else
    the course repo minus its -fYYYY/-sYYYY tag), so the page names the repo students
    actually get - deriving it from the course repo alone named the wrong repo, and titled
    the page wrong, whenever an entry set `semester_dest_repo`.

    Handed IN rather than looked up here, because `schedule.entry_for_repo` maps a repo to
    the FIRST entry citing it - and two entries may legitimately cite one
    `course_source_repo` (a copy-paste, or two variants handed out from one template).
    Looking it up here gave both of them the same slug, the same dates and one collection
    file, so the second assignment vanished from the site.

    BOTH orgs, because the two halves of an assignment live in different ones: the template
    and its README are read from `course_org`, and the repo a student actually works in is
    in `semester_org`. This took `course_org` alone and used it for both, so every released
    assignment told students their repo was "in `<course-org>`'s semester org" - naming the
    org they have no access to, and leaving them to guess the one they do.

    A released entry carries `repo_url` / `repo_name` for that repo. The URL is the semester
    org's repo list filtered to this assignment, not a per-student address: the site is one
    public page for the whole semester and cannot know who is reading it, but GitHub shows a
    signed-in student only the repos they can see - so the filter resolves to their own (or
    their team's). `repo_name` is the shape to expect, `<slug>-<your-handle>` or
    `-<your-team>` for a group assignment.

    An assignment NOT YET HANDED OUT is a PLACEHOLDER, flagged `handout_pending: true`:
    both schedule rows, and an entry the Assignments tab renders unlinked, saying it is
    not out yet. What is withheld is the assignment's CONTENT, which is the same line
    `_row_entry` draws for an unreleased session - the plan is public from the day it
    is written, the payload arrives on release.

    The distinction matters here more than anywhere else on the site. The rows are driven
    by the course org's `assignment-*` TEMPLATE repos
    (`discovery.discover_assignments`), which exist from the moment faculty write the
    assignment - weeks before it hands out - so the README is not read at all while the
    assignment is pending: neither the brief nor the real title (`# Detecting fraud in the
    transfer dataset` is the assignment, not its name) can reach the public semester site
    early. The placeholder carries only what the plan already publishes on the schedule -
    the slug's own name, the hand-out date and the deadline.

    Handed out means EITHER of two things, and it takes both being false to withhold:

    - `handed_out` holds this assignment's semester-side name - a frozen semester template repo
      exists (`discovery.discover_handed_out_assignments`), so students have their repos
      whatever route fired it. This is the same "what actually shipped" signal a session
      row reads, and the only one that covers the manual workflow, whose documented mode
      pins no `handout_datetime` at all until it fires.
    - `handout` has passed. A pin whose provisioning then failed still says the brief was
      meant to be out by now, and a schedule that says so is not a secret worth keeping.

    `now` is the moment to judge the pin against (default: actual now, in the handout's own
    semester timezone - `_coerce_datetime` hands out nothing naive).

    A self-select GROUP assignment inside its team-formation window is the third state,
    and it exists because the second bullet above is right about the brief and silent about
    everything else: that handout parks and provisions nothing until a team exists, so the
    pin published the brief and, beside it, a `repo_url` into a repo list an unteamed
    student sees nothing in. Both stay - the assignment really is out, and a team that
    formed on day one owns its repo already - and `team_join_url` / `team_join_cap` /
    `team_join_closes` are written ALONGSIDE them: the thing the student can actually do
    about an empty listing.

    The WINDOW, off `sched` and never "has any team formed yet": the second is
    semester-wide, so the first team to form would take the invitation away from every
    student still looking for one. `sched` is the plan the caller already parsed; without
    it (a caller that has no plan to hand) there is no window and the page reads as it
    always did."""
    slug = schedule.semester_name(*found) if found else assignment_slug(repo)
    # An unscheduled assignment's synthesised fallback date is due end-of-day.
    due = iso_when(when, "23:59:00")
    released = iso_when(handout) if handout is not None else due
    pinned_out = handout is not None and handout <= (
        now or datetime.now(handout.tzinfo)
    )
    out = slug in handed_out or pinned_out
    # A group assignment fans out one repo per TEAM, so the shape a student looks for
    # differs. Off the assignment's own spec, like every other consumer, rather than a
    # second copy of the rule here - which is how the site comes to name a shape the
    # handout does not create. It costs the template's grading_config.yml, memoised per
    # template per process; the semester's schedule.yml, which the site used to read it
    # from for free, no longer has a say.
    spec = load_grading_spec(
        course_org,
        repo,
        semester_org=semester_org if found else "",
        slug=found[0] if found else "",
    )
    # The slug's own name: the row's IDENTIFIER, bold beside its name, and the one half
    # that must not change at hand-out. It used to be overwritten by the README heading, so
    # a row published as "Assignment 2" became "Assignment 1 - linear regression from
    # scratch (individual)" the moment it shipped - the same row apparently becoming a
    # different thing. Exactly `_row_entry`'s split: `title` identifies, `subtitle`
    # names (and the theme renders the pair identically for both).
    title = identifier(slug)
    # The template's `title:` (its one home, decision 0009) wins, and is the only name
    # that can appear BEFORE hand-out: the README it otherwise comes from is embargoed
    # until then.
    subtitle = spec.title
    # The plan's `details:`, filling the Details column of BOTH rows above what they
    # already generate - the link to the brief on one, the submit address on the other.
    # There is no README fallback for it: the brief is the page's body, and a sentence
    # that appeared on the schedule only once the assignment shipped would be a different
    # row's worth of information arriving at hand-out.
    details = found[1].details if found else ""
    # Display-only, and it reaches nothing but the two rows: `due` above is already
    # resolved, and the freeze, the late window and the cutoff are `schedule.grading_cutoff_datetime`'s
    # business off `due_datetime`. A deadline that says "(TBC)" still closes when it says.
    tbc_fm = "tbc: true\n" if found and found[1].tbc else ""
    tbc_due = indent(tbc_fm, "    ")
    external = spec.submit_external
    # Is this assignment waiting on its teams RIGHT NOW? `schedule.formation_state` is the
    # same call `grades.team_lock_entries` makes for the lock the Join-team form reads, so
    # the page cannot invite a student through a door the form has already shut. The shape
    # question is the spec's, like every other shape fact on this page: `assigned` teams
    # are the teaching team's to write, and there is nothing for a student to open.
    window, shuts = (
        schedule.formation_state(sched, found[0], now or datetime.now(UTC))
        if sched is not None and found is not None
        else ("closed", None)
    )
    # Whether this page should ASK for a team. Keyed on the window, never on whether a
    # team has formed: "has anybody formed one" is a semester-wide answer, so the first team
    # to form would take the call to action away from every student still without one.
    #
    # It does NOT suppress `repo_url`. Hiding the button for the whole window would punish
    # exactly the students who acted first - a team that forms on day one would lose the
    # link to its own repo until the window shut - and for a shared drop box, which exists
    # from the hand-out whatever the teams do, it would hide a URL that was never empty.
    # The two live side by side: the button works for a student whose team exists (GitHub
    # filters the listing by what they can read), and the call to action beside it says
    # what to do if it comes back empty.
    forming = window == "open" and spec.team_formation_resolved == SELF_SELECT
    # Where the work goes. `submit_shape` is the SHAPE in one word (`course.submit_shape`:
    # `assignment-repo-private`, `assignment-repo-public`, `external`), written whatever
    # the handout state
    # because it is the plan's and is known before anything ships - the theme `case`s on
    # it, and without it both the page and the due row told a Moodle semester to submit by
    # pushing to `main`. ONE key and not the `submit_via`/`visibility` pair it is derived
    # from: the two are orthogonal in the config and are not on the page, and a theme that
    # branched on both had to be re-opened for every shape that is neither. An ADDRESS is a
    # place to go NOW, so `repo_url` and `submit_url` both wait until there is something at
    # the other end of them; a shape that creates no repo has no name to print at all.
    repo_lines = [f'submit_shape: "{spec.submit_shape}"']
    # Whose folder, or whose repo: a group assignment fans out per TEAM, and the two words
    # are the same word wherever the page names one.
    whose = "<your-team>" if spec.is_group else "<your-handle>"
    repo_name = ""
    if external:
        if out and spec.submit_url:
            repo_lines.append(f'submit_url: "{q(spec.submit_url)}"')
            repo_lines.append(f'submit_host: "{q(spec.submit_host)}"')
    elif spec.submit_shared:
        # The REAL name, and a real URL: there is one drop box for the whole semester, so
        # unlike every other shape the page can name the repo exactly rather than describe
        # its shape. No `repo_name_is_shape` with it - see below.
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
        # Whether `repo_name` is a SHAPE to substitute a handle into, or a real repo
        # name. The theme marks the button and the link for `open_in.html` on this and on
        # nothing else: a shared drop box is named exactly, has no `<your-handle>` to
        # replace, and a rewrite of it would point every reader at a repo that does not
        # exist. One flag rather than a second `case` in the theme, so a shape added later
        # says which it is rather than being matched by name.
        repo_lines.append("repo_name_is_shape: true")
    # What happens after the deadline, as the sentence the page's callout closes with
    # (`course.late_rule`, off the assignment's own grading_config.yml like every other
    # shape fact). Written for every TIMED shape and for no other: `external` creates no
    # repo, so no commit is pinned, no day is counted and no penalty is ever applied
    # (`course.collects_commits`) - a rule quoted there would be about a deadline this
    # toolkit does not hold. The page alone, not the due row: the row is a glance at WHEN
    # and WHERE, and the rule belongs beside the answer it qualifies.
    # When the work is READ, closing the route the callout has just given - one sentence
    # from `course.CUTOFF_SENTENCE`, which the repo's own About line carries too, so the
    # page and the repo cannot come to name two different moments. Front matter rather
    # than a line in the layout for that reason alone: three arms of one `case` would
    # otherwise hold three copies of it, and the About line a fourth.
    # Gated exactly like `late_rule` below, and for the same reason: `external` pins no
    # commit, so there is no `main` for a cutoff to be read off.
    cutoff_fm = (
        f'cutoff_sentence: "{q(CUTOFF_SENTENCE)}"\n' if spec.collects_commits else ""
    )
    late_fm = (
        f'late_rule: "{q(late_rule(spec.late_window_days, spec.late_penalty_per_day))}"\n'
        if spec.collects_commits
        else ""
    )
    # What the assignment is out of, off the `questions:` maxima it declares - the same
    # sum the gradebook prints beside a score (`grades.total_points`), so the two cannot
    # disagree. Written only when there IS one: `questions:` is optional, and a course may
    # write `Q1: see rubric`, where there is no total to print at all. The brief asks
    # nobody to type it (`scaffold._brief_stub`): a fact the assignment already declares
    # is read from the declaration. The page alone, like `late_rule` - the due row is a
    # glance at WHEN and WHERE.
    points = total_points(spec)
    points_fm = f'max_points: "{points}"\n' if points else ""
    # Who can read the repo this shape hands out, as the aside the layout prints under the
    # brief (`course.SHAPE_NOTES` - the same sentence the repo's own About line carries, so
    # the page and the repo cannot come to say different things). Written for every shape
    # that hands one out, the ordinary private repo included; empty for `external`, which
    # hands out no repo for a sentence to be about.
    # The PAGE alone: the due row is a glance at when and where, and a warning in it would
    # be read on the schedule by everyone, about every assignment, at once.
    # And only once the brief is out: a warning about a repo that does not exist yet would
    # sit above the line saying the assignment has not been handed out.
    note = shape_note(spec.submit_shape) if out else ""
    note_fm = f'shape_note: "{q(note)}"\n' if note else ""
    # The one thing a student can act on while this assignment waits for its teams: the
    # `join` repo's issue chooser, the cap on a team and the day the door shuts. Three
    # keys and no fourth flag - their PRESENCE is the state, so a theme that has never
    # heard of team formation renders nothing rather than an empty callout, and the layout
    # and the schedule row read the same two facts rather than each wording its own.
    #
    # The first thing on either site to link `join` at all, so it is built from the
    # SEMESTER org: the course org has no join repo, and the one this semester's students
    # are members of is the only one that would answer them.
    #
    # The day, not the moment, and SPOKEN here (`grades.spoken_day`, in the semester's zone):
    # it is the same day the Join-team form's refusal and the mail name, in the same
    # spelling, and an hour would invite a student to read a deadline off a page whose
    # timezone it does not state.
    #
    # WHICH teams exist rides with them, off the semester's private teams.csv: a student
    # deciding whether to start a team or ask to join one needs to know what is already
    # there, and this page is the one list of them - a push to teams.csv re-syncs it.
    #
    # NAMES AND COUNTS, and no readable handle: each team's members ride as salted SHA-256
    # digests (`member_digest`), for the page's script to recognise its own reader's team
    # and nothing else.
    #
    # And each team's own repo URL, precomputed here off the same `submission_repo` the
    # handout provisions with, so the page's script links a recognised reader straight to
    # it without a second spelling of the name. A shared drop box is one repo for every
    # team, so its URL is the drop box's; an external assignment has no repo to link.
    team_fm = ""
    if forming and shuts is not None:
        join_url = join_issue_url(semester_org)
        cap = team_cap(course_org, spec)

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
            f'team_join_url: "{join_url}"\n'
            f'team_join_cap: "{cap}"\n'
            f'team_join_closes: "{closes}"\n'
            f'team_salt: "{q(semester_org)}"\n'
            + (f"teams:\n{listed}" if listed else "")
        )
    # Written at BOTH levels: the due row is a sub-hash the theme reaches through
    # `map: "due_event"`, so it cannot see its parent's fields - and the row that tells a
    # student when to submit is the one that should say where.
    repo_fm = "".join(f"{ln}\n" for ln in repo_lines)
    repo_due = "".join(f"    {ln}\n" for ln in repo_lines)
    if out:
        readme = get_file_content(course_org, repo, "README.md") or ""
        for line in readme.splitlines():
            if line.startswith("# ") and not subtitle:
                subtitle = row_name(line[2:], title)
                break
        brief = "\n".join(
            ln for ln in readme.splitlines() if not ln.startswith("# ")
        ).strip()
        flags = ""
        # No trailing "your repo appears once the teaching team provisions it" line: the
        # repo exists by the time this renders, and the theme now links it twice off the
        # fields above. The body is the brief, and nothing else.
        body = liquid_raw(brief or "Assignment brief.")
    else:
        # A flag as well as the prose: the theme leaves the title unlinked off this,
        # and the sentence says why. Its twin on a session row, `unreleased: true`, is
        # written for the same reason - and the Readings tab now reads it rather than
        # inferring the state from an empty body.
        # No `repo_url`: there is nothing at the other end of it yet.
        flags = "handout_pending: true\n"
        # Word for word the shape of an unreleased session's line (`_row_entry`):
        # "**<what> is not yet released** - <where it will be> when <it is>", bold lead
        # inside italics. They render in the same table column and on adjacent tabs, so
        # they read as one status vocabulary or as two.
        # The word describes the repo the handout will CREATE, which for the shape whose
        # flag the students hold is a private one - `student_choice` is a rule about who
        # may change it later, not a repo anybody is ever handed.
        born = "private" if spec.visibility_is_students else spec.visibility
        if external:
            coming = "the brief appears here when it is"
        elif spec.submit_shared:
            # Not "your repo": there is one, it is the semester's, and what is the student's
            # own is a folder in it.
            coming = f"the `{repo_name}` drop box appears when it is"
        else:
            coming = f"your {born} `{repo_name}` repo appears when it is"
        body = f"_**{title} is not yet released** - {coming}._"
    # After the branch above, which is where a released entry learns its name from the
    # README. The due row is the same assignment, so it shows the same two halves -
    # identifier bold, name beneath - rather than one of them.
    sub_fm = f'subtitle: "{q(subtitle)}"\n' if subtitle else ""
    sub_due = f'    subtitle: "{q(subtitle)}"\n' if subtitle else ""
    # Written at BOTH levels for the same reason `repo_lines` is: the due row is a
    # sub-hash the theme reaches through `map: "due_event"`, so it cannot see its parent's
    # `details`, and the row that tells a student when to hand in is as entitled to the
    # sentence as the one that tells them it is out.
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
    lecture and lab rows (released ones linked into the private content repos, planned
    ones marked not-yet-released), this year's assignments, and the display-only rows of
    the schedule (exams, special events and term dates)."""

    def build(site_wd: Path) -> SitePlan:
        # ONE listing of the semester answers both questions this build asks of it: which
        # repos hold released content, and which assignments have actually gone out.
        # Taken here rather than memoised in `discovery`, because a memo would serve a
        # listing from before assign.py created the template it is syncing the site for.
        semester_repos = list_org_repos(semester_org)
        content_repos = semester_content_repos(semester_repos)
        release_sources = discover_release_sources(semester_org, content_repos)
        assignments = discover_assignments(course_org)
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
        # Every assignment this semester has a page for, numbered as every link to one
        # numbers it (`schedule.assignment_pages`: this term's templates plus the plan's
        # entries, hidden ones included so that a hidden page keeps its ordinal unspent).
        pages = schedule.assignment_pages(semester_org, sched, assignments)

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
        allow = _link_extensions(meta)
        # The repos this semester actually releases into - the only ones the index and
        # the syllabus lookup may read (see `_indexable_repos`).
        indexable = sorted(
            set(content_repos) & _indexable_repos(sched, release_sources)
        )
        # What this course declares PUBLIC, per release destination (`publish.yml` in the
        # source repo the plan names). The copy happens here, before a single row is
        # rendered, so every page that links a hosted copy links one that exists.
        policies = _publish_policies(course_org, sched, content_repos)
        hosted = _mirror_public(site_wd, semester_org, policies)

        # The rows: one per `releases:` entry, in date order, kind declared or inferred
        # once from where its first copy lands (the source repo's `materials.yml` aliases,
        # else the built-in ones).
        planned = planned_rows(
            sched, lambda repo: read_materials(course_org, repo).kinds
        )
        # The repos the release plan names or a released session was found in: never a
        # student's repo, whose folder names would reach this public site.
        live = frozenset(indexable)
        rows, present = _site_rows(semester_org, planned, allow, hosted, live)
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
                # The catch-all index behind the All Materials tab: every released file,
                # including what no session ordinal covers - across the repos faculty
                # actually release into, never everything discovery failed to exclude.
                "_data/materials.yml": _materials_index(
                    semester_org,
                    indexable,
                    hosted,
                    # Absent when the semester has no syllabus, so the home page shows no
                    # line rather than an empty one.
                    syllabus=_declared_syllabus(
                        course_org, semester_org, sched, live, hosted
                    ),
                ),
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
                # Named by the semester-side name, ordinal from the position in the full
                # list, so every assignment keeps its URL for the whole term. A pending one
                # is a placeholder rather than an absence - see `_assignment_entry`.
                # A hidden one is SKIPPED, not renumbered around: its ordinal stays spent,
                # so hiding one mid-term leaves every other assignment's URL - and every
                # synthesised fallback date - exactly where it was.
                "_assignments": {
                    f"{page.stem}.md": _assignment_entry(
                        course_org,
                        semester_org,
                        page.repo,
                        *_assignment_dates(
                            page.hit, start + timedelta(days=page.number * 14)
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

    # `--all-semesters` loops this in one process, and the index reads EVERY release
    # destination's tree, not just the session-bearing ones - so the memo would pin a few
    # hundred KB per repo for the whole run. Cleared on ENTRY rather than on the way out:
    # most semesters in a daily cron are already up to date and return early, so an exit-path
    # clear ran on the rare path and never on the common one. Keys include the org, so this
    # is purely about memory, never staleness.
    _repo_tree.cache_clear()
    # Cleared for memory, like the tree memo above, not for staleness: the key names the
    # course org, and the policy is one small file per source repo.
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
        "--source-repo",
        default=None,
        help="Course materials repo to publish; omit to re-sync from the settings the "
        f"last publish persisted in the site repo ({PUBLISH_CONFIG})",
    )
    pp.add_argument(
        "--readings-mode",
        choices=["reading-list", "actual-readings", "none"],
        default="reading-list",
    )
    pp.add_argument(
        "--no-include-lectures", action="store_true", help="Skip lecture files"
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
            if not args.source_repo:
                return resync_public_site(args.course_org)
            return sync_public_site(
                args.course_org,
                args.source_repo,
                args.readings_mode,
                include_lectures=not args.no_include_lectures,
            )
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
