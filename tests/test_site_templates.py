"""The Jekyll templates in `templates/site/` against the front matter site.py writes.

The two halves of the cohort site live in one repo now, and this is what holds them
together: every key a template READS must be a key the sync WRITES, and every flag the
sync writes must be read by something. Neither side fails loudly on its own - a template
reading `page.due` (which never existed) prints an empty string, and a flag nothing reads
is dead weight that looks live. Both went unnoticed for a term.

The generated side is the real fixture site (tests/fixtures/site/build_fixture.py), the
same one the `jekyll-contract` CI job builds with Jekyll - so the offline key check here
and the build there cannot disagree about what a cohort site contains.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from functools import cache
from pathlib import Path

import pytest
import yaml

from dsl_course import ghcli, public_site, site, site_repo

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "site"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "build_fixture", FIXTURE_DIR / "build_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_fixture = _load_builder()

STAMP = (
    "SYSTEM-OWNED - do not edit. Written by the DSL course sync from templates/site/ in "
    "the toolkit; every sync rewrites it."
)

# ---------------------------------------------------------------------------
# What the templates read
# ---------------------------------------------------------------------------

# Stripped before anything is extracted. A Liquid comment is where these files explain
# themselves, and they quote field names constantly - including ones that were removed on
# purpose. An HTML comment is the same.
_LIQUID_COMMENT = re.compile(
    r"\{%-?\s*comment\s*-?%\}.*?\{%-?\s*endcomment\s*-?%\}", re.DOTALL
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

_DATA_KEY = re.compile(r"\bsite\.data\.([A-Za-z0-9_.]+)")
# `page.x`, `entry.x`, `event.x` and `include.<anything>.x` all name a field on a document
# the sync generated - a collection entry, or (for `include.entry` inside
# materials_entry.html) a node of the All Materials tree.
_DOC_KEY = re.compile(
    r"\b(?:page|entry|event)\.([A-Za-z0-9_.]+)|\binclude\.[A-Za-z0-9_]+\.([A-Za-z0-9_.]+)"
)

# Liquid's own accessors on any value, not fields of ours.
_LIQUID_ACCESSORS = ("size", "first", "last")

# Jekyll puts these on every document whatever its front matter says.
JEKYLL_DOC_FIELDS = frozenset({"url", "content"})

# Front matter the sync deliberately never writes: hand-authored fields a template renders
# only behind an `{% if %}`. Listed rather than tolerated, so the check below stays an
# assertion about everything else.
HAND_AUTHORED = frozenset({"pdf", "attachment", "solutions"})


def _strip_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", _LIQUID_COMMENT.sub("", text))


def _trim_accessor(key: str) -> str:
    """`links.size` is a read of `links`; Liquid supplies the `.size`."""
    head, _, tail = key.rpartition(".")
    return head if head and tail in _LIQUID_ACCESSORS else key


def _reads(text: str) -> tuple[set[str], set[str]]:
    """(`site.data.*` paths, document fields) one template reads."""
    body = _strip_comments(text)
    data = {_trim_accessor(m.group(1)) for m in _DATA_KEY.finditer(body)}
    docs = {_trim_accessor(m.group(1) or m.group(2)) for m in _DOC_KEY.finditer(body)}
    return data, docs


# ---------------------------------------------------------------------------
# What the sync writes
# ---------------------------------------------------------------------------


def _front_matter(text: str) -> dict:
    _, _, rest = text.partition("---\n")
    block, _, _body = rest.partition("\n---\n")
    return yaml.safe_load(block) or {}


def _field_paths(value: object, prefix: str = "") -> set[str]:
    """Every dotted field path in a generated document - `due_event` and
    `due_event.date` both, since a template may read either."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            found.add(f"{prefix}{key}")
            found |= _field_paths(sub, f"{prefix}{key}.")
    elif isinstance(value, list):
        for item in value:
            found |= _field_paths(item, prefix)
    return found


@pytest.fixture(scope="module")
def generated() -> dict:
    """The whole generated cohort site, once."""
    return build_fixture.generated()


@pytest.fixture(scope="module")
def documents(generated) -> list[dict]:
    return [
        _front_matter(text)
        for entries in generated["collections"].values()
        for text in entries.values()
    ]


@pytest.fixture(scope="module")
def site_data(generated) -> dict:
    """`site.data` as Jekyll would see it: the generated `_data/*.yml` plus the seeded ones
    (late_policy, previous_offering)."""
    data = {
        Path(rel).stem: yaml.safe_load(text)
        for rel, text in site_repo.seed_templates().items()
        if rel.startswith("_data/")
    }
    for rel, text in generated["files"].items():
        if rel.startswith("_data/"):
            data[Path(rel).stem] = yaml.safe_load(text)
    return data


def _tree_nodes(nodes: list[dict]) -> list[dict]:
    """Every node of the All Materials tree, flat.

    Flat, and its fields read unprefixed, because `include.entry` IS one node at whatever
    depth the recursion has reached - so a key only a leaf carries (`view_url`, on a file
    the site hosts its own copy of) is read exactly the way one every level carries is."""
    return [
        n for node in nodes for n in [node, *_tree_nodes(node.get("entries") or [])]
    ]


@pytest.fixture(scope="module")
def written_fields(documents, generated) -> set[str]:
    """Every field a template may legitimately read off a document.

    The union of the collection entries' front matter and the All Materials tree's nodes,
    because `include.entry` means a collection entry in session_entry.html and a tree node
    in materials_entry.html - one namespace either way as far as an extracted key can
    tell."""
    fields = {p for doc in documents for p in _field_paths(doc)}
    index = yaml.safe_load(generated["files"]["_data/materials.yml"])
    return fields | {
        key for node in _tree_nodes(index.get("sections") or []) for key in node
    }


def _templates() -> dict[str, str]:
    return site_repo.site_templates()


