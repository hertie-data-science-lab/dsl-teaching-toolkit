# Schedule releases

Write the semester's plan into the semester's `semester-config/schedule.yml` once, and the scheduler runs the semester for you - every materials release, every assignment hand-out, every autograde run. 

The schedule file can be updated throughout the semester.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md)
- A bootstrapped [semester org](04-new-cohort-org.md) 
- Source material repos to be released (staged in course-org, released to semester-org)

<a id="write-your-terms-plan"></a>

## Write your semester's plan

> A fully worked `schedule.yml` (a whole semester): [example-course/semester-org/schedule.yml](../example-course/semester-org/schedule.yml). The schedule it renders, live: [here](https://hertie-dsl-demo-f2026.github.io/schedule/).

`schedule.yml` holds **one block per kind** - `lectures:`, `labs:`, `readings:`, `exams:`, `drop-ins:`, `events:`, `assets:` (the institution's kinds, from `policy.yml`) - and `assignments:`. Two scalars bookend the semester, `semester_start:` and `semester_end:`, and an optional `archive:` block freezes it read-only.

Each block is a map. The **key** of an entry (`lecture-3`, `lab-2b`) is its id: it names the entry's site row and, for an assignment, its mark sheet. It is never shown to students and never read for a number - so call it anything, and renaming it is the only thing that changes it.

### Entries of every kind

| Field | Required | Default | Meaning |
|---|---|---|---|
| `session` | on `lectures:` and `labs:` | - | the teaching unit (an integer): lecture 3, its lab and its readings share `session: 3`. Optional on `readings:` (absent: a row of its own); not used elsewhere |
| `number` | no | `session` | the number in the row's name (`Lab 2`) when it differs from the unit |
| `event_datetime` | **yes** | - | a datetime, a bare date, `tbc`, or a list of slots (below) |
| `title` | no | - | the session's name, beside the row's name ("Lecture 1 / Probability Theory") |
| `details` | no | - | what it covers (the learning objectives); shown on the Schedule and under the row's heading on its kind's tab; a `>` or `\|` block for paragraphs |
| `location` | no | - | the room, shown under the date |
| `tbc` | no | `false` | the date is provisional: the site marks it **(TBC)**; it still fires |
| `show_on_site` | no | `true` | `false`: no row on the site; its files still ship (readings: see below) |
| `files` | no | - | what ships (below) |
| `reading_list` | no | - | `readings:` only: markdown, shown with the readings on the site |

Each kind gets its own tab on the site, and its rows are named by the kind and the number (`Lecture 3`). Two shown entries of one kind with the same number are a problem. A lecture or lab without `session:` is a problem too (*Give lecture-3 a session*), and its files wait until it has one.

**A row appears as soon as you write it, not when it ships.** Until its files land the row says so (*"Materials for lecture 3 are not yet released"*), then links what landed: a file as itself, a folder as GitHub shows it. An entry with `event_datetime: tbc` is a **TBC** row after the dated rows of its kind (never in Updates), and nothing ships until it has a date.

### Slots: one entry, several times

A list under `event_datetime:` is one entry taught more than once - two lab groups - with one set of files:

```yaml
labs:
  lab-1:
    session: 1
    title: Introduction
    event_datetime:
      - at: 2026-09-16T08:00
        location: Lab 2.30
        details: Group 1
      - at: 2026-09-17T18:00
        location: Lab 2.32
        details: Group 2
    files:
      - from_course_repo: course-materials-f2026
        from_path: labs/01_intro
```

Each slot is a row on the Schedule; the files ship once, an hour before the first slot, and only the first is ever news in the Updates box.

### `files:`

| Field | Required | Default | Meaning |
|---|---|---|---|
| `from_course_repo` | **yes** | - | the repo in the COURSE org to copy from |
| `from_path` | **yes** | - | the folder or file to copy, or `/` for the whole repo |
| `to_semester_repo` | no | `materials` | the semester repo to copy into - created on first release |
| `to_path` | no | `from_path` | where it lands in `to_semester_repo` |
| `release_datetime` | no | an hour before the first slot (a whole-day slot: the day's start) | ship this copy at another time |

```yaml
lectures:
  lecture-2:
    session: 2
    event_datetime: 2026-09-15T10:00
    files:
      - from_course_repo: course-materials-f2026
        from_path: lectures/02_intro
# -> lands at materials/lectures/02_intro at 09:00
```

Folder names mean nothing: the block says what the entry is. A release only ever **adds** files, so solutions go out after the class as a second copy, staged **outside** the folder the lab ships:

```yaml
      - from_course_repo: course-materials-f2026
        from_path: solutions/labs/02_intro
        to_path: labs/02_intro/solutions
        release_datetime: 2026-09-17T18:00
```

No `files:` key: a session with nothing to ship (a row, never "not yet released"). `files: []`: files still to write - a to-do on the Dashboard.

NB: `from_path: /` releases the whole repo, less `.github` and `.system/`. A root `README.md` or `SYLLABUS.md` still carrying the scaffold's placeholder is **withheld** - see [08](08-release-materials-to-cohort.md#the-unwritten-root-stubs-are-withheld-until-you-write-them).

### Readings

A readings entry with a `session:` belongs to that unit: the unit's first row carries a **Readings** chip, and the Readings tab lists it under the lecture's name. One without is a row of its own. `show_on_site: false` keeps a session's readings off the Schedule; they stay on the Readings tab.

```yaml
readings:
  readings-4:
    session: 4
    event_datetime: 2026-09-15T09:00
    files:
      - from_course_repo: course-materials-f2026
        from_path: readings/04_week-4
    reading_list: |
      - Goodfellow et al., *Deep Learning*, ch. 6.
```

### Exams, drop-ins, events, assets

`exams:` and `events:` entries are calendar rows - a midterm, a clinic, a guest lecture. A bare date is a whole day. `assets:` entries ship supporting files (data, setup) on their date and are never a row.

```yaml
exams:
  midterm:
    title: Midterm exam
    event_datetime: 2026-11-03
    location: Main hall
events:
  guest-lecture:
    title: Guest lecture
    event_datetime: tbc
```

## `assignments:`

Each assignment's dates and run settings.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `number` | **yes** | - | "Assignment 1" on the site |
| `template` | **yes** | - | the course-org template each student's (or team's) repo is generated from |
| `repo_name` | no | `assignment-<number>` | what this semester's repos are called (`<repo_name>-<handle>`) |
| `handout_datetime` | no | - | when repos are provisioned. Omit it to hand out by hand |
| `due_datetime` | **yes** | - | the deadline students see; a bare date closes at **23:59:59** |
| `solution_datetime` | no | - | when the template's `solution/` is pushed into every repo. **No default**; must be after `handout_datetime` (and needs it) and no earlier than the late cutoff. It cannot be undone |
| `marks_return_datetime` | no | - | when marks go back automatically, once every unit is marked. `{event_datetime: ..., show_on_site: true}` also shows a "Marks expected" row |
| `details`, `show_on_site`, `tbc` | no | - | as for any entry; on both its rows (out and due) |

Run settings, each optional, written on the entry: `late_window_days`, `late_penalty_per_day`, `team_formation` (`self_select` or `assigned`), `max_team_size`, `assignment_repo_visibility` (`private`, `public`, `student_choice`; read when each repo is created), `marks_per_question`, `submit_url` (`submit_via: external` only). Nearest wins: the entry, then `semester-config/assignments.yml` `defaults:`, then the course's `assignment_defaults:` (`.github/dsl-course.yml`), then `policy.yml`. The late window and penalty go together: an entry naming one sets the other to none.

**The late cutoff is computed, never written:** `due_datetime` plus `late_window_days`.

```yaml
assignments:
  linear-regression:
    number: 1
    template: assignment-linear-regression
    handout_datetime: 2026-09-22T09:00
    due_datetime: 2026-10-13
    solution_datetime: 2026-10-16T09:00
    late_window_days: 2
    late_penalty_per_day: 10%
```

A `template:` that does not exist is reported and the assignment skipped; an entry without one, or without `due_datetime:`, is dropped. An assignment without `number:` is a problem (*Give assignment-2 a number*) and does not hand out until it has one. Two assignments off one template must each name their own `repo_name`; the hand-out, collect and patch ops refuse such a template and name both, and the schedule fires each on its own dates.

Adding or renaming an assignment wakes **Sync membership**, which rewrites `semester-config/.system/assignments.lock.yml` for the **Join team** form. The team-formation window runs from `handout_datetime` to the late cutoff ([09](09-release-assignment-to-cohort.md#group-assignments-creating-the-teams)).

## `archive:`

When this semester is archived: every repository in the org archived, nothing deleted, nobody removed. See [Archiving the semester](10-grade-and-return-assignments.md#archiving-the-semester).

**The block is the switch.** Write it and the semester is archived automatically; leave it out and nothing ever is. A new semester's seeded `schedule.yml` carries the block, so filling in `semester_end` arms a freeze sixty days later; delete the block to opt out.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | no | `semester_end` + `grace_days` | the day the whole semester org is archived |
| `grace_days` | no | `60` | days after `semester_end` the default date falls |
| `title` | no | `Semester archived` | the row's Title column |
| `show_on_site` | no | `true` | a row on the Schedule, and a notice in the Updates box for the fortnight before |
| `details` | no | *none* | the sentence that row and notice say; `{date}` is filled in |
| `tbc` | no | `false` | marks the date **(TBC)**. Display only |

`archive:` on its own means "yes, on the default date". With no block, or no date to count from, nothing is archived automatically and the digest issue says so; archive such a semester with the `semester.archive` op and `force` (console, or `python -m dsl_course semester.archive`).

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
- **GitHub API budget is running low**, in the course org's `.github` - the account that runs every course's workflows had under a quarter of its hourly budget left when a tick started, so runs may fail until the reset it names. It closes once a tick starts with over half left.

A newly bootstrapped org raises neither of the first two until it has seen its first dispatched run; the budget issue needs none. Thresholds and timing: [maintainers.md](reference/maintainers.md#the-schedulers-two-drivers).

## Changing dates mid-term

Just commit the edit to `semester-config/schedule.yml` on `main` - the **GitHub web UI is the recommended way** (or edit a local clone → commit → push). The push fires the scheduler itself, so the change takes effect within minutes; there is nothing to re-arm or re-deploy. 

The one caveat: already-fired **one-shot** actions don't rewind. A deadline snapshot, an autograde and a model-solution push each happen once, and re-doing one means deleting its marker - `.system/snapshots/<slug>.csv`, `.system/solutions/<slug>.json`, or the `_graded.json` / `_skipped.json` record in `.system/autograde/<slug>/` (deleting the whole folder works too). A `handout_datetime` the scheduler recorded is likewise never rewritten.

Everything else is **cumulative**: material deploys, assignment handouts, the site sync and the source digest are re-applied at every tick, so a late or lost tick heals itself. A release already shipped stays shipped, because unshipping it would mean rewriting the semester repo's git history.

## Verifying your schedule

**It checks itself.** Every commit touching `schedule.yml` or `assignments.yml` runs **Validate schedule** in `semester-config`. A commit that parses clean gets a green tick; one the scheduler cannot fully read gets a **red X** and a run summary naming what it dropped. That run emails nobody: the fault joins the standing *schedule.yml* [digest issue](#the-digest-issue) in `semester-config` instead, on the next tick - within the minute, since this push fires one - and that is what emails whoever wrote the line. An `assignments.yml` that is not YAML goes on its own digest issue instead, and the *schedule.yml* one is left as it stands until it parses.

> The run happens *after* the push: Actions cannot gate a commit, so the red X and the digest issue are how a fault reaches you, rather than the commit being refused.

The run summary shows what the parser *understood*, not just what it rejected - counts one short of what you wrote is how you catch a mistake that is valid YAML:

```
Parsed schedule.yml
  semester 2026-09-07 -> 2026-12-18  (Europe/Berlin)
  16 entries, 20 file(s) | 3 assignment(s) | 4 event(s)
```

**Empty weeks.** Each week of the term still to come (counted from `semester_start`, as the Dashboard's week strip numbers them) with nothing dated in it (no release, hand-out, due date, late cutoff, exam, event or archive) is an *Empty week* problem on the console's Dashboard. A week with something dated but no lecture, lab or readings release or session - for each of those kinds the semester runs weekly, that is, with a dated release in at least half (rounded up) of the weeks that have anything dated - is a *Missing lab* (etc.) problem instead; a hand-out or exam does not count, and one further-readings entry does not make readings weekly. An intentional gap (a reading or exam week) is dismissed with **Set aside**, which adds `empty_week:<monday>` (or `empty_week:<monday>:<kind>`, the Monday of the week's first day) to the course's `set_aside:` list.

**No files yet.** An entry written with `files: []` (or `files:` and nothing under it) is a to-do on the Dashboard, dated by the entry: write its files, or delete the key if the session ships nothing.

Three other ways to check, none of them required:

1. **Read the counts.** The `semester.check` op (console **Check semester setup**, or `python -m dsl_course semester.check`) reports the plan and semester dates, and flags `N entry/ies DROPPED`.
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

**The console's Dashboard counts only what is near.** A missing source is a *problem* (red, counted in the verdict line "Needs fixing: n problems in the next 7 days") once its date is within the next 7 days, or has passed. Until then it sits under **Coming up**, by week, with the day it becomes a problem. An entry with no date yet waits there too, under "No date yet". The digest's mail ladder above is unchanged.

### The digest issue

One issue per semester, titled **"schedule.yml: planned releases cite sources not staged in the course org"**, kept current by the scheduler. It carries everything wrong with this file - a source nobody has staged, an entry the parser had to drop, a file that does not parse at all, a group assignment whose teams have not all formed (which stays listed after the window shuts, until you fix it):

- its **body** is rewritten every run and always lists everything currently missing, grouped by severity, each line naming the exact field to edit (`lectures.lecture-2` → `from_path`), the one sentence that would fix it, and a link at its line in your `schedule.yml`. Editing a body doesn't email anyone, so this is free to happen on every tick.
- it **comments** only when something crosses a rung - a fault appears at warning, escalates, or clears - and `cc`s the same people the email is addressed to.
- it **closes itself** when nothing in the file is left to fix.

A semester written months ahead sits entirely at *advisory* and opens no issue at all.

Don't close it by hand: closing changes nothing in the file, and the next tick reopens it without emailing everybody again.

A source that cannot be *read* (a rate limit, a permissions blip) is never reported as missing.

This is one of eight such issues, one per file you edit by hand, all of them working the same way: [Why did I get this email?](reference/actions-reference.md#why-did-i-get-this-email).

By hand: add `--check-sources <course-org>` to either `--validate` form above. Every line names the field to go and edit, not just the entry it sits in:

```
  2 SOURCE(S) NOT IN hertie-dsl-demo-course-e1234 YET:
    [critical] lectures.lecture-2 -> from_path - schedule.yml:36 - hertie-dsl-demo-course-e1234/course-materials-f2026/lectures/02_lecture does not exist - fires Wed 19 Aug 2026, 08:00 Europe/Berlin
    [advisory] lectures.lecture-9 -> from_path - schedule.yml:184 - hertie-dsl-demo-course-e1234/course-materials-f2026/lectures/09_lecture does not exist - fires Wed 04 Nov 2026, 08:00 Europe/Berlin
```

## Dropped entries

An entry that is valid YAML but not a valid *schedule* entry is **dropped**: it cannot be run, so the rest of the semester parses without it. This is the one fault a green run hides, so every drop is named in the run log, counted by `semester.check`, and turned into a non-zero exit by `--validate`.

| Fault | What the semester loses |
|---|---|
| no valid `event_datetime` on an entry of any kind | nothing ships, and no row appears on the site |
| no valid `due_datetime`, or no `template`, on an `assignments:` entry | no deadline, no submission snapshot, no autograding |
| a `files` item missing `from_course_repo` or `from_path` | that one copy never ships |
| a key used in two blocks | the second entry is dropped |

Kept rather than dropped - the entry still runs on its documented fallback, and the fallback is reported alongside the drops (so `--validate` catches it): a malformed `handout_datetime` (**nothing is ever handed out**), `marks_return_datetime` (marks go back by hand) or `release_datetime` (the copy ships at its default); one unreadable slot of several (the others stand); a typo'd or unknown key at any level; and an unknown `timezone:` (falls back to `Europe/Berlin`). An assignment's `type` or `max_team_size` is noted with where it lives now.

`solution_datetime` is the exception that is **dropped, not kept**: malformed, missing its `handout_datetime`, not after it, or (in the schedule check on a push) before the late cutoff, the value is discarded and the model solution waits for a human. Honouring a bad one could ship the answers with the questions, and nothing undoes that.

## Timezones and bare dates

- Everything naive is read in the semester's `timezone:` (default `Europe/Berlin`).
- An explicit offset (`2026-09-15T14:00+00:00`) names that exact instant; it fires then, and the site shows it on the semester's own clock (`16:00` for a `Europe/Berlin` semester in September).
- A **bare date** with no time means **00:00** on an entry that ships files and on `release_datetime` (the day opens), **23:59:59** on an assignment `due_datetime` (the day closes), and a whole day on an `exams:` or `events:` entry.

## Deadline snapshots and autograding

Full details of this are in [10-grade-and-return-assignments.md](10-grade-and-return-assignments.md); below is as it pertains to the `schedule.yml`.

> **Marked ≠ released to students.** Everything lands in the private `semester-config` and nothing reaches a student until you run **Distribute grades**: [Grade and return assignments](10-grade-and-return-assignments.md).

Each assignment's **late cutoff** is `due_datetime` plus its `late_window_days`. From the **due date** the cron refreshes the grading sheet (and posts submission receipts) every quarter of an hour; at the cutoff it does three things, once each:

1. **Freezes** each submission repo's HEAD into `semester-config/.system/snapshots/<slug>.csv`, using the **server's** clock, and records against it when GitHub saw the push that delivered that commit.
2. **Freezes** the grading sheet - its `info:` never moves again.
3. **Autogrades** it (optional).

### Releasing the model solution

`solution_datetime` is separate from all of the above, and has no default - a solution released the moment submissions close rewards anyone who pushes late, so you name the moment or it never fires. At that datetime the scheduled run pushes the template's `solution/` folder into every student/team repo, which is exactly what the `assignment.handout_now` op with `solution_datetime: now` does by hand. Both are idempotent, so doing one after the other changes nothing. The push that first adds a `solution_datetime:` gets a notice on its commit saying so; a hand out with `now` (console or CLI) runs only straight after a preview of it by the same person.

It must not fall before the late cutoff: students still handing in could read it. The schedule check on a push and the console refuse such a date when it is saved. One in the file anyway (a late window changed afterwards, say) is held by the scheduler and shown at the cutoff, and the digest issue names the line to move. A cutoff that cannot be worked out holds it too. Only the solution waits; hand outs, releases and the freeze run as written.

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
