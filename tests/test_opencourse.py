"""opencourse: the public website's `opencourse.yml`, and its withhold list."""

from __future__ import annotations

import pytest
import yaml

from dsl_course import opencourse, releaseignore, schemas
from dsl_course.faults import Unusable
from dsl_course.opencourse import OpenCourse
from dsl_course.schema_check import validate


def test_absent_or_empty_is_off():
    assert opencourse.parse(None) == OpenCourse()
    assert opencourse.parse({}) == OpenCourse(enabled=False)


def test_the_seeded_file_is_instructor_owned_off_and_parses():
    text = opencourse.seed_text()
    assert text.startswith("# INSTRUCTOR-OWNED")
    assert opencourse.parse(yaml.safe_load(text)) == OpenCourse()


def test_a_seed_round_trips_its_values():
    oc = OpenCourse(
        True, "course-materials-f2025", "none", False, ("labs/**", "*.key", "!x", "#y")
    )
    assert opencourse.parse(yaml.safe_load(opencourse.seed_text(oc))) == oc


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"enabled": "yes"}, "enabled: must be boolean"),
        ({"readings_mode": "all"}, "readings_mode: must be one of"),
        ({"withhold": "labs/"}, "withhold: must be array"),
        ({"licence": "cc-by"}, "licence: is not a known field"),
        ({"enabled": True}, "enabled needs a source_repo"),
    ],
)
def test_a_file_that_does_not_validate_is_refused_by_name(data, why):
    with pytest.raises(Unusable, match=why):
        opencourse.parse(data)


def test_a_file_that_is_not_yaml_is_unusable(monkeypatch):
    def broken(*a):
        raise yaml.YAMLError("bad")

    monkeypatch.setattr(opencourse, "load_yaml_config", broken)
    with pytest.raises(Unusable, match="not valid YAML"):
        opencourse.read("Org")


def test_read_is_none_when_the_course_has_no_file(monkeypatch):
    monkeypatch.setattr(opencourse, "load_yaml_config", lambda *a: None)
    assert opencourse.read("Org") is None


def test_the_exported_schema_takes_what_the_parser_takes():
    exported = schemas.opencourse_schema()
    good = yaml.safe_load(opencourse.seed_text(OpenCourse(True, "m")))
    assert validate(good, exported) == []
    assert validate({"enabled": "yes"}, exported)


def test_the_withhold_list_prunes_like_a_root_releaseignore(tmp_path):
    for rel in ("labs/01/lab.pdf", "labs/01/data.csv", "lectures/01/a.pdf"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    lines = ("labs/", "!labs/01/lab.pdf", "*.pdf")
    assert releaseignore.excludes(tmp_path, tmp_path / "labs/01/lab.pdf", lines)
    assert releaseignore.excludes(tmp_path, tmp_path / "lectures/01/a.pdf", lines)
    deny = releaseignore.deny_lines(tmp_path, lines)
    assert deny(str(tmp_path), ["labs", "lectures"]) == {"labs"}
    # No `.releaseignore` file is read: the list alone decides.
    assert not releaseignore.excludes(tmp_path, tmp_path / "lectures/01/a.pdf")
