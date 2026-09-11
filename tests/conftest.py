"""Shared test helpers.

The package imports cleanly without network (the gh/git calls only fire when a function
runs), so tests import dsl_course modules directly and exercise the PURE logic: the
workflow renderers (their output must be GitHub-parseable YAML) and the content
transforms in site/release. The thin gh/git orchestration is deliberately NOT mocked -
that only asserts we wrote the call we wrote; its real failure modes need a live org.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from dsl_course import (
    bootstrap_course,
    central,
    collect,
    discovery,
    gh_contents,
    ghcli,
    grades,
    issues,
    pulls,
    repos,
    roster,
    schedule,
    site,
    sync_faculty,
    teams,
)

# students.csv's header row, DERIVED from the columns the engine declares rather than
# re-typed. `roster.FIELDS` is a frozen public contract (the shipped JavaScript spells the
# same columns out by hand), and five test files each carried their own copy of it - so a
# column added to FIELDS left five fixtures describing a roster that no longer exists.
ROSTER_HEADER = ",".join(roster.FIELDS)


def repo_row(name: str, **extra) -> dict:
    """One row of a `discovery.list_org_repos` listing, carrying every field it really has.

    Three test files kept their own partial builder, each missing a different key, so code
    that reads `archived` or `topics` off a listing was tested against rows that have
    neither. Defaults are the uninteresting answer; `extra` overrides what a test is about.
    """
    return {
        "name": name,
        "description": "",
        "visibility": "private",
        "url": f"https://github.com/org/{name}",
        "isTemplate": False,
        "archived": False,
        "topics": [],
        **extra,
    }


@pytest.fixture(autouse=True)
def _empty_write_governor():
    """`ghcli`'s write pacer keeps its timestamps at module level, so they would otherwise
    accumulate across the session until an unrelated test slept for a real minute."""
    ghcli._write_times.clear()


@pytest.fixture(autouse=True)
def _no_live_gh(monkeypatch):
    """Refuse any live `gh` call from a test.

    Nothing here is meant to reach GitHub (see the module docstring), but a tokenless CI
    box and an authenticated dev box disagree about what happens when something does: CI
    errors and the developer's machine quietly succeeds against real orgs. That is how a
    test that stubbed `site._session_files` but not `site._repo_tree` passed locally for a
    whole branch and failed only on the PR.

    Guards the `gh` BINARY rather than `ghcli.gh`, so the retry ladder and return-pair
    contract of `ghcli.gh` itself stay testable, and `git` against a tmp repo still runs. A
    test that legitimately fakes `gh` or `git` sets its own after this fixture and wins."""
    real_run = subprocess.run

    def guarded(cmd, *args, **kwargs):
        # `gh <cmd> --help` reads gh's own built-in flag list: no network, no auth, and it
        # is how test_gh_contract.py proves a flag the code passes really exists.
        if cmd and cmd[0] == "gh" and "--help" not in cmd:
            raise AssertionError(
                f"live `{' '.join(map(str, cmd[:3]))}` from a test - stub what the code "
                "under test reads (site._repo_tree, gh_contents.get_file_content, ...) instead."
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)


@pytest.fixture(autouse=True)
def _the_central_ref_is_present(monkeypatch):
    """Answer `central.central_ref_exists`'s probe with "yes" by default.

    Every workflow write goes through `central.pin_central_ref`, which asks GitHub whether
    the org's ref is on the central repo before pinning a workflow to it. Nothing here is
    meant to reach GitHub (see the module docstring), and "it is there" is the
    uninteresting answer for every test but the ones about the check itself - which set
    their own `central.gh` after this fixture and win.

    `identical` is what the SHA path reads off `compare/main...{sha}`; the branch path
    only looks at the exit code."""
    monkeypatch.setattr(central, "gh", lambda *a, **k: (0, "identical"))


@pytest.fixture(autouse=True)
def _no_cohort_is_closed_out(monkeypatch):
    """Answer `discovery.cohort_is_live`'s probe with "still running" by default.

    Every course-side sweep now asks whether a cohort's `classroom-config` is archived
    before writing into it, which is a live `gh api repos/<org>/classroom-config`. A
    running cohort is the uninteresting answer for every test but the ones about the skip
    itself, which set their own after this fixture and win."""
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, name: False)


@pytest.fixture(autouse=True)
def _not_on_a_runner(monkeypatch):
    """Every test runs as though it were NOT inside GitHub Actions, unless it says so.

    `collect.sandbox_unusable` asks the environment whether it is on a runner and, if it
    is, whether the `dsl-sandbox` account is there to drop student code to. CI *is* a
    runner and has no such account, so without this the whole suite inherited the
    fail-closed answer - graded nothing, and a dozen tests about what `collect` does with a
    cohort failed on Linux while passing on a laptop. The memos go with the variable: they
    are answered once per process, so a test that sets `GITHUB_ACTIONS` itself must not
    leave its answer behind for the next one."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    collect.sandbox_user.cache_clear()
    collect.sandbox_unusable.cache_clear()


