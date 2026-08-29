"""dsl-course site -- regenerate a course/cohort website from the live org structure.

Two sites, two audiences, one Jekyll template (course-website-template):

- **cohort site** (`<cohort>.github.io`, `sync_site`) - student-facing. Its lecture links
  point at the cohort's PRIVATE content repos (wherever a release actually landed each
  section - see `discovery.discover_release_sources`), so they 404 for non-members (the gate is
  deliberate). Regenerates `_lectures/`, `_assignments/`, `_events/` from the release state.
  Releases call it; the Sync site action runs it on demand.

- **course site** (`<course-org>.github.io`) - PUBLIC open courseware, opt-in, built in
  `public_site`; `public-sync` here is its CLI. It hosts the shared files rather than
  linking into the private repos - see that module.

Both hand their plan to `site_repo`, which applies it; pushing the site repo redeploys it.

Usage:
    python3 -m dsl_course.site sync --course-org TEST-HERTIE-COURSE \\
        --cohort-org TEST-HERTIE-COHORT-f2026
    python3 -m dsl_course.site public-sync --course-org TEST-HERTIE-COURSE \\
        --source-repo course-materials-f2026 --readings-mode reading-list
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from urllib.parse import quote

import yaml

from . import schedule
from .course import (
    assignment_slug,
    pages_repo,
    resolve_is_group,
    session_number,
    submission_repo,
    term_tag,
)
from .discovery import (
    COHORTS_PATH,
    discover_assignments,
    discover_cohort_repos,
    discover_cohorts,
    discover_handed_out_assignments,
    discover_release_sources,
)
from .gh_contents import get_file_content, repo_tree
from .log import log_err, log_step
from .public_site import resync_public_site, sync_public_site
from .readings import demote_headings, is_reading_overlay
from .repos import (
    default_branch,
    has_denied_component,
)
from .schedule_plan import (
    READINGS_SECTION,
    PlannedRow,
    planned_sessions,
    row_kind,
)
from .site_repo import (
    PUBLISH_CONFIG,
    ROW_NOUN,
    SitePlan,
    block,
    iso_when,
    links_block,
    liquid_raw,
    nav_yaml,
    people_yaml,
    q,
    row_file,
    site_readme,
    site_templates,
    slug,
    sync_site_repo,
    theme_pages,
    yaml_file,
)


def _semester_start(cohort_org: str) -> date:
    """Best-effort semester start from a fYYYY / sYYYY tag (for schedule ordering)."""
    tag = term_tag(cohort_org)
    if tag:
        return date(int(tag[1:]), 9 if tag[0] == "f" else 2, 1)
    return date(2026, 1, 1)


def _semester_label(cohort_org: str) -> str:
    """fYYYY -> 'Fall YYYY', sYYYY -> 'Spring YYYY' (for site.course_semester)."""
    tag = term_tag(cohort_org)
    return f"{'Fall' if tag[0] == 'f' else 'Spring'} {tag[1:]}" if tag else ""


@cache
def _repo_tree(org: str, repo: str) -> tuple[str, tuple[str, ...]]:
    """(default branch, every blob path in it) for a repo - one recursive tree fetch,
    memoised for the run. A cohort site asks for the files of EVERY released session, and
    they nearly all live in the same repo, so without the memo the identical tree got
    fetched once per session. Paths come back sorted, so callers filtering them keep a
    stable diff.

    Unbounded cache: this is a one-shot CLI process, and the trees it reads are the
    handful of repos one cohort released into.

    The fetch itself is gh_contents.repo_tree (shared with discovery's directory-side twin, so
    the absent-vs-failed discrimination is written once): a genuinely absent/empty tree is
    `()` and the caller simply finds no files, while any other failure RAISES rather than
    reporting an empty tree - swallowed, it republished the site with every material link
    stripped."""
    branch = default_branch(org, repo, fallback="main")
    return branch, repo_tree(org, repo, branch, "blob")


def _source_prefix(subpath: str, folder: str) -> str:
    """Where a discovered session folder sits in its repo - `subpath/folder`, or the bare
    folder when the release landed at the repo root. Stated once: three callers need it,
    and a fourth copy of the rule is how they come to disagree (same argument
    `deploy_dest` makes for the deploy side)."""
    return f"{subpath}/{folder}" if subpath else folder


def _source_section(repo: str, subpath: str) -> str:
    """The section a DISCOVERED release source belongs to - its subpath, or the repo itself
    when the folder sits at the root. The read-side twin of `deploy_section`, which names
    this rule in its own docstring; both must answer alike or a row's kind, its reading
    list and its index heading disagree about the same folder."""
    return subpath or repo


def _gh_url(org: str, repo: str, branch: str, kind: str, path: str) -> str:
    """A GitHub `blob`/`tree` URL for a path in a repo. One template, three callers."""
    return f"https://github.com/{org}/{repo}/{kind}/{branch}/{quote(path)}"


def _session_files(
    org: str, repo: str, subpath: str, folder: str
) -> list[tuple[str, str]]:
    """(name, blob-url) for every file at ANY depth under `folder` (already confirmed by
    discovery.discover_release_sources to match a session's ordinal prefix), at `subpath`
    in a repo (or the repo root when `subpath` is empty - a release destination left
    at its default).

    Recursive, because a release copies a session folder wholesale (deploy's
    copytree), so `03_week-3/handouts/notes.pdf` is just as released as a file sitting
    directly in `03_week-3/` - a non-recursive listing would silently drop it from the
    site. Filters the repo's one memoised recursive tree (`_repo_tree`) client-side, so
    no API call per session or per subfolder; names are the path relative to the session
    folder, so nested files stay distinguishable, and the ordering is by path for a
    stable diff."""
    prefix = _source_prefix(subpath, folder)
    branch, paths = _repo_tree(org, repo)
    return [
        (path[len(prefix) + 1 :], _gh_url(org, repo, branch, "blob", path))
        for path in paths
        if path.startswith(f"{prefix}/")
    ]


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


def _ext(name: str) -> str:
    """A file name's extension, lowercased and without the dot ('' when it has none). Not
    `Path().suffix`, which would call the whole of `Makefile` an extension-less name but
    read `figure-1` in `figure-1.tar.gz` inconsistently with the allowlist faculty write."""
    return name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""


def _shape_links(
    blobs: list[tuple[str, str]], tree_base: str, allow: frozenset[str]
) -> list[tuple[str, str]]:
    """The links a session row actually SHOWS, out of every file it released.

    Release is recursive (see `_session_files`) because a release copies a session folder
    wholesale, and it must stay that way. DISPLAY must not be: a rendered Quarto/Rmd deck is
    one deliverable plus hundreds of assets (`libs/`, `pics/`, `<name>_files/`), and linking
    each of them put 1,641 links across 27 rows on a live cohort site - burying the three
    files a student actually opens. Nothing here changes what ships, only what is listed.

    Two shapes, and neither leaves a released file unreachable from the page:

    - DEFAULT (`allow` empty) - the folder as GitHub shows it. A file at the session
      folder's root links to the file; each immediate subfolder gets ONE link to its tree,
      named with its file count. Nothing to configure, and a course that keeps handouts in
      `handouts/` reaches them in one more click rather than losing them.
    - ALLOWLIST (`site_link_extensions`) - only files with those extensions, at any depth,
      plus one "browse the folder" link, so a file the list does not name is still one
      click away instead of invisible.

    Nothing is filtered by NAME, dotfiles included. The exclusion list that would be needed
    cannot be written honestly: `__pycache__/`, `.ipynb_checkpoints/` and `node_modules/`
    are all clutter and none of them starts with a dot, while `.Rprofile`, `.env.example`
    and a `.devcontainer/` are real course material a rule about dots would hide. So the
    page says what was released, and the remedy for clutter sits where the clutter does -
    in what the release copies.

    `blobs` is (path-relative-to-the-session-folder, url) as `_session_files` returns it;
    `tree_base` is the session folder's own GitHub tree URL. Order follows `blobs` (path
    sorted), files before folders, for a stable diff."""
    if allow:
        return [(n, u) for n, u in blobs if _ext(n) in allow] + [
            (_BROWSE_ALL, tree_base)
        ]
    files = [(n, u) for n, u in blobs if "/" not in n]
    counts: dict[str, int] = {}
    for name, _ in blobs:
        head, sep, _rest = name.partition("/")
        if sep:
            counts[head] = counts.get(head, 0) + 1
    folders = [
        (f"{d}/ ({n} file{'' if n == 1 else 's'})", f"{tree_base}/{quote(d)}")
        for d, n in counts.items()
    ]
    return files + folders


def _session_links(
    org: str, repo: str, subpath: str, folder: str, allow: frozenset[str]
) -> list[tuple[str, str]]:
    """`_session_files` shaped for display (`_shape_links`), with the session folder's own
    GitHub tree URL for the folder links. The branch comes from the memoised `_repo_tree`,
    so naming the folder costs no extra API call."""
    branch, _paths = _repo_tree(org, repo)
    tree = _gh_url(org, repo, branch, "tree", _source_prefix(subpath, folder))
    return _shape_links(_session_files(org, repo, subpath, folder), tree, allow)


def _row_links(
    org: str, repo: str, subpath: str, folder: str, allow: frozenset[str]
) -> list[tuple[str, str]]:
    """One released section folder's display links - `_session_links`, minus the OVERLAY,
    whose content the row already inlines (see `_released_reading_list`). That file is the
    prose reading list; listing it again as a download says the same thing twice.

    Only the overlay is subtracted, not every text file. Subtracting by extension took an
    uploaded `notes.md` or `refs.bib` - a reading in its own right - out of the downloads as
    well, so a student could not get it."""
    links = _session_links(org, repo, subpath, folder, allow)
    if _source_section(repo, subpath) != READINGS_SECTION:
        return links
    return [(n, u) for n, u in links if not is_reading_overlay(n)]


def _released_reading_list(cohort_org: str, sources: list[tuple[str, str, str]]) -> str:
    """The prose a session row inlines: the text of the OVERLAY released into its `readings`
    section (`READINGS.md`), verbatim.

    Prose ONLY here, unlike the public site and the syllabus (`readings_block`), which name
    the other files because they have nowhere else to put them. This row does: every
    non-overlay file is already a real download beside it, via `_row_links`. Naming them here
    as well would print each reading twice on the same row.

    So the shared rule still holds - overlay is prose, everything else is a file - and only
    the CHANNEL differs: a link where the row can link, a name where it cannot.

    Reads the released COHORT copy, so a reading list appears on the same gate as every
    other material. `get_file_content` raises on anything but a 404 - a rate-limited read
    must not republish the row with the reading list silently emptied."""
    parts = []
    for repo, subpath, folder in sources:
        if _source_section(repo, subpath) != READINGS_SECTION:
            continue
        prefix = _source_prefix(subpath, folder)
        for name, _url in _session_files(cohort_org, repo, subpath, folder):
            if not is_reading_overlay(name):
                continue
            text = (
                get_file_content(cohort_org, repo, f"{prefix}/{name}") or ""
            ).strip()
            if text:
                parts.append(demote_headings(text))
    return "\n\n".join(parts)


def _section_boundary(repo: str, path: str) -> tuple[str, str]:
    """(section, the prefix of `path` that names it) for one released blob already known
    to hold a "/" - a root file is handled separately by the caller.

    The ordinal decides only the LEVEL a section is read at, never whether a file shows up:

    - a repo whose top-level directories are session folders (`01_intro/...`) IS one
      section, the repo's own name - the shape a cohort gets from
      `cohort_dest_repo: lectures`. Nothing is stripped, so a session folder is itself the
      first node the tree gets.
    - a repo holding `lectures/`, `labs/`, `datasets/` gives one section EACH, named after
      the top directory - the shape from a single `materials` repo. That directory name IS
      the prefix, stripped so its own children become the section's nodes.

    Both live cohort shapes therefore land where a reader expects. Same reading as
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
    url: str
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
    cohort_org: str,
    repo: str,
    branch: str,
    full_path: str,
    prefix: str,
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
            entry = node[part] = _IndexEntry(
                part,
                is_dir,
                _gh_url(
                    cohort_org, repo, branch, "tree" if is_dir else "blob", entry_path
                ),
            )
        entry.files += 1
        node = entry.entries


