# Grade and return assignments

One file to fill in, one button to send it. Everything else is the toolkit's.

## The shape of it

```
HANDOUT ─────► DUE ─────► late window ─────► CUTOFF ─────► DISTRIBUTE
(cron)         (cron)     (cron refreshes)   (cron)        (button)
   │             │              │               │              │
sheet created  sheet        sheet refreshed   sheet         feedback comment
(empty, dated) filled       as late work      frozen        + gradebook + email
                            arrives
```

Marking can start the moment anything is in. The sheet exists from handout; only the
machine facts move during the late window.

## `classroom-config/grading_sheets/<slug>.yml`

The one place a grader types. It arrives with every row present and a header saying which
fields the toolkit fills and when. Two shapes - individual and group - and worked examples
of both ship as `grading_sheets/*.yml.sample` in your `classroom-config`.

```yaml
teams:
  team-alpha:
    info:                       # toolkit-owned, shown for information only
      submitted: 2026-11-15T22:14+01:00
      days_late: 0
      contributions: |
        Anna: model architecture. Ben: training loop.
    score_group:                # yours
      Q1: 14                    # /15
      Q2: 13                    # /15
    feedback_group: |
      Excellent model design; the evaluation section is thin.
    members:
      anna-adams:
        adjustment_individual: +4
        feedback_individual:
        notes_not_shared_with_students:
```

| Field | Owner | Student sees |
| --- | --- | --- |
| `info.submitted`, `info.days_late`, `info.contributions`, `info.autograde`, `info.completion`, `info.submitted_note` | toolkit, refreshed until frozen | `submitted`, `days_late` |
| `score_individual` (per question, or one value) | you | the total, and the breakdown behind it |
| `feedback_group`, `feedback_individual` | you | yes (own + team) |
| `score_group` | you | the team's, in the TEAM repo's comment - never in a member's gradebook |
| `adjustment_individual` | you - the ONLY override, in both shapes | **no** - only the final grade it produced |
| `notes_not_shared_with_students` | you | **never** |
| final grade | derived on output, never stored | yes |

The final mark is `total × (1 − rate × days_late) + adjustment`, floored at 0. A
non-numeric score (`pass`, `A-`) is passed through verbatim with no arithmetic - unless a
late penalty applies to it, which no arithmetic can do: that mark is **held**. Nothing is
posted, written or emailed for it, the dry run and the run both count it (`held`), and it
goes out as soon as you put a number (or waive the penalty with `adjustment_individual`). The question names, the `# /N` maxima and the whole
header come from `grading_config.yml` and `schedule.yml` and are re-emitted on every write - so
edit them **there**, never in the sheet. The toolkit writes the file only when the data or
that header really moved, so your quoting and spacing survive the quarter-hourly tick; YAML
comments you add do not survive a rewrite when one happens.

### Where `info.submitted` comes from

A git committer date is written by the student's own client, so a submission dated before
the deadline is a claim, not an observation. At the **cutoff** the freeze therefore asks
GitHub when it saw the push that delivered the pinned commit, and records which rung
answered in `snapshots/<slug>.csv`:

| `submitted_source` | what `info.submitted` is | `info.submitted_note` |
|---|---|---|
| `push` | when **GitHub** recorded the push that delivered the pinned commit | none - there is nothing to flag |
| `commit` | its committer date - no push record matched | `no push record matched this commit - the time shown is its committer date, which the student sets` |
| `suspect` | its committer date, contradicted: GitHub's record of the repo's last push is later than the due moment while the commit claims to predate it | `commit dated before the push that delivered it - check` |

`days_late` and the penalty are derived from whatever `info.submitted` holds, so a `push`
row is timed by the server and the other two are the student's word plus a note. The
arithmetic says what the dates say either way; the note is for you.

Between the due date and the cutoff the sheet refreshes off committer dates alone (asking
GitHub per repo four times an hour would be one call per student per tick), so `submitted`
can move at the freeze - which is the last derivation there will ever be.

An assignment whose `grading_config.yml` says `submit_via: external` has no `info:` block at all:
there is no commit to time.

