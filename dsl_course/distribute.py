"""dsl-course distribute -- sending what a grader wrote in the grading sheet.

`distribute` fans each sheet out to the students' gradebooks, the semester grades file and an
email, and records every send in `distributed.csv`; `marks_due` says which assignments a
Scheduled release may return by itself. The CLI is `python3 -m dsl_course.grades`.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from . import mailer, records, roster, schedule
from .course import (
    CONFIG_REPO,
    GRADEBOOK_PREFIX,
    MARKS_RETURNED_NOTE,
    course_phrase,
    marks_returned_marker,
    submission_repo,
)
from .discovery import (
    course_name_or,
    course_org_for_semester,
    discover_semesters,
    listing_by_name,
)
from .faults import ConfigFault, Severity
from .gh_contents import (
    get_file_content,
    put_files,
)
from .ghcli import clone, git
from .gradebook import (
    CHANNEL_EMAIL,
    CHANNEL_GRADEBOOK,
    DISTRIBUTED_PATH,
    SEMESTER_CSV_NAME,
    Distributed,
    _already_returned,
    _on_the_roster,
    _read_distributed,
    _retired_gradebook_files,
    _told_grades,
    _undeclared_feedback,
    build_gradebooks,
    content_hash,
    dump_distributed,
    ensure_gradebooks,
    render_readme,
    render_registrar_csv,
    render_yaml,
    undecided_marks,
)
from .grading_sheet import (
    HOLD_REASONS,
    INFO_KEY,
    LATE_PENALTY_KEY,
    QUESTION_FEEDBACK_KEY,
    SHEETS_DIR,
    SheetSpec,
    SheetUnreadable,
    _misshapen,
    _people,
    _spec_from_sheet,
    _units,
    is_blank,
    load_sheets,
    parse_sheet,
    penalty_rate,
    sheet_hold_reasons,
    sheet_path,
)
from .grading_spec import (
    GRADING_FILE,
    sheet_specs,
)
from .issues import close_issues_titled, upsert_issue
from .log import (
    Summary,
    log,
    log_err,
    log_ok,
    log_person,
    log_step,
    plural,
)
from .receipts import (
    THREAD_NONE,
    IssueLookupFailed,
    find_receipts_issue,
    post_marked_comment,
    receipts_thread_policy,
)


def marks_return_record(name: str) -> str:
    """The fire-once record of an assignment whose marks automation has returned."""
    return records.path("marks_returned", f"{name}.json")


def marks_due(
    course_org: str, semester_org: str, sched, now: datetime
) -> tuple[list[ConfigFault], list[str]]:
    """The assignments whose `marks_return_datetime` has come and whose marks automation
    has not returned yet: `(faults, ready)`. `ready` is the schedule keys whose sheet has a
    mark for every unit - Return marks goes out for them. Each other one is a fault that
    says how many units are unmarked (a count, never a unit), and it is asked again every
    tick until the sheet is complete or the date is removed.

    Only an assignment past its moment costs a read: its marker, then its sheet."""
    faults: list[ConfigFault] = []
    ready: list[str] = []
    specs: dict[str, SheetSpec] | None = None
    for key, entry in sorted(sched.assignments.items()):
        when = entry.marks_return_datetime
        if when is None or when > now:
            continue
        name = schedule.semester_name(key, entry)
        if get_file_content(semester_org, CONFIG_REPO, marks_return_record(name)):
            continue
        if specs is None:
            specs = sheet_specs(course_org, sched)
        text = get_file_content(semester_org, CONFIG_REPO, sheet_path(name))
        try:
            sheet = parse_sheet(text) if text else None
        except SheetUnreadable:
            sheet = None  # the sheet's own digest says why
        spec = specs[name]
        if spec.not_migrated:
            # Its definition is refused, and the digest already carries that fault: a
            # return off a default-shaped spec would read the wrong container.
            continue
        if _misshapen(spec, sheet):
            continue  # nothing on it can be returned; the sheet's own digest says why
        units = ((sheet or {}).get(spec.container_key) or {}) if sheet else {}
        blank = _not_marked(spec, sheet) if sheet else {}
        if sheet and units and not blank:
            ready.append(key)
            continue
        # A blank unit with no submission is not a marking backlog: counted apart.
        absent = sum(
            1
            for unit in blank
            if spec.collects_commits
            and isinstance(units.get(unit), dict)
            and is_blank((units[unit].get(INFO_KEY) or {}).get("submitted"))
        )
        since = f"{when:%a %d %b %Y}"
        if units:
            counted = f"{len(blank)} of {len(units)} unit(s) unmarked"
            if absent:
                counted += f" ({absent} with no submission)"
        else:
            counted = "its grading sheet has no units yet"
        faults.append(
            ConfigFault(
                f"assignments.{key}",
                f"marks due since {since}; {counted} - they are returned "
                f"automatically on the first tick after every unit is marked",
                fires=when,
                # One mail, not the missed-moment ladder: waiting is the whole remedy,
                # and the maintainer has nothing to do about an unfinished sheet.
                ceiling=Severity.WARNING,
                field="marks_return_datetime",
                lineno=schedule.line_of(entry.lines, "marks_return_datetime"),
                file=schedule.SCHEDULE_PATH,
                fix_text=f"finish marking {sheet_path(name)}, or remove "
                "`marks_return_datetime:` to return the marks by hand",
                plain=f"Marks due since {since}; {counted}.",
                consequence="the marks are not returned until every unit is marked",
            )
        )
    return faults, ready


def dispatch_refusal(
    course_org: str, semester_org: str, assignment: str
) -> tuple[str, schedule.Schedule | None]:
    """Why a `return-marks` dispatch may not send, or "". Its payload is written by
    whoever holds a bot token, so nothing in it is trusted: the semester must be one the
    course registers, and the assignment one whose `marks_return_datetime` has come with
    every unit marked and nothing returned yet.

    Also the schedule it read (None when it refused before reading one), so the return
    that follows reads it once."""
    registered = {o.casefold() for o in discover_semesters(course_org)}
    if semester_org.casefold() not in registered:
        return f"{semester_org} is not a semester of {course_org} - nothing sent", None
    sched = schedule.load(semester_org)
    _faults, ready = marks_due(course_org, semester_org, sched, datetime.now(UTC))
    names = {schedule.semester_name(k, sched.assignments[k]) for k in ready}
    if not assignment or assignment not in names:
        refusal = (
            f"{assignment or 'no assignment'} is not due for return - nothing sent"
        )
        return refusal, sched
    return "", sched


def _not_marked(spec: SheetSpec, sheet: dict) -> dict[str, list[str]]:
    """`{unit: the questions still blank}` for every unit with SOME question marked and
    some not, and `{unit: []}` for every unit with no mark at all.

    A partly filled map totals to what has been typed so far, and a real run sends that
    total: which is right (a grader who wants to release Q1 early may) and is also exactly
    how half a mark reaches a student unnoticed. So the dry run says where they are, and
    the decision stays the grader's."""
    out = {}
    for unit, block in _units(spec, sheet):
        score = block.get(spec.score_key)
        if isinstance(score, dict) and spec.questions:
            blank = [k for k in spec.questions if is_blank(score.get(k))]
            if len(blank) == len(spec.questions):
                out[str(unit)] = []
            elif blank:
                out[str(unit)] = blank
        elif is_blank(score) or (
            isinstance(score, dict) and all(is_blank(v) for v in score.values())
        ):
            out[str(unit)] = []
    return out


