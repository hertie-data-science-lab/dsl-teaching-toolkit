"""dsl-course assign -- provision per-student assignment repos from a template repo.

Generates ONE private repo per onboarded ENROLLED student from an assignment TEMPLATE repo
(e.g. assignment-1-f2026) in the course org, using GitHub's native template-generate,
then adds the student as a collaborator (maintain). The template carries its own
starter code + autograder workflow, which every generated repo inherits. Students
never use a CLI. Roster rows with `role=auditor` are skipped - auditors are read-only.
Idempotent: existing repos are left alone.

    course/<template>  (private, is_template)
            |  generate (native)
            v
    cohort/<slug>-<handle>   (private; student = collaborator)
    where <slug> is the template name minus a trailing -fYYYY / -sYYYY.

For a `type: group` assignment it instead makes ONE repo per team, `cohort/<slug>-<team>`, and grants the
GitHub Team materialised from classroom-config/teams.csv (see dsl_course.sync_teams) - so
membership changes propagate to access. Grades are never written here; they go to each
student's private gradebook repo (see dsl_course.grades), so a possibly-public team repo
never carries marks.

For `submit_via: external` - handed in off GitHub (Moodle, Kaggle, in class) - it creates
NOTHING: no cohort template, no repo, no Feedback issue, no solution push. It still records
the handout and writes the grading sheet, the gradebooks and the site, which is everything
a handout owes the cohort around the work itself.

For `submit_via: shared_dropbox_repo` it freezes the cohort template as usual - the
brief lives there - and then creates ONE private repo, `<slug>-submissions`, with every onboarded
student (or every vetted team) on `push`. Each unit works in its own `<unit>/` folder and
can read everyone else's; a ruleset asks to stop the one repo being force-pushed or deleted,
though GitHub Free (every Hertie org, until the Education upgrade) refuses rulesets on a
private repo, so until then it is left unprotected and the run log says so. No Feedback
issue and no model solution: one repo the whole cohort reads is not a place to put either.

For `visibility: public` it creates the same repos world-readable - portfolio work - and
opens no Feedback issue: nothing about a student's marking may be written where the
internet can read it, so their feedback goes to their private gradebook alone. The cohort
template stays private either way.

For `visibility: student_choice` it creates the same PRIVATE repos and makes each unit
`admin` of its own - the one permission that carries GitHub's visibility switch - so the
student, or every member of a team, can publish their own work once it has been marked.
No Feedback issue there either: the repo may be public tomorrow. Until the grading cutoff
the scheduler puts any of them back that has gone public early.

Usage:
    python3 -m dsl_course.assign \\
        --master-org TEST-HERTIE-COURSE --course-source-repo assignment-1-f2026 \\
        --cohort-org TEST-HERTIE-COHORT-f2026
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from . import grades, roster, schedule, site, sync_teams, teams
from .access import (
    FACULTY_READ_ACCESS,
    grant_faculty,
    grant_team_repo_access,
    repo_teams,
)
from .collect import (
    load_grading_spec,
    sheet_spec,
    sync_sheet,
)
from .course import (
    ASSIGNED,
    CONFIG_REPO,
    SOLUTION_BRANCH,
    SOLUTION_DIR,
    github_visibility,
    shape_note,
    shared_repo,
    submission_repo,
    submit_shape,
    visibility_is_students,
)
from .discovery import (
    ASSIGNMENT_TEMPLATE_TOPIC,
    classify_repos,
    exists_in,
    list_org_repos,
    listing_by_name,
    listing_row,
)
from .fs import copy_tree
from .gh_contents import (
    blob_sha,
    file_exists,
    get_blob,
    get_file_content,
    put_file,
    put_files,
    repo_blob_shas,
    repo_tree,
)
from .ghcli import GIT_ENV, bot_login, clone, gh, git
from .log import (
    log,
    log_err,
    log_err_person,
    log_ok,
    log_person,
    log_skip,
    log_step,
)
from .releaseignore import RELEASEIGNORE, deny_for, excluded_in_tree
from .repos import (
    add_collaborator,
    default_branch,
    direct_collaborators,
    generate_from_template,
    listed_is_private,
    protect_shared_repo,
    remove_collaborator,
    set_repo_topics,
    set_visibility,
    topic_name,
)
from .workflows_place import NEVER_IN_STUDENT_REPOS

# Fire-once sentinel for the SCHEDULED solution push, in classroom-config. Needed because
# `due_releases` is cumulative by design - a handout release re-fires every tick so a late
# onboarder still gets their repo - and while re-probing a repo is cheap, push_solution
# CLONES every student repo. Without this marker a passed `solution_datetime` means a clone
# per student per hour for the rest of the term.
#
# A marker rather than a time window (`solution_datetime <= now < +1h`): a missed tick -
# an outage, a queued runner, a rate limit - would silently mean the solution never ships
# at all, and nothing would ever notice. Deleting the file re-releases it.
SOLUTION_RECORD_DIR = "solutions"


def _wait_for_content(
    org: str, repo: str, attempts: int = 12, delay: float = 1.5
) -> bool:
    """Poll until a freshly template-generated repo has content.

    GitHub's template-generate is asynchronous: a just-created repo can briefly be empty,
    and using it as a generate *source* (the next stage) then fails with `... is empty`.
    Returns True once the repo's root has files."""
    for attempt in range(attempts):
        code, out = gh("api", f"repos/{org}/{repo}/contents", "--jq", "length")
        if code == 0 and out.strip().isdigit() and int(out.strip()) > 0:
            return True
        # The delay SPACES the polls; after the last one there is nothing to space.
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


def _template_is_ready(entry: dict | None, slug: str) -> bool:
    """Whether an existing cohort template already carries everything the repair in
    `ensure_cohort_template` would write.

    `entry` is that repo's row in the caller's listing (None when there is no listing, or
    no such repo). The topics are the LAST thing the repair writes and the flag the
    second, so a repo carrying both has been through a complete repair - which is also why
    this needs no content check of its own: `_wait_for_content` gates both writes."""
    if entry is None:
        return False
    wanted = {topic_name(slug), ASSIGNMENT_TEMPLATE_TOPIC}
    return bool(entry.get("isTemplate")) and wanted <= set(entry.get("topics") or [])


def _about(what: str, shape: str) -> str:
    """A submission repo's About line: what it is, plus whatever this shape owes the
    student who opens it (`course.SHAPE_NOTES` - the same sentence the assignment's page
    prints under the brief).

    BOTH places, because they are read at different moments and only one of them is the
    repo: a student who clones from a link, or comes back to the repo in week nine, never
    reopens the page - and "this repo is public" is a fact they need where the commits go.

    Set at CREATION and never patched afterwards, like every other description the toolkit
    writes (`repos.generate_from_template`); a reworded one reaches existing repos through
    `repos.converge_descriptions` or not at all. Which is also why the notes are capped:
    GitHub TRUNCATES a description past `course.MAX_REPO_DESCRIPTION` rather than refusing
    it, so a note that outgrew the cap would lose its second half and say nothing about
    it."""
    note = shape_note(shape)
    return f"{what}. {note}" if note else what


def _tag_submission(cohort_org: str, repo: str, slug: str, have: set[str]) -> None:
    """Stamp `submission` + the assignment's own name on one submission repo. Checked.

    Called on the ALREADY-EXISTS path too, because the stamp is a separate PUT after the
    create and one that failed used to stand until `access.converge_topics` came round on
    the nightly refresh, up to a day later. `have` is the repo's topics off the caller's
    listing, so a repo already carrying them costs no call at all, and whatever else it
    carries is written back with them (the PUT replaces the whole list).

    A failed PUT is said out loud rather than dropped, because the topics are what the
    release targets and the faculty-access floor read. It is not a leak, though:
    `discovery.is_student_repo` recognises a submission repo by NAME as well as by topic,
    exactly so neither list depends on one PATCH having landed. A backstop is not a reason
    to leave the record wrong - it is the reason the line below does not cry fire.
    """
    wanted = {topic_name(slug), "submission"}
    if wanted <= have:
        return
    if not set_repo_topics(cohort_org, repo, sorted(have | wanted), person=True):
        log_err(
            f"  ! a submission repo in {cohort_org} carries no `submission` topic. The "
            f"name rule in `discovery.is_student_repo` still keeps it off the public org "
            f"landing page, so no handle is published - but the record stays wrong until "
            f"the stamp lands. The next tick with a repo listing retries it, as does the "
            f"nightly refresh."
        )


