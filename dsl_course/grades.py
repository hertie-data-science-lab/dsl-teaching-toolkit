"""dsl-course grades -- the grading sheet, and sending what a grader wrote in it.

A grader fills ONE file per assignment, `semester-config/grading_sheets/<slug>.yml`, and
`distribute` fans it out:

    grading_sheets/<slug>.yml   (the grader types here; the toolkit owns only `info:`)
          |
          +--> semester/grades-<handle>   (private; student = read) grades.yml + README.md
          +--> semester-config/.system/semester-grades.csv   (the semester grades file, never logged)
          +--> an email saying there is something new to read (no marks in it)

ONE place a mark is written, and it is the student's gradebook. Nothing is posted into a
submission repo; that repo's issue carries the submission receipts and nothing else.

Nothing is said twice: every send is recorded in `.system/gradebook/distributed.csv`, so a re-run
after one correction reaches one student.

Usage:
    python3 -m dsl_course.grades distribute --semester-org hertie-dsl-demo-f2026 [--preview]
"""

from __future__ import annotations

import sys

from .distribute import dispatch_refusal, distribute
from .log import CLIParser, add_preview_flag, log_err


def main() -> int:
    parser = CLIParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("distribute")
    p.add_argument("--semester-org", required=True)
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip the email notification (just push the grades).",
    )
    p.add_argument(
        "--receipt-note",
        action="store_true",
        help="Also post 'Marks returned: see your marks repo.' once on each returned "
        "unit's Submission receipts issue.",
    )
    p.add_argument(
        "--include-feedback",
        action="store_true",
        help="Put the markers' feedback text into each student's email.",
    )
    per_question = p.add_mutually_exclusive_group()
    per_question.add_argument(
        "--per-question",
        action="store_true",
        help="For this run only, show the marks and feedback per question of the "
        "assignment returned (every assignment, without --assignment), whatever its "
        "`marks_per_question` run setting says.",
    )
    per_question.add_argument(
        "--no-per-question",
        action="store_true",
        help="For this run only, withhold them, whatever the setting says: the score, "
        "late penalty, adjustment and final grade still go.",
    )
    p.add_argument(
        "--assignment",
        default="",
        help="Return this assignment only (its semester-side name), beside the ones "
        "already returned. Default: every sheet.",
    )
    p.add_argument(
        "--dispatched-by",
        default="",
        help="The course org whose Scheduled release asked for this automatic return "
        "(a `return-marks` dispatch): the semester must be in its registry, and the "
        "assignment must be due and fully marked, or nothing is sent.",
    )
    # Default ON: the rendered workflow passes --preview / --no-preview explicitly, so a
    # bare local invocation cannot send by accident.
    add_preview_flag(
        p,
        "Post who gets what as an issue in semester-config; push no grades, send nothing (default).",
    )
    args = parser.parse_args()

    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        sched = None  # read once per run: the dispatch check hands on what it read
        if args.dispatched_by:
            refusal, sched = dispatch_refusal(
                args.dispatched_by, args.semester_org, args.assignment
            )
            if refusal:
                log_err(refusal)
                return 1
        return distribute(
            args.semester_org,
            notify=not args.no_notify,
            dry_run=args.preview,
            receipt_note=args.receipt_note,
            include_feedback=args.include_feedback,
            per_question=(
                True if args.per_question else False if args.no_per_question else None
            ),
            assignment=args.assignment or None,
            sched=sched,
        )
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
