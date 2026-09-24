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
      - `formats` = which starter file(s) to seed, comma-separated (the first is the
        runnable one): `ipynb`, `py`, `rmd`,
        `qmd`, `latex` - or `none` on its own for the brief and nothing else. Picks the
        starters, and nothing else. Two that would land on one graded filename are
        refused (see [Formats](#formats-and-what-students-hand-in)).
      - `type` (`individual` or `group` - one repo per student vs per team)
      - `team_formation` (group only: `self_select` = students use the welcome repo's
        **Join team** form; `assigned` = you write `classroom-config/teams.csv`)
      - `submit_via` = where students hand in. `assignment_repo` = they push to their
        repo, and the cutoff, the receipts and the late window apply; `external` = handed
        in elsewhere (Moodle, Kaggle, in class), so **no repo is created**: the brief and
        a submit link appear on the site;
        `shared_dropbox_repo` = one private repo for the whole semester, each student
        pushing into their own folder and able to read everyone else's
      - `autograde` (off by default; on seeds a `tests/` stub on `solution` for you to
        fill, and each submission's pass count appears on the grading sheet as a first
        pass for graders - never shown to students)
      - `visibility` = who may read each student's repo: `private` (default), `public`, or
        `student_choice` (private, and the student is its admin: theirs to publish once the
        grading cutoff has passed). Read when the repo is created, so editing it afterwards
        moves nothing
   - Everything else - the team cap, the late window, the penalty - comes from
     `assignment_defaults:` in the course org's `.github/dsl-course.yml` and is written
     into the assignment's own `grading_config.yml`, where you can revise it per assignment.
   - With neither file stating one, late work follows the Hertie standard:
     `late_penalty_per_day: 10%` of the grade per day started, `late_window_days: 10`, the
     day the penalty reaches the whole grade. Write `late_window_days: 0` to accept nothing
     after the deadline.
   - this creates **`assignment-1-f2026`** with two branches of stubs for you to replace:

   | Branch | Holds | Who sees it |
   |--------|-------|-------------|
   | `main` | `README.md` (brief) + `starter.*` + `CONTRIBUTIONS.md` (group only) | **what students get** |
   | `solution` | `solution/` (model answer) + `grading_config.yml` + hidden `tests/` (only when `autograde` is on) | **faculty & instructors only** |

2. **Push your content** 
   - Brief + starter → `main`
   - Model solution and `grading_config.yml` → `solution`
   - Student repos are generated from **`main` only**, unless you set `solution_datetime` to `now` at release time. 
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

| `formats` | Starter on `main` | What the student commits |
|---|---|---|
| `ipynb` | `starter.ipynb` | the notebook, outputs saved, after **Restart kernel and run all** |
| `py` | `starter.py` | the `.py` files, runnable from the repository root |
| `rmd` | `starter.Rmd` | `starter.Rmd` **and** the knitted `starter.html` |
| `qmd` | `starter.qmd` | `starter.qmd` **and** the rendered `starter.html` |
| `latex` | `starter.tex` | `starter.tex` **and** the compiled `starter.pdf` |
| `none` | nothing at all | whatever your brief says |

> **The graded artefact is the built one** - a grader reads the rendered document and
> checks it against the source. The starter and the seeded brief both say so.

Two starters may not share the file a grader reads, and the button refuses the pairs that
would: `rmd,qmd`, because both build `starter.html`; and `ipynb,py` **when `autograde` is
on**, because the cutoff converts the submitted `starter.ipynb` to `starter.py` before the
hidden tests import it, over whatever the student wrote there. `ipynb,py` on a hand-marked
assignment is fine - nothing converts anything - and so is every other combination. The
refusal comes before the repo is created, so it costs a re-run of the button.

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
and untick `preview`. It reads `solution/` on the `solution` branch, strips the fences, and
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

`preview` is on by default and prints the file list and the counts, never a line of the
content. Only `.ipynb`, `.Rmd`, `.qmd`, `.py` and `.R` are derived; anything else under
`solution/` stays where it is.

### A value `grading_config.yml` cannot be read for

A value the toolkit cannot use costs that field and nothing else - grading falls back to
the default, silently, which is how `submit_via: emial` turned a semester's late arithmetic
off for a term. From 24 hours before the assignment's `grading_datetime` (its due date
where it declares none) each such value opens the semester's *assignment grading_config.yml
has values that will not grade as written* issue and emails whoever wrote the line, louder
as the moment approaches. Earlier than that nothing is said.

A template still carrying the pre-rename `grading.yml` is reported the same way - nothing
reads that file, so the assignment grades as if it declared nothing at all.

### Where the work goes, and who sees it

Two settings in `grading_config.yml`, and everything else follows from them.

| Setting | Values | What it does |
|---|---|---|
| `submit_via` | `assignment_repo` (default) | One private repo per student or team. The cutoff, the receipts and the late window apply. |
| | `external` | Handed in off GitHub. **No repo is created.** Nothing is collected, nothing is timed, and the grading sheet has no `info:` block. |
| | `shared_dropbox_repo` | **One private repo for the whole semester**, `<slug>-submissions`, with a folder per student or team inside it. The cutoff and the late window apply per folder; there is no receipts issue. |
| `submit_url` | an `https://` address | `external` only: puts a **Submit on \<host\>** button on the assignment's page and its due row. Without one the page says to read the brief. |
| `visibility` | `private` (default) | Only the student and the teaching team can read their repo. |
| | `public` | Every student's repo is world-readable from hand-out - portfolio work such as a hackathon. |
| | `student_choice` | Created **private**, with the student (or every member of a team) as its **admin**. After the grading cutoff they may publish it themselves from the repo's Settings; before it, the scheduler puts any published repo back to private. |

Older `grading_config.yml` files spell `assignment_repo` as `github` - both are read, but
`New assignment` and the scaffold only ever write `assignment_repo` now.

GitHub's fourth visibility, `internal` - readable by every member of an enterprise and by
nobody outside it - is **not supported**: it needs an Enterprise plan the courses do not
have, and a repo the whole institution can read is not a repo marks may be posted into.

A `public` or `student_choice` repo gets **no submission receipts**, and no **model solution**
is ever pushed into one - the answers would be published with the repo, so an assignment that is not
`private` keeps its model answer on the template's `solution` branch (the digest says so if
its `schedule.yml` entry asks for a solution release anyway). Students who want their own work public on
a `private` assignment publish a copy under their own account (the gradebook README tells
them how); the org's copy stays private.

