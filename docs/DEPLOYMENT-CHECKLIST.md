# Deployment checklist

Full checklist for standing up working course + cohort orgs: each step's workflow, inputs w/ copyable examples and outputs. 

Accompanies the e2e [worked example](../example-course/).

## Course setup (once)

| | Step | Org Level | Where | Input | Output |
|---|------|-------|-------|-------|--------|
| `[required]` | 1. Create the course org | course | GitHub [web UI](https://github.com/account/organizations/new) | name `<course-name>-<CODE>` (no year); invite **`hertie-dsl-bot`** as **Owner** (must accept) | an empty org the bot can bootstrap |
| `[required]` | 2. Bootstrap | course | [central repo → Actions → **Bootstrap Course Org**](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/bootstrap-org.yml) | `org`, `org_name`, `course_code`; optional `admin` (your handle); `central_ref` defaults to `release` | the `.github` control panel with every workflow, the `course-admin` team, [`dsl-course.yml`](#dsl-courseyml), `DSL_BOT_TOKEN` set for you |
| `[required]` | 3. Materials | course | course `.github` → **New materials repo**, then `git push` | `tag` (e.g. `f2026`); then your content ([layout](#materials-repo)) | `course-materials-<tag>` with run-from-repo Release workflows |
| `[required]` | 4. Assignment(s) | course | course `.github` → **New assignment**, then `git push` | `number` + `tag` + `format` (py/notebook) + `type` (individual/group); brief + starter on `main`, optional autograding on `solution` ([layout](#assignment-template)) | one `assignment-N-<tag>` template each |
| *(optional)* | 5. Course admins | course | edit [`dsl-course.yml`](#dsl-courseyml), commit to `main` ([05](05-manage-teaching-team.md)) | GitHub handles, optional `start`/`end` | admin on the course org + every cohort, reconciled |
| `[required]` | 6. Refresh | course | course `.github` → **Refresh actions** | none | dropdowns populated, secrets on content repos |

> Enrolment-code + grade emails send as `datasciencelab@hertie-school.org` via a central Entra app, authenticated by certificate ([details](../docs-admin-arch/central-admin.md#email)). Live on every course org. Where it is not, enrolment codes still land in `students.csv` (to be emailed by hand) and grades still reach each student's repo; only the notification is skipped.

## Cohort setup (per year)

| | Step | Org Level | Where | Input | Output |
|---|------|-------|-------|-------|--------|
| `[required]` | 1. Create the cohort org | cohort | GitHub [web UI](https://github.com/account/organizations/new) | name `<course-name>-f/sYYYY`; invite **`hertie-dsl-bot`** as **Owner** (must accept) | an empty org the bot can bootstrap |
| `[required]` | 2. Bootstrap | course → cohort | course `.github` → **Bootstrap cohort** | `cohort_org` | `welcome` (Join course / Join team issues) + `classroom-config` (all the files below), `students`/`auditors` teams, the cohort site, cohort registered with the cron |
| `[do this first]` | 3. The term plan | cohort | edit [`classroom-config/schedule.yml`](#scheduleyml) | releases, due dates, exams | the hourly cron runs the whole term; site dates; grading deadlines |
| `[required]` | 4. Roster | cohort | edit [`classroom-config/students.csv`](#studentscsv) | registrar rows | the enrolment + provisioning source of truth |
| *(optional)* | 5. Teaching team | cohort | edit [`classroom-config/people.yml`](#peopleyml) ([05](05-manage-teaching-team.md)) | handles (+ card fields), optional `start`/`end` | push access for this cohort's instructors/TAs + site cards; time-boxed if dated |
| `[required]` | 6. Enrol | course workflow, per cohort | course `.github` → **Send enrolment codes** (untick `dry_run`) | `cohort_org` | codes written to the roster + emailed; students join via the `welcome` **Join course** issue |
| *(optional)* | 7. Ad-hoc release | course workflow, per cohort | **Release materials** / **Release assignment** | see [08](08-release-materials-to-cohort.md)/[09](09-release-assignment-to-cohort.md) | anything out earlier/differently than the schedule says |
| *(optional)* | 8. Return marks | course workflows + [`grades/<slug>.csv`](#gradesslugcsv) | the [grading runbook](10-grade-and-return-assignments.md) | your marks | private per-student gradebooks |
| *(optional)* | 9. Check cohort setup | course workflow, per cohort | course `.github` → **Check cohort setup** | `cohort_org` | what's configured, what's missing, an edit link per gap |

## Inputs by file

> NB: all these `classroom-config/` files are kept in a private repo (PII stays there; not leaked publicly).

### `dsl-course.yml`

Live example: [`example-course/course-org/dsl-course.yml`](../example-course/course-org/dsl-course.yml).

- Course org's `.github` repo - the course's identity card. 
- Bootstrap writes it; you can optionally edit it; edits here are propagated to the course orgs' `.github` (which is mostly a series of pointers to this file).

```yaml
org: hertie-dsl-demo-course-e1234
org_name: DSL Demo Course        # names the ORG - the websites never show it
course_name: Deep Learning       # the cohort websites' title
course_code: E1234               # shown beside it
course_description: One or two sentences, on one line - the sites' blurb
site_link_extensions: [pdf, html]  # optional - cohort sites only; see below
people:
  course_admins:
    - github_handle: "janedoe"   # admin on the course org + every cohort
    - github_handle: "visiting"
      start: "2026-09-01"        # optional - access auto-starts/lapses on these dates
      end: "2027-06-30"
```

`course_admins` is the **course-level** grant - declared once here, mirrored into every cohort's
own `course-admin` team, and never re-declared per year. Deleting an entry, or an `end` date
passing, revokes on the next sync. This org's `instructors`/`teaching_assistants` keys are
display-only cards; TAs are declared per cohort in [`people.yml`](#peopleyml).
Runbook: [05](05-manage-teaching-team.md).

`course_name` / `course_code` / `course_description` are the fields that reach every
cohort website - a push here re-syncs them all: [11](11-configure-cohort-site.md).

`site_link_extensions` narrows what each session row **links** on the **cohort** sites,
never what it ships. Unset (the default), a row lists the files at its session folder's root
plus one link per subfolder, so a rendered Quarto/Rmd deck links the deck rather than its
hundreds of assets; set, only these file types are listed, plus a link to the folder itself.

It does **not** apply to the public open-courseware site, which serves its files itself and
has no directory listing to fall back on - a file excluded there would be published and
linked from nowhere. That site always lists each session folder's root files.

### `students.csv`

Live example: [`example-course/cohort-org/students.csv`](../example-course/cohort-org/students.csv).

`classroom-config/students.csv` - one row per student, straight from the registrar (seeded
header-only, with a filled `students.csv.sample` next to it). Leave the onboarding-owned
columns blank (`github_handle`, `github_id`, `enrol_code`). Deleting a row off-boards that student on the next push.

```csv
hertie_email,name,github_handle,github_id,enrol_code,role
j.doe@students.hertie-school.org,Jane Doe,,,,
e.evans@students.hertie-school.org,Eve Evans,,,,auditor
```

| Column | Filled by | Notes |
|--------|-----------|-------|
| `hertie_email`, `name` | you | `hertie_email` is the match key, and receives the enrolment code + grade notices |
| `github_handle`, `github_id` | **onboarding** | blank until the student joins; the immutable `github_id` survives handle renames |
| `enrol_code` | **Send enrolment codes** | the token the student pastes into the Join course issue |
| `role` | you | blank/`enrolled` = full participant; `auditor` = reads released materials, gets no assignments/grades, refused from teams |

Add any other column you want (a registrar id, a lecture section, notes) - the engine ignores it and preserves it.

### `people.yml`

Live example: [`example-course/cohort-org/people.yml`](../example-course/cohort-org/people.yml).

`classroom-config/people.yml` - this cohort's teaching team. Grants the cohort's `instructors`
team necessary access permissions at both the course- and cohort-org levels. This includes the ability to push content from the course-org to that year's content repos (`instructors-<tag>`), and supplies the cohort site's cards. `github_handle` is the only required field, the rest are optional.

```yaml
people:
  instructors:
    - github_handle: "janedoe"     # required - everything else is optional
      name: "Prof. Jane Doe"       # site card fields
      title: "Professor of ..."
      photo: "/_images/pp/jane.jpg"  # see "Staff photos" below
      url: "https://.../jane"
      start: "2026-09-01"          # access auto-starts/lapses on these dates
      end: "2027-01-31"
  teaching_assistants:
    - github_handle: "anOther"
```

The **course** org's `dsl-course.yml` accepts the same `instructors`/`teaching_assistants`
shape, but there it is **display-only** (public-site cards, no access) - course-wide admin is
[`course_admins`](#dsl-courseyml).

**Staff photos.** `photo` accepts either form:

| Form | Example | Use when |
|---|---|---|
| site-relative path | `/_images/pp/jane.jpg` | **the safe default.** Commit the image into this cohort's site repo `<cohort-org>.github.io` under `_images/pp/`. That directory is served (`include: ['_images']` in the site's `_config.yml`) and is not a synced collection, so no release, cron or **Sync site** run will ever overwrite it. |
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

### `teams.csv`

Live example: [`example-course/cohort-org/teams.csv`](../example-course/cohort-org/teams.csv).

`classroom-config/teams.csv` - group membership, per assignment. This can be popualted in 2 ways:
1. Students self-select via the `welcome` **Join team** issue (which enforces the per-assignment `max_team_size` set under
[`schedule.yml`](#scheduleyml)'s `assignments:`, default 5),
2. you edit it directly;
either way a push materialises a GitHub team per group, and releasing a group assignment grants each team one shared repo.

```csv
assignment,team,github_handle
assignment-4-project,team-x,anna-adams
assignment-4-project,team-x,ben-baker
```

### `grades/<slug>.csv`

Live example: [`example-course/cohort-org/grades/assignment-1.csv`](../example-course/cohort-org/grades/assignment-1.csv).

`classroom-config/grades/<slug>.csv` - one per assignment. The autograder creates it and
fills the machine columns (write-once); for hand-marked work copy the header from the seeded
`grades/assignment-1.csv.sample`. `final_grade` +
`individual_comments` are what the student sees; full column-by-column reference:
[the grading runbook](10-grade-and-return-assignments.md#2-add-your-marks-on-top-of--instead-of-autograde).

```csv
github_handle,team,autograde_score,manual_score,team_score,individual_adjustment,final_grade,individual_comments,team_comments
anna-adams,,38/40,9/10,,,A-,Great work,
```

### Materials repo

Live example: [`example-course/course-org/course-materials-f2026`](../example-course/course-org/course-materials-f2026).

`course-materials-<tag>` - private; students only ever see what you release. Any top-level
directory holding ordinal-prefixed subdirectories is a releasable section.

```
course-materials-f2026/
  lectures/01_intro/     any files - slides, notebooks, code
  readings/01_intro/
  labs/01_setup/         add your own sections freely
  SYLLABUS.md            optional - or any name (SYLLABUS.pdf, ...); released by
                         naming it as a `course_source_path`
```

### Assignment template

Live example: [`example-course/course-org/assignment-1-f2026`](../example-course/course-org/assignment-1-f2026).

`assignment-N-<tag>` - a template repo with two branches. Student repos are generated from
`main` only. The **New assignment** workflow's `format` (py/notebook) picks the starter
stub; `type` (individual/group) is recorded in `grading.yml`, and handout and grading obey
`type: group` automatically. The autograder takes either format - it converts any `.ipynb`
the submission holds before running the hidden tests.

```
main branch      README.md (the brief) + starter.*      -> what students get
solution branch  solution/ + grading.yml + tests/       -> faculty-only; hidden tests
                                                            power the (optional) autograder
```

### `schedule.yml`

Live example: [`example-course/cohort-org/schedule.yml`](../example-course/cohort-org/schedule.yml).

`classroom-config/schedule.yml` - the term plan: the **auto-release plan** the hourly cron
runs, and the **dates** that drive the website and grading. Times are read in `timezone`
(default `Europe/Berlin`) unless given an offset; a bare **release** date = 00:00, a bare
**due_datetime**/`grading_datetime` date = 23:59:59, a bare **events** date shows as 09:00. Times are
honoured to the hour.

Blocks encode behaviour: **`releases:`** deploys materials, **`assignments:`** runs the
handout/due/grading lifecycle, **`events:`** is display-only calendar rows, **`enrolment:`**
is the window in which enrolment codes are emailed automatically. Colour is a
display concern only - see [row types](#schedule-row-types) below.

**`releases`** - the term calendar and release plan in one block: each entry is a
label you choose, an `event_datetime:`, and the deploys it ships.
Sources are read from the course org, destinations written to this cohort, so entries name
repos, never orgs. Every release is idempotent - re-runs are no-ops.

| Action | Does | Fields |
|--------|------|--------|
| `deploy` | copy a source path → a cohort repo | `course_source_repo`, `course_source_path`, `cohort_dest_repo` (default `materials`), `cohort_dest_path` (default: mirror). A list, or a single mapping for one copy |
| `assignment` | one private repo per onboarded student - or per team, when the template's `grading.yml` says `type: group` | the template repo name |

(Grading takes no action here - each assignment is autograded automatically, once, at its
`grading_datetime` under `assignments:`.)

Per entry: `event_datetime` (required - when the thing happens; the site schedule shows it,
and it is the default fire time), `title` and `description` (optional - the session's name,
shown beside its ordinal, and a sentence about it on the Lectures tab), and the `deploy`
actions. A deploy item may carry its own
`deploy_datetime` to ship earlier or later than the calendar event.
Assignments take no entry here: their whole lifecycle (handout_datetime/due_datetime/grading_datetime) lives under
`assignments:` below (an `assignment:` action is also supported, for handing out by hand).
Anything that ships nothing - an exam, a clinic, a guest lecture - goes under `events:`.
Uncertain dates:
`tbc: true` beside a date = provisional, shown "(TBC)" but fires; `event_datetime: tbc`
= undated TBC row, nothing fires.

Deploy-item fields (paths are **relative to their repo**: `course_source_path` inside
`course_source_repo`, `cohort_dest_path` inside `cohort_dest_repo`):

| Field | Required | Default | Meaning |
|---|---|---|---|
| `course_source_repo` | **yes** | - | repo in the COURSE org to copy from |
| `course_source_path` | **yes** | - | folder or file to copy, relative to `course_source_repo` |
| `cohort_dest_repo` | no | `materials` | cohort repo to copy into (created on first release) |
| `cohort_dest_path` | no | mirrors `course_source_path` | where it lands, relative to `cohort_dest_repo` |
| `deploy_datetime` | no | the entry's `event_datetime` | ship this copy earlier/later |

**Minimal** - the recommended shape; everything not stated takes its default:

```yaml
timezone: Europe/Berlin
releases:
  lecture_02:
    event_datetime: 2026-09-15T10:00
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/02_intro
      # -> lands at materials/lectures/02_intro, shipped at class time
```

**Full** - every field spelled out, when the defaults aren't what you want:

```yaml
releases:
  session_2:
    event_datetime: 2026-09-15T10:00  # the class - what the site announces
    tbc: false                        # true = provisional date, shown "(TBC)"
    title: Linear regression          # the session's name, beside its "Session 2" ordinal
    description: Least squares by hand  # a sentence about it, on the Lectures tab
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/02_intro
        cohort_dest_repo: lecture_materials
        cohort_dest_path: lectures/02_intro
        deploy_datetime: 2026-09-15T09:00   # ships 1h early
  bonus-dataset:
    event_datetime: 2026-10-20T09:30  # a one-off that isn't a teaching session
    deploy:
      - course_source_repo: course-datasets-f2026
        course_source_path: week7/housing.csv
        cohort_dest_repo: materials
        cohort_dest_path: datasets/housing.csv
```

**Dates** - the website schedule and the grading deadlines. Absent values are synthesised
(semester from the tag, lectures weekly, assignments fortnightly, exams weeks 8 + 15).

Per assignment (`assignments.<slug>`); `due_datetime` and `course_source_repo` are required,
so a minimal entry is a slug, a date and the repo it hands out from:

```yaml
assignments:
  assignment-1:
    course_source_repo: assignment-1-f2026
    due_datetime: 2026-10-13
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `due_datetime` | **yes** | - (entry dropped without it) | what students see; bare date = 23:59:59 |
| `title` | no | the template README's `# ` heading | the assignment's name, beside the slug on the site. Declared here it shows from day one; the README fallback only appears at hand-out |
| `handout_datetime` | no* | - | when repos are provisioned, automatic. *Required for the schedule to release it. If you hand out via the **Release assignment** workflow instead, the workflow records the release moment here for you |
| `grading_datetime` | no | `due_datetime` | snapshot freezes + autograder fires (once) |
| `type` | no | individual | `group` / `individual` - how handout + grading fan out. Can also be set in the template's `grading.yml` |
| `max_team_size` | no | 5 | group assignments: Join-team cap |
| `course_source_repo` | **yes** | - (entry dropped without it) | the course-org repo this hands out from. A name that does not exist is reported loudly |
| `cohort_dest_repo` | no | the slug | the cohort-side name: student/team repos (`<name>-<handle>`), the frozen cohort template, the teams.csv key, snapshots and grades |

```yaml
semester_start: 2026-09-07
semester_end: 2026-12-18
assignments:                          # each assignment's WHOLE lifecycle, keyed by slug
  assignment-1:                       # students SEE this slug: it names their repo (`assignment-1-<handle>`)
    title: Linear regression          # optional: the name shown beside the slug
    handout_datetime: 2026-09-22T09:00  # optional: provision one repo per student (or per
                                        # team - the template's grading.yml decides), automatic
    due_datetime: 2026-10-13            # what students see
    grading_datetime: 2026-10-15        # optional: the grading pin - snapshot freezes and the
                                        # autograder fires (once). Default = due_datetime.
  assignment-4-project:
    due_datetime: 2026-11-27
    type: group                       # optional: group | individual
    max_team_size: 4                  # optional, group assignments: the welcome Join team
                                      # flow refuses members beyond this (default 5)
```

**`events`** - display-only calendar rows, keyed by a label you choose. Nothing deploys;
the row simply appears on the site's schedule.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | **yes** | - (entry dropped without it) | when it happens; a bare date is a whole day, shown as 09:00 |
| `type` | no | `special_event` | `exam` / `special_event` - which colour the row takes |
| `title` | no | prettified label | site row label |
| `tbc` | no | `false` | provisional date - shown "(TBC)" |

```yaml
events:
  mid-term:
    type: exam
    title: MidTerm Exam
    event_datetime: 2026-11-03
    tbc: true   # provisional - shown "(TBC)"
  final-exam:
    type: exam
    title: Final Exam
    event_datetime: 2026-12-15T14:00
  resit-exam:
    type: exam
    title: Resit Exam
    event_datetime: tbc   # undated - shown as a TBC row
  project-clinic:         # no type -> special_event
    title: Project clinic
    event_datetime: 2026-11-17T10:00
```

**`enrolment`** - the window in which the hourly cron emails enrolment codes, instead of
someone pressing **Send enrolment codes**. Every tick inside the window emails whoever still
has no code (the send stamps `code_sent_at` on each row it mails and skips a row that has
one), so a student added mid-window is emailed within the hour and nobody is emailed twice.
Needs the course org's `GRAPH_*` secrets, as the button does.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `send_codes_datetime` | **yes** | - (block dropped without it) | when the window opens |
| `send_until` | no | `semester_start` + 2 weeks (or `send_codes_datetime` + 2 weeks when the cohort pins no `semester_start`) | when it closes; after it, codes go out only by hand |
| `show_on_site` | no | **`false`** | `true` raises a schedule row on the opening date - default OFF, unlike `releases:`, because enrolment is administrative |
| `title` | no | `Enrolment opens` | that row's label, when shown |

```yaml
enrolment:
  send_codes_datetime: 2026-08-24T08:00   # opens a fortnight before term
  send_until: 2026-09-21T00:00            # optional: default semester_start + 2 weeks (or send_codes_datetime + 2 weeks with no semester_start)
  show_on_site: true                      # optional: default false
  title: Enrolment opens                  # optional
```

An open window over an EMPTY `students.csv` sends nothing, and by the time the window is
open it is nearly too late to fix - so **Check cohort setup** reports it, where it can be
read beforehand. The hourly cron stays green: it cannot clear the problem, and an hourly red
X for the length of the window is one nobody reads.

#### Schedule row types

The cohort site renders one merged, date-sorted schedule table; each row is colour-coded by
type, and the type is never a field you set - it follows from where the row came from:

| Row type | Comes from |
|---|---|
| lecture | a released session folder under `lectures/` |
| lab | a released session folder under `labs/` |
| assignment | an `assignments:` entry - shown on **both** its handout date and its due date |
| exam | an `events:` entry with `type: exam` |
| special_event | an `events:` entry with no `type` (clinic, guest lecture, revision session) |
| term_date | the `semester_start` / `semester_end` scalars |
| enrolment | the `enrolment:` window, when it sets `show_on_site: true` (violet, as the term rows are - it is term admin) |

So lecture vs lab is decided by the deployed section folder, not by the entry label, and a
week with both a lecture and a lab renders two rows.

**A malformed entry is dropped - and every drop is reported.** The parser never raises, so
the rest of the term still runs, but nothing is silent: each drop is named in the run log,
counted by **Check cohort setup**, and makes `--validate` exit non-zero. A push to
`schedule.yml` runs **Validate schedule** in `classroom-config`; a file the scheduler cannot
fully read goes red and opens an issue naming the bad entry.

| Mistake | What happens |
|---------|--------------|
| `event_datetime:` missing/unparseable | that `releases:`/`events:` entry is dropped |
| `send_codes_datetime:` missing/unparseable | the whole `enrolment:` block is dropped - no codes are emailed automatically |
| `send_until:` unparseable, or at or before `send_codes_datetime` | kept - the window closes at its default instead |
| `due_datetime:` missing/unparseable | the whole `assignments:` entry is dropped - no grading pin, no site date |
| `deploy` missing `course_source_repo`/`course_source_path` | that copy is dropped |
| `solution_datetime:` malformed, or not after `handout_datetime` | dropped - the solution waits for a human |
| `handout_datetime:` unparseable | kept, but nothing is ever handed out |
| `grading_datetime:` unparseable | kept - the grading deadline falls back to `due_datetime` |
| `deploy_datetime:` unparseable | kept - that copy ships at the `event_datetime` |
| `max_team_size:` unparseable | kept - no cap |
| unknown `type:`, unknown key, unknown `timezone:` | kept, on the documented fallback |

Verify with `python3 -m dsl_course.schedule --cohort-org <COHORT> --validate`. Full account:
[Schedule releases -> Dropped entries](07-schedule-releases.md#dropped-entries).

**What happens at the grading deadline.** The hourly cron freezes each submission repo's
commit into `classroom-config/snapshots/<slug>.csv` (write-once - delete it to re-freeze),
then autogrades **once** against the `<slug>-<tag>` template (the `_graded.json` / `_skipped.json`
record in `classroom-config/autograde/<slug>/` is the fired marker - delete it, or the whole
folder, to re-grade). Machine grade columns are write-once too. All of
this happens whether or not the cohort uses `releases`.

## Token

One secret, `DSL_BOT_TOKEN`, runs every workflow: the bot's classic PAT (`repo` + `admin:org` +
`workflow`), set once centrally. Bootstrap and Refresh put it everywhere it is needed - you
never set it by hand.

## Known limits

- **Moodle** roster-in / grade-out is manual CSV until Hertie IT enables Web Services.
- **Pages are public** on the Free plan; access-controlled once on Campus/Enterprise.
