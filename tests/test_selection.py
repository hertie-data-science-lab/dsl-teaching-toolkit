"""selection: the one Selection shape (decision 0040) - parse, the layers, the copytree
adapter, the floor, and the golden vectors the console's mirror is held to."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dsl_course import policy, schemas, selection, site
from dsl_course.faults import Unusable
from dsl_course.selection import Floor, Selection
from tests.fixtures.site import build_fixture

ROOT = Path(__file__).resolve().parents[1]
DEMO_HOSTING = ROOT / "console" / "test" / "fixtures" / "demo-hosting.json"


def test_every_key_is_optional_and_absent_is_no_filter():
    assert selection.parse(None, "x") == Selection()
    assert Selection().keeps("anything/at/all.bin", None)
    sel = selection.parse({"kinds": ["labs"], "types": [".PDF", "htm"]}, "x")
    assert sel == Selection(kinds=("labs",), types=("pdf", "html"))


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ([], "must be a mapping"),
        ({"kinds": "labs"}, "`kinds` must be a list"),
        ({"kinds": ["lab"]}, "not a kind of the institution"),
        ({"hosted": ["x"]}, "not a selection key"),
        ({"include": [1]}, "`include` must be a list"),
    ],
)
def test_a_selection_that_does_not_validate_is_refused_by_name(data, why):
    with pytest.raises(Unusable, match=why):
        selection.parse(data, "x")


def test_the_releaseignore_use_takes_exclude_only():
    with pytest.raises(Unusable, match="not a selection key"):
        selection.parse({"kinds": ["labs"]}, "x", exclude_only=True)


def test_exclude_beats_include_beats_kinds_and_types_hold_for_both():
    sel = Selection(
        kinds=("lectures",),
        types=("pdf",),
        include=("readings/**/key.pdf",),
        exclude=("**/drafts/",),
    )
    assert sel.keeps("lectures/01/a.pdf", "lectures")
    assert not sel.keeps("lectures/01/a.html", "lectures")  # type
    assert not sel.keeps("lectures/drafts/a.pdf", "lectures")  # exclude
    assert sel.keeps("readings/01/key.pdf", "readings")  # include
    assert not sel.keeps("readings/01/other.pdf", "readings")
    # A path no entry landed (no kind) is kept only by an include.
    assert not sel.keeps("extra/a.pdf", None)


def test_a_key_left_out_takes_the_uses_default_and_a_written_empty_one_does_not():
    default = selection.publish_default()
    assert selection.with_defaults(Selection(), default) == default
    written = selection.with_defaults(Selection(exclude=()), default)
    assert written.exclude == () and written.kinds == default.kinds


def test_the_host_default_is_every_shown_kind_but_readings():
    host = selection.host_default()
    assert "readings" not in host.kinds and "assets" not in host.kinds
    assert set(host.kinds) <= set(selection.kind_names())
    assert host.types == ("html", "pdf")


def test_the_denylist_is_the_first_layer_whatever_a_use_says():
    everything = Selection(include=("**",))
    for path in (
        "labs/01/Solution/a.ipynb",
        "tests/t.py",
        "a/.env.local",
        ".system/MAINTAINING.md",
        "x/.DS_Store",
        "x/__pycache__/m.pyc",
    ):
        assert not selection.kept(path, use=everything), path
    # A word inside a name is not a denied name.
    assert selection.kept("labs/solutions-discussion.md", use=everything)


def test_the_floor_is_the_last_layer():
    limit = Floor(never=("readings",), exclude=("*.key",))
    assert not selection.kept("r/p.pdf", lambda _p: "readings", limit=limit)
    assert not selection.kept("l/a.key", lambda _p: "lectures", limit=limit)
    assert selection.kept("l/a.pdf", lambda _p: "lectures", limit=limit)


def test_the_floor_reads_the_institution(monkeypatch):
    assert selection.floor(selection.HOST) == Floor()
    pol = {
        **policy.load(),
        "selection_floor": {"student_site_host": {"never": ["readings"]}},
    }
    monkeypatch.setattr(policy, "load", lambda: pol)
    assert selection.floor(selection.HOST) == Floor(never=("readings",))
    bad = {**pol, "selection_floor": {"student_site_host": {"kinds": ["x"]}}}
    monkeypatch.setattr(policy, "load", lambda: bad)
    with pytest.raises(Unusable, match="selection_floor.student_site_host"):
        selection.floor(selection.HOST)


def test_the_copytree_adapter_prunes_and_keeps_the_releaseignore_rule(tmp_path):
    for rel in (
        "labs/01/lab.pdf",
        "labs/01/solutions/a.pdf",
        "labs/drafts/x.pdf",
        "readings/01/p.pdf",
        "readings/01/key.pdf",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    (tmp_path / ".releaseignore").write_text("drafts/\n!drafts/x.pdf\n")
    use = Selection(kinds=("labs",), include=("readings/**/key.pdf",))
    kind_of = lambda rel: rel.split("/")[0]
    deny = selection.deny(tmp_path, use=use, kind_of=kind_of)
    assert deny(str(tmp_path / "labs"), ["01", "drafts"]) == {"drafts"}
    assert deny(str(tmp_path / "labs/01"), ["lab.pdf", "solutions"]) == {"solutions"}
    # A folder is never pruned for its kind: an include may name a file inside it.
    assert deny(str(tmp_path), ["labs", "readings", ".releaseignore"]) == {
        ".releaseignore"
    }
    assert deny(str(tmp_path / "readings/01"), ["p.pdf", "key.pdf"]) == {"p.pdf"}


def test_a_path_named_outright_says_why_it_cannot_go(tmp_path):
    (tmp_path / "labs/solutions").mkdir(parents=True)
    (tmp_path / "drafts").mkdir()
    (tmp_path / ".releaseignore").write_text("drafts/\n")
    assert selection.at_source(tmp_path, tmp_path / "labs/solutions") == "denylist"
    assert selection.at_source(tmp_path, tmp_path / "drafts") == "releaseignore"
    assert selection.at_source(tmp_path, tmp_path / "labs") is None


def test_an_exact_pattern_names_one_path_and_everything_under_it():
    pattern = selection.exact_pattern("a[b]/x*.pdf")
    sel = Selection(kinds=(), include=(pattern,))
    assert sel.keeps("a[b]/x*.pdf", None)
    assert not sel.keeps("ab/xx.pdf", None)
    folder = Selection(kinds=(), include=(selection.exact_pattern("readings/03"),))
    assert folder.keeps("readings/03/p.pdf", None)


def test_the_golden_vectors_are_the_engines_own_answers():
    # The console's mirror (`selection.ts`) is tested against these; they are computed
    # here, never typed, so the two cannot disagree unnoticed.
    exported = json.loads((ROOT / "console/schemas/selection.json").read_text())
    assert exported == json.loads(json.dumps(schemas.selection_json()))
    names = {c["name"] for c in exported["cases"]}
    assert len(names) == len(exported["cases"])


def demo_hosting() -> dict:
    """The demo fixture's hosting under its own `host:` and under the default, as the
    console preview is given it and as the real `site._hostable` answers it."""
    tree = build_fixture.TREE
    landings = site._landings(build_fixture.SCHEDULE)[build_fixture.MATERIALS]
    kind_of = site.kind_of_landed(landings)
    hosts = {"fixture": build_fixture.HOST, "default": selection.host_default()}
    return {
        "paths": list(tree),
        "kinds": {p: kind_of(p) for p in tree},
        "cases": [
            {
                "name": name,
                "host": site._with_public(host, landings).as_data(),
                "hosted": sorted(site._hostable(tree, host, landings)),
            }
            for name, host in hosts.items()
        ],
    }


def test_the_demo_fixtures_hosting_is_what_the_console_previews():
    # Brief item 4: the site's hosting set for the demo fixture equals the console
    # preview's (console/test/selection.test.ts reads the same file).
    want = demo_hosting()
    assert all(c["hosted"] for c in want["cases"]), "a case hosts nothing"
    have = json.loads(DEMO_HOSTING.read_text()) if DEMO_HOSTING.exists() else None
    assert have == want, (
        f"{DEMO_HOSTING.name} is stale: regenerate it with "
        "`python -c 'import json, tests.test_selection as t; "
        f"print(json.dumps(t.demo_hosting(), indent=2, sort_keys=True))' > {DEMO_HOSTING}`"
    )