@pytest.fixture(autouse=True)
def _clear_process_memos():
    """The per-process memos a single CLI run is entitled to keep: a repo's tree and its
    paths, a repo's metadata and its last committer, whether a central ref exists, the
    classroom-config files a run re-reads (students.csv, teams.csv, schedule.yml,
    people.yml), an assignment's definition, its course's defaults and its handed-out
    starters, and the login the token belongs to. Tests reuse the same org/repo names
    with different fakes, so clear them between tests."""
    site._repo_tree.cache_clear()
    central.central_ref_exists.cache_clear()
    repos._repo.cache_clear()
    roster._roster_text.cache_clear()
    teams._teams_text.cache_clear()
    schedule._schedule_text.cache_clear()
    schedule._repo_paths.cache_clear()
    grades._grading_text.cache_clear()
    grades.course_assignment_defaults.cache_clear()
    collect._starter_notebook_shas.cache_clear()
    gh_contents.last_committer.cache_clear()
    gh_contents.blame_logins.cache_clear()
    gh_contents.path_committers.cache_clear()
    sync_faculty.load_cohort_faculty.cache_clear()
    ghcli.bot_login.cache_clear()


def stub_bootstrap(monkeypatch) -> None:
    """Neutralise everything a bootstrap does EXCEPT the site sync - the org-level gh/git
    layer, the repo seeding and the summary output. Shared: two test files now drive
    `bootstrap_course.main`, and a per-file copy is how one of them ends up stubbing a
    step the other has since renamed."""
    bc = bootstrap_course
    # Every configuration step reports a failure count that _run threads into its exit
    # code and into the closing summary - a clean stub reports zero failures.
    for name in (
        "converge_org_settings",
        "create_default_teams",
        "grant_button_access",
        "setup_cohort_extras",
        "seed_workflows",
        "create_profile_repo",
    ):
        monkeypatch.setattr(bc, name, lambda *a, **k: 0)
    monkeypatch.setattr(bc, "preflight", lambda org: True)
    monkeypatch.setattr(bc, "add_course_admins", lambda org, handles: 0)
    monkeypatch.setattr(bc, "validate_secret_presence", lambda org, secret: True)
    monkeypatch.setattr(bc, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bc, "register_cohort", lambda course, cohort: True)
    monkeypatch.setattr(bc, "update_profile_readme", lambda *a, **k: 0)
    monkeypatch.setattr(bc.sync_faculty, "sync", lambda course, cohorts=None: 0)
    # The org's tier is read off its (not yet written) dsl-course.yml; a bootstrap test is
    # about what the run does, not which ref it seeds at.
    monkeypatch.setattr(bc, "central_ref_for", lambda org: "release")


# What the `gh issue create` in `GhFake` prints.
CREATED_ISSUE_URL = "https://github.com/Cohort/classroom-config/issues/12"


class GhFake:
    """A recording fake for the two `ghcli` entry points the issue helpers use - `gh` for
    the writes, `gh_json` for the listing - with every call captured.

    Shared, because four test files each grew their own copy and they disagreed about the
    one thing that matters: the listing goes through `gh_json` (which parses stdout ALONE,
    so a `gh` advisory on stderr cannot spoil it) and a read that failed reaches the code
    as the exception `gh_json` raises.

    `rows` is the OPEN issues and `closed` the closed ones; each row is stamped with its
    state, so a test says which list a row is in and never both."""

    def __init__(
        self,
        rows: list[dict] | None = None,
        closed: list[dict] | None = None,
        list_code: int = 0,
        write_code: int = 0,
        created_url: str = CREATED_ISSUE_URL,
    ):
        self.rows = [{**r, "state": "OPEN"} for r in rows or []]
        self.closed = [{**r, "state": "CLOSED"} for r in closed or []]
        self.list_code = list_code
        self.write_code = write_code
        self.created_url = created_url
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if self.write_code:
            return self.write_code, "boom"
        # What `gh issue create` really prints - the new issue's URL, which is the only
        # place a caller can learn the number of an issue it has just opened.
        return 0, f"{self.created_url}\n" if args[:2] == ("issue", "create") else ""

    def json(self, *args, **kwargs):
        self.calls.append(args)
        if self.list_code != 0:
            raise RuntimeError(f"`gh issue list` failed (exit {self.list_code}): boom")
        state = args[args.index("--state") + 1] if "--state" in args else "open"
        if state == "closed":
            return self.closed
        if state == "all":
            return self.rows + self.closed
        return self.rows

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def body_of(self, *prefix) -> str:
        (call,) = self.did(*prefix)
        return call[call.index("--body") + 1]


