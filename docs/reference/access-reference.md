# Access reference

Who can do what, and which team grants it. The companion to
[`actions-reference.md`](actions-reference.md) (every workflow, one line each).

To **change** anyone's access, don't use this page - follow
[05 Manage the teaching team](../05-manage-teaching-team.md). Access is declared in config files and
reconciled; nothing here is clicked.

## Two separate populations

They are not the same gate and do not overlap.

| | Who | Granted by | May do |
|---|---|---|---|
| **Provisioning** | DSL-wide | `faculty` / `instructors` / `admin` team in **`hertie-data-science-lab`** | run **Bootstrap Course Org** in the central repo. **Nothing else** - it grants no access inside any course. |
| **Running a course** | per course | that course org's `course-admin` or an `instructors-<tag>` team | every workflow in that course org's `.github` Actions tab |

A DSL faculty member who has never been declared in a course's config cannot push to it or
release anything there. Being a course admin, conversely, grants nothing centrally.

## Where each right is declared

| Right | Declared in | Level | Reaches |
|---|---|---|---|
| **Admin**, course-wide | course org `.github/dsl-course.yml` → `people:` `course_admins` (or the `admin` input at bootstrap) | **course** - once, for all years | `course-admin` team on the course org **and mirrored into every cohort org** |
| **Push**, one year's content | that cohort's `classroom-config/people.yml` → `instructors` / `teaching_assistants` | **cohort** - per year | cohort org `instructors` team + course org `instructors-<tag>` team |
| **Read** on released materials | `classroom-config/students.csv` | cohort | `students` or `auditors` team (`role` column) |
| **Write** on a shared project repo | `classroom-config/teams.csv` | cohort | `<assignment>-<team>` team |

`course_admins` is deliberately **course-level**: a course director should not be re-declared each
year, and their admin rights need to span every cohort. Instructors and TAs are deliberately
**cohort-level**: they change most years, so each cohort's list stands alone with no merge across
years and no accumulate-forever roster.

