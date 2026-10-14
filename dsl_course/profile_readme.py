"""Generate an org's landing pages from its live contents.

Two documents per org:

- `profile/README.md` - the org landing page. A semester org gets a student-facing page
  (how to enrol, where the materials are); a course org gets the faculty-facing index of
  semesters, repos, and every action workflow.

  The COURSE page is wholly generated - it is a live index of actions and semesters, and
  is read by faculty who can go and change the things it indexes. The SEMESTER page is
  the front door students land on, so it is theirs to word: seeded once, then left
  alone, EXCEPT the repo table between the `dsl:repo-table` markers, which keeps being
  regenerated from the org's live repo list. See splice_repo_table.
- the `.github` repo's own `README.md` - the orientation a faculty member sees on
  landing in that repo, next to the Actions tab where the workflows live. Wholly
  generated, and stamped as such.

Rendering is pure (render_profile_readme / render_dotgithub_readme take the repo list);
update_profile_readme is the one function that touches the network, and all it does with
it is read the org and write the two files. The description/access/topic convergence that
used to ride along here belongs to the nightly sweep, not to a README renderer - see
seed._converge_org_metadata.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from . import policy
from .central import CENTRAL
from .course import CONFIG_REPO, COURSE_CONFIG, JOIN_REPO, course_block
from .discovery import (
    being_set_up,
    course_name_for_semester,
    discover_semesters,
    join_issue_url,
    list_org_repos,
    org_meta,
    org_tier,
    student_repo_names,
)
from .gh_contents import get_file_content, put_files
from .log import log, log_err, log_ok
from .ops.registry import REGISTRY, SEMESTER, Operation


def _visible_repos(repos: list[dict]) -> list[dict]:
    """The repos a landing page names: everything bar `.github` and per-person machinery.

    Machinery is dropped, not just `.github`: a semester org holds one submission repo per
    student per assignment and one gradebook each, and listing them named every
    classmate's private repo - and so every enrolled handle and every team's membership -
    on the page students land on. They could never OPEN them, but the roster is exactly
    the thing this pipeline keeps out of public view. The site repo stays: it is public
    anyway, and faculty need the "do not touch" row.
    """
    students = student_repo_names(repos)
    return [r for r in repos if r["name"] != ".github" and r["name"] not in students]


def _desc(repo: dict) -> str:
    """A repo's own GitHub description, safe to drop into a table cell."""
    return (repo.get("description") or "").replace("|", "\\|").strip()


def _rows(repos: list[dict], middle: Callable[[dict], str]) -> str:
    """Clickable table body for `repos`, in the order given.

    `middle` fills the cell between each repo's link and its own GitHub description - the
    two pages want different things there, and the description is the same in both."""
    rows = [f"| [{r['name']}]({r['url']}) | {middle(r)} | {_desc(r)} |" for r in repos]
    return "\n".join(rows) or "| _(no repos yet)_ | | |"


def _repo_table(repos: list[dict]) -> str:
    """The COURSE org's repo table: GitHub visibility, `join` first then alphabetical.

    Faculty read this one, and they are the people for whom "private" is the whole answer
    - they can see every repo in the org, and what they want to know is which of them
    students could reach if a release went out."""
    visible = sorted(
        _visible_repos(repos),
        key=lambda r: (r["name"].lower() != JOIN_REPO, r["name"].lower()),
    )
    return _rows(visible, lambda r: r["visibility"].lower())


# What each repo a semester org seeds gets in the students' table: where it sorts, and who
# can actually OPEN it. The AUDIENCE, not GitHub's `visibility`: "private" is the answer
# to a question nobody landing here is asking, and it tells a student nothing about
# whether enrolling will let them in - which is what this page exists to say. Order runs
# students-first: the way in, then the content, then what instructors configure, then the
# generated site.
_SEMESTER_ROWS = {
    JOIN_REPO: (0, "public (students join here)"),
    "materials": (1, "enrolled students & auditors only"),
    CONFIG_REPO: (3, "instructor-only"),
}
# A repo we did not seed - a second released-content repo, or something an instructor made
# - sorts with the content and claims no grant we did not make: it gets its bare
# visibility rather than a guess at who was given read.
_UNSEEDED_RANK = 2
# The generated site, last and public. Matched on the suffix, not on `<org>.github.io`:
# renaming an org leaves the site repo under the old name (see site_repo), and the row
# still has to say "do not touch".
_SITE_ROW = (4, "public")


