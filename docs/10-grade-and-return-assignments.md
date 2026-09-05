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
| `info.submitted`, `info.days_late`, `info.contributions`, `info.autograde` | toolkit, refreshed until frozen | `submitted`, `days_late` |
| `score_individual` (per question, or one value) | you | the total, and the breakdown behind it |
| `feedback_group`, `feedback_individual` | you | yes (own + team) |
| `score_group` | you | the team's, in the TEAM repo's comment - never in a member's gradebook |
| `adjustment_individual` | you - the ONLY override, in both shapes | **no** - only the final grade it produced |
| `notes_not_shared_with_students` | you | **never** |
| final grade | derived on output, never stored | yes |

The final mark is `total × (1 − rate × days_late) + adjustment`, floored at 0. A
non-numeric score (`pass`, `A-`) is passed through verbatim with no arithmetic; the dry run
counts those as "needs a hand decision". The question names, the `# /N` maxima and the whole
header come from `grading_config.yml` and `schedule.yml` and are re-emitted on every write - so
edit them **there**, never in the sheet.

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
   nothing, and prints the counts.

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

`autograde: true` in the template's `grading_config.yml` runs the hidden tests from its `solution`
branch at the cutoff, against the frozen pin, in a sandbox with the token stripped. The
count lands in `info.autograde` (`7/9`) for your information only - it is never a mark by
itself and a student never sees it. Per-test detail goes to `classroom-config/autograde/`.
To regrade, delete `autograde/<slug>/`.

## Still on the grade CSVs?

A cohort that began marking in `grades/<slug>.csv` keeps distributing from it: those marks
reach gradebooks, the registrar export and the email, but not the Feedback issues, and the
gradebook's Submitted column stays blank because a CSV records no timing. New assignments
get a sheet.

See also: [Release an assignment](09-release-assignment-to-cohort.md) ·
[Schedule releases](07-schedule-releases.md)
