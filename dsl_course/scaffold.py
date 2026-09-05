"""dsl-course scaffold -- create correctly-structured course-materials / assignment repos.

Replaces the old "use this template" repo: the required structure is defined here in
code, so a new repo is always laid out the way the Release actions expect.

    scaffold materials   --org X --tag f2026                 -> course-materials-f2026
    scaffold assignment  --org X --number 1 --tag f2026      -> assignment-1-f2026

Materials repos get `lectures/`, `readings/` and `labs/` `01_session-1/` skeletons (any
top-level directory with an ordinal-prefixed subdirectory is a releasable section - add
more, e.g. `datasets/`, freely; delete `labs/` if unused) and the run-from-repo Release
workflows. Assignment repos get a starter on `main` (no tests - grading is faculty-side)
and a `solution` branch carrying the model solution, `grading_config.yml`, and the HIDDEN
tests, so generate never ships any of them to students.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from .access import COURSE_TEAM_ACCESS, grant_faculty, grant_tagged_team_access
from .central import CENTRAL
from .course import (
    FACULTY_ONLY_HEADING,
    MATERIALS_REPO_PREFIX,
    SYLLABUS_SAMPLE_FILE,
    pages_repo,
)
from .discovery import central_ref_for, discover_assignments, discover_cohorts
from .gh_contents import put_files, seed_if_absent
from .ghcli import GIT_ENV, clone, gh, git, is_already_exists
from .log import log, log_err, log_ok, log_skip, log_step
from .readings import READING_OVERLAY_FILE
from .releaseignore import RELEASEIGNORE
from .repos import create_repo, repo_exists, set_repo_topics
from .welcome import TEMPLATES, example_course_file
from .workflows_place import push_content_workflows

# The site repo's Pages build, seeded as its FIRST commit. `create_repo` does not auto-init,
# and Pages cannot be enabled - nor the first deploy dispatched - on a repo with no branch.
# Everything else a site holds arrives with the first `site sync`.
SITE_DEPLOY_WORKFLOW = ".github/workflows/deploy.yml"


_SYLLABUS_STUB = """\
# {tag} syllabus

*Optional - delete this file if your course does not need it.*

<!-- dsl-stub: still the scaffold's, so the toolkit keeps it up to date. Delete this
     comment (or just write over the file) and it is yours - never touched again.

     FACULTY & INSTRUCTORS: this is the students' syllabus, and it is yours to write - the
     headings below are the standard Hertie shape, so delete what your course does not use.
     Release it by naming this file as the release path (see MAINTAINING.md); the name and
     its capitalisation must match exactly, and any format works - rename this to
     SYLLABUS.pdf and release that instead if you author in Word.
     A filled example sits beside this file in SYLLABUS.md.sample. -->

## 1. General information

| | |
| --- | --- |
| Instructor | |
| E-mail | |
| Office hours | |
| Term | {tag} |
| Sessions | |
| Language of instruction | English |

## 2. Course contents and learning objectives

### Course contents

### Main learning objectives

### Target group

### Prerequisites

## 3. Grading and assignments

| Component | Weight | Due |
| --- | --- | --- |
| | | |

## 4. General readings

## 5. Course sessions and readings

