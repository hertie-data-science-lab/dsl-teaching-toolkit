"""The Jekyll site repo both course sites are published into.

What a cohort site and a public course site SHARE: the pages and nav the theme renders,
the `_config.yml` keys the sync owns, the people cards, and the clone - write the plan -
commit-if-changed - push cycle that redeploys the site. What differs (which rows exist,
what they link to) is the caller's: `site` builds a cohort site's plan, `public_site` the
open-courseware one, and both hand it here.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cache
from pathlib import Path

from . import scaffold, welcome
from .course import INSTRUCTORS_TEAM, active_today, pages_repo
from .discovery import list_org_repos
from .gh_contents import load_yaml_config
from .gh_teams import acting_login
from .ghcli import GIT_ENV, clone, gh, git, is_missing_resource
from .log import log, log_err, log_ok, log_step
from .repos import repo_exists, repo_is_archived

# The settings of the last manual publish, committed into the site repo so the daily cron
# can re-sync unattended. Leading `_`, so Jekyll ignores it rather than serving it.
PUBLISH_CONFIG = "_publish-config.yml"

# The shared Jekyll theme, and the ref every generated site pins it at.
#
# Pinned, because sites used to track its `main`: a theme PR reached all six live sites
# the moment it merged, and twice took two of them down before anyone had opened one.
# What the theme still owns is the GENERIC chrome - header, footer, nav, brand colours,
# the `default`/`page`/`post` layouts. The course-specific layouts, includes and
# stylesheet ship from `templates/site/` in THIS repo (see `site_templates`), where they
# sit beside the renderers whose front matter they read.
#
# A commit, not a tag, because the theme carries no release tags yet; `remote_theme:`
# takes either form, so this becomes `@v1.0.0` the day one is cut.
THEME_REPO = "hertie-data-science-lab/dsl-jekyll-theme"
THEME_REF = "9288394c5c6d78cf8e881bf4e22ab025a5da1888"

# `_config.yml` keys the sync owns because the templates it ships DEPEND on them, as
# opposed to the course-identity keys, which are content. Written whether or not the
# site's own `_config.yml` already has them: a site generated before this existed has no
# `remote_theme:` line to replace, and a site missing `dateformat` prints every date on
# every page as a raw ISO timestamp.
_THEME_CONFIG = {
    "remote_theme": f"{THEME_REPO}@{THEME_REF}",
    "dateformat": "%m/%d/%Y",
}

# The collections the shipped templates read (`site.lectures`, `site.assignments`,
# `site.events`, `site.announcements`) and the layout an assignment page gets. Both are
# multi-line blocks rather than scalars, so `_upsert_config` writes them whole; both are
# a CONTRACT of templates/site/ rather than a preference, and a site missing either
# builds green into empty pages - the worst way for this to be wrong.
_COLLECTIONS_BLOCK = """collections:
  events:
    output: true
  lectures:
    output: true
  assignments:
    output: true
  announcements:
    output: false
"""

_DEFAULTS_BLOCK = """defaults:
  - scope:
      path: ""
      type: "assignments"
    values:
      layout: "assignment"
