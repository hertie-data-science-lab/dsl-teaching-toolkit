"""dsl-course site -- regenerate a course/cohort website from the live org structure.

Two sites, two audiences, one Jekyll template (course-website-template):

- **cohort site** (`<cohort>.github.io`, `sync_site`) - student-facing. Its lecture links
  point at the cohort's PRIVATE content repos (wherever a release actually landed each
  section - see `seed.discover_release_sources`), so they 404 for non-members (the gate is
  deliberate). Regenerates `_lectures/`, `_assignments/`, `_events/` from the release state.
  Releases call it; the Sync site action runs it on demand.

- **course site** (`<course-org>.github.io`, `sync_public_site`) - PUBLIC open courseware,
  opt-in. The course `course-materials-*` repos are private, so public links to them 404;
  instead this HOSTS the shared files in the public site repo (Jekyll serves any path not
  starting with `_`) and links to site-relative URLs. Every section the source repo has
  (`lectures`, `labs`, ... - discovered, not hardcoded) is hosted; `readings` is special,
  being either a text-only list (`reading-list`) or hosted+linked (`actual-readings`).
  Session materials only - no assignments/events. Opt-in: the first publish is a manual
  click, which persists its settings into the site repo (`_publish-config.yml`); the daily
  cron then re-syncs from those (`public-sync` with no source args).

Pushing the site repo redeploys it either way.

Usage:
    python3 -m dsl_course.site sync --course-org TEST-HERTIE-COURSE \\
        --cohort-org TEST-HERTIE-COHORT-f2026
    python3 -m dsl_course.site public-sync --course-org TEST-HERTIE-COURSE \\
        --source-repo course-materials-f2026 --readings-mode reading-list
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from urllib.parse import quote

import yaml

from . import schedule, seed
from .assign import assignment_slug
from .discovery import discover_handed_out_assignments
from .utils import (
    GIT_ENV,
    READING_OVERLAY_NAMES,
    _acting_login,
    active_today,
    discover_sections,
    find_session_dir,
    get_default_branch,
    get_file_content,
    gh,
    git,
    is_missing_resource,
    load_yaml_config,
    log,
    log_err,
    log_ok,
    log_step,
    repo_exists,
    repo_is_archived,
    repo_tree,
    session_number,
    term_tag,
)

# Public course site: served folder for the hosted section files.
PUBLIC_MATERIALS_DIR = "public-materials"
# The OVERLAY - a session's optional prose reading list, identified by NAME.
#
# By name and not by extension, which is what this used to be. An extension test called every
# `.md`/`.txt`/`.bib` in a readings folder "the reading list" and inlined it into the page, so
# a faculty member who uploaded `lecture-notes.md` or `handout.txt` as an actual READING found
# it swallowed into the prose instead of listed as a download, and a `.bib` dumped raw BibTeX
# onto the page. No rule keyed on file type can treat every file type alike.
#
# So: one filename is the overlay; everything else in the folder is a file, whatever it is.
# The overlay is optional - a session with only PDFs needs nothing written - and additive: it
# renders ABOVE the file list, never instead of it (see `_readings_block`).
# `READING_OVERLAY_NAMES` lives in utils, beside the other generated faculty-side filenames,
# because `scaffold` seeds the file and this module and `syllabus` match on it. Its name
# equals `READINGS_SECTION` below by coincidence - a file stem and a folder name - not by
# derivation.
# The one section with copyright semantics of its own (--readings-mode); every OTHER
# section a repo happens to have is published as files, whatever it's called.
READINGS_SECTION = "readings"
# The settings of the last manual publish, committed into the site repo so the daily cron
# can re-sync unattended. Leading `_`, so Jekyll ignores it rather than serving it.
PUBLISH_CONFIG = "_publish-config.yml"
_GIT_ENV = GIT_ENV


def _cohort_tag(cohort_org: str) -> str | None:
    """The fYYYY / sYYYY semester tag in a cohort org name (e.g. 'f2026'), or None."""
    return term_tag(cohort_org)


def _semester_start(cohort_org: str) -> date:
    """Best-effort semester start from a fYYYY / sYYYY tag (for schedule ordering)."""
    tag = _cohort_tag(cohort_org)
    if tag:
        return date(int(tag[1:]), 9 if tag[0] == "f" else 2, 1)
    return date(2026, 1, 1)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "exam"


def _semester_label(cohort_org: str) -> str:
    """fYYYY -> 'Fall YYYY', sYYYY -> 'Spring YYYY' (for site.course_semester)."""
    tag = _cohort_tag(cohort_org)
    return f"{'Fall' if tag[0] == 'f' else 'Spring'} {tag[1:]}" if tag else ""


def _q(value: str) -> str:
    """Quote-safe a value for a ONE-LINE double-quoted YAML scalar: escape the two
    characters that are special inside one (`\\` and `"`), and fold newlines away - a
    multi-line value (a faculty `>` block in dsl-course.yml, say) would otherwise write a
    raw newline mid-scalar and break the file it lands in."""
    return " ".join(value.replace("\\", "\\\\").replace('"', "'").split())


def _liquid_raw(text: str) -> str:
    """Fence faculty-written text that is inlined verbatim into a Jekyll document. A `{{`
    or `{%` in it would otherwise run as Liquid, and a malformed tag fails the whole build;
    `{% raw %}` renders it literally."""
    return f"{{% raw %}}\n{text}\n{{% endraw %}}"


def _block(key: str, text: str) -> str:
    """A multi-line front-matter value as a YAML literal block - faculty-written text (a
    reading list) inlined verbatim, rather than folded onto one line by `_q`.

    The indentation indicator (`|2`) is deliberate: without it YAML takes the block's
    indentation from its first non-empty line, so a list that happens to start indented
    would make every following line look like the end of the block and break the whole
    file. Tabs are expanded for the same reason. Front matter is data, not a Liquid
    template, so unlike the body route (`_liquid_raw`) a `{{` in the text needs no fence."""
    lines = text.expandtabs(4).rstrip().splitlines()
    body = "\n".join(f"  {ln}" if ln.strip() else "" for ln in lines)
    return f"{key}: |2\n{body}\n"


def _set_config(text: str, key: str, value: str) -> str:
    """Replace a top-level `key: ...` line in _config.yml, preserving the rest.

    The value is always written as a one-line double-quoted scalar (see `_q`). Any
    indented continuation lines are consumed with it, so replacing a key someone left as
    a `>`/`|` block scalar doesn't strand its body as invalid YAML.

    A key the template's `_config.yml` doesn't have is a no-op - logged, so template drift
    (a key the code sets that the site theme dropped) is visible rather than silent."""
    new, n = re.subn(
        rf"(?m)^({re.escape(key)}:[ \t]*).*(?:\n[ \t]+\S.*)*$",
        lambda m: f'{m.group(1)}"{_q(value)}"',
        text,
        count=1,
    )
    if n == 0:
        log(f"  (_config.yml has no `{key}:` key - not written; template drift?)")
    return new


# The session pages. Their CONTENT is a theme layout (dsl-jekyll-theme's
# `_layouts/lectures.html`, `labs.html`, `readings.html`, `materials.html`,
# `assignments.html`), so these are the front matter that points at one plus the page's own
# intro line. Owned here, not left to the site template, because a template edit only
# reaches orgs created after it - the rendering used to live as inline Liquid in each site
# repo, and by the time it needed changing there were seven live sites to hand-patch. Every
# later change to how sessions render now ships from the theme alone.
#
# `_overwritten_edits` still reports a hand edit these replace, as it does for any other
# generated surface. (It does NOT fire on the first sync that takes them over: the page a
# site was generated with was authored by the token account, which reads as a machine.)
@dataclass(frozen=True)
class _ThemePage:
    """One generated page and its nav entry, declared together.

    Together deliberately: the tab bar and the pages it points at were two structures
    holding the same permalinks and titles, kept consistent only by both being written in
    the same breath. The day someone generated the nav for the public site too, its
    Readings tab would have pointed at a page that site never gets - a 404 nobody edited
    into existence. One row per page makes that impossible.

    Each page carries its OWN access sentence, because they genuinely differ: readings are
    a public citation list over gated files, everything else is gated outright. `gated_note`
    is what a COHORT site says; `open_note` what the public open-courseware site says
    instead, where the same files are published on purpose."""

    file: str
    layout: str
    title: str
    permalink: str
    icon: str
    gated_note: str
    open_note: str = ""
    # Pages only a COHORT site has. A public course site has no cohort repos to index, so
    # it keeps `/materials/` as the readings page it has always been.
    cohort_only: bool = False


_THEME_PAGES = (
    _ThemePage(
        "lectures.md",
        "lectures",
        "Lectures",
        "/lectures/",
        "fas fa-book-reader",
        "Lecture slides are accessible to enrolled students.",
        "Lecture slides by session.",
    ),
    _ThemePage(
        "labs.md",
        "labs",
        "Labs",
        "/labs/",
        "fas fa-flask",
        "Lab materials are only accessible to enrolled students.",
        "Lab materials by session.",
    ),
    _ThemePage(
        "readings.md",
        "readings",
        "Readings",
        "/readings/",
        "fas fa-book",
        "Citation lists are public; the files themselves are accessible to enrolled "
        "students.",
        "Readings by session.",
        cohort_only=True,
    ),
    _ThemePage(
        "assignments.md",
        "assignments",
        "Assignments",
        "/assignments/",
        "fas fa-user-graduate",
        # The layout says "No assignments released yet." when the collection is empty, so
        # this line is only ever shown beside an actual list.
        "Assignments are only accessible to enrolled students.",
        "Assignments by hand-out date.",
    ),
    _ThemePage(
        "materials.md",
        "materials",
        "All Materials",
        "/materials/",
        "fas fa-folder-open",
        # Deliberately not "everything released": a cohort org also holds each student's
        # private submission repo, which this must never list. See `_indexable_repos`.
        "Every course material released to this cohort, session or not. Course materials "
        "are only accessible to enrolled students.",
        cohort_only=True,
    ),
)

# The public site's `/materials/` - the readings page under its original name, which is
# where that site has always kept them.
_PUBLIC_MATERIALS_PAGE = _ThemePage(
    "materials.md",
    "readings",
    "Materials",
    "/materials/",
    "fas fa-book",
    "",
    "Readings by session.",
)

# Tabs the theme provides rather than generating: the template's own index.md and the
# Schedule, which is driven by the collections. Everything else is a row above.
_STATIC_NAV = (
    ("Home", "/", "fa fa-home fa-lg"),
    ("Schedule", "/schedule/", "fas fa-calendar-alt"),
)


def _site_pages(cohort: bool) -> tuple[_ThemePage, ...]:
    """The pages this kind of site gets, in nav order. A public course site drops the two
    cohort-only pages and keeps `/materials/` as its readings page."""
    if cohort:
        return _THEME_PAGES
    return tuple(pg for pg in _THEME_PAGES if not pg.cohort_only) + (
        _PUBLIC_MATERIALS_PAGE,
    )


def _theme_pages(cohort: bool) -> dict[str, str]:
    """The `{path: content}` for the pages whose rendering lives in the theme."""
    return {
        pg.file: (
            f"---\nlayout: {pg.layout}\ntitle: {pg.title}\n"
            f"permalink: {pg.permalink}\n---\n\n"
            + ((pg.gated_note if cohort else pg.open_note) or pg.open_note)
            + "\n"
        )
        for pg in _site_pages(cohort)
    }


def _nav_yaml(cohort: bool) -> str:
    """`_data/nav.yml` - the site's tab bar (the theme's `_includes/nav.html` reads it),
    built from the same page table, so a tab can never point at a page this site lacks."""
    rows = list(_STATIC_NAV) + [
        (pg.title, pg.permalink, pg.icon) for pg in _site_pages(cohort)
    ]
    body = "\n\n".join(
        f"- url: {url}\n  name: {name}\n  icon_class: {icon}"
        for name, url, icon in rows
    )
    return (
        "# Generated by `python3 -m dsl_course.site sync`. Rewritten on every sync - add a\n"
        "# page of your own as a file in the repo and link it from `index.md` instead.\n"
        "items:\n" + body + "\n"
    )


def _site_repo(org: str) -> str:
    """The GitHub Pages org site repo for an org - pushing it redeploys the site."""
    return f"{org.lower()}.github.io"


def _yaml_file(org: str, repo: str, path: str) -> dict:
    """A YAML config file from a repo as a mapping - `{}` when it is genuinely absent or
    empty (nothing declared: the site renders its defaults, which is correct).

    A file that exists but does NOT parse - or parses to a list/scalar - raises out of
    here (via load_yaml_config), to the per-cohort isolation the callers already have. It
    used to be coerced to `{}`, so one bad indent in a cohort's people.yml republished the
    site with the whole teaching team's cards wiped, green - exactly the failure
    `_team_people` next door is hardened against."""
    return load_yaml_config(org, repo, path) or {}


def _team_people(course_org: str, team: str) -> list[tuple[str, str, str]]:
    """(display-name, avatar-url, profile-url) for each member of a course-org team.

    A missing team (404) is an empty list - the site falls back gracefully. Any OTHER
    failure RAISES rather than returning `[]`: a swallowed failure wrote `instructors: []`
    and republished the site with the whole teaching team wiped. Fail-loud, like
    get_team_members - and the same rule per MEMBER: a deleted account (404) is one card
    the site can't show, but a transient failure on one lookup must not quietly drop that
    instructor's card from the republished site.

    The account running the sync is never a card: the bot sits in `instructors` for the
    access it needs, and it rendered on the public site as a member of the teaching team."""
    code, out = gh(
        "api",
        "--paginate",
        f"orgs/{course_org}/teams/{team}/members",
        "--jq",
        ".[].login",
    )
    if code != 0:
        if is_missing_resource(out):
            return []  # no such team - fall back, don't wipe
        raise RuntimeError(
            f"could not read the members of {course_org}/{team}: {out[:200]}"
        )
    people = []
    # None means the login could not be read; the gh-auth fail-fast guard has already run,
    # so that is not this function's problem - it just excludes nobody.
    acting = _acting_login()
    for login in out.splitlines():
        login = login.strip()
        if not login:
            continue
        if acting and login.lower() == acting.lower():
            log(
                f"  (skipping the sync's own account {login} - not a person on the site)"
            )
            continue
        c, u = gh(
            "api",
            f"users/{login}",
            "--jq",
            "[(.name // .login), .avatar_url, .html_url] | @tsv",
        )
        if c != 0:
            # A 404 is a genuinely gone account: one card fewer, said out loud rather than
            # silently. Anything else is a read failure, and dropping the card on it would
            # republish the site one instructor short with no sign anything went wrong.
            if is_missing_resource(u):
                log(f"  (no GitHub profile for {login} - no card on the site)")
                continue
            raise RuntimeError(
                f"could not read the GitHub profile of {login}: {u[:200]}"
            )
        if not u.strip():
            log(f"  (empty GitHub profile for {login} - no card on the site)")
            continue
        parts = (u.rstrip("\n").split("\t") + ["", "", ""])[:3]
        people.append(tuple(parts))
    return people


# A person entry mixes two concerns: who gets the GitHub grant, and what the website
# card shows. These keys drive the grant and are never rendered; everything else is
# display and is passed through to `_data/people.yml` as-is.
ACCESS_ONLY = ("github_handle", "start", "end")
# Our config spelling -> the key the Jekyll theme reads.
CARD_ALIASES = {"photo": "profile_pic", "url": "webpage"}
# Leading keys, so a generated file has a stable, readable order.
CARD_ORDER = ("name", "profile_pic", "webpage", "title")


def _card(entry: dict) -> dict:
    """One person entry -> the card dict written into `_data/people.yml`: drop the
    access-only keys, rename `photo`/`url` to the theme's names, keep everything else
    the course declared. Ordered by CARD_ORDER first, then the extras alphabetically."""
    card = {
        CARD_ALIASES.get(k, k): "" if v is None else str(v)
        for k, v in entry.items()
        if k not in ACCESS_ONLY
    }
    ordered = {k: card[k] for k in CARD_ORDER if k in card}
    ordered.update({k: card[k] for k in sorted(card) if k not in ordered})
    return ordered


def _people_from_meta(meta: dict) -> tuple[list[dict], list[dict]] | None:
    """Declared people from a `people:` block - either the COURSE org's
    `.github/dsl-course.yml` (course site: instructors only, TAs are never declared
    there) or a cohort's own `classroom-config/people.yml` (cohort site: instructors
    AND TAs). Same schema either way.

    Returns `(instructors, teaching_assistants)` as lists of **card dicts** keyed the way
    the Jekyll theme reads them, for entries active today (per optional start/end dates)
    that also declare a display `name`; or None when there is no `people:` block at all
    (then fall back to the GitHub teams). Schema (templates/course/people-header.yml +
    people-cards.yml for the course org's block, templates/classroom-config/people.yml
    for a cohort's):

        people:
          instructors:
            - github_handle: ...
              start: ...
              end: ...
              name: ...
              photo: <img-url>
              url: <bio-link>
              title: ...
          teaching_assistants:
            - github_handle: ...
              name: ...
              photo: ...
              url: ...
              title: ...

    Every declared field is passed through to the card: `photo`/`url` are renamed to the
    theme's `profile_pic`/`webpage`, ACCESS_ONLY keys are dropped (they govern the GitHub
    grant, not the display), and anything else a course chooses to add rides along
    verbatim, so a new field needs a theme change but no change here.
    """
    people = meta.get("people") if isinstance(meta, dict) else None
    if not isinstance(people, dict):
        return None
    today = date.today().isoformat()

    def rows(key: str) -> list[dict]:
        out = []
        for p in people.get(key) or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            if not active_today(p.get("start"), p.get("end"), today):
                continue
            out.append(_card(p))
        return out

    return rows("instructors"), rows("teaching_assistants")


def _people_yaml(
    org: str, meta: dict | None = None, *, edit_at: str, include_tas: bool = True
) -> str:
    """Build _data/people.yml. Prefer the declared `people:` block in the supplied meta
    (the course org's dsl-course.yml for the course site, a cohort's classroom-config/
    people.yml for the cohort site); else fall back to the GitHub `instructors` team of
    `org` (GitHub display name + avatar + profile link).

    `edit_at` names the file a human should edit instead - every sync rewrites this one,
    and an instructor who edited the generated file lost the change on the next run, so
    the header says so and points at `edit_at` in BOTH modes.

    `include_tas=False` (the course site) drops TA cards entirely - TAs are cohort-only,
    so the multi-year open-courseware site shows instructors only. Instructors and TAs
    share one GitHub team (there's no separate `teaching-assistants` team - see
    bootstrap_course.FACULTY_TEAMS), so the fallback can't distinguish TAs from
    instructors; declare a `people:` block to get separate TA cards."""
    override = _people_from_meta(meta or {})
    if override is not None:
        instructors, tas = override
        note = "declared in the `people:` block"
    else:
        instructors = [
            {"name": n, "profile_pic": p, "webpage": w}
            for n, p, w in _team_people(org, "instructors")
        ]
        tas = []
        note = "auto-generated from the org's instructors team"
    if not include_tas:
        tas = []

    def block(items: list[dict]) -> str:
        if not items:
            return " []"
        rows = []
        for card in items:
            # The theme's three core keys are always emitted, empty or not (a card the
            # theme can't find `profile_pic` on renders differently from one where it is
            # blank); optional fields appear only when they carry something.
            fields = [
                f'{k}: "{_q(card.get(k, ""))}"'
                for k in ("name", "profile_pic", "webpage")
            ] + [
                f'{k}: "{_q(v)}"'
                for k, v in card.items()
                if k not in ("name", "profile_pic", "webpage") and v != ""
            ]
            rows.append("  - " + "\n    ".join(fields))
        return "\n" + "\n".join(rows)

    featured = instructors[0] if instructors else {"name": "Course staff"}
    return (
        "# GENERATED by the DSL course sync - do not edit this file. Every sync rewrites\n"
        f"# it and your change is lost. Edit {edit_at} instead.\n"
        f"# These cards are {note}.\n\n"
        f'instructor:\n  name: "{_q(featured.get("name", ""))}"\n'
        f'  profile_pic: "{_q(featured.get("profile_pic", ""))}"\n'
        f'  webpage: "{_q(featured.get("webpage", ""))}"\n\n'
        f"instructors:{block(instructors)}\n\n"
        f"teaching_assistants:{block(tas)}\n"
    )


@cache
def _repo_tree(org: str, repo: str) -> tuple[str, tuple[str, ...]]:
    """(default branch, every blob path in it) for a repo - one recursive tree fetch,
    memoised for the run. A cohort site asks for the files of EVERY released session, and
    they nearly all live in the same repo, so without the memo the identical tree got
    fetched once per session. Paths come back sorted, so callers filtering them keep a
    stable diff.

    Unbounded cache: this is a one-shot CLI process, and the trees it reads are the
    handful of repos one cohort released into.

    The fetch itself is utils.repo_tree (shared with discovery's directory-side twin, so
    the absent-vs-failed discrimination is written once): a genuinely absent/empty tree is
    `()` and the caller simply finds no files, while any other failure RAISES rather than
    reporting an empty tree - swallowed, it republished the site with every material link
    stripped."""
    branch = get_default_branch(org, repo)
    return branch, repo_tree(org, repo, branch, "blob")


def _source_prefix(subpath: str, folder: str) -> str:
    """Where a discovered session folder sits in its repo - `subpath/folder`, or the bare
    folder when the release landed at the repo root. Stated once: three callers need it,
    and a fourth copy of the rule is how they come to disagree (same argument
    `_deploy_dest` makes for the deploy side)."""
    return f"{subpath}/{folder}" if subpath else folder


def _source_section(repo: str, subpath: str) -> str:
    """The section a DISCOVERED release source belongs to - its subpath, or the repo itself
    when the folder sits at the root. The read-side twin of `_deploy_section`, which names
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
    seed.discover_release_sources to match a session's ordinal prefix), at `subpath`
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


