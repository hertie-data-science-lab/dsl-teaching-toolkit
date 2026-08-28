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
from pathlib import Path

import pytest
import yaml

from dsl_course import site

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
# purpose. An HTML comment is the same, plus `_layouts/home.html`'s commented-out
# instructor block, which reads a shape nothing has written for years.
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

# Front matter the sync deliberately never writes: hand-authored fields from the upstream
# course-website-template that a template renders only behind an `{% if %}`. Listed rather
# than tolerated, so the check below stays an assertion about everything else.
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
    """`site.data` as Jekyll would see it: the generated `_data/*.yml` plus the ones a
    site brings with it (late_policy, previous_offering)."""
    data = {
        path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((FIXTURE_DIR / "base" / "_data").glob("*.yml"))
    }
    for rel, text in generated["files"].items():
        if rel.startswith("_data/"):
            data[Path(rel).stem] = yaml.safe_load(text)
    return data


@pytest.fixture(scope="module")
def written_fields(documents, generated) -> set[str]:
    """Every field a template may legitimately read off a document.

    The union of the collection entries' front matter and the All Materials tree's nodes,
    because `include.entry` means a collection entry in session_entry.html and a tree node
    in materials_entry.html - one namespace either way as far as an extracted key can
    tell."""
    fields = {p for doc in documents for p in _field_paths(doc)}
    index = yaml.safe_load(generated["files"]["_data/materials.yml"])
    return fields | _field_paths(index.get("sections"))


def _templates() -> dict[str, str]:
    return site._site_templates()


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


def test_a_schedule_row_template_exists_for_every_type_the_sync_emits(documents):
    # The schedule dispatches on `type`, so a row kind added to site.py without its
    # template renders as the neutral fallback row - silently, on the live schedule.
    emitted = {doc["type"] for doc in documents if doc.get("type")}
    emitted |= {doc["due_event"]["type"] for doc in documents if doc.get("due_event")}
    schedule_layout = _templates()["_layouts/schedule.html"]
    for kind in sorted(emitted):
        assert f"_includes/schedule_row_{kind}.html" in _templates(), kind
        assert f'{{% when "{kind}" %}}' in schedule_layout, kind


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
        "repo_url",
        "repo_name",
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
        "_sync_site_repo",
        lambda org, build: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site, "discover_cohort_repos", lambda orgs: [])
    monkeypatch.setattr(site, "discover_release_sources", lambda org, repos: [])
    monkeypatch.setattr(site, "discover_assignments", lambda org: [])
    monkeypatch.setattr(
        site, "discover_handed_out_assignments", lambda org: frozenset()
    )
    monkeypatch.setattr(site, "_yaml_file", lambda *a: {})
    monkeypatch.setattr(site.schedule, "load", lambda org: site.schedule.Schedule())
    monkeypatch.setattr(site, "_people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(site, "_repo_tree", lambda org, repo: ("main", ()))
    assert site.sync_site("Course-Org", ORG) == 0
    return captured["plan"]


@pytest.fixture
def public_plan(monkeypatch, tmp_path):
    """The `_SitePlan` a public course-site publish builds."""
    captured: dict = {}
    monkeypatch.setattr(
        site,
        "_sync_site_repo",
        lambda org, build, **kw: captured.update(plan=build(tmp_path)) or 0,
    )
    monkeypatch.setattr(site.seed, "discover_sessions", lambda org, repo: [])
    monkeypatch.setattr(site, "discover_sections", lambda src: [])
    monkeypatch.setattr(site, "_yaml_file", lambda *a: {})
    monkeypatch.setattr(site, "_people_yaml", lambda *a, **k: "people: []\n")
    monkeypatch.setattr(site, "gh", lambda *a, **k: (0, ""))
    assert site.sync_public_site("Course-Org", "course-materials-f2026") == 0
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
            ["git", "-C", str(wd), *site._GIT_ENV, "commit", "-q", "-m", "seed"],
            check=True,
        )
        return (0, "")

    return clone


def test_a_retired_path_leaves_the_site_repo_and_the_rest_stays(monkeypatch):
    # `files` cannot express a removal - the apply step is `git add -A` - so without
    # `retire` a template the toolkit stops shipping stays on every live site forever.
    tracked: dict = {}
    monkeypatch.setattr(site, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(
        site,
        "gh",
        _clone_with({"_layouts/class.html": "old\n", "_layouts/page.html": "keep\n"}),
    )
    real_git = site.git
    monkeypatch.setattr(
        site,
        "git",
        lambda *a: (0, "") if "push" in a else real_git(*a),
    )
    monkeypatch.setattr(
        site,
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

    def build(_wd: Path) -> site._SitePlan:
        return site._SitePlan(
            config={},
            collections={},
            files={"_layouts/new.html": "new\n"},
            retire=("_layouts/class.html", "_layouts/never-existed.html"),
            commit="site: sync",
        )

    assert site._sync_site_repo(ORG, build) == 0
    # Retired and gone; a retired path the site never had is not an error; everything
    # else the site holds is untouched.
    assert "_layouts/class.html" not in tracked["files"]
    assert "_layouts/page.html" in tracked["files"]
    assert "_layouts/new.html" in tracked["files"]
