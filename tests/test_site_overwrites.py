"""The sync tells a human when it overwrote them.

The incident: an instructor edited a generated file in the site repo by hand and the next
sync replaced it, silently. `sync_site_repo` now looks at who last touched each path the
sync commit rewrote and files an issue in the site repo naming the discarded commit.

Two invariants dominate: a machine author (the previous sync, the token account, an App)
must NEVER be mistaken for a person, and no failure in the notice may change the sync's
exit code - the site is already regenerated and pushed by then.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsl_course import ghcli, site_repo

ORG = "Cohort-f2026"
SITE = "cohort-f2026.github.io"
HUMAN = ("a1b2c3d4e5f6", "Jan Instructor", "jan@uni.example")
BOT = ("bbbbbbbbbbbb", "dsl-bot", "bot@dsl.local")
TOKEN_ACCOUNT = ("cccccccccccc", "DSL-Bot-Account", "noreply@github.com")
APP = ("dddddddddddd", "github-actions[bot]", "actions@github.com")


class _Fakes:
    """gh/git doubles for one `sync_site_repo` run, plus the calls they recorded."""

    def __init__(self, changed, last_touch, *, commit_rc=0, push_rc=0, replies=None):
        self.changed = changed
        self.last_touch = last_touch
        self.commit_rc = commit_rc
        self.push_rc = push_rc
        self.replies = replies or {}
        self.gh_calls: list[tuple[str, ...]] = []
        self.git_calls: list[tuple[str, ...]] = []

    def git(self, *args):
        self.git_calls.append(args)
        if "add" in args:
            return (0, "")
        if "commit" in args:
            return (self.commit_rc, "")
        if "push" in args:
            return (self.push_rc, "")
        if "show" in args:
            # `--format=` leaves a leading blank line - the parser must survive it.
            return (0, "\n" + "\n".join(self.changed))
        if "log" in args:
            row = self.last_touch.get(args[-1])
            return (0, "\t".join(row)) if row else (0, "")
        return (0, "")

    def gh(self, *args, **kwargs):
        self.gh_calls.append(args)
        if args[:2] == ("repo", "clone"):
            Path(args[3]).mkdir(parents=True, exist_ok=True)
            return (0, "")
        if args[0] == "api":
            return self.replies.get("login", (0, "jan-gh"))
        if args[:2] == ("issue", "list"):
            return self.replies.get("list", (0, ""))
        if args[:2] == ("issue", "create"):
            return self.replies.get("create", (0, "https://github.com/i/1"))
        if args[:2] == ("issue", "comment"):
            return self.replies.get("comment", (0, ""))
        return (0, "")

    def issue_calls(self) -> list[tuple[str, ...]]:
        return [c for c in self.gh_calls if c[0] == "issue"]

    def created_body(self) -> str:
        create = next(c for c in self.gh_calls if c[:2] == ("issue", "create"))
        return create[create.index("--body") + 1]

    def commented_body(self) -> str:
        comment = next(c for c in self.gh_calls if c[:2] == ("issue", "comment"))
        return comment[comment.index("--body") + 1]


def _run(monkeypatch, fakes: _Fakes) -> int:
    monkeypatch.setattr(site_repo, "gh", fakes.gh)
    monkeypatch.setattr(ghcli, "gh", fakes.gh)
    monkeypatch.setattr(site_repo, "git", fakes.git)
    monkeypatch.setattr(site_repo, "repo_exists", lambda org, name: True)
    monkeypatch.setattr(site_repo, "repo_is_archived", lambda org, name: False)
    monkeypatch.setattr(site_repo, "_acting_login", lambda: "dsl-bot-account")

    def build(_wd: Path) -> site_repo.SitePlan:
        return site_repo.SitePlan(config={}, collections={}, commit="site: sync")

    return site_repo.sync_site_repo(ORG, build)


@pytest.fixture
def human_edit(monkeypatch):
    """One rewritten file whose previous commit was Jan's."""
    fakes = _Fakes(["_data/people.yml"], {"_data/people.yml": HUMAN})
    return fakes, lambda: _run(monkeypatch, fakes)


def test_an_overwritten_human_edit_files_an_issue(human_edit):
    fakes, run = human_edit
    assert run() == 0  # the site is published; the notice is extra
    body = fakes.created_body()
    assert f"https://github.com/{ORG}/{SITE}/commit/{HUMAN[0]}" in body
    assert "@jan-gh" in body  # pinged, so he learns where to edit instead
    assert "`_data/people.yml`" in body
    assert "classroom-config/people.yml" in body  # where the edit belongs
    create = next(c for c in fakes.gh_calls if c[:2] == ("issue", "create"))
    assert create[create.index("--repo") + 1] == f"{ORG}/{SITE}"
    assert create[create.index("--title") + 1] == site_repo.OVERWRITE_ISSUE_TITLE


