"""The GitHub call budget of a status tick (decision 0034).

Many courses run on one token, so a tick's reads must stay a constant per course and per
semester: never one more per assignment, release or past semester than the engine already
pays, beyond one status.json read per registered semester on the course tick. Every `gh`
invocation goes through `ghcli._run_gh`; here an in-memory GitHub answers it and counts.
The read-once memos are on, as a CLI run turns them on, and conftest's stubs of the
cascade's own reads are undone so the count is a real run's. The numbers before decision
0034 (origin/main 0d001d02, the same fixture) are 43 for the semester and 18 for the
course.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime

import pytest

from dsl_course import discovery, gh_contents, ghcli, materials, settings, status_json

# The real reads, captured before conftest's autouse stubs replace them.
_REAL = {
    (settings, "org_meta"): discovery.org_meta,
    (settings, "course_org_for_semester"): discovery.course_org_for_semester,
    (settings, "_assignments_text"): settings._assignments_text,
    (discovery, "repo_is_archived"): discovery.repo_is_archived,
    (discovery, "repo_missing"): discovery.repo_missing,
}
NOW = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)


NOT_FOUND = (1, "", "gh: Not Found (HTTP 404)")


def _sha(text: str) -> str:
    data = text.encode()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class FakeGitHub:
    """Repos, files per branch and team members; every call is recorded."""

    def __init__(self):
        self.repos: dict[str, dict] = {}  # "org/repo" -> row
        self.files: dict[tuple[str, str], dict[str, str]] = {}  # (org/repo, branch)
        self.teams: dict[str, list[str]] = {}
        self.calls: list[tuple[str, ...]] = []
        self.unhandled: list[tuple[str, ...]] = []

    def repo(self, org, name, files=None, branches=None, **row):
        self.repos[f"{org}/{name}"] = {
            "name": name,
            "description": "",
            "visibility": "private",
            "url": f"https://github.com/{org}/{name}",
            "isTemplate": False,
            "archived": False,
            "pushed_at": "2026-09-01T00:00:00Z",
            "default_branch": "main",
            "topics": [],
            **row,
        }
        self.files[(f"{org}/{name}", "main")] = dict(files or {})
        for b, f in (branches or {}).items():
            self.files[(f"{org}/{name}", b)] = dict(f)

    # ---------------------------------------------------------------- the transport
    def __call__(self, args, stdin=None, retries=3):
        args = tuple(args)
        self.calls.append(args)
        try:
            out = self._answer(args)
        except KeyError:
            out = None
        if out is None:
            self.unhandled.append(args)
            return NOT_FOUND
        if isinstance(out, tuple):
            return out
        return 0, out, ""

    def _jq(self, args):
        return args[args.index("--jq") + 1] if "--jq" in args else None

    def _answer(self, args):
        if args[0] != "api":
            return None
        path = next(
            a for a in args[1:] if not a.startswith("-") and a not in (self._jq(args),)
        )
        jq = self._jq(args)
        m = re.match(r"orgs/([^/]+)/repos", path)
        if m:
            rows = [r for k, r in self.repos.items() if k.split("/")[0] == m[1]]
            if not rows and not any(k.startswith(m[1] + "/") for k in self.repos):
                return NOT_FOUND
            return "\n".join(json.dumps(r) for r in rows)
        m = re.match(r"orgs/([^/]+)/teams/([^/]+)/members", path)
        if m:
            members = self.teams.get(f"{m[1]}/{m[2]}")
            if members is None:
                return NOT_FOUND
            return (
                json.dumps([{"login": x} for x in members])
                if jq is None
                else "\n".join(members)
            )
        m = re.match(r"repos/([^/]+)/([^/?]+)(.*)$", path)
        if not m:
            return None
        key, rest = f"{m[1]}/{m[2]}", m[3]
        row = self.repos.get(key)
        if row is None:
            return NOT_FOUND
        if rest == "":
            body = {
                **row,
                "html_url": row["url"],
                "is_template": row["isTemplate"],
                "full_name": key,
            }
            if jq == ".default_branch":
                return row["default_branch"]
            if jq == ".archived":
                return json.dumps(row["archived"])
            return json.dumps(body)
        cm = re.match(r"/contents/?([^?]*)(?:\?ref=(.*))?$", rest)
        if cm:
            branch = cm[2] or row["default_branch"]
            files = self.files.get((key, branch))
            if files is None:
                return NOT_FOUND
            p = cm[1].strip("/")
            if p in files:
                text = files[p]
                enc = base64.b64encode(text.encode()).decode()
                if jq == ".content":
                    return enc
                if jq == ".sha":
                    return _sha(text)
                return f"{_sha(text)}\n{enc}"
            prefix = p + "/" if p else ""
            names = {}
            for f in files:
                if f.startswith(prefix):
                    head, sep, _ = f[len(prefix) :].partition("/")
                    names[head] = "dir" if sep else "file"
            if not names:
                return NOT_FOUND
            if jq == ".sha":
                return "\n".join(_sha(n) for n in names)
            return "\n".join(f"{n}\t{t}" for n, t in sorted(names.items()))
        tm = re.match(r"/git/trees/([^?]+)", rest)
        if tm:
            files = self.files.get((key, tm[1]))
            if files is None:
                return NOT_FOUND
            dirs = sorted(
                {
                    "/".join(f.split("/")[:i])
                    for f in files
                    for i in range(1, f.count("/") + 1)
                }
            )
            entries = [(d, "tree", _sha(d)) for d in dirs] + [
                (f, "blob", _sha(t)) for f, t in files.items()
            ]
            kind = re.search(r'select\(\.type=="(\w+)"\)', jq or "")
            if kind:
                entries = [e for e in entries if e[1] == kind[1]]
            lines = ["false"]
            for p, t, s in entries:
                lines.append(f"{p}\t{s}" if "@tsv" in (jq or "") else p)
            return "\n".join(lines)
        if rest.startswith("/commits"):
            if jq:
                return "2026-09-20T06:00:00Z"
            return json.dumps(
                [
                    {
                        "commit": {"committer": {"date": "2026-09-20T06:00:00Z"}},
                        "author": {"login": "prof"},
                    }
                ]
            )
        return None


COURSE, LIVE, OLD1, OLD2 = "c-org-e1", "c-org-f2026", "c-org-f2025", "c-org-s2025"
SCHEDULE = """\
timezone: Europe/Berlin
semester_start: 2026-09-07
semester_end: 2026-12-18
releases:
  syllabus:
    event_datetime: 2026-09-08T10:00
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: SYLLABUS.md
  lecture-1:
    event_datetime: 2026-09-10T10:00
    number: 1
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/01_intro
  lecture-2:
    event_datetime: 2026-09-24T10:00
    number: 2
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/02_trees
  lecture-3:
    event_datetime: 2026-10-08T10:00
    number: 3
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/03_forests
assignments:
  a1:
    number: 1
    course_source_repo: assignment-a1
    handout_datetime: 2026-09-15T10:00
    due_datetime: 2026-09-27T23:59
  a2:
    number: 2
    course_source_repo: assignment-a2
    handout_datetime: 2026-10-20T10:00
    due_datetime: 2026-11-01T23:59
