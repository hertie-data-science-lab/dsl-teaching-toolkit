# Add an assignment to the course org

Scaffold an assignment **template** repo, then fill in the brief, starter, and (optionally)
the model solution + autograder. One per assignment: `assignment-N-{f/s}YYYY`.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md) and push access on its content repos - see [Add materials → Prerequisites](02-add-materials-to-course.md#prerequisites).

## Steps

Live example: [`example-course/course-org/assignment-1-f2026/`](../example-course/course-org/assignment-1-f2026).

1. **Scaffold the template.** 
   - In the course org → `.github` → **Actions tab** → [New assignment](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-assignment.yml). Inputs:
      - `number` = `1`, `2`, etc (the assignment number)
      - `tag` = `f/sYYYY`, 
      - `format` (`py` starter script or `notebook`) - picks which starter stub you get.
        Either way the autograder accepts both: it `nbconvert`s any `.ipynb` the
        submission holds before running the hidden tests.
      - `type` (`individual` or `group` - one repo per student vs per team) 
   - this creates **`assignment-1-f2026`** with two branches of stubs for you to replace:

   | Branch | Holds | Who sees it |
   |--------|-------|-------------|
   | `main` | `README.md` (brief) + `starter.*` | **what students get** |
   | `solution` | `solution/` (model answer) + `grading_config.yml` + hidden `tests/` | **faculty & instructors only** |

2. **Clone the repo locally**
   - This allows you to make local edits and replace with your own content.

3. **Push your content** 
   - Brief + starter → `main`
   - Model solution, `grading_config.yml` and the hidden `tests/` → `solution`
   - Student repos are generated from **`main` only**, unless you tick `include_solution` at release time. 
   - For a purely hand-marked assignment, set `autograde: false` in `grading_config.yml` - or push no
     `solution` branch at all. **Do not delete `grading_config.yml`**: a missing file falls back to
     `autograde: true, tests: tests`, and the grading run then errors on the `tests/` folder
     that isn't there.
   - For a partially machine-marked assignment set `autograde: true` in `grading_config.yml`:
      - put the hidden tests in `tests/` (path configurable via `grading_config.yml`'s `tests:` field) plain pytest files that `from starter import ...` and check the submission, run faculty-side only, never shipped to students. 
     - `info.autograde` in the grading sheet then shows how many of them each submission passed - a count for you to mark against, never the mark itself, and never shown to a student.
     - Full grading flow: [Grade and return assignments](10-grade-and-return-assignments.md).

3. **Run Refresh actions** so the assignment dropdowns update.

Repeat for each assignment (`number` = 2, 3, …). 

### Group vs individual assignments

- If not defined, an assignment is default `type` = `individual`; it is individually assessed and returned to students. 
- A group project is the same flow with `type` = `group` - recorded in the solution branch's `grading_config.yml`, 
- For group projects, both handout and grading then run per team automatically (i.e one repo per team is created, and the grading run assesses at the team-level with individual carve outs for comments / grade adjustments):

> The type is determined in the assignment's solution branch in the `grading_config.yml`'s field `type: individual | group`. 
>- This field is initially set (1) at the course-level when the assignment itself is created using the ` New assignment` workflow (in the course org's `.github`'s Actions tab) 
> - It can be also be set in (2) the cohort's `classroom-config/schedule.yml`, which is more accessible to TAs with only cohort org write access:
>
>```yaml
># in classroom-config/schedule.yml
>assignments:
>  assignment-4-project:
>    type: group
>```
> Here (2) flips the initial course org template assignment type when it is deployed to the cohort org, where the template is copied into individual private repos for students to complete.

> **Deadlines aren't set here.** The due date students see is *per cohort*, in that cohort's `schedule.yml` - see [Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines).

## Next

- [Schedule the hand-out](07-schedule-releases.md) - the normal way to get it to students.
- [Release to a cohort](09-release-assignment-to-cohort.md) - freeze + hand out per-student repos by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New assignment.
