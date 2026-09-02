# Seeded / generated file review

Every user-facing file this pipeline deploys, copies or generates at bootstrap - rendered
concrete, grouped by **who reads it**.

**Workflow:** you hand-edit these files -> I diff each against a fresh render -> I port
your wording back into the generator that produces it.

> **Delete this whole directory before merging.** It is tracked on
> `docs/seeded-file-review` for review diffs only, and must never reach `main`.

Sample identifiers throughout: course org `hertie-dsl-demo-course-e1234`, cohort org
`hertie-dsl-demo-f2026`, tag `f2026` - matching `example-course/`. Workflows render at
`ref: release`, the tier every real org runs.

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
| `welcome-bot-messages.md` | **All 24 bot replies** a student gets on Join course / Join team | `templates/welcome/{onboard,team-formation}.yml` + `_shared-script.js` (inline JS literals) |
| `website-generated-prose.md` | Every string on the course website, split by which site owns it | `dsl_course/site.py`, `site_repo.py`, `templates/site/` (+ the external theme) |
| `welcome-repo/README.md` | The public landing page: "how to join" | `templates/welcome/README.md` |
| `welcome-repo/issue-form-*.yml` | The two issue forms and the chooser config beside them | `templates/welcome/ISSUE_TEMPLATE/` |
| `site-repo/index.md`, `schedule.md` | The two site pages whose prose is the instructor's | `templates/site-seed/` |
| `site-repo/_data/*.yml` | Late policy and previous-offering data the site renders | `templates/site-seed/_data/` |
| `emails/enrolment-code.txt` | Emailed enrolment code - rendered named AND degraded | `enrol_codes.code_message` |
| `emails/grades-updated.txt` | Grade-release notification - rendered named AND degraded | `grades._email_updates` |
| `gradebook-repo/README.md` | Landing page in their private `grades-<handle>` repo | `grades._STARTER_README` |
| `gradebook-repo/grades.yml` | The grades file they open (shape + field names) | `grades.gradebook_entry` |
| `assignment-repo/README.*.md` | What a student sees in their own assignment repo | `dsl_course/scaffold.py` |
| `assignment-repo/starter.*` | The starter they complete | `dsl_course/scaffold.py` |
| `assignment-repo/solution-README.md` | Model-solution page, post-deadline | `dsl_course/scaffold.py` |
| `materials-repo/README.md` | Released to students with the README toggle | `scaffold.materials_readme` |
| `materials-repo/SYLLABUS.md` | Syllabus stub, in the standard Hertie shape | `scaffold._SYLLABUS_STUB` |
| `materials-repo/readings/01_session-1/READINGS.md` | The optional prose reading list seeded in each session's readings folder - public when released | `scaffold._READINGS_STUB` |
| `org-landing-page/profile-README.md` | The cohort org's front page | `profile_readme.render_profile_readme` |

### 2-faculty-facing/

| Path | What it is | Maps back to |
| --- | --- | --- |
| `classroom-config/README.md` | The schema contract - the main doc faculty read | `templates/classroom-config/README.md` |
| `classroom-config/{students.csv,teams.csv,schedule.yml,people.yml}` | The four scaffolds faculty fill in | `templates/classroom-config/` |
| `classroom-config-samples/*.sample` | Filled worked examples shipped beside each scaffold | `example-course/cohort-org/` |
| `course-config/dsl-course.yml` | Course identity + admin SSOT (comments are the docs) | `templates/course/*` via `bootstrap_course._course_metadata` |
| `course-config/people-*.yml` | The three fragments the admin block is assembled from | `templates/course/` |
| `course-config/cohort-dsl-course.yml` | Cohort -> course pointer | `templates/cohort/dsl-course.yml` |
| `materials-repo/MAINTAINING.md` | How to operate a materials repo | `scaffold._maintaining` |
| `materials-repo/SYLLABUS.md.sample` | Filled syllabus example beside the stub - never released to students | `scaffold._syllabus_sample` |
| `materials-repo/.releaseignore` | The withhold list - what a release must not copy out | `scaffold._RELEASEIGNORE_STUB` |
| `assignment-repo/grading.*.yml` | Autograder config, one per `type:` (individual / group) | `scaffold._GRADING_YML` |
| `assignment-repo/hidden-test*.py` | Hidden-test stubs faculty replace | `scaffold._HIDDEN_TEST_{PY,NOTEBOOK}` |
| `assignment-repo/solution.*` | Model-solution stubs | `dsl_course/scaffold.py` |
| `org-landing-page/course-profile-README.md` | Course org front page - the full action index | `profile_readme.render_profile_readme` |
| `org-landing-page/*-dotgithub-README.md` | Orientation on landing in `.github` | `profile_readme.render_dotgithub_readme` |
| `check-cohort-setup-report.md` | The "Check cohort setup" button's output | `status.render_markdown` |
| `notifications.md` | **Issue + PR bodies** the system opens: grades preview PR, site-overwrite notice, cron-failure alert, schedule-validation failure, the missing-source digest; plus repo, team and label descriptions | `grades.py`, `site_repo.py`, `source_digest.py`, `repos.py`, `gh_teams.py`, `validate-schedule.yml` |

