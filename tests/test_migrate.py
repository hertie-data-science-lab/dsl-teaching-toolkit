"""The migration tool, against a stubbed GitHub: every step in both directions - a
preview plans and writes nothing, a real run does each step once with Actions disabled
throughout, and a second run finds every step already migrated and writes nothing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from dsl_course import migrate, records, repos
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
WORKFLOW = {".github/workflows/any.yml": b"on: push\n"}
COURSE_WORKFLOWS = {".github/workflows/refresh-actions.yml": b"refresh"}

OLD_PEOPLE = """# This cohort's own instructors/TAs
# - see deployed reference: https://github.com/Sem-f2026/classroom-config/blob/main/people.yml
# For a filled example - see `people.yml.sample`.
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
  a1:
    due_datetime: 2026-10-01T09:00
    cohort_dest_repo: work
releases:
  s1:
    type: lecture
    event_datetime: 2026-10-01T09:00
    deploy:
      - course_source_repo: cm
        course_source_path: lectures/01
        cohort_dest_repo: materials
        cohort_dest_path: lectures/01
events:
  exam:
    type: exam
    event_datetime: 2026-12-01T09:00
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
        self.actions: dict[tuple[str, str], bool] = {}
        self.runs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.commits: list[tuple[str, str, str, str]] = []
        self.paused_at_commit: list[bool] = []
        self.puts: list[tuple[str, str, bool]] = []
        self.fail_rename = False
        self.run_after_pause: tuple[str, str] | None = None

    def add(self, org, name, files=None, *, topics=(), archived=False, template=False,
            branches=None):  # fmt: skip
        self.repos[(org, name)] = {
            "archived": archived,
            "topics": list(topics),
            "isTemplate": template,
            "branches": {"main": dict(files or {}), **(branches or {})},
        }

    def _name(self, org, name):
        return self.redirects.get((org, name), name)

    def _repo(self, org, name):
        return self.repos.get((org, self._name(org, name)))

    def tree(self, org, name, branch="main"):
        return self._repo(org, name)["branches"][branch]

    def enabled(self, org, name):
        return self.actions.get((org, self._name(org, name)), True)

    def workflow_repos(self):
        return [
            key
            for key, r in self.repos.items()
            if any(p.startswith(".github/workflows/") for p in r["branches"]["main"])
        ]

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
            self.paused_at_commit.append(
                not any(self.enabled(*key) for key in self.workflow_repos())
            )
        return True

    def set_repo_topics(self, org, repo, topics):
        self._repo(org, repo)["topics"] = list(topics)
        return True

    def gh(self, *args, **kwargs):
        args = list(args)
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = next(a for a in args if "/" in a and not a.startswith(("-", ".")))
        fields = {}
        for i, a in enumerate(args):
            if a in ("-f", "-F"):
                key, value = args[i + 1].split("=", 1)
                fields[key] = value
        route, _, query = path.partition("?")
        parts = route.split("/")
        if parts[0] != "repos":
            raise AssertionError(f"unexpected gh call {args}")
        org, name = parts[1], parts[2]
        if self._repo(org, name) is None:
            return 1, "gh: Not Found (HTTP 404)"
        key = (org, self._name(org, name))
        if parts[3:] == ["actions", "permissions"]:
            if method == "PUT":
                self.actions[key] = fields["enabled"] == "true"
                self.puts.append((*key, self.actions[key]))
                if not self.actions[key] and self.run_after_pause == key:
                    self.runs.setdefault(key, []).append(("9999-12-31T00:00:00Z", "x"))
                return 0, ""
            return 0, "true" if self.enabled(*key) else "false"
        if parts[3:] == ["actions", "runs"]:
            runs = self.runs.get(key, [])
            if query.startswith("status="):
                state = query.split("=", 1)[1]
                return 0, str(sum(s == state for _, s in runs))
            since = query.split("%3E%3D", 1)[1]
            return 0, str(sum(at >= since for at, _ in runs))
        if len(parts) == 3 and method == "PATCH":
            if self.fail_rename:
                return 1, "HTTP 403: Forbidden"
            new = (org, fields["name"])
            self.repos[new] = self.repos.pop(key)
            self.redirects[key] = fields["name"]
            if key in self.actions:  # a repo keeps its settings across a rename
                self.actions[new] = self.actions.pop(key)
            return 0, ""
        if len(parts) == 3:
            return 0, json.dumps({"default_branch": "main", "name": key[1]})
        raise AssertionError(f"unexpected gh call {args}")


