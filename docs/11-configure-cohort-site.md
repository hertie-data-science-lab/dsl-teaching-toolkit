# Configure the semester website

Every semester has an auto-deployed site at `<semester-org>.github.io`, regenerated from the org's config files.
It is a **public calendar**: the schedule by kind, your home text and announcements, and a banner
linking the semester in the student console. Briefs, teams, materials, receipts and marks are in
the console; nothing about a student is on the site.

The console reads `.system/student-status.json` in the semester org's `.github`, which the
engine rewrites with every status refresh. It is public too, so it carries only what the site
could show: the schedule, the assignments' dates, rules and (once handed out) briefs, teams as
name and headcount, the instructor cards, the home text, announcements and the materials paths.

You never edit what the site shows - you edit the file it reads, and it re-syncs itself.

## What you set, and where

| To change | Edit | Field |
|---|---|---|
| Course blurb under the title | course org `.github/dsl-course.yml` | `course_description` |
| Course title + code | course org `.github/dsl-course.yml` | `course_name`, `course_code` |
| Semester + year | *nothing to set* | inferred from the semester org's `fYYYY`/`sYYYY` semester (`hertie-dsl-demo-f2026` → "Fall 2026") |
| Instructor / TA cards | semester `semester-config/instructors.yml` ([05](05-manage-teaching-team.md)) | every field you declare displays, bar `github_handle`, `start`, `end` (access only) and `email` (private unless the entry adds `show_email: true`); a card needs a `name` to appear at all |
| Instructor photos | site repo `<semester-org>.github.io` | commit the image under `_images/pp/`, then `photo: /_images/pp/jane.jpg`. Can also use a URL that allows hotlinking |
| Schedule rows, exams, assignment due dates | semester `semester-config/schedule.yml` ([07](07-schedule-releases.md)) | `releases`, `events`, `assignments` (there is no `exams:` key - an exam is an `events:` entry with `kind: exam`) |
| A hand-written entry in the **Updates** box | site repo | add a file under `_announcements/` with `date:` + `details:` front matter (the file's body works too). The box shows the newest 7 items (releases feed it automatically, and only once they have actually shipped); older ones roll off as new ones arrive - delete the file to pull one early |
| Materials links | *nothing to set* | the row appears as soon as `schedule.yml` names the session, marked "not released yet"; the links fill in as you [release](08-release-materials-to-cohort.md) |
| A session's name + blurb | semester `semester-config/schedule.yml` ([07](07-schedule-releases.md)) | `title`, `details` on the `releases:` entry - the Hertie syllabus's session title and learning objectives. `details` may run to several paragraphs, and shows in the schedule's Details column as well as on the session's tab |
| Readings on the **Readings** tab | course materials repo | drop the readings into `readings/NN_.../` and **every file is listed and linked automatically** for enrolled students - nothing to write. `READINGS.md` (or `.txt`/`.bib`) beside them is OPTIONAL, for what a file cannot say: a URL, pointers for what to focus on, or clean citation-style metadata. It is published as written (this site is public, so it never hosts a reading itself directly, rather links to the GH-hosted files (with their permission restrictions enforced there))|
| The syllabus link on the home page | *nothing to set* | a released syllabus is pinned (the file `materials.yml` declares, else `SYLLABUS.md`, else a root file named like one) |
| Which files each session links | course org `.github/dsl-course.yml` | *nothing to set* by default: a session lists its root files plus one link per subfolder, so a rendered deck lists the deck and not its assets. `site_link_extensions: [pdf, html]` narrows it further. Everything you release ships either way |

## What never to touch

These are rewritten on every sync. A hand edit here is overwritten - the sync opens (or
updates) one issue in the site repo linking the overwritten commit and naming the file to
edit instead, and **emails whoever made the edit**. Nothing is actually lost: the linked
commit still holds the change, to be copied back out and made at the source.

| In the site repo | What happens |
|---|---|
| `_lectures/`, `_assignments/`, `_events/` | each directory is **deleted and rebuilt** every sync - a file you drop in here vanishes |
| `_data/people.yml` | overwritten from `semester-config/instructors.yml` |
| `lectures.md`, `labs.md`, `readings.md` (a tab per kind) | front-matter stubs pointing at the layouts below - generated wrappers, so put your own words in `index.md`. The old `assignments.md`, `materials.md` and `profile.md` stubs and the `files/` copies are removed by the sync |
| `_data/nav.yml` | the tab bar - generated, so a new tab reaches sites that already exist. Add a page of your own as a file and link it from `index.md` |
| `_data/materials.yml`, `_data/console.yml` | the pinned syllabus, and the banner's link to the console |
| `_layouts/`, `_includes/`, `_sass/_course.scss` | how every page renders - shipped from `templates/site/` in the toolkit, so a rendering change reaches every course site at once |
| `.github/workflows/deploy.yml` | the Pages build - shipped from `templates/site/` too |
| `_config.yml` keys `course_name`, `course_code`, `course_semester`, `course_description`, `github_org` | overwritten from the sources in the table above |
| `_config.yml` keys `remote_theme`, `dateformat`, `collections`, `defaults` | the pinned theme and the settings the layouts above depend on |
| `README.md` | rewritten every sync - it is the repo's own "do not edit this repository" notice |

**Faculty are not expected to hand-edit the semester site at all.** Everything it shows comes
from the files in the table at the top of this page; edit those. What is left over -
`index.md`, `schedule.md`, any other page of your own, `_announcements/`,
further `_data/*.yml`, assets, `_images/`, `Gemfile`, `.gitignore` - is seeded once when the site
is created and never rewritten.

To change how a page *renders*, open a PR against
[`templates/site/`](../templates/site) in this toolkit rather than the site repo; the rest
of the styling lives in the shared
[`dsl-jekyll-theme`](https://github.com/hertie-data-science-lab/dsl-jekyll-theme), which
every site pins at a fixed ref.

## When it redeploys

| Trigger | Latency |
|---|---|
| Push to `semester-config/schedule.yml` or `instructors.yml` | immediate |
| **Release materials** / **Release assignment** workflow | immediate, in the same run |
| A scheduled release firing | within that tick ([about every 15 minutes](07-schedule-releases.md#what-drives-the-scheduler)) |
| Push to course org `.github/dsl-course.yml` | immediate - and re-syncs **every** semester site |
| **Sync site** workflow, course org `.github` | on demand |
| Anything else (e.g. editing a file inside an already-released repo) | the daily cron, **06:41 UTC** |

## Next

- [Manage the instructors](05-manage-teaching-team.md) - where the instructor cards come from.
- [Schedule releases](07-schedule-releases.md) - where the dates come from.
- Field-by-field schemas: [DEPLOYMENT-CHECKLIST](DEPLOYMENT-CHECKLIST.md#dsl-courseyml).

---
**Demo:** [`hertie-dsl-demo-f2026.github.io`](https://hertie-dsl-demo-f2026.github.io/), fed by
[`hertie-dsl-demo-course-e1234/.github/dsl-course.yml`](https://github.com/hertie-dsl-demo-course-e1234/.github/blob/main/dsl-course.yml)
and [`hertie-dsl-demo-f2026/semester-config`](https://github.com/hertie-dsl-demo-f2026/semester-config).
