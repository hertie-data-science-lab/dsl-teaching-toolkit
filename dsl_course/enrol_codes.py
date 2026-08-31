"""dsl-course enrol-codes -- generate per-student enrolment codes and email them.

Students enrol by pasting a random, **non-PII** code (not their email) into the welcome
Join issue, so no personal data ever touches the public repo - and because the code is
unguessable, a classmate can't bind your roster row to their account. This one action:

    1. fills blank `enrol_code` cells in classroom-config/students.csv (idempotent), then
    2. emails each not-yet-onboarded student their code (preview with --dry-run).

Email reaches the student's UNIVERSITY inbox (the roster `hertie_email`), replacing the
Excel -> Power Automate -> Outlook mail-merge. Reuses dsl_course.mailer.

Every roster row gets a code, auditors included - the code is how anyone onboards at all;
their `role` column is what routes them to the read-only `auditors` team on the way in.

Usage:
    python3 -m dsl_course.enrol_codes --cohort-org hertie-dsl-demo-f2026 --dry-run
    python3 -m dsl_course.enrol_codes --cohort-org hertie-dsl-demo-f2026
"""

from __future__ import annotations

import argparse
import csv
import io
import secrets
import sys
from datetime import UTC, datetime

from . import mailer, roster
from .discovery import course_name_for_cohort
from .gh_contents import get_file_with_sha, put_file, read_csv
from .log import log_err, log_ok, log_step

# No ambiguous characters (0/O, 1/l/I) so a student can read the code off an email.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def make_code() -> str:
    return "dsl-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def fill_column_in_csv(text: str, column: str, values_by_row: dict[int, str]) -> str:
    """Surgically write one column into the RAW students.csv, preserving everything else.

    `values_by_row` maps a 0-based DATA-row index (matching `roster.parse` order) to the
    value to drop into that row's blank `column` cell. Only that one column is touched: every
    other column - including ones `roster.FIELDS` doesn't know about, e.g. a faculty-added
    `notes`/`moodle_id` - is carried through untouched, and no cell's raw text is normalised
    (so a `role` of `audit`/`Auditor` is left exactly as written). This replaces the old
    round-trip through a whole-file re-serialisation, which wrote only `roster.FIELDS` and
    rewrote
    `role`, silently dropping unknown columns and mangling role text on every code write.

    Blank cells only, which is what makes both callers idempotent: a code already issued is
    never rotated, and a row already marked as emailed is never re-stamped. The column is
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
        if i in values_by_row and not (row.get(column) or "").strip():
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
        body = fill_column_in_csv(raw, column, rows_for_values(raw, items))
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
    course = f"the {course_name} course" if course_name else "the course"
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
        f"Whichever GitHub account opens the issue is linked to your hertie email "
        f"address automatically.\n"
    )
    return (student.hertie_email, subject, body)


def sample_body(welcome_url: str, course_name: str = "") -> str:
    """The code email rendered with PLACEHOLDERS, for the dry-run preview.

    `code_message` with `<name>`/`<code>` where a student's name and a live credential
    would go - see `mailer.sample_of`."""
    return mailer.sample_of(
        lambda student: code_message(student, welcome_url, course_name),
        enrol_code="<code>",
    )


def run(cohort_org: str, dry_run: bool = False) -> int:
    # Fetch the RAW roster text once: we parse it for the students, and (below) edit the same
    # text in place so writing codes back never disturbs columns roster doesn't model.
    read = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if read is None:  # genuinely absent - mirror roster.load's message
        log_err(
            f"Could not find {roster.ROSTER_PATH} in {cohort_org}/{roster.CONFIG_REPO} - "
            f"bootstrap the cohort first (bootstrap_course --cohort)."
        )
        return 1
    # The sha is kept so the write below can be refused if anything else commits to the
    # roster while this run is generating and mailing codes (see write_codes).
    raw, raw_sha = read
    students = roster.parse(raw)
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - no codes to generate.")
        return 1

    before = [s.enrol_code for s in students]
    added = assign_codes(students)  # in memory; persisted below unless dry-run
    log_step(
        f"Enrolment codes for {cohort_org}: {added} new code(s), "
        f"emailing not-yet-onboarded students"
    )
    if added and not dry_run:
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
            return 1
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
        return 0
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
    sent = mailer.send_bulk(
        messages, dry_run=dry_run, sample=sample_body(welcome_url, course_name)
    )
    if not dry_run and sent and _mark_sent(cohort_org, sent) != 0:
        return 1
    return 0 if len(sent) == len(messages) else 1


def _mark_sent(cohort_org: str, sent: list[str]) -> int:
    """Stamp `code_sent_at` on the rows that were actually emailed. 0 on success.

    Re-reads first: the code write above moved the sha, and a Join issue can land during
    the several minutes a throttled batch takes."""
    read = get_file_with_sha(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if read is not None:
        raw, sha = read
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        went_out = set(sent)
        marks = [
            (i, s.hertie_email.strip(), stamp)
            for i, s in enumerate(roster.parse(raw))
            if s.hertie_email in went_out
        ]
        if marks and write_column(
            cohort_org,
            raw,
            sha,
            "code_sent_at",
            marks,
            f"roster: record {len(marks)} enrolment code email(s) sent",
        ):
            log_ok(f"recorded {len(marks)} code email(s) as sent")
            return 0
    # The one failure that must never be swallowed: the students HAVE their codes, and
    # nothing on disk says so.
    log_err(
        f"{len(sent)} student(s) were emailed but {roster.ROSTER_PATH} in {cohort_org} "
        f"could not record it - re-running WILL email them again. Fix the roster write, "
        f"or fill code_sent_at by hand."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    # Default ON, and the rendered workflow passes --dry-run / --no-dry-run explicitly.
    # `store_true` meant a bare `python3 -m dsl_course.enrol_codes --cohort-org X` sent
    # for real, with no confirmation step - the safe default lived only in the YAML.
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Preview the codes + emails; write nothing, send nothing (default).",
    )
    args = parser.parse_args()
    # A read helper (or the mail transport) that couldn't reach its API raises; in an
    # Actions log a one-line error beats a traceback, and the run still goes red.
    try:
        return run(args.cohort_org, dry_run=args.dry_run)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
