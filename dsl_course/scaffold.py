"""dsl-course scaffold -- create correctly-structured course-materials / assignment repos.

Replaces the old "use this template" repo: the required structure is defined here in
code, so a new repo is always laid out the way the Release actions expect.

    scaffold materials   --org X --tag f2026                 -> course-materials-f2026
    scaffold assignment  --org X --number 1 --tag f2026      -> assignment-1-f2026

Materials repos get `lectures/`, `readings/` and `labs/` `01_session-1/` skeletons (any
top-level directory with an ordinal-prefixed subdirectory is a releasable section - add
more, e.g. `datasets/`, freely; delete `labs/` if unused) and the run-from-repo Release
buttons. Assignment repos get a starter on `main` (no tests - grading is faculty-side)
and a `solution` branch carrying the model solution, `grading.yml`, and the HIDDEN
tests, so generate never ships any of them to students.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from . import seed
from .utils import (
    GIT_ENV,
    create_repo,
    generate_from_template,
    gh,
    git,
    grant_course_team_access,
    grant_tagged_team_access,
    log,
    log_err,
    log_ok,
    log_skip,
    log_step,
    put_file,
    repo_exists,
    seed_files_if_absent,
    seed_if_absent,
    set_repo_topics,
)

WEBSITE_TEMPLATE_ORG = "hertie-data-science-lab"
WEBSITE_TEMPLATE = "course-website-template"

_GIT_ENV = GIT_ENV

_GRADING_YML = """\
# How the Grade assignment button autogrades this assignment (after the grading_deadline in schedule.yml).
# Delete this file (or set autograde: false) for a purely manually-graded one.
type: {kind}      # individual (one repo per student) or group (one repo per team)
format: {fmt}      # py or notebook
autograde: true       # false -> skip autograding (all-manual)
max_auto: 0           # points the hidden tests are worth (0 = informational)
tests: tests          # path (on THIS solution branch) holding the hidden tests
"""

_HIDDEN_TEST_PY = """\
# HIDDEN tests - run faculty-side by the Grade assignment button, never shipped to students.
# They import the student's submission (the repo root) and check it.
# Replace this placeholder with the real grading tests.
from starter import solve


def test_solve_runs():
    assert solve() is not None
"""

_HIDDEN_TEST_NOTEBOOK = """\
# HIDDEN tests - run faculty-side by the Grade assignment workflow, never shipped to students.
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


