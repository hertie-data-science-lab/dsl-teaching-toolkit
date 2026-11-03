"""One reader per config file.

One hand-edited file per level (decision 0039): `read_course(org)` is the course org's
`.github/course.yml`; `read_semester(org)` the semester's files in `semester-config` (its
course pointer `.system/course.yml` and `semester.yml`); the institution's is
`policy.load()`. Every module that reads one of them comes through here, so absent, empty
and malformed mean the same thing everywhere:

- absent (a 404): `text` is None, `mapping()` is None.
- present: `mapping()` is the parsed mapping (`{}` for an empty file), its keys' lines
  stamped on request (`lines=True`, see `gh_contents.LineLoader`).
- present but unusable (not YAML, or not a mapping): `mapping()` logs it, naming the
  file, and raises it.
- a read that FAILED (a rate limit, a lost scope) raises out of `read*` itself: "we could
  not look" is never "the file is broken" nor "the file is absent".

The text is read through `gh_contents`' per-run read memo, so a file is fetched once per
run however many modules ask, and a write this run makes to it is seen by the next read.
A model is a snapshot of the text: each caller parses its own copy and cannot mutate
another's.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import gh_contents, records
from .course import CONFIG_REPO, COURSE_CONFIG, SEMESTER_FILE


@dataclass(frozen=True)
class ConfigFile:
    """One hand-edited YAML file as this run read it. See the module docstring."""

    org: str
    repo: str
    path: str
    text: str | None

    @property
    def where(self) -> str:
        return f"{self.org}/{self.repo}/{self.path}"

    @property
    def absent(self) -> bool:
        return self.text is None

    def mapping(self, lines: bool = False) -> dict | None:
        """None when absent, `{}` when empty, else the mapping; a file that does not
        parse to a mapping is logged and raised (`gh_contents.load_yaml_config`)."""
        return gh_contents.parse_yaml_config(self.text, self.where, lines)

    def meta(self) -> dict:
        """`mapping`, with absent read as declaring nothing (`{}`)."""
        return self.mapping() or {}


def read(org: str, repo: str, path: str) -> ConfigFile:
    """`org/repo/path` as a `ConfigFile`. A read that failed other than with a 404 raises
    (`gh_contents.get_file_content`). A 404 is confirmed before it is absent: every file
    read here is one a set-up org has, and its absence is a fault mailed to people, a
    course with no admins, or a semester with no settings."""
    text = gh_contents.get_file_content(org, repo, path, confirm_absent=True)
    return ConfigFile(org, repo, path, text)


def read_course(org: str) -> ConfigFile:
    """The course org's one hand-edited file, `.github/course.yml`."""
    return read(org, ".github", COURSE_CONFIG)


@dataclass(frozen=True)
class SemesterConfig:
    """A semester org's hand-edited config, each file read when it is first asked for."""

    org: str

    def file(self, path: str) -> ConfigFile:
        return read(self.org, CONFIG_REPO, path)

    @property
    def pointer(self) -> ConfigFile:
        """The semester's pointer to its course org (`.system/course.yml`)."""
        return self.file(records.path("pointer"))

    @property
    def semester(self) -> ConfigFile:
        """`semester.yml`: `people:`, `defaults:`, `student_site:`."""
        return self.file(SEMESTER_FILE)


def read_semester(org: str) -> SemesterConfig:
    """The semester org's `semester-config` files (see `SemesterConfig`)."""
    return SemesterConfig(org)
