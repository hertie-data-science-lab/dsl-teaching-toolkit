"""The migration tool, against a stubbed GitHub: every step in both directions - a
preview plans and writes nothing, a real run does each step once, and a second run finds
every step already migrated."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from dsl_course import migrate, records
from dsl_course.central import PAUSE_VARIABLE
from dsl_course.course import (
    CONFIG_REPO,
    INSTRUCTORS_FILE,
    JOIN_REPO,
    OLD_CONFIG_REPO,
    OLD_JOIN_REPO,
    OLD_SEMESTER_TOPIC,
    SEMESTER_TOPIC,
)
from dsl_course.gh_contents import blob_sha

SEM, COURSE = "Sem-f2026", "Course-E1"

OLD_PEOPLE = """# This cohort's own instructors/TAs
people:
  instructors:
    - github_handle: "prof"
      email: "p@example.org"
      name: "Prof"
  teaching_assistants:
    - github_handle: "ta"   # a comment faculty wrote
      email: "t@example.org"
      start: "2026-08-01"
"""

OLD_SCHEDULE = """timezone: Europe/Berlin
assignments:
  - key: a1
    type: individual
    cohort_dest_repo: work
releases:
  - key: s1
    type: lecture
    cohort_dest_repo: materials
    cohort_dest_path: lectures/01
events:
  - key: exam
    type: exam
