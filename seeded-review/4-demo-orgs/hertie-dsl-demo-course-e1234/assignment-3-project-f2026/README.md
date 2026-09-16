<!-- source: https://github.com/hertie-dsl-demo-course-e1234/assignment-3-project-f2026/blob/main/README.md @ 3509839 -->
# Group project - an end-to-end modelling report

**Weight:** 25% of the final mark
**Teams:** 2-3 students (see `teams.csv`; one repository per team)
**Released:** 15 September 2026 | **Clinic:** 13 October | **Due:** Friday 23 October, 23:59
**Submission:** push to `main` in this team repository. The last commit before the deadline
(plus a silent 72-hour grace window) is what we mark.

## The task

Take a question you care about, a dataset you can defend, and build the smallest model that
answers it honestly. Then write it up so that a reader who was not in the room can tell
whether to believe you.

Not "get the highest score". A careful ridge regression with an honest evaluation beats a
tuned ensemble with a leak, every time, and we can tell the difference.

## What to build

```
REPORT.md            your write-up - the primary deliverable (see the required sections)
src/pipeline.py      the code, entry point `run_pipeline()`     (or src/pipeline.R)
requirements.txt     pinned dependencies                        (or renv.lock)
data/README.md       where the data came from and how to get it - NOT the raw data itself
```

Rules that are not negotiable:

- **Every fitted transformation inside the split.** Use a `Pipeline` / `recipe`. Section 9 of
  the course exists for this.
- **A fixed seed**, stated in the report.
- **Reproducible from a clean checkout** with one documented command.
- **No raw personal data in the repository.** Link to the source; commit a script that
  fetches it.
- **A model card**, as a section of `REPORT.md`.

## Required sections of `REPORT.md`

Use these exact headings - a template is in the file, and the structural checks look for
them.

1. **Question** - what you are predicting or grouping, and for whom the answer matters.
2. **Data** - source, period, unit of observation, `n`, and what is missing.
3. **Method** - the model family, the split scheme (and why it matches the dependence
   structure), and how hyperparameters were chosen.
4. **Results** - your metric with an interval, next to a baseline. Disaggregated where it
   matters.
5. **Limitations** - what would break this, in specifics.
6. **Model card** - intended use, out-of-scope use, data, performance by subgroup,
   monitoring.
7. **Contributions** - who did what, in two or three lines. Be accurate; adjustments depend
   on it.

Aim for 2,000-2,500 words plus figures. Longer is not better.

## Marking (25 marks)

| Component | Marks | What we look for |
|---|---|---|
| Question and framing | 4 | A real question; a metric that matches the decision |
| Data handling | 4 | Provenance, missingness handled deliberately, no leakage |
| Method | 5 | Defensible split, honest tuning, simplest model that works |
| Evaluation | 5 | Baseline, interval, disaggregation, calibration where relevant |
| Report and model card | 4 | Legible to a non-specialist; limitations that are specific |
| Reproducibility | 3 | Runs from a clean checkout; seed fixed; deps pinned |

**7 of those marks come from automated structural checks** run after the deadline - the
required files exist, the headings are present, the placeholders are gone, `run_pipeline` is
defined, dependencies are pinned, and a seed is set. They are a floor, not a grade: they
confirm the submission is complete and say nothing about whether it is good.

## Individual adjustment

The team mark is the starting point for everyone. Each member then receives a private
adjustment (typically -5 to +5) based on the commit history, the contributions section, and
the clinic conversation. Adjustments are visible only to the person concerned.

## The clinic, 17 November

Bring: your question, your split scheme, and the five-question leakage checklist from session
9 answered for *your* data. Fifteen minutes per team. Teams that come with a design leave
with a better one; teams that come with a finished model leave with a list.

## Getting started

1. Agree the question in week 1, not week 3. Write it down in `REPORT.md` first.
2. Build the baseline before the model. Put its score in the report and keep it there.
3. Split once, early, and do not look at the test set again until the report is written.
4. Commit often, in everyone's name. The history is evidence.