## Marking, step by step

1. **Handout.** The sheet appears with one row per student or team, and every submission
   repo gets a **Feedback** issue.
2. **The due date.** `info:` fills, and each student gets a submission receipt on that
   issue. Late pushes refresh both, quarter-hourly, until the cutoff.
3. **Collect submissions** (button) does that refresh now instead of waiting. It never
   freezes anything.
4. **Type the marks.** Anything you write is kept forever - including keys you invent, and
   rows for students who have left. Delete a key and it stays deleted.
5. **The cutoff** (`grading_datetime`, else the due date plus the late window) freezes the
   pin and the sheet. Its header then reads `FROZEN`.
6. **Distribute grades** (button), `dry_run` first. The dry run reads everything, writes
   nothing, and prints the counts - including how many marks are **held** for a hand
   decision and how many units still have unmarked questions. Set `assignment` to one slug
   to send just that one; blank sends every sheet in the cohort.

## What Distribute sends

- **A feedback comment** on each submission repo's Feedback issue. A team repo grants the
  whole team `maintain`, so a team comment carries the team score and the team feedback and
  **nothing personal** - no member's adjustment, feedback or final grade can appear there.
- **The private gradebook** `grades-<handle>`: `grades.yml` and a rendered `README.md`, in
  one commit. The student sees their final grade, never the sum behind it.
- **`cohort-gradebook.csv`** in `classroom-config` - the registrar export, one row per
  enrolled student including the ungraded. Private, never logged.
- **An email** with a link and no marks in it.

Nothing is said twice: every comment carries a content hash and every send is recorded in
`gradebook/distributed.csv`, so a re-run after one correction reaches one student. `silent`
skips the email.

## Autograding (optional)

Off unless you ask for it. `autograde: true` in the template's `grading_config.yml` runs the
hidden tests from its `solution` branch at the cutoff, against the frozen pin, in a sandbox with the token stripped. The
count lands in `info.autograde` (`7/9`) for your information only - it is never a mark by
itself and a student never sees it. Per-test detail goes to `classroom-config/autograde/`.
To regrade, delete `autograde/<slug>/`.

### Tests in another language: `tests/run.sh`

The hidden tests are pytest unless you say otherwise. Put a **`run.sh`** at the top of the
tests directory and the sandbox runs *that* instead, with the same time limit, the same
resource caps and the same stripped environment. Two variables are handed to it:

| Variable | Is |
|---|---|
| `DSL_JUNIT_OUT` | an absolute path your script **must** write a JUnit XML to. That file is the result - passed/total out of it become `info.autograde` |
| `DSL_SUBMISSION_DIR` | the absolute path of the student's submission |

The script is run with `sh` (no `chmod` needed), from the runspace - *not* from inside the
submission - so use `$DSL_SUBMISSION_DIR` for anything of the student's. Your **exit code is
ignored**: a suite with failures exits non-zero and is still a perfectly good result.
Writing no XML is the failure, and it is reported by name.

R, with `testthat`:

```sh
#!/bin/sh
# tests/run.sh - on the template's solution branch, beside your test-*.R files.
set -eu
DSL_TESTS_DIR=$(cd "$(dirname "$0")" && pwd)
export DSL_TESTS_DIR
Rscript -e '
  library(testthat)
  for (f in list.files(Sys.getenv("DSL_SUBMISSION_DIR"), "[.][Rr]$", full.names = TRUE))
    source(f)
  test_dir(Sys.getenv("DSL_TESTS_DIR"),
           reporter = JunitReporter$new(file = Sys.getenv("DSL_JUNIT_OUT")))
' || true          # a failing suite is a result, not an error
```

`JunitReporter` writes one `<testsuite>` per test file and every one of them is counted.
Your grading runner has to have R and `testthat` on it - the toolkit installs Python's
autograder dependencies and nothing else.

## The completion check (notebooks)

"Restart the kernel and run all cells before you hand in" is a rule a syllabus can state and
nobody could check - until the cutoff, when the toolkit runs the notebook itself and writes
one word into `info.completion`:

