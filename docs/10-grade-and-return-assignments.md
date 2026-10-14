# Grade and return assignments

One file to fill in, one button to send it. Everything else is the toolkit's.

## The shape of it

```
HANDOUT ─────► DUE ─────► late window ─────► CUTOFF ─────► DISTRIBUTE GRADES
(cron)         (cron)     (cron refreshes)   (cron)        (button)
   │             │              │               │              │
sheet created  sheet        sheet refreshed   sheet         gradebook + CSV
(empty, dated) filled       as late work      frozen        + email
                            arrives
```

Marking can start the moment anything is in. The sheet exists from handout; only the
machine facts move during the late window.

## `semester-config/grading_sheets/<slug>.yml`

The one place a grader types. It arrives with every row present and a header saying which
fields the toolkit fills and when. Two shapes - individual and group - and worked examples
of both are in the [worked example semester](../example-course/semester-org/grading_sheets).

```yaml
teams:
  team-alpha:
    info:                       # toolkit-owned, shown for information only
      submitted: 2026-11-15T22:14+01:00
      days_late: 0
      contributions: |
        Anna: model architecture. Ben: training loop.
    feedback_group: |
      Excellent model design; the evaluation section is thin.
    members:
      anna-adams:
        adjustment_individual: +4
        feedback_individual:
        notes_not_shared_with_students:
    score_group:                # yours
      Q1: 14                    # /15
      Q2: 13                    # /15 · report.tex
    feedback_per_question:
      Q1:
      Q2: One baseline is not a comparison.
```

| Field | Owner | Student sees |
| --- | --- | --- |
| `info.submitted`, `info.days_late`, `info.contributions`, `info.autograde`, `info.completion`, `info.submitted_note` | toolkit, refreshed until frozen | `submitted`, `days_late` |
| `score_individual` (per question, or one value) | you | the total, and the breakdown behind it |
| `feedback_group`, `feedback_individual` | you | yes (own + team) |
| `feedback_per_question` (where questions are declared; the team's on a group sheet) | you, optional | yes: a list after the overall feedback, inside the team's quote on a group sheet; a blank cell is not sent |
| `score_group` | you | never shown as-is; each member's gradebook shows only the final grade it derives - the score itself too, but only for a team with no repo of its own (`shared_dropbox_repo`, `external`) |
| `adjustment_individual` | you - the ONLY override of the mark, in both shapes | **no** - only the final grade it produced |
| `late_penalty` (per student or team, beside `info:`; optional) | you: `apply` (the default), `waive`, or the whole penalty as `15%` / `0.15` | with their marks (Distribute grades), the penalty that applied: `-20%`, `waived` or `-15% (set by hand)`; a submission receipt quotes the late rule's own figure only |
| `notes_not_shared_with_students` (each person's; on a group sheet the team's own too, beside `feedback_group`) | you | **never** |
| final grade | derived on output, never stored | yes |

The final mark is `total × (1 − rate × days_late) + adjustment`, floored at 0. A late
unit's `late_penalty` changes the middle term: `waive` (or `0%`) takes nothing off, and `15%` takes
off 15% in place of `rate × days_late`; a unit that is not late loses nothing whatever it
says. The console's Marks tab writes it for you: switch the penalty off, or type an
override. An assignment with no late penalty (`late_penalty_per_day` unset) has nothing to waive or
override: the Marks tab offers neither, any `late_penalty` written is ignored, and the Distribute
preview says so. A penalty changed after marks were returned goes out again as a changed mark. A
non-numeric score (`pass`, `A-`) is passed through verbatim with no arithmetic - unless a
late penalty applies to it, which no arithmetic can do: that mark is **held**. Nothing is
posted, written or emailed for it, the preview and the run both count it (`held`), and it
goes out as soon as you put a number (or write `late_penalty: waive`). The question names, the `# /N` maxima and the whole
header come from `grading_config.yml` and `schedule.yml` and are re-emitted on every write - so
edit them **there**, never in the sheet. The toolkit writes the file only when the data or
that header really moved, so your quoting and spacing survive the quarter-hourly tick; YAML
comments you add do not survive a rewrite when one happens. A sheet written before
`feedback_per_question` existed never gains it: add `feedback_per_question:` under a unit
by hand.

### The console's Marks tab

**Return marks** sits above the grid, with **Student view** under it; the grid itself is for
marking and saving, and its corner icon opens it full screen (Escape closes). The sheet is one row
per student or team. On a team assignment a team's ▸ shows its members, each with their own Adjustment, feedback and private notes; a
team with any of those set opens by itself. On an individual one the Adjustment column sits
behind the **Adjustments** chip. Every column header has a `?`; the **From the submission** heading
folds its columns into one. Drag a column's edge to resize it (double-click puts it back; widths are kept
in this browser), and open any text box's corner ⤢ for a large editor. The filters (All, Unmarked, Late,
Held) count from the sheet as typed, and a held row says why, by the same rules as Return marks.
Enter moves down a column of numbers; in a text box Ctrl+Enter (⌘+Enter) does. **Student view**
shows what one student's marks page will hold. Unsaved marks stay in this browser until saved, and
are offered back (Restore or Discard) on the next visit; a save that meets someone else's keeps
both and says how many rows they changed. Return marks counts who gets marks, who is skipped and
who is held (and how many unsaved rows it leaves out), gives the final grades in one line, and after a
preview names who would be emailed. The Overview's Return marks opens this same panel on the Marks tab.