def ignored_late_penalties(spec: SheetSpec, sheet: dict) -> int:
    """How many units carry a `late_penalty` on an assignment with no late rule - which
    `effective_penalty` ignores, so the preview says so rather than let a grader believe
    their waive or override did something."""
    if penalty_rate(spec.late_penalty_per_day) is not None:
        return 0
    return sum(
        1
        for _unit, block in _units(spec, sheet)
        if not is_blank(block.get(LATE_PENALTY_KEY))
    )


def _adjusted_count(spec: SheetSpec, sheet: dict) -> int:
    """How many individual adjustments a grader has written into one sheet - the dry run
    reports it, because an adjustment is the one thing in the file no arithmetic explains."""
    total = 0
    for unit, block in _units(spec, sheet):
        total += sum(
            1
            for person in _people(spec, unit, block).values()
            if isinstance(person, dict)
            and not is_blank(person.get("adjustment_individual"))
        )
    return total


def _undue_marks(
    specs: dict[str, SheetSpec], books: dict[str, dict[str, dict]], now: datetime
) -> int:
    """How many marks are about to go out with nothing derived behind them, because the
    assignment's due date has not passed yet.

    Nothing fills `info:` before the due date - there is nothing to derive there, and a
    handout must not cost an API call per student (`collect.sync_sheet`) - so a grade sent
    early carries a gradebook row reading `not submitted` while the work sits in the repo.
    A grader may well mean it (a mark released early, a semester that has all handed in), so
    this is counted and said out loud and never blocks: the alternative is withholding a
    mark somebody decided to send.

    Read off the student VIEWS rather than the sheet, because that is what the gradebook
    row is rendered from: `submitted` is dropped from a view when there is no submission
    time in it (`_allowlisted`), and a shape that collects no commits has none to want."""
    return sum(
        1
        for book in books.values()
        for slug, view in book.items()
        if (spec := specs.get(slug)) is not None
        and spec.collects_commits
        and spec.due_at is not None
        and now < spec.due_at
        and "final_grade" in view
        and "submitted" not in view
    )


def _hold_undecided(
    books: dict[str, dict[str, dict]], reasons: dict[str, dict[str, str]]
) -> dict[str, dict[str, tuple[str, str]]]:
    """Take every mark that still needs a person out of the books, and say whose it was.

    Two kinds arrive in `reasons`: what a grader TYPED that cannot be acted on
    (`sheet_hold_reasons`), and arithmetic that could not be done (`undecided_marks`) -
    `pass`, two days late, is not `pass minus 20%`. Sending either puts a line a student
    can read where a decision should have been, and the dry run that counted it has
    already gone by. So the mark is HELD: no gradebook entry, no column in the semester grades file, no
    email, until a grader settles it in the sheet. Nothing else that student has is held
    with it - unless the held mark is one they were already sent (`_hold_returned`).

    Returns `{slug: {handle: (submission unit, reason)}}` - the unit, because a group's
    mark is derived from the team block rather than from any member's view, so a grader
    reading the hold line is told which row to go and settle.
    """
    held: dict[str, dict[str, tuple[str, str]]] = {}
    # Folded: the books are keyed on the FIRST spelling of a handle across the sheets
    # (`build_gradebooks`), and a sheet may spell it another way.
    folded = {
        slug: {str(handle).casefold(): why for handle, why in whose.items()}
        for slug, whose in reasons.items()
    }
    for handle, book in books.items():
        for slug, view in list(book.items()):
            reason = (folded.get(slug) or {}).get(handle.casefold())
            if not reason:
                continue
            held.setdefault(slug, {})[handle] = (view.get("team") or handle, reason)
            del book[slug]
    # A student whose ONLY mark was held has nothing to be sent: leaving the empty book in
    # would commit a gradebook page with nothing new on it and email them about it.
    for handle in [h for h, book in books.items() if not book]:
        del books[handle]
    return held


