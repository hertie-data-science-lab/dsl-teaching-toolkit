# Add an assignment to the course org

Scaffold an assignment **template** repo, then fill in the brief and starter. Assignments
are marked by hand; the model solution and the autograder are optional extras on top. One
per assignment: `assignment-N-{f/s}YYYY`.

## Prerequisites

- A bootstrapped [course org](01-new-course-org.md) and push access on its content repos - see [Add materials → Prerequisites](02-add-materials-to-course.md#prerequisites).

## Steps

Live example: [`example-course/course-org/assignment-1-f2026/`](../example-course/course-org/assignment-1-f2026).

1. **Scaffold the template.** 
   - In the course org → `.github` → **Actions tab** → [New assignment](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/new-assignment.yml). Inputs:
      - `assignment_name` = the assignment's name, e.g. `Neural networks from scratch`
      - `assignment_number` = `1`, `2`, etc
      - `semester_tag` = `f/sYYYY`
      - `copy_from` (optional): an existing `assignment-*` template to start from instead -
        `main` and `solution` arrive whole, history included, and nothing is written over
        them. Every box below it is then ignored, because the copied `grading_config.yml`
        is this assignment's definition, and the run says so with a link to it. The name
        and the number are still used: they name the repo and describe it. A source with
        no `solution` branch is refused.
      - `format` = which starter file(s) to seed, comma-separated: `ipynb`, `py`, `rmd`,
        `qmd`, `latex` - or `none` on its own for the brief and nothing else. Picks the
        starters, and nothing else (see [Formats](#formats-and-what-students-hand-in)).
      - `type` (`individual` or `group` - one repo per student vs per team)
      - `team_formation` (group only: `self_select` = students use the welcome repo's
        **Join team** form; `assigned` = you write `classroom-config/teams.csv`)
      - `submit_via` = where students hand in. `github` = they push to their repo, and the
        cutoff, the receipts and the late window apply; `external` = handed in elsewhere
        (Moodle, Kaggle, in class), so the repo only carries the brief and the Feedback
        issue and nothing is ever collected from it
      - `autograde` (off by default; on seeds a `tests/` stub on `solution` for you to
        fill, and each submission's pass count appears on the grading sheet as a first
        pass for graders - never shown to students)
   - Everything else - the team cap, the late window, the penalty - comes from
     `assignment_defaults:` in the course org's `.github/dsl-course.yml` and is written
     into the assignment's own `grading_config.yml`, where you can revise it per assignment.
   - this creates **`assignment-1-f2026`** with two branches of stubs for you to replace:

   | Branch | Holds | Who sees it |
   |--------|-------|-------------|
   | `main` | `README.md` (brief) + `starter.*` + `CONTRIBUTIONS.md` (group only) | **what students get** |
   | `solution` | `solution/` (model answer) + `grading_config.yml` + hidden `tests/` (only when `autograde` is on) | **faculty & instructors only** |

2. **Push your content** 
   - Brief + starter → `main`
   - Model solution and `grading_config.yml` → `solution`
   - Student repos are generated from **`main` only**, unless you tick `include_solution` at release time. 
   - A purely hand-marked assignment needs nothing further: `autograde` defaults to **false**,
     and a template with no `solution` branch at all is hand-marked too. The cutoff still
     freezes the sheet and records the decision not to machine-mark it, and the grading sheet,
     the receipts and the late arithmetic all run as they always do.
   - Optionally, for a partially machine-marked assignment, set `autograde: true` in `grading_config.yml`:
     - put the hidden tests in `tests/` (the path is `grading_config.yml`'s `tests:` field): plain pytest files that `from starter import ...`, run faculty-side only and never shipped to students.
     - `info.autograde` in the grading sheet then shows how many of them each submission passed - a count for you to mark against, never the mark itself, and never shown to a student.
     - Not a Python course? Put a `run.sh` in `tests/` and the sandbox runs that instead - [the recipe](10-grade-and-return-assignments.md#tests-in-another-language-testsrunsh).
     - Full grading flow: [Grade and return assignments](10-grade-and-return-assignments.md).
   - A **notebook** assignment also gets a [completion check](10-grade-and-return-assignments.md#the-completion-check-notebooks)
     at the cutoff, hand-marked or not: the toolkit runs the notebook and records whether it
     goes top to bottom. `completion_check: false` in `grading_config.yml` turns it off.

3. **Run Refresh actions** so the assignment dropdowns update.

Repeat for each assignment (`number` = 2, 3, …). 

### Formats and what students hand in

Each format seeds one starter on `main`, and each one already builds: an `.Rmd` that
knits, a `.qmd` that renders, a `.tex` that compiles, a notebook that runs. Name several -
`ipynb,py` - and each gets its own starter and its own model-answer stub on `solution`.

| `format` | Starter on `main` | What the student commits |
|---|---|---|
| `ipynb` | `starter.ipynb` | the notebook, outputs saved, after **Restart kernel and run all** |
| `py` | `starter.py` | the `.py` files, runnable from the repository root |
| `rmd` | `starter.Rmd` | `starter.Rmd` **and** the knitted `starter.html` |
| `qmd` | `starter.qmd` | `starter.qmd` **and** the rendered `starter.html` |
| `latex` | `starter.tex` | `starter.tex` **and** the compiled `starter.pdf` |
| `none` | nothing at all | whatever your brief says |

> **The graded artefact is the built one** - a grader reads the rendered document and
> checks it against the source. The starter and the seeded brief both say so.

Two starters can want the same filename, and the second one wins. `rmd` and `qmd` both
build `starter.html`; and with `autograde` on, the cutoff converts a submitted
`starter.ipynb` to `starter.py` before the hidden tests import it, so on `ipynb,py` the
notebook is what gets marked. Combine formats that build different things, or say in the
brief which one is marked.

The `.Rmd` and `.qmd` stubs seed an `{r}` chunk; swap it for `{python}` if your course
works in Python and nothing else changes.

Grading reads whatever is actually in the repo, so a student who works in a notebook on a
`py` assignment still grades, and `none` - which stands alone in the box - is the raw-repo
option.

### One notebook, not two: derive the starter

Keeping the starter on `main` and the answer on `solution` by hand means writing the same
notebook twice and keeping the two in step for the rest of the term.

Write **one** notebook - the one you teach from - on the `solution` branch, in
`solution/`, and fence the answers off in the vocabulary nbgrader and Otter already use:

```python
def fit(x, y):
    ### BEGIN SOLUTION
    return x @ y
    ### END SOLUTION
```

Three ways to say it, and you can mix them in one file:

| Fence | Where | What the student gets |
|---|---|---|
| `### BEGIN SOLUTION` … `### END SOLUTION` | anywhere in a code cell, script or Rmd | `pass  # YOUR CODE HERE`, at the same indent (`# YOUR CODE HERE` outside Python) |
| a cell tagged `solution` | a whole notebook cell | the cell's heading, then `_YOUR ANSWER HERE_` - so `### Question 2 (3 points)` survives |
| `solution=TRUE` | an Rmd/qmd chunk option | the chunk, its name and its other options, with `# YOUR CODE HERE` for a body |

Then run **Derive student version** (course org → `.github` → Actions), pick the template,
and untick `dry_run`. It reads `solution/` on the `solution` branch, strips the fences, and
writes the result onto `main` - `solution/starter.ipynb` becomes `starter.ipynb`, which is
what template-generate hands each student. It never writes to `solution`.

Three things it refuses to do, because each one publishes the answer:

- **a file with nothing fenced in it is not written at all** - the "starter" derived from it
  would be your model answer, so the run names the file and goes red;
- **an unbalanced fence is refused** - a `BEGIN` with no `END` is a typo the run will not
  guess its way past;
- **a stripped code cell loses its stored outputs** - a solution notebook is a *run*
  notebook, and its outputs are the answers in print. Cells it did not change keep theirs,
  so a worked example in the brief still shows its output.

`dry_run` is on by default and prints the file list and the counts, never a line of the
content. Only `.ipynb`, `.Rmd`, `.qmd`, `.py` and `.R` are derived; anything else under
`solution/` stays where it is.

### A value `grading_config.yml` cannot be read for

A value the toolkit cannot use costs that field and nothing else - grading falls back to
the default, silently, which is how `submit_via: emial` turned a cohort's late arithmetic
off for a term. From 24 hours before the assignment's `grading_datetime` (its due date
where it declares none) each such value opens the cohort's *assignment grading_config.yml
has values that will not grade as written* issue and emails whoever wrote the line, louder
as the moment approaches. Earlier than that nothing is said.

A template still carrying the pre-rename `grading.yml` is reported the same way - nothing
reads that file, so the assignment grades as if it declared nothing at all.

### Group vs individual assignments

- An assignment with no `type` declared is `individual`: one repo per student, assessed and returned individually.
- `type` = `group` runs the same flow per team - one repo per team, assessed at team level with individual carve-outs for comments and grade adjustments.

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
> cohort's `welcome` repo answers on. The form runs in a public repo and cannot read this
> file, so the toolkit mirrors those two values into each cohort's
> `classroom-config/assignments.lock.yml` and the form reads that. Editing them here is
> enough: the mirror catches up on the next **Sync membership**, **Release assignment** or
> nightly **Refresh actions**. Until this template exists, its schedule entry is locked to
> "no teams", so nobody can form one for it.

> **Deadlines aren't set here.** The due date students see is *per cohort*, in that cohort's `schedule.yml` - see [Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines).

## Next

- [Schedule the hand-out](07-schedule-releases.md) - the normal way to get it to students.
- [Release to a cohort](09-release-assignment-to-cohort.md) - freeze + hand out per-student repos by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New assignment.
