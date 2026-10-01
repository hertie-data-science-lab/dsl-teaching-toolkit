"""dsl-course syllabus -- build a syllabus's weekly plan and write it into the syllabus.

A Hertie syllabus lists, session by session, a title, its learning objectives and its
readings. The semester's `semester-config/schedule.yml` already holds the first two
(`title:` / `details:` of each lecture entry) and its readings entries name the third, so
that plan can be written for the course team instead of by them.

The syllabus is a faculty document, so a write touches ONLY the block between
`<!-- dsl:weekly-plan -->` and `<!-- /dsl:weekly-plan -->` in the chosen Markdown file
(decision 0031 rule 9). Where the markers are absent they are appended, under a
`## Weekly plan` heading, at the end; the course team may move the marked block anywhere in
the file and the next write updates it in place. A syllabus that is not Markdown (a PDF)
is never written: the preview hands the block over to paste.

Readings are read from the COURSE org's source repos, not from what has been released: a
syllabus is written before the term starts, when nothing has shipped yet. Sessions, their
numbers and the readings under each are the website's own rows
(`schedule_plan.site_rows`): a numbered readings entry, from any repo, sits under the
lecture with its number; any other readings entry closes the list under
"Further readings".

`--course-source-repo` names the materials repo holding the syllabus; `--syllabus` the file
in it (default: the one its `materials.yml` declares, else SYLLABUS.md). Neither limits
where readings are read from.

Usage:
    python3 -m dsl_course.syllabus --course-org COURSE --semester-org SEMESTER \\
        --course-source-repo course-materials-f2026 [--syllabus SYLLABUS.md] [--no-preview]
"""

from __future__ import annotations

import sys

from . import schedule
from .gh_contents import get_file_content, get_file_with_sha, put_file, repo_tree
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
from .materials import PLAN_END, PLAN_START
from .materials import read as read_materials
from .readings import demote_headings, readings_block
from .repos import default_branch
from .schedule_plan import PlannedRow, planned_rows, site_rows

# How far a reading list's own headings are pushed down here: the syllabus puts a session at
# `###`, so its `# Session N readings` has to land below that.
_READINGS_SHIFT = 3

PLAN_HEADING = "## Weekly plan"


def place(text: str, body: str) -> str:
    """`text` (the syllabus) with `body` between the plan markers: the block replaced
    where both markers stand in order, else the markers and block appended at the end
    under `## Weekly plan`. Nothing outside the markers changes."""
    block = f"{PLAN_START}\n{body.strip()}\n{PLAN_END}"
    start = text.find(PLAN_START)
    end = text.find(PLAN_END, start + len(PLAN_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        return text[:start] + block + text[end + len(PLAN_END) :]
    head = text.rstrip("\n")
    return (
        f"{head}\n\n{PLAN_HEADING}\n\n{block}\n"
        if head
        else f"{PLAN_HEADING}\n\n{block}\n"
    )


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
    """The weekly plan as markdown (a `###` per session, the block's own heading left to
    the syllabus), plus how many sessions it holds.

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

    out = []
    for sr in lectures:
        row = sr.row
        out += [
            (
                f"### Session{f' {sr.number}' if sr.number else ''}"
                f"{f': {row.subtitle}' if row.subtitle else ''}"
            ),
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
    ap.add_argument(
        "--syllabus",
        default="",
        help="The Markdown file to write into (default: the declared syllabus).",
    )
    add_preview_flag(ap, "Print the block; write nothing (default).")
    a = ap.parse_args()

    log_step(f"Building the weekly plan from {a.semester_org}'s schedule.yml")
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
            f"Built the weekly plan: {listed}; nothing was written.",
            counts,
            block=body,
        )
    path = (
        a.syllabus.strip().strip("/")
        or read_materials(a.course_org, a.course_source_repo).syllabus
    )
    target = f"{a.course_source_repo}/{path}"

    def refuse(code: str, text: str) -> Summary:
        log_err(text)
        return Summary(text, counts, [{"code": code, "text": text}], code=1, block=body)

    if not path.lower().endswith((".md", ".markdown")):
        return refuse(
            "NOT_MARKDOWN",
            f"{target} is not a Markdown file, so the plan cannot be written into it. "
            "Copy it and paste it in.",
        )
    current = get_file_with_sha(a.course_org, a.course_source_repo, path)
    if current is None:
        return refuse(
            "NO_SYLLABUS", f"There is no {target} yet. Write the syllabus first."
        )
    text, sha = current
    if not put_file(
        a.course_org,
        a.course_source_repo,
        path,
        place(text, body).encode(),
        f"docs: write the weekly plan into {path}",
        expected_sha=sha,
    ):
        return refuse(
            "WRITE_FAILED", f"The weekly plan could not be written to {target}."
        )
    log_ok(f"{sessions} session(s) -> {target}")
    return Summary(
        f"Wrote the weekly plan ({listed}) into {target}.", counts, block=body
    )


if __name__ == "__main__":
    sys.exit(main())