<!-- The course website publishes this session by session, built from
     `classroom-config/schedule.yml` (each session's title and learning objectives) and
     each session's `readings/NN_.../` folder (its reading list). If you also list the
     sessions here - Hertie syllabi normally do - keep the two in step, or students will
     read one and see the other. -->
"""

# The filled syllabus faculty copy from, seeded beside their own SYLLABUS.md as
# SYLLABUS.md.sample. Its BODY is the worked example course's real syllabus
# (example-course/course-org/course-materials-f2026/SYLLABUS.md) rather than a second copy
# authored here - the same rule the classroom-config samples follow, so the syllabus the
# docs link to as the live example is the one faculty actually receive. Only the ownership
# notice is added here, at the write site: the example file is a course team's own
# INSTRUCTOR-OWNED syllabus in its own org, and must not claim otherwise.
_SYLLABUS_SAMPLE_NOTICE = """
*Optional - a worked example to copy from; delete it if you do not want it.*

<!-- SYSTEM-OWNED - do not edit, edits here are overwritten. A FILLED example, kept
     current by the toolkit: copy from it. Your own syllabus is SYLLABUS.md beside this
     file. This file is never released to students. -->
"""

EXAMPLE_SYLLABUS = "course-materials-f2026/SYLLABUS.md"


def _syllabus_sample() -> str:
    """The worked example syllabus with the sample's ownership notice under its title."""
    title, _, body = example_course_file(EXAMPLE_SYLLABUS).partition("\n")
    return f"{title}\n{_SYLLABUS_SAMPLE_NOTICE}{body}"


# The seeded reading list, shaped like a Hertie syllabus's readings block so a course
# team can paste theirs straight in: `Required Readings` / `Optional Readings` as
# sub-headings, and any further category (some syllabi add `Application Readings`)
# works the same way - the site renders whatever headings this file has, nested under
# the session's own. A stub rather than a `.gitkeep`: the folder's files are listed
# automatically, but nothing about an empty folder said so, nor that this file is where
# an online reading goes - the tab read blank with nothing to explain why.
_READINGS_STUB = (
    b"# Session 1 readings\n\n"
    b"<!-- dsl-stub: still the scaffold's, so the toolkit keeps it up to date.\n"
    b"     Write over it and it is yours. This file is OPTIONAL - delete it and the\n"
    b"     files you put in this folder are still listed. -->\n\n"
    b"Drop the readings themselves into this folder - PDFs, slides, notebooks,\n"
    b"anything. Every file here is listed and linked for enrolled students and\n"
    b"auditors automatically; you do not have to name them here as well.\n\n"
    b"This file is for what a file cannot say: a link to read online, or a proper\n"
    b"citation. Anything goes - a bare URL on its own line is fine.\n\n"
    b"## Required Readings\n\n"
    b"- Author, *Title*, ch. 1.\n"
    b"- https://example.org/an-online-reading\n\n"
    b"## Optional Readings\n\n"
    b"- Author, *Title*, ch. 2.\n\n"
    b"What you write here is PUBLIC (it is a citation list). The files beside it\n"
    b"stay behind the enrolled-student/auditor gate, unless the course runs a\n"
    b"public open-courseware site in `actual-readings` mode, which serves them\n"
    b"too. The session's learning objectives come from `description:` in\n"
    b"schedule.yml.\n"
)

_GRADING_YML = """\
# How the autograder marks this assignment (at the cutoff
# set in schedule.yml). Delete this file (or set autograde: false) for a purely
# manually-graded assignment.
type: {kind}      # individual (one repo per student) or group (one repo per team)
autograde: true       # false -> skip autograding (all-manual)
tests: tests          # path (on THIS solution branch) holding the hidden tests
                      # how many passed is shown to you as `info.autograde` in the grading
                      # sheet - never a mark by itself, and never something a student sees
"""

_HIDDEN_TEST_PY = """\
# HIDDEN tests - run faculty-side at the cutoff, never shipped to students.
# They import the student's submission (the repo root) and check it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
"""

_HIDDEN_TEST_NOTEBOOK = """\
# HIDDEN tests - run faculty-side at the cutoff, never shipped to students.
# The submitted notebook is nbconvert'd to starter.py first; this imports it and checks it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
"""


def _notebook(title_lines: list[str], code: str) -> str:
    """A minimal valid .ipynb: one markdown cell + one code cell. language_info carries
    `.py` so the grader's nbconvert names its output starter.py (see collect)."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [line + "\n" for line in title_lines],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in code.splitlines()],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "file_extension": ".py"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb, indent=1) + "\n"


# Seeded inert - every line a comment - so it withholds nothing until faculty write a
# pattern. It exists to be FOUND: a withhold list nobody knows about is one nobody uses,
# and a docs page is only reachable by someone who already suspects it exists.
_RELEASEIGNORE_STUB = f"""\
# INSTRUCTOR-OWNED - yours. Written once when this repo was scaffolded, and never
# rewritten by the toolkit, so anything you put here stays.
#
# Name a file here and it is never copied out of this repo - not into a cohort's
# materials, not onto the public site, not with a solution. Same syntax as .gitignore,
# and you can add another in any subfolder. The full rules:
# https://github.com/{CENTRAL}/blob/main/docs/08-release-materials-to-cohort.md
"""


def _actions_table(org: str) -> str:
    # The course org's `.github` Actions tab hosts the workflows that operate this course.
    # Both README (faculty & instructors orientation, pre-release) and MAINTAINING link it.
    actions_url = f"https://github.com/{org}/.github/actions"
    return (
        f"The course org's [`.github` Actions tab]({actions_url}) hosts the workflows that "
        "operate this course:\n\n"
        "| Action | What it does |\n"
        "| --- | --- |\n"
        "| **Release materials** | Copy any path - session folders, root files - into a "
        "cohort's `materials` repo by default, or a destination path you name. |\n"
        "| **Release assignment** | Freeze an assignment template, then generate one private "
        "repo per student (or per team). |\n"
        "| **New materials repo** | Scaffold a correctly structured materials repo; the "
        "release workflows come bootstrapped with it. |\n"
        "| **New assignment** | Scaffold an assignment template (starter + hidden "
        "autograder); the release workflows come bootstrapped with it. |\n"
        "| **Refresh actions** | Re-seed the run-from-repo workflows and repopulate dropdowns "
        "after you add sessions/sections. |\n"
        "| **Check cohort setup** | Read-only per-cohort checklist of what's configured. |\n\n"
        "(**Release materials** and **Release assignment** also appear in this repo's own "
        "Actions tab.)\n"
    )


def _maintaining(org: str, repo: str) -> str:
    """The generated maintainer guide - SYSTEM-owned docs, built from the actions table."""
    actions_table = _actions_table(org)
    return (
        "<!-- SYSTEM-OWNED - do not edit, edits here are overwritten. This file is "
        "regenerated by every Refresh actions run. -->\n\n"
        f"# Maintaining `{repo}`\n\n"
        "Reference for faculty & instructors on how to populate and operate this materials "
        "**source** repo. This file is **not** released to students. Keep student-facing "
        "wording in `README.md` and operational notes here.\n\n"
        "## What to edit vs leave alone\n\n"
        "| You edit / add | Visible to students? | Notes |\n"
        "| --- | --- | --- |\n"
        "| `lectures/`, `labs/`, `readings/` (and any other section folders) | Yes, when you "
        "release that session | The released files are copied into the cohort `materials` "
        "repo, or another destination path you name. |\n"
        "| root files - `SYLLABUS.md`, `README.md`, or any name you use | Yes, when you name "
        "the file as the release path | A root file is released like any other path: type "
        "`SYLLABUS.pdf` (or whatever the file is called) as the `course_source_path`. |\n"
        "| `MAINTAINING.md` (this file) | No | Your reference; never released. Leave it in the "
        "repo. |\n"
        "| `.github/workflows/` (the Release workflows) | No | **Infrastructure - do not edit or "
        "delete.** These run-from-repo workflows are what make releasing work; **Refresh actions** "
        "re-seeds them. |\n\n"
        "Rule of thumb: edit the content folders and the two root files; leave `MAINTAINING.md` "
        "and `.github/workflows/` alone.\n\n"
        "## Structure\n\n"
        "Any top-level directory containing at least one ordinal-prefixed subdirectory "
        "(`01_`, `02_`, `03_`, ...) is a releasable section:\n\n"
        "- `lectures/01_session-1/` - one folder per session's lecture files\n"
        "- `labs/01_session-1/` - one folder per session's lab (delete the `labs/` folder "
        "if your course has none)\n"
        "- `readings/01_session-1/` - one folder per session's readings. Just drop the "
        "readings in and every file is listed and linked for enrolled students "
        "automatically. An additional `READINGS.md` (or `.txt`/`.bib`) is OPTIONAL prose - "
        "a link to read online, or a citation; it is published publicly, while the files "
        "stay behind the enrolled-student gate (unless a public site toggles on "
        "`actual-readings`)\n"
        "- root files - your syllabus under any name (`SYLLABUS.md`, `SYLLABUS.pdf`, ...) "
        "and `README.md`: released by naming the file as the release path (the runner is "
        "case sensitive)\n\n"
        "Add more sessions by creating `lectures/02_session-2/`, `readings/02_session-2/`, ... "
        "(only the ordinal prefix matters - name the rest whatever you like), or add a whole "
        "new section (e.g. `datasets/01_intro/`). Nothing needs refreshing afterwards: the "
        "Release workflows take the path as free text (`course_source_path`), so a new "
        "session or section is releasable the moment you push it. **Refresh actions** "
        "repopulates the repo/cohort dropdowns, and runs itself nightly.\n\n"
        "## Available actions\n\n" + actions_table + "\n"
        "## Public course website (optional) **[DEFERRED]**\n\n"
        "The **Publish course website** action can share this repo's materials on a public "
        "open-courseware site. Lecture files are always hosted; for readings you choose "
        "`reading-list` (text/citation files are shown as a list - keep copyrighted PDFs out "
        "of the list by leaving them as non-text files) or `actual-readings` (every reading "
        "file is hosted and downloadable - you carry the copyright responsibility).\n"
    )


def materials_system_files(org: str, repo: str) -> dict[str, bytes]:
    """The SYSTEM-owned files in a materials repo: this toolkit describing itself, so
    they are REFRESHED rather than frozen - faculty edits here are overwritten, which
    each file says in its own text.

    Named here, where they are written, and pushed by `refresh_materials_system_files`
    from both the scaffold and the nightly refresh - one list rather than two that drift,
    the SYSTEM-owned half of the ownership split; the skeleton above is the other."""
    return {
        SYLLABUS_SAMPLE_FILE: _syllabus_sample().encode(),
        "MAINTAINING.md": _maintaining(org, repo).encode(),
    }


def refresh_materials_system_files(org: str, repo: str) -> int:
    """Re-push a materials repo's SYSTEM-owned files (materials_system_files).

    Called both at scaffold time and on the nightly refresh, so an improvement to the
    maintainer guide or the syllabus example reaches the courses already running. Before
    this they were written only by the scaffold, which made "SYSTEM-owned" true of new
    repos and nothing else: a course scaffolded before the example existed was never going
    to get one. `put_files` compares blob shas, so a repo already current is written
    nothing, and both files land in one commit because they always change together.

    Unlike the stubs this CREATES as well as updates - back-filling a file added after the
    repo was made is the point - so it is gated on the repo NAME, and the gate lives here
    rather than at the call site because no caller may skip it: `discover_content_repos`
    hands the nightly sweep the code and dataset repos too, and a materials-repo
    maintainer guide in `lecture-code-f2026` is the nonsense the scaffold's own gate
    `create=False` exists to avoid. Every materials repo is named `course-materials-<tag>`
    by `scaffold_materials`, from a workflow that takes only the tag, so the prefix is a
    toolkit guarantee rather than a convention.

    Returns 1 if the commit didn't land, so callers go red rather than report a converged
    repo - this runs unattended on a cron, where a silent skip is invisible for weeks."""
    if not repo.startswith(MATERIALS_REPO_PREFIX):
        return 0
    if not put_files(
        org,
        repo,
        materials_system_files(org, repo),
        "docs: maintainer guide + syllabus example",
    ):
        log_err(f"system files not written to {org}/{repo}")
        return 1
    return 0


def materials_readme(org: str) -> str:
    """The materials repo's student-facing README placeholder.

    Module-level, not inline in scaffold_materials, so it can be rendered WITHOUT creating
    a repo. The file is create-only, like every other instructor-owned file in the
    skeleton, so a wording fix reaches a repo that already exists only by writing it
    deliberately, after checking the placeholder is still untouched
    (deploy.UNEDITED_README_MARKERS).

    Release materials with the README toggle copies this file into the cohort's materials
    repo, where enrolled students read it - so it is written for them. How the source repo
    is structured, and how to operate it, is MAINTAINING.md, which is never released.
    """
    actions_table = _actions_table(org)
    return (
        # Two lines below carry deploy.py's UNEDITED_README_MARKERS - the "Replace this
        # placeholder" note and FACULTY_ONLY_HEADING. A release refuses to ship a README
        # still holding BOTH, so edit this stub's wording freely but keep those two intact
        # (test_scaffold.py asserts the seeded file still trips the guard).
        "<!-- INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n\n"
        "     FACULTY & INSTRUCTORS: replace the content below with a real, student-facing\n"
        "     overview of your course materials. Release materials with the 'include README'\n"
        "     toggle copies THIS file into the cohort's materials repo, where enrolled\n"
        "     students read it - so write it for them, not as internal notes. How this source\n"
        "     repo is structured, and how to operate it, is in MAINTAINING.md (for faculty &\n"
        "     instructors only - never released to students). -->\n\n"
        "# Course materials\n\n"
        "> **Replace this placeholder.** This file becomes the students' README for the\n"
        "> released materials. Add a short overview of the course, how the materials are\n"
        "> organised, and anything students should read first.\n\n"
        "---\n\n"
        f"## For faculty & instructors ({FACULTY_ONLY_HEADING})\n\n"
        "- **How to populate & operate this repo:** see [`MAINTAINING.md`](MAINTAINING.md) - "
        "it explains what to edit, what gets released to students, and what to leave alone. "
        "`MAINTAINING.md` is **not** deployed to the cohort org; leave it here as a persistent "
        "reference.\n"
        "- **Available actions:** " + actions_table
    )


def scaffold_materials(org: str, tag: str) -> int:
    repo = f"{MATERIALS_REPO_PREFIX}{tag}"
    log_step(f"Scaffolding {org}/{repo}")
    if not create_repo(
        org,
        repo,
        private=True,
        description=(
            "Course materials (lectures/labs/readings/datasets/other) by session"
        ),
    ):
        return 1
    grant_faculty(org, repo, COURSE_TEAM_ACCESS)
    grant_tagged_team_access(org, repo, tag)
    readme = materials_readme(org)
    failures = 0
    failures += refresh_materials_system_files(org, repo)
    # INSTRUCTOR-OWNED, every one of them, and all CREATE-ONLY: written when the repo is
    # scaffolded and never again. A re-run against a repo faculty have since authored must
    # not revert their work, and neither must the nightly refresh - see the note on
    # `_SYLLABUS_STUB` for why "is it still ours?" is not a question this can ask safely.
    # A failed seed (an absent file whose write failed) reds the scaffold.
    user_files = {
        "README.md": readme.encode(),
        "SYLLABUS.md": _SYLLABUS_STUB.format(tag=tag).encode(),
        "lectures/01_session-1/.gitkeep": b"",
        # A stub, not a .gitkeep: a text file here IS the published reading list (its
        # contents are inlined on the site's Materials tab), and an empty folder gave no
        # sign of that - the tab then reads blank with nothing to explain why.
        f"readings/01_session-1/{READING_OVERLAY_FILE}": _READINGS_STUB,
        "labs/01_session-1/.gitkeep": b"",
        RELEASEIGNORE: _RELEASEIGNORE_STUB.encode(),
    }
    # One commit for the skeleton: they all carried the same subject anyway, so writing
    # them one at a time opened a repo faculty then author by hand with a column of
    # identical `init: materials skeleton` lines.
    if not put_files(
        org, repo, user_files, "init: materials skeleton", create_only=True
    ):
        failures += 1
    # Equip the run-from-repo Release workflows (same as Refresh does for content repos).
    # push_content_workflows lands both in one commit, logs its own failure, and returns
    # 1 - a materials repo with no Release workflows must not report success.
    cohorts = discover_cohorts(org)
    failures += push_content_workflows(
        org, repo, cohorts, discover_assignments(org), central_ref_for(org)
    )
    if failures:
        return 1
    log_ok(f"materials repo ready: {org}/{repo}")
    return 0


def scaffold_assignment(
    org: str, number: str, tag: str, fmt: str = "py", kind: str = "individual"
) -> int:
    """fmt: 'py' (starter.py) or 'notebook' (starter.ipynb). It picks the starter stub and
    nothing else - the grader never reads it, converting whatever `.ipynb` a submission
    holds, so a student who works in a notebook on a `py` assignment still grades.
    kind: 'individual' (one repo per student) or 'group' (one repo per team, graded
    per team from classroom-config/teams.csv). This one DOES land verbatim in the solution
    branch's grading_config.yml, so the scaffold and the grader can never disagree."""
    repo = f"assignment-{number}-{tag}"
    log_step(f"Scaffolding {org}/{repo} ({kind}, {fmt}; template + solution branch)")
    if not create_repo(
        org,
        repo,
        private=True,
        is_template=True,
        description=f"Assignment {number} template",
    ):
        return 1
    grant_faculty(org, repo, COURSE_TEAM_ACCESS)
    grant_tagged_team_access(org, repo, tag)
    starter_name = "starter.ipynb" if fmt == "notebook" else "starter.py"
    brief = "group assignment" if kind == "group" else "assignment"
    # main: starter only (what students receive on generate). No tests, no autograder -
    # grading runs faculty-side from the solution branch. Create-only:
    # a re-run against a repo whose starter faculty have since authored must not revert it.
    # Count a failed create-only seed (not a skip of a live file) so a half-written starter
    # reds the scaffold, matching scaffold_materials rather than reporting a green "ready".
    seed_failures = 0
    if not seed_if_absent(
        org,
        repo,
        "README.md",
        # A STUB, deliberately: this is the page students read, and only faculty can
        # write it. Seeding a plausible-looking brief invites shipping it unedited, so
        # the placeholder is unmistakably one.
        f"# Assignment {number}\n\n_Write the {brief} instructions here._\n".encode(),
        "init: assignment starter",
    ):
        seed_failures += 1
    starter_code = "def solve():\n    raise NotImplementedError  # TODO"
    starter = (
        _notebook([f"# Assignment {number}"], starter_code)
        if fmt == "notebook"
        else f'"""Assignment {number}."""\n\n\n{starter_code}\n'
    )
    if not seed_if_absent(org, repo, starter_name, starter.encode(), "init: starter"):
        seed_failures += 1
    set_repo_topics(org, repo, [f"assignment-{number}", "assignment"])

    # solution branch: the model solution, grading_config.yml, and the HIDDEN tests -
    # all kept OFF main so generate never copies them into student repos.
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "r"
        if not clone(org, repo, wd):
            log_err("  ! could not clone to add the solution branch")
            return 1
        # A solution branch left by a prior run holds a real model solution and hidden
        # tests; overwriting it would destroy faculty work. Probe the REMOTE, because in
        # this fresh clone no local `solution` exists yet - `checkout -b` would happily
        # succeed and the run would only fail much later, at the push, with a misleading
        # error. ls-remote exits 0 when the branch exists, 2 when it does not. Probe the
        # FULL ref: a bare `solution` pattern tail-matches, so an unrelated
        # `feature/solution` branch would exit 0 and wrongly refuse the scaffold.
        if (
            git(
                "-C",
                str(wd),
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                "refs/heads/solution",
            )[0]
            == 0
        ):
            log_err(
                "  ! solution branch already exists - re-run refuses to overwrite it "
                f"(delete {org}/{repo}'s solution branch first if you really want it rebuilt)"
            )
            return 1
        if git("-C", str(wd), *GIT_ENV, "checkout", "-q", "-b", "solution")[0] != 0:
            # Any other local failure here must not be swallowed and then misreported as
            # a push failure below.
            log_err("  ! could not create the solution branch")
            return 1
        sol = wd / "solution"
        sol.mkdir()
        solution_code = "def solve():\n    return 42  # TODO"
        if fmt == "notebook":
            (sol / "solution.ipynb").write_text(
                _notebook(
                    [f"# Assignment {number} - model solution (stub)"], solution_code
                )
            )
        else:
            (sol / "solution.py").write_text(
                f'"""Model solution for assignment {number} (stub)."""\n\n\n{solution_code}\n'
            )
        (sol / "README.md").write_text(
            f"# Assignment {number} - model solution\n\n"
            "Goes out to students after the deadline, two ways:\n\n"
            "- **On a clock** - set `solution_datetime:` on this assignment in the "
            "cohort's `classroom-config/schedule.yml`, beside its `due_datetime`. The "
            "hourly cron pushes this folder into every student/team repo at that "
            "moment. Needs `handout_datetime:` set too - the schedule can only push a "
            "solution into repos it provisioned. There is no default: leave it out and "
            "the solution never ships automatically.\n"
            "- **By hand** - run **Release assignment** with **include_solution** "
            "ticked.\n\n"
            "Both do the same thing, idempotently, so a scheduled release you then "
            "re-run by hand changes nothing.\n"
        )
        # grading_config.yml + hidden tests for the faculty-side autograder. The
        # type chosen at scaffold time is recorded here - edit this file to change it
        # later. `fmt` is NOT: the grader converts whatever notebooks a submission holds,
        # so it only picks which starter and hidden-test stub are written below.
        (wd / "grading_config.yml").write_text(_GRADING_YML.format(kind=kind))
        tests = wd / "tests"
        tests.mkdir()
        (tests / "test_solution.py").write_text(
            _HIDDEN_TEST_NOTEBOOK if fmt == "notebook" else _HIDDEN_TEST_PY
        )
        git("-C", str(wd), *GIT_ENV, "add", "-A")
        git(
            "-C",
            str(wd),
            *GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            f"solution: assignment {number} (model + grading_config.yml + hidden tests)",
        )
        if (
            git("-C", str(wd), *GIT_ENV, "push", "-q", "-u", "origin", "solution")[0]
            != 0
        ):
            log_err("  ! could not push the solution branch")
            return 1
    if seed_failures:
        log_err(
            f"  ! {seed_failures} starter file(s) could not be written - the assignment "
            f"template is incomplete"
        )
        return 1
    log_ok(f"assignment template ready: {org}/{repo} (main + solution)")
    return 0


def _latest_deploy_run_id(org: str, site: str) -> str | None:
    """Newest deploy.yml run id for the site repo, or None if there are none yet."""
    code, out = gh(
        "api",
        f"repos/{org}/{site}/actions/workflows/deploy.yml/runs",
        "--jq",
        ".workflow_runs[0].id // empty",
    )
    return out.strip() if code == 0 and out.strip() else None


def _await_run(org: str, site: str, run_id: str, timeout: int = 180) -> str | None:
    """Poll a workflow run to completion; return its conclusion (e.g. 'success',
    'failure') or None on timeout."""
    waited = 0
    while waited < timeout:
        code, out = gh(
            "api",
            f"repos/{org}/{site}/actions/runs/{run_id}",
            "--jq",
            ".status,.conclusion",
        )
        if code == 0:
            parts = out.split()
            if parts and parts[0] == "completed":
                return parts[1] if len(parts) > 1 else ""
        time.sleep(6)
        waited += 6
    return None


def _dispatch_deploy(org: str, site: str) -> str | None:
    """Dispatch deploy.yml and return the id of the run it triggers, or None. The
    workflow takes a few seconds to index after it is seeded, so retry the
    dispatch; then wait for a new run (distinct from any prior one) to appear."""
    before = _latest_deploy_run_id(org, site)
    for _ in range(6):
        if gh("workflow", "run", "deploy.yml", "--repo", f"{org}/{site}")[0] == 0:
            break
        time.sleep(5)
    else:
        return None
    for _ in range(10):
        rid = _latest_deploy_run_id(org, site)
        if rid and rid != before:
            return rid
        time.sleep(3)
    return None


def scaffold_site(org: str) -> int:
    """Create an org's public website repo, seed its Pages build and enable GitHub Pages.
    Used for both the per-cohort student-facing site and the opt-in public course site -
    the org is whatever's passed.

    The repo is created EMPTY and the first `site sync` writes the site into it: the whole
    of a cohort site ships from `templates/site/` and `templates/site-seed/` in this repo,
    so there is no template repo to fall behind them.

    The repo is named `<org>.github.io` so it serves at the org root. It must be PUBLIC
    on the Free plan (Pages requires it); on GitHub Enterprise Cloud / Campus it can be
    made private with Pages access control. The site redeploys on every push."""
    site = pages_repo(org)
    log_step(f"Scaffolding website {org}/{site}")
    if repo_exists(org, site):
        log_skip(f"repo {org}/{site}")
    elif not create_repo(
        org,
        site,
        private=False,
        # Generated and rewritten on every sync, which is what the reader needs to know
        # from the org's landing page.
        description="[do not touch]: Course website (auto-deployed)",
    ):
        return 1

    # The first commit, through the Contents API - the only one that will create it in a
    # repo that has none (see gh_contents._commit_tree). It has to be the deploy workflow:
    # the two calls below enable Pages on it and dispatch it.
    if not put_files(
        org,
        site,
        {
            SITE_DEPLOY_WORKFLOW: (
                TEMPLATES / "site" / SITE_DEPLOY_WORKFLOW
            ).read_bytes()
        },
        "site: seed the Pages build",
        create_only=True,
    ):
        log_err(f"  ! could not seed the Pages build into {org}/{site}")
        return 1

    # Enable Pages with the GitHub Actions ("workflow") build, so deploy.yml publishes the
    # site. Ignore "already enabled".
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{site}/pages",
        "-f",
        "build_type=workflow",
    )
    if code != 0 and not is_already_exists(out):
        # POST creates; PUT updates a site that already has a different build type. Its
        # return used to be dropped, so a repo where BOTH calls failed - no Pages at all -
        # went on to "site scaffolded -> https://...", a URL that has never served
        # anything. Nothing downstream re-enables Pages, so this is the only chance.
        code, out = gh(
            "api",
            "--method",
            "PUT",
            f"repos/{org}/{site}/pages",
            "-f",
            "build_type=workflow",
        )
        if code != 0:
            log_err(f"  ! could not enable Pages on {org}/{site}: {out[:200]}")
            return 1

    # The auto-created github-pages environment restricts which branches may deploy -
    # clear the policy so any branch (the default, plus sync-site's pushes)
    # can deploy. Not fatal: Pages IS on, the default branch usually deploys anyway, and
    # the environment can lag its repo - but a silent failure here is what makes a
    # sync-site push deploy nothing, so say it.
    code, out = gh(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{site}/environments/github-pages",
        "-F",
        "deployment_branch_policy=null",
    )
    if code != 0:
        log_err(
            f"  ! could not clear the github-pages branch policy on {org}/{site}: "
            f"{out[:160]} - pushes from a non-default branch will not deploy"
        )

    # Seeding a workflow through the API doesn't fire it, so kick the first deploy by hand AND
    # confirm it lands. Enabling Pages with build_type=workflow races the platform's
    # provisioning, so the first deploy often fails transiently ("Deployment failed, try
    # again later"); re-dispatch a couple of times, waiting for each run to finish. A
    # miss is non-fatal - the site deploys on the first content push (your first Release)
    # anyway - but confirming here avoids a freshly-bootstrapped org showing a dead site.
    for attempt in range(1, 4):
        run_id = _dispatch_deploy(org, site)
        if run_id is None:
            continue
        conclusion = _await_run(org, site, run_id)
        if conclusion == "success":
            log_ok(f"site deployed -> https://{site}/")
            return 0
        log(
            f"  (deploy attempt {attempt} did not succeed: {conclusion or 'timed out'})"
        )
        time.sleep(10)
    log(
        "  (site not deployed yet - it will deploy on the next push to the site repo, "
        "e.g. your first Release materials)"
    )
    log_ok(f"site scaffolded -> https://{site}/")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("materials")
    pm.add_argument("--org", required=True)
    pm.add_argument("--tag", required=True, help="Year tag, e.g. f2026 or s2026")
    pa = sub.add_parser("assignment")
    pa.add_argument("--org", required=True)
    pa.add_argument("--number", required=True)
    pa.add_argument("--tag", required=True, help="Year tag, e.g. f2026 or s2026")
    pa.add_argument(
        "--format",
        dest="fmt",
        choices=["py", "notebook"],
        default="py",
        help="Starter/solution format: a .py script or a Jupyter notebook",
    )
    pa.add_argument(
        "--type",
        dest="kind",
        choices=["individual", "group"],
        default="individual",
        help="individual = one repo per student; group = one repo per team (teams.csv)",
    )
    ps = sub.add_parser("site")
    ps.add_argument("--org", required=True)
    args = parser.parse_args()
    # scaffold_materials equips the new repo's Release workflows, which reads the cohort
    # registry + assignment list; a read helper that couldn't reach the API raises, and in
    # an Actions log a one-line error beats a traceback.
    try:
        if args.cmd == "materials":
            return scaffold_materials(args.org, args.tag)
        if args.cmd == "site":
            return scaffold_site(args.org)
        return scaffold_assignment(args.org, args.number, args.tag, args.fmt, args.kind)
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
