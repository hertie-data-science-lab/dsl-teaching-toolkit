# Instructor Console

A static web app (Vite + TypeScript + Preact) that shows an instructor their courses and
cohorts as the lifecycle model describes them, reading GitHub with the instructor's own token.
Deployed by `.github/workflows/console-pages.yml` to
https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/.

## Run it locally

Node 22 or newer (`.nvmrc` pins 26).

    cd console
    npm ci
    npm run dev        # http://localhost:5173/dsl-teaching-toolkit/
    npm test           # vitest, against a fake fetch; no network
    npm run typecheck
    npm run build      # into dist/

## Sign-in today

Paste a **classic** personal access token with the `repo` and `workflow` scopes. The console
checks it with `GET /user`, refuses fine-grained tokens and missing scopes, and keeps it in
`sessionStorage` only: it is gone when the tab closes. Everything the console can read or
change is what that account can read or change on GitHub. Sign-in sits behind the `Auth`
interface in `src/auth/`; a GitHub App sign-in (`AppAuth`, decision 0002) replaces the token
later without touching the screens.

## What it reads

- Courses: orgs from `GET /user/orgs` whose `.github` repo carries the `dsl-course-hub`
  topic; cohorts from `.github/cohort-courses-pages.yml`; write access = push on `.github`.
- Status: `classroom-config/.dsl/status.json` (cohort) and `.github/.dsl/status.json`
  (course), validated against `schemas/status.schema.json`. Staleness compares the file's
  `inputs` with one tree read. An absent file shows "Status not computed yet".
- Automation's heartbeat: the course's Scheduled release run list.
- Screens that show a file read it directly: `schedule.yml` (Details, events),
  `students.csv`, `people.yml`, a template's `grading_config.yml`, the site's `index.md`.

## What it changes

- Files, as the signed-in user, with a sha-conditional write (a file that moved on since it
  was read is refused, never overwritten): `schedule.yml`, `people.yml`, `students.csv`,
  `teams.csv`, `grading_sheets/<slug>.yml`, `.github/dsl-course.yml`, a template's
  `grading_config.yml` (on `solution`), a materials repo's `publish.yml` and `.releaseignore`,
  the site's `index.md` and `_announcements/`. YAML is edited in place (`src/edit/yamlText.ts`):
  only the changed values' bytes move, so comments and their columns survive. After a write the
  console follows the commit's checks and says what they found.
- Operations, through the course org's Console workflow (`.github/.github/workflows/console.yml`,
  ref `main`, one `request` input; contracts section 1). `src/ops/adapter.ts` dispatches with
  `return_run_details`, polls the run, and reads the public `dsl-outcome` annotation and the
  private outcome file. Hand out, return marks, archive, update every copy and send new codes
  unlock only after a preview in the same session; publishing the public website, which has no
  engine preview, asks for a confirmation instead.

## Schemas

`schemas/` holds the JSON Schemas the engine exports with `python -m dsl_course.schemas`;
a Python test fails when they drift. Never edit them by hand.

## Routes

Hash tokens as in the design mockup: `#cohort`, `#schedule-s5`, `#assignment-<slug>`,
`#release-<id>`, `#template-<slug>`, `#marks-<slug>`, `#teams-<slug>`, `#materials-<repo>`;
the schedule editor also opens `#schedule-new`, `#schedule-term` and `#schedule-archive`.
A problem's `fix {screen, entry}` is `#<screen>-<entry>`.
The course or cohort rides in the query string: `?cohort=<org>` or `?course=<org>`.
