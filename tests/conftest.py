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
    config,
    discovery,
    gh_teams,
    ghcli,
    grading_sheet,
    issues,
    log,
    pulls,
    roster,
    run,
    schedule,
    settings,
)

# A test writes each student as ONE row in the pre-0.11.0 combined shape (students.csv
# with the engine's columns on it), DERIVED from the columns the engine declares rather
# than re-typed. `parse_roster` / `roster_files` split it exactly as the migration does
# (`roster.split_legacy`), so a fixture stays one line per student and the parse under
# test is the real one over the real two files.
ROSTER_HEADER = ",".join(
    (roster.LEGACY_EMAIL, *roster.FIELDS[1:], *roster.LEGACY_SYSTEM_COLUMNS)
)


def roster_files(
    text: str, teams: str | None = None, numbers: dict | None = None
) -> dict[str, str]:
    """`{students.csv: text, .system/roster.csv: text}` from one combined fixture (and an
    old-shape teams.csv, `numbers` = schedule key -> assignment number)."""
    students, system = roster.split_legacy(text, teams, numbers or {})
    return {roster.STUDENTS_PATH: students, roster.SYSTEM_PATH: system}


def parse_roster(
    text: str, teams: str | None = None, numbers: dict | None = None
) -> list[roster.Student]:
    """`roster.parse` over `roster_files(text)`."""
    files = roster_files(text, teams, numbers)
    return roster.parse(files[roster.STUDENTS_PATH], files[roster.SYSTEM_PATH])


def new_sheet(
    spec: grading_sheet.SheetSpec, units: list[tuple[str, list[str]]]
) -> dict:
    """A brand-new sheet: every unit present from the moment of handout, every human field
    blank. A sheet that arrives complete is one a grader can start typing into as soon as
    anything is in, and one whose missing row is visibly missing.

    `units` is `[(unit key, member handles)]`: for an individual assignment the key IS the
    handle and the list holds only it; for a group one the key is the team name."""
    return {
        spec.container_key: {
            unit: grading_sheet._fresh_block(spec, members) for unit, members in units
        }
    }


def split_grading(text: str | None) -> tuple[str | None, str | None]:
    """A test's old-style grading config split where the keys live now: the template's
    `grading_config.yml`, and the run settings as the semester's `assignments.yml`
    `defaults:` (decision 0009)."""
    if text is None:
        return None, None
    data = yaml.safe_load(text) or {}
    run = {k: data.pop(k) for k in settings.RUN_KEYS if k in data}
    template = yaml.safe_dump(data) if data else ""
    return template, (yaml.safe_dump({"defaults": run}) if run else None)


def repo_row(name: str, **extra) -> dict:
    """One row of a `discovery.list_org_repos` listing, carrying every field it really has.

    Three test files kept their own partial builder, each missing a different key, so code
    that reads `archived` or `topics` off a listing was tested against rows that have
    neither. Defaults are the uninteresting answer; `extra` overrides what a test is about.

    Built on `discovery.listing_row`, which is the package's own answer to "what shape is
    a row?" - so a field added there cannot go missing here, which is the whole reason
    this fixture exists. Two differences, both deliberate: `pushed_at` is blank rather
    than the moment of the call (a test that cares says so), and the org in the URL is the
    placeholder every caller of this already expects.
    """
    return discovery.listing_row("org", name) | {"pushed_at": "", **extra}


@pytest.fixture(autouse=True)
def _empty_write_governor():
    """`ghcli`'s write pacer keeps its timestamps at module level, so they would otherwise
    accumulate across the session until an unrelated test slept for a real minute."""
    ghcli._write_times.clear()


