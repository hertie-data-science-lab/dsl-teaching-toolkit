# Enrol students

1. Put the list of students into the roster CSV, 
2. Automatically send each student an enrolment code, 
3. Students then self-onboard via a Join course issue.

## Prerequisites

- A bootstrapped [semester org](04-new-cohort-org.md).

## Steps for initial enrolment

Live example roster: [`example-course/semester-org/students.csv`](../example-course/semester-org/students.csv).

1. **Add the students to the roster.**
   - Edit `semester-config/students.csv` in the **semester** org
   - Editing directly via the web UI is fine, or edit the repo locally, commit & push
   - One row per student: fill the first three columns - `hertie_email`, `name`, and optionally `role` (blank means enrolled; `auditor` gets the materials but no assignments or grades)
   - Leave the rest (`github_handle`, `github_id`, `enrol_code`) blank - onboarding and step 2 fill them in for you


   >Someone joins late? Add their row, commit & push - the push sends them their code.
   >Someone drops? Delete their row - the commit & push off-boards them.

2. **Codes are emailed automatically.**
   - Nothing to press: step 1's commit & push is the whole of it. The push fires the course org's **Send enrolment codes**, which writes an `enrol_code` onto every roster row that lacks one and emails each not-yet-onboarded student at their `hertie_email`, within a minute or so. Watch it in the **course** org → `.github` → **Actions**.
   - **Re-pushing is safe.** Each row records `code_sent_at` just before its code goes out, and only rows without it are emailed - so a later push chases the students who still need a code and leaves the rest alone. To deliberately re-send, clear that row's `code_sent_at` and push.
     > On a semester whose codes went out before `code_sent_at` existed, the first run mails every not-yet-onboarded student their existing code again; fill `code_sent_at` on the rows already mailed to skip it.

   - **A roster the toolkit cannot read stops the send, not the run.** Excel in a German locale saves a `;`-delimited CSV, and a deleted header row reads the same way: nothing is written, nothing is sent, and the run stays **green**. The same push opens *"students.csv has rows the toolkit cannot use"* in the semester's `semester-config` and emails whoever pushed it, naming the row and the column (never a cell). Save the file as comma-separated UTF-8 and push again. Every file you edit is checked this way - [Why did I get this email?](reference/actions-reference.md#why-did-i-get-this-email).
   - **A roster with nothing but its header is green too** - nothing outstanding, nothing to report.
   - **A run that genuinely breaks says so.** Everything else - no roster at all, no mail transport, a write GitHub refused - goes red, opens *"Send enrolment codes is failing"* in the course org's `.github` (cc `course-admin`) and emails the toolkit maintainer the failed step's log. It closes itself on the next successful send.

   > **If the emailing integration isn't live** the run still writes every code into `students.csv` and then goes red for want of a transport → copy each student's code out of the roster into an email of your own and send it by hand. Emailing is live once the course org has the `GRAPH_*` secrets, set centrally by the DSL team; **Send enrolment codes** and **Distribute grades** are what use them.

3. **Students self-onboard.**
   - Each student opens a **Join course** issue in the semester's `join` repo and pastes their code.
   - The match is on the **`enrol_code`**; the issue author is the authenticated GitHub handle, so the code binds that handle (and its GitHub id) to the roster row. Single-use once bound.
   - Success: label `onboarded`, issue closed, student added to the org and to `students` | `auditors`. They must accept the org invite before they see anything.
   - Failure: one neutral "could not be matched" message, whether the code is unknown or already claimed. **Triage `needs-review` issues, then delete them** - the code stays readable in the body's edit history until the issue is deleted (or rotate the code: blank the row's `enrol_code` and its `code_sent_at`, then push).
   - Students must never paste a code in a **comment** (public, never redacted). Blank issues are disabled in `join`.

   > The semester org's `join` repo is automatically seeded when the semester org is [bootstrapped by the course org](04-new-cohort-org.md#steps).


### Auditors (optional)

Set `role: auditor` on a roster row (blank means enrolled). Auditors get read on every
released-materials repo, exactly like enrolled students, but no assignment repo, no gradebook
and no marks. A **Join team** issue from an auditor is refused and labelled `needs-review`.

---

## Group assignments (rolling basis)

>This workflow is carried out *during* course delivery. Students form their teams **while the assignment is out**: self-selection opens at the hand-out and runs to the assignment's grading pin, and the release provisions each team's shared repo as it forms.

- There are 2 methods to form groups:
   1. Students open a **Join team** issue in `join`, 
   2. instructors edit `semester-config/teams.csv`(`assignment, team, github_handle`)
- The issue flow only accepts an assignment **declared under `assignments:` in
  `semester-config/schedule.yml`**, **whose `team_formation` is `self_select`** (the semester's `assignments.yml`),
  and **whose team-formation window is open**. It enforces that assignment's
  `max_team_size` (the semester's `assignments.yml`, else the course's `assignment_defaults`, else 5). Every answer
  reaches the form through the generated mirror `semester-config/.system/assignments.lock.yml`.
  Three outcomes, by label:
  - `team-recorded` (closed): the row is in `teams.csv`. The comment points to step 2 on
    the assignment page, where the team's repo appears within minutes.
  - `team-refused` (closed as not planned): the **student** can fix it, and the comment
    says how, with a link to a new Join team issue with the Team box filled in. Covers:
    an individual or instructor-assigned assignment; the window not open yet or closed;
    Join a team that doesn't exist (the nearest real team is named) or is full; Create
    onto a name that exists, or differs from one only in case, dashes and underscores;
    Join or Create of the team they're already in; an unreadable form, an unknown Action,
    or a name that isn't letters, digits and dashes.
  - `needs-review` (open): **you** must act - not on the roster or not onboarded, an
    auditor, a missing lock or team cap, a teams.csv header problem, or a write that failed.
- A student already in a team who **joins** or **creates** another is moved while the
  window is open, in one write to `teams.csv`. Sync membership then takes them out of the
  old GitHub team, including a team they leave empty. The old team's repo is kept, with
  what they pushed.
- An assignment whose course template does not exist yet refuses every request until the
  template is created.
- Team names are lower-cased; a GitHub handle or a faculty team name (`course-admin`) is refused.
- The teams that exist, and how much room each has, are listed on the assignment's page
  on the semester site - linked from the form, its refusals and the mail; students still
  without a team are emailed while the window is open. Both: [09](09-release-assignment-to-cohort.md#group-assignments-creating-the-teams).
- The **Sync membership** workflow then creates a GitHub team per group.
- A **Release assignment** run then grants each team its shared repo (the template declares `type: group`).

## Next

- [Release an assignment](09-release-assignment-to-cohort.md) once students have onboarded - or
  let the [schedule](07-schedule-releases.md) hand it out for you.

---
**Demo:** [Send enrolment codes](https://github.com/hertie-dsl-demo-course-e1234/.github/actions/workflows/send-codes.yml)
in the demo course org · Join course issue in [`hertie-dsl-demo-f2026/join`](https://github.com/hertie-dsl-demo-f2026).
