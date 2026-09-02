# Deep Learning (Demo) Course

>_This page is auto-generated - do not edit; manual edits are overwritten on the next refresh._

This is the dedicated **Deep Learning (Demo)** **course org** - persistent across years. It acts as:
1. A **private staging area** for pre-release version-controlled materials & assignments,
2. A **historical record** of past years' materials,
3. A **central control panel** for instructors to run workflows from, via the seeded [`.github` Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions).

The substantive repos of this org are private (not accessible to enrolled students); each year's student-facing interface lives in a separate **cohort org** that receives releases from here.

> **Faculty & instructors - start here:** New to the platform?
> Follow the step-by-step
> **[workflow runbooks](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/main/docs/README.md)**.
> The sections below are a live index of this org's cohorts, repositories, and actions.

## Cohorts

List of cohort orgs registered to receive releases from this course org. _Auto-discovered from the
`cohort-courses-pages.yml` registry_:

- [hertie-dsl-demo-f2026](https://github.com/hertie-dsl-demo-f2026)

## Repositories

List of all repositories associated with the course org. _Auto-discovered from the org's live repositories_.

| Repo | Visibility | Description |
| --- | --- | --- |
| [assignment-1-f2026](https://github.com/hertie-dsl-demo-course-e1234/assignment-1-f2026) | private | Assignment 1 template |
| [assignment-2-f2026](https://github.com/hertie-dsl-demo-course-e1234/assignment-2-f2026) | private | Assignment 2 template |
| [assignment-4-project-f2026](https://github.com/hertie-dsl-demo-course-e1234/assignment-4-project-f2026) | private | Assignment 4-project template |
| [course-materials-f2026](https://github.com/hertie-dsl-demo-course-e1234/course-materials-f2026) | private | Course materials (lectures/labs/readings/datasets/other) by session |

Edit & stage new course-related content in these, then release it to the relevant cohort org.

## Available actions for faculty, instructors & admin

All actions live in the [`.github` repo's Actions tab](https://github.com/hertie-dsl-demo-course-e1234/.github/actions)
_(automatically bootstrapped from the central
[dsl-teaching-toolkit repo](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit))_:

### Run directly by you (course instructors):

| Action | What it does | Managed |
| --- | --- | --- |
| [**Bootstrap cohort**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/bootstrap-cohort.yml) | Configures a freshly-created cohort org (sets up scaffold repos, registers it with the course org, seeds workflow functionality). | Run by instructor |
| [**Send enrolment codes**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/send-codes.yml) | Generates enrolment codes for each student and emails each their code (to their Hertie email address). Students paste the code into the welcome Join course issue. This keeps personal data out of the public repo. There is no button: a push to a cohort's `students.csv` is what fires it, and it sends for real - so a re-send means clearing that row's `code_sent_at` and pushing again. | Automatic |
| [**New materials repo**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-materials.yml) | Scaffolds a correctly-structured `course-materials-<year>` repo (session folders + the Release workflows). Ready for material to be added. | Run by instructor |
| [**New assignment**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-assignment.yml) | Scaffolds an `assignment-N-<year>` template repo (starter on `main`; the `solution` branch carries the model solution, `grading.yml`, and the hidden tests). | Run by instructor |
| [**Generate syllabus**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/generate-syllabus.yml) | Writes the "Course sessions and readings" section of a syllabus - one block per session, with its title, learning objectives and reading list - from a cohort's `classroom-config/schedule.yml` and this repo's `readings/` folders. It lands in `SYLLABUS.sessions.md` beside your syllabus (never released to students) and never edits `SYLLABUS.md` itself. | Run by instructor |
| [**Check cohort setup**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/check-cohort-setup.yml) | A per-cohort checklist of everything configured (identity, people, schedule + release plan, roster, teams, grades) with direct edit links for anything missing. Read-only. | Run by instructor |
| [**Publish course website**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/publish-site.yml) | **[OPTIONAL]** **[DEFERRED]** Build/refresh a public openware site for the course `hertie-dsl-demo-course-e1234.github.io`. This will share this course's lecture materials and (limited) readings with the open internet. Opt-in (the first run scaffolds the site); afterwards a daily cron re-syncs it from the settings that run chose, so later materials edits appear without another click. Pick a materials repo and choose for readings: `reading-list` (citations only) or `actual-readings` (also host the files). Because the materials repos are private, the site **hosts** the shared files itself. This is separate from each cohort's student-facing site. | Run by instructor |
| [**Release materials**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-materials.yml) | Manually release materials to student-facing cohort orgs *(NB: it is recommended to instead use the [scheduling function](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/main/docs/07-schedule-releases.md) for regular releases)*. Select path(s) for any folder or file, one or several at a time. | Run by instructor |
| [**Release assignment**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/release-assignment.yml) | Generate one private repo per student from a chosen `assignment-*` template repo. *(NB: it is recommended to instead use the [scheduling function](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/main/docs/07-schedule-releases.md) for regular releases)* | Run by instructor |
| [**Grade assignment**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/grade-assignment.yml) | Faculty-side autograder: after the deadline, run the HIDDEN tests (from the template's `solution` branch) against each submission and record the machine score into `classroom-config/grades/<assignment>.csv`. Nothing is written to student repos; faculty & instructors then add manual marks. Optional per assignment (skipped if `grading.yml` sets `autograde: false`). | Run by instructor |
| [**Sync gradebooks**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/sync-gradebooks.yml) | Ensure every onboarded student has a PRIVATE `grades-<handle>` repo (the single home for all their grades). | Run by instructor |
| [**Render grades (preview)**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/render-grades.yml) | Build per-student `gradebook/<handle>.yml` from `classroom-config/grades/<assignment>.csv` and open ONE pull request. **That PR is the preview** - review every student's grades in the diff before sending. | Run by instructor |
| [**Distribute grades**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/distribute-grades.yml) | After merging the preview PR, copy each student's gradebook into their private repo and (unless silenced) email each student a notification to their Hertie email address (needs the `GRAPH_*` secrets). | Run by instructor |

NB: alternatively each materials repo *also* carries its own **Release** workflows (run from inside the repo).

---

### Automatically handled within the pipeline as standard

The following are runnable by explicit ad hoc manual dispatch; course instructors can mostly ignore them:

| Action | What it does | Managed |
| --- | --- | --- |
| [**Sync membership**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/sync-membership.yml) | Reconciles org + `students`-team access (from `students.csv`), project teams (from `teams.csv`), `course_admins` (from this org's declared `people:` block, mirrored into every cohort's own `course-admin` team), and each cohort's own `instructors`/`teaching_assistants` (from its `classroom-config/people.yml`, reconciled into that cohort's `instructors` team AND a course-org `instructors-<tag>` team).<br><br> Triggers on (1) push (editing any of those files takes effect immediately, including removals so that the file is the live truth) and (2) on a daily cron (catches a faculty entry's `start`/`end` rotation with no edit that day);`workflow_dispatch` is a manual escape hatch. | Auto-handled |
| [**Refresh actions**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/refresh-actions.yml) | Repopulates the cohort/source-repo/assignment dropdowns, re-equips content repos, and rebuilds this index. Runs itself nightly, so this org stays in step with the central toolkit on its own. | Auto-handled |
| [**Scheduled release**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/scheduled-release.yml) | Hourly cron that auto-releases whatever each cohort's `classroom-config/schedule.yml` `releases:` plan says is now due (honouring each entry's `event_datetime` / `deploy_datetime` to the hour). Manual runs default to a dry-run preview ("what opens when"). The manual workflows above still work for early/ad-hoc release. | Auto-handled |
| _[**Sync site**](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/sync-site.yml)_ | _Regenerate a cohort's website from the org structure (releases do this automatically; standard workflow has no need for manual sync)._ | Auto-handled |


## Repository structure (required)

```
hertie-dsl-demo-course-e1234/                            <- this COURSE org (persistent)
|-- .github/                      profile + faculty & instructor workflows (see Actions tab) + cohort registry
|-- course-materials-<year>/      lectures/01_.../   readings/01_.../   (+ syllabus, README)
`-- assignment-<n>-<year>/        is_template repo:
                                    main      -> starter + autograder   (students get this)
                                    solution  -> solution/   (pushed to students on demand)

<Course>-f<year>/                 <- one COHORT org per year (Bootstrap cohort sets it up)
|-- welcome/                      Join issue -> onboard (enrol)
|-- classroom-config/             students.csv  (private roster)
|-- materials/                    released lectures/readings  (students-team read)
|-- <org>.github.io/              auto-deployed website (synced from this structure)
`-- <assignment>-<handle>/        one private repo per student
```

This whole structure is fully bootstrapped from the central [`dsl-teaching-toolkit`](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit) repo (via its **Bootstrap Course Org** action), and the actions above run that same central code.

The course-level actions assume this layout - use **New materials repo** / **New assignment** above to scaffold correctly. These scaffolds are designed to be generic & non-prescriptive, however if these formats to not suit your intended course delivery structure, please contact the DSL (`h.baker@hertie-school.org`).

### Materials repo

(`course-materials-<year>`) - the source for Release materials. Any path in it can be released. The convention below is what the downstream pipeline transformations expect: ordinal-prefixed (`01_`, `02_`, `03_`, ...) sub-directories, which is the only constraint. The following are seeded automatically, but edit them as you wish:
- `lectures/01_.../` - one folder per session's lecture files;
- `readings/01_.../` - one folder per session's readings;
- root files (`SYLLABUS.md`, `README.md`).

Add more sections freely (e.g. `labs/01_.../`, `datasets/01_.../`).

> Alternatively you could have `sessions/01_.../` with lectures & readings combined - or however you prefer to set it up. The only constraint is the ordinal-prefixed subdirectories.

### Assignment repo

`assignment-N-<year>` (an `is_template` repo) - the source for Release assignment:
- **`main` branch** - the starter code only (no tests, no autograder). This is exactly what students receive (native template-generate copies `main` only).
- **`solution` branch** - the model solution (`solution/`), plus **`grading.yml`** and the **hidden tests** that the Grade assignment workflow runs faculty-side. **All of this MUST live on this branch, never on `main`** - that is what guarantees it is never copied into student repos on generate. Only the `solution/` folder reaches students, and only when you run Release assignment with **include_solution** ticked (a separate, later commit); the hidden tests and `grading.yml` never do.

## Further details on how the actions behave

**Release materials** - run it from the materials repo (`course_source_repo` pre-filled with
that repo) or from the course org's central `.github` control panel (`course_source_repo` is
a dropdown). **Both** take the same five fields, which are exactly a `schedule.yml` `deploy:`
entry: `cohort_org`, `course_source_repo`, `course_source_path`, `cohort_dest_repo`,
`cohort_dest_path` - so the manual workflow and the scheduled release plan share one
vocabulary. `course_source_path` is any folder or file (`lectures/03_regression`,
`mlpkg/simulation`, `SYLLABUS.md`); a folder is copied whole, **every file** in it.
`course_source_path` and `cohort_dest_path` accept comma-separated lists paired in order, so
one click can release several paths at once; a blank `cohort_dest_path` mirrors each source
path. `cohort_dest_repo` (default `materials`) is created on demand, private, with
`students` **and** `auditors` read. Copies are additive and idempotent: only what you have
released appears, and re-releasing changes nothing.

**Release assignment** - two stages: (1) it freezes a cohort-level template repo
`<assignment>` from your `assignment-*-<year>` template; (2) it generates one private
`<assignment>-<handle>` repo per onboarded student **from that cohort template**, adding
each as collaborator. After the assignment deadline, rerun with **include_solution** to push the
template's `solution` branch into every student repo. Solutions stay on the `solution`
branch so a normal release never leaks them.

**The cohort website** - every cohort has an auto-deployed site `<org>.github.io`. It is regenerated
on every release (and via **Sync site**). Its lecture links point at the cohort's private repos, so
they only resolve for enrolled members (deliberate).

**The public course website** (optional) - `Publish course website` builds `hertie-dsl-demo-course-e1234.github.io`, a public
open-courseware site for the course as a whole. Unlike the cohort sites it **hosts** the shared lecture
files (the source repos are private, so links would 404); readings are published either as a text-only
reading list or as hosted files. It is opt-in - releases and refresh never touch it, so a public site
only exists once you run the action - but after that first run a daily cron re-syncs it from the
settings you chose, so later materials edits reach it on their own.

---
Maintained by the [Hertie Data Science Lab](https://github.com/hertie-data-science-lab).
