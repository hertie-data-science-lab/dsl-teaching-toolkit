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
from .site import _demote_headings
from .utils import (
    default_branch,
    get_file_content,
    log,
    log_err,
    log_ok,
    log_step,
    put_file,
    repo_tree,
    session_number,
)

# Written beside the syllabus, and named `.sessions.md` so it reads as a companion to
# SYLLABUS.md rather than a rival to it.
SESSIONS_FILE = "SYLLABUS.sessions.md"
READINGS_SECTION = "readings"
# The same text extensions the site treats as the reading list itself rather than a
# copyrighted attachment.
READING_LIST_EXTS = frozenset({"md", "markdown", "txt", "bib"})


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""


def _readings_for(course_org: str, repo: str, paths: tuple[str, ...], n: int) -> str:
    """The citation text a session's `readings/NN_.../` folder holds, or "" when it has
    none. Folder names carry an ordinal prefix, so the session is matched on that rather
    than on the rest of the name, which faculty choose freely."""
    prefix = None
    for path in paths:
        head, _, rest = path.partition("/")
        if head != READINGS_SECTION or not rest:
            continue
        folder = rest.split("/", 1)[0]
        if session_number(folder) == n:
            prefix = f"{READINGS_SECTION}/{folder}"
            break
    if prefix is None:
        return ""
    parts = []
    for path in paths:
        if not path.startswith(f"{prefix}/") or _ext(path) not in READING_LIST_EXTS:
            continue
        text = (get_file_content(course_org, repo, path) or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def build(course_org: str, cohort_org: str, source_repo: str) -> str:
    """The syllabus's sessions section, as markdown."""
    sched = schedule.load(cohort_org)
    branch = default_branch(course_org, source_repo)
    paths = repo_tree(course_org, source_repo, branch, "blob")

    rows: dict[int, tuple[str, str]] = {}
    for release in sorted(
        (r for r in sched.releases if r.when is not None), key=lambda r: r.when
    ):
        for d in release.deploy:
            dest = (d.cohort_dest_path or d.course_source_path).strip("/")
            n = session_number(dest.rsplit("/", 1)[-1])
            # First entry naming a session wins, matching how the site dates and names a
            # row - so the syllabus and the website cannot disagree about session 3.
            if n is not None and n not in rows:
                rows[n] = (release.title, release.description)

    out = ["## Course sessions and readings", ""]
    for n in sorted(rows):
        title, objectives = rows[n]
        out.append(f"### Session {n}{f': {title}' if title else ''}")
        out.append("")
        if objectives:
            out.append(f"*Learning objectives.* {' '.join(objectives.split())}")
            out.append("")
        readings = _readings_for(course_org, source_repo, paths, n)
        if readings:
            # The teaching team's own headings and ordering kept verbatim - a syllabus's
            # `Required Readings` / `Optional Readings` split is theirs to make - but pushed
            # below the session heading above them. A reading file opens with its own
            # `# Session N readings`, which unshifted outranks everything around it.
            out.append(_demote_headings(readings, shift=3))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--course-org", required=True)
    ap.add_argument("--cohort-org", required=True)
    ap.add_argument("--course-source-repo", required=True)
    ap.add_argument(
        "--write",
        action="store_true",
        help=f"also commit the block to {SESSIONS_FILE} in the source repo",
    )
    a = ap.parse_args()

    log_step(f"Building the syllabus sessions block from {a.cohort_org}'s schedule.yml")
    body = build(a.course_org, a.cohort_org, a.course_source_repo)
    sessions = body.count("**Session ")
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
        SESSIONS_FILE,
        (header + body).encode(),
        "docs: regenerate the syllabus sessions block",
    ):
        return 1
    log_ok(f"{sessions} session(s) -> {a.course_source_repo}/{SESSIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