def _liquid_templates() -> dict[str, str]:
    return {rel: text for rel, text in _templates().items() if rel.endswith(".html")}


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", sorted(_templates()))
def test_every_shipped_template_states_that_the_sync_owns_it(rel):
    # In the comment syntax of its own language, and for a layout INSIDE the front matter:
    # Jekyll needs `---` on line 1, so a notice above it breaks the page.
    text = _templates()[rel]
    flat = " ".join(text.replace("#", " ").replace("//", " ").split())
    assert STAMP in flat, rel
    if rel.startswith("_layouts/"):
        head = text.split("---\n")[1]
        assert STAMP in " ".join(head.replace("#", " ").split()), rel


def test_the_sync_ships_every_template_to_both_kinds_of_site(cohort_plan, public_plan):
    # A template that reaches the cohort sites but not the public course site is how the
    # open-courseware build breaks on a layout it never received.
    for plan in (cohort_plan, public_plan):
        missing = set(_templates()) - set(plan.files)
        assert not missing


# The schedule table's structural classes. The theme used to define them, so a course
# site got its table layout from a repo the toolkit does not ship; a theme release that
# drops them flattens every schedule, materials and assignments table on every live site.
_TABLE_TEMPLATES = ("_layouts/schedule.html",) + tuple(
    rel for rel in _templates() if rel.startswith("_includes/schedule_row_")
)
# Page chrome the theme still owns.
_THEME_CHROME = frozenset({"home", "post-header", "post-title"})
_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_SCSS_COMMENT = re.compile(r"//.*?$|/\*.*?\*/", re.DOTALL | re.MULTILINE)


def _classes(text: str) -> set[str]:
    """The static classes a template puts on an element - a Liquid-interpolated one
    (`table-row-{{ event.type }}`) names no single class, so it is skipped."""
    return {
        name
        for m in _CLASS_ATTR.finditer(_strip_comments(text))
        for name in m.group(1).split()
        if "{" not in name and "}" not in name
    }


# Everything else the toolkit renders and so has to style itself: the Updates box's inline
# source link, the row of buttons beside every other file link, and the form that fills in
# the profile the last two of those buttons need.
_OWN_CLASSES = frozenset(
    {
        "file-actions",
        "file-btns",
        "file-btn",
        "open-in-line",
        "profile-steps",
        "profile-step",
        "profile-step-title",
        "profile-step-num",
        "profile-field",
        "profile-label",
        "profile-choice",
        "profile-hint",
        "profile-buttons",
        "profile-entry",
        "profile-saved",
        "btn--field",
    }
)


@pytest.mark.parametrize(
    "cls",
    sorted(
        (
            {c for rel in _TABLE_TEMPLATES for c in _classes(_templates()[rel])}
            - _THEME_CHROME
        )
        | _OWN_CLASSES
    ),
)
def test_the_shipped_stylesheet_defines_every_class_the_toolkit_owns(cls):
    scss = _SCSS_COMMENT.sub("", _templates()["_sass/_course.scss"])
    assert re.search(rf"\.{re.escape(cls)}(?![-\w])", scss), (
        f".{cls} is rendered by a shipped template but no rule in "
        f"_sass/_course.scss defines it"
    )


def test_an_empty_site_checkout_is_seeded_with_everything_a_site_needs(tmp_path):
    # A site repo is created EMPTY, so the first sync is what puts `_config.yml`, the
    # landing page and the Gemfile there - and the config half writes nothing at all
    # until the file exists.
    site_repo.apply_plan(
        tmp_path,
        site_repo.SitePlan(
            config={"course_name": "Deep Learning"},
            collections={},
            commit="site: sync",
        ),
    )
    cfg = (tmp_path / "_config.yml").read_text(encoding="utf-8")
    assert 'course_name: "Deep Learning"' in cfg
    assert f"{site_repo.THEME_REPO}@{site_repo.THEME_REF}" in cfg
    assert "collections:" in cfg
    for rel in ("index.md", "schedule.md", "Gemfile", "_data/late_policy.yml"):
        assert (tmp_path / rel).is_file(), rel


def test_the_sync_writes_the_course_name_as_jekylls_own_title(tmp_path):
    # The theme falls back to `site.title` where a page has no `course_name`, and
    # jekyll-feed titles the feed with it. Left unset, jekyll-github-metadata tries to
    # synthesise one from the repository and fails the build when it cannot name it.
    # UPSERTED, not replaced: a site seeded before this has no `title:` line at all.
    (tmp_path / "_config.yml").write_text('course_name: "old"\n', encoding="utf-8")
    site_repo.apply_plan(
        tmp_path,
        site_repo.SitePlan(
            config={"course_name": "Deep Learning"},
            collections={},
            commit="site: sync",
        ),
    )
    cfg = (tmp_path / "_config.yml").read_text(encoding="utf-8")
    assert 'title: "Deep Learning"' in cfg


def test_a_plan_that_declares_no_course_name_leaves_the_title_alone(tmp_path):
    # The identity keys are only written when the course declares them, and `title` is
    # one of them - a sync with nothing to say must not blank the site's own heading.
    (tmp_path / "_config.yml").write_text('title: "Mine"\n', encoding="utf-8")
    site_repo.apply_plan(
        tmp_path, site_repo.SitePlan(config={}, collections={}, commit="site: sync")
    )
    assert 'title: "Mine"' in (tmp_path / "_config.yml").read_text(encoding="utf-8")


def test_the_seeded_config_carries_a_title_of_its_own(tmp_path):
    # The seed is what a site has before any course identity reaches it, and the build
    # has to survive that window.
    assert re.search(r"(?m)^title:", site_repo.seed_templates()["_config.yml"]), (
        "templates/site-seed/_config.yml has no title: key"
    )


def test_the_ci_fixture_bundles_the_metadata_plugin_a_live_site_loads():
    # Live sites carry the instructor-owned Gemfile they were seeded with, which bundles
    # github-pages, and Jekyll requires the :jekyll_plugins group whatever `plugins:`
    # says - so jekyll-github-metadata runs on a real site whether its config asks for it
    # or not. Out of this bundle, neither CI job can watch it fail, which is how theme
    # v2.0.0 went out green.
    gemfile = (FIXTURE_DIR / "Gemfile").read_text(encoding="utf-8")
    assert "group :jekyll_plugins do" in gemfile
    assert "jekyll-github-metadata" in gemfile


