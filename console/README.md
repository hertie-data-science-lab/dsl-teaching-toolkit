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

## Sign-in

Three paths, all behind the `Auth` interface in `src/auth/` (`ConsoleAuth` holds them).
Whichever is used, the console can read or change exactly what that account can on GitHub,
and the token stays in `sessionStorage`: it is gone when the tab closes.

- **Sign in with GitHub** (`AppAuth`, decisions 0002 and 0011): the default where an
  institution runs the relay. The web flow goes to GitHub with a `state` and a PKCE
  challenge, comes back to the console's own URL, and the relay (`relay/`) exchanges the code;
  the console refreshes the 8-hour token five minutes before it runs out. Shown only when the
  build sets both `VITE_GH_APP_CLIENT_ID` and `VITE_AUTH_RELAY_URL`; the App's callback URL
  must be the console's URL (for Hertie
  `https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/`), and the App must be
  installed on each course and cohort organisation.
- **Fine-grained token** (`PatAuth`): the no-server fallback, for an institution that runs no
  relay or when the App sign-in is blocked. It is owned by one organisation and reaches only
  that one; the sign-in probes the organisations it can check and names those the token
  cannot see. The organisation must not require approval of fine-grained tokens (decision 0011
  has the cohort set-up turn that off). GitHub lists no organisations to a fine-grained token
  (`GET /user/orgs` is empty), so the course list finds nothing through it until discovery
  learns another source.
- **Classic token** (`PatAuth`): `repo` and `workflow` scopes, checked from the
  `X-OAuth-Scopes` header. Works everywhere the account does; the widest grant of the three.

Build-time settings, for `npm run build` or `npm run dev`:

    VITE_GH_APP_CLIENT_ID=Iv23...    # the GitHub App's client id; empty hides the App button
    VITE_AUTH_RELAY_URL=https://dsl-console-auth.<subdomain>.workers.dev

## What it reads

- Courses: orgs from `GET /user/orgs` whose `.github` repo carries the `dsl-course-hub`
  topic; cohorts from `.github/cohort-courses-pages.yml`; write access = push on `.github`.
- Status: `classroom-config/.dsl/status.json` (cohort) and `.github/.dsl/status.json`
  (course), validated against `schemas/status.schema.json`. Staleness compares the file's
  `inputs` with one tree read. An absent file shows "Status not computed yet".
- Automation's heartbeat: the course's Scheduled release run list.
- Screens that show a file read it directly: `schedule.yml` (Details, events),
  `students.csv`, `people.yml`, a template's `grading_config.yml`, the site's `index.md`.
- Materials: the course org's repo list (`GET /orgs/{org}/repos`) for Other repos and last
  changes; a materials repo's recursive tree, badged from `publish.yml` and `.releaseignore`.

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
- Wizards: New course, New cohort, New assignment, New materials. Each step checks live state
  before it lets you continue (the org exists, `hertie-dsl-bot` is an owner, the set-up left
  its repos, the template has both branches and a settings file that parses), and unfinished
  answers stay in this browser so leaving loses nothing. Setting up a course is the one
  operation outside the instructor's orgs: the wizard dispatches the central
  `bootstrap-org.yml` in `hertie-data-science-lab/dsl-teaching-toolkit` and follows its run.
  New assignment writes the Advanced marking values `assignment.create` does not take into
  the new template's `grading_config.yml`.

## Schemas

`schemas/` holds the JSON Schemas the engine exports with `python -m dsl_course.schemas`;
a Python test fails when they drift. Never edit them by hand.

## Routes

Hash tokens as in the design mockup: `#cohort`, `#schedule-s5`, `#assignment-<slug>`,
`#release-<id>`, `#template-<slug>`, `#marks-<slug>`, `#teams-<slug>`, `#materials-<repo>`;
the schedule editor also opens `#schedule-new`, `#schedule-term` and `#schedule-archive`.
Wizards: `#new-course-1..4`, `#new-cohort-1..3`, `#new-assignment-1..4`, `#new-materials`; a
step past the first unfinished one opens that one instead. `?template=<repo>#schedule-new`
opens a new assignment entry for that template; `?wizard=new-cohort-3` adds a link back.
A problem's `fix {screen, entry}` is `#<screen>-<entry>`.
The course or cohort rides in the query string: `?cohort=<org>` or `?course=<org>`.