def _semester_row(repo: dict) -> tuple[int, str]:
    """`(rank, audience)` for one row of the semester table."""
    if repo["name"].endswith(".github.io"):
        return _SITE_ROW
    return _SEMESTER_ROWS.get(
        repo["name"], (_UNSEEDED_RANK, repo["visibility"].lower())
    )


def _semester_repo_table(repos: list[dict]) -> str:
    """The SEMESTER org's repo table - who can see each repo, students' repos first."""
    visible = sorted(
        _visible_repos(repos), key=lambda r: (_semester_row(r)[0], r["name"].lower())
    )
    return _rows(visible, lambda r: _semester_row(r)[1])


# The one generated region of the otherwise instructor-owned SEMESTER landing page. Matched
# on these bare sentinels, not on the whole comment, so the prose inside the opening
# marker can be reworded later without orphaning every page already deployed.
TABLE_START = "<!-- dsl:repo-table:start"
TABLE_END = "<!-- dsl:repo-table:end -->"


def _repo_table_block(repos: list[dict]) -> str:
    """The repo table wrapped in its markers - regenerated whole on every refresh."""
    return (
        f"{TABLE_START} - AUTO-GENERATED from this org's live repo list.\n"
        "     Edits between these markers are overwritten on the next refresh. The\n"
        "     \"What it's for\" column is each repo's own GitHub description - to change\n"
        '     what a row says, edit that. "Who can see it" is derived from the repo,\n'
        "     and is the audience rather than GitHub's public/private. -->\n"
        "| Repo | Who can see it | What it's for |\n"
        "| --- | --- | --- |\n"
        f"{_semester_repo_table(repos)}\n"
        f"{TABLE_END}"
    )


# Pages seeded before the course name existed open on `# <org>.`, and every self-reference
# below it - the welcome line, the site link, the Join link - names that same org. So the
# heading is where a rename shows up first. Today's seed opens on `# <course_name>`, which
# defaults to an org slug, so the heading alone is only a candidate: see
# `retitle_renamed_org`.
_H1_ORG_RE = re.compile(r"^#\s+([A-Za-z0-9][\w.-]*?)\.?\s*$", re.MULTILINE)


def _whole_name(name: str) -> str:
    """A pattern for `name` as a whole org name: not part of a longer one. `\\b` is not
    enough, because `-` is a word boundary, so `hertie-dl` would match inside
    `hertie-dl-f2026`."""
    return rf"(?<![\w-]){re.escape(name)}(?![\w-])"


def retitle_renamed_org(existing: str, org: str) -> tuple[str, str | None]:
    """`(page, old name)` with a renamed org's former name replaced throughout, or the page
    unchanged and None.

    Renaming an org leaves every self-reference in its own profile page pointing at a name
    that no longer resolves - including the Join link, so a student cannot enrol. The prose
    is instructor-owned and the refresh deliberately leaves it alone (see
    `_semester_profile_body`), which is right for wording someone improved and wrong here: an
    org name that is not this org's is not a stylistic choice, it is a stale fact. That is a
    narrower signal than "the page still looks generated", which is the heuristic that
    function's docstring rejects - it would flatten reworded prose, and this cannot.

    Keyed on the H1, which older seeds set to the org's own name. A page whose heading an
    instructor has replaced with a human title matches nothing and is left alone, as is one
    whose heading already names this org. The H1 is only a candidate: today's seed opens on
    the course name, which defaults to an org slug (`hertie-dl` for the semester org
    `hertie-dl-f2026`). So the page must also LINK to the candidate as an org - a
    `github.com/<name>` path segment or a `<name>.github.io` host - before anything is
    rewritten. Whole-name substitution, so a name that is a prefix of another (`hertie-dl`
    inside `hertie-dl-f2026`) is never corrupted."""
    m = _H1_ORG_RE.search(existing)
    if m is None:
        return existing, None
    was = m.group(1)
    if was.casefold() == org.casefold() or "-" not in was:
        return existing, None
    name = _whole_name(was)
    linked = rf"github\.com/{name}|//{name}\.github\.io"
    if not re.search(linked, existing, re.IGNORECASE):
        return existing, None
    return re.sub(name, org, existing, flags=re.IGNORECASE), was