Both files' person entries accept optional `start` / `end` ISO dates - see
[time-boxed access](../05-manage-teaching-team.md#time-box-it-start--end).

```mermaid
flowchart LR
  dcy["`COURSE org · .github/dsl-course.yml
people: course_admins`"] -->|Sync membership| ca["`course-admin team (course org)
admin on .github → every workflow, all cohorts`"]
  ca -->|mirrored down| cca["`course-admin team
(every cohort org)`"]
  py["`COHORT org · classroom-config/people.yml
instructors + teaching_assistants`"] -->|Sync membership| ci["`instructors team (cohort org)
classroom-config + welcome`"]
  py -->|synced upward| itag["`instructors-<tag> team (course org)
push on that tag's repos + .github → the workflows`"]
  ui["GitHub Teams UI (hand-add)"] -.->|reverted on next sync| ca
  ui -.->|reverted on next sync| ci
  ui -.->|reverted on next sync| itag
  ui -->|sticks - manual only| gen["`generic instructors team (course org)
escape hatch: invisible to config & Check cohort setup`"]
```

## What `course-admin` grants

Membership of **that course org's own `course-admin` team** makes **every** workflow in that org's
Actions tab visible and runnable, across all its cohorts. The team is mirrored into each of the
course's cohort orgs, where it holds **admin on every repo** - not ownership of the org itself.
It is scoped to **one course**.

Cron-driven runs (**Scheduled release**, and the automatic paths of **Sync site** /
**Sync membership** / **Publish course website**) skip the access gate entirely - a scheduled run
has no actor to check.

> **Publish course website:** `actual-readings` mode hosts the reading files publicly. Only
> publish what you hold the rights to share - use `reading-list` for copyrighted readings.

## What `instructors-<tag>` reaches

Push on:

- the course org's **`.github`** - which is what makes the central workflows visible and runnable
  for them; and
- every course-org repo whose **name ends `-<tag>`**: `course-materials-f2026`,
  `assignment-1-f2026`, `lecture-code-f2026`.

So a TA on `f2026` can push labs into `course-materials-f2026` and release them to the cohort
without any further grant - the release itself runs server-side as the bot.

The suffix match is the whole rule. A course-org repo **without** the year tag in its name is not
covered; name per-year content repos `<thing>-<tag>`, or grant that repo by hand. A repo scaffolded
by **New materials repo** / **New assignment** is granted **as it is created**, not on some later
sync.

Cohort-side, the same people get write on `classroom-config` and `welcome`.

## What faculty hold on each repo

Two teams carry every faculty grant: `instructors` (this org's teaching team) and `course-admin`.

| Repo | `instructors` | `course-admin` |
|---|---|---|
| course org - **every** repo, `.github` included | push | admin |
| cohort `.github`, `welcome`, `classroom-config` | push | admin |
| cohort released content, submission repos, `grades-<handle>` | **read** | admin |

Read on everything a cohort *receives*: a re-release overwrites released material, and marks
live in `classroom-config/grading_sheets/<slug>.yml` (**Distribute grades** rewrites gradebooks from it),
so an edit in the received copy would silently vanish. `.github` keeps push because GitHub
requires write to trigger a `workflow_dispatch`.

Grants are set at repo creation; the nightly **Refresh actions** sweep raises any repo below its
floor and never demotes.

## The four `instructors` teams

Four different teams share the word "instructors" - they are not interchangeable. The first names
a *population* (who teaches at DSL); the other three name a *role in one course*:

| Team | Lives in | Declared by | Grants |
| --- | --- | --- | --- |
| `instructors` | **`hertie-data-science-lab`** | nothing - manual | write on the toolkit → run **Bootstrap Course Org**. No access inside any course. |
| `instructors` | a **cohort** org | that cohort's `classroom-config/people.yml` | cohort-org membership for that year's instructors/TAs; reconciled |
| `instructors-<tag>` | the **course** org | the same `people.yml` (tag = e.g. `f2026`) | push on `.github` + that tag's content repos, i.e. the workflows for that cohort; reconciled |
| `instructors` | the **course** org (generic) | nothing - manual | a rare, permanent escape hatch |

The central one is the odd kind out: it is the only one that grants **provisioning** and the only
one that reaches nothing inside a course. See
[central-admin.md](../../docs-admin-arch/central-admin.md).

The generic course-org `instructors` team is the other exception: a manual add sticks until manually
removed, but it is **invisible to every config file and to Check cohort setup**. Use it sparingly and
record who's on it elsewhere. Route FA (faculty assistant) and TA access through `people.yml`.

## Rules that catch people out

- **Hand-added members get reverted.** Adding someone to `course-admin`, a cohort's `instructors`
  team or `instructors-<tag>` through the GitHub Teams UI survives only until the next Sync
  membership run, which removes anyone the config doesn't name. A hand-*removal* is likewise
  re-added. Edit the file.
- **Students hold `maintain` on their own submission repo** and read on their own
  `grades-<handle>`; nowhere else, so no faculty workflow is visible or runnable for them.
- **New members must accept a one-time org invite** - membership shows `pending` until they do.
- **Nobody ever holds the bot token.** Every workflow runs server-side under `DSL_BOT_TOKEN`; the
  actor's own permissions are only ever used as the gate.
- **This is rotation, not a security boundary.** `instructors-<tag>` has push on `.github`, and no
  branch protection is configured, so a member could edit `dsl-course.yml` to add themselves to
  `course_admins`, or extend their own `end` date in `classroom-config`. Fine for trusted teaching
  staff; if you need a hard boundary, protect `main` on `.github` and `classroom-config`.

## Where to look when access seems wrong

**Check cohort setup** (course `.github` → Actions, pick the cohort) is read-only and prints a per-cohort
checklist - identity, people, schedule + release plan, roster, teams, grades - with an edit link
for each gap. Start there. The **Sync membership** run log then lists every add and removal it
made.

## Related

- [05 Manage the teaching team](../05-manage-teaching-team.md) - the runbook for changing any of this.
- [`DEPLOYMENT-CHECKLIST.md`](../DEPLOYMENT-CHECKLIST.md) - field-by-field schemas for `dsl-course.yml`
  and `people.yml`.
- [`../../docs-admin-arch/central-admin.md`](../../docs-admin-arch/central-admin.md) - central DSL-org
  authority: who can create orgs, the bot and its rotation, the org inventory.
- [`../../docs-admin-arch/admin-setup.md`](../../docs-admin-arch/admin-setup.md) - the bot account, its
  PAT scopes, and the token model.
- [`../../docs-admin-arch/architecture.md`](../../docs-admin-arch/architecture.md) - how the pieces move.