"""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "exam"


def q(value: str) -> str:
    """Quote-safe a value for a ONE-LINE double-quoted YAML scalar: escape the two
    characters that are special inside one (`\\` and `"`), and fold newlines away - a
    multi-line value (a faculty `>` block in dsl-course.yml, say) would otherwise write a
    raw newline mid-scalar and break the file it lands in."""
    return " ".join(value.replace("\\", "\\\\").replace('"', "'").split())


def liquid_raw(text: str) -> str:
    """Fence faculty-written text that is inlined verbatim into a Jekyll document. A `{{`
    or `{%` in it would otherwise run as Liquid, and a malformed tag fails the whole build;
    `{% raw %}` renders it literally."""
    return f"{{% raw %}}\n{text}\n{{% endraw %}}"


def block(key: str, text: str) -> str:
    """A multi-line front-matter value as a YAML literal block - faculty-written text (a
    reading list) inlined verbatim, rather than folded onto one line by `q`.

    The indentation indicator (`|2`) is deliberate: without it YAML takes the block's
    indentation from its first non-empty line, so a list that happens to start indented
    would make every following line look like the end of the block and break the whole
    file. Tabs are expanded for the same reason. Front matter is data, not a Liquid
    template, so unlike the body route (`liquid_raw`) a `{{` in the text needs no fence."""
    lines = text.expandtabs(4).rstrip().splitlines()
    body = "\n".join(f"  {ln}" if ln.strip() else "" for ln in lines)
    return f"{key}: |2\n{body}\n"


# A generated collection page states its ownership INSIDE its front matter: Jekyll needs
# `---` on line 1, so a comment above it would break the page. Stamped at the write site
# (`sync_site_repo`) rather than in each of the six renderers, so a renderer added later
# cannot ship an unstamped page.
_FRONT_MATTER_STAMP = (
    "# SYSTEM-OWNED - do not edit. Generated by the DSL course sync, which clears and\n"
    "# rewrites this whole collection on every run. Edit the source instead: the cohort's\n"
    "# classroom-config/schedule.yml (dates, titles) or its org structure (what released).\n"
)


def _stamp_front_matter(text: str) -> str:
    """Insert the ownership notice as the first line inside a page's front matter.

    A page that somehow has no leading `---` is returned untouched rather than corrupted -
    stamping is a courtesy to whoever opens the file, never worth breaking a build for."""
    if not text.startswith("---\n"):
        return text
    return "---\n" + _FRONT_MATTER_STAMP + text[len("---\n") :]


# The site repo's own README. It is generated from `course-website-template`, whose README
# describes the TEMPLATE - so a deployed site repo used to carry no notice at all that it
# is machine-written and redeployed on every push. Written through `plan.files`, so it
# converges on every sync exactly like `_data/people.yml`.
def site_readme(org: str, cohort: bool) -> str:
    # Named from the same page table the sync writes them from, so the list cannot claim a
    # page this site does not have - or omit one it rewrites.
    tab_pages = ", ".join(f"`{pg.file}`" for pg in _site_pages(cohort))
    source = (
        "the cohort's `classroom-config/` files (`schedule.yml`, `people.yml`) and what "
        "the course org actually releases"
        if cohort
        else "the course org's `.github/dsl-course.yml` and the materials repo it publishes"
    )
    return (
        f"<!-- SYSTEM-OWNED - do not edit. Generated and redeployed by the DSL course "
        f"sync. -->\n\n"
        f"# {org} - auto-deployed course website\n\n"
        f"**Do not edit this repository.** It is machine-written: every sync rewrites the "
        f"generated files below and pushing redeploys the site, so an edit here is "
        f"overwritten and lost.\n\n"
        f"Its content comes from {source}.\n\n"
        f"## What the sync owns\n\n"
        f"| Path | Holds |\n"
        f"| --- | --- |\n"
        f"| `_lectures/` | one page per session and lab |\n"
        f"| `_assignments/` | one page per handed-out assignment |\n"
        f"| `_events/` | exams, term dates, display-only rows |\n"
        f"| `_data/people.yml` | the staff cards |\n"
        f"| `_data/nav.yml` | the nav bar |\n"
        + ("| `_data/materials.yml` | the All Materials index |\n" if cohort else "")
        + f"| the tab pages - {tab_pages} | the wrappers the tabs point at |\n"
        + "| `_layouts/`, `_includes/`, `_sass/_course.scss` | how every page renders |\n"
        + "| `_config.yml` | the course identity keys, the pinned theme, and the "
        "`collections:`/`defaults:` the layouts need |\n\n"
        "Each collection is CLEARED and rewritten on every sync, so a file you add to one "
        "disappears on the next run. The tab pages are rewritten too - they are generated "
        "wrappers, so put your own words in `index.md`, or in a page of your own linked "
        "from there.\n\n"
        "## Everything else is yours\n\n"
        "`index.md`, any page you add yourself, `_announcements/`, `_images/`, `Gemfile`, "
        "further `_data/*.yml` - never rewritten. Change them freely.\n\n"
        "The rendering is not yours to change here: `_layouts/`, `_includes/` and "
        "`_sass/_course.scss` are shipped from `templates/site/` in the DSL teaching "
        "toolkit, and the rest of the styling from the shared `dsl-jekyll-theme`. An edit "
        "in this repo is overwritten on the next sync; open a PR against the toolkit "
        "instead, and every course site gets it.\n\n"
        "If you edit a generated file anyway, the sync opens an issue naming the commit it "
        "overwrote, so the change can be copied back out of it.\n"
    )


def _stamp_config(text: str, keys: list[str]) -> str:
    """Correct `_config.yml`'s "Edit the fields below" header for the keys the sync owns.

    The template's header invites faculty to edit the fields under it - true of most of
    them, but not of the course-identity keys this sync overwrites from `dsl-course.yml`
    every run. Naming exactly which keys are machine-written keeps the rest of the header's
    invitation honest. A template that has dropped the line is left alone: the header is
    theme text, not a contract, and a missing line is not worth a failed sync."""
    line = "# Edit the fields below for your course.\n"
    if line not in text:
        return text
    owned = ", ".join(f"`{k}`" for k in keys)
    return text.replace(
        line,
        "# Edit the fields below for your course - EXCEPT the course-identity keys the DSL\n"
        f"# course sync owns and rewrites on every run: {owned}.\n"
        "# Change those in the course org's .github/dsl-course.yml instead.\n",
        1,
    )


# Stamped above anything this sync ADDS to a `_config.yml` it did not write - so a reader
# of the file can tell the lines that are theirs from the lines that get rewritten.
_MANAGED_MARKER = "# managed by the DSL course sync - rewritten on every run"


def _replace_config_scalar(text: str, key: str, value: str) -> str:
    """Replace a top-level `key: ...` line in _config.yml, preserving the rest.

    The value is written as a one-line double-quoted scalar (see `q`). Any indented
    continuation lines are consumed with it, so replacing a key someone left as a `>`/`|`
    block scalar doesn't strand its body as invalid YAML.

    REPLACE-ONLY: a key the site's `_config.yml` doesn't have is a no-op, logged so
    template drift is visible rather than silent. That is right for the course-IDENTITY
    keys, which are content - a site that dropped `course_code:` chose to. What the
    templates DEPEND on goes through `_upsert_config` instead."""
    new, n = re.subn(
        rf"(?m)^({re.escape(key)}:[ \t]*).*(?:\n[ \t]+\S.*)*$",
        lambda m: f'{m.group(1)}"{q(value)}"',
        text,
        count=1,
    )
    if not n:
        log(f"  (_config.yml has no `{key}:` key - not written; template drift?)")
    return new


def _upsert_config(text: str, key: str, body: str) -> str:
    """Write `body` - a complete `key: ...` line, or a whole multi-line block - over
    whatever `_config.yml` holds under `key`, appending it when the file holds none.

    For the keys the shipped templates DEPEND on: the theme settings, and the
    `collections:`/`defaults:` blocks that no one-line scalar could express. A missing one
    of those is not a faculty choice, it is a site generated before the key existed, and
    leaving it out renders a broken page - a site whose `collections:` lost `lectures`
    builds GREEN into an empty Lectures page, which is the failure nobody notices.

    Indented continuation lines go with the key, and the marker line above it is matched
    too, so a second sync replaces what the first wrote rather than stacking a copy."""
    written = _MANAGED_MARKER + "\n" + body.rstrip("\n") + "\n"
    new, n = re.subn(
        rf"(?m)^(?:{re.escape(_MANAGED_MARKER)}\n)?{re.escape(key)}:[ \t]*.*$"
        r"(?:\n[ \t]+.*)*\n?",
        lambda _m: written,
        text,
        count=1,
    )
    return new if n else text.rstrip("\n") + "\n\n" + written


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

    Each page carries its OWN access sentence, because they genuinely differ: a materials
    page is open to auditors, who read released materials but get no assignments, so the
    assignments page names students alone. `gated_note` is what a COHORT site says;
    `open_note` what the public open-courseware site says instead, where the same files are
    published on purpose."""

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
        "Lecture slides are only accessible to enrolled students & auditors.",
        "Lecture slides by session.",
    ),
    _ThemePage(
        "labs.md",
        "labs",
        "Labs",
        "/labs/",
        "fas fa-flask",
        "Lab materials are only accessible to enrolled students & auditors.",
        "Lab materials by session.",
    ),
    _ThemePage(
        "readings.md",
        "readings",
        "Readings",
        "/readings/",
        "fas fa-book",
        "Hosted files are only accessible to enrolled students & auditors.",
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
        "Assignments repos are only accessible to enrolled students.",
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
        "All released course material so far; only accessible to enrolled "
        "students/auditors.",
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


def theme_pages(cohort: bool) -> dict[str, str]:
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


def nav_yaml(cohort: bool) -> str:
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


@cache
def site_templates() -> dict[str, str]:
    """`{repo-relative path: content}` for everything under `templates/site/` - the
    course-specific Jekyll layouts, includes and stylesheet this repo owns.

    Walked, not enumerated: a template added to the directory ships on the next sync with
    no second edit here, which is the only way the two cannot disagree. The paths are the
    site-repo paths verbatim (`_layouts/schedule.html`, `_sass/_course.scss`), so they drop
    straight into `plan.files`.

    Read from real files rather than Python literals for the same reason as
    `welcome.template`: faculty (and the theme's maintainer) can read and PR the thing a
    site will actually receive."""
    root = welcome.TEMPLATES / "site"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def yaml_file(org: str, repo: str, path: str) -> dict:
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
    acting = acting_login()
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


def people_yaml(
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
            for n, p, w in _team_people(org, INSTRUCTORS_TEAM)
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
                f'{k}: "{q(card.get(k, ""))}"'
                for k in ("name", "profile_pic", "webpage")
            ] + [
                f'{k}: "{q(v)}"'
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
        f'instructor:\n  name: "{q(featured.get("name", ""))}"\n'
        f'  profile_pic: "{q(featured.get("profile_pic", ""))}"\n'
        f'  webpage: "{q(featured.get("webpage", ""))}"\n\n'
        f"instructors:{block(instructors)}\n\n"
        f"teaching_assistants:{block(tas)}\n"
    )


ROW_NOUN = {"lecture": "Session", "lab": "Lab"}


def row_file(session: str, kind: str) -> str:
    """The collection filename for one session row - lecture and lab rows of the same
    week are distinct files (`session-02.md`, `lab-02.md`) in the same collection."""
    return f"{'lab' if kind == 'lab' else 'session'}-{int(session):02d}.md"


def _singular(label: str) -> str:
    """A section label as a single-item noun for a link name: 'lectures' -> 'lecture',
    'labs' -> 'lab', 'faq' -> 'faq'. Sections are free-form directory names, so a bare
    `[:-1]` chopped a real character off every label that isn't a plural ('faq' -> 'fa').
    Deliberately no inflection library: strip one trailing 's', else leave it alone."""
    return label[:-1] if len(label) > 1 and label.endswith("s") else label


def links_block(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """A front-matter `links:` block from `(section-label, [(file-name, url), ...])` pairs
    in publication order, each link named `<section-singular> - <file>` (both sites label
    them identically), or `links: []` when there is nothing to link."""
    rows = []
    for label, pairs in sections:
        for name, url in pairs:
            # Route the name through q (escapes `\` AND `"`): a filename with a backslash
            # (`\sigma.pdf`) is an invalid YAML escape and fails the whole Jekyll build.
            safe = q(f"{_singular(label)} - {name}")
            rows.append(f'    - url: {url}\n      name: "{safe}"')
    return ("links:\n" + "\n".join(rows)) if rows else "links: []"


def iso_when(when: date | datetime, fallback_time: str = "09:00:00") -> str:
    """`when` as the offset-free local ISO stamp a front-matter `date:` wants.

    A datetime from schedule.yml is ALREADY in the cohort timezone - the parser converts
    an entry written with an explicit offset (`...T10:00+00:00`) into the cohort's own
    clock - so printing it needs no conversion here, only the offset dropped. A bare date
    (a synthesised fallback, or a whole-day schedule entry) has no clock and gets
    `fallback_time`."""
    if isinstance(when, datetime):
        return when.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{when.isoformat()}T{fallback_time}"


@dataclass
class SitePlan:
    """What one sync wants its site repo to contain, handed back to `sync_site_repo`.

    `config` are the `_config.yml` keys to overwrite (course identity); `collections` the
    collection dirs this sync OWNS, each cleared then rewritten from its `{filename:
    content}` (so an entry that is no longer generated - a de-released session, a template
    placeholder - disappears, and a collection the sync does not own is left alone);
    `files` every other tracked file to write, by repo-relative path (`_data/people.yml`,
    the publish config, ...); `retire` paths to DELETE if the site still has them;
    `commit` the commit subject; `label`/`done` the wording of this sync's log lines.

    `retire` exists because `files` cannot express a removal: the apply step is `git add
    -A` over a checkout, so a file the toolkit stops shipping simply stays in the repo
    forever. It is the local-checkout twin of `put_files(delete=...)`, and a path already
    absent is not an error - `git rm --ignore-unmatch` - so the same list is safe to
    re-declare on every sync until every site has converged."""

    config: dict[str, str]
    collections: dict[str, dict[str, str]]
    commit: str
    files: dict[str, str] = field(default_factory=dict)
    retire: tuple[str, ...] = ()
    label: str = "site"
    done: str = "synced + redeploying"


def _git_identity(key: str) -> str:
    """What GIT_ENV sets `user.name` / `user.email` to - read off GIT_ENV itself, so the
    machine-author test below cannot drift from the identity the sync commits under."""
    prefix = f"{key}="
    return next(v[len(prefix) :] for v in GIT_ENV if v.startswith(prefix))


def _is_machine_author(name: str, email: str) -> bool:
    """Whether a commit's author is this engine rather than a person: the sync's own git
    identity, the token account (which authors the commits made through the API - the
    scaffold's "Initial commit"), or any GitHub App. An unreadable acting login is None
    and matches nobody, which errs towards calling a commit human - the wrong guess there
    is one unnecessary issue, the other way round is a silently discarded edit."""
    acting = acting_login()
    return (
        name == _git_identity("user.name")
        or email == _git_identity("user.email")
        or (acting is not None and name.casefold() == acting.casefold())
        or name.endswith("[bot]")
    )


def _last_touched_before_head(wd: Path) -> dict[str, tuple[str, str, str]]:
    """`{path: (sha, author name, author email)}` for the last commit BEFORE HEAD to touch
    each path in the history.

    ONE walk. This was a `git log -1 -- <path>` per path, and a sync rewrites every
    generated page and collection entry, so an ordinary cohort site paid a hundred-odd
    subprocesses per sync to read one history. `--name-only` lists each commit's paths
    under its own header line (NUL-prefixed, so no filename can be mistaken for one), and
    the FIRST header a path appears under is the most recent commit to touch it.

    A path absent from the result was touched by no commit before HEAD: this sync created
    it. An unreadable history is `{}` - the same "say nothing" the per-path read gave."""
    code, out = git(
        "-C", str(wd), "log", "--name-only", "--format=%x00%H%x09%an%x09%ae", "HEAD^"
    )
    if code != 0:
        return {}
    touched: dict[str, tuple[str, str, str]] = {}
    author = ("", "", "")
    for line in out.splitlines():
        if line.startswith("\0"):
            sha, _, rest = line[1:].partition("\t")
            name, _, email = rest.partition("\t")
            author = (sha, name, email)
        elif line.strip():
            touched.setdefault(line.strip(), author)
    return touched


def _overwritten_edits(wd: Path) -> dict[str, tuple[str, list[str]]]:
    """The human commits the sync commit at HEAD just discarded: `{sha: (author, paths)}`.

    For every path HEAD rewrote, the last commit to touch it BEFORE HEAD is the edit that
    was replaced; a machine author there is the previous sync (nothing lost), anything
    else is a person. A path no earlier commit touched is a file this sync created.
    HEAD^ always exists - the clone carries at least the scaffold's initial commit."""
    code, out = git("-C", str(wd), "show", "--name-only", "--format=", "HEAD")
    if code != 0:
        raise RuntimeError(f"could not list the files the sync commit changed: {out}")
    touched = _last_touched_before_head(wd)
    overwritten: dict[str, tuple[str, list[str]]] = {}
    for path in (ln.strip() for ln in out.splitlines()):
        if not path:
            continue
        previous = touched.get(path)
        if previous is None:
            continue
        sha, name, email = previous
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


def _stale_site_repo(org: str, site: str) -> str | None:
    """A `*.github.io` repo in `org` under a name that is NOT `site`, if one exists.

    Renaming an org does not rename its `<org>.github.io` repo, and GitHub quietly
    demotes the now-mismatched repo from an org site to a project page. The expected
    site repo is then simply absent, which every sync happily read as "this cohort
    never opted into a site" - a permanent green no-op while the published site rotted.
    Finding the old name is what lets the sync say so instead."""
    for repo in list_org_repos(org):
        name = repo.get("name", "")
        if (
            name.casefold().endswith(".github.io")
            and name.casefold() != site.casefold()
        ):
            return name
    return None


def apply_plan(wd: Path, plan: SitePlan) -> None:
    """Write a `SitePlan` into a site checkout at `wd`: everything but the git operations.

    Separated from `sync_site_repo` so the CI fixture builder
    (tests/fixtures/site/build_fixture.py) writes its site through THIS code rather than a
    second copy of the config upsert and the collection regeneration - the fixture is what
    the `jekyll-contract` job builds to prove the shipped templates render, and a fixture
    assembled slightly differently from a real site proves it about the wrong site.

    Removals (`plan.retire`) stay with the caller: they are `git rm` against a checkout,
    not a write."""
    # _config.yml, in two halves. The plan's own keys are course IDENTITY (course_name
    # / _semester / _code / _description, github_org) and are replace-only. The theme
    # keys and the two blocks are the CONTRACT of the templates written below - a site
    # that lacks them renders those templates wrong - so they go in whether the file
    # has them or not.
    cfg_path = wd / "_config.yml"
    if cfg_path.is_file():
        cfg = cfg_path.read_text()
        for key, value in plan.config.items():
            cfg = _replace_config_scalar(cfg, key, value)
        for key, value in _THEME_CONFIG.items():
            cfg = _upsert_config(cfg, key, f'{key}: "{q(value)}"')
        cfg = _upsert_config(cfg, "collections", _COLLECTIONS_BLOCK)
        cfg = _upsert_config(cfg, "defaults", _DEFAULTS_BLOCK)
        owned = [*plan.config, *_THEME_CONFIG, "collections", "defaults"]
        cfg_path.write_text(_stamp_config(cfg, sorted(owned)))

    # Regenerate the owned collections; leave everything else (layouts, pages) as the
    # template provides.
    for coll, entries in plan.collections.items():
        d = wd / coll
        if d.is_dir():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / ".gitkeep").write_text("")
        for fname, content in entries.items():
            (d / fname).write_text(_stamp_front_matter(content))

    for rel, content in plan.files.items():
        (wd / rel).parent.mkdir(parents=True, exist_ok=True)
        (wd / rel).write_text(content)


_GIT_FAILURE_TAIL_LINES = 5


def _git_failure_tail(out: str, lines: int = _GIT_FAILURE_TAIL_LINES) -> str:
    """The last few lines of a failed git command, indented for the log.

    A push fails for reasons nobody can guess from "push failed": GitHub's push
    protection blocking a committed secret, a repository rule violation, a 403 on a repo
    whose permissions changed. `ghcli.git` already hands back git's combined output and
    it was being thrown away, so the daily site cron reported a bare failure and the
    actual message - the only thing that says what to fix - reached nobody.

    Only the tail, because a push prints progress before it prints the reason. No
    credential can appear in it: the clone's remote is a plain https URL and gh keeps the
    token in the credential helper."""
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-lines:]
    return "\n".join(f"    {ln}" for ln in tail) or "    (git said nothing)"


