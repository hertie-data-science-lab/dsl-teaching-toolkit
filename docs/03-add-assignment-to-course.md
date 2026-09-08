# Add an assignment to the course org

Scaffold an assignment **template** repo, then fill in the brief, starter, and (optionally)
the model solution + autograder. One per assignment: `assignment-N-{f/s}YYYY`.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md) and push access on its content repos - see [Add materials → Prerequisites](02-add-materials-to-course.md#prerequisites).

## Steps

Live example: [`example-course/course-org/assignment-1-f2026/`](../example-course/course-org/assignment-1-f2026).

1. **Scaffold the template.** 
   - In the course org → `.github` → **Actions tab** → [New assignment](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-assignment.yml). Inputs:
      - `assignment_name` = the assignment's name, e.g. `Neural networks from scratch`
      - `assignment_number` = `1`, `2`, etc
      - `semester_tag` = `f/sYYYY`
      - `format` (`ipynb` / `py` / `rmd` / `qmd` / `latex` / `none`) - picks which starter
        stub you get, and nothing else. Grading reads whatever is in the repo, so a student
        who works in a notebook on a `py` assignment still grades; `none` seeds no starter
        at all.
      - `type` (`individual` or `group` - one repo per student vs per team)
      - `team_formation` (group only: `self_select` = students use the welcome repo's
        **Join team** form; `assigned` = you write `classroom-config/teams.csv`)
      - `submit_via` (`github`, or `external` for work handed in on Moodle / Kaggle / in
        class - the repo then carries the brief and the Feedback issue and nothing is ever
        collected from it)
      - `autograde` (off by default; on seeds a `tests/` stub and runs it at the cutoff)
   - Everything else - the team cap, the late window, the penalty - comes from
     `assignment_defaults:` in the course org's `.github/dsl-course.yml` and is written
     into the assignment's own `grading_config.yml`, where you can revise it per assignment.
   - this creates **`assignment-1-f2026`** with two branches of stubs for you to replace:

   | Branch | Holds | Who sees it |
   |--------|-------|-------------|
   | `main` | `README.md` (brief) + `starter.*` + `CONTRIBUTIONS.md` (group only) | **what students get** |
   | `solution` | `solution/` (model answer) + `grading_config.yml` + hidden `tests/` (only when `autograde` is on) | **faculty & instructors only** |

2. **Clone the repo locally**
   - This allows you to make local edits and replace with your own content.

3. **Push your content** 
   - Brief + starter → `main`
   - Model solution, `grading_config.yml` and the hidden `tests/` → `solution`
   - Student repos are generated from **`main` only**, unless you tick `include_solution` at release time. 
   - A purely hand-marked assignment needs nothing: `autograde` defaults to **false**, and a
     template with no `solution` branch at all is hand-marked too. The cutoff still freezes the
     sheet and records the decision not to machine-mark it.
   - For a partially machine-marked assignment set `autograde: true` in `grading_config.yml`:
      - put the hidden tests in `tests/` (path configurable via `grading_config.yml`'s `tests:` field) plain pytest files that `from starter import ...` and check the submission, run faculty-side only, never shipped to students. 
     - `info.autograde` in the grading sheet then shows how many of them each submission passed - a count for you to mark against, never the mark itself, and never shown to a student.
     - Full grading flow: [Grade and return assignments](10-grade-and-return-assignments.md).

3. **Run Refresh actions** so the assignment dropdowns update.

Repeat for each assignment (`number` = 2, 3, …). 

### Group vs individual assignments

- If not defined, an assignment is default `type` = `individual`; it is individually assessed and returned to students.
- A group project is the same flow with `type` = `group` - recorded in the solution branch's `grading_config.yml`.
- For group projects, both handout and grading then run per team automatically (i.e one repo per team is created, and the grading run assesses at the team-level with individual carve outs for comments / grade adjustments).

> The shape is set in ONE place: `type: individual | group` in the assignment's own
> `grading_config.yml`, on the template's `solution` branch. **New assignment** writes it
> from the button, and you edit it there afterwards. The cohort's `schedule.yml` used to be
> able to override it and no longer can - `type:` and `max_team_size:` there are now
> unrecognised keys that **Validate schedule** flags.
>
>```yaml
># in the template's solution branch: grading_config.yml
>type: group
>team_formation: self_select   # or `assigned`, and you write teams.csv
>max_team_size: 4
>```

> **Deadlines aren't set here.** The due date students see is *per cohort*, in that cohort's `schedule.yml` - see [Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines).

## Next

- [Schedule the hand-out](07-schedule-releases.md) - the normal way to get it to students.
- [Release to a cohort](09-release-assignment-to-cohort.md) - freeze + hand out per-student repos by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New assignment.
