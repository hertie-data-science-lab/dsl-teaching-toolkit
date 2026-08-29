# Configure the cohort website

Every cohort has an auto-deployed site at `<cohort-org>.github.io`, regenerated from the org's config files.

You never edit what the site shows - you edit the file it reads, and it re-syncs itself.

## What you set, and where

| To change | Edit | Field |
|---|---|---|
| Course  blurb under the title | course org `.github/dsl-course.yml` | `course_description` |
| Course title + code | course org `.github/dsl-course.yml` | `course_name`, `course_code` - **not** `org_name` |
| Semester + year | *nothing to set* | inferred from the cohort org's `fYYYY`/`sYYYY` tag (`hertie-dsl-demo-f2026` → "Fall 2026") |
| Instructor / TA cards | cohort `classroom-config/people.yml` ([05](05-manage-teaching-team.md)) | every field you declare displays, bar `github_handle`, `start`, `end` (access only); a card needs a `name` to appear at all |
| Instructor photos | site repo `<cohort-org>.github.io` | commit the image under `_images/pp/`, then `photo: /_images/pp/jane.jpg`. Can also use a URL that allows hotlinking |
| Schedule rows, exams, assignment due dates | cohort `classroom-config/schedule.yml` ([07](07-schedule-releases.md)) | `releases`, `events`, `assignments` (there is no `exams:` key - an exam is an `events:` entry with `type: exam`) |
| A hand-written entry in the **Updates** box | site repo | add a file under `_announcements/` with `date:` + `description:` front matter. The box shows the newest 7 items (releases feed it automatically); older ones roll off as new ones arrive - delete the file to pull one early |
| Materials links | *nothing to set* | the row appears as soon as `schedule.yml` names the session, marked "not released yet"; the links fill in as you [release](08-release-materials-to-cohort.md) |
| A session's name + blurb | cohort `classroom-config/schedule.yml` ([07](07-schedule-releases.md)) | `title`, `description` on the `releases:` entry - the Hertie syllabus's session title and learning objectives. `description` may run to several paragraphs |
| Readings on the **Readings** tab | course materials repo | drop the readings into `readings/NN_.../` and **every file is listed and linked automatically** for enrolled students - nothing to write. `READINGS.md` (or `.txt`/`.bib`) beside them is OPTIONAL, for what a file cannot say: a URL, pointers for what to focus on, or clean citation-style metadata. It is published as written (this site is public, so it never hosts a reading itself directly, rather links to the GH-hosted files (with their permission restrictions enforced there))|
| The **All Materials** tab | *nothing to set* | every file released to the cohort, grouped by section and nested exactly as its repo has it - a folder opens in the page itself, at any depth, rather than only counting its contents. The only page not keyed on a session ordinal, so a released `SYLLABUS.md` or a flat `datasets/` appears here rather than on a session tab. A released syllabus is *also* pinned on the home page (found by name at the repo root, in any format) |
| Which files each session links | course org `.github/dsl-course.yml` | *nothing to set* by default: a session lists its root files plus one link per subfolder, so a rendered deck lists the deck and not its assets. `site_link_extensions: [pdf, html]` narrows it further. Everything you release ships either way |

## What never to touch

These are rewritten on every sync. A hand edit is lost - the sync then opens an issue in the site
repo linking the overwritten commit and naming the file to edit instead.

| In the site repo | What happens |
|---|---|
| `_lectures/`, `_assignments/`, `_events/` | each directory is **deleted and rebuilt** every sync - a file you drop in here vanishes |
| `_data/people.yml` | overwritten from `classroom-config/people.yml` |
| `lectures.md`, `labs.md`, `readings.md`, `materials.md`, `assignments.md` | front-matter stubs pointing at the layouts below - generated wrappers, so put your own words in `index.md` |
| `_data/nav.yml` | the tab bar - generated, so a new tab reaches sites that already exist. Add a page of your own as a file and link it from `index.md` |
| `_data/materials.yml` | the All Materials index, rebuilt from what each cohort repo actually holds |
| `_layouts/`, `_includes/`, `_sass/_course.scss` | how every page renders - shipped from `templates/site/` in the toolkit, so a rendering change reaches every course site at once |
| `_config.yml` keys `course_name`, `course_code`, `course_semester`, `course_description`, `github_org` | overwritten from the sources in the table above |
| `_config.yml` keys `remote_theme`, `dateformat`, `collections`, `defaults` | the pinned theme and the settings the layouts above depend on |
| `README.md` | rewritten every sync - it is the repo's own "do not edit this repository" notice |

**Faculty are not expected to hand-edit the cohort site at all.** Everything it shows comes
from the files in the table at the top of this page; edit those. What is left over -
`index.md` and any other page of your own, `_announcements/`, further `_data/*.yml`, assets,
`_images/`, `Gemfile` - is yours and survives.

To change how a page *renders*, open a PR against
[`templates/site/`](../templates/site) in this toolkit rather than the site repo; the rest
of the styling lives in the shared
[`dsl-jekyll-theme`](https://github.com/hertie-data-science-lab/dsl-jekyll-theme), which
every site pins at a fixed ref.

## When it redeploys

| Trigger | Latency |
|---|---|
| Push to `classroom-config/schedule.yml` or `people.yml` | immediate |
| **Release materials** / **Release assignment** workflow | immediate, in the same run |
| A scheduled release firing | within that hourly tick |
| Push to course org `.github/dsl-course.yml` | immediate - and re-syncs **every** cohort site |
| **Sync site** workflow, course org `.github` | on demand |
| Anything else (e.g. editing a file inside an already-released repo) | the daily cron, **06:00 UTC** |

## Next

- [Manage the teaching team](05-manage-teaching-team.md) - where the staff cards come from.
- [Schedule releases](07-schedule-releases.md) - where the dates come from.
- Field-by-field schemas: [DEPLOYMENT-CHECKLIST](DEPLOYMENT-CHECKLIST.md#dsl-courseyml).

---
**Demo:** [`hertie-dsl-demo-f2026.github.io`](https://hertie-dsl-demo-f2026.github.io/), fed by
[`hertie-dsl-demo-course-e1234/.github/dsl-course.yml`](https://github.com/hertie-dsl-demo-course-e1234/.github/blob/main/dsl-course.yml)
and [`hertie-dsl-demo-f2026/classroom-config`](https://github.com/hertie-dsl-demo-f2026/classroom-config).
