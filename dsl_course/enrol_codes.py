"""dsl-course enrol-codes -- generate per-student enrolment codes and email them.

Students enrol by pasting a random, **non-PII** code (not their email) into the join
Join issue, so no personal data ever touches the public repo - and because the code is
unguessable, a classmate can't bind your roster row to their account. This one action:

    1. gives every students.csv row without one an `enrol_code` in the SYSTEM-OWNED
       semester-config/.system/roster.csv (idempotent), then
    2. emails each not-yet-onboarded student their code.

It writes only `.system/roster.csv`, never the students.csv faculty edit, so a roster push
never races it. Email reaches the student's university inbox (students.csv `email`),
replacing the
Excel -> Power Automate -> Outlook mail-merge. Reuses dsl_course.mailer.

Every roster row gets a code, auditors included - the code is how anyone onboards at all;
their `role` column is what routes them to the read-only `auditors` team on the way in.

The ordinary caller is not a person: a push to a semester's own students.csv, which its
semester-config dispatcher turns into a `send-codes` repository_dispatch at the course
org. That path passes `--dispatched-by`, because its semester name is untrusted input (see
`refuse_unregistered`), and it is unattended and for real - there is no preview mode.

The other caller is the console's "send new codes": `--resend-unjoined` mints a NEW code
for every roster row without a `github_handle`, so the old codes stop working, and emails
them. It previews with `--preview`, which counts and changes nothing (`resend_unjoined`).

Usage:
    python3 -m dsl_course.enrol_codes --semester-org hertie-dsl-demo-f2026 --no-preview
    python3 -m dsl_course.enrol_codes --semester-org hertie-dsl-demo-f2026 \\
        --resend-unjoined [--no-preview]
    python3 -m dsl_course.enrol_codes --semester-org hertie-dsl-demo-f2026 \\
        --dispatched-by hertie-dsl-demo-course-e1234 --no-preview

Bare, every one of these previews and sends nothing: acting takes `--no-preview`.
"""

from __future__ import annotations

import enum
import json
import re
import secrets
import sys
from datetime import UTC, datetime

from . import mailer, roster, status
from .course import CONFIG_REPO, course_phrase
from .discovery import (
    SEMESTERS_PATH,
    course_name_or,
    discover_semesters,
    join_issue_url,
    semester_is_live,
)
from .faults import Unusable
from .gh_contents import get_file_content, get_file_with_sha, put_file_as_read
from .log import (
    CLIParser,
    Summary,
    add_preview_flag,
    log,
    log_err,
    log_ok,
    log_person,
    log_step,
    plural,
)

# No ambiguous characters (0/O, 1/l/I) so a student can read the code off an email.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def make_code() -> str:
    return "dsl-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def write_column(
    semester_org: str,
    raw: str | None,
    sha: str,
    column: str,
    values: dict[str, str],
    message: str,
    replacing: str | None = "",
) -> str | None:
    """Commit one column of `.system/roster.csv` (`{email: value}`, `roster.fill_system`)
    without clobbering a concurrent edit - a Join issue binding a handle while this run is
    sending (`put_file_as_read`). `raw`/`sha` are the file as read (None/"" = absent).
    Returns the text AS COMMITTED, or None if no attempt was accepted.

    A refused write re-applies only the values THIS run produced onto the fresh text, and
    only into cells still holding `replacing`, so a value that arrived in between is left
    as it is. Which is why the committed TEXT is returned rather than a bare success:
    after a retry the roster can hold a different code from the one this run generated,
    and the caller must email what the roster holds."""

    def fill(text: str | None) -> str:
        return roster.fill_system(text, column, values, replacing)

    return put_file_as_read(
        semester_org,
        CONFIG_REPO,
        roster.SYSTEM_PATH,
        fill(raw),
        sha,
        fill,
        message,
    )


def _read(semester_org: str) -> tuple[str, str | None, str] | None:
    """`(students.csv, .system/roster.csv or None, its sha or "")`, or None when
    students.csv is absent (logged)."""
    students = get_file_content(semester_org, CONFIG_REPO, roster.STUDENTS_PATH)
    if students is None:
        log_err(
            f"Could not find {roster.STUDENTS_PATH} in {semester_org}/{CONFIG_REPO} - "
            f"bootstrap the semester first (bootstrap_course --semester)."
        )
        return None
    read = get_file_with_sha(semester_org, CONFIG_REPO, roster.SYSTEM_PATH)
    raw, sha = read if read is not None else (None, "")
    return students, raw, sha


