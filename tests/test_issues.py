"""issues: find-by-exact-title, then create-or-edit-or-comment-or-close.

The exact-title match is the whole safety property - `gh issue list --search` is full text,
so an issue a HUMAN filed quoting the title comes back in the results, and four call sites
each grew their own copy of this search with only one of them filtering afterwards. The
other property under test is the notification rule: a comment (which emails) is posted only
on an issue that already existed, because a new one notifies by being created.
"""

from __future__ import annotations

import pytest
from conftest import CREATED_ISSUE_URL, issue_row

from dsl_course import issues

REPO = "Cohort-f2026/classroom-config"
TITLE = "Scheduled release: late delivery"


def _issue(number: int, title: str, body: str = "") -> dict:
    return issue_row(number, title, body)


# ------------------------------------------------------------------ the exact-title match


def test_an_issue_a_human_filed_quoting_the_title_is_not_ours(gh):
    gh([_issue(3, f"re: {TITLE}", "my notes"), _issue(4, f"{TITLE} - again?")])
    assert issues.find_issue(REPO, TITLE) is None


def test_the_exact_title_is_found_among_the_near_misses(gh):
    gh([_issue(3, f"re: {TITLE}"), _issue(7, TITLE, "the body")])
    assert issues.find_issue(REPO, TITLE) == issues.Issue(7, "the body")


def test_a_human_quoting_the_title_is_never_rewritten_but_gets_a_neighbour(gh):
    fake = gh([_issue(3, f"re: {TITLE}", "my notes")])
    assert issues.upsert_issue(REPO, TITLE, "ours").errors == 0
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
        stdout=f'[{{"number": 7, "title": "{TITLE}", "body": "the body", "state": "OPEN"}}]',
        stderr="! A new release of gh is available\n",
    )
    assert issues.find_issue(REPO, TITLE) == issues.Issue(7, "the body")


def test_a_listing_that_is_not_json_raises_a_runtime_error(monkeypatch):
    # The contract callers rely on is RuntimeError, whatever the listing turned out to be.
    _gh_process(monkeypatch, stdout="not json at all")
    with pytest.raises(RuntimeError):
        issues.find_issue(REPO, TITLE)


def test_an_unreadable_listing_makes_upsert_fail_without_writing(gh):
    fake = gh([], list_code=1)
    assert issues.upsert_issue(REPO, TITLE, "ours").errors == 1
    assert fake.did("issue", "create") == fake.did("issue", "edit") == []


# ------------------------------------------------------------------------ create vs edit


def test_upsert_creates_when_there_is_nothing_open(gh):
    fake = gh([])
    assert issues.upsert_issue(REPO, TITLE, "the body").errors == 0
    (created,) = fake.did("issue", "create")
    assert created[created.index("--title") + 1] == TITLE
    assert fake.body_of("issue", "create") == "the body"


def test_upsert_edits_the_open_issue_in_place(gh):
    fake = gh([_issue(7, TITLE, "stale body")])
    assert issues.upsert_issue(REPO, TITLE, "fresh body").errors == 0
    assert fake.did("issue", "create") == []
    (edited,) = fake.did("issue", "edit")
    assert edited[2] == "7"
    assert fake.body_of("issue", "edit") == "fresh body"


def test_a_failed_write_is_counted_not_swallowed(gh):
    gh([], write_code=1)
    assert issues.upsert_issue(REPO, TITLE, "the body").errors == 1


def test_an_opened_issue_reports_the_url_it_was_given(gh):
    # The tick that OPENS the issue is the tick a notifier has something to say, and the
    # number is printed by `gh issue create` and nowhere else - so a bare count left the
    # mail beside the issue unable to link it.
    gh([])
    assert issues.upsert_issue(REPO, TITLE, "the body").url == CREATED_ISSUE_URL


def test_an_edited_issue_reports_the_url_of_the_issue_it_found(gh):
    gh([_issue(7, TITLE, "stale body")])
    assert issues.upsert_issue(REPO, TITLE, "fresh").url.endswith("/issues/7")


def test_a_write_that_printed_no_url_reports_none_rather_than_guessing(gh):
    gh([], write_code=1)
    assert issues.upsert_issue(REPO, TITLE, "the body").url is None


# ------------------------------------------------------------------------ the comment rule


