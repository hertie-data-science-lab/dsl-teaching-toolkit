"""A release lands on `upstream` and is MERGED into the branch students read.

Against real repositories - a bare origin per repo, cloned exactly as `deploy_many` clones
one - because every property here is a fact about git that a stubbed `git` can only assert
back at itself: that a cohort's own edit survives the next release, that a conflict leaves
the branch students read exactly as they last saw it, and that a dest released into before
any of this existed grows an `upstream` cut from what it already had.

Only `gh` is faked (into a local `git clone`), plus the three org-level calls a release
makes around the copy. The commits, branches, merges and pushes are git's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dsl_course import deploy, ghcli, pulls
from dsl_course.schedule import Deploy

PR_URL = "https://github.com/Cohort-Org/materials/pull/1"

# The fixtures' own commits stand in for a person's; they borrow the engine's identity and
# its disabled hooks, so a developer's global git hooks cannot fail them.
_ID = ghcli.GIT_ENV


def _git(*args: str) -> str:
    code, out = ghcli.git(*args)
    assert code == 0, f"`git {' '.join(args)}` failed: {out}"
    return out


class PullsFake:
    """The `pulls` module as `deploy` sees it, recording what it was asked to upsert.

    `upsert_pr` is idempotent by head branch - that is `test_pulls`' subject, and asserting
    it again here would only re-test the fake. What this one is for is WHAT the release
    asks for, and how often."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert_pr(self, repo: str, **kwargs) -> pulls.Upserted:
        self.calls.append({"repo": repo, **kwargs})
        return pulls.Upserted(0, PR_URL)


class World:
    """A course org and a cohort org as bare repositories on disk."""

    def __init__(self, root: Path, pulls_fake: PullsFake) -> None:
        self.root = root
        self.origins = root / "origins"
        self.origins.mkdir(parents=True, exist_ok=True)
        self.pulls = pulls_fake
        self._scratch = 0

    def bare(self, name: str) -> Path:
        path = self.origins / f"{name}.git"
        if not path.exists():
            _git("init", "-q", "--bare", "-b", "main", str(path))
        return path

    def commit(self, name: str, files: dict[str, str], message: str = "edit") -> None:
        """Put one commit on `main` of a bare repo, through a throwaway clone - the only
        way to write into a repo with no working tree."""
        self._scratch += 1
        work = self.root / "scratch" / f"{name}{self._scratch}"
        _git("clone", "-q", str(self.bare(name)), str(work))
        for rel, text in files.items():
            path = work / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        _git("-C", str(work), "add", "-A")
        _git("-C", str(work), *_ID, "commit", "-q", "--no-verify", "-m", message)
        _git("-C", str(work), "push", "-q", "origin", "HEAD:refs/heads/main")

    def release(self, *paths: str) -> tuple[int, bool]:
        return deploy.deploy_many(
            "Course-Org",
            "Cohort-Org",
            [Deploy("cm", p, "materials", None) for p in paths],
            sync=False,
        )

    def branches(self, name: str = "materials") -> list[str]:
        listed = _git(
            "--git-dir",
            str(self.bare(name)),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        )
        return sorted(listed.split())

    def sha(self, branch: str, name: str = "materials") -> str:
        return _git("--git-dir", str(self.bare(name)), "rev-parse", branch)

    def read(self, branch: str, path: str, name: str = "materials") -> str:
        return _git("--git-dir", str(self.bare(name)), "show", f"{branch}:{path}")

    def files(self, branch: str, name: str = "materials") -> list[str]:
        listed = _git(
            "--git-dir", str(self.bare(name)), "ls-tree", "-r", "--name-only", branch
        )
        return sorted(listed.split())


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    fake = PullsFake()
    built = World(tmp_path, fake)

    def clone_only(*args, **kwargs):
        if args[:2] == ("repo", "clone"):
            name = args[2].split("/", 1)[1]
            return ghcli.git("clone", "-q", str(built.bare(name)), str(args[3]))
        return 0, ""

    monkeypatch.setattr(ghcli, "gh", clone_only)
    monkeypatch.setattr(deploy, "create_repo", lambda *a, **k: True)
    monkeypatch.setattr(deploy, "default_branch", lambda *a, **k: "main")
    monkeypatch.setattr(deploy, "grant_read_teams", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "grant_faculty", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "pulls", fake)
    return built


# ------------------------------------------------------------------ the ordinary release


def test_a_release_lands_on_upstream_and_the_read_branch_follows_it(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"README.md": "the cohort"})

    assert world.release("lectures/01") == (0, True)

    assert world.branches() == ["main", "upstream"]
    # Nothing had diverged, so the merge is a fast-forward: what students read IS what was
    # released, commit for commit.
    assert world.sha("main") == world.sha("upstream")
    assert world.read("main", "lectures/01/lab.md") == "week one"
    assert world.read("main", "README.md") == "the cohort"


