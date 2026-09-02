"""Generate an org's landing pages from its live contents.

Two documents per org:

- `profile/README.md` - the org landing page. A cohort org gets a student-facing page
  (how to enrol, where the materials are); a course org gets the faculty-facing index of
  cohorts, repos, and every action workflow.

  The COURSE page is wholly generated - it is a live index of actions and cohorts, and
  is read by faculty who can go and change the things it indexes. The COHORT page is
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

from .central import CENTRAL
from .course import COURSE_CONFIG
from .discovery import (
    course_name_of,
    discover_cohorts,
    list_org_repos,
    org_meta,
    org_tier,
    student_repo_names,
)
from .gh_contents import get_file_content, put_files
from .log import log, log_err, log_ok


def _visible_repos(repos: list[dict]) -> list[dict]:
    """The repos a landing page names: everything bar `.github` and per-person machinery.

    Machinery is dropped, not just `.github`: a cohort org holds one submission repo per
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
    """The COURSE org's repo table: GitHub visibility, `welcome` first then alphabetical.

    Faculty read this one, and they are the people for whom "private" is the whole answer
    - they can see every repo in the org, and what they want to know is which of them
    students could reach if a release went out."""
    visible = sorted(
        _visible_repos(repos),
        key=lambda r: (r["name"].lower() != "welcome", r["name"].lower()),
    )
    return _rows(visible, lambda r: r["visibility"].lower())


# What each repo a cohort org seeds gets in the students' table: where it sorts, and who
# can actually OPEN it. The AUDIENCE, not GitHub's `visibility`: "private" is the answer
# to a question nobody landing here is asking, and it tells a student nothing about
# whether enrolling will let them in - which is what this page exists to say. Order runs
# students-first: the way in, then the content, then what instructors configure, then the
# generated site.
_COHORT_ROWS = {
    "welcome": (0, "public (students join here)"),
    "materials": (1, "enrolled students & auditors only"),
    "classroom-config": (3, "instructor-only"),
}
# A repo we did not seed - a second released-content repo, or something an instructor made
# - sorts with the content and claims no grant we did not make: it gets its bare
# visibility rather than a guess at who was given read.
_UNSEEDED_RANK = 2
# The generated site, last and public. Matched on the suffix, not on `<org>.github.io`:
# renaming an org leaves the site repo under the old name (see site_repo), and the row
# still has to say "do not touch".
_SITE_ROW = (4, "public")


def _cohort_row(repo: dict) -> tuple[int, str]:
    """`(rank, audience)` for one row of the cohort table."""
    if repo["name"].endswith(".github.io"):
        return _SITE_ROW
    return _COHORT_ROWS.get(repo["name"], (_UNSEEDED_RANK, repo["visibility"].lower()))


def _cohort_repo_table(repos: list[dict]) -> str:
    """The COHORT org's repo table - who can see each repo, students' repos first."""
    visible = sorted(
        _visible_repos(repos), key=lambda r: (_cohort_row(r)[0], r["name"].lower())
    )
    return _rows(visible, lambda r: _cohort_row(r)[1])


# The one generated region of the otherwise instructor-owned COHORT landing page. Matched
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
        f"{_cohort_repo_table(repos)}\n"
        f"{TABLE_END}"
    )


# The seeded page's first heading is `# <org>.`, and every self-reference below it - the
# welcome line, the site link, the Join link - names that same org. So the heading is where
# a rename shows up first, and it is enough to detect one.
_H1_ORG_RE = re.compile(r"^#\s+([A-Za-z0-9][\w.-]*?)\.?\s*$", re.MULTILINE)