"""


def world() -> FakeGitHub:
    gh = FakeGitHub()
    gh.repo(
        COURSE,
        ".github",
        {
            "dsl-course.yml": "course_name: ML\ncourse_code: E1\ncourse_description: d\npeople:\n  course_admins:\n    - github_handle: admin1\n      email: a@x.edu\n",
            "semesters.yml": f"semesters:\n  - {LIVE}\n  - {OLD1}\n  - {OLD2}\n",
            "opencourse.yml": "enabled: false\n",
            ".github/workflows/scheduled-release.yml": "on: push\n",
        },
    )
    gh.repo(
        COURSE,
        "course-materials-f2026",
        {
            "SYLLABUS.md": "# Syllabus\n",
            "lectures/01_intro/notes.md": "x",
            "lectures/02_trees/notes.md": "x",
            "lectures/03_forests/notes.md": "x",
            ".releaseignore": "drafts/\n",
        },
        topics=["dsl-materials"],
    )
    for name in ("assignment-a1", "assignment-a2"):
        gh.repo(
            COURSE,
            name,
            {"README.md": "# A\n", "starter.py": "x"},
            branches={
                "solution": {
                    "README.md": "# A\n",
                    "grading_config.yml": "type: individual\nstarter: handwritten\n",
                    "solution/a.py": "x",
                }
            },
            isTemplate=True,
            topics=["dsl-assignment"],
        )
    for org, archived in ((LIVE, False), (OLD1, True), (OLD2, True)):
        gh.repo(
            org,
            "semester-config",
            {
                "schedule.yml": SCHEDULE,
                "instructors.yml": "instructors:\n  - github_handle: prof\n    email: p@x.edu\n",
                "students.csv": "email,name,github_handle,student_id,enrol_code,code_sent_at,onboarded,role\n",
                ".system/dsl-course.yml": f"course: {COURSE}\n",
            },
            archived=archived,
        )
        gh.repo(org, "join", {"README.md": "x"})
        gh.repo(
            org, f"{org}.github.io", {"index.md": "# Welcome\n"}, visibility="public"
        )
        gh.repo(org, "materials", {"lectures/01_intro/notes.md": "x"})
        gh.teams[f"{org}/instructors"] = ["prof"]
    return gh


def _status(org: str, end: str) -> str:
    """The part of a semester's status.json the course tick reads."""
    return json.dumps(
        {
            "semester": {
                "org": org,
                "live": True,
                "timezone": "Europe/Berlin",
                "end": end,
                "template_moments": {
                    "assignment-a1": "2026-09-15T10:00:00+02:00",
                    "assignment-a2": "2026-10-20T10:00:00+02:00",
                },
            }
        }
    )