def _hold_returned(
    books: dict[str, dict[str, dict]],
    held: dict[str, dict[str, tuple[str, str]]],
    told: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Take out of the run, whole, every student a HELD mark of whose they have already
    been sent, and say what the semester grades file last carried for each:
    `{handle: {slug: final grade}}`.

    A hold stops a NEW value going out. Taking the mark out of the book did that, and for
    a mark the student already had it also took it AWAY: their gradebook was rewritten
    without it, the export's cell for it went blank, and they were emailed that their
    grades had changed. So for this run that student is held whole - no gradebook commit,
    no email, their export row exactly as the last real run wrote it - and each other
    mark of theirs in the run is held with it (`returned`), so the automatic return of
    that assignment asks again once the sheet is settled.

    `told` is `_told_grades`, keyed on the folded handle. A mark with no final grade in
    the export (feedback alone) cannot be told from one never sent, and is held as
    before."""
    kept = {
        handle: told[handle.casefold()]
        for slug, whose in held.items()
        for handle in whose
        if (told.get(handle.casefold()) or {}).get(slug)
    }
    for handle in kept:
        for slug, view in (books.pop(handle, None) or {}).items():
            held.setdefault(slug, {})[handle] = (view.get("team") or handle, "returned")
    return kept


def _with_told(
    books: dict[str, dict[str, dict]], kept: dict[str, dict[str, str]]
) -> dict[str, dict[str, dict]]:
    """The books the semester grades file is rendered from: this run's, plus each student
    held whole (`_hold_returned`) as the export last carried them."""
    return books | {
        handle: {slug: {"final_grade": grade} for slug, grade in row.items()}
        for handle, row in kept.items()
    }


# The view keys a GRADER writes. The rest - `max_points`, `submitted`, `days_late` - are
# the toolkit's facts about the assignment and fill in on their own, so a book carrying
# only those has changed without there being anything for a student to read.
_GRADER_KEYS = (
    "final_grade",
    "score",
    "feedback",
    QUESTION_FEEDBACK_KEY,
    "team_feedback",
)


def _has_mark(book: dict[str, dict]) -> bool:
    """Whether any assignment in this book carries a mark or a word of feedback."""
    return any(key in view for view in book.values() for key in _GRADER_KEYS)


# Leads every EMAIL digest `_marks_digest` writes. Part of the digest: every recorded row
# carries it, so dropping it would re-mail every student once.
MARKS_DIGEST_PREFIX = "m1:"


def _canonical(value: object) -> object:
    """`value` with every mapping key a string, so a per-question map keyed `1:` beside
    `Q2:` still sorts."""
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    return value


def _marks_digest(
    book: dict[str, dict], specs: dict[str, SheetSpec] | None = None
) -> str:
    """What the EMAIL keys on: what a grader wrote, per assignment, and nothing else. A
    refreshed `submitted`, a new `max_points` or a respelt date moves grades.yml without
    there being anything new for a student to read; a late penalty or an adjustment that
    moves a mark moves `final_grade`, so it is still in here.

    The team's score on a group assignment with a repo per team is left out (`specs`
    names those): a member's page has shown it only since 0.11.0, and keying the email on
    it would have mailed every such student once to say nothing new. Its total moves the
    final grade, which is in here."""
    team_repo = {
        slug
        for slug, spec in (specs or {}).items()
        if spec.is_group and spec.creates_unit_repos
    }
    projection = {
        slug: {
            key: view[key]
            for key in _GRADER_KEYS
            if key in view and not (key == "score" and slug in team_repo)
        }
        for slug, view in book.items()
    }
    text = json.dumps(
        _canonical({slug: keys for slug, keys in projection.items() if keys}),
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return MARKS_DIGEST_PREFIX + content_hash(text)


# The data file of a gradebook.
GRADES_DATA = "grades.yml"


def _sheets_changed_at(wd: Path, slugs: Iterable[str]) -> dict[str, datetime]:
    """When each sheet last changed, off the clone's own history: the committer date,
    which is the moment the console's `returned` state compares a gradebook's record with.
    A sheet whose date cannot be read is left out (its records are simply not re-stamped).
    """
    out: dict[str, datetime] = {}
    for slug in slugs:
        code, text = git(
            "log", "-1", "--format=%cI", "--", f"{SHEETS_DIR}/{slug}.yml", cwd=str(wd)
        )
        if code != 0:
            continue
        try:
            out[slug] = datetime.fromisoformat(text.strip())
        except ValueError:
            continue
    return out


def _stale_record(
    entry: tuple[str, str, str] | None, book: dict[str, dict], changed: dict
) -> bool:
    """Whether a gradebook record that still matches its content predates a later commit
    to one of the sheets it is rendered from. Its moment then says "current as of" too
    early, and the console reads the assignment as still being marked for good, since a
    sheet commit that moves no mark (a comment, a reorder) rewrites no gradebook."""
    try:
        at = datetime.fromisoformat(entry[1]) if entry else None
    except ValueError:
        return False
    if at is None:
        return False
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return any(changed[slug] > at for slug in book if slug in changed)


def _gradebook_files(
    handle: str, book: dict[str, dict], titles: dict[str, str]
) -> dict[str, bytes]:
    """The two files a student's private gradebook holds: the data and the page."""
    return {
        GRADES_DATA: render_yaml({"student": handle, "assignments": book}).encode(),
        "README.md": render_readme(handle, book, titles).encode(),
    }


def _commit_record(
    semester_org: str, writes: dict[str, bytes], message: str, delete: list[str]
) -> bool:
    """Land `distributed.csv` and the semester grades file, with ONE retry on a fresh head.

    The ref update is deliberately not forced, so a commit that landed between reading the
    head and moving it makes this fail - and the quarter-hourly scheduler commits into the
    same repo (a snapshot, a grading sheet) all through the term. The cost of losing that
    race was not a lost file but a lost RECORD: a gradebook commit is guarded by a blob
    compare, but the emails are guarded only by this file, so the next run mailed every
    student again.

    A retry is safe because the paths are DISJOINT from anything the cron writes and
    `put_files` re-reads the head, rebuilds the tree and re-filters the no-ops on each
    call: the second attempt is the same commit against whatever landed meanwhile. Two
    attempts, not a ladder - a second loss is a fault to report, not a race to keep
    running."""
    for attempt in (1, 2):
        if put_files(semester_org, CONFIG_REPO, writes, message, delete=delete):
            return True
        if attempt == 1:
            log(
                f"  ({CONFIG_REPO} moved under this run - rebuilding the record commit)"
            )
    return False


# The commit message of the record written before the emails go (`_record_before_mail`).
INTENT_MESSAGE = "grades: record the emails about to be sent"


def _reachable(handles: list[str], students: list[roster.Student] | None) -> list[str]:
    """The handles the roster gives an email address - the ones a notification can go to."""
    emails = {
        s.github_handle.casefold()
        for s in students or []
        if s.github_handle and s.hertie_email
    }
    return [h for h in handles if h.casefold() in emails]


def _record_before_mail(
    semester_org: str,
    record: Distributed,
    pending: list[str],
    live: dict[str, str],
    now: str,
    registrar: bytes | None,
    retired: list[str],
) -> bool:
    """Commit `distributed.csv` with every pending address marked told BEFORE the mail
    goes, so a record commit that fails after the send can never mail anybody twice:
    without this the next run, and the scheduler's automatic return every quarter-hour,
    re-mailed everyone. The final commit (`_final_record`) resets the row of an address
    whose mail failed or raised, so only a runner killed between the two commits loses a
    notification (the gradebook still holds the marks).

    The semester grades file goes in the same commit: the gradebooks are written by now,
    and a scoped return refuses while `distributed.csv` has rows and the file is missing
    (`_already_returned`), so a run that died after this commit would otherwise wedge
    every later one. `retired` is the file's old name where the clone still has it
    (`records.RETIRED_SEMESTER_GRADES`), deleted in the commit that writes the new one."""
    intent = dict(record)
    for handle in pending:
        intent[(handle, "", CHANNEL_EMAIL)] = (live[handle], now, "")
    writes = {DISTRIBUTED_PATH: dump_distributed(intent).encode()}
    if registrar is not None:
        writes[SEMESTER_CSV_NAME] = registrar
    return _commit_record(
        semester_org,
        writes,
        f"{INTENT_MESSAGE} ({len(pending)} notification(s))",
        retired if registrar is not None else [],
    )


def _final_record(
    semester_org: str,
    record: Distributed,
    registrar: bytes | None,
    *,
    marker: str | None,
    now: str,
    counts: dict[str, int],
    delete: list[str],
    retired: list[str],
) -> bool:
    """The semester grades file and the record of what went out, in ONE commit - together
    with the retired files this semester is migrating off, so the old and the new can
    never both be present for a reader to choose between - and, for a scoped return,
    `marker`'s returned record. `retired` (the semester grades file's old name, where the
    clone had it) goes only in a commit that writes the file under its new one."""
    writes = {DISTRIBUTED_PATH: dump_distributed(record).encode()}
    if registrar is None:
        # `roster.load` answers None for a roster it could not READ and [] for one with no
        # rows, and the export is one row per ENROLLED student - so regenerating it from
        # either would commit a header line over the file a registrar transcribes grades
        # from. Leaving it is the only safe answer; the run goes red and the next one
        # rebuilds it.
        log_err(
            f"the roster is empty or could not be read - {SEMESTER_CSV_NAME} left as it is"
        )
    else:
        writes[SEMESTER_CSV_NAME] = registrar
    if marker:
        # Returned, in the same commit as the record of it: the automatic return at
        # `marks_return_datetime` does not ask again.
        writes[marks_return_record(marker)] = (
            json.dumps({"returned": now}, indent=2) + "\n"
        ).encode()
    recorded = _commit_record(
        semester_org,
        writes,
        f"grades: distribute ({counts['gradebooks']} gradebook(s), "
        f"{counts['emails']} email(s))",
        [*delete, *(retired if registrar is not None else [])],
    )
    if not recorded:
        log_err(
            f"grades were sent but {DISTRIBUTED_PATH} could not be written - the "
            f"emails were recorded before they went, so the next run re-sends none"
        )
    return recorded


