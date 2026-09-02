# Welcome-bot messages (student-facing)

Every message the onboarding bot posts back to a student, extracted from the JS template
literals inside `templates/welcome/onboard.yml`, `templates/welcome/team-formation.yml`
and the helper they share, `templates/welcome/_shared-script.js`.

**24 distinct replies** - 11 on Join course, 13 on Join team. Messages assembled from two
or more source fragments (`... ` + `...`) are shown here as the student receives them, on
one line.

**Edit the message text below.** Don't edit the YAML - I'll port your wording back into
the template literals. Keep the `${...}` placeholders intact (they interpolate at runtime).

`${handle}` = student's GitHub login · `${org}` = cohort org · `${assignment}`, `${team}`,
`${cap}`, `${attempt}`, `${e.status}`, `${unresolved.length}` as named.

**How a reply gets posted.** Every failure path goes through one `fail(msg, label)` helper
in `_shared-script.js`: it posts `msg` as an issue comment, adds `label`, and calls
`core.setFailed` with the same text minus the `**` bold markers - so each failure is also a
red Actions annotation faculty read, with no separate wording to edit. Success paths post
their comment, add their label and close the issue inline.

---

## A. Join course (`onboard.yml`)

Workflow `Onboard student`, routed by the form's own `onboarding` label
(`templates/welcome/ISSUE_TEMPLATE/01-join-course.yml`).

### A1 · Success - normal enrolment
*Trigger: code matched, roster row written, org membership + role team granted.*
Label `onboarded` · title -> `Join course: ${handle}` · issue closed

```
Welcome **@${handle}** - your enrolment code is matched.

- **Check your email and accept the invitation to the `${org}` org**. Once you've accepted, your handle + GitHub id are recorded and your access is set. Until you accept, you can't see anything here.
```
> The same message and the same single label go to enrolled students and auditors alike -
> deliberately, because this issue is public and permanent, so a per-role label or a
> per-role sentence would publish each student's enrolment status. Consequence: the reply
> never tells the student which team they were added to, nor that they are an auditor with
> no assignment repos and no gradebook. Flag if you want that said somewhere private.

### A2 · Success - author is org staff (self-test path)
*Trigger: the Join issue's author is already an org **owner** (`role: admin`). The roster
row is linked; every access-changing call is skipped so the owner is not demoted.*
Label `staff` · title -> `Join course: ${handle} (staff)` · issue closed

```
Thanks **@${handle}** - your enrolment code is matched, and your handle + GitHub id are now linked to that roster row.

- You're already an **owner/admin of `${org}`**, so there are **no access changes**. Nothing else to do.
```