def _emit_entries(entries: list[_IndexEntry], indent: str) -> list[str]:
    """YAML lines for one level of the All Materials tree, `indent` growing with every
    level it recurses into - a file three folders down reads no differently than one at
    the top, just deeper in the page."""
    lines: list[str] = []
    for e in entries:
        lines.append(f'{indent}- name: "{q(e.label)}"')
        lines.append(f"{indent}  url: {e.url}")
        if e.is_dir:
            lines.append(f"{indent}  files: {e.files}")
            lines.append(f"{indent}  entries:")
            lines.extend(_emit_entries(e.children, indent + "    "))
    return lines


def _indexable_repos(
    sched: schedule.Schedule, release_sources: list[tuple[str, str, str, int]]
) -> set[str]:
    """Which cohort repos the All Materials index is allowed to read.

    A POSITIVE allowlist, deliberately. `discover_cohort_repos` works by exclusion - a repo
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
    planned = {d.cohort_dest_repo for r in sched.releases for d in r.deploy}
    return planned | {repo for repo, _sub, _folder, _n in release_sources}


def _released_syllabus(cohort_org: str, content_repos: list[str]) -> str | None:
    """The URL of the syllabus released to this cohort, or None when there isn't one - the
    home page then shows no line at all rather than an empty one.

    Found by name, under whatever name and format the course uses (`SYLLABUS.md`,
    `SYLLABUS.pdf`, `syllabus-2026.docx`). Faculty name it; we only have to find it - and a
    release can come from the manual workflow with a typed path, so there is no declaration to
    read instead.

    Two rules that matter more than they look:

    - ROOT files only. One live cohort has `lectures/01_introduction/pics/
      ids-syllabus-2024.png`, and pinning a screenshot on the landing page as the syllabus
      would be worse than pinning nothing.
    - An exact `syllabus.*` stem wins over a longer name. Plain sorting put
      `SYLLABUS-draft.pdf` ahead of `SYLLABUS.pdf` ('-' sorts before '.'), so a cohort that
      shipped a draft alongside the real thing got the draft on its front page.

    Reads the trees the caller already discovered, so this costs no API call. Order is
    deterministic without re-sorting: `content_repos` arrives sorted and `_repo_tree` returns
    sorted paths."""
    fallback = None
    for repo in content_repos:
        branch, paths = _repo_tree(cohort_org, repo)
        for path in paths:
            if "/" in path or "syllab" not in path.lower():
                continue
            url = _gh_url(cohort_org, repo, branch, "blob", path)
            if path.rsplit(".", 1)[0].lower() == "syllabus":
                return url
            fallback = fallback or url
    return fallback


def _materials_index(
    cohort_org: str, content_repos: list[str], syllabus: str | None = None
) -> str:
    """`_data/materials.yml` - every file released to this cohort, nested exactly as its
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
    Nothing is filtered by name (see `_shape_links`): a dotfile can be course material, and
    most real clutter is not dotted anyway.

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
    # name is not lossy: a root document reaches a cohort by being released FROM one file in
    # the course materials repo, so the copies are the same document by construction.
    docs: dict[str, _IndexEntry] = {}
    for repo in sorted(content_repos):
        branch, paths = _repo_tree(cohort_org, repo)
        for path in paths:
            # A released `solution/`, `grading.yml` or hidden `tests/` is not course
            # material, and this index is the one page that lists everything a release
            # happened to carry - so it was the shortest route from "someone released a
            # folder wholesale" to "the whole class has the answers".
            if has_denied_component(path):
                continue
            if "/" not in path:
                docs.setdefault(
                    path,
                    _IndexEntry(
                        path, False, _gh_url(cohort_org, repo, branch, "blob", path), 1
                    ),
                )
                continue
            section, prefix = _section_boundary(repo, path)
            _insert_released_path(
                found.setdefault(section, {}), cohort_org, repo, branch, path, prefix
            )
    rows_out: list[str] = []
    for section in sorted(found):
        entries = _sorted_entries(found[section])
        rows_out.append(f'  - name: "{q(section)}"')
        rows_out.append(f"    files: {sum(e.files for e in entries)}")
        rows_out.append("    entries:")
        rows_out.extend(_emit_entries(entries, "      "))
    doc_rows = [
        line
        for e in sorted(docs.values(), key=lambda e: e.name.lower())
        for line in (f'  - name: "{q(e.name)}"', f"    url: {e.url}")
    ]
    header = (
        "# Generated by `python3 -m dsl_course.site sync` - every released file, nested\n"
        "# as its repo has it. Edit nothing here; it is rewritten on every sync.\n"
    ) + (f"syllabus: {syllabus}\n" if syllabus else "")
    # Stated, not reached: `sections: []` is the empty index, the same shape
    # `links_block` uses for a row with nothing to link.
    # Documents first, then the sections - the order the page renders them in.
    body = "documents:\n" + "\n".join(doc_rows) + "\n" if doc_rows else ""
    body += "sections:\n" + "\n".join(rows_out) if rows_out else "sections: []"
    return header + body + "\n"


