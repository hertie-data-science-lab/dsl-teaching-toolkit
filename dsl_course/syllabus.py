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
syllabus is written before the term starts, when nothing has shipped yet. Sessions, their
numbers and the readings under each are the website's own rows
(`schedule_plan.site_rows`): an untitled readings entry, from any repo, sits under the
first lecture on or after its date; any other readings entry closes the list under
"Further readings".

`--course-source-repo` names the materials repo the block is written into
(`.system/SYLLABUS.sessions.md`); it no longer limits where readings are read from.

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
from .schedule_plan import PlannedRow, planned_rows, site_rows

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

    Sessions are the shown lecture rows of `schedule_plan.site_rows`, numbered as the
    website numbers them, so the two cannot disagree about what session 3 is called."""
    sched = schedule.load(semester_org)
    rows = planned_rows(sched, lambda repo: read_materials(course_org, repo).kinds)
    shown = site_rows(rows)
    lectures = [sr for sr in shown if sr.row.shown and sr.row.kind == "lecture"]
    further = [sr.row for sr in shown if sr.row.kind == "readings"]
    trees: dict[str, tuple[str, ...]] = {}

    def readings(entries: list[PlannedRow]) -> list[str]:
        out = []
        for r in entries:
            text = _readings_for(course_org, r, trees)
            if text:
                # The teaching team's own headings and ordering kept verbatim - a
                # syllabus's `Required Readings` / `Optional Readings` split is theirs to
                # make - but pushed below the session heading above them.
                out += [demote_headings(text, _READINGS_SHIFT), ""]
        return out

    out = ["## Course sessions and readings", ""]
    for sr in lectures:
        row = sr.row
        out += [
            f"### Session {sr.number}{f': {row.subtitle}' if row.subtitle else ''}",
            "",
        ]
        if row.details:
            out += [f"*Learning objectives.* {' '.join(row.details.split())}", ""]
        out += readings(sr.readings)
    tail = readings(further)
    if tail:
        out += ["### Further readings", "", *tail]
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
