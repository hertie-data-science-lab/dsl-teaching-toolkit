"""dsl-course public course site - the open-courseware half of the site build.

The course org's `<course-org>.github.io` is PUBLIC and opt-in. The `course-materials-*`
repos it publishes are private, so linking into them would 404 for the public; instead
this HOSTS the chosen repo's files in the site repo (Jekyll serves any path not starting
with `_`) and links to site-relative URLs. Session materials only - no assignments, no
events, no semester repos. What it publishes is the course's `course.yml`
`open_course_site:` (`opencourse`), its `publish:` Selection run through `selection` (the
institution denylist, the repo's `.releaseignore`, then the selection and the institution's
floor): the Publish website operation and the daily update both read it, and `enabled:
false` stops both.

Driven through `python3 -m dsl_course.site public-sync`, which delegates here.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import yaml

from . import policy
from .course import (
    OPEN_SITE_BLOCK,
    course_block,
    discover_local_sessions,
    discover_sections,
    find_session_dir,
    pages_repo,
)
from .discovery import org_meta
from .faults import Unusable
from .fs import copy_tree
from .ghcli import clone
from .log import Summary, log, log_err, log_step
from .materials import folder_kind
from .opencourse import OPENCOURSE_FILE, OpenCourse
from .opencourse import read as read_opencourse
from .readings import readings_block
from .selection import PUBLISH, Selection, denied, deny, floor, kept
from .selection import releaseignored as releaseignored_in
from .site_repo import (
    ROW_NOUN,
    Link,
    SitePlan,
    block,
    iso_when,
    kinds_yaml,
    links_block,
    nav_yaml,
    people_yaml,
    row_file,
    site_readme,
    site_templates,
    sync_site_repo,
    theme_pages,
)

# Public course site: served folder for the hosted section files.
PUBLIC_MATERIALS_DIR = "public-materials"

# The open site is still built from one repo's folders (its own design, `openware`, is
# not written yet): `readings` is the section `publish.reading_lists` speaks for, and a
# lab-kind section makes a lab row (`row_kind`). The semester site reads kinds from the
# schedule.
READINGS_SECTION = "readings"


def course_facts(meta: dict) -> str:
    """`_data/course.yml`: the contact and the licence the home page shows (decision 0019).

    The course's own `open_course_site.contact` / `course.licence` from course.yml, else
    the institution's first contact and first licence. A licence the institution names
    carries its URL; one it does not (or `All rights reserved`, which has none) is shown
    as plain text."""
    pol = policy.load()
    site = (
        meta.get(OPEN_SITE_BLOCK) if isinstance(meta.get(OPEN_SITE_BLOCK), dict) else {}
    )
    contact = str(site.get("contact") or "").strip() or policy.contact()
    name = (
        str(course_block(meta).get("licence") or "").strip()
        or pol["licences"][0]["name"]
    )
    url = next((lc["url"] for lc in pol["licences"] if lc["name"] == name), "")
    facts = {"contact": contact, "licence": {"name": name, "url": url}}
    return yaml.safe_dump(facts, sort_keys=False, allow_unicode=True)


def shown_sections(sections: list[str]) -> list[str]:
    """The sections that get rows: one whose name is a shown kind of the institution's
    (`lectures/`, `labs/`); a supporting-files one (`data/`, and any folder no kind
    names) never does (decisions 0026 rule 3, 0031 rule 10; kinds from the institution
    file only, ruling of 14 Oct 2026)."""
    return [s for s in sections if folder_kind(s) in policy.shown_kinds()]


def row_kind(section: str) -> str:
    """The open site's row for a section: its kind when the open site has a row of that
    kind (`ROW_NOUN`: a lab-kind one, `labs/`), else 'lecture' (the session row)."""
    kind = folder_kind(section)
    return kind if kind in ROW_NOUN else "lecture"


def _section_kind(section: str) -> str:
    """A section's kind as `publish.kinds` names it (`lectures`), from its folder name."""
    return policy.plural_of(folder_kind(section))


def _public_links(local_dir: Path, url_prefix: str) -> list[Link]:
    """A `Link` (display-name, site-relative URL) per file of a copied session folder that
    the page LISTS - not every file it serves.

    URLs are relative to the public site root (`/PUBLIC_MATERIALS_DIR/...`), so they
    resolve for the public - never blob/raw URLs into the private source repo. Names are
    the path relative to the session folder (so two nested `notes.pdf` stay
    distinguishable, as on the semester site) and URL-encoded so spaces etc. survive.

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
    the allowlist excluded would be served and unreachable. The semester site takes the extra
    narrowing because it CAN offer a folder link; this one keeps every file reachable."""
    files = sorted(q for q in local_dir.rglob("*") if q.is_file())
    # Denylisted paths are already absent from a folder THIS run copied (`selection`),
    # so this is the second lock on the same door - and the one that holds if a file
    # ever reaches the served tree by another route.
    rels = [
        rel
        for rel in (q.relative_to(local_dir).as_posix() for q in files)
        if not denied(rel)
    ]
    if any("/" not in rel for rel in rels):
        rels = [rel for rel in rels if "/" not in rel]
    return [Link(rel, f"{url_prefix}/{quote(rel)}") for rel in rels]