def _dest_link(cohort_org: str, dest: str, live_repos: frozenset[str]) -> str:
    """A planned destination (`repo/path`) as markdown - a LINK when there is something to
    link to, plain code when there is not.

    The path itself does not exist yet, by definition: that is what "not released" means, so
    linking it would hand a student a 404. What can exist is the destination repo, and once
    it does, its tree is already in hand - so the link points at the deepest ancestor of the
    path that is really there. `live_repos` is the cohort's existing repos, already
    discovered by the caller, so knowing this costs no extra API call."""
    repo, _, path = dest.partition("/")
    if repo not in live_repos:
        return f"`{dest}`"
    branch, blobs = _repo_tree(cohort_org, repo)
    here = ""
    for part in path.split("/"):
        candidate = f"{here}/{part}" if here else part
        if not any(b == candidate or b.startswith(f"{candidate}/") for b in blobs):
            break
        here = candidate
    return f"[`{dest}`]({_gh_url(cohort_org, repo, branch, 'tree', here) if here else f'https://github.com/{cohort_org}/{repo}'})"


def _describe(text: str) -> str:
    """A row's `description:` front matter - the session's learning objectives.

    A block scalar once it has a newline in it. The Hertie syllabus format writes these as
    a paragraph (sometimes two), and `q` folds every newline away, so a one-line scalar
    silently ran two paragraphs together. Empty stays absent rather than blank, so the
    theme can test for it."""
    if not text.strip():
        return ""
    if "\n" in text.strip():
        return block("description", text)
    return f'description: "{q(text)}"\n'


