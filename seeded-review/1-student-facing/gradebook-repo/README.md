=== before the first Distribute grades (now written at gradebook PROVISIONING -
grades.provision_one, at onboarding - not at first distribute; grades._STARTER_README) ===

# Your gradebook

This private repository is viewable only by you. Grades and feedback for each piece of assessment appear in `grades.yml` as the course progresses.

## What each field means

| Field | Meaning |
| --- | --- |
| `final_grade` | Your mark for that assignment. This is the authoritative one. |
| `score` | Individual assignments only: the marks behind that total. |
| `feedback` | Your marker's feedback on your own work. |
| `submitted`, `days_late`, `penalty` | When your work was recorded, and what any late days cost. |
| `team` | Group assignments only: the team you submitted with. |
| `team_feedback` | Group assignments only: feedback shared with the whole team. |

=== after Distribute grades (rewritten wholesale on every run that touches this student,
grades.render_readme) - anna-adams, one assignment_repo-private assignment (2 days late,
so the score line renders: grades._readme_grade_line) and one external one; row and
section titles now `<identifier> · <name>` via `grades._readme_label` (course.identifier /
course.row_name) ===

This gradebook is private to you. It is regenerated each time grades are distributed; do not edit it.

| Assignment | Final grade | Submitted | Late | Team |
|---|---|---|---|---|
| Assignment 1 · Linear regression | pass | external |  |  |
| Assignment 2 · Gradient descent | 16 / 50 | 29 Oct 22:14 | 2 days late |  |

## Assignment 1 · Linear regression
**Final grade:** pass

Graded off-platform; recorded here for the record.

## Assignment 2 · Gradient descent
**Score:** 20 / 50 · 2 days late · penalty -20% · **Final grade:** 16 / 50

Right idea; the proof in Q3 is incomplete.