def splice_repo_table(existing: str, repos: list[dict]) -> str | None:
    """Refresh only the marked repo table in `existing`, leaving the rest of the page alone.

    "The rest of the page" is everything outside the markers, to the byte: the read
    decodes the API's base64 in Python (`gh_contents._decoded`), so a page's leading and
    trailing blank lines survive the round trip.

    None when the markers aren't both there in order - which is the signal to leave the
    page entirely alone rather than guess where the table belongs. An instructor who
    deletes the markers has said, unambiguously, that they want the whole page."""
    start = existing.find(TABLE_START)
    end = existing.find(TABLE_END)
    if start == -1 or end == -1 or end < start:
        return None
    return (
        existing[:start] + _repo_table_block(repos) + existing[end + len(TABLE_END) :]
    )


def cli_command(op: Operation, org: str) -> str:
    """The laptop command for `op` in this course org (decision 0041, `dsl_course.cli`):
    its required arguments spelt as placeholders, previewing as every CLI does."""
    parts = ["python -m dsl_course", op.name, "--course-org", org]
    if op.scope == SEMESTER:
        parts += ["--semester-org", "<semester-org>"]
    for key in op.args_schema.get("required", []):
        parts += ["--arg", f"{key}=<{key}>"]
    return " ".join(parts)


def _ops_table(org: str) -> str:
    """One row per registry op: what it does, the Console, and the CLI command."""
    url = policy.load()["institution"].get("console_url") or ""
    console = f"[Console]({url})" if url else "Console"
    return "\n".join(
        f"| `{op.name}` | {op.help} | {console} | `{cli_command(op, org)}` |"
        for op in REGISTRY.values()
    )


def render_dotgithub_readme(org: str, course_name: str, is_semester: bool) -> str:
    """The `.github` repo's OWN README - the orientation a faculty & instructors member sees on landing
    in this repo just after bootstrap. Distinct from profile/README.md (the org landing
    page); this shows on the repo itself, next to the Actions tab where the workflows live."""
    if is_semester:
        return f"""<!-- SYSTEM-OWNED - do not edit, edits here are overwritten on the next refresh. -->

# {course_name} - semester control repo

This is the **`.github` repo** for the `{org}` semester org. **Students and instructors rarely need to touch anything in this repo directly.**

_Instructors (TAs and faculty assistants included): nothing to run from here - every operation is in the Instructor Console, or `python -m dsl_course` from a laptop, against the parent **course org**._

Built and kept in sync by the [DSL teaching toolkit](https://github.com/{CENTRAL}).
"""
    return f"""<!-- SYSTEM-OWNED - do not edit, edits here are overwritten on the next refresh. -->

# {course_name} - course control panel

This is the **`.github` repo** for the `{org}` course org - the primary control panel faculty & instructors use
to run and configure the course.

## Run an operation

Every operation is a button in the **Instructor Console** and a command, `python -m dsl_course <op>`, from a laptop. Either needs you to be (1) in this org's `course-admin` team (declared here, course-wide), or (2) in a semester's `instructors` team (declared in that semester's own `semester-config/semester.yml`). The full list, with each command, is on the **[org home page](https://github.com/{org})**. The [Actions tab](https://github.com/{org}/.github/actions) holds the automatic workflows those operations and the schedule run.

## Typical flow

1. **New course materials** / **New assignment** - scaffold your content repos, then fill them in.
   Write the answer once on the `solution` branch and let **Derive student version** write `main`'s starter from it.
2. Create an empty **semester org** for the year, add the bot as an Owner, then **New semester** sets it up.
3. Each session: pre-schedule releases and hand-outs in `schedule.yml` (recommended), or **Release** / **Hand out** now.
4. Grading: the sheet appears at handout -> **Collect submissions** (or wait for the cron) -> type the marks -> **Return marks** (preview first).
5. End of term: **Archive** - freezes the year's student repos and seals the semester's private record. Nothing is deleted.

## What's in here

- `.github/workflows/` - the workflows. SYSTEM-OWNED: do not edit or delete them.
- `{COURSE_CONFIG}` - this course's identity (name/code) and the registry of `course_admins`, who persist across years. INSTRUCTOR-OWNED. (Per-semester instructors/TAs and the schedule are declared in the semester org - not here).
- `profile/README.md` - the public org landing page (an auto-generated repo index). SYSTEM-OWNED: do not edit it.

Built and kept in sync by the [DSL teaching toolkit](https://github.com/{CENTRAL}).
"""