def _returned_units(
    specs: dict[str, SheetSpec],
    sheets: dict[str, dict],
    books: dict[str, dict[str, dict]],
    live: dict[str, str],
) -> list[tuple[SheetSpec, str]]:
    """`(spec, unit repo)` for every unit whose marks this run's gradebooks now hold: a
    member's book is live and carries a final grade for that assignment. Only shapes with
    a Submission receipts issue of their own, since the note goes there."""
    out: list[tuple[SheetSpec, str]] = []
    held = {h.casefold() for h in live}
    by_fold = {h.casefold(): h for h in books}
    for slug in sorted(sheets):
        spec = specs[slug]
        if not (spec.has_receipts_issue and spec.creates_unit_repos):
            continue
        for unit, block in ((sheets[slug] or {}).get(spec.container_key) or {}).items():
            if not isinstance(block, dict):
                continue
            members = (block.get("members") or {}) if spec.is_group else {unit: None}
            if any(
                str(m).casefold() in held
                and "final_grade"
                in (books.get(by_fold.get(str(m).casefold(), "")) or {}).get(slug, {})
                for m in members
            ):
                out.append((spec, submission_repo(slug, str(unit))))
    return out


def _post_returned_notes(
    semester_org: str,
    units: list[tuple[SheetSpec, str]],
    listed: dict[str, dict] | None,
) -> int:
    """Post `MARKS_RETURNED_NOTE` on each unit's Submission receipts issue, once per assignment
    (`marks_returned_marker`). Only on an issue that already exists and a repo the listing
    still says is private - the same guard every write into a Submission receipts issue takes.
    Returns how many units now carry the note. Never fatal: the gradebook is the record."""
    posted = 0
    for spec, repo in units:
        if receipts_thread_policy(spec, listed, repo) == THREAD_NONE:
            continue
        found = find_receipts_issue(semester_org, repo)
        if isinstance(found, IssueLookupFailed) or found is None:
            log_person(
                f"    [skip] {semester_org}/{repo}: no Submission receipts issue for the note"
            )
            continue
        if post_marked_comment(
            semester_org,
            repo,
            found[0],
            MARKS_RETURNED_NOTE,
            marks_returned_marker(spec.slug),
        ):
            posted += 1
            log_person(f"    marks-returned note on {semester_org}/{repo}#{found[0]}")
    return posted


def feedback_text(book: dict[str, dict], titles: dict[str, str]) -> str:
    """The feedback in one student's gradebook, as plain text for an email: one paragraph
    per assignment that has any - the overall feedback, then each question's. "" when
    there is none."""
    parts: list[str] = []
    for slug in sorted(book):
        view = book[slug]
        said = [
            str(view[key]).strip()
            for key in ("feedback", "team_feedback")
            if not is_blank(view.get(key))
        ]
        said += [
            f"{name}: {text}"
            for name, text in (view.get(QUESTION_FEEDBACK_KEY) or {}).items()
        ]
        if said:
            parts.append(f"{titles.get(slug) or slug}:\n" + "\n\n".join(said))
    return "\n\n".join(parts)


