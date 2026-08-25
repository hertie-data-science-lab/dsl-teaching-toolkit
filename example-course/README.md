# E2E Dummy Course Delivery 

A complete, ready-to-copy set of **dummy course materials**: incl a growing lecture package, placeholder lab materials, three
assignments (one group project run by three teams of 3-4), a roster with an auditor, instructor/TA
cards, and a full term's auto-release schedule. Follow the same steps to stand up your own course.

Find the example materials in this markdown's parent directory.

Full input reference:
[`DEPLOYMENT-CHECKLIST.md`](../docs/DEPLOYMENT-CHECKLIST.md).
 
## What this stands up 

i.e. what these files were used to create

| Tier | Org | Role | URL|
|------|-----|------|----|
| Course | **`hertie-dsl-demo-course-e1234`** | persistent control panel - materials, assignment templates, the buttons | [Course org](https://github.com/hertie-dsl-demo-course-e1234) | 
| Cohort | **`hertie-dsl-demo-f2026`** | student-facing target - welcome, roster, released materials, the site | [Cohort org](https://github.com/hertie-dsl-demo-f2026) & [Deployed site](https://hertie-dsl-demo-f2026.github.io)|

## What's in this dataset

```
example-course/
  course-org/
    dsl-course.yml                  # course identity + course_admins + display-only cards
    course-materials-f2026/
      lectures/01_week-1../05_week-5/  # 5 sessions (slides.md + a code demo each)
      readings/01_week-1../05_week-5/  # 5 sessions of placeholder readings
      labs/01_week-1../05_week-5/      # 5 sessions of labs (lab.py + lab.ipynb each)
      SYLLABUS.md
    lecture-code-f2026/mlpkg/       # a growing package, disclosed module-by-module
    assignment-1-f2026/             # individual (.py)      main/ + solution/
    assignment-2-f2026/             # individual (notebook) main/ + solution/
    assignment-4-project-f2026/     # GROUP project         main/ + solution/
  cohort-org/
    students.csv                    # 10 students + 1 auditor (handles blank until they onboard)
    teams.csv                       # 3 project teams of 3-4 (auditors are refused from teams)
    schedule.yml                    # the full term: releases + due dates + events
    people.yml                      # this cohort's own instructors/TAs (real push access)
    grades/*.csv                    # per-assignment grade tables (auto/manual/final)
```

> NB: **`cohort-org/` is shipped, not just documented.** Every file in it is seeded into each
> cohort's private `classroom-config` repo as the `.sample` twin of the scaffold faculty fill
> in - `students.csv` → `students.csv.sample`, and so on for every other file here. The set is
> derived by walking this directory (`welcome.CLASSROOM_SAMPLES`), so adding a file here ships
> it; bootstrap and the nightly Refresh both converge them, so editing one updates every
> cohort's worked example. Keep the contents fictional and self-contained: no real accounts,
> and links written as full URLs (a repo-relative `docs/...` link resolves to nothing once the
> file has landed in a cohort org).

> NB: **Assignment layout:** each `assignment-*/` splits into `main/` (→ the repo's `main` branch,
> what students get) and `solution/` (→ the `solution` branch: model solution, `grading.yml`, and
> the HIDDEN `tests/` that **Grade assignment** runs). Student repos never get `solution/`.