def assign_codes(students: list[roster.Student], gen=make_code) -> int:
    """Fill enrol_code on rows that lack one (unique). Mutates rows; returns count added."""
    seen = {s.enrol_code for s in students if s.enrol_code}
    added = 0
    for s in students:
        if s.enrol_code:
            continue
        code = gen()
        while code in seen:
            code = gen()
        s.enrol_code = code
        seen.add(code)
        added += 1
    return added


def code_message(
    student: roster.Student,
    join_url: str,
    course_name: str = "",
    replaces: bool = False,
) -> mailer.Message:
    """The enrolment-code email for one student: (to, subject, body).

    `course_name` names the course in the subject and the opening line - a student
    reading several of these in one week needs to know which one this is, and the inbox
    list is where they tell them apart. It is optional because the name is
    read live from the course org's dsl-course.yml, which a half-configured course may
    not carry yet; the wording then falls back to "the course" rather than emailing a
    blank."""
    course = course_phrase(course_name)
    subject = (
        f"Your enrolment code for {course_name}"
        if course_name
        else "Your course enrolment code"
    )
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"To join {course} on GitHub, open a 'Join course' issue here:\n"
        f"  {join_url}\n\n"
        f"and paste this enrolment code when asked:\n\n"
        f"    {student.enrol_code}\n\n"
        f"Whichever GitHub account opens the issue is linked to your Hertie email "
        f"address automatically.\n"
    )
    if replaces:
        body += "\nThis code replaces any code you were sent before, which no longer works.\n"
    return (student.email, subject, body)


class Outcome(enum.Enum):
    """What one `run` came to. Richer than an exit code, because the reasons a send did
    not happen are not interchangeable: two of them mean nothing is outstanding, and the
    rest are each a different thing for a person to go and fix. Each value is the phrase a
    log line puts after "no codes went out:" (see `reds_the_run`).
    """

    SENT = "the codes were emailed"
    NOTHING_TO_SEND = "every student who needs a code already has one"
    NO_ROSTER = "students.csv could not be read"
    # The one outcome that is none of those and still leaves the run green: the file IS
    # there and says something no parser can use. See `reds_the_run`.
    UNUSABLE_ROSTER = "students.csv cannot be read as written"
    # A roster with nothing but its header - a semester bootstrapped and not yet enrolled.
    EMPTY_ROSTER = "students.csv has no rows yet"
    NO_TRANSPORT = "no mail transport is configured (the GRAPH_* secrets)"
    FAILED = "the send failed"