def distribute(
    semester_org: str,
    notify: bool = True,
    dry_run: bool = False,
    *,
    receipt_note: bool = False,
    include_feedback: bool = False,
    per_question: bool = True,
    assignment: str | None = None,
    sched: schedule.Schedule | None = None,
) -> int:
    """Send every mark a grader has written where it has to go: each student's private
    gradebook, the semester grades file, and an email saying there is something new to read.

    ONE place a mark is written, and it is the gradebook. Nothing is posted into a
    submission repo: a thread in a repo the toolkit does not own the visibility of is a
    mark one edited `visibility:` line publishes, and a student looking for a grade should
    have one address rather than one per assignment. The per-repo issue is still there and
    still carries the submission receipts (`collect._post_receipts`); it carries no mark.

    ONE clone of semester-config and one pass over it - the sheets and `distributed.csv`
    are both read locally, so the only per-student calls left are the
    writes. Every one of those is skipped when `distributed.csv` says the same content has
    already gone out, which is what makes a correction to one grade reach one student.

    A student's gradebook is the whole of what they have been given, so it is never
    rendered from one selected sheet alone: that silently deleted every other assignment
    from every gradebook it touched. Unscoped, every sheet is rendered. `assignment` (a
    semester-side name) scopes a run to that assignment's marks: the gradebook is rendered
    from its sheet plus the sheets ALREADY RETURNED - the assignments the semester grades
    file already carries a column for, which is what the last real run told students -
    so nothing already given is taken away and nothing not yet returned goes out. The
    semester grades file is rendered from the same books.

    A preview - the default - writes no grades and sends nothing: it prints the counts a
    grader checks before pressing it for real, and posts the per-student detail as an
    issue in the private semester-config (`_preview`).

    `receipt_note` also posts `MARKS_RETURNED_NOTE` once per assignment on each returned
    unit's Submission receipts issue; `include_feedback` puts the feedback text into the email. Both
    off by default. The marks and feedback per question are withheld for every assignment
    whose run setting `marks_per_question` is false, and - `per_question` off, a one-run
    override - for the assignment this run returns (every assignment, unscoped)."""
    # ONE listing of the semester for the whole run: it answers "does this student already
    # have a gradebook?". A dry run writes nothing and needs none.
    listed = None if dry_run else listing_by_name(semester_org)
    provisioning_failed = bool(
        ensure_gradebooks(semester_org, dry_run=dry_run, existing=listed)
    )
    course_org = course_org_for_semester(semester_org)
    sched = sched if sched is not None else schedule.load(semester_org)
    students = roster.load(semester_org)
    moment = datetime.now(UTC)
    now = moment.isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "cfg"
        if not clone(semester_org, CONFIG_REPO, wd):
            log_err(f"could not clone {semester_org}/{CONFIG_REPO}")
            return 1
        try:
            sheets = load_sheets(wd)
        except SheetUnreadable as exc:
            # Nothing goes out, rather than everything but this one: a grader fixes the
            # file and presses the button again, where a partial send would have to be
            # reconciled student by student.
            log_err(f"{exc} - nothing sent; fix the file and run this again")
            return 1
        if not sheets:
            log_err(
                f"no {SHEETS_DIR}/ in {semester_org}/{CONFIG_REPO} - hand out an "
                f"assignment (which creates its grading sheet) first"
            )
            return 1
        if assignment:
            if assignment not in sheets:
                log_err(f"{assignment} has no grading sheet in {SHEETS_DIR}/")
                return 1
            returned = _already_returned(wd)
            if returned is None:
                # Not knowing what went out would take it away again: every gradebook
                # rendered without an assignment already returned loses it.
                log_err(
                    f"{SEMESTER_CSV_NAME} is missing or cannot be read, so which "
                    f"assignments were already returned is not known - nothing sent. "
                    f"Return every assignment once (no `assignment`), which rewrites it"
                )
                return 1
            sheets = {
                name: sheet
                for name, sheet in sheets.items()
                if name == assignment or name in returned
            }
            log(f"  returning {assignment} (with {len(sheets) - 1} already returned)")
        specs = sheet_specs(course_org, sched)
        sources: dict[str, tuple[SheetSpec, dict]] = {}
        for slug, sheet in sheets.items():
            specs.setdefault(slug, _spec_from_sheet(slug, sheet))
            sources[slug] = (specs[slug], sheet)
        refused = sorted(slug for slug in sources if specs[slug].not_migrated)
        if refused:
            # Nothing goes out, as for an unreadable sheet: its shape and late rule would
            # be defaults nobody wrote, and leaving it out of the books would take an
            # already-returned mark away from every gradebook.
            log_err(
                f"{', '.join(refused)}: {GRADING_FILE} is refused (an old key, or a run "
                f"setting) - nothing sent; see its digest issue"
            )
            return 1
        misshapen = sorted(slug for slug in sources if _misshapen(*sources[slug]))
        if misshapen:
            # As for an unreadable sheet: a team whose `members:` cannot be read is a
            # team whose marks reach nobody, so nothing goes out until it is fixed. The
            # sheet's digest names the line.
            log_err(
                f"{', '.join(misshapen)}: the marking sheet is not one entry per "
                f"student or team - nothing sent; fix the file and run this again"
            )
            return 1
        titles = {slug: specs[slug].title for slug in sources}
        withheld = {
            slug for slug, (spec, _) in sources.items() if not spec.marks_per_question
        }
        if not per_question:
            withheld |= {assignment} if assignment else set(sources)
        books, unknown = _on_the_roster(build_gradebooks(sources, withheld), students)
        distributed = _read_distributed(wd)
        retired = _retired_gradebook_files(wd)
        old_grades = records.RETIRED_SEMESTER_GRADES
        retired_grades = [old_grades] if (wd / old_grades).is_file() else []
        changed = {} if dry_run else _sheets_changed_at(wd, sheets)
        export = _told_grades(wd)
        # None: the export is missing or unreadable while gradebooks went out already.
        told_known = _already_returned(wd) is not None

    # What a grader typed outranks the arithmetic, as one reason per handle.
    held = _hold_undecided(
        books,
        {
            slug: undecided_marks(specs[slug], sheet)
            | sheet_hold_reasons(specs[slug], sheet)
            for slug, sheet in sheets.items()
        },
    )
    if held and not told_known and not dry_run:
        # `_hold_returned` cannot tell a held mark already sent from a new one without the
        # export, and taking a sent mark out of the book takes it away from the student.
        log_err(
            f"a mark is held and {SEMESTER_CSV_NAME} is missing or cannot be read, so "
            f"whether it was already sent is not known - nothing sent. Settle the held "
            f"marks (the preview lists them), then run this again"
        )
        return 1
    kept = _hold_returned(books, held, export[0])
    log_step(f"Distributing {len(books)} gradebook(s) in {semester_org}")
    record: Distributed = dict(distributed)
    # Key ORDER is the order the spec prints the `Done` line in, because that line is a
    # JSON dump of this dict and a grader reads it as text. `unknown` is the one key the
    # spec does not name: marks for handles nobody enrolled are worth a count, and it is
    # slotted where it disturbs the spec's own sequence least.
    counts = {
        "gradebooks": 0,
        "emails": 0,
        "held": sum(len(whose) for whose in held.values()),
        "unknown": unknown,
        "failed": 0,
    }
    if assignment and held.get(assignment) and not dry_run:
        # A count: which marks are held is the sheet digest's to say, line by line.
        log(
            f"  {assignment} is not recorded as returned: "
            f"{plural(len(held[assignment]), 'mark')} held until the sheet is fixed, "
            f"and the automatic return asks again"
        )
    for slug in sorted(held):
        for handle, (_unit, reason) in sorted(held[slug].items()):
            log_person(
                f"  [hold] {slug} for {handle} - {HOLD_REASONS[reason]}; nothing is sent "
                f"until it is settled in the sheet"
            )
    undue = _undue_marks(specs, books, moment)
    if undue:
        # A count, in both the dry run and the real one, and never a block: the gradebook
        # row will read `not submitted` for work that is sitting in the repo, because
        # nothing derives the submission facts until the due date has passed.
        log(
            f"  WARNING: {undue} mark(s) sent before the due date - the "
            f"submission facts are not derived yet"
        )

    # 1. The private gradebook: grades.yml and README.md in ONE commit per student, so a
    #    student never sees a page that disagrees with the data beside it.
    #
    #    A handle earns its place in `live` only where the gradebook really does hold that
    #    content. `live` is what step 2 keys the email on, so a student whose write FAILED
    #    must not be told to go and read a page that never changed - and, worse, have that
    #    telling recorded, which would stop them being told when it lands.
    #    TWO digests per student, because the two channels are answering different
    #    questions. The COMMIT is keyed on the whole book, so every wording change the
    #    toolkit makes to the README lands; the EMAIL is keyed on what a grader wrote
    #    (`_marks_digest`), so "there is something new to read" means a MARK moved. Keyed
    #    on one hash, a single standing sentence added to the page re-mailed every student
    #    in every live semester to tell them nothing.
    live: dict[str, str] = {}
    for handle in sorted(books):
        files = _gradebook_files(handle, books[handle], titles)
        digest = content_hash("".join(f.decode() for f in files.values()))
        marks = _marks_digest(books[handle], specs)
        entry = record.get((handle, "", CHANNEL_GRADEBOOK))
        if entry and entry[0] == digest:
            live[handle] = marks
            # Unchanged, but checked against a newer sheet: the record's moment moves up,
            # so `returned` holds through an edit that changed no mark. Never for a
            # student with a mark held, whose return is not done.
            if not any(handle in whose for whose in held.values()) and _stale_record(
                entry, books[handle], changed
            ):
                record[(handle, "", CHANNEL_GRADEBOOK)] = (digest, now, entry[2])
            continue
        if dry_run:
            live[handle] = marks
            counts["gradebooks"] += 1
            continue
        if put_files(
            semester_org,
            f"{GRADEBOOK_PREFIX}{handle}",
            files,
            "grades: update",
            person=True,
        ):
            record[(handle, "", CHANNEL_GRADEBOOK)] = (digest, now, "")
            live[handle] = marks
            counts["gradebooks"] += 1
            log_person(f"  [ok] {GRADEBOOK_PREFIX}{handle}")
        else:
            counts["failed"] += 1

    # 2. Who still needs telling. Keyed on the MARKS, so a student whose book changed only
    #    because the page around their grades was reworded is not emailed, and one whose
    #    email FAILED last time is.
    pending: list[str] = []
    for handle, marks in sorted(live.items()):
        if record.get((handle, "", CHANNEL_EMAIL), ("",))[0] == marks:
            continue
        if not _has_mark(books[handle]):
            # Nothing a grader wrote yet: the book moved on the toolkit's facts alone (a
            # submission time, the points available). Not recorded either, so the run
            # that brings the first mark is the one that tells them.
            continue
        pending.append(handle)

    if dry_run and receipt_note:
        log(
            f"  would post the marks-returned note on up to "
            f"{len(_returned_units(specs, sheets, books, live))} Submission receipts issue(s)"
        )
    if dry_run:
        previewed = _preview(
            semester_org,
            sheets,
            specs,
            books,
            counts,
            pending if notify else [],
            held,
            students or [],
            export,
            first_email={
                h for h in pending if not record.get((h, "", CHANNEL_EMAIL), ("",))[0]
            },
            moment=moment,
            notify=notify,
        )
        # `not students` on both exits - no rows AND no file, for the same reason:
        # `ensure_gradebooks` passes over either rather than redden the nightly sync it now
        # also runs on, so saying so is this run's own job. Distribute is about to write a
        # mark per student, and a header-only students.csv is not a semester nobody enrolled
        # in by the time marks exist: `_on_the_roster` keeps only the books belonging to
        # somebody the roster knows, so an empty roster drops EVERY mark in the run as
        # `unknown`. This exit is the only signal that happened.
        if provisioning_failed or not students or not previewed:
            return 1
        return return_summary(counts, len(pending) if notify else 0, dry_run=True)

    # Only a student the roster can reach is recorded before the mail: a withdrawn one
    # (no roster email) is never mailed, and a row for them would be a commit about nobody.
    reachable = _reachable(pending, students) if notify else []
    # What the final commit writes, whatever happens between here and there.
    registrar = (
        render_registrar_csv(students, _with_told(books, kept)).encode()
        if students
        else None
    )
    if reachable and not _record_before_mail(
        semester_org, record, reachable, live, now, registrar, retired_grades
    ):
        log_err(
            f"{DISTRIBUTED_PATH} could not be written before the emails went, so none "
            f"was sent - the next run sends them"
        )
        return return_summary(counts, 0, dry_run=False, code=1)

    def finish(failed_mail: int, told: list[str], raised: bool = False) -> bool:
        """The final record: `told` as told, and every other address back as it was, so
        an email that did not go is retried by the next Return marks run. A run that
        `raised` never marks the assignment returned, and nor does one that held any of
        its marks: the automatic return asks again every tick, and each later run sends
        only what `distributed.csv` says has not gone out yet."""
        for handle in told:
            record[(handle, "", CHANNEL_EMAIL)] = (live[handle], now, "")
        counts["emails"] = len(told)
        return _final_record(
            semester_org,
            record,
            registrar,
            # Held back when not one email went (a token fault, no mail configured):
            # the automatic return then asks again, and the reset rows mean it cannot
            # mail anybody twice. One refused address in a class that was mailed does
            # not hold it back, or it would re-fire every quarter-hour.
            marker=(
                assignment
                if assignment
                and not raised
                and not counts["failed"]
                and not (failed_mail and not told)
                and not held.get(assignment)
                else None
            ),
            now=now,
            counts=counts,
            delete=retired,
            retired=retired_grades,
        )

    failed_mail, told = 0, []
    try:
        if notify and pending:
            failed_mail, told = _email_updates(
                semester_org,
                pending,
                dry_run=False,
                feedback=(
                    {h: feedback_text(books[h], titles) for h in pending}
                    if include_feedback
                    else None
                ),
            )
        if receipt_note:
            counts["receipt_notes"] = _post_returned_notes(
                semester_org, _returned_units(specs, sheets, books, live), listed
            )
    except BaseException:
        # The send raised (a Graph token fault) or something after it did: whatever was
        # not confirmed sent counts as not sent, and the record is still made, so the
        # rows written before the mail are reset rather than left claiming it went.
        finish(len(reachable) - len(told), told, raised=True)
        raise
    recorded = finish(failed_mail, told)
    # The dry run's preview is out of date once anything has gone out. Not a reason to red
    # the run: a preview left open is closed by the next real one.
    close_issues_titled(f"{semester_org}/{CONFIG_REPO}", PREVIEW_TITLE, PREVIEW_SENT)
    # Counts only: this workflow's log is world-readable and every target here is a
    # student. The per-target lines above went through log_person.
    log_ok(f"Done - {json.dumps(counts)}")
    code = (
        1
        if provisioning_failed
        or counts["failed"]
        or failed_mail
        or not recorded
        or not students
        else 0
    )
    return return_summary(counts, counts["emails"], dry_run=False, code=code)