@pytest.fixture
def fake(monkeypatch):
    f = FakeGitHub()
    for name in (
        "list_org_repos",
        "repo_blob_shas",
        "get_file_content",
        "move_files",
        "set_repo_topics",
        "gh",
    ):
        monkeypatch.setattr(migrate, name, getattr(f, name))
    # The REAL default_branch / repo_missing, over the same stub: a 404 is a 404.
    monkeypatch.setattr(repos, "gh", f.gh)
    monkeypatch.setattr(migrate, "central_ref_for", lambda org: "main")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return f


def _render(f, org, repo, files):
    f.move_files(org, repo, {}, "ci: refresh", files=files)
    return 0


def _migrated_course(fake, monkeypatch):
    fake.add(
        COURSE,
        ".github",
        {"semesters.yml": b"semesters:\n- Sem-f2026\n", **COURSE_WORKFLOWS},
        topics=["dsl-course-hub"],
    )
    fake.add(
        COURSE, "course-materials-f2026", {".system/MAINTAINING.md": b"g", **WORKFLOW}
    )
    monkeypatch.setattr(
        migrate.seed, "github_workflow_files", lambda org, ref: COURSE_WORKFLOWS
    )


@pytest.fixture
def semester(fake, monkeypatch):
    """An unmigrated semester under a migrated course, and its re-render recorders."""
    _migrated_course(fake, monkeypatch)
    fake.add(SEM, ".github", {"dsl-course.yml": f"course: {COURSE}\n".encode()},
             topics=[OLD_SEMESTER_TOPIC])  # fmt: skip
    fake.add(SEM, OLD_JOIN_REPO, {"README.md": b"# Join\n", **WORKFLOW})
    fake.add(SEM, "materials", {"lectures/01/x.md": b"x"})
    fake.add(SEM, "sem-f2026.github.io", {"index.md": b"x", **WORKFLOW})
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
            **WORKFLOW,
            **OLD_RECORDS,
        },
    )
    joined = {".github/workflows/onboard.yml": b"onboard"}
    lock, readme = b"assignments: {}\n", {"README.md": b"# readme\n"}
    monkeypatch.setattr(migrate, "join_files", lambda org: joined)
    monkeypatch.setattr(migrate, "team_lock_content", lambda course, sem: lock)
    monkeypatch.setattr(migrate, "profile_files", lambda org, **k: readme)
    calls: list[str] = []

    def rerender(name, repo, files):
        return lambda *a, **k: calls.append(name) or _render(fake, SEM, repo, files)

    monkeypatch.setattr(
        migrate, "refresh_join_workflows", rerender("join", JOIN_REPO, joined)
    )
    monkeypatch.setattr(
        migrate,
        "refresh_config_system_files",
        lambda org, ref: (
            calls.append("config")
            or _render(fake, org, CONFIG_REPO, migrate.config_system_files(ref))
        ),
    )
    monkeypatch.setattr(migrate, "refresh_semester_pointer", lambda org, course: 0)
    monkeypatch.setattr(
        migrate,
        "sync_team_lock",
        lambda c, s: (
            _render(fake, s, CONFIG_REPO, {records.path("lock"): lock})
            or SimpleNamespace(ok=True)
        ),
    )
    monkeypatch.setattr(
        migrate, "update_profile_readme", rerender("profile", ".github", readme)
    )
    monkeypatch.setattr(
        migrate.status,
        "refresh",
        lambda course, sem=None: (
            calls.append("status")
            or _render(
                fake,
                sem,
                CONFIG_REPO,
                {
                    records.path("status"): json.dumps(
                        {"semester": {}, "problems": []}
                    ).encode()
                },
            )
        ),
    )
    return calls


def _main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["migrate", *argv])
    return migrate.main()


def _state(fake):
    return json.dumps(
        {f"{o}/{n}": r for (o, n), r in fake.repos.items()},
        default=repr,
        sort_keys=True,
    )


# ---------------------------------------------------------------- the semester org


def test_a_preview_prints_the_plan_and_writes_nothing(
    fake, semester, monkeypatch, capsys
):
    before = _state(fake)
    assert _main(monkeypatch, SEM) == 0
    out = capsys.readouterr().out
    assert f"rename {OLD_CONFIG_REPO} -> {CONFIG_REPO}" in out
    assert f"rename {OLD_JOIN_REPO} -> {JOIN_REPO}" in out
    assert "move 10 record file(s) under .system/" in out
    # The pause names every repo whose workflows act on the semester, the course's too.
    assert f"disable Actions in {SEM}/{OLD_CONFIG_REPO}, {SEM}/{OLD_JOIN_REPO}" in out
    assert f"{SEM}/sem-f2026.github.io, {COURSE}/.github" in out
    assert "PREVIEW - nothing was written" in out
    assert _state(fake) == before
    assert fake.commits == [] and fake.puts == [] and semester == []