def run(semester_org: str) -> Outcome:
    read = _read(semester_org)
    if read is None:
        return Outcome.NO_ROSTER
    # The roster file's sha is kept so the write below is refused if a Join issue commits
    # to it while this run is generating and mailing codes (see write_column).
    text, raw, raw_sha = read
    try:
        students = roster.parse(text, raw)
    except Unusable as exc:
        # A `;`-delimited Excel export, or a header row somebody deleted. Nothing here can
        # act on it - and nothing here should red for it either: the roster is faculty's
        # to fix, and the same push that fired this send fires the scheduler's dispatcher,
        # which opens the students.csv digest issue and mails whoever pushed it.
        log_err(f"{exc} No codes generated or sent.")
        return Outcome.UNUSABLE_ROSTER
    if not students:
        log_err(f"roster in {semester_org} has no rows yet - no codes to generate.")
        return Outcome.EMPTY_ROSTER

    # One code per address: two rows of one email are one student (the duplicate is on
    # the students.csv digest issue), and a row with no address cannot be mailed.
    unique = list(roster.by_email(students).values())
    before = [s.enrol_code for s in unique]
    added = assign_codes(unique)  # in memory; persisted below
    log_step(
        f"Enrolment codes for {semester_org}: {added} new code(s), "
        f"emailing not-yet-onboarded students"
    )
    if added:
        new_codes = {
            s.email.strip(): s.enrol_code
            for i, s in enumerate(unique)
            if not before[i] and s.enrol_code
        }
        written = write_column(
            semester_org,
            raw,
            raw_sha,
            "enrol_code",
            new_codes,
            f"roster: assign {len(new_codes)} enrolment code(s)",
        )
        if written is None:
            log_err(
                f"could not write the enrolment codes to {roster.SYSTEM_PATH} in "
                f"{semester_org} - nothing emailed, so re-running is safe."
            )
            return Outcome.FAILED
        log_ok(f"wrote {added} code(s) to {roster.SYSTEM_PATH}")
        # Email what the ROSTER holds, never the codes generated in memory: a refused write
        # is re-applied onto a fresh read, and a code that landed in between is left as it
        # is - so the in-memory code for that student is one nobody can enrol with.
        unique = list(roster.by_email(roster.parse(text, written)).values())

    join_url = join_issue_url(semester_org)
    # `code_sent_at` keeps a re-run from re-mailing students who already have their code.
    already = {s.email.strip().casefold() for s in unique if s.code_sent_at.strip()}
    # A code on two rows binds whoever joins first to the first row: mail neither (the
    # roster file is the engine's own, so this is a fault to report, never faculty's).
    shared = roster.shared_codes(unique)
    targets = []
    held = 0
    for s in unique:
        email = s.email.strip().casefold()
        if s.enrol_code and email and not s.onboarded and email not in already:
            if s.enrol_code.casefold() in shared:
                held += 1
                continue
            targets.append(s)
    if held:
        log_err(
            f"{held} student(s) share an enrol_code with another in "
            f"{roster.SYSTEM_PATH} - not emailed until each has its own"
        )
    if not targets:
        log_ok("no not-yet-onboarded students still to be sent a code.")
        return Outcome.NOTHING_TO_SEND
    # Asked BEFORE anything is claimed below, not inferred afterwards from an empty batch:
    # the claim stamps the roster, and an org whose GRAPH_* secrets were never set would
    # otherwise have its whole roster marked as emailed by a run that could never have
    # emailed anybody.
    if mailer.graph_config_from_env() is None:
        log_err(
            f"no mail transport for {semester_org} - nothing claimed, nothing sent."
        )
        return Outcome.NO_TRANSPORT
    # The codes are already committed by this point: a course file that cannot be read
    # costs the name, never the email.
    course_name = course_name_or(semester_org=semester_org)
    out = _claimed_send(
        semester_org,
        [code_message(s, join_url, course_name) for s in targets],
        [s.email for s in targets],
        "",
        "nothing emailed, so re-running is safe. Fix the roster write.",
    )
    if out is None or out[1]:
        return Outcome.FAILED
    sent = out[0]
    log_ok(f"emailed {len(sent)} code(s), all of them recorded")
    return Outcome.SENT


