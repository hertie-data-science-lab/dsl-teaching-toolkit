# Faculty & instructors actions reference

Every workflow, one line each. They all live in the **course org's `.github` Actions tab**.
**Release materials** and **Release assignment** *also* live inside each content repo.

Step-by-step flows: [workflow runbooks](../README.md). File layouts and CSV columns:
[`DEPLOYMENT-CHECKLIST.md`](../DEPLOYMENT-CHECKLIST.md). Who may run each workflow, and which team
grants it: [`access-reference.md`](access-reference.md).

## Setup

| Action | Effect |
| --- | --- |
| **Bootstrap cohort** | Configure a pre-created cohort org: `welcome` + `classroom-config`, permissions, site, `course_admins`, register + refresh. Safe to re-run on a live cohort - your `classroom-config` files are never overwritten. |
| **New materials repo** | Scaffold a `course-materials-<tag>` repo (lectures/readings/labs session folders, `SYLLABUS.md`, the run-from-repo Release workflows). |
| **New assignment** | Scaffold an `assignment-N-<tag>` template: brief + starter on `main`; stub solution, `grading.yml` and a hidden test on the `solution` branch. `format` picks py/notebook stubs; `type: group` makes handout + grading run per team. |
| **Generate syllabus** | Write the syllabus's "Course sessions and readings" section - one block per session, with its title, learning objectives and reading list - from a cohort's `schedule.yml` and this repo's `readings/`. Lands in `SYLLABUS.sessions.md` beside your syllabus, never released to students; it never touches `SYLLABUS.md` itself. |
| **Refresh actions** | Re-seed the run-from-repo workflows, propagate the repo secret, repopulate every dropdown, rebuild the profile READMEs. No inputs. Also runs itself daily, so every org converges on central `main` within 24h without anyone clicking. _(All DSL orgs at once: [Refresh Course Orgs Inventory](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/refresh-inventory.yml).)_ |
| **Check cohort setup** | Read-only per-cohort checklist of what's configured and what's missing, with an edit link for each gap. |
| **Sync membership** | Reconcile `students`/`auditors` teams (`students.csv`), project teams (`teams.csv`) and instructor/course-admin access (`people.yml`, `dsl-course.yml`). Automatic on push to those files, plus a daily cron - run it by hand only to apply a `start`/`end` date that rolled over without an edit. See [05](../05-manage-teaching-team.md), [`access-reference.md`](access-reference.md). |

## Release

| Action | Effect |
| --- | --- |
| **Scheduled release** | **Primary** - the hourly cron fires the cohort's `releases` plan and freezes passed deadlines. Manual runs default to `dry_run=true`. See [07](../07-schedule-releases.md). |
| **Release materials** | Copy `course_source_path` (a folder, a file, or a comma-separated list) from a course-org `course_source_repo` into the cohort's `cohort_dest_repo` at `cohort_dest_path` - the same four fields as a `schedule.yml` `deploy`. Covers session folders, datasets, root files and code subpackages alike. _Fallback - see [07](../07-schedule-releases.md), [08](../08-release-materials-to-cohort.md)._ |
| **Release assignment** | Freeze a cohort template from the chosen `assignment-*`, then generate one private `<slug>-<handle>` repo per onboarded student. `include_solution` and `dry_run` default off; `type` defaults to `auto` (follow `schedule.yml` / the template's `grading.yml`). _Fallback - see [07](../07-schedule-releases.md), [09](../09-release-assignment-to-cohort.md)._ |
| **Send enrolment codes** | Generate an `enrol_code` per roster row, write it back to `students.csv`, email each not-yet-onboarded student theirs. **`dry_run` defaults to `true` - nothing is written or sent until you untick it.** |
| **Sync site** | Regenerate a cohort's website. Releases, a push to `schedule.yml` and a daily cron already do this for you. |

## Grades

Full flow: [Grade and return assignments](../10-grade-and-return-assignments.md).

| Action | Effect |
| --- | --- |
| **Grade assignment** | Faculty-side autograder: pins each submission to the frozen deadline snapshot, runs the template's hidden tests, writes `autograde_score` (and `team`, on a group assignment) into `classroom-config/grades/<slug>.csv`. Nothing is written to student repos. |
| **Sync gradebooks** | Ensure every onboarded, enrolled student has a private `grades-<handle>` repo (student = read). Idempotent. |
| **Render grades (preview)** | Pivot the grade CSVs into `gradebook/<handle>.yml` + a wide `cohort-gradebook.csv`, and open **one** PR in `classroom-config` - that diff is the preview. |
| **Distribute grades** | After merging that PR: push each gradebook to the student's private repo and email them. **`dry_run` defaults to `true`**; `silent` pushes without emailing. |

## Optional: public course website

| Action | Effect |
| --- | --- |
| **Publish course website** | Build/refresh a **public** `<course-org>.github.io` sharing this course's lectures + readings. Pick a `source_repo`; `readings_mode` = `reading-list` (citations only, default), `actual-readings` (host the files) or `none`. The first run opts in and records its settings in `_publish-config.yml`; a daily cron re-syncs from them - delete that file to stop. |

## When a scheduled run fails

The five scheduled actions (Scheduled release, Sync membership, Sync site, Refresh actions,
Publish course website) run with nobody watching, so a failure **opens an issue in your
`.github` repo** titled *"&lt;action&gt; is failing"*, with a link to the run and a cc to your
org's `course-admin` team, so it reaches an inbox. It comments on
that same issue while the failure persists, and closes it as soon as a run succeeds - so an
open one always means "still broken". Don't close it by hand; fix the cause and re-run the
action.
