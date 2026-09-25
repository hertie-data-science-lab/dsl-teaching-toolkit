"""The migration tool, against a stubbed GitHub: every step in both directions - a
preview plans and writes nothing, a real run does each step once with Actions disabled
throughout, and a second run finds every step already migrated and writes nothing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from dsl_course import discovery, migrate, records, repos, scaffold
from dsl_course.central import MissingCentralRef
from dsl_course.course import (
    CONFIG_REPO,
    INSTRUCTORS_FILE,
    JOIN_REPO,
    OLD_CONFIG_REPO,
    OLD_JOIN_REPO,
    OLD_SEMESTER_TOPIC,
    SEMESTER_TOPIC,
    SUBMIT_VIA,
)
from dsl_course.faults import NOT_MIGRATED
from dsl_course.gh_contents import blob_sha
from dsl_course.welcome import template

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


class FakeClock:
    """The wall clock `settle` reads and the sleep it waits with: a sleep moves the clock,
    and says so in `calls` (shared with the fake GitHub, so a test reads one sequence)."""

    # Three minutes past a quarter hour: clear of the ticks at :00 and :07.
    def __init__(self, calls: list[str], at: float = 1_790_337_780.0) -> None:
        self.at, self.calls = at, calls
        self.hooks: list = []  # called with the new time after each sleep

    def now(self) -> float:
        return self.at

    def sleep(self, seconds: float) -> None:
        self.calls.append(f"sleep {seconds:.0f}")
        self.at += seconds
        for hook in list(self.hooks):
            hook(self.at)


class FakeGitHub:
    """The org state the tool reads and writes, and every GitHub call it makes."""

    def __init__(self) -> None:
        self.repos: dict[tuple[str, str], dict] = {}
        self.redirects: dict[tuple[str, str], str] = {}
        self.actions: dict[tuple[str, str], dict] = {}
        self.runs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.commits: list[tuple[str, str, str, str]] = []
        self.paused_at_commit: list[bool] = []
        self.puts: list[tuple[str, str, bool]] = []
        self.dispatches: list[tuple[str, dict]] = []  # (org/repo, the fields sent)
        self.fail_dispatch = False
        self.fail_rename = False
        self.redirect_renames = True  # GitHub's 301 from a renamed repo's old name
        self.run_after_pause: tuple[str, str] | None = None
        self.run_after_pause_state = "in_progress"
        self.central_runs: dict[
            str, list[str]
        ] = {}  # the toolkit's: workflow -> states
        # Every call that matters to the pause's order, in order: "runs <org>/<repo>" (a
        # look for live runs), "record <org>" (a pause record written), "off <org>/<repo>"
        # / "on <org>/<repo>" (an Actions switch), "sleep <s>".
        self.calls: list[str] = []

    def add(self, org, name, files=None, *, topics=(), archived=False, template=False,
            branches=None, description=""):  # fmt: skip
        self.repos[(org, name)] = {
            "description": description,
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

    def setting(self, org, name):
        default = {"enabled": True, "allowed_actions": "all"}
        return self.actions.get((org, self._name(org, name)), default)

    def enabled(self, org, name):
        return self.setting(org, name)["enabled"]

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
                "description": r["description"],
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
        # gh_contents.move_files' own rule: a target that differs refuses the commit.
        if any(
            old in tree and new in tree and tree[old] != tree[new]
            for old, new in moves.items()
        ):
            return False
        for old, new in moves.items():
            if old in tree:
                tree.setdefault(new, tree[old])
                del tree[old]
        for path, body in (files or {}).items():
            tree[path] = body
        for path in delete:
            tree.pop(path, None)
        if tree != before:
            if message == migrate.PAUSE_COMMIT:
                self.calls.append(f"record {org}")
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
                # gh's own rule: -f sends a string, -F a typed value (true is a boolean).
                typed = {"true": True, "false": False}.get(value, value)
                fields[key] = typed if a == "-F" else value
        route, _, query = path.partition("?")
        parts = route.split("/")
        if parts[0] != "repos":
            raise AssertionError(f"unexpected gh call {args}")
        org, name = parts[1], parts[2]
        if f"{org}/{name}" == migrate.CENTRAL and parts[3:5] == [
            "actions",
            "workflows",
        ]:
            state = query.split("=", 1)[1]
            return 0, str(self.central_runs.get(parts[5], []).count(state))
        if self._repo(org, name) is None:
            return 1, "gh: Not Found (HTTP 404)"
        key = (org, self._name(org, name))
        if parts[3:] == ["dispatches"] and method == "POST":
            if self.fail_dispatch:
                return 1, "HTTP 422: Unprocessable"
            self.dispatches.append((f"{org}/{name}", fields))
            return 0, ""
        if parts[3:] == ["actions", "permissions"]:
            if method == "PUT":
                on = fields["enabled"] is True
                was = self.setting(*key)
                allowed = fields.get("allowed_actions", was["allowed_actions"])
                self.actions[key] = {
                    "enabled": on,
                    "allowed_actions": allowed if on else None,
                }
                self.puts.append((*key, on))
                self.calls.append(f"{'on' if on else 'off'} {key[0]}/{key[1]}")
                if not on and self.run_after_pause == key:
                    self.runs.setdefault(key, []).append(
                        ("9999-12-31T00:00:00Z", self.run_after_pause_state)
                    )
                return 0, ""
            state = self.setting(*key)
            body = {"enabled": state["enabled"]}
            if state["enabled"]:
                body["allowed_actions"] = state["allowed_actions"]
            return 0, json.dumps(body)
        if parts[3:] == ["actions", "runs"]:
            self.calls.append(f"runs {key[0]}/{key[1]}")
            params = dict(p.split("=", 1) for p in query.split("&"))
            since = params.get("created", "%3E%3D").split("%3E%3D", 1)[1]
            state = params.get("status")
            return 0, str(
                sum(
                    at >= since and state in (None, s)
                    for at, s in self.runs.get(key, [])
                )
            )
        if len(parts) == 3 and method == "PATCH":
            if self.fail_rename:
                return 1, "HTTP 403: Forbidden"
            new = (org, fields["name"])
            self.repos[new] = self.repos.pop(key)
            if "description" in fields:
                self.repos[new]["description"] = fields["description"]
            if self.redirect_renames:
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
    # The checkout is the pinned ref, unless a test says otherwise.
    monkeypatch.setattr(migrate, "git", lambda *a, **k: (0, ""))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(migrate, "_LISTINGS", {})
    f.clock = FakeClock(f.calls)
    monkeypatch.setattr(migrate, "clock", f.clock.now)
    monkeypatch.setattr(migrate, "sleep", f.clock.sleep)
    return f


def _render(f, org, repo, files):
    f.move_files(org, repo, {}, "ci: refresh", files=files)
    return 0


MATERIALS_SYSTEM = {".system/MAINTAINING.md": b"g", ".system/SYLLABUS.md.sample": b"s"}


def _hosted(repo, workflows):
    return {path: f"{repo} {path}".encode() for path in workflows}


def _course_renders(fake, monkeypatch):
    """What the course re-render writes, read off the fake org, for `Course.drift` (the
    names migrate imports) and for the stubbed `seed.refresh` (`_render_course`)."""
    monkeypatch.setattr(
        migrate.seed, "github_workflow_files", lambda org, ref: COURSE_WORKFLOWS
    )
    monkeypatch.setattr(migrate, "discover_semesters", lambda org: [SEM])
    monkeypatch.setattr(
        migrate,
        "discover_content_repos",
        lambda org: sorted(
            r["name"]
            for r in fake.list_org_repos(org)
            if r["name"].startswith("course-materials-")
        ),
    )
    monkeypatch.setattr(
        migrate,
        "discover_assignment_repos",
        lambda org: [r for r in fake.list_org_repos(org) if r["isTemplate"]],
    )
    monkeypatch.setattr(
        migrate,
        "content_workflow_files",
        lambda sems, names, repo, ref, *, workflows: _hosted(repo, workflows),
    )
    monkeypatch.setattr(
        migrate, "materials_system_files", lambda org, repo: MATERIALS_SYSTEM
    )


def _render_course(fake, org):
    _render(fake, org, ".github", COURSE_WORKFLOWS)
    for row in fake.list_org_repos(org):
        name = row["name"]
        if name.startswith("course-materials-"):
            files = {**_hosted(name, migrate.RELEASE_WORKFLOWS), **MATERIALS_SYSTEM}
            _render(fake, org, name, files)
        elif row["isTemplate"] and not row["archived"]:
            _render(fake, org, name, _hosted(name, migrate.TEMPLATE_WORKFLOWS))
    return 0


def _migrated_course(fake, monkeypatch):
    fake.add(
        COURSE,
        ".github",
        {"semesters.yml": b"semesters:\n- Sem-f2026\n", **COURSE_WORKFLOWS},
        topics=["dsl-course-hub"],
    )
    fake.add(COURSE, "course-materials-f2026", WORKFLOW, topics=["dsl-materials"])
    _course_renders(fake, monkeypatch)
    _render_course(fake, COURSE)
    fake.commits.clear()
    fake.paused_at_commit.clear()


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
    assert f"move 10 record file(s) under .system/ in {OLD_CONFIG_REPO}" in out
    # Per step, the writes it would make: each move, each delete, each re-rendered file -
    # a record folder by its file count, never by the names inside it.
    assert "-   assignments.lock.yml -> .system/assignments.lock.yml" in out
    assert "-   autograde/ -> .system/autograde/ (2 file(s))" in out
    assert "someone" not in out
    assert "-   grading_sheets/a1.yml.sample" in out
    # The re-render reads the migrated layout: listed only once the steps above have run.
    assert "differs (listed once the steps above have run)" in out
    assert "unpause automation:\n    - restore the recorded Actions settings" in out
    # The pause names every repo whose workflows act on the semester, the course's too.
    assert f"disable Actions in {SEM}/{OLD_CONFIG_REPO}, {SEM}/{OLD_JOIN_REPO}" in out
    assert f"{SEM}/sem-f2026.github.io, {COURSE}/.github" in out
    assert f"dispatch Sync membership for {SEM} in {COURSE}/.github" in out
    assert "PREVIEW - nothing was written" in out
    assert _state(fake) == before
    assert fake.commits == [] and fake.puts == [] and semester == []
    assert fake.dispatches == []


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
    # The pause record is written first, while everything still runs; every commit after
    # it - status.json included - landed with Actions off everywhere, until the restore
    # (the record's removal comes after it); and all are back on now.
    assert fake.commits[0][3] == migrate.PAUSE_COMMIT
    assert fake.commits[-2][3] == "ci: refresh" and fake.commits[-1][3] == (
        migrate.PAUSE_COMMIT
    )
    assert all(fake.paused_at_commit[1:-1]) and not fake.paused_at_commit[-1]
    assert all(fake.enabled(*key) for key in fake.workflow_repos())
    assert semester == ["join", "config", "profile", "status"]
    # The ticks the pause dropped, dispatched once Actions are back, scoped to this
    # semester exactly as its semester-config push would send them.
    assert fake.dispatches == [
        (
            f"{COURSE}/.github",
            {
                "event_type": "scheduled-release",
                "client_payload[semester_org]": SEM,
                "client_payload[driver]": "migrate",
            },
        ),
        (
            f"{COURSE}/.github",
            {
                "event_type": "sync-membership",
                "client_payload[semester_org]": SEM,
                "client_payload[driver]": "migrate",
            },
        ),
    ]
    out = capsys.readouterr().out
    assert (
        f"dispatched Scheduled release for {SEM}: https://github.com/{COURSE}/.github/"
        "actions/workflows/scheduled-release.yml?query=event%3Arepository_dispatch"
    ) in out
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
    assert len(fake.dispatches) == 2  # nothing paused, so nothing was dropped


def test_a_failed_step_stops_and_says_the_org_is_still_paused(
    fake, semester, monkeypatch, capsys
):
    fake.fail_rename = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "rename repos did not verify - stopped here" in err
    assert "Rollback: rename each repo back" in err
    # The course's repos serve every semester of it: back on at once. The semester's own
    # stay off until the rerun finishes.
    assert (
        f"Actions are back on in the course's repos, which every semester shares: "
        f"{COURSE}/.github, {COURSE}/course-materials-f2026. "
        f"Actions are still DISABLED in: {SEM}/{OLD_CONFIG_REPO}, {SEM}/{OLD_JOIN_REPO}"
    ) in err
    assert "actions/permissions -F enabled=true" in err
    assert not fake.enabled(SEM, OLD_CONFIG_REPO)
    assert fake.enabled(COURSE, ".github") and fake.enabled(
        COURSE, "course-materials-f2026"
    )
    # Nothing but the record of what the settings were; nothing dispatched into a pause.
    assert [c[3] for c in fake.commits] == [migrate.PAUSE_COMMIT] and semester == []
    assert fake.dispatches == []


def test_a_dispatch_that_fails_is_named_and_the_org_is_still_migrated(
    fake, semester, monkeypatch, capsys
):
    fake.fail_dispatch = True
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    err = capsys.readouterr().err
    assert f"could not dispatch Scheduled release for {SEM}" in err
    assert "the next tick catches up" in err
    assert all(fake.enabled(*key) for key in fake.workflow_repos())


def test_the_rename_brings_an_old_description_to_the_current_wording(
    fake, semester, monkeypatch, capsys
):
    old = (
        "[visible to instructors only]: Everything you configure for this cohort is here "
        "- student roster, teams, term schedule, and marking. Students never see it, and "
        "no PII leaves this repo."
    )
    want = repos.current_description(old, "semester")
    assert want and "semester" in want
    fake._repo(SEM, OLD_CONFIG_REPO)["description"] = old
    assert _main(monkeypatch, SEM) == 0
    assert f"-   description -> {want}" in capsys.readouterr().out
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert fake._repo(SEM, CONFIG_REPO)["description"] == want


def test_the_re_render_plan_lists_what_it_deletes(fake, semester, monkeypatch, capsys):
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    fake.tree(SEM, JOIN_REPO)[migrate.RETIRED_JOIN_FORMS[0]] = b"old form"
    capsys.readouterr()
    assert _main(monkeypatch, SEM) == 0
    assert (
        f"-   {JOIN_REPO}/{migrate.RETIRED_JOIN_FORMS[0]} (retired: deleted)"
        in capsys.readouterr().out
    )


def test_a_rename_whose_old_name_does_not_redirect_stops(
    fake, semester, monkeypatch, capsys
):
    fake.redirect_renames = False
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f"{SEM}/{OLD_CONFIG_REPO} does not redirect to {CONFIG_REPO}" in err
    assert "rename repos did not verify - stopped here" in err


def test_a_crash_mid_run_says_the_org_is_still_paused(
    fake, semester, monkeypatch, capsys
):
    def boom(*a, **k):
        raise RuntimeError("HTTP 502")

    monkeypatch.setattr(migrate.Semester, "layout", boom)
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "layout: stopped by an error - HTTP 502" in err
    assert "Actions are still DISABLED in:" in err


def test_a_stopped_run_is_released_by_the_next_one(fake, semester, monkeypatch, capsys):
    fake.fail_rename = True
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    fake.fail_rename = False
    fake.paused_at_commit.clear()
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert all(fake.enabled(*key) for key in fake.workflow_repos())
    # The rerun switched the course's repos off again before it wrote anything.
    assert all(fake.paused_at_commit[:-1]) and not fake.paused_at_commit[-1]


def test_a_run_that_starts_after_the_pause_stops_it(
    fake, semester, monkeypatch, capsys
):
    fake.run_after_pause = (COURSE, ".github")
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f"a workflow run is queued or running in {COURSE}/.github" in err
    assert "wait for it to finish, then re-run the migration" in err
    assert [c[3] for c in fake.commits] == [migrate.PAUSE_COMMIT]


def test_a_run_dispatched_with_the_pause_that_has_finished_passes_it(
    fake, semester, monkeypatch, capsys
):
    # The rehearsal's Sync membership, dispatched in the same second as the pause and
    # finished before the verify read the runs: it wrote nothing after the pause.
    fake.run_after_pause = (COURSE, ".github")
    fake.run_after_pause_state = "completed"
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert "queued or running" not in capsys.readouterr().err


@pytest.mark.parametrize("state", migrate.LIVE_RUN_STATES)
def test_a_run_not_yet_finished_refuses_the_migration(
    fake, semester, monkeypatch, capsys, state
):
    fake.runs[(SEM, OLD_JOIN_REPO)] = [("2026-09-24T09:00:00Z", state)]
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert f"queued or running in {SEM}/{OLD_JOIN_REPO}" in capsys.readouterr().err
    assert fake.commits == [] and fake.puts == []


@pytest.mark.parametrize("workflow", migrate.CENTRAL_REFRESHERS)
def test_any_central_deploy_in_flight_refuses_the_migration(
    fake, semester, monkeypatch, capsys, workflow
):
    # Whatever tier the course runs: a deploy may have picked its orgs before a flip.
    fake.central_runs[workflow] = ["waiting"]
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert f"{migrate.CENTRAL} ({workflow})" in capsys.readouterr().err
    assert fake.commits == [] and fake.puts == []


def test_only_the_re_render_left_lists_its_files(fake, semester, monkeypatch, capsys):
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    fake.tree(SEM, JOIN_REPO)[".github/workflows/onboard.yml"] = b"stale"
    capsys.readouterr()
    assert _main(monkeypatch, SEM) == 0
    assert f"-   {JOIN_REPO}/.github/workflows/onboard.yml" in capsys.readouterr().out


def test_a_status_that_fails_twice_says_both_times_the_org_is_paused(
    fake, semester, monkeypatch, capsys
):
    bad = json.dumps({"semester": {}, "problems": [{"code": NOT_MIGRATED}]}).encode()
    monkeypatch.setattr(
        migrate.status,
        "refresh",
        lambda course, sem=None: _render(
            fake, sem, CONFIG_REPO, {records.path("status"): bad}
        ),
    )
    for _ in range(2):
        assert _main(monkeypatch, SEM, "--no-preview") == 1
        err = capsys.readouterr().err
        assert "status did not verify - stopped here" in err
        assert f"Actions are still DISABLED in: {SEM}/{OLD_CONFIG_REPO}" in err
    assert not fake.enabled(SEM, CONFIG_REPO)


def test_a_semester_re_render_that_failed_off_the_files_is_run_again(
    fake, semester, monkeypatch, capsys
):
    # Every file written, the team lock sync failed: done reads as the verify only once
    # the window is closed, so the rerun re-renders rather than skipping.
    synced = migrate.sync_team_lock
    monkeypatch.setattr(
        migrate,
        "sync_team_lock",
        lambda c, s: synced(c, s) and SimpleNamespace(ok=False),
    )
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "re-render did not verify - stopped here" in capsys.readouterr().err
    semester.clear()
    monkeypatch.setattr(migrate, "sync_team_lock", synced)
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert semester == ["join", "config", "profile", "status"]


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
        # Off with no record of this semester's: another run holds the course.
        fake.actions[(COURSE, ".github")] = {"enabled": False, "allowed_actions": None}
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    if unfinished == "registry":
        assert f"{COURSE} is not fully migrated" in err
        assert f"`python -m dsl_course.migrate {COURSE} --no-preview`" in err
    else:
        assert f"another semester's migration under {COURSE} is in flight" in err
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
    migrate._forget()
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


def test_a_record_already_at_its_new_path_with_other_bytes_stops_the_layout(
    fake, semester, monkeypatch, capsys
):
    tree = fake.tree(SEM, OLD_CONFIG_REPO)
    tree[".system/autograde/a1/_graded.json"] = b'{"ok": false}\n'
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert "layout did not verify - stopped here" in capsys.readouterr().err
    tree = fake.tree(SEM, CONFIG_REPO)
    # Both versions kept, nothing else of the layout written.
    assert tree["autograde/a1/_graded.json"] == b'{"ok": true}\n'
    assert tree[".system/autograde/a1/_graded.json"] == b'{"ok": false}\n'
    assert [c for c in fake.commits if c[3] == migrate.LAYOUT_COMMIT] == []


def test_an_outcome_of_a_renamed_op_takes_the_new_id(
    fake, semester, monkeypatch, capsys
):
    record = {"schema": "dsl.outcome/1", "op": "cohort.check", "conclusion": "done"}
    tree = fake.tree(SEM, OLD_CONFIG_REPO)
    tree[".dsl/outcomes/cohort.check.json"] = json.dumps(record).encode()
    tree[".system/outcomes/cohort.archive.json"] = b"not json"
    assert _main(monkeypatch, SEM) == 0
    out = capsys.readouterr().out
    assert "rename 2 console outcome record(s) to the new op id" in out
    assert (
        "-   .dsl/outcomes/cohort.check.json -> .system/outcomes/semester.check.json"
        in out
    )
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    tree = fake.tree(SEM, CONFIG_REPO)
    assert not [p for p in tree if "outcomes/cohort." in p]
    moved = json.loads(tree[".system/outcomes/semester.check.json"])
    assert moved == {**record, "op": "semester.check"}
    assert tree[".system/outcomes/semester.archive.json"] == b"not json"
    # The other records moved by blob, as before.
    assert (
        tree[".system/outcomes/release.now.json"]
        == OLD_RECORDS[".dsl/outcomes/release.now.json"]
    )


SEEDED_SCHEDULE = """# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.
#
# This cohort's schedule + auto-release plan. Instructors edit it directly.
#
# - see reference: https://github.com/hertie-dsl-demo-f2026/classroom-config/blob/main/schedule.yml
# Our cohort meets on Tuesdays.
timezone: Europe/Berlin
archive:
  title: Cohort archived      # optional - the row's Title column
  details: >-
    This cohort is archived on {date}: every repository in it becomes read-only. You keep read access, so you can still fork or clone anything you want to keep working on into your own account.
