# Schedule releases

Write the term's plan into the cohort's `classroom-config/schedule.yml` once, and the hourly cron runs the term for you - every materials release, every assignment hand-out, every autograde run. 

The schedule file can be updated throughout the semester.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md)
- A bootstrapped [cohort org](04-new-cohort-org.md) 
- Source material repos to be released (staged in course-org, released to cohort-org)

# Write your term's plan

> For a fully worked example schedule.yml (a full term) see [here](../example-course/cohort-org/schedule.yml).

> An example of the automatically generated schedule on the deployed `.github.io` site can also be seen live [here](https://hertie-dsl-demo-f2026.github.io/schedule/). 

Three blocks carry the whole term, and each is defined by what it **does**:

- **`releases:`** - the entries that **deploy**: file(s) copied from course org staging -> the cohort org, where students can access them.
- **`assignments:`** - each assignment's whole lifecycle: hand-out, due date, grading.
- **`events:`** - **display-only** calendar rows. Nothing deploys; the row simply appears on the cohort site.

Two scalars sit alongside them - `semester_start:` and `semester_end:` - which bookend the term and render as rows of their own.

## `releases:` 

Use this for releasing teaching materials, code, datasets, anything else.

Each entry is a label you choose (`lecture-1`, `lab-1`, `bonus-dataset`) - yours, and never shown to students: the site names a row by its ordinal (`Session 1`, `Lab 1`), taken from the session folder the deploy lands in, plus your `title:` if you give one. Each entry holds:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | **yes** | - | when the class happens - what the site's schedule shows, and the default fire time for this entry's deploys |
| `deploy` (nested entry) | no | - | the copies this entry ships (a nested list - see below) |
| `title` | no | - | the session's name, shown beside its ordinal ("Session 1 / Probability Theory") on the schedule, Lectures, Materials and Labs tabs |
| `description` | no | - | what the session covers - the **learning objectives** of a Hertie syllabus. Shown under the session heading on the Lectures, Labs and Readings tabs; may run to several paragraphs (use a `>` or `\|` block) |
| `tbc` | no | `false` | signals the date is provisional: it fires as normal just the deployed site marks it **(TBC)** |
| `show_on_site` | no | `true` | `false` releases **silently**: the deploys ship exactly as written, but the entry raises no row of its own and never sets an existing row's date or name (it still contributes where its files will land). For content that belongs to a session without being an occasion of its own; see [Silent releases](#silent-releases) |


NB: **the calendar event is not the release.** 
  - A release entry's `event_datetime:` is when the session *happens* - that is what the cohort's `.github.io` site's deployed schedule shows, and it is the default fire time for the entry's deploys. 
  - However, a deploy can also carry its own separate `deploy_datetime:` to ship its files earlier (or later) than the class they belong to. 
  - If nothing needs to ship at all, the row belongs under `events:`, not here.

### Silent releases

Most weeks a session's readings ride the lecture's own entry, so they ship on its clock and there is nothing to decide. Give them their own entry - because they go out a week ahead, say - and by default that entry announces itself: readings land in the same site row as that session's lecture, and the row takes the **earliest** date and title of every entry touching it. So a readings entry dated the 15th silently moves "Session 4" from the 22nd to the 15th, and can rename it.

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

What is *not* withheld is where the files are going: session 4's row still names `materials/readings/04_week-4` among the paths its materials will appear at, and is flagged as having a reading list pending, so an unreleased session can say readings are coming. Only the date and the name are silenced.

A silenced entry is also left out of the **generated syllabus** (Generate syllabus reads the same plan), which is usually what you want for a readings drop that belongs to a session already listed there.

NB: **a row appears as soon as you write it, not when it ships.** Every dated `releases:` entry gets its schedule row from the moment it lands on `main` - so writing the term up front publishes the whole term. Until its files ship the row carries no links and says so ("Materials for session 3 are not released yet - they will appear in `materials/lectures/03_week-3` when released."), then picks up the links on release. Assignments are the exception: one appears on the site only when it actually hands out - no page, no released row, no due row before that. Its template repo exists from the day faculty write it, weeks early, and the site is public, so the plan stays in this file until the hand-out fires. An entry with `event_datetime: tbc` has nowhere to sit on a dated table, so it waits for a real date.

Nested under `deploy:` we havee the following:

| Field | Required | Default | Meaning |
|---|---|---|---|
| `course_source_repo` | **yes** | - | the repo in the COURSE org to copy from |
| `course_source_path` | **yes** | - | the folder or file to copy, relative to `course_source_repo` - or `/` for the whole repo |
| `cohort_dest_repo` | no | `materials` | the cohort repo to copy into - created on first release |
| `cohort_dest_path` | no | mirrors `course_source_path` | where it lands, relative to `cohort_dest_repo` |
| `deploy_datetime` | no | the entry's `event_datetime` | ship this one copy earlier (or later) than the class it belongs to |

NB: `cohort_dest_repo` is yours to choose - one shared `materials` repo, or one repo for lectures, another for labs etc; any non-existent repo and/or directory structure specified between `cohort_dest_repo` and `cohort_dest_path` is created on release if non-exist.

NB: `course_source_path: /` (or `.`) releases the **whole repo**. Two root entries are left behind: `.github` (the faculty Release workflows) and `MAINTAINING.md` (your operating notes, which the scaffold marks as never released). Nested copies - a `labs/.github/` of your own - travel normally. The workflow uses the identical spelling, so this reads straight across from `docs/08`.

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
Each item under `deploy:` is one file to be deployed. Paths are **relative to their repo**: 
- `course_source_path` inside `course_source_repo`
- `cohort_dest_path` inside `cohort_dest_repo`. 

Spell fields out only where a default doesn't fit - a different
destination repo/path, or an early ship time:

```yaml
releases:
  lecture_02:
    event_datetime: 2026-09-15T10:00   # class time - what the deployed site schedule will announce
    deploy:
      - course_source_repo: course-materials-f2026 # item 1
        course_source_path: lectures/02_intro
        cohort_dest_repo: lecture_materials
        deploy_datetime: 2026-09-15T09:00   # is released 1h early
      - course_source_repo: course-materials-f2026 # item 2
        course_source_path: readings/02_intro
        cohort_dest_repo: lecture_materials   

  lab_02:
    event_datetime: 2026-09-17T14:00   # the lab session, which the undefined deploy_datetime will default to
    deploy:
      - course_source_repo: course-materials-f2026
        course_source_path: labs/02_intro
        cohort_dest_repo: lab_materials

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
        cohort_dest_path: labs/02_intro/solutions
        deploy_datetime: 2026-09-17T18:00          # after the lab
```

Both land on the same Lab row; the solutions links appear at 18:00 and never overwrite the
scripts. **Stage the solutions outside the folder you release as the lab** - a `solutions/`
inside `labs/02_intro` ships with the 14:00 copy.

## `assignments:` 

For the full assignment lifecyle: hand-out, due date, grading

Keyed by a slug you choose. As with a `deploy:`, `course_source_repo` names where it comes from and `cohort_dest_repo` what it is called in the cohort (default: the slug). 

> `teams.csv` rows and the grades/snapshot files key on the cohort name too - `cohort_dest_repo` if set, else the slug.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `handout_datetime` | no* | - | when repos are provisioned, automatically. |
| `due_datetime` | **yes** | - | the deadline students see; a bare date closes at **23:59:59** |
| `grading_datetime` | no | `due_datetime` | when the snapshot freezes and it is [autograded](#deadline-snapshots-and-autograding) |
| `solution_datetime` | no | - | when the template's `solution/` is pushed into every provisioned repo. **No default** - omit it and the solution only ever goes out by hand. Must be **after** `handout_datetime`, and needs it set |
| `type` | no | `individual` | `individual` or `group`  |
| `max_team_size` | no | `5` | group assignments only: the welcome repo's Join-team cap |
| `course_source_repo` | **yes** | - | the course-org repo this hands out from - one repo per student (or team) is generated from it |
| `cohort_dest_repo` | no | the slug | what the cohort-side repos are called: `<name>-<handle>` per student (or `<name>-<team>`), and the frozen cohort template `<name>` |

```yaml
assignments:
  assignment-1:
    course_source_repo: assignment-1-f2026  # required: the course-org repo it hands out from
    cohort_dest_repo: assignment-1-basics # optional: the cohort-side name. Default if undefined: the slug (i.e. assignment-1).
    handout_datetime: 2026-09-22T09:00  
    due_datetime: 2026-10-13            # what students see
    grading_datetime: 2026-10-15        # snapshot freezes + autograded (default when undefined: mirrors due_datetime)
    solution_datetime: 2026-10-16T09:00 # optional: pushes the model solution to every repo. No default - omitted = never
    type: group                         # default: individual 
    max_team_size: 3

  regression: # the slug is a label; the repo is named outright
    course_source_repo: wk3-regression-f2026
    due_datetime: 2026-11-10
```

A `course_source_repo:` naming a repo that does not exist is reported loudly and the assignment is skipped - it can only be a typo, and its one other symptom is an assignment that never hands out and never grades. An entry missing the field altogether is dropped, like one missing `due_datetime:`.

## `events:` 

Could be an exam, a drop-in clinic, a guest lecture, a revision session: anything students should see on the calendar that releases no files.

| Field | Required | Default | Meaning |
|---|---|---|---|
| `event_datetime` | **yes** | - | when it happens; as displayed on the deployed site schedule |
| `type` | no | `special_event` | e.g. `exam` or `special_event` - affects which colour the row takes |
| `title` | no | prettified label | the row label on the site |
| `tbc` | no | `false` | the date is provisional: the site marks it **(TBC)** |

```yaml
events:
  mid-term:
    type: exam
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

---
Full schema, field by field, see [here](DEPLOYMENT-CHECKLIST.md#scheduleyml).

 For a fully worked example schedule.yml (a full term) see [here](../example-course/cohort-org/schedule.yml).

---


## Changing dates mid-term

Just commit the edit to `classroom-config/schedule.yml` on `main` - the **GitHub web UI is the recommended way** (or edit a local clone → commit → push). The hourly cron readswhatever is on `main` at each tick, so the change takes effect within the hour; there is nothing to re-arm or re-deploy. 

The one caveat: already-fired **one-shot** actions don't rewind - a release already shipped stays shipped, and a snapshot/autograde that already ran re-runs only if you delete its marker (`snapshots/<slug>.csv` / the `_graded.json` or `_skipped.json` record in `autograde/<slug>/` - deleting the whole folder works too).

## Verifying your schedule

**It checks itself.** Every commit touching `schedule.yml` runs **Validate schedule** in `classroom-config`. A commit that parses clean gets a green tick; one the scheduler cannot fully read gets a **red X**, and an issue naming the bad entry is opened and assigned to you, closing itself when a later commit parses clean.

> The run happens *after* the push, not before it: GitHub Actions cannot gate a commit, and branch protection needs a paid plan on a private repo. So the red X and the issue are how a fault reaches you, rather than the commit being refused.

The run summary shows what the parser *understood*, not just what it rejected - counts one short of what you wrote is how you catch a mistake that is valid YAML:

```
Parsed schedule.yml
  term 2026-09-07 -> 2026-12-18  (Europe/Berlin)
  11 release(s), 19 deploy(s) | 3 assignment(s) | 4 event(s)
```

Three other ways to check, none of them required:

1. **Read the counts.** **Check cohort setup** reports the release plan and term dates, and flags `N entry/ies DROPPED`.
2. **Validate by hand.** `python3 -m dsl_course.schedule --cohort-org hertie-dsl-demo-f2026 --validate`, or `--file schedule.yml --validate` against a local copy. Without `--validate` it prints the schedule *as parsed*, as JSON.
3. **Dry-run the cron.** Run **Scheduled release** by hand; `dry_run` defaults to **`true`**, so it lists what *would* open and releases nothing.

## Sources that do not exist yet

A dropped entry is a fault in the *file*. The other way a term quietly fails is a perfectly valid entry pointing at a folder that isn't there - `lectures/04_lecture` when the repo has `lectures/04_week-4`. Nothing detects that until the deploy fires and ships nothing.

So the sources are checked against the course org in two places: **Validate schedule**, whenever you commit a change to `schedule.yml`, and the **hourly cron**, which is the one that catches a plan written in August and forgotten. Because a term written up front legitimately names folders nobody has authored yet, how loud that is depends on how close the deploy is:

| Distance to the deploy | Severity | What you see |
|---|---|---|
| more than 7 days | advisory | a line in the run summary and a yellow annotation against `schedule.yml` in the commit. Nobody is emailed |
| 7 days or less | warning | the above, plus a **digest issue** in `classroom-config` - so it reaches your inbox rather than waiting to be found |
| 48 hours or less, or already passed | **error** | the digest issue comments to say it escalated, and the **hourly cron goes red** |

**Validate schedule never goes red for a missing source, at any rung.** Its red X means one thing - an entry you wrote is not in your plan - and it clears when the file next parses cleanly. A missing source is not a broken file and doesn't clear when the file is edited, so it gets its own channel: annotations on the commit, and the digest issue below.

### The digest issue

One issue per cohort, titled **"schedule.yml: planned releases cite sources not staged in the course org"**, kept current by the hourly cron:

- its **body** is rewritten every run and always lists everything currently missing, grouped by severity, each line naming the exact field to edit (`releases.lecture_02` → `course_source_path`). Editing a body doesn't email anyone, so this is free to happen hourly.
- it **comments** only when something crosses a rung - a fault appears at warning or above, or escalates. Comments *do* email, and they `cc @<cohort-org>/instructors`, so you hear the transitions and nothing else.
- it **closes itself** when the last missing source is staged.

Appears, escalates, clears - three notifications over the life of a problem, however many hourly ticks happen in between. A term written months ahead sits entirely at *advisory* and opens no issue at all.

A source that cannot be *read* (a rate limit, a permissions blip) is never reported as missing - that would turn every entry in the plan into a phantom typo.

By hand: add `--check-sources <course-org>` to either `--validate` form above. Every line names the field to go and edit, not just the entry it sits in:

```
  2 SOURCE(S) NOT IN hertie-dsl-demo-course-e1234 YET:
    [error] releases.lecture_02 -> course_source_path (due Wed 19 Aug 2026, 08:00): `course-materials-f2026/lectures/02_lecture` does not exist yet - this copy ships nothing
    [advisory] releases.lecture_09 -> course_source_path (due Wed 04 Nov 2026, 08:00): `course-materials-f2026/lectures/09_lecture` does not exist yet - this copy ships nothing
```

## Dropped entries

An entry that is valid YAML but not a valid *schedule* entry is **dropped**: it cannot be run, so the rest of the term parses without it. This is the one fault a green run hides, so every drop is named in the run log, counted on **Check cohort setup**, and turned into a non-zero exit by `--validate`.

| Fault | What the cohort loses |
|---|---|
| no valid `event_datetime` on a `releases:` or `events:` entry | nothing deploys, and no row appears on the site |
| no valid `due_datetime` on an `assignments:` entry | no deadline, no submission snapshot, no autograding |
| a `deploy` item missing `course_source_repo` or `course_source_path` | that one copy never ships |

Kept rather than dropped - the entry still runs on its documented fallback, and the fallback is reported alongside the drops (so `--validate` catches it): a malformed `handout_datetime` (**nothing is ever handed out**), `grading_datetime` (falls back to `due_datetime`), `deploy_datetime` (the copy ships at the `event_datetime`) or `max_team_size` (no cap); an unknown `type:` on an assignment (treated as individual) or an event (shown as a plain special event); a typo'd or unknown key at any level; and an unknown `timezone:` (falls back to `Europe/Berlin`).

`solution_datetime` is the exception that is **dropped, not kept**: malformed, missing its `handout_datetime`, or not after it, the value is discarded and the model solution waits for a human. Honouring a bad one could ship the answers with the questions, and nothing undoes that.

## Timezones and bare dates

- Everything naive is read in the cohort's `timezone:` (default `Europe/Berlin`).
- An explicit offset (`2026-09-15T14:00+00:00`) names that exact instant; it fires then, and the site shows it on the cohort's own clock (`16:00` for a `Europe/Berlin` cohort in September).
- A **bare date** with no time means **00:00** on a release's `event_datetime`/`deploy_datetime` (the day opens), **23:59:59** on an assignment `due_datetime` (the day closes), and a whole day on an `events:` entry's `event_datetime` (the site shows a 09:00 placeholder).

## Deadline snapshots and autograding

Full details of this are in [10-grade-and-return-assignments.md](10-grade-and-return-assignments.md); below is as it pertains to the `schedule.yml`.

> **Autograded ≠ released to students.** The scores land only in the private `classroom-config` - faculty review them (and the whole-class `cohort-gradebook.csv`) and nothing reaches a student until the separate **Distribute grades** workflow: [three gates](10-grade-and-return-assignments.md).

Each assignment's **grading deadline** is `grading_datetime` if you set it, else `due_datetime`. Shortly after it passes, the hourly run does two things, once each:

1. **Freezes** each submission repo's HEAD into `classroom-config/snapshots/<slug>.csv`, using the **server's** clock.
2. **Autogrades** it (optional).

### Releasing the model solution

`solution_datetime` is separate from all of the above, and has no default - a solution released the moment submissions close rewards anyone who pushes late, so you name the moment or it never fires. At that datetime the hourly run pushes the template's `solution/` folder into every student/team repo, which is exactly what **Release assignment** with `include_solution` does by hand. Both are idempotent, so doing one after the other changes nothing.

It needs `handout_datetime` set: the schedule can only push a solution into repos the schedule provisioned. If you hand out manually, release the solution manually too.

Both ways of getting the two dates wrong are **refused at validate time**, not honoured: a `solution_datetime` with no `handout_datetime` (nothing to push into), and one at or before the handout - which would ship the answers with the questions on the first release, and no later run could take that back. In either case the solution simply waits for a human, and `--validate` names the entry.

---

## Next

- [Manually Release materials](08-release-materials-to-cohort.md) 
- [Manually release an assignment](09-release-assignment-to-cohort.md)
- [Grade and return assignments](10-grade-and-return-assignments.md)

---

**Demo:** `classroom-config/schedule.yml` in [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026),
run by [Scheduled release](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/scheduled-release.yml).
