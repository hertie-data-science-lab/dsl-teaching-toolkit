# DSL Teaching Console

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
and the token stays in `sessionStorage`: it is gone when the tab closes. At reload a saved
token or App session is dropped only when GitHub (or the relay) refuses it. With no answer
an App session is kept for the next reload; a pasted token is kept and checked again, the
screen saying it is retrying. Sign-out also
ends any run the console is following and forgets this session's runs and previews.

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
    VITE_GH_APP_SLUG=dsl-teaching-toolkit  # the App's URL name; empty drops the install step

The deployed console gets them in `.github/workflows/console-pages.yml`, as an `env:` on the
`npm run build` step, from the repository variables `GH_APP_CLIENT_ID`, `AUTH_RELAY_URL` and
`GH_APP_SLUG` (any may be empty).

## Setting up an org

The first step of New course and New semester lists the three things only a person can do on
GitHub, each with a tick that appears by itself (the step re-checks every 10 seconds and when
the window regains focus, until all pass): create the org, install the console app on it,
and invite `hertie-dsl-bot` as an Owner. The bot accepts the invitation itself, on the
scheduler's next quarter-hourly run in any course org, once the maintainers have added the org
to `orgs.yml` (`dsl_course/invitations.py`; maintainers.md, "The course org registry"). New
semester lists the semester's org in the course's `semesters.yml` as soon as it exists, which
is enough: the course's own run accepts the bot's invitation to it. Whether
the app is installed can only be seen from an App sign-in; with a token the line says so and
does not hold the step back.

The install link goes to the App's install page for that org in the same tab, and GitHub
sends the person back to the App's **Setup URL** with `installation_id` and `setup_action`;
the console drops them and reopens the wizard step the link was pressed on. In the App's
settings (General, Post installation):

- **Setup URL**: the console's URL, `https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/`;
- **Redirect on update**: on, so changing an existing installation comes back too;
- **Request user authorization (OAuth) during installation**: off (GitHub then ignores the
  Setup URL).

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
- anything else is not shown (a course the person can read but not change still shows read only,
  unless they only study in it: then they reach it through their semesters, decision 0031). A
  student cannot read a semester's pointer, so its course is the known course whose registry
  lists it.

An instructor always lands on **All courses** (decision 0030): every course the lab runs, read
from the toolkit's public `orgs.yml`, in three sections, **DSL courses**, **This semester**
(running semesters) and **Past semesters** (archived or past their end, newest first, ten at a
time with Show more; always shown, saying so when the person has none). A semester whose end
cannot be read ends on a date its key implies (a fall semester on 1 February, spring 1 August,
summer 1 October, winter 1 April). The person's own rows are in colour and ordered by what needs
them, each card with the course code on a quiet line under its title and naming their role
(course admin from `dsl-course.yml`, else instructor or teaching assistant from the newest
running semester's `instructors.yml`, else "you teach on this course"); the others are greyed,
not links, and say "Not one of your courses" ("You are a student" for a course the person
studies in). Each section has its own **My courses** checkbox (the first one in the page head,
left of New course), on by default and kept in this browser per account and section; while it is
on, the head says how many rows it hides ("+2 others"). Then **Your semesters** (one card per
semester the person is a student of, past ones greyed; such a semester shows only here, never
also as a row above, decision 0031). For a student-only account it is its own page, laid out as
the instructor's (decision 0029): **This semester** (the live ones, each card with its week and
"Next: ...") and **Past semesters** (archived, or past their last day; with no dates read yet,
by the date their key implies), which a **Current only** checkbox in the page head hides (kept
in this browser, per account). A student-only account with exactly one live semester (neither
archived nor ended, judged by its key) lands on its This week instead; the side nav and the
banner call an ended semester ended by the same rule. The top bar's Guide explains the
instructor console, so only a person with an instructor role sees it.

The mode picks the shell. `?semester=<org>` opens that semester's student screens (This
week, Schedule, Assignments, Marks, Materials, Join, Instructors). For a
semester the person teaches, that is the **Student view** (the Student view pill in the
course banner): the same screens with the instructor's own identity and a note, never a
student's repos or marks (rule 7). Its top bar reads "Student view (preview)", a link back to
the semester's Dashboard, as is "Back to instructor view" in its banner. Anywhere else the
console is in instructor mode for anyone who teaches somewhere, and in student mode otherwise.

The **side nav** is one tree in both consoles (decision 0031 rule 11), with the chevron before
what it expands. An instructor's is anchored on the open course: "All courses" above it, the
course name (a link to its overview), the course's pages (Overview first, where the name
goes, as a semester's Dashboard is where its name goes; a read-only course shows only Overview),
then its semesters as nodes: being set up
first, every live one (a green dot), then past ones ("ended" or "archived") newest first to
three rows, the rest under "Older semesters (n)". The open semester is expanded to its nine
pages, else on a course page the newest live one; one at a time. Other courses are reached
through All courses, and so are the person's own student semesters. A student's tree is
inverted, since a student takes each course once: the open semester's term is the anchor, its
courses the nodes (the open one expanded), another live term under them, and Past semesters
below, each expanding to its courses as links. A Student view's tree is that one semester's.

