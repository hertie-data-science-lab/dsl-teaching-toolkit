# Faculty & instructors workflows

Step-by-step runbooks for instructor-facing processes, end to end. 

## The two tiers

| Tier | Lives in | Lifetime | Holds |
|------|----------|----------|-------|
| **Course org** | `hertie-<course-slug>-<code>`, e.g. `hertie-dsl-demo-course-e1234` | persistent (all years) | materials, assignment templates, the faculty & instructors **control panel** (`.github`) |
| **Cohort org** | `hertie-<course-slug>-<termtag>`, termtag `fYYYY`/`sYYYY`, e.g. `hertie-dsl-demo-f2026` | one per year | released materials, student repos, roster, the cohort website |

The course org is the single source of truth (SSOT); each cohort org receives **releases** of it.
Full model: [`../docs-admin-arch/architecture.md`](../docs-admin-arch/architecture.md).

## End-to-end path

```mermaid
flowchart TD
  A["`**Admin**: add the person to
hertie-data-science-lab / faculty or instructors team
(grants provisioning only)`"] --> B

  subgraph COURSE["Course org (one-time)"]
    B["`**01 New course org**
create + bootstrap`"]
    C["`**02 Add materials**
scaffold + push lectures/readings`"]
    D["`**03 Add assignment**
scaffold + push brief/solution`"]
    B --> C
    B --> D
  end

  subgraph COHORT["Cohort org (once / year)"]
    E["`**04 New cohort org**
create + bootstrap`"]
    T["`**05 Teaching team**
declare instructors/TAs,
optionally time-boxed`"]
    F["`**06 Enrol students**
Send enrolment codes + Join course issue`"]
    S["`**07 Schedule releases**
fill schedule.yml, the whole term, up front
(or manual release from course org's .github repo)`"]
    G["`**Releases fire**
materials · assignments · autograde runs`"]
    I["Sync site (automatic)"]
    J["`**10 Grade + return**
autograde → marks → preview → distribute`"]
    E --> T
    E --> F
    E --> S
    F --> G
    S ==>|"scheduled cron - the primary path"| G
    G --> I
    G --> J
  end

  B --> E
  C --> G
  D --> G
```
> NB: all workflows can be automated at the start of the semester by filling out the cohort org's `schedule.yml` for that semester. This will then automatically handle the release of materials / assignments / grading runs etc, with specific workflows manually run from the course org's `.github` repo for ad hoc use. 

## The workflows

Numbered in reading order - **course-level** (01-03) before **cohort-level** (04-11):

| # | Workflow | Tier | When |
|---|----------|------|------|
| 01 | [New course org](01-new-course-org.md) | course | once, when a course first goes on the platform |
| 02 | [Add materials to course](02-add-materials-to-course.md) | course | per materials repo (usually once/year) |
| 03 | [Add assignment to course](03-add-assignment-to-course.md) | course | per assignment |
| 04 | [New cohort org](04-new-cohort-org.md) | cohort | once per year |
| 05 | [Manage the teaching team](05-manage-teaching-team.md) | course + cohort | whenever staff join or leave - incl. **fixed-term** access for a TA or guest lecturer |
| 06 | [Enrol students to cohort](06-enrol-students-to-cohort.md) | cohort | start of each cohort |
| 07 | [Schedule releases & deployed calendar](07-schedule-releases.md) | cohort | once per cohort, up front - **the primary release path** |
| 08 | [Manual release materials to cohort](08-release-materials-to-cohort.md) | cohort | fallback/ad-hoc release |
| 09 | [manual release assignment to cohort](09-release-assignment-to-cohort.md) | cohort | fallback/ad-hoc hand-out |
| 10 | [Grade and return assignments](10-grade-and-return-assignments.md) | cohort | per assignment, after the deadline |
| 11 | [Configure the cohort website](11-configure-cohort-site.md) | course + cohort | whenever the site should say something different - and to know what not to hand-edit |

For a one-page summary of **every workflow**, see [`actions-reference.md`](reference/actions-reference.md);
for who may run them, [`access-reference.md`](reference/access-reference.md). If you maintain the
toolkit itself rather than a course, start at [`maintainers.md`](reference/maintainers.md); to
get a toolkit change out to live orgs, see
[Deploying the toolkit](../docs-admin-arch/central-admin.md#deploying-the-toolkit).

## Three things that look cosmetic and are not

- **The cohort org's `fYYYY`/`sYYYY` suffix** is parsed: it picks the year's `instructors-<tag>`
  team and `*-<tag>` content repos. The course org's name is not validated.
- **Repo topics** (`dsl-course-hub`, `dsl-cohort`, `submission`, `gradebook`,
  `assignment-template`) are how discovery tells orgs and repos apart. Remove one by hand and
  the repo drops out of every sweep.
- **`.github/.last-refresh`** is a heartbeat: GitHub disables crons after 60 quiet days, so the
  nightly refresh commits a date. If it has stopped, run any workflow by hand to restart them.

## Example org artefacts

Every file these runbooks ask you to write exists, filled in, in
[`../example-course/`](../example-course/) - a complete worked dummy course you can copy
from:

| Runbook | Worked example |
|---------|----------------|
| [01](01-new-course-org.md) course identity, `course_admins`, staff cards | [`course-org/dsl-course.yml`](../example-course/course-org/dsl-course.yml) |
| [02](02-add-materials-to-course.md) materials tree | [`course-materials-f2026/`](../example-course/course-org/course-materials-f2026/) - `lectures/`, `readings/`, `labs/`, `SYLLABUS.md` |
| [03](03-add-assignment-to-course.md) assignment `main/` + `solution/` | [`assignment-1`](../example-course/course-org/assignment-1-f2026/) (`.py`), [`assignment-2`](../example-course/course-org/assignment-2-f2026/) (notebook), [`assignment-4-project`](../example-course/course-org/assignment-4-project-f2026/) (**group**) - each with `grading.yml` + hidden `tests/` |
| [05](05-manage-teaching-team.md) the teaching team, time-boxed | [`people.yml`](../example-course/cohort-org/people.yml) - two TAs with `start`/`end` dates |
| [06](06-enrol-students-to-cohort.md) roster + project teams | [`students.csv`](../example-course/cohort-org/students.csv) (incl. an auditor), [`teams.csv`](../example-course/cohort-org/teams.csv) |
| [07](07-schedule-releases.md) the whole term's plan | [`schedule.yml`](../example-course/cohort-org/schedule.yml) - `releases` with `event_datetime`s + `deploy_datetime`s, `assignments` + `grading_datetime`, `events` (exams, a clinic) |
| [10](10-grade-and-return-assignments.md) grade tables | [`grades/assignment-1.csv`](../example-course/cohort-org/grades/assignment-1.csv), [`grades/assignment-4-project.csv`](../example-course/cohort-org/grades/assignment-4-project.csv) (team grades) |
| [08](08-release-materials-to-cohort.md) a growing package | [`lecture-code-f2026/mlpkg/`](../example-course/course-org/lecture-code-f2026/) - disclosed subpackage by subpackage |

Field-by-field rules for all of these: [`DEPLOYMENT-CHECKLIST.md`](DEPLOYMENT-CHECKLIST.md).

## Demo orgs (live reference)

A standing demo you can inspect at while reading - one course org, two cohorts, running the
current engine:

- Course org: **[`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234)** 
- Cohort org (current): **[`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026)** <- read here, more filled out with example files.
- Cohort org (last year): **[`hertie-dsl-demo-f2025`](https://github.com/hertie-dsl-demo-f2025)**  <- empty stub, demonstrates how legacy cohort orgs remain attached to their hub course org for historical & archival reference.
