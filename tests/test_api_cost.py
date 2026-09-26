"""What one engine run costs in GitHub API calls, with a ceiling a regression trips.

The bot account is ONE user with 5,000 calls an hour for every course org, and on
2026-09-26 the new engine spent it in half an hour on the demo alone: a preview tick of a
two-student semester cost 113 calls, 45 of them the same pointer file. So every operation
a workflow or the Console runs is driven here end to end - the real CLI, command line and
all - against a fake GitHub at the `gh` binary, which counts every call and answers from
the demo's own layout fixtures. A change that makes a run dearer fails here, like a
layering violation does.

Each ceiling is the count measured here after the read-once change, plus 25% headroom.
The live numbers for the same runs against the demo orgs are beside each, for scale. The
unit is one `gh` invocation, which is not always one API request: a listing (`--paginate`,
`gh issue list`) is one page per hundred rows, so it costs more as the org grows.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import clear_process_memos

from dsl_course import (
    central,
    discovery,
    gh_contents,
    ghcli,
    issues,
    log,
    migrate,
    repos,
    scheduler,
    settings,
    status,
    sync_membership,
)

from dsl_course.discovery import ASSIGNMENT_TEMPLATE_TOPIC

FIXTURES = Path(__file__).parent / "fixtures" / "layouts"
COURSE = "Course-e1234"
SEMESTER = "Course-f2026"
STUDENTS = ("octo-one", "octo-two")
ASSIGNMENTS = ("assignment-1", "assignment-2")

# A flag of `gh api` that takes a value, so the path is the first word that is neither.
_VALUED = {
    "--jq",
    "--method",
    "-X",
    "-f",
    "-F",
    "--field",
    "--raw-field",
    "--input",
    "-H",
}


def _sha(text: str) -> str:
    return hashlib.sha1(
        f"blob {len(text.encode())}\0".encode() + text.encode()
    ).hexdigest()


def _schedule() -> str:
    """The demo's release plan, with two assignments on the same term."""
    text = (FIXTURES / "demo-schedule.yml").read_text()
    blocks = "".join(
        f"  {slug}:\n"
        f"    course_source_repo: {slug}-f2026\n"
        f"    handout_datetime: 2026-09-{8 + 7 * i:02d}T10:00\n"
        f"    due_datetime: 2026-09-{15 + 7 * i:02d}T23:59\n"
        for i, slug in enumerate(ASSIGNMENTS)
    )
    return f"{text}\nassignments:\n{blocks}"


# What a semester repo's team grants are once every release and sync has converged them.
_SEMESTER_GRANTS = {
    "students": "pull",
    "auditors": "pull",
    "instructors": "push",
    "course-admin": "admin",
}


def _submission_topics(name: str, students: tuple[str, ...]) -> list[str]:
    """A handed-out repo's topics, as the handout stamps them; none for anything else."""
    for slug in ASSIGNMENTS:
        if name.removeprefix(f"{slug}-") in students:
            return [slug, "submission"]
    return ["gradebook"] if name.startswith("grades-") else []


