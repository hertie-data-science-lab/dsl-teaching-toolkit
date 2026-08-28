# Enrol students

1. Put the list of student into the roster CSV, 
2. Automatically send each student an enrolment code, 
3. Students then self-onboard via a Join course issue.

## Prerequisites

- A bootstrapped [cohort org](04-new-cohort-org.md).

## Steps for initial enrolement.

Live example roster: [`example-course/cohort-org/students.csv`](../example-course/cohort-org/students.csv).

1. **Add the students to the roster.**
   - Edit `classroom-config/students.csv` in the **cohort** org
   - Editing directly via the web UI is fine, or edit the repo locally, commit & push
   - one row per student: `hertie_email, name`
   - Leave `github_handle, github_id, enrol_code` blank - onboarding and step 2 fill them automatically
   - Set `role: auditor` for anyone who should get the materials but no assignments or grades.


   >Someone joins late? Add their row, commit & push - then re-run step 2 for their code.
   >Someone drops? Delete their row - the commit & push off-boards them.

2. **Send enrolment codes.**
   - In Your **course** org → `.github` → **Actions** → **Send enrolment codes**: pick the cohort.
   - This workflow writes an `enrol_code` onto every roster row that lacks one and emails each not-yet-onboarded student at their `hertie_email`.
   - NB: `dry_run` defaults to `true`. It lists masked recipients and subjects only - never a code or a name, because the run log is public. Untick it to write and send.
   
   > **If the emailing integration isn't live for any reason** the codes can still be written into `students.csv` by the `Send enrolment codes` workflow → then copy each student's code into an email of your own and send out manually.

3. **Students self-onboard.**
   - Each student opens a **Join course** issue in the cohort's `welcome` repo and pastes their code.
   - That records their GitHub handle agaist their registered `hertie_email`, and adds them to the org and their role's team (student | auditor).
   - They must accept the org invite before they can see anything.

   > The cohort org's `welcome` repo is automatically seeded when the cohort org is [bootstrapped by the course org](04-new-cohort-org.md#steps).


### Auditors (optional)

Set `role: auditor` on a roster row (blank means enrolled). Auditors get read on every
released-materials repo, exactly like enrolled students, but no assignment repo, no gradebook
and no marks. A **Join team** issue from an auditor is refused and labelled `needs-review`.
---

## Group assignments (rolling basis)

>This workflow is carried out *during* course delivery, however groups needs to be formed *before* the associated group assignment is released. 

- There are 2 methods to form groups:
   1. Students open a **Join team** issue in `welcome`, 
   2. instructors edit `classroom-config/teams.csv`(`assignment, team, github_handle`)
- The self-serve issue flow only accepts an assignment already declared under `assignments:` in
  `classroom-config/schedule.yml`, and enforces its **team-size cap** there: `max_team_size`
  (default 5 when unset). Team names cannot be a GitHub handle or a faculty team name.
- The **Sync membership** workflow then creates a GitHub team per group.
- A **Release assignment** run with `group` ticked then grants each team its shared repo.

> TODO: later add ability to join groups after group assignment released.

## Next

- [Release an assignment](09-release-assignment-to-cohort.md) once students have onboarded - or
  let the [schedule](07-schedule-releases.md) hand it out for you.

---
**Demo:** [Send enrolment codes](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/send-codes.yml)
in the demo course org · Join course issue in [`hertie-dsl-demo-f2026/welcome`](https://github.com/hertie-dsl-demo-f2026).