"""

# The fire-once markers and every other record, at their pre-0010 paths.
OLD_RECORDS = {
    "assignments.lock.yml": b"assignments:\n",
    "cohort-gradebook.csv": b"github_handle\n",
    "snapshots/a1.csv": b"repo,sha\n",
    "autograde/a1/_graded.json": b'{"ok": true}\n',
    "autograde/a1/someone.pdf": bytes(range(256)),
    "solutions/a1.json": b'{"released": "2026-10-01"}\n',
    "gradebook/distributed.csv": b"target,sha\n",
    "team-formation/mailed.csv": b"recipient\n",
    ".dsl/status.json": b'{"schema": "dsl.status/1", "problems": []}\n',
    ".dsl/outcomes/release.now.json": b"{}\n",
}


class FakeGitHub:
    """The org state the tool reads and writes, and every GitHub call it makes."""

    def __init__(self) -> None:
        self.repos: dict[tuple[str, str], dict] = {}
        self.redirects: dict[tuple[str, str], str] = {}
        self.vars: dict[str, dict[str, str]] = {}
        self.runs: dict[str, int] = {}
        self.commits: list[tuple[str, str, str, str]] = []
        self.fail_rename = False

    def add(self, org, name, files=None, *, topics=(), archived=False, template=False,
            branches=None):  # fmt: skip
        self.repos[(org, name)] = {
            "archived": archived,
            "topics": list(topics),
            "isTemplate": template,
            "branches": {"main": dict(files or {}), **(branches or {})},
        }

    def _repo(self, org, name):
        name = self.redirects.get((org, name), name)
        return self.repos.get((org, name))

    def tree(self, org, name, branch="main"):
        return self._repo(org, name)["branches"][branch]

    # -- the names migrate imports
    def list_org_repos(self, org):
        return [
            {
                "name": n,
                "archived": r["archived"],
                "topics": r["topics"],
                "isTemplate": r["isTemplate"],
            }
            for (o, n), r in self.repos.items()
            if o == org
        ]

    def default_branch(self, org, repo):
        return "main"

    def repo_blob_shas(self, org, repo, branch):
        found = self._repo(org, repo)
        if found is None:
            return {}
        return {p: blob_sha(b) for p, b in found["branches"].get(branch, {}).items()}

    def get_file_content(self, org, repo, path, ref=""):
        found = self._repo(org, repo)
        if found is None:
            return None
        body = found["branches"].get(ref or "main", {}).get(path)
        return None if body is None else body.decode()

    def move_files(
        self, org, repo, moves, message, *, files=None, delete=(), branch=""
    ):
        tree = self.tree(org, repo, branch or "main")
        before = dict(tree)
        for old, new in moves.items():
            if old in tree:
                tree.setdefault(new, tree[old])
                del tree[old]
        for path, body in (files or {}).items():
            tree[path] = body
        for path in delete:
            tree.pop(path, None)
        if tree != before:
            self.commits.append((org, repo, branch or "main", message))
        return True

    def set_repo_topics(self, org, repo, topics):
        self._repo(org, repo)["topics"] = list(topics)
        return True

    def gh(self, *args, **kwargs):
        args = list(args)
        method = "GET"
        if "--method" in args:
            method = args[args.index("--method") + 1]
        path = next(a for a in args if "/" in a and not a.startswith(("-", ".")))
        fields = dict(
            args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "-f"
        )
        parts = path.split("?")[0].split("/")
        if parts[0] == "orgs" and parts[2:4] == ["actions", "variables"]:
            if method == "GET":
                store = self.vars.get(parts[1], {})
                name = parts[4]
                return (0, store[name]) if name in store else (1, "HTTP 404: Not Found")
            store = self.vars.setdefault(parts[1], {})
            if method == "POST":
                store[fields["name"]] = fields["value"]
            elif method == "PATCH":
                store[parts[4]] = fields["value"]
            elif method == "DELETE":
                store.pop(parts[4], None)
            return 0, ""
        if parts[0] == "repos" and parts[3:5] == ["actions", "runs"]:
            return 0, str(self.runs.get(parts[1], 0) if "in_progress" in path else 0)
        if parts[0] == "repos" and method == "PATCH" and len(parts) == 3:
            if self.fail_rename:
                return 1, "HTTP 403: Forbidden"
            org, old = parts[1], parts[2]
            self.repos[(org, fields["name"])] = self.repos.pop((org, old))
            self.redirects[(org, old)] = fields["name"]
            return 0, ""
        raise AssertionError(f"unexpected gh call {args}")


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    for name in (
        "list_org_repos", "default_branch", "repo_blob_shas", "get_file_content",
        "move_files", "set_repo_topics", "gh",
    ):  # fmt: skip
        monkeypatch.setattr(migrate, name, getattr(f, name))
    monkeypatch.setattr(migrate, "central_ref_for", lambda org: "main")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return f


def _render(f, org, repo, files):
    f.move_files(org, repo, {}, "ci: refresh", files=files)
    return 0


@pytest.fixture
def semester(fake, monkeypatch):
    """An unmigrated semester under a migrated course, and its re-render recorders."""
    fake.add(COURSE, ".github", {"semesters.yml": b"semesters:\n- Sem-f2026\n"},
             topics=["dsl-course-hub"])  # fmt: skip
    fake.add(SEM, ".github", {"dsl-course.yml": f"course: {COURSE}\n".encode()},
             topics=[OLD_SEMESTER_TOPIC])  # fmt: skip
    fake.add(SEM, OLD_JOIN_REPO, {"README.md": b"# Join\n"})
    fake.add(SEM, "materials", {"lectures/01/x.md": b"x"})
    fake.add(
        SEM,
        OLD_CONFIG_REPO,
        {
            "students.csv": b"email\n",
            "schedule.yml": OLD_SCHEDULE.encode(),
            "people.yml": OLD_PEOPLE.encode(),
            "grading_sheets/a1.yml": b"rows: []\n",
            "students.csv.sample": b"sample\n",
            "grading_sheets/a1.yml.sample": b"sample\n",
            **OLD_RECORDS,
        },
    )
    joined = {".github/workflows/onboard.yml": b"onboard"}
    monkeypatch.setattr(migrate, "join_files", lambda org: joined)
    calls: list[str] = []
    monkeypatch.setattr(
        migrate, "refresh_join_workflows",
        lambda org: calls.append("join") or _render(fake, org, JOIN_REPO, joined),
    )  # fmt: skip
    monkeypatch.setattr(
        migrate, "refresh_config_system_files",
        lambda org, ref: calls.append("config")
        or _render(fake, org, CONFIG_REPO, migrate.config_system_files(ref)),
    )  # fmt: skip
    monkeypatch.setattr(migrate, "refresh_semester_pointer", lambda org, course: 0)
    monkeypatch.setattr(
        migrate, "sync_team_lock", lambda c, s: SimpleNamespace(ok=True)
    )
    monkeypatch.setattr(migrate, "update_profile_readme", lambda org, **k: 0)
    monkeypatch.setattr(
        migrate.status, "refresh",
        lambda course, sem=None: calls.append("status") or _render(
            fake, sem, CONFIG_REPO,
            {records.path("status"): json.dumps({"problems": []}).encode()},
        ),
    )  # fmt: skip
    return calls


def _main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["migrate", *argv])
    return migrate.main()


# ---------------------------------------------------------------- the semester org


def test_a_preview_prints_the_plan_and_writes_nothing(
    fake, semester, monkeypatch, capsys
):
    before = json.dumps(
        {f"{o}/{n}": r for (o, n), r in fake.repos.items()},
        default=repr,
        sort_keys=True,
    )
    assert _main(monkeypatch, SEM) == 0
    out = capsys.readouterr().out
    assert f"rename {OLD_CONFIG_REPO} -> {CONFIG_REPO}" in out
    assert f"rename {OLD_JOIN_REPO} -> {JOIN_REPO}" in out
    assert "move 10 record file(s) under .system/" in out
    assert "PREVIEW - nothing was written" in out
    after = json.dumps(
        {f"{o}/{n}": r for (o, n), r in fake.repos.items()},
        default=repr,
        sort_keys=True,
    )
    assert after == before
    assert fake.commits == [] and fake.vars == {} and semester == []


def test_a_real_run_migrates_every_step_once(fake, semester, monkeypatch, capsys):
    markers = {p: blob_sha(b) for p, b in OLD_RECORDS.items()}

    assert _main(monkeypatch, SEM, "--no-preview") == 0

    names = {n for (o, n) in fake.repos if o == SEM}
    assert {CONFIG_REPO, JOIN_REPO} <= names
    assert not names & {OLD_CONFIG_REPO, OLD_JOIN_REPO}
    tree = fake.tree(SEM, CONFIG_REPO)
    # Every record MOVED, byte for byte: present once, at the new path only.
    moved = migrate.fold(set(OLD_RECORDS), migrate.SEMESTER_MOVES)
    assert moved["autograde/a1/_graded.json"] == ".system/autograde/a1/_graded.json"
    assert moved["cohort-gradebook.csv"] == ".system/semester-gradebook.csv"
    for old, new in moved.items():
        assert old not in tree, old
        # status.json alone is rewritten afterwards, by the final check.
        if new != records.path("status"):
            assert blob_sha(tree[new]) == markers[old], old
    assert not [p for p in tree if p.endswith(".sample")]
    assert "people.yml" not in tree
    instructors = yaml.safe_load(tree[INSTRUCTORS_FILE])["instructors"]
    assert [(p["github_handle"], p["role"]) for p in instructors] == [
        ("prof", "instructor"),
        ("ta", "teaching_assistant"),
    ]
    assert "# a comment faculty wrote" in tree[INSTRUCTORS_FILE].decode()
    assert f"course: {COURSE}" in tree[records.path("pointer")].decode()
    assert "dsl-course.yml" not in fake.tree(SEM, ".github")
    schedule = tree["schedule.yml"].decode()
    assert "cohort_dest" not in schedule
    assert "    type: individual" in schedule  # an assignment's shape stays `type`
    assert "    kind: lecture" in schedule and "    kind: exam" in schedule
    assert fake._repo(SEM, ".github")["topics"] == [SEMESTER_TOPIC]
    assert fake.vars.get(SEM, {}) == {} and fake.vars.get(COURSE, {}) == {}
    assert semester == ["join", "config", "status"]
    layout = [c for c in fake.commits if c[3] == migrate.LAYOUT_COMMIT]
    assert [(c[0], c[1]) for c in layout] == [(SEM, CONFIG_REPO), (SEM, ".github")]


def test_a_second_run_finds_every_step_already_migrated(
    fake, semester, monkeypatch, capsys
):
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    commits = list(fake.commits)
    semester.clear()
    capsys.readouterr()

    assert _main(monkeypatch, SEM, "--no-preview") == 0
    out = capsys.readouterr().out
    # Seven steps, each "already migrated" in the plan and again in the run; the pause
    # is not even set, since there is no work for it to protect.
    assert out.count("already migrated") == 14
    assert fake.vars.get(SEM, {}) == {} and fake.vars.get(COURSE, {}) == {}
    assert semester == ["status"]  # the one check that always runs
    assert fake.commits == commits


def test_a_failed_step_stops_the_run_and_names_its_rollback(
    fake, semester, monkeypatch, capsys
):
    fake.fail_rename = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "rename repos did not verify - stopped here" in err
    assert "Rollback: rename each repo back" in err
    # Paused, and nothing after the failed step ran.
    assert fake.vars[SEM][PAUSE_VARIABLE] == "true"
    assert fake.commits == [] and semester == []


def test_a_run_in_progress_stops_the_pause(fake, semester, monkeypatch, capsys):
    monkeypatch.setattr(migrate, "_runs_alive", lambda org: 0)
    assert migrate.preflight(SEM) is not None
    monkeypatch.setattr(migrate, "_runs_alive", lambda org: 1 if org == COURSE else 0)
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "queued or running" in capsys.readouterr().err
    assert fake.commits == []


def test_an_archived_semester_is_never_touched(fake, semester, monkeypatch, capsys):
    fake._repo(SEM, OLD_CONFIG_REPO)["archived"] = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "archived semesters are never touched" in capsys.readouterr().err
    assert fake.commits == [] and fake.vars == {}


def test_a_semester_waits_for_its_course(fake, semester, monkeypatch, capsys):
    fake.tree(COURSE, ".github")["cohort-courses-pages.yml"] = b"cohorts: []\n"
    assert _main(monkeypatch, SEM) == 1
    assert "migrate the course org first" in capsys.readouterr().err


def test_it_never_runs_inside_a_workflow(fake, semester, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _main(monkeypatch, SEM) == 1
    assert "never from a workflow" in capsys.readouterr().err


# ---------------------------------------------------------------- the course org


@pytest.fixture
def course(fake, monkeypatch):
    fake.add(
        COURSE,
        ".github",
        {
            "cohort-courses-pages.yml": b"# registry\ncohorts:\n- Sem-f2026\n",
            "dsl-course.yml": (
                b"course_name: X\ncohort_defaults:\n  timezone: Europe/Berlin\n"
                b"assignment_defaults:\n  format: ipynb  # the runnable one\n"
            ),
            ".dsl/status.json": b"{}\n",
            ".github/.last-refresh": b"2026-09-01\n",
            ".github/.missing-cohorts": b"",
        },
        topics=["dsl-course-hub"],
    )
    fake.add(
        COURSE, "assignment-1-f2026", {"README.md": b"brief"}, template=True,
        branches={"solution": {"grading_config.yml": b"format: ipynb\nautograde: true\n"}},
    )  # fmt: skip
    fake.add(
        COURSE, "course-materials-f2026",
        {"MAINTAINING.md": b"guide", "SYLLABUS.md.sample": b"s", "SYLLABUS.md": b"x"},
    )  # fmt: skip
    workflows = {".github/workflows/refresh-actions.yml": b"refresh"}
    monkeypatch.setattr(
        migrate.seed, "github_workflow_files", lambda org, ref: workflows
    )
    calls: list[str] = []
    monkeypatch.setattr(
        migrate.seed, "refresh",
        lambda org: calls.append("refresh") or _render(fake, org, ".github", workflows),
    )  # fmt: skip
    monkeypatch.setattr(
        migrate.status, "refresh",
        lambda org, sem=None: calls.append("status") or _render(
            fake, org, ".github",
            {records.path("status"): json.dumps({"problems": []}).encode()},
        ),
    )  # fmt: skip
    return calls


def test_a_course_preview_writes_nothing(fake, course, monkeypatch, capsys):
    assert _main(monkeypatch, COURSE) == 0
    out = capsys.readouterr().out
    assert "cohort-courses-pages.yml -> semesters.yml" in out
    assert "assignment-1-f2026@solution/grading_config.yml: format: -> formats:" in out
    assert fake.commits == [] and fake.vars == {} and course == []


def test_a_course_run_migrates_and_a_second_finds_it_done(
    fake, course, monkeypatch, capsys
):
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    tree = fake.tree(COURSE, ".github")
    assert "cohort-courses-pages.yml" not in tree
    assert yaml.safe_load(tree["semesters.yml"]) == {"semesters": ["Sem-f2026"]}
    assert tree[records.path("heartbeat")] == b"2026-09-01\n"
    assert records.path("missing_semesters") in tree
    assert ".github/.last-refresh" not in tree and ".dsl/status.json" not in tree
    meta = tree["dsl-course.yml"].decode()
    assert "semester_defaults:" in meta and "cohort_defaults" not in meta
    assert "  formats: [ipynb]  # the runnable one" in meta
    grading = fake.tree(COURSE, "assignment-1-f2026", "solution")["grading_config.yml"]
    assert grading == b"formats: [ipynb]\nautograde: true\n"
    materials = fake.tree(COURSE, "course-materials-f2026")
    assert set(materials) == {
        ".system/MAINTAINING.md", ".system/SYLLABUS.md.sample", "SYLLABUS.md"
    }  # fmt: skip
    assert fake.vars.get(COURSE, {}) == {}
    assert course == ["refresh", "status"]

    commits = list(fake.commits)
    course.clear()
    capsys.readouterr()
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert capsys.readouterr().out.count("already migrated") == 16
    assert course == ["status"] and fake.commits == commits


# ---------------------------------------------------------------- the rewrites


def test_people_yml_becomes_one_list_with_a_role_each():
    new = migrate.people_to_instructors(OLD_PEOPLE)
    assert new.startswith("# This cohort's own instructors/TAs\ninstructors:\n")
    assert migrate.same_people(OLD_PEOPLE, new)


def test_an_unseeded_people_shape_is_left_for_a_person():
    assert migrate.people_to_instructors("people:\n  course_admins: []\n") is None
    assert migrate.people_to_instructors("instructors: []\n") is None


def test_the_format_key_becomes_a_list():
    assert migrate.formats_line("format: ipynb") == "formats: [ipynb]"
    assert migrate.formats_line('format: "Rmd"  # r') == 'formats: ["Rmd"]  # r'
    assert migrate.formats_line("formats: [py]") == "formats: [py]"


def test_the_registry_key_is_renamed_and_nothing_else():
    assert migrate.registry_keys("# c\ncohorts:\n- a\n") == "# c\nsemesters:\n- a\n"
