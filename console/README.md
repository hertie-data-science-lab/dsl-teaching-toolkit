# Instructor Console

A static web app (Vite + TypeScript + Preact) that shows an instructor their courses and
semesters as the lifecycle model describes them, and a student their semesters, reading GitHub
with the person's own token.
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
  `https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/`), the App must be
  installed on each course and semester organisation, and its permissions must include the
  organisation permission Members: read (discovery reads the person's memberships).
- **Fine-grained token** (`PatAuth`): the no-server fallback, for an institution that runs no
  relay or when the App sign-in is blocked. It is owned by one organisation and reaches only
  that one; the sign-in probes the organisations it can check and names those the token
  cannot see. It needs the organisation permission Members: read, since both the probe and
  discovery read `GET /user/memberships/orgs/{org}`. The organisation must not require approval of fine-grained tokens (decision 0011
  has the semester set-up turn that off). Discovery finds its organisations another way (below).
- **Classic token** (`PatAuth`): `repo` and `workflow` scopes, checked from the
  `X-OAuth-Scopes` header. Works everywhere the account does; the widest grant of the three.

Build-time settings, for `npm run build` or `npm run dev`:

    VITE_GH_APP_CLIENT_ID=Iv23...    # the GitHub App's client id; empty hides the App button
    VITE_AUTH_RELAY_URL=https://dsl-console-auth.<subdomain>.workers.dev

The deployed console gets them in `.github/workflows/console-pages.yml`, as an `env:` on the
`npm run build` step, from the repository variables `GH_APP_CLIENT_ID` and `AUTH_RELAY_URL`
(either may be empty).

The build writes a Content-Security-Policy meta into `index.html` (`src/csp.ts`): scripts from
the console's own origin, calls to `api.github.com`, `github.com` and the relay's origin,
images from GitHub's avatars, fonts from Google Fonts, no frames, and no `'unsafe-eval'`: the
schema validators are compiled at build time (`virtual:validators` in `vite.config.ts`, Ajv
standalone, from every schema under `schemas/` and each op's `args_schema`), so a schema the
console validates against must live there. The relay URL must be https. `npm run dev` leaves
the policy out.

## Roles and modes

Role is per organisation, from the signed-in account (decision 0011 rule 2), and one person
may hold both across organisations:

- **instructor** of an org: push on its `.github` repo, or the org is a semester registered by a
  course the person can write to;
- **student** of a semester org: an active member with no push on its `.github`;
- anything else is not shown (a course the person can read but not change still shows read only).

Home shows **Your courses** (the instructor's course and semester cards) and **Your semesters**
(one card per semester the person is a student of, archived ones greyed), with a **Show these
semesters** choice kept in this browser (`localStorage`, per account). A student-only account
lands on Your semesters, with This week across the semesters it shows.

The mode picks the shell. `?semester=<org>` opens that semester's student screens (This
week, Schedule, Assignments, Marks, Materials, Join, Instructors). For a
semester the person teaches, that is the **Student view** (the Student view link in the
semester nav): the same screens with the instructor's own identity and a banner, never a
student's repos or marks (rule 7). Anywhere else the console is in instructor mode for anyone
who teaches somewhere, and in student mode otherwise.

## Student screens and their sources

The semester's `status.json` is private (in `semester-config`), so a student cannot read it.
Each screen reads with the student's own account:

| Screen | Shared facts (`StudentData`) | The student's own (GitHub, directly) |
|---|---|---|
| This week (per semester, and on Home across the semesters shown, each line in its semester's timezone) | rows due, handed out, released or on in the next 7 days; releases and announcements of the last 7; open team formation; the About block: course name, syllabus pinned, the home text, announcements | gradebook's last change (marks returned); whether they have a team; a patch note on their Submission receipts newer than their last visit |
| Schedule | rows by week, coloured by kind; TBC dates; row details; file chips that open each file; readings (files, reading list, "to come") | their state on hand-out and due rows; rows for their repos marked |
| Assignments | dates (TBC), late cutoff, late rule, points, how to hand in, solution shown, the shape note, the brief (a fold, rendered by GitHub), the course's late-work sentences | `<slug>-<handle>`, a team repo they can push to, the drop box; their team (from the repo, else from `GET /user/teams` by the `<slug>-` prefix, so a drop-box or external group finds it too) and its members; the Submission receipts issue (label `dsl-receipts`, or `dsl-feedback` on older repos): its body, the newest receipt, a patch note as "pull before you continue", every comment in a fold; the CONTRIBUTIONS.md ask on a team repo; for a student-choice repo after the cutoff, the Settings link to make it public |
| Marks | assignment titles | `grades-<handle>/grades.yml`: final grade, score (per question when given), penalty, feedback overall and per question, team and team feedback, a term total if present |
| Materials | the materials repos; each session's readings | the repo's recursive tree; each file read when opened |
| Set up | the materials repos | whether they forked each (`GET /repos/{login}/{repo}`: `fork` and `parent`); clone commands, VS Code and github.dev links; their assignment repos to clone. The local folders are kept in this browser only |
| Join | assignments forming teams, and each one's teams so far (name, headcount, cap; never who) with a Pick that fills in the team | their own Join course / Join team issues in `join` and the automation's last reply; after "You joined", the invitation's accept link |
| Instructors | the cards, with an email only where the instructor chose to show it | none (a picture hosted on the semester site is read through the API and shown as `data:`) |

Every screen also reads the person's role: `GET /orgs/{org}/teams/auditors/memberships/{login}`
(the team is secret, but a member may read their own membership). An **auditor** sees the
materials and the schedule and a note saying what auditing means; nothing offers them a repo,
a team or marks. On Home, a semester that has **invited** the person (`GET
/user/memberships/orgs?state=pending`, classic and App sign-in) shows as Invited with GitHub's
accept link; a fine-grained token cannot list invitations. If the role cannot be read, the screens say so and
promise no repo, team or marks; an auditor's nav omits Marks and Join.

The visit time behind "new since your last visit" is stored only after that semester's
receipts were read. Signing out forgets every visit time and remembered folder of that
login in this browser, the rendered markdown, the team list and the semester facts.

The shared facts come through one interface, `StudentData` (`src/model/student.ts`), read by
`StatusFileSource` from the engine's public `<semester>/.github/.system/student-status.json`
(`dsl_course/student_status.py`, rewritten with every status refresh; its allow-list schema is
`schemas/student-status.schema.json`). It carries the cutoff, the timezone and the policy's
kinds, so the console derives none of them. A semester whose engine has not written the file
yet is read from its site repo instead (`SiteSource`: the generated collections, `index.md`,
`_data/*.yml`), which also serves site-hosted instructor pictures. In a Student view nothing
of the student's own is read.

An **archived semester** is history: the student's own repos (read-only) and their marks from
the gradebook, from the same reads as a live one. No operation runs against it.

**Materials** open inside the console from the private copy: markdown and notebooks through
GitHub's markdown endpoint (one call; its HTML is sanitised by GitHub), notebook outputs as
text and images; an HTML page with its `<stem>_files/` bundle inlined (stylesheets as
`<style>`, images and fonts as data: URLs) in a frame sandboxed with no permissions. The
page's policy (no inline scripts, no `'unsafe-eval'`, no frames other than srcdoc) is inherited by every
document made from the console page, so a deck that needs its scripts (reveal.js, Quarto) opens in
the **deck viewer** instead: `deck.html`, a second document in the build with its own policy
(`default-src 'none'; script-src 'unsafe-inline' blob:; style-src 'unsafe-inline'; img-src
data: blob:; font-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'`). The
console opens it with `noopener` and hands it the inlined deck over a `BroadcastChannel` named
by a random id in its URL fragment (`src/model/deckTab.ts`); the viewer clears its session
storage, drops the fragment and shows the deck in a frame sandboxed to `allow-scripts` only (an
opaque origin: no storage, no cookies, `top.opener` null). The deck also downloads as one
self-contained file. PDFs open in
a new tab or download; anything else downloads. Files over 1 MB come from the blob API; the
API serves nothing over 100 MB.

**Join** opens the issue itself, as the student, through the API: the seeded form's body
under a hidden first line (`names.json` `join_markers`). GitHub drops the form's label on an
issue an account without push creates that way, so the `join` workflows route on that line
as well as on the label. When the API refuses, the console offers GitHub's own form,
prefilled (text inputs by field id). The answer is polled from the student's issues every
15 s for up to 5 minutes while one still waits. `?join=<org>`
(and Home's "Have an enrolment code?") opens Join course for a semester the person is not a
member of yet.

**Rate limit** (5,000 requests an hour per person; every read is ETag-cached, and an
unchanged 304 costs nothing). Opening a semester reads its `student-status.json` once (a
semester without one: its site, 9 fixed reads plus one per generated file).
Then the repo list, the gradebook and its last commit, the auditors membership, and one call
per team; `/user/teams` is read once per session, not per semester. This week and Assignments
add two calls per private repo (receipts issue, comments). A brief or the home text is
rendered once per page load (one `/markdown` call, a brief only when its fold opens); a
site-hosted card picture is one call. Set up adds one call per materials repo. A file costs
one call, a markdown file or notebook two, an HTML page one per bundle file it uses (at most
80); file bytes are kept by blob sha (up to 64 MB), so reopening costs nothing. **Home's This
week costs all of that again for each semester shown**: its site read, the repo list,
gradebook, role and teams, and the receipts reads (about 50 calls per semester on the demo on
a first visit, mostly free 304s after).

## What it reads

- Organisations (`src/model/discovery.ts`), by token kind, merged without duplicates:
  - classic token: `GET /user/orgs` and `GET /user/memberships/orgs?state=active`;
  - GitHub App: those two, plus the organisation accounts of `GET /user/installations`;
  - fine-grained token: GitHub answers neither organisation listing for it (`/user/orgs` is an
    empty list, `/user/memberships/orgs` is closed to it), so the owners of `GET /user/repos`
    and the public memberships (`GET /users/{login}/orgs`) are the candidates.

  A candidate not already known as a membership is kept only if
  `GET /user/memberships/orgs/{org}` says active. A fine-grained token that reaches none says
  so on Home: add the organisations under Resource owner and grant Members: read when creating
  it. When every listing fails (GitHub not answering), Home says the courses could not be
  listed rather than showing an empty list.
- Courses: organisations whose `.github` repo carries the `dsl-course-hub` topic; semesters from
  `.github/semesters.yml`; write access = push on `.github`.
- Semesters: organisations whose `.github` carries `dsl-semester`; the course
  from that repo's `dsl-course.yml` (`course:`), its name from the course's public `.github`;
  archived when that `.github` repo is archived. A semester known only from a course's
  registry has its `.github` read when its student screens open. Org names compare
  case-insensitively.
- Status: `semester-config/.system/status.json` (semester) and `.github/.system/status.json`
  (course), validated against `schemas/status.schema.json`. Staleness compares the file's
  `inputs` with one tree read. An absent file shows "Status not computed yet".
- Automation's heartbeat: the course's Scheduled release run list.
- Screens that show a file read it directly: `schedule.yml` (Details, events),
  `students.csv`, `instructors.yml`, a template's `grading_config.yml`, the site's `index.md`.
- Materials: the course org's repo list (`GET /orgs/{org}/repos`) for Other repos and last
  changes; a materials repo's recursive tree, badged from `publish.yml` and `.releaseignore`.

## What it changes

- Files, as the signed-in user, with a sha-conditional write (a file that moved on since it
  was read is refused, never overwritten): `schedule.yml`, `instructors.yml`, `students.csv`,
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
- Wizards: New course, New semester, New assignment, New materials. Each step checks live state
  before it lets you continue (the org exists, `hertie-dsl-bot` is an owner, the set-up left
  its repos, the template has both branches and a settings file that parses), and unfinished
  answers stay in this browser so leaving loses nothing. Setting up a course is the one
  operation outside the instructor's orgs: the wizard dispatches the central
  `bootstrap-org.yml` in `hertie-data-science-lab/dsl-teaching-toolkit` and follows its run.
  New assignment writes the Advanced marking values `assignment.create` does not take into
  the new template's `grading_config.yml`.

## Names and migration

Every repo and path name comes from `src/model/names.ts`, which reads the engine's
`schemas/names.json` (decisions 0010 and 0012). A semester's course comes from its pointer,
`.system/dsl-course.yml` in `semester-config`. The console reads
the new names only. An org that still carries a retired one (the `classroom-config` or
`welcome` repo, `people.yml`, `.dsl/`, the `dsl-cohort` topic, the old course registry or its
`cohorts:` key, `cohort_defaults:` in `dsl-course.yml`) shows one screen, "This semester has
not been migrated yet" (or course), with the engine's `NOT_MIGRATED` sentence for each, and
nothing else of it loads (`src/model/migration.ts`). Only the org a page is about is checked.
A check GitHub does not answer shows "Could not check whether this semester is migrated" and
runs again on the next page; it never counts as migrated.

## Schemas

`schemas/` holds the JSON Schemas the engine exports with `python -m dsl_course.schemas`;
a Python test fails when they drift. Never edit them by hand. `names.json` is every repo name
and path the console must spell as the engine does (the config and join repos, `.system/` and
each record in it); read it rather than writing a literal.

## Routes

Hash tokens as in the design mockup: `#semester`, `#schedule-s5`, `#assignment-<slug>`,
`#release-<id>`, `#template-<slug>`, `#marks-<slug>`, `#teams-<slug>`, `#materials-<repo>`;
the schedule editor also opens `#schedule-new`, `#schedule-semester` and `#schedule-archive`.
The hashes decision 0012 renamed redirect: `#cohort` to `#semester`, `#staff` to
`#instructors`, `#new-cohort-<n>` to `#new-semester-<n>`, `#schedule-term` to
`#schedule-semester`.
Wizards: `#new-course-1..4`, `#new-semester-1..3`, `#new-assignment-1..4`, `#new-materials`; a
step past the first unfinished one opens that one instead. `?template=<repo>#schedule-new`
opens a new assignment entry for that template; `?wizard=new-semester-3` adds a link back.
A problem's `fix {screen, entry}` is `#<screen>-<entry>`.
The course or semester rides in the query string: `?cohort=<org>` or `?course=<org>`
(`?semester=` opens the student screens).
Student screens: `?semester=<org>#week` (and `#schedule`, `#assignments`, `#marks`,
`#materials`, `#materials-<repo>/<path>` for an open file, `#setup`, `#join`, `#instructors`);
`?join=<org>` for Join course.
