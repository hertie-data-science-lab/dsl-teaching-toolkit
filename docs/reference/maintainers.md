# Maintainer guide

For whoever maintains **this repo**. Everything here is a constraint the code depends on and
only its comments record. If you run a course rather than the toolkit, you want
[the runbooks](../README.md) instead.

## A merge to main is not a deploy

Every seeded workflow, in every bootstrapped org, checks this repo out at the ref **that org**
runs - `central_ref:` in its course org's `.github/dsl-course.yml`, defaulting to
`central.CENTRAL_REF` (`release`). Landing on `main` therefore changes nothing anywhere.

Deploying is promoting: `main` -> `staging` (the demo org) -> `release` (everything else), via
the **Promote** workflow, which can only fast-forward a tier along main's history. Engine
changes are then live on the next press in each org; workflow *shapes* (inputs, jobs, crons)
are re-rendered by each org's nightly **Refresh actions**, and Promote runs that refresh itself
out of the promoted checkout so they land at once. Rollback is a `git revert` on `main`,
promoted forward - never a force-push.
Tiers, soak checklist and the full rollback procedure:
[central-admin.md](../../docs-admin-arch/central-admin.md#deploying-the-toolkit).

## Doc filenames are a public API

These paths are linked by **absolute URL** from inside every bootstrapped org, so renaming one
breaks a live link that faculty click:

| Doc | Linked from |
|---|---|
| `docs/07-schedule-releases.md` | `source_digest.py`, `profile_readme.py`, `templates/classroom-config/schedule.yml`, `templates/classroom-config/validate-schedule.yml` |
| `docs/05-manage-teaching-team.md` | `templates/classroom-config/people.yml` |
| `docs/README.md` | `profile_readme.py` |

`grep -rn 'blob/.*/docs/' dsl_course/ templates/` before any rename.

## Frozen public contracts

Things whose *literal spelling* is depended on from outside Python:

- **CLI module names.** Seeded workflows and templates invoke `python3 -m dsl_course.<x>`:
  `assign`, `bootstrap_course`, `collect`, `deploy`, `enrol_codes`, `grades`, `list_orgs`,
  `scaffold`, `schedule`, `scheduler`, `seed`, `site`, `status`, `syllabus`, `sync_faculty`,
  `sync_membership`, `sync_roster`, `sync_teams`. A rename strands every org until it refreshes.
- **`roster.FIELDS` / `roster.normalise_role` / `teams.FIELDS`** are re-implemented in the
  shipped JavaScript (`templates/welcome/onboard.yml`, `team-formation.yml`), which cites them by
  name. Change a column and change both sides.
- **`gh_contents.STUB_MARKS` and `SUPERSEDED_DESCRIPTIONS` / `SUPERSEDED_COHORT_*` / `SUPERSEDED_COURSE_*`**
  are convergence chains matched against *live* state. Rewording a stub or a repo description
  means **adding a link to the chain**, never editing one: an org on the oldest string must still
  reach the newest in one pass.
- **Repo topics** are machinery markers: `dsl-course-hub`, `dsl-cohort`, `submission`, `gradebook`,
  `assignment-template`. Discovery reads them; renaming one is a discovery outage.
- **`.github/cohort-courses-pages.yml`** is the cohort registry every dropdown reads, and
  **`.github/.last-refresh`** is the heartbeat that keeps an org's crons from GitHub's 60-day
  inactivity disable.

## File ownership

Seeded files carry their owner on the first line, and the write site enforces it:

- **SYSTEM-OWNED** - written unconditionally on every bootstrap and refresh, so fixes reach
  running courses. Workflows, generated docs, `*.sample`.
- **INSTRUCTOR-OWNED** - `gh_contents.seed_if_absent` only. Rewriting one destroys live state (roster
  rows, enrol codes, the term's schedule). The code comments call this "USER-owned"; the shipped
  stamp says INSTRUCTOR-OWNED. Same thing.
- **`dsl-stub:`** is the third state: an instructor-owned file we seeded and they have not yet
  touched. `gh_contents.STUB_MARKS` recognises it and `gh_contents.refresh_stubs` re-pushes it, so an
  improvement reaches repos that still carry the placeholder and nobody's writing is overwritten.

The full rule is the ownership note at the top of `bootstrap_course.py`.

A course website is wholly the toolkit's: `scaffold_site` creates `<org>.github.io` EMPTY and
seeds only its Pages build, then every sync writes `templates/site/` (SYSTEM-OWNED) and seeds
`templates/site-seed/` into any path the site lacks (INSTRUCTOR-OWNED). There is no
`course-website-template` repo any more.

## Module layers

`dsl_course` is layered, and the import graph is acyclic (`tests/test_architecture.py`
enforces both that and the absence of function-local imports):

- **L0, no dependencies** - `log` (console prefixes), `ghcli` (`gh`/`git`, timeouts, the
  404 test), `course` (the course vocabulary: config repo, term tag, session-folder rule,
  syllabus filenames, org topics), `readings`, `central`.
- **L1, GitHub primitives** - `gh_teams` (org/team membership), `repos` (repo existence,
  creation, topics, descriptions, the publication denylist), `gh_contents` (file reads and
  writes, seeded stubs), `access` (team permissions and the faculty floor).
- **L2 and up** - `discovery`, `roster`/`teams`/`schedule`, `schedule_plan` (the session
  rows a plan declares), `site_repo` (the Jekyll site repo both websites publish into),
  then the CLIs.

Add a name to the layer that owns the subject, not to whichever module already imports it.

## Adding a workflow

Four places, in order - miss the last and every org keeps two buttons for one job:

1. a renderer in `workflows_render.py`;
2. its path in `seed.seed_github_workflows`'s `files` dict (or `workflows_place.WORKFLOWS`
   for a run-from-repo one);
3. `tests/test_renderers.py`'s `ALL_RENDERED` - a completeness test fails otherwise;
4. when *retiring* a path, add it to that call's `delete=` tuple (or
   `workflows_place.RETIRED_WORKFLOWS`), so orgs seeded before the change drop the old file.

