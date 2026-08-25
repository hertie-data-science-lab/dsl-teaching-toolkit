# Manually release materials to a cohort

Deploy any path - a session folder, a dataset, a syllabus file, a code subpackage - from the staging course-org repo into a student-facing cohort repo.

> NB: this is the manual ad hoc alternative to [pre-scheduling & automating](07-schedule-releases.md) the term's releases.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- A course [materials repo](02-add-materials-to-course.md) with the sessions you want to release.
- A bootstrapped [cohort org](04-new-cohort-org.md).

## The schedule automatically handles releases in advance (recommended)

A `deploy` entry in the cohort's `schedule.yml` releases exactly what the button below does, at the datetime you give it: [Schedule releases](07-schedule-releases.md). 

This is the recommended method for releasing materials, as it involves a one-time setup cost and also creates an entry in the deployed `<course>.github.io` site, so students can clearly understand the course plan in advance.

## Release materials via manual dispatch

The `release materials` workflow can be found in the course org's: 
  1. `.github` → **Actions** tab → **Release materials** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-materials.yml) 
  2. within any boostrapped materials repo (i.e. any repo created using the `new materials repo` workflow) → **Actions** tab → **Release materials** - e.g. [this demo repo](https://github.com/hertie-dsl-demo-course-e1234/course-materials-f2026/actions)

The button's inputs are **the same fields as a `schedule.yml` `deploy` entry** - what you type here is exactly what you would have written in the schedule:

| # | Input | Required | Default | Meaning |
|---|---|---|---|---|
| 1 | `course_source_repo` | yes | *(the repo you run it from; centrally, the latest dated repo)* | repo to release from in the COURSE org |
| 2 | `course_source_path` | yes | - | folder or file to copy - or a **comma-separated list** |
| 3 | `cohort_org` | yes | *(latest cohort)* | target cohort org (dropdown) |
| 4 | `cohort_dest_repo` | yes | - | repo in the cohort org (created if missing, private, `students` + `auditors` read) |
| 5 | `cohort_dest_path` | no | blank → mirrors `course_source_path` (recreates same structure) | where to put it |

### [How to specify release paths](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-materials.yml)

- **Release one file**: `course_source_path` = `lectures/02_intro` → lands at `materials/lectures/02_intro`.
- **Release multiple specific files** - use a comma-separated list: `course_source_path` = `lectures/02_intro, readings/02_intro, labs/02_intro`  (each path mirrors itself into `cohort_dest_repo`).
- **Release a whole dir**: `course_source_path` = `lectures/` 
- Root files are just paths too: `course_source_path` = `SYLLABUS.md` 
- **Everything in one press:** `course_source_path` = `/` (or `.`) releases the whole repo, minus the faculty side of it: 
  - `.git` (copying it would repoint the cohort repo at the course repo), 
  - `.github` (the Release buttons and their token wiring) 
  - and `MAINTAINING.md` (your operating notes - the scaffold marks it never released). 

Re-releasing is safe to re-run - copies are additive and idempotent.

### Phased code release

The same button releases **code**, because code is just another path. Keep a growing package in a course-org repo (e.g. `lecture-code-f2026`) and disclose it topic by topic as you teach: 
- `course_source_path` = `mlpkg/simulation` (a subpackage folder) or `mlpkg/train/warmup.py` (a single module). 
- Copies are additive, so each release extends what students already have 
- release the package base early (e.g. `mlpkg/core`) so partial releases still import. The [example schedule](../example-course/cohort-org/schedule.yml) shows the scheduled version of the same pattern (weeks 1, 3 and 5 each unlock an `mlpkg` subpackage).

## The README is withheld until you write it

Releasing the repo root (`/`), or naming `README.md` outright, copies the materials repo's
`README.md` to students as their course overview. While it is still the scaffold's
placeholder - addressed to faculty, with a "delete this section before releasing" block and
a link to `MAINTAINING.md` - the release **skips it and goes red**. Everything else in the
release still ships. Rewrite it for students and release again.

## Live updates to the deployed `<course>.github.io` site
- Any released materials will automatically show up in the deployed site (i.e. their release triggers a redeploy).
  - Releases trigger **Sync site** for you, as does a push to `classroom-config/schedule.yml` or `people.yml`
  - Plus there's a daily cron (06:00 UTC)
- You can also run [Sync site](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/sync-site.yml)
by hand only when you don't want to wait for the cron to fire - e.g. after editing a file inside an already-released repo.

> What the site shows, what redeploys it, and which files it overwrites:
> [11 Configure the cohort website](11-configure-cohort-site.md).

## Next

- [Add an assignment](03-add-assignment-to-course.md), then [release it](09-release-assignment-to-cohort.md).
- [Schedule releases](07-schedule-releases.md) - the same four fields, fired automatically.

---
**Demo:** released into [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026); site at
`hertie-dsl-demo-f2026.github.io`.