def sync_site_repo(
    org: str,
    build: Callable[[Path], SitePlan | None],
    *,
    scaffold_missing: bool = False,
) -> int:
    """The site-repo mechanics both syncs drive: ensure `<org>.github.io` exists, clone it,
    let `build` gather that sync's own data (writing into the working tree it is handed -
    the public site hosts files there) and declare a `SitePlan`, apply the plan, then
    commit-if-changed and push. Pushing redeploys the site.

    `build` returns None to abort with exit 1, having logged its own reason. A missing site
    repo is a quiet no-op (a cohort that never opted into a site), unless
    `scaffold_missing` - the public course site's opt-in first publish, which creates it.
    An ARCHIVED one is the same quiet no-op: a past cohort's site is deliberately frozen,
    and it clones and commits happily before 403ing on the push, so without the check the
    daily cron failed on it every day forever.

    A pushed sync that discarded someone's manual edit also files an issue naming it - a
    courtesy that never changes this function's exit code."""
    site = pages_repo(org)
    just_scaffolded = False
    if not repo_exists(org, site):
        try:
            stale = _stale_site_repo(org, site)
        except RuntimeError as exc:
            log_err(str(exc))
            return 1
        if stale is not None:
            log_err(
                f"{org} has no {site}, but it does hold {org}/{stale} - the org was "
                "renamed and its Pages site was silently demoted to a project page. "
                f"Rename {stale} to {site} (GitHub does not do it for you), then re-run."
            )
            return 1
        if not scaffold_missing:
            log(f"  (no site repo {org}/{site} - skipping site sync)")
            return 0
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
            if clone(org, site, wd):
                break
            if attempt + 1 < attempts:
                time.sleep(5)
        else:
            log_err(f"could not clone {org}/{site}")
            return 1

        plan = build(wd)
        if plan is None:
            return 1

        apply_plan(wd, plan)

        # Removals. `git add -A` below stages everything the working tree holds, so a file
        # the toolkit no longer ships would otherwise live on in the site repo untouched.
        for rel in plan.retire:
            git(
                "-C",
                str(wd),
                *GIT_ENV,
                "rm",
                "-r",
                "-q",
                "--ignore-unmatch",
                "--",
                rel,
            )

        git("-C", str(wd), *GIT_ENV, "add", "-A")
        code, _ = git(
            "-C", str(wd), *GIT_ENV, "commit", "-q", "--no-verify", "-m", plan.commit
        )
        if code != 0:
            log_ok(f"{plan.label} already up to date")
            return 0
        code, out = git("-C", str(wd), *GIT_ENV, "push", "-q", "origin", "HEAD")
        if code != 0:
            log_err(f"{plan.label} push failed:\n{_git_failure_tail(out)}")
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
