"""dsl-course receipts -- the Submission receipts issue on a submission repo.

Its body, finding or opening it, which repos get one (`receipts_thread_policy`), and
posting a receipt or the marks-returned note. It carries receipts and nothing else: a mark
is never posted there (`gradebook`).
"""

from __future__ import annotations

from .course import (
    RECEIPTS_ISSUE_LABEL,
    RECEIPTS_ISSUE_LABELS,
    RECEIPTS_ISSUE_MARKS,
    RECEIPTS_ISSUE_TITLE,
    receipt_body,
    receipts_issue_body,
)
from .ghcli import bot_login, gh, is_missing_resource
from .gradebook import (
    _penalty_display,
)
from .grading_sheet import (
    SheetSpec,
    penalty_rate,
)
from .log import (
    log,
    log_err,
)
from .repos import (
    ensure_label,
    listed_is_private,
)

# ----------------------------------------------------------- the Submission receipts issue, in situ

# Reading and writing the issue whose CONTRACT lives in `course`. Everything that decides
# WHAT is said is there; everything that decides whether a call is made is here.
_RECEIPTS_LABEL_COLOUR = "0e8a16"
_RECEIPTS_LABEL_DESCRIPTION = "Submission receipts from the toolkit"


def late_policy(spec) -> str:
    """`accepted until Sunday 11 October 2026, 23:59 (Europe/Berlin), at 10% of your grade
    per day started.` - or "" when nothing is accepted after the deadline.

    ONE sentence for both places a student meets the policy - the Submission receipts issue at handout
    and every receipt after it. Two spellings of the same rule is how a student ends up
    reading two different deadlines.

    The rate TRAILS the date rather than bracketing it: `cutoff_long` already ends in the
    semester's timezone, and two parentheticals in a row read as a typo."""
    if not spec.collects_commits or not (spec.late_window_days and spec.cutoff_long):
        return ""
    rate = (
        f", at {spec.late_penalty_per_day} of your grade per day started"
        if spec.late_penalty_per_day
        else ""
    )
    return f"accepted until {spec.cutoff_long}{rate}."


def receipts_thread_body(
    spec, unit: str = "", members: tuple[str, ...] | list[str] = ()
) -> str:
    """The Submission receipts issue's body for one submission repo, from the assignment's own spec.

    `unit` is the row that repo belongs to - a team name on a group assignment, the
    student's handle on an individual one - and WHICH VARIANT to write is read off the
    spec rather than left to the caller. Only a group body names the team and asks for
    CONTRIBUTIONS.md, and the openers that reach a repo late (the first refresh after the
    due date, distribute) each know their unit but used to pass neither - so a team whose
    issue was opened lazily never got the ask, on the one path where there was still time
    to act on it."""
    late = late_policy(spec)
    team = unit if spec.is_group else ""
    # Handles, not names: this body is written from the provisioning path, which knows the
    # repo's collaborators and not the roster's display names - and a handle is what the
    # repo shows the team anyway.
    team_line = (
        f"{team} ({', '.join('@' + h for h in members)})" if team and members else ""
    )
    return receipts_issue_body(
        due_display=spec.due_long,
        late_policy_line=late,
        team_line=team_line,
    )


def late_line(spec) -> str:
    """The late policy as the sentence a receipt carries, or "" when there is no window."""
    policy = late_policy(spec)
    return f"Late work is {policy}" if policy else ""


def penalty_display(spec, days: int) -> str:
    """`-20%` for the receipt - `_penalty_display` under the spec the caller already holds.

    The same function as the gradebook's, deliberately: those are the two places one
    student reads one deduction, and two renderers of it drifted apart above two decimal
    places the moment one of them rounded. The late rule's own figure only: a grader's
    `late_penalty` (waived, set by hand) reaches the student with their marks, through
    Distribute grades, never on a receipt posted before them."""
    return _penalty_display(penalty_rate(spec.late_penalty_per_day), days)


