"""The Jekyll templates in `templates/site/` against the front matter site.py writes.

The two halves of the semester site live in one repo now, and this is what holds them
together: every key a template READS must be a key the sync WRITES, and every flag the
sync writes must be read by something. Neither side fails loudly on its own - a template
reading `page.due` (which never existed) prints an empty string, and a flag nothing reads
is dead weight that looks live. Both went unnoticed for a term.

The generated side is the real fixture site (tests/fixtures/site/build_fixture.py), the
same one the `jekyll-contract` CI job builds with Jekyll - so the offline key check here
and the build there cannot disagree about what a semester site contains.
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
# the sync generated - a collection entry.
_DOC_KEY = re.compile(
    r"\b(?:page|entry|event)\.([A-Za-z0-9_.]+)|\binclude\.[A-Za-z0-9_]+\.([A-Za-z0-9_.]+)"
)

# Liquid's own accessors on any value, not fields of ours.
_LIQUID_ACCESSORS = ("size", "first", "last")

# Jekyll puts these on every document whatever its front matter says.
JEKYLL_DOC_FIELDS = frozenset({"url", "content", "collection"})

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
    """The whole generated semester site, once."""
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
    (previous_offering)."""
    data = {
        Path(rel).stem: yaml.safe_load(text)
        for rel, text in site_repo.seed_templates().items()
        if rel.startswith("_data/")
    }
    for rel, text in generated["files"].items():
        if rel.startswith("_data/"):
            data[Path(rel).stem] = yaml.safe_load(text)
    return data


@pytest.fixture(scope="module")
def written_fields(documents) -> set[str]:
    """Every field a template may legitimately read off a document: the collection
    entries' front matter."""
    return {p for doc in documents for p in _field_paths(doc)}


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


def test_the_sync_ships_every_template_to_both_kinds_of_site(
    semester_plan, public_plan
):
    # A template that reaches the semester sites but not the public course site is how the
    # open-courseware build breaks on a layout it never received.
    for plan in (semester_plan, public_plan):
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