Every course and semester page, in either console, opens with the **course banner**: crumbs
that follow the tree ("All courses › Course › Semester"; a student's "Your semesters ›
Semester › Course", led by All courses for a person who also teaches), the course name as the page's one h1, and on a semester page the
semester's line under it (its name, state, week and dates) with the Student view pill (or
"Back to instructor view") and the semester on GitHub on the right. The overview's banner is
its head, with New semester; the page's own title is an h2 under the banner, with its `?`. A
student's banner takes the week and the dates from `student-status.json`, and This week says
how old that file's facts are ("Updated 3 h ago"). The footer names the course and the
semester. On a phone the preview's top-bar link reads "Preview".

The **course overview** is a status board in two columns. Setup & To do heads the left: two
folds, Initial setup (folded once every step not set aside is done) and To do (open while it
has items), the same checklist with a `?` on each line. For a viewer with write access the circle
before an open line is a button (decision 0032): an optional item (the engine's `stage_optional`
or a to-do's `optional`) can be set aside, which writes its id into `dsl-course.yml`'s
`set_aside:` through the Course details save path and moves it to a "Set aside (n)" fold at the
end of its section, with Bring back; a required one says why it cannot be and where it is done. Problems heads the right (the
course's, then each live semester's, tagged). The other panels, Semesters (each with its next
automatic event), Course details (with the public website's indicator and Publish button),
Recent activity (the last five operations across the course and its live semesters; who ran
each comes from its outcome file) and Handout materials followed by Assignment templates as one
block, go wherever the two columns come out closest in height (`splitColumns`, from each
panel's estimated height, problems counted up to four). Until the course's and every semester's
status has loaded the columns keep a fixed layout (`SETTLING_COLUMNS`), so panels do not move as
each arrives. The Dashboard's line under its title
says only what the banner does not: the exams and the archive date.

The Dashboard's week strip is its only filter: This week on load, any set of weeks picked,
none picked for all of them. The Problems heading names the selection ("Problems in weeks 3
and 5") with a "Show all weeks" link while a filter is on.

## Student screens and their sources

The semester's `status.json` is private (in `semester-config`), so a student cannot read it.
Each screen reads with the student's own account:

| Screen | Shared facts (`StudentData`) | The student's own (GitHub, directly) |
|---|---|---|
| This week (in the semester's timezone; "Updated <age>" from the file's `generated_at`) | rows due, handed out, released or on in the next 7 days; releases and announcements of the last 7; open team formation; the About block: course name, syllabus pinned, the home text, announcements | gradebook's last change (marks returned); whether they have a team; a patch note on their Submission receipts newer than their last visit |
| Schedule | rows by semester week (week 1 from `semester_start`, as the Dashboard counts; one group each for before and after the semester), coloured by kind; TBC dates; row details; file chips that open each file; readings (files, reading list, "to come") | their state on hand-out and due rows; rows for their repos marked |
| Assignments | dates (TBC), late cutoff, late rule, points, how to hand in, solution shown, the shape note, the brief (a fold, rendered by GitHub), the course's late-work sentences | `<slug>-<handle>`, a team repo they can push to, the drop box; their team (from the repo, else from `GET /user/teams` by the `<slug>-` prefix, so a drop-box or external group finds it too) and its members; the Submission receipts issue (label `dsl-receipts`, or `dsl-feedback` on older repos): its body, the newest receipt, a patch note as "pull before you continue", every comment in a fold; the CONTRIBUTIONS.md ask on a team repo; for a student-choice repo after the cutoff, the Settings link to make it public |
| Marks | assignment titles | `grades-<handle>/grades.yml`: final grade, score (per question when given), penalty, feedback overall and per question, team and team feedback, a term total if present |
| Materials | the materials repos; each session's readings | the repo's recursive tree (supporting folders such as `data/` and `img/` last, folded, under "Supporting files"); each file read when opened |
| Set up | the materials repos | whether they forked each (`GET /repos/{login}/{repo}`: `fork` and `parent`; asked again on focus and every 10 s, for up to 5 minutes, while a read says one is not forked); the Open button for each fork and each of their assignment repos, both in the semester's folder from Profile (decision 0027) |
| Join | assignments forming teams, and each one's teams so far (name, headcount, cap; never who) with a Pick that fills in the team | their own Join course / Join team issues in `join` and the automation's last reply; after "You joined", the invitation's accept link |
| Instructors | the cards, with an email only where the instructor chose to show it | none (a picture hosted on the semester site is read through the API and shown as `data:`) |

Every screen also reads the person's role: `GET /orgs/{org}/teams/auditors/memberships/{login}`
(the team is secret, but a member may read their own membership). An **auditor** sees the
materials and the schedule and a note saying what auditing means; nothing offers them a repo,
a team or marks. At the top of Home and of the student screens, every course or semester that
has **invited** the person (`GET /user/memberships/orgs?state=pending`, classic and App
sign-in; a fine-grained token cannot list invitations) shows with Accept. A classic token
accepts in the console (`PATCH /user/memberships/orgs/{org}`); the App cannot, as that needs
Members: write (decision 0002), so it, and a refused accept, link to GitHub's accept page and
read the list again when the tab regains focus. If the role cannot be read, the screens say so and
promise no repo, team or marks; an auditor's nav omits Marks and Join.

The visit time behind "new since your last visit" is stored only after that semester's
receipts were read. Signing out forgets every visit time of that login in this browser, the
rendered markdown, the team list and the semester facts. It keeps Profile, one for both
roles: the root folder, each course's own folder where one is set, the editor, and the folder
picked for the folder check (in IndexedDB).

The shared facts come through one interface, `StudentData` (`src/model/student.ts`), read by
`StatusFileSource` from the engine's public `<semester>/.github/.system/student-status.json`
(`dsl_course/student_status.py`, rewritten with every status refresh; its allow-list schema is
`schemas/student-status.schema.json`). It carries the cutoff, the timezone, the policy's
kinds and the semester's dates, so the console derives none of them, and `generated_at`, when
its facts last changed. A file of the first schema (`dsl.student-status/1`) is still read, its
dates and age unknown. A semester whose engine has not written the file
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
  `inputs` with one recursive tree read, by full path (`.system/assignments.lock.yml`
  included); an input recorded `null` is unchanged while the file is still absent. An absent
  status file shows "Status not computed yet".
- Automation's heartbeat: the course's Scheduled release run list.
- Screens that show a file read it directly: `schedule.yml` (Details, events),
  `students.csv`, `instructors.yml`, a template's `grading_config.yml`, the site's `index.md`.
- Handout materials: the course org's repo list (`GET /orgs/{org}/repos`) for Other repos and
  last changes; a materials repo's recursive tree, badged from its `.releaseignore`.

## What it changes

- Files, as the signed-in user, with a sha-conditional write (a file that moved on since it
  was read is refused, never overwritten): `schedule.yml`, `instructors.yml`, `students.csv`,
  `teams.csv`, `grading_sheets/<slug>.yml`, `.github/dsl-course.yml`, a template's
  `grading_config.yml` (on `solution`), a materials repo's `materials.yml` and `.releaseignore`,
  the course's `.github/opencourse.yml` (the public website),
  the site's `index.md` and `_announcements/`. "Treat as handout materials" on an Other repos
  row adds the `dsl-materials` topic to that repo (`PUT /repos/{o}/{r}/topics`). YAML is edited in place (`src/edit/yamlText.ts`):
  only the changed values' bytes move, so comments and their columns survive. After a write the
  console follows the commit's checks and says what they found.
- Operations, through the course org's Console workflow (`.github/.github/workflows/console.yml`,
  ref `main`, one `request` input; contracts section 1). `src/ops/adapter.ts` dispatches with
  `return_run_details`, polls the run, and reads the public `dsl-outcome` annotation and the
  private outcome file. Hand out, return marks, archive, update every copy and send new codes
  unlock only after a preview in the same session; publishing the public website, which has no
  engine preview, asks for a confirmation instead. Other previews are offered only where they
  show something you act on (`PREVIEW_NOT_OFFERED` in `src/ops/registry.ts` lists the ones
  that are not). The run popup lists each job step once (GitHub's "Post ..." steps are left
  out), resizes by its bottom-left grip, and its Stop cancels the workflow run
  (`POST .../actions/runs/{id}/cancel`), saying "Stopping…" until GitHub ends it and then
  "Stopped". Once a run ends, "See on GitHub" opens the op's `target` (`src/ops/defs.ts`):
  the repo, branch or folder it changed.
- Wizards: New course, New semester, New assignment, New handout materials. Each step checks live state
  before it lets you continue (the org exists, `hertie-dsl-bot` is an owner, the set-up left
  its repos, the template has both branches and a settings file that parses), and unfinished
  answers stay in this browser so leaving loses nothing. Setting up a course is the one
  operation outside the instructor's orgs: the wizard dispatches the central
  `bootstrap-org.yml` in `hertie-data-science-lab/dsl-teaching-toolkit` and follows its run.
  New assignment writes the marking values `assignment.create` does not take (points per
  question among them) into the new template's `grading_config.yml`. Starting from another
  repo is the console's (decision 0014): the engine makes the template fresh, then the
  console copies the ticked files onto `main` and `solution` with the signed-in user's
  token, one commit per branch through the git data API.

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

Hash tokens as in the design mockup: `#dashboard`, `#schedule-s5`, `#assignment-<slug>`,
`#release-<id>`, `#template-<slug>`, `#marks-<slug>`, `#teams-<slug>`, `#materials-<repo>`;
the schedule editor also opens `#schedule-new`, `#schedule-semester` and `#schedule-archive`.
Renamed hashes redirect: `#cohort` and `#semester` to `#dashboard`, `#staff` to
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