def withhold_from_template(cohort_org: str, template: str) -> bool:
    """Delete what a student repo must not carry: whatever the template's own
    `.releaseignore` withholds, and this toolkit's own release buttons. True on success.

    The one outbound copy with no clone to filter: `generate_from_template` is a server-side
    GitHub copy of the whole default branch, so there is no `copytree` and no ignore hook to
    hang a filter on. It happens here instead - after the cohort template has populated and
    BEFORE it is marked `is_template`, which is the moment student repos start coming off
    it. The `.releaseignore` files go with it; they withhold themselves.

    Reads the COHORT copy rather than the course template. Same tree, but filtering what is
    actually there makes a re-run self-healing: a template that predates this, or one whose
    earlier filter half-failed, is cleaned by the next handout.

    Fails CLOSED - an unreadable tree returns False and stops the handout - because the
    thing being withheld is the kind of thing that must not reach students by accident, and
    "could not tell" is not "nothing to withhold". A handout blocked by a transient API
    failure is re-run by the next hourly tick or the next click; answers published to a
    cohort cannot be taken back.

    `NEVER_IN_STUDENT_REPOS` goes with no pattern needed and no way to opt back in - the
    rule `.releaseignore` already applies to ITSELF (`releaseignore._SELF_EXCLUDED`), for
    the same reason. A course-org assignment template hosts **Release assignment** so
    faculty can hand it out from the repo they are editing, and template-generate copies
    the whole default branch - so without this the button, and the org-admin token it
    reads, would land in every student repo. Matched by exact PATH, so the autograder
    workflow beside it - and anything else under `.github/workflows/` - is untouched."""
    branch = default_branch(cohort_org, template, fallback="main")
    try:
        # Blobs only: `put_files` can delete nothing else, and `from_tree` derives the
        # directories it needs from the paths themselves.
        paths = repo_tree(cohort_org, template, branch, kind="blob")
        # `repo_tree` answers () for a 404 rather than raising, and `default_branch` will
        # have GUESSED `main` if the repo could not be read - so an empty tree here is not
        # "nothing to withhold". `_wait_for_content` has just confirmed this repo HAS
        # content, which makes an empty tree a contradiction, and the safe reading of a
        # contradiction is "could not tell".
        if not paths:
            raise RuntimeError(
                f"{branch} came back empty, though the repo has content - the branch name "
                f"may be a guess"
            )
        # Inside the guard: `get_file_content` raises on any non-404 failure, and an
        # unhandled raise here escapes through provision_all into the hourly scheduler and
        # takes every later cohort's release with it.
        withheld = excluded_in_tree(
            paths, lambda p: get_file_content(cohort_org, template, p)
        )
    except RuntimeError as exc:
        log_err(
            f"  ! could not read {cohort_org}/{template}, so a `{RELEASEIGNORE}` in it "
            f"could not be applied - handout stopped rather than risk shipping what it "
            f"withholds. Re-run it: {exc}"
        )
        return False
    # Whatever the file names, plus the buttons that are never a student's to press.
    # Sorted, so the delete list is stable however the two sets overlap.
    withheld = tuple(sorted(set(withheld) | (set(paths) & set(NEVER_IN_STUDENT_REPOS))))
    if not withheld:
        return True
    if not put_files(
        cohort_org,
        template,
        {},
        f"chore: withhold {len(withheld)} path(s) from the student repos",
        delete=withheld,
    ):
        log_err(
            f"  ! could not withhold {len(withheld)} path(s) - what a `{RELEASEIGNORE}` "
            f"names in {cohort_org}/{template}, or a faculty release button - handout "
            f"stopped"
        )
        return False
    log_ok(f"withheld {len(withheld)} path(s) from {cohort_org}/{template}")
    return True


def ensure_cohort_template(
    master_org: str,
    template: str,
    cohort_org: str,
    slug: str,
    listing: dict[str, dict] | None = None,
) -> str | None:
    """Stage 1: freeze a cohort-level template repo (named `<slug>`) from the course
    template, so the cohort has its own copy and per-student repos generate from it
    (the role Classroom 50's classroom template used to play). Returns the cohort
    template name, or None on failure. Idempotent.

    `listing` is the cohort's repos keyed by name, off the ONE listing its caller holds
    (`discovery.listing_by_name`); None means there is none, and this probes for itself."""
    entry = listing.get(slug) if listing is not None else None
    exists = exists_in(listing, cohort_org, slug)
    if exists:
        log_skip(f"cohort template {cohort_org}/{slug}")
        if _template_is_ready(entry, slug):
            # Already frozen, flagged and topiced. The repair below is what HEALS a
            # half-created template, and it has to stay reachable - but running it
            # unconditionally meant a probe, a PATCH and a topics PUT per handed-out
            # assignment on every hourly tick, re-writing state the listing just showed us
            # is already correct.
            return slug
    elif not generate_from_template(
        template_org=master_org,
        template_name=template,
        owner=cohort_org,
        name=slug,
        private=True,
        description=f"{slug} - cohort assignment template",
    ):
        return None
    else:
        log_ok(f"created cohort template {cohort_org}/{slug}")
    # Reached on the create path AND by a pre-existing template the listing did not show as
    # ready - never only on the create path. A prior run that timed out in
    # `_wait_for_content` left the repo existing but with `is_template` never set; an
    # exists-short-circuit then returned the slug without re-checking, so every later handout
    # failed with a misleading "<slug> is not a template" error. Both steps are idempotent,
    # so a retry HEALS a half-created template rather than being wedged by it.
    if not _wait_for_content(cohort_org, slug):
        log_err(
            f"  ! cohort template {cohort_org}/{slug} did not populate in time "
            f"(template-generate is async) - re-run the release"
        )
        return None
    # Before `is_template`, which is the moment per-student repos start generating FROM
    # this: a path withheld after that point is already in somebody's repo.
    if not withhold_from_template(cohort_org, slug):
        return None
    code, out = gh(
        "api",
        "--method",
        "PATCH",
        f"repos/{cohort_org}/{slug}",
        "-F",
        "is_template=true",
    )
    if code != 0:
        log_err(
            f"  ! could not set is_template on {cohort_org}/{slug} - per-student repos "
            f"generate FROM it, so this must succeed: {out[:160]}"
        )
        return None
    # READ on the frozen hand-out, at the moment it is frozen. Every other repo a cohort
    # receives is granted where it is created; this one was not, and the only reason older
    # templates carry a grant at all is that the nightly floor
    # (`access.converge_faculty_access`) added it overnight - so an instructor who is not
    # an org owner could not open the handout they had just pressed the button for. READ,
    # like every other one: the template is frozen, and nothing is marked here.
    # `grant_faculty` goes through `repos.gh_settled`, which is what waits out a repo
    # generated seconds ago, and a cohort whose faculty teams do not exist yet is a note
    # rather than an error - the sweep repairs it.
    grant_faculty(cohort_org, slug, FACULTY_READ_ACCESS, missing_is_note=True)
    # The topic is not decoration: discovery.discover_handed_out_assignments reads it back
    # as the record that this assignment went out, and the site withholds the brief until
    # it does. So a failure here is said out loud with its consequence attached rather than
    # dropped - the hand-out itself succeeded, and failing it now would be worse.
    stamped = set_repo_topics(cohort_org, slug, [slug, ASSIGNMENT_TEMPLATE_TOPIC])
    if not stamped:
        log_err(
            f"  ! {cohort_org}/{slug} carries no `{ASSIGNMENT_TEMPLATE_TOPIC}` topic. That "
            f"topic is what the cohort site reads as the record that {slug} was handed "
            f"out, so its brief stays withheld there until the topic is set by hand (or "
            f"its `handout_datetime` passes)."
        )
    if listing is not None and slug not in listing:
        # Written back into the caller's rows, as the repair above leaves it: whatever
        # reads them after this - the units below, the next release of the same tick -
        # finds the template this run froze rather than freezing it a second time
        # (`discovery.listing_row`).
        listing[slug] = listing_row(cohort_org, slug) | {
            "isTemplate": True,
            "topics": [slug, ASSIGNMENT_TEMPLATE_TOPIC] if stamped else [],
        }
    return slug


def fetch_solution(master_org: str, template: str, dest: Path) -> Path | None:
    """Clone the template's `solution` branch and return its solution/ dir, or None.

    Solutions live on a non-default branch so native template-generate (default branch
    only) never copies them into student repos - they're pushed separately, on demand."""
    if not clone(master_org, template, dest, branch=SOLUTION_BRANCH):
        log_err(
            f"  ! no `{SOLUTION_BRANCH}` branch on {master_org}/{template} - "
            f"nothing to push (add the solution there first)"
        )
        return None
    sol = dest / SOLUTION_DIR
    if not sol.is_dir():
        # The branch exists but holds no `solution/` folder - the model answer was committed
        # at the branch root, or the folder was renamed. Silent before, which made the
        # caller's failure look like a missing branch.
        log_err(
            f"  ! {master_org}/{template}'s `{SOLUTION_BRANCH}` branch has no "
            f"`{SOLUTION_DIR}/` folder - nothing to push"
        )
        return None
    return sol


def push_solution(cohort_org: str, repo: str, sol_dir: Path) -> bool:
    """Push the solution/ folder into an existing student repo (idempotent overwrite).

    Withholds whatever the template's `.releaseignore` files exclude, anchored at the clone
    root - which is `sol_dir.parent`, by construction in `fetch_solution`. Going through
    `fs.copy_tree` rather than a bare `copytree` is also what stops a symlink inside a
    solution folder being followed out of it."""
    with tempfile.TemporaryDirectory() as work:
        wd = Path(work) / "r"
        if not clone(cohort_org, repo, wd):
            return False
        copy_tree(sol_dir, wd / SOLUTION_DIR, deny_for(sol_dir.parent))
        git("-C", str(wd), *GIT_ENV, "add", "-A")
        code, _ = git(
            "-C",
            str(wd),
            *GIT_ENV,
            "commit",
            "-q",
            "--no-verify",
            "-m",
            "add solution",
        )
        if code != 0:
            return True  # already present, nothing new
        return git("-C", str(wd), *GIT_ENV, "push", "-q", "origin", "HEAD")[0] == 0


# ------------------------------------------ patching an assignment that is already out

# One patch, one comment. The digest is over the paths and the CONTENT written, so a second
# press that changes nothing says nothing, and a genuinely different correction to the same
# path is a new note rather than a silent one. Same idea as the `dsl-receipt:` marker the
# grading receipts carry - see `grades.post_marked_comment`, which is what enforces it.
PATCH_MARKER = "<!-- dsl-patch:{digest} -->"

# What one submission repo came to. Counted into the summary line; per-repo detail goes
# through `log_person`, because the repo NAME is `<slug>-<handle>`.
PATCHED = "patched"
PATCH_UNCHANGED = "already up to date"
PATCH_KEPT = "the student's own edit kept"
PATCH_FAILED = "failed"


def corrected_digests(files: dict[str, bytes]) -> dict[str, str]:
    """`{path: blob sha}` for the correction - computed ONCE for the whole run.

    Every repo asks the same question of the same bytes ("is this already there? is it
    still what the hand-out gave them?"), and the answer is a sha of the correction, not
    of the repo. Hashing inside the loop re-read every corrected file per student."""
    return {path: blob_sha(body) for path, body in files.items()}


def patch_marker(digests: dict[str, str]) -> str:
    """The idempotence marker for one patch: a digest of what it writes."""
    fingerprint = "\n".join(f"{path}:{sha}" for path, sha in sorted(digests.items()))
    return PATCH_MARKER.format(digest=blob_sha(fingerprint.encode())[:12])