def _lecture_entry(
    cohort_org: str,
    session: str,
    row: PlannedRow,
    sources: list[tuple[str, str, str]],
    kind: str = "lecture",
    allow: frozenset[str] = frozenset(),
    live_repos: frozenset[str] = frozenset(),
) -> str:
    """One row of a teaching week: the lecture (`kind='lecture'`) or the lab
    (`kind='lab'`), which the theme renders as separate schedule lines out of the same
    `_lectures` collection.

    `sources` is (repo, subpath, folder) triples already confirmed (by
    discovery.discover_release_sources) to hold this exact session - callers pass only the
    sources known to match, so every call here is a real hit, not a probe.

    `row` is what the PLAN says about this session (`PlannedRow`): when the class happens,
    where its deploys will land, and the name and blurb its entry declared. A row discovery
    found but the plan never named gets a synthesised one, so there is no second shape to
    handle here. Taking the row whole rather than six of its fields is what stops this
    signature growing once per plan field.

    `title` stays the ordinal (`Session 3`) - what the theme has always assumed it is.
    `subtitle` and `description` are the plan's `title:` / `description:`: the session's
    name, and a sentence about it. Both are omitted when empty rather than written blank,
    so the theme can test for them.

    EMPTY `sources` is the not-yet-released row: the session is in the plan but its
    materials have not shipped, so the row carries no links, flags itself `unreleased:
    true` for the theme, and names the destinations the copy is going to land in. The whole term is on the schedule from the day it is written, exactly as an
    assignment's row appears from the day its template repo exists rather than the day it
    hands out.

    Only the links, the reading list and the body differ between the two - the front matter
    is one template, so a field added to the row (the way the event rows grew `tbc:`)
    cannot land on one kind of row and miss the other."""
    title = f"{ROW_NOUN[kind]} {session}"
    # `_row_name`, so an entry that declares `title: Lab 1` renders "Lab 1" and not
    # "Lab 1 / Lab 1" - the same trim an assignment's README heading gets, because faculty
    # repeat the identifier just as readily in the plan as in a README.
    subtitle, description = _row_name(row.subtitle, title), row.description
    reading_list = ""
    if sources:
        flags = ""
        links = links_block(
            [
                (
                    _source_section(repo, subpath),
                    _row_links(cohort_org, repo, subpath, folder, allow),
                )
                for repo, subpath, folder in sources
            ]
        )
        reading_list = _released_reading_list(cohort_org, sources)
        # No body. It used to read "Materials for session 1. Open the links above (you must
        # be an enrolled member of ...)" on every released row of every course - the links
        # are right there, and each page already states who can open them.
        body = ""
    else:
        # A flag as well as the prose: the theme can badge or grey an unreleased row off
        # this (as it already does for `tbc:`/`dateless:`), and until it does the sentence
        # below carries the meaning on its own. Without it a placeholder is
        # indistinguishable from a released folder that happens to hold no files.
        flags = "unreleased: true\n"
        links = links_block([])
        where = ", ".join(_dest_link(cohort_org, d, live_repos) for d in row.dests)
        # Italic, with the lead in bold: this is the one line on an unreleased row, and
        # it sat in the same weight as the session description above it - so a reader
        # scanning the Lectures tab read a paragraph before learning there was nothing to
        # open. `.session-note` sets the gap above it (dsl-jekyll-theme's _layout.scss).
        body = (
            f"_**Materials for {title.lower()} are not yet released**"
            + (f" - they will appear in {where} when they are" if where else "")
            + "._"
        )
        # `subtitle` and `description` are deliberately KEPT here. They describe what the
        # session is about, which is known the day the plan is written; the body next to
        # them describes whether its files have shipped. So the whole term reads as a
        # syllabus from day one, rather than as a list of empty rows that fills in weekly.
    # The plan ships readings for this row but no readings section has landed, so the
    # Readings tab says so rather than leaving the session off the page entirely. Decided
    # here, beside the reading list it is about, rather than by the caller.
    released = {_source_section(repo, subpath) for repo, subpath, _folder in sources}
    if row.readings_planned and READINGS_SECTION not in released:
        flags += "readings_pending: true\n"
    return (
        f"---\n"
        f"type: {kind}\n"
        f"date: {iso_when(row.when)}\n"
        f'title: "{title}"\n'
        + (f'subtitle: "{q(subtitle)}"\n' if subtitle else "")
        + _describe(description)
        + flags
        + (block("reading_list", reading_list) if reading_list else "")
        + f"{links}\n"
        f"---\n"
        f"{body}\n"
    )


# Separators faculty put between a row's identifier and its name. Two dash characters are
# in live sources already (`Assignment 1 - ...` and `Assignment 1 — ...`), which is exactly
# why this is a set and not a `-`.
_NAME_SEPARATORS = "-\u2013\u2014:|"