@pytest.fixture
def gh(monkeypatch):
    """A `GhFake` wired into `dsl_course.issues`, which is where every self-updating issue
    in the toolkit reaches `gh`."""

    def _make(rows=None, closed=None, list_code=0, write_code=0) -> GhFake:
        fake = GhFake(rows, closed, list_code, write_code)
        monkeypatch.setattr(issues, "gh", fake)
        monkeypatch.setattr(issues, "gh_json", fake.json)
        return fake

    return _make


def issue_row(number: int, title: str, body: str = "") -> dict:
    """One row of the `gh issue list --json number,body,title,state` listing. `GhFake`
    stamps the state, from whichever of its two lists the row was put in."""
    return {"number": number, "title": title, "body": body}


# --------------------------------------------------------------- against real git

# Two suites run against real repositories rather than a stubbed `git` - the release's
# merge onto `upstream` (`test_release_merge`) and the propagate back out of a cohort
# (`test_propagate`). Both are about what ends up on a BRANCH and in a COMMIT HISTORY,
# which a stubbed `git` can only assert back at itself, and both need the same three
# things: bare origins on disk, a way to put a commit in one, and a `gh repo clone` that
# reaches them. Written once here, so the two cannot drift into asserting against
# differently-built worlds.


def git_ok(*args: str) -> str:
    """One git command that MUST succeed, returning its stdout.

    These fixtures build their world with git itself, and a set-up step that failed
    silently would leave the test asserting about a repo that was never built."""
    code, out = ghcli.git(*args)
    assert code == 0, f"`git {' '.join(args)}` failed: {out}"
    return out


class PullsFake:
    """`pulls` as `deploy` and `propagate` see it, recording what it was asked to upsert.

    `upsert_pr` is idempotent by head branch - that is `test_pulls`' subject, and asserting
    it again in a caller's tests would only re-test the fake. What this records is WHAT is
    proposed, and how often."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.calls: list[dict] = []

    def upsert_pr(self, repo: str, **kwargs) -> pulls.Upserted:
        self.calls.append({"repo": repo, **kwargs})
        return pulls.Upserted(0, self.url)


class BareOrigins:
    """GitHub repositories as bare git repositories on disk, and the reads a test makes of
    them afterwards.

    Every method takes the repo NAME first and a ref second, because a suite that works in
    one repo and one that works across four both read better that way; each suite wraps
    these in whatever its own subject calls them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origins = root / "origins"
        self.origins.mkdir(parents=True, exist_ok=True)
        self._scratch = 0

    def bare(self, name: str) -> Path:
        """The bare repo `name`, created empty the first time it is mentioned."""
        path = self.origins / f"{name}.git"
        if not path.exists():
            git_ok("init", "-q", "--bare", "-b", "main", str(path))
        return path

    def commit(
        self,
        name: str,
        files: dict[str, str | None],
        message: str = "edit",
        branch: str = "main",
    ) -> None:
        """One commit on `branch` of a bare repo, through a throwaway clone - the only way
        to write into a repo with no working tree. A `None` value DELETES that path.

        `branch` is what students read unless a test needs the release branch a held merge
        leaves ahead of it (`deploy.UPSTREAM_BRANCH`), which is a real state a cohort repo
        sits in whenever a release conflicted.

        The commit borrows the engine's identity and its disabled hooks, so a developer's
        global git hooks cannot fail somebody else's test run."""
        self._scratch += 1
        work = self.root / "scratch" / f"{name}{self._scratch}"
        git_ok("clone", "-q", str(self.bare(name)), str(work))
        if branch != "main":
            remote = f"origin/{branch}"
            probe = ghcli.git("-C", str(work), "rev-parse", "--verify", remote)
            # Off the remote branch when it is already there, off whatever was cloned when
            # it is not - which is how a first release cuts `upstream` too.
            start = [remote] if probe[0] == 0 else []
            git_ok(
                "-C", str(work), *ghcli.GIT_ENV, "checkout", "-q", "-B", branch, *start
            )
        for rel, text in files.items():
            path = work / rel
            if text is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git_ok("-C", str(work), "add", "-A")
        git_ok(
            "-C",
            str(work),
            *ghcli.GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            message,
        )
        git_ok("-C", str(work), "push", "-q", "origin", f"HEAD:refs/heads/{branch}")

    def clone_only(self, *args, **kwargs) -> tuple[int, str]:
        """`ghcli.gh` as these fixtures allow it: `repo clone` reaches the bare repo beside
        it, and every other call is a silent success. Anything that is genuinely about a
        gh API call is somebody else's test."""
        if args[:2] == ("repo", "clone"):
            name = args[2].split("/", 1)[1]
            return ghcli.git("clone", "-q", str(self.bare(name)), str(args[3]))
        return 0, ""

    def _read(self, name: str, *args: str) -> str:
        return git_ok("--git-dir", str(self.bare(name)), *args)

    def refs(self, name: str) -> list[str]:
        """Every branch the bare repo holds, sorted."""
        listed = self._read(
            name, "for-each-ref", "--format=%(refname:short)", "refs/heads"
        )
        return sorted(listed.split())

    def rev(self, name: str, ref: str) -> str:
        return self._read(name, "rev-parse", ref)

    def show(self, name: str, ref: str, path: str) -> str:
        return self._read(name, "show", f"{ref}:{path}")

    def tree(self, name: str, ref: str) -> list[str]:
        """Every path in the tree at `ref`, sorted."""
        return sorted(self._read(name, "ls-tree", "-r", "--name-only", ref).split())

    def subjects(self, name: str, rev_range: str) -> list[str]:
        """The commit subjects in `rev_range`, oldest first."""
        listed = self._read(name, "log", "--format=%s", "--reverse", rev_range)
        return listed.splitlines()


