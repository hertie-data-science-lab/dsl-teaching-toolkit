"""The one reader per config file: absent, empty and malformed mean the same thing to
every caller, and a read that failed is never either."""

from __future__ import annotations

import pytest
import yaml

from dsl_course import config, gh_contents
from dsl_course.faults import Unusable


def _files(monkeypatch, files: dict[tuple[str, str, str], str]) -> list:
    reads = []

    def get(org, repo, path, ref="", **_kw):
        reads.append((org, repo, path))
        return files.get((org, repo, path))

    monkeypatch.setattr(gh_contents, "get_file_content", get)
    return reads


def test_an_absent_course_file_declares_nothing(monkeypatch):
    _files(monkeypatch, {})
    course = config.read_course("C")
    assert (course.state, course.absent, course.problem) == (config.ABSENT, True, None)
    assert course.mapping() is None
    assert course.meta() == {}


def test_an_empty_file_is_present_and_empty(monkeypatch):
    _files(monkeypatch, {("C", ".github", "course.yml"): "# nothing yet\n"})
    course = config.read_course("C")
    assert course.state == config.PARSED
    assert course.mapping() == {}


def test_a_course_file_parses_with_its_lines_on_request(monkeypatch):
    text = "course:\n  name: Nets\n  toolkit_version_ref: main\n"
    _files(monkeypatch, {("C", ".github", "course.yml"): text})
    course = config.read_course("C")
    assert course.mapping() == {
        "course": {"name": "Nets", "toolkit_version_ref": "main"}
    }
    stamped = course.mapping(lines=True)
    assert gh_contents.take_lines(stamped["course"])["toolkit_version_ref"] == 3


def test_each_caller_gets_its_own_copy(monkeypatch):
    _files(monkeypatch, {("C", ".github", "course.yml"): "course:\n  name: Nets\n"})
    course = config.read_course("C")
    course.mapping()["course"]["name"] = "changed"
    assert course.meta() == {"course": {"name": "Nets"}}


@pytest.mark.parametrize(
    ("text", "error"),
    [("course: [not closed", yaml.YAMLError), ("- a\n- list\n", Unusable)],
)
def test_a_file_that_is_not_a_mapping_is_a_fault_logged_once_asked(
    monkeypatch, capsys, text, error
):
    _files(monkeypatch, {("C", ".github", "course.yml"): text})
    course = config.read_course("C")
    assert course.state == config.FAULTS
    assert isinstance(course.problem, error)
    with pytest.raises(error):
        course.data()
    assert capsys.readouterr().err == ""
    with pytest.raises(error):
        course.mapping()
    assert "C/.github/course.yml" in capsys.readouterr().err


def test_a_read_that_failed_raises_rather_than_reading_as_absent(monkeypatch):
    def get(*a, **k):
        raise RuntimeError("could not read C/.github/course.yml: HTTP 403")

    monkeypatch.setattr(gh_contents, "get_file_content", get)
    with pytest.raises(RuntimeError):
        config.read_course("C")


def test_the_semester_files_are_read_from_its_config_repo_when_asked(monkeypatch):
    reads = _files(
        monkeypatch,
        {
            ("S", "semester-config", ".system/course.yml"): "course: C\n",
            ("S", "semester-config", "semester.yml"): "people: []\n",
        },
    )
    semester = config.read_semester("S")
    assert reads == []
    assert semester.pointer.meta() == {"course": "C"}
    assert semester.semester.meta() == {"people": []}
    assert reads == [
        ("S", "semester-config", ".system/course.yml"),
        ("S", "semester-config", "semester.yml"),
    ]


def test_a_file_in_a_clone_reads_the_same_way(tmp_path):
    assert config.read_local(tmp_path, "materials.yml").absent
    (tmp_path / "materials.yml").write_text("syllabus: S.pdf\n", encoding="utf-8")
    assert config.read_local(tmp_path, "materials.yml").meta() == {"syllabus": "S.pdf"}
