"""opencourse: the public website's `open_course_site:` block of `course.yml`, and its
`publish:` Selection."""

from __future__ import annotations

import pytest
import yaml

from dsl_course import config, opencourse, schemas, selection
from dsl_course.faults import Unusable
from dsl_course.opencourse import OpenCourse
from dsl_course.schema_check import validate
from dsl_course.selection import Selection


def _seeded(oc: OpenCourse | None = None) -> dict | None:
    """The seeded block, read back the way the engine reads `course.yml`."""
    return opencourse.block(yaml.safe_load(opencourse.seed_text(oc)))


def test_absent_or_empty_is_off():
    assert opencourse.parse(None) == OpenCourse()
    assert opencourse.parse({}) == OpenCourse(enabled=False)


def test_the_seeded_block_is_off_and_parses():
    assert opencourse.seed_text().startswith("open_course_site:")
    oc = opencourse.parse(_seeded())
    assert oc == OpenCourse(publish=Selection(kinds=("lectures", "labs")))
    # The keys the seed leaves out take the open site's defaults (decision 0040).
    assert oc.selection.exclude == selection.PUBLISH_EXCLUDE
    assert oc.reading_lists is True


def test_the_default_exclude_keeps_answers_off_at_any_depth():
    kept = (
        "labs/01/lab1_solution.ipynb",
        "solutions/a.py",
        "exams/2025/paper.pdf",
        "lectures/03/midterm_exam.pdf",
        "grades/marks.csv",
        "notes/private/todo.md",
        "Exams/paper.pdf",
        "labs/lab1_Solution.ipynb",
    )
    public = (
        "lectures/01/slides.pdf",
        "labs/01/lab1.ipynb",
        "examples/a.py",
        "benchmarks/run.py",
        "notes/remarks.md",
        "lectures/04/image-resolution.ipynb",
        "conflict-resolution/notes.md",
    )
    sel = OpenCourse().selection
    for rel in kept:
        assert not sel.keeps(rel, "lectures"), rel
    for rel in public:
        assert sel.keeps(rel, "lectures"), rel


def test_a_seed_round_trips_its_values():
    oc = OpenCourse(
        True,
        "course-materials-f2025",
        Selection(kinds=("readings",), exclude=("labs/**", "*.key", "!x", "#y")),
        False,
    )
    assert opencourse.parse(_seeded(oc)) == oc
    # An empty `exclude:` is written, so a migrated live site keeps no default list.
    bare = OpenCourse(True, "m", Selection(kinds=("lectures",), exclude=()))
    assert opencourse.parse(_seeded(bare)).selection.exclude == ()
    # The site's contact rides in the block (decision 0019).
    block = opencourse.block(yaml.safe_load(opencourse.seed_text(oc, "c@x.edu")))
    assert block["contact"] == "c@x.edu" and opencourse.parse(block) == oc


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"enabled": "yes"}, "enabled: must be boolean"),
        ({"readings_mode": "all"}, "`readings_mode` is retired"),
        ({"withhold": ["labs/"]}, "`withhold` is retired"),
        ({"publish": {"kinds": ["lecture"]}}, "not a kind of the institution"),
        ({"publish": {"withhold": []}}, "withhold: is not a known field"),
        ({"publish": {"reading_lists": "yes"}}, "reading_lists: must be boolean"),
        ({"licence": "cc-by"}, "licence: is not a known field"),
        ({"enabled": True}, "enabled needs a source_repo"),
    ],
)
def test_a_file_that_does_not_validate_is_refused_by_name(data, why):
    with pytest.raises(Unusable, match=why):
        opencourse.parse(data)


def _course_yml(monkeypatch, text: str | None) -> None:
    monkeypatch.setattr(
        opencourse,
        "read_course",
        lambda org: config.ConfigFile(org, ".github", "course.yml", text),
    )


def test_a_file_that_is_not_yaml_is_unusable(monkeypatch):
    _course_yml(monkeypatch, "open_course_site: [\n")
    with pytest.raises(Unusable, match="not valid YAML"):
        opencourse.read("Org")


def test_read_is_none_when_the_course_has_no_file_or_no_block(monkeypatch):
    _course_yml(monkeypatch, None)
    assert opencourse.read("Org") is None
    _course_yml(monkeypatch, "course:\n  name: C\n")
    assert opencourse.read("Org") is None
    _course_yml(monkeypatch, "open_course_site: off\n")
    with pytest.raises(Unusable, match="not a mapping"):
        opencourse.read("Org")


def test_the_exported_schema_takes_what_the_parser_takes():
    exported = schemas.course_schema()
    good = yaml.safe_load(opencourse.seed_text(OpenCourse(True, "m")))
    assert validate(good, exported) == []
    assert validate({"open_course_site": {"enabled": "yes"}}, exported)