def render_profile_readme(
    org: str,
    course_name: str,
    repos: list[dict],
    is_semester: bool,
    semesters: list[str] | None = None,
    *,
    central_ref: str,
    setting_up: frozenset[str] = frozenset(),
) -> str:
    """Org overview. Semester orgs get a student-facing page; course orgs a faculty & instructors one.

    `central_ref` is the ref of the central toolkit this org runs (discovery.central_ref_for);
    the faculty page links into the docs at it, so an org on `main` reads the trunk's docs
    rather than a runbook for engine code it is not running."""
    if is_semester:
        return f"""<!-- INSTRUCTOR-OWNED - this is the page students land on, so it is yours to word,
     and write it for THEM rather than for staff. It is seeded ONCE and your edits
     survive the nightly refresh. Two exceptions: the repo table below, between the
     dsl:repo-table markers, is regenerated from this org's live repo list; and if this
     org is renamed, its former name is replaced throughout, so the Join link below
     keeps resolving. -->

# {course_name}

Welcome! This is the course organisation for **{course_name}**.

## Course website

**[{course_name} - course website](https://{org.lower()}.github.io/)** - schedule,
lectures, assignments, and the instructors. This is the recommended way to navigate
this organisation once enrolled.

## Getting started

1. Open a **Join course** issue in
   [`{JOIN_REPO}`]({join_issue_url(org)}) to enrol - your
   GitHub handle is captured automatically.
2. Once you're enrolled, course materials open up here session by session, and your own
   assignment repositories appear in this org. Everything is automatically deployed to
   and updated on the live website.

## Working with the materials

Materials are read-only for you. Fork to work in your own copy and to propose a fix by
pull request; a pull request's diff is visible to the whole semester, so keep your answers
out of it. Copy a notebook before working in it. To keep your work if you ever leave the
org, push it to a repo of your own.

If you find a mistake or a typo in the materials, open a pull request; if something needs
clarifying, open an issue.

## Where things are

{_repo_table_block(repos)}

---
_Hertie Data Science Lab._
"""
    table = _repo_table(repos)
    semester_lines = (
        "\n".join(
            f"- {c} _(being set up)_"
            if c in setting_up
            else f"- [{c}](https://github.com/{c})"
            for c in (semesters or [])
        )
        or "_(none registered yet - set one up with New semester in the Console)_"
    )
    return f"""# {course_name} Course

>_This page is auto-generated - do not edit; manual edits are overwritten on the next refresh._

This is the dedicated **{course_name}** **course org** - persistent across years. It acts as:
1. A **private staging area** for pre-release version-controlled materials & assignments,
2. A **historical record** of past years' materials,
3. A **central control panel** for instructors to run workflows from, via the seeded [`.github` Actions tab](https://github.com/{org}/.github/actions).

The substantive repos of this org are private (not accessible to enrolled students); each year's student-facing interface lives in a separate **semester org** that receives releases from here.

> **Faculty & instructors - start here:** New to the platform?
> Follow the step-by-step
> **[workflow runbooks](https://github.com/{CENTRAL}/blob/{central_ref}/docs/README.md)**.
> The sections below are a live index of this org's semesters, repositories, and actions.

## Semesters

List of semester orgs registered to receive releases from this course org. _Auto-discovered from the
`semesters.yml` registry_:

{semester_lines}

## Repositories

List of all repositories associated with the course org. _Auto-discovered from the org's live repositories_.

| Repo | Visibility | Description |
| --- | --- | --- |
{table}

Edit & stage new course-related content in these, then release it to the relevant semester org.

## What you can do

Every operation below is a button in the Instructor Console and the same request from a
laptop: `python -m dsl_course <op>` in a checkout of the
[toolkit](https://github.com/{CENTRAL}), with `gh` signed in. A command previews unless
given `--no-preview`; `python -m dsl_course <op> --help` lists its `--arg`s.

| Operation | What it does | Console | Command |
| --- | --- | --- | --- |
{_ops_table(org)}

## Workflows in this repo

The [`.github` Actions tab](https://github.com/{org}/.github/actions) runs these
_(automatically bootstrapped from the central
[dsl-teaching-toolkit repo](https://github.com/{CENTRAL}))_:

| Workflow | What it does | Managed |
| --- | --- | --- |
| [**Collect submissions**](https://github.com/{org}/.github/actions/workflows/collect-submissions.yml) | Refresh an assignment's grading sheet now instead of waiting for the cron. The Console's *Collect now* runs it: grading runs student code in this workflow's own sandboxed job. | Console |
| [**Distribute grades**](https://github.com/{org}/.github/actions/workflows/distribute-grades.yml) | Send what the grading sheet holds: each student's private `grades-<handle>` repo, the semester grades file, and an email. The Console's *Return marks* and the scheduler both run it, in one queue. | Console |
| [**Send enrolment codes**](https://github.com/{org}/.github/actions/workflows/send-codes.yml) | Emails each student their enrolment code. There is no button: a push to a semester's `students.csv` fires it, and it sends for real. | Automatic |
| [**Sync membership**](https://github.com/{org}/.github/actions/workflows/sync-membership.yml) | Reconciles teams and access from `students.csv`, `teams.csv`, this org's `people:` block and each semester's `semester.yml`. Runs on a push to any of them and daily; a manual run walks every semester. | Auto-handled |
| [**Sync site**](https://github.com/{org}/.github/actions/workflows/sync-site.yml) | Regenerates every semester's website from the org structure. Releases and schedule pushes already do; a manual run walks every semester. | Auto-handled |
| [**Publish course website**](https://github.com/{org}/.github/actions/workflows/publish-site.yml) | **[OPTIONAL]** Builds the public open-courseware site `{org}.github.io` as `.github/opencourse.yml` says (off by default); a daily cron re-syncs it once on. | Auto-handled |
| [**Refresh actions**](https://github.com/{org}/.github/actions/workflows/refresh-actions.yml) | Repopulates the dropdowns, re-equips content repos, and rebuilds this index. Runs itself nightly. | Auto-handled |
| [**Scheduled release**](https://github.com/{org}/.github/actions/workflows/scheduled-release.yml) | Auto-releases whatever each semester's `schedule.yml` says is now due, about every 15 minutes. Manual runs default to a preview. | Auto-handled |
| [**Console**](https://github.com/{org}/.github/actions/workflows/console.yml) | Runs what the Instructor Console asks for. Not for pressing by hand: the Console fills in the request and follows the run. | Auto-handled |

---


## Repository structure (required)

```
{org}/                            <- this COURSE org (persistent)
|-- .github/                      profile + faculty & instructor workflows (see Actions tab) + semester registry
|-- course-materials-<year>/      lectures/01_.../   readings/01_.../   (+ syllabus, README)
`-- assignment-<name>/            is_template repo (topic dsl-assignment):
                                    main      -> starter + autograder   (students get this)
                                    solution  -> solution/   (pushed to students on demand)

<Course>-f<year>/                 <- one SEMESTER org per year (New semester sets it up)
|-- join/                         Join issue -> onboard (enrol)
|-- semester-config/             students.csv  (private roster)
|-- materials/                    released lectures/readings  (students-team read)
|-- <org>.github.io/              auto-deployed website (synced from this structure)
`-- <assignment>-<handle>/        one private repo per student (default; see submit_via)
```

This whole structure is fully bootstrapped from the central [`dsl-teaching-toolkit`](https://github.com/{CENTRAL}) repo (via its **Bootstrap Course Org** action), and the actions above run that same central code.

The course-level actions assume this layout - use **New course materials** / **New assignment** above to scaffold correctly. These scaffolds are designed to be generic & non-prescriptive, however if these formats to not suit your intended course delivery structure, please contact the DSL (`h.baker@hertie-school.org`).

### Materials repo

(`course-materials-<year>`) - the source a release copies from. Any path in it can be released. The convention below is what the downstream pipeline transformations expect: ordinal-prefixed (`01_`, `02_`, `03_`, ...) sub-directories, which is the only constraint. The following are seeded automatically, but edit them as you wish:
- `lectures/01_.../` - one folder per session's lecture files;
- `readings/01_.../` - one folder per session's readings;
- root files (`SYLLABUS.md`, `README.md`).

Add more sections freely (e.g. `labs/01_.../`, `datasets/01_.../`).

> Alternatively you could have `sessions/01_.../` with lectures & readings combined - or however you prefer to set it up. The only constraint is the ordinal-prefixed subdirectories.

### Assignment repo

`assignment-<name>` (an `is_template` repo) - the source a hand-out copies from, once a semester's schedule names it:
- **`main` branch** - the starter code only (no tests, no autograder). This is exactly what students receive (native template-generate copies `main` only).
- **`solution` branch** - the model solution (`solution/`), plus **`grading_config.yml`** and the **hidden tests** the autograder runs faculty-side at the cutoff. **All of this MUST live on this branch, never on `main`** - that is what guarantees it is never copied into student repos on generate. Only the `solution/` folder reaches students, and only when its `solution_datetime` passes, or you hand it out with **solution_datetime** `now` (a separate, later commit); the hidden tests and `grading_config.yml` never do.

## Further details on how the actions behave

**Releasing materials** (`release.entry` for a schedule entry, `release.adhoc` for anything
else) takes the same five fields as a `schedule.yml` `deploy:` entry: `semester_org`,
`course_source_repo`, `course_source_path`, `semester_dest_repo`, `semester_dest_path` - so
a release now and the scheduled release plan share one vocabulary. `course_source_path` is any folder or file (`lectures/03_regression`,
`mlpkg/simulation`, `SYLLABUS.md`); a folder is copied whole, **every file** in it.
`course_source_path` and `semester_dest_path` accept comma-separated lists paired in order, so
one release can carry several paths at once; a blank `semester_dest_path` mirrors each source
path. `semester_dest_repo` (default `materials`) is created on demand, private, with
`students` **and** `auditors` read. Copies are additive and idempotent: only what you have
released appears, and re-releasing changes nothing.

**Handing out an assignment** (`assignment.handout_now`) - two stages: (1) it freezes a semester-level template repo
`<assignment>` from your `assignment-*-<year>` template; (2) it generates one private
`<assignment>-<handle>` repo per onboarded student **from that semester template**, adding
each as collaborator. After the assignment deadline, hand it out again with **solution_datetime** `now` to push the
template's `solution` branch into every student repo. Solutions stay on the `solution`
branch so a normal release never leaks them. A template's `submit_via` can change stage
(2) instead: `external` creates no repo at all, and `shared_dropbox_repo` creates one
`<assignment>-submissions` drop box for the whole semester rather than one repo each.

**The semester website** - every semester has an auto-deployed site `<org>.github.io`. It is regenerated
on every release (and via **Sync site**). Its lecture links point at the semester's private repos, so
they only resolve for enrolled members (deliberate).

**The public course website** (optional) - `Publish course website` builds `{org}.github.io`, a public
open-courseware site for the course as a whole. Unlike the semester sites it **hosts** the shared lecture
files (the source repos are private, so links would 404); readings are published either as a text-only
reading list or as hosted files. It is off by default and driven by `.github/opencourse.yml` (the
console's Public website section) - releases and refresh never touch it - and once on, a daily cron
re-syncs it, so later materials edits reach it on their own.

---
Maintained by the [Hertie Data Science Lab](https://github.com/hertie-data-science-lab).
"""