def test_a_second_release_with_nothing_new_moves_nothing(world, capsys):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"README.md": "the cohort"})
    world.release("lectures/01")
    before = world.sha("main")

    assert world.release("lectures/01") == (0, False)
    assert world.sha("main") == before
    assert "nothing new to release" in capsys.readouterr().out


def test_a_cohort_edit_survives_the_next_release(world):
    # The whole point of the merge. The copy used to land on the branch students read, so
    # an instructor's correction was reverted by the next tick of the release cron.
    world.commit("cm", {"lectures/01/lab.md": "week one", "lectures/02/lab.md": "two"})
    world.commit("materials", {"README.md": "the cohort"})
    world.release("lectures/01")
    world.commit("materials", {"NOTES.md": "read this first"}, "an instructor's note")

    assert world.release("lectures/02") == (0, True)
    assert world.read("main", "NOTES.md") == "read this first"
    assert world.read("main", "lectures/02/lab.md") == "two"


# ------------------------------------------------------------------------ the conflict


def _conflict(world) -> tuple[int, bool]:
    """A release whose source and dest have both changed the same line."""
    world.commit("cm", {"lectures/01/lab.md": "version one\n"})
    world.commit("materials", {"README.md": "the cohort"})
    world.release("lectures/01")
    world.commit("materials", {"lectures/01/lab.md": "the cohort's fix\n"}, "fix")
    world.commit("cm", {"lectures/01/lab.md": "version two\n"}, "rewrite")
    return world.release("lectures/01")


def test_a_conflict_leaves_the_branch_students_read_untouched(world, capsys):
    assert _conflict(world) == (0, False)
    # What students read is exactly what the cohort last put there...
    assert world.read("main", "lectures/01/lab.md") == "the cohort's fix"
    # ...and the release is not lost: it is on `upstream`, waiting to be merged.
    assert world.read("upstream", "lectures/01/lab.md") == "version two"
    assert "held for review" in capsys.readouterr().out


def test_a_conflict_asks_the_instructors_to_decide(world):
    _conflict(world)
    (call,) = world.pulls.calls
    assert call["repo"] == "Cohort-Org/materials"
    assert (call["head"], call["base"]) == (deploy.UPSTREAM_BRANCH, "main")
    assert call["reviewer"] == "Cohort-Org/instructors"


def test_the_pull_request_body_carries_paths_and_nothing_about_people(world):
    # This repo is readable by the whole cohort. The diff says who changed what, in the
    # one place GitHub already shows it; the body says which paths are in the branch.
    _conflict(world)
    body = world.pulls.calls[0]["body"]
    assert "lectures/01" in body
    assert "@" not in body


def test_a_re_run_re_offers_the_same_merge_rather_than_a_new_one(world):
    _conflict(world)
    held = world.sha("main")

    # Nothing new to copy this time, but `upstream` is still ahead - so the merge is
    # offered again, through the same head branch, which is what makes `pulls` adopt the
    # pull request that is already open instead of filing a second one.
    assert world.release("lectures/01") == (0, False)
    assert world.sha("main") == held
    assert [c["head"] for c in world.pulls.calls] == ["upstream", "upstream"]


def test_resolving_the_conflict_by_hand_ends_the_holding_pattern(world):
    _conflict(world)
    # What an instructor does on GitHub: take a decision about the two versions.
    world.commit("materials", {"lectures/01/lab.md": "version two\n"}, "resolved")

    # The next release merges cleanly and the cohort is reading the released copy again.
    assert world.release("lectures/01") == (0, True)
    assert world.read("main", "lectures/01/lab.md") == "version two"
    assert len(world.pulls.calls) == 1  # no second conflict, no second pull request


# ------------------------------------------------- dests that have not seen this before


def test_a_dest_released_into_before_upstream_existed_gets_one_cut_from_it(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"README.md": "released last term"})
    assert world.branches() == ["main"]

    assert world.release("lectures/01") == (0, True)
    assert world.branches() == ["main", "upstream"]
    # Cut from what the cohort already had, so the first merge is a fast-forward of just
    # this release rather than a conflict against every file already there.
    assert world.read("upstream", "README.md") == "released last term"


def test_an_empty_dest_gets_both_branches(world):
    # The repo `create_repo` just made: no commits, no branches, no HEAD to read.
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.bare("materials")

    assert world.release("lectures/01") == (0, True)
    assert world.branches() == ["main", "upstream"]
    assert world.sha("main") == world.sha("upstream")
    assert world.files("main") == ["lectures/01/lab.md"]