def _reading_list_md(readings_session_dir: Path, keep: Callable[[Path], bool]) -> str:
    """The readings rendered as TEXT for `reading-list` mode (no files hosted, no links).

    `readings_block`'s rule over a local directory: the overlay's prose inlined verbatim,
    then every other file by NAME only - so the public sees WHAT to read without the
    copyrighted bytes being published. This mode links nothing, so naming the files here is
    the only way they appear at all.

    `keep` is not optional and is why this takes a callable: naming a file IS publishing it
    here, and inlining an overlay publishes every word of it, so a withheld reading has to
    be dropped before `readings_block` ever sees it."""
    d = readings_session_dir
    return readings_block(
        [p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and keep(p)],
        lambda n: (d / n).read_text(encoding="utf-8", errors="replace"),
    )


def _public_lecture_entry(
    session: str,
    when: date,
    section_links: list[tuple[str, list[Link]]],
    reading_list_md: str,
    kind: str = "lecture",
) -> str:
    """A public session entry: hosted links for every published section (whatever this
    repo's sections are - `lectures`, `faq`, ... - plus `readings` in actual-readings
    mode), plus the reading list in `reading_list:` when in reading-list mode. Public-facing
    body - no 'enrolled students only' gate. The week's `labs` section is a `lab` row of
    its own (`kind`), exactly as on the semester site.

    The reading list goes in the front matter, not the body, so that BOTH sites feed the
    theme's Materials layout from one field rather than each carrying its own mechanism -
    and so the text needs no `{% raw %}` fence (front matter is data, not a template).

    A public course site has no schedule.yml to read, so it declares no `subtitle:` or
    `details:` of its own; the theme simply shows the ordinal.

    `section_links` is `(section, [Link, ...])` in publication order; each link is
    named `<section-singular> - <file>`, as on the semester site."""
    links = links_block(section_links)
    title = f"{ROW_NOUN[kind]} {session}"
    return (
        f"---\n"
        f"kind: {kind}\n"
        f"date: {iso_when(when)}\n"
        f'title: "{title}"\n'
        + (block("reading_list", reading_list_md) if reading_list_md else "")
        + f"{links}\n"
        f"---\n"
        f"Materials for {title.lower()}.\n"
    )