def receipt(
    spec, event: str, *, sha: str = "", pushed_display: str = "", days: int = 0
) -> str:
    """One receipt for this assignment, composed from its own late policy."""
    return receipt_body(
        event,
        sha=sha,
        pushed_display=pushed_display,
        days_late=days,
        penalty_display=penalty_display(spec, days),
        late_line=late_line(spec),
    )


class IssueLookupFailed:
    """The receipts-issue lookup could not be READ - as against finding nothing there.

    A 5xx or a secondary limit that outlived the retry ladder used to come back as "this
    repo has no Submission receipts issue", and the very next thing that happens is a SECOND issue
    opened over the thread the student was told to read. Falsy, so `if not found` still
    reads naturally; distinguished from None by identity, never by truth."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False


LOOKUP_FAILED = IssueLookupFailed()

# Oldest first, and the author with it. The toolkit opens the Submission receipts issue at handout,
# so ours is the oldest one carrying the label - but a student holds `maintain` on their
# own submission repo and can open and label their own, and GitHub's default ordering is
# newest first, which handed it theirs.
_ISSUE_ORDER = "sort=created&direction=asc"


def _receipts_issues(
    semester_org: str, repo: str, query: str, jq: str
) -> list[str] | None:
    """The issues this query matches, or None when the question could not be ANSWERED.

    A 404 IS an answer: there is no such repo, so it has no Submission receipts issue. Every shape
    can reach one - a student who never onboarded, a team formed after the handout, an
    assignment handed in off GitHub whose repos were never created - and answering "could
    not read it" for them turned a semester of absent repos into a red run and a `[wait]`
    line per student. What stops the 404 being read as "so open one" is the policy
    (`receipts_thread_policy`), which never creates where the listing did not show a
    private repo.

    Anything else is genuinely "could not answer" - a token that lost its grant is not a
    repo with no issues - and the caller must not act on the difference."""
    code, out = gh("api", f"repos/{semester_org}/{repo}/issues?{query}", "--jq", jq)
    if code != 0:
        return [] if is_missing_resource(out) else None
    return [line for line in out.splitlines() if line.strip()]


def _ours(rows: list[list[str]], login_at: int) -> list[str]:
    """The issue this toolkit opened, if one of these is - else the oldest of them.

    Ordering already puts ours first whenever it was opened first; preferring the author
    closes the case where a student's own labelled issue predates the handout's. One
    candidate is not a choice, and asking who we are costs a call, so it is not asked."""
    if len(rows) < 2:
        return rows[0]
    mine = bot_login()
    for row in rows:
        if mine and len(row) > login_at and row[login_at] == mine:
            return row
    return rows[0]


def find_receipts_issue(
    semester_org: str, repo: str
) -> tuple[int, str] | IssueLookupFailed | None:
    """`(number, state)` of this repo's Submission receipts issue, None if it has none, or
    `LOOKUP_FAILED` if the question could not be answered.

    Three rungs, cheapest first: the LABEL, then a body carrying one of the marks, then the
    exact title. Deliberately the LIST endpoint rather than `gh issue list --search`: the
    search index lags behind by minutes, and a lookup that comes back empty here does not
    mean "not there", it means "opened a second one" - which is what the search path did.

    Pull requests are issues to this endpoint, so they are filtered out: a PR titled
    A pull request titled like the issue would otherwise be commented on instead."""
    # One query per label in the chain: `labels=a,b` means BOTH, not either.
    for label in RECEIPTS_ISSUE_LABELS:
        by_label = _receipts_issues(
            semester_org,
            repo,
            f"labels={label}&state=all&per_page=5&{_ISSUE_ORDER}",
            ".[] | select(.pull_request == null) | "
            '"\\(.number)\\t\\(.state)\\t\\(.user.login // "")"',
        )
        if by_label is None:
            return LOOKUP_FAILED
        if by_label:
            row = _ours([line.split("\t") for line in by_label], 2)
            return int(row[0]), row[1]
    marks = " or ".join(f'contains("{mark}")' for mark in RECEIPTS_ISSUE_MARKS)
    listed = _receipts_issues(
        semester_org,
        repo,
        f"state=all&per_page=50&{_ISSUE_ORDER}",
        ".[] | select(.pull_request == null) | "
        f'"\\(.number)\\t\\(.state)\\t\\(if ((.body // "") | {marks}) then "mark" else "" end)'
        '\\t\\(.title)\\t\\(.user.login // "")"',
    )
    if listed is None:
        return LOOKUP_FAILED
    if not listed:
        return None
    rows = [line.split("\t") for line in listed]
    marked = [r for r in rows if len(r) > 2 and r[2] == "mark"]
    titled = [r for r in rows if len(r) > 3 and r[3] == RECEIPTS_ISSUE_TITLE]
    for candidate in (marked, titled):
        if candidate:
            row = _ours(candidate, 4)
            return int(row[0]), row[1]
    return None


def ensure_receipts_issue(
    semester_org: str, repo: str, body: str, dry_run: bool = False
) -> int | IssueLookupFailed | None:
    """This repo's Submission receipts issue number, opening one if it has none.

    A CLOSED issue is reopened: a student who closes theirs must still receive their
    receipts and their grade, and a second issue would split the thread they were told to
    read. Never two - see `find_receipts_issue` for why the lookup is not a search, and
    why a lookup that FAILED opens nothing: `LOOKUP_FAILED` comes straight back, and the
    caller leaves this unit for the next tick."""
    found = find_receipts_issue(semester_org, repo)
    if isinstance(found, IssueLookupFailed):
        log_err(
            f"  ! could not read the Submission receipts issues in {semester_org} - opening none, "
            f"posting none; the next run tries again"
        )
        return LOOKUP_FAILED
    if found is not None:
        number, state = found
        if state == "closed" and not dry_run:
            # `--method PATCH` so ghcli's write pacer counts it (see `_is_mutating`).
            code, out = gh(
                "api",
                "--method",
                "PATCH",
                f"repos/{semester_org}/{repo}/issues/{number}",
                "--field",
                "state=open",
            )
            if code != 0:
                log_err(
                    f"  ! could not reopen the Submission receipts issue: {out[:160]}"
                )
        return number
    if dry_run:
        log("    PREVIEW  would open the Submission receipts issue")
        return None
    # The label first: GitHub silently drops a label the repo does not have, and the label
    # is the cheapest rung of the lookup above.
    ensure_label(
        semester_org,
        repo,
        RECEIPTS_ISSUE_LABEL,
        color=_RECEIPTS_LABEL_COLOUR,
        description=_RECEIPTS_LABEL_DESCRIPTION,
        person=True,
    )
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{semester_org}/{repo}/issues",
        "--field",
        f"title={RECEIPTS_ISSUE_TITLE}",
        "--field",
        f"body={body}",
        "--field",
        f"labels[]={RECEIPTS_ISSUE_LABEL}",
        "--jq",
        ".number",
    )
    if code != 0:
        log_err(f"  ! could not open the Submission receipts issue: {out[:160]}")
        return None
    return int(out.strip()) if out.strip().isdigit() else None


def post_marked_comment(
    semester_org: str,
    repo: str,
    issue_no: int,
    body: str,
    marker: str,
    dry_run: bool = False,
) -> bool:
    """Post one comment on the Submission receipts issue, unless it already carries `marker`.

    The marker is the whole idempotence story, and it is why both callers share this: the
    refresh pass runs four times an hour for the length of the late window, and Patch
    released assignment is re-pressed after every correction. Neither may say the same
    thing twice.

    Paginated, because "already said" is only true of the comments we actually read: a
    thread that outgrew one page would hide its own markers and be told everything
    again."""
    code, out = gh(
        "api",
        "--paginate",
        f"repos/{semester_org}/{repo}/issues/{issue_no}/comments?per_page=100",
        "--jq",
        ".[].body",
    )
    if code != 0:
        log_err(
            f"  ! could not read the Submission receipts issue's comments: {out[:160]}"
        )
        return False
    if marker in out:
        return True  # already said, on this commit, for this event
    if dry_run:
        log("    PREVIEW  would post a submission receipt")
        return True
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{semester_org}/{repo}/issues/{issue_no}/comments",
        "--field",
        f"body={body}\n{marker}\n",
    )
    if code != 0:
        log_err(f"  ! could not comment on the Submission receipts issue: {out[:160]}")
        return False
    return True


def post_receipt(
    semester_org: str, repo: str, issue_no: int, body: str, marker: str, dry_run=False
) -> bool:
    """A submission receipt - `post_marked_comment` under the name its caller uses."""
    return post_marked_comment(semester_org, repo, issue_no, body, marker, dry_run)


# What a run may do about one unit's Submission receipts issue. THREE answers and one function that
# gives them (`receipts_thread_policy`), asked by the submission receipts and by nothing
# else now that no mark is posted into a thread at all. THREE and not a boolean because
# the answers are about the REPO: `find` is "there may be a thread here, but do not open
# one", which is not the same fact as "there is nowhere to post", and the two used to be
# conflated in each caller that asked the question for itself.
THREAD_CREATE = "create"  # open one if this repo has none
THREAD_FIND = "find"  # use the thread it has; never open one
THREAD_NONE = "none"  # do not look, do not post


def receipts_thread_policy(
    spec: SheetSpec, listed: dict[str, dict] | None, repo: str
) -> str:
    """May this run touch `repo`'s Submission receipts issue, and may it OPEN one?

    Everything the toolkit knows about that, in one place: the assignment's SHAPE (does it
    have a Submission receipts issue at all?) and the org LISTING's word on the repo (is it there,
    and is it still private?). Both are needed - the file says what was handed out and the
    listing says what is there now - and each answer here is deliberately narrow in the
    direction that cannot hurt a student: a listing is a snapshot, and answering "no
    thread" off it wrongly drops a receipt.

    - `listed is None` - we could not look at all: FIND. The thread a student was told to
      read is still the right place for their receipts; opening one on an org nobody could
      list is how a second Submission receipts issue appears over it.
    - the repo is NOT in the listing: FIND where the shape creates a repo per unit (it may
      have been created since the listing was taken), NONE where it does not - an external
      assignment has no repos, and probing each would be an issues call per student for an
      answer already known.
    - the listing says the repo is not private: NONE. A hand-in time is a fact about a
      student, and it does not go where the world can read it - which is what a
      `visibility:` edited after handout leaves behind.
    - otherwise: CREATE for an assignment whose shape HAS a Submission receipts issue and whose shape
      was read from a real definition; FIND for every other one, which is what keeps a
      semester handed out before these shapes existed (Maths a1) getting its receipts in the
      thread it was told to read."""
    if listed is None:
        return THREAD_FIND
    row = listed.get(repo)
    if row is None:
        return THREAD_FIND if spec.creates_unit_repos else THREAD_NONE
    if not listed_is_private(row):
        return THREAD_NONE
    if spec.may_open_receipts_issue:
        return THREAD_CREATE
    return THREAD_FIND


def receipts_thread(
    spec: SheetSpec,
    semester_org: str,
    repo: str,
    unit: str,
    members: list[str],
    listed: dict[str, dict] | None,
    *,
    dry_run: bool = False,
) -> int | IssueLookupFailed | None:
    """The Submission receipts issue this unit's receipt goes on: its number, None where there is none
    this run may use, or `LOOKUP_FAILED` where the question could not be answered.

    THE entry point, and the only consumer of `receipts_thread_policy`. It takes the
    STRONGEST answer the policy gives and nothing weaker - the repo is in the listing, the
    listing says it is private, and the shape has a Submission receipts issue - because a receipt is
    the one thing that would OPEN a thread nobody has asked for yet. A repo the listing
    does not carry, or a listing that could not be read at all, waits for a tick that can
    say so: the grading sheet is the record and the receipt is a courtesy."""
    if receipts_thread_policy(spec, listed, repo) != THREAD_CREATE:
        return None
    return ensure_receipts_issue(
        semester_org, repo, receipts_thread_body(spec, unit, members), dry_run
    )
