# Notifications - issues and PRs the system opens

Prose the system writes into GitHub issues and pull requests, plus the short strings it
writes as repo, team and label descriptions. Faculty read all of these. Edit the text;
I'll port it back to the generator named under each.

Placeholders in `{braces}` are filled at runtime. Demo values elsewhere:
course org `hertie-dsl-demo-course-e1234`, cohort org `hertie-dsl-demo-f2026`.

---

## 1. Grades preview PR
`dsl_course/grades.py:581-587` · opened in the cohort's `classroom-config` (private) by
**Render grades (preview)**. Reused if one is already open on the render branch.

**Title:** `Grades: review before distribution`

```
Rendered {n} gradebook(s) from `grades/`.

**This is the preview.** Review every student's grades in the diff below, then merge to distribute to each private `grades-<handle>` repo.
```

---

## 2. Site-sync overwrite notice
`dsl_course/site_repo.py:813-905` · opened in the cohort's site repo (PUBLIC) when a sync
replaces a hand-edited generated file. Deduped by exact title; commented on if already open.
Every failure here is swallowed - a notice must never red the sync.

**Title:** `Manual edits to generated site files are overwritten by the sync`

```
The site sync regenerates parts of this repo from the org structure, so an edit made directly here is replaced the next time it runs. It has just replaced:

- `{path}` - edited by @{login} in [`{sha:0:7}`](https://github.com/{org}/{site}/commit/{sha})

Nothing is lost - each link above is the commit that was overwritten, so the change can be copied back out of it.

Make the edit at the source instead, and it survives every sync:

- **Staff cards** - the cohort's `classroom-config/people.yml` (for a public course site, the `people:` block of the course org's `.github/dsl-course.yml`).
- **Schedule rows, sessions, assignments** - the org structure and the cohort's `classroom-config/schedule.yml`.

The sync owns `_lectures/`, `_assignments/`, `_events/`, `_data/people.yml` and a few `_config.yml` keys, and names the source in a header where the file format allows one. Everything else in this repo is yours and is never rewritten.
```

One `- ` line per overwritten path. When a commit author's git email maps to no GitHub
account the row says `` `{git author name}` `` instead of `@{login}`, and this is appended
once:
```
cc @{org}/instructors - a commit author's email is not linked to a GitHub account, so they could not be mentioned directly.
```

---

## 3. Unattended cron failure
`dsl_course/workflows_render.py:252-306` · appended to every cron-bearing workflow
(**Sync membership**, **Sync site**, **Scheduled release**, **Refresh actions**,
**Publish course website**). Filed in the repo the workflow runs in - for the course-org
buttons that is the PUBLIC `.github`. Deduped by title; closes itself on the next green
run. Skipped for a `workflow_dispatch` failure (someone is watching that run); a manual
SUCCESS still closes the issue.

**Title:** `{workflow name} is failing`