### 3-infrastructure/

Workflow YAML. Faculty read the **button names, input descriptions and header comments**;
the rest is machinery. Edit the prose, leave the shell logic.

- `course-org-buttons/` (18) - `dsl_course/workflows_render.py`, manifest in `seed.py`
- `cohort-classroom-config/` (4) - `templates/classroom-config/`
- `cohort-welcome/` (2) - `templates/welcome/`; **edit `welcome-bot-messages.md` instead**
  for the student replies
- `course-site-repo/` (1) - `templates/site/.github/workflows/deploy.yml`

---

## Three things to know while editing

1. **Shared boilerplate.** The `check-team` gate, the checkout+python preamble, the cron
   failure-issue steps, `concurrency` and `permissions: {}` are single constants in
   `workflows_render.py`. Change one in any workflow file and I apply it to all of them -
   say so explicitly if you want a divergence instead. The same holds for
   `_shared-script.js`, which appears inline in both welcome workflows.
2. **`actions_table`** is one string rendered into both `1-student-facing/materials-repo/README.md`
   and `2-faculty-facing/materials-repo/MAINTAINING.md`. Same rule.
3. **Live-discovered values** - dropdown options, repo tables, the status report's counts -
   come from the org at runtime. The demo values here stand in for them; edit the
   surrounding prose, not the data.

## Ownership - which edits actually stick

Checked against the code, not against last round's table: what `seed.refresh` rewrites on
the nightly cron is what reaches the orgs already running.

| Class | Files | Behaviour |
| --- | --- | --- |
| **SYSTEM** | every workflow in `3-infrastructure/`; the welcome **issue forms** and chooser config; `classroom-config/README.md`; all `*.sample` (incl. `SYLLABUS.md.sample`); `MAINTAINING.md`; cohort `dsl-course.yml`; the course profile README and both `.github` READMEs | Rewritten on every nightly refresh. Your edits propagate to **every existing org** within 24h. |
| **SYSTEM, in part** | cohort `profile/README.md` | Instructor-owned prose, except the region between the `dsl:repo-table` markers, which is regenerated whole on every refresh - and `retitle_renamed_org`, which rewrites a dead org name throughout. Edit the prose; do not hand-edit inside the markers. |
| **USER, create-only** | `classroom-config/{students,teams,schedule,people}`; `welcome/README.md`; materials `README.md`, `SYLLABUS.md`, `readings/*/READINGS.md` and `.releaseignore`; the assignment READMEs and starters; the `site-repo/` pages and `_data/`; course `dsl-course.yml` | Seeded once, never rewritten. Your edits reach **newly bootstrapped orgs only** - existing ones keep what they have. |

> Consequence: editing a create-only scaffold changes nothing for live cohorts. If you want
> a change to reach them, it has to go in a SYSTEM-owned file, or be propagated by hand.

The `dsl-stub:` marker is **not** an ownership class. It marks a seeded file as still
unwritten, and only `SYLLABUS.md` acts on it: `deploy._is_withheld_stub` reads it to keep
the placeholder syllabus out of a release. `READINGS.md` carries the marker but nothing
reads it there.

## Not included

Runtime data, not seeded prose - shape depends on live state:
- enrolment codes written into `students.csv`
- the cohort registry (`discovery.py`), autograde markers (`collect.py`), release markers (`schedule.py`)
- `.github/.last-refresh` heartbeat (a bare ISO date)
- `SYLLABUS.sessions.md` (`syllabus.py`) - generated on demand from live schedule and
  readings content, not seeded at bootstrap

Also excluded: `docs/` and `docs-admin-arch/` - faculty read them, but they stay in this
repo and are linked, never copied into an org. Edit those in place.