### A3 · No usable enrolment code in the form
*Trigger: the enrolment-code field is blank, or does not match `dsl-xxxxxx` strictly (a
mail client's Unicode hyphen, say). Any `dsl-xxxxxx` anywhere in the body has already been
redacted by this point.* Label `needs-review`
```
Thanks @${handle} - there was no valid **enrolment code** included in the submitted form (codes look like `dsl-xxxxxx`). Please open a new Join course issue with the code in the form field.
```

### A4 · Too many unresolved Join issues (throttle)
*Trigger: this author already has 3+ open `needs-review` Join issues in this repo. Checked
before the private roster is read. A triaged issue (relabelled or closed) stops counting.*
Label `needs-review`
```
Thanks @${handle} - you have ${unresolved.length} unresolved Join course issues - contact the teaching team.
```

### A5 · Bot token not configured
*Trigger: `DSL_BOT_TOKEN` unset - the cohort was seeded but the secret never mirrored.*
Label `needs-review`
> Names an org secret (`DSL_BOT_TOKEN`) a student cannot see - flag if you want that
> reduced to "enrolment is not yet set up".
```
Thanks @${handle} - enrolment is **not yet armed**: the `DSL_BOT_TOKEN` secret is unset, so I can't read the private roster or invite you. Contact the instructor(s) for this course.
```

### A6 · Roster header missing a required column
Label `needs-review`
> Names the column layout of a private file (`classroom-config/students.csv`) the student
> cannot open - flag if you want it cut to "the teaching team's roster needs fixing".
```
Thanks @${handle} - the enrolment roster file's header is missing a required column (`github_handle`, `github_id`, `enrol_code`). Contact the instructor(s) for this course.
```

### A7 · Code not matched to an unclaimed placement
*One reason-free message for THREE triggers, deliberately: no roster row carries that code;
the matching row is already bound to a different GitHub account; and the same
already-bound re-check taken again on the fresh row inside the write-retry loop. Telling
them apart in a public issue would confirm to any reader that a pasted code is a real,
live one. A code is single-use, with one exception - the same account under a new login,
matched on the immutable GitHub id, which re-links the row instead.*
Label `needs-review`
```
Thanks @${handle} - that enrolment code could not be matched to an unclaimed enrolment placement. Check the code in your email; if you have already joined from another GitHub account, or think this is a mistake, contact the teaching team.
```

### A8 · Roster row vanished mid-write
*Trigger: the row was there on the first read and gone on a retry's re-read - someone
edited the roster while the bot was writing.* Label `needs-review`
```
Thanks @${handle} - your roster row disappeared while I was writing to it. Please re-open this issue, or contact the teaching team.
```

### A9 · Roster write failed after retries
*Trigger: 8 attempts exhausted, or a non-retryable error (not a 409, and not a 403 naming
GitHub's secondary rate limit).* Label `needs-review`
> Leaks the raw HTTP status (`${e.status}`) to the student - flag if you want that dropped.
```
Thanks @${handle} - I couldn't write you to the roster after ${attempt} attempt(s) (`${e.status}`). A maintainer will action this.
```

### A10 · Roster columns changed mid-write
*Trigger: the header differs from the one the column indices were resolved against, so
the write is abandoned rather than risked against the wrong cells.* Label `needs-review`
```
Thanks @${handle} - the roster's columns changed while I was writing to it, so I stopped rather than risk writing into the wrong cells. Please contact the instructor(s) for this course.
```

### A11 · Unexpected failure (catch-all)
*Trigger: anything that threw and was not expected - the outer `catch`.*
Label `needs-review`
> Leaks the raw HTTP status / error name to the student.
```
Thanks @${handle} - something went wrong on my side (`${e.status || e.name}`). Please contact the instructor(s) for this course; there is nothing further you need to do.
```

---

## B. Join team (`team-formation.yml`)

Workflow `Form team`, routed by the form's own `team-formation` label
(`templates/welcome/ISSUE_TEMPLATE/02-join-team.yml`).

### B1 · Success - team recorded
*Trigger: the row was appended to `teams.csv`.*
Label `team-recorded` · title -> `Join team: ${handle}` · issue closed
```
Done **@${handle}** - you're in team **${team}** for `${assignment}`.

- Membership is recorded; your team's repo appears once the teaching team provisions this assignment.
- Need to change teams? Contact the teaching team.
```

### B2 · Already in this exact team (no-op)
*Trigger: an idempotent re-open - the student is already in this team for this assignment.*
*No label applied · issue closed, title unchanged.*
```
You're already in **${team}** for `${assignment}` @${handle} - nothing to do.
```

### B3 · Bot token not configured
*Trigger: `DSL_BOT_TOKEN` unset.* Label `needs-review`
> Names an org secret a student cannot see - same call as A5.
```
Thanks @${handle} - team formation is **not yet armed**: the `DSL_BOT_TOKEN` secret is unset, so I can't read the roster or record your team. A maintainer will action this.
```

### B4 · Form fields unreadable
*Trigger: missing Assignment or Team, or a team name with spaces, emoji or other
characters outside letters/numbers/dashes.* Label `needs-review`
```
Thanks @${handle} - I couldn't read both an **Assignment** and a **Team** from the form. Team names use letters, numbers and dashes only. Please open a new issue.
```

### B5 · Team name not available
*One reason-free message for TWO triggers: the GitHub team the row would become
(`<assignment>-<team>`) is a reserved faculty team (`course-admin`, `instructors`,
`students`, `auditors`, or anything starting `instructors-`); or the team name is a handle
on the roster (a team named after a classmate would be granted that classmate's repo).
Reason-free on purpose: a message that said why would answer, for any name anyone cared to
try, whether that person is in this cohort.* Label `needs-review`
```
Thanks @${handle} - that assignment + team combination isn't available. Pick another team name.
```

### B6 · Roster header missing `github_handle`
Label `needs-review`
> Names a column of a private file the student cannot open - same call as A6.
```
Thanks @${handle} - the roster's header is missing a `github_handle` column. A maintainer will action this.
```

### B7 · Not a project participant
*One message for TWO triggers: the author is not on the roster at all (they skipped Join
course), and the author is on it as an `auditor` - read-only, so they never receive an
assignment repo. Neutral on purpose: this issue is public and permanent, and saying which
of the two it was would publish the author's enrolment role.* Label `needs-review`
```
Thanks @${handle} - I can't find you on the course enrolment roster yet. Please open a **Join course** issue first to onboard, then come back to join a team. If you have already joined, contact the teaching team.
```

### B8 · Assignment not declared as a group assignment
*Trigger: the slug is not under `assignments:` in the cohort's schedule, there is no
schedule at all, or the entry declares a `type:` that is not `group`. Without this one
student could mint any number of real GitHub teams under names of their choosing.*
Label `needs-review`
```
Thanks @${handle} - `${assignment}` isn't an assignment in this cohort's schedule yet. Check the slug with the teaching team - they can add it to the cohort's schedule.
```

### B9 · `teams.csv` header missing a required column
Label `needs-review`
> Names a private file (`classroom-config/teams.csv`) and its columns - the student cannot
> open either.
```
Thanks @${handle} - `teams.csv` is missing a required column (`assignment`, `team`, `github_handle`). A maintainer will action this.
```

### B10 · Already in a different team for this assignment
*Trigger: one team per student per assignment.* Label `needs-review`
```
You're already in team **${cell(mine, iTeam)}** for `${assignment}` @${handle}. Contact the teaching team to switch teams.
```

### B11 · Team is full
*Trigger: member count has hit `max_team_size` for this assignment, or the default of 5.
Re-checked inside the write loop, so two students committing at the same moment cannot
put a team over its cap.* Label `needs-review`
```
Team **${team}** for `${assignment}` is full (${cap} member${cap === 1 ? '' : 's'}). Pick another team name.
```

### B12 · Team write failed after retries
*Trigger: 8 attempts exhausted, or a non-retryable error (not a 409, not the 422
create-on-first-use race, not a 403 naming the secondary rate limit).* Label `needs-review`
> Leaks the raw HTTP status (`${e.status}`).
```
Thanks @${handle} - I couldn't record your team after ${attempt} attempt(s) (`${e.status}`). A maintainer will action this.
```

### B13 · Unexpected failure (catch-all)
*Trigger: the outer `catch`.* Label `needs-review`
> Leaks the raw HTTP status / error name.
```
Thanks @${handle} - something went wrong on my side (`${e.status || e.name}`). A maintainer will action this; there is nothing you need to do.
```

---

## Consistency notes (worth a decision while you edit)

1. **Three success openers**: `Welcome **@handle**` (A1), `Thanks **@handle**` (A2),
   `Done **@handle**` (B1). Pick one register?
2. **Three messages skip the `Thanks @handle` opener**: B2, B10, B11 - they open with
   `You're already ...` / `Team **x** ...`. Deliberate or drift?
3. **Three escalation phrasings, split by workflow rather than by cause.** `onboard.yml`
   says **"Contact the instructor(s) for this course"** for infrastructure faults (A5, A6,
   A10, A11); `team-formation.yml` says **"A maintainer will action this"** for the same
   class of fault (B3, B6, B9, B12, B13); and both say **"contact the teaching team"** for
   student-actionable ones. A9 is in `onboard.yml` but uses the maintainer wording. Three
   words for the same audience - worth collapsing to two.
4. **Four messages leak a raw HTTP status** to a student (A9, A11, B12, B13). Say if you
   want `(\`${e.status}\`)` dropped from the student-facing text; it is already in the
   Actions annotation faculty read.
5. **Four messages name something behind the private gate** - `DSL_BOT_TOKEN` (A5, B3),
   the roster's column layout (A6, B6), `teams.csv` (B9). A student can act on none of it.
6. **Two messages are shared constants with several triggers** (A7's `NO_MATCH`, B5's
   `NAME_TAKEN`, B7's `NOT_A_PARTICIPANT`) - reworded once, they change everywhere. That
   is deliberate: each is reason-free so that the reply cannot be used as an oracle from a
   public issue.
7. **The cohort's private schedule path is no longer named** in B8 or B11. If you want a
   student pointed at *where* the cap comes from, it has to be a page they can open.

## Labels applied

| Label | When |
| --- | --- |
| `needs-review` | every failure path in both workflows - the faculty triage queue |
| `onboarded` | successful Join course, whatever the roster role |
| `staff` | Join course opened by an org owner |
| `team-recorded` | successful Join team |
| *(none)* | B2, the already-in-this-team no-op |

## Routing labels (not applied by the bot - declared by the forms)

| Label | Declared by | Gates |
| --- | --- | --- |
| `onboarding` | `01-join-course.yml` | `onboard.yml`'s `if:` |
| `team-formation` | `02-join-team.yml` | `team-formation.yml`'s `if:` |

Both are seeded into the `welcome` repo by `welcome.refresh_welcome_workflows`
(`WELCOME_LABELS`): GitHub silently drops a form-declared label the repo does not have,
and a Join issue with no routing label skips both workflows entirely - no redaction, no
comment, no `needs-review`, a green "skipped" run. Blank issues are disabled
(`ISSUE_TEMPLATE/config.yml`) for the same reason.