def _is_reading_overlay(name: str) -> bool:
    """Is this path a session's optional prose reading list (`READINGS.md`, `.txt`, `.bib`)?

    The ONE test that decides prose-vs-file for a readings folder, by NAME rather than by
    extension - see `READING_OVERLAY_NAMES` in utils for why that distinction is the whole point.
    Takes a path or a bare name; only the last segment is read, so it works on the repo-tree
    paths, the release-relative names and the local filenames its callers each hold."""
    return name.rsplit("/", 1)[-1].lower() in READING_OVERLAY_NAMES


def _readings_block(names: list[str], read_overlay: Callable[[str], str | None]) -> str:
    """A session's reading list: its overlay prose, then every OTHER file by name.

    THE rule, in one place, because its three readers - the cohort site, the public course
    site and the generated syllabus - each used to decide it for themselves and disagreed. A
    folder holding only PDFs rendered as links on one site, as a name list on another, and as
    nothing whatsoever in the syllabus, where a session came out an empty heading.

    Additive, never suppressive. The overlay is prose a reader wants first, so it leads; the
    files are what a student downloads, so they always follow. Neither hides the other:
    - files only - the file list IS the reading list, and nobody had to write anything;
    - overlay only - the URL-only week, one line of markdown and no citation format imposed;
    - both - prose on top, files beneath.

    `names` is every path in the session's readings folder; `read_overlay` reads one of them,
    and is a callable because its two callers hold different transports - the public site a
    local file, the syllabus a blob in the course org. Lazy, so only the overlay is ever
    fetched: reading eagerly would pull every PDF over the API just to find the prose.

    Raw filenames, deliberately: deriving "Blitzstein 2019 ch.1" from `blitzstein-ch1.pdf`
    would be inventing a citation. Faculty who want that write it in the overlay.

    The overlay is never ALSO listed as a download - its content is already on the page."""
    prose, files = [], []
    for name in sorted(names):
        if _is_reading_overlay(name):
            text = (read_overlay(name) or "").strip()
            if text:
                prose.append(text)
        else:
            files.append(f"- {name.rsplit('/', 1)[-1]}")
    return "\n\n".join(prose + (["\n".join(files)] if files else []))


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
    return [(n, u) for n, u in links if not _is_reading_overlay(n)]


