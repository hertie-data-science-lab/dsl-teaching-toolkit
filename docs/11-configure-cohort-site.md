# Configure the semester website

Every semester has an auto-deployed site at `<semester-org>.github.io`, regenerated from the org's config files.
Its tabs are Home, Schedule, one tab per kind of row (Lectures, Labs, Readings, ...),
Assignments (a page per assignment), All Materials and Your Profile.
No student's handle, email or mark is on the site; teams show by name, with salted member
digests.

The console reads `.system/student-status.json` in the semester org's `.github`, which the
engine rewrites with every status refresh. It is public too, so it carries only what the site
could show: the semester's dates, the schedule, the assignments' dates, rules and (once handed out) briefs, teams as
name and headcount, the instructor cards, the home text, announcements and the materials paths.

You never edit what the site shows - you edit the file it reads, and it re-syncs itself.
The console's Site > Site settings does this for you: each field saves into the file below.

## What you set, and where

| To change | Edit | Field |
|---|---|---|
| Course blurb under the title | course org `.github/course.yml` | `course.description` |
| Course title + code | course org `.github/course.yml` | `course.name`, `course.code` |
| Semester + year | *nothing to set* | inferred from the semester org's `fYYYY`/`sYYYY` semester (`hertie-dsl-demo-f2026` → "Fall 2026") |
| Instructor / TA cards | semester `semester-config/semester.yml` `people:` ([05](05-manage-teaching-team.md)) | every field you declare displays, bar `github_handle`, `start`, `end` (access only) and `email` (private unless the entry adds `show_email: true`); a card needs a `name` to appear at all |
| Instructor photos | site repo `<semester-org>.github.io` | commit the image under `_images/pp/`, then `photo: /_images/pp/jane.jpg`. Can also use a URL that allows hotlinking |
| Schedule rows, exams, assignment due dates | semester `semester-config/schedule.yml` ([07](07-schedule-releases.md)) | one block per kind (`lectures`, `labs`, `readings`, `exams`, `events`, ...) and `assignments` |
| School name, links, footer address | site repo `_config.yml` | `schoolname`, `schoolurl`, `dsl_org_url`, `address` - never rewritten by the sync; in the console, under Site settings > Advanced |
| A hand-written entry in the **Updates** box | site repo | add a file under `_announcements/` with `date:` + `details:` front matter (the file's body works too). The box shows the newest 7 items (releases feed it automatically, and only once they have actually shipped); older ones roll off as new ones arrive - delete the file to pull one early |
| Materials links | *nothing to set* | the row appears as soon as `schedule.yml` names the session, marked "not released yet"; the links fill in as you [release](08-release-materials-to-cohort.md) |
| A session's name, blurb and room | semester `semester-config/schedule.yml` ([07](07-schedule-releases.md)) | `title`, `details`, `location` on the entry - the Hertie syllabus's session title and learning objectives. `details` may run to several paragraphs, and shows in the schedule's Details column as well as on the session's tab |
| Readings on the **Readings** tab | semester `semester-config/schedule.yml` ([07](07-schedule-releases.md#readings)) | a `readings:` entry's files are listed and linked for enrolled students; its `reading_guide:` (markdown: URLs, chapters, citations) is shown as written. The site is public, so it hosts no reading unless a file says `public: true` (the **public** toggle on the file in the console); the rest link to the GitHub-hosted files, whose permissions apply. A session's readings put a **Readings** chip on its unit's row |
| The syllabus link on the home page | *nothing to set* | a released syllabus is pinned (the file `semester.yml` `student_site.syllabus` names, else `SYLLABUS.md`, else a root file named like one) |
| The **All Materials** tab | *nothing to set* | every file released to the semester, grouped by section and nested exactly as its repo has it - a folder opens in the page itself, at any depth. Root files (the syllabus, a README) are listed once, as course documents. `solution/`, `tests/`, grading files and `.env` are never listed |
| Rendered decks and pdfs opening in the browser instead of showing as source | semester `semester-config/semester.yml` `student_site.host` ([below](#what-the-public-sites-show)) | the site hosts a viewable copy of each selected file; its name opens the copy, with `view` and `GitHub` in its menu, and an `<name>.html` brings its `<name>_files/` bundle. Narrow `host:` and the copy goes on the next sync (the site repo's git history keeps the old bytes - purge by hand). A `host:` that does not validate stops the sync |
| Open files in your local copy (a file link opening in a student's own fork or clone) | *nothing to set* | every file's name opens its rendered copy where the site hosts one, else GitHub; the chevron beside it opens a menu of one-line rows - **View here** (only for a file the site hosts a copy of), **View on GitHub**, a hairline, **Edit in browser**, **Edit in** the student's editor (VS Code until they say otherwise) - each with a short status on the right: where it goes, or why an Edit row is greyed (a pdf/pptx/docx/xlsx/zip, or a file outside the repo the profile forks). A student who fills in the site's **Your Profile** tab - handle, fork, clone, folders, editor - gets both Edit rows live on every row of the repo it forks (the first the semester releases into, when there are several - the tab says which; before that, each says "set up in Profile"); on each assignment page they also get their own repo by name, with Edit online and Edit locally and the clone command. The profile is saved in that student's own browser and sent nowhere |
| Which files each session links | *nothing to set* | a session lists its root files plus one link per subfolder, so a rendered deck lists the deck and not its assets. Everything you release ships either way |

## What the public sites show

Both sites are public. Which released files each shows is one shape, a **selection**
(decision 0040); every key is optional:

```yaml
kinds: [lectures, labs]          # schedule blocks; a file's kind is the entry that released it
types: [html, pdf]
include: ["readings/**/key.pdf"] # .gitignore syntax, from the repo's root
exclude: ["**/drafts/"]          # beats everything
```

| Site | Where | Left out = |
|---|---|---|
| Semester site (hosted copies) | `semester.yml` `student_site.host` | every kind but readings (copyright), html and pdf |
| Open course site | `course.yml` `open_course_site.publish` (+ `reading_lists: true`) | lectures and labs, reading lists as text; names with solution, exam, grade, marks or private kept off |

A file no schedule entry released has no kind: it is shown only if `include` names it. The
institution denylist and each repo's `.releaseignore` apply first
([08](08-release-materials-to-cohort.md#withholding-files-with-releaseignore)), and the
institution may set a hard limit (`institution.yml` `selection_floor`; none at Hertie).
The console edits each selection on a file tree; the readings chip turns red when readings
are shown.

## What never to touch

These are rewritten on every sync. A hand edit here is overwritten - the sync opens (or
updates) one issue in the site repo linking the overwritten commit and naming the file to
edit instead, and **emails whoever made the edit**. Nothing is actually lost: the linked
commit still holds the change, to be copied back out and made at the source.

| In the site repo | What happens |
|---|---|
| `_lectures/`, `_assignments/`, `_events/` | each directory is **deleted and rebuilt** every sync - a file you drop in here vanishes |
| `_data/people.yml` | overwritten from `semester-config/semester.yml` `people:` |
| `lectures.md`, `labs.md`, `readings.md` (a tab per kind), `assignments.md`, `materials.md`, `profile.md` | front-matter stubs pointing at the layouts below - generated wrappers, so put your own words in `index.md` |
| `_data/nav.yml` | the tab bar - generated, so a new tab reaches sites that already exist. Add a page of your own as a file and link it from `index.md` |
| `_data/materials.yml`, `_data/console.yml` | the pinned syllabus, the All Materials index and the repos it covers (Your Profile forks the first), and the banner's link to the console |
| `files/<repo>/` | the public copies `student_site.host` selects - **deleted and rebuilt** per repo every sync, and removed for a repo the plan no longer releases into |
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
| Push to `semester-config/schedule.yml` or `semester.yml` | immediate |
| A release or hand-out by hand (`release.entry`, `release.adhoc`, `assignment.handout_now`) | immediate, in the same run |
| A scheduled release firing | within that tick ([about every 15 minutes](07-schedule-releases.md#what-drives-the-scheduler)) |
| Push to course org `.github/course.yml` | immediate - and re-syncs **every** semester site |
| **Sync site** workflow, course org `.github` | on demand |
| Anything else (e.g. editing a file inside an already-released repo) | the daily cron, **06:41 UTC** |

## Next

- [Manage the instructors](05-manage-teaching-team.md) - where the instructor cards come from.
- [Schedule releases](07-schedule-releases.md) - where the dates come from.
- Field-by-field schemas: [DEPLOYMENT-CHECKLIST](DEPLOYMENT-CHECKLIST.md#courseyml).

---
**Demo:** [`hertie-dsl-demo-f2026.github.io`](https://hertie-dsl-demo-f2026.github.io/), fed by
[`hertie-dsl-demo-course-e1234/.github/course.yml`](https://github.com/hertie-dsl-demo-course-e1234/.github/blob/main/course.yml)
and [`hertie-dsl-demo-f2026/semester-config`](https://github.com/hertie-dsl-demo-f2026/semester-config).
