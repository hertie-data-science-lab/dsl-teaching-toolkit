"""Carrying a cohort's edits back into the course org as a pull request.

Against real repositories, for the reason `test_release_merge` gives: what this is about
is what ends up on a branch and in a commit history, which a stubbed `git` can only
assert back at itself. Only `gh` is faked (into a local `git clone`), plus `pulls` and the
cohort's parsed schedule.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dsl_course import ghcli, propagate
from dsl_course.schedule import Deploy, Release, Schedule
from tests.conftest import BareOrigins, PullsFake

COURSE = "Course-Org"
COHORT = "Cohort-Org"
BRANCH = "from-Cohort-Org"
PR_URL = "https://github.com/Course-Org/cm/pull/1"

FIRED = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class World(BareOrigins):
    """A course org and a cohort org as bare repositories on disk, plus the plan the
    cohort is running. Everything under it - the origins, the throwaway-clone commits (a
    `None` value deletes a path, which is the state this module deliberately does not
    carry), the reads afterwards - is `conftest.BareOrigins`, shared with
    `test_release_merge`."""

    def __init__(self, root, fake: PullsFake) -> None:
        super().__init__(root)
        self.pulls = fake
        self.releases: list[Release] = []

    def plan(self, *releases: Release) -> None:
        self.releases = list(releases)

    def run(self, now: datetime = NOW, dry_run: bool = False) -> propagate.Propagated:
        return propagate.propagate(COURSE, COHORT, now, dry_run=dry_run)

    def branches(self, name: str) -> list[str]:
        return self.refs(name)

    def proposed(self, name: str = "cm", branch: str = BRANCH) -> list[str]:
        """The commit subjects this branch adds on top of `main`, oldest first."""
        return self.subjects(name, f"main..{branch}")

    def read(self, path: str, name: str = "cm", branch: str = BRANCH) -> str:
        return self.show(name, branch, path)

    def files(self, name: str = "cm", branch: str = BRANCH) -> list[str]:
        return self.tree(name, branch)


@pytest.fixture
def world(tmp_path, monkeypatch) -> World:
    fake = PullsFake(PR_URL)
    built = World(tmp_path, fake)

    monkeypatch.setattr(ghcli, "gh", built.clone_only)
    monkeypatch.setattr(propagate, "pulls", fake)
    monkeypatch.setattr(
        propagate.schedule, "load", lambda org: Schedule(releases=built.releases)
    )
    return built


def _release(label: str, when: datetime, *deploys: Deploy) -> Release:
    return Release(label, when, deploy=list(deploys))


# ----------------------------------------------------------------- what is carried


def test_a_cohort_edit_is_proposed_back_as_one_commit_and_one_pull_request(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"lectures/01/lab.md": "week one, corrected"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    done = world.run()
    assert (done.errors, done.urls) == (0, (PR_URL,))
    assert world.read("lectures/01/lab.md") == "week one, corrected"
    assert world.proposed() == ["propagate: lectures/01 from Cohort-Org"]
    (call,) = world.pulls.calls
    assert call["repo"] == "Course-Org/cm"
    assert (call["head"], call["base"]) == (BRANCH, "main")
    assert call["title"] == "Cohort edits from Cohort-Org"
    assert call["refresh_body"] is True
    # `main` is untouched: this PROPOSES, it never writes to the branch faculty read.
    assert world.read("lectures/01/lab.md", branch="main") == "week one"


def test_a_second_run_regenerates_the_branch_and_reuses_the_pull_request(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"lectures/01/lab.md": "corrected"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))
    assert world.run().errors == 0

    world.commit("materials", {"lectures/01/lab.md": "corrected again"})
    assert world.run().errors == 0
    # One commit, not two: the branch is cut fresh from main every run, so it proposes
    # what the cohort has NOW rather than accumulating every version of it.
    assert world.proposed() == ["propagate: lectures/01 from Cohort-Org"]
    assert world.read("lectures/01/lab.md") == "corrected again"
    assert len(world.pulls.calls) == 2  # the same head branch - `upsert_pr` adopts it


def test_a_deploy_that_has_not_fired_yet_is_not_propagated(world):
    world.commit("cm", {"lectures/02/lab.md": "week two"})
    world.commit("materials", {"lectures/02/lab.md": "edited early"})
    world.plan(_release("lecture-2", LATER, Deploy("cm", "lectures/02", "materials")))

    assert world.run().errors == 0
    assert world.pulls.calls == []
    assert world.branches("cm") == ["main"]


def test_a_cohort_that_changed_nothing_opens_nothing(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"lectures/01/lab.md": "week one"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    assert world.run().errors == 0
    assert world.pulls.calls == []
    assert world.branches("cm") == ["main"]


def test_a_path_the_cohort_does_not_have_is_a_note_not_a_failure(world, capsys):
    # A plan entry can be edited after it fired, and a cohort repo is faculty's to
    # reorganise - neither is a broken run.
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"README.md": "the cohort"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    assert world.run().errors == 0
    assert world.pulls.calls == []
    assert "[note]" in capsys.readouterr().out


# ------------------------------------------------------------------ what is NOT carried


def test_a_deletion_is_named_in_the_body_and_never_made(world):
    world.commit(
        "cm", {"lectures/01/lab.md": "week one", "lectures/01/notes.md": "notes"}
    )
    world.commit(
        "materials", {"lectures/01/lab.md": "week one", "lectures/01/notes.md": "notes"}
    )
    world.commit("materials", {"lectures/01/notes.md": None}, "the cohort drops notes")
    world.commit("materials", {"lectures/01/lab.md": "corrected"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    assert world.run().errors == 0
    assert "lectures/01/notes.md" in world.files()
    (call,) = world.pulls.calls
    assert "Deletions are not propagated" in call["body"]
    assert "`lectures/01/notes.md`" in call["body"]


def test_a_cohort_behind_its_latest_release_is_not_carried_back(world, capsys):
    # `upstream` ahead of the branch students read is a release held at a conflict pull
    # request. The cohort's copy is missing the course org's own newer content, so
    # carrying it back would propose that content as a cohort edit and revert the fix on
    # merge - which is what the teardown would do, propagating before it seals.
    world.commit(
        "cm", {"lectures/01/lab.md": "the course fix", "labs/01/lab.md": "lab one"}
    )
    world.commit("materials", {"lectures/01/lab.md": "week one"})
    world.commit(
        "materials", {"lectures/01/lab.md": "the course fix"}, branch="upstream"
    )
    world.commit("extras", {"labs/01/lab.md": "the cohort's fix"})
    world.plan(
        _release(
            "lecture-1",
            FIRED,
            Deploy("cm", "lectures/01", "materials"),
            Deploy("cm", "labs/01", "extras"),
        )
    )

    assert world.run().errors == 0
    # Nothing off the stale dest; every other dest is carried exactly as usual.
    assert world.proposed() == ["propagate: labs/01 from Cohort-Org"]
    # The course's own newer content, not the cohort's stale copy of what preceded it.
    assert world.read("lectures/01/lab.md") == "the course fix"
    (call,) = world.pulls.calls
    assert "behind their latest release" in call["body"]
    assert "`materials/lectures/01`" in call["body"]
    assert "[skip]" in capsys.readouterr().out


def test_a_cohort_with_no_upstream_branch_is_carried(world):
    # A cohort released into before releases were merge-based has no `upstream` at all:
    # there is no release for it to be behind, so it is read like any other dest.
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"lectures/01/lab.md": "corrected"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    assert world.run().errors == 0
    assert world.branches("materials") == ["main"]
    assert world.proposed() == ["propagate: lectures/01 from Cohort-Org"]


def test_a_whole_repo_carry_leaves_the_root_excluded_paths_alone(world):
    # The root-only half of the release filter, run in reverse. A cohort repo's `.github`
    # is the toolkit's own rendered workflows, so carrying it back would push a cohort's
    # workflows over the COURSE org's - and the course's own root files would be reported
    # as paths the cohort had deleted. The comparison that decides "is this the whole
    # repo?" is between resolved paths, so a temp dir behind a symlink must not break it.
    world.commit(
        "cm",
        {".github/workflows/release.yml": "course", "MAINTAINING.md": "the course org"},
    )
    world.commit(
        "materials",
        {".github/workflows/release.yml": "cohort", "lectures/01/lab.md": "corrected"},
    )
    world.plan(_release("everything", FIRED, Deploy("cm", "/", "materials")))

    assert world.run().errors == 0
    assert world.read(".github/workflows/release.yml") == "course"
    assert world.read("lectures/01/lab.md") == "corrected"
    (call,) = world.pulls.calls
    assert ".github" not in call["body"] and "MAINTAINING.md" not in call["body"]


def test_the_body_names_paths_and_says_the_branch_is_regenerated(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("materials", {"lectures/01/lab.md": "corrected"})
    world.plan(_release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")))

    assert world.run().errors == 0
    body = world.pulls.calls[0]["body"]
    assert "`materials/lectures/01` -> `lectures/01`" in body
    assert "force-pushed on every run" in body
    assert "cherry-pick" in body


# --------------------------------------------------------------------- shape of a run


def test_each_source_repo_gets_its_own_pull_request(world):
    world.commit("cm", {"lectures/01/lab.md": "week one"})
    world.commit("labs", {"labs/01/lab.md": "lab one"})
    world.commit(
        "materials", {"lectures/01/lab.md": "corrected", "labs/01/lab.md": "fixed"}
    )
    world.plan(
        _release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials")),
        _release("lab-1", FIRED, Deploy("labs", "labs/01", "materials")),
    )

    assert world.run().errors == 0
    assert sorted(c["repo"] for c in world.pulls.calls) == [
        "Course-Org/cm",
        "Course-Org/labs",
    ]


def test_one_commit_per_released_path_in_session_order(world):
    world.commit(
        "cm",
        {
            "lectures/01/lab.md": "week one",
            "lectures/02/lab.md": "week two",
            "lectures/03/lab.md": "week three",
        },
    )
    world.commit(
        "materials",
        {
            "lectures/01/lab.md": "one, fixed",
            "lectures/02/lab.md": "two, fixed",
            "lectures/03/lab.md": "three, fixed",
        },
    )
    world.plan(
        _release(
            "lecture-3",
            datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
            Deploy("cm", "lectures/03", "materials"),
        ),
        _release(
            "lecture-1",
            datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc),
            Deploy("cm", "lectures/01", "materials"),
            Deploy("cm", "lectures/02", "materials"),
        ),
    )

    assert world.run().errors == 0
    # The plan's own order, entry by entry and deploy by deploy inside an entry - so the
    # branch reads the way the term ran rather than alphabetically.
    assert world.proposed() == [
        "propagate: lectures/03 from Cohort-Org",
        "propagate: lectures/01 from Cohort-Org",
        "propagate: lectures/02 from Cohort-Org",
    ]


def test_a_dry_run_clones_nothing_and_prints_the_pairs(world, capsys):
    world.plan(
        _release("lecture-1", FIRED, Deploy("cm", "lectures/01", "materials", "wk01"))
    )

    assert world.run(dry_run=True).errors == 0
    out = capsys.readouterr().out
    assert "Cohort-Org/materials/wk01 -> Course-Org/cm/lectures/01" in out
    assert world.pulls.calls == []
    assert not (world.origins / "cm.git").exists()


def test_a_cohort_with_nothing_released_yet_does_nothing(world):
    world.plan()
    assert world.run().errors == 0
    assert world.pulls.calls == []