def test_a_real_run_migrates_every_step_once_with_actions_off(
    fake, semester, monkeypatch, capsys
):
    markers = {p: blob_sha(b) for p, b in OLD_RECORDS.items()}

    assert _main(monkeypatch, SEM, "--no-preview") == 0

    names = {n for (o, n) in fake.repos if o == SEM}
    assert {CONFIG_REPO, JOIN_REPO} <= names
    assert not names & {OLD_CONFIG_REPO, OLD_JOIN_REPO}
    tree = fake.tree(SEM, CONFIG_REPO)
    moved = migrate.fold(set(OLD_RECORDS), migrate.SEMESTER_MOVES)
    assert moved["autograde/a1/_graded.json"] == ".system/autograde/a1/_graded.json"
    assert moved["cohort-gradebook.csv"] == ".system/semester-gradebook.csv"
    for old, new in moved.items():
        assert old not in tree, old
        # status.json and the lock are rewritten afterwards, by this engine.
        if new not in (records.path("status"), records.path("lock")):
            assert blob_sha(tree[new]) == markers[old], old
    assert not [p for p in tree if p.endswith(".sample")]
    assert "people.yml" not in tree
    text = tree[INSTRUCTORS_FILE].decode()
    instructors = yaml.safe_load(text)["instructors"]
    assert [(p["github_handle"], p["role"]) for p in instructors] == [
        ("prof", "instructor"),
        ("ta", "teaching_assistant"),
    ]
    assert "# a comment faculty wrote" in text
    assert "people.yml.sample" not in text and OLD_CONFIG_REPO not in text
    assert "example-course/semester-org/instructors.yml" in text
    assert f"course: {COURSE}" in tree[records.path("pointer")].decode()
    assert "dsl-course.yml" not in fake.tree(SEM, ".github")
    schedule = tree["schedule.yml"].decode()
    assert "cohort_dest" not in schedule
    assert "    kind: lecture" in schedule and "    kind: exam" in schedule
    assert fake._repo(SEM, ".github")["topics"] == [SEMESTER_TOPIC]
    # Every commit landed with Actions off everywhere - but the status check, which runs
    # after the unpause - and all are back on now.
    assert fake.commits and all(fake.paused_at_commit[:-1])
    assert all(fake.enabled(*key) for key in fake.workflow_repos())
    assert semester == ["join", "config", "profile", "status"]
    layout = [c for c in fake.commits if c[3] == migrate.LAYOUT_COMMIT]
    assert [(c[0], c[1]) for c in layout] == [(SEM, CONFIG_REPO), (SEM, ".github")]


def test_a_second_run_finds_every_step_already_migrated(
    fake, semester, monkeypatch, capsys
):
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    commits, puts = list(fake.commits), list(fake.puts)
    semester.clear()
    capsys.readouterr()

    assert _main(monkeypatch, SEM, "--no-preview") == 0
    out = capsys.readouterr().out
    # Eight steps, each "already migrated" in the plan and again in the run: nothing is
    # paused, nothing written, status.json not rewritten.
    assert out.count("already migrated") == 16
    assert fake.commits == commits and fake.puts == puts and semester == []


def test_a_failed_step_stops_and_says_the_org_is_still_paused(
    fake, semester, monkeypatch, capsys
):
    fake.fail_rename = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "rename repos did not verify - stopped here" in err
    assert "Rollback: rename each repo back" in err
    assert f"Actions are still DISABLED in: {SEM}/{OLD_CONFIG_REPO}" in err
    assert "actions/permissions -F enabled=true" in err
    assert not fake.enabled(SEM, OLD_CONFIG_REPO) and not fake.enabled(
        COURSE, ".github"
    )
    assert fake.commits == [] and semester == []


def test_a_crash_mid_run_says_the_org_is_still_paused(
    fake, semester, monkeypatch, capsys
):
    def boom(*a, **k):
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(migrate, "move_files", boom)
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "layout: stopped by an error - HTTP 502" in err
    assert "Actions are still DISABLED in:" in err


def test_a_stopped_run_is_released_by_the_next_one(fake, semester, monkeypatch, capsys):
    fake.fail_rename = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    fake.fail_rename = False
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert all(fake.enabled(*key) for key in fake.workflow_repos())


