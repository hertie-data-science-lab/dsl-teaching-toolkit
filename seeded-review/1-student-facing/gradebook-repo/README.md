=== before the first Distribute grades (seeded at gradebook creation, grades._STARTER_README) ===
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
| `team_comments` | Group assignments only: feedback shared with the whole team. |

=== after Distribute grades (rewritten wholesale on every run that touches this student, grades.render_readme) ===
This gradebook is private to you. It is regenerated each time grades are distributed; do not edit it.

| Assignment | Final grade | Submitted | Late | Team |
|---|---|---|---|---|
| Linear regression | 88 | external |  |  |
| Group project | 47 / 50 | 15 Nov 22:14 | on time | team-alpha |

## Linear regression
**Final grade:** 88

Clear, correct solution.
Watch the vectorised edge cases.

## Group project
**Final grade:** 47 / 50

Led the architecture and carried the team's core work.

> **Team feedback (shared with team-alpha):** Excellent model design and a clear training story.
> The evaluation section is thin - one baseline is not a comparison.