def return_summary(
    counts: dict[str, int], emails: int, dry_run: bool, code: int = 0
) -> Summary:
    """Return marks' sentence, off distribute's counts. Counts only - every target is a
    student, and this lands in a public annotation."""
    books = plural(counts["gradebooks"], "marks repo")
    mails = plural(emails, "email")
    if dry_run:
        text = f"Preview: {books} would be updated and {mails} sent"
    elif not counts["gradebooks"] and not emails:
        text = "No new marks to return"
    else:
        text = f"Marks returned: {books} updated, {mails} sent"
    if counts.get("held"):
        text += f"; {plural(counts['held'], 'mark')} held until the sheet is fixed"
    out = {k: v for k, v in counts.items() if isinstance(v, int)}
    conclusion = (
        "nothing_to_do"
        if not dry_run and not code and not counts["gradebooks"] and not emails
        else None
    )
    return Summary(f"{text}.", out, code=code, conclusion=conclusion)


# The dry run's per-student detail: an issue in the PRIVATE semester-config, found by
# this exact title, rewritten by every dry run and closed by the real one.
PREVIEW_TITLE = "Distribute grades preview"
PREVIEW_SENT = (
    "Grades sent by a real run of Distribute grades - this preview is out of date."
)
# GitHub refuses an issue body longer than this.
_ISSUE_BODY_CAP = 65_536