def test_a_seeded_file_the_site_already_has_is_left_alone(tmp_path):
    # Seed-once: everything under templates/site-seed/ is the faculty's once written.
    mine = "---\nlayout: home\n---\n\nmy own words\n"
    (tmp_path / "index.md").write_text(mine, encoding="utf-8")
    site_repo.apply_plan(
        tmp_path, site_repo.SitePlan(config={}, collections={}, commit="site: sync")
    )
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == mine


def test_the_fixture_config_pins_the_theme_the_toolkit_ships(tmp_path):
    # CI's `jekyll-theme-seam` job builds this fixture against the real theme and reads
    # the ref out of the generated `_config.yml` rather than repeating it. A fixture that
    # stopped emitting a pin would build unthemed and pass on nothing.
    build_fixture.build(tmp_path)
    cfg = (tmp_path / "_config.yml").read_text(encoding="utf-8")
    assert f"{site_repo.THEME_REPO}@{site_repo.THEME_REF}" in cfg


_IMG_TAG = re.compile(r"<img\b[^>]*>")


@pytest.mark.parametrize("rel", sorted(_liquid_templates()))
def test_every_image_a_template_renders_carries_alt_text(rel):
    # A screen reader announces an alt-less portrait as its file name.
    for tag in _IMG_TAG.finditer(_strip_comments(_liquid_templates()[rel])):
        assert re.search(r'\balt="[^"]+"', tag.group(0)), f"{rel}: {tag.group(0)}"


_WHEN_INCLUDE = re.compile(
    r'{%\s*when\s+"(?P<kind>[^"]+)"\s*%}{%\s*include\s+(?P<include>\S+)'
)


def test_a_schedule_row_template_exists_for_every_type_the_sync_emits(documents):
    # The schedule dispatches on `type`, so a row kind added to site.py without a branch
    # renders as the neutral fallback row - silently, on the live schedule. What each
    # branch includes is its own business, so this checks only that the branch exists and
    # that the template it names ships.
    emitted = {doc["type"] for doc in documents if doc.get("type")}
    emitted |= {doc["due_event"]["type"] for doc in documents if doc.get("due_event")}
    branches = {
        m["kind"]: m["include"]
        for m in _WHEN_INCLUDE.finditer(_templates()["_layouts/schedule.html"])
    }
    for kind in sorted(emitted):
        assert kind in branches, kind
        assert f"_includes/{branches[kind]}" in _templates(), kind


@pytest.mark.parametrize("rel", sorted(_liquid_templates()))
def test_every_document_field_a_template_reads_is_one_the_sync_writes(
    rel, written_fields
):
    _data, fields = _reads(_liquid_templates()[rel])
    unknown = fields - written_fields - JEKYLL_DOC_FIELDS - HAND_AUTHORED
    assert not unknown, f"{rel} reads {sorted(unknown)}, which nothing writes"


@pytest.mark.parametrize("rel", sorted(_liquid_templates()))
def test_every_data_file_a_template_reads_is_one_the_site_has(rel, site_data):
    data, _fields = _reads(_liquid_templates()[rel])
    for path in sorted(data):
        node = site_data
        for part in path.split("."):
            assert isinstance(node, dict) and part in node, (
                f"{rel} reads site.data.{path}, which no _data file provides"
            )
            node = node[part]


# Flags the renderers write purely so a template can branch on them - they carry no text
# of their own. One that nothing reads is a state the site silently stopped showing.
@pytest.mark.parametrize(
    "flag",
    [
        "unreleased",
        "readings_pending",
        "handout_pending",
        "tbc",
        "dateless",
        "hide_time",
        "announce",
        "repo_url",
        "repo_name",
        "submit_external",
        "submit_url",
        "submit_host",
        "due_event",
    ],
)
def test_every_flag_the_sync_writes_is_read_by_a_template(flag, written_fields):
    assert flag in written_fields, f"the fixture never exercises {flag}"
    read = {
        field for text in _liquid_templates().values() for field in _reads(text)[1]
    } | {
        # The schedule reaches the due row through `map: "due_event"` rather than a field
        # read, so the sub-hash is named in a filter argument.
        m
        for text in _liquid_templates().values()
        for m in re.findall(r'map:\s*"([A-Za-z0-9_]+)"', text)
    }
    assert flag in read, f"nothing renders {flag}"


# The `{% if ...submit_external %}...{% elsif ... %}...{% endif %}` either template wraps
# its where-to-submit wording in. Three shapes now, so the branch is an `elsif`: an
# external assignment has no repo, a github one has, and a pending one has neither.
_EXTERNAL_BRANCH = re.compile(
    r"{%-?\s*if\s+[\w.]*\bsubmit_external\s*-?%}(?P<external>.*?)"
    r"{%-?\s*elsif\b(?P<github>.*?){%-?\s*endif\s*-?%}",
    re.DOTALL,
)


@pytest.mark.parametrize(
    "rel", ["_layouts/assignment.html", "_includes/schedule_row_due.html"]
)
def test_only_a_github_assignment_is_told_to_submit_by_pushing(rel):
    # The two places that say where the work goes. Both printed the push sentence off
    # `repo_url` alone, so an assignment handed in on Moodle told the cohort to push to
    # `main`; and both then named a repo for it, which since the handout stopped creating
    # one does not exist. The state is the assignment's own definition's to declare, so
    # every word about a repo has to sit behind the flag that carries it.
    body = _strip_comments(_templates()[rel])
    halves = [(m["external"], m["github"]) for m in _EXTERNAL_BRANCH.finditer(body)]
    assert halves, f"{rel} does not branch on submit_external"
    for external, _github in halves:
        assert "submit_url" in external or "outside GitHub" in external, (
            f"{rel} says nothing in the external case"
        )
    for phrase in ("push", "Submit via", "repo_name", "repo_url"):
        behind = sum(github.count(phrase) for _external, github in halves)
        assert body.count(phrase) == behind, (
            f"{rel} says {phrase!r} outside the submit_external branch"
        )