def profile_files(
    org: str,
    course_name: str | None = None,
    *,
    central_ref: str,
    repos: list[dict] | None = None,
) -> dict[str, bytes]:
    """The org's `.github` READMEs exactly as `update_profile_readme` writes them, without
    writing: `README.md` always, `profile/README.md` unless an instructor's page is left
    as it is (see `_semester_profile_body`)."""
    if course_name is None:
        # Guarded load: absent (None) is normal - a semester org has no course.yml of its
        # own. A MALFORMED config raises here (with a clear, logged message) rather than
        # the bare `yaml.safe_load` traceback that used to surface from mid-refresh - a
        # non-mapping is likewise refused, not coerced to {}.
        cfg = org_meta(org)
        # A SEMESTER org has no course.yml of its own (its pointer lives in its config
        # repo), so this used to fall all the way back to the org slug and title the
        # students' landing page "hertie-dsl-demo-f2026". Follow the pointer to the course
        # org that does hold the name; the slug stays as the last resort.
        course_name = (
            course_block(cfg).get("name")
            or (course_name_for_semester(org) if not cfg else "")
            or org
        )
    if repos is None:
        repos = list_org_repos(org)
    # `tier` is None for an org the listing cannot place (a legacy semester with no topics
    # and no `join`); the page renders it as a course org, as before.
    is_semester = org_tier(repos) == "semester"
    semesters = None if is_semester else discover_semesters(org)
    body = render_profile_readme(
        org,
        course_name,
        repos,
        is_semester,
        semesters,
        central_ref=central_ref,
        setting_up=frozenset(s for s in semesters or [] if being_set_up(s)),
    )
    if is_semester:
        body = _semester_profile_body(org, repos, body)
    files = {
        "README.md": render_dotgithub_readme(org, course_name, is_semester).encode()
    }
    if body is not None:
        files["profile/README.md"] = body.encode()
    return files