def _row_name(declared: str, identifier: str) -> str:
    """A row's NAME out of what faculty wrote, given the identifier the site already shows
    in bold beside it - so the pair reads "Session 3 / Probability theory" and never
    "Session 3 / Session 3".

    Faculty conventionally repeat the identifier: a template README opens `# Assignment 1 -
    linear regression from scratch`, and a `releases:` entry is as likely to say
    `title: Lab 1` as to name the lab. Printed whole under the identifier that reads
    "Assignment 1 / Assignment 1 - linear regression from scratch" and "Lab 1 / Lab 1", so
    drop the prefix and whatever separates it.

    Text that does NOT open with the identifier (`Group project - an end-to-end modelling
    report`) is the name already and is returned as it stands. Casefolded, so text that
    differs from the identifier only in capitalisation still matches."""
    name = declared.strip()
    if name.casefold().startswith(identifier.casefold()):
        rest = name[len(identifier) :].lstrip()
        # Only when a separator actually follows: `Assignment 10` must not be read as
        # `Assignment 1` plus the name "0".
        if rest[:1] in tuple(_NAME_SEPARATORS):
            return rest[1:].strip()
        if not rest:
            return ""
    return name


def _assignment_entry(
    course_org: str,
    cohort_org: str,
    repo: str,
    when: date | datetime,
    handout: datetime | None = None,
    found: tuple[str, schedule.AssignmentEntry] | None = None,
    handed_out: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> str:
    """An assignment's page, plus the two schedule rows it drives: the entry's own
    `date:` is the "released!" row and its `due_event:` sub-block the due row.

    `when` is the due date (a real one from schedule.yml, or a synthesised fallback);
    `handout` the scheduled provisioning moment when there is one. A handout dates the
    released-row where it belongs - at hand-out, not at the deadline - while an
    unscheduled assignment keeps both rows on the due date (the only date known).

    `found` is this assignment's `(slug, entry)` from the plan, already resolved by the
    caller, or None for one the plan does not name. It supplies the cohort-side repo name
    exactly as assign.py / collect.py resolve it (`cohort_dest_repo` else the slug, else
    the course repo minus its -fYYYY/-sYYYY tag), so the page names the repo students
    actually get - deriving it from the course repo alone named the wrong repo, and titled
    the page wrong, whenever an entry set `cohort_dest_repo`.

    Handed IN rather than looked up here, because `schedule.entry_for_repo` maps a repo to
    the FIRST entry citing it - and two entries may legitimately cite one
    `course_source_repo` (a copy-paste, or two variants handed out from one template).
    Looking it up here gave both of them the same slug, the same dates and one collection
    file, so the second assignment vanished from the site.

    BOTH orgs, because the two halves of an assignment live in different ones: the template
    and its README are read from `course_org`, and the repo a student actually works in is
    in `cohort_org`. This took `course_org` alone and used it for both, so every released
    assignment told students their repo was "in `<course-org>`'s cohort org" - naming the
    org they have no access to, and leaving them to guess the one they do.

    A released entry carries `repo_url` / `repo_name` for that repo. The URL is the cohort
    org's repo list filtered to this assignment, not a per-student address: the site is one
    public page for the whole cohort and cannot know who is reading it, but GitHub shows a
    signed-in student only the repos they can see - so the filter resolves to their own (or
    their team's). `repo_name` is the shape to expect, `<slug>-<your-handle>` or
    `-<your-team>` for a group assignment.

    An assignment NOT YET HANDED OUT is a PLACEHOLDER, flagged `handout_pending: true`:
    both schedule rows, and an entry the Assignments tab renders unlinked, saying it is
    not out yet. What is withheld is the assignment's CONTENT, which is the same line
    `_lecture_entry` draws for an unreleased session - the plan is public from the day it
    is written, the payload arrives on release.

    The distinction matters here more than anywhere else on the site. The rows are driven
    by the course org's `assignment-*` TEMPLATE repos
    (`discovery.discover_assignments`), which exist from the moment faculty write the
    assignment - weeks before it hands out - so the README is not read at all while the
    assignment is pending: neither the brief nor the real title (`# Detecting fraud in the
    transfer dataset` is the assignment, not its name) can reach the public cohort site
    early. The placeholder carries only what the plan already publishes on the schedule -
    the slug's own name, the hand-out date and the deadline.

    Handed out means EITHER of two things, and it takes both being false to withhold:

    - `handed_out` holds this assignment's cohort-side name - a frozen cohort template repo
      exists (`discovery.discover_handed_out_assignments`), so students have their repos
      whatever route fired it. This is the same "what actually shipped" signal a session
      row reads, and the only one that covers the manual workflow, whose documented mode
      pins no `handout_datetime` at all until it fires.
    - `handout` has passed. A pin whose provisioning then failed still says the brief was
      meant to be out by now, and a schedule that says so is not a secret worth keeping.

    `now` is the moment to judge the pin against (default: actual now, in the handout's own
    cohort timezone - `_coerce_datetime` hands out nothing naive)."""
    slug = schedule.cohort_name(*found) if found else assignment_slug(repo)
    # An unscheduled assignment's synthesised fallback date is due end-of-day.
    due = iso_when(when, "23:59:00")
    released = iso_when(handout) if handout is not None else due
    pinned_out = handout is not None and handout <= (
        now or datetime.now(handout.tzinfo)
    )
    out = slug in handed_out or pinned_out
    # A group assignment fans out one repo per TEAM, so the shape a student looks for
    # differs. Through `resolve_is_group` rather than testing `type == "group"` here: that
    # is the single precedence every other consumer resolves through, and a second copy of
    # it in the one place students READ the answer is how the site comes to name a shape
    # the handout does not create. `template_group=None` leaves the design-time grading.yml
    # unconsulted - the site will not spend an API call per assignment on a repo name.
    group = resolve_is_group(
        force=False,
        schedule_type=found[1].type if found else None,
        template_group=None,
    )
    repo_name = submission_repo(slug, "<your-team>" if group else "<your-handle>")
    # The slug's own name: the row's IDENTIFIER, bold beside its name, and the one half
    # that must not change at hand-out. It used to be overwritten by the README heading, so
    # a row published as "Assignment 2" became "Assignment 1 - linear regression from
    # scratch (individual)" the moment it shipped - the same row apparently becoming a
    # different thing. Exactly `_lecture_entry`'s split: `title` identifies, `subtitle`
    # names (and the theme renders the pair identically for both).
    title = slug.replace("-", " ").title()
    # The plan's own declaration wins, and is the only one that can appear BEFORE hand-out:
    # the README it otherwise comes from is embargoed until then.
    subtitle = found[1].title if found else ""
    # `repo_name` either way - the shape is the plan's, known before anything ships - and
    # `repo_url` only once there is something at the other end of it. So the theme tests
    # the flag for state and the URL only for "have I somewhere to link", rather than
    # inferring one from the other.
    repo_lines = [f'repo_name: "{q(repo_name)}"']
    if out:
        repo_lines.insert(
            0,
            f'repo_url: "https://github.com/orgs/{cohort_org}/repositories?q={slug}-"',
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
                subtitle = _row_name(line[2:], title)
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
        # Word for word the shape of an unreleased session's line (`_lecture_entry`):
        # "**<what> is not yet released** - <where it will be> when <it is>", bold lead
        # inside italics. They render in the same table column and on adjacent tabs, so
        # they read as one status vocabulary or as two.
        body = (
            f"_**{title} is not yet released** - your private "
            f"`{repo_name}` repo appears when it is._"
        )
    title = q(title)
    # After the branch above, which is where a released entry learns its name from the
    # README. The due row is the same assignment, so it shows the same two halves -
    # identifier bold, name beneath - rather than one of them.
    sub_fm = f'subtitle: "{q(subtitle)}"\n' if subtitle else ""
    sub_due = f'    subtitle: "{q(subtitle)}"\n' if subtitle else ""
    return (
        f"---\n"
        f"type: assignment\n"
        f"date: {released}\n"
        f'title: "{title}"\n'
        f"{sub_fm}"
        f"{flags}"
        f"{repo_fm}"
        f"due_event:\n"
        f"    type: due\n"
        f"    date: {due}\n"
        f'    description: "{title}"\n'
        f"{sub_due}"
        f"{repo_due}"
        f"---\n"
        f"{body}\n"
    )


def _exam_entry(
    title: str,
    when: date | datetime,
    tbc: bool = False,
    dateless: bool = False,
) -> str:
    """A red exam row (the template's schedule_row_exam.html styles `type: exam`).

    `when` is a datetime when schedule.yml gave the exam a real start time, or a bare date
    (whole-day entry, or the synthesised mid/end-of-semester fallback) - which keeps the
    09:00 placeholder.

    TBC: an undated exam (`date: tbc`) still needs a sortable date for the theme, so the
    caller passes end-of-term as `when` with `dateless=True` - the theme then prints
    "TBC" instead; `tbc=True` with a real date adds the "(TBC)" marker."""
    flags = ""
    if tbc or dateless:
        flags = "tbc: true\n" + ("dateless: true\n" if dateless else "")
    return (
        f"---\n"
        f"type: exam\n"
        f"date: {iso_when(when)}\n"
        f"{flags}"
        f'description: "{q(title)}"\n'
        f"---\n"
        f"Details to be confirmed.\n"
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


def _special_event_entry(
    title: str,
    when: date | datetime,
    tbc: bool = False,
    dateless: bool = False,
) -> str:
    """A generic schedule row (the theme's schedule_row_special_event.html) for a
    display-only entry: a clinic, a guest lecture, a review session. Nothing is released;
    the site simply shows it.

    The name goes in `description`, which the theme renders in the schedule's TITLE
    column - the same place a session's ordinal and an assignment's identifier sit. It
    used to go in `name`, which the theme renders in the EVENT column, so a guest lecture
    printed its whole name where "Lecture" / "Lab" / "Exam" print a row's KIND, and left
    its title cell empty. One meaning per column: Event says what kind of row this is,
    Title says which one it is. (`semester_start`/`_end` keep `name` - there the kind IS
    the name, "Term starts", and there is nothing declared to put beside it.)

    TBC: an undated entry (`event_datetime: tbc`) still needs a sortable `date:` for the
    theme, so the caller passes end-of-term as `when` plus `dateless=True` - the theme
    then prints "TBC" instead of the placeholder. A dated entry with `tbc=True` keeps its
    date and gains a "(TBC)" marker."""
    flags = ""
    if tbc or dateless:
        flags = "tbc: true\n" + ("dateless: true\n" if dateless else "")
    return (
        f"---\n"
        f"type: special_event\n"
        f"date: {iso_when(when)}\n"
        f"{flags}"
        f'description: "{q(title)}"\n'
        f"---\n"
    )


def _event_entry(event: schedule.Event, fallback: date) -> str:
    """One `events:` row, rendered as the type it declared - an exam or a special event.
    An event with no title of its own falls back to its prettified label, and an undated
    one (`event_datetime: tbc`) sorts at `fallback` (end of term) as a dateless row."""
    render = _exam_entry if event.type == "exam" else _special_event_entry
    return render(
        event.title or _pretty(event.label),
        event.when if event.when is not None else fallback,
        event.tbc,
        event.when is None,
    )


def _term_date_entry(name: str, when: date) -> str:
    """A semester-boundary row (the theme's schedule_row_term_date.html). `name` fills the
    row's event column and is the only text it shows, so the description stays empty;
    `hide_time` suppresses the placeholder clock time - a term boundary is a whole day,
    not a 09:00 appointment."""
    return (
        f"---\n"
        f"type: term_date\n"
        f"date: {iso_when(when)}\n"
        f"hide_time: true\n"
        f'name: "{q(name)}"\n'
        f'description: ""\n'
        f"---\n"
    )


def sync_site(course_org: str, cohort_org: str) -> int:
    """Regenerate the cohort's student-facing site from the live org state: the term's
    lecture and lab rows (released ones linked into the private content repos, planned
    ones marked not-yet-released), this year's assignments, and the display-only rows of
    the schedule (exams, special events, term dates)."""

    def build(_wd: Path) -> SitePlan:
        content_repos = discover_cohort_repos([cohort_org])
        release_sources = discover_release_sources(cohort_org, content_repos)
        # One row per (ordinal, kind): a week's lecture materials and its lab are separate
        # rows on the schedule, so a lab released into `labs/` never folds into the
        # lecture's row (and never shows up twice, on the schedule and the labs page).
        sources_by_row: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for repo, subpath, folder, n in release_sources:
            key = (str(n), row_kind(_source_section(repo, subpath)))
            sources_by_row.setdefault(key, []).append((repo, subpath, folder))
        assignments = discover_assignments(course_org)
        # A persistent course org holds per-year templates (assignment-*-fYYYY); a cohort
        # site should list only its own year's, matched on the cohort's fYYYY/sYYYY tag.
        tag = term_tag(cohort_org)
        if tag:
            assignments = [a for a in assignments if a.lower().endswith(tag)]
        # Which of them this cohort has actually been given - what gates their briefs. Read
        # from the cohort org rather than inferred from the plan, since the manual workflow
        # hands out with no `handout_datetime` pinned at all.
        handed_out = discover_handed_out_assignments(cohort_org)

        # Course identity comes from the course org metadata, semester from the cohort tag.
        meta = yaml_file(course_org, ".github", "dsl-course.yml")
        # Schedule is cohort-specific (it varies by year), so it comes from the cohort's
        # own classroom-config/schedule.yml. So do this cohort's instructors/TAs - read
        # from its own classroom-config/people.yml below, NOT the course org (whose
        # dsl-course.yml carries only the multi-year instructor cards).
        sched = schedule.load(cohort_org)
        # Every datetime on `sched` is already the cohort's wall clock (the parser converts
        # a written offset into the cohort timezone), so the renderers below just print it.
        start = sched.semester_start or _semester_start(cohort_org)
        # Every session row the plan declares, dated and with its destinations. A row that
        # discovery already found takes its date from here (else a synthesised weekly date
        # below); a planned row discovery has NOT found yet becomes a not-yet-released row,
        # so the whole term is on the schedule the day it is written rather than filling in
        # release by release. Discovery still leads: a folder released outside the plan
        # (the manual workflow, an off-plan extra) keeps its row whether or not it is here.
        # Every assignment this cohort has, from BOTH sides. Discovery finds the course
        # org's template repos, which is how one handed out off-plan still appears; the
        # plan's own `assignments:` entries are how one appears BEFORE its template is
        # staged - a term written in August names repos nobody has created yet, and the
        # schedule already publishes those dates. Without the plan side, a cohort could
        # write four assignments and see one row.
        #
        # Keyed on the COHORT-side name - the identity assign.py and collect.py use -
        # because two plan entries may cite one `course_source_repo`, and keying on the
        # repo folded them into a single row. Sorted by that name, so an assignment's page
        # keeps its URL when faculty add another mid-term.
        by_name: dict[str, tuple[str, tuple[str, schedule.AssignmentEntry] | None]] = {}
        for repo in assignments:
            hit = schedule.entry_for_repo(sched, repo)
            key = schedule.cohort_name(*hit) if hit else assignment_slug(repo)
            by_name.setdefault(key, (repo, hit))
        for plan_slug, plan_entry in sched.assignments.items():
            by_name.setdefault(
                schedule.cohort_name(plan_slug, plan_entry),
                (plan_entry.course_source_repo, (plan_slug, plan_entry)),
            )
        cohort_assignments = sorted(by_name.items())

        planned = planned_sessions(sched)
        rows = sorted(
            set(sources_by_row) | set(planned), key=lambda k: (int(k[0]), k[1])
        )
        # Every key of sources_by_row is in rows by construction, so this is arithmetic
        # rather than a scan.
        log_step(
            f"Syncing {cohort_org}/{pages_repo(cohort_org)}: {len(rows)} session row(s) "
            f"({len(rows) - len(sources_by_row)} not released yet), "
            f"{len(cohort_assignments)} assignment(s)"
        )

        # What a session row LINKS, out of everything it released - the default
        # folder-shaped listing unless this course declared an extension allowlist.
        allow = _link_extensions(meta)
        # The repos this cohort actually releases into - the only ones the index and
        # the syllabus lookup may read (see `_indexable_repos`).
        indexable = sorted(
            set(content_repos) & _indexable_repos(sched, release_sources)
        )

        def session_row(s: str, kind: str) -> str:
            """One row, from the plan where it has one and a synthesised weekly date where
            it does not. A row discovery found but the plan never named (the manual workflow,
            an off-plan extra) gets a stand-in row: the weekly fallback date and no declared
            name. It still appears, which is the point - and resolving the absence HERE is
            what keeps the renderer to one shape rather than a field-by-field fallback."""
            row = planned.get((s, kind)) or PlannedRow(
                when=start + timedelta(days=int(s) * 7)
            )
            return _lecture_entry(
                cohort_org,
                s,
                row,
                sources_by_row.get((s, kind), []),
                kind,
                allow,
                live_repos=frozenset(content_repos),
            )

        config = {}
        if meta.get("course_name"):
            config["course_name"] = str(meta["course_name"])
        if _semester_label(cohort_org):
            config["course_semester"] = _semester_label(cohort_org)
        if meta.get("course_code"):
            config["course_code"] = str(meta["course_code"])
        # The site's blurb. Declared once in the course org's dsl-course.yml and pushed to
        # every cohort site; left as the site repo has it when the course doesn't declare
        # one. Written as a single line whatever the source shape (see q).
        if meta.get("course_description"):
            config["course_description"] = str(meta["course_description"])
        # The footer's GitHub link (the site's only click-back). This is the COHORT site,
        # so it links the cohort org - where this year's materials and the students' own
        # repos live - never the course org (faculty-side) or the template's default.
        config["github_org"] = cohort_org

        # The display-only half of the schedule. `events:` rows render as what they
        # declared (exam or special event); an undated (TBC) one sorts at end-of-term.
        end = sched.semester_end or start + timedelta(weeks=15)
        event_entries = {
            f"{i + 1:02d}-{slug(e.label)}.md": _event_entry(e, end)
            for i, e in enumerate(sched.events)
        }
        # Every course has exams, so a schedule that names none still gets stub mid/end
        # dates of a ~15-week semester (bounded by semester_end when set) - a placeholder
        # faculty replace, rather than a schedule page with no exams on it at all.
        if not any(e.type == "exam" for e in sched.events):
            event_entries |= {
                "midterm.md": _exam_entry("MidTerm Exam", start + timedelta(weeks=8)),
                "final.md": _exam_entry("Final Exam", end),
            }
        # The term's own boundaries, when the schedule pins them.
        if sched.semester_start:
            event_entries["term-start.md"] = _term_date_entry(
                "Term starts", sched.semester_start
            )
        if sched.semester_end:
            event_entries["term-end.md"] = _term_date_entry(
                "Term ends", sched.semester_end
            )

        return SitePlan(
            config=config,
            # People: this cohort's own classroom-config/people.yml (instructors AND TAs -
            # the per-cohort teaching team; schema in
            # templates/classroom-config/people.yml), else its instructors team.
            files={
                "README.md": site_readme(cohort_org, cohort=True),
                "_data/people.yml": people_yaml(
                    cohort_org,
                    yaml_file(cohort_org, "classroom-config", "people.yml"),
                    edit_at=f"{cohort_org}/classroom-config/people.yml",
                ),
                "_data/nav.yml": nav_yaml(cohort=True),
                # The catch-all index behind the All Materials tab: every released file,
                # including what no session ordinal covers - across the repos faculty
                # actually release into, never everything discovery failed to exclude.
                "_data/materials.yml": _materials_index(
                    cohort_org,
                    indexable,
                    # Absent when the cohort has no syllabus, so the home page shows no
                    # line rather than an empty one.
                    syllabus=_released_syllabus(cohort_org, indexable),
                ),
                **theme_pages(cohort=True),
                # The course-specific layouts, includes and stylesheet - shipped
                # from templates/site/, not from the shared theme, so a change to
                # how a session renders is tested against the generator that
                # writes its front matter before any site sees it.
                **site_templates(),
            },
            # Assignment handout/due dates come from schedule.yml when set (keyed on the
            # assignment slug), else a synthesised fortnightly cadence.
            collections={
                "_lectures": {
                    row_file(s, kind): session_row(s, kind) for s, kind in rows
                },
                # Named by the cohort-side name, ordinal from the position in the full
                # list, so every assignment keeps its URL for the whole term. A pending one
                # is a placeholder rather than an absence - see `_assignment_entry`.
                "_assignments": {
                    f"{i + 1:02d}-{name}.md": _assignment_entry(
                        course_org,
                        cohort_org,
                        repo,
                        *_assignment_dates(hit, start + timedelta(days=(i + 1) * 14)),
                        found=hit,
                        handed_out=handed_out,
                    )
                    for i, (name, (repo, hit)) in enumerate(cohort_assignments)
                },
                "_events": event_entries,
            },
            commit="site: sync from org structure",
        )

    # `--all-cohorts` loops this in one process, and the index reads EVERY release
    # destination's tree, not just the session-bearing ones - so the memo would pin a few
    # hundred KB per repo for the whole run. Cleared on ENTRY rather than on the way out:
    # most cohorts in a daily cron are already up to date and return early, so an exit-path
    # clear ran on the rare path and never on the common one. Keys include the org, so this
    # is purely about memory, never staleness.
    _repo_tree.cache_clear()
    return sync_site_repo(cohort_org, build)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("sync")
    ps.add_argument("--course-org", required=True)
    ps.add_argument(
        "--cohort-org", default=None, help="One cohort; omit with --all-cohorts"
    )
    ps.add_argument(
        "--all-cohorts",
        action="store_true",
        help="Sync every registered cohort (a course-level change, e.g. dsl-course.yml)",
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
    if args.cmd != "public-sync" and not (args.all_cohorts or args.cohort_org):
        log_err("pass --cohort-org or --all-cohorts.")
        return 1
    # A read helper that couldn't reach the API raises RuntimeError; a config file with
    # one bad indent raises yaml.YAMLError out of load_yaml_config (people.yml is
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
        if args.all_cohorts:
            rc = 0
            for cohort in discover_cohorts(args.course_org):
                # One cohort's raised failure (an unreachable API, a people.yml that
                # doesn't parse) must not skip every LATER cohort's site on the 06:00
                # cron - log it, mark the batch failed, and carry on. The same per-cohort
                # isolation PR #151/#146 applied to the nightly refresh and the scheduler.
                try:
                    rc |= sync_site(args.course_org, cohort)
                except Exception as exc:
                    log_err(
                        f"site sync for {cohort} failed ({type(exc).__name__}): {exc}"
                    )
                    rc |= 1  # accumulate, don't clobber prior cohorts' status bits
            return rc
        # --cohort-org arrives on the automatic path straight from a repository_dispatch's
        # `client_payload.cohort_org`, written by whoever holds a cohort's DSL_BOT_TOKEN - a
        # lower trust tier than the course org. Naming SOMEONE ELSE'S cohort would rebuild
        # that cohort's site from this dispatch. The registry is the authority on which
        # cohorts this course org owns, so an unregistered name is refused. Checked here
        # rather than inside sync_site, because every internal caller (a release, the
        # scheduler, the --all-cohorts loop above) already passes a cohort it read FROM the
        # registry - only the CLI takes one from outside. Casefold: GitHub org names are
        # case-insensitive.
        # An EMPTY registry authorises nothing. It used to short-circuit the whole check,
        # so a course org that had never registered a cohort - or whose registry failed to
        # parse to anything - accepted any org name a dispatch cared to name.
        registered = discover_cohorts(args.course_org)
        if args.cohort_org.casefold() not in {c.casefold() for c in registered}:
            listed = ", ".join(sorted(registered)) or "nothing"
            log_err(
                f"{args.cohort_org} is not registered under {args.course_org} "
                f"({COHORTS_PATH} lists {listed}) - refusing to sync its site."
            )
            return 1
        return sync_site(args.course_org, args.cohort_org)
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