# Enough to tell an address from a typo before a code is minted for it: something before
# an `@`, a dotted domain after it, no whitespace. Graph is the real test.
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def resend_unjoined(
    semester_org: str, dry_run: bool = True
) -> tuple[Outcome, dict[str, int]]:
    """Send a NEW code to every roster row without a `github_handle`, replacing the old
    one - which then stops working - and email it. The console's "send new codes".

    A row whose email is not an address, or repeats one already taken, is skipped and
    counted, never named: the `Done` line lands in a public log. `dry_run` counts and
    changes nothing. Otherwise it follows `run`'s order: the transport is asked first,
    the codes are committed, `code_sent_at` is claimed, then the batch is sent and any
    claim it did not spend is released, so a later roster push retries it.

    Returns the outcome and its counts: `students` (who would get or got a new code),
    `skipped` (rows whose address is unusable) and `sent`."""
    read = _read(semester_org)
    if read is None:
        return Outcome.NO_ROSTER, {}
    text, raw, raw_sha = read
    try:
        students = roster.parse(text, raw)
    except Unusable as exc:
        log_err(f"{exc} No codes generated or sent.")
        return Outcome.UNUSABLE_ROSTER, {}
    seen: set[str] = set()
    targets: list[roster.Student] = []
    skipped = 0
    for s in students:
        if s.onboarded:
            continue
        email = s.email.strip()
        if not _ADDRESS.fullmatch(email) or email.casefold() in seen:
            skipped += 1
            continue
        seen.add(email.casefold())
        targets.append(s)
    counts = {"students": len(targets), "skipped": skipped, "sent": 0}
    if dry_run or not targets:
        log_ok(
            f"Done - {json.dumps(counts)}"
            + (" (preview: no code changed, nothing sent)" if dry_run else "")
        )
        return Outcome.NOTHING_TO_SEND, counts
    if mailer.graph_config_from_env() is None:
        log_err(
            f"no mail transport for {semester_org} - no code changed, nothing sent."
        )
        return Outcome.NO_TRANSPORT, counts
    taken = {s.enrol_code for s in students if s.enrol_code}
    fresh: dict[str, str] = {}
    for s in targets:
        code = make_code()
        while code in taken:
            code = make_code()
        taken.add(code)
        fresh[s.email.strip()] = code
    written = write_column(
        semester_org,
        raw,
        raw_sha,
        "enrol_code",
        fresh,
        f"roster: replace {len(fresh)} enrolment code(s)",
        replacing=None,
    )
    if written is None:
        log_err(
            f"could not write the new codes to {roster.SYSTEM_PATH} in {semester_org} - "
            f"the old codes still work and nothing was emailed."
        )
        return Outcome.FAILED, counts
    # Email what the ROSTER holds, and only rows still unjoined that carry this run's code:
    # a Join that landed in between is somebody who needs no new code.
    to_mail = [
        s
        for s in roster.by_email(roster.parse(text, written)).values()
        if not s.onboarded and fresh.get(s.email.strip()) == s.enrol_code
    ]
    if not to_mail:
        # Every one of them joined while the codes were being written: nobody to mail.
        counts["students"] = 0
        log_ok(f"Done - {json.dumps(counts)}")
        return Outcome.NOTHING_TO_SEND, counts
    course_name = course_name_or(semester_org=semester_org)
    join_url = join_issue_url(semester_org)
    out = _claimed_send(
        semester_org,
        [code_message(s, join_url, course_name, replaces=True) for s in to_mail],
        [s.email for s in to_mail],
        None,
        "the new codes are in the roster and nothing was emailed; the next roster "
        "push will not send them either, so run this again.",
    )
    if out is None:
        return Outcome.FAILED, counts
    sent, unsent = out
    counts["sent"] = len(sent)
    log_ok(f"Done - {json.dumps(counts)}")
    return (Outcome.FAILED if unsent else Outcome.SENT), counts


