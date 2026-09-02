"""dsl-course enrol-codes -- generate per-student enrolment codes and email them.

Students enrol by pasting a random, **non-PII** code (not their email) into the welcome
Join issue, so no personal data ever touches the public repo - and because the code is
unguessable, a classmate can't bind your roster row to their account. This one action:

    1. fills blank `enrol_code` cells in classroom-config/students.csv (idempotent), then
    2. emails each not-yet-onboarded student their code.

Email reaches the student's UNIVERSITY inbox (the roster `hertie_email`), replacing the
Excel -> Power Automate -> Outlook mail-merge. Reuses dsl_course.mailer.

Every roster row gets a code, auditors included - the code is how anyone onboards at all;
their `role` column is what routes them to the read-only `auditors` team on the way in.

One caller, and it is not a person: a push to a cohort's own students.csv, which its
classroom-config dispatcher turns into a `send-codes` repository_dispatch at the course
org. That path passes `--dispatched-by`, because its cohort name is untrusted input (see
`refuse_unregistered`). Every send is therefore unattended and for real - there is no
preview mode, and re-sending means pushing the roster again with `code_sent_at` cleared.

Usage:
    python3 -m dsl_course.enrol_codes --cohort-org hertie-dsl-demo-f2026
    python3 -m dsl_course.enrol_codes --cohort-org hertie-dsl-demo-f2026 \\
        --dispatched-by hertie-dsl-demo-course-e1234
"""

from __future__ import annotations

import argparse
import csv
import enum
import io
import secrets
import sys
from datetime import UTC, datetime

from . import mailer, roster
from .course import course_phrase
from .discovery import COHORTS_PATH, course_name_for_cohort, discover_cohorts
from .gh_contents import get_file_with_sha, put_file, read_csv
from .log import log_err, log_ok, log_person, log_step

# No ambiguous characters (0/O, 1/l/I) so a student can read the code off an email.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def make_code() -> str:
    return "dsl-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def fill_column_in_csv(
    text: str, column: str, values_by_row: dict[int, str], replacing: str = ""
) -> str:
    """Surgically write one column into the RAW students.csv, preserving everything else.

    `values_by_row` maps a 0-based DATA-row index (matching `roster.parse` order) to the
    value to drop into that row's blank `column` cell. Only that one column is touched: every
    other column - including ones `roster.FIELDS` doesn't know about, e.g. a faculty-added
    `notes`/`moodle_id` - is carried through untouched, and no cell's raw text is normalised
    (so a `role` of `audit`/`Auditor` is left exactly as written). This replaces the old
    round-trip through a whole-file re-serialisation, which wrote only `roster.FIELDS` and
    rewrote
    `role`, silently dropping unknown columns and mangling role text on every code write.

    Only cells whose current text is `replacing` are written, which is what makes every
    caller idempotent: the default (blank) means a code already issued is never rotated and
    a row already marked as emailed is never re-stamped. The one caller that passes
    anything else is the release of a claim that could not be spent (`_release_unsent`),
    which blanks the cells still holding ITS OWN stamp and nobody else's. The column is
    appended if the roster predates it, so a deployed cohort needs no migration."""
    # read_csv, not a bare DictReader: this path bypasses `roster.parse`, and a
    # `;`-delimited Excel export was written straight back with a code column bolted on -
    # exit 0, roster destroyed.
    reader = read_csv(text, roster.REQUIRED_FIELDS, roster.ROSTER_PATH)
    fieldnames = list(reader.fieldnames or [])
    if column not in fieldnames:
        fieldnames.append(column)
    rows = list(reader)
    for i, row in enumerate(rows):
        if i in values_by_row and (row.get(column) or "").strip() == replacing:
            row[column] = values_by_row[i]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return out.getvalue()


def rows_for_values(text: str, items: list[tuple[int, str, str]]) -> dict[int, str]:
    """Where each value belongs in `text`, as `{row index: value}`.

    `items` is `(row index at read time, hertie_email, value)`. A row is re-located by
    EMAIL, because a row index taken from an earlier read is not stable across an edit that
    landed in between - a Join issue filling in a handle, a faculty member inserting a row.
    A row carrying no email at all cannot be re-located and keeps its original index; that
    is the one row for which a concurrent edit can still land the value on the wrong line,
    and it is also a row nobody can be emailed at.

    An email that is NOT unique cannot re-locate anything either - a registrar export with
    the same address on two rows (a duplicate enrolment, a shared departmental inbox) sent
    both codes to the FIRST of them, so the two collapsed onto one key and one of the rows
    was left with no code at all, silently. Those rows keep their original index too."""
    seen: dict[str, int] = {}
    for i, row in enumerate(read_csv(text, roster.REQUIRED_FIELDS, roster.ROSTER_PATH)):
        email = (row.get("hertie_email") or "").strip()
        if email:
            # -1 marks "more than one row carries this" - not usable as a destination.
            seen[email] = i if email not in seen else -1
    placed: dict[int, str] = {}
    for index, email, value in items:
        at = seen.get(email, -1)
        placed[at if at >= 0 else index] = value
    return placed


