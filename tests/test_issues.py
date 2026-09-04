"""issues: find-by-exact-title, then create-or-edit-or-comment-or-close.

The exact-title match is the whole safety property - `gh issue list --search` is full text,
so an issue a HUMAN filed quoting the title comes back in the results, and four call sites
each grew their own copy of this search with only one of them filtering afterwards. The
other property under test is the notification rule: a comment (which emails) is posted only
on an issue that already existed, because a new one notifies by being created.
"""

from __future__ import annotations

import pytest

from dsl_course import issues

REPO = "Cohort-f2026/classroom-config"
TITLE = "Scheduled release: late delivery"


class _Gh:
    """A recording fake for the two ghcli entry points this module uses - `gh` for the
    writes, `gh_json` for the listing - with every call captured.

    The listing goes through `gh_json` because that is the one that parses stdout alone; a
    read that failed reaches the code as an exception, which is what `gh_json` raises."""

    def __init__(
        self, rows: list[dict] | None = None, list_code: int = 0, write_code: int = 0
    ):
        self.rows = rows or []
        self.list_code = list_code
        self.write_code = write_code
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        return self.write_code, "boom" if self.write_code else ""

    def json(self, *args, **kwargs):
        self.calls.append(args)
        if self.list_code != 0:
            raise RuntimeError(f"`gh issue list` failed (exit {self.list_code}): boom")
        return self.rows

    def did(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix]

    def body_of(self, *prefix) -> str:
        (call,) = self.did(*prefix)
        return call[call.index("--body") + 1]


@pytest.fixture
def gh(monkeypatch):
    def _make(rows=None, list_code=0, write_code=0):
        fake = _Gh(rows, list_code, write_code)
        monkeypatch.setattr(issues, "gh", fake)
        monkeypatch.setattr(issues, "gh_json", fake.json)
        return fake

    return _make


def _issue(number: int, title: str, body: str = "") -> dict:
    return {"number": number, "title": title, "body": body}


# ------------------------------------------------------------------ the exact-title match


def test_an_issue_a_human_filed_quoting_the_title_is_not_ours(gh):
    gh([_issue(3, f"re: {TITLE}", "my notes"), _issue(4, f"{TITLE} - again?")])
    assert issues.find_issue(REPO, TITLE) is None


def test_the_exact_title_is_found_among_the_near_misses(gh):
    gh([_issue(3, f"re: {TITLE}"), _issue(7, TITLE, "the body")])
    assert issues.find_issue(REPO, TITLE) == (7, "the body")


def test_a_human_quoting_the_title_is_never_rewritten_but_gets_a_neighbour(gh):
    fake = gh([_issue(3, f"re: {TITLE}", "my notes")])
    assert issues.upsert_issue(REPO, TITLE, "ours") == 0
    assert fake.did("issue", "edit") == []
    assert len(fake.did("issue", "create")) == 1


def test_the_lookup_asks_for_more_than_the_default_page(gh):
    # `gh issue list` returns 30 by default and the title match is client-side, so a repo
    # whose issue list buried ours past the 30th result read as "no issue" - and every
    # tick opened a fresh one.
    fake = gh([])
    assert issues.find_issue(REPO, TITLE) is None
    (args,) = fake.did("issue", "list")
    assert args[args.index("--limit") + 1] == "100"
    assert args[args.index("--state") + 1] == "open"


def test_a_listing_that_could_not_be_read_is_not_no_issue(gh):
    # Absence must be a real answer: reported as "no issue", a rate-limited listing would
    # open a duplicate on every tick.
    gh([], list_code=1)
    with pytest.raises(RuntimeError):
        issues.find_issue(REPO, TITLE)


def _gh_process(monkeypatch, stdout: str, stderr: str = "", code: int = 0) -> None:
    """Drive the REAL read helper off a fake `gh` process, so what the parser is handed is
    decided here rather than by a stub standing in for it."""
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr),
    )


