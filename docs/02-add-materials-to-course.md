# Add materials to the course org

Create the year's materials repo and fill it with lectures + readings. **Release materials**
later copies session folders from here into a cohort. One repo per year: `course-materials-{f/s}YYYY`.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- Push access on its content repos, from either: 
   1. `course-admin` membership (course org), or
   2. being declared an instructor/TA in a cohort org's `classroom-config/people.yml`

> see [Manage the teaching team](05-manage-teaching-team.md) for full access details.

## Steps

Live example: [`example-course/course-org/course-materials-f2026/`](../example-course/course-org/course-materials-f2026). Its [`SYLLABUS.md`](../example-course/course-org/course-materials-f2026/SYLLABUS.md) is the same file you receive as `SYLLABUS.md.sample` beside your own - the toolkit seeds it from there, so the two cannot drift.

1. **Scaffold the repo.** 
   - Course org → `.github` → **Actions** →
   [New materials repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-materials.yml); input `tag` = `fYYYY`/`sYYYY` (e.g. `f2026`) 
   - this creates **`course-materials-f2026`** - a private repo, pre-seeded with (edit as needed):
      - `lectures/01_session-1/`, 
      - `readings/01_session-1/`, 
      - `labs/01_session-1/`, 
      - a `README.md` 
      - a `MAINTAINING.md`, 
      - a placeholder `SYLLABUS.md` 
      - a commented-out `.releaseignore` 
      >It also seeds the two run-from-repo Release workflows (Release materials, Release assignment). 
   - `copy_from` (optional) starts the new repo as an existing `course-materials-*` instead
     of as the skeleton - every branch, every file, the whole history. Your content arrives
     as you left it; only `MAINTAINING.md`, `SYLLABUS.md.sample` and the workflows are
     rewritten. This is how a course carries forward from one year to the next.
   - You have push on it immediately.

2. **Push your content** to remote's `main` (git push or the web uploader):

   ```
   lectures/01_session-1/   any files - slides, demo code, notebooks …
   readings/01_session-1/   just drop the readings in - every hosted file here is listed
                            and linked for enrolled students to access automatically.
                            Additionally, an optional READINGS.md (or .txt/.bib) acts as an overlay, to add what a directly hosted file cannot express (a URL, pointers, page & chapter numbers to focus on, or metadata in clean citation format); it is published publicly on the deployed site (whereas the hosted files themselves are privately hosted and accessible to enrolled students and auditors only)
   labs/01_session-1/       any files 
   SYLLABUS.md              optional - or any name you like (SYLLABUS.pdf, ...):
                            a root file releases by being named as the path
   ```

   Any top-level directory holding ordinal-prefixed subdirectories is releasable:
   - You can further add your own sections freely (e.g. `datasets/`). 
   - Only the leading ordinal (`01_`, `02_`, …) matters -
   name the rest however is clearest (`01_intro`, `02_regression`, …).

   *NB: this repo stays private - students never see it. Only the sessions you **actively release** reach the cohort org, so you can privately stage the whole course here.*

   *NB: to withhold a file from every release, name it in `.releaseignore` - `.gitignore` syntax, in any folder ([08](08-release-materials-to-cohort.md#withholding-files-with-releaseignore)).*

   *NB: a session folder is released whole, subfolders included ([11](11-configure-cohort-site.md)).*

   *NB: material with no `NN_` session folder (a root `SYLLABUS.md`, a flat `datasets/`) still releases, and appears on the cohort site's **All Materials** tab.*

   *NB: this repo stays the source of truth, but a release is now a MERGE - so a fix typed into the cohort's copy survives, and **Propagate cohort edits** offers it back here as a pull request ([08](08-release-materials-to-cohort.md#carrying-cohort-edits-back)).*

3. **Run Refresh actions** in the course org's `.github` Actions tab - only after creating a
   *new repo*, not after pushing content into one.
   - What it repopulates is the repo dropdowns - `course_source_repo`, the assignment list,
     the cohort list - and it runs itself nightly. The Release workflows take a typed
     `course_source_path`, so new sessions need no refresh.

## Next

- [Add an assignment](03-add-assignment-to-course.md).
- [Schedule releases](07-schedule-releases.md) - plan the term up front, the primary path.
- [Release to a cohort](08-release-materials-to-cohort.md) - open sessions up to students by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New materials repo.