@pytest.fixture(autouse=True)
def _team_lag_waits_cost_nothing(monkeypatch):
    """`gh_teams` rides out GitHub's 404 on a just-created team by sleeping, against a
    budget kept at module level: give every test a fresh budget and a sleep that returns
    at once, so a test that stubs a 404 member listing does not wait two real minutes."""
    monkeypatch.setattr(gh_teams, "_lag_spent", 0)
    monkeypatch.setattr(gh_teams, "_sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _no_start_of_run_hooks(monkeypatch):
    """Mark the start-of-run hooks as already run, so a test that parses a CLI's command
    line does not read the API budget (a live `GET /user`). The budget line's own tests
    drive `ghcli.budget_at_start` directly."""
    monkeypatch.setattr(log, "_cli_started", True)


@pytest.fixture(autouse=True)
def _no_live_gh(monkeypatch):
    """Refuse any live `gh` call from a test.

    Nothing here is meant to reach GitHub (see the module docstring), but a tokenless CI
    box and an authenticated dev box disagree about what happens when something does: CI
    errors and the developer's machine quietly succeeds against real orgs. That is how a
    test that stubbed the file listing but not `site._repo_tree` passed locally for a
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
def _no_semester_is_closed_out(monkeypatch):
    """Answer `discovery.semester_is_live`'s probe with "still running" by default.

    Every course-side sweep now asks whether a semester's `semester-config` is archived
    before writing into it, which is a live `gh api repos/<org>/semester-config`. A
    running semester is the uninteresting answer for every test but the ones about the skip
    itself, which set their own after this fixture and win. A semester with no
    `semester-config` at all is asked about its topic too (not migrated); "it is there"
    is the default answer to that as well."""
    monkeypatch.setattr(discovery, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(discovery, "repo_missing", lambda org, name: False)


@pytest.fixture(autouse=True)
def _no_course_or_semester_defaults(monkeypatch):
    """Answer the cascade's live reads (`settings`) with "nothing declared" by default:
    the course's `course.yml`, the semester's `semester.yml`, the run settings
    the settings written on a schedule.yml entry (`schedule.run_settings_of`, which reads
    the parsed plan) and the semester's pointer to its course. Every spec read and every late cutoff resolves through them,
    and the institution's defaults are the uninteresting answer for every test but the ones
    about a layer, which set their own after this fixture and win."""
    monkeypatch.setattr(settings, "org_meta", lambda org: {})
    monkeypatch.setattr(settings, "_semester_text", lambda org: None)
    monkeypatch.setattr(schedule, "run_settings_of", lambda org, slug: {})
    monkeypatch.setattr(settings, "course_org_for_semester", lambda org: "")


@pytest.fixture(autouse=True)
def _not_on_a_runner(monkeypatch):
    """Every test runs as though it were NOT inside GitHub Actions, unless it says so.

    `collect.sandbox_unusable` asks the environment whether it is on a runner and, if it
    is, whether the `dsl-sandbox` account is there to drop student code to. CI *is* a
    runner and has no such account, so without this the whole suite inherited the
    fail-closed answer - graded nothing, and a dozen tests about what `collect` does with a
    semester failed on Linux while passing on a laptop. The memos go with the variable: they
    are answered once per process, so a test that sets `GITHUB_ACTIONS` itself must not
    leave its answer behind for the next one."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    run.reset()


@pytest.fixture(autouse=True)
def _clear_process_memos():
    clear_process_memos()


def clear_process_memos() -> None:
    """Back to the state a process starts in (`run.reset`): a fresh per-run context and
    every read-once memo off. Tests reuse the same org/repo names with different fakes,
    so nothing one test read may answer the next."""
    run.reset()


def fake_config(monkeypatch, files) -> None:
    """Answer `config.read` - the one door to every hand-edited config file - from
    `files(org, repo, path)`: a mapping (written out as YAML), the file's text, or None
    for a file that is not there."""

    def read(org: str, repo: str, path: str) -> config.ConfigFile:
        found = files(org, repo, path)
        text = (
            found if found is None or isinstance(found, str) else yaml.safe_dump(found)
        )
        return config.ConfigFile(org, repo, path, text)

    monkeypatch.setattr(config, "read", read)


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
        "setup_semester_extras",
        "seed_workflows",
        "create_profile_repo",
    ):
        monkeypatch.setattr(bc, name, lambda *a, **k: 0)
    monkeypatch.setattr(bc, "preflight", lambda org, semester=False: True)
    monkeypatch.setattr(bc, "add_course_admins", lambda org, handles: 0)
    monkeypatch.setattr(bc, "validate_secret_presence", lambda org, secret: True)
    monkeypatch.setattr(bc, "put_file", lambda *a, **k: True)
    monkeypatch.setattr(bc, "register_semester", lambda course, semester: True)
    monkeypatch.setattr(bc, "update_profile_readme", lambda *a, **k: 0)
    monkeypatch.setattr(bc.sync_faculty, "sync", lambda course, semesters=None: 0)
    # The org's tier is read off its (not yet written) course.yml; a bootstrap test is
    # about what the run does, not which ref it seeds at.
    monkeypatch.setattr(bc, "central_ref_for", lambda org: "release")