def retitle_renamed_org(existing: str, org: str) -> tuple[str, str | None]:
    """`(page, old name)` with a renamed org's former name replaced throughout, or the page
    unchanged and None.

    Renaming an org leaves every self-reference in its own profile page pointing at a name
    that no longer resolves - including the Join link, so a student cannot enrol. The prose
    is instructor-owned and the refresh deliberately leaves it alone (see
    `_cohort_profile_body`), which is right for wording someone improved and wrong here: an
    org name that is not this org's is not a stylistic choice, it is a stale fact. That is a
    narrower signal than "the page still looks generated", which is the heuristic that
    function's docstring rejects - it would flatten reworded prose, and this cannot.

    Keyed on the H1, which the generator seeds as the org's own name. A page whose heading
    an instructor has replaced with a human title matches nothing and is left alone, as is
    one whose heading already names this org. Word-boundary substitution, so a name that is
    a prefix of another (`hertie-nlp-f2026` inside `hertie-nlp-f2026-archive`) is not
    corrupted."""
    m = _H1_ORG_RE.search(existing)
    if m is None:
        return existing, None
    was = m.group(1)
    if was.casefold() == org.casefold() or "-" not in was:
        return existing, None
    return re.sub(rf"\b{re.escape(was)}\b", org, existing), was


def splice_repo_table(existing: str, repos: list[dict]) -> str | None:
    """Refresh only the marked repo table in `existing`, leaving the rest of the page alone.

    "The rest of the page" is everything outside the markers, with one caveat worth naming:
    `get_file_content` returns `gh()`'s output, which is `.strip()`ed - so a page's leading
    and trailing blank lines do not survive the round trip. The prose itself is untouched.

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


def render_dotgithub_readme(org: str, course_name: str, is_cohort: bool) -> str:
    """The `.github` repo's OWN README - the orientation a faculty & instructors member sees on landing
    in this repo just after bootstrap. Distinct from profile/README.md (the org landing
    page); this shows on the repo itself, next to the Actions tab where the workflows live."""
    if is_cohort:
        return f"""<!-- SYSTEM-OWNED - do not edit, edits here are overwritten on the next refresh. -->

# {course_name} - cohort control repo

This is the **`.github` repo** for the `{org}` cohort org. **Students and instructors rarely need to touch anything in this repo directly.**

_Teaching staff (instructors, TAs, faculty assistants): your action buttons aren't here - they live in the parent **course org's** `.github` control panel, on its Actions tab._

