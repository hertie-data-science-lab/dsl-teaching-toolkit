# Add materials to the course org

Create the year's materials repo and fill it with lectures + readings. **Release materials**
later copies files and folders from here into a semester. One repo per year: `course-materials-{f/s}YYYY`
by default. What makes a repo a materials repo is its `dsl-materials` topic, which the scaffold sets;
add the topic to a repo you made by hand to have it listed as one.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- Push access on its content repos, from either: 
   1. `course-admin` membership (course org), or
   2. being declared an instructor/TA in a semester org's `semester-config/instructors.yml`

> see [Manage the instructors](05-manage-teaching-team.md) for full access details.

## Steps

Live example: [`example-course/course-org/course-materials-f2026/`](../example-course/course-org/course-materials-f2026). Its [`SYLLABUS.md`](../example-course/course-org/course-materials-f2026/SYLLABUS.md) is the same file you receive as `SYLLABUS.md.sample` beside your own - the toolkit seeds it from there, so the two cannot drift.

1. **Scaffold the repo.** 
   - Course org → `.github` → **Actions** →
   [New materials repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-materials.yml); input `semester` = `fYYYY`/`sYYYY` (e.g. `f2026`) 
   - this creates **`course-materials-f2026`** - a private repo, pre-seeded with (edit as needed):
      - `lectures/01_session-1/`, 
      - `readings/01_session-1/`, 
      - `labs/01_session-1/`, 
      - a `README.md` 
      - `.system/MAINTAINING.md` (your operating notes) and `.system/SYLLABUS.md.sample`, 
      - a placeholder `SYLLABUS.md` 
      - a commented-out `.releaseignore` 
      - a `publish.yml` 
      >It also seeds the two run-from-repo Release workflows (Release materials, Release assignment). 
   - `public_dirs` / `public_types` (optional) fill in that `publish.yml`: which folders,
     and which file types out of them, the semester site may **host publicly** so they open
     rendered in a browser instead of showing as source on GitHub. Both default to
     publishing nothing. "lectures" and "readings" mean the folders of that kind (the
     folder names below), not folders of that name. Everything unmatched stays private
     to enrolled students, exactly as today; `solution/`, `tests/`, grading files and
     `.env` are never hosted whatever you write. Unlike `.gitignore`, a negated folder
     (`!labs/sub/`) excludes its whole subtree. Edit `publish.yml` afterwards - no workflow rewrites it, and it applies to
     every semester of this course ([11](11-configure-cohort-site.md)).
   - `copy_from` (optional) starts the new repo as an existing materials repo instead
     of as the skeleton - every branch, every file, the whole history. Your content arrives
     as you left it; only `.system/` and the workflows are
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

   The layout is yours: any file or folder is releasable, and a row on the semester site is a
   schedule entry, not a folder. The skeleton's `01_` prefixes are a starter, never needed.
   An entry that declares no `kind` takes it from the top folder its copy lands in: `labs/`,
   `lab/`, `tutorials/` are labs, `readings/`, `reading/`, `literature/` readings, anything
   else a lecture. `materials.yml` (below) covers the rest.

   *NB: this repo stays private - students never see it. Only the sessions you **actively release** reach the semester org, so you can privately stage the whole course here.*

   *NB: to withhold a file from every release, name it in `.releaseignore` - `.gitignore` syntax, in any folder ([08](08-release-materials-to-cohort.md#withholding-files-with-releaseignore)).*

   *NB: a session folder is released whole, subfolders included ([11](11-configure-cohort-site.md)).*

   *NB: material no schedule entry names (a manual release, a flat `datasets/`) appears on the semester site's **All Materials** tab.*

   *NB: this repo stays the source of truth, but a release is now a MERGE - so a fix typed into the semester's copy survives, and **Propagate semester edits** offers it back here as a pull request ([08](08-release-materials-to-cohort.md#carrying-semester-edits-back)).*

<a id="materialsyml"></a>

   *NB: an optional `materials.yml` at the repo root says what the convention cannot:*

   ```yaml
   syllabus: E1282_syllabus.pdf   # the syllabus the home page pins (default SYLLABUS.md)
   kinds:                         # a top folder -> the kind of the rows it feeds
     quiz: exam
     seminars: lab
   ```

   *`kinds:` keys name the top folder the copy LANDS in (its semester-side path), in any
   case. A file that does not parse stops the site sync and says so.*

3. **Run Refresh actions** in the course org's `.github` Actions tab - only after creating a
   *new repo*, not after pushing content into one.
   - What it repopulates is the repo dropdowns - `course_source_repo`, the assignment list,
     the semester list - and it runs itself nightly. The Release workflows take a typed
     `course_source_path`, so new sessions need no refresh.

## Next

- [Add an assignment](03-add-assignment-to-course.md).
- [Schedule releases](07-schedule-releases.md) - plan the semester up front, the primary path.
- [Release to a semester](08-release-materials-to-cohort.md) - open sessions up to students by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New materials repo.
