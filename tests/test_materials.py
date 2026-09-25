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