**First report** (the only one that cc's anyone):
```
The unattended run failed or was cancelled: {RUN_URL}

cc @{org}/course-admin
```

**Repeat report** - a comment on the open issue, and only once the thread has been quiet
for six hours, without the cc (whoever it reached the first time is already subscribed):
```
The unattended run failed or was cancelled: {RUN_URL}

Nothing retries it before the next scheduled run. This issue closes itself once a run succeeds.
```

**Closing comment:** `Recovered: {RUN_URL}`

---

## 4. Schedule validation failure
`templates/classroom-config/validate-schedule.yml:123-155` · filed in the cohort's
`classroom-config` on a push that breaks `schedule.yml`, assigned to whoever pushed
(unassigned if that fails). Commented on if already open.

**Title:** `schedule.yml has entries the scheduler cannot read`

```
Validation of `schedule.yml` failed.

​```
{validator report - see section 5}
​```

Commit: {SHA}
Run: {RUN_URL}

A dropped entry is silently absent from the term plan - no release, or no deadline, snapshot or autograding. Fix `schedule.yml` on `main` and this issue closes itself.

Field reference: https://github.com/{central}/blob/{central_ref}/docs/07-schedule-releases.md
```

**Closing comment:** `schedule.yml now parses with nothing dropped.`

**Job-log annotations** from the same run (Actions renders these against `schedule.yml` in
the commit's own diff):
```
::error file=schedule.yml::schedule.yml has entries the scheduler cannot read - see the run summary
::warning file=schedule.yml::{one line per source fault - see section 5}
::notice::could not resolve this cohort's course org - the source check did not run
```

---

## 5. Schedule validator report
`dsl_course/schedule.py:1221-1240` (the parse) and `1513-1536` (the source check) ·
written to the run's job summary under a `## schedule.yml` heading and embedded verbatim
in the issue above.

```
Parsed ../cohort/schedule.yml
  term 2026-09-07 -> 2026-12-18  (Europe/Berlin)
  16 release(s), 20 deploy(s) | 3 assignment(s) | 4 event(s)

  {n} ENTRY/IES DROPPED:
    - {reason}
```

Then, when the run could resolve the course org (`--check-sources`), either:
```
  {n} SOURCE(S) NOT IN hertie-dsl-demo-course-e1234 YET:
    [error] {where} -> {field} (due {Tue 06 Oct 2026, 03:00}): {what is missing}

  A source you have not written yet looks exactly like this, so this is only
  a fault once its moment is close: advisory until 7 days out, then a warning, then an ERROR inside 48h - at which point it is about to ship nothing.
```
or:
```
  every source in the plan exists in hertie-dsl-demo-course-e1234
```

The verdict line last, and it counts DROPPED entries only - a missing source never fails
the run: `OK: nothing dropped` / `INVALID: {n} entry/ies dropped`, or
`INVALID: {file} could not be parsed` when the whole file is unreadable.

---

## 6. Missing-source digest
`dsl_course/source_digest.py` · ONE self-updating issue per cohort in its
`classroom-config`, kept in step by the hourly **Scheduled release** cron
(`scheduler._preflight_sources`). The body is rewritten every run (a body edit emails
nobody); a comment is posted only when a fault appears at or above WARNING or escalates a
rung; the issue closes itself when the last fault clears. Three emails over the life of a
problem. Silent entirely while nothing has passed WARNING and no issue is open.

**Title:** `schedule.yml: planned releases cite sources not staged in the course org`

**Body** (`render_body`, rewritten whole every run):
```
`classroom-config/schedule.yml` names sources that are not in `hertie-dsl-demo-course-e1234` right now. Each one ships nothing when its moment arrives.

Either stage the missing path in the course org, or correct the field named below. This issue rewrites itself every run and closes when the list empties.

### ERROR (1)

**Deploys within 48h (or already passed) - these will ship nothing.**

- **`releases.lecture-5`** -> `course_source_path`  
  `course-materials-f2026/lectures/05_week-5` does not exist yet  
  _due Tue 06 Oct 2026, 03:00_

### WARNING (1)

**Deploys within 7 days.**

- **`releases.lecture-9`** -> `course_source_path`  
  `course-materials-f2026/lectures/09_week-9` is withheld by a `.releaseignore`  
  _due Thu 08 Oct 2026, 09:00_

### ADVISORY (2)

Further out - listed so the picture is complete, not to be acted on yet.

- **`releases.readings-6`** -> `course_source_path`  
  `course-materials-f2026/readings/06_week-6` does not exist yet  
  _due Tue 13 Oct 2026, 09:00_
- **`assignments.assignment-4-project`** -> `course_source_repo`  
  `assignment-4-project-f2026` does not exist in hertie-dsl-demo-course-e1234  
  _due Sat 14 Nov 2026, 09:00_

---
Field reference: https://github.com/hertie-data-science-lab/dsl-teaching-toolkit/blob/{central_ref}/docs/07-schedule-releases.md

<!-- dsl-source-state: {"assignments.assignment-4-project.course_source_repo": "advisory", "releases.lecture-5.course_source_path": "error", "releases.lecture-9.course_source_path": "warning", "releases.readings-6.course_source_path": "advisory"} -->
```

**Transition comment** (`_comment`) - the only half that emails anyone. Sections appear
only when non-empty, and a brand-new issue gets none (its own creation notifies):
```
**Escalated** (closer to its deadline):
- `releases.lecture-5.course_source_path` is now **error**

**New**:
- `releases.lecture-9.course_source_path` (warning)

**Cleared**:
- `releases.lecture-2.course_source_path`

cc @hertie-dsl-demo-f2026/instructors
```

**Closing comment:** `Every source the plan names is now staged in the course org.`

---

## 7. Repo descriptions
Short strings, but they are the "What it's for" column on both org landing pages. A
description is set at repo CREATION only; earlier wordings are rewritten by
`repos.converge_descriptions` off the `SUPERSEDED_*` tables (`repos.py:134-183`), so an
edit here reaches existing orgs only if the old string is added to that chain.

The CURRENT wording of every repo the pipeline seeds:

| Repo | Text | Source | Seen by |
| --- | --- | --- | --- |
| course org `.github` | `[control panel]: Org profile & configuration` | `bootstrap_course.py:395` | faculty |
| cohort org `.github` | `[do not touch]: Org profile and configuration` | `bootstrap_course.py:395` | both |
| cohort `welcome` | `Course front door - open a Join issue to enrol` | `bootstrap_course.py:477` | student |
| cohort `classroom-config` | `[visible to instructors only]: Everything you configure for this cohort is here - student roster, teams, term schedule, and marking. Students never see it, and no PII leaves this repo.` | `bootstrap_course.py:514` | faculty |
| course `course-materials-<tag>` | `Course materials (lectures/labs/readings/datasets/other) by session` | `scaffold.py:411` | faculty |
| course `assignment-N-<tag>` | `Assignment {number} template` | `scaffold.py:473` | faculty |
| `<org>.github.io` (both tiers) | `[do not touch]: Course website (auto-deployed)` | `scaffold.py:679` | both |
| cohort `materials` | `Released lectures, labs, readings, & other materials` | `deploy.py:209` | student |
| cohort `<slug>-<handle>` | `{slug} - submission repo` | `assign.py:378` | student |
| cohort `grades-<handle>` | `Private gradebook for @{handle}` | `grades.py:418` | student (own repo only) |
| cohort assignment template | `{slug} - cohort assignment template` | `assign.py:224` | faculty |

Superseded wordings still being converged (left) -> current (right), `repos.py:134-183`:

| Old text still in the wild | Rewritten to |
| --- | --- |
| `Released course materials (enrolled students only)` | `Released lectures, labs, readings, & other materials` |
| `Released lectures, labs, readings, and other materials` | `Released lectures, labs, readings, & other materials` |
| `Course materials (lectures/readings by session)` | `Course materials (lectures/labs/readings/datasets/other) by session` |
| `Course website (auto-deployed on push)` | `[do not touch]: Course website (auto-deployed)` |
| `Cohort course website (auto-deployed on push)` | `[do not touch]: Course website (auto-deployed)` |
| `Org profile and configuration` (cohort org) | `[do not touch]: Org profile and configuration` |
| `Org profile and configuration` (course org) | `[control panel]: Org profile & configuration` |
| `PRIVATE cohort config - roster (students.csv). No PII leaves here.` | the `[visible to instructors only]: ...` text above |

## 8. Team and label descriptions
Team descriptions show on each team's page; the two labels show on every Join issue.

| Team / label | Text | Source |
| --- | --- | --- |
| `instructors` (every org) | `Instructors and TAs` | `course.py:67` |
| `course-admin` (every org) | `Course administrators - DSL team` | `course.py:68` |
| `students` (cohort, secret) | `Enrolled students` | `course.py:81` |
| `auditors` (cohort, secret) | `Auditors - read-only (released materials only, no assignments)` | `course.py:83` |
| `instructors-<tag>` (course org) | `Instructors for {tag} (cohort-declared)` | `access.py:116`, `sync_faculty.py:237` |
| per-team (cohort) | `Project team (auto-managed from teams.csv)` | `sync_teams.py:83` |
| label `onboarding` | `Join course issue - routes the Onboard student workflow` | `welcome.py:131` |
| label `team-formation` | `Join team issue - routes the Form team workflow` | `welcome.py:132` |

## 9. Commit messages in student-readable repos
Low-visibility but permanent in history: `grades: update`, `init gradebook`,
`add solution`, `release: sync materials into {repo}`.