"""


def test_seeded_text_takes_the_new_names_and_the_instructors_words_are_listed(
    fake, semester, monkeypatch, capsys
):
    config = fake.tree(SEM, OLD_CONFIG_REPO)
    config["schedule.yml"] = SEEDED_SCHEDULE.encode()
    fake.tree(SEM, OLD_JOIN_REPO)["README.md"] = (
        b"Open a [Join course](https://github.com/Sem-f2026/welcome/issues/new/choose)"
        b" issue.\nThe whole cohort reads this.\n"
    )
    fake.tree(SEM, ".github")["profile/README.md"] = (
        b"Enrol in [`welcome`](https://github.com/Sem-f2026/welcome/issues/new/choose).\n"
        b"Instructors: `classroom-config/people.yml`.\n"
    )
    assert _main(monkeypatch, SEM) == 0
    out = capsys.readouterr().out
    assert "old repo names and seeded wording rewritten in:" in out
    assert f"-   {CONFIG_REPO}/schedule.yml" in out
    assert f"-   {JOIN_REPO}/README.md" in out
    assert "wording for the instructor to review (left as it is):" in out
    # By line, never quoted: a person's words may name someone.
    assert f"-   {CONFIG_REPO}/schedule.yml: line(s) 6" in out
    assert f"-   {JOIN_REPO}/README.md: line(s) 2" in out
    assert "Tuesdays" not in out and "whole cohort" not in out

    assert _main(monkeypatch, SEM, "--no-preview") == 0
    text = fake.tree(SEM, CONFIG_REPO)["schedule.yml"].decode()
    assert "# This semester's schedule + auto-release plan." in text
    assert "example-course/semester-org/schedule.yml" in text
    assert "  title: Semester archived      # optional" in text
    assert "    This semester is archived on {date}: every repository" in text
    assert "# Our cohort meets on Tuesdays." in text  # theirs, left
    instructors = fake.tree(SEM, CONFIG_REPO)[INSTRUCTORS_FILE].decode()
    assert (
        "# This semester's own instructors: its instructors and teaching" in instructors
    )
    join = fake.tree(SEM, JOIN_REPO)["README.md"].decode()
    assert "https://github.com/Sem-f2026/join/issues/new/choose" in join
    assert "The whole cohort reads this." in join
    profile = fake.tree(SEM, ".github")["profile/README.md"].decode()
    assert "[`join`](https://github.com/Sem-f2026/join/issues/new/choose)" in profile
    assert "`semester-config/instructors.yml`" in profile
    layout = [c[1] for c in fake.commits if c[3] == migrate.LAYOUT_COMMIT]
    assert layout == [CONFIG_REPO, ".github", JOIN_REPO]

    commits = list(fake.commits)
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert fake.commits == commits


def test_a_course_s_seeded_text_takes_the_new_names(fake, course, monkeypatch, capsys):
    tree = fake.tree(COURSE, ".github")
    tree["dsl-course.yml"] = (
        b"course_name: X\n"
        b"# This is the persistent COURSE org - it spans many cohorts (years). Cohorts are\n"
        b"# registered separately in .github/cohort-courses-pages.yml.\n"
        b"# Our cohorts are small.\n"
    )
    solution = fake.tree(COURSE, "assignment-1-f2026", "solution")
    solution["grading_config.yml"] = (
        b"# INSTRUCTOR-OWNED - defines the assignment. Dates live in the cohort's "
        b"schedule.yml.\nformat: ipynb\n"
    )
    assert _main(monkeypatch, COURSE) == 0
    out = capsys.readouterr().out
    assert "seeded wording rewritten in .github/dsl-course.yml" in out
    assert "rewritten in assignment-1-f2026@solution/grading_config.yml" in out
    assert "-   .github/dsl-course.yml: line(s) 4" in out
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    meta = fake.tree(COURSE, ".github")["dsl-course.yml"].decode()
    assert "it spans many semesters (years). Semesters are" in meta
    assert "# registered separately in .github/semesters.yml." in meta
    assert "# Our cohorts are small." in meta
    grading = fake.tree(COURSE, "assignment-1-f2026", "solution")["grading_config.yml"]
    assert grading.decode().splitlines()[0] == scaffold._GRADING_STAMP
    assert (
        COURSE,
        "assignment-1-f2026",
        "solution",
        migrate.TEXT_COMMIT,
    ) in fake.commits


def test_every_new_seeded_line_is_the_current_templates():
    # The new side of the table is what Bootstrap semester / New assignment / a new
    # course seed today: a template reworded later fails here, not in a migrated org.
    current = set(
        "\n".join(
            [
                migrate.semester_scaffold(SEM, "schedule.yml", "main"),
                migrate.semester_scaffold(SEM, INSTRUCTORS_FILE, "main"),
                template("course/dsl-course.yml"),
                template("course/people-cards.yml"),
                scaffold._GRADING_STAMP,
                *(
                    scaffold._grading_config(
                        title="t",
                        kind="individual",
                        submit_via=shape,
                        formats=["ipynb"],
                        autograde=True,
                    )
                    for shape in SUBMIT_VIA
                ),
            ]
        ).split("\n")
    )
    missing = [
        new for new in migrate.seeded_wording("main").values() if new not in current
    ]
    assert missing == []
    assert [new for _, new in migrate.seeded_blocks()] == [
        template("course/people-header.yml")
    ] * 2


def test_the_renames_are_mechanical_and_leave_other_words():
    org = "Sem-f2026"
    text = (
        f"https://github.com/{org}/welcome/issues and "
        f"[`welcome`](https://github.com/{org}/welcome/issues/new/choose) and "
        f"https://github.com/{org}/classroom-config/blob/main/people.yml and "
        f"https://github.com/{org}/welcome-x and "
        "`classroom-config/people.yml` and classroom-config-2 and "
        "in classroom-config/people.yml. and semester-config/people.yml and "
        "classroom-config/people.yml.sample and "
        "https://github.com/hertie-dl-f2025/welcome/issues and "
        "https://github.com/some-other-org/welcome and "
        "https://github.com/some-other-org/classroom-config and "
        "old-classroom-config and old-cohort-courses-pages.yml and "
        "the `welcome` lecture and .github/cohort-courses-pages.yml"
    )
    want = (
        f"https://github.com/{org}/join/issues and "
        f"[`join`](https://github.com/{org}/join/issues/new/choose) and "
        f"https://github.com/{org}/semester-config/blob/main/instructors.yml and "
        f"https://github.com/{org}/welcome-x and "
        "`semester-config/instructors.yml` and classroom-config-2 and "
        # A full stop ends the sentence, not the file name; the path an earlier pass
        # half-renamed is renamed whole.
        "in semester-config/instructors.yml. and semester-config/instructors.yml and "
        "semester-config/people.yml.sample and "
        # Another org's links resolve where they are (an archived org is never
        # migrated); a name inside another, or a plain word, is not a repo name.
        "https://github.com/hertie-dl-f2025/welcome/issues and "
        "https://github.com/some-other-org/welcome and "
        "https://github.com/some-other-org/classroom-config and "
        "old-classroom-config and old-cohort-courses-pages.yml and "
        "the `welcome` lecture and .github/cohort-courses-pages.yml"
    )
    assert migrate.renamed(text, org) == want
    assert migrate.renamed(want, org) == want
    # The registry's old name, only where the course's own dsl-course.yml says it.
    registry = migrate.renamed(text, org, registry=True)
    assert registry.endswith("old-cohort-courses-pages.yml and the `welcome` lecture "
                             "and .github/semesters.yml")  # fmt: skip


def test_a_filled_in_instructors_yml_keeps_no_seeded_cohort_line():
    # The seeded example block stays in a filled-in file; its photo line is the
    # toolkit's wording, not the instructor's.
    old = (
        '#       photo: "/_images/pp/jane.jpg" # optional. Either (1) a relative path to '
        "an image committed under `_images/pp/` in this cohort's site repo,\n"
    )
    assert migrate.review_lines(migrate.seeded_yaml(old, "main", SEM)) == []


def test_the_re_render_is_planned_to_run_while_work_is_left_above(
    fake, course, monkeypatch, capsys
):
    # Every file already current, one step above with work: the re-render still runs,
    # because the pause record will exist by the time it is reached.
    tree = fake.tree(COURSE, ".github")
    del tree["cohort-courses-pages.yml"]
    tree["semesters.yml"] = b"semesters:\n- Sem-f2026\n"
    _render_course(fake, COURSE)
    assert _main(monkeypatch, COURSE) == 0
    out = capsys.readouterr().out
    assert "re-render: runs after the steps above" in out
    assert "re-render: already migrated" not in out


def test_a_replaced_seeded_line_keeps_its_line_ending():
    old = "# This cohort's own instructors/TAs\r\nplain: 1\r\n"
    new = migrate.seeded_yaml(old, "main", SEM)
    assert new.startswith("# This semester's own instructors:")
    assert new.split("\n")[0].endswith("one list.\r")


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
                b"org: C\norg_name: C\ncourse_name: X\n"
                b"cohort_defaults:\n  timezone: Europe/Berlin\n"
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
        {
            "MAINTAINING.md": b"guide",
            "SYLLABUS.md.sample": b"s",
            "SYLLABUS.md": b"x",
            migrate.RELEASE_WORKFLOWS[0]: b"old",
        },
    )
    _course_renders(fake, monkeypatch)
    calls: list[str] = []
    # "refresh" is the course-only re-render; a refresh that would go on into the
    # semesters (not yet migrated) records itself differently, and fails every assert.
    monkeypatch.setattr(
        migrate.seed,
        "refresh",
        lambda org, *, course_only=False: (
            calls.append("refresh" if course_only else "refresh with semesters")
            or _render_course(fake, org)
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
    assert f"-   .github/.last-refresh -> {records.path('heartbeat')}" in out
    assert "-   MAINTAINING.md -> .system/MAINTAINING.md" in out
    assert "course-materials-f2026: add the topic dsl-materials" in out
    assert "(the files are listed once the registry step has run)" in out
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
    assert yaml.safe_load(meta)["course_name"] == "X"
    for key in ("org", "org_name", "cohort_defaults", "semester_defaults"):
        assert key not in yaml.safe_load(meta)
    assert "  formats: [ipynb]  # the runnable one" in meta
    grading = fake.tree(COURSE, "assignment-1-f2026", "solution")["grading_config.yml"]
    assert grading == b"formats: [ipynb]\nautograde: true\n"
    materials = fake.tree(COURSE, "course-materials-f2026")
    assert set(materials) == {
        ".system/MAINTAINING.md",
        ".system/SYLLABUS.md.sample",
        "SYLLABUS.md",
        *migrate.RELEASE_WORKFLOWS,
    }
    assert fake._repo(COURSE, "course-materials-f2026")["topics"] == ["dsl-materials"]
    template = fake.tree(COURSE, "assignment-1-f2026")
    assert set(migrate.TEMPLATE_WORKFLOWS) <= set(template)
    assert all(fake.paused_at_commit[1:-1]) and fake.enabled(COURSE, ".github")
    assert course == ["refresh", "status"]
    assert fake.dispatches == [
        (
            f"{COURSE}/.github",
            {"event_type": "scheduled-release", "client_payload[driver]": "migrate"},
        ),
        (
            f"{COURSE}/.github",
            {
                "event_type": "sync-membership",
                "client_payload[all_semesters]": True,
                "client_payload[driver]": "migrate",
            },
        ),
    ]

    commits, puts = list(fake.commits), list(fake.puts)
    course.clear()
    capsys.readouterr()
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert capsys.readouterr().out.count("already migrated") == 22
    assert course == [] and fake.commits == commits and fake.puts == puts


def test_a_course_with_no_workflow_repo_passes_the_pause(
    fake, course, monkeypatch, capsys
):
    for repo in (".github", "assignment-1-f2026", "course-materials-f2026"):
        tree = fake.tree(COURSE, repo)
        for path in [p for p in tree if p.startswith(".github/workflows/")]:
            del tree[path]
    assert fake.workflow_repos() == []  # genuinely nothing to pause
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert migrate.PAUSE_RECORD not in fake.tree(COURSE, ".github")
    assert [c[3] for c in fake.commits].count(migrate.PAUSE_COMMIT) == 2
    assert course == ["refresh", "status"]


def test_an_unmigrated_course_is_planned_with_the_real_registry_reader(
    fake, course, monkeypatch, capsys
):
    # The registry read the engine does (NOT_MIGRATED on the old file), over the fake:
    # nothing that needs the new registry may run before the registry step.
    monkeypatch.setattr(discovery, "get_file_content", fake.get_file_content)
    monkeypatch.setattr(migrate, "discover_semesters", discovery.discover_semesters)
    monkeypatch.setattr(
        migrate.seed,
        "github_workflow_files",
        lambda org, ref: discovery.discover_semesters(org) and COURSE_WORKFLOWS,
    )
    assert _main(monkeypatch, COURSE) == 0
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    assert "NOT_MIGRATED" not in capsys.readouterr().err


def test_a_materials_clash_in_one_repo_writes_no_other(
    fake, course, monkeypatch, capsys
):
    fake.add(
        COURSE,
        "course-materials-g2026",
        {"MAINTAINING.md": b"one", ".system/MAINTAINING.md": b"two"},
    )
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "materials files did not verify - stopped here" in err
    assert "course-materials-g2026: 1 move(s) onto a file that differs" in err
    assert "MAINTAINING.md" in fake.tree(COURSE, "course-materials-f2026")
    assert not [c for c in fake.commits if c[1].startswith("course-materials-")]


def test_the_course_re_render_is_checked_in_every_repo_it_writes(
    fake, course, monkeypatch, capsys
):
    # A refresh that re-renders `.github` only: the content repo and the template still
    # differ, and a retired workflow is still in the template - the verify names each.
    fake.tree(COURSE, "assignment-1-f2026")[migrate.RETIRED_WORKFLOWS[0]] = b"old"
    monkeypatch.setattr(
        migrate.seed,
        "refresh",
        lambda org, **k: _render(fake, org, ".github", COURSE_WORKFLOWS),
    )
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "re-render did not verify" in err
    assert f"course-materials-f2026/{migrate.RELEASE_WORKFLOWS[0]}" in err
    assert "course-materials-f2026/.system/MAINTAINING.md" in err
    assert f"assignment-1-f2026/{migrate.TEMPLATE_WORKFLOWS[0]}" in err
    assert (
        f"assignment-1-f2026/{migrate.RETIRED_WORKFLOWS[0]} (retired: deleted)" in err
    )


def test_a_renamed_org_workflow_is_planned_as_a_deletion_and_verified_gone(
    fake, course, monkeypatch, capsys
):
    old = ".github/workflows/archive-cohort.yml"
    fake.tree(COURSE, ".github")[old] = b"old button"
    assert _main(monkeypatch, COURSE) == 0
    capsys.readouterr()
    migrate._forget()
    # Once the registry is migrated the re-render's plan names it; a refresh that leaves
    # it behind does not verify.
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f".github/{old} (retired: deleted)" in err
    assert "re-render did not verify" in err


def test_a_course_stopped_at_the_re_render_finishes_on_the_next_run(
    fake, course, monkeypatch, capsys
):
    # The state the rehearsal left: the pause recorded, the six steps up to the materials
    # done, the re-render failed. The next run skips the five work steps, re-renders the
    # course alone, writes the status and restores Actions from the pause record.
    rendered = migrate.seed.refresh
    monkeypatch.setattr(migrate.seed, "refresh", lambda org, **k: 1)
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    assert "re-render did not verify - stopped here" in capsys.readouterr().err
    assert migrate.PAUSE_RECORD in fake.tree(COURSE, ".github")
    assert not fake.enabled(COURSE, ".github")
    assert course == []

    monkeypatch.setattr(migrate.seed, "refresh", rendered)
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    out = capsys.readouterr().out
    for step in (
        "registry",
        ".system/ in .github",
        "dsl-course.yml keys",
        "template keys",
        "seeded text",
        "materials files",
    ):
        assert f"[skip] {step}: already migrated" in out
    assert course == ["refresh", "status"]
    assert migrate.PAUSE_RECORD not in fake.tree(COURSE, ".github")
    assert all(fake.enabled(*key) for key in fake.workflow_repos())


def test_a_course_re_render_that_failed_off_the_files_is_run_again(
    fake, course, monkeypatch, capsys
):
    # The rehearsal: every file written, but the refresh failed at something no file
    # shows (the repo secrets). The rerun must not read the re-render as done.
    rendered = migrate.seed.refresh
    monkeypatch.setattr(
        migrate.seed, "refresh", lambda org, **k: rendered(org, **k) or 1
    )
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    assert "re-render did not verify - stopped here" in capsys.readouterr().err
    assert course == ["refresh"]

    monkeypatch.setattr(migrate.seed, "refresh", rendered)
    assert _main(monkeypatch, COURSE) == 0
    assert migrate.RERUN_NOTE in capsys.readouterr().out
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    out = capsys.readouterr().out
    assert "[skip] re-render" not in out
    assert course == ["refresh", "refresh", "status"]
    assert migrate.PAUSE_RECORD not in fake.tree(COURSE, ".github")


@pytest.mark.parametrize(
    "block",
    [
        "cohort_defaults:\n  timezone: America/New_York\n",
        "semester_defaults:\n  archive:\n    auto: true\n    grace_days: 30\n",
    ],
    ids=["timezone", "grace-days"],
)
def test_a_semester_default_that_differs_from_the_policy_stops_before_any_write(
    fake, course, monkeypatch, capsys, block
):
    tree = fake.tree(COURSE, ".github")
    tree["dsl-course.yml"] = f"course_name: X\n{block}".encode()
    before = _state(fake)
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "differs from the policy - carry it by hand" in err
    assert "semester-config/schedule.yml" in err
    assert _state(fake) == before and fake.commits == [] and fake.puts == []


def test_a_semester_default_equal_to_the_policy_is_stripped(fake, course, monkeypatch):
    zone = migrate.policy.defaults()["timezone"]
    grace = migrate.policy.defaults()["archive"]["grace_days"]
    fake.tree(COURSE, ".github")["dsl-course.yml"] = (
        f"course_name: X\nsemester_defaults:\n  timezone: {zone}\n"
        f"  archive:\n    auto: false\n    grace_days: {grace}\n"
    ).encode()
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    meta = yaml.safe_load(fake.tree(COURSE, ".github")["dsl-course.yml"])
    assert meta == {"course_name": "X"}


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


def test_the_unpause_restores_each_repos_own_earlier_setting(
    fake, semester, monkeypatch
):
    fake.actions[(SEM, "sem-f2026.github.io")] = {
        "enabled": False,
        "allowed_actions": None,
    }
    fake.actions[(COURSE, ".github")] = {"enabled": True, "allowed_actions": "selected"}
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert not fake.enabled(SEM, "sem-f2026.github.io")  # off before, off after
    assert fake.setting(COURSE, ".github") == {
        "enabled": True,
        "allowed_actions": "selected",
    }
    # Recorded under its old name, restored under the new one.
    assert fake.enabled(SEM, CONFIG_REPO)
    assert migrate.PAUSE_RECORD not in fake.tree(SEM, ".github")


def test_a_ctrl_c_after_the_pause_names_the_paused_repos(
    fake, semester, monkeypatch, capsys
):
    def interrupted(*a, **k):
        # GitHub unreadable by now too: the names must come from what the pause recorded.
        monkeypatch.setattr(migrate, "list_org_repos", lambda org: 1 / 0)
        raise KeyboardInterrupt

    monkeypatch.setattr(migrate.Semester, "rename", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _main(monkeypatch, SEM, "--no-preview")
    err = capsys.readouterr().err
    assert f"Actions are still DISABLED in: {SEM}/{OLD_CONFIG_REPO}, " in err
    assert f"{COURSE}/.github" in err and migrate.PAUSE_RECORD in err


def test_a_preview_lists_each_org_once(fake, semester, monkeypatch):
    listed: list[str] = []
    real = migrate.list_org_repos
    monkeypatch.setattr(
        migrate, "list_org_repos", lambda org: listed.append(org) or real(org)
    )
    assert _main(monkeypatch, SEM) == 0
    assert sorted(listed) == [COURSE, SEM]


def test_a_junk_central_ref_is_reported_not_raised(fake, course, monkeypatch, capsys):
    # A course org's preflight reads no ref: the first read of it is main's own.
    def junk(org):
        raise MissingCentralRef("central_ref `nope` is not a ref of the toolkit")

    monkeypatch.setattr(migrate, "central_ref_for", junk)
    assert migrate.preflight(COURSE) is not None
    assert _main(monkeypatch, COURSE) == 1
    assert "central_ref `nope` is not a ref" in capsys.readouterr().err


def test_the_pointer_is_planned_only_when_it_will_be_written(
    fake, semester, monkeypatch, capsys
):
    fake.tree(SEM, OLD_CONFIG_REPO)[records.path("pointer")] = b"course: C\n"
    assert _main(monkeypatch, SEM) == 0
    assert "write the course pointer" not in capsys.readouterr().out


def test_a_type_line_inside_a_block_scalar_is_prose_and_stays():
    text = (
        "releases:\n"
        "  s1:\n"
        "    type: lecture\n"
        "    details: >-\n"
        "      Bring a laptop.\n"
        "      type: whatever you like\n"
        "\n"
        "      cohort_dest_repo: said in prose\n"
        "    event_datetime: 2026-10-01T09:00\n"
        "events:\n"
        "  - notes: |  # a list entry\n"
        "      type: exam\n"
        "    type: exam\n"
    )
    assert migrate.schedule_keys(text) == (
        "releases:\n"
        "  s1:\n"
        "    kind: lecture\n"
        "    details: >-\n"
        "      Bring a laptop.\n"
        "      type: whatever you like\n"
        "\n"
        "      cohort_dest_repo: said in prose\n"
        "    event_datetime: 2026-10-01T09:00\n"
        "events:\n"
        "  - notes: |  # a list entry\n"
        "      type: exam\n"
        "    kind: exam\n"
    )


def test_a_checkout_that_is_not_the_pinned_ref_is_named_by_file(
    fake, semester, monkeypatch, capsys
):
    monkeypatch.setattr(
        migrate, "git", lambda *a, **k: (0, "dsl_course/grades.py\ntemplates/x.yml\n")
    )
    assert _main(monkeypatch, SEM) == 0
    err = capsys.readouterr().err
    assert "this checkout differs from main" in err
    assert "2 file(s): dsl_course/grades.py, templates/x.yml" in err


def test_the_course_config_rewrite_strips_the_retired_keys_and_keeps_the_rest():
    text = (
        "# INSTRUCTOR-OWNED\n"
        "org: C\n"
        "org_name: The course  # shown nowhere\n"
        "course_name: X\n"
        "semester_defaults:\n"
        "  timezone: Europe/Berlin\n"
        "  # a comment in the block\n"
        "  archive:\n"
        "    auto: true\n"
        "\n"
        "# the admins\n"
        "assignment_defaults:\n"
        "  format: py\n"
        "  max_team_size: 4\n"
    )
    got = migrate.course_config_keys(text)
    assert got == (
        "# INSTRUCTOR-OWNED\n"
        "course_name: X\n"
        "\n"
        "# the admins\n"
        "assignment_defaults:\n"
        "  formats: [py]\n"
        "  max_team_size: 4\n"
    )
    assert migrate.course_config_keys(got) == got
    assert migrate.retired_course_faults(yaml.safe_load(got)) == []


# ------------------------------------ assignments.yml: the run settings move (WP-B2)

B1_SCHEDULE = """timezone: Europe/Berlin
assignments:
  a1:
    title: Regression
    details: >-
      title: this is prose and stays
    course_source_repo: assignment-1-f2026
    due_datetime: 2026-10-13
    grading_datetime: 2026-10-15
  a2:
    course_source_repo: assignment-2-f2026
    semester_dest_repo: homework-2
    due_datetime: 2026-10-20T23:59
    grading_datetime: 2026-10-20T23:59
