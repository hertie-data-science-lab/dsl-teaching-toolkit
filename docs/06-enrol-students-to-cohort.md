# Enrol students

1. Put the list of students into the roster CSV, 
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
   - In your **course** org → `.github` → **Actions** → **Send enrolment codes**: pick the cohort.
   - This workflow writes an `enrol_code` onto every roster row that lacks one and emails each not-yet-onboarded student at their `hertie_email`.
   - NB: `dry_run` defaults to `true`. It lists masked recipients and subjects, plus one sample body rendered from placeholders - never a real code or name, because the run log is public. It also checks the mail credential, so a green preview means a real send will authenticate. Untick it to write and send.
   - **Re-running is safe.** Each row records `code_sent_at` once its code has gone out, and only rows without it are emailed - so a re-run chases the students who still need a code and leaves the rest alone. To deliberately re-send, clear that row's `code_sent_at`.
     > On a cohort whose codes went out BEFORE this landed, no row carries `code_sent_at` yet, so the first run still mails every not-yet-onboarded student (the same code they already have). It is correct from then on; to skip that one run, fill `code_sent_at` on the rows already mailed.

   > **If the emailing integration isn't live for any reason** the codes can still be written into `students.csv` by the `Send enrolment codes` workflow → then copy each student's code into an email of your own and send out manually.

   > Emailing is live once the course org has the `GRAPH_*` secrets - set centrally by the DSL team. **Send enrolment codes**, **Distribute grades** and the hourly **Scheduled release** cron use them; without them they write their files, send nothing, and say so.

   > **Or skip the button entirely.** Declare an `enrolment:` window in the cohort's [`schedule.yml`](07-schedule-releases.md#enrolment) and the hourly cron does this step for you, for as long as the window is open - which means a student you add to `students.csv` in week one is emailed within the hour, without anyone remembering to re-run anything.

3. **Students self-onboard.**
   - Each student opens a **Join course** issue in the cohort's `welcome` repo and pastes their code.
   - The match is on the **`enrol_code`**; the issue author is the authenticated GitHub handle, so the code binds that handle (and its GitHub id) to the roster row. Single-use once bound.
   - Success: label `onboarded`, issue closed, student added to the org and to `students` | `auditors`. They must accept the org invite before they see anything.
   - Failure: one neutral "could not be matched" message, whether the code is unknown or already claimed. **Triage `needs-review` issues, then delete them** - the code stays readable in the body's edit history until the issue is deleted (or rotate the code: blank the row's `enrol_code`, re-run Send codes).
   - Students must never paste a code in a **comment** (public, never redacted). Blank issues are disabled in `welcome`.

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
- The issue flow only accepts an assignment already **declared under `assignments:` in
  `classroom-config/schedule.yml`** (declare it before students form teams) and enforces its
  `max_team_size` (default 5).
- Team names are lower-cased; a GitHub handle or a faculty team name (`course-admin`) is refused.
- The **Sync membership** workflow then creates a GitHub team per group.
- A **Release assignment** run with `group` ticked then grants each team its shared repo.

> TODO: later add ability to join groups after group assignment released.

## Next

- [Release an assignment](09-release-assignment-to-cohort.md) once students have onboarded - or
  let the [schedule](07-schedule-releases.md) hand it out for you.

---
**Demo:** [Send enrolment codes](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/send-codes.yml)
in the demo course org · Join course issue in [`hertie-dsl-demo-f2026/welcome`](https://github.com/hertie-dsl-demo-f2026).