# Bounded: each attempt costs a read and a write, and a roster being edited faster than
# that is a person at a keyboard, not a race worth grinding against.
WRITE_ATTEMPTS = 3


def write_column(
    cohort_org: str,
    raw: str,
    sha: str,
    column: str,
    items: list[tuple[int, str, str]],
    message: str,
    replacing: str = "",
) -> str | None:
    """Commit one column into students.csv without clobbering a concurrent edit.
    Returns the roster text AS COMMITTED, or None if no attempt was accepted.

    `put_file`'s ordinary path re-reads the sha immediately before writing, so the write
    succeeds however stale its content is: a Join binding committed while Send codes was
    running was silently reverted, and the student's handle was simply gone. The sha the
    roster was READ at is sent instead, so GitHub refuses a write onto a file that has
    moved on. We then re-read, re-apply only the values THIS run produced onto the fresh
    text - `fill_column_in_csv` fills blank cells only, so a value that arrived in
    between is left exactly as it is - and try again.

    Which is exactly why the committed TEXT is returned rather than a bare success: after
    a retry the roster can hold a different code from the one this run generated, and the
    caller must email what the roster holds."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        body = fill_column_in_csv(raw, column, rows_for_values(raw, items), replacing)
        if put_file(
            cohort_org,
            roster.CONFIG_REPO,
            roster.ROSTER_PATH,
            body.encode(),
            message,
            expected_sha=sha,
        ):
            return body
        if attempt == WRITE_ATTEMPTS:
            break
        log_err(
            f"{roster.ROSTER_PATH} in {cohort_org} could not be written as read - "
            f"re-reading and retrying ({attempt}/{WRITE_ATTEMPTS - 1})"
        )
        fresh = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
        if fresh is None:
            break
        raw, sha = fresh
    return None


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
    student: roster.Student, welcome_url: str, course_name: str = ""
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
        f"  {welcome_url}\n\n"
        f"and paste this enrolment code when asked:\n\n"
        f"    {student.enrol_code}\n\n"
        f"Whichever GitHub account opens the issue is linked to your Hertie email "
        f"address automatically.\n"
    )
    return (student.hertie_email, subject, body)


class Outcome(enum.Enum):
    """What one `run` came to. Richer than an exit code, because the reasons a send did
    not happen are not interchangeable: two of them mean nothing is outstanding, and the
    rest are each a different thing for a person to go and fix. Each value is the phrase a
    log line puts after "no codes went out:" (see `reds_the_run`).
    """

    SENT = "the codes were emailed"
    NOTHING_TO_SEND = "every student who needs a code already has one"
    NO_ROSTER = "students.csv could not be read"
    EMPTY_ROSTER = "students.csv has no rows yet"
    NO_TRANSPORT = "no mail transport is configured (the GRAPH_* secrets)"
    FAILED = "the send failed"


def run(cohort_org: str) -> Outcome:
    # Fetch the RAW roster text once: we parse it for the students, and (below) edit the same
    # text in place so writing codes back never disturbs columns roster doesn't model.
    read = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if read is None:  # genuinely absent - mirror roster.load's message
        log_err(
            f"Could not find {roster.ROSTER_PATH} in {cohort_org}/{roster.CONFIG_REPO} - "
            f"bootstrap the cohort first (bootstrap_course --cohort)."
        )
        return Outcome.NO_ROSTER
    # The sha is kept so the write below can be refused if anything else commits to the
    # roster while this run is generating and mailing codes (see write_codes).
    raw, raw_sha = read
    students = roster.parse(raw)
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - no codes to generate.")
        return Outcome.EMPTY_ROSTER

    before = [s.enrol_code for s in students]
    added = assign_codes(students)  # in memory; persisted below
    log_step(
        f"Enrolment codes for {cohort_org}: {added} new code(s), "
        f"emailing not-yet-onboarded students"
    )
    if added:
        # Write ONLY the newly filled enrol_code cells back into the raw CSV, preserving
        # every other column and each cell's raw text (see fill_enrol_codes_in_csv). Row
        # order matches roster.parse, so index i lines up with students[i] - and
        # write_codes re-locates each row by email if it has to re-read.
        new_codes = [
            (i, s.hertie_email.strip(), s.enrol_code)
            for i, s in enumerate(students)
            if not before[i] and s.enrol_code
        ]
        written = write_column(
            cohort_org,
            raw,
            raw_sha,
            "enrol_code",
            new_codes,
            f"roster: assign {len(new_codes)} enrolment code(s)",
        )
        if written is None:
            log_err(
                f"could not write the enrolment codes to {roster.ROSTER_PATH} in "
                f"{cohort_org} - nothing emailed, so re-running is safe."
            )
            return Outcome.FAILED
        log_ok(f"wrote {added} code(s) to {roster.ROSTER_PATH}")
        # Email what the ROSTER holds, never the codes generated in memory: a refused write
        # is re-applied onto a fresh read, and a code that landed in between is left as it
        # is - so the in-memory code for that student is one nobody can enrol with.
        students = roster.parse(written)

    welcome_url = f"https://github.com/{cohort_org}/welcome/issues/new/choose"
    # One set, two jobs: `code_sent_at` keeps a re-run from re-mailing students who already
    # have their code, and adding each address as we go collapses a duplicated roster row,
    # which would otherwise get two emails carrying two different codes.
    already = {
        s.hertie_email.strip().casefold() for s in students if s.code_sent_at.strip()
    }
    targets = []
    for s in students:
        email = s.hertie_email.strip().casefold()
        if s.enrol_code and email and not s.onboarded and email not in already:
            already.add(email)
            targets.append(s)
    if not targets:
        log_ok("no not-yet-onboarded students still to be sent a code.")
        return Outcome.NOTHING_TO_SEND
    # Asked BEFORE anything is claimed below, not inferred afterwards from an empty batch:
    # the claim stamps the roster, and an org whose GRAPH_* secrets were never set would
    # otherwise have its whole roster marked as emailed by a run that could never have
    # emailed anybody.
    if mailer.graph_config_from_env() is None:
        log_err(f"no mail transport for {cohort_org} - nothing claimed, nothing sent.")
        return Outcome.NO_TRANSPORT
    # The codes are already committed to students.csv by this point, and
    # load_yaml_config RAISES on a malformed dsl-course.yml or a non-404 read failure -
    # while main() catches only RuntimeError. Unguarded, a bad course file meant a
    # traceback with the codes persisted and not one email sent. Same guard as
    # grades._email_updates, for the same reason.
    try:
        course_name = course_name_for_cohort(cohort_org)
    except Exception as exc:  # a name is never worth losing the codes email over
        log_err(f"could not read the course name ({exc}) - mailing without it")
        course_name = ""
    messages = [code_message(s, welcome_url, course_name) for s in targets]
    recipients = [s.hertie_email for s in targets]
    # CLAIM, then send. `write_column` reports a refused write by RETURNING - it never
    # raises - so send-then-stamp left the one ordering an unattended caller cannot
    # survive: a roster that cannot be written (an archived classroom-config, a new branch
    # ruleset, a token that lost write scope, a run of 5xx) meant the batch went out with
    # `code_sent_at` still blank, so the next push to the roster - and the send fills its
    # own blank cells, so it triggers one - mailed the very same students again. Stamping
    # first makes a write failure mean NOTHING WAS MAILED, which the next push retries.
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    if not _claim_sent(cohort_org, recipients, stamp):
        log_err(
            f"could not stamp code_sent_at in {roster.ROSTER_PATH} for {cohort_org} - "
            f"nothing emailed, so re-running is safe. Fix the roster write."
        )
        return Outcome.FAILED
    try:
        sent = mailer.send_bulk(messages)
    except Exception:
        # A transport that RAISED (a credential Graph refused) sent nothing at all, and
        # the claim above must not outlive it - unreleased, it is a whole cohort silently
        # marked as emailed. The release logs its own failure and never raises, so the
        # original exception is what reaches the caller.
        _release_unsent(cohort_org, recipients, stamp)
        raise
    # A partly-delivered batch is ROUTINE, not exotic: `send_bulk` stops at its own time
    # budget and says "re-run to continue". Releasing the claims it did not spend is what
    # keeps that true - without it, the tail of every throttled batch would be stamped as
    # emailed and never mailed at all.
    went_out = set(sent)
    unsent = [to for to in recipients if to not in went_out]
    if unsent:
        _release_unsent(cohort_org, unsent, stamp)
        return Outcome.FAILED
    log_ok(f"emailed {len(sent)} code(s), all of them recorded")
    return Outcome.SENT


def _claim_sent(cohort_org: str, recipients: list[str], stamp: str) -> bool:
    """Stamp `code_sent_at` on the rows about to be emailed. True if it was committed.

    Re-reads first: the code write above moved the sha, and a Join issue can land between
    the two writes.

    A claim, not a record - it is written BEFORE the send (see `run`), so what it really
    asserts is "nobody else will mail these rows". `_release_unsent` gives back whatever
    the send then failed to spend."""
    read = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if read is None:
        return False
    raw, sha = read
    want = set(recipients)
    marks = [
        (i, s.hertie_email.strip(), stamp)
        for i, s in enumerate(roster.parse(raw))
        if s.hertie_email in want
    ]
    return bool(marks) and bool(
        write_column(
            cohort_org,
            raw,
            sha,
            "code_sent_at",
            marks,
            f"roster: claim {len(marks)} enrolment code email(s)",
        )
    )


def _release_unsent(cohort_org: str, unsent: list[str], stamp: str) -> None:
    """Blank the `code_sent_at` claims the send did not spend, so a later run retries them.

    Only cells still holding THIS run's exact `stamp` are blanked (`replacing=stamp`), so a
    row somebody else stamped in between is left exactly as it is.

    Never raises: it runs on the failure path, including from an `except` block where a
    raise of its own would replace the exception the caller has to see."""
    try:
        read = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
        released: str | None = None
        if read is not None:
            raw, sha = read
            want = set(unsent)
            marks = [
                (i, s.hertie_email.strip(), "")
                for i, s in enumerate(roster.parse(raw))
                if s.hertie_email in want and s.code_sent_at.strip() == stamp
            ]
            released = (
                write_column(
                    cohort_org,
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
                f"{len(unsent)} student(s) in {cohort_org} were not emailed - their "
                f"code_sent_at claim was released, so the next run retries them."
            )
            return
    except Exception as exc:  # a failed release must still be REPORTED, not raised
        log_err(f"releasing the unsent enrolment claims in {cohort_org} failed: {exc}")
    # The one failure that must never be swallowed: the roster says these students were
    # emailed and they were not, so nothing will ever retry them. No address here - this
    # line lands in a world-readable Actions log - but the stamp is exact, and every row
    # carrying it is a row to clear.
    log_err(
        f"{len(unsent)} student(s) in {roster.ROSTER_PATH} in {cohort_org} are stamped "
        f"code_sent_at={stamp} but were never emailed, and the stamp could not be "
        f"cleared - delete that exact timestamp from those rows by hand, or they never "
        f"receive a code."
    )
    for to in unsent:
        log_person(f"  not emailed, still claimed: {to}")


def reds_the_run(outcome: Outcome) -> bool:
    """Whether **Send enrolment codes** should exit non-zero on this outcome.

    Everything except the two that mean nothing is outstanding: the run was fired by a
    roster edit somebody had just made, and they are owed a red X for any reason no email
    went out - a missing roster and unset secrets included."""
    return outcome not in (Outcome.SENT, Outcome.NOTHING_TO_SEND)


def refuse_unregistered(cohort_org: str, course_org: str) -> bool:
    """Whether a DISPATCHED send must be refused because `course_org` does not own
    `cohort_org`. True means refuse.

    A dispatched cohort name reaches here straight from a `repository_dispatch`'s
    `client_payload.cohort_org`, which is written by whoever holds a cohort's DSL_BOT_TOKEN
    - a LOWER trust tier than the course org. Naming SOMEONE ELSE'S cohort would have this
    run generate codes into that cohort's roster and email its students. The registry is
    the authority on which cohorts a course org owns, so a name that is not in it is
    refused rather than acted on. Compared casefold: GitHub org names are case-insensitive,
    and the registry's spelling need not match the dispatch's.

    An EMPTY registry authorises nothing - the same rule, and the same reason, as
    sync_membership.sync: a course org that has never registered a cohort must not accept
    any org name a dispatch cares to name.

    Every send comes through the dispatched path, so `--dispatched-by` is always passed
    in production; it stays a flag rather than a required argument so a maintainer can
    still run the CLI by hand against a cohort they already know."""
    registered = discover_cohorts(course_org)
    if cohort_org.casefold() in {c.casefold() for c in registered}:
        return False
    listed = ", ".join(sorted(registered)) or "nothing"
    log_err(
        f"{cohort_org} is not registered under {course_org} "
        f"({COHORTS_PATH} lists {listed}) - refusing to send its enrolment codes. "
        f"Register the cohort first if this is genuinely its course org."
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument(
        "--dispatched-by",
        default=None,
        metavar="COURSE_ORG",
        help=(
            "This run came from a repository_dispatch in COURSE_ORG: refuse unless "
            "--cohort-org is registered under it (see refuse_unregistered). Omitted "
            "only by a maintainer running the CLI by hand."
        ),
    )
    args = parser.parse_args()
    # A read helper (or the mail transport) that couldn't reach its API raises; in an
    # Actions log a one-line error beats a traceback, and the run still goes red.
    try:
        if args.dispatched_by and refuse_unregistered(
            args.cohort_org, args.dispatched_by
        ):
            return 1
        return int(reds_the_run(run(args.cohort_org)))
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