## Crons and gates

Five seeded crons: **Scheduled release** hourly; **Refresh actions** 05:27, **Publish course
website** 05:30, **Sync membership** and **Sync site** 06:00 daily. Each reports its own failures,
because GitHub emails a scheduled-run failure only to whoever last committed the file - the bot.

Every `workflow_dispatch` job sits behind the `check-team` gate (`workflows_render._CHECK_TEAM`),
which asks for write on the repo the button lives in. The scheduler and refresh are **ungated**: a
cron has no actor to check, and both only re-call idempotent work.

`seed refresh` is serialised against itself (`concurrency: seed-refresh`) and **deliberately not
shared** with the click workflows that end in a refresh. Actions concurrency has no queue - a group
holds one pending run, so a third arrival cancels the second, and an operator's click would
silently do nothing.

## Tests

`python3 -m pytest -q` (CI runs the same). `pytest` and `jekyll-contract`, `ci.yml`'s two
jobs, are both required checks on `main`. Conventions:

- `conftest._no_live_gh` refuses any live `gh` from a test, guarding the **binary** rather than
  `ghcli.gh`, so the retry ladder stays testable and `git` against a tmp repo still runs.
- Never mock the subject under test - stub what it reads.
- No `inspect.getsource` assertions: test behaviour, not text.

## Working conventions

Feature branches (`feature/*`, `fix/*`, `refactor/*`, `docs/*`), squash-merged via PR. Subjects
`type(scope): imperative`. Never add AI attribution anywhere - the `commit-msg` hook rejects it.
Coding agents work only in worktrees under the scratchpad, never the live checkout.

## Actions minutes

Every org is on GitHub Free. A **public** `.github` repo gets unlimited minutes, which is why the
control panel is public; private repos draw on the free allowance. Usage per org:
`gh api organizations/{org}/settings/billing/usage`.