# How far the inlined reading list's own headings are pushed down, so they nest under the
# session heading the page puts above them. A reading file written in the Hertie syllabus
# shape opens with `# Session 1 readings` and sub-heads `## Required Readings`; at their
# written levels those outrank the page's own `<h2>Session 1`, which is exactly backwards.
_HEADING_SHIFT = 2


def _demote_headings(text: str, shift: int = _HEADING_SHIFT) -> str:
    """Push every ATX heading in faculty-written markdown down `shift` levels.

    `shift` is the caller's, because the heading it has to nest under differs: the site puts
    a session at `<h2>`, the syllabus generator at `<h3>`.

    Only outside fenced code blocks: a `# comment` on the first line of a shell example is
    not a heading, and deepening it would rewrite the example. A heading also needs
    whitespace after its hashes, so `#hashtag` stays prose. Levels clamp at 6, which is as
    deep as HTML goes."""
    out, fenced = [], False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[hashes:]
            if rest[:1] in (" ", "\t", ""):
                line = "#" * min(hashes + shift, 6) + rest
        out.append(line)
    return "\n".join(out)


def _released_reading_list(cohort_org: str, sources: list[tuple[str, str, str]]) -> str:
    """The prose a session row inlines: the text of the OVERLAY released into its `readings`
    section (`READINGS.md`), verbatim.

    Prose ONLY here, unlike the public site and the syllabus (`_readings_block`), which name
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
            if not _is_reading_overlay(name):
                continue
            text = (
                get_file_content(cohort_org, repo, f"{prefix}/{name}") or ""
            ).strip()
            if text:
                parts.append(_demote_headings(text))
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
    `_deploy_section` - head-of-path, else the repo - one level down."""
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
        lines.append(f'{indent}- name: "{_q(e.label)}"')
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
    release can come from the manual button with a typed path, so there is no declaration to
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
        rows_out.append(f'  - name: "{_q(section)}"')
        rows_out.append(f"    files: {sum(e.files for e in entries)}")
        rows_out.append("    entries:")
        rows_out.extend(_emit_entries(entries, "      "))
    doc_rows = [
        line
        for e in sorted(docs.values(), key=lambda e: e.name.lower())
        for line in (f'  - name: "{_q(e.name)}"', f"    url: {e.url}")
    ]
    header = (
        "# Generated by `python3 -m dsl_course.site sync` - every released file, nested\n"
        "# as its repo has it. Edit nothing here; it is rewritten on every sync.\n"
    ) + (f"syllabus: {syllabus}\n" if syllabus else "")
    # Stated, not reached: `sections: []` is the empty index, the same shape
    # `_links_block` uses for a row with nothing to link.
    # Documents first, then the sections - the order the page renders them in.
    body = "documents:\n" + "\n".join(doc_rows) + "\n" if doc_rows else ""
    body += "sections:\n" + "\n".join(rows_out) if rows_out else "sections: []"
    return header + body + "\n"


