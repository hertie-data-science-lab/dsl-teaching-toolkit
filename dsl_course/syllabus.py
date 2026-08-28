"""dsl-course syllabus -- build the "Course sessions and readings" section of a syllabus.

A Hertie syllabus lists, session by session, a title, its learning objectives and its
readings. The cohort's `classroom-config/schedule.yml` already holds the first two
(`title:` / `description:`) and each session's `readings/NN_.../` folder holds the third, so
that section can be written for the course team instead of by them.

DELIBERATELY paste-ready output, not an edit of their document. The syllabus is a faculty
document - often the one submitted to the school, and for one live course a Word file
exported to PDF - so a tool that rewrote a region of it would sooner or later overwrite work
the day before a deadline, and could not help the PDF authors at all. This prints the block
and writes it beside the syllabus as `SYLLABUS.sessions.md` (never released to students);
the course team pastes what they want.

Readings are read from the COURSE org's staging repo, not from what has been released: a
syllabus is written before the term starts, when nothing has shipped yet.

Usage:
    python3 -m dsl_course.syllabus --course-org COURSE --cohort-org COHORT \\
        --course-source-repo course-materials-f2026 [--write]
"""

from __future__ import annotations

import argparse
import sys

from . import schedule
from .course import SYLLABUS_SESSIONS_FILE, session_number
from .gh_contents import get_file_content, put_file, repo_tree
from .log import log, log_err, log_ok, log_step
from .readings import demote_headings, readings_block
from .repos import default_branch
from .schedule_plan import READINGS_SECTION, planned_sessions

# How far a reading list's own headings are pushed down here: the syllabus puts a session at
# `###`, so its `# Session N readings` has to land below that.
_READINGS_SHIFT = 3


def _readings_for(course_org: str, repo: str, paths: tuple[str, ...], n: int) -> str:
    """Session `n`'s reading list from its `readings/NN_.../` folder, or "" when it has none.
    The folder is matched on its ordinal prefix, since faculty choose the rest.

    `readings_block`'s rule, so the syllabus says what the two websites say. It used to keep
    only citation-extension files, which made a session whose readings are PDFs come out as a
    bare heading with nothing under it - the one destination where uploading a reading and
    writing no prose left NOTHING at all."""
    prefix = next(
        (
            f"{READINGS_SECTION}/{q.split('/')[1]}"
            for q in paths
            if q.startswith(f"{READINGS_SECTION}/")
            and "/" in q[len(READINGS_SECTION) + 1 :]
            and session_number(q.split("/")[1]) == n
        ),
        None,
    )
    if prefix is None:
        return ""
    return readings_block(
        [p[len(prefix) + 1 :] for p in paths if p.startswith(f"{prefix}/")],
        lambda name: get_file_content(course_org, repo, f"{prefix}/{name}"),
    )


def build(course_org: str, cohort_org: str, source_repo: str) -> tuple[str, int]:
    """The syllabus's sessions section as markdown, plus how many sessions it holds.

    The count is returned rather than scraped back out of the text. It was counted by
    searching the finished markdown for a marker that the formatter had since changed, so it
    was always zero and the CLI always reported "no dated sessions" - state the builder
    already had, thrown away and re-derived wrongly.

    Sessions come from `schedule_plan.planned_sessions`, the same function the website
    reads, so the two cannot disagree about what session 3 is called. Re-deriving it here
    did: it took the title from the earliest deploy touching a session whether or not that
    entry declared one, so a readings-only or "Course opens" entry silently blanked a
    session the site names."""
    sched = schedule.load(cohort_org)
    branch = default_branch(course_org, source_repo)
    paths = repo_tree(course_org, source_repo, branch, "blob")

    # Lecture rows only: a week's lab is its own row on the website, but a syllabus lists
    # the session once.
    rows = {
        int(ordinal): row
        for (ordinal, kind), row in planned_sessions(sched).items()
        if kind == "lecture"
    }

    out = ["## Course sessions and readings", ""]
    for n in sorted(rows):
        row = rows[n]
        out.append(f"### Session {n}{f': {row.subtitle}' if row.subtitle else ''}")
        out.append("")
        if row.description:
            out.append(f"*Learning objectives.* {' '.join(row.description.split())}")
            out.append("")
        readings = _readings_for(course_org, source_repo, paths, n)
        if readings:
            # The teaching team's own headings and ordering kept verbatim - a syllabus's
            # `Required Readings` / `Optional Readings` split is theirs to make - but pushed
            # below the session heading above them.
            out.append(demote_headings(readings, _READINGS_SHIFT))
            out.append("")
    return "\n".join(out).rstrip() + "\n", len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--course-org", required=True)
    ap.add_argument("--cohort-org", required=True)
    ap.add_argument("--course-source-repo", required=True)
    ap.add_argument(
        "--write",
        action="store_true",
        help=f"also commit the block to {SYLLABUS_SESSIONS_FILE} in the source repo",
    )
    a = ap.parse_args()

    log_step(f"Building the syllabus sessions block from {a.cohort_org}'s schedule.yml")
    body, sessions = build(a.course_org, a.cohort_org, a.course_source_repo)
    if not sessions:
        log_err(
            f"{a.cohort_org}'s schedule.yml names no dated sessions, so there is nothing "
            "to write. Add `releases:` entries (see docs/07) and run this again."
        )
        return 1
    log(f"\n{body}")
    if not a.write:
        log_ok(f"{sessions} session(s) - paste the block above, or re-run with --write")
        return 0
    header = (
        "<!-- Generated by `python3 -m dsl_course.syllabus` from this cohort's\n"
        "     classroom-config/schedule.yml and this repo's readings/ folders. Paste what\n"
        "     you want into SYLLABUS.md; edits here are overwritten. Never released to\n"
        "     students. -->\n\n"
    )
    if not put_file(
        a.course_org,
        a.course_source_repo,
        SYLLABUS_SESSIONS_FILE,
        (header + body).encode(),
        "docs: regenerate the syllabus sessions block",
    ):
        return 1
    log_ok(f"{sessions} session(s) -> {a.course_source_repo}/{SYLLABUS_SESSIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
