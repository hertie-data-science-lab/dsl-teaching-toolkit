# Feedback issue body (opened at handout)

Every submission repo carries ONE issue, titled `Feedback`, labelled
`dsl-feedback`, opened at handout by `grades.ensure_feedback_issue` with a
body from `grades.feedback_body` (`course.feedback_issue_body` underneath). Which variant
is written is read off the assignment's own `grading_config.yml` - the caller does not
choose. Receipts and, later, the feedback comment are posted into this same issue - see
`submission-receipts.md` and `feedback-comment.md` beside this file.

### Individual, submitted on GitHub (assignment-2 - has a late window)

```
<!-- dsl-course: feedback -->
**Due:** Tuesday 27 October 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Tuesday 3 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. A submission receipt is posted here at the deadline and after any late push, and your feedback and grade follow as a comment once marking is complete.
```

### Individual, submitted outside GitHub (assignment-1 - `submit_via: external`)

No late policy line, no submission paragraph - there is no commit to grade, so nothing here
talks about pushing.

```
<!-- dsl-course: feedback -->
**Due:** Tuesday 13 October 2026, 23:59 (Europe/Berlin)

This assignment is submitted outside GitHub (see the brief). This repository holds the brief and your feedback.
```

### Group (assignment-4-project, team-alpha)

The team line and the CONTRIBUTIONS.md ask appear only here - an individual repo's issue
never mentions either.

```
<!-- dsl-course: feedback -->
**Due:** Sunday 15 November 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Sunday 22 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.
**Team:** team-alpha (@anna-adams, @ben-baker, @amara-okonjo, @jonas-bergstrom) - fill in CONTRIBUTIONS.md before the deadline.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. A submission receipt is posted here at the deadline and after any late push, and your feedback and grade follow as a comment once marking is complete.
```