def scaffold_materials(org: str, tag: str) -> int:
    repo = f"course-materials-{tag}"
    log_step(f"Scaffolding {org}/{repo}")
    if not create_repo(
        org,
        repo,
        private=True,
        description="Course materials (lectures/readings by session)",
    ):
        return 1
    grant_course_team_access(org, repo)
    grant_tagged_team_access(org, repo, tag)
    actions_url = f"https://github.com/{org}/.github/actions"
    # The course org's `.github` Actions tab hosts the buttons that operate this course.
    # Both README (faculty & instructors orientation, pre-release) and MAINTAINING link it.
    actions_table = (
        f"The course org's [`.github` Actions tab]({actions_url}) hosts the buttons that "
        "operate this course:\n\n"
        "| Action | What it does |\n"
        "| --- | --- |\n"
        "| **Release materials** | Copy session folders (+ optional syllabus/README) into a "
        "cohort's `materials` repo. |\n"
        "| **Release assignment** | Freeze an assignment template, then generate one private "
        "repo per student. |\n"
        "| **New materials repo** | Scaffold another structured materials repo. |\n"
        "| **New assignment** | Scaffold an assignment template (starter + hidden autograder). |\n"
        "| **Refresh actions** | Re-seed the run-from-repo buttons and repopulate dropdowns "
        "after you add sessions/sections. |\n"
        "| **Check cohort setup** | Read-only per-cohort checklist of what's configured. |\n\n"
        "(**Release materials** and **Release assignment** also appear in this repo's own "
        "Actions tab.)\n"
    )
    # README.md is student-facing: Release materials with the README toggle copies THIS
    # file into the cohort's materials repo, where enrolled students read it. So it ships
    # as a replace-me placeholder written for students - the how-this-repo-works reference
    # for faculty & instructors lives in MAINTAINING.md (a root file that is never released:
    # release only copies section folders, the syllabus, and README.md).
    readme = (
        "<!-- FACULTY & INSTRUCTORS: replace the content below with a real, student-facing\n"
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
        "## For faculty & instructors (delete this section before releasing the README)\n\n"
        "- **How to populate & operate this repo:** see [`MAINTAINING.md`](MAINTAINING.md) - "
        "it explains what to edit, what gets released to students, and what to leave alone. "
        "`MAINTAINING.md` is **not** deployed to the cohort org; leave it here as a persistent "
        "reference.\n"
        "- **Available actions:** " + actions_table
    )
    maintaining = (
        f"# Maintaining `{repo}` (faculty & instructors)\n\n"
        "Reference for faculty & instructors on how to populate and operate this materials "
        "**source** repo. This file is **not** released to students - Release materials only "
        "copies session folders, the syllabus, and (when toggled) `README.md`. Keep "
        "student-facing wording in `README.md` and operational notes here.\n\n"
        "## What to edit vs leave alone\n\n"
        "| You edit / add | Visible to students? | Notes |\n"
        "| --- | --- | --- |\n"
        "| `lectures/`, `readings/` (and any section folders) session content | Yes, when you "
        "Release that session | The released files are copied into the cohort `materials` repo. |\n"
        "| `SYLLABUS.md`, `README.md` (root) | Only if you toggle them on at release | Write "
        "`README.md` for students; it replaces the placeholder. |\n"
        "| `MAINTAINING.md` (this file) | No | Your reference; never released. Leave it in the "
        "repo. |\n"
        "| `.github/workflows/` (the Release buttons) | No | **Infrastructure - do not edit or "
        "delete.** These run-from-repo buttons are what make releasing work; **Refresh actions** "
        "re-seeds them. |\n\n"
        "Rule of thumb: edit the content folders and the two root files; leave `MAINTAINING.md` "
        "and `.github/workflows/` alone.\n\n"
        "## Structure\n\n"
        "Any top-level directory containing at least one ordinal-prefixed subdirectory "
        "(`01_`, `02_`, `03_`, ...) is a releasable section - no config to declare it:\n\n"
        "- `lectures/01_session-1/` - one folder per session's lecture files\n"
        "- `readings/01_session-1/` - one folder per session's readings. A text file here "
        "(`reading.md`, `.txt`, `.bib`) IS the reading list shown on the cohort site; "
        "other files in it are linked, not published\n"
        "- `labs/01_session-1/` - one folder per session's lab (delete the `labs/` folder "
        "if your course has none)\n"
        "- `*syllabus*`, `README.md` (root) - released via the syllabus / README toggles\n\n"
        "Add more sessions by creating `lectures/02_session-2/`, `readings/02_session-2/`, ... "
        "(only the ordinal prefix matters - name the rest whatever you like), or add a whole "
        "new section (e.g. `datasets/01_intro/`) - then run **Refresh actions** so the session "
        "dropdown and Release button's section toggles pick it up.\n\n"
        "## Available actions\n\n" + actions_table + "\n"
        "## Public course website (optional)\n\n"
        "The **Publish course website** action can share this repo's materials on a public "
        "open-courseware site. Lecture files are always hosted; for readings you choose "
        "`reading-list` (text/citation files are shown as a list - keep copyrighted PDFs out "
        "of the list by leaving them as non-text files) or `actual-readings` (every reading "
        "file is hosted and downloadable - you carry the copyright responsibility).\n"
    )
    failures = 0
    # MAINTAINING.md is SYSTEM-owned generated docs, built from the actions table above (like
    # classroom-config's README contract): it must refresh on a re-run when the toolkit
    # changes it, so it's written unconditionally with put_file - never frozen create-only. A
    # failed write reds the scaffold rather than shipping a stale/absent maintainer guide.
    if not put_file(
        org, repo, "MAINTAINING.md", maintaining.encode(), "docs: maintaining guide"
    ):
        failures += 1
    # USER-owned skeletons: create-only, so a re-run against a repo faculty have since
    # authored must not revert their README/SYLLABUS to the stub or resurrect a deleted
    # starter directory. A failed seed (an absent file whose write failed) reds the scaffold.
    user_files = {
        "README.md": readme.encode(),
        "lectures/01_session-1/.gitkeep": b"",
        # A stub, not a .gitkeep: a text file here IS the published reading list (its
        # contents are inlined on the site's Materials tab), and an empty folder gave no
        # sign of that - the tab then reads blank with nothing to explain why.
        "readings/01_session-1/reading.md": (
            b"# Session 1 readings\n\n"
            b"- Author, *Title*, ch. 1.\n"
            b'- Author (2026), "Paper title", doi:...\n\n'
            b"This file is the reading list students see. Put reading PDFs in this folder "
            b"too - they are linked, never published.\n"
        ),
        "labs/01_session-1/.gitkeep": b"",
        "SYLLABUS.md": f"# {tag} syllabus\n\nReplace with the real syllabus.\n".encode(),
    }
    # One commit for the skeleton: all five carried the same subject anyway, so writing
    # them one at a time opened a repo faculty then author by hand with five identical
    # `init: materials skeleton` lines.
    if not seed_files_if_absent(org, repo, user_files, "init: materials skeleton"):
        failures += 1
    # Equip the run-from-repo Release buttons (same as Refresh does for content repos).
    # _push_workflows lands both in one commit, logs its own failure, and returns 1 - a
    # materials repo with no Release buttons must not report success.
    cohorts = seed.discover_cohorts(org)
    failures += seed._push_workflows(org, repo, cohorts, seed.discover_assignments(org))
    if failures:
        return 1
    log_ok(f"materials repo ready: {org}/{repo}")
    return 0


