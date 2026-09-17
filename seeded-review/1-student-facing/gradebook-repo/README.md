=== before the first Distribute grades (now written at gradebook PROVISIONING -
grades.provision_one, at onboarding - not at first distribute; grades._STARTER_README) ===

# Your gradebook

This private repository is viewable only by you. Grades and feedback for each piece of assessment appear in `grades.yml` as the course progresses.

Feedback for assignments handed in outside GitHub, in a shared repo, or in a repo that is public or yours to publish appears here and nowhere else.

## What each field means

| Field | Meaning |
| --- | --- |
| `final_grade` | Your mark for that assignment. This is the authoritative one. |
| `score` | Individual assignments only: the marks behind that total. |
| `feedback` | Your marker's feedback on your own work. |
| `submitted`, `days_late`, `penalty` | When your work was recorded, and what any late days cost. |
| `team` | Group assignments only: the team you submitted with. |
| `team_feedback` | Group assignments only: feedback shared with the whole team. |

## Keeping your work

Your assignment repos stay readable after the course ends, and unless the assignment says otherwise they are private to you and the teaching team. To show one publicly, publish a copy under your own account; the original is untouched.

```
git clone https://github.com/<cohort-org>/<slug>-<your-handle>
cd <slug>-<your-handle>
git remote set-url origin https://github.com/<you>/<new-public-repo>
git push -u origin main
```

=== after Distribute grades (rewritten wholesale on every run that touches this student,
grades.render_readme) - anna-adams, one github-private assignment and one external one ===

This gradebook is private to you. It is regenerated each time grades are distributed; do not edit it.

Feedback for assignments handed in outside GitHub, in a shared repo, or in a repo that is public or yours to publish appears here and nowhere else.

| Assignment | Final grade | Submitted | Late | Team |
|---|---|---|---|---|
| Linear regression | pass | external |  |  |
| Gradient descent | 88 | 27 Oct 22:14 | on time |  |

## Linear regression
**Final grade:** pass

Graded off-platform; recorded here for the record.

## Gradient descent
**Final grade:** 88

Clear, correct solution.
Watch the vectorised edge cases.

## Keeping your work

Your assignment repos stay readable after the course ends, and unless the assignment says otherwise they are private to you and the teaching team. To show one publicly, publish a copy under your own account; the original is untouched.

```
git clone https://github.com/<cohort-org>/<slug>-<your-handle>
cd <slug>-<your-handle>
git remote set-url origin https://github.com/<you>/<new-public-repo>
git push -u origin main
```
