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
        stub you get, and nothing else (see [Formats](#formats-and-what-students-hand-in)).
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
     - Not a Python course? Put a `run.sh` in `tests/` and the sandbox runs that instead - [the recipe](10-grade-and-return-assignments.md#tests-in-another-language-testsrunsh).
     - Full grading flow: [Grade and return assignments](10-grade-and-return-assignments.md).
   - A **notebook** assignment also gets a [completion check](10-grade-and-return-assignments.md#the-completion-check-notebooks)
     at the cutoff, hand-marked or not: the toolkit runs the notebook and records whether it
     goes top to bottom. `completion_check: false` in `grading_config.yml` turns it off.

3. **Run Refresh actions** so the assignment dropdowns update.

Repeat for each assignment (`number` = 2, 3, …). 

### Formats and what students hand in

Each `format` seeds one starter on `main`, and each one already builds: an `.Rmd` that
knits, a `.qmd` that renders, a `.tex` that compiles, a notebook that runs.

| `format` | Starter on `main` | What the student commits |
|---|---|---|
| `ipynb` | `starter.ipynb` | the notebook, outputs saved, after **Restart kernel and run all** |
| `py` | `starter.py` | the `.py` files, runnable from the repository root |
| `rmd` | `starter.Rmd` | `starter.Rmd` **and** the knitted `starter.html` |
| `qmd` | `starter.qmd` | `starter.qmd` **and** the rendered `starter.html` |
| `latex` | `starter.tex` | `starter.tex` **and** the compiled `starter.pdf` |
| `none` | nothing at all | whatever your brief says |

> **The graded artefact is the built one.** For every source format the rendered document -
> the HTML, the PDF - is committed beside its source, and that is what a grader reads; the
> source is what we check it against. The starter says so, and so does the brief the button
> seeds, so it is on the page whatever else you write.

The `.Rmd` and `.qmd` stubs seed an `{r}` chunk; swap it for `{python}` if your course
works in Python and nothing else changes.

`format` picks the starter and nothing else. Grading reads whatever is actually in the
repo, so a student who works in a notebook on a `py` assignment still grades, and `none`
is the raw-repo option.

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
>
> `team_formation` and `max_team_size` are also what the **Join team** form in each
> cohort's `welcome` repo answers on. It cannot read this file - it runs in a public repo
> under a token that has no access to the templates - so the toolkit mirrors those two
> values into each cohort's `classroom-config/assignments.lock.yml` and the form reads
> that. Editing them here is enough: the mirror catches up on the next **Sync
> membership**, **Release assignment** or nightly **Refresh actions**. Until this template
> exists, its schedule entry is locked to "no teams", so nobody can form one for it.

> **Deadlines aren't set here.** The due date students see is *per cohort*, in that cohort's `schedule.yml` - see [Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines).

## Next

- [Schedule the hand-out](07-schedule-releases.md) - the normal way to get it to students.
- [Release to a cohort](09-release-assignment-to-cohort.md) - freeze + hand out per-student repos by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New assignment.