| `info.completion` | Means |
|---|---|
| `ran-clean` | every cell executed and none raised |
| `errors:3` | three cells raised - read the executed copy in `autograde/<slug>/` to see which |
| `not-attempted` | the notebook is byte-for-byte the starter you handed out, or nothing was pushed |
| `no-notebook` | the submission holds no `.ipynb` |
| `timed-out` | the notebook never finished inside the time limit |
| `did-not-run` | our runner could not execute it at all - tell the maintainer |

Like `info.autograde` it is **information, never a mark**, and a student never sees it. The
executed notebook is archived beside the result JSON as `autograde/<slug>/<key>.ipynb`,
which is what you read when the state is `errors:N`.

It is **on by default for `format: ipynb`** and off for everything else; `completion_check:
true` / `false` in `grading_config.yml` overrides either way. It is independent of
`autograde`, and that is the point - most notebook assignments are marked by hand.

Two things it is worth writing the assignment for:

- **It runs offline.** A notebook that downloads its data at run time reports `errors:N`.
  Commit the data, or cache it in the repo.
- **It runs the notebook, not your kernel.** Anything the notebook needs must be importable
  on the grading runner, which has the toolkit's own dependencies and whatever your workflow
  installs.

## The grader's reading copy (optional)

Off unless you ask for it. Mark the hand-marked questions in the starter with Otter's
fences - `<!-- BEGIN QUESTION -->` and `<!-- END QUESTION -->` in a markdown cell (or on
their own line in an Rmd/qmd) - and set `grader_pdf: true` in the template's
`grading_config.yml`. At the cutoff every submission is filtered down to just those
questions and archived beside the autograde detail, as
`classroom-config/autograde/<slug>/<key>.pdf`. Setup cells, imports and machine-marked
work are left out, so you read the answers rather than the repo.

It is **not** behind `autograde:`, deliberately: the fences delimit what a *person* marks,
so an all-manual assignment is the one that wants this most.

The runner may not be able to render a PDF - nbconvert's PDF path needs a LaTeX
installation that a bare Actions runner has not got - so the export falls back to
`.html`, and then to the filtered source (`.ipynb`, or `.Rmd`, which nothing on the runner
can knit). The run log says which, in counts. None of that ever reds the cutoff pass: a
submission with no fences in it, or one repo that could not be read, is counted and the
freeze carries on.

## Closing the cohort out

Once the last grades have gone out, run **Archive cohort** on that cohort. It is the end of
the year's work, and it is what stops a finished cohort quietly keeping every student's
write access to their repos for ever.

It does five things, in this order:

1. revokes each student's direct access to the submission repos and gradebooks named after
   them, and cancels any repo invitation they never accepted;
2. archives those repos - GitHub's read-only freeze;
3. archives `welcome`, so nobody can still Join a term that is over;
4. writes `archive/teardown.md` into `classroom-config`, recording what was frozen;
5. archives `classroom-config` itself, which is also what tells the nightly refresh this
   cohort is finished and to leave it alone.

**Nothing is deleted, ever.** Archiving is reversible: un-archive a repo from its own
Settings page and it is back exactly as it was, and `students.csv` is what re-grants a
student their access if you have to reopen one - a grade appeal, a late submission.

`dry_run` is on by default and prints the counts. The real run **refuses** unless your
`schedule.yml` declares a `semester_end` that has passed; tick `force` to close out a cohort
whose term dates were never filled in. Run it again if it fails part-way - it picks up where
it stopped, and only the last step seals the record.

What it does **not** touch: org membership, and the project teams that grant access to
group repos. Those follow `students.csv` and `teams.csv` through Sync membership, so empty
those files if you want the students out of the org as well.

`classroom-config` is now the cohort's whole record of assessment - roster, teams, schedule,
grading sheets, autograde detail, what was sent to whom, and `cohort-gradebook.csv`. Delete
the repository, and the archived student repos with it, when your institution's retention
period for that record expires.

See also: [Release an assignment](09-release-assignment-to-cohort.md) ·
[Schedule releases](07-schedule-releases.md)