@pytest.fixture
def counted(monkeypatch):
    """The fake GitHub, counting, with the memos a CLI run turns on (`read_once`,
    `hold_listings`) and every per-process read memo empty - one tick, one process."""
    gh = world()
    # The two archived semesters' files were frozen when they ended.
    for org, end in ((LIVE, "2026-12-18"), (OLD1, "2026-02-10"), (OLD2, "2025-07-15")):
        gh.files[(f"{org}/semester-config", "main")][".system/status.json"] = _status(
            org, end
        )
    for (module, name), real in _REAL.items():
        monkeypatch.setattr(module, name, real)
    monkeypatch.setattr(ghcli, "_run_gh", gh)
    materials.read.cache_clear()
    gh_contents.read_once(True)
    discovery.hold_listings(True)
    yield gh
    gh_contents.read_once(False)
    discovery.hold_listings(False)
    materials.read.cache_clear()


def _reads_of(gh: FakeGitHub, org: str) -> list[str]:
    return [
        a[1] for a in gh.calls if f"/{org}/" in a[1] or a[1].startswith(f"repos/{org}/")
    ]


def test_the_semester_tick_costs_what_it_did(counted):
    gh = counted
    doc = status_json.collect_semester(COURSE, LIVE, NOW)
    # origin/main before decision 0034: 43. The root stub the plan copies (SYLLABUS.md)
    # is the course gather's own read, so the memo answers it.
    assert len(gh.calls) == 43
    assert doc["semester"]["template_moments"]["assignment-a2"] == (
        "2026-10-20T10:00:00+02:00"
    )


def test_the_course_tick_reads_one_file_per_semester(counted):
    gh = counted
    doc = status_json.collect_course(COURSE, NOW)
    # origin/main: 18. Decision 0034 adds one status.json read per registered semester
    # (semesters.yml lists names only, so the two archived ones cost theirs too).
    assert len(gh.calls) == 18 + 3
    for org in (LIVE, OLD1, OLD2):
        assert _reads_of(gh, org) == [
            f"repos/{org}/semester-config/contents/.system/status.json"
        ]
    # No schedule, no "is it archived" probe, for any semester.
    assert not [
        a for a in gh.calls if a[1].endswith(("schedule.yml", "/semester-config"))
    ]
    assert doc["problems"] == []