# What the `gh issue create` in `GhFake` prints.
CREATED_ISSUE_URL = "https://github.com/Semester/semester-config/issues/12"


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
# merge onto `upstream` (`test_release_merge`) and the propagate back out of a semester
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
        leaves ahead of it (`deploy.UPSTREAM_BRANCH`), which is a real state a semester repo
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
    return (trigger["workflow_dispatch"] or {}).get("inputs") or {}


def workflow_jobs(rendered: str) -> dict:
    return yaml.safe_load(rendered)["jobs"]


def entry_link_rows(rendered: str) -> list[dict]:
    """Every link of a rendered session entry's `links:` block, whole - each a mapping of
    whatever fields the emitter wrote (`url`, `name`, `section`).

    Parsed rather than substring-matched: a link is four fields now, so an assertion
    written against the rendered bytes would be asserting the emitter's line order as much
    as its content."""
    front = rendered.split("---\n")[1]
    return yaml.safe_load(front).get("links") or []


def entry_links(rendered: str) -> list[tuple[str, str]]:
    """The (section, name) pairs of the same block - the view most assertions want."""
    return [(l["section"], l["name"]) for l in entry_link_rows(rendered)]


def run_settings(monkeypatch, read) -> None:
    """Stub a semester's run settings from `read(org)`, the text of an `assignments.yml`
    as it was written before decisions 0038/0039: its `defaults:` become `semester.yml`'s,
    and each `assignments.<key>` block becomes the settings written on that key's
    schedule.yml entry (`settings.inline_settings`). `visibility` is spelt
    `assignment_repo_visibility` in both. Text that is not YAML is `semester.yml` as it
    stands."""

    def split(org):
        text = read(org)
        try:
            data = yaml.safe_load(text) if text else None
        except yaml.YAMLError:
            return text, None
        if not isinstance(data, dict):
            return text, None
        blocks = data.pop("assignments", None)
        if isinstance(data.get("defaults"), dict):
            data["defaults"] = {
                settings.INLINE_NAMES.get(k, k): v for k, v in data["defaults"].items()
            }
        defaults = yaml.safe_dump(data, sort_keys=False) if data else None
        if not isinstance(blocks, dict):
            return defaults, None
        inline = {
            key: {settings.INLINE_NAMES.get(k, k): v for k, v in (block or {}).items()}
            for key, block in blocks.items()
        }
        return defaults, yaml.safe_dump({"assignments": inline}, sort_keys=False)

    def inline(org, slug):
        text = split(org)[1]
        blocks = (yaml.safe_load(text) or {}).get("assignments") if text else None
        return settings.inline_blocks(blocks).get(slug, {})

    monkeypatch.setattr(settings, "_semester_text", lambda org: split(org)[0])
    # The entry's own settings, as the parsed plan carries them (`AssignmentEntry.settings`).
    monkeypatch.setattr(schedule, "run_settings_of", inline)