def patch_note(paths: list[str], on: date) -> str:
    """The line each Feedback issue gets. It names the FILES, never the student, and says
    the one thing a student has to do about it."""
    listed = ", ".join(f"`{path}`" for path in sorted(paths))
    what = (
        f"updated {listed}"
        if len(paths) == 1
        else f"updated {len(paths)} files - {listed} -"
    )
    return (
        f"The teaching team {what} in this repository on {on}; pull before you continue. "
        f"Your own commits are untouched."
    )


def template_files(course_org: str, template: str, path: str) -> dict[str, bytes]:
    """`{path: content}` for `path` on the TEMPLATE's default branch - one file, or every
    file under it when `path` names a folder.

    Read as BLOBS, off the same recursive tree that names the paths, and not through the
    Contents API. What this returns is hashed (`corrected_digests`), compared against every
    repo's tree, and committed into student repos, so bytes that are merely equivalent are
    not good enough: the Contents API inlines nothing over 1 MiB - it returns `content: ""`
    on a 200, so a plot-heavy notebook came back EMPTY and was written as an empty file
    into every untouched submission - and `gh()`'s `.strip()` eats a trailing newline, so
    the written blob never equalled the template's and a repo already holding the exact
    hand-out was "patched" for whitespace. Off the blobs API the digests here ARE the
    template's shas (`gh_contents.get_blob` verifies that before returning), which is what
    makes a re-run idempotent against the template rather than only against itself.

    Text in practice: `put_files` sends text, so a binary asset cannot travel this way even
    though it can now be read. A corrected notebook, script or brief can."""
    branch = default_branch(course_org, template, fallback="main")
    prefix = path.strip("/") + "/"
    files: dict[str, bytes] = {}
    for candidate, sha in sorted(repo_blob_shas(course_org, template, branch).items()):
        if candidate != path.strip("/") and not candidate.startswith(prefix):
            continue
        content = get_blob(course_org, template, sha)
        if content is None:
            log_err(f"  ! {template}/{candidate} could not be read - not patched")
            continue
        files[candidate] = content
    return files


def patch_targets(listing: list[dict], slug: str) -> list[str]:
    """Every LIVE submission repo generated from the cohort-side template `slug`.

    Off the org listing rather than the roster, deliberately: what has to be patched is
    what EXISTS. A student who has since left the course still holds their repo and still
    has the broken file in it, and a team repo whose members changed is one repo either
    way. Archived repos are skipped - they are read-only, so the write would 403, and a
    frozen repo is a finished one."""
    derived = classify_repos(listing)
    return sorted(
        row["name"]
        for row in listing
        if derived.get(row["name"]) == slug and not row.get("archived")
    )


def _to_patch(
    live: dict[str, str],
    corrected: dict[str, bytes],
    digests: dict[str, str],
    handout: dict[str, str],
    overwrite: bool,
) -> tuple[dict[str, bytes], list[str]]:
    """`(what to write, what the student changed and we are leaving alone)` for one repo.

    Three states per path, decided on blob shas off ONE tree read: already the corrected
    content (nothing to do), still exactly what the hand-out put there (safe to replace),
    or something else - which is the student's work on that file. The hand-out baseline is
    the cohort-side TEMPLATE every one of these repos was generated from, so "the student
    changed it" is a comparison against what they were actually given rather than a guess
    off commit authorship (the generate commit is the bot's, so authorship says nothing).
    """
    write: dict[str, bytes] = {}
    kept: list[str] = []
    for path, body in corrected.items():
        current = live.get(path)
        if current == digests[path]:
            continue
        if current is not None and current != handout.get(path) and not overwrite:
            kept.append(path)
            continue
        write[path] = body
    return write, kept


def patch_one_repo(
    cohort_org: str,
    repo: str,
    corrected: dict[str, bytes],
    digests: dict[str, str],
    handout: dict[str, str],
    overwrite: bool,
) -> tuple[str, list[str]]:
    """Commit the correction into one submission repo. Returns `(verdict, paths written)`
    - one of the PATCH_* verdicts, and exactly what this repo got.

    The paths are what the Feedback note then names: a patch of two files where the
    student had already rewritten one of them touched one file and used to tell them both
    had changed.

    A NEW COMMIT on the student's own default branch, through `put_files` - so it is one
    commit whatever the patch touches, it is never a force-push (the trees API can only add
    a commit on top of the ref it read), and a path already carrying the corrected bytes
    costs no commit at all."""
    try:
        live = repo_blob_shas(cohort_org, repo, default_branch(cohort_org, repo))
    except RuntimeError:
        log_err_person(
            "  ! a submission repo could not be read - not patched",
            f"{cohort_org}/{repo}",
        )
        return PATCH_FAILED, []
    write, kept = _to_patch(live, corrected, digests, handout, overwrite)
    if kept:
        log_person(
            f"    {cohort_org}/{repo}: keeping the student's own {', '.join(kept)}"
        )
    if not write:
        return (PATCH_KEPT if kept else PATCH_UNCHANGED), []
    if not put_files(
        cohort_org,
        repo,
        write,
        f"fix: the teaching team updated {', '.join(sorted(write))}",
        person=True,
    ):
        return PATCH_FAILED, []
    log_person(f"  [ok] patched {cohort_org}/{repo}")
    return PATCHED, sorted(write)


def note_the_patch(
    cohort_org: str,
    repo: str,
    paths: list[str],
    marker: str,
    on: date,
    row: dict | None,
) -> bool:
    """Tell one student, on the Feedback issue they were pointed at when the repo appeared.

    Only where that issue already EXISTS: `assign` opens it at hand-out with the assignment's
    due date and brief in the body, and a patch has neither to hand - opening one here would
    put a thread with the wrong body over the one the student is reading. A repo whose issue
    is missing is counted and named through `log_person`, not silently passed over.

    And only where the LISTING says the repo is still private. `row` is this repo's row of
    the listing `patch_released` already holds, and the same guard every other write into
    a Feedback thread takes (`grades.feedback_thread_policy`): a thread in a public repo
    is a thread the internet reads, and this one names the files a student was handed
    wrong. Optimistic like `repos.listed_is_private` - a row nobody could read answers
    private - because the cost of being wrong the other way is a note nobody gets."""
    if not listed_is_private(row):
        log_person(
            f"    {cohort_org}/{repo}: the listing says it is not private - the file is "
            f"patched, the note is not posted"
        )
        return False
    found = grades.find_feedback_issue(cohort_org, repo)
    if isinstance(found, grades.IssueLookupFailed) or found is None:
        log_person(
            f"    {cohort_org}/{repo}: no Feedback issue to post the patch note on"
        )
        return False
    return grades.post_marked_comment(
        cohort_org, repo, found[0], patch_note(paths, on), marker
    )


def patch_released(
    master_org: str,
    template: str,
    cohort_org: str,
    path: str,
    slug: str = "",
    overwrite: bool = False,
    dry_run: bool = True,
) -> int:
    """Push a corrected file (or folder) from `template`'s default branch into every
    submission repo of the assignment it handed out, and say so on each Feedback issue.

    The three things it will not do, because each of them is how a fix becomes a loss:
    it never force-pushes (the commit goes on top of whatever the student has), it never
    replaces a file the student has changed unless `overwrite` says so in as many words,
    and it patches the cohort-side TEMPLATE too - so a student who onboards tomorrow is
    given the corrected file rather than the one everyone else was just patched off.

    That template is the BASELINE the third rule rests on ("does this repo still hold what
    it was given?"), so it moves LAST and only once every submission repo is patched.
    Patched first, a run that failed on one repo would leave that repo holding the
    original while the baseline said corrected - and the re-run this function asks for
    would read the untouched file as the student's own work and refuse to fix it, while
    reporting success.

    Counts only in the log: this runs in the course org's PUBLIC `.github`, and one
    `<slug>-<handle>` line there publishes who is in the cohort."""
    sched = schedule.load(cohort_org)
    target = schedule.resolve_target(sched, template, slug)
    if isinstance(target, str):
        log_err(target)
        return 1
    _key, cohort_slug = target
    log_step(
        f"Patching {cohort_slug} in {cohort_org} from {master_org}/{template}:{path}"
        f"{' (dry run)' if dry_run else ''}"
    )
    corrected = template_files(master_org, template, path)
    # The second outbound copy from a course template, and the one with no
    # `generate_from_template` in it. `path` is free text naming a file OR A FOLDER, so
    # `.github` or `.github/workflows` sweeps up the Release assignment button the
    # template now hosts - into every submission repo, and onto the cohort template every
    # later onboarder generates from, undoing `withhold_from_template` after the fact.
    # Same set, same exact-path rule, same absence of a way to opt back in.
    #
    # ONLY that set. A `.releaseignore` in the course template is not read here, so a
    # folder `path` can still carry a rubric it withholds - the tree this reads is the
    # COURSE template's, where those paths are still present, and whether faculty may
    # patch one out deliberately is a question this filter does not answer. That gap
    # pre-dates the buttons and is its own change.
    unpatchable = sorted(set(corrected) & set(NEVER_IN_STUDENT_REPOS))
    if unpatchable:
        corrected = {p: b for p, b in corrected.items() if p not in unpatchable}
        log(
            f"  withheld from the patch: {', '.join(unpatchable)} - a faculty release "
            f"button is never a student's to hold"
        )
    if not corrected:
        if unpatchable:
            # Not "there is no such path" - there is, and every file under it is one a
            # student's repo may never hold. Saying the other thing sends faculty looking
            # for a file that is sitting on the branch in front of them.
            log_err(
                f"`{path}` on {master_org}/{template} holds nothing but faculty release "
                f"buttons, which are never a student's to press - nothing to patch."
            )
        else:
            log_err(
                f"`{path}` is not on {master_org}/{template}'s default branch - nothing "
                f"to patch. Commit the correction to the template first; this button "
                f"only distributes what is already there."
            )
        return 1
    try:
        handout = repo_blob_shas(
            cohort_org, cohort_slug, default_branch(cohort_org, cohort_slug)
        )
    except RuntimeError as exc:
        log_err(
            f"{cohort_org}/{cohort_slug} could not be read: {exc}. That repo is the frozen "
            f"hand-out every submission was generated from, and without it there is no way "
            f"to tell a student's own edit from the file they were given."
        )
        return 1
    listing = list_org_repos(cohort_org)
    targets = patch_targets(listing, cohort_slug)
    # Kept beside the targets, which came out of the same listing: the patch NOTE goes
    # into a Feedback issue, and whether that issue is one the world can read is the
    # listing's answer (`note_the_patch`).
    rows = {row["name"]: row for row in listing}
    if load_grading_spec(master_org, template).submit_shared:
        # One repo for the whole cohort, and `patch_targets` finds it by the same
        # template-prefix rule that finds a repo per unit - so the loop below needs no arm
        # of its own. Said out loud because "1 submission repo" would otherwise read as a
        # cohort of one, and the drop box is NAMED because its name carries no handle.
        # The targets are counted and never listed: the same prefix rule also matches any
        # `<slug>-<handle>` repo a cohort was handed before the assignment became a drop
        # box, and this log runs in the course org's PUBLIC `.github`.
        log(
            f"  {len(corrected)} file(s) -> the {shared_repo(cohort_slug)} drop box "
            f"({len(targets)} repo(s) to patch)"
        )
    else:
        log(f"  {len(corrected)} file(s) -> {len(targets)} submission repo(s)")
    if dry_run:
        log_ok(
            f"dry run: {len(corrected)} file(s) would be patched into {len(targets)} "
            f"submission repo(s) and into {cohort_slug}; overwrite is "
            f"{'ON' if overwrite else 'OFF'}"
        )
        return 0

    on = datetime.now(timezone.utc).date()
    digests = corrected_digests(corrected)
    marker = patch_marker(digests)
    tally: dict[str, int] = {}
    notes = 0
    for repo in targets:
        verdict, written = patch_one_repo(
            cohort_org, repo, corrected, digests, handout, overwrite
        )
        tally[verdict] = tally.get(verdict, 0) + 1
        if verdict == PATCHED and note_the_patch(
            cohort_org, repo, written, marker, on, rows.get(repo)
        ):
            notes += 1
    # The cohort-side template LAST, and only once every submission repo is done: it is
    # what late onboarders generate from, but it is also the baseline that tells a
    # student's own edit from the file they were given, so moving it while a repo still
    # holds the original is what would strand that repo on the re-run (see above).
    if tally.get(PATCH_FAILED):
        frozen = "deferred - a submission repo failed"
    else:
        frozen, _ = patch_one_repo(
            cohort_org, cohort_slug, corrected, digests, handout, True
        )
    log(
        f"  {cohort_slug} (the frozen hand-out): {frozen}; "
        + "; ".join(f"{n} {what}" for what, n in sorted(tally.items()))
    )
    failures = tally.get(PATCH_FAILED, 0) + (1 if frozen == PATCH_FAILED else 0)
    if failures:
        log_err(
            f"{failures} repo(s) were NOT patched (named above through the private log) - "
            f"re-run once the cause is fixed; a repo already carrying the correction is "
            f"passed over, so a second run costs nothing"
        )
        return 1
    log_ok(
        f"{cohort_slug}: {tally.get(PATCHED, 0)} repo(s) patched, {notes} note(s) posted, "
        f"{tally.get(PATCH_KEPT, 0)} left as the student wrote them"
    )
    return 0