@pytest.mark.parametrize("author", [BOT, TOKEN_ACCOUNT, APP])
def test_machine_authors_are_never_notified_about(monkeypatch, author):
    # The previous sync, the token account behind API-made commits (the scaffold's
    # "Initial commit"), and any App. Each rewrite of a file the engine itself wrote is
    # the normal case - an issue for it would be noise on every single run.
    fakes = _Fakes(
        ["_data/people.yml", "_events/final.md"],
        dict.fromkeys(["_data/people.yml", "_events/final.md"], author),
    )
    assert _run(monkeypatch, fakes) == 0
    assert fakes.issue_calls() == []


def test_a_file_this_sync_created_is_nobodys_edit(monkeypatch):
    # No commit before HEAD touched it, so `git log HEAD^` answers with nothing.
    fakes = _Fakes(["_lectures/session-01.md"], {})
    assert _run(monkeypatch, fakes) == 0
    assert fakes.issue_calls() == []


def test_an_open_issue_gets_a_comment_not_a_duplicate(human_edit):
    fakes, run = human_edit
    fakes.replies["list"] = (0, "7\n")
    assert run() == 0
    assert not [c for c in fakes.gh_calls if c[:2] == ("issue", "create")]
    comment = next(c for c in fakes.gh_calls if c[:2] == ("issue", "comment"))
    assert comment[2] == "7"
    assert HUMAN[0][:7] in fakes.commented_body()


def test_an_unreadable_issue_list_still_notifies(human_edit):
    # Filing a duplicate beats telling nobody their work was discarded.
    fakes, run = human_edit
    fakes.replies["list"] = (1, "gh: HTTP 502")
    assert run() == 0
    assert fakes.created_body()


def test_a_failed_issue_creation_is_loud_but_never_fails_the_sync(human_edit, capsys):
    fakes, run = human_edit
    fakes.replies["create"] = (1, "gh: HTTP 403 Forbidden")
    assert run() == 0  # the site IS published - a courtesy must not invent a red cron
    err = capsys.readouterr().err
    assert "could not notify" in err and "403" in err


def test_a_raising_detection_is_loud_but_never_fails_the_sync(monkeypatch, capsys):
    fakes = _Fakes(["_data/people.yml"], {"_data/people.yml": HUMAN})
    monkeypatch.setattr(
        site_repo,
        "_overwritten_edits",
        lambda wd: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    assert _run(monkeypatch, fakes) == 0
    assert "overwritten manual edits" in capsys.readouterr().err


def test_an_unresolvable_login_falls_back_to_the_git_author_name(human_edit):
    # Jan's case exactly: his git email is linked to no GitHub account, so the commits
    # API answers `null` and there is nobody to @-mention.
    fakes, run = human_edit
    fakes.replies["login"] = (0, "null")
    assert run() == 0
    body = fakes.created_body()
    assert "`Jan Instructor`" in body
    assert "@" not in body.split("Nothing is lost")[0]
    # Nobody to ping means nobody is emailed, so the org's instructors are cc'd instead.
    assert f"cc @{ORG}/instructors" in body


def test_a_mentioned_author_is_not_backed_by_a_team_ping(human_edit):
    # The direct ping already emails Jan; adding the team would spam every instructor who
    # never touched the file.
    fakes, run = human_edit
    assert run() == 0
    assert f"{ORG}/instructors" not in fakes.created_body()


def test_an_up_to_date_run_inspects_nothing(monkeypatch):
    # Nothing was committed, so nothing was overwritten - no history walk, no gh calls.
    fakes = _Fakes(["_data/people.yml"], {"_data/people.yml": HUMAN}, commit_rc=1)
    assert _run(monkeypatch, fakes) == 0
    assert fakes.issue_calls() == []
    assert not [c for c in fakes.git_calls if "show" in c or "log" in c]


def test_a_failed_push_overwrote_nothing_on_the_remote(monkeypatch):
    fakes = _Fakes(["_data/people.yml"], {"_data/people.yml": HUMAN}, push_rc=1)
    assert _run(monkeypatch, fakes) == 1
    assert fakes.issue_calls() == []


def test_the_machine_identity_is_read_off_git_env():
    # A rename of the bot identity in ghcli.GIT_ENV must not silently turn every sync
    # commit into a "human edit" here.
    assert site_repo._git_identity("user.name") == "dsl-bot"
    assert site_repo._git_identity("user.email") == "bot@dsl.local"