def update_profile_readme(
    org: str,
    course_name: str | None = None,
    *,
    central_ref: str,
    repos: list[dict] | None = None,
) -> int:
    """(Re)generate the org's profile/README.md from its metadata + live repo list
    (`profile_files`).

    A semester org (one with a `join` repo) gets a student-facing page; a course org
    gets the faculty-facing one.

    `repos` is the caller's listing when it already holds one (seed.refresh does, and has
    just swept it), so an org's nightly run pays for `list_org_repos` once rather than
    once per consumer. Fetched here when it is not given.

    Returns the number of failed writes (0 or 1), so the nightly refresh can count it:
    the commit's return used to be discarded under an unconditional "refreshed" line, and
    a whole org whose landing pages never converged reported success every night."""
    files = profile_files(org, course_name, central_ref=central_ref, repos=repos)
    # Both are rendered from the same org snapshot and move together, so they belong in one
    # commit - kept separate from the workflow refresh's commit, because `docs:` vs `ci:` is
    # the one distinction in this history worth reading.
    if not put_files(
        org, ".github", files, "docs: refresh org READMEs (profile + .github)"
    ):
        log_err(f"could not write {org}/.github READMEs")
        return 1
    log_ok(
        "profile + .github READMEs refreshed"
        if "profile/README.md" in files
        else ".github README refreshed (landing page left as the instructor has it)"
    )
    return 0


