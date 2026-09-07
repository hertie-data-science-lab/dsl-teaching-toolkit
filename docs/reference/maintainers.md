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
whole Actions tab, Refresh included. That is the order the `staging` retirement followed:
2026-09-07, the demo course org was moved to `main` and refreshed green before this change
merged, and the `staging` branch is deleted after the merge.
Tiers, the pre-promotion checklist and the full rollback procedure:
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
  `notify`, `scaffold`, `schedule`, `scheduler`, `seed`, `site`, `status`, `syllabus`,
  `sync_faculty`, `sync_membership`, `sync_roster`, `sync_teams`. A rename strands every org
  until it refreshes.
- **`roster.FIELDS` / `roster.normalise_role` / `teams.FIELDS`** are re-implemented in the
  shipped JavaScript (`templates/welcome/onboard.yml`, `team-formation.yml`), which cites them by
  name. Change a column and change both sides.
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

## Secrets an org carries

Two values are published onto an org by the toolkit itself, both through
`bootstrap_course.set_org_secret` - which scopes the org secret to the infra repos that
exist and then mirrors it as a repo secret onto the private ones, because on GitHub Free a
`selected` org secret is never delivered to a private repo:

| Value | Held centrally as | Reaches an org via |
|---|---|---|
| `DSL_BOT_TOKEN` | a secret on this repo | Bootstrap with `set_secret: true`; `seed refresh` also mirrors it onto each content repo |
| `DSL_MAINTAINER_EMAIL` | a repository **variable** on this repo | Bootstrap with `set_secret: true`, and Bootstrap cohort forwards it to a cohort |

`DSL_MAINTAINER_EMAIL` is where fault mail goes. A variable centrally and a secret on the
org: an address is not a credential (and a masked secret cannot be read back to check it),
but a seeded workflow can only read it from `secrets.`. `mailer.maintainer_address` falls
back to `GRAPH_SENDER` when it is absent, so an org without it mails the shared send mailbox
rather than nobody, and `Check cohort setup` reports which of the two an org is on.

Nothing converges it. "Refresh actions" runs INSIDE the course org and cannot read this
repo's variables, so an org bootstrapped before the variable existed gets it once, by hand:

    gh secret set DSL_MAINTAINER_EMAIL --org <course-org> \
      --visibility selected --repos .github --body '<address>'

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

## Crons and gates

Five seeded crons: **Scheduled release** at :07/:22/:37/:52 every hour; **Refresh actions**
05:27, **Publish course website** 05:58, **Sync membership** 06:13, **Sync site** 06:41 daily.
Each reports its own failures, because GitHub emails a scheduled-run failure only to whoever
last committed the file - the bot.

No cron may sit on minute 0/15/30/45 and no two daily ones may share a slot - GitHub drops the
most contended minutes first (on `0 * * * *` the scheduler was delivered 6 ticks a day, not 24),
and membership must write the teams that Sync site then reads. Both rules are enforced by
`tests/test_renderers.py`; the reasoning sits above the cron literals in `workflows_render`.

**A content fault never reds a cron.** A source the plan cites and the org has not got is
faculty's to fix, so it is delivered by the cohort's digest issue and an email to the people git
names for that line - never by the exit code. A red X on any of the five crons means the run
itself broke, which is why the maintainer is emailed its log tail.

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
(snapshot + autograde), then puts both orgs back. It proves the wiring unit tests cannot -
a click, a cron, a token and a repo - and it runs against the demo org between **merging to
main and Promote to release**, alongside the manual inspection that is the actual gate. Two scheduler passes are needed because `scheduler.run` snapshots
before it hands out.

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
dispatching. One-off setup: `python3 -m dsl_course.grades sync --cohort-org <demo cohort>`
once, so the test student's `grades-<handle>` repo already exists - a repo the run created
is drift the teardown cannot take back. (There is no button for it any more: Sync
gradebooks was retired with the other two grading workflows.)

## Working conventions

Feature branches (`feature/*`, `fix/*`, `refactor/*`, `docs/*`), squash-merged via PR. Subjects
`type(scope): imperative`. Never add AI attribution anywhere - the `commit-msg` hook rejects it.
Coding agents work only in worktrees under the scratchpad, never the live checkout.

## Actions minutes

Every org is on GitHub Free. A **public** `.github` repo gets unlimited minutes, which is why the
control panel is public; private repos draw on the free allowance. Usage per org:
`gh api organizations/{org}/settings/billing/usage`.
