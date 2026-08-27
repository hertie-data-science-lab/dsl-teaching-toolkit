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
from .utils import get_file_content, log_err, log_ok, log_step, put_file, strip_bom

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
    round-trip through `roster.dump`, which re-serialised only `roster.FIELDS` and rewrote
    `role`, silently dropping unknown columns and mangling role text on every code write.

    The `enrol_code` column is appended if the roster predates it."""
    reader = csv.DictReader(io.StringIO(strip_bom(text)))
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

    `course_name` names the course in the opening line - a student reading several of
    these in one week needs to know which one this is. It is optional because the name is
    read live from the course org's dsl-course.yml, which a half-configured course may
    not carry yet; the wording then falls back to "the course" rather than emailing a
    blank."""
    course = f"the {course_name}" if course_name else "the"
    subject = "Your course enrolment code"
    body = (
        f"Hello {student.name or 'there'},\n\n"
        f"To join {course} course on GitHub, open a 'Join' issue here:\n"
        f"  {welcome_url}\n\n"
        f"and paste this enrolment code when asked:\n\n"
        f"    {student.enrol_code}\n\n"
        f"Whichever GitHub account opens the issue is linked to your hertie email "
        f"address automatically.\n"
    )
    return (student.hertie_email, subject, body)


def run(cohort_org: str, dry_run: bool = False) -> int:
    # Fetch the RAW roster text once: we parse it for the students, and (below) edit the same
    # text in place so writing codes back never disturbs columns roster doesn't model.
    raw = get_file_content(cohort_org, roster.CONFIG_REPO, roster.ROSTER_PATH)
    if raw is None:  # genuinely absent - mirror roster.load's message
        log_err(
            f"Could not find {roster.ROSTER_PATH} in {cohort_org}/{roster.CONFIG_REPO} - "
            f"bootstrap the cohort first (bootstrap_course --cohort)."
        )
        return 1
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
        # Write ONLY the newly filled enrol_code cells back into the raw CSV, preserving every
        # other column and each cell's raw text (see fill_enrol_codes_in_csv). Row order
        # matches roster.parse, so index i lines up with students[i].
        codes_by_row = {
            i: s.enrol_code
            for i, s in enumerate(students)
            if not before[i] and s.enrol_code
        }
        body = fill_enrol_codes_in_csv(raw, codes_by_row)
        if not put_file(
            cohort_org,
            roster.CONFIG_REPO,
            roster.ROSTER_PATH,
            body.encode(),
            f"roster: assign {added} enrolment code(s)",
        ):
            return 1
        log_ok(f"wrote {added} code(s) to {roster.ROSTER_PATH}")

    welcome_url = f"https://github.com/{cohort_org}/welcome/issues/new/choose"
    targets = [
        s for s in students if s.enrol_code and s.hertie_email and not s.onboarded
    ]
    if not targets:
        log_ok("no not-yet-onboarded students with an email to mail.")
        return 0
    course_name = course_name_for_cohort(cohort_org)
    messages = [code_message(s, welcome_url, course_name) for s in targets]
    sent = mailer.send_bulk(messages, dry_run=dry_run)
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