def provision_one(
    master_org: str,
    template: str,
    cohort_org: str,
    repo: str,
    handles: list[str],
    slug: str,
    sol_dir: Path | None = None,
    team: str | None = None,
    touch_existing: bool = True,
    existing: dict[str, dict] | None = None,
    feedback_body: str = "",
    visibility: str = "private",
) -> str:
    """Generate one submission repo and grant its members access.

    `visibility` is the assignment's own (`grading_config.yml`), and is acted on at CREATE
    only - see `repos.set_visibility` for why a public repo is born private. A repo that
    already exists is never re-PATCHed: the tick re-fires every handed-out assignment, so
    that would nightly undo a deliberate change, and an edit to `visibility:` after
    hand-out is reported by the cohort's `grading_config.yml` digest instead.

    It also decides the unit's own PERMISSION. `maintain` is the floor for everything the
    toolkit owns the flag on, and it deliberately excludes GitHub's visibility switch;
    `student_choice` is the shape where the student owns it, so there the grant is `admin`
    - on the collaborator and on the team alike, because a team project belongs to all of
    its members and a repo only one of them could publish is not theirs.

    `existing` is the cohort's repos off ONE listing (`discovery.listing_by_name`), keyed
    by name; membership in it answers "does this repo already exist?" without a GET per
    student, and each row carries the `topics` that `_tag_submission` converges off. None -
    no listing to hand - falls back to probing this one repo, and skips that convergence
    rather than paying a read per student for it.

    A repo this call CREATES is written back into it (`discovery.listing_row`), so the
    listing stays true for everything that reads it after this: the caller's own gradebook
    pass, and the next assignment of the same tick.

    `touch_existing=False` (the hourly scheduler): a repo that already exists, with no
    solution due, is left exactly as it is - no access re-grant, no team reconcile. The
    manual Release assignment button keeps the default and so remains the way a faculty
    member repairs one student's access by re-running it.

    Individual assignments pass a single-element `handles` list (a team of one) and no
    `team`, so each member is added as a collaborator. Group assignments also pass the
    GitHub Team slug: the team is materialised from `handles` and granted on the repo, so
    membership changes propagate to access (and members get @mentions + a team space)."""
    # ONE answer for both arms below, off the vocabulary's own predicate rather than the
    # word: a group repo whose team could not publish it would leave a student_choice
    # assignment half-owned, and that is exactly the kind of drift two spellings buy.
    permission = "admin" if visibility_is_students(visibility) else "maintain"
    if team is not None and not handles:
        # Every member was rejected by the roster allowlist upstream (typo'd handles, a
        # stranger's login), so this team can be granted nothing. Checked BEFORE anything is
        # created: a private repo nobody can open is left behind for the term otherwise.
        log_err(f"  ! team {team} has no vetted members - no repo created for it")
        return "failed-no-members"
    existed = exists_in(existing, cohort_org, repo)
    feedback_failed = False
    visibility_failed = False
    if existed:
        log_person(f"  [skip] repo {cohort_org}/{repo}")
        # Converge the stamp off the listing row that already answered "does it exist?",
        # rather than pay a read per student for it. An ARCHIVED repo is passed over: it
        # is read-only, so the PUT would 403 on every tick for the rest of the term, and a
        # finished cohort is meant to stay frozen - `access.converge_topics` skips them
        # for the same reason.
        row = existing[repo] if existing is not None else None
        if row is not None and not row.get("archived"):
            _tag_submission(cohort_org, repo, slug, set(row.get("topics") or []))
        if sol_dir is None and not touch_existing:
            # Nothing is due for this repo. The scheduler re-runs every handed-out release
            # on every hourly tick, so re-granting access here cost 2-4 API calls per
            # student per assignment for the rest of the term (1,440+/hour for a large
            # cohort) - mostly writes, against a 5,000/hour budget shared by every cron.
            # Faculty access repairs are the nightly sweep's job (converge_faculty_access),
            # a team's late joiners arrive through Sync membership, and a student's access
            # is repaired by re-running the Release assignment button (touch_existing).
            return "skipped"
    elif not generate_from_template(
        template_org=master_org,
        template_name=template,
        owner=cohort_org,
        name=repo,
        private=True,
        description=_about(
            f"{slug} - submission repo", submit_shape("assignment_repo", visibility)
        ),
        person=True,
    ):
        return "failed-create"
    else:
        log_person(f"  [ok] created {cohort_org}/{repo}")
        # The flip `repos.set_visibility` describes. It is COUNTED
        # (`failed-visibility` below) because a repo the instructor said was portfolio
        # work, left private, is not the assignment they handed out; but it withholds
        # NOTHING - the repo exists, the students still get their access and their
        # solution, and the next manual Release assignment repairs the flag.
        #
        # Secret scanning and its push protection are NOT asked for here: GitHub turns
        # both on for a public repository by default, so a PATCH would only ever repeat
        # what is already true.
        # The row written back into the caller's listing carries GITHUB's vocabulary and
        # nothing else, and two things make the config's word the wrong one for it.
        # `student_choice` is a rule about who may flip the repo later, not a value GitHub
        # has ever heard of (`course.github_visibility`); and a `public` flip that FAILED
        # leaves the repo exactly as `/generate` made it. Writing the INTENTION there hid
        # the failure from the one reader that exists to catch it - the digest's
        # `grades._visibility_faults` compares these rows against the file.
        listed_as = github_visibility(visibility)
        if visibility == "public" and not set_visibility(
            cohort_org, repo, "public", person=True
        ):
            visibility_failed = True
            listed_as = "private"
        if existing is not None:
            existing[repo] = listing_row(cohort_org, repo, listed_as)
        _tag_submission(cohort_org, repo, slug, set())
        # The Feedback issue, on the CREATE path only. It is where the submission
        # receipts are posted, so the student is told at handout what the thread is for.
        # Never re-probed for a repo that already exists: that would be one listing
        # per student per hourly tick for the rest of the term, for an issue that does not
        # go away - and the refresh pass opens a missing one lazily when it first has
        # something to say.
        if feedback_body and not grades.ensure_feedback_issue(
            cohort_org, repo, feedback_body
        ):
            # Reported in the RETURN value, not just the log. The issue is where the
            # submission receipts are posted, and it is opened on the CREATE path only -
            # the cron re-fires every handed-out release on every tick, so
            # re-probing an existing repo would cost one listing per student per tick for
            # the rest of the term. A repo that misses its one chance therefore has to red
            # the run, or a whole cohort's handout goes green with nowhere to post into.
            feedback_failed = True
            log_err(
                "  ! a submission repo has no Feedback issue yet - the refresh pass "
                "opens it before the first receipt"
            )

    solution_failed = False
    if sol_dir is not None:
        # template-generate is async, so a repo THIS run created can still be empty. A
        # clone of an empty repo has no branch, and the solution then lands on whatever the
        # runner's `init.defaultBranch` is - invisible to the student and to grading.
        if not existed and not _wait_for_content(cohort_org, repo):
            log_err(
                f"  ! {cohort_org}/{repo} has not populated yet - no solution pushed; "
                f"the next run retries"
            )
            solution_failed = True
        elif push_solution(cohort_org, repo, sol_dir):
            log_person("  [ok]   + solution pushed")
        else:
            # Reported in the RETURN value, not just the log: provision_all writes a
            # fire-once marker off these statuses, so a push that only logged its failure
            # meant the marker was written anyway - the student never received the
            # solution, and the marker guaranteed no later tick would retry.
            log_err("  ! could not push solution")
            solution_failed = True

    # Before the group/individual split, because the group arm RETURNS inside itself: a
    # call after it reaches individual assignments only, and every team project repo would
    # have gone on granting nobody but the team. This repo used to grant no faculty at all,
    # so an instructor who was not an org OWNER could not open the work they had to mark.
    #
    # AT CREATION ONLY. A team grant does not decay, and the nightly sweep
    # (access.converge_faculty_access) owns the floor for every repo that already exists -
    # so re-granting here bought nothing and cost two PUTs per student per assignment on
    # every path that reaches this line.
    #
    # READ, not write. Marking happens in
    # `classroom-config/grading_sheets/<slug>.yml` (docs/10),
    # and by the time anyone marks, the deadline snapshot has already frozen this repo's
    # HEAD and the autograder has run off that snapshot - so a commit here would reach no
    # gradebook and form no part of the record. Faculty need to SEE the work, not edit it.
    if not existed:
        grant_faculty(
            cohort_org,
            repo,
            FACULTY_READ_ACCESS,
            missing_is_note=True,
            person=True,
        )
    if team is not None:
        # Group: materialise the team from its members and grant it on the repo, so
        # post-sync membership edits propagate to access (vs. one-off collaborator grants).
        # A team that couldn't take all its members grants access to nobody missing, so
        # its result counts towards this repo's status rather than being discarded.
        team_ok = sync_teams.ensure_team(cohort_org, team, set(handles), prune=False)
        access_ok = grant_team_repo_access(
            cohort_org, team, repo, permission, person=True
        )
        if access_ok:
            log_person(f"  [ok]   + team {team} ({permission})")
        if not team_ok:
            # One per group repo, and `provision_all` tallies the `failed-team-members`
            # status below into the count faculty read. The team NAME is a roster of who
            # is grouped with whom, and the repo is named after it.
            log_err_person(
                "  ! a team is missing member(s) - they cannot see their repo",
                f"  ! team {team} is missing member(s) - they cannot see "
                f"{cohort_org}/{repo}",
            )
        access_failure = (
            "failed-no-access"
            if not access_ok
            else "failed-team-members"
            if not team_ok
            else ""
        )
    else:
        # Ordering hazard (individual path): granting a repo collaborator BEFORE the
        # student has accepted their org invite records them as an OUTSIDE collaborator,
        # which can make a later team-based add 422 forever. The individual flow is
        # collaborator-based by design (see the module docstring - groups are the
        # team-based path), and onboarding normally accepts the org invite first, so this
        # stays a direct grant; the group path already routes access through the team to
        # avoid the wedge.
        added = 0
        for handle in handles:
            if add_collaborator(
                cohort_org, repo, handle, permission=permission, person=True
            ):
                log_person(f"  [ok]   + @{handle} ({permission})")
                added += 1
            else:
                log_err(f"  ! could not add @{handle} (not a real account?)")
        # A repo nobody can open is a failed handout - "failed" is what the exit code
        # keys on (see provision_all), so the run goes red rather than quietly ok.
        access_failure = "failed-no-collaborator" if added == 0 else ""

    # ONE status tail for both arms, in one precedence. A failed solution push WINS over
    # every other fault: provision_all writes the FIRE-ONCE solution marker off these
    # statuses, so a repo that reported any other failure had its missing solution
    # forgotten - and the marker guaranteed no later tick would retry. Every other fault
    # here is persistent and unrelated to the push.
    if solution_failed:
        return "failed-solution"
    if access_failure:
        return access_failure
    if visibility_failed:
        return "failed-visibility"
    if feedback_failed:
        return "failed-no-feedback-issue"
    return "skipped" if existed else "ok"


