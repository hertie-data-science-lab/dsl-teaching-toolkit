"""What one engine run costs in GitHub API calls, with a ceiling a regression trips.

The bot account is ONE user with 5,000 calls an hour for every course org, and on
2026-09-26 the new engine spent it in half an hour on the demo alone: a preview tick of a
two-student semester cost 113 calls, 45 of them the same pointer file. So the three runs
that fire most often are driven here end to end - the real CLI, command line and all -
against a fake GitHub at the `gh` binary, which counts every call and answers from the
demo's own layout fixtures. A change that makes a run dearer fails here, like a layering
violation does.

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


class FakeGitHub:
    """The `gh` binary for one course org and one semester: every call recorded, every
    answer made from the files and trees below, anything else a 404."""

    def __init__(self) -> None:
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
            + "".join(f"{h}@example.org,{h},enrolled,{h}\n" for h in STUDENTS),
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
                (COURSE, f"{slug}-f2026"): ["README.md", "assignment.ipynb"]
                for slug in ASSIGNMENTS
            },
        }
        self.repos: dict[str, list[str]] = {
            COURSE: [".github", *{repo for _, repo in self.trees}],
            SEMESTER: [".github", "semester-config", "materials"]
            + [f"{slug}-{h}" for slug in ASSIGNMENTS for h in STUDENTS],
        }
        self.calls: list[tuple[str, ...]] = []

    def did(self, *prefix: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def run(self, cmd, *args, **kwargs):
        if not cmd or cmd[0] != "gh":
            return _real_run(cmd, *args, **kwargs)
        argv = tuple(map(str, cmd[1:]))
        self.calls.append(argv)
        code, out = self.answer(argv)
        err = "" if code == 0 else out
        return SimpleNamespace(
            returncode=code, stdout=out if code == 0 else "", stderr=err
        )

    def answer(self, argv: tuple[str, ...]) -> tuple[int, str]:
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
                    "isTemplate": False,
                    "archived": False,
                    "pushed_at": "",
                    "topics": [],
                }
                for name in self.repos.get(parts[1], [])
            ]
            return 0, "\n".join(json.dumps(r) for r in rows)
        if parts[0] == "orgs":
            return 0, "" if jq else "[]"
        if parts[0] != "repos" or len(parts) < 3:
            return _NOT_FOUND
        org, repo, rest = parts[1], parts[2], parts[3:]
        if repo not in self.repos.get(org, []):
            return _NOT_FOUND
        if not rest:
            return 0, json.dumps(
                {
                    "name": repo,
                    "default_branch": "main",
                    "private": True,
                    "archived": False,
                    "size": 1,
                }
            )
        if rest[0] == "contents":
            return self._contents(org, repo, "/".join(rest[1:]), jq)
        if rest[:2] == ["git", "trees"]:
            return self._tree(org, repo, jq)
        if rest[0] in ("collaborators", "invitations", "commits", "issues"):
            return 0, "" if jq else "[]"
        if rest[0] == "actions":
            return 0, '{"workflow_runs": []}'
        return _NOT_FOUND

    def _contents(self, org: str, repo: str, path: str, jq: str) -> tuple[int, str]:
        text = self.files.get((org, repo, path))
        if text is None:
            names = sorted(
                p.split("/")[-1]
                for (o, r, p) in self.files
                if (o, r) == (org, repo) and p.rsplit("/", 1)[0] == path
            )
            return (0, "\n".join(names)) if names else _NOT_FOUND
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
    # Measured 2026-09-26: 32 here (51 before the read-once memos). The same preview tick
    # against the demo's f2026 semester: 115 before, 59 after.
    _tick(monkeypatch)
    assert len(github.calls) <= 40
    assert _reads_twice(github.calls) == []
    assert len(github.did("issue", "list")) == 1


def test_a_membership_sync_stays_under_its_ceiling(github, monkeypatch):
    # Measured 2026-09-26: 22 here. The demo's two semesters: 53 before, 53 after - the
    # cost is per student repo (collaborators + invitations), which this fixture has none
    # of, and the tick's hot spots were not on this path.
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
    return sum(
        1 for c in github.calls if any(a.endswith(f"/contents/{path}") for a in c)
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


def test_a_write_to_the_repo_forgets_what_was_read_from_it(memo):
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    gh_contents.put_file(SEMESTER, "semester-config", "other.txt", b"x", "msg")
    gh_contents.get_file_content(SEMESTER, "semester-config", PATH_)
    gh_contents.get_file_content(COURSE, ".github", "semesters.yml")
    assert _reads_of(memo, PATH_) == 2
    assert _reads_of(memo, "semesters.yml") == 1


def test_a_repo_created_in_an_org_forgets_that_orgs_reads(memo):
    # Its absence was a definite answer, and the create makes it wrong.
    assert gh_contents.get_file_content(SEMESTER, "new-repo", "README.md") is None
    ghcli.gh("api", "--method", "POST", f"orgs/{SEMESTER}/repos", "-f", "name=new-repo")
    gh_contents.get_file_content(SEMESTER, "new-repo", "README.md")
    assert _reads_of(memo, "README.md") == 2


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
        "api", "--method", "PUT", f"repos/{SEMESTER.upper()}/SEMESTER-CONFIG/contents/x"
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
