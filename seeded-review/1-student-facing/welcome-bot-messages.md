# Welcome-bot messages (student-facing)

Every message the onboarding bot posts back to a student, extracted from the JS template
literals inside `templates/welcome/onboard.yml` and `templates/welcome/team-formation.yml`.

**Edit the message text below.** Don't edit the YAML - I'll port your wording back into
the template literals. Keep the `${...}` placeholders intact (they interpolate at runtime).

`${handle}` = student's GitHub login · `${org}` = cohort org · `${team}` = `students` or
`auditors` · `${assignment}`, `${cap}`, `${attempt}`, `${e.status}` as named.

---

## A. Join course (`onboard.yml`)

### A1 · Success - normal enrolment
Label `enrolled` / `auditor` · title -> `Join course: ${handle}` · issue closed

```
Welcome **@${handle}** - your enrolment code is matched.

- **Check your email and accept the invitation to the `${org}` org** - until you accept, you can't see anything here.
- Your handle + GitHub id are recorded; you've been added to the **${team}** team (unlocks released materials once you've accepted).
```
final bullet, auditors only:
```
- You're enrolled as an **auditor**: you get the released materials, but no assignment repos and no grades.
```
final bullet, enrolled students:
```
- Your assignment repos appear here once the teaching team provisions each assignment.
```

### A2 · Success - author is org staff (self-test path)
Label `staff` · title -> `Join course: ${handle} (staff)` · issue closed

```
Thanks **@${handle}** - your enrolment code is matched, and your handle + GitHub id are now linked to that roster row.

- You're already an **owner/admin of `${org}`**, so I made **no access changes**: adding you as a `member` would DOWNGRADE your owner role and lock you out of the private repos.
- Nothing else to do. To exercise the real student path end to end, re-run this from an account that isn't org staff.
```

### A3 · Enrolment code not recognised
*Trigger: no roster row matches the typed code.* Label `needs-review`
```
Thanks @${handle} - that enrolment code isn't recognised. Check the code in your email, or contact the teaching team.
```

### A4 · Code already linked to another account
*Trigger: matched row already carries a different handle. Appears twice in the file (pre-write check and post-retry re-check) - identical text.* Label `needs-review`
```
That enrolment code is already linked to a different GitHub account. Contact the teaching team to resolve this.
```

### A5 · Code missing from the form
*Trigger: the enrolment-code field is blank or mangled.* Label `needs-review`
```
Thanks @${handle} - I couldn't read an **enrolment code** from the form. Please edit the issue and add it.
```

### A6 · Bot token not configured
*Trigger: `DSL_BOT_TOKEN` unset - the cohort was seeded but the secret never mirrored.* Label `needs-review`
```
Thanks @${handle} - enrolment is **not yet armed**: the `DSL_BOT_TOKEN` secret is unset, so I can't read the private roster or invite you. A maintainer will action this.
```

### A7 · Roster header missing a required column
Label `needs-review`
```
Thanks @${handle} - the roster's header is missing a required column (`github_handle`, `github_id`, `enrol_code`). 
```

### A8 · Roster row vanished mid-write
*Trigger: the registrar edited the roster while the bot was writing.* Label `needs-review`
```
Thanks @${handle} - your roster row disappeared while I was writing to it. Please re-open this issue, or contact the teaching team.
```

### A9 · Roster write failed after retries
*Trigger: 8 attempts exhausted, or a non-retryable error.* Label `needs-review`
> Leaks the raw HTTP status (`${e.status}`) to the student - flag if you want that dropped.
```
Thanks @${handle} - I couldn't write you to the roster after ${attempt} attempt(s) (`${e.status}`). A maintainer will action this.
```

### A10 · Roster columns changed mid-write
Label `needs-review`
```
Thanks @${handle} - the roster's columns changed while I was writing to it, so I stopped rather than risk writing into the wrong cells. A maintainer will action this.
```

---

## B. Join team (`team-formation.yml`)

### B1 · Success - team recorded
Label `team-recorded` · title -> `Join team: ${team} (${assignment}) - ${handle}` · closed
```
Done **@${handle}** - you're in team **${team}** for `${assignment}`.

- Membership is recorded; your team's repo appears once the teaching team provisions this assignment.
- Need to change teams? Contact the teaching team.
```

### B2 · Already in this exact team (no-op)
*No label applied · issue closed, title unchanged.*
```
You're already in **${team}** for `${assignment}` @${handle} - nothing to do.
```

### B3 · Already in a different team for this assignment
Label `needs-review`
```
You're already in team **${existingTeam}** for `${assignment}` @${handle}. Contact the teaching team to switch teams.
```

### B4 · Team is full
*Trigger: member count has hit `max_team_size` (schedule.yml) or the default of 5.* Label `needs-review`
> Names a private-repo path (`classroom-config/schedule.yml`) the student cannot open - flag if you want that removed.
```
Team **${team}** for `${assignment}` is full (${cap} member${cap === 1 ? '' : 's'}. Pick another team name.
```

### B5 · Not on the roster yet
*Trigger: student skipped Join course.* Label `needs-review`
```
Thanks @${handle} - I can't find you on the course enrollment roster yet. Please open a **Join** issue first to onboard, then come back to join a team.
```

### B6 · Auditor refused
Label `needs-review`
```
Thanks @${handle} - you're on the roster as an **auditor** (read-only: the released materials, but no assignment repos and no grades), so you can't join a project team. Contact the teaching team if you should be enrolled instead.
```

### B7 · Form fields unreadable
*Trigger: missing Assignment/Team, or a team name with spaces or emoji.* Label `needs-review`
```
Thanks @${handle} - I couldn't read both an **Assignment** and a **Team** from the form. Team names use letters, numbers and dashes only. Please edit the issue.
```

### B8 · Bot token not configured
Label `needs-review`
```
Thanks @${handle} - team formation is **not yet armed**: the `DSL_BOT_TOKEN` secret is unset, so I can't read the roster or record your team. A maintainer will action this.
```

### B9 · Roster header missing `github_handle`
Label `needs-review`
```
Thanks @${handle} - the roster's header is missing a `github_handle` column. A maintainer will action this.
```

### B10 · `teams.csv` header missing a required column
Label `needs-review`
```
Thanks @${handle} - `teams.csv` is missing a required column (`assignment`, `team`, `github_handle`). A maintainer will action this.
```

### B11 · Team write failed after retries
Label `needs-review`
```
Thanks @${handle} - I couldn't record your team after ${attempt} attempt(s) (`${e.status}`). A maintainer will action this.
```

---

## Consistency notes (worth a decision while you edit)

1. **Three different success openers**: `Welcome **@handle**` (A1), `Thanks **@handle**`
   (A2), `Done **@handle**` (B1). Pick one register?
2. **Four messages skip the `Thanks @handle` opener**: A4, B2, B3. Deliberate or drift?
3. **Two escalation phrasings**: `A maintainer will action this.` (infrastructure faults)
   vs `Contact the teaching team` (student-actionable). Mostly consistent; A8 mixes both.
4. **A4 is a duplicated string literal**, not a shared constant - if you change it I'll
   update both occurrences (or factor it into one constant, your call).
5. **Every failure also becomes a red Actions annotation** with `**` bold markers stripped
   - same wording, seen by faculty. No separate text to edit.

## Labels applied

| Label | When |
| --- | --- |
| `needs-review` | every failure path in both workflows - the faculty triage queue |
| `enrolled` / `auditor` | successful Join course, by roster role |
| `staff` | Join course opened by an org owner |
| `team-recorded` | successful Join team |
| *(none)* | B2, the already-in-this-team no-op |