Built and kept in sync by the [DSL teaching toolkit](https://github.com/{CENTRAL}).
"""
    return f"""<!-- SYSTEM-OWNED - do not edit, edits here are overwritten on the next refresh. -->

# {course_name} - course control panel

This is the **`.github` repo** for the `{org}` course org - the primary control panel faculty & instructors use
to run and configure the course.

## Run an action

Open the **[Actions tab](https://github.com/{org}/.github/actions)**, pick a workflow, and click **Run workflow**. Workflows only show if you have write access - i.e. you're either (1) in this org's `course-admin` team (declared here, course-wide), or (2) in a cohort's `instructors-<tag>` team (declared in that cohort's own `classroom-config/people.yml` then back-propagated). The full, annotated list of actions is on the **[org home page](https://github.com/{org})**.

## Typical flow

1. **New materials repo** / **New assignment** - scaffold your content repos, then fill them in.
2. Create an empty **cohort org** for the year, add the bot as an Owner, then run **Bootstrap cohort**.
3. Each session: **Release materials** / **Release assignment** - or pre-schedule them in `schedule.yml` (recommended).
4. Grading: **Grade assignment** -> **Sync gradebooks** -> **Render grades** -> **Distribute grades**.

## What's in here

- `.github/workflows/` - the workflows. SYSTEM-OWNED: do not edit or delete them.
- `{COURSE_CONFIG}` - this course's identity (name/code) and the registry of `course_admins`, who persist across years. INSTRUCTOR-OWNED. (Per-cohort instructors/TAs and the schedule are declared in the cohort org - not here).
- `profile/README.md` - the public org landing page (an auto-generated repo index). SYSTEM-OWNED: do not edit it.

Built and kept in sync by the [DSL teaching toolkit](https://github.com/{CENTRAL}).
"""


def render_profile_readme(
    org: str,
    org_name: str,
    course_name: str,
    repos: list[dict],
    is_cohort: bool,
    cohorts: list[str] | None = None,
    *,
    central_ref: str,
) -> str:
    """Org overview. Cohort orgs get a student-facing page; course orgs a faculty & instructors one.

    `central_ref` is the ref of the central toolkit this org runs (discovery.central_ref_for);
    the faculty page links into the docs at it, so an org on `staging` reads the staging docs
    rather than a runbook for engine code it is not running."""
    if is_cohort:
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
lectures, assignments, and the teaching team. This is the recommended way to navigate
this organisation once enrolled.

## Getting started

1. Open a **Join course** issue in
   [`welcome`](https://github.com/{org}/welcome/issues/new/choose) to enrol - your
   GitHub handle is captured automatically.
2. Once you're enrolled, course materials open up here session by session, and your own
   assignment repositories appear in this org. Everything is automatically deployed to
   and updated on the live website.

## Where things are

{_repo_table_block(repos)}

---
_Hertie Data Science Lab._
"""
    table = _repo_table(repos)
    cohort_lines = (
        "\n".join(f"- [{c}](https://github.com/{c})" for c in (cohorts or []))
        or "_(none registered yet - run Bootstrap cohort)_"
    )
    return f"""# {course_name} Course

>_This page is auto-generated - do not edit; manual edits are overwritten on the next refresh._

This is the dedicated **{course_name}** **course org** - persistent across years. It acts as:
1. A **private staging area** for pre-release version-controlled materials & assignments,
2. A **historical record** of past years' materials,
3. A **central control panel** for instructors to run workflows from, via the seeded [`.github` Actions tab](https://github.com/{org}/.github/actions).

The substantive repos of this org are private (not accessible to enrolled students); each year's student-facing interface lives in a separate **cohort org** that receives releases from here.

> **Faculty & instructors - start here:** New to the platform?
> Follow the step-by-step
> **[workflow runbooks](https://github.com/{CENTRAL}/blob/{central_ref}/docs/README.md)**.
> The sections below are a live index of this org's cohorts, repositories, and actions.

## Cohorts

List of cohort orgs registered to receive releases from this course org. _Auto-discovered from the
`cohort-courses-pages.yml` registry_:

{cohort_lines}

## Repositories

List of all repositories associated with the course org. _Auto-discovered from the org's live repositories_.

| Repo | Visibility | Description |
| --- | --- | --- |
{table}

Edit & stage new course-related content in these, then release it to the relevant cohort org.

## Available actions for faculty, instructors & admin

All actions live in the [`.github` repo's Actions tab](https://github.com/{org}/.github/actions)
_(automatically bootstrapped from the central
[dsl-teaching-toolkit repo](https://github.com/{CENTRAL}))_:

### Run directly by you (course instructors):

| Action | What it does | Managed |
| --- | --- | --- |
| [**Bootstrap cohort**](https://github.com/{org}/.github/actions/workflows/bootstrap-cohort.yml) | Configures a freshly-created cohort org (sets up scaffold repos, registers it with the course org, seeds workflow functionality). | Run by instructor |
| [**Send enrolment codes**](https://github.com/{org}/.github/actions/workflows/send-codes.yml) | Generates enrolment codes for each student and emails each their code (to their Hertie email address). Students paste the code into the welcome Join course issue. This keeps personal data out of the public repo. There is no button: a push to a cohort's `students.csv` is what fires it, and it sends for real - so a re-send means clearing that row's `code_sent_at` and pushing again. | Automatic |
| [**New materials repo**](https://github.com/{org}/.github/actions/workflows/new-materials.yml) | Scaffolds a correctly-structured `course-materials-<year>` repo (session folders + the Release workflows). Ready for material to be added. | Run by instructor |
| [**New assignment**](https://github.com/{org}/.github/actions/workflows/new-assignment.yml) | Scaffolds an `assignment-N-<year>` template repo (starter on `main`; the `solution` branch carries the model solution, `grading.yml`, and the hidden tests). | Run by instructor |
| [**Generate syllabus**](https://github.com/{org}/.github/actions/workflows/generate-syllabus.yml) | Writes the "Course sessions and readings" section of a syllabus - one block per session, with its title, learning objectives and reading list - from a cohort's `classroom-config/schedule.yml` and this repo's `readings/` folders. It lands in `SYLLABUS.sessions.md` beside your syllabus (never released to students) and never edits `SYLLABUS.md` itself. | Run by instructor |
| [**Check cohort setup**](https://github.com/{org}/.github/actions/workflows/check-cohort-setup.yml) | A per-cohort checklist of everything configured (identity, people, schedule + release plan, roster, teams, grades) with direct edit links for anything missing. Read-only. | Run by instructor |
| [**Publish course website**](https://github.com/{org}/.github/actions/workflows/publish-site.yml) | **[OPTIONAL]** **[DEFERRED]** Build/refresh a public openware site for the course `{org}.github.io`. This will share this course's lecture materials and (limited) readings with the open internet. Opt-in (the first run scaffolds the site); afterwards a daily cron re-syncs it from the settings that run chose, so later materials edits appear without another click. Pick a materials repo and choose for readings: `reading-list` (citations only) or `actual-readings` (also host the files). Because the materials repos are private, the site **hosts** the shared files itself. This is separate from each cohort's student-facing site. | Run by instructor |
| [**Release materials**](https://github.com/{org}/.github/actions/workflows/release-materials.yml) | Manually release materials to student-facing cohort orgs *(NB: it is recommended to instead use the [scheduling function](https://github.com/{CENTRAL}/blob/{central_ref}/docs/07-schedule-releases.md) for regular releases)*. Select path(s) for any folder or file, one or several at a time. | Run by instructor |
| [**Release assignment**](https://github.com/{org}/.github/actions/workflows/release-assignment.yml) | Generate one private repo per student from a chosen `assignment-*` template repo. *(NB: it is recommended to instead use the [scheduling function](https://github.com/{CENTRAL}/blob/{central_ref}/docs/07-schedule-releases.md) for regular releases)* | Run by instructor |
| [**Grade assignment**](https://github.com/{org}/.github/actions/workflows/grade-assignment.yml) | Faculty-side autograder: after the deadline, run the HIDDEN tests (from the template's `solution` branch) against each submission and record the machine score into `classroom-config/grades/<assignment>.csv`. Nothing is written to student repos; faculty & instructors then add manual marks. Optional per assignment (skipped if `grading.yml` sets `autograde: false`). | Run by instructor |
| [**Sync gradebooks**](https://github.com/{org}/.github/actions/workflows/sync-gradebooks.yml) | Ensure every onboarded student has a PRIVATE `grades-<handle>` repo (the single home for all their grades). | Run by instructor |
| [**Render grades (preview)**](https://github.com/{org}/.github/actions/workflows/render-grades.yml) | Build per-student `gradebook/<handle>.yml` from `classroom-config/grades/<assignment>.csv` and open ONE pull request. **That PR is the preview** - review every student's grades in the diff before sending. | Run by instructor |
| [**Distribute grades**](https://github.com/{org}/.github/actions/workflows/distribute-grades.yml) | After merging the preview PR, copy each student's gradebook into their private repo and (unless silenced) email each student a notification to their Hertie email address (needs the `GRAPH_*` secrets). | Run by instructor |

NB: alternatively each materials repo *also* carries its own **Release** workflows (run from inside the repo).

---

### Automatically handled within the pipeline as standard

The following are runnable by explicit ad hoc manual dispatch; course instructors can mostly ignore them:

| Action | What it does | Managed |
| --- | --- | --- |
| [**Sync membership**](https://github.com/{org}/.github/actions/workflows/sync-membership.yml) | Reconciles org + `students`-team access (from `students.csv`), project teams (from `teams.csv`), `course_admins` (from this org's declared `people:` block, mirrored into every cohort's own `course-admin` team), and each cohort's own `instructors`/`teaching_assistants` (from its `classroom-config/people.yml`, reconciled into that cohort's `instructors` team AND a course-org `instructors-<tag>` team).<br><br> Triggers on (1) push (editing any of those files takes effect immediately, including removals so that the file is the live truth) and (2) on a daily cron (catches a faculty entry's `start`/`end` rotation with no edit that day);`workflow_dispatch` is a manual escape hatch. | Auto-handled |
| [**Refresh actions**](https://github.com/{org}/.github/actions/workflows/refresh-actions.yml) | Repopulates the cohort/source-repo/assignment dropdowns, re-equips content repos, and rebuilds this index. Runs itself nightly, so this org stays in step with the central toolkit on its own. | Auto-handled |
| [**Scheduled release**](https://github.com/{org}/.github/actions/workflows/scheduled-release.yml) | Hourly cron that auto-releases whatever each cohort's `classroom-config/schedule.yml` `releases:` plan says is now due (honouring each entry's `event_datetime` / `deploy_datetime` to the hour). Manual runs default to a dry-run preview ("what opens when"). The manual workflows above still work for early/ad-hoc release. | Auto-handled |
| _[**Sync site**](https://github.com/{org}/.github/actions/workflows/sync-site.yml)_ | _Regenerate a cohort's website from the org structure (releases do this automatically; standard workflow has no need for manual sync)._ | Auto-handled |


## Repository structure (required)

```
{org}/                            <- this COURSE org (persistent)
|-- .github/                      profile + faculty & instructor workflows (see Actions tab) + cohort registry
|-- course-materials-<year>/      lectures/01_.../   readings/01_.../   (+ syllabus, README)
`-- assignment-<n>-<year>/        is_template repo:
                                    main      -> starter + autograder   (students get this)
                                    solution  -> solution/   (pushed to students on demand)

<Course>-f<year>/                 <- one COHORT org per year (Bootstrap cohort sets it up)
|-- welcome/                      Join issue -> onboard (enrol)
|-- classroom-config/             students.csv  (private roster)
|-- materials/                    released lectures/readings  (students-team read)
|-- <org>.github.io/              auto-deployed website (synced from this structure)
`-- <assignment>-<handle>/        one private repo per student
```

This whole structure is fully bootstrapped from the central [`dsl-teaching-toolkit`](https://github.com/{CENTRAL}) repo (via its **Bootstrap Course Org** action), and the actions above run that same central code.

The course-level actions assume this layout - use **New materials repo** / **New assignment** above to scaffold correctly. These scaffolds are designed to be generic & non-prescriptive, however if these formats to not suit your intended course delivery structure, please contact the DSL (`h.baker@hertie-school.org`).

### Materials repo

(`course-materials-<year>`) - the source for Release materials. Any path in it can be released. The convention below is what the downstream pipeline transformations expect: ordinal-prefixed (`01_`, `02_`, `03_`, ...) sub-directories, which is the only constraint. The following are seeded automatically, but edit them as you wish:
- `lectures/01_.../` - one folder per session's lecture files;
- `readings/01_.../` - one folder per session's readings;
- root files (`SYLLABUS.md`, `README.md`).

Add more sections freely (e.g. `labs/01_.../`, `datasets/01_.../`).

> Alternatively you could have `sessions/01_.../` with lectures & readings combined - or however you prefer to set it up. The only constraint is the ordinal-prefixed subdirectories.

### Assignment repo

`assignment-N-<year>` (an `is_template` repo) - the source for Release assignment:
- **`main` branch** - the starter code only (no tests, no autograder). This is exactly what students receive (native template-generate copies `main` only).
- **`solution` branch** - the model solution (`solution/`), plus **`grading.yml`** and the **hidden tests** that the Grade assignment workflow runs faculty-side. **All of this MUST live on this branch, never on `main`** - that is what guarantees it is never copied into student repos on generate. Only the `solution/` folder reaches students, and only when you run Release assignment with **include_solution** ticked (a separate, later commit); the hidden tests and `grading.yml` never do.

## Further details on how the actions behave

**Release materials** - run it from the materials repo (`course_source_repo` pre-filled with
that repo) or from the course org's central `.github` control panel (`course_source_repo` is
a dropdown). **Both** take the same five fields, which are exactly a `schedule.yml` `deploy:`
entry: `cohort_org`, `course_source_repo`, `course_source_path`, `cohort_dest_repo`,
`cohort_dest_path` - so the manual workflow and the scheduled release plan share one
vocabulary. `course_source_path` is any folder or file (`lectures/03_regression`,
`mlpkg/simulation`, `SYLLABUS.md`); a folder is copied whole, **every file** in it.
`course_source_path` and `cohort_dest_path` accept comma-separated lists paired in order, so
one click can release several paths at once; a blank `cohort_dest_path` mirrors each source
path. `cohort_dest_repo` (default `materials`) is created on demand, private, with
`students` **and** `auditors` read. Copies are additive and idempotent: only what you have
released appears, and re-releasing changes nothing.

**Release assignment** - two stages: (1) it freezes a cohort-level template repo
`<assignment>` from your `assignment-*-<year>` template; (2) it generates one private
`<assignment>-<handle>` repo per onboarded student **from that cohort template**, adding
each as collaborator. After the assignment deadline, rerun with **include_solution** to push the
template's `solution` branch into every student repo. Solutions stay on the `solution`
branch so a normal release never leaks them.

**The cohort website** - every cohort has an auto-deployed site `<org>.github.io`. It is regenerated
on every release (and via **Sync site**). Its lecture links point at the cohort's private repos, so
they only resolve for enrolled members (deliberate).

**The public course website** (optional) - `Publish course website` builds `{org}.github.io`, a public
open-courseware site for the course as a whole. Unlike the cohort sites it **hosts** the shared lecture
files (the source repos are private, so links would 404); readings are published either as a text-only
reading list or as hosted files. It is opt-in - releases and refresh never touch it, so a public site
only exists once you run the action - but after that first run a daily cron re-syncs it from the
settings you chose, so later materials edits reach it on their own.

---
Maintained by the [Hertie Data Science Lab](https://github.com/hertie-data-science-lab).
"""


def update_profile_readme(
    org: str,
    org_name: str | None = None,
    course_name: str | None = None,
    *,
    central_ref: str,
    repos: list[dict] | None = None,
) -> int:
    """(Re)generate the org's profile/README.md from its metadata + live repo list.

    A cohort org (one with a `welcome` repo) gets a student-facing page; a course org
    gets the faculty-facing one.

    `repos` is the caller's listing when it already holds one (seed.refresh does, and has
    just swept it), so an org's nightly run pays for `list_org_repos` once rather than
    once per consumer. Fetched here when it is not given.

    Returns the number of failed writes (0 or 1), so the nightly refresh can count it:
    the commit's return used to be discarded under an unconditional "refreshed" line, and
    a whole org whose landing pages never converged reported success every night."""
    if org_name is None or course_name is None:
        # Guarded load: absent (None) is normal - a cohort org has no dsl-course.yml of its
        # own, so fall back to the org name. A MALFORMED config raises here (with a clear,
        # logged message) rather than the bare `yaml.safe_load` traceback that used to
        # surface from mid-refresh - a non-mapping is likewise refused, not coerced to {}.
        cfg = org_meta(org)
        org_name = org_name or cfg.get("org_name") or org
        # A COHORT org's dsl-course.yml is only a pointer - it carries no course_name of
        # its own, so this used to fall all the way back to the org slug and title the
        # students' landing page "hertie-dsl-demo-f2026". Follow the pointer to the course
        # org that does hold the name; the slug stays as the last resort.
        # `cfg` is the cohort's own pointer, already read above - so resolve the second
        # hop directly rather than calling course_name_for_cohort, which would re-fetch
        # this same file. course_name_of("") returns "", so no guard is needed here.
        course_name = (
            course_name
            or cfg.get("course_name")
            or course_name_of(str(cfg.get("course") or ""))
            or org_name
        )
    if repos is None:
        repos = list_org_repos(org)
    # `tier` is None for an org the listing cannot place (a legacy cohort with no topics
    # and no `welcome`); the page renders it as a course org, as before.
    is_cohort = org_tier(repos) == "cohort"
    cohorts = None if is_cohort else discover_cohorts(org)
    body = render_profile_readme(
        org, org_name, course_name, repos, is_cohort, cohorts, central_ref=central_ref
    )
    if is_cohort:
        body = _cohort_profile_body(org, repos, body)
    files = {"README.md": render_dotgithub_readme(org, course_name, is_cohort).encode()}
    if body is not None:
        files["profile/README.md"] = body.encode()
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


def _cohort_profile_body(org: str, repos: list[dict], seeded: str) -> str | None:
    """What to write to a COHORT org's profile/README.md - or None to write nothing.

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
