# Enrol students

1. Put the list of student into the roster CSV, 
2. Automatically send each student an enrolment code, 
3. Students then self-onboard via a Join course issue.

## Prerequisites

- A bootstrapped [cohort org](04-new-cohort-org.md).

## Steps for initial enrolment

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

   > **What "live" means:** the mail secrets are set centrally by the DSL team, on the course org - `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER` (Microsoft Graph, preferred) or `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` (+ optional `SMTP_PORT`, `SMTP_FROM`). Only two workflows need them: **Send enrolment codes** and **Distribute grades**. With neither set, both run and write their files but send nothing, and say so.

3. **Students self-onboard.**
   - Each student opens a **Join course** issue in the cohort's `welcome` repo and pastes their code.
   - The match is on the **`enrol_code`** alone - never on the email, which the student never types.
     The issue author is the authenticated GitHub handle, so what the code buys is the link between
     that handle (plus its immutable GitHub id) and the roster row the registrar seeded. A code is
     single-use: once bound to an account it can't be claimed by another.
   - A successful run labels the issue `onboarded`, retitles it and closes it, and adds the student
     to the org and their role's team (`students` | `auditors`).
   - They must accept the org invite before they can see anything.
   - **A failure gets one neutral message**: "that code could not be matched to an unclaimed roster
     row". Unknown code and already-claimed code read identically on purpose - telling them apart in
     a public issue would confirm to any reader that a pasted code is live.
   - **Triage `needs-review` issues, then delete them.** A failed Join issue still carries the
     student's code in its body *history* (the workflow redacts the current body, but GitHub
     keeps every revision readable). Resolve the cause, then delete the issue - or rotate the
     code by blanking that row's `enrol_code` cell and re-running Send codes.
   - **Students must never paste a code in a comment.** Comments are public and are never redacted.
     Blank issues are disabled in `welcome` for the same reason: an issue opened outside a form
     carries no routing label, so nothing redacts it and nobody is notified.

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
- The self-serve issue flow only accepts an assignment already **declared under `assignments:` in
  `classroom-config/schedule.yml`** - declare it there before you tell students to form teams - and
  enforces its **team-size cap**: `max_team_size` (default 5 when unset).
- Team names are stored and compared **lower-cased**. A name that is a roster GitHub handle is
  refused (a group repo is `<slug>-<team>` and a submission repo `<slug>-<handle>`, so the two would
  collide), and so is one that would spell a faculty team such as `course-admin`.
- The **Sync membership** workflow then creates a GitHub team per group.
- A **Release assignment** run with `group` ticked then grants each team its shared repo.

> TODO: later add ability to join groups after group assignment released.

## Next

- [Release an assignment](09-release-assignment-to-cohort.md) once students have onboarded - or
  let the [schedule](07-schedule-releases.md) hand it out for you.

---
**Demo:** [Send enrolment codes](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/send-codes.yml)
in the demo course org · Join course issue in [`hertie-dsl-demo-f2026/welcome`](https://github.com/hertie-dsl-demo-f2026).