### When a sheet has something nobody can act on

The quarter-hourly tick reads every sheet. Anything it cannot use - a save that does not
parse, a key written twice, a mark or an adjustment that is not a number, a late unit's
`late_penalty` that is not `apply`, `waive` or a percentage, a question
`grading_config.yml` does not declare, a handle in two submission units (however it is
capitalised), a team's `members:` that is not one entry per member - opens **one** issue in
`semester-config` (*grading sheets have entries the grader cannot read*) listing each by file
and line, and emails whoever committed that line. Nothing is refreshed or sent for that unit
until it is settled; a sheet that does not parse, or whose `members:` cannot be read, stops
Return marks altogether. A held mark the student was already sent is never taken back: that
student's gradebook, email and export row stay as last sent until it is settled. With the
semester grades file missing or unreadable, which marks were sent cannot be told, so a held mark
stops Return marks until it is settled. The issue
rewrites itself on every tick and closes when the last one goes.

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
answered in `.system/snapshots/<slug>.csv`:

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
no repo is created for it, so there is no commit to time and nothing waits on the cutoff: mark it
and run **Distribute grades** whenever you like.

For `submit_via: shared_dropbox_repo` the timing is per FOLDER of the one drop box: `submitted` is the
last commit at or before the cutoff that touches `<handle>/` (or `<team>/`) **and was made
by one of that unit's own members**, so a classmate's edit is never marked as their work. A
folder nobody in the unit ever pushed to has no submission. Two things differ from the
shapes with a repo each: `suspect` never appears (GitHub's last-push record is the whole
repo's, so it would accuse the entire semester of one student's late push), and
`submitted_note` carries what the search through the folder found:

| `info.submitted_note` | what it means |
|---|---|
| `last change to this folder was by someone outside the team - check` | the pin is the unit's own last commit, but a classmate touched the folder more recently |
| `commit author not linked to a GitHub account - check` | the pinned commit's git address is not one GitHub can match to any account - usually a laptop configured with a personal email. It is counted as the unit's own work, which is why you are told |
| `no commit by this unit in the 100 newest commits touching its folder - check` | we stopped looking, rather than found nothing: `submitted` is blank because the unit's own commits are buried under a hundred of somebody else's, not because they pushed nothing |

## Marking, step by step

1. **Handout.** The sheet appears with one row per student or team, every student has a
   private `grades-<handle>` gradebook, and a `submit_via: assignment_repo`, `visibility: private`
   submission repo gets a **Submission receipts** issue. An `external` assignment has no repos, a
   `shared_dropbox_repo` one has a repo the whole semester reads, and a `public` or `student_choice` repo
   is not private - none of the three gets an issue, so none of the three gets receipts.
   Nothing else turns on it: marks and feedback go to the gradebook for every shape alike.
2. **The due date.** `info:` fills, and each student gets a submission receipt on that
   issue. Late pushes refresh both, quarter-hourly, until the cutoff.