def test_an_advisory_on_stderr_does_not_spoil_a_good_listing(monkeypatch):
    # gh writes advisories - a token nearing expiry, an update notice - to stderr, and the
    # joined pair is not JSON. Parsed, that raised a JSONDecodeError past every caller,
    # which catches RuntimeError: one advisory would have aborted a release tick.
    _gh_process(
        monkeypatch,
        stdout=f'[{{"number": 7, "title": "{TITLE}", "body": "the body"}}]',
        stderr="! A new release of gh is available\n",
    )
    assert issues.find_issue(REPO, TITLE) == (7, "the body")


def test_a_listing_that_is_not_json_raises_a_runtime_error(monkeypatch):
    # The contract callers rely on is RuntimeError, whatever the listing turned out to be.
    _gh_process(monkeypatch, stdout="not json at all")
    with pytest.raises(RuntimeError):
        issues.find_issue(REPO, TITLE)


def test_an_unreadable_listing_makes_upsert_fail_without_writing(gh):
    fake = gh([], list_code=1)
    assert issues.upsert_issue(REPO, TITLE, "ours") == 1
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


# ------------------------------------------------------------------------ create vs edit


def test_upsert_creates_when_there_is_nothing_open(gh):
    fake = gh([])
    assert issues.upsert_issue(REPO, TITLE, "the body") == 0
    (created,) = fake.did("issue", "create")
    assert created[created.index("--title") + 1] == TITLE
    assert fake.body_of("issue", "create") == "the body"


def test_upsert_edits_the_open_issue_in_place(gh):
    fake = gh([_issue(7, TITLE, "stale body")])
    assert issues.upsert_issue(REPO, TITLE, "fresh body") == 0
    assert fake.did("issue", "create") == []
    (edited,) = fake.did("issue", "edit")
    assert edited[2] == "7"
    assert fake.body_of("issue", "edit") == "fresh body"


def test_a_failed_write_is_counted_not_swallowed(gh):
    gh([], write_code=1)
    assert issues.upsert_issue(REPO, TITLE, "the body") == 1


# ------------------------------------------------------------------------ the comment rule


def test_a_comment_is_posted_only_on_an_issue_that_already_existed(gh):
    # A brand-new issue emails everyone watching by being created; a comment repeating
    # itself on top of that is the noise this whole shape exists to avoid.
    fake = gh([])
    assert issues.upsert_issue(REPO, TITLE, "body", comment="something changed") == 0
    assert fake.did("issue", "comment") == []

    fake = gh([_issue(7, TITLE, "stale")])
    assert issues.upsert_issue(REPO, TITLE, "body", comment="something changed") == 0
    (commented,) = fake.did("issue", "comment")
    assert commented[2] == "7"
    assert fake.body_of("issue", "comment") == "something changed"


def test_no_comment_means_a_silent_body_edit(gh):
    # The hourly case: GitHub does not email on a body edit, so a tick with nothing new to
    # say refreshes the body and stays quiet.
    fake = gh([_issue(7, TITLE, "stale")])
    assert issues.upsert_issue(REPO, TITLE, "body") == 0
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


# -------------------------------------------------------------------------------- closing


def test_close_closes_every_exact_match_and_leaves_the_neighbours(gh):
    # Plural on purpose: the callers are stateless, so a duplicate opened during an outage
    # has to be cleared too or it stands for the rest of the term.
    fake = gh([_issue(3, f"re: {TITLE}"), _issue(7, TITLE), _issue(9, TITLE)])
    assert issues.close_issues_titled(REPO, TITLE, "cleared") == 0
    closed = fake.did("issue", "close")
    assert [c[2] for c in closed] == ["7", "9"]
    assert all("--comment" in c for c in closed)


def test_closing_nothing_is_a_success_and_writes_nothing(gh):
    fake = gh([])
    assert issues.close_issues_titled(REPO, TITLE) == 0
    assert fake.did("issue", "close") == []


def test_close_without_a_comment_passes_none(gh):
    fake = gh([_issue(7, TITLE)])
    assert issues.close_issues_titled(REPO, TITLE) == 0
    (closed,) = fake.did("issue", "close")
    assert "--comment" not in closed


def test_a_close_that_fails_is_counted(gh):
    gh([_issue(7, TITLE)], write_code=1)
    assert issues.close_issues_titled(REPO, TITLE) == 1