def _semester_profile_body(org: str, repos: list[dict], seeded: str) -> str | None:
    """What to write to a SEMESTER org's profile/README.md - or None to write nothing.

    The page is the students' front door and instructor-owned, so a refresh must not
    flatten wording an instructor has since improved. Three cases:

    - ABSENT -> seed the full page (markers included).
    - present WITH markers -> refresh only the table between them; the prose is theirs.
    - present WITHOUT markers -> leave it completely alone, and say so. An instructor who
      has no markers either wrote the page before they existed or deleted them, and there
      is no way to tell those apart from the bytes: the page is treated as wholly theirs
      rather than destroyed to install machinery. To hand a page back to the generator,
      delete it - the next refresh reseeds it, markers and all.

    The orgs that predated the markers were migrated once, by hand, after confirming each
    page was still byte-identical to what the generator produced. That is deliberately NOT
    a rule in here: keying it on a leftover "auto-generated" footer would have flattened
    any page whose prose an instructor had reworded while leaving the footer in place,
    which nothing ever told them was load-bearing."""
    existing = get_file_content(org, ".github", "profile/README.md")
    if existing is None:
        return seeded
    # Before anything else, and whether or not the markers are there: a page naming an org
    # this is not is stale by construction, and its Join link is a dead end for students.
    existing, renamed_from = retitle_renamed_org(existing, org)
    if renamed_from:
        log_ok(f"profile/README.md: {renamed_from} -> {org} (org renamed)")
    spliced = splice_repo_table(existing, repos)
    if spliced is not None:
        return spliced
    log(
        f"  ({org}/.github/profile/README.md has no dsl:repo-table markers - left as it "
        "is. Paste them back around the repo table to resume refreshing it.)"
    )
    # Except a rename: the page is theirs, but the old org's name in it is not a wording
    # choice, and a Join link that 404s costs a student their enrolment.
    return existing if renamed_from else None
