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
| **New assignment** | Scaffold an `assignment-N-<tag>` template from eight answers (name, number, tag, format, type, team_formation, submit_via, autograde): brief + starter (+ `CONTRIBUTIONS.md` for a group) on `main`; stub solution and `grading_config.yml` on the `solution` branch, with a hidden-test stub only when autograding was asked for. `format` picks the starter stub and nothing else; `type: group` makes handout + grading run per team. |
| **Derive student version** | Write a template's student starter onto `main` from the ONE notebook you keep on its `solution` branch: `### BEGIN SOLUTION` / `### END SOLUTION` regions, `solution`-tagged cells and `solution=TRUE` Rmd chunks are replaced with placeholders, and a stripped code cell loses its stored outputs. It never writes to `solution`, and a file with nothing fenced in it is never written at all. **`dry_run` defaults to `true`** and prints the file list and counts only. See [03](../03-add-assignment-to-course.md#one-notebook-not-two-derive-the-starter). |
| **Generate syllabus** | Write the syllabus's "Course sessions and readings" section - one block per session, with its title, learning objectives and reading list - from a cohort's `schedule.yml` and this repo's `readings/`. Lands in `SYLLABUS.sessions.md` beside your syllabus, never released to students; it never touches `SYLLABUS.md` itself. |
| **Refresh actions** | Re-seed the run-from-repo workflows, propagate the repo secret, repopulate every dropdown, rebuild the profile READMEs. No inputs. Also runs itself daily, so every org converges on the toolkit tier its course org runs within 24h without anyone clicking. _(All DSL orgs at once: [Refresh Course Orgs Inventory](https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/actions/workflows/refresh-inventory.yml).)_ |
| **Check cohort setup** | Read-only per-cohort checklist of what's configured and what's missing, with an edit link for each gap. |
| **Sync membership** | Reconcile `students`/`auditors` teams (`students.csv`), project teams (`teams.csv`) and instructor/course-admin access (`people.yml`, `dsl-course.yml`), and rewrite `assignments.lock.yml` - the generated mirror the **Join team** form reads (hence the `schedule.yml` trigger). Automatic on push to any of those files, plus a daily cron - run it by hand only to apply a `start`/`end` date that rolled over without an edit. See [05](../05-manage-teaching-team.md), [`access-reference.md`](access-reference.md). |

## Release

| Action | Effect |
| --- | --- |
| **Scheduled release** | **Primary** - fires the cohort's `releases` plan and freezes passed deadlines, roughly every 15 minutes. Manual runs default to `dry_run=true`. See [07](../07-schedule-releases.md#what-drives-the-scheduler). |
| **Release materials** | Copy `course_source_path` (a folder, a file, or a comma-separated list) from a course-org `course_source_repo` into the cohort's `cohort_dest_repo` at `cohort_dest_path` - the same four fields as a `schedule.yml` `deploy`. Covers session folders, datasets, root files and code subpackages alike. _Fallback - see [07](../07-schedule-releases.md), [08](../08-release-materials-to-cohort.md)._ |
| **Release assignment** | Freeze a cohort template from the chosen `assignment-*`, then generate one private `<slug>-<handle>` repo per onboarded student. `include_solution` and `dry_run` default off; `type` defaults to `auto` (follow `schedule.yml` / the template's `grading_config.yml`). _Fallback - see [07](../07-schedule-releases.md), [09](../09-release-assignment-to-cohort.md)._ |
| **Patch released assignment** | Push a corrected file (or folder) from the template's default branch into every submission repo of an assignment already handed out - a new commit on each student's branch, never a force-push - and post a note on each Feedback issue. A file the student has already changed is kept unless `overwrite` says otherwise; the frozen cohort-side hand-out is patched too, so later onboarders get the fix. **`dry_run` defaults to `true`**. See [09](../09-release-assignment-to-cohort.md#fixing-a-file-after-the-assignment-has-gone-out). |
| **Send enrolment codes** | **No button** - it runs only on a push to a cohort's `students.csv`, and it sends for real. Generates an `enrol_code` per roster row, writes it back to `students.csv`, emails each not-yet-onboarded student theirs. Safe on every push: a row's `code_sent_at` stops it being mailed twice (from the first run that sets it), so a re-send means clearing that cell and pushing. |
| **Sync site** | Regenerate a cohort's website. Releases, a push to `schedule.yml` and a daily cron already do this for you. |

## Grades

Full flow: [Grade and return assignments](../10-grade-and-return-assignments.md).

| Action | Effect |
| --- | --- |
| **Collect submissions** | Refresh one assignment's grading sheet now instead of waiting for the cron: re-read each submission, refill `info:`, post any receipt still owed. It never freezes anything - the cutoff does that. |
| **Distribute grades** | Send what the grading sheet holds: a feedback comment on each submission repo's Feedback issue, each student's private `grades-<handle>` repo (`grades.yml` + `README.md`), `cohort-gradebook.csv`, and an email. Nothing is said twice, so a re-run after one correction reaches one student. **`dry_run` defaults to `true`**; `silent` sends without emailing; `assignment` narrows the run to one slug (blank = every sheet). |

## End of term

| Action | Effect |
| --- | --- |
| **Archive cohort** | Close a finished cohort out: revoke each student's direct access to the submission repos and gradebooks named after them, archive those repos, archive `welcome` so a finished term cannot still be joined, record what was frozen in `classroom-config/archive/teardown.md`, and archive `classroom-config` last - which also tells the nightly refresh to leave the cohort alone. **Nothing is deleted** and archiving is reversible from each repo's Settings. **`dry_run` defaults to `true`**; a real run refuses unless `schedule.yml`'s `semester_end` has passed, and `force` overrides that. Safe to re-run - it resumes. See [10](../10-grade-and-return-assignments.md#closing-the-cohort-out). |

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

On the same throttle, the **toolkit maintainer is emailed** the run's URL and the last 30
lines of the step that failed. A broken run is infrastructure rather than teaching, so the
issue says so too: nothing here is for teaching staff to do. The two channels fire together
or not at all - an email always has an issue behind it, and a thread being kept quiet for
six hours sends no email either.

**Scheduled release** reports per job, because it releases and grades separately: the release
pass keeps *"Scheduled release is failing"*, and a cohort whose autograding fails gets its own
*"Scheduled release (autograde &lt;cohort&gt;) is failing"* - so one stuck cohort neither hides
the others nor delays them.

Two further issues watch the schedule being *kept* rather than a run's exit code, and also close
themselves. What opens each one is in
[07](../07-schedule-releases.md#what-drives-the-scheduler):

- *"Scheduled release: driver health"*, in your `.github`, cc `course-admin`.
- *"Scheduled release: late delivery"*, in the cohort's private `classroom-config`, cc that
  cohort's `instructors`.
