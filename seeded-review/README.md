# Seeded / generated file review

Every user-facing file this pipeline deploys, copies or generates at bootstrap - rendered
concrete, grouped by **who reads it**.

**Workflow:** you hand-edit these files -> I diff each against a fresh render -> I port
your wording back into the generator that produces it.

> **Delete this whole directory before merging.** It is tracked on
> `docs/seeded-file-review` for review diffs only, and must never reach `main`.

Sample identifiers throughout: course org `hertie-dsl-demo-course-e1234`, cohort org
`hertie-dsl-demo-f2026`, tag `f2026` - matching `example-course/`.

---

## Layout

```
1-student-facing/     what a STUDENT reads
2-faculty-facing/     what FACULTY / INSTRUCTORS read
3-infrastructure/     workflow YAML (faculty see the button labels + descriptions)
```

### 1-student-facing/

| Path | What it is | Maps back to |
| --- | --- | --- |
| `welcome-bot-messages.md` | **All 22 bot replies** a student gets on Join course / Join team | `templates/welcome/{onboard,team-formation}.yml` (inline JS literals) |
| `website-generated-prose.md` | Every string on the course website, split by which repo owns it | `dsl_course/site.py` (+ 2 external repos) |
| `welcome-repo/README.md` | The public landing page: "how to join" | `templates/welcome/README.md` |
| `welcome-repo/issue-form-*.yml` | The two issue forms students fill in | `templates/welcome/ISSUE_TEMPLATE/` |
| `emails/enrolment-code.txt` | Emailed enrolment code | `dsl_course/enrol_codes.py:84` |
| `emails/grades-updated.txt` | Grade-release notification | `dsl_course/grades.py:545` |
| `gradebook-repo/README.md` | Landing page in their private `grades-<handle>` repo | `grades.py:91` |
| `gradebook-repo/grades.yml` | The grades file they open (shape + field names) | `grades.gradebook_entry` |
| `assignment-repo/README.*.md` | What a student sees in their own assignment repo | `dsl_course/scaffold.py` |
| `assignment-repo/starter.*` | The starter they complete | `dsl_course/scaffold.py` |
| `assignment-repo/solution-README.md` | Model-solution page, post-deadline | `dsl_course/scaffold.py` |
| `materials-repo/README.md` | Released to students with the README toggle | `dsl_course/scaffold.py` |
| `materials-repo/SYLLABUS.md` | Syllabus stub | `dsl_course/scaffold.py:229` |
| `org-landing-page/profile-README.md` | The cohort org's front page | `profile_readme.render_profile_readme` |

### 2-faculty-facing/

| Path | What it is | Maps back to |
| --- | --- | --- |
| `classroom-config/README.md` | The schema contract - the main doc faculty read | `templates/classroom-config/README.md` |
| `classroom-config/{students.csv,teams.csv,schedule.yml,people.yml}` | The four scaffolds faculty fill in | `templates/classroom-config/` |
| `classroom-config-samples/*.sample` | Filled worked examples shipped beside each scaffold | `example-course/cohort-org/` |
| `course-config/dsl-course.yml` | Course identity + admin SSOT (comments are the docs) | `templates/course/*` via `bootstrap_course._course_metadata` |
| `course-config/cohort-dsl-course.yml` | Cohort -> course pointer | `templates/cohort/dsl-course.yml` |
| `materials-repo/MAINTAINING.md` | How to operate a materials repo | `dsl_course/scaffold.py` |
| `assignment-repo/grading.*.yml` | Autograder config, 3 variants | `scaffold._GRADING_YML` |
| `assignment-repo/hidden-test*.py` | Hidden-test stubs faculty replace | `scaffold._HIDDEN_TEST_{PY,NOTEBOOK}` |
| `assignment-repo/solution.*` | Model-solution stubs | `dsl_course/scaffold.py` |
| `org-landing-page/course-profile-README.md` | Course org front page - the full action index | `profile_readme.render_profile_readme` |
| `org-landing-page/*-dotgithub-README.md` | Orientation on landing in `.github` | `profile_readme.render_dotgithub_readme` |
| `check-cohort-setup-report.md` | The "Check cohort setup" button's output | `status.render_markdown` |
| `notifications.md` | **Issue + PR bodies** the system opens: grades preview PR, site-overwrite notice, cron-failure alert, schedule-validation failure; plus repo/team descriptions | `grades.py`, `site.py`, `workflows_render.py`, `validate-schedule.yml` |

### 3-infrastructure/

Workflow YAML. Faculty read the **button names, input descriptions and header comments**;
the rest is machinery. Edit the prose, leave the shell logic.

- `course-org-buttons/` (18) - `dsl_course/workflows_render.py`
- `cohort-classroom-config/` (3) - `templates/classroom-config/`
- `cohort-welcome/` (2) - `templates/welcome/`; **edit `welcome-bot-messages.md` instead** for the student replies

---

## Three things to know while editing

1. **Shared boilerplate.** The `check-team` gate, the checkout+python preamble, the cron
   failure-issue steps and `permissions: {}` are single constants in
   `workflows_render.py`. Change one in any workflow file and I apply it to all 17 - say
   so explicitly if you want a divergence instead.
2. **`actions_table`** is one string rendered into both `1-student-facing/materials-repo/README.md`
   and `2-faculty-facing/materials-repo/MAINTAINING.md`. Same rule.
3. **Live-discovered values** - dropdown options, repo tables, the status report's counts -
   come from the org at runtime. The demo values here stand in for them; edit the
   surrounding prose, not the data.

## Ownership - which edits actually stick

| Class | Files | Behaviour |
| --- | --- | --- |
| **SYSTEM** | all workflows, `classroom-config/README.md`, both org READMEs, all `*.sample`, `MAINTAINING.md`, cohort `dsl-course.yml` | Rewritten on every nightly refresh. Your edits propagate to **every existing org** within 24h. |
| **USER** | `classroom-config/{students,teams,schedule,people}`, `welcome/README.md`, materials `README`/`SYLLABUS`, assignment starters, course `dsl-course.yml` | Seeded create-only. Your edits reach **newly bootstrapped orgs only** - existing ones keep what they have. |

> Consequence: editing a USER-owned scaffold changes nothing for live cohorts. If you want
> a change to reach them, it has to go in a SYSTEM-owned file (or be propagated by hand).

## Not included

Runtime data, not seeded prose - shape depends on live state:
- enrolment codes written into `students.csv`
- the cohort registry (`discovery.py`), autograde markers (`collect.py`), release markers (`schedule.py`)
- `.github/.last-refresh` heartbeat (a bare ISO date)

Also excluded: `docs/` and `docs-admin-arch/` - faculty read them, but they stay in this
repo and are linked, never copied into an org. Edit those in place.
