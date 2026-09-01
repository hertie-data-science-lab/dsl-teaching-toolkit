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
| `docs/08-release-materials-to-cohort.md` | `scaffold._RELEASEIGNORE_STUB` (seeded into every materials repo) |
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
  means **adding a link to the chain**, never editing one. For the descriptions, an org on the
  oldest string must still reach the newest in one pass; for `STUB_MARKS`, a repo seeded with an
  older wording must still be recognised as unwritten, or its placeholder syllabus ships.
- **Repo topics** are machinery markers: `dsl-course-hub`, `dsl-cohort`, `submission`, `gradebook`,
  `assignment-template`. Discovery reads them; renaming one is a discovery outage.
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
| 2 | `central` (which ref an org runs), `repos` (existence, creation, topics, descriptions, the publication denylist), `gh_teams` (an org's settings and its teams) |
| 3 | `gh_contents` (file reads and writes, seeded stubs), `workflows_render` |
| 4 | `discovery`, `roster`/`teams`/`schedule`, `workflows_place` |
| 5 and up | `access` (team permissions and the faculty floor), `schedule_plan` (the session rows a plan declares), `welcome`, `profile_readme`, `scaffold`, `site_repo` (the Jekyll site repo both websites publish into), `site`, then the CLIs |

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

## Crons and gates

Five seeded crons: **Scheduled release** hourly; **Refresh actions** 05:27, **Publish course
website** 05:30, **Sync membership** and **Sync site** 06:00 daily. Each reports its own failures,
because GitHub emails a scheduled-run failure only to whoever last committed the file - the bot.

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

## Working conventions

Feature branches (`feature/*`, `fix/*`, `refactor/*`, `docs/*`), squash-merged via PR. Subjects
`type(scope): imperative`. Never add AI attribution anywhere - the `commit-msg` hook rejects it.
Coding agents work only in worktrees under the scratchpad, never the live checkout.

## Actions minutes

Every org is on GitHub Free. A **public** `.github` repo gets unlimited minutes, which is why the
control panel is public; private repos draw on the free allowance. Usage per org:
`gh api organizations/{org}/settings/billing/usage`.