# A week's lecture and its lab are two separate rows of the theme's schedule table, and
# the labs page selects `type: lab` out of the `_lectures` collection - so which row a
# released folder lands in is decided by its section (the directory it was released into),
# never by anything faculty declare. Everything that isn't `labs` is lecture material.
LAB_SECTION = "labs"
_ROW_NOUN = {"lecture": "Session", "lab": "Lab"}


def _row_kind(section: str) -> str:
    """The schedule-row type a released section belongs to: 'lab' or 'lecture'."""
    return "lab" if section == LAB_SECTION else "lecture"


def _row_file(session: str, kind: str) -> str:
    """The collection filename for one session row - lecture and lab rows of the same
    week are distinct files (`session-02.md`, `lab-02.md`) in the same collection."""
    return f"{'lab' if kind == 'lab' else 'session'}-{int(session):02d}.md"


def _singular(label: str) -> str:
    """A section label as a single-item noun for a link name: 'lectures' -> 'lecture',
    'labs' -> 'lab', 'faq' -> 'faq'. Sections are free-form directory names, so a bare
    `[:-1]` chopped a real character off every label that isn't a plural ('faq' -> 'fa').
    Deliberately no inflection library: strip one trailing 's', else leave it alone."""
    return label[:-1] if len(label) > 1 and label.endswith("s") else label


def _iso_when(when: date | datetime, fallback_time: str = "09:00:00") -> str:
    """`when` as the offset-free local ISO stamp a front-matter `date:` wants.

    A datetime from schedule.yml is ALREADY in the cohort timezone - the parser converts
    an entry written with an explicit offset (`...T10:00+00:00`) into the cohort's own
    clock - so printing it needs no conversion here, only the offset dropped. A bare date
    (a synthesised fallback, or a whole-day schedule entry) has no clock and gets
    `fallback_time`."""
    if isinstance(when, datetime):
        return when.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{when.isoformat()}T{fallback_time}"