class FakeGitHub:
    """The `gh` binary for one course org and one semester: every call recorded, every
    answer made from the files and trees below, anything else a 404."""

    def __init__(
        self,
        students: tuple[str, ...] = STUDENTS,
        pushed: tuple[str, ...] = (),
        gone: tuple[str, ...] = (),
    ) -> None:
        # `pushed`: students who pushed since the sheets last looked. `gone`: handles off
        # the roster whose submission repos are still in the org.
        self.students, self.pushed = students, pushed
        roster = ",".join(("hertie_email", "name", "role", "github_handle"))
        self.files: dict[tuple[str, str, str], str] = {
            (
                COURSE,
                ".github",
                "dsl-course.yml",
            ): "course_name: Deep Learning\ncourse_code: E1234\n",
            (COURSE, ".github", "semesters.yml"): f"semesters:\n- {SEMESTER}\n",
            (
                SEMESTER,
                "semester-config",
                ".system/dsl-course.yml",
            ): f"course: {COURSE}\n",
            (SEMESTER, "semester-config", "schedule.yml"): _schedule(),
            (SEMESTER, "semester-config", "students.csv"): roster
            + "\n"
            + "".join(f"{h}@example.org,{h},enrolled,{h}\n" for h in students),
            (
                SEMESTER,
                "semester-config",
                "teams.csv",
            ): "assignment,team,github_handle\n",
            (SEMESTER, "semester-config", "instructors.yml"): "instructors: []\n",
        }
        materials = (FIXTURES / "demo-tree.txt").read_text().splitlines()
        self.trees: dict[tuple[str, str], list[str]] = {
            (COURSE, "course-materials-f2026"): materials,
            (COURSE, "lecture-code-f2026"): [
                "dldemo/uncertainty.py",
                "dldemo/audit.py",
                "dldemo/serving.py",
            ],
            **{
                (org, name): ["README.md", "assignment.ipynb"]
                for slug in ASSIGNMENTS
                for org, name in ((COURSE, f"{slug}-f2026"), (SEMESTER, slug))
            },
        }
        self.repos: dict[str, list[str]] = {
            COURSE: [".github", *{repo for _, repo in self.trees}],
            SEMESTER: [
                ".github",
                "semester-config",
                "materials",
                "join",
                f"{SEMESTER.lower()}.github.io",
                *ASSIGNMENTS,
                *(f"grades-{h}" for h in students),
                *(f"{slug}-{h}" for slug in ASSIGNMENTS for h in students + gone),
            ],
        }
        self.calls: list[tuple[str, ...]] = []
        self.not_modified: list[tuple[str, ...]] = []

    def did(self, *prefix: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def run(self, cmd, *args, **kwargs):
        if cmd and cmd[0] == "git":
            # Local git runs for real; nothing reaches a remote.
            if ghcli._git_subcommand(tuple(cmd[1:])) in _GIT_REMOTE_VERBS:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return _real_run(cmd, *args, **kwargs)
        if not cmd or cmd[0] != "gh":
            return _real_run(cmd, *args, **kwargs)
        argv = tuple(map(str, cmd[1:]))
        self.calls.append(argv)
        self.stdin = kwargs.get("input")
        if argv[:2] == ("repo", "clone"):
            return self._clone(*argv[2].split("/"), Path(argv[3]))
        code, out = self.answer(argv)
        err = "" if code == 0 else out
        return SimpleNamespace(
            returncode=code, stdout=out if code == 0 else "", stderr=err
        )

    def _clone(self, org: str, repo: str, dest: Path) -> SimpleNamespace:
        """A working copy of what the fake holds for the repo. A semester's `materials`
        already carries everything its course repo releases, as it does a term in."""
        paths = list(self.trees.get((org, repo), ["README.md"]))
        if (org, repo) == (SEMESTER, "materials"):
            paths += self.trees[(COURSE, "course-materials-f2026")]
        files = {p: p for p in paths}
        files |= {p: t for (o, r, p), t in self.files.items() if (o, r) == (org, repo)}
        dest.mkdir(parents=True, exist_ok=True)
        git = ["git", "-C", str(dest), "-c", "user.email=f@x", "-c", "user.name=f"]
        _real_run(["git", "init", "-q", "-b", "main", str(dest)], check=True)
        for path, text in files.items():
            (dest / path).parent.mkdir(parents=True, exist_ok=True)
            (dest / path).write_text(text)
        url = f"https://github.com/{org}/{repo}.git"
        for step in (["remote", "add", "origin", url], ["add", "-A"]):
            _real_run(git + step, check=True, capture_output=True)
        _real_run(
            git + ["-c", "core.hooksPath=/dev/null", "commit", "-qm", "init"],
            check=True,
            capture_output=True,
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def answer(self, argv: tuple[str, ...]) -> tuple[int, str]:
        method = next(
            (argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ("--method", "-X")),
            "GET",
        )
        if argv[:1] == ("api",) and method != "GET":
            return self._write(argv)
        if argv[:2] == ("issue", "list"):
            return 0, "[]"
        if argv[:1] != ("api",):
            return 0, ""
        words, skip = [], False
        for arg in argv[1:]:
            if skip:
                skip = False
            elif arg in _VALUED:
                skip = True
            elif not arg.startswith("-"):
                words.append(arg)
        path = words[0] if words else ""
        jq = argv[argv.index("--jq") + 1] if "--jq" in argv else ""
        parts = path.split("?")[0].split("/")
        if path == "user":
            return 0, "bot" if jq else '{"login": "bot"}'
        if parts[0] == "orgs" and len(parts) == 2:
            return 0, parts[1] if jq else json.dumps({"login": parts[1]})
        if parts[0] == "orgs" and parts[2] == "repos":
            rows = [
                {
                    "name": name,
                    "description": "",
                    "visibility": "private",
                    "url": f"https://github.com/{parts[1]}/{name}",
                    # A semester template the handout has frozen: flagged and topiced.
                    "isTemplate": ready,
                    "archived": False,
                    # A repo nobody has pushed to since the handout is quiet.
                    "pushed_at": (
                        "2099-01-01T00:00:00Z"  # after any sheet last looked
                        if any(name.endswith(f"-{h}") for h in self.pushed)
                        else "2026-09-01T00:00:00Z"
                    ),
                    "topics": (
                        [repos.topic_name(name), ASSIGNMENT_TEMPLATE_TOPIC]
                        if ready
                        else _submission_topics(name, self.students)
                    ),
                }
                for name in self.repos.get(parts[1], [])
                for ready in [parts[1] == SEMESTER and name in ASSIGNMENTS]
            ]
            return 0, "\n".join(json.dumps(r) for r in rows)
        if path == "graphql":
            org = next(a.partition("=")[2] for a in argv if a.startswith("org="))
            return 0, self._collaborators(org)
        if parts[0] == "orgs":
            return self._org(parts, path, jq)
        if parts[0] != "repos" or len(parts) < 3:
            return _NOT_FOUND
        org, repo, rest = parts[1], parts[2], parts[3:]
        if f"{org}/{repo}" == central.CENTRAL:
            return 0, "identical"  # the org's central ref is on the toolkit's main
        if repo not in self.repos.get(org, []):
            return _NOT_FOUND
        if not rest:
            return 0, json.dumps(
                {
                    "name": repo,
                    "default_branch": "main",
                    "private": True,
                    "archived": False,
                    "allow_forking": True,
                    "size": 1,
                }
            )
        if rest[0] == "contents":
            where = "/".join(rest[1:])
            code, out = self._contents(org, repo, where, jq)
            text = self.files.get((org, repo, where))
            if code == 0 and text is not None and _if_none_match(argv) == _sha(text):
                # GitHub answers a conditional read of an unchanged file with a 304,
                # which it does not count - and gh, as gh 2.83 does: `--jq` over the
                # empty body fails as bad JSON, and only the unfiltered ask says 304.
                self.not_modified.append(argv)
                if jq:
                    return 1, "unexpected end of JSON input"
                return 1, "HTTP/2.0 304 Not Modified\r\nEtag: x\r\n\r\ngh: HTTP 304"
            if code == 0 and "--include" in argv and not jq and text is not None:
                encoded = base64.b64encode(text.encode()).decode()
                body = json.dumps({"sha": _sha(text), "content": encoded})
                return 0, f'HTTP/2.0 200 OK\r\nEtag: "{_sha(text)}"\r\n\r\n{body}'
            return code, out
        if rest[:2] == ["git", "trees"]:
            return self._tree(org, repo, jq)
        if rest[0] == "issues" and "labels=" in path and repo not in self.repos[COURSE]:
            # Every handed-out repo already carries its receipts thread.
            return 0, "1\topen\tbot"
        if rest[0] == "teams":
            # A semester repo carries the grants every release converges.
            return 0, "\n".join(
                json.dumps({"slug": team, "permissions": {perm: True}})
                for team, perm in _SEMESTER_GRANTS.items()
            )
        if rest[0] in ("collaborators", "invitations", "commits", "issues"):
            return 0, "" if jq else "[]"
        if rest[0] == "actions":
            return 0, '{"workflow_runs": []}'
        return _NOT_FOUND

    def _org(self, parts: list[str], path: str, jq: str) -> tuple[int, str]:
        """The steady state of an org a term into its run: every student a member of the
        org and of `students`, every grant a sync converges already there."""
        org, rest = parts[1], parts[2:]
        students = list(self.students) if org == SEMESTER else []
        if rest == ["members"] and "role=admin" not in path:
            return 0, "\n".join(students)
        if rest[:1] == ["memberships"]:
            return (0, "active (member)") if rest[1] in students else _NOT_FOUND
        if rest[:1] == ["teams"] and rest[2:] == ["members"] and rest[1] == "students":
            return 0, json.dumps(
                [{"login": h, "id": i} for i, h in enumerate(students)]
            )
        if rest[:1] == ["teams"] and rest[2:] == ["repos"]:
            # The faculty floor, held: write where faculty author, read on what is a
            # student's, admin for course-admin everywhere.
            def perm(name: str) -> str:
                if rest[1] == "course-admin":
                    return "admin"
                personal = name.startswith("grades-") or _submission_topics(
                    name, self.students
                )
                return "pull" if org == SEMESTER and personal else "push"

            return 0, "\n".join(
                json.dumps({"name": name, "permissions": {perm(name): True}})
                for name in self.repos.get(org, [])
            )
        return 0, "" if jq else "[]"

    def _collaborators(self, org: str) -> str:
        """The GraphQL answer: every repo, and its student as its one direct collaborator."""
        rows = []
        for name in self.repos.get(org, []):
            who = [f"{h}:READ" for h in self.students if name.endswith(f"-{h}")]
            rows.append(f"{name}\t{len(who)}\t{','.join(who)}")
        return "\n".join(rows)

    def _write(self, argv: tuple[str, ...]) -> tuple[int, str]:
        """A write: a repo that is already there refuses its create as GitHub does, and
        every other write succeeds and changes nothing the reads answer from."""
        fields = dict(
            a.split("=", 1) for a in argv if "=" in a and not a.startswith("-")
        )
        path = next((a for a in argv[1:] if a.startswith(("repos/", "orgs/"))), "")
        parts = path.split("?")[0].split("/")
        org = fields.get("owner") if parts[-1] == "generate" else parts[1]
        if parts[0] == "orgs" and parts[2:] == ["teams"]:
            # Every team a sync makes was made on its first run.
            return (
                1,
                "gh: Validation Failed: Name must be unique for this org (HTTP 422)",
            )
        if parts[-1] in ("repos", "generate") and "name" in fields:
            if fields["name"] in self.repos.get(org, []):
                return 1, "gh: name already exists on this account (HTTP 422)"
            self.repos.setdefault(org, []).append(fields["name"])
        if parts[0] == "repos" and parts[3:4] == ["contents"] and self.stdin:
            # A file written is there for every later read, as on GitHub.
            text = base64.b64decode(self.stdin).decode()
            self.files[(parts[1], parts[2], "/".join(parts[4:]))] = text
        return 0, "{}"

    def _contents(self, org: str, repo: str, path: str, jq: str) -> tuple[int, str]:
        text = self.files.get((org, repo, path))
        if text is None:
            here = [p for (o, r, p) in self.files if (o, r) == (org, repo)]
            names = sorted(
                {
                    p[len(path) :].lstrip("/").split("/")[0]
                    for p in here + self.trees.get((org, repo), [])
                    if p.startswith(f"{path}/" if path else "")
                }
            )
            if not names:
                return _NOT_FOUND
            return 0, str(len(names)) if jq == "length" else "\n".join(names)
        encoded = base64.b64encode(text.encode()).decode()
        if jq == ".sha":
            return 0, _sha(text)
        if jq == ".content":
            return 0, encoded
        return 0, f"{_sha(text)}\n{encoded}"

    def _tree(self, org: str, repo: str, jq: str) -> tuple[int, str]:
        files = self.trees.get((org, repo), [])
        dirs = sorted(
            {
                "/".join(p.split("/")[:i])
                for p in files
                for i in range(1, p.count("/") + 1)
            }
        )
        entries = [(p, "blob") for p in files] + [(d, "tree") for d in dirs]
        if 'select(.type=="blob")' in jq:
            entries = [e for e in entries if e[1] == "blob"]
        elif 'select(.type=="tree")' in jq:
            entries = [e for e in entries if e[1] == "tree"]
        if ".mode" in jq:
            lines = [f"{p}\t{_sha(p)}\t100644" for p, _ in entries]
        elif ".sha" in jq:
            lines = [f"{p}\t{_sha(p)}" for p, _ in entries]
        else:
            lines = [p for p, _ in entries]
        return 0, "\n".join(["false", *lines])


def _if_none_match(argv: tuple[str, ...]) -> str:
    """The ETag a conditional read sends, unquoted, or ""."""
    header = next((a for a in argv if a.startswith("If-None-Match:")), "")
    return header.partition(":")[2].strip().strip('"')


_GIT_REMOTE_VERBS = frozenset({"push", "fetch", "pull", "ls-remote", "clone"})
_NOT_FOUND = (1, "gh: Not Found (HTTP 404)")
_real_run = subprocess.run

# The live reads `tests/conftest.py` answers for every other test ("the central ref is
# there", "the semester is running", "nothing declared"), taken before it does: a cost
# test has to pay for them like a real run.
_REAL = [
    (central, "gh", central.gh),
    (discovery, "repo_is_archived", discovery.repo_is_archived),
    (discovery, "repo_missing", discovery.repo_missing),
    (settings, "org_meta", settings.org_meta),
    (settings, "_assignments_text", settings._assignments_text),
    (settings, "course_org_for_semester", settings.course_org_for_semester),
]


@pytest.fixture
def github(monkeypatch):
    """The fake `gh`, and a CLI start that really runs its start-of-run hooks - the budget
    line and the read-once memos - as a real run does."""
    fake = FakeGitHub()
    for module, name, real in _REAL:
        monkeypatch.setattr(module, name, real)
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr(log, "_cli_started", False)
    monkeypatch.setattr(ghcli, "_at_exit", lambda fn, *a: None)
    return fake


def _run(monkeypatch, cli, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", [cli.__name__, *argv])
    cli.main()


# Reads that must never be issued twice in one run: a file, a tree, a repo's metadata.
_READ_ONCE = ("/contents/", "/git/trees/", "/git/blobs/")


def _reads_twice(calls: list[tuple[str, ...]]) -> list[str]:
    """Every file, tree, blob or repo-metadata PATH read more than once, whatever `--jq`
    shape each read asked for."""
    seen: set[str] = set()
    twice = []
    for call in calls:
        if call[:1] != ("api",) or "--method" in call:
            continue
        path = next((a for a in call if a.startswith("repos/")), "")
        path = path.split("?recursive")[0].casefold()
        if any(m in path for m in _READ_ONCE) or path.count("/") == 2:
            if path in seen:
                twice.append(path)
            seen.add(path)
    return twice


def _tick(monkeypatch) -> None:
    _run(
        monkeypatch,
        scheduler,
        "--course-org",
        COURSE,
        "--semester-org",
        SEMESTER,
        "--now",
        "2026-09-26T12:00:00+00:00",
        "--preview",
    )


def test_the_memos_change_what_a_tick_costs_and_nothing_it_says(
    github, monkeypatch, capsys
):
    # The same preview tick with the memos on and off: identical output, fewer calls.
    _tick(monkeypatch)
    on, cost_on = capsys.readouterr().out, len(github.calls)
    github.calls.clear()
    clear_process_memos()  # every memo a module keeps, the two under test switched off
    monkeypatch.setattr(log, "_cli_started", False)
    monkeypatch.setattr(gh_contents, "read_once", lambda on: None)
    monkeypatch.setattr(issues, "list_once", lambda on: None)
    _tick(monkeypatch)
    off = capsys.readouterr().out
    assert on == off
    assert cost_on < len(github.calls)


def test_a_preview_tick_stays_under_its_ceiling(github, monkeypatch):
    # Measured 2026-09-26: 51 here before the read-once memos, 32 after them, 31 once the
    # grading leg reads its markers off one tree. The same preview tick against the
    # demo's f2026 semester: 115, 59 (54 with `--skip-autograde`), then 54.
    _tick(monkeypatch)
    assert len(github.calls) <= 40
    assert _reads_twice(github.calls) == []
    assert len(github.did("issue", "list")) == 1


def test_a_membership_sync_stays_under_its_ceiling(github, monkeypatch):
    # Measured 2026-09-26: 22 here. The demo's two semesters: 53 calls, then 45 once one
    # GraphQL query per semester answers every repo's collaborators (8 REST reads of the
    # prune's stale repos gone; only their invitations are still read per repo).
    _run(
        monkeypatch,
        sync_membership,
        "--course-org",
        COURSE,
        "--all-semesters",
        "--preview",
    )
    assert len(github.calls) <= 28
    assert _reads_twice(github.calls) == []


def test_a_status_refresh_stays_under_its_ceiling(github, monkeypatch):
    # Measured 2026-09-26: 11 here. The demo's f2026 semester: 12 before, 12 after.
    _run(
        monkeypatch,
        status,
        "--course-org",
        COURSE,
        "--semester-org",
        SEMESTER,
        "--preview",
    )
    assert len(github.calls) <= 14
    assert _reads_twice(github.calls) == []


# --------------------------------------------------------------- the read-once memo


PATH_ = "schedule.yml"


def _reads_of(github, path: str) -> int:
    """How many READS of `path` reached the fake (a write to it is not one)."""
    return sum(
        1
        for c in github.calls
        if "--method" not in c and any(a.endswith(f"/contents/{path}") for a in c)
    )


@pytest.fixture
def memo(github):
    gh_contents.read_once(True)
    return github


def test_off_by_default_every_read_goes_to_github(github):
    # Outside a CLI run - the e2e harness is one long process reading what remote runs
    # write - nothing is held.
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(github, PATH_) == 2


def test_a_file_is_read_once_and_an_absence_too(memo):
    for _ in range(3):
        gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
        assert gh_contents.get_file_content(SEMESTER, "semester-config", "nope") is None
    assert _reads_of(memo, PATH_) == _reads_of(memo, "nope") == 1


def test_a_read_that_failed_is_asked_again(memo, monkeypatch):
    # Only a definite answer is kept: a 403 or a rate limit is not "absent".
    answer = memo.answer
    monkeypatch.setattr(
        memo,
        "answer",
        lambda argv: (
            (1, "gh: Forbidden (HTTP 403)") if PATH_ in " ".join(argv) else answer(argv)
        ),
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(memo, PATH_) == 2


def test_a_contents_write_forgets_only_the_path_it_wrote(memo):
    # One commit of one path: that file changed, its neighbours did not.
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(SEMESTER, "semester-config", "students.csv")
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    gh_contents.put_file(SEMESTER, "semester-config", PATH_, b"x", "msg")
    for path in (PATH_, "students.csv"):
        gh_contents.get_file_content(SEMESTER, "semester-config", path)
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    # the first read, `put_file`'s own look at the sha it replaces, and the re-read
    assert _reads_of(memo, PATH_) == 3
    assert _reads_of(memo, "students.csv") == _reads_of(memo, "semesters.yml") == 1


def test_a_contents_write_forgets_the_listings_and_trees_above_it(memo):
    gh_contents.file_exists(SEMESTER, "semester-config", ".system")
    gh_contents.repo_tree(COURSE, "course-materials-f2026", "main")
    gh_contents.put_file(SEMESTER, "semester-config", ".system/new.yml", b"x", "m")
    gh_contents.put_file(COURSE, "course-materials-f2026", "README.md", b"x", "m")
    gh_contents.file_exists(SEMESTER, "semester-config", ".system")
    gh_contents.repo_tree(COURSE, "course-materials-f2026", "main")
    assert _reads_of(memo, ".system") == 2
    assert sum("/git/trees/" in c[1] for c in memo.calls) == 2


def test_an_unchanged_file_a_push_forgot_comes_back_as_a_free_304(memo, monkeypatch):
    # Its ETag is its blob sha, so the re-read asks If-None-Match and costs no budget.
    before = gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    monkeypatch.setattr(
        ghcli,
        "_push_remote",
        lambda a, c: f"https://github.com/{SEMESTER}/semester-config.git",
    )
    ghcli.git("-C", "/nonexistent", "push", "-q", "origin", "HEAD")
    assert gh_contents.get_file_content(SEMESTER, "semester-config", PATH_) == before
    assert gh_contents.get_file_content(SEMESTER, "semester-config", PATH_) == before
    assert _reads_of(memo, PATH_) == 2
    assert len(memo.not_modified) == 1


@pytest.mark.parametrize(
    "read",
    [
        gh_contents.get_file_content,
        gh_contents.get_file_with_sha,
        gh_contents.file_exists,
    ],
)
def test_every_file_read_shape_survives_its_conditional_re_read(
    memo, monkeypatch, read
):
    # gh runs `--jq` over a 304's empty body and fails; the re-read must still answer
    # exactly what the first read did, for every shape a file is read in.
    key = (SEMESTER, "semester-config", PATH_)
    before = read(*key)
    monkeypatch.setattr(
        ghcli,
        "_push_remote",
        lambda a, c: f"https://github.com/{SEMESTER}/semester-config.git",
    )
    ghcli.git("-C", "/nonexistent", "push", "-q", "origin", "HEAD")
    assert read(*key) == before
    assert len(memo.not_modified) == 1
    assert all("--jq" not in call for call in memo.not_modified)


def test_a_changed_file_a_push_forgot_is_read_afresh(memo, monkeypatch):
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    memo.files[(SEMESTER, "semester-config", PATH_)] = "changed\n"
    monkeypatch.setattr(
        ghcli,
        "_push_remote",
        lambda a, c: f"https://github.com/{SEMESTER}/semester-config.git",
    )
    ghcli.git("-C", "/nonexistent", "push", "-q", "origin", "HEAD")
    text = gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert text == "changed\n"
    assert memo.not_modified == []


def test_a_repo_created_in_an_org_forgets_that_orgs_reads(memo):
    # Its absence was a definite answer, and the create makes it wrong.
    assert gh_contents.get_file_content(SEMESTER, "new-repo", "README.md") is None
    ghcli.gh("api", "--method", "POST", f"orgs/{SEMESTER}/repos", "-f", "name=new-repo")
    gh_contents.get_file_content(SEMESTER, "new-repo", "README.md")
    assert _reads_of(memo, "README.md") == 2


def test_a_create_refused_for_a_taken_name_keeps_the_orgs_reads(memo):
    # Every release asks to create its dest, and GitHub says it is there: nothing was
    # made, so nothing else in the org moved.
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    repos.repo_is_archived(SEMESTER, "semester-config")
    repos.create_repo(SEMESTER, "materials")
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    repos.repo_is_archived(SEMESTER, "semester-config")
    assert _reads_of(memo, PATH_) == 1
    assert len(memo.did("api", f"repos/{SEMESTER}/semester-config")) == 1


def test_a_settings_patch_keeps_the_files_and_forgets_that_repos_metadata(memo):
    gh_contents.get_file_content(SEMESTER, "materials", "README.md")
    for name in ("materials", "semester-config"):
        repos.repo_is_archived(SEMESTER, name)
    ghcli.gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{SEMESTER}/materials",
        "--field",
        "allow_forking=true",
    )
    gh_contents.get_file_content(SEMESTER, "materials", "README.md")
    for name in ("materials", "semester-config"):
        repos.repo_is_archived(SEMESTER, name)
    assert _reads_of(memo, "README.md") == 1
    assert len(memo.did("api", f"repos/{SEMESTER}/materials")) == 2
    assert len(memo.did("api", f"repos/{SEMESTER}/semester-config")) == 1


def test_an_orgs_listing_is_taken_once_and_handed_out_as_a_copy(memo):
    discovery.hold_listings(True)
    first = discovery.list_org_repos(SEMESTER)
    first[0]["name"] = "scribbled"
    assert discovery.list_org_repos(SEMESTER)[0]["name"] != "scribbled"
    assert (
        len(memo.did("api", "--paginate", f"orgs/{SEMESTER}/repos?per_page=100")) == 1
    )


@pytest.mark.parametrize(
    "write",
    [
        ("api", "--method", "PUT", f"repos/{SEMESTER}/materials/topics"),
        (
            "api",
            "--method",
            "PATCH",
            f"repos/{SEMESTER}/materials",
            "-F",
            "archived=true",
        ),
        ("api", "--method", "POST", f"orgs/{SEMESTER}/repos", "-f", "name=new"),
        ("api", "--method", "PUT", f"repos/{SEMESTER}/materials/contents/x.md"),
    ],
)
def test_a_write_that_changes_a_row_forgets_the_orgs_listing(memo, write):
    discovery.hold_listings(True)
    discovery.list_org_repos(SEMESTER)
    discovery.list_org_repos(COURSE)
    ghcli.gh(*write)
    discovery.list_org_repos(SEMESTER)
    discovery.list_org_repos(COURSE)
    assert (
        len(memo.did("api", "--paginate", f"orgs/{SEMESTER}/repos?per_page=100")) == 2
    )
    assert len(memo.did("api", "--paginate", f"orgs/{COURSE}/repos?per_page=100")) == 1


def test_a_push_whose_remote_cannot_be_read_forgets_everything(memo, tmp_path):
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    ghcli.git("-C", str(tmp_path), "push", "-q", "origin", "HEAD")
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(memo, PATH_) == 2


def test_an_issue_write_keeps_the_files(memo):
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    ghcli.gh("issue", "close", "7", "--repo", f"{SEMESTER}/semester-config")
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(memo, PATH_) == 1


def test_a_patch_of_the_repo_object_forgets_its_metadata(github):
    repos.repo_is_archived(SEMESTER, "semester-config")
    repos.repo_is_archived(SEMESTER, "semester-config")
    ghcli.gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{SEMESTER}/semester-config",
        "-F",
        "archived=true",
    )
    repos.repo_is_archived(SEMESTER, "semester-config")
    assert len(github.did("api", f"repos/{SEMESTER}/semester-config")) == 2


def test_a_push_forgets_only_the_repo_it_landed_in(memo, monkeypatch):
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    monkeypatch.setattr(
        ghcli, "_push_remote", lambda a, c: f"https://github.com/{COURSE}/.github.git"
    )
    ghcli.git("-C", "/nonexistent", "push", "-q", "origin", "HEAD")
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    assert _reads_of(memo, PATH_) == 1
    assert _reads_of(memo, "semesters.yml") == 2


def test_names_differing_only_in_case_are_one_repo(memo):
    # GitHub's owner and repo names are not case-sensitive, and callers do not agree.
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(SEMESTER.lower(), "Semester-Config", PATH_)
    assert _reads_of(memo, PATH_) == 1
    ghcli.gh(
        "api",
        "--method",
        "PUT",
        f"repos/{SEMESTER.upper()}/SEMESTER-CONFIG/contents/{PATH_}",
    )
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(memo, PATH_) == 2


def test_a_rename_forgets_the_absence_of_the_new_name(memo):
    assert gh_contents.get_file_content(SEMESTER, "renamed", "README.md") is None
    ghcli.gh(
        "api", "--method", "PATCH", f"repos/{SEMESTER}/materials", "-f", "name=renamed"
    )
    gh_contents.get_file_content(SEMESTER, "renamed", "README.md")
    assert _reads_of(memo, "README.md") == 2


def test_a_team_or_collaborator_write_keeps_the_files(memo):
    # A hand-out tick makes dozens of these; each one clearing the org's reads brought
    # the repeated pointer reads straight back.
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    ghcli.gh("api", "--method", "PUT", f"orgs/{SEMESTER}/teams/students/memberships/u")
    ghcli.gh(
        "api", "--method", "PUT", f"repos/{SEMESTER}/semester-config/collaborators/u"
    )
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    assert _reads_of(memo, PATH_) == 1


def test_a_blob_outlives_every_write(memo, monkeypatch):
    # Addressed by its sha, so nothing can change it.
    text = "x\n"
    sha = hashlib.sha1(f"blob {len(text)}\0{text}".encode()).hexdigest()
    answer = memo.answer
    blob = (0, base64.b64encode(text.encode()).decode())
    monkeypatch.setattr(
        memo, "answer", lambda argv: blob if "/git/blobs/" in argv[1] else answer(argv)
    )
    gh_contents.get_blob(SEMESTER, "semester-config", sha)
    ghcli.forget_all()
    gh_contents.get_blob(SEMESTER, "semester-config", sha)
    assert sum("/git/blobs/" in c[1] for c in memo.calls) == 1


# -------------------------------------------------- a CLI that waits on other runs


def test_migrate_keeps_every_read_live(github, monkeypatch):
    # It plans, waits for other runs to finish, then writes on top of what they wrote.
    monkeypatch.setattr(sys, "argv", ["migrate", SEMESTER, "--abandon"])
    with pytest.raises(SystemExit):
        migrate.main()
    assert gh_contents._reads is None and issues._listings is None


def test_what_a_settle_waited_through_is_read_fresh_and_written_fresh(
    memo, monkeypatch
):
    # A read before the wait, a commit by another run during it, and the write after it:
    # the write must carry the other run's bytes, not revert them.
    issues.list_once(True)
    key = (SEMESTER, "semester-config", PATH_)
    before = gh_contents.get_file_content(*key)
    monkeypatch.setattr(migrate, "_alive", lambda targets: [])
    memo.files[key] = before + "# another run's commit\n"
    assert migrate.settle(lambda: [], before_switch=False)
    after = gh_contents.get_file_content(*key)
    assert after.endswith("# another run's commit\n")
    written = []
    monkeypatch.setattr(
        gh_contents, "gh", lambda *a, **k: written.append(k.get("stdin")) or (0, "")
    )
    gh_contents.put_file(*key, (after + "edit\n").encode(), "msg", expected_sha="s")
    assert (
        base64.b64decode(written[-1])
        .decode()
        .endswith("# another run's commit\nedit\n")
    )