`visibility` is read when each repo is **created**. Editing it after the assignment has
gone out changes nothing on GitHub, so the semester's *grading_config.yml* digest reports the
disagreement until the line and the repos say the same thing again. `student_choice` is
exempt from that check - a mixture is what it is for - and is checked against the ORG
instead: it needs **Allow members to change repository visibilities** ON and **Allow
members to delete or transfer repositories** OFF (semester org → Settings → Member
privileges, set by hand once - see the
[deployment checklist](DEPLOYMENT-CHECKLIST.md#semester-setup-per-year)). The same digest
faults while either is wrong.

#### A shared drop box

`submit_via: shared_dropbox_repo` hands out ONE private repo for the assignment, `<slug>-submissions`,
and gives every onboarded student (or every team) `push` on it. Each unit works in its own
`<handle>/` or `<team>/` folder; the whole semester can read the whole repo, which is the
point - peer-visible presentations, referee reports, a gallery of submissions.

The folder name is the student's GitHub handle (or the team name) spelt **exactly** as
*students.csv* or *teams.csv* has it, including its capitals: GitHub matches a path
case-sensitively, so work pushed to `Anna-Adams/` is not in `anna-adams/` and is not found.
Say so in the brief, and check the spelling of a handle whose folder the sheet says is empty.

What to know before you pick it:

- **The folders are a convention, not a boundary.** Anyone with push can write into anyone
  else's folder. A submission is pinned to the last commit in a folder that one of *its own*
  members made, so a classmate's edit cannot be marked as a student's work - and when the
  most recent hand on a folder was not the unit's, the grading sheet says so
  (`submitted_note`). Nothing is lost either way: `git log` keeps every version, and the
  repo carries a ruleset against force-pushes and deletion - though that needs GitHub Team,
  and every Hertie org is on Free until the Education upgrade lands, so until then the drop
  box is left unprotected and the release run log says so.
- **One drop box per assignment.** Two schedule entries resolving to one semester-side name
  are refused, as they are for every other shape.
- **Private only.** One repo holds the whole semester's work and no student can opt out of
  being in it, so a `visibility:` line on a shared assignment is dropped at the parse.
- **No receipts issue, no model solution.** Both would be written where the whole semester
  can read them.
- **Hand-marked.** `autograde:`, `completion_check:` and `grader_pdf:` are dropped at the
  parse if you set them (and the notebook completion check is off here whether or not the
  file mentions it): all three run per unit against the unit's own repo, so each student
  would have the whole drop box cloned and scored under their own name, and every one of
  them would get the same result.

The **Submission receipts** issue - the thread a student is told what we recorded for them
in - exists only where there is a private repo of the student's own to put it in, so an
`external` or `shared_dropbox_repo` assignment has none. No mark is lost with it: marks and
feedback go to the private `grades-<handle>` gradebook for every shape alike, which is
[Grade and return assignments](10-grade-and-return-assignments.md).

`submit_url` is not a form box: **New assignment** seeds a commented line for it in
`grading_config.yml`, and you fill it in there. `visibility` is box 10 on that form, which
is GitHub's cap of ten inputs - every further setting lives in `grading_config.yml` only.

### Group vs individual assignments

- An assignment with no `type` declared is `individual`: one repo per student, assessed and returned individually.
- `type` = `group` runs the same flow per team - one repo per team, assessed at team level with individual carve-outs for comments and grade adjustments.

> The shape is set in ONE place: `type: individual | group` in the assignment's own
> `grading_config.yml`, on the template's `solution` branch. **New assignment** writes it
> from the button, and you edit it there afterwards. The semester's `schedule.yml` used to be
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
> semester's `welcome` repo answers on. The form runs in a public repo and cannot read this
> file, so the toolkit mirrors those two values into each semester's
> `classroom-config/assignments.lock.yml` and the form reads that. Editing them here is
> enough: the mirror catches up on the next **Sync membership**, **Release assignment** or
> nightly **Refresh actions**. Until this template exists, its schedule entry is locked to
> "no teams", so nobody can form one for it.

> **Deadlines aren't set here.** The due date students see is *per semester*, in that semester's `schedule.yml` - see [Release assignment → Deadlines](09-release-assignment-to-cohort.md#deadlines).

## Next

- [Schedule the hand-out](07-schedule-releases.md) - the normal way to get it to students.
- [Release to a semester](09-release-assignment-to-cohort.md) - freeze + hand out per-student repos by hand.

---
**Demo:** [`hertie-dsl-demo-course-e1234`](https://github.com/hertie-dsl-demo-course-e1234) → New assignment.
