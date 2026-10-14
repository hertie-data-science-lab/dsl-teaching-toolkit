# Add materials to the course org

Create the year's materials repo and fill it with lectures + readings. The semester's
`schedule.yml` later copies files and folders from here into a semester. One repo per year: `course-materials-{f/s}YYYY`
by default. What makes a repo a materials repo is its `dsl-materials` topic, which the scaffold sets;
add the topic to a repo you made by hand to have it listed as one (the console's **Handout
materials** page does it: **Treat as course materials** on the repo's row). The same page shows each materials repo's
files as a folder tree: open a folder to see inside it; a folder links to GitHub, and a file opens in
the console (its GitHub link and a **Full screen** button in its header, for presenting).

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- Push access on its content repos, from either: 
   1. `course-admin` membership (course org), or
   2. being declared an instructor/TA in a semester org's `semester-config/instructors.yml`

> see [Manage the instructors](05-manage-teaching-team.md) for full access details.

## Steps

Live example: [`example-course/course-org/course-materials-f2026/`](../example-course/course-org/course-materials-f2026). Its [`SYLLABUS.md`](../example-course/course-org/course-materials-f2026/SYLLABUS.md) is the same file you receive as `SYLLABUS.md.sample` beside your own - the toolkit seeds it from there, so the two cannot drift.

1. **Scaffold the repo.** 
   - the `materials.create` op (console, or `python -m dsl_course materials.create`); input `semester` = `fYYYY`/`sYYYY` (e.g. `f2026`)
   - this creates **`course-materials-f2026`** - a private repo, pre-seeded with (edit as needed):
      - empty `lectures/`, `labs/` and `readings/` folders,
      - a `README.md` 
      - `.system/MAINTAINING.md` (your operating notes) and `.system/SYLLABUS.md.sample`, 
      - a placeholder `SYLLABUS.md` 
      - a commented-out `.releaseignore` 
   - `copy_from` (optional) starts the new repo as an existing materials repo instead
     of as the skeleton - every branch, every file, the whole history. Your content arrives
     as you left it; only `.system/` and the workflows are
     rewritten. This is how a course carries forward from one year to the next.
   - You have push on it immediately.

2. **Push your content** to remote's `main` (git push or the web uploader):

   ```
   lectures/01_intro/   any files - slides, demo code, notebooks …
   readings/01_intro/   the readings; the reading list itself is the schedule entry's
                        `reading_guide:` (07), shown on the site
   labs/01_intro/       any files
   SYLLABUS.md          optional - or any name you like (SYLLABUS.pdf, ...)
   ```

   The layout is yours: any file or folder is releasable, and what a release IS (a
   lecture, a lab, readings) is the block of `schedule.yml` it is written in, never its
   folder ([07](07-schedule-releases.md)). Folder names and `NN_` prefixes mean nothing.

   *NB: this repo stays private - students never see it. Only the sessions you **actively release** reach the semester org, so you can privately stage the whole course here.*

   *NB: to withhold a file from every release, name it in `.releaseignore` - `.gitignore` syntax, in any folder ([08](08-release-materials-to-cohort.md#withholding-files-with-releaseignore)).*

   *NB: a session folder is released whole, subfolders included ([11](11-configure-cohort-site.md)).*

   *NB: material no schedule entry names (a manual release, a flat `datasets/`) appears in the semester site's **All Materials** (or the student console's, for a course with `student_console: true`).*

   *NB: this repo stays the source of truth, but a release is now a MERGE - so a fix typed into the semester's copy survives, and the `release.propagate_back` op offers it back here as a pull request ([08](08-release-materials-to-cohort.md#carrying-semester-edits-back)).*

<a id="materialsyml"></a>

   *NB: an optional `materials.yml` at the repo root names the syllabus the home page pins:*

   ```yaml
   syllabus: E1282_syllabus.pdf   # default SYLLABUS.md
   ```

   *NB: the console's **Weekly plan** writes every session with its date and readings into
   your Markdown syllabus, between `<!-- dsl:weekly-plan -->` and `<!-- /dsl:weekly-plan -->`
   (added under `## Weekly plan` at the end the first time). Move the marked block anywhere;
   the next Write updates it there. A PDF syllabus: use Copy and paste it in.*

3. **Run Refresh actions** in the course org's `.github` Actions tab - only after creating a
   *new repo*, not after pushing content into one.
   - What it repopulates is the repo dropdowns - the materials repos, the assignment list,
     the semester list - and it runs itself nightly. New sessions need no refresh.

## Next

- [Add an assignment](03-add-assignment-to-course.md).
- [Schedule releases](07-schedule-releases.md) - plan the semester up front, the primary path.
- [Release to a semester](08-release-materials-to-cohort.md) - open sessions up to students by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New materials repo.
