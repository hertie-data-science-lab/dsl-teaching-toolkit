# Submission receipts (posted into the Feedback issue)

One comment per event, additive - a comment edited in place would leave no trace of when
the work actually arrived. `grades.receipt` (`course.receipt_body` underneath) composes the
text; `grades.post_receipt` posts it once per `(commit, event)` pair (the hidden
`<!-- dsl-receipt:<sha>:<event> -->` marker is what makes a re-run idempotent - never shown
to a student). All four below render from assignment-2's spec (individual, on GitHub, a
7-day late window at 10%/day).

**None of this fires for an assignment with `submit_via: external`** (assignment-1): there
is no commit to time, so `_post_receipts` returns immediately and that Feedback issue only
ever carries the handout body and, later, the feedback comment.

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