def _links_block(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """A front-matter `links:` block from `(section-label, [(file-name, url), ...])` pairs
    in publication order, each link named `<section-singular> - <file>` (both sites label
    them identically), or `links: []` when there is nothing to link."""
    rows = []
    for label, pairs in sections:
        for name, url in pairs:
            # Route the name through _q (escapes `\` AND `"`): a filename with a backslash
            # (`\sigma.pdf`) is an invalid YAML escape and fails the whole Jekyll build.
            safe = _q(f"{_singular(label)} - {name}")
            rows.append(f'    - url: {url}\n      name: "{safe}"')
    return ("links:\n" + "\n".join(rows)) if rows else "links: []"


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
    a paragraph (sometimes two), and `_q` folds every newline away, so a one-line scalar
    silently ran two paragraphs together. Empty stays absent rather than blank, so the
    theme can test for it."""
    if not text.strip():
        return ""
    if "\n" in text.strip():
        return _block("description", text)
    return f'description: "{_q(text)}"\n'


def _lecture_entry(
    cohort_org: str,
    session: str,
    row: _PlannedRow,
    sources: list[tuple[str, str, str]],
    kind: str = "lecture",
    allow: frozenset[str] = frozenset(),
    live_repos: frozenset[str] = frozenset(),
) -> str:
    """One row of a teaching week: the lecture (`kind='lecture'`) or the lab
    (`kind='lab'`), which the theme renders as separate schedule lines out of the same
    `_lectures` collection.

    `sources` is (repo, subpath, folder) triples already confirmed (by
    seed.discover_release_sources) to hold this exact session - callers pass only the
    sources known to match, so every call here is a real hit, not a probe.

    `row` is what the PLAN says about this session (`_PlannedRow`): when the class happens,
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
    title = f"{_ROW_NOUN[kind]} {session}"
    subtitle, description = row.subtitle, row.description
    reading_list = ""
    if sources:
        flags = ""
        links = _links_block(
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
        links = _links_block([])
        where = ", ".join(_dest_link(cohort_org, d, live_repos) for d in row.dests)
        body = (
            f"Materials for {title.lower()} are not released yet"
            + (f" - they will appear in {where} when released" if where else "")
            + "."
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
        f"date: {_iso_when(row.when)}\n"
        f'title: "{title}"\n'
        + (f'subtitle: "{_q(subtitle)}"\n' if subtitle else "")
        + _describe(description)
        + flags
        + (_block("reading_list", reading_list) if reading_list else "")
        + f"{links}\n"
        f"---\n"
        f"{body}\n"
    )


def _assignment_entry(
    course_org: str,
    repo: str,
    when: date | datetime,
    handout: datetime | None = None,
    sched: schedule.Schedule | None = None,
    handed_out: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> str:
    """An assignment's page, plus the two schedule rows it drives: the entry's own
    `date:` is the "released!" row and its `due_event:` sub-block the due row.

    `when` is the due date (a real one from schedule.yml, or a synthesised fallback);
    `handout` the scheduled provisioning moment when there is one. A handout dates the
    released-row where it belongs - at hand-out, not at the deadline - while an
    unscheduled assignment keeps both rows on the due date (the only date known).

    `sched` supplies the cohort-side repo name: resolved exactly as assign.py / collect.py
    do (`cohort_dest_repo` or the schedule slug when the schedule keys this repo, else the
    course repo minus its -fYYYY/-sYYYY tag), so the page names the repo students actually
    get. Deriving it from the course repo alone named the wrong repo - and titled the page
    wrong - whenever an entry set `cohort_dest_repo`.

    An assignment NOT YET HANDED OUT withholds its brief, the way an unshipped session
    withholds its materials (`_lecture_entry`): the row appears from the day the template
    repo exists, but its body says so and flags `unreleased: true` rather than inlining the
    README. The template repo exists from the moment faculty write the assignment - weeks
    before it hands out - so publishing its README on sight put the whole brief on the
    cohort site, which is PUBLIC, while the scheduler was still correctly holding the
    student repos back.

    Handed out means EITHER of two things, and it takes both being false to withhold:

    - `handed_out` holds this assignment's cohort-side name - a frozen cohort template repo
      exists (`discovery.discover_handed_out_assignments`), so students have their repos
      whatever route fired it. This is the same "what actually shipped" signal a session
      row reads, and the only one that covers the manual button, whose documented mode
      pins no `handout_datetime` at all until it fires.
    - `handout` has passed. A pin whose provisioning then failed still says the brief was
      meant to be out by now, and a schedule that says so is not a secret worth keeping.

    Withheld is the ROW'S CONTENT, not the row: the dates stay, since a deadline is the
    plan and belongs on the schedule the day it is written. The TITLE is withheld with the
    body - `# Detecting fraud in the transfer dataset` is the assignment, not its name - so
    a pending row is titled from its slug and the README is not read at all. That is the
    same rule `_lecture_entry` follows: an unreleased row names itself from the PLAN
    (schedule.yml), never from the artefact it is withholding.

    `now` is the moment to judge the pin against (default: actual now, in the handout's own
    cohort timezone - `_coerce_datetime` hands out nothing naive)."""
    found = schedule.entry_for_repo(sched, repo) if sched is not None else None
    slug = schedule.cohort_name(*found) if found else assignment_slug(repo)
    # An unscheduled assignment's synthesised fallback date is due end-of-day.
    due = _iso_when(when, "23:59:00")
    released = _iso_when(handout) if handout is not None else due
    pinned_out = handout is not None and handout <= (
        now or datetime.now(handout.tzinfo)
    )
    pending = slug not in handed_out and not pinned_out
    # Named once: both bodies point at the same repo, and the two sentences drifted apart
    # the moment either was edited on its own.
    student_repo = f"`{slug}-<your-handle>` repo in `{course_org}`'s cohort org"
    # The plan-side name, and all a pending row gets.
    title = slug.replace("-", " ").title()
    if pending:
        flags = "unreleased: true\n"
        body = (
            f"This assignment has not been handed out yet. Its brief appears here, and "
            f"your private {student_repo}, when it does."
        )
    else:
        flags = ""
        readme = get_file_content(course_org, repo, "README.md") or ""
        for line in readme.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        brief = "\n".join(
            ln for ln in readme.splitlines() if not ln.startswith("# ")
        ).strip()
        body = (
            f"{_liquid_raw(brief or 'Assignment brief.')}\n\n"
            f"_Your private {student_repo} appears once the teaching team provisions it._"
        )
    title = _q(title)
    return (
        f"---\n"
        f"type: assignment\n"
        f"date: {released}\n"
        f'title: "{title}"\n'
        f"{flags}"
        f"due_event:\n"
        f"    type: due\n"
        f"    date: {due}\n"
        f'    description: "{title}"\n'
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
        f"date: {_iso_when(when)}\n"
        f"{flags}"
        f'description: "{_q(title)}"\n'
        f"---\n"
        f"Details to be confirmed.\n"
    )


def _assignment_dates(
    sched: schedule.Schedule, repo: str, fallback: date
) -> tuple[date | datetime, datetime | None]:
    """(due, handout) for an assignment from schedule.yml (keyed on the slug, repo minus
    its -fYYYY/-sYYYY tag). An unscheduled assignment is due on `fallback` and has no
    handout; a scheduled one has a handout only when the plan pins (or the manual release
    button recorded) one."""
    found = schedule.entry_for_repo(sched, repo)
    entry = found[1] if found else None
    if entry is None:
        return fallback, None
    return entry.due_datetime, entry.handout_datetime


def _deploy_dest(deploy: schedule.Deploy) -> str:
    """Where a deploy lands inside its destination repo - `cohort_dest_path` when it is
    set, else the source path mirrored. Stated once: the ordinal and the section of a row
    are both read off this, and deriving them from two separate copies of the rule is how
    they come to disagree."""
    return (deploy.cohort_dest_path or deploy.course_source_path).strip("/")


def _deploy_section(deploy: schedule.Deploy) -> str:
    """The section a deploy lands in - the top-level directory of its destination path,
    or the destination repo itself when the path is a bare session folder (a release into
    a repo that IS one section). The read-side twin is `_source_section`, which reports for
    an already-released folder, so both sides classify a row the same way."""
    head, sep, _ = _deploy_dest(deploy).partition("/")
    return head if sep else deploy.cohort_dest_repo


@dataclass
class _PlannedRow:
    """What the release PLAN says about one session row, before anything has shipped.

    `when` is the earliest event_datetime touching the row; `dests` the cohort-side
    `repo/path`s its deploys will land in (ordered, deduped); `subtitle` and `description`
    the display text its entry declared; `readings_planned` whether any of its deploys
    targets the readings section - which is how a row can say a reading list is still to
    come rather than leaving the session off the Materials tab entirely."""

    when: date | datetime
    dests: dict[str, None] = field(default_factory=dict)
    subtitle: str = ""
    description: str = ""
    readings_planned: bool = False
    # When the entry that supplied `subtitle`/`description` happens. Kept on the row so
    # one row's state lives in one object: `when` is the min over every entry touching the
    # row, which is not the same thing as "which entry named it".
    named_at: datetime | None = None


def _planned_sessions(sched: schedule.Schedule) -> dict[tuple[str, str], _PlannedRow]:
    """Every session row the PLAN declares - (ordinal, 'lecture'|'lab') -> what the plan
    says about it (see `_PlannedRow`).

    Keyed by the ordinal and section of each deploy's destination folder, so the site can
    both date a released row from the plan that released it AND raise a row for a session
    whose materials have not shipped yet (`sync_site` unions these keys with what
    discovery found). Keying on the row, not the week, is what lets Wednesday's lab carry
    its own time rather than inheriting Monday's lecture. Deploys may ship on their own
    `deploy_datetime` clocks; the site announces the class, not the copy. Earliest wins
    when several releases touch the same row, and the destinations are collected in plan
    order (deduped - two deploys of one entry can name the same one) so a placeholder row
    can name where its materials are going to appear."""
    out: dict[tuple[str, str], _PlannedRow] = {}
    for release in sched.releases:
        if release.when is None:
            continue  # event_datetime: tbc - undated, can't place a session
        for d in release.deploy:
            dest = _deploy_dest(d)
            n = session_number(dest.rsplit("/", 1)[-1])
            if n is None:
                continue
            section = _deploy_section(d)
            key = (str(n), _row_kind(section))
            row = out.setdefault(key, _PlannedRow(when=release.when))
            row.when = min(row.when, release.when)
            # dict-as-ordered-set, not a list: dedupe where the destinations are
            # collected, so the consumer is a plain join and the returned value means
            # what the docstring says it does.
            row.dests[f"{d.cohort_dest_repo}/{dest}"] = None
            row.readings_planned = row.readings_planned or section == READINGS_SECTION
            # A row is NAMED by the same entry it is DATED by: the earliest one touching
            # it. Title and description are adopted as a pair - they describe one session,
            # and taking the name from one entry and the blurb from another would read as
            # a mismatch nobody wrote.
            if (release.title or release.description) and (
                row.named_at is None or release.when < row.named_at
            ):
                row.named_at = release.when
                row.subtitle = release.title
                row.description = release.description
    return out


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
        f'name: "{_q(title)}"\n'
        f"date: {_iso_when(when)}\n"
        f"{flags}"
        f'description: ""\n'
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
        f"date: {_iso_when(when)}\n"
        f"hide_time: true\n"
        f'name: "{_q(name)}"\n'
        f'description: ""\n'
        f"---\n"
    )


@dataclass
class _SitePlan:
    """What one sync wants its site repo to contain, handed back to `_sync_site_repo`.

    `config` are the `_config.yml` keys to overwrite (course identity); `collections` the
    collection dirs this sync OWNS, each cleared then rewritten from its `{filename:
    content}` (so an entry that is no longer generated - a de-released session, a template
    placeholder - disappears, and a collection the sync does not own is left alone);
    `files` every other tracked file to write, by repo-relative path (`_data/people.yml`,
    the publish config, ...); `commit` the commit subject; `label`/`done` the wording of
    this sync's log lines."""

    config: dict[str, str]
    collections: dict[str, dict[str, str]]
    commit: str
    files: dict[str, str] = field(default_factory=dict)
    label: str = "site"
    done: str = "synced + redeploying"


def _git_identity(key: str) -> str:
    """What GIT_ENV sets `user.name` / `user.email` to - read off GIT_ENV itself, so the
    machine-author test below cannot drift from the identity the sync commits under."""
    prefix = f"{key}="
    return next(v[len(prefix) :] for v in _GIT_ENV if v.startswith(prefix))


def _is_machine_author(name: str, email: str) -> bool:
    """Whether a commit's author is this engine rather than a person: the sync's own git
    identity, the token account (which authors the commits made through the API - the
    scaffold's "Initial commit"), or any GitHub App. An unreadable acting login is None
    and matches nobody, which errs towards calling a commit human - the wrong guess there
    is one unnecessary issue, the other way round is a silently discarded edit."""
    acting = _acting_login()
    return (
        name == _git_identity("user.name")
        or email == _git_identity("user.email")
        or (acting is not None and name.casefold() == acting.casefold())
        or name.endswith("[bot]")
    )


def _overwritten_edits(wd: Path) -> dict[str, tuple[str, list[str]]]:
    """The human commits the sync commit at HEAD just discarded: `{sha: (author, paths)}`.

    For every path HEAD rewrote, the last commit to touch it BEFORE HEAD is the edit that
    was replaced; a machine author there is the previous sync (nothing lost), anything
    else is a person. A path no earlier commit touched is a file this sync created.
    HEAD^ always exists - the clone carries at least the scaffold's initial commit."""
    code, out = git("-C", str(wd), "show", "--name-only", "--format=", "HEAD")
    if code != 0:
        raise RuntimeError(f"could not list the files the sync commit changed: {out}")
    overwritten: dict[str, tuple[str, list[str]]] = {}
    for path in (ln.strip() for ln in out.splitlines()):
        if not path:
            continue
        code, out = git(
            "-C", str(wd), "log", "-1", "--format=%H%x09%an%x09%ae", "HEAD^", "--", path
        )
        if code != 0 or not out.strip():
            continue
        sha, _, rest = out.strip().partition("\t")
        name, _, email = rest.partition("\t")
        if _is_machine_author(name, email):
            continue
        overwritten.setdefault(sha, (name, []))[1].append(path)
    return overwritten


OVERWRITE_ISSUE_TITLE = (
    "Manual edits to generated site files are overwritten by the sync"
)


def _commit_login(org: str, site: str, sha: str) -> str | None:
    """The GitHub login behind a commit, or None when its git email is linked to no
    account - then the caller falls back to the git author name, which is all GitHub
    itself knows about that author either."""
    code, out = gh("api", f"repos/{org}/{site}/commits/{sha}", "--jq", ".author.login")
    login = out.strip()
    return login if code == 0 and login and login != "null" else None


def _notify_overwritten_edits(
    org: str, site: str, overwritten: dict[str, tuple[str, list[str]]]
) -> None:
    """Open (or comment on) one issue in the site repo naming the edits this sync just
    replaced, linking the discarded commits and pointing at the files to edit instead.

    A courtesy, not data: every failure here is logged and swallowed, including by the
    caller. The site is already regenerated and pushed by the time this runs, so making
    the notice able to fail the sync would turn a helper against silent data loss into a
    new source of red crons - inverting the incident it exists to prevent."""
    rows = []
    unmentionable = False
    for sha, (name, paths) in overwritten.items():
        # An @-mention when the git email is linked to an account, else the git author
        # name - which is all GitHub knows about that author either.
        login = _commit_login(org, site, sha)
        unmentionable = unmentionable or login is None
        who = f"@{login}" if login else f"`{name}`"
        for path in paths:
            rows.append(
                f"- `{path}` - edited by {who} in "
                f"[`{sha[:7]}`](https://github.com/{org}/{site}/commit/{sha})"
            )
    body = (
        "The site sync regenerates parts of this repo from the org structure, so an edit "
        "made directly here is replaced the next time it runs. It has just replaced:\n\n"
        + "\n".join(rows)
        + "\n\nNothing is lost - each link above is the commit that was overwritten, so "
        "the change can be copied back out of it.\n\nMake the edit at the source "
        "instead, and it survives every sync:\n\n"
        "- **Staff cards** - the cohort's `classroom-config/people.yml` (for a public "
        "course site, the `people:` block of the course org's "
        "`.github/dsl-course.yml`).\n"
        "- **Schedule rows, sessions, assignments** - the org structure and the cohort's "
        "`classroom-config/schedule.yml`.\n\n"
        "The sync owns `_lectures/`, `_assignments/`, `_events/`, `_data/people.yml` and "
        "a few `_config.yml` keys, and names the source in a header where the file format "
        "allows one. Everything else in this repo is yours and is never rewritten.\n"
    )
    # An issue only emails people it mentions. When an author's git email is linked to no
    # account there is nobody to ping - which was the incident - so fall back to the org's
    # instructors team, who can pass it on. Only then: a group ping when the direct one
    # already worked is noise for everyone who did not touch the file.
    if unmentionable:
        body += (
            f"\ncc @{org}/instructors - a commit author's email is not linked to a "
            "GitHub account, so they could not be mentioned directly.\n"
        )
    repo = f"{org}/{site}"
    code, out = gh(
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--search",
        f"{OVERWRITE_ISSUE_TITLE} in:title",
        "--json",
        "number",
        "--jq",
        ".[0].number",
    )
    # A lookup that failed (or answered with anything but a number) is not fatal: filing a
    # duplicate issue beats not telling anyone their edit was discarded.
    existing = out.strip() if code == 0 and out.strip().isdigit() else ""
    if existing:
        code, out = gh("issue", "comment", existing, "--repo", repo, "--body", body)
    else:
        code, out = gh(
            "issue",
            "create",
            "--repo",
            repo,
            "--title",
            OVERWRITE_ISSUE_TITLE,
            "--body",
            body,
        )
    if code != 0:
        log_err(f"could not notify {repo} about the overwritten edits: {out[:200]}")
    else:
        log(f"  (manual edits to {len(rows)} file(s) were overwritten - issue filed)")


def _sync_site_repo(
    org: str,
    build: Callable[[Path], _SitePlan | None],
    *,
    scaffold_missing: bool = False,
) -> int:
    """The site-repo mechanics both syncs drive: ensure `<org>.github.io` exists, clone it,
    let `build` gather that sync's own data (writing into the working tree it is handed -
    the public site hosts files there) and declare a `_SitePlan`, apply the plan, then
    commit-if-changed and push. Pushing redeploys the site.

    `build` returns None to abort with exit 1, having logged its own reason. A missing site
    repo is a quiet no-op (a cohort that never opted into a site), unless
    `scaffold_missing` - the public course site's opt-in first publish, which creates it.
    An ARCHIVED one is the same quiet no-op: a past cohort's site is deliberately frozen,
    and it clones and commits happily before 403ing on the push, so without the check the
    daily cron failed on it every day forever.

    A pushed sync that discarded someone's manual edit also files an issue naming it - a
    courtesy that never changes this function's exit code."""
    # `--all-cohorts` loops this in one process, and the index reads EVERY release
    # destination's tree, not just the session-bearing ones - so the memo would pin a few
    # hundred KB per repo for the whole run. Cleared on ENTRY rather than on the way out:
    # most cohorts in a daily cron are already up to date and return early, so an exit-path
    # clear ran on the rare path and never on the common one. Keys include the org, so this
    # is purely about memory, never staleness.
    _repo_tree.cache_clear()
    site = _site_repo(org)
    just_scaffolded = False
    if not repo_exists(org, site):
        if not scaffold_missing:
            log(f"  (no site repo {org}/{site} - skipping site sync)")
            return 0
        from . import scaffold

        log_step(f"No public site yet - scaffolding {org}/{site}")
        if scaffold.scaffold_site(org) != 0:
            return 1
        just_scaffolded = True
    elif repo_is_archived(org, site):
        log(f"  (site repo {org}/{site} is archived - skipping site sync)")
        return 0

    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "site"
        # A repo THIS run just created can lag its template-generate, so retry the clone;
        # an existing site repo either clones now or is a real failure.
        attempts = 6 if just_scaffolded else 1
        for attempt in range(attempts):
            if gh("repo", "clone", f"{org}/{site}", str(wd), "--", "-q")[0] == 0:
                break
            if attempt + 1 < attempts:
                time.sleep(5)
        else:
            log_err(f"could not clone {org}/{site}")
            return 1

        plan = build(wd)
        if plan is None:
            return 1

        # Course identity into _config.yml (course_name / _semester / _code /
        # _description, github_org) - only the keys the plan declares, nothing else.
        cfg_path = wd / "_config.yml"
        if cfg_path.is_file():
            cfg = cfg_path.read_text()
            for key, value in plan.config.items():
                cfg = _set_config(cfg, key, value)
            cfg_path.write_text(cfg)

        # Regenerate the owned collections; leave everything else (layouts, pages) as the
        # template provides.
        for coll, entries in plan.collections.items():
            d = wd / coll
            if d.is_dir():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            (d / ".gitkeep").write_text("")
            for fname, content in entries.items():
                (d / fname).write_text(content)

        for rel, content in plan.files.items():
            (wd / rel).parent.mkdir(parents=True, exist_ok=True)
            (wd / rel).write_text(content)

        git("-C", str(wd), *_GIT_ENV, "add", "-A")
        code, _ = git(
            "-C", str(wd), *_GIT_ENV, "commit", "-q", "--no-verify", "-m", plan.commit
        )
        if code != 0:
            log_ok(f"{plan.label} already up to date")
            return 0
        if git("-C", str(wd), *_GIT_ENV, "push", "-q", "origin", "HEAD")[0] != 0:
            log_err(f"{plan.label} push failed")
            return 1
        # Only now is anything actually overwritten on the remote: a run that committed
        # nothing replaced nothing, and a failed push left the remote as it was. Whatever
        # happens in here, the site is correct and published - so the return code below is
        # deliberately untouched (see _notify_overwritten_edits).
        try:
            overwritten = _overwritten_edits(wd)
            if overwritten:
                _notify_overwritten_edits(org, site, overwritten)
        except Exception as exc:
            log_err(
                f"could not check {org}/{site} for overwritten manual edits "
                f"({type(exc).__name__}): {exc}"
            )
    log_ok(f"{plan.label} {plan.done} -> https://{site}/")
    return 0


def sync_site(course_org: str, cohort_org: str) -> int:
    """Regenerate the cohort's student-facing site from the live org state: the term's
    lecture and lab rows (released ones linked into the private content repos, planned
    ones marked not-yet-released), this year's assignments, and the display-only rows of
    the schedule (exams, special events, term dates)."""

    def build(_wd: Path) -> _SitePlan:
        content_repos = seed.discover_cohort_repos([cohort_org])
        release_sources = seed.discover_release_sources(cohort_org, content_repos)
        # One row per (ordinal, kind): a week's lecture materials and its lab are separate
        # rows on the schedule, so a lab released into `labs/` never folds into the
        # lecture's row (and never shows up twice, on the schedule and the labs page).
        sources_by_row: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for repo, subpath, folder, n in release_sources:
            key = (str(n), _row_kind(_source_section(repo, subpath)))
            sources_by_row.setdefault(key, []).append((repo, subpath, folder))
        assignments = seed.discover_assignments(course_org)
        # A persistent course org holds per-year templates (assignment-*-fYYYY); a cohort
        # site should list only its own year's, matched on the cohort's fYYYY/sYYYY tag.
        tag = _cohort_tag(cohort_org)
        if tag:
            assignments = [a for a in assignments if a.lower().endswith(tag)]
        # Which of them this cohort has actually been given - what gates their briefs. Read
        # from the cohort org rather than inferred from the plan, since the manual button
        # hands out with no `handout_datetime` pinned at all.
        handed_out = discover_handed_out_assignments(cohort_org)

        # Course identity comes from the course org metadata, semester from the cohort tag.
        meta = _yaml_file(course_org, ".github", "dsl-course.yml")
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
        # (the manual button, an off-plan extra) keeps its row whether or not it is here.
        planned = _planned_sessions(sched)
        rows = sorted(
            set(sources_by_row) | set(planned), key=lambda k: (int(k[0]), k[1])
        )
        # Every key of sources_by_row is in rows by construction, so this is arithmetic
        # rather than a scan.
        log_step(
            f"Syncing {cohort_org}/{_site_repo(cohort_org)}: {len(rows)} session row(s) "
            f"({len(rows) - len(sources_by_row)} not released yet), "
            f"{len(assignments)} assignment(s)"
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
            it does not. A row discovery found but the plan never named (the manual button,
            an off-plan extra) gets a stand-in row: the weekly fallback date and no declared
            name. It still appears, which is the point - and resolving the absence HERE is
            what keeps the renderer to one shape rather than a field-by-field fallback."""
            row = planned.get((s, kind)) or _PlannedRow(
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
        # one. Written as a single line whatever the source shape (see _q).
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
            f"{i + 1:02d}-{_slug(e.label)}.md": _event_entry(e, end)
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

        return _SitePlan(
            config=config,
            # People: this cohort's own classroom-config/people.yml (instructors AND TAs -
            # the per-cohort teaching team; schema in
            # templates/classroom-config/people.yml), else its instructors team.
            files={
                "_data/people.yml": _people_yaml(
                    cohort_org,
                    _yaml_file(cohort_org, "classroom-config", "people.yml"),
                    edit_at=f"{cohort_org}/classroom-config/people.yml",
                ),
                "_data/nav.yml": _nav_yaml(cohort=True),
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
                **_theme_pages(cohort=True),
            },
            # Assignment handout/due dates come from schedule.yml when set (keyed on the
            # assignment slug), else a synthesised fortnightly cadence.
            collections={
                "_lectures": {
                    _row_file(s, kind): session_row(s, kind) for s, kind in rows
                },
                "_assignments": {
                    f"{i + 1:02d}-{a}.md": _assignment_entry(
                        course_org,
                        a,
                        *_assignment_dates(
                            sched, a, start + timedelta(days=(i + 1) * 14)
                        ),
                        sched=sched,
                        handed_out=handed_out,
                    )
                    for i, a in enumerate(assignments)
                },
                "_events": event_entries,
            },
            commit="site: sync from org structure",
        )

    return _sync_site_repo(cohort_org, build)


def _public_links(local_dir: Path, url_prefix: str) -> list[tuple[str, str]]:
    """(display-name, site-relative URL) for the files of a copied session folder that the
    page LISTS - not every file it serves.

    URLs are relative to the public site root (`/PUBLIC_MATERIALS_DIR/...`), so they
    resolve for the public - never blob/raw URLs into the private source repo. Names are
    the path relative to the session folder (so two nested `notes.pdf` stay
    distinguishable, as on the cohort site) and URL-encoded so spaces etc. survive.

    Every file stays COPIED and served whatever this returns - a rendered deck's `libs/`
    and `<name>_files/` must remain reachable at their original relative paths or the
    `.html` loads with no styles and no figures. Only the LISTING is filtered.

    Nested files are folded away ONLY when there is something at the root to fold them
    into. That is what the fold means - "these are the assets of that deliverable" - and
    with no root file they are not assets, they are the material. Listing them anyway is
    also the only way to keep them reachable: Jekyll serves no directory index, so this
    host has no folder link to offer instead, and a session whose files all sit in
    subfolders would otherwise be copied, served, and linked from nowhere - or, since a
    section with no links is skipped entirely, get no page at all.

    `site_link_extensions` deliberately does NOT apply here, for the same reason: a file
    the allowlist excluded would be served and unreachable. The cohort site takes the extra
    narrowing because it CAN offer a folder link; this one keeps every file reachable."""
    files = sorted(q for q in local_dir.rglob("*") if q.is_file())
    rels = [q.relative_to(local_dir).as_posix() for q in files]
    if any("/" not in rel for rel in rels):
        rels = [rel for rel in rels if "/" not in rel]
    return [(rel, f"{url_prefix}/{quote(rel)}") for rel in rels]


def _reading_list_md(readings_session_dir: Path) -> str:
    """The readings rendered as TEXT for `reading-list` mode (no files hosted, no links).

    `_readings_block`'s rule over a local directory: the overlay's prose inlined verbatim,
    then every other file by NAME only - so the public sees WHAT to read without the
    copyrighted bytes being published. This mode links nothing, so naming the files here is
    the only way they appear at all."""
    d = readings_session_dir
    return _readings_block(
        [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()],
        lambda n: (d / n).read_text(encoding="utf-8", errors="replace"),
    )


def _public_lecture_entry(
    session: str,
    when: date,
    section_links: list[tuple[str, list[tuple[str, str]]]],
    reading_list_md: str,
    kind: str = "lecture",
) -> str:
    """A public session entry: hosted links for every published section (whatever this
    repo's sections are - `lectures`, `faq`, ... - plus `readings` in actual-readings
    mode), plus the reading list in `reading_list:` when in reading-list mode. Public-facing
    body - no 'enrolled students only' gate. The week's `labs` section is a `lab` row of
    its own (`kind`), exactly as on the cohort site.

    The reading list goes in the front matter, not the body, so that BOTH sites feed the
    theme's Materials layout from one field rather than each carrying its own mechanism -
    and so the text needs no `{% raw %}` fence (front matter is data, not a template).

    A public course site has no schedule.yml to read, so it declares no `subtitle:` or
    `description:` of its own; the theme simply shows the ordinal.

    `section_links` is `(section, [(name, url), ...])` in publication order; each link is
    named `<section-singular> - <file>`, as on the cohort site."""
    links_block = _links_block(section_links)
    title = f"{_ROW_NOUN[kind]} {session}"
    return (
        f"---\n"
        f"type: {kind}\n"
        f"date: {_iso_when(when)}\n"
        f'title: "{title}"\n'
        + (_block("reading_list", reading_list_md) if reading_list_md else "")
        + f"{links_block}\n"
        f"---\n"
        f"Materials for {title.lower()}.\n"
    )


def sync_public_site(
    course_org: str,
    source_repo: str,
    readings_mode: str = "reading-list",
    include_lectures: bool = True,
) -> int:
    """Build/refresh the PUBLIC course site `<course-org>.github.io` (open courseware).

    Opt-in: the first run scaffolds the site (Pages), later runs re-sync it. Every run
    records its settings in the site repo (`PUBLISH_CONFIG`) so the daily cron can repeat
    them unattended. Hosts the chosen `course-materials-*` repo's files - every section it
    actually has (see utils.discover_sections), plus, in `actual-readings` mode, `readings`
    - in the public site repo and links to them with site-relative URLs. `reading-list` mode
    publishes the citation text only. `include_lectures` toggles the file sections as a
    group (its name predates generic sections; the workflow input is unchanged). Session
    materials only - no assignments/events. Served files are namespaced per source repo
    so several years can coexist on one site."""
    if not include_lectures and readings_mode == "none":
        log_err("nothing to publish - file sections off and readings set to none.")
        return 1

    def build(site_wd: Path) -> _SitePlan | None:
        sessions = seed.discover_sessions(course_org, source_repo)
        log_step(
            f"Publishing {course_org}/{_site_repo(course_org)} from {source_repo}: "
            f"{len(sessions)} session(s), readings={readings_mode}, "
            f"file sections={'on' if include_lectures else 'off'}"
        )
        meta = _yaml_file(course_org, ".github", "dsl-course.yml")
        # A course site spans years and has no per-cohort schedule.yml to read (that's
        # cohort-scoped), so the date is a neutral fallback that only orders the session
        # entries.
        start = date(2025, 1, 1)

        # Wipe only THIS source's served subtree (idempotent re-publish; multi-repo safe).
        served_root = site_wd / PUBLIC_MATERIALS_DIR / source_repo
        if served_root.exists():
            shutil.rmtree(served_root)

        lecture_entries: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as work:
            src, spec = Path(work) / "src", f"{course_org}/{source_repo}"
            if gh("repo", "clone", spec, str(src), "--", "-q")[0] != 0:
                log_err(f"could not clone {spec}")
                return None

            # Sections are whatever THIS repo has (the same discovery the release buttons
            # use), not a hardcoded lectures/readings pair - a course whose content lives
            # in `labs/` publishes labs. `readings` is the one section with special
            # semantics (--readings-mode, below); `include_lectures` gates all the others.
            file_sections = (
                [sec for sec in discover_sections(src) if sec != READINGS_SECTION]
                if include_lectures
                else []
            )
            log(
                f"  sections published as files: {', '.join(file_sections) or '(none)'}"
            )

            for s in sessions:
                if not s.isdigit():
                    continue
                site_session = served_root / f"session-{s}"
                url_base = f"/{PUBLIC_MATERIALS_DIR}/{source_repo}/session-{s}"
                # Links per row: the week's `labs` section becomes its own lab row,
                # everything else (lectures, faq, readings) the session row.
                section_links: list[tuple[str, list[tuple[str, str]]]] = []
                lab_links: list[tuple[str, list[tuple[str, str]]]] = []
                reading_list_md = ""

                for section in file_sections:
                    sec_src = find_session_dir(src / section, s)
                    if sec_src is None:
                        continue
                    dest = site_session / section
                    shutil.copytree(sec_src, dest, dirs_exist_ok=True)
                    links = _public_links(dest, f"{url_base}/{section}")
                    if links:
                        rows = (
                            lab_links if _row_kind(section) == "lab" else section_links
                        )
                        rows.append((section, links))

                read_src = find_session_dir(src / READINGS_SECTION, s)
                if read_src is not None:
                    if readings_mode == "actual-readings":
                        dest = site_session / READINGS_SECTION
                        shutil.copytree(read_src, dest, dirs_exist_ok=True)
                        links = _public_links(dest, f"{url_base}/{READINGS_SECTION}")
                        if links:
                            section_links.append((READINGS_SECTION, links))
                    elif readings_mode == "reading-list":
                        reading_list_md = _reading_list_md(read_src)

                # A row with nothing published gets no page at all, rather than an empty
                # one the public would click through to.
                if not section_links and not lab_links and not reading_list_md:
                    log(f"  (session {s}: nothing to publish - no page)")
                    continue
                when = start + timedelta(days=int(s) * 7)
                if section_links or reading_list_md:
                    lecture_entries[_row_file(s, "lecture")] = _public_lecture_entry(
                        s, when, section_links, reading_list_md
                    )
                if lab_links:
                    lecture_entries[_row_file(s, "lab")] = _public_lecture_entry(
                        s, when, lab_links, "", "lab"
                    )

        config = {}
        if meta.get("course_name"):
            config["course_name"] = str(meta["course_name"])
        if meta.get("course_code"):
            config["course_code"] = str(meta["course_code"])
        config["course_semester"] = "Open Courseware"  # neutral: the site is multi-year
        # The public open-courseware site belongs to the COURSE org (multi-year), so its
        # footer links there - unlike a cohort site, which links its cohort org.
        config["github_org"] = course_org

        return _SitePlan(
            config=config,
            # Sessions only: regen _lectures, and clear _assignments/_events so any
            # template placeholders (and a previous run's content) stay off a public site.
            collections={
                "_lectures": lecture_entries,
                "_assignments": {},
                "_events": {},
            },
            files={
                # People from the course org's declared `people:` block (else the GitHub
                # teams). Instructors only - the open-courseware site is multi-year, and
                # TAs are declared per cohort (in each cohort's people.yml), never
                # course-level.
                "_data/people.yml": _people_yaml(
                    course_org,
                    meta,
                    edit_at=f"the `people:` block of {course_org}/.github/dsl-course.yml",
                    include_tas=False,
                ),
                # No cohort repos to index, so `/materials/` stays the readings page.
                "_data/nav.yml": _nav_yaml(cohort=False),
                **_theme_pages(cohort=False),
                # Persist the settings THIS publish used, in the site repo itself, so the
                # daily cron can repeat it with no inputs (see resync_public_site).
                PUBLISH_CONFIG: (
                    "# Written by `python3 -m dsl_course.site public-sync` - the settings of the\n"
                    "# last publish. The daily 'Publish course website' cron re-syncs from them;\n"
                    "# delete this file to stop the automatic refresh.\n"
                    f"source_repo: {source_repo}\n"
                    f"readings_mode: {readings_mode}\n"
                    f"include_lectures: {str(include_lectures).lower()}\n"
                ),
            },
            commit=f"site: publish public course site from {source_repo}",
            label="public site",
            done="published",
        )

    return _sync_site_repo(course_org, build, scaffold_missing=True)


def resync_public_site(course_org: str) -> int:
    """Re-publish the public course site from the settings the last publish persisted.

    The daily cron path: a materials edit then reaches the public site without anyone
    re-clicking the button. Opting in is still a deliberate manual publish, so a course org
    with no public site - or a site with no `PUBLISH_CONFIG` (published before this existed,
    or deliberately unhooked by deleting the file) - is a one-line no-op, NOT a failure:
    the cron ships in every course org's `.github`, and most never publish."""
    site = _site_repo(course_org)
    hint = "run the Publish course website action (or pass --source-repo) to publish"
    if not repo_exists(course_org, site):
        log(f"no public course site ({course_org}/{site}) - nothing to re-sync; {hint}")
        return 0
    raw = get_file_content(course_org, site, PUBLISH_CONFIG) or ""
    cfg = yaml.safe_load(raw) if raw.strip() else None
    if not isinstance(cfg, dict) or not cfg.get("source_repo"):
        log(f"no {PUBLISH_CONFIG} in {course_org}/{site} - nothing to re-sync; {hint}")
        return 0
    log_step(f"Re-syncing {course_org}/{site} from {PUBLISH_CONFIG}")
    return sync_public_site(
        course_org,
        str(cfg["source_repo"]),
        str(cfg.get("readings_mode") or "reading-list"),
        include_lectures=bool(cfg.get("include_lectures", True)),
    )


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
            from .seed import discover_cohorts

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
        registered = seed.discover_cohorts(args.course_org)
        if registered and args.cohort_org.casefold() not in {
            c.casefold() for c in registered
        }:
            log_err(
                f"{args.cohort_org} is not registered under {args.course_org} "
                f"({seed.COHORTS_PATH} lists {', '.join(sorted(registered))}) - refusing "
                f"to sync its site."
            )
            return 1
        return sync_site(args.course_org, args.cohort_org)
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