def test_a_comment_is_posted_only_on_an_issue_that_already_existed(gh):
    # A brand-new issue emails everyone watching by being created; a comment repeating
    # itself on top of that is the noise this whole shape exists to avoid.
    fake = gh([])
    assert (
        issues.upsert_issue(REPO, TITLE, "body", comment="something changed").errors
        == 0
    )
    assert fake.did("issue", "comment") == []

    fake = gh([_issue(7, TITLE, "stale")])
    assert (
        issues.upsert_issue(REPO, TITLE, "body", comment="something changed").errors
        == 0
    )
    (commented,) = fake.did("issue", "comment")
    assert commented[2] == "7"
    assert fake.body_of("issue", "comment") == "something changed"


def test_no_comment_means_a_silent_body_edit(gh):
    # The hourly case: GitHub does not email on a body edit, so a tick with nothing new to
    # say refreshes the body and stays quiet.
    fake = gh([_issue(7, TITLE, "stale")])
    assert issues.upsert_issue(REPO, TITLE, "body").errors == 0
    assert len(fake.did("issue", "edit")) == 1
    assert fake.did("issue", "comment") == []


# ------------------------------------------------------------- the issue somebody closed


def test_both_halves_of_the_title_come_back_from_one_search(gh):
    # A caller whose state lives in the body it last wrote needs the newest thing it
    # wrote, open or closed - and asking for the closed half separately is a second
    # listing on every tick that has no open issue.
    fake = gh(
        [_issue(11, TITLE, "current")],
        closed=[_issue(3, TITLE, "older"), _issue(9, TITLE, "newest")],
    )
    found = issues.find_issues(REPO, TITLE)
    assert found.open == issues.Issue(11, "current")
    assert found.last_closed == issues.Issue(9, "newest", closed=True)
    (listed,) = fake.did("issue", "list")
    assert listed[listed.index("--state") + 1] == "all"


def test_a_closed_issue_a_human_titled_similarly_is_not_ours_either(gh):
    gh([], closed=[_issue(3, f"re: {TITLE}", "my notes")])
    assert issues.find_issues(REPO, TITLE) == (None, None)


def test_nothing_of_that_title_at_all_is_two_nones(gh):
    gh([])
    assert issues.find_issues(REPO, TITLE) == (None, None)


# ---------------------------------------------------------- the listing already made


def test_upsert_uses_the_listing_the_caller_already_made(gh):
    # Every consumer reads the body for its own previous state before deciding what to
    # write, so a second search here is a second listing on every tick.
    fake = gh([_issue(7, TITLE, "stale")])
    found = issues.find_issues(REPO, TITLE)
    assert issues.upsert_issue(REPO, TITLE, "fresh", existing=found.open).errors == 0
    assert len(fake.did("issue", "list")) == 1
    assert fake.body_of("issue", "edit") == "fresh"


def test_a_caller_that_looked_and_found_nothing_is_not_asked_again(gh):
    # `existing=None` is an ANSWER - "I looked, nothing is open" - and it must not read as
    # "I did not look", or the tick that has to CREATE searches twice.
    fake = gh([])
    issues.find_issues(REPO, TITLE)
    assert issues.upsert_issue(REPO, TITLE, "ours", existing=None).errors == 0
    assert len(fake.did("issue", "list")) == 1
    assert len(fake.did("issue", "create")) == 1


def test_the_issue_url_is_spelled_once_for_every_caller():
    # A mail links the record it is summarising, and two format strings for one URL is one
    # rename away from linking nowhere.
    assert issues.issue_url(REPO, 7) == f"https://github.com/{REPO}/issues/7"


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


# ------------------------------------------------------- every open title, in one listing


def test_open_titles_answers_about_several_issues_in_one_read(gh):
    # `status` asks about seven known titles at once. One search apiece is seven round
    # trips for one table - and unlike a caller about to WRITE one of them, it needs no
    # body and no closed twin.
    fake = gh(
        [_issue(3, TITLE), _issue(4, "people.yml has entries the sync cannot use")]
    )
    assert issues.open_titles(REPO) == {
        TITLE,
        "people.yml has entries the sync cannot use",
    }
    assert len(fake.did("issue", "list")) == 1


def test_open_titles_is_the_whole_list_not_a_title_search(gh):
    # No `--search`: the caller matches the titles it knows about itself, and a search
    # term would silently cap the answer to whatever one phrase matched.
    fake = gh([_issue(3, TITLE)])
    issues.open_titles(REPO)
    (listed,) = fake.did("issue", "list")
    assert "--search" not in listed and "--state" in listed


def test_a_listing_that_could_not_be_read_is_not_an_empty_one(gh):
    # Absence has to be a real answer: a status table that read a rate limit as "nothing
    # is open" would report a cohort with a broken roster as healthy.
    gh([], list_code=1)
    with pytest.raises(RuntimeError):
        issues.open_titles(REPO)
