# Receipts issue body (opened at handout)

Every submission repo carries ONE issue, titled `Submission receipts`, labelled
`dsl-feedback` (the label and the hidden mark keep the old word on purpose - see below),
opened at handout by `grades.ensure_receipts_issue` with a body from
`grades.receipts_thread_body` (`course.receipts_issue_body` underneath). Which variant is
written is read off the assignment's own `grading_config.yml` - the caller does not
choose. It says WHERE the work goes and WHAT will be posted here, and nothing about a
mark: there is no "your feedback follows as a comment" sentence any more, in either
variant - a student has one address for a grade, their private gradebook, and a repo they
may be told to publish is not a second one.

**Only ONE shape ever gets one: `submit_via: assignment_repo` with `visibility: private`**
(`course.has_receipts_issue(submit_via, visibility) -> bool`, `dsl_course/course.py`):

```
return submit_via == "assignment_repo" and visibility == "private"
```

The other four shapes - `assignment_repo`/`public`, `assignment_repo`/`student_choice`,
`shared_dropbox_repo`, `external` - open **no receipts issue at all**. `assign.provision_one`
composes the body itself, off `gspec.has_receipts_issue`, and hands `ensure_receipts_issue`
an empty string for every unit of those four shapes; the call in `provision_one` is gated
`if receipts_thread_body and not grades.ensure_receipts_issue(...)`, so an empty body opens
nothing at all - `grades.receipts_thread_body` is never even called for them in the handout
path. Every shape's mark and feedback go to the student's private gradebook instead - see
`gradebook-repo/README.md` beside this file, the one place any of it ever reaches the
student, for all five shapes alike.

**Label and mark kept the word `feedback` on purpose.** They are not read by anyone: they
are what the lookup MATCHES against live issues, so changing either makes every thread
opened under the old wording invisible and a second one appears over it. The title is the
only thing renamed - the lookup is label, then mark, then title, which is what lets the
title be renamed at all (`course.RECEIPTS_ISSUE_TITLE = "Submission receipts"`,
`course.RECEIPTS_ISSUE_LABEL = "dsl-feedback"`,
`course.RECEIPTS_ISSUE_MARKS = ("<!-- dsl-course: feedback -->",)`).

### assignment_repo / private - individual (assignment-2, has a late window)

```
<!-- dsl-course: feedback -->
**Due:** Tuesday 27 October 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Tuesday 3 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. This thread is your receipt for that: one at the deadline saying what was recorded, one after any late push, and one at the cutoff when the commit we grade is fixed.
```

### assignment_repo / private - group (assignment-4-project, team-alpha)

The team line and the CONTRIBUTIONS.md ask appear only here - an individual repo's issue
never mentions either.

```
<!-- dsl-course: feedback -->
**Due:** Sunday 15 November 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Sunday 22 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.
**Team:** team-alpha (@anna-adams, @ben-baker, @amara-okonjo, @jonas-bergstrom) - fill in CONTRIBUTIONS.md before the deadline.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. This thread is your receipt for that: one at the deadline saying what was recorded, one after any late push, and one at the cutoff when the commit we grade is fixed.
```

### assignment_repo / public, assignment_repo / student_choice, shared_dropbox_repo, external - NONE, and why

`assign.py`'s module docstring states the four shapes plainly (`dsl_course/assign.py`):

> "For `submit_via: external`... it creates NOTHING: no cohort template, no repo, no
> receipts issue, no solution push."
>
> "For `submit_via: shared_dropbox_repo`... No receipts issue and no model solution: one
> repo the whole cohort reads is not a place to put either."
>
> "For `visibility: public` it creates the same repos world-readable - portfolio work -
> and opens no receipts issue: a hand-in time is a fact about a student and does not go
> where the internet can read it. No mark is lost with it - marks go to the private
> gradebook for every shape alike."
>
> "For `visibility: student_choice` it creates the same PRIVATE repos... No receipts issue
> there either: the repo may be public tomorrow."