def _preview(
    semester_org: str,
    sheets: dict[str, dict],
    specs: dict[str, SheetSpec],
    books: dict[str, dict[str, dict]],
    counts: dict[str, int],
    emailed: list[str],
    held: dict[str, dict[str, tuple[str, str]]],
    students: list[roster.Student],
    told: tuple[dict[str, dict[str, str]], set[str]],
    *,
    first_email: set[str],
    moment: datetime,
    notify: bool,
) -> bool:
    """The dry run's report: counts a grader can check in the log, and the detail behind
    them in the preview issue. False when the issue could not be written.

    No names, and no marks, in the log: this is the log of a workflow that runs in a
    PUBLIC repo. Who would get what goes into the issue, in the private semester-config."""
    told_grades, columns = told
    not_marked = {slug: _not_marked(specs[slug], sheets[slug]) for slug in sheets}
    ignored = {
        slug: ignored_late_penalties(specs[slug], sheets[slug] or {}) for slug in sheets
    }
    exported = {slug for book in books.values() for slug in book}
    for slug in sorted(sheets):
        spec = specs[slug]
        units = (sheets[slug] or {}).get(spec.container_key) or {}
        views = [book[slug] for book in books.values() if slug in book]
        # Only rows that have been MARKED are counted as grades: an unmarked one is
        # neither a grade this run would derive nor a decision anybody has to take, and
        # counting it as either told a grader the sheet was further on than it is. The
        # held ones are no longer in the books - they are added back to the head count
        # here, because they are still students on this assignment.
        marked = [v for v in views if "final_grade" in v]
        hand = len(held.get(slug, {}))
        team_clause = f" in {len(units)} team(s)" if spec.is_group else ""
        log(
            f"  {slug}: {len(views) + hand} student(s){team_clause} · {len(marked)} "
            f"final grade(s) derived, {_adjusted_count(spec, sheets[slug])} adjusted, "
            f"{hand} held for a hand decision"
        )
        # Named, not just counted: "1 held" tells a grader to go looking without saying
        # what for, and every one of these is a thing they typed and can fix in a minute.
        tally = Counter(reason for _unit, reason in held.get(slug, {}).values())
        for reason, count in sorted(tally.items()):
            log(f"    {count} with {HOLD_REASONS[reason]}")
        partly = sum(1 for blank in not_marked[slug].values() if blank)
        if partly:
            log(f"    {partly} unit(s) have unmarked questions")
        if ignored[slug]:
            log(
                f"    {ignored[slug]} unit(s) carry a late_penalty, but the assignment "
                f"has no late policy - ignored"
            )
        if stray := _undeclared_feedback(spec, units):
            log(
                f"    WARNING: {stray} unit(s) give feedback on a question the "
                f"assignment does not declare - it is sent under the name typed"
            )
        if slug in exported and slug not in columns:
            log(f"  {SEMESTER_CSV_NAME}: would gain column {slug}")
    if counts["unknown"]:
        # Counted, not named: a handle nobody enrolled is still somebody's.
        log(f"  {counts['unknown']} mark(s) for handles not on the roster - ignored")
    log(
        f"  would update {counts['gradebooks']} gradebook(s) and email "
        f"{len(emailed)} student(s)"
    )
    repo = f"{semester_org}/{CONFIG_REPO}"
    wrote = upsert_issue(
        repo,
        PREVIEW_TITLE,
        _preview_body(
            semester_org,
            specs,
            books,
            emailed,
            held,
            students,
            told_grades,
            not_marked,
            ignored,
            first_email=first_email,
            moment=moment,
            notify=notify,
        ),
    )
    if not wrote.errors:
        log(f"  Who gets what: {wrote.url or f'{PREVIEW_TITLE} in {repo}'}")
    log_ok("PREVIEW - no grades written, no mail sent")
    return not wrote.errors


# Each hold reason as the preview says it of one student, after their handle and name.
# `HOLD_REASONS` is the log's noun phrase; this is the sentence a grader reads in the
# issue, in their own words for the sheet rather than the pipeline's.
_HOLD_PREVIEW = {
    "penalty": "was late, and their mark is not a number a late penalty can come off",
    "score": "has a question mark that is not a number",
    "adjustment": "has an adjustment that is not a number",
    "late_penalty": "has a late penalty that is not apply, waive or a percentage",
    "question": "has a mark for a question the assignment does not have",
    "duplicate": "is in more than one team",
    "returned": "has another mark held that they were already sent, so nothing of "
    "theirs changes until it is settled",
}


def _preview_body(
    semester_org: str,
    specs: dict[str, SheetSpec],
    books: dict[str, dict[str, dict]],
    emailed: list[str],
    held: dict[str, dict[str, tuple[str, str]]],
    students: list[roster.Student],
    told: dict[str, dict[str, str]],
    not_marked: dict[str, dict[str, list[str]]],
    ignored: dict[str, int] | None = None,
    *,
    first_email: set[str],
    moment: datetime,
    notify: bool,
) -> str:
    """The preview issue, laid out by what a grader has to do: what to fix, what is not
    marked yet, which grades would change, and who would be emailed and why. A grade is
    listed only where it differs from the semester grades file, which is what the last real
    run sent. If it does not fit in one issue, what is left out is counted."""
    names = {s.github_handle.casefold(): s.name for s in students if s.github_handle}

    def who(handle: str) -> str:
        name = names.get(handle.casefold(), "")
        return f"`{handle}` ({name})" if name else f"`{handle}`"

    fixes = [
        f"- **{slug}** · {who(handle)} {_HOLD_PREVIEW[reason]}. Fix it in "
        f"`{SHEETS_DIR}/{slug}.yml`."
        for slug, whose in sorted(held.items())
        for handle, (_unit, reason) in sorted(whose.items())
    ]

    unmarked = []
    for slug, units in sorted(not_marked.items()):
        noun = ("team", "teams") if specs[slug].is_group else ("student", "students")
        by_blank: dict[tuple[str, ...], list[str]] = {}
        for unit, blank in sorted(units.items()):
            by_blank.setdefault(tuple(blank), []).append(unit)
        # The rows with no mark at all first: they are the bigger job.
        for blank, whose in sorted(by_blank.items(), key=lambda kv: (bool(kv[0]), kv)):
            what = f"{', '.join(blank)} blank" if blank else "no mark yet"
            unmarked.append(
                f"- **{slug}** · {plural(len(whose), *noun)}: "
                f"{', '.join(f'`{u}`' for u in whose)} ({what})"
            )

    changes = []
    changed: set[str] = set()
    for handle, book in sorted(books.items()):
        was = told.get(handle.casefold(), {})
        for slug, view in sorted(book.items()):
            grade = str(view.get("final_grade", ""))
            if grade and grade != was.get(slug):
                changed.add(handle)
                changes.append(
                    (
                        slug,
                        handle,
                        f"- **{slug}** · {who(handle)} · {grade} "
                        + (f"(was {was[slug]})" if slug in was else "(new)"),
                    )
                )

    mails = []
    for handle in sorted(emailed):
        if handle in first_email:
            why = "their first grades email"
        elif handle in changed:
            why = "a grade changed"
        else:
            why = "feedback changed"
        shown = " · ".join(
            f"{slug} {view['final_grade']}"
            for slug, view in sorted(books.get(handle, {}).items())
            if not is_blank(view.get("final_grade"))
        )
        mails.append(
            f"- {who(handle)} - {why}.\n"
            f"  Their gradebook shows: {shown or 'feedback only, no grade yet'}"
        )

    sections = [
        (
            f"### ⚠️ Fix these first - held back, not sent ({len(fixes)})",
            fixes,
            "Nothing - no mark is held back.",
        ),
        (
            f"### Not marked yet ({sum(len(u) for u in not_marked.values())})",
            unmarked,
            "Nothing - every row in every sheet has a mark.",
        ),
        (
            f"### Grades that would change ({len(changes)})",
            [line for _slug, _handle, line in sorted(changes)],
            "Nothing new or changed since grades were last sent.",
        ),
        (
            f"### Students who would be emailed ({len(mails)})",
            mails,
            "Nothing - `notify` is unticked, so nobody is emailed."
            if not notify
            else "Nothing - nobody has a new mark to be told about.",
        ),
    ]
    head = [
        f"**Nothing has been sent.** Preview: {moment.day} {moment:%b %H:%M} UTC.",
        (
            "This is what running Distribute grades for real (with `preview` "
            "unticked) would do now."
        ),
        "Each preview replaces this text; the real run closes this issue.",
    ]
    # A `late_penalty` on an assignment with no late policy: ignored, and said so.
    no_policy = [
        f"- **{slug}** has no late policy, so the `late_penalty` on "
        f"{plural(n, 'unit', 'units')} is ignored: nothing comes off."
        for slug, n in sorted((ignored or {}).items())
        if n
    ]
    if no_policy:
        head += ["", *no_policy]
    tail = []
    if emailed:
        # Rendered exactly as the send renders it, course name and all: a preview that
        # showed the generic wording while the real mail named the course was reviewing
        # text nobody would ever receive.
        subject, body = sample_message(
            semester_org, course_name_or(semester_org=semester_org)
        )
        tail = [
            "",
            "<details><summary>The email they would get</summary>",
            "",
            "```",
            f"Subject: {subject}",
            "",
            body.rstrip(),
            "```",
            "",
            "</details>",
        ]

    def render(shown: list[int]) -> str:
        out = list(head)
        for (heading, items, nothing), n in zip(sections, shown, strict=True):
            out += ["", heading]
            if not items:
                out.append(nothing)
                continue
            out += items[:n]
            if n < len(items):
                out.append(
                    f"_{len(items) - n} more not shown - the list is longer than one "
                    f"issue can hold._"
                )
        return "\n".join(out + tail)

    # Every heading, its empty line or its count of what was left out, and the email
    # always fit; the bullets fill what is left, in order, and stop at the first that
    # does not.
    shown = [0] * len(sections)
    room = _ISSUE_BODY_CAP - len(render(shown))
    for i, (_heading, items, _nothing) in enumerate(sections):
        for item in items:
            if len(item) + 1 > room:
                return render(shown)
            room -= len(item) + 1
            shown[i] += 1
    return render(shown)


