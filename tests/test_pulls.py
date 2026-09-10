"""pulls: find-by-head-branch, then create-or-adopt.

A PR is addressed by the branch it proposes, because that is the thing the caller owns.
The properties under test are the ones that keep a quarter-hourly release from becoming a
wall of notifications: one PR per head branch however often the run repeats, a listing
that could not be read never reads as "no PR", and the two edits that ride alongside -
the review request and the body refresh - are both optional and neither can lose the PR.
"""

from __future__ import annotations

import pytest

from dsl_course import pulls

REPO = "Cohort-f2026/materials"
HEAD = "upstream"
CREATED_URL = "https://github.com/Cohort-f2026/materials/pull/7"


def _row(number: int, head: str, fork: bool = False, base: str = "main") -> dict:
    return {
        "number": number,
        "url": f"https://github.com/{REPO}/pull/{number}",
        "baseRefName": base,
        "headRefName": head,
        "isCrossRepository": fork,
    }


class PrFake:
    """A recording fake for the two `ghcli` entry points `pulls` uses - `gh` for the
    writes, `gh_json` for the listing, which is where a failed read arrives as the
    exception `gh_json` raises."""

    def __init__(self, rows=None, list_code=0, write_code=0, edit_code=0):
        self.rows = rows or []
        self.list_code = list_code
        self.write_code = write_code
        self.edit_code = edit_code
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("pr", "create"):
            return (self.write_code, "boom" if self.write_code else f"{CREATED_URL}\n")
        if args[:2] == ("pr", "edit"):
            return (self.edit_code, "boom" if self.edit_code else "")
        return 0, ""

    def json(self, *args, **kwargs):
        self.calls.append(args)
        if self.list_code != 0:
            raise RuntimeError(f"`gh pr list` failed (exit {self.list_code}): boom")
        return self.rows

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]


@pytest.fixture
def gh_pr(monkeypatch):
    def _make(rows=None, list_code=0, write_code=0, edit_code=0) -> PrFake:
        fake = PrFake(rows, list_code, write_code, edit_code)
        monkeypatch.setattr(pulls, "gh", fake)
        monkeypatch.setattr(pulls, "gh_json", fake.json)
        return fake

    return _make


def _upsert(**kwargs) -> pulls.Upserted:
    return pulls.upsert_pr(
        REPO,
        head=HEAD,
        base="main",
        title="Release held for review",
        body="materials/week02",
        **kwargs,
    )


# ------------------------------------------------------------- one PR per head branch


def test_an_open_pr_from_the_same_branch_is_adopted_not_duplicated(gh_pr):
    # The release that held its merge back runs again every quarter of an hour until
    # somebody merges or closes the PR. A second one per tick is the failure this exists
    # to prevent.
    fake = gh_pr([_row(4, HEAD)])
    assert _upsert() == pulls.Upserted(0, f"https://github.com/{REPO}/pull/4")
    assert fake.did("pr", "create") == []


def test_a_pr_from_a_different_branch_is_not_adopted(gh_pr):
    # `gh pr list --head` filters server-side, but the listing also answers for a fork
    # whose branch is named like ours - adopting one of those edits a student's PR.
    fake = gh_pr([_row(4, "student-fix"), _row(5, "upstream-notes")])
    assert _upsert().url == CREATED_URL
    assert len(fake.did("pr", "create")) == 1


def test_a_pr_from_a_fork_is_not_adopted_however_its_branch_is_named(gh_pr):
    # `headRefName` carries no owner, and this toolkit tells every student to fork the
    # materials repo - so a fork's branch of the same name is in the listing too.
    # Adopting one would edit a student's PR and leave the release's own question unasked.
    fake = gh_pr([_row(4, HEAD, fork=True)])
    assert pulls.find_pr(REPO, HEAD) is None
    assert _upsert().url == CREATED_URL
    assert len(fake.did("pr", "create")) == 1


def test_the_lookup_asks_for_the_field_that_tells_a_fork_apart(gh_pr):
    fake = gh_pr([])
    assert pulls.find_pr(REPO, HEAD) is None
    (args,) = fake.did("pr", "list")
    assert "isCrossRepository" in args[args.index("--json") + 1].split(",")


