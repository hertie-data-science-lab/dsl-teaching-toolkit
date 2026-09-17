# Submission receipts (posted into the Feedback issue)

One comment per event, additive - a comment edited in place would leave no trace of when
the work actually arrived. `grades.receipt` (`course.receipt_body` underneath) composes the
text; `grades.post_receipt` posts it once per `(commit, event)` pair (the hidden
`<!-- dsl-receipt:<sha>:<event> -->` marker is what makes a re-run idempotent - never shown
to a student). All four below render from assignment-2's spec (individual,
`assignment_repo`/`private`, a 7-day late window at 10%/day).

**Posted only where `spec.has_feedback_issue` is true - i.e. `assignment_repo`/`private`
alone.** `collect._post_receipts` returns immediately `if not spec.has_feedback_issue:` for
every other shape (`assignment_repo`/`public`, `assignment_repo`/`student_choice`,
`shared_dropbox_repo`, `external`): there is no Feedback issue to post into for any of
them, not merely (for `external`) no commit to time. Their Feedback issue only ever
carries nothing at all - the gradebook is the one channel that reaches all five shapes.

### At the due date - submitted on time

```
**Submission recorded** · `a1b2c3d` · committed Tuesday 27 October 2026, 22:14 (Europe/Berlin) · on time
Late work is accepted until Tuesday 3 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started. A further push replaces this.
```

### At the due date - nothing submitted

```
**No submission recorded** at the deadline. Late work is accepted until Tuesday 3 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.
```

### After a late push (`RECEIPT_UPDATED`, refreshed quarter-hourly until the cutoff)

```
**Submission updated** · `f6e5d4c` · committed Thursday 29 October 2026, 10:03 (Europe/Berlin) · 2 days late (-20%)
```

### At the cutoff (`RECEIPT_FROZEN` - pin and sheet both freeze; no percentage quoted here,
it was already given on the late-push receipt)

```
**Frozen for grading** · `f6e5d4c` · 2 days late. No further pushes count.
```