def update_message(
    student: roster.Student,
    semester_org: str,
    course_name: str = "",
    feedback: str = "",
) -> mailer.Message:
    """The 'your grades have been updated' email for one student: (to, subject, body).

    The course goes in the SUBJECT as well as the body: the inbox list is where a student
    taking several of these actually tells them apart, and by the time they have opened it
    the body is redundant. A course with no name yet degrades to `course_phrase`'s plain
    "the course" in the body, and to the generic subject - never a blank, and never a
    literal placeholder."""
    url = f"https://github.com/{semester_org}/{GRADEBOOK_PREFIX}{student.github_handle}"
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"Your grades for {course_phrase(course_name)} have been updated. View them in "
        f"your private gradebook:\n"
        f"  {url}\n"
    )
    if feedback:
        body += f"\nFeedback from your markers:\n\n{feedback}\n"
    subject = (
        f"Your grades for {course_name} have been updated"
        if course_name
        else "Your grades have been updated"
    )
    return (student.hertie_email, subject, body)


def sample_message(
    semester_org: str, course_name: str = "", feedback: bool = False
) -> tuple[str, str]:
    """The notification's `(subject, body)` rendered with PLACEHOLDERS, for the preview.

    `update_message` with a placeholder in place of a student - see `mailer.sample_message_of`."""
    return mailer.sample_message_of(
        lambda student: update_message(
            student, semester_org, course_name, "<feedback>" if feedback else ""
        ),
        github_handle="<handle>",
    )


def sample_body(
    semester_org: str, course_name: str = "", feedback: bool = False
) -> str:
    """The body alone - what `send_bulk` prints beneath a dry-run send."""
    return sample_message(semester_org, course_name, feedback)[1]


def _email_updates(
    semester_org: str,
    handles: list[str],
    dry_run: bool = False,
    feedback: dict[str, str] | None = None,
) -> tuple[int, list[str]]:
    """Email each student a 'grades updated' notification to their Hertie email address,
    linking to their private gradebook repo (the grade's source of truth).

    Returns `(how many FAILED, which handles were told)`. `distribute` exits on the first
    and records the second: the grades themselves are already pushed by this point, so a
    mail failure is not a reason to undo anything - but a student who never got the
    notification does not know to look, and a green run told nobody."""
    # Fold-keyed: the gradebook names come from what a marker typed into the sheet and the
    # roster's casing is its own, so a case-only difference used to mean a student was
    # silently never told their grades had landed.
    students = roster.load(semester_org)
    if students is None:
        # Distinct from an empty roster: unreadable must red, as it does in enrol_codes.run.
        log_err(
            f"roster in {semester_org} could not be read - "
            f"{len(handles)} notification(s) not sent."
        )
        return len(handles), []
    by_handle: dict[str, roster.Student] = {}
    for s in students:
        if s.github_handle:
            by_handle.setdefault(s.github_handle.casefold(), s)
    # Name the course in the body - a student taking several of these can't tell one
    # "your grades have been updated" from another. Read live from the course org's
    # dsl-course.yml; a course that carries no name yet keeps the generic wording rather
    # than emailing a blank.
    course_name = course_name_or(semester_org=semester_org)
    messages = []
    # Keyed on the ADDRESS, holding every handle that maps to it: two roster rows sharing
    # an address (one student, two accounts) would otherwise record only the last, leaving
    # the first permanently unrecorded and re-notified on every run.
    handles_for: dict[str, list[str]] = {}
    for handle in handles:
        student = by_handle.get(handle.casefold())
        if not student or not student.hertie_email:
            continue
        email = student.hertie_email.strip().casefold()
        if email in handles_for:
            handles_for[email].append(handle)
            continue
        handles_for[email] = [handle]
        messages.append(
            update_message(
                student, semester_org, course_name, (feedback or {}).get(handle, "")
            )
        )
    if not messages:
        # A withdrawn student is an ordinary state and must not red every distribution
        # from here on; a count says it happened without naming anyone.
        if handles:
            log_err(f"{len(handles)} gradebook(s) have no roster row with an email")
        return 0, []
    sent = mailer.send_bulk(
        messages,
        dry_run=dry_run,
        sample=sample_body(semester_org, course_name, feedback is not None),
    )
    failed = len(messages) - len(sent)
    if failed:
        log_err(f"{failed} of {len(messages)} grade notification(s) not sent")
    return failed, [
        h for to in sent for h in handles_for.get(to.strip().casefold(), [])
    ]
