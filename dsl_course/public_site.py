"""dsl-course public course site - the open-courseware half of the site build.

The course org's `<course-org>.github.io` is PUBLIC and opt-in. The `course-materials-*`
repos it publishes are private, so linking into them would 404 for the public; instead
this HOSTS the chosen repo's files in the site repo (Jekyll serves any path not starting
with `_`) and links to site-relative URLs. Session materials only - no assignments, no
events, no cohort repos. The first publish is a manual click that persists its settings
into the site repo (`PUBLISH_CONFIG`); the daily cron then re-syncs from those.

Driven through `python3 -m dsl_course.site public-sync`, which delegates here.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import yaml

from .course import discover_sections, find_session_dir, pages_repo
from .discovery import discover_sessions
from .gh_contents import get_file_content
from .ghcli import gh
from .log import log, log_err, log_step
from .readings import readings_block
from .repos import has_denied_component, is_denied_publication, repo_exists
from .schedule_plan import READINGS_SECTION, row_kind
from .site_repo import (
    PUBLISH_CONFIG,
    ROW_NOUN,
    SitePlan,
    block,
    iso_when,
    links_block,
    nav_yaml,
    people_yaml,
    row_file,
    site_readme,
    site_templates,
    sync_site_repo,
    theme_pages,
    yaml_file,
)

# Public course site: served folder for the hosted section files.
PUBLIC_MATERIALS_DIR = "public-materials"


def _publication_ignore(dirpath: str, names: list[str]) -> set[str]:
    """A `copytree` ignore filter for the PUBLIC site: drop every denylisted name, at any
    depth (see repos.PUBLICATION_DENYLIST).

    At any depth, unlike `deploy._copy_ignore`, which anchors its exclusions to the repo
    root: the release path excludes plumbing that only ever lives at the root, while the
    thing this exists to stop - a `solution/` beside the lab it answers - is precisely a
    nested folder."""
    return {n for n in names if is_denied_publication(n)}


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
    # Denylisted paths are already absent from a folder THIS run copied
    # (`_publication_ignore`), so this is the second lock on the same door - and the one
    # that holds if a file ever reaches the served tree by another route.
    rels = [
        rel
        for rel in (q.relative_to(local_dir).as_posix() for q in files)
        if not has_denied_component(rel)
    ]
    if any("/" not in rel for rel in rels):
        rels = [rel for rel in rels if "/" not in rel]
    return [(rel, f"{url_prefix}/{quote(rel)}") for rel in rels]


def _reading_list_md(readings_session_dir: Path) -> str:
    """The readings rendered as TEXT for `reading-list` mode (no files hosted, no links).

    `readings_block`'s rule over a local directory: the overlay's prose inlined verbatim,
    then every other file by NAME only - so the public sees WHAT to read without the
    copyrighted bytes being published. This mode links nothing, so naming the files here is
    the only way they appear at all."""
    d = readings_session_dir
    return readings_block(
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
    links = links_block(section_links)
    title = f"{ROW_NOUN[kind]} {session}"
    return (
        f"---\n"
        f"type: {kind}\n"
        f"date: {iso_when(when)}\n"
        f'title: "{title}"\n'
        + (block("reading_list", reading_list_md) if reading_list_md else "")
        + f"{links}\n"
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
    actually has (see course.discover_sections), plus, in `actual-readings` mode, `readings`
    - in the public site repo and links to them with site-relative URLs. `reading-list` mode
    publishes the citation text only. `include_lectures` toggles the file sections as a
    group (its name predates generic sections; the workflow input is unchanged). Session
    materials only - no assignments/events. Served files are namespaced per source repo
    so several years can coexist on one site."""
    if not include_lectures and readings_mode == "none":
        log_err("nothing to publish - file sections off and readings set to none.")
        return 1

    def build(site_wd: Path) -> SitePlan | None:
        sessions = discover_sessions(course_org, source_repo)
        log_step(
            f"Publishing {course_org}/{pages_repo(course_org)} from {source_repo}: "
            f"{len(sessions)} session(s), readings={readings_mode}, "
            f"file sections={'on' if include_lectures else 'off'}"
        )
        meta = yaml_file(course_org, ".github", "dsl-course.yml")
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

            # Sections are whatever THIS repo has (the same discovery the release workflows
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
                    shutil.copytree(
                        sec_src, dest, dirs_exist_ok=True, ignore=_publication_ignore
                    )
                    links = _public_links(dest, f"{url_base}/{section}")
                    if links:
                        rows = (
                            lab_links if row_kind(section) == "lab" else section_links
                        )
                        rows.append((section, links))

                read_src = find_session_dir(src / READINGS_SECTION, s)
                if read_src is not None:
                    if readings_mode == "actual-readings":
                        dest = site_session / READINGS_SECTION
                        shutil.copytree(
                            read_src,
                            dest,
                            dirs_exist_ok=True,
                            ignore=_publication_ignore,
                        )
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
                    lecture_entries[row_file(s, "lecture")] = _public_lecture_entry(
                        s, when, section_links, reading_list_md
                    )
                if lab_links:
                    lecture_entries[row_file(s, "lab")] = _public_lecture_entry(
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
                # TAs are declared per cohort (in each cohort's people.yml), never
                # course-level.
                "_data/people.yml": people_yaml(
                    course_org,
                    meta,
                    edit_at=f"the `people:` block of {course_org}/.github/dsl-course.yml",
                    include_tas=False,
                ),
                "README.md": site_readme(course_org, cohort=False),
                # No cohort repos to index, so `/materials/` stays the readings page.
                "_data/nav.yml": nav_yaml(cohort=False),
                **theme_pages(cohort=False),
                # The course-specific layouts, includes and stylesheet - shipped
                # from templates/site/, not from the shared theme, so a change to
                # how a session renders is tested against the generator that
                # writes its front matter before any site sees it.
                **site_templates(),
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

    return sync_site_repo(course_org, build, scaffold_missing=True)


def resync_public_site(course_org: str) -> int:
    """Re-publish the public course site from the settings the last publish persisted.

    The daily cron path: a materials edit then reaches the public site without anyone
    re-running the workflow. Opting in is still a deliberate manual publish, so a course org
    with no public site - or a site with no `PUBLISH_CONFIG` (published before this existed,
    or deliberately unhooked by deleting the file) - is a one-line no-op, NOT a failure:
    the cron ships in every course org's `.github`, and most never publish."""
    site = pages_repo(course_org)
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
