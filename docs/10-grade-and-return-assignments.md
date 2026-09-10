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

### When a sheet has something nobody can act on

The quarter-hourly tick reads every sheet. Anything it cannot use - a save that does not
parse, a key written twice, a mark or an adjustment that is not a number, a question
`grading_config.yml` does not declare, a handle in two submission units - opens **one**
issue in `classroom-config` (*grading sheets have entries the grader cannot read*) listing
each by file and line, and emails whoever committed that line. Nothing is refreshed or sent
for that unit until it is settled. The issue rewrites itself on every tick and closes when
the last one goes.

Neither the issue nor the email ever names the student or the team: a unit key is a person,
and both are public. They cite the line.

The assignment's own `grading_config.yml` is checked the same way, in its own issue
(*assignment grading_config.yml has values that will not grade as written*) - a value that
will not grade as it reads is reported against the moment it is used, so it reaches you
before the marking does, not after. For the whole set of these, see
[Why did I get this email?](reference/actions-reference.md#why-did-i-get-this-email).

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

`days_late` and the penalty are derived from whatever `info.submitted` holds; the note is
for you.

Between the due date and the cutoff the sheet refreshes off committer dates alone, so
`submitted` can move at the freeze - the last derivation there will ever be.

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
executed notebook is archived beside the result JSON as `autograde/<slug>/<key>.ipynb`.

It is **on by default for `format: ipynb`** and off for everything else; `completion_check:
true` / `false` in `grading_config.yml` overrides either way. It is independent of
`autograde`, and that is the point - most notebook assignments are marked by hand.

Four things to write the assignment for:

- **It runs with network access blocked as far as the runner allows** - the proxy
  variables point at a dead port, which every well-behaved client honours, but it is not a
  jail. A notebook that downloads its data at run time reports `errors:N`. Commit the data,
  or cache it in the repo.
- **It runs as a different user, with a time limit, and nothing it starts survives it.**
  Your notebook is executed as an unprivileged sandbox account with no access to the
  toolkit's credentials, under the same wall clock and memory caps as the hidden tests, and
  everything it left running is killed before the next submission is looked at. If the
  grading runner cannot provide that account, nothing is executed at all and the assignment
  is recorded as not machine-marked - a runner fault to report, not a result.
- **It checks one notebook: the first one you did not hand out.** Notebooks are taken
  shallowest first, and any still byte-identical to the starter are skipped - so a
  `00-setup.ipynb` you shipped alongside is passed over. Only when EVERY notebook in the
  repo is still the untouched starter is the state `not-attempted`.
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

**This happens on its own.** Sixty days after your `semester_end`, the scheduler archives
the whole cohort org. You do not have to remember it, and nobody has to be around for it.

Set your own date, or turn the default off, in `schedule.yml`:

```yaml
archive:
  date: 2027-02-16       # optional - default: semester_end + 60 days
  show_on_site: true     # optional - default: true. A "Cohort archived" row on the site
```

A cohort that declares neither `semester_end` nor `archive.date` is **never** archived
automatically. Its `schedule.yml` notice issue will say so, term after term.

**A fortnight before**, the cohort gets one issue in `classroom-config` and one email to
the teaching team saying what is about to happen. That is the moment to move the date if
you need longer - move it inside the fortnight and a notice for the new date opens and
mails again. Students see it too, in the site's Updates box and on its schedule.

**On the day**, in this order:

1. the cohort's edits to released material are offered back to the course org as a pull
   request (see [Carrying cohort edits back](08-release-materials-to-cohort.md#carrying-cohort-edits-back));
2. the toolkit's own open notices in `classroom-config` are closed;
3. the website is synced one last time, so it ships the archived state;
4. **every repository in the org is archived** - students' work, the released materials,
   `welcome` (so nobody can still Join a term that is over), the website, `.github`;
5. `archive/teardown.md` is written into `classroom-config`, recording what was frozen;
6. `classroom-config` is archived last, which is what tells every nightly sync this cohort
   is finished and to leave it alone.

**Nobody is removed and nothing is deleted.** An archived repository is read-only for
everyone, so students keep read access to their own work, to the materials and to their
grades - indefinitely - and nobody, students or faculty, can change any of it. Org
membership and the project teams are untouched.

To reopen anything - a grade appeal, a late submission - un-archive that repo from its own
Settings page. It comes back exactly as it was, write access included.

**Archive cohort** is the button for closing a cohort out early. `dry_run` is on by default
and prints the counts; the real run **refuses** until the archive date has arrived, and
`force` overrides that. Run it again if it fails part-way - it picks up where it stopped,
and only the last step seals the record.

`classroom-config` is now the cohort's whole record of assessment - roster, teams, schedule,
grading sheets, autograde detail, what was sent to whom, and `cohort-gradebook.csv`. Delete
the repository, and the archived student repos with it, when your institution's retention
period for that record expires.

See also: [Release an assignment](09-release-assignment-to-cohort.md) ·
[Schedule releases](07-schedule-releases.md)