def _grant_drop_box(
    cohort_org: str,
    repo: str,
    units: list[tuple[str, list[str]]],
    key: str,
    *,
    group: bool,
) -> tuple[int, bool]:
    """CONVERGE who may push to the ONE drop box on the units that should: grant everyone
    who is missing, and - for an individual assignment - revoke every direct grant that
    belongs to nobody on the current list. Returns `(changes made, nothing failed)`.

    The changes made are what tells a tick that did something from the thousand that did
    not: a drop box that already exists is not the record its per-unit twin is, because a
    student who onboards in week three needs their grant on a repo that has been there
    since week one. So the loop re-runs every tick and this asks, ONCE, who is granted
    already (`repos.direct_collaborators` / `access.repo_teams`) rather than paying an
    idempotent PUT per unit per tick for the rest of the term.

    GRANTING alone is not enough here, and this is the one repo where that matters: a
    per-unit repo holds one student's own work, but the drop box holds the WHOLE cohort's,
    so a student who withdraws keeps push on everybody else's until something takes it
    away. `units` is built from the enrolled, onboarded roster upstream, so it is the
    answer - and `direct_collaborators` is the only set revoked against, which is what
    keeps faculty and the bot (who reach the repo through a team, or by owning the org)
    out of it.

    Not a cohort-wide team, which would be one grant for everybody: `sync_teams` would
    then have to own and prune it, and a student who left the course would keep push on
    the work of the ones who stayed until it did.

    A listing that could not be READ grants everyone again - `None` is not "nobody" - and
    the PUT is idempotent, so the cost of being wrong that way is a call.

    A group's MEMBERSHIP is the same as it is for a repo per team: the team is
    materialised when it is first granted, and a member who joins it later arrives through
    Sync membership rather than through this loop, which would otherwise reconcile every
    team of every handed-out assignment on every tick."""
    made = 0
    failed = 0
    if group:
        granted = repo_teams(cohort_org, repo)
        for unit, members in units:
            team = teams.team_slug(key, unit)
            if granted is not None and team.casefold() in granted:
                continue
            if not sync_teams.ensure_team(cohort_org, team, set(members), prune=False):
                # The team NAME is a roster of who is grouped with whom, so it goes to the
                # verbose log and the actionable half stays public.
                log_err_person(
                    f"  ! a team is missing member(s) - they cannot see {repo}",
                    f"  ! team {team} is missing member(s) - they cannot see "
                    f"{cohort_org}/{repo}",
                )
                failed += 1
            if grant_team_repo_access(cohort_org, team, repo, "push", person=True):
                made += 1
                log_person(f"  [ok]   + team {team} (push)")
            else:
                failed += 1
        return made, failed == 0
    granted = direct_collaborators(cohort_org, repo)
    # The unique handles, not the units: an individual assignment has one member per unit,
    # and a student on two rows of the roster is one person with one grant to make.
    wanted = {handle for _unit, members in units for handle in members}
    for handle in sorted(wanted):
        if granted is not None and handle.casefold() in granted:
            continue
        if add_collaborator(cohort_org, repo, handle, permission="push", person=True):
            made += 1
            log_person(f"  [ok]   + @{handle} (push)")
        else:
            failed += 1
    # ...and the other direction. A listing that could not be read revokes NOTHING: the
    # cost of granting again on a bad read is one idempotent call, and the cost of
    # revoking on one is a student locked out of their own submission. The bot is spared
    # by name because a run that removed its own grant could not repair anything
    # afterwards; everyone else in this set was put there by this loop.
    stale = sorted((granted or frozenset()) - {h.casefold() for h in wanted})
    for login in stale:
        if login == bot_login().casefold():
            continue
        if remove_collaborator(cohort_org, repo, login, person=True):
            made += 1
            log_person(f"  [ok]   - @{login} (no longer on the roster)")
        else:
            failed += 1
    return made, failed == 0