releases:
  s1:
    event_datetime: 2026-10-01T09:00
    assignment: assignment-1-f2026
enrolment:
  send_codes_datetime: 2026-08-24T08:00
  show_on_site: true
events:
  exam:
    kind: exam
    event_datetime: 2026-12-01T09:00
"""
A1_RUN_KEYS = {"max_team_size": 3, "late_window_days": 10, "late_penalty_per_day": "5%"}
INSTITUTION = [
    ("semester", {}),
    ("course", {}),
    ("institution", migrate.settings.institution_defaults()),
]


def test_the_schedule_loses_the_keys_that_left_it_and_nothing_else():
    new = migrate.schedule_timing(B1_SCHEDULE)
    meta = yaml.safe_load(new)
    assert set(meta["assignments"]["a1"]) == {
        "details",
        "course_source_repo",
        "due_datetime",
    }
    assert meta["assignments"]["a1"]["details"] == "title: this is prose and stays"
    assert "semester_dest_repo" not in meta["assignments"]["a2"]
    assert set(meta["releases"]["s1"]) == {"event_datetime"}
    assert "enrolment" not in meta and meta["events"]["exam"]["kind"] == "exam"
    assert migrate.schedule_timing(new) == new


def test_the_old_files_say_what_assignments_yml_must_hold():
    meta = yaml.safe_load(B1_SCHEDULE)
    want, notes = migrate.instance_additions(
        meta,
        {"assignment-1-f2026": A1_RUN_KEYS},
        INSTITUTION,
        migrate.settings.Instance(),
    )
    # a1: the template's team cap and penalty (the institution's differ); its window is
    # what the old grading_datetime said - two days after the due date.
    assert want["a1"] == {
        "max_team_size": 3,
        "late_window_days": 2,
        "late_penalty_per_day": "5%",
    }
    # a2: grading_datetime WAS the due date: no late work; and its repo name moved.
    assert want["a2"] == {"late_window_days": 0, "late_penalty_per_day": "10%",
                          "semester_dest_repo": "homework-2"}  # fmt: skip
    assert notes == []


def test_a_setting_assignments_yml_already_states_is_left_as_written():
    meta = yaml.safe_load(B1_SCHEDULE)
    existing = migrate.settings.parse_instance(
        "assignments:\n  a1:\n    late_window_days: 7\n    max_team_size: 4\n"
    )
    want, _ = migrate.instance_additions(
        meta, {"assignment-1-f2026": A1_RUN_KEYS}, INSTITUTION, existing
    )
    assert "a1" not in want


def test_a_cutoff_that_is_not_whole_days_after_the_due_date_is_named():
    meta = yaml.safe_load(
        "assignments:\n  a1:\n    course_source_repo: t\n"
        "    due_datetime: 2026-10-13T12:00\n    grading_datetime: 2026-10-15T20:00\n"
    )
    want, notes = migrate.instance_additions(
        meta, {}, INSTITUTION, migrate.settings.Instance()
    )
    assert want["a1"]["late_window_days"] == 2
    (note,) = notes
    assert "2026-10-15 20:00" in note and "2026-10-15 12:00" in note


def test_the_run_keys_are_written_into_the_skeleton_and_read_back():
    skeleton = migrate.semester_scaffold(SEM, "assignments.yml", "main")
    text = migrate.with_instance_keys(
        skeleton,
        {"a1": {"late_window_days": 2, "late_penalty_per_day": "5%"},
         "a2": {"semester_dest_repo": "homework-2"}},
    )  # fmt: skip
    got = migrate.settings.parse_instance(text)
    assert got.faults == ()
    assert got.blocks["a1"] == {"late_window_days": 2, "late_penalty_per_day": "5%"}
    assert got.dests == {"a2": "homework-2"}
    assert text.startswith(skeleton.rstrip("\n"))  # the skeleton's comments kept
    # Into a file with a block of its own: the slug's block gains the key.
    again = migrate.with_instance_keys(text, {"a1": {"max_team_size": 3}})
    assert migrate.settings.parse_instance(again).blocks["a1"]["max_team_size"] == 3


def test_a_template_loses_its_run_settings_live_or_commented():
    old = (
        "title: T\n"
        "team_formation: self_select   # group only\n"
        "submit_url: https://moodle.example.org/x\n"
        "                              # external only\n"
        "# visibility: private         # institution default\n"
        "formats: [py]\n"
        "\n"
        "late_window_days: 10\n"
        "late_penalty_per_day: 10%\n"
        "\n"
        "autograde: false\n"
    )
    new = migrate.strip_run_keys(old)
    assert new == "title: T\nformats: [py]\n\nautograde: false\n"
    assert migrate.template_run_keys(old) == {
        "team_formation": "self_select",
        "submit_url": "https://moodle.example.org/x",
        "late_window_days": 10,
        "late_penalty_per_day": "10%",
    }
    assert migrate._grading_clean(new) and not migrate._grading_clean(old)


def test_the_course_records_each_templates_run_settings_before_stripping_them(
    fake, course, monkeypatch
):
    fake.tree(COURSE, "assignment-1-f2026", "solution")["grading_config.yml"] = (
        b"formats: [ipynb]\nmax_team_size: 3\nlate_window_days: 4\n"
    )
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    grading = fake.tree(COURSE, "assignment-1-f2026", "solution")["grading_config.yml"]
    assert grading == b"formats: [ipynb]\n"
    record = json.loads(fake.tree(COURSE, ".github")[migrate.RUN_KEYS_RECORD])
    assert record == {"assignment-1-f2026": {"max_team_size": 3, "late_window_days": 4}}


def test_a_semester_migrated_before_b2_completes_on_the_next_run(
    fake, semester, monkeypatch, capsys
):
    # The first run, by the tool as it was: everything but assignments.yml.
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    tree = fake.tree(SEM, CONFIG_REPO)
    tree["schedule.yml"] = B1_SCHEDULE.encode()
    del tree["assignments.yml"]
    # The course step has already taken a1's template's run settings out, into its record.
    fake.tree(COURSE, ".github")[migrate.RUN_KEYS_RECORD] = json.dumps(
        {"assignment-1-f2026": A1_RUN_KEYS}
    ).encode()
    fake.commits.clear()
    capsys.readouterr()

    assert _main(monkeypatch, SEM) == 0  # the preview shows the diff
    out = capsys.readouterr().out
    assert "  keys:\n" in out and "-    grading_datetime: 2026-10-15" in out
    assert "seed the skeleton, and write what the old files said" in out
    assert "+    late_window_days: 2" in out and "+# INSTRUCTOR-OWNED" not in out
    assert fake.commits == []

    assert _main(monkeypatch, SEM, "--no-preview") == 0
    tree = fake.tree(SEM, CONFIG_REPO)
    got = migrate.settings.parse_instance(tree["assignments.yml"].decode())
    assert got.blocks["a1"] == {
        "max_team_size": 3,
        "late_window_days": 2,
        "late_penalty_per_day": "5%",
    }
    assert got.dests == {"a2": "homework-2"}
    assert migrate._schedule_clean(tree["schedule.yml"].decode(),
                                   tree["assignments.yml"].decode())  # fmt: skip
    keys = [c for c in fake.commits if c[3] == migrate.KEYS_COMMIT]
    assert keys == [(SEM, CONFIG_REPO, "main", migrate.KEYS_COMMIT)]  # both files, once
    commits = list(fake.commits)
    capsys.readouterr()
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert "  keys: already migrated" in capsys.readouterr().out
    assert fake.commits == commits


# ---------------------------------------------------------------- the pause's order


def _first(calls: list[str], prefix: str) -> int:
    return next(i for i, c in enumerate(calls) if c.startswith(prefix))


def _last(calls: list[str], prefix: str) -> int:
    return max(i for i, c in enumerate(calls) if c.startswith(prefix))


def _finishes_after(fake, key, sleeps: int) -> None:
    """A run in `key` that is going now and has finished once `sleeps` sleeps are over."""
    fake.runs[key] = [("2026-09-25T12:00:00Z", "in_progress")]
    left = [sleeps]

    def tick(_at):
        left[0] -= 1
        if left[0] == 0:
            fake.runs[key] = [("2026-09-25T12:00:00Z", "completed")]

    fake.clock.hooks.append(tick)


def test_the_pause_waits_for_quiet_then_records_then_switches_off_then_verifies(
    fake, semester, monkeypatch, capsys
):
    # The rehearsal's 12:15 autograde: a run going when the pause is due is waited for,
    # never switched off under it.
    _finishes_after(fake, (COURSE, ".github"), sleeps=2)
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    calls = fake.calls
    record, off = _first(calls, "record "), _first(calls, "off ")
    assert calls[:record].count(f"sleep {migrate.QUIET_POLL}") == 2
    assert f"runs {COURSE}/.github" in calls[:record]
    assert record < off
    # Every target switched off before the verify looks for runs again.
    last_off = _last(calls, "off ")
    assert f"runs {COURSE}/.github" in calls[last_off : _first(calls, "on ")]
    assert "waiting for the run(s) in" in capsys.readouterr().out


def test_a_run_still_going_after_the_wait_stops_before_anything_is_written(
    fake, semester, monkeypatch, capsys
):
    fake.runs[(SEM, OLD_JOIN_REPO)] = [("2026-09-25T12:00:00Z", "in_progress")]
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f"queued or running in {SEM}/{OLD_JOIN_REPO} (waited 4 minutes)" in err
    assert fake.commits == [] and fake.puts == []
    assert fake.clock.at - 1_790_337_780.0 >= migrate.QUIET_WAIT


def test_a_pause_due_just_before_a_tick_waits_past_it(
    fake, semester, monkeypatch, capsys
):
    fake.clock.at = 1_790_337_780.0 + 12 * 60 - 30  # 30 s before the quarter hour
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    calls = fake.calls
    assert calls[0] == f"sleep {30 + migrate.QUIET_POLL}"
    assert _first(calls, "sleep") < _first(calls, "record ")
    assert (
        "a scheduled tick is due in 30 s - waiting past it" in capsys.readouterr().out
    )
    assert migrate.next_tick(1_790_337_780.0 + 12 * 60) == 0


def test_a_run_that_slips_in_during_the_switch_is_waited_for(
    fake, semester, monkeypatch, capsys
):
    fake.run_after_pause = (COURSE, ".github")

    def finish(_at):
        fake.runs[(COURSE, ".github")] = [("9999-12-31T00:00:00Z", "completed")]

    fake.clock.hooks.append(finish)
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert _first(fake.calls, "off ") < _first(fake.calls, "sleep")


def test_a_preview_never_waits(fake, semester, monkeypatch, capsys):
    fake.runs[(SEM, OLD_JOIN_REPO)] = [("2026-09-25T12:00:00Z", "in_progress")]
    fake.central_runs["deploy-preview.yml"] = ["in_progress"]
    assert _main(monkeypatch, SEM) == 0
    assert not [c for c in fake.calls if c.startswith(("runs ", "sleep"))]
    assert "PREVIEW - nothing was written" in capsys.readouterr().out


def test_a_course_stopped_mid_run_stays_paused(fake, course, monkeypatch, capsys):
    # Only a SEMESTER's stop puts the course back: a course stopped mid-migration is the
    # thing its own pause protects.
    monkeypatch.setattr(migrate, "move_files", _fail_on(fake, migrate.LAYOUT_COMMIT))
    assert _main(monkeypatch, COURSE, "--no-preview") == 1
    err = capsys.readouterr().err
    assert "back on" not in err
    assert not fake.enabled(COURSE, ".github")


def _fail_on(fake, message):
    def move(org, repo, moves, msg, **kw):
        return False if msg == message else fake.move_files(org, repo, moves, msg, **kw)

    return move


# ---------------------------------------------------------------- hold mode


def _hold_stubs(fake, monkeypatch):
    """The course re-render and both status writes, for a course and a semester migrated
    one after the other in one test."""
    monkeypatch.setattr(
        migrate.seed,
        "refresh",
        lambda org, *, course_only=False: _render_course(fake, org),
    )

    def status(course, sem=None):
        doc = {"semester": {}} if sem else {"course": {"semesters": []}}
        where = (sem, CONFIG_REPO) if sem else (course, ".github")
        blob = json.dumps({**doc, "problems": []}).encode()
        return _render(fake, *where, {records.path("status"): blob})

    monkeypatch.setattr(migrate.status, "refresh", status)


def _hold_record(fake, org):
    return json.loads(fake.tree(org, ".github")[migrate.PAUSE_RECORD])


def test_hold_pauses_every_live_org_then_migrations_never_unpause_then_release(
    fake, semester, monkeypatch, capsys
):
    _hold_stubs(fake, monkeypatch)
    # An archived semester in the registry is never held (nothing is written into it).
    fake.tree(COURSE, ".github")["semesters.yml"] = b"semesters:\n- Sem-f2026\n- Old\n"
    fake.add("Old", ".github", WORKFLOW, topics=[OLD_SEMESTER_TOPIC])
    fake.add("Old", OLD_CONFIG_REPO, WORKFLOW, archived=True)

    assert _main(monkeypatch, "--hold", COURSE) == 0  # a preview
    assert fake.commits == [] and fake.puts == []
    capsys.readouterr()

    assert _main(monkeypatch, "--hold", COURSE, "--no-preview") == 0
    calls = fake.calls
    # Quiet across every org first, then every record, then any switch.
    assert _last(calls, "record ") < _first(calls, "off ")
    assert calls.index(f"record {COURSE}") < calls.index(f"record {SEM}")
    assert _first(calls, "runs ") < _first(calls, "record ")
    assert _hold_record(fake, COURSE)["hold"] is True
    sem_record = _hold_record(fake, SEM)
    assert sem_record == {
        "hold": True,
        "migrated": False,
        "repos": {
            f"{SEM}/{OLD_CONFIG_REPO}": {"enabled": True, "allowed_actions": "all"},
            f"{SEM}/{OLD_JOIN_REPO}": {"enabled": True, "allowed_actions": "all"},
            f"{SEM}/sem-f2026.github.io": {"enabled": True, "allowed_actions": "all"},
        },
    }
    assert ("Old", ".github") not in fake.actions
    assert not any(fake.enabled(*k) for k in fake.workflow_repos() if k[0] != "Old")

    # The course, then the semester: each skips the pause and never unpauses.
    capsys.readouterr()
    assert _main(monkeypatch, COURSE) == 0
    assert "on hold: nothing is unpaused" in capsys.readouterr().out
    for org in (COURSE, SEM):
        fake.calls.clear()
        assert _main(monkeypatch, org, "--no-preview") == 0, capsys.readouterr().err
        assert not [c for c in fake.calls if c.startswith(("on ", "off "))]
        assert _hold_record(fake, org)["migrated"] is True
        out = capsys.readouterr().out
        assert f"{org} is migrated - on hold: Actions stay off" in out
    assert fake.dispatches == []
    assert CONFIG_REPO in {n for (o, n) in fake.repos if o == SEM}
    assert not any(fake.enabled(*k) for k in fake.workflow_repos() if k[0] != "Old")

    # A second run under the hold finds nothing to do.
    commits = list(fake.commits)
    assert _main(monkeypatch, SEM, "--no-preview") == 0
    assert fake.commits == commits

    assert _main(monkeypatch, "--status", COURSE) == 0
    out = capsys.readouterr().out
    assert f"{COURSE}: on hold, migrated - Actions off in 2 of 2" in out
    assert f"{SEM}: on hold, migrated - Actions off in 3 of 3" in out

    assert _main(monkeypatch, "--release", COURSE, "--no-preview") == 0
    assert all(fake.enabled(*k) for k in fake.workflow_repos())
    for org in (COURSE, SEM):
        assert migrate.PAUSE_RECORD not in fake.tree(org, ".github")
    # The course last, so its ticks find every semester back on; one catch-up for all.
    ons = [c for c in fake.calls if c.startswith("on ")]
    assert ons[-1].startswith(f"on {COURSE}/") and ons[0].startswith(f"on {SEM}/")
    assert [d[1]["event_type"] for d in fake.dispatches] == [
        "scheduled-release",
        "sync-membership",
    ]
    assert all(d[1]["client_payload[driver]"] == "migrate" for d in fake.dispatches)
    assert all("client_payload[semester_org]" not in d[1] for d in fake.dispatches)


def test_a_semester_under_a_held_course_must_be_held_too(
    fake, semester, monkeypatch, capsys
):
    _hold_stubs(fake, monkeypatch)
    assert _main(monkeypatch, "--hold", COURSE, "--no-preview") == 0
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    del fake.tree(SEM, ".github")[migrate.PAUSE_RECORD]  # a semester the hold missed
    capsys.readouterr()
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    assert f"{COURSE} is on hold and {SEM} is not" in capsys.readouterr().err


def test_a_stop_under_hold_restores_nothing(fake, semester, monkeypatch, capsys):
    _hold_stubs(fake, monkeypatch)
    assert _main(monkeypatch, "--hold", COURSE, "--no-preview") == 0
    assert _main(monkeypatch, COURSE, "--no-preview") == 0
    fake.fail_rename = True
    fake.calls.clear()
    capsys.readouterr()
    assert _main(monkeypatch, SEM, "--no-preview") == 1
    err = capsys.readouterr().err
    assert f"{SEM} is on hold: Actions stay off in every org of {COURSE}" in err
    assert "back on" not in err
    assert not [c for c in fake.calls if c.startswith("on ")]
    assert _hold_record(fake, SEM)["migrated"] is False


def test_release_names_an_org_not_migrated_inside_the_hold(
    fake, semester, monkeypatch, capsys
):
    assert _main(monkeypatch, "--hold", COURSE, "--no-preview") == 0
    capsys.readouterr()
    assert _main(monkeypatch, "--release", COURSE) == 0  # a preview
    out = capsys.readouterr().out
    assert f"{SEM} (NOT migrated inside the hold)" in out
    assert not [c for c in fake.calls if c.startswith("on ")]


def test_hold_takes_a_course_org_only(fake, semester, monkeypatch, capsys):
    assert _main(monkeypatch, "--hold", SEM, "--no-preview") == 1
    assert "is not a course org" in capsys.readouterr().err
    assert fake.commits == []


@pytest.mark.parametrize("repo", [OLD_CONFIG_REPO, CONFIG_REPO])
@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_the_old_people_header_becomes_the_current_one_as_a_block(repo, eol):
    # As seeded, and as the first rehearsal's rename left it (`semester-config/people.yml`,
    # a path that never existed).
    old = migrate.OLD_PEOPLE_HEADER.replace(
        "classroom-config/people.yml.", f"{repo}/people.yml."
    )
    text = ("course_name: X\n\n" + old + "people:\n  course_admins: []\n").replace(
        "\n", eol
    )
    new = migrate.seeded_yaml(text, "main", COURSE)
    header = template("course/people-header.yml")
    assert new == (
        "course_name: X\n\n" + header + "people:\n  course_admins: []\n"
    ).replace("\n", eol)
    assert migrate.review_lines(new) == []


def test_a_template_s_seeded_lines_take_the_semester_wording():
    for shape in SUBMIT_VIA:
        new = scaffold._grading_config(
            title="t",
            kind="individual",
            submit_via=shape,
            formats=["ipynb"],
            autograde=True,
        )
        old = new.replace("whole semester", "whole cohort")
        assert "whole cohort" in old
        assert migrate.seeded_yaml(old, "main", COURSE) == new
