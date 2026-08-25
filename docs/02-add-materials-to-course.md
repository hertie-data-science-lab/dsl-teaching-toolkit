# Add materials to the course org

Create the year's materials repo and fill it with lectures + readings. **Release materials**
later copies session folders from here into a cohort. One repo per year: `course-materials-{f/s}YYYY`.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md).
- Push access on its content repos, this involves either: 
   1. `course-admin` membership (course org), or
   2. being declared an instructor/TA in a cohort's org's `classroom-config/people.yml`

> see [Manage the teaching team](05-manage-teaching-team.md) for full access details.

## Steps

Live example: [`example-course/course-org/course-materials-f2026/`](../example-course/course-org/course-materials-f2026).

1. **Scaffold the repo.** 
   - Course org → `.github` → **Actions** →
   [New materials repo](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-materials.yml); input `tag` = `f/sYYY` 
   - this creates **`course-materials-f2026`** - a private repo, pre-seeded with (edit as needed):
      - `lectures/01_session-1/`, 
      - `readings/01_session-1/`, 
      - `labs/01_session-1/`, 
      - a `README.md` 
      - a `MAINTAINING.md`, 
      - a placeholder `SYLLABUS.md` 
      >It also seeds the three Release buttons & later scheduled release functionality. 
   - You have push on it immediately.

2. **Clone the repo locally**
   - This allows you to make local edits and replace with your own content.
   
2. **Push your content** to remote's `main` (git push or the web uploader):

   ```
   lectures/01_session-1/   any files - slides, demo code, notebooks …
   readings/01_session-1/   a text file (reading.md/.txt/.bib) IS the reading list
                            published on the cohort site; other files are linked
   labs/01_session-1/       any files 
   SYLLABUS.md              optional (any root file matching *syllabus*)
   ```

   Any top-level directory holding ordinal-prefixed subdirectories is releasable:
   - You can further add your own sections freely (e.g. `datasets/`). 
   - Only the leading ordinal (`01_`, `02_`, …) matters -
   name the rest however is clearest (`01_intro`, `02_regression`, …).

   *NB: this repo stays private - students never see it. Only the sessions you **actively release** reach the cohort org, so you can privately stage the whole course here.*

   *NB: a session folder is released whole, subfolders included - but the site lists its root files plus one link per subfolder, so a rendered deck links the deck rather than its hundreds of assets ([11](11-configure-cohort-site.md)).*

3. **Run Refresh actions** in the course org's `.github` Actions tab. 
   - This updates the `session` dropdown and each section's checkbox with what you just pushed.

## Next

- [Add an assignment](03-add-assignment-to-course.md).
- [Schedule releases](07-schedule-releases.md) - plan the term, and never click a release button.
- [Release to a cohort](08-release-materials-to-cohort.md) - open sessions up to students by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New materials repo.