def _claimed_send(
    semester_org: str,
    messages: list[mailer.Message],
    recipients: list[str],
    replacing: str | None,
    unclaimed: str,
) -> tuple[list[str], list[str]] | None:
    """Claim `code_sent_at` for `recipients`, send `messages`, and give back every claim
    the send did not spend. `(sent, unsent)` addresses, or None when the claim could not
    be written (`unclaimed` says what that leaves, after the stamp line).

    CLAIM, then send. `write_column` reports a refused write by RETURNING - it never
    raises - so send-then-stamp left the one ordering an unattended caller cannot
    survive: a roster that cannot be written (an archived semester-config, a new branch
    ruleset, a token that lost write scope, a run of 5xx) meant the batch went out with
    `code_sent_at` still blank, so the next push to the roster mailed the very same
    students again. Stamping first makes a write failure mean NOTHING WAS MAILED.

    A transport that RAISED (a credential Graph refused) sent nothing at all, and the
    claim must not outlive it - unreleased, it is a whole semester silently marked as
    emailed. The release logs its own failure and never raises, so the original exception
    is what reaches the caller. A partly-delivered batch is ROUTINE: `send_bulk` stops at
    its own time budget, and releasing the claims it did not spend is what keeps "re-run
    to continue" true."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    if not _claim_sent(semester_org, recipients, stamp, replacing=replacing):
        log_err(
            f"could not stamp code_sent_at in {roster.SYSTEM_PATH} for {semester_org} - "
            f"{unclaimed}"
        )
        return None
    try:
        sent = mailer.send_bulk(messages)
    except Exception:
        _release_unsent(semester_org, recipients, stamp)
        raise
    went_out = set(sent)
    if unsent := [to for to in recipients if to not in went_out]:
        _release_unsent(semester_org, unsent, stamp)
    return sent, unsent


def _claim_sent(
    semester_org: str, recipients: list[str], stamp: str, replacing: str | None = ""
) -> bool:
    """Stamp `code_sent_at` on the rows about to be emailed. True if it was committed.

    Re-reads first: the code write above moved the sha, and a Join issue can land between
    the two writes.

    A claim, not a record - it is written BEFORE the send (see `run`), so what it really
    asserts is "nobody else will mail these rows". `_release_unsent` gives back whatever
    the send then failed to spend."""
    if not recipients:
        return False
    read = get_file_with_sha(semester_org, CONFIG_REPO, roster.SYSTEM_PATH)
    if read is None:
        return False
    raw, sha = read
    return bool(
        write_column(
            semester_org,
            raw,
            sha,
            "code_sent_at",
            {to: stamp for to in recipients},
            f"roster: claim {len(recipients)} enrolment code email(s)",
            replacing=replacing,
        )
    )


def _release_unsent(semester_org: str, unsent: list[str], stamp: str) -> None:
    """Blank the `code_sent_at` claims the send did not spend, so a later run retries them.

    Only cells still holding THIS run's exact `stamp` are blanked (`replacing=stamp`), so a
    row somebody else stamped in between is left exactly as it is.

    Never raises: it runs on the failure path, including from an `except` block where a
    raise of its own would replace the exception the caller has to see."""
    try:
        read = get_file_with_sha(semester_org, CONFIG_REPO, roster.SYSTEM_PATH)
        released: str | None = None
        if read is not None:
            raw, sha = read
            held = roster.parse_system(raw)
            marks = {
                to: ""
                for to in unsent
                if held.get(to.strip().casefold(), {}).get("code_sent_at") == stamp
            }
            released = (
                write_column(
                    semester_org,
                    raw,
                    sha,
                    "code_sent_at",
                    marks,
                    f"roster: release {len(marks)} unsent enrolment code claim(s)",
                    replacing=stamp,
                )
                if marks
                # Nothing still carries our stamp, so there is no claim left to give back.
                else ""
            )
        if released is not None:
            log_err(
                f"{len(unsent)} student(s) in {semester_org} were not emailed - their "
                f"code_sent_at claim was released, so the next run retries them."
            )
            return
    except Exception as exc:  # a failed release must still be REPORTED, not raised
        log_err(
            f"releasing the unsent enrolment claims in {semester_org} failed: {exc}"
        )
    # The one failure that must never be swallowed: the roster says these students were
    # emailed and they were not, so nothing will ever retry them. No address here - this
    # line lands in a world-readable Actions log - but the stamp is exact, and every row
    # carrying it is a row to clear.
    log_err(
        f"{len(unsent)} student(s) in {roster.SYSTEM_PATH} in {semester_org} are stamped "
        f"code_sent_at={stamp} but were never emailed, and the stamp could not be "
        f"cleared - delete that exact timestamp from those rows by hand, or they never "
        f"receive a code."
    )
    for to in unsent:
        log_person(f"  not emailed, still claimed: {to}")


# The outcomes that leave **Send enrolment codes** green. Three mean nothing is
# outstanding - a roster with nothing but its header is the normal state of a freshly
# bootstrapped semester, not a failure. The fourth is a CONTENT fault: a roster nobody can
# parse is faculty's to fix, it is already reported to them by name through the
# students.csv digest issue, and reddening this run instead files `Send enrolment codes is
# failing` in the course org and mails the maintainer a CSV they cannot correct.
# Everything else - a roster that is not there, a mail transport that is not configured, a
# write GitHub refused - is a real failure, and the run that a roster push just fired is
# owed its red X for it.
_GREEN = (
    Outcome.SENT,
    Outcome.NOTHING_TO_SEND,
    Outcome.EMPTY_ROSTER,
    Outcome.UNUSABLE_ROSTER,
)


def reds_the_run(outcome: Outcome) -> bool:
    """Whether **Send enrolment codes** should exit non-zero on this outcome - see
    `_GREEN` for which do not and why."""
    return outcome not in _GREEN


def new_codes_summary(counts: dict[str, int], dry_run: bool, rc: int) -> Summary | int:
    """Send new codes' sentence, off `resend_unjoined`'s counts. Counts only: the run
    log and the annotation are public, and every row here is a student."""
    if not counts or rc:
        return rc  # the roster could not be read, or the send failed: the log says why
    who = plural(counts["students"], "student")
    skipped = counts["skipped"]
    tail = (
        f" {plural(skipped, 'row')} skipped for an unusable email address."
        if skipped
        else ""
    )
    if dry_run:
        text = f"{who} who have not joined would get new codes.{tail}"
    elif not counts["students"]:
        return Summary(
            f"Every student on the roster has joined; no new codes to send.{tail}",
            counts,
            conclusion="nothing_to_do",
        )
    else:
        text = (
            f"New codes sent to {plural(counts['sent'], 'student')} who have not "
            f"joined; their old codes no longer work.{tail}"
        )
    return Summary(text, counts)


def refuse_unregistered(semester_org: str, course_org: str) -> bool:
    """Whether a DISPATCHED send must be refused because `course_org` does not own
    `semester_org`. True means refuse.

    A dispatched semester name reaches here straight from a `repository_dispatch`'s
    `client_payload.semester_org`, which is written by whoever holds a semester's DSL_BOT_TOKEN
    - a LOWER trust tier than the course org. Naming SOMEONE ELSE'S semester would have this
    run generate codes into that semester's roster and email its students. The registry is
    the authority on which semesters a course org owns, so a name that is not in it is
    refused rather than acted on. Compared casefold: GitHub org names are case-insensitive,
    and the registry's spelling need not match the dispatch's.

    An EMPTY registry authorises nothing - the same rule, and the same reason, as
    sync_membership.sync: a course org that has never registered a semester must not accept
    any org name a dispatch cares to name.

    Every send comes through the dispatched path, so `--dispatched-by` is always passed
    in production; it stays a flag rather than a required argument so a maintainer can
    still run the CLI by hand against a semester they already know."""
    registered = discover_semesters(course_org)
    if semester_org.casefold() in {c.casefold() for c in registered}:
        return False
    listed = ", ".join(sorted(registered)) or "nothing"
    log_err(
        f"{semester_org} is not registered under {course_org} "
        f"({SEMESTERS_PATH} lists {listed}) - refusing to send its enrolment codes. "
        f"Register the semester first if this is genuinely its course org."
    )
    return True


def main() -> int:
    parser = CLIParser(description=__doc__)
    parser.add_argument("--semester-org", required=True)
    parser.add_argument(
        "--dispatched-by",
        default=None,
        metavar="COURSE_ORG",
        help=(
            "This run came from a repository_dispatch in COURSE_ORG: refuse unless "
            "--semester-org is registered under it (see refuse_unregistered). Omitted "
            "only by a maintainer running the CLI by hand."
        ),
    )
    parser.add_argument(
        "--resend-unjoined",
        action="store_true",
        help="Send a NEW code to every row without a github_handle; the old codes stop "
        "working. See resend_unjoined.",
    )
    add_preview_flag(
        parser,
        "With --resend-unjoined: count who would get a new code, change nothing (default).",
    )
    args = parser.parse_args()
    if args.preview and not args.resend_unjoined:
        # The roster send has no preview of its own: a preview of it sends nothing.
        log("  [preview] nothing sent - sending codes acts only with --no-preview")
        return 0
    # A read helper (or the mail transport) that couldn't reach its API raises; in an
    # Actions log a one-line error beats a traceback, and the run still goes red.
    try:
        if args.dispatched_by and refuse_unregistered(
            args.semester_org, args.dispatched_by
        ):
            return 1
        # A closed-out semester's semester-config is read-only, so the `code_sent_at` stamp
        # this write-back depends on could not land - and nobody is being enrolled into a
        # term that is over anyway.
        if not semester_is_live(args.semester_org):
            return 0
        counts = None
        if args.resend_unjoined:
            outcome, counts = resend_unjoined(args.semester_org, dry_run=args.preview)
        else:
            outcome = run(args.semester_org)
        rc = int(reds_the_run(outcome))
        if counts is not None:
            rc = new_codes_summary(counts, args.preview, rc)
        # Dispatched by a roster push: the codes just sent change the semester's status.
        # The course org is known only on that path, and the write is not counted.
        if args.dispatched_by and not args.preview:
            status.refresh(args.dispatched_by, args.semester_org)
        return rc
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