def test_a_run_that_starts_after_the_pause_stops_it(
    fake, semester, monkeypatch, capsys
):
    fake.run_after_pause = (COURSE, ".github")
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f"a run started after the pause in {COURSE}/.github" in err
    assert "wait for it to finish, then re-run the migration" in err
    assert fake.commits == []


def test_a_run_in_progress_refuses_the_migration(fake, semester, monkeypatch, capsys):
    fake.runs[(SEM, OLD_JOIN_REPO)] = [("2026-09-24T09:00:00Z", "in_progress")]
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert f"queued or running in {SEM}/{OLD_JOIN_REPO}" in capsys.readouterr().err
    assert fake.commits == [] and fake.puts == []


def test_an_archived_semester_is_never_touched(fake, semester, monkeypatch, capsys):
    fake._repo(SEM, OLD_CONFIG_REPO)["archived"] = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "archived semesters are never touched" in capsys.readouterr().err
    assert fake.commits == [] and fake.puts == []


@pytest.mark.parametrize("unfinished", ["registry", "paused"])
def test_a_semester_waits_for_its_whole_course(
    fake, semester, monkeypatch, capsys, unfinished
):
    if unfinished == "registry":
        fake.tree(COURSE, ".github")["cohort-courses-pages.yml"] = b"cohorts: []\n"
    else:
        fake.actions[(COURSE, ".github")] = False  # the course's own run stopped paused
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert f"{COURSE} is not fully migrated" in capsys.readouterr().err
    assert fake.puts == [] and fake.enabled(COURSE, "course-materials-f2026")


def test_it_never_runs_inside_a_workflow(fake, semester, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert _main(monkeypatch, SEM) == 1
    assert "never from a workflow" in capsys.readouterr().err


def test_a_repo_that_is_not_there_reads_as_empty_not_as_an_error(fake, semester):
    # Not listed at all, and listed but answering 404 (the listing raced a rename): both
    # are "not migrated yet", through the real default_branch.
    assert migrate._files(SEM, CONFIG_REPO) == {}
    fake.list_org_repos = lambda org: [{"name": CONFIG_REPO, "archived": False}]
    migrate.list_org_repos = fake.list_org_repos
    assert migrate._files(SEM, CONFIG_REPO) == {}


def test_a_semester_without_a_welcome_repo_still_migrates(
    fake, semester, monkeypatch, capsys
):
    del fake.repos[(SEM, OLD_JOIN_REPO)]
    fake.add(SEM, JOIN_REPO, {"README.md": b"# Join\n"})
    assert _main(monkeypatch, SEM, "--no-preview") == 0


def test_a_schedule_the_rewrite_cannot_fix_fails_the_keys_verify(
    fake, semester, monkeypatch, capsys
):
    # A flow-style entry the line rewrite does not reach: the new engine still refuses
    # it, so the step must not pass.
    fake.tree(SEM, OLD_CONFIG_REPO)["schedule.yml"] = (
        b"assignments:\n  a1: {cohort_dest_repo: m}\n"
    )
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "keys did not verify" in capsys.readouterr().err


def test_an_unconvertible_people_yml_stops_before_anything_is_written(
    fake, semester, monkeypatch, capsys
):
    fake.tree(SEM, OLD_CONFIG_REPO)["people.yml"] = b"people:\n  course_admins: []\n"
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "cannot be converted faithfully" in capsys.readouterr().err
    assert [c for c in fake.commits if c[3] == migrate.LAYOUT_COMMIT] == []


def test_the_seeded_skeleton_becomes_the_new_skeleton(fake, semester, monkeypatch):
    skeleton = (
        "# INSTRUCTOR-OWNED\n#\n# people:\n#   instructors:\n#     - github_handle: x\n"
    )
    fake.tree(SEM, OLD_CONFIG_REPO)["people.yml"] = skeleton.encode()
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    text = fake.tree(SEM, CONFIG_REPO)[INSTRUCTORS_FILE].decode()
    assert text == migrate.semester_scaffold(SEM, INSTRUCTORS_FILE, "main")
    assert "# instructors:" in text


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
            ".github/workflows/old.yml": b"old",
        },
        topics=["dsl-course-hub"],
    )
    fake.add(
        COURSE,
        "assignment-1-f2026",
        {"README.md": b"brief", **WORKFLOW},
        template=True,
        branches={
            "solution": {"grading_config.yml": b"format: ipynb\nautograde: true\n"}
        },
    )
    fake.add(
        COURSE,
        "course-materials-f2026",
        {"MAINTAINING.md": b"guide", "SYLLABUS.md.sample": b"s", "SYLLABUS.md": b"x"},
    )
    monkeypatch.setattr(
        migrate.seed, "github_workflow_files", lambda org, ref: COURSE_WORKFLOWS
    )
    calls: list[str] = []
    monkeypatch.setattr(
        migrate.seed,
        "refresh",
        lambda org: (
            calls.append("refresh") or _render(fake, org, ".github", COURSE_WORKFLOWS)
        ),
    )
    monkeypatch.setattr(
        migrate.status,
        "refresh",
        lambda org, sem=None: (
            calls.append("status")
            or _render(
                fake,
                org,
                ".github",
                {
                    records.path("status"): json.dumps(
                        {"course": {"semesters": []}, "problems": []}
                    ).encode()
                },
            )
        ),
    )
    return calls