def source_fault(
    where: str = "releases.lecture_02",
    what: str = "Course-Org/cm/lectures/02_lecture does not exist",
    fires: datetime | None = None,
    *,
    field: str = "course_source_path",
    kind: schedule.FaultKind | None = None,
    lineno: int | None = None,
    repo: str = "cm",
    path: str = "lectures/02_lecture",
    ceiling: schedule.Severity = schedule.Severity.MISSED,
) -> schedule.SourceFault:
    """A `SourceFault` for a test that is about what a fault DOES, not how one is built.

    `kind` defaults to whatever `field` implies, which is what `source_faults` sets for a
    plain missing source - the case every notification test wants. Four test files carried
    their own builder and each defaulted a different field, so a fault's `key`, its line
    citation and its fix sentence were each pinned against a different shape."""
    if kind is None:
        kind = (
            schedule.FaultKind.MISSING_REPO
            if field == "course_source_repo"
            else schedule.FaultKind.MISSING_PATH
        )
    return schedule.SourceFault(
        where,
        what,
        fires,
        file=schedule.SCHEDULE_PATH,
        field=field,
        kind=kind,
        ceiling=ceiling,
        lineno=lineno,
        repo=repo,
        path=path,
    )


def workflow_inputs(rendered: str) -> dict:
    """Parse a rendered workflow and return its `workflow_dispatch.inputs`.

    PyYAML follows YAML 1.1, where the bare key `on:` parses to boolean True, so the
    top-level trigger key is `True`, not the string "on" (GitHub's own parser is fine
    with `on:`). Accept either so the test asserts real structure, not the quirk."""
    doc = yaml.safe_load(rendered)
    trigger = doc.get("on", doc.get(True))
    return trigger["workflow_dispatch"].get("inputs") or {}


def workflow_jobs(rendered: str) -> dict:
    return yaml.safe_load(rendered)["jobs"]


def entry_link_rows(rendered: str) -> list[dict]:
    """Every link of a rendered session entry's `links:` block, whole - each a mapping of
    whatever fields the emitter wrote (`url`, `name`, `section`, and `view_url` only where
    the file has a hosted copy).

    Parsed rather than substring-matched: a link is four fields now, so an assertion
    written against the rendered bytes would be asserting the emitter's line order as much
    as its content."""
    front = rendered.split("---\n")[1]
    return yaml.safe_load(front).get("links") or []


def entry_links(rendered: str) -> list[tuple[str, str]]:
    """The (section, name) pairs of the same block - the view most assertions want."""
    return [(l["section"], l["name"]) for l in entry_link_rows(rendered)]
