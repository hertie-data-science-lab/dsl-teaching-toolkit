# Feedback comment (posted by Distribute grades)

The last thing a submission repo's Feedback issue receives: one comment, from
`grades.individual_issue_body` (an individual repo) or `grades.team_issue_body` (a team
repo, `TeamResult` - carries no member field at all, so no member's adjustment, feedback or
final grade can leak into a comment the whole team reads). Every send carries a content
hash so a re-run after one correction updates nothing that already went out unchanged.

**Same gate as the receipts beside this file**: posted only for `assignment_repo`/`private`.
For `assignment_repo`/`public`, `assignment_repo`/`student_choice`, `shared_dropbox_repo`
and `external`, this comment is never posted anywhere - the mark and the words reach the student only through
`gradebook-repo/README.md`'s per-assignment section (`grades.render_readme`).

### Individual - anna-adams, assignment-2 (plain grade)

```
### Feedback · Gradient descent
**Grade:** 88 · submitted 27 Oct 22:14

Clear, correct solution.
Watch the vectorised edge cases.
```

### Individual - ben-baker, assignment-2 (an `adjustment_individual` moved the grade: `80 -6 = 74`)

Still plain - this `view` carries no `penalty` key, so the comment states the grade with no
arithmetic shown, whatever produced it.

```
### Feedback · Gradient descent
**Grade:** 74 · submitted 27 Oct 22:14

Right idea; the proof in Q3 is incomplete.
```

### Team - team-alpha, assignment-4-project (on time)

Per-member `adjustment_individual` is never shown here - see each member's own gradebook in
`gradebook-repo/`.

```
### Feedback · Group project
**Team score:** 43 / 50 (Q1 14, Q2 13, Q3 10, Q4 6) · submitted on time

Excellent model design and a clear training story.
The evaluation section is thin - one baseline is not a comparison.

Your own final grade and personal feedback are in your private gradebook: `grades-<your handle>`.
```

### Team - team-beta, assignment-4-project (2 days late, one question (Q4) left unmarked)

```
### Feedback · Group project
**Team score:** 31 / 50 (Q1 12, Q2 11, Q3 8) · 2 days late · penalty -20%

Good pipeline and docs; Q4 is missing. Two days late.

Your own final grade and personal feedback are in your private gradebook: `grades-<your handle>`.
```