def test_a_pr_someone_retargeted_is_left_exactly_as_they_left_it(gh_pr, capsys):
    # Same head branch, another base: a human pointed this pull request somewhere else,
    # and adopting it would edit their pull request and merge the release into a branch
    # nobody asked for. Nothing new is opened either - a second open pull request from one
    # head branch is what GitHub refuses anyway - so the run names it and moves on.
    fake = gh_pr([_row(4, HEAD, base="release")])
    assert _upsert(refresh_body=True) == pulls.Upserted(
        0, f"https://github.com/{REPO}/pull/4"
    )
    assert fake.did("pr", "create") == []
    assert fake.did("pr", "edit") == []
    warned = capsys.readouterr().out
    assert "#4" in warned and "`release`" in warned


def test_the_lookup_asks_for_the_base_the_pr_proposes(gh_pr):
    fake = gh_pr([])
    assert pulls.find_pr(REPO, HEAD) is None
    (args,) = fake.did("pr", "list")
    assert "baseRefName" in args[args.index("--json") + 1].split(",")


def test_the_lowest_numbered_open_pr_wins(gh_pr):
    # A duplicate opened during an outage must not change which PR the next run edits.
    gh_pr([_row(9, HEAD), _row(4, HEAD)])
    assert _upsert().url.endswith("/pull/4")


def test_a_listing_that_could_not_be_read_opens_nothing(gh_pr):
    # Absence has to be a real answer: read as "no PR", a rate-limited listing would open
    # a duplicate on every tick.
    fake = gh_pr([], list_code=1)
    with pytest.raises(RuntimeError):
        pulls.find_pr(REPO, HEAD)
    assert _upsert() == pulls.Upserted(1, None)
    assert fake.did("pr", "create") == []


def test_the_lookup_asks_for_more_than_the_default_page(gh_pr):
    fake = gh_pr([])
    assert pulls.find_pr(REPO, HEAD) is None
    (args,) = fake.did("pr", "list")
    assert args[args.index("--limit") + 1] == "100"
    assert args[args.index("--state") + 1] == "open"
    assert args[args.index("--head") + 1] == HEAD


# ------------------------------------------------------------------- the two side edits


def test_the_reviewer_is_requested_on_the_pr_this_run_opened(gh_pr):
    fake = gh_pr([])
    assert _upsert(reviewer="Cohort-f2026/instructors").url == CREATED_URL
    (edit,) = fake.did("pr", "edit")
    assert edit[2] == "7"  # the number parsed out of what `gh pr create` printed
    assert edit[edit.index("--add-reviewer") + 1] == "Cohort-f2026/instructors"


def test_a_review_request_that_did_not_stick_still_keeps_the_pr(gh_pr):
    # Whether a TEAM can be requested at all depends on the org's plan. The PR is the
    # thing that matters; a release must not go red because the request bounced.
    gh_pr([], edit_code=1)
    assert _upsert(reviewer="Cohort-f2026/instructors") == pulls.Upserted(
        0, CREATED_URL
    )


def test_an_adopted_pr_is_not_re_requested(gh_pr):
    # The reviewers a human has since dismissed are their decision.
    fake = gh_pr([_row(4, HEAD)])
    assert _upsert(reviewer="Cohort-f2026/instructors").errors == 0
    assert fake.did("pr", "edit") == []


def test_an_adopted_pr_keeps_its_body_unless_a_refresh_is_asked_for(gh_pr):
    fake = gh_pr([_row(4, HEAD)])
    assert _upsert().errors == 0
    assert fake.did("pr", "edit") == []
    assert _upsert(refresh_body=True).errors == 0
    (edit,) = fake.did("pr", "edit")
    assert edit[edit.index("--body") + 1] == "materials/week02"


def test_a_create_that_failed_is_one_counted_error(gh_pr):
    fake = gh_pr([], write_code=1)
    assert _upsert() == pulls.Upserted(1, None)
    assert fake.did("pr", "edit") == []
