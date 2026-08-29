"""dsl-course enrol-codes -- generate per-student enrolment codes and email them.

Students enrol by pasting a random, **non-PII** code (not their email) into the welcome
Join issue, so no personal data ever touches the public repo - and because the code is
unguessable, a classmate can't bind your roster row to their account. This one action:

    1. fills blank `enrol_code` cells in classroom-config/students.csv (idempotent), then
    2. emails each not-yet-onboarded student their code over SMTP (preview with --dry-run).

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

from . import mailer, roster
from .discovery import course_name_for_cohort
from .gh_contents import get_file_with_sha, put_file, read_csv
from .log import log_err, log_ok, log_step

# No ambiguous characters (0/O, 1/l/I) so a student can read the code off an email.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def make_code() -> str:
    return "dsl-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def fill_enrol_codes_in_csv(text: str, codes_by_row: dict[int, str]) -> str:
    """Surgically write enrol codes into the RAW students.csv, preserving everything else.

    `codes_by_row` maps a 0-based DATA-row index (matching `roster.parse` order) to the code
    to drop into that row's blank `enrol_code` cell. Only that one column is touched: every
    other column - including ones `roster.FIELDS` doesn't know about, e.g. a faculty-added
    `notes`/`moodle_id` - is carried through untouched, and no cell's raw text is normalised
    (so a `role` of `audit`/`Auditor` is left exactly as written). This replaces the old
    round-trip through a whole-file re-serialisation, which wrote only `roster.FIELDS` and
    rewrote
    `role`, silently dropping unknown columns and mangling role text on every code write.

    The `enrol_code` column is appended if the roster predates it."""
    # read_csv, not a bare DictReader: this path bypasses `roster.parse`, and a
    # `;`-delimited Excel export was written straight back with a code column bolted on -
    # exit 0, roster destroyed.
    reader = read_csv(text, roster.REQUIRED_FIELDS, roster.ROSTER_PATH)
    fieldnames = list(reader.fieldnames or [])
    if "enrol_code" not in fieldnames:
        fieldnames.append("enrol_code")
    rows = list(reader)
    for i, row in enumerate(rows):
        if i in codes_by_row and not (row.get("enrol_code") or "").strip():
            row["enrol_code"] = codes_by_row[i]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return out.getvalue()


def rows_for_codes(text: str, codes: list[tuple[int, str, str]]) -> dict[int, str]:
    """Where each generated code belongs in `text`, as `{row index: code}`.

    `codes` is `(row index at generation time, hertie_email, code)`. A row is re-located by
    EMAIL, because a row index taken from an earlier read is not stable across an edit that
    landed in between - a Join issue filling in a handle, a faculty member inserting a row.
    A row carrying no email at all cannot be re-located and keeps its original index; that
    is the one row for which a concurrent edit can still land the code on the wrong line,
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
    return {
        (at if (at := seen.get(email, -1)) >= 0 else index): code
        for index, email, code in codes
    }


# Bounded: each attempt costs a read and a write, and a roster being edited faster than
# that is a person at a keyboard, not a race worth grinding against.
WRITE_ATTEMPTS = 3


def write_codes(
    cohort_org: str, raw: str, sha: str, codes: list[tuple[int, str, str]]
) -> bool:
    """Commit the generated codes into students.csv without clobbering a concurrent edit.

    `put_file`'s ordinary path re-reads the sha immediately before writing, so the write
    succeeds however stale its content is: a Join binding committed while Send codes was
    running was silently reverted, and the student's handle was simply gone. The sha the
    roster was READ at is sent instead, so GitHub refuses a write onto a file that has
    moved on. We then re-read, re-apply only the codes THIS run generated onto the fresh
    text - `fill_enrol_codes_in_csv` fills blank cells only, so a code that arrived in
    between is left exactly as it is - and try again."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        body = fill_enrol_codes_in_csv(raw, rows_for_codes(raw, codes))
        if put_file(
            cohort_org,
            roster.CONFIG_REPO,
            roster.ROSTER_PATH,
            body.encode(),
            f"roster: assign {len(codes)} enrolment code(s)",
            expected_sha=sha,
        ):
            return True
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
    return False


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
        f"To join {course} on GitHub, open a 'Join' issue here:\n"
        f"  {welcome_url}\n\n"
        f"and paste this enrolment code when asked:\n\n"
        f"    {student.enrol_code}\n\n"
        f"Whichever GitHub account opens the issue is linked to your hertie email "
        f"address automatically.\n"
    )
    return (student.hertie_email, subject, body)


def sample_body(welcome_url: str, course_name: str = "") -> str:
    """The code email rendered with PLACEHOLDERS, for the dry-run preview.

    A dry run masks every recipient and prints no real body, which leaves the one thing a
    reviewer actually wants to check - the wording - invisible. This is the same
    `code_message` template with `<name>`/`<code>` in place of a student's name and a live
    credential, so it can be printed in a world-readable Actions log."""
    placeholder = roster.Student(
        hertie_email="<email>",
        name="<name>",
        github_handle="",
        github_id="",
        enrol_code="<code>",
    )
    return code_message(placeholder, welcome_url, course_name)[2]


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
        if not write_codes(cohort_org, raw, raw_sha, new_codes):
            log_err(
                f"could not write the enrolment codes to {roster.ROSTER_PATH} in "
                f"{cohort_org} - nothing emailed, so re-running is safe."
            )
            return 1
        log_ok(f"wrote {added} code(s) to {roster.ROSTER_PATH}")

    welcome_url = f"https://github.com/{cohort_org}/welcome/issues/new/choose"
    targets = [
        s for s in students if s.enrol_code and s.hertie_email and not s.onboarded
    ]
    if not targets:
        log_ok("no not-yet-onboarded students with an email to mail.")
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
    return 0 if sent == len(messages) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort-org", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the codes + emails; write nothing, send nothing.",
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
