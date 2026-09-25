# Schedule releases

Write the semester's plan into the semester's `semester-config/schedule.yml` once, and the scheduler runs the semester for you - every materials release, every assignment hand-out, every autograde run. 

The schedule file can be updated throughout the semester.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md)
- A bootstrapped [semester org](04-new-cohort-org.md) 
- Source material repos to be released (staged in course-org, released to semester-org)

<a id="write-your-terms-plan"></a>

## Write your semester's plan

> For a fully worked example schedule.yml (a full semester) see [here](../example-course/semester-org/schedule.yml).

> An example of the automatically generated schedule on the deployed `.github.io` site can also be seen live [here](https://hertie-dsl-demo-f2026.github.io/schedule/). 

Three blocks carry the whole semester, and each is defined by what it **does**:

- **`releases:`** - the entries that **deploy**: file(s) copied from course org staging -> the semester org, where students can access them.
- **`assignments:`** - each assignment's whole lifecycle: hand-out, due date, grading.
- **`events:`** - **display-only** calendar rows. Nothing deploys; the row simply appears on the semester site.

Two scalars sit alongside them - `semester_start:` and `semester_end:` - which bookend the semester and render as rows of their own. An optional `archive:` block freezes the semester read-only - writing it is what turns that on.

### One word per column

The site's schedule table has four columns, and every block below fills them with the same four fields - so a field means the same thing wherever you write it:

| Column | Field |
|---|---|
| **Event** | `type:` - what KIND of row this is |
| **Date** | `event_datetime:` |
| **Title** | `title:` |
| **Details** | `details:` |

`show_on_site:` (default `true`) and `tbc:` (default `false`) go with them everywhere. `details:` renders **above** whatever the Details cell already generates - the materials links, the "not released yet" note, the submit address - never instead of it.

## `releases:` 

Use this for releasing teaching materials, code, datasets, anything else.

Each entry is a label you choose (`lecture-1`, `lab-1`, `bonus-dataset`) - yours, and never shown to students: the site names a row by its ordinal (`Session 1`, `Lab 1`), taken from the session folder the deploy lands in, plus your `title:` if you give one. Each entry holds:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | **yes** | - | when the class happens - what the site's schedule shows, and the default fire time for this entry's deploys |
| `deploy` (nested entry) | no | - | the copies this entry ships (a nested list - see below) |
| `kind` | no | inferred from where the deploys land | `lecture` / `lab` / `readings` - which row this entry belongs to. Only needed when the destination path cannot say it (lab material that does not land under `labs/`); `readings` claims no row of its own - so a `title:` or `details:` written beside it has no row to appear on, and **Validate schedule** says so (write them on the entry that raises the session's row instead). The declaration travels with this entry's own deploy destination, so that destination must name the session's folder (`clinics/03_week-3`) rather than a parent holding it. An unrecognised value is flagged by **Validate schedule** and the row is placed as if you had declared none |
| `title` | no | - | the session's name, shown beside its ordinal ("Session 1 / Probability Theory") on the schedule, Lectures, Materials and Labs tabs |
| `details` | no | - | what the session covers - the **learning objectives** of a Hertie syllabus. Shown in the schedule's Details column AND under the session heading on the Lectures, Labs and Readings tabs; may run to several paragraphs (use a `>` or `\|` block) |
| `tbc` | no | `false` | signals the date is provisional: it fires as normal just the deployed site marks it **(TBC)** |
| `show_on_site` | no | `true` | `false` releases **silently**: the deploys ship exactly as written, but the entry raises no row of its own and never sets an existing row's date or name (it still contributes where its files will land). For content that belongs to a session without being an occasion of its own; see [Silent releases](#silent-releases) |


NB: **the calendar event is not the release.** If nothing needs to ship at all, the row belongs under `events:`, not here.

### Silent releases

Most weeks a session's readings ride the lecture's own entry and ship on its clock. Give them their own entry - because they go out a week ahead, say - and by default that entry announces itself: readings land in the same site row as that session's lecture, and the row takes the **earliest** date and title of every entry touching it. So a readings entry dated the 15th silently moves "Session 4" from the 22nd to the 15th, and can rename it.

`show_on_site: false` is the opt-out. The entry deploys exactly as written and tells the schedule nothing:

```yaml
  readings-4:
    event_datetime: 2026-09-15T09:00
    show_on_site: false
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: readings/04_week-4
```

The files still reach students on the 15th, and still appear on session 4's row once released - what is withheld is the entry's claim on the schedule, not its content. The same applies to any release that is not an occasion: an errata drop, a dataset added mid-term.

What is *not* withheld is where the files are going: session 4's row still names `materials/readings/04_week-4` among the paths its materials will appear at, and is flagged as having a reading list pending, so an unreleased session can say readings are coming.

A silenced entry is also left out of the **generated syllabus** (Generate syllabus reads the same plan).

NB: **a row appears as soon as you write it, not when it ships.** Every dated `releases:` entry gets its schedule row from the moment it lands on `main` - so writing the semester up front publishes the whole semester. Until its files ship the row carries no links and says so (*"**Materials for session 3 are not yet released** - they will appear in `materials/lectures/03_week-3` when they are."*), then picks up the links on release. An `assignments:` entry works the same way: its hand-out and due rows appear the day you write them, and what waits for the hand-out is the assignment's *content* - the brief, and the title the template's README gives it. Until then the row carries only the plan-side name (`Assignment 1`) and says it is not handed out yet. An entry with `event_datetime: tbc` has nowhere to sit on a dated table, so it waits for a real date.

Nested under `deploy:` we have the following:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `course_source_repo` | **yes** | - | the repo in the COURSE org to copy from |
| `course_source_path` | **yes** | - | the folder or file to copy, relative to `course_source_repo` - or `/` for the whole repo |
| `semester_dest_repo` | no | `materials` | the semester repo to copy into - created on first release |
| `semester_dest_path` | no | mirrors `course_source_path` | where it lands, relative to `semester_dest_repo` |
| `deploy_datetime` | no | the entry's `event_datetime` | ship this one copy earlier (or later) than the class it belongs to |

NB: `semester_dest_repo` is yours to choose - one shared `materials` repo, or one repo for lectures, another for labs etc; any non-existent repo and/or directory structure specified between `semester_dest_repo` and `semester_dest_path` is created on release if non-exist.

NB: `course_source_path: /` (or `.`) releases the **whole repo**. Two root entries are left behind: `.github` (the faculty Release workflows) and `.system/` (the toolkit's files: your operating notes `MAINTAINING.md`, the syllabus example and the generated sessions block). Nested copies - a `labs/.github/` of your own - travel normally.

NB: a root `README.md` or `SYLLABUS.md` still carrying the scaffold's placeholder is **withheld** from the release, with a warning on the run summary and everything else shipped - see [08 -> The unwritten root stubs](08-release-materials-to-cohort.md#the-unwritten-root-stubs-are-withheld-until-you-write-them).

At a minimum only `course_source_repo` + `course_source_path` are required, everything else defaults:

```yaml
releases:
  lecture_02:
    event_datetime: 2026-09-15T10:00
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: lectures/02_intro
# -> lands at materials/lectures/02_intro when the class starts (the event_datetime)

  lab_02:
    event_datetime: 2026-09-17T14:00
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: labs/02_intro
```
Each item under `deploy:` is one copy, and every path is relative to its own repo.

Spell fields out only where a default doesn't fit - a different
destination repo/path, or an early ship time:

```yaml
releases:
  lecture_02:
    event_datetime: 2026-09-15T10:00   # class time - what the deployed site schedule will announce
    deploy:
      - course_source_repo: course-materials-f2026 # item 1
        course_source_path: lectures/02_intro
        semester_dest_repo: lecture_materials
        deploy_datetime: 2026-09-15T09:00   # is released 1h early
      - course_source_repo: course-materials-f2026 # item 2
        course_source_path: readings/02_intro
        semester_dest_repo: lecture_materials   

  lab_02:
    event_datetime: 2026-09-17T14:00   # the lab session, which the undefined deploy_datetime will default to
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: labs/02_intro
        semester_dest_repo: lab_materials

```

### Releasing solutions after the class

A deploy fires on its own `deploy_datetime`, and a release only ever **adds** files - so a
second copy into the same session folder is how solutions reach students after the lab:

```yaml
  lab_02:
    event_datetime: 2026-09-17T14:00
    title: Your first classifier
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: labs/02_intro          # the empty scripts, at lab time
      - course_source_repo: course-materials-f2026
        course_source_path: solutions/labs/02_intro   # staged OUTSIDE labs/02_intro
        semester_dest_path: labs/02_intro/solutions
        deploy_datetime: 2026-09-17T18:00          # after the lab
```

Both land on the same Lab row; the solutions links appear at 18:00 and never overwrite the
scripts. **Stage the solutions outside the folder you release as the lab** - a `solutions/`
inside `labs/02_intro` ships with the 14:00 copy.

## `assignments:` 

Each assignment's **dates**, keyed by a key you choose. `course_source_repo` names the template it hands out from.

**The key is shown to students**: it names their repo (`assignment-1-<handle>`) and heads the row on the site (`assignment-3-project` reads "Assignment 3 Project"). Keep it short; the assignment's name is the template's `title:`.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `course_source_repo` | **yes** | - | the course-org template one repo per student (or team) is generated from |
| `handout_datetime` | no* | - | when repos are provisioned, automatically. *Omit it to hand out by hand |
| `due_datetime` | **yes** | - | the deadline students see; a bare date closes at **23:59:59** |
| `solution_datetime` | no | - | when the model answer and rubric (the template's `solution/`) are pushed into every repo. Not the same as returning marks, and it cannot be undone for reuse. **No default**. Must be **after** `handout_datetime`, and needs it set |
| `marks_return_datetime` | no | - | when this assignment's marks go back, automatically, once every unit is marked; until then a problem says how many are not. Internal: to show a "Marks expected" row, write it as `{event_datetime: 2026-10-27, show_on_site: true}` |
| `details` | no | - | a sentence in the Details column of **both** its rows (out and due) |
| `show_on_site` | no | `true` | `false` and the site says nothing about this assignment. It still hands out, snapshots and grades |
| `tbc` | no | `false` | both rows marked **(TBC)**. **Display only** |

**The late cutoff is computed, never written:** `due_datetime` plus the assignment's `late_window_days` (below). `late_window_days: 0` means no late work.

```yaml
assignments:
  assignment-1:
    course_source_repo: assignment-1-f2026
    handout_datetime: 2026-09-22T09:00
    due_datetime: 2026-10-13
    solution_datetime: 2026-10-16T09:00 # optional. No default - omitted = never
```

### `assignments.yml` - how this semester runs each assignment

Beside `schedule.yml` in `semester-config`. Every key is optional; nearest wins: the assignment's block, then `defaults:`, then the course's `assignment_defaults:` (`.github/dsl-course.yml`), then the institution's (`policy.yml`). The late window and penalty go together: a block naming one sets the other to none.

| Key | Where | Meaning |
|---|---|---|
| `late_window_days` | both | days after the due date that work is still accepted; `0` = none |
| `late_penalty_per_day` | both | `10%` or `0.1`, of the earned mark, per day started |
| `team_formation` | both | `self_select` (the Join team form) or `assigned` (you write teams.csv) |
| `max_team_size` | both | group assignments only |
| `visibility` | both | `private`, `public` or `student_choice`. Read when each repo is created: change it before the first hand out; afterwards a change is a problem, not a move |
| `submit_url` | both | `submit_via: external` only: the site's Submit button (`https://` only) |
| `semester_dest_repo` | per assignment | what this semester's repos are called (default: the key) |

```yaml
defaults:
  late_window_days: 7
  late_penalty_per_day: 10%
assignments:
  assignment-1:
    late_window_days: 2         # both halves: a block naming one sets the other to none
    late_penalty_per_day: 10%
  assignment-2:
    semester_dest_repo: homework-2
```

A block for a key `schedule.yml` does not name is a problem; a key with no block runs on the defaults. **Validate schedule** checks both files on every push to either.

A `course_source_repo:` naming a repo that does not exist is reported loudly and the assignment is skipped. An entry missing it is dropped, like one missing `due_datetime:`.

**Two assignments off one template** (a resit, or two halves of a semester) are allowed only when **every** entry citing it sets its own `semester_dest_repo` in `assignments.yml`. The buttons start from the template and cannot tell the two apart, so **Release assignment**, **Collect submissions** and **Patch** refuse such a template and name both; the schedule fires each on its own dates.

Adding or renaming an assignment wakes **Sync membership**, which rewrites `semester-config/.system/assignments.lock.yml`, the mirror the **Join team** form reads. Its team-formation window runs from `handout_datetime` to the late cutoff ([09](09-release-assignment-to-cohort.md#group-assignments-creating-the-teams)).

## `events:` 

Could be an exam, a drop-in clinic, a guest lecture, a revision session: anything students should see on the calendar that releases no files.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | **yes** | - | when it happens; as displayed on the deployed site schedule |
| `kind` | no | `special_event` | e.g. `exam` or `special_event` - affects which colour the row takes |
| `title` | no | prettified label | the row label on the site |
| `details` | no | - | what the row's Details column says: the room, the format, what to bring. There is no default - an exam with no `details:` says nothing, rather than the "Details to be confirmed." the toolkit used to write into every one |
| `tbc` | no | `false` | the date is provisional: the site marks it **(TBC)** |
| `show_on_site` | no | `true` | `false` keeps the row in this file and off the site |

```yaml
events:
  mid-term:
    kind: exam
    title: MidTerm Exam
    event_datetime: 2026-11-03

  project-clinic:                     
    title: Project clinic
    event_datetime: 2026-11-17T10:00
    tbc: true  # provisional' - site shows "(TBC)" next to the given date time.

  guest-lecture:  
    title: Guest lecture  
    event_datetime: tbc # site will show just TBC, no proposed datetime
                       # sorted end-of-term until a real date replaces
```

## `archive:`

When this semester is archived: every repository in the org archived, nothing deleted, nobody removed. See [Archiving the semester](10-grade-and-return-assignments.md#archiving-the-semester).

**The block is the switch.** Write it and the semester is archived automatically; leave it out and nothing ever is. Every field inside it is optional. A new semester's seeded `schedule.yml` already carries the block, so filling in `semester_end` arms a freeze sixty days later; delete the block to opt out.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | no | `semester_end` + `grace_days` | the day the whole semester org is archived |
| `grace_days` | no | `60` | how many days after `semester_end` the default date falls |
| `title` | no | `Semester archived` | the row's Title column |
| `show_on_site` | no | `true` | a "Semester archived" row on the deployed schedule, and a notice in the site's Updates box for the fortnight before |
| `details` | no | *none* | the sentence that row and that notice say - all of it; `{date}` in it is filled in with the archive date |
| `tbc` | no | `false` | the date is provisional: the site marks it **(TBC)**. **Display only** - the archive still happens on the date above |

```yaml
semester_end: 2026-12-18
archive:
  event_datetime: 2027-02-16  # optional - without it, 60 days after semester_end
  details: >-                 # optional - what students are told, in your own words
    This semester is archived on 2027-02-16: every repository in it becomes read-only.
    You keep read access.
```

`details:` is where the sentence comes from, and the only place: there is no wording
of the toolkit's own behind it, because what archiving means for your students is yours to
say. Write none and the row still shows - "Semester archived", with its date - and says
nothing under it, and nothing goes in the Updates box. The skeleton in a new semester's
`schedule.yml` carries a suggested sentence ready to uncomment.

`archive:` on its own means "yes, on the default date". With no block, or a block with
neither an `event_datetime` nor a `semester_end` to count from, nothing is archived automatically and
the semester's digest issue says so. Archiving such a semester is the **Archive semester**
button with `force`.

---
Full schema, field by field, see [here](DEPLOYMENT-CHECKLIST.md#scheduleyml).

---


## What drives the scheduler

**Scheduled release**, in the course org's `.github`, is the workflow that runs your plan. Three things start it:

- a timer on the DSL lab server, which dispatches every course org roughly every 15 minutes - the primary driver;
- GitHub's own cron at :07/:22/:37/:52, which GitHub delivers only some of the time - a backstop, not the clock;
- a push to `semester-config/schedule.yml`, which fires a tick straight away.

The two drivers guard each other, so a time in the plan is honoured to within about 15 minutes: **pin a release ahead of the class that needs it**, not at its start time. The button is there too, for a run on demand. Releasing and grading are separate jobs, grading one per semester, so a long autograding pass never holds up anyone's release.

If a driver stops, or something due ships late, the scheduler files an issue and closes it again once things recover:

- **Scheduled release: driver health**, in the course org's `.github` - the lab server has stopped dispatching, so only GitHub's unreliable cron is left; tell whoever runs the infrastructure. It ccs your `course-admin` team.
- **Scheduled release: late delivery**, in the semester's private `semester-config` - something due shipped more than an hour late, naming the schedule entries and by how many minutes. It ccs the semester's `instructors`.

A newly bootstrapped org raises neither until it has seen its first dispatched run. Thresholds and timing: [maintainers.md](reference/maintainers.md#the-schedulers-two-drivers).

## Changing dates mid-term

Just commit the edit to `semester-config/schedule.yml` on `main` - the **GitHub web UI is the recommended way** (or edit a local clone → commit → push). The push fires the scheduler itself, so the change takes effect within minutes; there is nothing to re-arm or re-deploy. 

The one caveat: already-fired **one-shot** actions don't rewind. A deadline snapshot, an autograde and a model-solution push each happen once, and re-doing one means deleting its marker - `.system/snapshots/<slug>.csv`, `.system/solutions/<slug>.json`, or the `_graded.json` / `_skipped.json` record in `.system/autograde/<slug>/` (deleting the whole folder works too). A `handout_datetime` the scheduler recorded is likewise never rewritten.

Everything else is **cumulative**: material deploys, assignment handouts, the site sync and the source digest are re-applied at every tick, so a late or lost tick heals itself. A release already shipped stays shipped, because unshipping it would mean rewriting the semester repo's git history.

## Verifying your schedule

**It checks itself.** Every commit touching `schedule.yml` runs **Validate schedule** in `semester-config`. A commit that parses clean gets a green tick; one the scheduler cannot fully read gets a **red X** and a run summary naming what it dropped. That run emails nobody: the fault joins the standing *schedule.yml* [digest issue](#the-digest-issue) in `semester-config` instead, on the next tick - within the minute, since this push fires one - and that is what emails whoever wrote the line.

> The run happens *after* the push: Actions cannot gate a commit, so the red X and the digest issue are how a fault reaches you, rather than the commit being refused.

The run summary shows what the parser *understood*, not just what it rejected - counts one short of what you wrote is how you catch a mistake that is valid YAML:

```
Parsed schedule.yml
  semester 2026-09-07 -> 2026-12-18  (Europe/Berlin)
  11 release(s), 19 deploy(s) | 3 assignment(s) | 4 event(s)
```

Three other ways to check, none of them required:

1. **Read the counts.** **Check semester setup** reports the release plan and semester dates, and flags `N entry/ies DROPPED`.
2. **Validate by hand.** `python3 -m dsl_course.schedule --semester-org hertie-dsl-demo-f2026 --validate`, or `--file schedule.yml --validate` against a local copy. Without `--validate` it prints the schedule *as parsed*, as JSON.
3. **Preview it.** Run **Scheduled release** by hand; `preview` defaults to **`true`**, so it lists what *would* open and releases nothing.

## Sources that do not exist yet

A dropped entry is a fault in the *file*. The other way a semester quietly fails is a perfectly valid entry pointing at a folder that isn't there - `lectures/04_lecture` when the repo has `lectures/04_week-4`. Nothing detects that until the deploy fires and ships nothing.

So the sources are checked against the course org in two places: **Validate schedule**, whenever you commit a change to `schedule.yml`, and the **scheduler**, which is the one that catches a plan written in August and forgotten. Because a semester written up front legitimately names folders nobody has authored yet, how loud that is depends on how close the deploy is:

| Distance to the deploy | Severity | What you see |
|---|---|---|
| more than 24 hours | advisory | a line in the run summary and a yellow annotation on the offending line of `schedule.yml` in the commit. Nobody is emailed |
| 24 hours or less | warning | the **digest issue** in `semester-config` opens (or updates), comments, and **you are emailed** |
| 12 hours or less | **urgent** | the issue comments to say it escalated, and a second email goes out |
| 6 hours or less | **critical** | it comments again, one rung louder, and the email copies the toolkit maintainer |
| the moment has passed | **missed** | the copy did not ship. A last comment and a last email, and the fault stays listed until the source is staged |

**Who is emailed.** Whoever git says can act: the person who last edited that line of `schedule.yml`, and whoever last committed to the materials or template repo it names. A TA's email copies the semester's instructors. If git can name nobody in `instructors.yml` - the line was never edited by instructors, or the blame could not be read - every instructor is emailed instead. Addresses come from the `email:` field on each entry in `semester-config/instructors.yml`; the digest issue `cc`s the same people by handle. If nobody in `instructors.yml` has an `email:` at all, the course admins are emailed, and the toolkit maintainer if the course names none.

**Nothing is emailed between 23:00 and 07:00** in the semester's own timezone. The issue still updates and comments immediately; the email is held and sent on the first tick after 07:00, as one message at the loudest rung it reached overnight.

**And a push gets an immediate reply.** Committing a `schedule.yml` that leaves a release inside 24 hours with nothing staged gets a comment on that commit, naming each line and its deadline. Distant faults get nothing - the digest issue holds those.

**No rung reds the scheduled run.** A red X on **Scheduled release** keeps the one meaning it has everywhere else - the run itself broke.

**Validate schedule never goes red for a missing source either.** Its red X means one thing - an entry you wrote is not in your plan - and it clears when the file next parses cleanly. A missing source gets its own channel: annotations on the commit, and the digest issue below.

### The digest issue

One issue per semester, titled **"schedule.yml: planned releases cite sources not staged in the course org"**, kept current by the scheduler. It carries everything wrong with this file - a source nobody has staged, an entry the parser had to drop, a file that does not parse at all, a group assignment whose teams have not all formed (which stays listed after the window shuts, until you fix it):

- its **body** is rewritten every run and always lists everything currently missing, grouped by severity, each line naming the exact field to edit (`releases.lecture_02` → `course_source_path`), the one sentence that would fix it, and a link at its line in your `schedule.yml`. Editing a body doesn't email anyone, so this is free to happen on every tick.
- it **comments** only when something crosses a rung - a fault appears at warning, escalates, or clears - and `cc`s the same people the email is addressed to.
- it **closes itself** when nothing in the file is left to fix.

A semester written months ahead sits entirely at *advisory* and opens no issue at all.

Don't close it by hand: closing changes nothing in the file, and the next tick reopens it without emailing everybody again.

A source that cannot be *read* (a rate limit, a permissions blip) is never reported as missing.

This is one of seven such issues, one per file you edit by hand, all of them working the same way: [Why did I get this email?](reference/actions-reference.md#why-did-i-get-this-email).

By hand: add `--check-sources <course-org>` to either `--validate` form above. Every line names the field to go and edit, not just the entry it sits in:

```
  2 SOURCE(S) NOT IN hertie-dsl-demo-course-e1234 YET:
    [critical] releases.lecture_02 -> course_source_path - schedule.yml:36 - hertie-dsl-demo-course-e1234/course-materials-f2026/lectures/02_lecture does not exist - fires Wed 19 Aug 2026, 08:00 Europe/Berlin
    [advisory] releases.lecture_09 -> course_source_path - schedule.yml:184 - hertie-dsl-demo-course-e1234/course-materials-f2026/lectures/09_lecture does not exist - fires Wed 04 Nov 2026, 08:00 Europe/Berlin
```

## Dropped entries

An entry that is valid YAML but not a valid *schedule* entry is **dropped**: it cannot be run, so the rest of the semester parses without it. This is the one fault a green run hides, so every drop is named in the run log, counted on **Check semester setup**, and turned into a non-zero exit by `--validate`.

| Fault | What the semester loses |
|---|---|
| no valid `event_datetime` on a `releases:` or `events:` entry | nothing deploys, and no row appears on the site |
| no valid `due_datetime` on an `assignments:` entry | no deadline, no submission snapshot, no autograding |
| a `deploy` item missing `course_source_repo` or `course_source_path` | that one copy never ships |

Kept rather than dropped - the entry still runs on its documented fallback, and the fallback is reported alongside the drops (so `--validate` catches it): a malformed `handout_datetime` (**nothing is ever handed out**), `marks_return_datetime` (marks go back by hand) or `deploy_datetime` (the copy ships at the `event_datetime`); an unknown `kind:` on an event (shown as a plain special event); a typo'd or unknown key at any level; and an unknown `timezone:` (falls back to `Europe/Berlin`). A key that left this file - an assignment's `title`, `grading_datetime` or `semester_dest_repo`, a release's `assignment`, `enrolment` - is `NOT_MIGRATED`, naming where it lives now; an assignment carrying one is dropped until the migration moves it.

An empty `deploy:` - the key written with nothing under it - is flagged too. It parses as "no copies", so the entry becomes a display-only row that ships nothing; if that is what you meant, delete the key (or write `deploy: []`) and the flag goes away.

`solution_datetime` is the exception that is **dropped, not kept**: malformed, missing its `handout_datetime`, or not after it, the value is discarded and the model solution waits for a human. Honouring a bad one could ship the answers with the questions, and nothing undoes that.

## Timezones and bare dates

- Everything naive is read in the semester's `timezone:` (default `Europe/Berlin`).
- An explicit offset (`2026-09-15T14:00+00:00`) names that exact instant; it fires then, and the site shows it on the semester's own clock (`16:00` for a `Europe/Berlin` semester in September).
- A **bare date** with no time means **00:00** on a release's `event_datetime`/`deploy_datetime` (the day opens), **23:59:59** on an assignment `due_datetime` (the day closes), and a whole day on an `events:` entry's `event_datetime` (the site shows a 09:00 placeholder).

## Deadline snapshots and autograding

Full details of this are in [10-grade-and-return-assignments.md](10-grade-and-return-assignments.md); below is as it pertains to the `schedule.yml`.

> **Marked ≠ released to students.** Everything lands in the private `semester-config` and nothing reaches a student until you run **Distribute grades**: [Grade and return assignments](10-grade-and-return-assignments.md).

Each assignment's **late cutoff** is `due_datetime` plus its `late_window_days`. From the **due date** the cron refreshes the grading sheet (and posts submission receipts) every quarter of an hour; at the cutoff it does three things, once each:

1. **Freezes** each submission repo's HEAD into `semester-config/.system/snapshots/<slug>.csv`, using the **server's** clock, and records against it when GitHub saw the push that delivered that commit.
2. **Freezes** the grading sheet - its `info:` never moves again.
3. **Autogrades** it (optional).

### Releasing the model solution

`solution_datetime` is separate from all of the above, and has no default - a solution released the moment submissions close rewards anyone who pushes late, so you name the moment or it never fires. At that datetime the scheduled run pushes the template's `solution/` folder into every student/team repo, which is exactly what **Release assignment** with `solution_datetime: now` does by hand. Both are idempotent, so doing one after the other changes nothing. The push that first adds a `solution_datetime:` gets a notice on its commit saying so; a hand out with `now` (button or console) runs only straight after a preview of it by the same person.

It needs `handout_datetime` set: the schedule can only push a solution into repos the schedule provisioned. If you hand out manually, release the solution manually too.

---

## Next

- [Withhold files from a release](08-release-materials-to-cohort.md#withholding-files-with-releaseignore) - `.releaseignore`, in `.gitignore` syntax
- [Manually Release materials](08-release-materials-to-cohort.md) 
- [Manually release an assignment](09-release-assignment-to-cohort.md)
- [Grade and return assignments](10-grade-and-return-assignments.md)

---

**Demo:** `semester-config/schedule.yml` in [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026),
run by [Scheduled release](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/scheduled-release.yml).