def scaffold_assignment(
    org: str, number: str, tag: str, fmt: str = "py", kind: str = "individual"
) -> int:
    """fmt: 'py' (starter.py) or 'notebook' (starter.ipynb, nbconvert'd at grading time);
    kind: 'individual' (one repo per student) or 'group' (one repo per team, graded
    per team from classroom-config/teams.csv). Both land verbatim in the solution
    branch's grading.yml, so the scaffold and the grader can never disagree."""
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
    grant_course_team_access(org, repo)
    grant_tagged_team_access(org, repo, tag)
    starter_name = "starter.ipynb" if fmt == "notebook" else "starter.py"
    submission = (
        "any team member's push to `main` counts as the team's submission"
        if kind == "group"
        else "that push is your submission"
    )
    # main: starter only (what students receive on generate). No tests, no autograder -
    # grading runs faculty-side from the solution branch (see Grade assignment). Create-only:
    # a re-run against a repo whose starter faculty have since authored must not revert it.
    # Count a failed create-only seed (not a skip of a live file) so a half-written starter
    # reds the scaffold, matching scaffold_materials rather than reporting a green "ready".
    seed_failures = 0
    if not seed_if_absent(
        org,
        repo,
        "README.md",
        f"# Assignment {number}\n\nComplete the TODOs in `{starter_name}` and push to "
        f"`main` ({submission}).\n".encode(),
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

    # solution branch: the model solution, grading.yml, and the HIDDEN tests - all kept OFF
    # main so generate never copies them into student repos.
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "r"
        if gh("repo", "clone", f"{org}/{repo}", str(wd), "--", "-q")[0] != 0:
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
        if git("-C", str(wd), *_GIT_ENV, "checkout", "-q", "-b", "solution")[0] != 0:
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
            "Released to students after the deadline via Release assignment with "
            "**include_solution** ticked.\n"
        )
        # grading.yml + hidden tests for the faculty-side Grade assignment button. The
        # type/format chosen at scaffold time are recorded here - edit this file to
        # change them later.
        (wd / "grading.yml").write_text(_GRADING_YML.format(kind=kind, fmt=fmt))
        tests = wd / "tests"
        tests.mkdir()
        (tests / "test_solution.py").write_text(
            _HIDDEN_TEST_NOTEBOOK if fmt == "notebook" else _HIDDEN_TEST_PY
        )
        git("-C", str(wd), *_GIT_ENV, "add", "-A")
        git(
            "-C",
            str(wd),
            *_GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            f"solution: assignment {number} (model + grading.yml + hidden tests)",
        )
        if (
            git("-C", str(wd), *_GIT_ENV, "push", "-q", "-u", "origin", "solution")[0]
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
    workflow takes a few seconds to index after template-generate, so retry the
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
    """Generate an org's public website (from course-website-template) and enable GitHub
    Pages with the template's deploy-on-push workflow. Used for both the per-cohort
    student-facing site and the opt-in public course site - the org is whatever's passed.

    The repo is named `<org>.github.io` so it serves at the org root. It must be PUBLIC
    on the Free plan (Pages requires it); on GitHub Enterprise Cloud / Campus it can be
    made private with Pages access control. The site redeploys on every push."""
    site = f"{org.lower()}.github.io"
    log_step(f"Scaffolding website {org}/{site}")
    if repo_exists(org, site):
        log_skip(f"repo {org}/{site}")
    elif not generate_from_template(
        template_org=WEBSITE_TEMPLATE_ORG,
        template_name=WEBSITE_TEMPLATE,
        owner=org,
        name=site,
        private=False,
        description="Course website (auto-deployed on push)",
    ):
        log_err(
            f"  ! could not generate the site from {WEBSITE_TEMPLATE_ORG}/{WEBSITE_TEMPLATE}"
        )
        return 1

    # Enable Pages with the GitHub Actions ("workflow") build, so the template's
    # deploy.yml publishes the site. Ignore "already enabled".
    code, out = gh(
        "api",
        "--method",
        "POST",
        f"repos/{org}/{site}/pages",
        "-f",
        "build_type=workflow",
    )
    if code != 0 and "409" not in out and "already" not in out.lower():
        gh(
            "api",
            "--method",
            "PUT",
            f"repos/{org}/{site}/pages",
            "-f",
            "build_type=workflow",
        )

    # The auto-created github-pages environment restricts which branches may deploy -
    # clear the policy so any branch (the template's default, plus sync-site's pushes)
    # can deploy.
    gh(
        "api",
        "--method",
        "PUT",
        f"repos/{org}/{site}/environments/github-pages",
        "-F",
        "deployment_branch_policy=null",
    )

    # template-generate doesn't fire workflows, so kick the first deploy by hand AND
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
            log_ok(f"site deployed -> https://{org.lower()}.github.io/")
            return 0
        log(
            f"  (deploy attempt {attempt} did not succeed: {conclusion or 'timed out'})"
        )
        time.sleep(10)
    log(
        "  (site not deployed yet - it will deploy on the next push to the site repo, "
        "e.g. your first Release materials)"
    )
    log_ok(f"site scaffolded -> https://{org.lower()}.github.io/")
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
    # scaffold_materials equips the new repo's Release buttons, which reads the cohort
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
