# Feedback issue body (opened at handout)

Every submission repo carries ONE issue, titled `Feedback`, labelled `dsl-feedback`,
opened at handout by `grades.ensure_feedback_issue` with a body from `grades.feedback_body`
(`course.feedback_issue_body` underneath). Which variant is written is read off the
assignment's own `grading_config.yml` - the caller does not choose.

**Only ONE shape ever gets one: `submit_via: github` with `visibility: private`**
(`course.has_feedback_issue(submit_via, visibility) -> bool`, `dsl_course/course.py`):

```
return submit_via == "github" and visibility == "private"
```

The other four shapes - `github`/`public`, `github`/`student_choice`, `shared`, `external` -
open **no Feedback issue at all**. `grades.ensure_feedback_issue` is passed
`create=spec.has_feedback_issue`, so for those four it only ever finds an existing thread
and never opens one; `grades.feedback_body` is never even called for them in the handout
path. Their feedback goes to the student's private gradebook instead - see
`gradebook-repo/README.md` beside this file, whose starter page and post-Distribute render
both carry:

> "Feedback for assignments handed in outside GitHub, in a shared repo, or in a repo that
> is public or yours to publish appears here and nowhere else." (`grades._CHANNEL_NOTE`)

### github / private - individual (assignment-2, has a late window)

```
<!-- dsl-course: feedback -->
**Due:** Tuesday 27 October 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Tuesday 3 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. A submission receipt is posted here at the deadline and after any late push, and your feedback and grade follow as a comment once marking is complete.
```

### github / private - group (assignment-4-project, team-alpha)

The team line and the CONTRIBUTIONS.md ask appear only here - an individual repo's issue
never mentions either.

```
<!-- dsl-course: feedback -->
**Due:** Sunday 15 November 2026, 23:59 (Europe/Berlin)
**Late work:** accepted until Sunday 22 November 2026, 23:59 (Europe/Berlin), at 10% of your grade per day started.
**Team:** team-alpha (@anna-adams, @ben-baker, @amara-okonjo, @jonas-bergstrom) - fill in CONTRIBUTIONS.md before the deadline.

Push your work to this repository as normal; the last commit to `main` before the deadline is what we grade. A submission receipt is posted here at the deadline and after any late push, and your feedback and grade follow as a comment once marking is complete.
```

### github / public, github / student_choice, shared, external - NONE, and why

`assign.py`'s module docstring states the four shapes plainly (`dsl_course/assign.py`):

> "For `submit_via: external`... it creates NOTHING: no cohort template, no repo, no
> Feedback issue, no solution push."
>
> "For `submit_via: shared`... No Feedback issue and no model solution: one repo the whole
> cohort reads is not a place to put either."
>
> "For `visibility: public`... opens no Feedback issue: nothing about a student's marking
> may be written where the internet can read it, so their feedback goes to their private
> gradebook alone."
>
> "For `visibility: student_choice`... No Feedback issue there either: the repo may be
> public tomorrow."
