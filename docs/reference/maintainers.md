# Maintainer guide

For whoever maintains **this repo**. Everything here is a constraint the code depends on and
only its comments record. If you run a course rather than the toolkit, you want
[the runbooks](../README.md) instead.

## A merge to main is a deploy - to the demo org

Every seeded workflow, in every bootstrapped org, checks this repo out at the ref **that org**
runs - `central_ref:` in its course org's `.github/dsl-course.yml`, defaulting to
`central.CENTRAL_REF` (`release`).

Two tiers, along one linear history. PRs squash-merge to `main`, which is what the demo
course org runs: **Deploy main** fans the refresh out to every org on `main` as the merge
lands, so a change is live there within minutes. Real orgs run `release`, which moves only
when someone presses **Promote to release** - a fast-forward along main's history, with no
approval environment, because the gate is an INSPECTION. Press it only once a manual
end-to-end look at the demo course org has passed: its issues, comments, the mails that went
out, the run logs and the cohort site, read by a person. A green test suite is not that
inspection.

Engine changes are live on the next press in each org; workflow *shapes* (inputs, jobs, crons)
are re-rendered by the same fan-out, and by each org's nightly **Refresh actions**. Rollback is
a `git revert` on `main`, promoted forward - never a force-push. `central_ref:` may be `main`,
`release`, or a full 40-character SHA on main's history.