def ensure_drop_box(
    cohort_template: str,
    cohort_org: str,
    slug: str,
    units: list[tuple[str, list[str]]],
    key: str,
    *,
    group: bool,
    listing: dict[str, dict] | None = None,
) -> tuple[bool, bool]:
    """The whole of what `submit_via: shared_dropbox_repo` hands out: ONE private
    `<slug>-submissions` off the frozen cohort template, push for every unit, and a
    ruleset that stops it being rewritten. Returns `(nothing failed, anything changed)`.

    The name carries no handle, so unlike every other submission repo it may be printed in
    a public workflow log in full (`course.shared_repo`).

    The ruleset is the last step and a COUNTED failure: an unprotected drop box is one
    force-push from erasing the cohort's work and the history the deadline snapshot pins
    to, which is not a state to leave a handout green in. It is idempotent, so the next
    tick repairs it.

    `listing` is the caller's own rows, mutated with the drop box this run creates, so the
    next pass of the same tick sees the repo rather than creating it again."""
    repo = shared_repo(slug)
    existed = exists_in(listing, cohort_org, repo)
    if existed:
        log_skip(f"drop box {cohort_org}/{repo}")
        # Converge the stamp off the row that already answered "is it there?", exactly as
        # the per-unit path does - and pass over an archived repo, which is read-only.
        row = listing.get(repo) if listing is not None else None
        if row is not None and not row.get("archived"):
            _tag_submission(cohort_org, repo, slug, set(row.get("topics") or []))
    elif not generate_from_template(
        template_org=cohort_org,
        template_name=cohort_template,
        owner=cohort_org,
        name=repo,
        private=True,
        description=_about(
            f"{slug} - shared submission drop box",
            submit_shape("shared_dropbox_repo", "private"),
        ),
    ):
        log_err(f"could not create the {slug} drop box in {cohort_org}.")
        return False, False
    else:
        log_ok(f"created {cohort_org}/{repo}")
        if listing is not None:
            listing[repo] = listing_row(cohort_org, repo)
        _tag_submission(cohort_org, repo, slug, set())
        # READ, for the same reason every other repo a cohort receives gets read: the work
        # is marked in `classroom-config/grading_sheets/`, so a commit here would reach no
        # gradebook. At CREATION only - the nightly sweep owns the floor after that.
        grant_faculty(cohort_org, repo, FACULTY_READ_ACCESS, missing_is_note=True)
    made, granted_ok = _grant_drop_box(cohort_org, repo, units, key, group=group)
    if made:
        log_ok(f"{cohort_org}/{repo}: {made} unit(s) can now push")
    protected = protect_shared_repo(cohort_org, repo)
    return granted_ok and protected, bool(made) or not existed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master-org", required=True, help="Course org (template source)"
    )
    parser.add_argument(
        "--course-source-repo",
        dest="template",
        required=True,
        help="COURSE-org repo to hand out from (e.g. assignment-1-f2026)",
    )
    parser.add_argument("--cohort-org", required=True, help="Cohort org (target)")
    parser.add_argument(
        "--roster",
        default=None,
        help="Local students.csv (default: cohort classroom-config)",
    )
    parser.add_argument(
        "--solution",
        action="store_true",
        help="Also push the solution (template's `solution` branch) into each student repo",
    )
    parser.add_argument(
        "--slug",
        default="",
        help="Which assignment in the cohort's schedule.yml this is, when two of them hand out from the same template (each with its own cohort_dest_repo). Leave empty otherwise.",
    )
    # The PATCH mode: `--patch-path` switches this CLI from handing an assignment out to
    # correcting one that is already out. A flag rather than a subcommand, because
    # `python3 -m dsl_course.assign --master-org ...` is a frozen public contract - every
    # bootstrapped org's Release assignment workflow spells it, and an org that has not
    # refreshed yet must keep working.
    parser.add_argument(
        "--patch-path",
        default="",
        help="PATCH MODE: push this file/folder from the template's default branch into "
        "every submission repo of the assignment it handed out.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Patch mode: replace the file even where the student has changed it.",
    )
    # Tri-state on purpose. Provisioning has always defaulted to a REAL run (an operator
    # who pressed Release assignment meant it), and patching defaults to a preview like
    # every other button that writes into student repos. `None` is "the caller said
    # nothing", so each mode keeps its own default and `--no-dry-run` reaches both.
    parser.add_argument(
        "--dry-run", action=argparse.BooleanOptionalAction, default=None
    )
    args = parser.parse_args()
    # A read helper that couldn't reach the API raises; in an Actions log a one-line
    # error beats a traceback, and the run still goes red.
    try:
        if args.patch_path:
            return patch_released(
                args.master_org,
                args.template,
                args.cohort_org,
                args.patch_path,
                slug=args.slug,
                overwrite=args.overwrite,
                dry_run=True if args.dry_run is None else args.dry_run,
            )
        rc, _changed = provision_all(
            args.master_org,
            args.template,
            args.cohort_org,
            roster_path=args.roster,
            solution=args.solution,
            dry_run=bool(args.dry_run),
            slug=args.slug,
            # ONE listing of the cohort for this press, taken here because there is no
            # tick above to have taken it: every repo question below is answered off it,
            # and None (it could not be read) falls back to a probe per repo.
            listing=listing_by_name(args.cohort_org),
        )
        return rc
    except RuntimeError as exc:
        log_err(str(exc))
        return 1


def solution_record_path(slug: str) -> str:
    """Where the fire-once record for `slug`'s solution release lives."""
    return f"{SOLUTION_RECORD_DIR}/{slug}.json"


def solution_released(cohort_org: str, slug: str) -> bool:
    """Whether the model solution for `slug` has already been pushed to this cohort.

    Read by the scheduler, so a passed `solution_datetime` fires exactly once. The manual
    Release assignment path does NOT consult it - an operator ticking include_solution is
    asking for it now, and push_solution is an idempotent overwrite anyway."""
    return file_exists(cohort_org, CONFIG_REPO, solution_record_path(slug))


def record_solution_released(cohort_org: str, slug: str, repos: int) -> bool:
    """Write the fire-once record, so no later tick re-pushes the solution.

    Written only after a run in which every solution push succeeded - a partial push must
    re-run, or the students it missed would never receive the solution at all. Returns
    whether the record actually landed: a marker that did not is what makes every later
    tick re-clone every submission repo to re-push a solution they already have."""
    return put_file(
        cohort_org,
        CONFIG_REPO,
        solution_record_path(slug),
        json.dumps(
            {"assignment": slug, "repos": repos, "released": "by dsl-course"}, indent=2
        ).encode()
        + b"\n",
        f"chore: record the model solution release for {slug}",
    )


# provision_one statuses that mean the model solution did NOT reach that unit's repo, and
# so must withhold the fire-once release marker. Every OTHER `failed-*` happens AFTER the
# push (a dead handle, an unreachable team, a Feedback issue that would not open) and is
# persistent, so withholding the marker for one would re-clone every submission repo every
# hour for the rest of the term - the exact cost the marker exists to prevent.
_SOLUTION_NOT_PUSHED = ("failed-solution", "failed-create")


# What one handout arm did, in the four facts `provision_all`'s tail reads off it: the
# per-unit outcome counts, whether this pass changed anything, the units it made a repo
# for, and whether a requested model solution could not be fetched. `None` in place of one
# is a DRY RUN - the arm has said what it would create and created nothing, so there is no
# tail to run.
_Released = tuple[dict[str, int], bool, list[tuple[str, list[str], str | None]], bool]


def _dry_run_cohort_template(cohort_org: str, slug: str) -> None:
    """The line every arm that CREATES repos prints first in a dry run: whatever shape
    follows, the cohort-side template is frozen before it. One spelling, so the two arms
    cannot describe the same step differently."""
    log(f"    DRY-RUN  cohort template {cohort_org}/{slug}")


def _release_external(
    cohort_org: str, slug: str, what: str, solution: bool, dry_run: bool
) -> _Released | None:
    """The `external` arm: the work is handed in off GitHub (Moodle, Kaggle, in class), so
    the handout creates nothing at all. Everything this assignment owes its cohort - the
    schedule entry, the grading sheet, the gradebooks, the site - is the tail every shape
    shares."""
    log_step(
        f"Releasing {slug} to {cohort_org}: handed in off GitHub, so no repos - the "
        f"schedule, the grading sheet, the gradebooks and the site for {what}"
    )
    if solution:
        # Nowhere to push it: the model answer stays on the template's solution branch,
        # which is where the teaching team reads it from anyway.
        log(f"  (no model solution to push - {slug} creates no submission repos)")
    if dry_run:
        log(f"    DRY-RUN  no repos; record the handout and the sheet for {what}")
        return None
    # There is no new repo here to mark a late onboarder's tick as having done something,
    # so the sheet in the tail decides it instead.
    return {}, False, [], False