def test_an_external_assignments_page_has_no_repo_for_a_profile_to_rewrite(generated):
    # `open_in.html` rewrites every `data-dsl-repo` on a page to the reader's OWN repo
    # name, off `page.repo_name`. An external assignment has neither, so there is nothing
    # to substitute into - and a marker left outside the github branch would render as
    # `-jane` with nothing in front of it.
    body = _strip_comments(_templates()["_layouts/assignment.html"])
    halves = [(m["external"], m["github"]) for m in _EXTERNAL_BRANCH.finditer(body)]
    behind = sum(github.count("data-dsl-repo") for _external, github in halves)
    assert body.count("data-dsl-repo") == behind
    page = _front_matter(generated["collections"]["_assignments"]["03-assignment-3.md"])
    assert page["submit_external"] is True
    assert "repo_name" not in page and "repo_url" not in page
    assert page["submit_host"] == "moodle.example.edu"
    assert "repo_name" not in page["due_event"]


# ---------------------------------------------------------------------------
# plan.retire
# ---------------------------------------------------------------------------

ORG = "Cohort-f2026"


@pytest.fixture
def cohort_plan(monkeypatch, tmp_path):
    """The `_SitePlan` a real cohort sync builds, against a faked org."""
    captured: dict = {}
    monkeypatch.setattr(
        site,
        "sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site, "list_org_repos", lambda org: [])
    monkeypatch.setattr(site, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site, "discover_assignments", lambda org: [])
    monkeypatch.setattr(site, "yaml_file", lambda *a: {})
    monkeypatch.setattr(site.schedule, "load", lambda org: site.schedule.Schedule())
    monkeypatch.setattr(site, "people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(site, "_repo_tree", cache(lambda org, repo: ("main", ())))
    assert site.sync_site("Course-Org", ORG) == 0
    return captured["plan"]


@pytest.fixture
def public_plan(monkeypatch, tmp_path):
    """The `_SitePlan` a public course-site publish builds."""
    captured: dict = {}
    monkeypatch.setattr(
        public_site,
        "sync_site_repo",
        lambda org, build, **kw: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(public_site, "discover_sections", lambda src: [])
    monkeypatch.setattr(public_site, "yaml_file", lambda *a: {})
    monkeypatch.setattr(public_site, "people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(ghcli, "gh", lambda *a, **k: (0, ""))
    assert public_site.sync_public_site("Course-Org", "course-materials-f2026") == 0
    return captured["plan"]


def _clone_with(files: dict[str, str]):
    """A `gh repo clone` double that hands back a real one-commit git checkout."""

    def clone(*args, **kwargs):
        if args[:2] != ("repo", "clone"):
            return (0, "")
        wd = Path(args[3])
        wd.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(wd), "init", "-q"], check=True)
        for rel, body in files.items():
            (wd / rel).parent.mkdir(parents=True, exist_ok=True)
            (wd / rel).write_text(body)
        subprocess.run(["git", "-C", str(wd), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(wd), *site_repo.GIT_ENV, "commit", "-q", "-m", "seed"],
            check=True,
        )
        return (0, "")

    return clone


def test_a_failed_push_reports_what_git_said(monkeypatch, capsys):
    # A push-protection block or a 403 surfaced as a bare "<label> push failed": git had
    # already printed the one actionable line and it was thrown away, so the daily site
    # cron reported a failure nobody could act on.
    monkeypatch.setattr(site_repo, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(ghcli, "gh", _clone_with({"README.md": "old\n"}))
    blocked = (
        "Enumerating objects: 5, done.\n"
        "remote: error: GH013: Repository rule violations found\n"
        "remote: - GITHUB PUSH PROTECTION\n"
        "! [remote rejected] HEAD -> main (push declined)\n"
    )
    real_git = site_repo.git
    monkeypatch.setattr(
        site_repo,
        "git",
        lambda *a: (1, blocked) if "push" in a else real_git(*a),
    )

    def build(_wd: Path) -> site_repo.SitePlan:
        return site_repo.SitePlan(
            config={},
            collections={},
            files={"README.md": "new\n"},
            retire=(),
            commit="site: sync",
        )

    assert site_repo.sync_site_repo(ORG, build) == 1
    err = capsys.readouterr().err
    assert "GITHUB PUSH PROTECTION" in err
    assert "push declined" in err


def test_a_retired_path_leaves_the_site_repo_and_the_rest_stays(monkeypatch):
    # `files` cannot express a removal - the apply step is `git add -A` - so without
    # `retire` a template the toolkit stops shipping stays on every live site forever.
    tracked: dict = {}
    monkeypatch.setattr(site_repo, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(
        ghcli,
        "gh",
        _clone_with({"_layouts/class.html": "old\n", "_layouts/page.html": "keep\n"}),
    )
    real_git = site_repo.git
    monkeypatch.setattr(
        site_repo,
        "git",
        lambda *a: (0, "") if "push" in a else real_git(*a),
    )
    monkeypatch.setattr(
        site_repo,
        "_overwritten_edits",
        lambda wd: (
            tracked.update(
                files=subprocess.run(
                    ["git", "-C", str(wd), "ls-files"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.split()
            )
            or {}
        ),
    )

    def build(_wd: Path) -> site_repo.SitePlan:
        return site_repo.SitePlan(
            config={},
            collections={},
            files={"_layouts/new.html": "new\n"},
            retire=("_layouts/class.html", "_layouts/never-existed.html"),
            commit="site: sync",
        )

    assert site_repo.sync_site_repo(ORG, build) == 0
    # Retired and gone; a retired path the site never had is not an error; everything
    # else the site holds is untouched.
    assert "_layouts/class.html" not in tracked["files"]
    assert "_layouts/page.html" in tracked["files"]
    assert "_layouts/new.html" in tracked["files"]


# ---------------------------------------------------------------------------
# The hosted copy of a published file
# ---------------------------------------------------------------------------

# The three pages that show a file link - a session row, the Updates box, the All
# Materials tree. All three go through one include, which is what stops the same file
# opening rendered on one page and as source on another.
_LINK_TEMPLATES = (
    "_includes/session_entry.html",
    "_includes/lecture_links.html",
    "_includes/materials_entry.html",
)

_VIEW_BRANCH = re.compile(
    r"{%-?\s*if\s+[\w.]*\bview_url\s*-?%}(?P<shown>.*?){%-?\s*endif\s*-?%}", re.DOTALL
)


@pytest.mark.parametrize("rel", _LINK_TEMPLATES)
def test_every_page_that_shows_a_file_link_goes_through_the_one_include(rel):
    assert "include file_link.html" in _strip_comments(_templates()[rel])


def test_render_is_offered_only_where_the_site_hosts_a_copy():
    # `render` opens the cohort site's own copy, which exists only for a published file a
    # browser draws (html, pdf). Rendered unconditionally it is a button to a 404 on every
    # other row of every site.
    body = _strip_comments(_templates()["_includes/file_link.html"])
    branches = [m["shown"] for m in _VIEW_BRANCH.finditer(body)]
    assert branches, "file_link.html does not branch on view_url"
    assert (
        body.count(">render</a>") == sum(b.count(">render</a>") for b in branches) == 1
    )
    # `source` is the opposite: unconditional in the button row, because the file's home
    # on GitHub is worth a button whether or not anything else is. The Updates box's
    # inline shape keeps it conditional - there the name itself goes to GitHub unless a
    # hosted copy has taken it.
    assert body.count(">source</a>") == 2
    assert sum(b.count(">source</a>") for b in branches) == 1
    # And the FIRST branch is the name's own href, so a hosted copy is what it opens.
    assert body.index("include.view_url") < body.index("file-btns")


def test_every_button_in_a_file_row_opens_a_new_tab():
    # Every one of them leaves the page the reader is working through - a repo, a rendered
    # deck, an editor in the browser - and taking the list away to show one of them is how
    # a student loses their place in it. `rel=noopener` comes with `target=_blank`.
    body = _strip_comments(_templates()["_includes/file_link.html"])
    for button in body.split('<a class="file-btn"')[1:]:
        head = button.split(">")[0]
        assert 'target="_blank"' in head and 'rel="noopener"' in head, head
    # The NAME is not one of them: it is the quiet link the page has always had, and on a
    # site that publishes nothing it is the only link in the row.
    assert 'target="_blank"' not in body.split('<span class="file-btns">')[0]


def test_the_updates_box_takes_the_inline_shape_of_the_source_link():
    # The box brackets every file it lists, so the bracketed control would read
    # `[slides.html [source]]`. One include, two shapes, and the caller says which.
    assert "inline=true" in _strip_comments(
        _templates()["_includes/lecture_links.html"]
    )
    link = _strip_comments(_templates()["_includes/file_link.html"])
    assert "include.inline" in link
    assert "· <a href=" in link
    # And no button row inside the sentence.
    assert (
        "file-btns"
        not in link.split("{% if include.inline %}")[1].split("{% else %}")[0]
    )
    # The lists that bracket nothing keep the bracketed one.
    for rel in ("_includes/session_entry.html", "_includes/materials_entry.html"):
        assert "inline" not in _strip_comments(_templates()[rel])


def test_the_shared_link_include_adds_no_whitespace_of_its_own():
    # It renders inside a sentence in the Updates box, where a stray newline is a visible
    # space in the middle of `[name]`.
    body = _templates()["_includes/file_link.html"]
    # No trailing newline of its own, and the comment above the markup trims the one after
    # it (`-%}`).
    assert body.endswith("{% endif %}")
    assert "-%}\n<a" in body


def test_the_fixture_site_holds_both_shapes_of_file_row(generated):
    # The fixture is what CI builds with Jekyll, so it has to exercise the branch: one
    # published file with a hosted copy, one without. With only one shape, a template that
    # rendered the other wrong would build green.
    session = _front_matter(generated["collections"]["_lectures"]["session-01.md"])
    hosted = {link["name"]: link.get("view_url") for link in session["links"]}
    assert hosted["slides.html"].startswith("https://")
    assert hosted["slides.pdf"] is None


def test_the_hosted_copy_is_served_from_the_path_the_link_names(tmp_path):
    # The two halves of publishing, against each other: the mirror writes `files/<repo>/
    # <path>` and the link says `https://<site>/files/<repo>/<path>`. They are built by
    # different code and a mismatch is a 404 on a live site.
    build_fixture.build(tmp_path)
    index = yaml.safe_load(
        (tmp_path / "_data" / "materials.yml").read_text(encoding="utf-8")
    )
    served = [
        p.relative_to(tmp_path).as_posix()
        for p in (tmp_path / "files").rglob("*")
        if p.is_file()
    ]
    assert served
    for node in _tree_nodes(index["sections"]):
        if node.get("view_url"):
            assert node["view_url"].split(".github.io/")[1] in served


# ---------------------------------------------------------------------------
# Opening a file in the student's own copy (_includes/open_in.html)
# ---------------------------------------------------------------------------

# The pages that LIST released files, plus the two that name a student's own repo. The
# Updates box and the schedule table show file links too and are deliberately left out (a
# sentence and a timetable, not a list), and /profile/ takes the include for its own
# buttons rather than for the set-up line.
_OPEN_IN_LAYOUTS = frozenset(
    {
        "_layouts/lectures.html",
        "_layouts/labs.html",
        "_layouts/readings.html",
        "_layouts/materials.html",
        "_layouts/assignment.html",
        "_layouts/home.html",
        "_layouts/profile.html",
    }
)


def _open_in() -> str:
    return _templates()["_includes/open_in.html"]


def test_the_open_in_control_is_included_once_by_every_page_that_lists_files():
    # Once: the include carries the site's only script, which guards itself but would
    # otherwise be parsed twice. And by every one of them: a page that lists files without
    # it is a page where the profile a student filled in silently does nothing.
    included = {
        rel
        for rel, text in _liquid_templates().items()
        if "include open_in.html" in _strip_comments(text)
    }
    assert included == _OPEN_IN_LAYOUTS
    for rel in included:
        assert _strip_comments(_templates()[rel]).count("include open_in.html") == 1, (
            rel
        )


def test_the_control_takes_what_it_needs_from_data_attributes():
    # Never from the rendered prose: an assignment page's repo SHAPE is front matter, and
    # reading it out of the sentence that happens to print it would break the day that
    # sentence is reworded.
    body = _strip_comments(_open_in())
    assert 'data-org="{{ site.github_org }}"' in body
    assert 'data-repo-name="{{ page.repo_name | escape }}"' in body


def test_the_control_parses_the_url_shape_the_sync_writes():
    # THE contract between dsl_course/site.py and the script: `_gh_url` writes the link,
    # the script reads it back off the rendered page. A change to either alone turns every
    # control on every cohort site off, silently.
    literal = re.search(r"var SOURCE = /(.+?)/;", _open_in()).group(1)
    pattern = re.compile(literal.replace("\\/", "/"))
    url = site._gh_url(
        "cohort-f2026", "materials", "main", "blob", "labs/01/lab one.ipynb"
    )
    m = pattern.match(url)
    assert m, url
    assert m.groups() == (
        "cohort-f2026",
        "materials",
        "blob",
        "main",
        "labs/01/lab%20one.ipynb",
    )
    # A directory link is the same shape with `tree`, and is offered too - "local" opens
    # the folder.
    tree = site._gh_url("cohort-f2026", "materials", "main", "tree", "labs/01")
    assert pattern.match(tree).group(3) == "tree"
    # Another org's repo is left alone by the script; the shape still has to parse for the
    # comparison to happen at all.
    assert pattern.match("https://example.invalid/a/b/blob/main/c") is None


def test_only_one_file_knows_where_the_settings_are_stored():
    # Two files, one browser: /profile/ writes the settings and the control reads them
    # back, so a key spelt twice is a profile that fills in and quietly does nothing. The
    # page names FIELDS and goes through the include, which owns the keys.
    keys = re.search(r"var KEYS = \{(.+?)\};", _open_in(), re.DOTALL).group(1)
    for key in (
        "dsl.handle",
        "dsl.folder.materials",
        "dsl.folder.assignments",
        "dsl.editor",
        "dsl.forked",
    ):
        assert f'"{key}"' in keys, key
    assert '"dsl.' not in _templates()["_layouts/profile.html"]


# --- H
@pytest.mark.parametrize("cls", ["callout", "btn"])
def test_a_class_the_control_hooks_onto_is_one_a_shipped_layout_renders(cls):
    # `.callout a.btn` is how the script finds an assignment's repo button. The THEME
    # styles both, so no rule in _sass/_course.scss would notice the assignment layout
    # spelling them differently - the control would just never appear there again.
    assert ".callout a.btn" in _open_in()
    assert cls in _classes(_templates()["_layouts/assignment.html"]), cls


# ---------------------------------------------------------------------------
# Your Profile (_layouts/profile.html)
# ---------------------------------------------------------------------------


def _profile() -> str:
    return _templates()["_layouts/profile.html"]


def test_the_profile_page_is_four_numbered_steps_in_the_order_they_happen():
    # Details, fork, clone, open. The order is the point: each step's button needs what
    # the one before it produced, and a page that offered all four at once is how a
    # student came to clone a fork that did not exist yet.
    body = _strip_comments(_profile())
    numbers = re.findall(r'<span class="profile-step-num">(\d)</span>', body)
    assert numbers == ["1", "2", "3", "4"]
    titles = re.findall(r"</span>\s*([^<]+?)</h2>", body)
    assert titles == [
        "Your details",
        "Fork the materials repo",
        "Clone your fork",
        "Open it in your editor",
    ]


def test_the_folder_fields_default_to_one_folder_per_repo_under_this_cohort():
    # The path a student is most likely to want, spelt out rather than described: the
    # clone itself, not the folder it sits in. `localUrl` accepts either - a folder that
    # already ends in the repo name does not get it twice - so the placeholder can be the
    # more useful of the two. It sits under a folder named for the cohort org, so a
    # student on two courses is not steered into cloning two `materials` into one place.
    body = _strip_comments(_profile())
    placeholders = dict(
        re.findall(r'id="dsl-(materials|assignments)"[^>]*?placeholder="([^"]+)"', body)
    )
    assert placeholders["materials"].endswith(
        "/repositories/{{ site.github_org }}/materials"
    )
    assert placeholders["assignments"].endswith(
        "/repositories/{{ site.github_org }}/assignments"
    )


def test_the_folder_examples_are_respelt_for_the_readers_platform():
    # The placeholders ship as macOS spells a home folder; the script swaps that prefix for
    # the Windows or Linux one where the browser can name the platform, and turns the
    # separators round for Windows. A Windows student shown `/Users/j.doe/...` typed it in
    # verbatim and got a path no editor could open.
    body = _strip_comments(_profile())
    placeholders = re.findall(
        r'id="dsl-(?:materials|assignments)"[^>]*?placeholder="([^"]+)"', body
    )
    assert all(p.startswith("/Users/j.doe/Documents/") for p in placeholders)
    assert 'var MAC_HOME = "/Users/j.doe/Documents";' in body
    assert 'win: "C:\\\\Users\\\\j.doe\\\\Documents"' in body
    assert 'linux: "/home/j.doe"' in body
    ready = body.split("api.ready(function () {")[1]
    assert "examples.materials = localExample(examples.materials);" in ready
    assert "examples.assignments = localExample(examples.assignments);" in ready


def test_nothing_is_stored_until_save_is_pressed():
    # Typing is not saving: a half-typed path that enabled a button would open the wrong
    # folder and read as the page's fault. `refresh` runs on every keystroke and must
    # therefore write nothing - the only writers are the click handlers.
    body = _strip_comments(_profile())
    refresh = body.split("function refresh() {")[1].split("\n  }")[0]
    assert "api.save" not in refresh
    # And the readout under each field comes from storage, not from the boxes.
    assert "api.stored" in body


def test_every_field_carries_its_own_save_and_its_own_saved_line():
    # One Save per field, beside its own box, with what it stored directly underneath -
    # rather than one button at the end of the step for four unrelated settings.
    body = _strip_comments(_profile())
    saves = re.findall(r'data-save="(\w+)"', body)
    assert saves == ["handle", "materials", "assignments", "editor"]
    for name in saves:
        assert f'id="dsl-saved-{name}"' in body, name
    # Each Save is a button in the same red family as the step buttons, sized down.
    assert body.count('class="btn btn--field"') == len(saves)


def test_the_boxes_hold_an_editable_value_and_save_leaves_it_there():
    # The box is seeded with the saved value, or the example until there is one, so a
    # student edits `j.doe` into their own name instead of retyping a path over a grey
    # hint that goes at the first keystroke. Save keeps what it stored in the box; an
    # empty box or the untouched example saves nothing; `clear` is the way back to unset
    # and puts the example back.
    body = _strip_comments(_profile())
    ready = body.split("api.ready(function () {")[1]
    assert (
        "box(FIELDS[i]).value = api.stored(FIELDS[i]) || examples[FIELDS[i]]" in ready
    )
    save = body.split("function save(name) {")[1].split("\n  }")[0]
    assert (
        "if (!value || (value === examples[name] && !api.stored(name))) { return; }"
        in save
    )
    assert 'box(name).value = ""' not in save
    assert 'setAttribute("placeholder"' not in body
    assert '"clear"' in body
    assert "if (box(name)) { box(name).value = examples[name]; }" in body


def test_the_folder_boxes_run_the_width_of_the_page():
    # An absolute path with the cohort org in it does not fit a 26em box; only the handle
    # box, which holds a username, stays capped.
    scss = _templates()["_sass/_course.scss"]
    entry = scss.split(".profile-entry {")[1].split("}")[0]
    assert "max-width" not in entry
    text_input = scss.split('input[type="text"] {')[1].split("}")[0]
    assert "max-width" not in text_input
    assert "#dsl-handle," in scss


def test_the_clone_button_waits_for_the_fork():
    # Cloning a fork that does not exist is the one failure with no useful message at the
    # other end - the editor just reports a repository it cannot find. The flag is this
    # browser's own; the page cannot ask GitHub without sending the reader's handle
    # somewhere, so a student who forked elsewhere says so by hand.
    body = _strip_comments(_profile())
    clone = body.split('link(el("dsl-clone"),')[1].split(";")[0]
    assert "forked" in clone and "me.handle" in clone
    assert 'el("dsl-forked").addEventListener' in body


def test_step_three_names_the_folder_the_clone_dialog_should_be_pointed_at():
    # No clone URL can carry a destination - `vscode://vscode.git/clone` takes a url and a
    # ref, and the git extension hands the dialog no parent path - so the page names the
    # folder instead. Without it a student takes whatever folder the dialog opens at, and
    # step 4 and every `local` button then point at a clone that is not there.
    body = _strip_comments(_profile())
    assert 'id="dsl-clone-parent"' in body
    refresh = body.split("function refresh() {")[1].split("\n  }")[0]
    assert "api.cloneParent(me.materials, repo)" in refresh
    # Only once there is a folder to name and a clone about to be made.
    assert 'show("dsl-clone-where", !!parent && !!me.handle && !!forked);' in refresh
    # The rule lives beside the one it inverts: a saved folder that already names the repo
    # is where the clone GOES, so the dialog wants the folder above it - and a folder that
    # does not is the dialog's answer as it stands.
    helper = _strip_comments(_open_in()).split("function cloneParent(folder, repo) {")[
        1
    ]
    helper = helper.split("\n  }")[0]
    assert "trimmed.slice(cut + 1) !== repo" in helper
    assert "cloneParent: cloneParent," in _strip_comments(_open_in())


def test_step_three_also_prints_the_command_that_needs_no_dialog():
    # The page cannot RUN anything - no browser API executes a shell command - but the line
    # it prints clones into the exact folder, which no dialog and no URL can be told. It is
    # also the only clone a student whose editor is not VS Code is offered at all.
    body = _strip_comments(_profile())
    assert 'id="dsl-clone-cmd"' in body
    refresh = body.split("function refresh() {")[1].split("\n  }")[0]
    assert "api.cloneCommand(me.handle, repo, me.materials)" in refresh
    assert 'show("dsl-clone-cmd", !!parent && !!me.handle && !!forked);' in refresh
    # Built once, not per keystroke: `refresh` runs on every one, and a block rebuilt each
    # time would hang a fresh Copy listener on every one.
    assert 'var cmd = api.commandBlock().querySelector("code");' in body
    assert "api.commandBlock()" not in refresh


def test_the_clone_command_targets_the_repo_folder_itself():
    # One segment further down than the dialog hint: the dialog is given the folder the
    # clone goes INTO, `git clone` the folder it becomes. Both off the same saved path.
    body = _strip_comments(_open_in())
    path = body.split("function localPath(folder, repo) {")[1].split("\n  }")[0]
    assert "cloneParent(folder, repo)" in path
    cmd = body.split("function cloneCommand(owner, repo, folder) {")[1].split("\n  }")[
        0
    ]
    assert "localPath(folder, repo)" in cmd
    # Quoted, because nothing stops a space in a home folder - and double quotes are what
    # bash, zsh, PowerShell and cmd all honour.
    assert cmd.count("'\"'") == 1 and '.git "' in cmd


def test_copy_falls_back_to_a_selection_and_never_leaves_a_dead_button():
    # `navigator.clipboard` is the whole of it on an https Pages site; the selection trick
    # covers the rest. Either way the command stays on the page to select by hand, and the
    # button says which happened rather than silently doing nothing.
    body = _strip_comments(_open_in())
    copy = body.split("function copy(text, btn) {")[1].split("\n  }")[0]
    assert "navigator.clipboard.writeText" in copy
    assert "selectCopy(text)" in copy
    assert '"Copied"' in copy and '"Copy failed"' in copy
    # Copy reads the block's own <code>, so a refilled block cannot copy the old command.
    block = body.split("function commandBlock() {")[1].split("\n  }")[0]
    assert "copy(code.textContent, btn)" in block


def test_a_custom_scheme_link_never_opens_a_new_tab():
    # `target=_blank` on a `vscode://` link hands the URL to the handler and leaves an
    # empty tab behind for the reader to close. One helper decides, so every button that
    # can carry either kind of URL gets the same answer.
    body = _strip_comments(_templates()["_includes/open_in.html"])
    helper = body.split("function newTab(a, href) {")[1].split("\n  }")[0]
    assert "/^https?:/i" in helper
    assert 'a.setAttribute("target", "_blank")' in helper
    assert 'a.removeAttribute("target")' in helper


def test_the_profile_tab_is_lifted_out_of_the_nav_by_its_permalink():
    # The header is the theme's, so the tab bar is where the toolkit can reach the tab
    # from - by the page it points at, never by its place in the row, which the next tab
    # added would take.
    scss = _SCSS_COMMENT.sub("", _templates()["_sass/_course.scss"])
    rule = 'li:has(> a[href$="/profile/"])'
    assert rule in scss
    lifted = scss.split(rule)[1].split("\n}")[0]
    assert "position: absolute" in lifted
    # And put back in the flow where the nav collapses behind the burger.
    assert "position: static" in lifted


def test_a_students_own_repo_replaces_the_shape_wherever_a_page_prints_it():
    # A page is public and identical for everyone, so it prints `assignment-1-<your-handle>`
    # and points at the org's filtered repo list. Once the browser knows the handle both can
    # be exact - and both are MARKED in the layout, never matched on the sentence around
    # them, because those sentences get reworded.
    body = _strip_comments(_open_in())
    assert "[data-dsl-repo]" in body and "[data-dsl-repo-url]" in body
    page = _strip_comments(_templates()["_layouts/assignment.html"])
    # Every one of them, not a count that drifts: an unmarked span goes on saying
    # `<your-handle>` on a page where every other mention of the repo is the real name.
    assert page.count("{{ page.repo_name | escape }}</code>") == page.count(
        "<code data-dsl-repo>"
    )
    assert page.count('href="{{ page.repo_url }}"') == page.count("data-dsl-repo-url")
    # A GROUP assignment is named for the team, which no browser can know, so it is left
    # exactly as rendered.
    own = body.split("function ownRepo(shape, handle) {")[1].split("\n  }")[0]
    assert '"<your-handle>"' in own


def test_the_assignment_page_offers_two_edits_and_no_clone_button():
    # Edit online, edit locally - beside the solid button for the repo on GitHub that the
    # page already carried. No Clone: it worked for VS Code alone, could not be told where
    # to put the repo, and a student who accepted the folder its dialog opened at broke
    # `Edit locally` next to it. The clone is a command under the buttons instead.
    body = _strip_comments(_open_in())
    block = body.split("function decorateAssignment(root, me) {")[1].split("\n  }")[0]
    assert "vscode://vscode.git/clone" not in block
    offers = block.split("var offers = [")[1].split("];")[0]
    labels = re.findall(r'"(Edit online|Edit locally|Clone)"', offers)
    assert labels == ["Edit online", "Edit locally"]


def test_the_clone_command_is_tied_to_the_button_it_is_needed_for():
    # `Edit locally` opens a folder only a clone puts there, and the page cannot know
    # whether one was made - so it says so rather than leaving a student to find out by
    # pressing it. No local button, nothing for the clone to be a prerequisite of.
    body = _strip_comments(_open_in())
    block = body.split("function decorateAssignment(root, me) {")[1].split("\n  }")[0]
    assert "if (offers[1][0]) {" in block
    assert "cloneCommand(org, repo, me.assignments)" in block
    note = 'NB: "Edit locally" requires you to have run the following clone command:'
    assert "under(line('" + note + "'));" in block


def test_the_callout_keeps_the_brief_last():
    # Buttons, the note about them, then the callout's own sentence - which belongs to the
    # brief and is nobody's to move. Each block goes under the last rather than at the end
    # of the callout, which is how the command came to sit below the prose it explains.
    block = _strip_comments(_open_in()).split(
        "function decorateAssignment(root, me) {"
    )[1]
    block = block.split("\n  }")[0]
    assert "row.parentNode.insertBefore(node, row.nextSibling);" in block
    assert "row = node;" in block
    # Never onto the callout itself, which is what put the command below the prose.
    assert "parentNode.appendChild" not in block
    # Air under the command, so the brief's sentence does not read as part of it.
    scss = _SCSS_COMMENT.sub("", _templates()["_sass/_course.scss"])
    cmd = scss.split(".cmd {")[1].split("}")[0]
    assert "margin: 0.3em 0 1em 0;" in cmd
    # Quiet: the clone lines are small and grey, so the brief's own sentence stays the
    # black, full-size thing in the callout. Not `@extend %quiet-note` - that sizes the
    # block too, and the `code` inside sizes itself off it.
    assert "color: $grey-color-dark;" in cmd
    assert "@extend %quiet-note;" not in cmd
    where = scss.split(".callout .open-in-where {")[1].split("}")[0]
    assert "@extend %quiet-note;" in where