An org's seeded workflows check the toolkit out at **the ref they were rendered with**, so
moving an org between refs is always: edit `central_ref:`, run that org's **Refresh actions**,
*then* retire the old ref. Deleting a ref an org is still rendered against takes down its
whole Actions tab, Refresh included. That is the order the `staging` retirement followed.
Tiers, the pre-promotion checklist and the full rollback procedure:
[central-admin.md](../../docs-admin-arch/central-admin.md#deploying-the-toolkit).

## Doc filenames are a public API

These paths are linked by **absolute URL** from inside every bootstrapped org, so renaming one
breaks a live link that faculty click:

| Doc | Linked from |
|---|---|
| `docs/01-new-course-org.md` | `config_digest.COURSE` |
| `docs/03-add-assignment-to-course.md` | `config_digest.GRADING_CONFIG` |
| `docs/05-manage-teaching-team.md` | `templates/classroom-config/people.yml`, `config_digest.PEOPLE` |
| `docs/06-enrol-students-to-cohort.md` | `config_digest.ROSTER` |
| `docs/07-schedule-releases.md` | `source_digest.py`, `profile_readme.py`, `templates/classroom-config/schedule.yml`, `templates/classroom-config/validate-schedule.yml` |
| `docs/08-release-materials-to-cohort.md` | `scaffold._RELEASEIGNORE_STUB` (seeded into every materials repo) |
| `docs/09-release-assignment-to-cohort.md` | `config_digest.TEAMS` |
| `docs/10-grade-and-return-assignments.md` | `config_digest.GRADING_SHEETS` |
| `docs/README.md` | `profile_readme.py` |

Every `Digest.doc` is one of these: each digest issue ends with a `Field reference:` link built
from it, so the seven of them are the widest surface in this table.

`grep -rn 'blob/.*/docs/' dsl_course/ templates/` before any rename.

## Frozen public contracts

Things whose *literal spelling* is depended on from outside Python:

- **CLI module names.** Seeded workflows and templates invoke `python3 -m dsl_course.<x>`:
  `assign`, `bootstrap_course`, `collect`, `deploy`, `derive`, `enrol_codes`, `grades`,
  `list_orgs`, `notify`, `scaffold`, `schedule`, `scheduler`, `seed`, `site`,
  `source_digest`, `status`, `syllabus`, `sync_faculty`, `sync_membership`, `sync_roster`,
  `sync_teams`, `teardown`.
  A rename strands every org until it refreshes. `assign` carries TWO modes on one flat
  parser rather than a subcommand, for the same reason: `--patch-path` switches it from
  handing an assignment out to patching one that is already out, and every org's Release
  assignment workflow spells the bare form.
- **`roster.FIELDS` / `roster.normalise_role` / `teams.FIELDS`** are re-implemented in the
  shipped JavaScript (`templates/welcome/onboard.yml`, `team-formation.yml`), which cites them by
  name. Change a column and change both sides.
- **`grades.team_lock_text`'s LAYOUT.** `classroom-config/assignments.lock.yml` is parsed by
  a line scanner in `templates/welcome/team-formation.yml` (github-script has no YAML
  library), which matches a two-space assignment key and four-space `team_formation:` /
  `max_team_size:` under it. Re-indenting the writer, or nesting the entries any deeper,
  makes every Join-team request in every cohort read as "not an assignment here" - and the
  form is the only place a student would find out. `tests/test_welcome_templates.py` runs
  the SHIPPED scanner over the writer's real output; keep that pairing.
- **`gh_contents.STUB_MARKS` and `SUPERSEDED_DESCRIPTIONS` / `SUPERSEDED_COHORT_*` / `SUPERSEDED_COURSE_*`**
  are convergence chains matched against *live* state. Rewording a stub or a repo description
  means **adding a link to the chain**, never editing one. For the descriptions, an org on the
  oldest string must still reach the newest in one pass; for `STUB_MARKS`, a repo seeded with an
  older wording must still be recognised as unwritten, or its placeholder syllabus ships.
- **`course.FEEDBACK_ISSUE_TITLE` / `FEEDBACK_ISSUE_LABEL` / `FEEDBACK_ISSUE_MARKS`** identify
  the one Feedback issue in each submission repo. `assign` opens it, `grades` posts receipts and
  grades into it, and the lookup (label -> body mark -> exact title) is what stops a second one
  appearing over a thread a student is already reading. `FEEDBACK_ISSUE_MARKS` is a chain like
  `STUB_MARKS`: add a wording, never edit one, or every issue opened under the old one becomes
  invisible. The hidden `<!-- dsl-receipt:{sha}:{event} -->` on each receipt comment is what
  makes the quarter-hourly refresh post once rather than four times an hour.
- **Repo topics** are machinery markers: `dsl-course-hub`, `dsl-cohort`, `submission`, `gradebook`,
  `assignment-template`. Discovery reads them; renaming one is a discovery outage.
- **An ARCHIVED `classroom-config`** is a cohort's "finished" marker. `teardown` archives it
  last, after everything else it freezes; `seed.refresh`'s per-cohort loop reads it off the
  org listing and skips that cohort whole, and `grades.write_team_lock` reads it so the
  membership sync does not write into a sealed repo. So archiving one closes a cohort whether the person doing it meant
  that or not, and anything that freezes a cohort must do it in that order - the archived
  repo is read-only, and a marker set early strands whatever had not happened yet.
- **`.github/cohort-courses-pages.yml`** is the cohort registry every dropdown reads, and
  **`.github/.last-refresh`** is the heartbeat that keeps an org's crons from GitHub's 60-day
  inactivity disable.
- **`releaseignore.RELEASEIGNORE`** (`.releaseignore`) is a filename faculty type into their
  own content repos. A rename silently stops withholding whatever the old name held back -
  worse than an outage, because the release still goes green. Nothing re-spells it: the
  matcher withholds the file itself, so no other module needs to name it.
  Seeded CREATE-ONLY, in the scaffold's materials skeleton, and deliberately NOT a
  `dsl-stub:` file: `is_untouched_stub` asks whether the mark is anywhere in the text, and
  the natural edit to a withhold list is to APPEND a pattern under the seeded comments. A
  marked file would still read as untouched and be rewritten by the nightly refresh -
  faculty's patterns gone, and whatever they withheld shipping again on a green run. The
  price is that its wording cannot be improved in a repo that already has it.

## The grading sandbox's two outward contracts

`collect` runs student code, so what it promises the outside world is short and load-bearing:

- **`tests/run.sh`** (`collect.RUN_SCRIPT`) on a template's `solution` branch replaces pytest.
  It is run with `sh`, from the runspace, under `RUN_TIMEOUT` and the same rlimits, and is
  handed `DSL_JUNIT_OUT` (an absolute path it must write a JUnit XML to - that XML *is* the
  score) and `DSL_SUBMISSION_DIR` (the checkout). **Its exit code is ignored**; no XML is the
  failure. Faculty write these by hand, in R and whatever comes next, so the three names are
  frozen the way a CLI module name is - renaming one silently stops scoring an entire course
  and the run still goes green. `score_from_junit` counts every `<testsuite>` in the file
  because testthat writes one per test file.
- **The completion check** executes the pinned notebook with `nbconvert --execute
  --allow-errors` and records `ran-clean` / `errors:N` / `not-attempted` / `no-notebook` /
  `timed-out` / `did-not-run` into `info.completion`, archiving the executed copy at
  `autograde/<slug>/<key>.ipynb` under a 5 MiB cap. It needs **`ipykernel`** as well as
  `nbconvert` (nbconvert converts without a kernel and cannot execute without one) - both
  pinned in `requirements.txt`, which every seeded workflow installs; a runner without them
  records the decision once and stays green rather than reporting a cohort of failures.
  `not-attempted` is byte identity (`gh_contents.blob_sha`) against the notebooks still on
  the template's default branch, and needs EVERY notebook in the checkout to match one; the
  notebook executed is the first that does not. The baseline is the course template's `main`
  as it stands NOW, not the frozen cohort-side hand-out, so **rewriting a template's `main`
  after handout makes every submission look attempted** - which is the safe way round. It
  runs OFFLINE (the proxy variables point at a dead port - best-effort, not a jail), which
  docs/10 tells faculty to write the assignment for.

  It is independent of `autograde`: `collect` reaches its target loop for a hand-marked
  assignment, and only "no tests AND no completion check" is the exit that records a skip.

### What the sandbox actually promises

Every graded subprocess - the hidden tests, `run.sh`, the notebook execution, the grader's
reading copy - goes through `collect._run_limited`, which is the only place any of them is
spawned. What it guarantees, and what it does not:

- **A separate uid.** Graded code runs as `dsl-sandbox` (`course.SANDBOX_USER`), created
  once per job by the rendered preamble of every job that grades, via
  `sudo -n -u dsl-sandbox env -i <sanitised env> <argv>`. This is the load-bearing one. A
  UID is the boundary, not an environment: on Linux a process reads `/proc/<pid>/environ`
  of anything running as its own user (Yama's `ptrace_scope` gates ATTACH, not that read),
  and the grading process holds `GH_TOKEN` - the org-owner PAT - for the whole leg. Without
  the separate uid, `grep -l GH_TOKEN= /proc/*/environ` from a notebook cell reads it
  straight out, past the sanitised environment and past the "no secret-bearing step after
  the graded one" rule alike.
- **FAIL CLOSED under Actions.** No account, or no passwordless `sudo`, and `collect`
  records a skip for the assignment before it clones anything. Student code is never run by
  the process holding the token. Off a runner (`GITHUB_ACTIONS` unset - a maintainer's
  laptop, where there is no such account and no bot token in the environment) it degrades
  to the in-process sandbox and says so loudly, once per run.
- **Nothing outlives a submission.** `sudo -n pkill -9 -u dsl-sandbox` runs after every
  graded command, however it ended. A `fork(); setsid(); fork()` daemon escapes the process
  GROUP by definition, and the leg walks submissions serially in one process - so a
  survivor of one student's run would be alive while the next student's clone sits in a
  predictable temp path. `killpg` cannot reach it at all once it runs as another uid.
- **The graded trees are handed over and taken back.** The checkout and the runspace are
  `chown`ed to the sandbox user before the run and back afterwards (widened to their
  `mkdtemp` roots - a 0700 root would otherwise leave the checkout unreachable), and
  `HOME`/`TMPDIR` are re-pointed into them. What the grader reads back out of them - the
  JUnit report, the executed notebook, the rendered copy - has to be a REGULAR file, read
  under a size cap with `O_NOFOLLOW`/`O_NONBLOCK` (`collect._result_bytes`); anything else
  counts as "the run produced no result", because a FIFO at the report path would otherwise
  block that read until the six-hour job ceiling and a symlink would score whatever it
  pointed at. A chown-back that fails costs a log line and a temp tree left behind for the
  length of the job, not a traceback out of the parent's own cleanup.
- **Best-effort, and only that: the network.** The proxy variables point at a dead port, so
  every well-behaved client fails - but there is no network namespace here, and a
  determined socket still opens. docs/10 tells faculty to commit the data rather than rely
  on this.
- Also: `RUN_TIMEOUT` per subprocess, the `_apply_rlimits` caps,
  output to `DEVNULL`, `.git` and the student's own rigging files removed before anything
  starts, and no token of any kind in the child's environment.

## Secrets an org carries

Three values are published onto an org by the toolkit itself, all through
`bootstrap_course.set_org_secret` - which scopes the org secret to the infra repos that
exist and then mirrors it as a repo secret onto the private ones, because on GitHub Free a
`selected` org secret is never delivered to a private repo:

| Value | Held centrally as | Reaches an org via |
|---|---|---|
| `DSL_BOT_TOKEN` | a secret on this repo | Bootstrap with `set_secret: true`; `seed refresh` also mirrors it onto each content repo |
| `DSL_MAINTAINER_EMAIL` | a repository **variable** on this repo | Bootstrap with `set_secret: true`, and Bootstrap cohort forwards it to a cohort |
| `DSL_COURSE_ADMIN_EMAILS` | a repository **variable** on this repo | Bootstrap with `set_secret: true`. COURSE orgs only - Bootstrap cohort does NOT forward it |

`DSL_MAINTAINER_EMAIL` is where fault mail goes. A variable centrally and a secret on the
org: an address is not a credential (and a masked secret cannot be read back to check it),
but a seeded workflow can only read it from `secrets.`. `mailer.maintainer_address` falls
back to `GRAPH_SENDER` when it is absent, so an org without it mails the shared send mailbox
rather than nobody, and `Check cohort setup` reports which of the two an org is on.

Nothing converges it. "Refresh actions" runs INSIDE the course org and cannot read this
repo's variables, so an org bootstrapped before the variable existed gets it once, by hand:

    gh secret set DSL_MAINTAINER_EMAIL --org <course-org> \
      --visibility selected --repos .github --body '<address>'

`DSL_COURSE_ADMIN_EMAILS` is the same arrangement one level down, and needs the same command
with the same flags on an org bootstrapped before it existed:

    gh secret set DSL_COURSE_ADMIN_EMAILS --org <course-org> \
      --visibility selected --repos .github --body 'a@x.edu,b@x.edu'

It is a comma-separated list of the course admins' addresses, and it is who hears about a
fault in that course org's OWN config - `dsl-course.yml` and `cohort-courses-pages.yml`,
which share one digest issue in the course org's `.github` (*dsl-course.yml / cohort registry
has entries the sync cannot use*). An org secret and never an `email:` in `dsl-course.yml`:
that file is public, and is itself one of the files these mails are about. Two steps read it
- the scheduler's release pass and Sync membership's automatic job, both in the course org -
and no other rendered workflow carries it (`tests/test_renderers.py` enforces both halves).
Unset, the digest issue's `cc @<course>/course-admin` is the only channel and the run log
says how many admins went unmailed.

`.github` is the only infra repo a COURSE org has, and it is public, so no mirror is needed
there. (Re-running Bootstrap on the org with `set_secret: true` does the same thing and is
the documented idempotent-repair path.) Cohort orgs need nothing, and that is a constraint,
not an omission: every fault mail is sent from the course org's `.github`, so **no workflow
seeded into a cohort may wire the mail env** - a cohort carries `DSL_BOT_TOKEN` and nothing
else, and a step reading `GRAPH_*` there resolves to empty and sends to nobody while
reading as a channel that works. `Validate schedule` therefore asks for the maintainer in
its annotation instead of emailing them
(`tests/test_validate_schedule_template.py` enforces it).

Nothing converges a cohort's addresses either. `email:` is required on every instructor and
TA entry in a cohort's `classroom-config/people.yml`, and that file is INSTRUCTOR-OWNED, so
no refresh can fill it in: until somebody edits it by hand the whole feature is inert on
that cohort - every fault still opens its digest issue and still @mentions the instructors
team, and no email goes anywhere. `Check cohort setup`'s C7 row counts the entries without
one, and the run log names the handles.

The four `GRAPH_*` transport secrets are a one-time central setup, set by hand per org and
never propagated:
[central-admin.md](../../docs-admin-arch/central-admin.md#email).

## File ownership

Seeded files carry their owner on the first line, and the write site enforces it:

- **SYSTEM-OWNED** - written unconditionally on every bootstrap and refresh, so fixes reach
  running courses. Workflows, generated docs, `*.sample`.
- **INSTRUCTOR-OWNED** - `gh_contents.seed_if_absent` only. Rewriting one destroys live state (roster
  rows, enrol codes, the term's schedule). The code comments call this "USER-owned"; the shipped
  stamp says INSTRUCTOR-OWNED. Same thing.
- **`dsl-stub:`** is the third state: an instructor-owned file we seeded and they have not yet
  written. `gh_contents.STUB_MARKS` recognises it, and `deploy._is_withheld_stub` reads that to
  keep an unwritten SYLLABUS.md out of a release - shipping the placeholder would give students
  faculty instructions and empty tables as their syllabus.
  It does NOT license a rewrite. Nothing refreshes a seeded stub any more: the marker cannot
  tell "still ours" from "edited in place", because a file filled in UNDER the marker still
  carries it, and rewriting that destroys the writing. Every instructor-owned file is
  create-only, and improving one in a repo that already has it is a deliberate hand-write.

The full rule is the ownership note at the top of `bootstrap_course.py`.

### The one SYSTEM-OWNED file that is not a template

`classroom-config/assignments.lock.yml` is SYSTEM-OWNED like the dispatchers, but it is
DERIVED - rendered per cohort from that cohort's `schedule.yml` and each named template's
`grading_config.yml` - so it cannot join `welcome.CLASSROOM_SYSTEM_FILES`, which maps a
repo path to a file under `templates/`. Adding a file like this is four places:

1. the renderer and the writer, beside what owns the subject (`grades.team_lock_text` /
   `grades.write_team_lock`), with the SYSTEM-OWNED stamp emitted by the writer itself;
2. `seed.refresh`'s per-cohort loop - which is BOTH how it is seeded (a Bootstrap cohort
   run ends in `seed refresh`) and how it converges nightly. An archived cohort is skipped
   there, which is what keeps a finished semester frozen;
3. every path that can move one of its inputs, so it does not wait for the night:
   `sync_membership.sync` (its dispatcher fires on a `schedule.yml` push) and
   `assign.provision_all`. `put_file` blob-compares, so the extra call sites cost a read
   apiece and no commit;
4. a test that the nightly loop reaches every live cohort, beside the pointer's
   (`tests/test_bootstrap_seeding.py`).

It carries no `.sample` twin and is absent from `example-course/cohort-org/`: nobody edits
it, so there is nothing in it for a person to copy.

A course website is wholly the toolkit's: `scaffold_site` creates `<org>.github.io` EMPTY and
seeds only its Pages build, then every sync writes `templates/site/` (SYSTEM-OWNED) and seeds
`templates/site-seed/` into any path the site lacks (INSTRUCTOR-OWNED). There is no
`course-website-template` repo any more.

## Module layers

`dsl_course` is layered, and the import graph is acyclic (`tests/test_architecture.py`
enforces both that and the absence of function-local imports). A module imports only
layers above its own:

| Layer | Modules |
|---|---|
| 0, nothing | `log`, `course` (the course vocabulary: config repo, term tag, session-folder rule, syllabus filenames, org topics), `readings`, `fs`, `releaseignore` (the `.releaseignore` rule) |
| 1, the shell | `ghcli` (`gh`/`git`, timeouts, the 404 test) |
| 2 | `central` (which ref an org runs), `repos` (existence, creation, topics, descriptions, the publication denylist), `gh_teams` (an org's settings and its teams), `issues` (one self-updating issue, found by its EXACT title) |
| 3 | `gh_contents` (file reads and writes, seeded stubs), `workflows_render` |
| 4 | `discovery`, `roster`/`teams`/`schedule`, `workflows_place` |
| 5 and up | `access` (team permissions and the faculty floor), `schedule_plan` (the session rows a plan declares), `cadence` (the scheduler's driver-health and late-delivery alarms, read off its own run history), `welcome`, `profile_readme`, `scaffold`, `site_repo` (the Jekyll site repo both websites publish into), `site`, then the CLIs |

Two placements are not where they read: `access` sits above `discovery`, because the
faculty floor is computed from what discovery finds, and `site_repo` above `scaffold` and
`welcome`, whose seeding it reuses.

`releaseignore` is the only module at layer 0 with a third-party dependency (`pathspec`).
Keep it out of widely imported modules - an import in `repos` or `gh_contents` gives every
CLI in the package that dependency.

Add a name to the layer that owns the subject, not to whichever module already imports it.

## Adding a workflow

Four places, in order - miss the last and every org keeps two buttons for one job:

1. a renderer in `workflows_render.py`;
2. its path in `seed.seed_github_workflows`'s `files` dict (or `workflows_place.WORKFLOWS`
   for a run-from-repo one);
3. `tests/test_renderers.py`'s `ALL_RENDERED` - a completeness test fails otherwise;
4. when *retiring* a path, add it to that call's `delete=` tuple (or
   `workflows_place.RETIRED_WORKFLOWS`), so orgs seeded before the change drop the old file.

## The clean break in `schedule.yml`

`type:` and `max_team_size:` were accepted on an assignment entry and BEAT the template's
own `grading_config.yml`. They are gone from `KNOWN_ASSIGNMENT` and from `AssignmentEntry`:
`schedule.yml` says WHEN, `grading_config.yml` says WHAT, and the two no longer overlap.
Written in a schedule now they are unknown keys - `Validate schedule` names the file they
moved to and the entry still runs.

No fallback, and deliberately none: a fallback is how the two files came to disagree, with
repos of one kind graded as the other. **The migration is by hand, per org** - the toolkit
never rewrites an instructor's file - and step 1 goes BEFORE the release ships:

1. **Before the promote**, add `type:` (and `team_formation:` / `max_team_size:` where the
   assignment is a group one) to each template's `grading_config.yml`, on its `solution`
   branch. On the shipping release the schedule still wins, so this changes nothing; leave
   it until afterwards and every cohort that declared `group` only in `schedule.yml` hands
   out one repo per STUDENT and its Join-team form refuses every request, so the teams
   cannot even be formed to recover;
2. after the promote, delete the two lines from every live cohort's `schedule.yml`;
3. run **Sync membership** (or wait for 06:13) so `assignments.lock.yml` is rewritten from
   the templates.

Between (2) and (3) the Join-team form answers off the previous lock file, which is the
reason the file exists rather than the form reading the schedule. A schedule assignment
whose template does not exist yet locks to `none` and refuses every team - the case Maths
a2-a4 were in, and the reason the button that creates a template writes its
`grading_config.yml` for you.

## Config faults

Every file faculty edit by hand can be wrong in a way the toolkit detects and only a human
can fix. One type carries all of them (`faults.ConfigFault`), one engine keeps their issues
(`config_digest`), one notifier addresses them (`notify`).

**Seven digest issues**, one per file, each found by its EXACT title - a title that varied
with the faults would never match and every run would open a new issue, so these are frozen:

| issue title | file | where it lives |
| --- | --- | --- |
| `schedule.yml: planned releases cite sources not staged in the course org` | `schedule.yml` | cohort `classroom-config` |
| `people.yml has entries the sync cannot use` | `people.yml` | cohort `classroom-config` |
| `students.csv has rows the toolkit cannot use` | `students.csv` | cohort `classroom-config` |
| `teams.csv has rows the toolkit cannot use` | `teams.csv` | cohort `classroom-config` |
| `grading sheets have entries the grader cannot read` | `grading_sheets/` | cohort `classroom-config` |
| `assignment grading_config.yml has values that will not grade as written` | template `solution` branch | cohort `classroom-config` |
| `dsl-course.yml / cohort registry has entries the sync cannot use` | both course files | course `.github` |

The body is rewritten every tick (GitHub does not email about that); a comment - which it
does - is posted only on appearance, escalation and clearing. An empty fault list CLOSES the
issue, which is why a file that could not be READ drops out of the tick instead of syncing
empty. `schedule.yml` absorbed the old *entries the scheduler cannot read* issue, closing it
once (`source_digest.ABSORBED`); the grading sheets share one issue because a cohort marks
half a dozen assignments in one afternoon.

**Two ladders**, and `ConfigFault.fires` is which:

- A **moment** - a source the plan cites, a `grading_config.yml` value used to grade -
  climbs as its moment approaches: WARNING at 24h, URGENT at 12h, CRITICAL at 6h, MISSED
  once it has passed, maintainer copied from CRITICAL. Held 23:00-07:00 in the cohort's own
  zone, then sent once at the loudest rung crossed overnight.
- **No moment** - a line nobody can read - sits flat at WARNING from the moment it exists:
  waiting changes nothing about it. Mailed on appearance, again at 2 days and at 7 days with
  the maintainer copied, then silence. Never held: the value of saying it inside the minute
  is that whoever pushed it is still at the keyboard.

**Recipients** are the committer of the faulty line, by blame of the file at that line (for a
CSV, the actor who pushed it, skipping the bot). Any addressee who is a TA puts the
instructors on Cc; nobody identifiable falls back to every instructor, and a cohort whose
`people.yml` holds no address at all falls further - to `DSL_COURSE_ADMIN_EMAILS`, then to the
maintainer, with the run log naming which fallback it used and never an address. The
course-level digest goes to `DSL_COURSE_ADMIN_EMAILS` (see
[Secrets an org carries](#secrets-an-org-carries)) with the maintainer copied from the first
mail, and falls back to `cc @<course>/course-admin`.
A CSV or sheet fault carries the row or line and the column - never a cell value, never a
handle - in the mail, the issue and the log alike.

**What still reds an unattended run.** Nothing above does, and there is no exception. A
content fault is delivered by its digest issue and the mail beside it, never by an exit
code: Sync membership skips the cohort it cannot read (`sync_membership._CONTENT_FAULT` -
`faults.Unusable` plus a YAML error), skips a cohort with no `people.yml` and no
`students.csv`, and does not count a `teams.csv` handle that is not on the roster; a course
file it cannot read reconciles nothing at all and still exits 0; a `schedule.yml` that does
not parse is one fault on the file, not a red scheduler tick; Send enrolment codes is green
for a roster nobody can parse and for one with nothing in it yet (`enrol_codes._GREEN`). A
red X means the RUN broke - a `gh` read or write refused, a token that lost its scope, an
unset mail transport - and the maintainer is emailed its log tail. `faults.Unusable` is the
whole distinction: it IS a RuntimeError, so every consumer still stops for it, but a `gh`
read that failed is not one. `gh_contents.get_file_content` draws the same line at the API -
None only for a 404, raise otherwise - which is why an absent file is faculty's to fix and a
rate limit is not.

`Validate schedule` keeps its red X, its annotations and its commit comment on a push: those
reach a person who is at the keyboard. `Check cohort setup` shows which digests are standing
(rows C8 and C9).

## Crons and gates

Five seeded crons: **Scheduled release** at :07/:22/:37/:52 every hour; **Refresh actions**
05:27, **Publish course website** 05:58, **Sync membership** 06:13, **Sync site** 06:41 daily.
Each reports its own failures, because GitHub emails a scheduled-run failure only to whoever
last committed the file - the bot. **Send enrolment codes** carries the same three steps
(`_CRON_NOTICE` + `_CRON_MAIL` + `_CRON_CLOSE`) without being a cron at all: a roster push
fires it, so it has no actor either. Six workflows report their own failures; only five
declare a `schedule:`, which is what `CRONS` in `tests/test_renderers.py` means.

No cron may sit on minute 0/15/30/45 and no two daily ones may share a slot - GitHub drops the
most contended minutes first (on `0 * * * *` the scheduler was delivered 6 ticks a day, not 24),
and membership must write the teams that Sync site then reads. Both rules are enforced by
`tests/test_renderers.py`; the reasoning sits above the cron literals in `workflows_render`.

A red X on any of the six means the run itself broke, never that a file faculty own is wrong
- see [Config faults](#config-faults) for the line between the two, and for where each of
these checks runs. The COURSE org's own two files are checked once per scheduler tick
(`scheduler._preflight_course`) and again on a push to either, from Sync membership
(`--check-course-config`). Neither pass changes an exit code, and neither does the cohort
listing that follows: a registry nobody can parse lists no cohorts and releases nothing.
That is why the check runs BEFORE the listing - reported there, or not at all.

## The scheduler's two drivers

Even off a contended minute GitHub delivers only 2-7% of the fires it promises, with observed
gaps of 13 hours, so its cron is the **backstop** and not the clock. The primary driver is a
systemd timer on the lab server ds01 - `dsl-scheduled-release.timer` running
`scripts/maintenance/dsl-scheduled-release.sh`, both in `hertie-data-science-lab/ds01-infra` - which
POSTs `repository_dispatch: scheduled-release` to the `.github` repo of every `dsl-course-hub`
org at :00/:15/:30/:45. A push to a cohort's `schedule.yml` dispatches the same event. Each
driver guards the other, and the workflow is one run per arrival whichever it came from.

Those four minutes are not a breach of the rule above: that rule is about **GitHub's** cron
scheduler dropping the contended ones. A REST POST is served like any other API call, and the
offsets deliberately interleave GitHub's :07/:22/:37/:52, so a lost fire costs at most 8 minutes.

`cadence.py` reads the workflow's own run history on every real all-cohorts run (never on a
dry-run) and files two self-closing issues. A due moment that shipped more than **60 min** late
opens *Scheduled release: late delivery* in that cohort's private `classroom-config`, which
closes once the last **8** qualifying gaps are all 20 min or less. A dispatch-driven run more
than **2h** old means ds01 is down and opens *Scheduled release: driver health* in the course
org's `.github`; the last GitHub cron fire is printed there as information and never alarms.

The check only runs inside a run, so driver health is decided at the first run more than 2h after
the last dispatch-driven one - with ds01 down, the next GitHub-delivered cron run, which can take
hours. Everything either alarm says is bounded by the **20** runs the check fetches, and both are
armed only while a dispatch-driven run sits inside that window: a freshly bootstrapped or newly
promoted org never alarms on its way in, and an org whose dispatcher died long enough ago to
scroll out of the window re-disarms unless its driver-health issue is already open.

**Break-glass.** If both drivers are down, or Actions itself is out, drive a course org from a
laptop with a `repo`-scoped token: `GH_TOKEN=<token> python3 -m dsl_course.scheduler
--course-org <org> --all-cohorts`. Add `--dry-run` first - it prints what would fire and writes
nothing. It is the code path the workflow runs, and the one-shot markers are what make repeating
it safe.

Every `workflow_dispatch` job sits behind the `check-team` gate (`workflows_render._CHECK_TEAM`),
which asks for write on the repo the button lives in. The scheduler, refresh and Send enrolment
codes are **ungated**: neither a cron nor a `repository_dispatch` has an actor to check, and each
only re-calls idempotent work. Send enrolment codes has no `workflow_dispatch` at all - a push to
a cohort's `students.csv` is its only trigger, and therefore the only way codes are sent.

`seed refresh` is serialised against itself (`concurrency: seed-refresh`) and **deliberately not
shared** with the click workflows that end in a refresh. Actions concurrency has no queue - a group
holds one pending run, so a third arrival cancels the second, and an operator's click would
silently do nothing.

## Tests

`python3 -m pytest -q` (CI runs the same). `pytest` and `jekyll-contract`, `ci.yml`'s two
jobs, are both required checks on `main`. Python 3.10 is the floor (ruff's `target-version`);
CI and every seeded workflow run 3.12. Conventions:

- `conftest._no_live_gh` refuses any live `gh` from a test, guarding the **binary** rather than
  `ghcli.gh`, so the retry ladder stays testable and `git` against a tmp repo still runs.
- Never mock the subject under test - stub what it reads.
- No `inspect.getsource` assertions: test behaviour, not text.

## End-to-end harness

`tests/e2e` drives the REAL seeded workflows against the demo tier: New assignment ->
schedule block -> Scheduled release (handout) -> a genuine student push -> Scheduled release
(due-date sheet refresh) -> Scheduled release (snapshot + autograde), then puts both orgs
back. It proves the wiring unit tests cannot - a click, a cron, a token and a repo - and it
runs against the demo org between **merging to main and Promote to release**, alongside the
manual inspection that is the actual gate. Three scheduler passes, not two - handout, due,
cutoff - for the reason given with the cleanup notes at the end of this section.

    DSL_E2E=1 \
    DSL_ORG_ALLOWLIST=hertie-dsl-demo-course-e1234,hertie-dsl-demo-f2026 \
    GH_TOKEN=<maintainer classic PAT, incl. delete_repo> \
    DSL_E2E_STUDENT=<handle> DSL_E2E_STUDENT_TOKEN=<fine-grained PAT> \
    python3 -m pytest tests/e2e -q

Without `DSL_E2E=1` the whole directory is skipped (it still shows as a skip, so a broken
gate is visible); the pure parts are covered by `tests/test_e2e_harness.py` in the ordinary
suite. The student token is fine-grained, Contents R/W on the demo cohort org only. The
maintainer token holds `delete_repo`, which the bot never does - that is why cleanup is a
command and never a workflow. Environment variables only; no dotfile.

Three fences, and all three must hold. `DSL_ORG_ALLOWLIST` refuses any WRITE (`gh` or `git
push`) outside the orgs it names - opt-in, unset everywhere else, and it raises rather than
returning a failure pair, which `repo_exists` would read as absence. `tests/e2e/allowlist.py`
names the two demo orgs as a literal; `DSL_E2E_ORGS` may only NARROW that. Preflight refuses
to start unless the course org declares `central_ref: main`, `main` is this checkout's
HEAD, every workflow the org holds is byte-for-byte what this checkout renders for it, the
test student has a roster row, and the run's namespace is empty.

That workflow check is a blob-sha comparison, not a timestamp: the preflight renders the
org's whole `.github/workflows` set with `seed.github_workflow_files` (the same call
Refresh actions makes, at the tier the org declares) and compares each blob sha with the
org's tree. Every input to that render is discovered from the org, so nothing is hardcoded
and no file is excused. A named file means run **Refresh actions** and start again. The
`.last-refresh` heartbeat is only checked for existence - its content is the date, so it
moves at most once a day and could never show a promotion made an hour ago.

Everything a run creates is namespaced `assignment-90-<run id>`. If it dies halfway:

    python -m tests.e2e.cleanup --run-id <run id> [--dry-run]

which deletes only repos matching `assignment-90-<run id>(-.+)?`, removes only its own
`# dsl-e2e:<run id>` fenced block from schedule.yml, and drops only its own snapshot /
autograde / grading-sheet artefacts. Anything else that drifted is REPORTED, never deleted.
Budget 20-30 minutes of wall clock, ~16 runs, all in public repos and therefore free.
Three Scheduled-release dispatches are needed, not two: the pass that hands out cannot
also collect, and the DUE date and the CUTOFF drive different passes (refresh, then
freeze). Each schedule edit the run makes drives a tick of its own as well (the cohort's
`dispatch-scheduled-release.yml` fires on the push), which the harness waits out before
dispatching. One-off setup:
`python3 -c "from dsl_course import grades; grades.ensure_gradebooks('<demo cohort>')"`
once, so the test student's `grades-<handle>` repo already exists - a repo the run created
is drift the teardown cannot take back. There is no button and no subcommand for it any
more: `distribute` provisions the gradebooks it needs, and that is the only caller.

## Working conventions

Feature branches (`feature/*`, `fix/*`, `refactor/*`, `docs/*`), squash-merged via PR. Subjects
`type(scope): imperative`. Never add AI attribution anywhere - the `commit-msg` hook rejects it.
Coding agents work only in worktrees under the scratchpad, never the live checkout.

## Actions minutes

Every org is on GitHub Free. A **public** `.github` repo gets unlimited minutes, which is why the
control panel is public; private repos draw on the free allowance. Usage per org:
`gh api organizations/{org}/settings/billing/usage`.
