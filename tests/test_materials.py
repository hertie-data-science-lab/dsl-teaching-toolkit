"""materials: a materials repo's optional `materials.yml`, and the topic that marks it."""

from __future__ import annotations

import pytest
import yaml

from dsl_course import materials, schemas
from dsl_course.faults import Unusable
from dsl_course.schema_check import validate


def test_absent_is_the_defaults():
    assert materials.parse(None) == materials.Declared("SYLLABUS.md", {})
    assert materials.parse({}) == materials.Declared()


def test_a_declared_syllabus_and_aliases():
    declared = materials.parse(
        {
            "syllabus": "/E1282_syllabus.pdf",
            "kinds": {"tutorials/": "Lab", "quiz": "other"},
        }
    )
    assert declared.syllabus == "E1282_syllabus.pdf"
    assert declared.kinds == {"tutorials": "lab", "quiz": "other"}


def test_an_alias_to_a_kind_the_policy_does_not_know_lands_on_other():
    assert materials.parse({"kinds": {"quiz": "quizzes"}}).kinds == {"quiz": "other"}


def test_a_file_that_does_not_validate_is_refused_by_name():
    with pytest.raises(Unusable, match=r"materials.yml.syllabs: is not a known field"):
        materials.parse({"syllabs": "x.pdf"})
    with pytest.raises(Unusable, match="kinds: must be object"):
        materials.parse({"kinds": ["lab"]})


def test_the_exported_schema_takes_what_the_parser_takes():
    schema = schemas.materials_schema()
    assert validate({"syllabus": "s.pdf", "kinds": {"quiz": "drop-in"}}, schema) == []
    assert validate({"kinds": {"quiz": "quizzes"}}, schema) != []


def test_the_topic_makes_a_materials_repo_not_the_name():
    assert materials.is_materials_repo(
        {"name": "nlp-labs", "topics": ["dsl-materials"]}
    )
    assert not materials.is_materials_repo(
        {"name": "course-materials-f2026", "topics": []}
    )


def test_a_file_that_is_not_yaml_is_unusable_not_a_traceback(monkeypatch):
    def broken(org, repo, path):
        raise yaml.YAMLError("bad indent")

    monkeypatch.setattr(materials, "load_yaml_config", broken)
    materials.read.cache_clear()
    with pytest.raises(Unusable, match="cm/materials.yml is not valid YAML"):
        materials.read("C", "cm")
    materials.read.cache_clear()


def test_folder_keys_and_aliases_match_whatever_the_case():
    assert materials.parse({"kinds": {"Quiz": "exam"}}).kinds == {"quiz": "exam"}
    assert materials.infer_kind("Quiz", {"quiz": "exam"}) == "exam"
    assert materials.alias_kind("LABS") == "lab"
    assert materials.alias_kind("code") is None


# ---------------------------------------------------------------- publish.yml


def test_a_deck_carries_its_asset_folders_with_it():
    assert materials.bundle_prefixes("lectures/01_lecture/deck.html") == (
        "lectures/01_lecture/deck_files/",
        "lectures/01_lecture/media/",
        "lectures/01_lecture/libs/",
        "lectures/01_lecture/images/",
    )


def test_a_hosted_deck_takes_a_media_folder_but_not_a_neighbours():
    # Maths keeps its deck assets in `media/` beside the deck.
    paths = (
        "lectures/01_lecture/deck.html",
        "lectures/01_lecture/media/fig.png",
        "lectures/01_lecture/notes.pdf",
        "lectures/02_lecture/media/other.png",
    )
    assert materials.hosted_paths(paths, ["**/*.html"]) == {
        "lectures/01_lecture/deck.html",
        "lectures/01_lecture/media/fig.png",
    }


def test_a_denylisted_bundle_file_stays_home():
    paths = ("l/deck.html", "l/deck_files/solutions/a.js", "l/deck_files/b.js")
    assert materials.hosted_paths(paths, ["l/*.html"]) == {
        "l/deck.html",
        "l/deck_files/b.js",
    }


@pytest.mark.parametrize(
    ("path", "pairs", "source"),
    [
        ("week-1/a.html", [("lectures/01", "week-1")], "lectures/01/a.html"),
        ("week-1", [("lectures/01/a.html", "week-1")], "lectures/01/a.html"),
        # A whole-repo copy, spelt any of the ways faculty write it.
        ("lectures/01/a.html", [(".", "/")], "lectures/01/a.html"),
        ("01/a.html", [("lectures", "")], "lectures/01/a.html"),
        # The longest semester prefix wins; a sibling that only shares letters does not.
        ("x/y/a.pdf", [("src", "x"), ("deep", "x/y")], "deep/a.pdf"),
        ("week-10/a.pdf", [("lectures/01", "week-1")], None),
    ],
)
def test_a_semester_path_goes_back_to_its_source(path, pairs, source):
    assert materials.source_path(path, pairs) == source


def test_each_copy_is_judged_by_the_repo_it_came_from():
    feeds = (
        materials.Feed(("lectures/**/*.html",), (("lectures/01", "s1/lecture"),)),
        materials.Feed(("src/**",), (("src", "s1/code"),)),
    )
    paths = ("s1/lecture/a.html", "s1/lecture/b.pdf", "s1/code/x.py", "extra/c.html")
    # `extra/` came by hand: judged as named, by every feed's patterns.
    assert materials.hosted_copy(paths, feeds) == {"s1/lecture/a.html", "s1/code/x.py"}
    feeds += (materials.Feed(("extra/**",), (("other", "elsewhere"),)),)
    assert "extra/c.html" in materials.hosted_copy(paths, feeds)


def test_a_copy_into_another_copys_folder_owns_it():
    # A copies `lectures` whole with `lectures/**` public; B, which hosts nothing, copies a
    # code repo into `lectures/05/code`. B's files are B's: not hosted.
    feeds = (
        materials.Feed(("lectures/**",), (("lectures", "lectures"),)),
        materials.Feed((), (("dldemo", "lectures/05/code"),)),
    )
    paths = ("lectures/05/slides.pdf", "lectures/05/code/serving.py")
    assert materials.hosted_copy(paths, feeds) == {"lectures/05/slides.pdf"}


def test_a_negated_folder_excludes_its_subtree_whatever_matched_before():
    paths = ("labs/a.pdf", "labs/sub/lab.pdf", "x/data/a.csv", "data/b.csv")
    assert materials.hosted_paths(paths, ["labs/**", "!labs/sub/"]) == {"labs/a.pdf"}
    assert materials.hosted_paths(paths, ["**", "!data/"]) == {
        "labs/a.pdf",
        "labs/sub/lab.pdf",
    }
