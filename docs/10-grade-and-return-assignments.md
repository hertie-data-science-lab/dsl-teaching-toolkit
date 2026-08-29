# Grade and return assignments

> **This workflow is a prototype - and entirely optional.** It works best where parts of the
> grading are automatable (hidden tests against code submissions). If it doesn't suit how you
> plan to grade your course, skip it and grade as you always have. Feedback is very welcome,
> and we're happy to customise it to your course's needs - just get in touch with the DSL team.

Autograde (optional) → add your marks → preview → distribute. Marks land in each student's
private `grades-<handle>` repo, never in their assignment repo.

> **Nothing reaches students until you Distribute.** The pipeline has three gates, and only
> the last is student-visible:
> 1. **Autograde** writes machine scores into the private `classroom-config` only
>    (`grades/<slug>.csv` + per-test detail in `autograde/<slug>/`).
> 2. **Review**: you see exactly what the class scored before anything goes out -
>    `grades/<slug>.csv` per assignment, and after **Render grades** the read-only
>    `cohort-gradebook.csv` (one row per student, the whole-class glance view). Add
>    `manual_score`/`individual_adjustment`/`final_grade`/`individual_comments` at leisure; machine cells are write-once, so
>    your corrections stand.
> 3. **Distribute grades** is the only step that pushes anything to students (with its own
>    `dry_run`, and email notify optional).

## Prerequisites

- An assignment [released](09-release-assignment-to-cohort.md) to the cohort.
- *(autograding only)* hidden tests + `grading.yml` on the template's `solution` branch. Without
  them (or with `autograde: false`), skip step 1 and grade entirely by hand.

## 1. Grade assignment (for autograde only)

**This runs itself.** At each assignment's grading deadline the hourly cron autogrades it
**once**, with no manual run required. Run the workflow only for a deliberate re-grade.

Course `.github` → **Actions** → **Grade assignment**: `cohort_org`, `course_source_repo`, plus `group`
(a force-override - an assignment declared `type: group` grades per team anyway)
and `dry_run` (both default **off**). It runs the hidden tests and writes into
`classroom-config`:

- `grades/<slug>.csv` → the `autograde_score` column, on group assignments as well as individual ones (every member of a team carries the team's count, plus `team`). **Write-once:**
  a cell that already holds a value is never overwritten - re-running fills empty cells only,
  so your hand-edits stand. For fresh machine scores, blank those cells first.
- `autograde/<slug>/<handle-or-team>.json` → the raw per-test result, for appeals.
- `autograde/<slug>/_graded.json` (a completed run) or `_skipped.json` (a decision not to
  grade) → the **fire-once marker**. While either record exists the cron will not grade this
  assignment again; the folder alone is not the marker, so an aborted run still re-grades.
  Delete the folder to let the next hourly tick re-grade.

There is **no deadline input**: the deadline is the cohort schedule's
`assignments.<slug>.grading_datetime` (default: the `due_datetime`), and the graded commit is the one frozen into
`classroom-config/snapshots/<slug>.csv` (see
[Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines)). A blank sha
there means nothing was pushed by the deadline, and that scores zero.

Nothing is written to any student repo. Auditors are never graded.

The run log is public, so it names each submission by a per-run `#tag`, not by handle. The tag
is recorded beside the repo in `classroom-config/autograde/<slug>/*.json`; scores are in
`classroom-config/grades/<slug>.csv`.

### What autograde guarantees

- **The graded commit is the frozen one.** Grading pins to the snapshot sha, never to a
  client-supplied commit date. A late push, a backdated commit, or a force-push that rewrites
  history after the deadline cannot move the pin: if the frozen commit can't be recovered, the
  target scores zero rather than grading the rewritten history.
- **Student-committed files can't rig the score.** The hidden tests run in a sandbox outside
  the checkout; a committed `report.xml`, `conftest.py`, `pytest.ini`, `sitecustomize.py`, or a
  module shadowing a standard-library name cannot change which tests run or the score they
  produce. The bot credential is removed from the checkout before any submission code runs.
- **A failed run is safe to retry.** The fire-once marker is written only after every score is
  recorded and every repo was read. If a run is interrupted, or any submission repo can't be
  read, no marker is written and the next tick re-grades - already-recorded scores are left
  untouched (write-once). A snapshot is likewise never frozen while **every** target repo is
  still absent (404 - not provisioned yet), so a late hand-out isn't locked in as "nobody
  submitted". A repo that exists but is empty is a real non-submission and *is* frozen, as a
  zero - that closes the backdating window.

## 2. Add your marks (on top of / instead of autograde)

Live example: [`example-course/cohort-org/grades/assignment-1.csv`](../example-course/cohort-org/grades/assignment-1.csv).

Edit `classroom-config/grades/<slug>.csv` (directly editing via web UI is fine; otherwise edit a local copy of the repo, commit & push)

> **Where does the CSV come from?** Step 1 creates it. If you're not autograding, create it
> yourself: `classroom-config/grades/<slug>.csv` (the folder is seeded empty), with the header
> row below and one row per student handle. Any column you leave out is treated as blank.

| Column | You fill? | The student sees it? | What it's for |
|--------|-----------|----------------------|---------------|
| `github_handle` | no - roster | - | which student the row is |
| `team` | no - autograder | yes (group only) | their team, on group assignments |
| `autograde_score` | no - autograder | **no** | how many hidden tests passed (a count, not a mark). On a group assignment every member carries the team's |
| `manual_score` | yes | **no** | your hand-marked part - a working column |
| `team_score` | yes (group) | yes | the shared team mark - yours, never machine-written |
| `individual_adjustment` | yes (group) | only their own | that member's individual adjustment |
| `final_grade` | **yes** | **yes** | **the mark. Nothing computes it for you** |
| `individual_comments` | yes | yes | feedback for that student |
| `team_comments` | yes (group) | yes | feedback shared with the whole team |

- **For group projects**:
  - `team_score` (shared), each member's private `individual_adjustment`, shared
  `team_comments`, plus each member's own `final_grade`.
  - No one sees another member's adjustment.
- **For no-autograde**: A hand-marked assignment just needs `final_grade` + `individual_comments`.
- `autograde_score` and `manual_score` are faculty-internal and never shown to the student.
- `final_grade` is what the student sees, and you own it - nothing sums `autograde_score` + `manual_score` for you.
- Values stay as you type them - a letter, a percentage, `+4` - nothing is coerced or rounded.

## 3. Sync gradebooks

Course `.github` → **Actions** → **Sync gradebooks**: pick `cohort_org`, plus `dry_run`
(defaults **off**).

- Gives every onboarded, enrolled student a **private** `grades-<handle>` repo, with that
  student added as a read-only collaborator. Auditors get none.
- Idempotent - re-run after late enrolments.

## 4. Render grades (preview)

Course `.github` → **Actions** → **Render grades (preview)**: pick `cohort_org`. No other
inputs - it never sends anything.

- Opens **one** PR in `classroom-config` (branch `grades-update`, "Grades: review before
  distribution") holding a `gradebook/<handle>.yml` per student - what that student will
  receive in step 5.
- **That diff is the preview.** Only `final_grade`, `individual_comments` and, for group work, `team`,
  `team_score`, that member's own `individual_adjustment` and `team_comments` are copied into a student's
  file. `autograde_score` and `manual_score` never are.
- It also regenerates `cohort-gradebook.csv` at the repo root - a wide all-students view for
  you, which stays in `classroom-config` and is never distributed.
- Review, then **merge**. Nothing reaches a student until you do.

> **Everything here is private.** `classroom-config` and every `grades-<handle>` repo are
> private; a student is a read-only collaborator on their own gradebook repo and has no access
> to `classroom-config`, so they can't see the source CSVs, the preview PR, or anyone else's
> marks.

## 5. Distribute grades

Course `.github` → **Actions** → **Distribute grades**: pick `cohort_org`, plus `dry_run` and
`silent`. Run it **after merging step 4's PR** - it distributes what was merged.

Copies each merged gradebook to `grades-<handle>/grades.yml` and emails the student a "your grades have been updated" link (no marks in the email). 

> *NB: the automated email functionality is configured centrally by the DSL team; if/when it isn't live, the grades still reach each student's repo, but no email notification will be dispatched.

**`dry_run` defaults to `true`** - it pushes nothing and sends nothing until you untick it.
A dry run lists the **masked** recipient and the subject of each message, never the body:
this log is public. `silent` pushes the grades without emailing.

## Next

- Repeat 2-5 per assignment as deadlines pass. Step 1 has already run itself -
  [Schedule releases](07-schedule-releases.md).

  > ℹ️ **Autograding fires once**, at each assignment's grading deadline, and never again -
  > the `_graded.json` / `_skipped.json` record inside `autograde/<slug>/` is the marker. To
  > re-grade: delete that folder (the next
  > hourly tick regrades) or press **Grade assignment**. Either way, clear the `autograde_score`
  > cells you want recomputed first - they are write-once and are otherwise left
  > exactly as you left them.

---
**Demo:** per-student `grades-<handle>` repos in [`hertie-dsl-demo-f2026`](https://github.com/hertie-dsl-demo-f2026).
