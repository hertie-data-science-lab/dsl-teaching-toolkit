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
| Readings on the **Readings** tab | course materials repo | a text file in `readings/NN_.../` (`reading.md`, `.txt`, `.bib`) is the reading list, published as written once that folder is released. Other files there are linked into the private repo - this site is public, so it never hosts a reading |
| The **All Materials** tab | *nothing to set* | every file released to the cohort, grouped by section and nested exactly as its repo has it - a folder opens in the page itself, at any depth, rather than only counting its contents. The only page not keyed on a session ordinal, so a released `SYLLABUS.md` or a flat `datasets/` appears here and nowhere else |
| Which files each session links | course org `.github/dsl-course.yml` | *nothing to set* by default: a session lists its root files plus one link per subfolder, so a rendered deck lists the deck and not its assets. `site_link_extensions: [pdf, html]` narrows it further. Everything you release ships either way |

## What never to touch

These are rewritten on every sync. A hand edit is lost - the sync then opens an issue in the site
repo linking the overwritten commit and naming the file to edit instead.

| In the site repo | What happens |
|---|---|
| `_lectures/`, `_assignments/`, `_events/` | each directory is **deleted and rebuilt** every sync - a file you drop in here vanishes |
| `_data/people.yml` | overwritten from `classroom-config/people.yml` |
| `lectures.md`, `labs.md`, `readings.md`, `materials.md`, `assignments.md` | front-matter stubs pointing at the shared theme layouts, so a change to how sessions render reaches every site at once |
| `_data/nav.yml` | the tab bar - generated, so a new tab reaches sites that already exist. Add a page of your own as a file and link it from `index.md` |
| `_data/materials.yml` | the All Materials index, rebuilt from what each cohort repo actually holds |
| `_config.yml` keys `course_name`, `course_code`, `course_semester`, `course_description`, `github_org` | overwritten from the sources in the table above |

**Everything else in the site repo is yours and survives forever** - a custom `index.md` or any
other page, CSS/SCSS, `_layouts/`, further `_data/*.yml`, assets, `_images/`. Only the surfaces
listed above are ever written.

## When it redeploys

| Trigger | Latency |
|---|---|
| Push to `classroom-config/schedule.yml` or `people.yml` | immediate |
| **Release materials** / **Release assignment** button | immediate, in the same run |
| A scheduled release firing | within that hourly tick |
| Push to course org `.github/dsl-course.yml` | immediate - and re-syncs **every** cohort site |
| **Sync site** button, course org `.github` | on demand |
| Anything else (e.g. editing a file inside an already-released repo) | the daily cron, **06:00 UTC** |

## Next

- [Manage the teaching team](05-manage-teaching-team.md) - where the staff cards come from.
- [Schedule releases](07-schedule-releases.md) - where the dates come from.
- Field-by-field schemas: [DEPLOYMENT-CHECKLIST](DEPLOYMENT-CHECKLIST.md#dsl-courseyml).

---
**Demo:** [`hertie-dsl-demo-f2026.github.io`](https://hertie-dsl-demo-f2026.github.io/), fed by
[`hertie-dsl-demo-course-e1234/.github/dsl-course.yml`](https://github.com/hertie-dsl-demo-course-e1234/.github/blob/main/dsl-course.yml)
and [`hertie-dsl-demo-f2026/classroom-config`](https://github.com/hertie-dsl-demo-f2026/classroom-config).