3. **Collect submissions** (button) does that refresh now instead of waiting. It never
   freezes anything. Its `assignment` box names the schedule key only when two entries
   share the template.
4. **Type the marks.** Anything you write is kept forever - including keys you invent, and
   rows for students who have left. Delete a key and it stays deleted.
5. **The late cutoff** (the due date plus the late window) freezes the
   pin and the sheet. Its header then reads `FROZEN`.
   A late rule changed after the due date (the console's Overview says so before you save)
   moves the cutoff and recomputes every late penalty; return the changed marks again.
6. **Distribute grades** (button), `preview` first. Leave `assignment` empty to return every sheet, or name one to return only its marks; each gradebook still shows everything already returned, rendered from those sheets as they stand now, so a mark changed on an already-returned assignment goes out with it. A scoped run refuses when it cannot tell what was returned (the semester grades file missing or unreadable); an unscoped run rewrites it. The preview writes no grades and sends
   no mail. It prints the counts, and posts who gets what - each changed grade, who is
   emailed, marks **held** for a hand decision, unmarked questions - as a *Distribute
   grades preview* issue in `semester-config`. Each preview rewrites that issue; the real
   run closes it.

## What Distribute grades sends

Three channels, and a submission repo is not one of them. Nothing a grader typed is ever
posted into a repo: a student has one address for a mark, and a repo whose `visibility:`
could change is not it.

- **The private gradebook** `grades-<handle>`: `grades.yml` and a rendered `README.md`, in
  one commit. The student sees their final grade, never the sum behind it. Each member of
  a team reads the team's shared feedback here, in their own repo, beside their own grade.
- **`.system/semester-gradebook.csv`** in `semester-config` - the semester grades file, one row per
  enrolled student including the ungraded. Private, never logged.
- **An email** with a link and no marks in it.

Nothing is said twice: every send is recorded in `.system/gradebook/distributed.csv`, so a re-run
after one correction reaches one student. The emails are recorded just before they go, so a
run that fails afterwards never mails anyone twice. If some emails fail, they are retried
only when you press Return marks again; if every one fails, `marks_return_datetime` tries
again on its own. It also keeps trying while any mark is held, and sends only what has not
gone out. A `grading_config.yml` refused as `NOT_MIGRATED` stops its sheet refresh and
Return marks until the migration runs. Untick `notify` to skip the email.

Two options, both off by default. `include_feedback` puts the markers' feedback text into
the email: the overall feedback, then each question's. `receipt_note` posts one line,
"Marks returned: see your marks repo.", on each returned student's or team's Submission
receipts issue - once per assignment, however often you run it. The note carries no mark.

The gradebook and the email are decided separately, on purpose. The **commit** is made
whenever anything in the repo would change, so an improvement to the page's own wording
reaches every student. The **email** is sent only when a MARK moves - a grade, a score or
feedback - so a new submission time, a new maximum or a respelt date rewrites the page and
mails nobody. A student with no mark or feedback anywhere yet is not emailed at all; the run
that brings their first one does.

Every real return of a semester waits its turn - the automatic one, the button and the
Console's **Return marks** (which starts this workflow) - so two never mail a class at once.
A preview waits for nothing.

## Autograding (optional)

Off unless you ask for it. `autograde: true` in the template's `grading_config.yml` runs the
hidden tests from its `solution` branch at the late cutoff, against the frozen pin, in a sandbox with the token stripped. The
count lands in `info.autograde` (`7/9`) for your information only - it is never a mark by
itself and a student never sees it. Per-test detail goes to `semester-config/.system/autograde/`.
To regrade, delete `.system/autograde/<slug>/`.

**Writing the tests.** One test function per behaviour, named for it: the per-test detail lists
each test by name with pass or fail. Import the student's module by its starter
name (`from starter import fit`) - the runner puts the submission on `sys.path`. Plain
`assert`s, `pytest.approx` for numbers, `pytest.raises` for errors; count on the standard
library and pytest, nothing more.

**Worked example:** the demo course's [tests folder](https://github.com/hertie-dsl-demo-course-e1234/assignment-1-f2026/tree/solution/tests) - ten tests and a
[README](https://github.com/hertie-dsl-demo-course-e1234/assignment-1-f2026/blob/solution/tests/README.md) on adapting them to your assignment.

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
| `errors:3` | three cells raised - read the executed copy in `.system/autograde/<slug>/` to see which |
| `not-attempted` | the notebook is byte-for-byte the starter you handed out, or nothing was pushed |
| `no-notebook` | the submission holds no `.ipynb` |
| `timed-out` | the notebook never finished inside the time limit |
| `did-not-run` | our runner could not execute it at all - tell the maintainer |

Like `info.autograde` it is **information, never a mark**, and a student never sees it. The
executed notebook is archived beside the result JSON as `.system/autograde/<slug>/<key>.ipynb`.

It is **on by default when `formats:` starts with `ipynb`** and off for everything else; `completion_check:
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
`semester-config/.system/autograde/<slug>/<key>.pdf`. Setup cells, imports and machine-marked
work are left out, so you read the answers rather than the repo. A question marked from
another file (`file:` in `questions:`) gets that file archived beside it as
`<key>.<file>`, read as its compiled `.pdf` when one is committed.

It is **not** behind `autograde:`, deliberately: the fences delimit what a *person* marks,
so an all-manual assignment is the one that wants this most.

The runner may not be able to render a PDF - nbconvert's PDF path needs a LaTeX
installation that a bare Actions runner has not got - so the export falls back to
`.html`, and then to the filtered source (`.ipynb`, or `.Rmd`, which nothing on the runner
can knit). The run log says which, in counts. None of that ever reds the cutoff pass: a
submission with no fences in it, or one repo that could not be read, is counted and the
freeze carries on.

<a id="closing-the-cohort-out"></a>

## Archiving the semester

**Ask for it once and it happens on its own.** Write an `archive:` block in `schedule.yml`
and the scheduler archives the whole semester org on its date. You do not have to remember
it, and nobody has to be around for it.

```yaml
archive:
  event_datetime: 2027-02-16   # optional - default: semester_end + 60 days
  show_on_site: true     # optional - default: true. A "Semester archived" row on the site
```

The block is the switch: `archive:` on its own is enough, and means sixty days after your
`semester_end`. **Without the block, nothing is ever archived** - the semester stays live and
writable, and its digest issue says so, semester after semester.

**A fortnight before**, the semester gets one issue in `semester-config` and one email to
the instructors saying what is about to happen. That is the moment to move the date if
you need longer - move it inside the fortnight and a notice for the new date opens and
mails again. Students see it too, in the site's Updates box and on its schedule.

**On the day**, in this order:

1. the semester's edits to released material are offered back to the course org as a pull
   request (see [Carrying semester edits back](08-release-materials-to-cohort.md#carrying-semester-edits-back));
2. the toolkit's own open notices in `semester-config` are closed;
3. the website is synced one last time, so it ships the archived state;
4. **every repository in the org is archived** - students' work, the released materials,
   `join` (so nobody can still Join a semester that is over), the website, `.github`;
5. `.system/archive.md` (the archive record) is written into `semester-config`, recording what was archived;
6. `semester-config` is archived last, which is what tells every nightly sync this semester
   is finished and to leave it alone.

**Nobody is removed and nothing is deleted.** An archived repository is read-only for
everyone, so students keep read access to their own work, to the materials and to their
grades - indefinitely - and nobody, students or faculty, can change any of it. They can
still fork or clone any of it into their own accounts afterwards. Org membership and the
project teams are untouched.

To reopen anything - a grade appeal, a late submission - un-archive that repo from its own
Settings page. It comes back exactly as it was, write access included.

**Archive semester** is the button for archiving a semester early, or at all. `preview` is on
by default and prints the counts; the real run **refuses** until the archive date has
arrived, and `force` overrides that - which is how a semester with no `archive:` block, and
so no date, is archived. Run it again if it fails part-way - it picks up where it stopped,
and only the last step seals the record.

`semester-config` is now the semester's whole record of assessment - roster, teams, schedule,
grading sheets, autograde detail, what was sent to whom, and `.system/semester-gradebook.csv`. Delete
the repository, and the archived student repos with it, when your institution's retention
period for that record expires.

See also: [Release an assignment](09-release-assignment-to-cohort.md) ·
[Schedule releases](07-schedule-releases.md)