def _release_shared(
    cohort_template: str,
    cohort_org: str,
    slug: str,
    key: str,
    sheet_units: list[tuple[str, list[str]]],
    what: str,
    *,
    group: bool,
    solution: bool,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> _Released | None:
    """The `shared_dropbox_repo` arm: ONE private drop box for the whole cohort,
    generated from the frozen cohort template, with push for every unit.

    No units are returned: there is no repo per unit here, so nothing downstream that
    counts them - the solution record above all - has anything to count."""
    drop_box = shared_repo(slug)
    log_step(
        f"Releasing {slug} to {cohort_org}: freeze cohort template, then one private "
        f"drop box {cohort_org}/{drop_box} with push for {what}"
    )
    if solution:
        # Nowhere private to put it - see the `shared_dropbox_repo` note beside
        # `course.SUBMIT_VIA`.
        # The fire-once marker is deliberately not written (`can_hold_solution` gates it),
        # so an instructor who corrects the shape can still release it.
        log(
            f"  (no model solution to push - {slug} has one drop box the whole "
            f"cohort can read)"
        )
    if dry_run:
        _dry_run_cohort_template(cohort_org, slug)
        log(f"    DRY-RUN  {cohort_org}/{drop_box}  <- push for {what}")
        return None
    drop_box_ok, changed = ensure_drop_box(
        cohort_template,
        cohort_org,
        slug,
        sheet_units,
        key,
        group=group,
        listing=listing,
    )
    results: dict[str, int] = {}
    if not drop_box_ok:
        # Counted like any other handout failure, through the same `results` the tail
        # reads: the repos (here, the repo) are what this function is judged on.
        results["failed-drop-box"] = 1
    return results, changed, [], False


def _release_units(
    master_org: str,
    template: str,
    cohort_template: str,
    cohort_org: str,
    slug: str,
    key: str,
    spec,
    gspec,
    sheet_units: list[tuple[str, list[str]]],
    what: str,
    *,
    group: bool,
    solution: bool,
    touch_existing: bool,
    dry_run: bool,
    listing: dict[str, dict] | None,
) -> _Released | None:
    """The `assignment_repo` arm: one repo per unit (student, or team), generated from
    the frozen cohort template, plus the model solution where the shape can hold one."""
    # NO model solution into repos the toolkit cannot promise are private. `public`
    # publishes the model answer to the internet and `student_choice` lets any student
    # publish it, and neither can be taken back - so the stage is skipped whole, and the
    # FIRE-ONCE marker is deliberately not written for it (`can_hold_solution` gates it in
    # `provision_all`), which leaves an instructor who corrects the shape able to release
    # it on a later run. The plan and the definition are edited by different people, so
    # the digest reports the disagreement as well (`grades.grading_spec_faults`).
    if solution and not gspec.can_hold_solution:
        log(
            f"  (model solution not pushed - {slug}'s repos are public or "
            f"student-owned, so the answers would be published with them)"
        )
        solution = False
    # A provisioning unit is (repo_name, [member handles], team slug), and each carries
    # the body its Feedback issue is opened with. Both are names for a repo, so both
    # belong to the only shape that creates one.
    #
    # No body at all where the shape HAS no Feedback issue - a `public` repo is not a
    # place to write a student's hand-in times - and `provision_one` opens one only for a
    # unit it was given a body for. The derived rule decides it, so nothing here re-states
    # which shapes have a thread and which do not.
    solo_body = (
        grades.feedback_body(spec) if gspec.has_feedback_issue and not group else ""
    )
    units: list[tuple[str, list[str], str | None]] = []
    feedback_bodies: dict[str, str] = {}
    for unit, members in sheet_units:
        repo = submission_repo(slug, unit)
        units.append((repo, members, teams.team_slug(key, unit) if group else None))
        feedback_bodies[repo] = (
            grades.feedback_body(spec, unit, members)
            if gspec.has_feedback_issue and group
            else solo_body
        )
    # The shape in the one line faculty read in the run log. Only the two that are
    # NOT the default say anything: `private` is what a reader already assumes, and a
    # note on every handout is a note nobody reads on the one that matters.
    shape_note = ""
    if gspec.visibility == "public":
        shape_note = " as PUBLIC repos"
    elif gspec.visibility_is_students:
        shape_note = " as private repos their students may publish"
    log_step(
        f"Releasing {slug} to {cohort_org}: freeze cohort template, then provision "
        f"{what}{shape_note}{' + solution' if solution else ''}"
    )
    if dry_run:
        _dry_run_cohort_template(cohort_org, slug)
        for repo, handles, team in units:
            via = f" (team {team})" if team else ""
            log_person(
                f"    DRY-RUN  {cohort_org}/{repo}{via}  <- "
                f"{', '.join('@' + h for h in handles)}"
            )
        return None

    results: dict[str, int] = {}
    solution_unavailable = False
    with tempfile.TemporaryDirectory() as soldir:
        # Solution still comes from the COURSE template's solution branch.
        sol_dir = None
        if solution:
            sol_dir = fetch_solution(master_org, template, Path(soldir) / "t")
            if sol_dir is None:
                # NOT fatal. The fan-out below is what gets students their repos at all,
                # and a scheduled handout re-runs every tick - so returning here would
                # mean a template whose solution branch is missing, renamed, or holding
                # the model answer outside `solution/` stops provisioning for every
                # student who onboards from that moment on, with the solution request as
                # the only cause. Hand out the repos, report the failure, ship no solution.
                log_err(
                    "  ! no usable solution to push - provisioning continues without it"
                )
                solution_unavailable = True

        # One repo per unit (student, or team), FROM the cohort template.
        for repo, handles, team in units:
            log_person(f"-> {repo}")
            status = provision_one(
                cohort_org,
                cohort_template,
                cohort_org,
                repo,
                handles,
                slug,
                sol_dir,
                team=team,
                touch_existing=touch_existing,
                existing=listing,
                feedback_body=feedback_bodies.get(repo, ""),
                visibility=gspec.visibility,
            )
            results[status] = results.get(status, 0) + 1

    log_ok(f"Done - {json.dumps(results)}")
    return results, any(k != "skipped" for k in results), units, solution_unavailable


def provision_all(
    master_org: str,
    template: str,
    cohort_org: str,
    roster_path: str | None = None,
    solution: bool = False,
    dry_run: bool = False,
    touch_existing: bool = True,
    scheduled: bool = False,
    slug: str = "",
    listing: dict[str, dict] | None = None,
) -> tuple[int, bool]:
    """Freeze the cohort template, then provision a repo per unit (student, or team).

    Returns `(exit code, whether anything changed)` - the shape `deploy.deploy_many`
    already uses. The scheduler re-fires every handed-out release on every hourly tick
    (that is what gets a late onboarder their repo), so almost every tick provisions
    nothing at all; without the second half of the answer the caller could only assume it
    had, and re-rendered the whole cohort website once an hour for the rest of the term.
    `changed` is the same predicate this function's own site sync uses: at least one unit
    was not `skipped`.

    Callable directly (e.g. by the scheduler) as well as from the CLI. Individual or
    group is the assignment's own declaration - `type:` in the grading_config.yml on the
    template's solution branch - and there is no override: the sheet, the Join-team form
    and the teams themselves are all keyed on that file, so a handout free to disagree
    with it puts a cohort's work in repos nothing else is looking for.

    `scheduled` marks the hourly cron: a group assignment with no teams yet is then a
    green wait, not the error a button press gets.

    `slug` names WHICH schedule entry is being handed out, and only the scheduler can
    answer it: it knows which release it is firing. The button asks nobody - a template
    two entries hand out from is refused below, because they make different repos for
    different students and keep separate grades, and guessing is a whole cohort's work in
    the wrong place.

    `listing` is the cohort's repos keyed by name, off the ONE listing its caller already
    holds - the tick's (`scheduler.run`) or the one the button takes for itself - and BOTH
    arms run on it: "is this repo already there?" for the cohort template and every unit,
    and "does this student already have a gradebook?" in the tail. It is also MUTATED,
    which is what makes one listing enough: every repo this run creates is written back
    into it (`discovery.listing_row`), so the next assignment of the same tick sees the
    repos and gradebooks this one just made instead of creating them again and counting
    GitHub's refusals as failures. None is "we could not look", and each step below falls
    back to a probe of its own."""
    if master_org == cohort_org:
        log_err("master-org and cohort-org must differ.")
        return 1, False
    # The assignment's own definition, read ONCE here: it answers the shape (below), and
    # it composes both the grading sheet's header and the Feedback issue's body further
    # down. Two reads of one memoised file is not expensive, but it is two places for the
    # answer to be spelt, which is how a handout came to provision a shape the sheet did
    # not expect.
    gspec = load_grading_spec(master_org, template)
    # The assignment's own grading_config.yml is the only declaration there is.
    group = gspec.is_group
    if group:
        log("  (declared `type: group` - provisioning per team)")

    students = roster.load_path(roster_path) if roster_path else roster.load(cohort_org)
    if students is None:  # missing/unreadable roster - load() already logged why
        return 1, False
    if not students:
        log_err(f"roster in {cohort_org} has no rows yet - nobody to provision for.")
        return 1, False
    # Auditors are read-only - they see released materials, never an assignment repo.
    participants = roster.enrolled(students)
    auditing = len(students) - len(participants)
    onboarded = [s for s in participants if s.onboarded]
    skipped = len(participants) - len(onboarded)
    # TWO names, and they are not interchangeable.
    #   `slug`: the cohort-side NAME - `cohort_dest_repo`, else the schedule key, else (for
    #     a handout of an unscheduled template) the template name minus its tag. Every repo
    #     made here, and every snapshot/autograde/grades artefact, is named after it.
    #   `key`: the SCHEDULE KEY. teams.csv is keyed on it - the welcome Join-team form
    #     validates the assignment against `assignments:` in schedule.yml and writes that
    #     key - and `sync_teams.desired_teams` derives its GitHub team slugs from it.
    # They differ exactly when `cohort_dest_repo` is set. Keying the lookup or the team slug
    # on the name then meant no teams found at all, or a team granted on the repo under a
    # slug that Sync membership reconciles a DIFFERENT team for.
    sched = schedule.load(cohort_org)
    # The parameter is consumed HERE and nowhere else: from the next line on, `slug` means
    # the cohort-side name, exactly as it does everywhere else in this file.
    #
    # Two entries handing out from one template is legitimate - a resit off the same
    # brief - and `resolve_target` refuses to choose between them. The REMEDY is this
    # caller's to name: the button cannot answer, because it is one press and the answer
    # decides which half of the cohort gets repos, so it is sent to the schedule, which
    # fires each entry on its own datetime and therefore knows which one it is.
    target = schedule.resolve_target(
        sched,
        template,
        slug,
        remedy="hand this one out from the schedule (each entry fires on its own "
        "handout_datetime) rather than from this button",
    )
    if isinstance(target, str):
        log_err(target)
        return 1, False
    key, slug = target
    if not gspec.creates_repos and key not in sched.assignments:
        # Nothing is written at all, and this is the only shape it can happen to. An
        # assignment that creates no repo leaves the schedule entry as the one record that
        # it went out - and the brief, the sheet's cutoff and the site's due row all hang
        # off a `due_datetime:` that a fabricated entry cannot supply.
        log_err(
            f"`{slug}` is handed in off GitHub and this cohort's schedule.yml has no "
            f"entry for it - add the assignment there with a `due_datetime:` first, then "
            f"release it."
        )
        return 1, False
    # The sheet's header and the Feedback issue's body, off the definition read above.
    spec = sheet_spec(sched, key, slug, gspec, group)

    # WHAT the assignment is handed out to, in the sheet's own vocabulary and known to
    # every shape: (unit, [member handles]). Individual = one per onboarded student; group
    # = one per team from teams.csv, keyed on `key`. The repo names and the Feedback issue
    # bodies are the github arm's, and are built there.
    if group:
        groups = teams.teams_for(teams.load(cohort_org), key)
        if not groups:
            # WHO fills teams.csv is the assignment's own declaration, and the two answers
            # need different words: telling a course whose teams the teaching team
            # allocates to wait for students to self-select points them at a form that
            # refuses every request (see templates/welcome/team-formation.yml).
            # The RAW declaration, not `team_formation_resolved`: a template that
            # declares nothing self-selects.
            self_select = gspec.team_formation != ASSIGNED
            if scheduled:
                # Teams appear days after the handout datetime either way - and the cron
                # re-fires every hour until they do. Wait, exactly as an individual
                # handout waits for its first onboarded student.
                arrives = (
                    "the first team forms"
                    if self_select
                    else "the teaching team writes them into teams.csv"
                )
                log(
                    f"  [wait] no teams for `{key}` in {cohort_org} yet - the handout "
                    f"fires on the tick after {arrives}"
                )
                return 0, False
            how = (
                "students self-select via the welcome 'Join team' issue, or seed the CSV"
                if self_select
                else "this assignment allocates teams (`team_formation: assigned`), so "
                "the teaching team fills the CSV - the Join-team form refuses it"
            )
            log_err(
                f"no teams for `{key}` in {cohort_org}/classroom-config/teams.csv - {how}."
            )
            return 1, False
        # teams.csv is student-writable (the welcome "Join team" issue appends rows), so its
        # handles must pass the SAME roster allowlist sync_teams applies: only enrolled,
        # onboarded roster handles - never a typo or a stranger's login that would be INVITED
        # into the private cohort org (and granted `maintain` on a repo) by ensure_team.
        # `vet_groups` is that one allowlist; the reporting below is this path's own.
        #
        # The team NAME from teams.csv (and, below, the student's handle) is what the
        # GRADING SHEET is keyed on - the same key `collect.submission_targets` uses, so
        # the handout's rows and every later refresh's rows are the same rows.
        sheet_units: list[tuple[str, list[str]]] = []
        for team, vetted, rejected in sync_teams.vet_groups(groups, participants):
            for m in rejected:
                # Names a handle a STUDENT typed into teams.csv, and this workflow's log is
                # world-readable - so the handle is verbose-only and the actionable line
                # below names nobody. Same split as sync_teams' own rejection log.
                log_person(
                    f"    {m} in teams.csv ({key}/{team}) is not an enrolled, onboarded "
                    f"roster handle - excluding it (would invite an arbitrary account "
                    f"into {cohort_org})"
                )
            if rejected:
                log_err(
                    f"{len(rejected)} handle(s) in teams.csv ({key}/{team}) are not "
                    f"enrolled, onboarded roster handles - excluded (they would invite "
                    f"arbitrary accounts into {cohort_org}). Re-run the CLI locally with "
                    f"DSL_VERBOSE=1 to see which."
                )
            sheet_units.append((team, vetted))
        what = f"{len(sheet_units)} team(s)"
    else:
        sheet_units = [(s.github_handle, [s.github_handle]) for s in onboarded]
        what = f"{len(sheet_units)} student(s)"

    if skipped:
        log(f"  ({skipped} not-yet-onboarded row(s) skipped)")
    if auditing:
        log(f"  ({auditing} auditor row(s) skipped - read-only, no assignment repos)")

    # Stage 1, and the one thing BOTH repo-making shapes need: the cohort's own frozen
    # copy of the course template. The drop box is generated from it and so is every unit
    # repo, so it is taken once, here, rather than at the top of two arms that could then
    # come to freeze different things. A dry run creates nothing and so takes none - each
    # arm says what it would do and returns before it would be used.
    cohort_template = ""
    if gspec.creates_repos and not dry_run:
        frozen = ensure_cohort_template(master_org, template, cohort_org, slug, listing)
        if frozen is None:
            log_err("could not create the cohort assignment template.")
            return 1, False
        cohort_template = frozen

    # The one place the three shapes part, and the whole of what makes them different.
    # `external` is handed in off GitHub (Moodle, Kaggle, in class), so everything the
    # other two do - the repos, the Feedback issue, the model solution - has nothing to
    # act on. `shared_dropbox_repo` makes ONE drop box for the whole cohort instead of
    # a repo per unit.
    # What a handout owes the cohort AROUND the work is the tail, which all three share.
    if gspec.submit_external:
        released = _release_external(cohort_org, slug, what, solution, dry_run)
    elif gspec.submit_shared:
        released = _release_shared(
            cohort_template,
            cohort_org,
            slug,
            key,
            sheet_units,
            what,
            group=group,
            solution=solution,
            dry_run=dry_run,
            listing=listing,
        )
    else:
        released = _release_units(
            master_org,
            template,
            cohort_template,
            cohort_org,
            slug,
            key,
            spec,
            gspec,
            sheet_units,
            what,
            group=group,
            solution=solution,
            touch_existing=touch_existing,
            dry_run=dry_run,
            listing=listing,
        )
    if released is None:
        return 0, False  # a dry run: it has said what it would create, and created none
    results, changed, units, solution_unavailable = released

    # ---------------------------------------- what a handout owes the cohort, either shape

    # The grading sheet arrives WITH the handout: every row present, every human field
    # blank, and a header saying which fields the toolkit fills and when. A sheet that only
    # appeared once someone had submitted would be one a grader cannot plan around - and a
    # missing row would be indistinguishable from an ungraded one. Nothing is derived here
    # (there is nothing to derive before the due date, and a handout must not cost an API
    # call per student); the hourly refresh takes over from the due date.
    #
    # Where there are repos, only when this pass actually provisioned something: the
    # scheduler re-fires every handed-out assignment on every tick (`due_releases` is
    # cumulative), and a pass that skipped every repo has nothing new to put in the sheet.
    # Where there are NONE there is no such signal - no repo is ever created to notice - and
    # this is the only pass that knows the units before the due date, so the sheet is
    # written on every tick and whether it had to be CREATED is what `changed` means for the
    # rest of this run. It costs one contents read; the write is skipped when the rows and
    # the header have not moved.
    #
    # Not fatal, and deliberately not counted: the repos are handed out by this point, and
    # the refresh pass creates a sheet it finds missing on the next tick.
    sheet_written = False
    if changed or not gspec.creates_repos:
        sheet = sync_sheet(
            master_org,
            cohort_org,
            sched,
            key,
            slug,
            template,
            is_group=group,
            now=datetime.now(timezone.utc),
            units=sheet_units,
            listing=listing,
        )
        sheet_written = sheet.written
        if not sheet.written:
            log_err(
                f"  ! could not write the grading sheet for {slug} - the hourly refresh "
                f"creates it on a later tick"
            )
        if not gspec.creates_repos:
            changed = sheet.created

    # Record the handout moment back into the cohort's schedule.yml (write-once - a
    # handout the schedule already carries is never touched). The schedule is the primary
    # route AND the one record of when each assignment went out; a manual workflow run
    # fills the field the dispatcher didn't. record_handout keys on the schedule KEY, not the
    # cohort-side name: when `cohort_dest_repo` is set the two differ, and passing the name
    # made it miss the real entry and append a bogus duplicate block (dropping its due date).
    #
    # Ungated where there are repos: a first tick with nobody onboarded yet is still the
    # moment the assignment went out. A shape that creates none has no such signal, so it
    # records on any tick whose SHEET landed - not only the one that created it. Gating on
    # creation instead meant a manual release whose sheet already existed, or one fired
    # before anybody had onboarded, never recorded the handout at all - and the brief is
    # published off that record, so it never appeared. `record_handout` is write-once, so
    # repeating it on every later tick costs one read and changes nothing.
    if gspec.creates_repos or sheet_written:
        schedule.record_handout(cohort_org, key)

    # ...and refresh the Join-team form's mirror while this run holds the schedule and the
    # spec. A REAL handout is the moment the two can most recently have moved, and the form
    # is read by students who cannot see either file. Gated on `changed` for the same reason
    # the sheet and the site sync are: `due_releases` is cumulative, so the scheduler
    # re-fires every handed-out assignment on every tick, and a pass that skipped every repo
    # handed nothing out - it would only pay one contents read per assignment per quarter of
    # an hour to write a file `put_file` then finds unchanged. Not counted into `failed`:
    # the repos are out, and Sync membership rewrites it on every schedule.yml push anyway.
    if changed:
        grades.write_team_lock(
            cohort_org=cohort_org, course_org=master_org, sched=sched
        )

    # site.sync_site now RAISES on a genuine tree/team read failure (post-PR2), and a config
    # file that doesn't parse raises yaml.YAMLError - which is NOT a RuntimeError. The repos
    # are already handed out by this point, so neither failure may abort the run with a
    # traceback and misreport the whole handout as failed: log it, count it (so the run goes
    # red and the next Sync site / tick refreshes the site), and return normally.
    site_failed = False
    try:
        # A tick that created or changed nothing has nothing to show the site: skipping the
        # sync here is what stops every handed-out assignment re-rendering the site hourly.
        if changed:
            site.sync_site(master_org, cohort_org)
    except (RuntimeError, yaml.YAMLError) as exc:
        log_err(
            f"site sync failed after provisioning {slug} - the repos are handed out; the "
            f"site refreshes on the next Sync site or scheduler tick: {exc}"
        )
        site_failed = True

    # A gradebook per onboarded student, from the handout rather than from the first
    # distribute: the brief points at "your gradebook" from the day it is published, and a
    # student who onboarded this hour has just been given their repo. Idempotent, and off
    # the listing this run already took rather than a second one. LAST, after the site: it
    # is a call per student who has none yet, and everything ahead of it is what a cohort
    # is waiting on - the repos, then the page that tells them where to find them.
    if changed:
        grades.ensure_gradebooks(cohort_org, existing=listing)

    failed = site_failed or any(k.startswith("failed") for k in results)
    # Record the release only when every solution push in this run landed, and only when
    # there was at least one repo to push into. Deliberately NOT gated on `failed`:
    #   - a site-sync failure, or one dead student handle, says nothing about whether the
    #     solution shipped - and both are PERSISTENT, so withholding the marker for them
    #     would re-clone every student repo every hour for the rest of the term, which is
    #     the exact cost this marker exists to prevent;
    #   - `units == []` (nobody onboarded yet) means nothing was pushed at all, so
    #     recording it would mean everyone who onboards later never gets the solution.
    # The statuses that mean this unit never received the solution are read here directly:
    # `failed-solution` is a push that was attempted and failed, and `failed-create` is a
    # repo that never existed to push INTO - provision_one returns it before the push, so
    # the marker used to be written over it and no later tick ever retried.
    solution_pushed = (
        solution
        # The same predicate the arm above skipped the push on, asked once more because
        # the arm's own `solution` is its own: a shape that cannot hold the model answer
        # never pushed one, so its fire-once marker must not be written over the absence.
        and gspec.can_hold_solution
        and not solution_unavailable
        and bool(units)
        and not any(results.get(s) for s in _SOLUTION_NOT_PUSHED)
    )
    if solution_pushed and not record_solution_released(cohort_org, slug, len(units)):
        log_err(
            f"the solution for {slug} shipped, but its fire-once record could not be "
            f"written to {cohort_org}/classroom-config - until it is, every hourly tick "
            f"re-clones every submission repo to push a solution they already have"
        )
        failed = True
    return (1 if failed or solution_unavailable else 0), changed


if __name__ == "__main__":
    sys.exit(main())
