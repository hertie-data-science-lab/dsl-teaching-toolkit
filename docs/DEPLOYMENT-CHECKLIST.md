# Deployment checklist

Full checklist for standing up working course + semester orgs: each step's workflow, inputs w/ copyable examples and outputs. 

Accompanies the e2e [worked example](../example-course/).

## Course setup (once)

| | Step | Org Level | Where | Input | Output |
|---|------|-------|-------|-------|--------|
| `[required]` | 1. Create the course org | course | GitHub [web UI](https://github.com/account/organizations/new) | name `<course-name>-<CODE>` (no year); invite **`hertie-dsl-bot`** as **Owner** (it joins by itself once the DSL team registers the course) | an empty org the bot can bootstrap |
| `[required]` | 2. Bootstrap | course | [central repo → Actions → **Bootstrap Course Org**](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/bootstrap-org.yml) | `org`, `course_name`, `course_code`; optional `admin` (your handle); `central_ref` defaults to `release` | the `.github` control panel with every workflow, the `course-admin` team, [`course.yml`](#courseyml), `DSL_BOT_TOKEN` set for you |
| `[required]` | 3. Materials | course | Console **New course materials** (`python -m dsl_course materials.create`), then `git push` | `semester` (e.g. `f2026`); then your content ([layout](#materials-repo)) | `course-materials-<semester>` |
| `[required]` | 4. Assignment(s) | course | Console **New assignment** (`python -m dsl_course assignment.create`), then `git push` | `name` + `formats` (one or more starters, the first runnable) + `type` (individual/group); brief + starter on `main`, optional autograding on `solution` ([layout](#assignment-template)) | one `assignment-<name>` template each |
| *(optional)* | 5. Course admins | course | edit [`course.yml`](#courseyml), commit to `main` ([05](05-manage-teaching-team.md)) | GitHub handles, optional `start`/`end` | admin on the course org + every semester, reconciled |
| `[required]` | 6. Refresh | course | course `.github` → **Refresh actions** | none | dropdowns populated, secrets on content repos and assignment templates |

> Enrolment-code + grade emails send as `datasciencelab@hertie-school.org` via a central Entra app, authenticated by certificate ([details](../docs-admin-arch/central-admin.md#email)). Live on every course org. Where it is not, enrolment codes still land in `.system/roster.csv` (to be emailed by hand) and grades still reach each student's repo; only the notification is skipped.

<a id="cohort-setup-per-year"></a>

## Semester setup (per year)

| | Step | Org Level | Where | Input | Output |
|---|------|-------|-------|-------|--------|
| `[required]` | 1. Create the semester org | semester | GitHub [web UI](https://github.com/account/organizations/new) | name `<course-name>-f/sYYYY`; invite **`hertie-dsl-bot`** as **Owner** and list the org in the course's `.github/semesters.yml` (the console does both); it joins by itself within 15 minutes | an empty org the bot can bootstrap |
| `[required]` | 2. Bootstrap | course → semester | Console **Set up** on the new semester (`python -m dsl_course semester.bootstrap`) | `semester_org` | `join` (Join course / Join team issues) + `semester-config` (all the files below), `students`/`auditors` teams, the semester site, semester registered with the scheduler |
| `[do this first]` | 3. The semester plan | semester | edit [`semester-config/schedule.yml`](#scheduleyml) | sessions, releases, due dates, exams | the scheduler runs the whole semester; site dates; grading deadlines |
| `[required]` | 4. Roster | semester | edit [`semester-config/students.csv`](#studentscsv) | registrar rows | the enrolment + provisioning source of truth |
| *(optional)* | 5. Instructors | semester | edit [`semester-config/semester.yml`](#semesteryml) ([05](05-manage-teaching-team.md)) | handles (+ card fields), optional `start`/`end` | push access for this semester's instructors/TAs + site cards; time-boxed if dated |
| `[required]` | 6. Enrol | *nothing to run* - the roster push does it | - | - | codes written to the roster + emailed within a minute; students join via the `join` **Join course** issue |
| *(optional)* | 7. Ad-hoc release | Console, per semester | **Release** / **Hand out** (`release.adhoc`, `release.entry`, `assignment.handout_now`) | see [08](08-release-materials-to-cohort.md)/[09](09-release-assignment-to-cohort.md) | anything out earlier/differently than the schedule says |
| *(optional)* | 8. Return marks | course workflows + [`grading_sheets/<slug>.yml`](#grading_sheetsslugyml) | the [grading runbook](10-grade-and-return-assignments.md) | your marks | private per-student gradebooks |
| *(optional)* | 9. Check the semester | Console, per semester | the semester's **Refresh**, or `python -m dsl_course semester.check` | `semester_org` | what's configured, what's missing, an edit link per gap |

> **Member privileges (once, and only if any assignment uses `assignment_repo_visibility: student_choice`).**
> Semester org → Settings → Member privileges: **Allow members to change repository
> visibilities** ON, **Allow members to delete or transfer repositories** OFF. Both are
> web-only - no API call sets them - so the toolkit reads them and the semester's
> *grading_config.yml* digest faults while either is wrong.

## Inputs by file

> NB: all these `semester-config/` files are kept in a private repo (PII stays there; not leaked publicly).

<a id="dsl-courseyml"></a>

### `course.yml`

Live example: [`example-course/course-org/course.yml`](../example-course/course-org/course.yml).

- Course org's `.github` repo - the course's one settings file.
- Bootstrap writes it; edit it as needed.

```yaml
course:
  name: Deep Learning             # every site's title
  code: E1234                     # shown beside it
  description: One paragraph - the sites' blurb
  # toolkit_version_ref: release  # absent = release
  # licence: CC BY 4.0            # one of the institution's licences; absent = its first
people:
  course_admins:
    - github_handle: "janedoe"    # admin on the course org + every semester
      email: "jane@example.org"   # optional, PUBLIC - where a fault in this file is mailed
    - github_handle: "visiting"
      start: "2026-09-01"         # optional - access auto-starts/lapses on these dates
      end: "2027-06-30"
defaults:                         # every key seeded; the comment shows the inherited value and its source
  # late_window_days: 10          (institution)
  max_team_size: 4                # overrides the institution's
open_course_site:                 # the public website
  enabled: false
  source_repo: course-materials-f2026
  readings_mode: reading-list     # reading-list | actual-readings | none
  include_lectures: true
  withhold: [.system/]            # .releaseignore syntax
  # contact: course@example.org   # unset = the institution's
set_aside: [C6]                   # optional - suggestions set aside; see below
```

`course_admins` is the **course-level** grant - declared once here, mirrored into every semester's
own `course-admin` team, and never re-declared per year. They are also the course leads on the
open course site. Deleting an entry, or an `end` date passing, revokes on the next sync. A
semester's instructors and TAs are declared in its own [`semester.yml`](#semesteryml). Runbook:
[05](05-manage-teaching-team.md).

An admin's `email` is optional: a fault in this file is mailed to it. This file is public,
so an address written here is public too; without one, the digest issue's @mention is the
only channel.

`defaults:` is the course layer of the cascade: `timezone`, `late_window_days`,
`late_penalty_per_day`, `max_team_size`, `assignment_repo_visibility`, `archive_grace_days`,
`materials_repo`, `marks_per_question` and `team_formation`. Each key resolves nearest first:
the assignment's `schedule.yml` entry, the semester's `semester.yml`, this block, then the
institution's `institution.yml`. A late line alone is a whole rule: the other half is then none.

`course.name` / `course.code` / `course.description` reach every semester website - a push
here re-syncs them all: [11](11-configure-cohort-site.md). The public website shows those
three plus `open_course_site.contact` and `course.licence`, from its next publish.

`set_aside` lists the suggestions nobody needs, on the course's and every semester's
Dashboard (the console writes it from the circle before each Suggestions line; Bring back
removes the id): setup steps `C4`-`C6`, the suggested to-dos (syllabus and its weekly plan,
withheld patterns reviewed, course description) and the semesters' suggested to-dos (site home
page, archive date, instructor emails). A needed item, an unknown id, or an item done anyway is
ignored. Unset, nothing is set aside.

<a id="teamscsv"></a>

### `students.csv`

Live example: [`example-course/semester-org/students.csv`](../example-course/semester-org/students.csv).

`semester-config/students.csv` - one row per student, straight from the registrar (seeded
header-only). Deleting a row off-boards that student on the next push.

```csv
email,name,role,team:4
j.doe@students.hertie-school.org,Jane Doe,,team-x
e.evans@students.hertie-school.org,Eve Evans,auditor,
```

| Column | Notes |
|--------|-------|
| `email`, `name` | `email` is the match key, and receives the enrolment code + grade notices |
| `role` | blank/`enrolled` = full participant; `auditor` = reads released materials, gets no assignments/grades, refused from teams |
| `team:<number>` | one per team assignment (its schedule `number`); a team = the rows sharing a value, blank = no team. The **Join team** form writes the cell; for `team_formation: assigned` you type it |

Add any other column you want (a registrar id, a lecture section, notes) - the engine ignores it and preserves it.

The engine's own columns live beside it in `semester-config/.system/roster.csv` (`email`,
`github_handle`, `github_id`, `enrol_code`, `code_sent_at`): written by **Send enrolment
codes** and onboarding, never by you, except a switched account's new `github_handle`
([06](06-enrol-students-to-cohort.md)).

<a id="peopleyml"></a>
<a id="instructorsyml"></a>

### `semester.yml`

Live example: [`example-course/semester-org/semester.yml`](../example-course/semester-org/semester.yml).

`semester-config/semester.yml` - this semester's people and defaults.

`people:` - this semester's instructors, one list. Grants the semester's `instructors`
team necessary access permissions at both the course- and semester-org levels, including push
from the course org into that year's content repos (`instructors-<semester>`), and supplies the
semester site's cards. `github_handle`, `role` (`instructor` or `teaching_assistant`) and
`email` are required on every entry; the rest are optional.

```yaml
people:
  - github_handle: "janedoe"     # required, with `role` and `email` - the rest is optional
    role: instructor             # instructor | teaching_assistant
    email: "jane@example.org"    # required, and private
    name: "Prof. Jane Doe"       # site card fields
    title: "Professor of ..."
    photo: "/_images/pp/jane.jpg"  # see "Instructor photos" below
    url: "https://.../jane"
    start: "2026-09-01"          # access auto-starts/lapses on these dates
    end: "2027-01-31"
  - github_handle: "anOther"
    role: teaching_assistant
    email: "another@example.org"
defaults:                        # as course.yml's, one layer nearer; every key seeded commented
  # max_team_size: 4             (course)
  late_window_days: 5            # overrides the course and the institution
```

**Instructor photos.** `photo` accepts either form:

| Form | Example | Use when |
|---|---|---|
| site-relative path | `/_images/pp/jane.jpg` | **the safe default.** Commit the image into this semester's site repo `<semester-org>.github.io` under `_images/pp/`. That directory is served (`include: ['_images']` in the site's `_config.yml`) and is not a synced collection, so no release, cron or **Sync site** run will ever overwrite it. |
| absolute URL | `https://github.com/janedoe.png` | the host allows hotlinking. GitHub avatars (`https://github.com/<handle>.png`) always do. |

Institutional profile sites often **don't** - `hertie-school.org`, for one, returns 403 to any
off-site request, so a headshot copied from a staff profile page renders as a broken image. If a
photo doesn't appear, check the URL with `curl -sI <url>` before suspecting the sync. A card with
no usable `photo` still renders, just without the image.

`start`/`end` are inclusive ISO dates, both optional (no `start` = active immediately, no `end` =
indefinite). Access is revoked automatically once `end` passes - a full reconcile runs every sync,
so a lapsed date prunes the member exactly as deleting the entry would. **An edit here dispatches
Sync membership on the push (immediate); a date rolling over with no edit waits for the daily
cron (~24h)** - run **Sync membership** by hand if you need it sooner. Runbook:
[05](05-manage-teaching-team.md).

### `grading_sheets/<slug>.yml`

Live example: [`example-course/semester-org/grading_sheets/assignment-1.yml`](../example-course/semester-org/grading_sheets/assignment-1.yml).

`semester-config/grading_sheets/<slug>.yml` - one per assignment, created at handout with
every row in it. Everything under `info:` is the toolkit's and refreshes until the cutoff;
everything else is yours and is never touched. Field-by-field reference:
[the grading runbook](10-grade-and-return-assignments.md).

```yaml
submissions:
  anna-adams:
    info:                    # toolkit-owned
      submitted: 2026-10-03T22:14+02:00
      days_late: 0
    score_individual: 38
    adjustment_individual:
    feedback_individual: |
      Great work.
    notes_not_shared_with_students:
```

### Materials repo

Live example: [`example-course/course-org/course-materials-f2026`](../example-course/course-org/course-materials-f2026).

`course-materials-<semester>` - private; students only ever see what you release. Any top-level
directory holding ordinal-prefixed subdirectories is a releasable section.

```
course-materials-f2026/
  lectures/01_intro/     any files - slides, notebooks, code
  readings/01_intro/
  labs/01_setup/         add your own sections freely
  SYLLABUS.md            optional - or any name (SYLLABUS.pdf, ...); released by
                         naming it as a `from_path`
```

### Assignment template

Live example: [`example-course/course-org/assignment-linear-regression`](../example-course/course-org/assignment-linear-regression).

`assignment-<name>` - a template repo with two branches. Student repos are generated from
`main` only. **New assignment**'s `formats` - a comma-separated list of
`ipynb`/`py`/`rmd`/`qmd`/`latex`, or `none` on its own - picks the starter stubs; `type`
(individual/group) is recorded in `grading_config.yml`, and handout and grading obey
`type: group` automatically. The autograder takes any format - it converts any `.ipynb`
the submission holds before running the hidden tests.

```
main branch      README.md (the brief) + starter.*      -> what students get
solution branch  solution/ + grading_config.yml + tests/       -> faculty-only; hidden tests
                                                            power the (optional) autograder
```

`grading_config.yml`, on the `solution` branch, is what the assignment is:
`title`, `type`, `submit_via`, `formats` (a list; the first is the runnable one), `questions`,
`autograde`, `completion_check`, `grader_pdf`, `tests`, `starter`. How a semester runs it -
team formation, team size, the late rule, `assignment_repo_visibility`, `submit_url` - is that semester's
[`schedule.yml`](07-schedule-releases.md#assignments); one of those keys written here refuses the whole template
(`RUN_SETTING_IN_TEMPLATE`). The button writes it from its five inputs; every key
is documented inline in the generated file and in
[Add an assignment](03-add-assignment-to-course.md). Two of them drive the cutoff:
`autograde: true` runs `tests/` (or `tests/run.sh`, in any language), and `completion_check`
(default: on when `formats:` starts with `ipynb`) executes the notebook and records whether it runs top to
bottom.

### `schedule.yml`

Live example: [`example-course/semester-org/schedule.yml`](../example-course/semester-org/schedule.yml).
Every field: [Schedule releases](07-schedule-releases.md).

`semester-config/schedule.yml` - the semester plan the scheduler runs and the dates that
drive the website and grading: one block per kind (`lectures:`, `labs:`, `readings:`,
`exams:`, `drop-ins:`, `events:`, `assets:`), `assignments:`, and the `semester:` block
(start, end, breaks, archive). Each block is a map keyed by an id you choose, never shown.

```yaml
lectures:
  lecture-2:
    session: 2                        # the unit: required on lectures and labs
    event_datetime: 2026-09-15T10:00  # or a bare date, `tbc`, or a list of slots
    files:
      - from_course_repo: course-materials-f2026
        from_path: lectures/02_intro  # -> materials/lectures/02_intro at 09:00
assignments:
  linear-regression:
    number: 1
    template: assignment-linear-regression
    due_datetime: 2026-10-13          # bare date = 23:59:59
```

#### Schedule row types

The semester site renders one merged, date-sorted schedule table; each row is colour-coded
by its kind, which is the block it is written in:

| Row type | Comes from |
|---|---|
| lecture, lab, readings, drop-in, exam | an entry of that block; each slot of an entry is a row |
| assignment | an `assignments:` entry - shown on **both** its handout date and its due date |
| other | an `events:` entry (clinic, guest lecture), and the `archive` block's date |
| semester | the `semester:` block's start and end; a break is a grey band |

`assets:` entries ship files and are never a row. Labels, colours and tabs come from the
institution's `institution.yml` `kinds:`.

**A malformed entry is dropped - and every drop is reported** in the run log, by the
Console's Check (`semester.check`), and by `--validate` exiting non-zero. A push to `schedule.yml` runs
**Validate schedule** in `semester-config`. Verify with
`python3 -m dsl_course.schedule --semester-org <SEMESTER> --validate`. Full account:
[Schedule releases -> Dropped entries](07-schedule-releases.md#dropped-entries).

**What happens at the grading deadline.** The scheduler freezes each submission repo's
commit into `semester-config/.system/snapshots/<slug>.csv` (write-once - delete it to re-freeze),
then examines it **once** - the hidden tests where the `<slug>-<semester>` template's
`grading_config.yml` says `autograde: true`, the completion check where it asks for one (the
`_graded.json` / `_skipped.json` record in `semester-config/.system/autograde/<slug>/` is the fired
marker - delete it, or the whole folder, to re-grade). All of this happens whether or not the
semester releases any materials.

## Token

One secret, `DSL_BOT_TOKEN`, runs every workflow: the bot's classic PAT (`repo` + `admin:org` +
`workflow`), set once centrally. Bootstrap and Refresh put it everywhere it is needed - you
never set it by hand.

## Known limits

- **Moodle** roster-in / grade-out is manual CSV until Hertie IT enables Web Services.
- **Pages are public** on the Free plan; access-controlled once on Campus/Enterprise.