def test_a_course_preview_writes_nothing(fake, course, monkeypatch, capsys):
    before = _state(fake)
    assert _main(monkeypatch, COURSE) == 0
    out = capsys.readouterr().out
    assert "cohort-courses-pages.yml -> semesters.yml" in out
    assert "assignment-1-f2026@solution/grading_config.yml: format: -> formats:" in out
    assert f"disable Actions in {COURSE}/.github, {COURSE}/assignment-1-f2026" in out
    assert _state(fake) == before and fake.puts == [] and course == []


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
        ".system/MAINTAINING.md",
        ".system/SYLLABUS.md.sample",
        "SYLLABUS.md",
    }
    assert all(fake.paused_at_commit[:-1]) and fake.enabled(COURSE, ".github")
    assert course == ["refresh", "status"]

    commits, puts = list(fake.commits), list(fake.puts)
    course.clear()
    capsys.readouterr()
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert capsys.readouterr().out.count("already migrated") == 18
    assert course == [] and fake.commits == commits and fake.puts == puts


def test_a_registry_already_renamed_but_keyed_the_old_way_is_rewritten(
    fake, course, monkeypatch
):
    tree = fake.tree(COURSE, ".github")
    del tree["cohort-courses-pages.yml"]
    tree["semesters.yml"] = b"cohorts:\n- Sem-f2026\n"
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert yaml.safe_load(tree["semesters.yml"]) == {"semesters": ["Sem-f2026"]}


def test_an_archived_course_is_never_touched(fake, course, monkeypatch, capsys):
    fake._repo(COURSE, ".github")["archived"] = True
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    assert "an archived course is never touched" in capsys.readouterr().err


# ---------------------------------------------------------------- the rewrites


def test_people_yml_becomes_one_list_with_a_role_each():
    new = migrate.people_to_instructors(OLD_PEOPLE)
    assert "\ninstructors:\n" in new
    assert migrate.same_people(OLD_PEOPLE, new)


@pytest.mark.parametrize(
    "old",
    [
        # a comment after a role key
        "people:\n  instructors:   # the lecturers\n    - github_handle: a\n      email: e\n",
        # four-space indent
        (
            "people:\n    instructors:\n        - github_handle: a\n          email: e\n"
            "    teaching_assistants:\n        - github_handle: b\n"
        ),
        # flow-style entries, in a block list and as the role's whole value
        (
            "people:\n  instructors:\n    - {github_handle: a, email: e}\n"
            "  teaching_assistants: [{github_handle: b}, {github_handle: c}]\n"
        ),
        # entries at the role key's own indent
        "people:\n  instructors:\n  - github_handle: a\n",
    ],
    ids=["role-comment", "four-space", "flow", "flush-dash"],
)
def test_every_shape_of_people_yml_converts_faithfully(old):
    new = migrate.people_to_instructors(old)
    assert new is not None
    assert migrate.same_people(old, new), new


def test_an_unseeded_people_shape_is_left_for_a_person():
    assert migrate.people_to_instructors("people:\n  course_admins: []\n") is None
    assert migrate.people_to_instructors("instructors: []\n") is None


def test_the_format_key_becomes_a_list():
    assert migrate.formats_line("format: ipynb") == "formats: [ipynb]"
    assert migrate.formats_line('format: "Rmd"  # r') == 'formats: ["Rmd"]  # r'
    assert migrate.formats_line('format: "a#b"  # c') == 'formats: ["a#b"]  # c'
    assert migrate.formats_line("format:") == "formats: []"
    assert migrate.formats_line('format: ""  # none') == "formats: []  # none"
    assert migrate.formats_line("formats: [py]") == "formats: [py]"


def test_the_registry_key_is_renamed_and_nothing_else():
    assert migrate.registry_keys("# c\ncohorts:\n- a\n") == "# c\nsemesters:\n- a\n"