# Everything else the toolkit renders and so has to style itself: an assignment page's
# kicker line, the Updates box's inline source link, the row of buttons beside every other
# file link, and the form that fills in the profile the last two of those buttons need.
_OWN_CLASSES = frozenset(
    {
        "post-kicker",
        "post-due",
        "shape-note",
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
    for rel in ("index.md", "schedule.md", "Gemfile", "_data/previous_offering.yml"):
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


def test_a_schedule_row_template_exists_for_every_type_the_sync_emits(generated):
    # The schedule dispatches on `kind`, so a row kind added to site.py without a branch
    # renders as the neutral fallback row - silently, on the live schedule. What each
    # branch includes is its own business, so this checks only that the branch exists and
    # that the template it names ships. A release row, of any kind, is the one generic row.
    documents = [
        _front_matter(text)
        for name, entries in generated["collections"].items()
        if name != "_lectures"
        for text in entries.values()
    ]
    emitted = {doc["kind"] for doc in documents if doc.get("kind")}
    emitted |= {doc["due_event"]["kind"] for doc in documents if doc.get("due_event")}
    schedule = _strip_comments(_templates()["_layouts/schedule.html"])
    assert 'event.collection == "lectures"' in schedule
    assert "include schedule_row_lecture.html event=event kind=kind.label" in schedule
    branches = {
        m["kind"]: m["include"]
        for m in _WHEN_INCLUDE.finditer(_templates()["_layouts/schedule.html"])
    }
    for kind in sorted(emitted):
        assert kind in branches, kind
        assert f"_includes/{branches[kind]}" in _templates(), kind


def test_no_generated_row_carries_the_retired_type_key(generated):
    # `kind` replaced `type`, which no template reads any more.
    for name, entries in generated["collections"].items():
        for file, text in entries.items():
            doc = _front_matter(text)
            assert "type" not in doc and "type" not in (doc.get("due_event") or {}), (
                f"{name}/{file}"
            )


def test_every_row_that_can_carry_a_provisional_date_marks_it():
    # `tbc:` is one key on every block, so every row it can reach has to be able to say
    # so. The lecture/lab row read it nowhere and rendered nothing, which is the one
    # failure a faculty member cannot see from the file they wrote: they marked a class
    # date provisional and the deployed schedule went on showing it as settled.
    for rel, text in _liquid_templates().items():
        if not rel.startswith("_includes/schedule_row_"):
            continue
        body = _strip_comments(text)
        # The lab row is the lecture row, included with its own word.
        if "schedule_row_lecture.html" in body:
            continue
        assert "include.event.tbc" in body, rel
        assert "(TBC)" in body, rel


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
        "off_schedule",
        "handout_pending",
        "tbc",
        "dateless",
        "hide_time",
        "announce",
        "repo_url",
        "repo_name",
        "submit_shape",
        "submit_url",
        "submit_host",
        "due_event",
        "team_join_url",
        "team_join_closes",
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


def test_no_site_carries_a_late_policy_of_its_own_any_more():
    # The seeded `_data/late_policy.yml` was INSTRUCTOR-OWNED prose ("8 free late days")
    # beside a rule the toolkit actually enforces from each assignment's own
    # `grading_config.yml`, and the two disagreed on every live site. The data file, its
    # include and the box that rendered them are gone; a live site keeps its orphan copy
    # and nothing reads it.
    assert not [rel for rel in _templates() if "late_policy" in rel]
    assert not [rel for rel in site_repo.seed_templates() if "late_policy" in rel]
    for rel, text in _templates().items():
        assert "late_policy" not in text, rel


# ---------------------------------------------------------------------------
# plan.retire
# ---------------------------------------------------------------------------

ORG = "Semester-f2026"


@pytest.fixture
def semester_plan(monkeypatch, tmp_path):
    """The `_SitePlan` a real semester sync builds, against a faked org."""
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

# The two places that show a file link - a session row and the Updates box. Both go
# through one include, which is what stops the same file
# opening rendered on one page and as source on another.
_LINK_TEMPLATES = (
    "_includes/session_entry.html",
    "_includes/lecture_links.html",
)

_VIEW_BRANCH = re.compile(
    r"{%-?\s*if\s+[\w.]*\bview_url\s*-?%}(?P<shown>.*?){%-?\s*endif\s*-?%}", re.DOTALL
)


@pytest.mark.parametrize("rel", _LINK_TEMPLATES)
def test_every_page_that_shows_a_file_link_goes_through_the_one_include(rel):
    assert "include file_link.html" in _strip_comments(_templates()[rel])


def test_render_is_offered_only_where_the_site_hosts_a_copy():
    # `render` opens the semester site's own copy, which exists only for a published file a
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
    assert "inline" not in _strip_comments(_templates()["_includes/session_entry.html"])


def test_the_shared_link_include_adds_no_whitespace_of_its_own():
    # It renders inside a sentence in the Updates box, where a stray newline is a visible
    # space in the middle of `[name]`.
    body = _templates()["_includes/file_link.html"]
    # No trailing newline of its own, and the comment above the markup trims the one after
    # it (`-%}`).
    assert body.endswith("{% endif %}")
    assert "-%}\n<a" in body


# ---------------------------------------------------------------------------
# A public calendar (decision 0011 rule 5)
# ---------------------------------------------------------------------------


def test_the_calendar_ships_no_page_of_the_retired_sections():
    # The assignment page (and its hashed-handle team recognition), Your Profile, the All
    # Materials tree and the open-in control are the student console's now.
    for rel in site_repo.RETIRED_TEMPLATES:
        assert rel not in _templates()
    for text in _liquid_templates().values():
        assert "/profile/" not in _strip_comments(text)
        assert "members_sha256" not in text and "team_salt" not in text


def test_the_banner_is_on_home_schedule_and_every_kind_tab(generated):
    for rel in ("_layouts/home.html", "_layouts/schedule.html", "_layouts/kind.html"):
        assert "{% include console_banner.html %}" in _templates()[rel]
    banner = _strip_comments(_templates()["_includes/console_banner.html"])
    assert "site.data.console.url" in banner
    assert (
        "?semester=hertie-dsl-fixture-f2026#week"
        in generated["files"]["_data/console.yml"]
    )


def test_an_assignment_entry_is_rows_and_no_page(generated):
    # Its collection is data only, so Jekyll writes no /assignments/<n>/ page, and nothing
    # the sync writes is left for a page to render.
    assert "assignments:\n    output: false" in site_repo._COLLECTIONS_BLOCK
    for text in generated["collections"]["_assignments"].values():
        fm = _front_matter(text)
        for key in (
            "late_rule",
            "cutoff_sentence",
            "shape_note",
            "max_points",
            "teams",
        ):
            assert key not in fm
    row = _strip_comments(_templates()["_includes/schedule_row_assignment.html"])
    assert "include.event.url" not in row


def test_the_schedule_row_names_the_day_teams_close(generated):
    # The Details cell's one call to action: form a team, by this day.
    row = _strip_comments(_liquid_templates()["_includes/schedule_row_assignment.html"])
    assert "include.event.team_join_url" in row
    # Off-site, so it leaves in a new tab like every other link on this site that does.
    assert (
        '<a target="_blank" rel="noopener" href="{{ include.event.team_join_url }}">'
        in row
    )
    assert "{{ include.event.team_join_closes }}" in row


# ---------------------------------------------------------------------------
# One word per column
# ---------------------------------------------------------------------------
# The schedule table has four columns and the generated front matter now has one key per
# column: `type` (Event), `date` (Date), `title` (Title), `details` (Details). `title` and
# `details` used to be the one key `description`, which meant the session blurb on a
# lecture row and the row's NAME on an exam, a special event, a term boundary and an
# assignment's due row - so a template had to know which kind of row it was on to know
# which of the two it was reading.

# The one include every Details cell opens with. The line that renders a declared
# `details:` lives in `_includes/schedule_details.html` and nowhere else: six copies of a
# cell is how one of them comes to render it differently.
DETAILS_INCLUDE = "{%- include schedule_details.html"

DETAILS_ROWS = (
    "_includes/schedule_row_lecture.html",
    "_includes/schedule_row_assignment.html",
    "_includes/schedule_row_due.html",
    "_includes/schedule_row_exam.html",
    "_includes/schedule_row_special_event.html",
)


def _details_cell(text: str) -> str:
    """The Details column's cell of one schedule-row template, comments stripped."""
    body = _strip_comments(text)
    _, marker, rest = body.partition('data-label="Details">')
    assert marker, "no Details cell"
    cell, _, _ = rest.partition("</div>")
    return cell


def test_the_details_include_renders_the_front_matter_it_is_handed():
    # The single copy. `markdownify` because front matter is raw markdown, unlike
    # `content`, which Jekyll has already rendered - without it a blurb's asterisks show.
    # Keyed on `include.details` rather than `include.event.details`, so the schedule
    # layout's fallback row, which has no `include.event`, can hand it a value too.
    body = _strip_comments(_templates()["_includes/schedule_details.html"])
    assert "{%- if include.details %}" in body
    assert "{{ include.details | markdownify }}" in body


@pytest.mark.parametrize("rel", DETAILS_ROWS)
def test_declared_details_render_above_what_the_cell_generates(rel):
    # ADDITIVE, and outside every branch. A lecture row's links and its "not released yet"
    # note are two arms of one `if`; details written inside either arm would disappear on
    # the other - so a session that declared a blurb would show it only until its slides
    # shipped.
    cell = _details_cell(_templates()[rel])
    above, marker, below = cell.partition(DETAILS_INCLUDE)
    assert marker, rel
    assert not above.strip(), rel
    assert below.strip(), rel


def test_the_schedule_layouts_fallback_row_shows_declared_details_too():
    # The `{% else %}` arm an unrecognised `type:` falls through to. It renders a plain
    # row rather than failing the build, and a plain row still has a Details column.
    cell = _details_cell(_templates()["_layouts/schedule.html"])
    assert DETAILS_INCLUDE in cell
    assert "details=event.details" in cell


def test_the_exam_row_still_renders_a_body_below_its_details():
    # The hardcoded "Details to be confirmed." body went; the line that RENDERS a body did
    # not. Deleting both would blank an exam that has something to say.
    cell = _details_cell(_templates()["_includes/schedule_row_exam.html"])
    assert "include.event.content" in cell
    assert cell.index(DETAILS_INCLUDE) < cell.index("include.event.content")


@pytest.mark.parametrize(
    "rel",
    [
        "_includes/schedule_row_lecture.html",
        "_includes/schedule_row_assignment.html",
        "_includes/schedule_row_due.html",
        "_includes/schedule_row_exam.html",
        "_includes/schedule_row_special_event.html",
        "_includes/schedule_row_term_date.html",
    ],
)
def test_every_schedule_row_takes_its_title_from_title(rel):
    body = _strip_comments(_templates()[rel])
    _, marker, rest = body.partition('data-label="Title">')
    assert marker, rel
    cell, _, _ = rest.partition("</div>")
    assert "include.event.title" in cell, rel


def test_the_updates_box_still_prints_a_rows_declared_sentence():
    # The archive row writes its sentence to `details:` like every other row now, instead
    # of into the page body. The box captured its bullet out of `content` alone, so the
    # move would have left the only warning a student gets before the freeze rendering as
    # an empty bullet - which the `strip` below the capture then drops, silently.
    body = _strip_comments(_liquid_templates()["_includes/announcements.html"])
    arm = body.partition("{%- else -%}")[2].partition("{%- endif -%}")[0]
    assert "n.details | markdownify" in arm
    assert "n.content" in arm
    assert arm.index("n.details") < arm.index("n.content")


def test_no_template_reads_description_off_a_generated_document():
    # The key is gone from every generated page. A template still reading it prints an
    # empty string - silently, and on the one column a reader looks at first.
    for rel, text in _liquid_templates().items():
        assert "description" not in _reads(text)[1], rel


def test_the_term_date_row_names_its_kind_and_not_its_entry():
    # `{{ include.event.name | default: "Term" }}` outlived the `name:` site.py wrote, and
    # a default is exactly the failure that looks fine: the column would have gone on
    # reading "Term" for every row while the name sat unread.
    body = _strip_comments(_templates()["_includes/schedule_row_term_date.html"])
    _, _, rest = body.partition('data-label="Event">')
    cell, _, _ = rest.partition("</div>")
    assert cell.strip() == "Semester"