def sync_public_site(course_org: str, oc: OpenCourse) -> int:
    """Build/refresh the PUBLIC course site `<course-org>.github.io` (open courseware)
    from `oc`, the course's `open_course_site:`.

    The first run scaffolds the site (Pages), later runs re-sync it. Hosts the files of
    the declared `source_repo`'s sections (see course.discover_sections) that `publish:`
    keeps - a section's kind from its folder, each file through `selection`'s layers -
    in the public site repo, and links to them with site-relative URLs. `reading_lists`
    publishes the readings' citation text; the readings themselves only when `readings`
    is a published kind or an `include` names them. Session materials only - no
    assignments/events. Served files are namespaced per source repo so several years can
    coexist on one site."""
    source_repo, sel = oc.source_repo, oc.selection
    if not sel.kinds and not sel.include and not oc.reading_lists:
        log_err("nothing to publish - no kinds, no includes and no reading lists.")
        return 1
    limit = floor(PUBLISH)

    def build(site_wd: Path) -> SitePlan | None:
        meta = org_meta(course_org)
        # A course site spans years and has no per-semester schedule.yml to read (that's
        # semester-scoped), so the date is a neutral fallback that only orders the session
        # entries.
        start = date(2025, 1, 1)

        # Wipe only THIS source's served subtree (idempotent re-publish; multi-repo safe).
        served_root = site_wd / PUBLIC_MATERIALS_DIR / source_repo
        if served_root.exists():
            shutil.rmtree(served_root)

        lecture_entries: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as work:
            src, spec = Path(work) / "src", f"{course_org}/{source_repo}"
            if not clone(course_org, source_repo, src):
                log_err(f"could not clone {spec}")
                return None

            # One rule (`selection`): the institution denylist, faculty's
            # `.releaseignore`, `publish:` and the institution's floor, anchored at the
            # CLONE root even though what gets copied is a session folder deep inside it.
            def withhold(section: str):
                kind = _section_kind(section)
                return deny(src, use=sel, kind_of=lambda _rel: kind, limit=limit)

            withheld = releaseignored_in(src)
            # A reading list NAMES files, so it is a copy of a kind too: the denylist,
            # `.releaseignore` and `publish.exclude` hold for it as for the files.
            listed_names = Selection(exclude=sel.exclude)

            # Both readings of the source's structure come off THE CLONE - which every
            # session below is copied out of anyway. The session list used to be a
            # recursive git-tree fetch of a repo this function had just downloaded whole.
            sessions = discover_local_sessions(src)
            log_step(
                f"Publishing {course_org}/{pages_repo(course_org)} from {source_repo}: "
                f"{len(sessions)} session(s), kinds={', '.join(sel.kinds or ()) or '(none)'}, "
                f"reading lists={'on' if oc.reading_lists else 'off'}"
            )

            # Sections are whatever THIS repo has (the same discovery the release workflows
            # use), not a hardcoded lectures/readings pair - a course whose content lives
            # in `labs/` publishes labs. `readings` is the one section with special
            # semantics (`reading_lists`, below). Supporting-files sections (any folder no
            # kind names) are never rows here either.
            file_sections = shown_sections(
                [sec for sec in discover_sections(src) if sec != READINGS_SECTION]
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
                section_links: list[tuple[str, list[Link]]] = []
                lab_links: list[tuple[str, list[Link]]] = []
                reading_list_md = ""

                for section in file_sections:
                    sec_src = find_session_dir(src / section, s)
                    if sec_src is None:
                        continue
                    dest = site_session / section
                    copy_tree(sec_src, dest, withhold(section))
                    links = _public_links(dest, f"{url_base}/{section}")
                    if links:
                        rows = (
                            lab_links if row_kind(section) == "lab" else section_links
                        )
                        rows.append((section, links))

                read_src = find_session_dir(src / READINGS_SECTION, s)
                if read_src is not None:
                    dest = site_session / READINGS_SECTION
                    copy_tree(read_src, dest, withhold(READINGS_SECTION))
                    links = _public_links(dest, f"{url_base}/{READINGS_SECTION}")
                    if links:
                        section_links.append((READINGS_SECTION, links))
                    if oc.reading_lists:
                        reading_list_md = _reading_list_md(
                            read_src,
                            lambda p: kept(
                                p.relative_to(src).as_posix(),
                                use=listed_names,
                                withheld=withheld,
                            ),
                        )

                # A row with nothing published gets no page at all, rather than an empty
                # one the public would click through to.
                if not section_links and not lab_links and not reading_list_md:
                    log(f"  (session {s}: nothing to publish - no page)")
                    continue
                when = start + timedelta(days=int(s) * 7)
                if section_links or reading_list_md:
                    lecture_entries[row_file(s, "lecture")] = _public_lecture_entry(
                        s, when, section_links, reading_list_md
                    )
                if lab_links:
                    lecture_entries[row_file(s, "lab")] = _public_lecture_entry(
                        s, when, lab_links, "", "lab"
                    )

        config = {}
        identity = course_block(meta)
        if identity.get("name"):
            config["course_name"] = str(identity["name"])
        if identity.get("code"):
            config["course_code"] = str(identity["code"])
        # Always written, blank included: the seed's placeholder paragraph must never
        # reach the open web (the home page drops the section when it is blank).
        config["course_description"] = str(identity.get("description") or "").strip()
        config["course_semester"] = "Open Courseware"  # neutral: the site is multi-year
        # The public open-courseware site belongs to the COURSE org (multi-year), so its
        # footer links there - unlike a semester site, which links its semester org.
        config["github_org"] = course_org

        return SitePlan(
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
                # TAs are declared per semester (in each semester's semester.yml), never
                # course-level.
                "_data/people.yml": people_yaml(
                    course_org,
                    meta,
                    edit_at=f"the `people:` block of {course_org}/.github/course.yml",
                    include_tas=False,
                ),
                "_data/course.yml": course_facts(meta),
                "README.md": site_readme(course_org, semester=False),
                # No semester repos to index, so `/materials/` stays the readings page.
                "_data/nav.yml": nav_yaml(semester=False),
                "_data/kinds.yml": kinds_yaml(),
                **theme_pages(semester=False),
                # The course-specific layouts, includes and stylesheet - shipped
                # from templates/site/, not from the shared theme, so a change to
                # how a session renders is tested against the generator that
                # writes its front matter before any site sees it.
                **site_templates(),
            },
            commit=f"site: publish public course site from {source_repo}",
            label="public site",
            title="Public website",
            done="published",
        )

    return sync_site_repo(course_org, build, scaffold_missing=True)


def publish(course_org: str, *, daily: bool = False) -> int:
    """Publish the public website as the course's `open_course_site:` declares it.

    `daily` is the cron's catch-up: a course with no `open_course_site:`, or one that says
    `enabled: false`, is a one-line no-op there, since the cron ships in every course org
    and most never publish. Asked for by a person, the same answer is a refusal with the
    sentence that says what to change."""
    where = f"{course_org}/.github/{OPENCOURSE_FILE}"
    try:
        oc = read_opencourse(course_org)
    except Unusable as exc:
        text = f"The public website settings do not parse: {exc}."
        log_err(text)
        return Summary(text, reasons=[{"code": "UNUSABLE", "text": text}], code=1)
    if oc is None or not oc.enabled:
        text = (
            "The public website is off. Turn it on under Public website, then publish."
        )
        if daily:
            log(f"{where} is absent or says enabled: false - nothing to publish")
            return Summary(text, conclusion="nothing_to_do")
        log_err(text)
        return Summary(text, reasons=[{"code": "WEBSITE_OFF", "text": text}], code=1)
    log_step(f"Publishing from {where}")
    return sync_public_site(course_org, oc)
