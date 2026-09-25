"""dsl-course syllabus -- build the "Course sessions and readings" section of a syllabus.

A Hertie syllabus lists, session by session, a title, its learning objectives and its
readings. The semester's `semester-config/schedule.yml` already holds the first two
(`title:` / `details:` of each lecture entry) and its readings entries name the third, so
that section can be written for the course team instead of by them.

DELIBERATELY paste-ready output, not an edit of their document. The syllabus is a faculty
document - often the one submitted to the school, and for one live course a Word file
exported to PDF - so a tool that rewrote a region of it would sooner or later overwrite work
the day before a deadline, and could not help the PDF authors at all. This prints the block
and writes it to `.system/SYLLABUS.sessions.md` (never released to students);
the course team pastes what they want.

Readings are read from the COURSE org's source repos, not from what has been released: a
syllabus is written before the term starts, when nothing has shipped yet. Every entry of
kind `readings`, from any repo, is listed under the first lecture on or after its date
(readings ship ahead of their session); one after the last lecture closes the list.

Usage:
    python3 -m dsl_course.syllabus --course-org COURSE --semester-org SEMESTER \\
        --course-source-repo course-materials-f2026 [--no-preview]
"""

from __future__ import annotations

import sys

from . import schedule
from .course import SYLLABUS_SESSIONS_FILE
from .gh_contents import get_file_content, put_file, repo_tree
from .log import (
    CLIParser,
    Summary,
    add_preview_flag,
    log,
    log_err,
    log_ok,
    log_step,
    plural,
)
from .materials import read as read_materials
from .readings import demote_headings, readings_block
from .repos import default_branch
from .schedule_plan import PlannedRow, planned_rows

# How far a reading list's own headings are pushed down here: the syllabus puts a session at
# `###`, so its `# Session N readings` has to land below that.
_READINGS_SHIFT = 3


def _readings_for(course_org: str, row: PlannedRow, trees: dict) -> str:
    """A readings entry's reading list, from what its copies take out of the COURSE org:
    a file, a folder, or a whole repo, each through `readings_block`, the rule the site
    uses too."""
    parts = []
    for d in row.deploys:
        repo, src = d.course_source_repo, d.course_source_path.strip("/")
        if repo not in trees:
            trees[repo] = repo_tree(
                course_org, repo, default_branch(course_org, repo), "blob"
            )
        if src in trees[repo]:  # one file
            base, _, name = src.rpartition("/")
            names = [name]
        else:  # a folder, or the whole repo
            base, prefix = src, f"{src}/" if src else ""
            names = [p[len(prefix) :] for p in trees[repo] if p.startswith(prefix)]
        text = readings_block(
            names,
            lambda name, repo=repo, base=base: get_file_content(
                course_org, repo, f"{base}/{name}" if base else name
            ),
        )
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def build(course_org: str, semester_org: str) -> tuple[str, int]:
    """The syllabus's sessions section as markdown, plus how many sessions it holds.

    Sessions are the schedule's shown lecture entries, from `schedule_plan.planned_rows`,
    the rows the website reads, so the two cannot disagree about what a session is called.
    Readings attach by date (see the module docstring)."""
    sched = schedule.load(semester_org)
    rows = planned_rows(sched, lambda repo: read_materials(course_org, repo).kinds)
    lectures = [r for r in rows if r.shown and r.kind == "lecture"]
    readings: dict[int, list[PlannedRow]] = {}
    for r in rows:
        if r.kind == "readings":
            at = next(
                (i for i, lec in enumerate(lectures) if lec.when >= r.when),
                len(lectures),
            )
            readings.setdefault(at, []).append(r)
    trees: dict[str, tuple[str, ...]] = {}

    out = ["## Course sessions and readings", ""]
    for i in range(len(lectures) + 1):
        if i < len(lectures):
            row = lectures[i]
            out.append(
                f"### Session {i + 1}{f': {row.subtitle}' if row.subtitle else ''}"
            )
            out.append("")
            if row.details:
                out.append(f"*Learning objectives.* {' '.join(row.details.split())}")
                out.append("")
        elif readings.get(i):
            out.append("### Further readings")
            out.append("")
        for r in readings.get(i, []):
            text = _readings_for(course_org, r, trees)
            if text:
                # The teaching team's own headings and ordering kept verbatim - a
                # syllabus's `Required Readings` / `Optional Readings` split is theirs to
                # make - but pushed below the session heading above them.
                out.append(demote_headings(text, _READINGS_SHIFT))
                out.append("")
    return "\n".join(out).rstrip() + "\n", len(lectures)


def main() -> int:
    ap = CLIParser(description=__doc__)
    ap.add_argument("--course-org", required=True)
    ap.add_argument("--semester-org", required=True)
    ap.add_argument("--course-source-repo", required=True)
    add_preview_flag(
        ap, "Print the block; commit nothing to the source repo (default)."
    )
    a = ap.parse_args()

    log_step(
        f"Building the syllabus sessions block from {a.semester_org}'s schedule.yml"
    )
    body, sessions = build(a.course_org, a.semester_org)
    if not sessions:
        log_err(
            f"{a.semester_org}'s schedule.yml names no dated sessions, so there is nothing "
            "to write. Add `releases:` entries (see docs/07) and run this again."
        )
        text = "The semester's schedule.yml has no dated sessions, so there is no list."
        return Summary(text, reasons=[{"code": "NO_SESSIONS", "text": text}], code=1)
    log(f"\n{body}")
    counts = {"sessions": sessions}
    listed = plural(sessions, "session")
    if a.preview:
        log_ok(
            f"{sessions} session(s) - paste the block above, or re-run with --no-preview"
        )
        return Summary(
            f"Built the session list: {listed}; nothing was written.",
            counts,
            block=body,
        )
    header = (
        "<!-- Generated by `python3 -m dsl_course.syllabus` from this semester's\n"
        "     semester-config/schedule.yml and its readings entries. Paste what\n"
        "     you want into SYLLABUS.md; edits here are overwritten. Never released to\n"
        "     students. -->\n\n"
    )
    target = f"{a.course_source_repo}/{SYLLABUS_SESSIONS_FILE}"
    if not put_file(
        a.course_org,
        a.course_source_repo,
        SYLLABUS_SESSIONS_FILE,
        (header + body).encode(),
        "docs: regenerate the syllabus sessions block",
    ):
        text = f"The session list could not be written to {target}."
        return Summary(
            text,
            counts,
            [{"code": "WRITE_FAILED", "text": text}],
            code=1,
            block=body,
        )
    log_ok(f"{sessions} session(s) -> {target}")
    return Summary(
        f"Wrote the session list ({listed}) to {target}.", counts, block=body
    )


if __name__ == "__main__":
    sys.exit(main())
